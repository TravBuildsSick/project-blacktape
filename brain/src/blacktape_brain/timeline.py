"""Builds a unified chronological timeline across chats, gps, google signals,
and friend add/update events.

Was a full recompute over an in-memory aligned dict (see `align.py`'s
docstring); now queries `store.py`'s SQLite tables instead. Each event kind
is fetched with its own indexed-timestamp-range SQL query (`store.py`'s
`idx_*_timestamp`/`idx_chat_convo_ts` indexes), and only the results are
merged/sorted in Python — the merge is over the already-filtered rows, not
a whole-dataset scan.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

def _range_clause(column: str, since: str | None, until: str | None) -> tuple[str, list[Any]]:
    clauses = []
    params: list[Any] = []
    if since:
        clauses.append(f"{column} >= ?")
        params.append(since)
    if until:
        clauses.append(f"{column} <= ?")
        params.append(until)
    return (" AND " + " AND ".join(clauses) if clauses else "", params)

def _chat_events(conn: sqlite3.Connection, since: str | None, until: str | None) -> list[dict[str, Any]]:
    where, params = _range_clause("timestamp", since, until)
    rows = conn.execute(
        f"SELECT id, conversation, timestamp, sender, is_sender, content FROM chat_messages "
        f"WHERE timestamp != ''{where}",
        params,
    ).fetchall()

    events = []
    for row in rows:
        events.append(
            {
                "id": f"chat:{row['id']}",
                "timestamp": row["timestamp"],
                "kind": "chat",
                "label": row["conversation"],
                "summary": row["content"] or "[NO CONTENT]",
                "details": {
                    "conversation": row["conversation"],
                    "sender": row["sender"] or "Unknown",
                    "direction": "outbound" if row["is_sender"] else "inbound",
                },
            }
        )
    return events

def _gps_events(conn: sqlite3.Connection, since: str | None, until: str | None) -> list[dict[str, Any]]:
    where, params = _range_clause("timestamp", since, until)
    rows = conn.execute(
        f"SELECT id, timestamp, layer, source, lat, lon FROM gps_points "
        f"WHERE timestamp != ''{where}",
        params,
    ).fetchall()

    events = []
    for row in rows:
        events.append(
            {
                "id": f"gps:{row['id']}",
                "timestamp": row["timestamp"],
                "kind": "gps",
                "label": row["layer"] or "gps",
                "summary": row["source"] or "GPS point",
                "details": {
                    "layer": row["layer"] or "unknown",
                    "source": row["source"] or "unknown",
                    "coordinates": f"{row['lat']}, {row['lon']}",
                },
            }
        )
    return events

def _google_events(conn: sqlite3.Connection, since: str | None, until: str | None) -> list[dict[str, Any]]:
    where, params = _range_clause("timestamp", since, until)
    rows = conn.execute(
        f"SELECT id, timestamp, subkind, summary, source, details_json FROM google_signals "
        f"WHERE timestamp != ''{where}",
        params,
    ).fetchall()

    events = []
    for row in rows:
        details = (json.loads(row["details_json"]) if row["details_json"] else None) or {}
        events.append(
            {
                "id": f"google:{row['id']}",
                "timestamp": row["timestamp"],
                "kind": "google",
                "label": row["subkind"] or "google_signal",
                "summary": row["summary"] or "Google signal",
                "details": {"source": row["source"] or "unknown", **details},
            }
        )
    return events

def _friend_events(conn: sqlite3.Connection, since: str | None, until: str | None) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, category, username, display_name, created, modified, source FROM friend_records"
    ).fetchall()

    events = []
    for row in rows:
        display_name = row["display_name"] or row["username"] or "Unknown profile"
        created, modified = row["created"], row["modified"]

        if created and (not since or created >= since) and (not until or created <= until):
            events.append(
                {
                    "id": f"friend-created:{row['id']}",
                    "timestamp": created,
                    "kind": "friend",
                    "label": row["category"],
                    "summary": f"{display_name} added to {row['category']}",
                    "details": {
                        "username": row["username"] or "unknown",
                        "display_name": display_name,
                        "bucket": row["category"],
                        "source": row["source"] or "unknown",
                        "event": "created",
                    },
                }
            )
        if modified and modified != created and (not since or modified >= since) and (not until or modified <= until):
            events.append(
                {
                    "id": f"friend-modified:{row['id']}",
                    "timestamp": modified,
                    "kind": "friend",
                    "label": row["category"],
                    "summary": f"{display_name} updated in {row['category']}",
                    "details": {
                        "username": row["username"] or "unknown",
                        "display_name": display_name,
                        "bucket": row["category"],
                        "source": row["source"] or "unknown",
                        "event": "modified",
                    },
                }
            )
    return events

def build_timeline(
    conn: sqlite3.Connection,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    events = (
        _chat_events(conn, since, until)
        + _gps_events(conn, since, until)
        + _google_events(conn, since, until)
        + _friend_events(conn, since, until)
    )
    events.sort(key=lambda item: item["timestamp"])
    return events[:limit] if limit else events
