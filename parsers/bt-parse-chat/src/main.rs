use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use common::{read_json_files, relative_filename, walk_and_parse_json_files};
use serde::Serialize;
use serde_json::{Map, Value};

const CONTENT_INDICATORS: &[&str] = &["text", "Content", "body", "message", "data", "Media Type"];

const SENDER_NAME_KEYS: &[&str] = &["From", "sender", "senderName", "sender_name", "author"];

const TIMESTAMP_KEYS: &[&str] = &["Created", "timestamp", "Timestamp", "time", "date"];

const SENDER_FLAG_KEYS: &[&str] = &["is_sender", "IsSender", "FromMe", "isSender"];

#[derive(Serialize)]
struct ChatRecord {
    conversation: String,
    sender: String,
    text: String,

    source: String,

    timestamp: Value,
    is_sender_flag: Option<bool>,

    metadata: Value,
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

fn process_item(conv_id: &str, filename: &str, item: &Map<String, Value>) -> ChatRecord {
    let timestamp = TIMESTAMP_KEYS
        .iter()
        .find_map(|k| item.get(*k))
        .cloned()
        .unwrap_or_else(|| Value::String("1970-01-01 00:00:00".to_string()));

    let mut is_sender_flag: Option<bool> = None;
    for flag in SENDER_FLAG_KEYS {
        if let Some(v) = item.get(*flag) {
            is_sender_flag = Some(is_truthy(v));
            break;
        }
    }

    let sender = SENDER_NAME_KEYS
        .iter()
        .find_map(|k| item.get(*k).filter(|v| is_truthy(v)))
        .map(py_str)
        .unwrap_or_else(|| "Unknown".to_string());

    let text = item
        .get("Content")
        .filter(|v| is_truthy(v))
        .or_else(|| item.get("text").filter(|v| is_truthy(v)))
        .map(py_str)
        .unwrap_or_else(|| {
            let media_type = match item.get("Media Type") {
                Some(v) => py_str(v),
                None => "DATA_SIGNAL".to_string(),
            };
            format!("[{media_type}]")
        });

    ChatRecord {
        conversation: conv_id.to_string(),
        sender,
        text,
        source: filename.to_string(),
        timestamp,
        is_sender_flag,
        metadata: Value::Object(item.clone()),
    }
}

fn recursive_search(
    node: &Value,
    current_context: &str,
    filename: &str,
    results: &mut Vec<ChatRecord>,
) {
    match node {
        Value::Object(obj) => {
            if CONTENT_INDICATORS.iter().any(|k| obj.contains_key(*k)) {
                results.push(process_item(current_context, filename, obj));
            } else {
                for (key, value) in obj {
                    let new_context: &str = match value {
                        Value::Array(_) | Value::Object(_) => key.as_str(),
                        _ => current_context,
                    };
                    recursive_search(value, new_context, filename, results);
                }
            }
        }
        Value::Array(arr) => {
            for item in arr {
                recursive_search(item, current_context, filename, results);
            }
        }
        _ => {}
    }
}

fn conversation_file_messages(data: &Value) -> Option<&Vec<Value>> {
    data.get("messages").and_then(|v| v.as_array())
}

fn conversation_name(data: &Value, filename: &str) -> String {
    for key in ["threadName", "title", "name", "chatName"] {
        if let Some(Value::String(s)) = data.get(key) {
            if !s.is_empty() {
                return s.clone();
            }
        }
    }
    filename_stem(filename)
}

fn filename_stem(filename: &str) -> String {
    let base = filename.rsplit('/').next().unwrap_or(filename);
    match base.rsplit_once('.') {
        Some((stem, _ext)) => stem.to_string(),
        None => base.to_string(),
    }
}

fn scan_file(filename: &str, data: &Value) -> Vec<ChatRecord> {
    let lowered = filename.to_lowercase();

    if ["location", "gps", "points", "friends", "ranking"]
        .iter()
        .any(|s| lowered.contains(s))
    {
        return Vec::new();
    }

    if let Some(messages) = conversation_file_messages(data) {
        let conv_name = conversation_name(data, filename);
        return messages
            .iter()
            .filter_map(Value::as_object)
            .map(|obj| process_item(&conv_name, filename, obj))
            .collect();
    }

    let mut extracted = Vec::new();
    recursive_search(data, "GENERAL_SIGNAL", filename, &mut extracted);
    extracted
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let export_dir = match args.next() {
        Some(arg) => PathBuf::from(arg),
        None => {
            eprintln!("usage: bt-parse-chat <export_dir> [file...]");
            return ExitCode::FAILURE;
        }
    };

    let explicit_files: Vec<PathBuf> = args.map(PathBuf::from).collect();

    let mut all_records: Vec<ChatRecord> = Vec::new();

    let files = if explicit_files.is_empty() {
        walk_and_parse_json_files(&export_dir)
    } else {
        read_json_files(&explicit_files)
    };
    for (path, data) in files {
        let filename = relative_filename(&path, &export_dir);
        all_records.extend(scan_file(&filename, &data));
    }

    match serde_json::to_writer(std::io::stdout(), &all_records) {
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
    fn groups_conversation_file_by_thread_name_not_wrapper_key() {
        let data = json!({
            "participants": ["Johnna Rogers", "Eric Scheidker"],
            "threadName": "Eric Scheidker_19",
            "messages": [
                { "senderName": "Johnna Rogers", "text": "hi", "timestamp": 1721909487868_i64 },
                { "senderName": "Eric Scheidker", "text": "hey back", "timestamp": 1742517092927_i64 }
            ]
        });
        let records = scan_file("messages2/Eric Scheidker_19.json", &data);
        assert_eq!(records.len(), 2);
        assert_eq!(records[0].conversation, "Eric Scheidker_19");
        assert_eq!(records[1].conversation, "Eric Scheidker_19");
        assert_eq!(records[0].sender, "Johnna Rogers");
        assert_eq!(records[1].sender, "Eric Scheidker");
    }

    #[test]
    fn conversation_file_falls_back_to_filename_when_no_thread_name() {
        let data = json!({
            "participants": ["A", "B"],
            "messages": [{ "senderName": "A", "text": "hi" }]
        });
        let records = scan_file("messages2/Billy Smith_2.json", &data);
        assert_eq!(records[0].conversation, "Billy Smith_2");
    }

    #[test]
    fn different_conversation_files_stay_separate() {
        let a = json!({ "threadName": "Alice_1", "messages": [{ "senderName": "Alice", "text": "hi" }] });
        let b =
            json!({ "threadName": "Bob_2", "messages": [{ "senderName": "Bob", "text": "yo" }] });
        let records_a = scan_file("Alice_1.json", &a);
        let records_b = scan_file("Bob_2.json", &b);
        assert_ne!(records_a[0].conversation, records_b[0].conversation);
    }

    #[test]
    fn sender_name_key_is_used_when_from_and_sender_absent() {
        let data = json!({ "text": "hi", "senderName": "Eric Scheidker" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].sender, "Eric Scheidker");
    }

    #[test]
    fn skips_location_gps_and_points_files() {
        let data = json!({ "text": "hi" });
        assert!(scan_file("Location History.json", &data).is_empty());
        assert!(scan_file("gps_export.json", &data).is_empty());
        assert!(scan_file("checkin_points.json", &data).is_empty());
    }

    #[test]
    fn skips_friends_and_ranking_files() {
        let data = json!({ "Friends": [{ "data": "not a chat message" }] });
        assert!(scan_file("friends.json", &data).is_empty());
        assert!(scan_file("account_ranking.json", &data).is_empty());
    }

    #[test]
    fn finds_top_level_message_dict() {
        let data = json!({
            "text": "hello there",
            "From": "alice",
            "timestamp": "2024-01-01 00:00:00"
        });
        let records = scan_file("chat.json", &data);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].conversation, "GENERAL_SIGNAL");
        assert_eq!(records[0].sender, "alice");
        assert_eq!(records[0].text, "hello there");
        assert_eq!(records[0].timestamp, json!("2024-01-01 00:00:00"));
        assert_eq!(records[0].source, "chat.json");
    }

