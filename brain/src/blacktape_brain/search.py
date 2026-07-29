"""Case-insensitive substring search over chat message content.

Now a SQL `LIKE ... COLLATE NOCASE` query against `store.py`'s
`chat_messages` table instead of a Python scan over an in-memory aligned
dict. Plain `LIKE` rather than SQLite FTS5: personal-export-scale chat
history doesn't need a specialized text index, matching the same
scale assumption behind `mapview.py`'s plain bbox query over SpatiaLite.
"""
from __future__ import annotations

import sqlite3
from typing import Any

def search(conn: sqlite3.Connection, query: str, limit: int | None = None) -> list[dict[str, Any]]:
    if not query:
        return []

    sql = (
        "SELECT conversation, timestamp, content, sender, is_sender FROM chat_messages "
        "WHERE content LIKE ? ESCAPE '\\' COLLATE NOCASE ORDER BY timestamp"
    )
    params: list[Any] = [f"%{_escape_like(query)}%"]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    return [
        {
            "conversation": row["conversation"],
            "timestamp": row["timestamp"] or "",
            "content": row["content"] or "",
            "sender": row["sender"] or "Unknown",
            "is_sender": bool(row["is_sender"]),
        }
        for row in conn.execute(sql, params).fetchall()
    ]

def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
