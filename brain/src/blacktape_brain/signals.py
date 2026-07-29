"""Attaches each `google_signals` row (wifi scans, detected activity — the
"RSS"/RSSI wifi-strength data and activity-detection events `bt-parse-google`
extracts from Timeline Edits) to whichever `gps_points` row is closest to it
in time, so a signal that has no location of its own can still be shown in
the context of where it most likely happened.

Correctness depends on both tables sharing the same time basis: `store.
insert_gps_records` and `store.insert_google_records` both run their
timestamps through `align.to_local_time` with the same `--tz`/`tz=`
ingest-time parameter, so a signal's timestamp and a GPS point's timestamp
are directly comparable strings here — no separate UTC/local reconciliation
needed at query time.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

DEFAULT_MAX_GAP_SECONDS = 1800

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

def _parse(ts: str) -> datetime | None:
    try:
        return datetime.strptime(ts, TIMESTAMP_FORMAT)
    except (ValueError, TypeError):
        return None

def nearest_gps_point(
    conn: sqlite3.Connection, timestamp: str, max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS
) -> dict[str, Any] | None:
    """The `gps_points` row closest to `timestamp`, or `None` if there is
    no GPS data at all or the closest point is further away than
    `max_gap_seconds`. Looks at just the two neighbors immediately before
    and after `timestamp` (both indexed lookups via `idx_gps_timestamp`)
    rather than scanning every point, since the nearest point in a
    timestamp-sorted table is always one of those two.
    """
    target = _parse(timestamp)
    if target is None:
        return None

    before = conn.execute(
        "SELECT timestamp, lat, lon FROM gps_points WHERE timestamp <= ? AND timestamp != '' "
        "ORDER BY timestamp DESC LIMIT 1",
        (timestamp,),
    ).fetchone()
    after = conn.execute(
        "SELECT timestamp, lat, lon FROM gps_points WHERE timestamp >= ? AND timestamp != '' "
        "ORDER BY timestamp ASC LIMIT 1",
        (timestamp,),
    ).fetchone()

    best: dict[str, Any] | None = None
    best_gap: float | None = None
    for candidate in (before, after):
        if candidate is None:
            continue
        candidate_ts = _parse(candidate["timestamp"])
        if candidate_ts is None:
            continue
        gap = abs((candidate_ts - target).total_seconds())
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best = {
                "lat": candidate["lat"],
                "lon": candidate["lon"],
                "timestamp": candidate["timestamp"],
                "gap_seconds": gap,
            }

    if best is None or best_gap is None or best_gap > max_gap_seconds:
        return None
    return best

def attach_nearest_gps_points(
    conn: sqlite3.Connection,
    signals: list[dict[str, Any]],
    max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS,
) -> list[dict[str, Any]]:
    """Sets `gps_point` (a `nearest_gps_point` result, or `None`) on each
    dict in `signals` in place, keyed off each signal's own `timestamp`
    field, and returns `signals` for convenience. Caller-supplied dicts
    rather than a fresh query here since every current caller
    (`explore.build_explore`) already has its own shaped signal dicts to
    annotate.
    """
    for signal in signals:
        signal["gps_point"] = nearest_gps_point(conn, signal.get("timestamp") or "", max_gap_seconds)
    return signals

def query_google_signals_with_location(
    conn: sqlite3.Connection, max_gap_seconds: int = DEFAULT_MAX_GAP_SECONDS, limit: int | None = None
) -> list[dict[str, Any]]:
    """Every `google_signals` row (not just `explore.py`'s first-80
    preview), each annotated with its nearest GPS point — the `query`
    CLI subcommand backing a dedicated "signals on the map" view.
    """
    sql = "SELECT timestamp, kind, subkind, source, summary, details_json FROM google_signals ORDER BY timestamp"
    params: list[Any] = []
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    signals = []
    for row in conn.execute(sql, params).fetchall():
        signals.append(
            {
                "timestamp": row["timestamp"] or "",
                "kind": row["kind"] or "",
                "subkind": row["subkind"] or "",
                "summary": row["summary"] or "",
                "source": row["source"] or "",
                "details": json.loads(row["details_json"]) if row["details_json"] else {},
            }
        )
    return attach_nearest_gps_points(conn, signals, max_gap_seconds)
