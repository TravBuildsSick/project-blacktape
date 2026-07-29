from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.signals import (
    attach_nearest_gps_points,
    nearest_gps_point,
    query_google_signals_with_location,
)

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _gps_file(conn, path="a.json"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", "snapchat", "known")

def _google_file(conn, path="Timeline Edits.json"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", "google", "known")

def test_nearest_gps_point_picks_closer_of_before_and_after(conn) -> None:
    file_id = _gps_file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 12:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"},
            {"timestamp": "2024-01-01 12:20:00", "lat": 2.0, "lon": 2.0, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"}, tz="UTC",
    )

    nearest = nearest_gps_point(conn, "2024-01-01 12:16:00")
    assert nearest is not None
    assert nearest["lat"] == 2.0
    assert nearest["gap_seconds"] == 240

def test_nearest_gps_point_none_when_too_far(conn) -> None:
    file_id = _gps_file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 12:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"}, tz="UTC",
    )

    assert nearest_gps_point(conn, "2024-01-01 13:00:00") is None

def test_nearest_gps_point_none_when_no_gps_data(conn) -> None:
    assert nearest_gps_point(conn, "2024-01-01 12:00:00") is None

def test_nearest_gps_point_none_for_unparseable_timestamp(conn) -> None:
    file_id = _gps_file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 12:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"}, tz="UTC",
    )
    assert nearest_gps_point(conn, "not-a-timestamp") is None

def test_attach_nearest_gps_points_sets_key_on_each_signal(conn) -> None:
    file_id = _gps_file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 12:00:00", "lat": 5.0, "lon": 6.0, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"}, tz="UTC",
    )
    signals = [{"timestamp": "2024-01-01 12:00:30"}, {"timestamp": "2024-01-01 20:00:00"}]
    result = attach_nearest_gps_points(conn, signals)
    assert result is signals
    assert signals[0]["gps_point"]["lat"] == 5.0
    assert signals[1]["gps_point"] is None

def test_query_google_signals_with_location_end_to_end(conn) -> None:
    gps_file_id = _gps_file(conn)
    google_file_id = _google_file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 12:00:00", "lat": 40.0, "lon": -74.0, "source": "a.json"}],
        {"a.json": gps_file_id}, {"a.json": "known"}, tz="UTC",
    )
    store.insert_google_records(
        conn,
        [
            {
                "timestamp": "2024-01-01T12:01:00Z",
                "kind": "google",
                "subkind": "wifi_scan",
                "source": "Timeline Edits.json",
                "summary": "Wi-Fi scan captured 2 devices",
                "details": {"strongest_rssi": -55},
            }
        ],
        {"Timeline Edits.json": google_file_id}, {"Timeline Edits.json": "known"}, tz="UTC",
    )
    result = query_google_signals_with_location(conn)
    assert len(result) == 1
    assert result[0]["subkind"] == "wifi_scan"
    assert result[0]["details"]["strongest_rssi"] == -55
    assert result[0]["gps_point"]["lat"] == 40.0
    assert result[0]["gps_point"]["lon"] == -74.0
    assert result[0]["gps_point"]["gap_seconds"] == 60
