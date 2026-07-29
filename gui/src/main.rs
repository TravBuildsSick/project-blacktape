slint::include_modules!();

mod mapview_client;
mod parsers;

use std::cell::RefCell;
use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::rc::Rc;
use std::sync::mpsc;
use std::time::Duration;

use serde_json::Value;
use slint::{Model, ModelRc, VecModel};

enum ParserMsg {
    Log(String),
    BrainBinary(PathBuf),
    DbPath(PathBuf),
    Done,
}

const NAV_LABELS: [&str; 5] = ["HOME", "GPS", "FRIENDS", "GOOGLE", "CHAT"];

fn now_stamp(start: &std::time::Instant) -> String {
    let elapsed = start.elapsed();
    format!(
        "[{:02}:{:02}:{:02}]",
        elapsed.as_secs() / 3600,
        (elapsed.as_secs() / 60) % 60,
        elapsed.as_secs() % 60
    )
}

fn record_count(value: &Value) -> usize {
    match value {
        Value::Array(a) => a.len(),
        Value::Object(o) => o.len(),
        _ => 0,
    }
}

fn render_body_for(binary: &std::path::Path, db_path: &std::path::Path, page_index: i32) -> String {
    let result = match page_index {
        0 => parsers::run_query(binary, "analytics", db_path, &[]).map(|analytics| {
            let overview = analytics.get("overview").cloned().unwrap_or_default();
            let mut out = String::new();
            if let Value::Object(fields) = overview {
                for (key, value) in fields {
                    out.push_str(&format!("{:<15} {}\n", key.to_uppercase(), value));
                }
            }
            out
        }),

        3 => parsers::run_query(binary, "signals", db_path, &[]).map(|signals| {
            format!(
                "{} google signals\n\n{}",
                record_count(&signals),
                serde_json::to_string_pretty(&signals).unwrap_or_default()
            )
        }),
        _ => Ok(String::new()),
    };

    result.unwrap_or_else(|e| format!("(query failed: {e})"))
}

fn refresh_gps_map(
    binary: &std::path::Path,
    db_path: &std::path::Path,
    mapview_handle: &mut Option<mapview_client::MapviewHandle>,
) -> String {
    let grouped = match parsers::run_query(
        binary,
        "map",
        db_path,
        &[
            "--min-lat",
            "-90",
            "--max-lat",
            "90",
            "--min-lon",
            "-180",
            "--max-lon",
            "180",
            "--group-by-day",
        ],
    ) {
        Ok(value) => value,
        Err(e) => return format!("(query failed: {e})"),
    };

    let Value::Object(days) = &grouped else {
        return "(no gps points ingested)".to_string();
    };
    let day_count = days.len();
    let point_count: usize = days
        .values()
        .map(|v| v.as_array().map_or(0, Vec::len))
        .sum();

    let payload = serde_json::to_string(&grouped).unwrap_or_else(|_| "{}".to_string());
    match mapview_client::ensure_running_and_send(mapview_handle, &payload) {
        Ok(()) => format!(
            "GPS map opened in a separate window.\n\n\
             {day_count} day(s), {point_count} point(s).\n\n\
             Use the panel on the map window's left edge to show/hide days, \
             select individual points, step through them chronologically \
             with PREV/NEXT, or draw a line connecting them in order."
        ),
        Err(e) => {
            format!("{day_count} day(s), {point_count} point(s) — failed to open map window: {e}")
        }
    }
}

fn load_conversation_boxes(
    binary: &std::path::Path,
    db_path: &std::path::Path,
) -> Vec<ConversationBox> {
    let Ok(Value::Array(rows)) = parsers::run_query(binary, "conversations", db_path, &[]) else {
        return Vec::new();
    };

    rows.iter()
        .map(|row| {
            let conversation = row
                .get("conversation")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let count = row
                .get("message_count")
                .and_then(Value::as_i64)
                .unwrap_or(0);
            let since = row
                .get("first_timestamp")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let until = row
                .get("last_timestamp")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            ConversationBox {
                label: conversation.clone().into(),
                conversation: conversation.into(),
                message_count: count as i32,
                since: since.into(),
                until: until.into(),
                expanded: false,
                loaded: false,
                messages: ModelRc::from(Rc::new(VecModel::<ChatMessageEntry>::default())),
            }
        })
        .collect()
}

