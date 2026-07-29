use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use common::{read_json_files, relative_filename, walk_and_parse_json_files};
use serde::Serialize;
use serde_json::{json, Map, Value};

#[derive(Serialize)]
struct Event {
    id: String,
    timestamp: String,
    kind: String,
    subkind: String,
    source: String,
    summary: String,

    details: Value,
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

fn py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn str_or_empty(obj: &Map<String, Value>, key: &str) -> String {
    match obj.get(key).filter(|v| is_truthy(v)) {
        Some(v) => py_str(v).trim().to_string(),
        None => String::new(),
    }
}

fn or_unknown(value: &str) -> String {
    if value.is_empty() {
        "unknown".to_string()
    } else {
        value.to_string()
    }
}

fn to_f64_lenient(value: Option<&Value>) -> f64 {
    match value.filter(|v| is_truthy(v)) {
        None => 0.0,
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(0.0),
        Some(_) => 0.0,
    }
}

fn nested_obj<'a>(
    obj: &'a Map<String, Value>,
    key: &str,
    empty: &'a Map<String, Value>,
) -> &'a Map<String, Value> {
    obj.get(key)
        .filter(|v| is_truthy(v))
        .and_then(Value::as_object)
        .unwrap_or(empty)
}

fn scan_file(filename: &str, data: &Value) -> Vec<Event> {
    let lowered = filename.to_lowercase();
    if !lowered.contains("timeline edits") && !lowered.contains("timeline_edits") {
        return Vec::new();
    }
    let Value::Object(obj) = data else {
        return Vec::new();
    };

    let empty_edits: Vec<Value> = Vec::new();
    let edits: &Vec<Value> = obj
        .get("timelineEdits")
        .filter(|v| is_truthy(v))
        .or_else(|| obj.get("Timeline Edits").filter(|v| is_truthy(v)))
        .and_then(Value::as_array)
        .unwrap_or(&empty_edits);

    let mut events: Vec<Event> = Vec::new();

    for (index, edit) in edits.iter().enumerate() {
        let Some(edit) = edit.as_object() else {
            continue;
        };

        let empty_map = Map::new();
        let raw_signal = nested_obj(edit, "rawSignal", &empty_map);
        let signal = nested_obj(raw_signal, "signal", &empty_map);
        let metadata = nested_obj(raw_signal, "metadata", &empty_map);

        let additional_timestamp = str_or_empty(raw_signal, "additionalTimestamp");
        let device_id = str_or_empty(edit, "deviceId");
        let platform = str_or_empty(metadata, "platform");

        let activity_record = nested_obj(signal, "activityRecord", &empty_map);
        let activities: Vec<&Map<String, Value>> = activity_record
            .get("detectedActivities")
            .filter(|v| is_truthy(v))
            .and_then(Value::as_array)
            .map(|arr| arr.iter().filter_map(Value::as_object).collect())
            .unwrap_or_default();

        if !activities.is_empty() {
            let best_activity = activities
                .iter()
                .copied()
                .fold(None::<(&Map<String, Value>, f64)>, |best, current| {
                    let current_conf = to_f64_lenient(current.get("probability"));
                    match best {
                        Some((_, best_conf)) if best_conf >= current_conf => best,
                        _ => Some((current, current_conf)),
                    }
                })
                .map(|(item, _)| item)
                .expect("activities checked non-empty above");

            let activity_type_for_summary = match best_activity.get("activityType") {
                Some(v) => py_str(v),
                None => "UNKNOWN".to_string(),
            };

            let activity_type_for_details = {
                let raw = str_or_empty(best_activity, "activityType");
                if raw.is_empty() {
                    "UNKNOWN".to_string()
                } else {
                    raw
                }
            };
            let activity_confidence = to_f64_lenient(best_activity.get("probability"));

            let timestamp = activity_record
                .get("timestamp")
                .filter(|v| is_truthy(v))
                .map(py_str)
                .unwrap_or_else(|| additional_timestamp.clone())
                .trim()
                .to_string();

            events.push(Event {
                id: format!("google-activity:{index}"),
                timestamp,
                kind: "google".to_string(),
                subkind: "activity".to_string(),
                source: filename.to_string(),
                summary: format!("{activity_type_for_summary} activity detected"),
                details: json!({
                    "device_id": or_unknown(&device_id),
                    "platform": or_unknown(&platform),
                    "activity_type": activity_type_for_details,
                    "activity_confidence": activity_confidence,
                    "additional_timestamp": or_unknown(&additional_timestamp),
                }),
            });
        }

        let wifi_scan = nested_obj(signal, "wifiScan", &empty_map);
        let devices: Vec<&Map<String, Value>> = wifi_scan
            .get("devices")
            .filter(|v| is_truthy(v))
            .and_then(Value::as_array)
            .map(|arr| arr.iter().filter_map(Value::as_object).collect())
            .unwrap_or_default();

        if !devices.is_empty() {
            let strongest: Option<f64> = devices
                .iter()
                .filter_map(|d| d.get("rawRssi"))
                .filter(|v| !v.is_null())
                .filter_map(|v| match v {
                    Value::Number(n) => n.as_f64(),
                    Value::String(s) => s.trim().parse::<f64>().ok(),
                    _ => None,
                })
                .fold(None, |acc: Option<f64>, v| {
                    Some(acc.map_or(v, |m| m.max(v)))
                });

            let timestamp = wifi_scan
                .get("deliveryTime")
                .filter(|v| is_truthy(v))
                .map(py_str)
                .unwrap_or_else(|| additional_timestamp.clone())
                .trim()
                .to_string();

            events.push(Event {
                id: format!("google-wifi:{index}"),
                timestamp,
                kind: "google".to_string(),
                subkind: "wifi_scan".to_string(),
                source: filename.to_string(),
                summary: format!("Wi-Fi scan captured {} devices", devices.len()),
                details: json!({
                    "device_id": or_unknown(&device_id),
                    "platform": or_unknown(&platform),
                    "scan_source": or_unknown(&str_or_empty(wifi_scan, "source")),
                    "device_count": devices.len(),
                    "strongest_rssi": strongest.map(Value::from).unwrap_or_else(|| Value::String("unknown".to_string())),
                    "additional_timestamp": or_unknown(&additional_timestamp),
                }),
            });
        }
    }

    events.retain(|event| !event.timestamp.is_empty());
    events
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let export_dir = match args.next() {
        Some(arg) => PathBuf::from(arg),
        None => {
            eprintln!("usage: bt-parse-google <export_dir> [file...]");
            return ExitCode::FAILURE;
        }
    };

    let explicit_files: Vec<PathBuf> = args.map(PathBuf::from).collect();

    let mut all_events: Vec<Event> = Vec::new();

    let files = if explicit_files.is_empty() {
        walk_and_parse_json_files(&export_dir)
    } else {
        read_json_files(&explicit_files)
    };
    for (path, data) in files {
        let filename = relative_filename(&path, &export_dir);
        all_events.extend(scan_file(&filename, &data));
    }

    match serde_json::to_writer(std::io::stdout(), &all_events) {
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

    fn sample_edit() -> Value {
        json!({
            "deviceId": 12345,
            "rawSignal": {
                "additionalTimestamp": "2024-03-01T12:00:00Z",
                "metadata": { "platform": "ANDROID" },
                "signal": {
                    "activityRecord": {
                        "timestamp": "2024-03-01T12:00:05Z",
                        "detectedActivities": [
                            { "activityType": "STILL", "probability": 0.4 },
                            { "activityType": "IN_VEHICLE", "probability": 0.9 }
                        ]
                    },
                    "wifiScan": {
                        "source": "SCAN",
                        "deliveryTime": "2024-03-01T12:00:06Z",
                        "devices": [
                            { "mac": "aa", "rawRssi": -70 },
                            { "mac": "bb", "rawRssi": -55 }
                        ]
                    }
                }
            }
        })
    }

    #[test]
    fn extracts_activity_and_wifi_events() {
        let data = json!({ "timelineEdits": [sample_edit()] });
        let events = scan_file("Google/Timeline Edits.json", &data);
        assert_eq!(events.len(), 2);

        let activity = &events[0];
        assert_eq!(activity.id, "google-activity:0");
        assert_eq!(activity.subkind, "activity");
        assert_eq!(activity.timestamp, "2024-03-01T12:00:05Z");
        assert_eq!(activity.summary, "IN_VEHICLE activity detected");
        assert_eq!(activity.details["device_id"], "12345");
        assert_eq!(activity.details["platform"], "ANDROID");
        assert_eq!(activity.details["activity_type"], "IN_VEHICLE");
        assert_eq!(activity.details["activity_confidence"], 0.9);

        let wifi = &events[1];
        assert_eq!(wifi.id, "google-wifi:0");
        assert_eq!(wifi.subkind, "wifi_scan");
        assert_eq!(wifi.timestamp, "2024-03-01T12:00:06Z");
        assert_eq!(wifi.summary, "Wi-Fi scan captured 2 devices");
        assert_eq!(wifi.details["scan_source"], "SCAN");
        assert_eq!(wifi.details["device_count"], 2);
        assert_eq!(wifi.details["strongest_rssi"], -55.0);
    }

    #[test]
    fn ignores_files_not_named_timeline_edits() {
        let data = json!({ "timelineEdits": [sample_edit()] });
        assert!(scan_file("Google/location_history.json", &data).is_empty());
    }

    #[test]
    fn ignores_non_object_top_level_data() {
        let data = json!(["not", "an", "object"]);
        assert!(scan_file("Google/Timeline Edits.json", &data).is_empty());
    }

    #[test]
    fn filters_out_events_with_empty_timestamp() {
        let data = json!({
            "timelineEdits": [
                {
                    "deviceId": 1,
                    "rawSignal": {
                        "additionalTimestamp": "",
                        "signal": {
                            "activityRecord": {
                                "detectedActivities": [
                                    { "activityType": "STILL", "probability": 1.0 }
                                ]
                            }
                        }
                    }
                }
            ]
        });
        assert!(scan_file("Timeline Edits.json", &data).is_empty());
    }

    #[test]
    fn summary_and_details_disagree_on_empty_activity_type() {
        let data = json!({
            "timelineEdits": [
                {
                    "deviceId": 1,
                    "rawSignal": {
                        "additionalTimestamp": "2024-01-01T00:00:00Z",
                        "signal": {
                            "activityRecord": {
                                "detectedActivities": [
                                    { "activityType": "", "probability": 1.0 }
                                ]
                            }
                        }
                    }
                }
            ]
        });
        let events = scan_file("Timeline Edits.json", &data);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].summary, " activity detected");
        assert_eq!(events[0].details["activity_type"], "UNKNOWN");
    }

    #[test]
    fn tie_break_on_probability_keeps_first_activity() {
        let data = json!({
            "timelineEdits": [
                {
                    "deviceId": 1,
                    "rawSignal": {
                        "additionalTimestamp": "2024-01-01T00:00:00Z",
                        "signal": {
                            "activityRecord": {
                                "detectedActivities": [
                                    { "activityType": "FIRST", "probability": 0.5 },
                                    { "activityType": "SECOND", "probability": 0.5 }
                                ]
                            }
                        }
                    }
                }
            ]
        });
        let events = scan_file("Timeline Edits.json", &data);
        assert_eq!(events[0].details["activity_type"], "FIRST");
    }

    #[test]
    fn strongest_rssi_unknown_when_no_devices_report_it() {
        let data = json!({
            "timelineEdits": [
                {
                    "deviceId": 1,
                    "rawSignal": {
                        "additionalTimestamp": "2024-01-01T00:00:00Z",
                        "signal": {
                            "wifiScan": {
                                "devices": [{ "mac": "aa" }, { "mac": "bb" }]
                            }
                        }
                    }
                }
            ]
        });
        let events = scan_file("Timeline Edits.json", &data);
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].details["strongest_rssi"], "unknown");
    }
}
