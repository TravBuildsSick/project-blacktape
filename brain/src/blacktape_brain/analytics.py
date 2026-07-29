"""Computes aggregate statistics across chats, gps, friends, and google
signals for the analytics dashboard view.

Was a full recompute over an in-memory aligned dict; now every aggregate
is a SQL `COUNT`/`GROUP BY` against `store.py`'s tables — the database
does the aggregation instead of Python iterating every record on every
batch. `google_signals.details_json` is queried via SQLite's built-in
`json_extract` rather than deserialized and inspected in Python.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from blacktape_brain import store

def _friend_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    summary: dict[str, Any] = {
        row["category"]: row["count"]
        for row in conn.execute(
            "SELECT category, COUNT(*) AS count FROM friend_records GROUP BY category"
        ).fetchall()
    }
    summary["total_records"] = sum(summary.values())
    summary["unique_usernames"] = conn.execute(
        "SELECT COUNT(DISTINCT username) AS c FROM friend_records WHERE username IS NOT NULL AND username != ''"
    ).fetchone()["c"]
    return summary

def compute_analytics(conn: sqlite3.Connection) -> dict[str, Any]:
    total_messages = conn.execute("SELECT COUNT(*) AS c FROM chat_messages").fetchone()["c"]
    conversations = conn.execute("SELECT COUNT(DISTINCT conversation) AS c FROM chat_messages").fetchone()["c"]
    gps_points = conn.execute("SELECT COUNT(*) AS c FROM gps_points").fetchone()["c"]
    google_signals = conn.execute("SELECT COUNT(*) AS c FROM google_signals").fetchone()["c"]

    friends_summary = _friend_summary(conn)

    top_conversations = [
        {"conversation": row["conversation"], "messages": row["messages"], "last_timestamp": row["last_timestamp"]}
        for row in conn.execute(
            "SELECT conversation, COUNT(*) AS messages, MAX(timestamp) AS last_timestamp "
            "FROM chat_messages GROUP BY conversation ORDER BY messages DESC LIMIT 8"
        ).fetchall()
    ]

    gps_layers = {
        row["layer"] or "unknown": row["count"]
        for row in conn.execute(
            "SELECT layer, COUNT(*) AS count FROM gps_points GROUP BY layer"
        ).fetchall()
    }
    busiest_days = [
        {"day": row["day"], "points": row["count"]}
        for row in conn.execute(
            "SELECT substr(timestamp, 1, 10) AS day, COUNT(*) AS count FROM gps_points "
            "WHERE timestamp != '' GROUP BY day ORDER BY count DESC LIMIT 8"
        ).fetchall()
        if row["day"]
    ]

    google_signal_types = {
        row["subkind"]: row["count"]
        for row in conn.execute(
            "SELECT subkind, COUNT(*) AS count FROM google_signals WHERE subkind IS NOT NULL GROUP BY subkind"
        ).fetchall()
    }
    top_activities = [
        {"activity": row["activity"], "count": row["count"]}
        for row in conn.execute(
            "SELECT json_extract(details_json, '$.activity_type') AS activity, COUNT(*) AS count "
            "FROM google_signals WHERE activity IS NOT NULL GROUP BY activity ORDER BY count DESC LIMIT 6"
        ).fetchall()
    ]
    google_platforms = {
        row["platform"]: row["count"]
        for row in conn.execute(
            "SELECT json_extract(details_json, '$.platform') AS platform, COUNT(*) AS count "
            "FROM google_signals WHERE platform IS NOT NULL GROUP BY platform"
        ).fetchall()
    }

    return {
        "overview": {
            "messages": total_messages,
            "conversations": conversations,
            "gps_points": gps_points,
            "friend_records": friends_summary.get("total_records", 0),
            "google_signals": google_signals,
        },
        "chat": {"top_conversations": top_conversations},
        "gps": {"layers": gps_layers, "busiest_days": busiest_days},
        "friends": {"summary": friends_summary, "ranking": store.latest_ranking(conn)},
        "google": {
            "signal_types": google_signal_types,
            "top_activities": top_activities,
            "platforms": google_platforms,
        },
    }
