"""Map view: bounding-box queries over `store.py`'s `gps_points` table.

Filters on the plain indexed `lat`/`lon` columns (`idx_gps_bbox`) rather
than a spatial index (SpatiaLite/R-tree) — personal-export-scale point
counts don't need one; a bbox scan over an indexed pair of columns is
cheap enough. Revisit only if a real export turns out to have enough
points that this becomes the bottleneck.

`raw_json` (the full original record) is deliberately not selected by
`query_bbox` — it's fetched lazily via `point_detail` only when a caller
actually needs it (e.g. the user clicks a point in the GUI), so a map
scan over many points doesn't have to pull the heavy column for all of
them.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

def query_bbox(
    conn: sqlite3.Connection,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    since: str | None = None,
    until: str | None = None,
    confidence: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    clauses = ["lat BETWEEN ? AND ?", "lon BETWEEN ? AND ?"]
    params: list[Any] = [min_lat, max_lat, min_lon, max_lon]

    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("timestamp <= ?")
        params.append(until)
    if confidence:
        clauses.append("source_confidence = ?")
        params.append(confidence)

    sql = (
        "SELECT id, timestamp, lat, lon, layer, source, source_system, source_confidence "
        f"FROM gps_points WHERE {' AND '.join(clauses)} ORDER BY timestamp"
    )
    if limit:
        sql += " LIMIT ?"
        params.append(limit)

    return [dict(row) for row in conn.execute(sql, params).fetchall()]

def query_bbox_by_day(
    conn: sqlite3.Connection,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    since: str | None = None,
    until: str | None = None,
    confidence: str | None = None,
    limit: int | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Same filters as `query_bbox`, bucketed into `{day: [points]}` — a
    map view that wants to render/scrub one day at a time (rather than one
    giant pin cluster) groups on this instead of the flat list. `day` is
    the first 10 characters of each point's `timestamp`
    ("YYYY-MM-DD"), which — since `store.insert_gps_records` converts
    every GPS timestamp into the user's local timezone at ingest time
    (see `align.to_local_time`) — is the day the point actually happened
    in, not the UTC day the source export recorded it in.

    `query_bbox` already orders by `timestamp` ascending, so day keys come
    out of `dict` insertion order chronologically (oldest day first) with
    no separate sort here, and each day's own point list stays
    chronological too.
    """
    points = query_bbox(
        conn, min_lat, max_lat, min_lon, max_lon, since=since, until=until, confidence=confidence, limit=limit
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for point in points:
        day = point["timestamp"][:10]
        grouped.setdefault(day, []).append(point)
    return grouped

def point_detail(conn: sqlite3.Connection, point_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, timestamp, lat, lon, layer, source, source_system, source_confidence, raw_json "
        "FROM gps_points WHERE id = ?",
        (point_id,),
    ).fetchone()
    if row is None:
        return None
    result = dict(row)
    result["raw"] = json.loads(result.pop("raw_json")) if result.get("raw_json") else {}
    return result
