use std::path::{Path, PathBuf};

use serde_json::Value;
use walkdir::WalkDir;

pub const MAX_WALK_DEPTH: usize = 2;

pub const MAX_JSON_FILE_BYTES: u64 = 25 * 1024 * 1024;

pub const MAX_TOTAL_JSON_BYTES: u64 = 256 * 1024 * 1024;

pub fn walk_json_files(root: &Path) -> Vec<PathBuf> {
    WalkDir::new(root)
        .max_depth(MAX_WALK_DEPTH)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|entry| entry.file_type().is_file())
        .map(|entry| entry.into_path())
        .filter(|path| path.extension().and_then(|ext| ext.to_str()) == Some("json"))
        .collect()
}

pub fn read_json_file(path: &Path) -> std::io::Result<Option<serde_json::Result<Value>>> {
    let size = std::fs::metadata(path)?.len();
    if size > MAX_JSON_FILE_BYTES {
        return Ok(None);
    }
    let contents = std::fs::read_to_string(path)?;
    Ok(Some(serde_json::from_str(&contents)))
}

fn read_and_log(path: &Path) -> Option<(PathBuf, Value)> {
    match read_json_file(path) {
        Ok(Some(Ok(data))) => Some((path.to_path_buf(), data)),
        Ok(Some(Err(err))) => {
            eprintln!("skipping {}: invalid json: {err}", path.display());
            None
        }
        Ok(None) => {
            eprintln!(
                "skipping {}: exceeds {}-byte size cap",
                path.display(),
                MAX_JSON_FILE_BYTES
            );
            None
        }
        Err(err) => {
            eprintln!("skipping {}: {err}", path.display());
            None
        }
    }
}

pub fn walk_and_parse_json_files(root: &Path) -> Vec<(PathBuf, Value)> {
    walk_and_parse_json_files_with_total_cap(root, MAX_TOTAL_JSON_BYTES)
}

fn walk_and_parse_json_files_with_total_cap(root: &Path, total_cap: u64) -> Vec<(PathBuf, Value)> {
    let mut out = Vec::new();
    let mut total_bytes: u64 = 0;
    for path in walk_json_files(root) {
        let size = match std::fs::metadata(&path) {
            Ok(meta) => meta.len(),
            Err(err) => {
                eprintln!("skipping {}: {err}", path.display());
                continue;
            }
        };
        if total_bytes.saturating_add(size) > total_cap {
            eprintln!(
                "skipping {}: would exceed {total_cap}-byte aggregate cap across matched files",
                path.display()
            );
            continue;
        }

        if let Some(parsed) = read_and_log(&path) {
            total_bytes += size;
            out.push(parsed);
        }
    }
    out
}

pub fn read_json_files(paths: &[PathBuf]) -> Vec<(PathBuf, Value)> {
    paths.iter().filter_map(|path| read_and_log(path)).collect()
}

pub fn relative_filename(path: &Path, root: &Path) -> String {
    let stripped = path.strip_prefix(root).unwrap_or(path);
    let text = if stripped.as_os_str().is_empty() {
        path.file_name().unwrap_or(path.as_os_str())
    } else {
        stripped.as_os_str()
    };
    text.to_string_lossy().replace('\\', "/")
}

pub fn normalize_timestamp(ts: &str) -> String {
    let trimmed = ts.trim();
    let has_tz_marker = trimmed.ends_with('Z')
        || trimmed.contains("UTC")
        || trimmed.contains('+')
        || trimmed.matches('-').count() > 2;

    if has_tz_marker {
        trimmed.to_string()
    } else {
        format!("{trimmed} UTC")
    }
}

