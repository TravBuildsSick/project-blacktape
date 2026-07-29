"""SQLite-backed incremental storage for ingested export data.

Replaces the old model (`cli.stream_events` accumulating a `raw` dict and
re-running `align`/`timeline`/`analytics`/`explore` over the whole thing on
every batch). Records are inserted per batch as they're parsed; the view
layer (`timeline.py`, `analytics.py`, `explore.py`, `search.py`,
`mapview.py`) queries this database instead of recomputing over an
in-memory structure.

Indexed columns are kept thin (timestamp, lat/lon, source/source_system,
a file pointer) so filtering/scanning doesn't have to touch heavier
columns (`raw_json`/`metadata`/`details_json`, full chat `content`) unless
a caller actually asks for them.

`source_confidence` is `'known'` for records extracted from a file
`classify.classify_file` recognized as Google/Snapchat, `'inferred'` for
records `bt-parse-gps`/`bt-parse-chat` extracted from an unrecognized
file via their source-agnostic heuristics (lat/lon hunt, message-shaped
recursive search). It's denormalized onto every record table (copied from
the owning `files` row at insert time) so a map/timeline query can filter
on confidence without a join.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    ingested_at TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_confidence TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gps_points (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    timestamp TEXT NOT NULL,
    lat REAL,
    lon REAL,
    layer TEXT,
    source TEXT,
    source_system TEXT,
    source_confidence TEXT NOT NULL,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_gps_timestamp ON gps_points(timestamp);
CREATE INDEX IF NOT EXISTS idx_gps_bbox ON gps_points(lat, lon);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    conversation TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    sender TEXT,
    is_sender INTEGER,
    source_confidence TEXT NOT NULL,
    content TEXT,
    raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_chat_convo_ts ON chat_messages(conversation, timestamp);
CREATE INDEX IF NOT EXISTS idx_chat_timestamp ON chat_messages(timestamp);

CREATE TABLE IF NOT EXISTS google_signals (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    timestamp TEXT NOT NULL,
    kind TEXT,
    subkind TEXT,
    source TEXT,
    summary TEXT,
    source_confidence TEXT NOT NULL,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_google_timestamp ON google_signals(timestamp);

CREATE TABLE IF NOT EXISTS friend_records (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    category TEXT NOT NULL,
    username TEXT,
    display_name TEXT,
    created TEXT,
    modified TEXT,
    source TEXT,
    source_confidence TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_friend_category ON friend_records(category);
CREATE INDEX IF NOT EXISTS idx_friend_username ON friend_records(username);

CREATE TABLE IF NOT EXISTS friend_ranking (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id),
    ingested_at TEXT NOT NULL,
    snapscore INTEGER,
    total_friends INTEGER,
    following INTEGER,
    raw_json TEXT
);
"""

