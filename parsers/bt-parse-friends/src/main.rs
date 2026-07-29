use std::collections::HashMap;
use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

use common::{read_json_files, relative_filename, walk_and_parse_json_files};
use serde::Serialize;
use serde_json::{Map, Value};

const FRIEND_KEYS: &[&str] = &[
    "friends",
    "deleted friends",
    "friend requests sent",
    "blocked users",
    "hidden friend suggestions",
    "ignored snapchatters",
    "pending requests",
    "shortcuts",
];

#[derive(Serialize, Clone)]
struct FriendRecord {
    username: String,
    display_name: String,
    created: String,
    modified: String,

    source: String,
    category: String,

    file: String,
}

#[derive(Serialize)]
struct Ranking {
    snapscore: i64,
    total_friends: i64,
    following: i64,
    raw: Value,
}

#[derive(Serialize, Default)]
struct Output {
    categories: HashMap<String, Vec<FriendRecord>>,
    ranking: Option<Ranking>,
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

fn str_field(map: &Map<String, Value>, key: &str) -> String {
    match map.get(key).filter(|v| is_truthy(v)) {
        Some(v) => py_str(v).trim().to_string(),
        None => String::new(),
    }
}

fn slugify(value: &str) -> String {
    value
        .trim()
        .to_lowercase()
        .replace('&', "and")
        .replace(['/', '-'], " ")
        .replace(' ', "_")
}

fn to_int(value: Option<&Value>) -> i64 {
    match value {
        None | Some(Value::Null) => 0,
        Some(Value::String(s)) if s.trim().is_empty() => 0,
        Some(Value::String(s)) => s.trim().parse::<f64>().map(|f| f as i64).unwrap_or(0),
        Some(Value::Number(n)) => n.as_f64().map(|f| f as i64).unwrap_or(0),
        Some(_) => 0,
    }
}

fn normalize_friend(item: &Map<String, Value>, category: &str, filename: &str) -> FriendRecord {
    FriendRecord {
        username: str_field(item, "Username"),
        display_name: str_field(item, "Display Name"),
        created: str_field(item, "Creation Timestamp"),
        modified: str_field(item, "Last Modified Timestamp"),
        source: str_field(item, "Source"),
        category: slugify(category),
        file: filename.to_string(),
    }
}

fn scan_file(
    filename: &str,
    data: &Value,
) -> (HashMap<String, Vec<FriendRecord>>, Option<Ranking>) {
    let lowered_name = filename.to_lowercase();
    let mut categories: HashMap<String, Vec<FriendRecord>> = HashMap::new();
    let mut ranking = None;

    let Value::Object(obj) = data else {
        return (categories, ranking);
    };

    if lowered_name.contains("friends") {
        for (key, value) in obj {
            let normalized_key = key.trim().to_lowercase();
            if FRIEND_KEYS.contains(&normalized_key.as_str()) {
                if let Value::Array(items) = value {
                    let records: Vec<FriendRecord> = items
                        .iter()
                        .filter_map(Value::as_object)
                        .map(|item| normalize_friend(item, key, filename))
                        .collect();
                    categories.insert(slugify(key), records);
                }
            }
        }
    }

    if lowered_name.contains("ranking") {
        if let Some(Value::Object(stats)) = obj.get("Statistics") {
            ranking = Some(Ranking {
                snapscore: to_int(stats.get("Snapscore")),
                total_friends: to_int(stats.get("Your Total Friends")),
                following: to_int(stats.get("The Number of Accounts You Follow")),
                raw: Value::Object(stats.clone()),
            });
        }
    }

    (categories, ranking)
}

fn main() -> ExitCode {
    let mut args = env::args().skip(1);
    let export_dir = match args.next() {
        Some(arg) => PathBuf::from(arg),
        None => {
            eprintln!("usage: bt-parse-friends <export_dir> [file...]");
            return ExitCode::FAILURE;
        }
    };

    let explicit_files: Vec<PathBuf> = args.map(PathBuf::from).collect();

    let mut output = Output::default();

    let files = if explicit_files.is_empty() {
        walk_and_parse_json_files(&export_dir)
    } else {
        read_json_files(&explicit_files)
    };
    for (path, data) in files {
        let filename = relative_filename(&path, &export_dir);

        let (file_categories, file_ranking) = scan_file(&filename, &data);
        for (key, mut records) in file_categories {
            output
                .categories
                .entry(key)
                .or_default()
                .append(&mut records);
        }
        if let Some(r) = file_ranking {
            output.ranking = Some(r);
        }
    }

    match serde_json::to_writer(std::io::stdout(), &output) {
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
    fn extracts_friend_categories_from_friends_file() {
        let data = json!({
            "Friends": [
                {
                    "Username": "alice",
                    "Display Name": "Alice A",
                    "Creation Timestamp": "2020-01-01 00:00:00 UTC",
                    "Last Modified Timestamp": "2021-01-01 00:00:00 UTC",
                    "Source": "added_by_username"
                }
            ],
            "Blocked Users": [
                {
                    "Username": "bob",
                    "Display Name": "",
                    "Creation Timestamp": "2019-06-01 00:00:00 UTC",
                    "Last Modified Timestamp": "2019-06-01 00:00:00 UTC",
                    "Source": ""
                }
            ],
            "Not A Real Category": [
                { "Username": "should_not_appear" }
            ]
        });

        let (categories, ranking) = scan_file("friends.json", &data);
        assert!(ranking.is_none());
        assert_eq!(categories.len(), 2);

        let friends = &categories["friends"];
        assert_eq!(friends.len(), 1);
        assert_eq!(friends[0].username, "alice");
        assert_eq!(friends[0].display_name, "Alice A");
        assert_eq!(friends[0].category, "friends");

        let blocked = &categories["blocked_users"];
        assert_eq!(blocked.len(), 1);
        assert_eq!(blocked[0].username, "bob");
        assert_eq!(blocked[0].display_name, "");
        assert_eq!(blocked[0].category, "blocked_users");

        assert!(!categories.contains_key("not_a_real_category"));
    }

    #[test]
    fn extracts_ranking_from_ranking_file() {
        let data = json!({
            "Statistics": {
                "Snapscore": "12345",
                "Your Total Friends": 87,
                "The Number of Accounts You Follow": "42",
                "Some Other Field": "kept in raw"
            }
        });

        let (categories, ranking) = scan_file("ranking.json", &data);
        assert!(categories.is_empty());
        let ranking = ranking.expect("expected ranking to be present");
        assert_eq!(ranking.snapscore, 12345);
        assert_eq!(ranking.total_friends, 87);
        assert_eq!(ranking.following, 42);
        assert_eq!(ranking.raw["Some Other Field"], "kept in raw");
    }

    #[test]
    fn ignores_non_matching_filenames() {
        let data = json!({ "Friends": [{"Username": "alice"}] });
        let (categories, ranking) = scan_file("unrelated_export.json", &data);
        assert!(categories.is_empty());
        assert!(ranking.is_none());
    }

    #[test]
    fn ignores_non_object_top_level_data() {
        let data = json!(["not", "an", "object"]);
        let (categories, ranking) = scan_file("friends.json", &data);
        assert!(categories.is_empty());
        assert!(ranking.is_none());
    }

    #[test]
    fn to_int_treats_none_and_empty_string_as_zero_but_parses_zero_itself() {
        assert_eq!(to_int(None), 0);
        assert_eq!(to_int(Some(&Value::Null)), 0);
        assert_eq!(to_int(Some(&Value::String(String::new()))), 0);
        assert_eq!(to_int(Some(&json!(0))), 0);
        assert_eq!(to_int(Some(&json!("7"))), 7);
        assert_eq!(to_int(Some(&json!("not a number"))), 0);
    }

    #[test]
    fn slugify_matches_legacy_rules() {
        assert_eq!(slugify("Friend Requests Sent"), "friend_requests_sent");
        assert_eq!(slugify("Home & Work"), "home_and_work");
        assert_eq!(slugify("A/B-C"), "a_b_c");
    }
}
