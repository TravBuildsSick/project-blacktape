from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.timeline import build_timeline

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _file(conn, path="a.json", source_system="snapchat", confidence="known"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", source_system, confidence)

def test_build_timeline_empty_db_is_empty(conn) -> None:
    assert build_timeline(conn) == []

def test_build_timeline_includes_chat_events(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [{"conversation": "convo-1", "sender": "me", "text": "hi", "timestamp": "2024-01-01 00:00:00",
          "is_sender_flag": True, "source": "a.json", "metadata": {}}],
        {"a.json": file_id},
        {"a.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 1
    event = timeline[0]
    assert event["kind"] == "chat"
    assert event["summary"] == "hi"
    assert event["details"]["direction"] == "outbound"
    assert event["details"]["sender"] == "me"

def test_build_timeline_skips_chat_messages_without_timestamp(conn) -> None:

    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [{"conversation": "c", "text": "no ts", "timestamp": None, "source": "a.json", "metadata": {}}],
        {"a.json": file_id},
        {"a.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 1
    assert timeline[0]["timestamp"] == "1970-01-01 00:00:00"

def test_build_timeline_includes_gps_events(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 00:00:00 UTC", "layer": "location_history", "source": "loc.json", "lat": 1.0, "lon": 2.0}],
        {"loc.json": file_id},
        {"loc.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 1
    event = timeline[0]
    assert event["kind"] == "gps"
    assert event["details"]["coordinates"] == "1.0, 2.0"

def test_build_timeline_includes_google_signal_events(conn) -> None:
    file_id = _file(conn, path="Timeline Edits.json", source_system="google")
    store.insert_google_records(
        conn,
        [{"timestamp": "2024-01-01T00:00:00Z", "subkind": "activity", "summary": "IN_VEHICLE activity detected",
          "source": "Timeline Edits.json", "details": {"activity_type": "IN_VEHICLE"}}],
        {"Timeline Edits.json": file_id},
        {"Timeline Edits.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 1
    event = timeline[0]
    assert event["kind"] == "google"
    assert event["label"] == "activity"
    assert event["details"]["activity_type"] == "IN_VEHICLE"
    assert event["details"]["source"] == "Timeline Edits.json"

def test_build_timeline_includes_friend_created_and_modified_events(conn) -> None:
    file_id = _file(conn, path="friends.json")
    store.insert_friend_records(
        conn,
        {"friends": [{"username": "alice", "display_name": "Alice A", "created": "2020-01-01 00:00:00",
                      "modified": "2021-01-01 00:00:00", "file": "friends.json"}]},
        {"friends.json": file_id},
        {"friends.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 2
    events = {e["details"]["event"] for e in timeline}
    assert events == {"created", "modified"}

def test_build_timeline_skips_modified_event_when_same_as_created(conn) -> None:
    file_id = _file(conn, path="friends.json")
    store.insert_friend_records(
        conn,
        {"friends": [{"username": "alice", "created": "2020-01-01 00:00:00", "modified": "2020-01-01 00:00:00",
                      "file": "friends.json"}]},
        {"friends.json": file_id},
        {"friends.json": "known"},
    )
    timeline = build_timeline(conn)
    assert len(timeline) == 1
    assert timeline[0]["details"]["event"] == "created"

def test_build_timeline_is_sorted_chronologically_across_kinds(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [{"conversation": "c", "text": "third", "timestamp": "2024-01-03 00:00:00", "source": "a.json", "metadata": {}}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    store.insert_gps_records(
        conn,
        [{"timestamp": "2024-01-01 00:00:00 UTC", "lat": 1, "lon": 2, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    store.insert_google_records(
        conn,
        [{"timestamp": "2024-01-02 00:00:00", "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    timeline = build_timeline(conn)
    assert [e["kind"] for e in timeline] == ["gps", "google", "chat"]

def test_build_timeline_filters_by_since_and_until(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 00:00:00", "lat": 1, "lon": 2, "source": "a.json"},
            {"timestamp": "2024-06-01 00:00:00", "lat": 1, "lon": 2, "source": "a.json"},
            {"timestamp": "2024-12-01 00:00:00", "lat": 1, "lon": 2, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"},
        tz="UTC",
    )
    timeline = build_timeline(conn, since="2024-02-01", until="2024-11-01")
    assert len(timeline) == 1
    assert timeline[0]["timestamp"] == "2024-06-01 00:00:00"