def connect(db_path: Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn

def register_file(
    conn: sqlite3.Connection,
    path: str,
    size_bytes: int,
    ingested_at: str,
    source_system: str,
    source_confidence: str,
) -> int:
    """Inserts one `files` row and returns its id. Called once per file per
    batch (not per record) — `insert_*_batch` below look up the resulting
    id via each record's own filename field.
    """
    cursor = conn.execute(
        "INSERT INTO files (path, size_bytes, ingested_at, source_system, source_confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, size_bytes, ingested_at, source_system, source_confidence),
    )
    return int(cursor.lastrowid)

def _file_id(file_ids: dict[str, int], filename: str) -> int | None:
    return file_ids.get(filename)

def insert_gps_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    file_ids: dict[str, int],
    confidence_by_file: dict[str, str],
    tz: str | None = None,
) -> int:
    """`tz` is an IANA zone name (e.g. "America/New_York") to convert each
    point's timestamp into at insert time; `None` (the default) means the
    system's own configured local timezone. Converting here, once, at
    ingest — rather than at query/render time — is what lets
    `mapview`'s day-bucketing (and `analytics.busiest_days`, which already
    grouped by `substr(timestamp, 1, 10)`) group by the day the point
    actually happened in the user's own timezone, not the UTC day the
    source export recorded it in.
    """
    import json

    from blacktape_brain.align import to_local_time

    rows = []
    for record in records:
        filename = record.get("source") or ""
        file_id = _file_id(file_ids, filename)
        if file_id is None:
            continue
        rows.append(
            (
                file_id,
                to_local_time(record.get("timestamp"), tz) if record.get("timestamp") else "",
                record.get("lat"),
                record.get("lon"),
                record.get("layer"),
                filename,
                record.get("source_system"),
                confidence_by_file.get(filename, "inferred"),
                json.dumps(record),
            )
        )
    conn.executemany(
        "INSERT INTO gps_points "
        "(file_id, timestamp, lat, lon, layer, source, source_system, source_confidence, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def insert_chat_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    file_ids: dict[str, int],
    confidence_by_file: dict[str, str],
) -> int:
    import json

    from blacktape_brain.align import parse_timestamp

    rows = []
    for record in records:
        filename = record.get("source") or ""
        file_id = _file_id(file_ids, filename)
        if file_id is None:
            continue

        flag = record.get("is_sender_flag")
        is_sender = None if flag is None else (1 if flag else 0)
        rows.append(
            (
                file_id,
                record.get("conversation") or "GENERAL_SIGNAL",
                parse_timestamp(record.get("timestamp")),
                record.get("sender") or "Unknown",
                is_sender,
                confidence_by_file.get(filename, "inferred"),
                record.get("text") or "",
                json.dumps(record.get("metadata")),
            )
        )
    conn.executemany(
        "INSERT INTO chat_messages "
        "(file_id, conversation, timestamp, sender, is_sender, source_confidence, content, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def infer_and_backfill_direction(conn: sqlite3.Connection) -> None:
    """Fills in `is_sender` for rows where the source export had no
    explicit from-me flag (`is_sender` was left `NULL` by
    `insert_chat_records` above) — e.g. a real Facebook Messenger export,
    where each conversation file only has `senderName`, never an explicit
    "is this mine" marker.

    Heuristic: in a set of 1:1 conversation exports, the account owner is
    the one sender who shows up across *multiple* distinct conversations
    (each conversation's other participant is a different friend, but the
    exporting user is a party to all of them). A sender who only ever
    appears in a single conversation is presumed to be that conversation's
    other party, not the account owner.

    Safe to call repeatedly (e.g. once per ingested batch): only touches
    rows still `NULL`, and only reads from `chat_messages` itself, so
    re-running it as more conversations are ingested just refines the
    guess — it never un-fills a row already resolved (explicitly, or by
    an earlier, less-informed run of this same backfill).
    """
    primary_sender_row = conn.execute(
        "SELECT sender FROM chat_messages "
        "WHERE sender IS NOT NULL AND sender != '' AND sender != 'Unknown' "
        "GROUP BY sender HAVING COUNT(DISTINCT conversation) > 1 "
        "ORDER BY COUNT(DISTINCT conversation) DESC LIMIT 1"
    ).fetchone()
    if primary_sender_row is None:
        return
    primary_sender = primary_sender_row["sender"]

    conn.execute(
        "UPDATE chat_messages SET is_sender = 1 WHERE is_sender IS NULL AND sender = ?",
        (primary_sender,),
    )
    conn.execute(
        "UPDATE chat_messages SET is_sender = 0 "
        "WHERE is_sender IS NULL AND sender IS NOT NULL AND sender != '' AND sender != ?",
        (primary_sender,),
    )

def insert_google_records(
    conn: sqlite3.Connection,
    records: Iterable[dict[str, Any]],
    file_ids: dict[str, int],
    confidence_by_file: dict[str, str],
    tz: str | None = None,
) -> int:
    """`tz` matches `insert_gps_records`'s own `--tz`/`tz=` — converting
    both tables' timestamps into the same local-time basis at insert time
    is what makes `signals.nearest_gps_point`'s time-proximity matching
    meaningful; comparing a UTC-ish raw Google signal timestamp against an
    already-local-converted GPS timestamp would be off by the local
    offset (and wrong across a DST boundary), silently pairing signals
    with the wrong point.
    """
    import json

    from blacktape_brain.align import to_local_time

    rows = []
    for record in records:
        filename = record.get("source") or ""
        file_id = _file_id(file_ids, filename)
        if file_id is None:
            continue
        rows.append(
            (
                file_id,
                to_local_time(record.get("timestamp"), tz) if record.get("timestamp") else "",
                record.get("kind"),
                record.get("subkind"),
                filename,
                record.get("summary"),
                confidence_by_file.get(filename, "inferred"),
                json.dumps(record.get("details")),
            )
        )
    conn.executemany(
        "INSERT INTO google_signals "
        "(file_id, timestamp, kind, subkind, source, summary, source_confidence, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def insert_friend_records(
    conn: sqlite3.Connection,
    categories: dict[str, list[dict[str, Any]]],
    file_ids: dict[str, int],
    confidence_by_file: dict[str, str],
) -> int:
    rows = []
    for category, entries in categories.items():
        for record in entries:

            filename = record.get("file") or ""
            file_id = _file_id(file_ids, filename)
            if file_id is None:
                continue
            rows.append(
                (
                    file_id,
                    category,
                    record.get("username"),
                    record.get("display_name"),
                    record.get("created"),
                    record.get("modified"),
                    filename,
                    confidence_by_file.get(filename, "inferred"),
                )
            )
    conn.executemany(
        "INSERT INTO friend_records "
        "(file_id, category, username, display_name, created, modified, source, source_confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)

def upsert_ranking(
    conn: sqlite3.Connection,
    ranking: dict[str, Any] | None,
    file_id: int,
    ingested_at: str,
) -> None:
    """Inserts a new `friend_ranking` row for this batch. "Last batch wins"
    is a query-time concern (`ORDER BY ingested_at DESC LIMIT 1`), matching
    the old `_merge_raw` behavior and the Rust binary's own "last file
    wins" self-walk behavior — not an actual SQL UPSERT.
    """
    import json

    if ranking is None:
        return
    conn.execute(
        "INSERT INTO friend_ranking (file_id, ingested_at, snapscore, total_friends, following, raw_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            file_id,
            ingested_at,
            ranking.get("snapscore"),
            ranking.get("total_friends"),
            ranking.get("following"),
            json.dumps(ranking.get("raw")),
        ),
    )

def list_friend_records(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """Raw `friend_records` listing (category/username/display_name/
    created/modified), for callers that want the records themselves
    rather than `analytics.py`'s aggregate summary.
    """
    sql = "SELECT category, username, display_name, created, modified, source FROM friend_records ORDER BY category, username"
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]

def list_friends_grouped(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """One row per friend identity instead of `list_friend_records`'s flat
    `(category, username)` rows — a person who's in both `friends` and
    `blocked_users` (for example) currently shows up as two disconnected
    rows; this groups them into one, with `categories` a list of every
    category they appear under.

    Identity is `username` when present, falling back to `display_name`
    for the rare record with no username. `MAX(display_name)` is an MVP
    simplification for picking a representative display name within a
    group — good enough since a given username's rows normally agree on
    display name anyway; revisit if real export data ever shows that
    assumption breaking.
    """
    sql = (
        "SELECT "
        "  CASE WHEN username IS NOT NULL AND username != '' THEN username ELSE display_name END AS identity, "
        "  MAX(username) AS username, "
        "  MAX(display_name) AS display_name, "
        "  GROUP_CONCAT(DISTINCT category) AS categories, "
        "  MIN(created) AS created, "
        "  MAX(modified) AS modified "
        "FROM friend_records "
        "GROUP BY identity "
        "ORDER BY identity COLLATE NOCASE"
    )
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    rows = []
    for row in conn.execute(sql, params).fetchall():
        record = dict(row)
        categories = record.pop("categories") or ""
        record["categories"] = sorted(categories.split(",")) if categories else []
        rows.append(record)
    return rows

def list_conversations(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    """One row per distinct `conversation`, with just enough to render a
    collapsed summary box: message count and the timestamp range. Doesn't
    touch `content`/`raw_json` at all — mirrors `mapview.query_bbox` vs.
    `point_detail`'s summary-then-detail split, here for chat boxes that
    stay collapsed until a caller (the GUI) asks for one conversation's
    actual messages via `list_chat_messages(conn, conversation=...)`.
    """
    sql = (
        "SELECT conversation, COUNT(*) AS message_count, "
        "MIN(timestamp) AS first_timestamp, MAX(timestamp) AS last_timestamp "
        "FROM chat_messages GROUP BY conversation ORDER BY MAX(timestamp) DESC"
    )
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]

def list_chat_messages(
    conn: sqlite3.Connection,
    conversation: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Raw `chat_messages` listing (not the heavier `raw_json`/`metadata`
    column), for callers that want the messages themselves rather than
    `search.py`'s filtered results. `conversation`, when given, narrows
    this to one conversation's messages — the query a GUI box's expand
    click fires, as opposed to `list_conversations` above (the query its
    collapsed state fires).
    """
    sql = "SELECT conversation, timestamp, sender, is_sender, content FROM chat_messages"
    params: list[Any] = []
    if conversation is not None:
        sql += " WHERE conversation = ?"
        params.append(conversation)
    sql += " ORDER BY timestamp"
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]

def latest_ranking(conn: sqlite3.Connection) -> dict[str, Any]:
    import json

    row = conn.execute(
        "SELECT snapscore, total_friends, following, raw_json FROM friend_ranking "
        "ORDER BY ingested_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return {}
    return {
        "snapscore": row["snapscore"],
        "total_friends": row["total_friends"],
        "following": row["following"],
        "raw": json.loads(row["raw_json"]) if row["raw_json"] else {},
    }
