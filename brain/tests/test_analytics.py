from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.analytics import compute_analytics

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _file(conn, path="a.json", source_system="snapchat"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", source_system, "known")

def test_compute_analytics_empty_db_has_zeroed_overview(conn) -> None:
    result = compute_analytics(conn)
    assert result["overview"] == {
        "messages": 0,
        "conversations": 0,
        "gps_points": 0,
        "friend_records": 0,
        "google_signals": 0,
    }
    assert result["chat"]["top_conversations"] == []
    assert result["gps"] == {"layers": {}, "busiest_days": []}
    assert result["friends"]["ranking"] == {}
    assert result["google"] == {"signal_types": {}, "top_activities": [], "platforms": {}}

def test_compute_analytics_overview_counts(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [
            {"conversation": "c1", "timestamp": "2024-01-01 00:00:00", "text": "a", "source": "a.json", "metadata": {}},
            {"conversation": "c1", "timestamp": "2024-01-02 00:00:00", "text": "b", "source": "a.json", "metadata": {}},
            {"conversation": "c2", "timestamp": "2024-01-01 00:00:00", "text": "c", "source": "a.json", "metadata": {}},
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    store.insert_gps_records(
        conn, [{"timestamp": "2024-01-01 00:00:00", "lat": 1, "lon": 2, "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    store.insert_google_records(
        conn, [{"id": "g1", "timestamp": "2024-01-01 00:00:00", "source": "a.json"}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    store.insert_friend_records(
        conn, {"friends": [{"username": "a", "file": "a.json"}, {"username": "b", "file": "a.json"}]},
        {"a.json": file_id}, {"a.json": "known"},
    )

    result = compute_analytics(conn)
    assert result["overview"] == {
        "messages": 3,
        "conversations": 2,
        "gps_points": 1,
        "friend_records": 2,
        "google_signals": 1,
    }

def test_compute_analytics_top_conversations_sorted_by_message_count(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [{"conversation": "small", "timestamp": "2024-01-01 00:00:00", "text": "x", "source": "a.json", "metadata": {}}]
        + [
            {"conversation": "big", "timestamp": t, "text": "x", "source": "a.json", "metadata": {}}
            for t in ["2024-01-01 00:00:00", "2024-01-02 00:00:00", "2024-01-03 00:00:00"]
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    result = compute_analytics(conn)
    top = result["chat"]["top_conversations"]
    assert top[0]["conversation"] == "big"
    assert top[0]["messages"] == 3
    assert top[0]["last_timestamp"] == "2024-01-03 00:00:00"
    assert top[1]["conversation"] == "small"

def test_compute_analytics_gps_layers_and_busiest_days(conn) -> None:
    file_id = _file(conn)
    store.insert_gps_records(
        conn,
        [
            {"timestamp": "2024-01-01 08:00:00", "layer": "location_history", "lat": 1, "lon": 2, "source": "a.json"},
            {"timestamp": "2024-01-01 09:00:00", "layer": "location_history", "lat": 1, "lon": 2, "source": "a.json"},
            {"timestamp": "2024-01-02 08:00:00", "layer": "memories_history", "lat": 1, "lon": 2, "source": "a.json"},
        ],
        {"a.json": file_id}, {"a.json": "known"},
        tz="UTC",
    )
    result = compute_analytics(conn)
    assert result["gps"]["layers"] == {"location_history": 2, "memories_history": 1}
    days = {d["day"]: d["points"] for d in result["gps"]["busiest_days"]}
    assert days == {"2024-01-01": 2, "2024-01-02": 1}

def test_compute_analytics_friend_summary_and_ranking(conn) -> None:
    file_id = _file(conn, path="friends.json")
    store.insert_friend_records(
        conn,
        {
            "friends": [{"username": "alice", "file": "friends.json"}, {"username": "bob", "file": "friends.json"}],
            "blocked_users": [{"username": "bob", "file": "friends.json"}],
        },
        {"friends.json": file_id}, {"friends.json": "known"},
    )
    store.upsert_ranking(conn, {"snapscore": 100, "total_friends": 2, "following": 2, "raw": {}}, file_id, "2024-01-01T00:00:00")

    result = compute_analytics(conn)
    summary = result["friends"]["summary"]
    assert summary["friends"] == 2
    assert summary["blocked_users"] == 1
    assert summary["total_records"] == 3
    assert summary["unique_usernames"] == 2
    assert result["friends"]["ranking"]["snapscore"] == 100

def test_compute_analytics_google_signal_aggregation(conn) -> None:
    file_id = _file(conn, path="Timeline Edits.json", source_system="google")
    store.insert_google_records(
        conn,
        [
            {"subkind": "activity", "timestamp": "t", "source": "Timeline Edits.json",
             "details": {"activity_type": "IN_VEHICLE", "platform": "ANDROID"}},
            {"subkind": "activity", "timestamp": "t", "source": "Timeline Edits.json",
             "details": {"activity_type": "IN_VEHICLE", "platform": "ANDROID"}},
            {"subkind": "wifi_scan", "timestamp": "t", "source": "Timeline Edits.json",
             "details": {"platform": "IOS"}},
        ],
        {"Timeline Edits.json": file_id}, {"Timeline Edits.json": "known"},
    )
    result = compute_analytics(conn)
    assert result["google"]["signal_types"] == {"activity": 2, "wifi_scan": 1}
    assert result["google"]["top_activities"] == [{"activity": "IN_VEHICLE", "count": 2}]
    assert result["google"]["platforms"] == {"ANDROID": 2, "IOS": 1}
