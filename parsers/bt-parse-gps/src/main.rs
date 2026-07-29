use std::env;
use std::path::PathBuf;
use std::process::ExitCode;
use std::sync::OnceLock;

use common::{read_json_files, relative_filename, walk_and_parse_json_files, SourceSystem};
use regex::Regex;
use serde_json::{Map, Value};

fn num_pattern() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"-?\d+\.?\d*").unwrap())
}

fn is_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().map(|f| f != 0.0).unwrap_or(true),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

fn first_truthy<'a>(obj: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter()
        .filter_map(|key| obj.get(*key))
        .find(|value| is_truthy(value))
}

fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn classify_layer(filename: &str, data: &Value) -> String {
    let lowered = filename.to_lowercase();
    let source_system = common::detect_source_system(filename, data);
    let obj = data.as_object();

    if source_system == SourceSystem::Google {
        let has_timeline_edits_key = obj
            .map(|o| o.contains_key("timelineEdits"))
            .unwrap_or(false);
        if lowered.contains("timeline edits")
            || lowered.contains("timeline_edits")
            || has_timeline_edits_key
        {
            return "google_timeline_edits".to_string();
        }
        return "google_location_history".to_string();
    }

    let has_memories_key = obj
        .map(|o| o.contains_key("Memories History"))
        .unwrap_or(false);
    if lowered.contains("memories") || has_memories_key {
        return "memories_history".to_string();
    }

    let has_location_history_key = obj
        .map(|o| o.contains_key("Location History"))
        .unwrap_or(false);
    if lowered.contains("location_history")
        || lowered.contains("location")
        || has_location_history_key
    {
        return "location_history".to_string();
    }

    "other".to_string()
}

const DEFAULT_FALLBACK_TS: &str = "1970-01-01 00:00:00 UTC";

fn normalize_timestamp(value: Option<&Value>, fallback: &str) -> String {
    let Some(value) = value.filter(|v| is_truthy(v)) else {
        return fallback.to_string();
    };
    let text = py_str(value).trim().to_string();
    if text.is_empty() {
        return fallback.to_string();
    }
    if text.contains("UTC") || text.ends_with('Z') {
        text
    } else {
        format!("{text} UTC")
    }
}

fn to_float(value: &Value) -> Option<f64> {
    let text = py_str(value);
    num_pattern().find(&text)?.as_str().parse::<f64>().ok()
}

fn is_plausible_coordinate(lat: f64, lon: f64) -> bool {
    if lat == 0.0 && lon == 0.0 {
        return false;
    }
    (-90.0..=90.0).contains(&lat) && (-180.0..=180.0).contains(&lon)
}

#[allow(clippy::too_many_arguments)]
fn append_point(
    points: &mut Vec<Value>,
    filename: &str,
    layer: &str,
    source_system: &str,
    timestamp: &str,
    lat: Option<f64>,
    lon: Option<f64>,
    metadata: Map<String, Value>,
) {
    let (Some(lat), Some(lon)) = (lat, lon) else {
        return;
    };
    if !is_plausible_coordinate(lat, lon) {
        return;
    }

    let mut record = Map::new();
    record.insert(
        "timestamp".to_string(),
        Value::String(normalize_timestamp(
            Some(&Value::String(timestamp.to_string())),
            DEFAULT_FALLBACK_TS,
        )),
    );
    record.insert("lat".to_string(), Value::from(lat));
    record.insert("lon".to_string(), Value::from(lon));
    record.insert("source".to_string(), Value::String(filename.to_string()));
    record.insert("layer".to_string(), Value::String(layer.to_string()));
    record.insert(
        "source_system".to_string(),
        Value::String(source_system.to_string()),
    );
    for (k, v) in metadata {
        record.insert(k, v);
    }

    points.push(Value::Object(record));
}