pub fn coerce_number(value: &Value) -> Option<f64> {
    match value {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SourceSystem {
    Google,
    Snapchat,
    Unknown,
}

impl SourceSystem {
    pub fn as_str(&self) -> &'static str {
        match self {
            SourceSystem::Google => "google",
            SourceSystem::Snapchat => "snapchat",
            SourceSystem::Unknown => "unknown",
        }
    }
}

const SNAPCHAT_CONTENT_MARKERS: &[&str] = &[
    "Memories History",
    "Home, School & Work",
    "Frequent Locations",
    "Latest Location",
    "Daily Top Locations",
    "Businesses and places you may have visited",
    "Areas you may have visited in the last two years",
];

const GOOGLE_CONTENT_MARKERS: &[&str] = &["Home & Work", "Timeline Edits", "timelineEdits"];

pub fn detect_source_system(filename: &str, data: &Value) -> SourceSystem {
    let lowered = filename.to_lowercase();

    if lowered.contains("timeline edits") || lowered.contains("timeline_edits") {
        return SourceSystem::Google;
    }
    if lowered.contains("takeout") {
        return SourceSystem::Google;
    }

    if lowered.contains("mydata") {
        return SourceSystem::Snapchat;
    }
    if lowered.contains("chat_history") {
        return SourceSystem::Snapchat;
    }
    if lowered.contains("friends") || lowered.contains("ranking") {
        return SourceSystem::Snapchat;
    }
    if lowered.contains("snapchat/") || lowered.starts_with("snapchat") {
        return SourceSystem::Snapchat;
    }
    if lowered.contains("google/") || lowered.starts_with("google") {
        return SourceSystem::Google;
    }

    let Value::Object(obj) = data else {
        return SourceSystem::Unknown;
    };

    let keys: std::collections::HashSet<&str> = obj.keys().map(|k| k.trim()).collect();

    if SNAPCHAT_CONTENT_MARKERS.iter().any(|m| keys.contains(m)) {
        return SourceSystem::Snapchat;
    }
    if GOOGLE_CONTENT_MARKERS.iter().any(|m| keys.contains(m)) {
        return SourceSystem::Google;
    }

    SourceSystem::Unknown
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_timestamp_without_tz() {
        assert_eq!(
            normalize_timestamp("2024-03-01 12:00:00"),
            "2024-03-01 12:00:00 UTC"
        );
    }

    #[test]
    fn leaves_timestamp_with_tz_alone() {
        assert_eq!(
            normalize_timestamp("2024-03-01 12:00:00 UTC"),
            "2024-03-01 12:00:00 UTC"
        );
        assert_eq!(
            normalize_timestamp("2024-03-01T12:00:00Z"),
            "2024-03-01T12:00:00Z"
        );
    }

    #[test]
    fn coerces_string_and_number() {
        assert_eq!(coerce_number(&Value::from(1.5)), Some(1.5));
        assert_eq!(coerce_number(&Value::from("2.25")), Some(2.25));
        assert_eq!(coerce_number(&Value::from("not a number")), None);
    }

    #[test]
    fn relative_filename_strips_root_prefix() {
        assert_eq!(
            relative_filename(Path::new("/export/json/friends.json"), Path::new("/export")),
            "json/friends.json"
        );
    }

    #[test]
    fn relative_filename_falls_back_to_file_name_when_root_is_the_file_itself() {
        assert_eq!(
            relative_filename(
                Path::new("/export/json/friends.json"),
                Path::new("/export/json/friends.json")
            ),
            "friends.json"
        );
    }

    #[test]
    fn skips_files_over_the_size_cap_without_reading_them() {
        let dir = tempfile::tempdir().unwrap();
        let small = dir.path().join("small.json");
        std::fs::write(&small, b"{}").unwrap();
        assert!(matches!(
            read_json_file(&small),
            Ok(Some(Ok(Value::Object(_))))
        ));

        let big = dir.path().join("big.json");

        let file = std::fs::File::create(&big).unwrap();
        file.set_len(MAX_JSON_FILE_BYTES + 1).unwrap();
        assert!(matches!(read_json_file(&big), Ok(None)));
    }

    #[test]
    fn walk_json_files_does_not_descend_past_max_depth() {
        let dir = tempfile::tempdir().unwrap();

        std::fs::write(dir.path().join("shallow.json"), b"{}").unwrap();

        let at_max = dir.path().join("a");
        std::fs::create_dir_all(&at_max).unwrap();
        std::fs::write(at_max.join("at_max.json"), b"{}").unwrap();

        let too_deep = at_max.join("b");
        std::fs::create_dir_all(&too_deep).unwrap();
        std::fs::write(too_deep.join("too_deep.json"), b"{}").unwrap();

        let found = walk_json_files(dir.path());
        let names: std::collections::HashSet<String> = found
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().to_string())
            .collect();
        assert!(names.contains("shallow.json"));
        assert!(names.contains("at_max.json"));
        assert!(!names.contains("too_deep.json"));
    }

    #[test]
    fn walk_and_parse_json_files_skips_invalid_and_oversized_but_keeps_good_files() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("good.json"), b"{\"a\":1}").unwrap();
        std::fs::write(dir.path().join("bad.json"), b"not json").unwrap();
        let big = dir.path().join("big.json");
        let file = std::fs::File::create(&big).unwrap();
        file.set_len(MAX_JSON_FILE_BYTES + 1).unwrap();

        let results = walk_and_parse_json_files(dir.path());
        assert_eq!(results.len(), 1);
        assert_eq!(results[0].0.file_name().unwrap(), "good.json");
    }

    #[test]
    fn walk_and_parse_json_files_stops_once_aggregate_cap_reached() {
        let dir = tempfile::tempdir().unwrap();

        std::fs::write(dir.path().join("a.json"), b"{\"a\":1}").unwrap();
        std::fs::write(dir.path().join("b.json"), b"{\"b\":2}").unwrap();
        std::fs::write(dir.path().join("c.json"), b"{\"c\":3}").unwrap();

        let cap = 14;
        let results = walk_and_parse_json_files_with_total_cap(dir.path(), cap);
        assert_eq!(results.len(), 2);
    }

    #[test]
    fn read_json_files_skips_bad_entries_but_ignores_neither_walk_nor_aggregate_cap() {
        let dir = tempfile::tempdir().unwrap();
        let good = dir.path().join("good.json");
        std::fs::write(&good, b"{\"a\":1}").unwrap();
        let bad = dir.path().join("bad.json");
        std::fs::write(&bad, b"not json").unwrap();

        let outside = dir.path().join("nested").join("outside.json");
        std::fs::create_dir_all(outside.parent().unwrap()).unwrap();
        std::fs::write(&outside, b"{\"b\":2}").unwrap();

        let results = read_json_files(&[good.clone(), bad, outside.clone()]);
        let paths: Vec<&PathBuf> = results.iter().map(|(p, _)| p).collect();
        assert_eq!(paths, vec![&good, &outside]);
    }

    #[test]
    fn read_json_files_still_enforces_per_file_size_cap() {
        let dir = tempfile::tempdir().unwrap();
        let big = dir.path().join("big.json");
        let file = std::fs::File::create(&big).unwrap();
        file.set_len(MAX_JSON_FILE_BYTES + 1).unwrap();

        assert!(read_json_files(&[big]).is_empty());
    }

    #[test]
    fn detects_source_system_from_filename() {
        assert_eq!(
            detect_source_system("Google/Takeout/location_history.json", &Value::Null),
            SourceSystem::Google
        );
        assert_eq!(
            detect_source_system("chat_history.json", &Value::Null),
            SourceSystem::Snapchat
        );
        assert_eq!(
            detect_source_system("unknown_export.json", &Value::Null),
            SourceSystem::Unknown
        );
    }

    #[test]
    fn detects_real_snapchat_export_by_key_set_when_filename_is_ambiguous() {
        let data = serde_json::json!({
            "Frequent Locations": [],
            "Latest Location": {},
            "Home, School & Work": {},
            "Daily Top Locations": [],
            "Businesses and places you may have visited": [],
            "Areas you may have visited in the last two years": []
        });

        assert_eq!(
            detect_source_system("export.json", &data),
            SourceSystem::Snapchat
        );
    }

    #[test]
    fn unrecognized_filename_and_content_is_unknown() {
        let data = serde_json::json!({ "message": "looks like a chat but isn't" });
        assert_eq!(
            detect_source_system("some_other_apps_export.json", &data),
            SourceSystem::Unknown
        );
    }
}