    #[test]
    fn threads_filename_through_nested_conversation_context() {
        let data = json!({
            "conversations": {
                "conv_42": [
                    { "text": "hey", "sender": "bob" }
                ]
            }
        });
        let records = scan_file("nested/chat_history.json", &data);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].source, "nested/chat_history.json");
    }

    #[test]
    fn uses_parent_key_as_conversation_context() {
        let data = json!({
            "conversations": {
                "conv_42": [
                    { "text": "hey", "sender": "bob" }
                ]
            }
        });
        let records = scan_file("chat.json", &data);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].conversation, "conv_42");
        assert_eq!(records[0].sender, "bob");
    }

    #[test]
    fn does_not_descend_into_a_matched_message() {
        let data = json!({
            "text": "outer message",
            "data": { "text": "should not surface separately" }
        });
        let records = scan_file("chat.json", &data);
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].text, "outer message");
    }

    #[test]
    fn sender_flag_truthy_string_is_true_python_gotcha() {
        let data = json!({ "text": "x", "FromMe": "false" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].is_sender_flag, Some(true));
    }

    #[test]
    fn sender_flag_zero_is_false() {
        let data = json!({ "text": "x", "is_sender": 0 });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].is_sender_flag, Some(false));
    }

    #[test]
    fn sender_flag_absent_is_none() {
        let data = json!({ "text": "x" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].is_sender_flag, None);
    }

    #[test]
    fn media_type_absent_key_uses_data_signal_default() {
        let data = json!({ "data": {} });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].text, "[DATA_SIGNAL]");
    }

    #[test]
    fn media_type_present_but_empty_uses_dict_get_default_quirk() {
        let data = json!({ "Media Type": "" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].text, "[]");
    }

    #[test]
    fn media_type_present_with_value() {
        let data = json!({ "Media Type": "IMAGE" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].text, "[IMAGE]");
    }

    #[test]
    fn sender_defaults_to_unknown() {
        let data = json!({ "text": "x" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].sender, "Unknown");
    }

    #[test]
    fn timestamp_falls_back_to_legacy_default_without_utc_suffix() {
        let data = json!({ "text": "x" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].timestamp, json!("1970-01-01 00:00:00"));
    }

    #[test]
    fn timestamp_preserves_original_type_not_stringified() {
        let data = json!({ "text": "x", "time": 1_700_000_000 });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].timestamp, json!(1_700_000_000));
    }

    #[test]
    fn metadata_preserves_full_original_item() {
        let data = json!({ "text": "hi", "custom_field": "kept" });
        let records = scan_file("chat.json", &data);
        assert_eq!(records[0].metadata["custom_field"], "kept");
        assert_eq!(records[0].metadata["text"], "hi");
    }
}
