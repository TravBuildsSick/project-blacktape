from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.mapview import point_detail, query_bbox, query_bbox_by_day

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _file(conn, path="a.json", source_system="snapchat", confidence="known"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", source_system, confidence)

def test_query_bbox_filters_points_outside_the_box(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "t", "lat": 10.0, "lon": 10.0, "source": "a.json"},
            {"timestamp": "t", "lat": 50.0, "lon": 50.0, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    results = query_bbox(conn, min_lat=0, max_lat=20, min_lon=0, max_lon=20)
    assert len(results) == 1
    assert results[0]["lat"] == 10.0

def test_query_bbox_does_not_select_raw_json(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn, [{"timestamp": "t", "lat": 1.0, "lon": 1.0, "source": "a.json", "extra": "heavy"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    results = query_bbox(conn, min_lat=0, max_lat=2, min_lon=0, max_lon=2)
    assert "raw_json" not in results[0]

def test_query_bbox_filters_by_confidence(conn) -> None:
    known_id = _file(conn, path="known.json", source_system="snapchat", confidence="known")
    inferred_id = _file(conn, path="inferred.json", source_system="unknown", confidence="inferred")
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "t", "lat": 1.0, "lon": 1.0, "source": "known.json"},
            {"timestamp": "t", "lat": 1.0, "lon": 1.0, "source": "inferred.json"},
        ],
        {"known.json": known_id, "inferred.json": inferred_id},
        {"known.json": "known", "inferred.json": "inferred"},
    )
    known_only = query_bbox(conn, min_lat=0, max_lat=2, min_lon=0, max_lon=2, confidence="known")
    assert len(known_only) == 1
    assert known_only[0]["source"] == "known.json"

def test_query_bbox_filters_by_timestamp_range(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 00:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"},
            {"timestamp": "2024-06-01 00:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"},
        tz="UTC",
    )
    results = query_bbox(conn, min_lat=0, max_lat=2, min_lon=0, max_lon=2, since="2024-03-01")
    assert len(results) == 1
    assert results[0]["timestamp"] == "2024-06-01 00:00:00"

def test_point_detail_fetches_raw_json_lazily(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn, [{"timestamp": "t", "lat": 1.0, "lon": 1.0, "source": "a.json", "extra": "heavy"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    point_id = conn.execute("SELECT id FROM gps_points").fetchone()["id"]
    detail = point_detail(conn, point_id)
    assert detail["raw"]["extra"] == "heavy"

def test_point_detail_missing_id_returns_none(conn) -> None:
    assert point_detail(conn, 9999) is None

def test_query_bbox_by_day_groups_points_under_their_local_day(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 08:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"},
            {"timestamp": "2024-01-01 09:00:00", "lat": 1.5, "lon": 1.5, "source": "a.json"},
            {"timestamp": "2024-01-02 08:00:00", "lat": 2.0, "lon": 2.0, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"},
        tz="UTC",
    )
    grouped = query_bbox_by_day(conn, min_lat=0, max_lat=5, min_lon=0, max_lon=5)
    assert list(grouped.keys()) == ["2024-01-01", "2024-01-02"]
    assert len(grouped["2024-01-01"]) == 2
    assert len(grouped["2024-01-02"]) == 1

def test_query_bbox_by_day_uses_the_ingest_time_converted_local_day(conn) -> None:

    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-06-01 02:00:00", "lat": 1.0, "lon": 1.0, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"},
        tz="America/New_York",
    )
    grouped = query_bbox_by_day(conn, min_lat=0, max_lat=5, min_lon=0, max_lon=5)
    assert list(grouped.keys()) == ["2024-05-31"]

def test_query_bbox_by_day_respects_confidence_and_bbox_filters(conn) -> None:
    known_id = _file(conn, path="known.json", confidence="known")
    inferred_id = _file(conn, path="inferred.json", source_system="unknown", confidence="inferred")
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 08:00:00", "lat": 1.0, "lon": 1.0, "source": "known.json"},
            {"timestamp": "2024-01-01 08:00:00", "lat": 1.0, "lon": 1.0, "source": "inferred.json"},
            {"timestamp": "2024-01-01 08:00:00", "lat": 50.0, "lon": 50.0, "source": "known.json"},
        ],
        {"known.json": known_id, "inferred.json": inferred_id},
        {"known.json": "known", "inferred.json": "inferred"},
        tz="UTC",
    )
    grouped = query_bbox_by_day(conn, min_lat=0, max_lat=5, min_lon=0, max_lon=5, confidence="known")
    assert len(grouped["2024-01-01"]) == 1
    assert grouped["2024-01-01"][0]["source"] == "known.json"
