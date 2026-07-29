"""Decides whether a discovered file is recognizable Snapchat or Google
export data.

Mirrors `common::detect_source_system` (`parsers/common/src/lib.rs`):
filename checks first, falling back to top-level JSON key markers for
ambiguous filenames. Kept in sync with the Rust version by hand — if one
changes, update the other.

This is the "should this even be parsed" decision for a brain-driven
ingest run. The `bt-parse-*` binaries themselves no longer make this call
(see `parsers/common/src/lib.rs`'s doc comment on `detect_source_system`
for why that gating moved here instead of living in each of the four
Rust binaries): they just parse whatever files they're handed. `planner.
recognized_json_files` is what actually filters using this module.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

class SourceSystem(Enum):
    GOOGLE = "google"
    SNAPCHAT = "snapchat"
    UNKNOWN = "unknown"

SNAPCHAT_CONTENT_MARKERS = {
    "Memories History",
    "Home, School & Work",
    "Frequent Locations",
    "Latest Location",
    "Daily Top Locations",
    "Businesses and places you may have visited",
    "Areas you may have visited in the last two years",
}

GOOGLE_CONTENT_MARKERS = {"Home & Work", "Timeline Edits", "timelineEdits"}

def classify_file(filename: str, data: Any) -> SourceSystem:
    """Identifies whether `filename`/`data` is recognizable Snapchat or
    Google export data, filename first and falling back to top-level JSON
    key markers for ambiguous filenames.
    """
    lowered = filename.lower()

    if "timeline edits" in lowered or "timeline_edits" in lowered:
        return SourceSystem.GOOGLE
    if "takeout" in lowered:
        return SourceSystem.GOOGLE

    if "mydata" in lowered:
        return SourceSystem.SNAPCHAT
    if "chat_history" in lowered:
        return SourceSystem.SNAPCHAT
    if "friends" in lowered or "ranking" in lowered:
        return SourceSystem.SNAPCHAT
    if "snapchat/" in lowered or lowered.startswith("snapchat"):
        return SourceSystem.SNAPCHAT
    if "google/" in lowered or lowered.startswith("google"):
        return SourceSystem.GOOGLE

    if not isinstance(data, dict):
        return SourceSystem.UNKNOWN

    keys = {k.strip() for k in data.keys()}

    if keys & SNAPCHAT_CONTENT_MARKERS:
        return SourceSystem.SNAPCHAT
    if keys & GOOGLE_CONTENT_MARKERS:
        return SourceSystem.GOOGLE

    return SourceSystem.UNKNOWN
