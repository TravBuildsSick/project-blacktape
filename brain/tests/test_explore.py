from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.explore import build_explore

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _file(conn, path="a.json", source_system="google"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", source_system, "known")

def test_build_explore_empty_db(conn) -> None:
    assert build_explore(conn) == {
        "sources": [],
        "identity": [],
        "google_signals": [],
        "other": [],
    }

def test_build_explore_identity_is_always_empty(conn) -> None:

    result = build_explore(conn)
    assert result["sources"] == []
    assert result["identity"] == []

def test_build_explore_includes_google_signals(conn) -> None:
    file_id = _file(conn, path="Timeline Edits.json")

    store.insert_google_records(
        conn,
        [{"timestamp": "2024-01-01T00:00:00Z", "subkind": "activity", "summary": "IN_VEHICLE activity detected",
          "source": "Timeline Edits.json", "details": {"activity_type": "IN_VEHICLE"}}],
        {"Timeline Edits.json": file_id}, {"Timeline Edits.json": "known"}, tz="UTC",
    )
    result = build_explore(conn)
    assert result["google_signals"] == [
        {
            "timestamp": "2024-01-01 00:00:00",
            "kind": "activity",
            "summary": "IN_VEHICLE activity detected",
            "details": {"activity_type": "IN_VEHICLE"},
            "source": "Timeline Edits.json",
            "gps_point": None,
        }
    ]

def test_build_explore_includes_only_other_layer_gps_points(conn) -> None:
    file_id = _file(conn, path="x.json", source_system="snapchat")
    store.insert_gps_records(
        conn,
        [
            {"layer": "other", "timestamp": "2024-01-01 00:00:00 UTC", "lat": 1.0, "lon": 2.0, "source": "x.json"},
            {"layer": "location_history", "timestamp": "2024-01-01 00:00:00 UTC", "lat": 1.0, "lon": 2.0, "source": "x.json"},
        ],
        {"x.json": file_id}, {"x.json": "known"},
    )
    result = build_explore(conn)
    assert len(result["other"]) == 1
    assert result["other"][0]["details"]["coordinates"] == "1.0, 2.0"

def test_build_explore_truncates_lists_to_80(conn) -> None:
    file_id = _file(conn, path="x.json", source_system="snapchat")
    store.insert_google_records(
        conn,
        [{"timestamp": "t", "subkind": "activity", "source": "x.json"} for _ in range(100)],
        {"x.json": file_id}, {"x.json": "known"},
    )
    store.insert_gps_records(
        conn,
        [{"layer": "other", "timestamp": "t", "source": "x.json"} for _ in range(100)],
        {"x.json": file_id}, {"x.json": "known"},
    )
    result = build_explore(conn)
    assert len(result["google_signals"]) == 80
    assert len(result["other"]) == 80