fn scan_google_timeline_edits(filename: &str, data: &Map<String, Value>) -> Vec<Value> {
    let mut points = Vec::new();

    let empty = Vec::new();
    let edits: &Vec<Value> = data
        .get("timelineEdits")
        .or_else(|| data.get("Timeline Edits"))
        .and_then(Value::as_array)
        .unwrap_or(&empty);

    let mut context_by_additional_timestamp: std::collections::HashMap<String, Map<String, Value>> =
        std::collections::HashMap::new();

    for edit in edits {
        let Some(edit) = edit.as_object() else {
            continue;
        };
        let raw_signal = edit.get("rawSignal").and_then(Value::as_object);
        let signal = raw_signal
            .and_then(|rs| rs.get("signal"))
            .and_then(Value::as_object);
        let additional_timestamp = raw_signal
            .and_then(|rs| rs.get("additionalTimestamp"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let metadata = raw_signal
            .and_then(|rs| rs.get("metadata"))
            .and_then(Value::as_object);

        let context = context_by_additional_timestamp
            .entry(additional_timestamp.clone())
            .or_default();

        if let Some(platform) = metadata
            .and_then(|m| m.get("platform"))
            .filter(|v| is_truthy(v))
        {
            context.insert("platform".to_string(), platform.clone());
        }
        if let Some(device_id) = edit.get("deviceId").filter(|v| is_truthy(v)) {
            context.insert("device_id".to_string(), Value::String(py_str(device_id)));
        }

        let activities = signal
            .and_then(|s| s.get("activityRecord"))
            .and_then(Value::as_object)
            .and_then(|ar| ar.get("detectedActivities"))
            .and_then(Value::as_array);

        if let Some(activities) = activities {
            let best = activities
                .iter()
                .filter_map(Value::as_object)
                .map(|item| {
                    let activity_type = item
                        .get("activityType")
                        .filter(|v| is_truthy(v))
                        .map(py_str)
                        .unwrap_or_else(|| "UNKNOWN".to_string());
                    let activity_confidence =
                        item.get("probability").and_then(to_float).unwrap_or(0.0);
                    (activity_type, activity_confidence)
                })
                .fold(None::<(String, f64)>, |best, current| match &best {
                    Some((_, best_conf)) if *best_conf >= current.1 => best,
                    _ => Some(current),
                });

            if let Some((activity_type, activity_confidence)) = best {
                context.insert("activity_type".to_string(), Value::String(activity_type));
                context.insert(
                    "activity_confidence".to_string(),
                    Value::from(activity_confidence),
                );
            }
        }

        let devices = signal
            .and_then(|s| s.get("wifiScan"))
            .and_then(Value::as_object)
            .and_then(|ws| ws.get("devices"))
            .and_then(Value::as_array);

        if let Some(devices) = devices {
            if !devices.is_empty() {
                let wifi_scan_source = signal
                    .and_then(|s| s.get("wifiScan"))
                    .and_then(Value::as_object)
                    .and_then(|ws| ws.get("source"))
                    .filter(|v| is_truthy(v))
                    .map(py_str)
                    .unwrap_or_default();
                context.insert(
                    "wifi_scan_count".to_string(),
                    Value::from(devices.len() as u64),
                );
                context.insert(
                    "wifi_scan_source".to_string(),
                    Value::String(wifi_scan_source),
                );
            }
        }
    }

    for edit in edits {
        let Some(edit) = edit.as_object() else {
            continue;
        };
        let raw_signal = edit.get("rawSignal").and_then(Value::as_object);
        let signal = raw_signal
            .and_then(|rs| rs.get("signal"))
            .and_then(Value::as_object);
        let additional_timestamp = raw_signal
            .and_then(|rs| rs.get("additionalTimestamp"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let position = signal
            .and_then(|s| s.get("position"))
            .and_then(Value::as_object);
        let point = position
            .and_then(|p| p.get("point"))
            .and_then(Value::as_object);

        let lat = point
            .and_then(|p| p.get("latE7"))
            .filter(|v| is_truthy(v))
            .and_then(to_float)
            .map(|v| v / 1e7);
        let lon = point
            .and_then(|p| p.get("lngE7"))
            .filter(|v| is_truthy(v))
            .and_then(to_float)
            .map(|v| v / 1e7);

        let timestamp_value = position
            .and_then(|p| p.get("timestamp"))
            .filter(|v| is_truthy(v))
            .cloned()
            .unwrap_or_else(|| Value::String(additional_timestamp.clone()));
        let timestamp = py_str(&timestamp_value);

        let position_source = position
            .and_then(|p| p.get("source"))
            .filter(|v| is_truthy(v))
            .map(py_str)
            .unwrap_or_default();
        let accuracy_m = position
            .and_then(|p| p.get("accuracyMm"))
            .filter(|v| !v.is_null())
            .and_then(to_float)
            .map(|v| v / 1000.0);
        let altitude_m = position
            .and_then(|p| p.get("altitudeMeters"))
            .cloned()
            .unwrap_or(Value::Null);

        let mut metadata = Map::new();
        metadata.insert(
            "signal_type".to_string(),
            Value::String("position".to_string()),
        );
        metadata.insert(
            "position_source".to_string(),
            Value::String(position_source),
        );
        metadata.insert(
            "accuracy_m".to_string(),
            accuracy_m.map(Value::from).unwrap_or(Value::Null),
        );
        metadata.insert("altitude_m".to_string(), altitude_m);
        if let Some(context) = context_by_additional_timestamp.get(&additional_timestamp) {
            for (k, v) in context {
                metadata.insert(k.clone(), v.clone());
            }
        }

        append_point(
            &mut points,
            filename,
            "google_timeline_edits",
            "google",
            &timestamp,
            lat,
            lon,
            metadata,
        );
    }

    points
}

fn hunt(
    obj: &Value,
    current_ts: &str,
    filename: &str,
    layer: &str,
    source_system: &str,
    points: &mut Vec<Value>,
) {
    match obj {
        Value::Object(map) => {
            let mut current_ts = current_ts.to_string();
            if let Some(potential_ts) = first_truthy(map, &["Created", "Date", "timestamp", "time"])
            {
                current_ts = normalize_timestamp(Some(potential_ts), &current_ts);
            }

            let lat = first_truthy(map, &["lat", "latitude", "Latitude"]);
            let lon = first_truthy(map, &["lon", "longitude", "Longitude"]);
            let loc_str = first_truthy(map, &["Location", "Coordinates", "coords"]);

            if let (Some(lat), Some(lon)) = (lat, lon) {
                append_point(
                    points,
                    filename,
                    layer,
                    source_system,
                    &current_ts,
                    to_float(lat),
                    to_float(lon),
                    Map::new(),
                );
            } else if let Some(Value::String(loc_str)) = loc_str {
                let matches: Vec<&str> = num_pattern()
                    .find_iter(loc_str)
                    .map(|m| m.as_str())
                    .collect();
                if matches.len() >= 2 {
                    let lat = matches[matches.len() - 2].parse::<f64>().ok();
                    let lon = matches[matches.len() - 1].parse::<f64>().ok();
                    append_point(
                        points,
                        filename,
                        layer,
                        source_system,
                        &current_ts,
                        lat,
                        lon,
                        Map::new(),
                    );
                }
            }

            for value in map.values() {
                hunt(value, &current_ts, filename, layer, source_system, points);
            }
        }
        Value::Array(items) => {
            for item in items {
                hunt(item, current_ts, filename, layer, source_system, points);
            }
        }
        _ => {}
    }
}

fn scan_file(filename: &str, data: &Value) -> Vec<Value> {
    let layer = classify_layer(filename, data);
    let source_system = common::detect_source_system(filename, data).as_str();

    if layer == "google_timeline_edits" {
        if let Value::Object(obj) = data {
            return scan_google_timeline_edits(filename, obj);
        }
    }

    let mut points = Vec::new();
    hunt(
        data,
        DEFAULT_FALLBACK_TS,
        filename,
        &layer,
        source_system,
        &mut points,
    );

    if points.is_empty() {
        if let Value::Object(obj) = data {
            if let Some(Value::Array(history)) = obj.get("Location History") {
                for entry in history {
                    let Value::Array(entry) = entry else { continue };
                    if entry.len() < 2 {
                        continue;
                    }
                    let ts = py_str(&entry[0]);
                    let coords = py_str(&entry[1]);
                    let parts: Vec<&str> = coords.split(',').collect();
                    if parts.len() < 2 {
                        continue;
                    }
                    let lat = to_float(&Value::String(parts[0].to_string()));
                    let lon = to_float(&Value::String(parts[1].to_string()));
                    let mut metadata = Map::new();
                    metadata.insert(
                        "signal_type".to_string(),
                        Value::String("history_point".to_string()),
                    );
                    append_point(
                        &mut points,
                        filename,
                        &layer,
                        &source_system,
                        &ts,
                        lat,
                        lon,
                        metadata,
                    );
                }
            }
        }
    }

    points
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let export_dir = match args.next() {
        Some(arg) => PathBuf::from(arg),
        None => {
            eprintln!("usage: bt-parse-gps <export_dir> [file...]");
            return ExitCode::FAILURE;
        }
    };

    let explicit_files: Vec<PathBuf> = args.map(PathBuf::from).collect();

    let mut all_points: Vec<Value> = Vec::new();

    let files = if explicit_files.is_empty() {
        walk_and_parse_json_files(&export_dir)
    } else {
        read_json_files(&explicit_files)
    };
    for (path, data) in files {
        let filename = relative_filename(&path, &export_dir);
        all_points.extend(scan_file(&filename, &data));
    }

    match serde_json::to_writer(std::io::stdout(), &all_points) {
        Ok(()) => ExitCode::SUCCESS,
        Err(err) => {
            eprintln!("failed to serialize output: {err}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn detects_real_snapchat_export_by_key_set() {
        let data = json!({
            "Frequent Locations": [],
            "Latest Location": {},
            "Home, School & Work": {},
            "Daily Top Locations": [],
            "Location History": [],
            "Businesses and places you may have visited": [],
            "Areas you may have visited in the last two years": []
        });

        assert_eq!(
            common::detect_source_system("location_history.json", &data),
            SourceSystem::Snapchat
        );
    }

    #[test]
    fn scans_google_timeline_edits() {
        let data = json!({
            "timelineEdits": [
                {
                    "deviceId": 12345,
                    "rawSignal": {
                        "additionalTimestamp": "2024-03-01T12:00:00Z",
                        "metadata": { "platform": "ANDROID" },
                        "signal": {
                            "position": {
                                "timestamp": "2024-03-01T12:00:05Z",
                                "source": "WIFI",
                                "accuracyMm": 5000,
                                "altitudeMeters": 12.5,
                                "point": { "latE7": 279000000, "lngE7": -822500000 }
                            },
                            "activityRecord": {
                                "detectedActivities": [
                                    { "activityType": "STILL", "probability": 0.4 },
                                    { "activityType": "IN_VEHICLE", "probability": 0.9 }
                                ]
                            },
                            "wifiScan": {
                                "source": "SCAN",
                                "devices": [{"mac": "aa"}, {"mac": "bb"}]
                            }
                        }
                    }
                }
            ]
        });

        let points = scan_file("Google/Timeline Edits.json", &data);
        assert_eq!(points.len(), 1);
        let p = &points[0];
        assert_eq!(p["lat"], 27.9);
        assert_eq!(p["lon"], -82.25);
        assert_eq!(p["layer"], "google_timeline_edits");
        assert_eq!(p["source_system"], "google");
        assert_eq!(p["signal_type"], "position");
        assert_eq!(p["position_source"], "WIFI");
        assert_eq!(p["accuracy_m"], 5.0);
        assert_eq!(p["altitude_m"], 12.5);
        assert_eq!(p["platform"], "ANDROID");
        assert_eq!(p["device_id"], "12345");
        assert_eq!(p["activity_type"], "IN_VEHICLE");
        assert_eq!(p["activity_confidence"], 0.9);
        assert_eq!(p["wifi_scan_count"], 2);
        assert_eq!(p["wifi_scan_source"], "SCAN");
    }

    #[test]
    fn hunts_generic_nested_lat_lon() {
        let data = json!({
            "Vault": {
                "Date": "2023-05-10 08:00:00",
                "Entries": [
                    {
                        "note": "no coords here"
                    },
                    {
                        "latitude": "40.7128",
                        "longitude": "-74.0060"
                    }
                ]
            }
        });

        let points = scan_file("snapchat/mydata~vault.json", &data);
        assert_eq!(points.len(), 1);
        let p = &points[0];
        assert_eq!(p["lat"], 40.7128);
        assert_eq!(p["lon"], -74.006);
        assert_eq!(p["timestamp"], "2023-05-10 08:00:00 UTC");
        assert_eq!(p["source_system"], "snapchat");
    }

    #[test]
    fn rejects_null_island_sentinel() {
        let data = json!({ "lat": 0.0, "lon": 0.0 });
        assert!(scan_file("mydata.json", &data).is_empty());
    }

    #[test]
    fn rejects_out_of_range_coordinates() {
        let data = json!({ "lat": 400.0, "lon": -74.0 });
        assert!(scan_file("mydata.json", &data).is_empty());
    }

    #[test]
    fn accepts_real_nonzero_coordinates_near_the_equator_or_meridian() {
        let data = json!({ "lat": 0.5, "lon": 45.0 });
        let points = scan_file("mydata.json", &data);
        assert_eq!(points.len(), 1);
        assert_eq!(points[0]["lat"], 0.5);
        assert_eq!(points[0]["lon"], 45.0);
    }

    #[test]
    fn falls_back_to_location_history_when_hunt_finds_nothing() {
        let data = json!({
            "Location History": [
                ["2022-01-01 00:00:00 UTC", "51.5074,-0.1278"]
            ]
        });

        let points = scan_file("Google/location_history.json", &data);
        assert_eq!(points.len(), 1);
        let p = &points[0];
        assert_eq!(p["lat"], 51.5074);
        assert_eq!(p["lon"], -0.1278);
        assert_eq!(p["signal_type"], "history_point");
    }
}