fn load_friend_rows(binary: &std::path::Path, db_path: &std::path::Path) -> Vec<FriendRow> {
    let Ok(Value::Array(rows)) = parsers::run_query(binary, "friends-grouped", db_path, &[]) else {
        return Vec::new();
    };

    rows.iter()
        .map(|row| {
            let username = row.get("username").and_then(Value::as_str).unwrap_or("");
            let display_name = row
                .get("display_name")
                .and_then(Value::as_str)
                .unwrap_or("");
            let identity = if !username.is_empty() {
                if !display_name.is_empty() && display_name != username {
                    format!("{username} ({display_name})")
                } else {
                    username.to_string()
                }
            } else if !display_name.is_empty() {
                display_name.to_string()
            } else {
                "(unknown)".to_string()
            };
            let categories = row
                .get("categories")
                .and_then(Value::as_array)
                .map(|cats| {
                    cats.iter()
                        .filter_map(Value::as_str)
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .unwrap_or_default();
            FriendRow {
                identity: identity.into(),
                categories: categories.into(),
                created: row
                    .get("created")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
                modified: row
                    .get("modified")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
            }
        })
        .collect()
}

type FriendSortState = Vec<(String, bool)>;

fn apply_friend_sort(rows: &mut [FriendRow], sort_state: &FriendSortState) {
    for (field, ascending) in sort_state.iter().rev() {
        let key = |row: &FriendRow| -> String {
            match field.as_str() {
                "identity" => row.identity.to_string(),
                "categories" => row.categories.to_string(),
                "created" => row.created.to_string(),
                "modified" => row.modified.to_string(),
                _ => String::new(),
            }
        };
        rows.sort_by(|a, b| {
            let ordering = key(a).to_lowercase().cmp(&key(b).to_lowercase());
            if *ascending {
                ordering
            } else {
                ordering.reverse()
            }
        });
    }
}

fn friend_sort_badge(sort_state: &FriendSortState, field: &str) -> String {
    match sort_state.iter().position(|(f, _)| f == field) {
        Some(idx) => {
            let (_, ascending) = &sort_state[idx];
            format!(
                "{}{}",
                idx + 1,
                if *ascending { " \u{25b2}" } else { " \u{25bc}" }
            )
        }
        None => String::new(),
    }
}

fn apply_and_set_friend_sort(
    window: &AppWindow,
    rows_raw: &[FriendRow],
    sort_state: &FriendSortState,
) {
    let mut rows = rows_raw.to_vec();
    apply_friend_sort(&mut rows, sort_state);
    window.set_friend_rows(ModelRc::from(Rc::new(VecModel::from(rows))));
    window.set_friend_sort_identity_badge(friend_sort_badge(sort_state, "identity").into());
    window.set_friend_sort_categories_badge(friend_sort_badge(sort_state, "categories").into());
    window.set_friend_sort_created_badge(friend_sort_badge(sort_state, "created").into());
    window.set_friend_sort_modified_badge(friend_sort_badge(sort_state, "modified").into());
}

fn fetch_conversation_messages(
    binary: &std::path::Path,
    db_path: &std::path::Path,
    conversation: &str,
) -> Vec<ChatMessageEntry> {
    let Ok(Value::Array(rows)) = parsers::run_query(
        binary,
        "chats",
        db_path,
        &["--conversation", conversation, "--limit", "500"],
    ) else {
        return Vec::new();
    };

    rows.iter()
        .map(|row| ChatMessageEntry {
            sender: row
                .get("sender")
                .and_then(Value::as_str)
                .unwrap_or("Unknown")
                .into(),
            timestamp: row
                .get("timestamp")
                .and_then(Value::as_str)
                .unwrap_or("")
                .into(),
            content: row
                .get("content")
                .and_then(Value::as_str)
                .unwrap_or("")
                .into(),
        })
        .collect()
}

fn refresh_page(
    window: &AppWindow,
    binary: &std::path::Path,
    db_path: &std::path::Path,
    index: i32,
    conversation_model: &Rc<RefCell<Option<Rc<VecModel<ConversationBox>>>>>,
    mapview_handle: &Rc<RefCell<Option<mapview_client::MapviewHandle>>>,
    friend_rows_raw: &Rc<RefCell<Vec<FriendRow>>>,
    friend_sort_state: &Rc<RefCell<FriendSortState>>,
) {
    if index == 4 {
        let boxes = load_conversation_boxes(binary, db_path);
        let model = Rc::new(VecModel::from(boxes));
        window.set_conversation_boxes(ModelRc::from(model.clone()));
        *conversation_model.borrow_mut() = Some(model);
    } else if index == 1 {
        let body = refresh_gps_map(binary, db_path, &mut mapview_handle.borrow_mut());
        window.set_main_body_text(body.into());
    } else if index == 2 {
        let rows = load_friend_rows(binary, db_path);
        *friend_rows_raw.borrow_mut() = rows;
        apply_and_set_friend_sort(
            window,
            &friend_rows_raw.borrow(),
            &friend_sort_state.borrow(),
        );
    } else {
        let body = render_body_for(binary, db_path, index);
        window.set_main_body_text(body.into());
    }
}

fn main() -> Result<(), slint::PlatformError> {
    let window = AppWindow::new()?;

    let nav_model = Rc::new(VecModel::from(
        NAV_LABELS
            .iter()
            .map(|label| NavItem {
                label: (*label).into(),
                enabled: true,
            })
            .collect::<Vec<_>>(),
    ));
    window.set_nav_items(ModelRc::from(nav_model));

    let log_model = Rc::new(VecModel::from(vec![slint::SharedString::from(
        "[00:00:00] AWAITING INPUT",
    )]));
    window.set_log_lines(ModelRc::from(log_model.clone()));

    window.set_conversation_boxes(ModelRc::from(Rc::new(
        VecModel::<ConversationBox>::default(),
    )));
    window.set_friend_rows(ModelRc::from(Rc::new(VecModel::<FriendRow>::default())));

    let start = std::time::Instant::now();

    let brain_binary: Rc<RefCell<Option<PathBuf>>> = Rc::new(RefCell::new(None));
    let db_path: Rc<RefCell<Option<PathBuf>>> = Rc::new(RefCell::new(None));

    let conversation_model: Rc<RefCell<Option<Rc<VecModel<ConversationBox>>>>> =
        Rc::new(RefCell::new(None));

    let mapview_handle: Rc<RefCell<Option<mapview_client::MapviewHandle>>> =
        Rc::new(RefCell::new(None));

    let friend_rows_raw: Rc<RefCell<Vec<FriendRow>>> = Rc::new(RefCell::new(Vec::new()));
    let friend_sort_state: Rc<RefCell<FriendSortState>> =
        Rc::new(RefCell::new(vec![("identity".to_string(), true)]));

    {
        let window_weak = window.as_weak();
        let brain_binary = brain_binary.clone();
        let db_path = db_path.clone();
        let conversation_model = conversation_model.clone();
        let mapview_handle = mapview_handle.clone();
        let friend_rows_raw = friend_rows_raw.clone();
        let friend_sort_state = friend_sort_state.clone();
        window.on_nav_selected(move |index| {
            let window = window_weak.unwrap();
            window.set_selected_nav(index);
            window.set_page_label(NAV_LABELS[index as usize].into());

            match (brain_binary.borrow().as_ref(), db_path.borrow().as_ref()) {
                (Some(binary), Some(db)) => refresh_page(
                    &window,
                    binary,
                    db,
                    index,
                    &conversation_model,
                    &mapview_handle,
                    &friend_rows_raw,
                    &friend_sort_state,
                ),
                _ => window.set_main_body_text("(no data yet — use Insert Data)".into()),
            }
        });
    }

    {
        let window_weak = window.as_weak();
        let friend_rows_raw = friend_rows_raw.clone();
        let friend_sort_state = friend_sort_state.clone();
        window.on_friend_sort_clicked(move |field, shift| {
            let window = window_weak.unwrap();
            let field = field.to_string();
            {
                let mut state = friend_sort_state.borrow_mut();
                if shift {
                    if let Some(pos) = state.iter().position(|(f, _)| *f == field) {
                        let (f, ascending) = state.remove(pos);
                        state.push((f, !ascending));
                    } else {
                        state.push((field, true));
                    }
                } else if state.len() == 1 && state[0].0 == field {
                    state[0].1 = !state[0].1;
                } else {
                    state.clear();
                    state.push((field, true));
                }
            }
            apply_and_set_friend_sort(
                &window,
                &friend_rows_raw.borrow(),
                &friend_sort_state.borrow(),
            );
        });
    }

    {
        let brain_binary = brain_binary.clone();
        let db_path = db_path.clone();
        let conversation_model = conversation_model.clone();
        window.on_conversation_toggled(move |index| {
            let (Some(binary), Some(db)) = (
                brain_binary.borrow().as_ref().cloned(),
                db_path.borrow().as_ref().cloned(),
            ) else {
                return;
            };
            let Some(model) = conversation_model.borrow().clone() else {
                return;
            };
            let idx = index as usize;
            if idx >= model.row_count() {
                return;
            }

            let mut row = model.row_data(idx).expect("index checked above");
            if !row.expanded && !row.loaded {
                let messages = fetch_conversation_messages(&binary, &db, row.conversation.as_str());
                row.messages = ModelRc::from(Rc::new(VecModel::from(messages)));
                row.loaded = true;
            }
            row.expanded = !row.expanded;
            model.set_row_data(idx, row);
        });
    }

    let active_timer: Rc<RefCell<Option<slint::Timer>>> = Rc::new(RefCell::new(None));
    {
        let window_weak = window.as_weak();
        let log_model = log_model.clone();
        let active_timer = active_timer.clone();
        let brain_binary = brain_binary.clone();
        let db_path = db_path.clone();
        let conversation_model = conversation_model.clone();
        let mapview_handle = mapview_handle.clone();
        let friend_rows_raw = friend_rows_raw.clone();
        let friend_sort_state = friend_sort_state.clone();
        window.on_insert_data(move || {
            let window = window_weak.unwrap();
            if window.get_busy() {
                return;
            }

            let Some(dir) = rfd::FileDialog::new()
                .set_title("Select an export directory")
                .pick_folder()
            else {
                return;
            };

            window.set_busy(true);
            log_model.push(format!("{} SELECTED {}", now_stamp(&start), dir.display()).into());

            let (tx, rx) = mpsc::channel::<ParserMsg>();
            let thread_start = start;
            std::thread::spawn(move || {
                let binary = match parsers::find_brain_binary() {
                    Ok(path) => path,
                    Err(e) => {
                        let stamp = now_stamp(&thread_start);
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN NOT FOUND: {e}"
                        )));
                        let _ = tx.send(ParserMsg::Done);
                        return;
                    }
                };
                let _ = tx.send(ParserMsg::BrainBinary(binary.clone()));

                let mut child = match parsers::spawn_ingest_stream(&binary, &dir) {
                    Ok(child) => child,
                    Err(e) => {
                        let stamp = now_stamp(&thread_start);
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN FAILED TO START: {e}"
                        )));
                        let _ = tx.send(ParserMsg::Done);
                        return;
                    }
                };

                let stdout = child.stdout.take().expect("piped stdout");
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else { break };
                    if line.trim().is_empty() {
                        continue;
                    }

                    let stamp = now_stamp(&thread_start);
                    match serde_json::from_str::<Value>(&line) {
                        Ok(event) => {
                            let phase = event.get("phase").and_then(Value::as_i64).unwrap_or(0);
                            let phases = event.get("phases").and_then(Value::as_i64).unwrap_or(1);
                            let files_in_batch = event
                                .get("files_in_batch")
                                .and_then(Value::as_i64)
                                .unwrap_or(0);
                            let _ = tx.send(ParserMsg::Log(format!(
                                "{stamp} BATCH {}/{phases} ({files_in_batch} files)",
                                phase + 1
                            )));
                            if let Some(totals) = event.get("totals") {
                                let _ = tx.send(ParserMsg::Log(format!("{stamp} TOTALS {totals}")));
                            }
                            if let Some(db) = event.get("db_path").and_then(Value::as_str) {
                                let _ = tx.send(ParserMsg::DbPath(PathBuf::from(db)));
                            }
                        }
                        Err(e) => {
                            let _ = tx.send(ParserMsg::Log(format!("{stamp} BAD EVENT: {e}")));
                        }
                    }
                }

                let stamp = now_stamp(&thread_start);
                match child.wait() {
                    Ok(status) if !status.success() => {
                        let mut stderr_text = String::new();
                        if let Some(mut err) = child.stderr.take() {
                            let _ = err.read_to_string(&mut stderr_text);
                        }
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN FAILED ({status}): {stderr_text}"
                        )));
                    }
                    Err(e) => {
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN WAIT FAILED: {e}"
                        )));
                    }
                    _ => {}
                }
                let _ = tx.send(ParserMsg::Done);
            });

            let timer = slint::Timer::default();
            let timer_handle = active_timer.clone();
            let window_weak2 = window_weak.clone();
            let log_model2 = log_model.clone();
            let brain_binary2 = brain_binary.clone();
            let db_path2 = db_path.clone();
            let conversation_model2 = conversation_model.clone();
            let mapview_handle2 = mapview_handle.clone();
            let friend_rows_raw2 = friend_rows_raw.clone();
            let friend_sort_state2 = friend_sort_state.clone();
            timer.start(
                slint::TimerMode::Repeated,
                Duration::from_millis(30),
                move || match rx.try_recv() {
                    Ok(ParserMsg::Log(line)) => {
                        log_model2.push(line.into());
                    }
                    Ok(ParserMsg::BrainBinary(binary)) => {
                        *brain_binary2.borrow_mut() = Some(binary);
                    }
                    Ok(ParserMsg::DbPath(path)) => {
                        *db_path2.borrow_mut() = Some(path.clone());

                        let window = window_weak2.unwrap();
                        if let Some(binary) = brain_binary2.borrow().as_ref() {
                            let index = window.get_selected_nav();
                            refresh_page(
                                &window,
                                binary,
                                &path,
                                index,
                                &conversation_model2,
                                &mapview_handle2,
                                &friend_rows_raw2,
                                &friend_sort_state2,
                            );
                        }
                    }
                    Ok(ParserMsg::Done) => {
                        let window = window_weak2.unwrap();
                        window.set_busy(false);
                        window.set_has_data(true);
                        *timer_handle.borrow_mut() = None;
                    }
                    Err(mpsc::TryRecvError::Empty) => {}
                    Err(mpsc::TryRecvError::Disconnected) => {
                        let window = window_weak2.unwrap();
                        window.set_busy(false);
                        *timer_handle.borrow_mut() = None;
                    }
                },
            );
            *active_timer.borrow_mut() = Some(timer);
        });
    }

    {
        let window_weak = window.as_weak();
        let log_model = log_model.clone();
        let active_timer = active_timer.clone();
        let brain_binary = brain_binary.clone();
        let db_path = db_path.clone();
        let conversation_model = conversation_model.clone();
        let mapview_handle = mapview_handle.clone();
        let friend_rows_raw = friend_rows_raw.clone();
        let friend_sort_state = friend_sort_state.clone();
        window.on_insert_files(move || {
            let window = window_weak.unwrap();
            if window.get_busy() {
                return;
            }

            let Some(files) = rfd::FileDialog::new()
                .set_title("Select one or more export files")
                .add_filter("JSON", &["json"])
                .pick_files()
            else {
                return;
            };

            window.set_busy(true);
            log_model
                .push(format!("{} SELECTED {} file(s)", now_stamp(&start), files.len()).into());

            let (tx, rx) = mpsc::channel::<ParserMsg>();
            let thread_start = start;
            std::thread::spawn(move || {
                let binary = match parsers::find_brain_binary() {
                    Ok(path) => path,
                    Err(e) => {
                        let stamp = now_stamp(&thread_start);
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN NOT FOUND: {e}"
                        )));
                        let _ = tx.send(ParserMsg::Done);
                        return;
                    }
                };
                let _ = tx.send(ParserMsg::BrainBinary(binary.clone()));

                let mut child = match parsers::spawn_ingest_stream_files(&binary, &files) {
                    Ok(child) => child,
                    Err(e) => {
                        let stamp = now_stamp(&thread_start);
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN FAILED TO START: {e}"
                        )));
                        let _ = tx.send(ParserMsg::Done);
                        return;
                    }
                };

                let stdout = child.stdout.take().expect("piped stdout");
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else { break };
                    if line.trim().is_empty() {
                        continue;
                    }

                    let stamp = now_stamp(&thread_start);
                    match serde_json::from_str::<Value>(&line) {
                        Ok(event) => {
                            let phase = event.get("phase").and_then(Value::as_i64).unwrap_or(0);
                            let phases = event.get("phases").and_then(Value::as_i64).unwrap_or(1);
                            let files_in_batch = event
                                .get("files_in_batch")
                                .and_then(Value::as_i64)
                                .unwrap_or(0);
                            let _ = tx.send(ParserMsg::Log(format!(
                                "{stamp} BATCH {}/{phases} ({files_in_batch} files)",
                                phase + 1
                            )));
                            if let Some(totals) = event.get("totals") {
                                let _ = tx.send(ParserMsg::Log(format!("{stamp} TOTALS {totals}")));
                            }
                            if let Some(db) = event.get("db_path").and_then(Value::as_str) {
                                let _ = tx.send(ParserMsg::DbPath(PathBuf::from(db)));
                            }
                        }
                        Err(e) => {
                            let _ = tx.send(ParserMsg::Log(format!("{stamp} BAD EVENT: {e}")));
                        }
                    }
                }

                let stamp = now_stamp(&thread_start);
                match child.wait() {
                    Ok(status) if !status.success() => {
                        let mut stderr_text = String::new();
                        if let Some(mut err) = child.stderr.take() {
                            let _ = err.read_to_string(&mut stderr_text);
                        }
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN FAILED ({status}): {stderr_text}"
                        )));
                    }
                    Err(e) => {
                        let _ = tx.send(ParserMsg::Log(format!(
                            "{stamp} BLACKTAPE-BRAIN WAIT FAILED: {e}"
                        )));
                    }
                    _ => {}
                }
                let _ = tx.send(ParserMsg::Done);
            });

            let timer = slint::Timer::default();
            let timer_handle = active_timer.clone();
            let window_weak2 = window_weak.clone();
            let log_model2 = log_model.clone();
            let brain_binary2 = brain_binary.clone();
            let db_path2 = db_path.clone();
            let conversation_model2 = conversation_model.clone();
            let mapview_handle2 = mapview_handle.clone();
            let friend_rows_raw2 = friend_rows_raw.clone();
            let friend_sort_state2 = friend_sort_state.clone();
            timer.start(
                slint::TimerMode::Repeated,
                Duration::from_millis(30),
                move || match rx.try_recv() {
                    Ok(ParserMsg::Log(line)) => {
                        log_model2.push(line.into());
                    }
                    Ok(ParserMsg::BrainBinary(binary)) => {
                        *brain_binary2.borrow_mut() = Some(binary);
                    }
                    Ok(ParserMsg::DbPath(path)) => {
                        *db_path2.borrow_mut() = Some(path.clone());
                        let window = window_weak2.unwrap();
                        if let Some(binary) = brain_binary2.borrow().as_ref() {
                            let index = window.get_selected_nav();
                            refresh_page(
                                &window,
                                binary,
                                &path,
                                index,
                                &conversation_model2,
                                &mapview_handle2,
                                &friend_rows_raw2,
                                &friend_sort_state2,
                            );
                        }
                    }
                    Ok(ParserMsg::Done) => {
                        let window = window_weak2.unwrap();
                        window.set_busy(false);
                        window.set_has_data(true);
                        *timer_handle.borrow_mut() = None;
                    }
                    Err(mpsc::TryRecvError::Empty) => {}
                    Err(mpsc::TryRecvError::Disconnected) => {
                        let window = window_weak2.unwrap();
                        window.set_busy(false);
                        *timer_handle.borrow_mut() = None;
                    }
                },
            );
            *active_timer.borrow_mut() = Some(timer);
        });
    }

    {
        let window_weak = window.as_weak();
        let log_model = log_model.clone();
        let brain_binary = brain_binary.clone();
        let db_path = db_path.clone();
        let conversation_model = conversation_model.clone();
        let friend_rows_raw = friend_rows_raw.clone();
        window.on_purge_data(move || {
            let window = window_weak.unwrap();
            if window.get_busy() || !window.get_has_data() {
                return;
            }

            let Some(binary) = brain_binary.borrow().clone() else {
                return;
            };
            let Some(path) = db_path.borrow().clone() else {
                return;
            };

            window.set_busy(true);
            let stamp = now_stamp(&start);
            match parsers::run_purge(&binary, &path) {
                Ok(message) => log_model.push(format!("{stamp} PURGED {message}").into()),
                Err(e) => log_model.push(format!("{stamp} PURGE FAILED: {e}").into()),
            }

            *db_path.borrow_mut() = None;
            *conversation_model.borrow_mut() = None;
            friend_rows_raw.borrow_mut().clear();
            window.set_conversation_boxes(ModelRc::from(Rc::new(
                VecModel::<ConversationBox>::default(),
            )));
            window.set_friend_rows(ModelRc::from(Rc::new(VecModel::<FriendRow>::default())));
            window.set_main_body_text("".into());
            window.set_has_data(false);
            window.set_busy(false);
        });
    }

    window.run()
}
