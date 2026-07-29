"""Per-record timestamp normalization, shared by `store.py`'s incremental
inserts.

Ported close to verbatim from `parsers/legacy_reference/data_aligner.py`'s
timestamp normalization helper. Used to be the home of a whole-dataset
`align()` step that cross-linked every parser's raw JSON into one aligned
dict (grouping chat records by conversation, etc.) recomputed from scratch
on every ingest batch; that whole-dataset pass wasn't actually load-bearing
— every bit of it decomposed into either a per-record transform (this
module) or a query-time SQL operation (`GROUP BY conversation`, `ORDER BY
timestamp`, friend ranking's "last batch wins" becoming `ORDER BY
ingested_at DESC LIMIT 1`) — so it's gone in favor of `store.py` inserting
each record independently as its batch is parsed. See `store.py` and
`timeline.py`/`analytics.py`/`explore.py`/`search.py`/`mapview.py` for
where that logic now lives.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_TIMESTAMP = "1970-01-01 00:00:00"

EPOCH_MS_THRESHOLD = 1e11

TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M %p",
    "%d/%m/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
]

def _is_numeric(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False

def parse_timestamp(value: Any) -> str:
    """Normalize a timestamp value into a standardized 'YYYY-MM-DD HH:MM:SS'
    string.

    Supports datetime objects, common string formats, and numeric epoch
    values (int/float, or a numeric string) — bt-parse-chat's "time" field
    can carry a raw epoch int straight through from the source export
    (see ChatRecord.timestamp in bt-parse-chat/src/main.rs), and without
    epoch handling here it fell through to plain str(value) stringification,
    which sorts wrong against ISO-string timestamps from other sources
    (e.g. "1714590300" sorts before "2024-05-01 18:00:00" as a string, even
    though it's chronologically later).
    """
    if not value:
        return DEFAULT_TIMESTAMP

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if abs(value) >= EPOCH_MS_THRESHOLD else value
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except (OverflowError, OSError, ValueError):

            return str(value)

    if isinstance(value, str):
        text = value.replace(" UTC", "").replace("Z", "").strip()

        if _is_numeric(text):
            numeric = float(text)
            seconds = numeric / 1000 if abs(numeric) >= EPOCH_MS_THRESHOLD else numeric
            try:
                return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            except (OverflowError, OSError, ValueError):
                return str(value)

        for fmt in TIMESTAMP_FORMATS:
            try:
                return datetime.strptime(text, fmt).strftime("%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                continue

    return str(value)

def to_local_time(value: Any, tz: str | None = None) -> str:
    """Normalizes `value` like `parse_timestamp`, then converts it from UTC
    into `tz` (an IANA zone name, e.g. "America/New_York") or, if `tz` is
    not given, the system's configured local timezone.

    Uses `datetime.astimezone()`, which resolves DST from the target
    zone's own transition rules for the *record's own instant* rather than
    "now"'s offset — a GPS export's timestamp range commonly crosses DST
    boundaries, so a fixed now-based offset would misconvert half the
    points in it.

    Falls back to the UTC-normalized string unchanged if it isn't an
    actual parseable instant (e.g. `parse_timestamp`'s own fallback for a
    heuristic-matched non-timestamp field) — nothing to convert.
    """
    normalized = parse_timestamp(value)
    try:
        utc_dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return normalized
    target = ZoneInfo(tz) if tz else None
    return utc_dt.astimezone(target).strftime("%Y-%m-%d %H:%M:%S")
