"""Builds the "explore" view: identity markers, google signals, and
unclassified ("other" layer) gps points, for ad-hoc browsing.

Now queries `store.py`'s SQLite tables instead of an in-memory aligned
dict. `identity`/`sources` are still always empty — there's no `files`-
backed identity table because the legacy `GenericScanner` that would have
populated one was never ported to a `bt-parse-*` binary (see align.py's
former docstring, now migrated to this note).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from blacktape_brain.signals import attach_nearest_gps_points

EXPLORE_LIMIT = 80

def build_explore(conn: sqlite3.Connection) -> dict[str, Any]:
    google_signals = []
    for row in conn.execute(
        "SELECT timestamp, subkind, summary, source, details_json FROM google_signals "
        "ORDER BY id LIMIT ?",
        (EXPLORE_LIMIT,),
    ).fetchall():
        google_signals.append(
            {
                "timestamp": row["timestamp"] or "",
                "kind": row["subkind"] or "google_signal",
                "summary": row["summary"] or "Google signal",
                "details": (json.loads(row["details_json"]) if row["details_json"] else None) or {},
                "source": row["source"] or "",
            }
        )

    attach_nearest_gps_points(conn, google_signals)

    other_records = []
    for row in conn.execute(
        "SELECT timestamp, lat, lon, source, source_system FROM gps_points "
        "WHERE layer = 'other' ORDER BY id LIMIT ?",
        (EXPLORE_LIMIT,),
    ).fetchall():
        other_records.append(
            {
                "timestamp": row["timestamp"] or "",
                "type": "map_other",
                "summary": row["source"] or "Unclassified map point",
                "details": {
                    "coordinates": f"{row['lat']}, {row['lon']}",
                    "source_system": row["source_system"] or "unknown",
                },
            }
        )

    return {
        "sources": [],
        "identity": [],
        "google_signals": google_signals,
        "other": other_records,
    }
