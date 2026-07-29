from __future__ import annotations

import json
from pathlib import Path

import pytest

from blacktape_brain import store

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def test_init_db_creates_all_tables(conn) -> None:
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert tables >= {
        "files",
        "gps_points",
        "chat_messages",
        "google_signals",
        "friend_records",
        "friend_ranking",
    }

def test_register_file_returns_incrementing_ids(conn) -> None:
    id1 = store.register_file(conn, "a.json", 10, "2024-01-01T00:00:00", "snapchat", "known")
    id2 = store.register_file(conn, "b.json", 20, "2024-01-01T00:00:00", "unknown", "inferred")
    assert id2 != id1

def test_insert_gps_records_tags_confidence_per_file(conn) -> None:
    known_id = store.register_file(conn, "location_history.json", 5, "t", "snapchat", "known")
    unknown_id = store.register_file(conn, "junk.json", 5, "t", "unknown", "inferred")
    file_ids = {"location_history.json": known_id, "junk.json": unknown_id}
    confidence = {"location_history.json": "known", "junk.json": "inferred"}

    records = [
        {"timestamp": "2024-01-01 00:00:00 UTC", "lat": 1.0, "lon": 2.0, "layer": "location_history",
         "source": "location_history.json", "source_system": "snapchat"},
        {"timestamp": "2024-01-02 00:00:00 UTC", "lat": 3.0, "lon": 4.0, "layer": "other",
         "source": "junk.json", "source_system": "unknown"},
    ]
    inserted = store.insert_gps_records(conn, records, file_ids, confidence)
    assert inserted == 2

    rows = conn.execute("SELECT source, source_confidence, lat, lon FROM gps_points ORDER BY id").fetchall()
    assert rows[0]["source_confidence"] == "known"
    assert rows[1]["source_confidence"] == "inferred"
    assert rows[1]["lat"] == 3.0

def test_insert_gps_records_skips_records_whose_file_was_not_registered(conn) -> None:
    records = [{"timestamp": "t", "lat": 1.0, "lon": 2.0, "source": "never_registered.json"}]
    inserted = store.insert_gps_records(conn, records, {}, {})
    assert inserted == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM gps_points").fetchone()["c"] == 0

def test_insert_gps_records_stores_raw_json_for_lazy_fetch(conn) -> None:
    file_id = store.register_file(conn, "a.json", 5, "t", "snapchat", "known")
    records = [{"timestamp": "t", "lat": 1.0, "lon": 2.0, "source": "a.json", "extra_field": "detail"}]
    store.insert_gps_records(conn, records, {"a.json": file_id}, {"a.json": "known"})

    raw = json.loads(conn.execute("SELECT raw_json FROM gps_points").fetchone()["raw_json"])
    assert raw["extra_field"] == "detail"

def test_insert_gps_records_converts_timestamp_into_given_timezone(conn) -> None:
    file_id = store.register_file(conn, "a.json", 5, "t", "snapchat", "known")
    records = [{"timestamp": "2024-05-01 18:00:00 UTC", "lat": 1.0, "lon": 2.0, "source": "a.json"}]
    store.insert_gps_records(conn, records, {"a.json": file_id}, {"a.json": "known"}, tz="America/New_York")

    row = conn.execute("SELECT timestamp FROM gps_points").fetchone()
    assert row["timestamp"] == "2024-05-01 14:00:00"

def test_insert_gps_records_falsy_timestamp_stays_empty_regardless_of_tz(conn) -> None:
    file_id = store.register_file(conn, "a.json", 5, "t", "snapchat", "known")
    records = [{"timestamp": None, "lat": 1.0, "lon": 2.0, "source": "a.json"}]
    store.insert_gps_records(conn, records, {"a.json": file_id}, {"a.json": "known"}, tz="America/New_York")

    row = conn.execute("SELECT timestamp FROM gps_points").fetchone()
    assert row["timestamp"] == ""

def test_insert_chat_records_normalizes_timestamp_and_conversation(conn) -> None:
    file_id = store.register_file(conn, "chat_history.json", 5, "t", "snapchat", "known")
    records = [
        {
            "conversation": "convo-1",
            "sender": "alice",
            "text": "hello",
            "timestamp": 1714586700,
            "is_sender_flag": True,
            "source": "chat_history.json",
            "metadata": {"raw": True},
        },
        {
            "conversation": None,
            "sender": None,
            "text": "signal",
            "timestamp": None,
            "is_sender_flag": False,
            "source": "chat_history.json",
            "metadata": {},
        },
    ]
    inserted = store.insert_chat_records(conn, records, {"chat_history.json": file_id}, {"chat_history.json": "known"})
    assert inserted == 2

    rows = conn.execute("SELECT conversation, timestamp, sender, is_sender FROM chat_messages ORDER BY id").fetchall()
    assert rows[0]["conversation"] == "convo-1"
    assert rows[0]["timestamp"] == "2024-05-01 18:05:00"
    assert rows[0]["is_sender"] == 1
    assert rows[1]["conversation"] == "GENERAL_SIGNAL"
    assert rows[1]["sender"] == "Unknown"
    assert rows[1]["timestamp"] == "1970-01-01 00:00:00"

def test_insert_google_records(conn) -> None:
    file_id = store.register_file(conn, "Timeline Edits.json", 5, "t", "google", "known")
    records = [
        {
            "id": "g1",
            "timestamp": "2024-01-01T00:00:00Z",
            "kind": "google_signal",
            "subkind": "activity",
            "source": "Timeline Edits.json",
            "summary": "IN_VEHICLE",
            "details": {"activity_type": "IN_VEHICLE"},
        }
    ]
    inserted = store.insert_google_records(conn, records, {"Timeline Edits.json": file_id}, {"Timeline Edits.json": "known"})
    assert inserted == 1

    row = conn.execute("SELECT subkind, summary, details_json FROM google_signals").fetchone()
    assert row["subkind"] == "activity"
    assert json.loads(row["details_json"]) == {"activity_type": "IN_VEHICLE"}

def test_insert_friend_records_across_categories(conn) -> None:
    file_id = store.register_file(conn, "friends.json", 5, "t", "snapchat", "known")
    categories = {
        "friends": [
            {"username": "alice", "display_name": "Alice", "created": "2020-01-01", "modified": "2021-01-01", "file": "friends.json"},
        ],
        "blocked_users": [
            {"username": "eve", "display_name": "Eve", "created": "2020-01-01", "modified": "2020-01-01", "file": "friends.json"},
        ],
    }
    inserted = store.insert_friend_records(conn, categories, {"friends.json": file_id}, {"friends.json": "known"})
    assert inserted == 2

    rows = conn.execute("SELECT category, username FROM friend_records ORDER BY category").fetchall()
    assert [(r["category"], r["username"]) for r in rows] == [("blocked_users", "eve"), ("friends", "alice")]

def test_upsert_ranking_and_latest_ranking_is_last_batch_wins(conn) -> None:
    file_id = store.register_file(conn, "friends.json", 5, "t", "snapchat", "known")
    store.upsert_ranking(conn, {"snapscore": 100, "total_friends": 1, "following": 1, "raw": {}}, file_id, "2024-01-01T00:00:00")
    store.upsert_ranking(conn, {"snapscore": 200, "total_friends": 2, "following": 2, "raw": {}}, file_id, "2024-01-02T00:00:00")

    latest = store.latest_ranking(conn)
    assert latest["snapscore"] == 200
    assert latest["total_friends"] == 2

def test_upsert_ranking_ignores_none() -> None:

    import sqlite3

    conn = sqlite3.connect(":memory:")
    store.upsert_ranking(conn, None, file_id=1, ingested_at="t")

def test_latest_ranking_empty_db_returns_empty_dict(conn) -> None:
    assert store.latest_ranking(conn) == {}

def test_list_friend_records_returns_all_categories(conn) -> None:
    file_id = store.register_file(conn, "friends.json", 5, "t", "snapchat", "known")
    store.insert_friend_records(
        conn,
        {
            "friends": [{"username": "alice", "display_name": "Alice", "created": "c", "modified": "m", "file": "friends.json"}],
            "blocked_users": [{"username": "eve", "display_name": "Eve", "created": "c", "modified": "m", "file": "friends.json"}],
        },
        {"friends.json": file_id}, {"friends.json": "known"},
    )
    records = store.list_friend_records(conn)
    assert {r["username"] for r in records} == {"alice", "eve"}

def test_list_friends_grouped_combines_categories_per_identity(conn) -> None:
    file_id = store.register_file(conn, "friends.json", 5, "t", "snapchat", "known")
    store.insert_friend_records(
        conn,
        {
            "friends": [{"username": "alice", "display_name": "Alice", "created": "2020-01-01", "modified": "2021-01-01", "file": "friends.json"}],
            "blocked_users": [{"username": "alice", "display_name": "Alice", "created": "2020-01-01", "modified": "2020-06-01", "file": "friends.json"}],
            "pending_requests": [{"username": "bob", "display_name": "Bob", "created": "2022-01-01", "modified": "2022-01-01", "file": "friends.json"}],
        },
        {"friends.json": file_id}, {"friends.json": "known"},
    )

    grouped = store.list_friends_grouped(conn)
    assert len(grouped) == 2

    alice = next(r for r in grouped if r["username"] == "alice")
    assert alice["categories"] == ["blocked_users", "friends"]
    assert alice["display_name"] == "Alice"

    bob = next(r for r in grouped if r["username"] == "bob")
    assert bob["categories"] == ["pending_requests"]

def test_list_chat_messages_returns_all_messages_ordered_by_timestamp(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "c", "text": "second", "timestamp": "2024-01-02 00:00:00", "source": "chat.json", "metadata": {}},
            {"conversation": "c", "text": "first", "timestamp": "2024-01-01 00:00:00", "source": "chat.json", "metadata": {}},
        ],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    messages = store.list_chat_messages(conn)
    assert [m["content"] for m in messages] == ["first", "second"]

def test_list_chat_messages_respects_limit(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [{"conversation": "c", "text": f"m{i}", "timestamp": f"t{i}", "source": "chat.json", "metadata": {}} for i in range(5)],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    assert len(store.list_chat_messages(conn, limit=2)) == 2

def test_list_chat_messages_filters_by_conversation(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "alice", "text": "hi alice", "timestamp": "t1", "source": "chat.json", "metadata": {}},
            {"conversation": "bob", "text": "hi bob", "timestamp": "t2", "source": "chat.json", "metadata": {}},
        ],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    messages = store.list_chat_messages(conn, conversation="alice")
    assert [m["content"] for m in messages] == ["hi alice"]

def test_list_conversations_groups_and_counts_without_touching_content(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "alice", "text": "hi", "timestamp": "2024-01-01 00:00:00", "source": "chat.json", "metadata": {}},
            {"conversation": "alice", "text": "bye", "timestamp": "2024-01-03 00:00:00", "source": "chat.json", "metadata": {}},
            {"conversation": "bob", "text": "yo", "timestamp": "2024-01-02 00:00:00", "source": "chat.json", "metadata": {}},
        ],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    summaries = {row["conversation"]: row for row in store.list_conversations(conn)}

    assert summaries["alice"]["message_count"] == 2
    assert summaries["alice"]["first_timestamp"] == "2024-01-01 00:00:00"
    assert summaries["alice"]["last_timestamp"] == "2024-01-03 00:00:00"
    assert summaries["bob"]["message_count"] == 1
    assert "content" not in summaries["alice"]

def test_list_conversations_orders_most_recent_activity_first(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "old", "text": "hi", "timestamp": "2024-01-01 00:00:00", "source": "chat.json", "metadata": {}},
            {"conversation": "recent", "text": "hi", "timestamp": "2024-06-01 00:00:00", "source": "chat.json", "metadata": {}},
        ],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    summaries = store.list_conversations(conn)
    assert [row["conversation"] for row in summaries] == ["recent", "old"]

def test_infer_and_backfill_direction_uses_sender_recurring_across_conversations(conn) -> None:

    file_id = store.register_file(conn, "chat.json", 5, "t", "unknown", "inferred")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "Eric_19", "sender": "Johnna Rogers", "text": "hi", "timestamp": "t1", "source": "chat.json", "metadata": {}},
            {"conversation": "Eric_19", "sender": "Eric Scheidker", "text": "hey", "timestamp": "t2", "source": "chat.json", "metadata": {}},
            {"conversation": "Billy_2", "sender": "Johnna Rogers", "text": "yo", "timestamp": "t3", "source": "chat.json", "metadata": {}},
            {"conversation": "Billy_2", "sender": "Billy Smith", "text": "sup", "timestamp": "t4", "source": "chat.json", "metadata": {}},
        ],
        {"chat.json": file_id}, {"chat.json": "inferred"},
    )

    rows = conn.execute("SELECT is_sender FROM chat_messages").fetchall()
    assert all(row["is_sender"] is None for row in rows)

    store.infer_and_backfill_direction(conn)

    by_sender = {
        row["sender"]: bool(row["is_sender"])
        for row in conn.execute("SELECT sender, is_sender FROM chat_messages").fetchall()
    }
    assert by_sender["Johnna Rogers"] is True
    assert by_sender["Eric Scheidker"] is False
    assert by_sender["Billy Smith"] is False

def test_infer_and_backfill_direction_does_not_overwrite_explicit_flag(conn) -> None:
    file_id = store.register_file(conn, "chat_history.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [
            {"conversation": "c1", "sender": "me", "text": "hi", "timestamp": "t1",
             "is_sender_flag": False, "source": "chat_history.json", "metadata": {}},
        ],
        {"chat_history.json": file_id}, {"chat_history.json": "known"},
    )
    store.infer_and_backfill_direction(conn)
    row = conn.execute("SELECT is_sender FROM chat_messages").fetchone()
    assert row["is_sender"] == 0

def test_infer_and_backfill_direction_no_op_when_no_sender_recurs(conn) -> None:

    file_id = store.register_file(conn, "chat.json", 5, "t", "unknown", "inferred")
    store.insert_chat_records(
        conn,
        [{"conversation": "only_one", "sender": "A", "text": "hi", "timestamp": "t1", "source": "chat.json", "metadata": {}}],
        {"chat.json": file_id}, {"chat.json": "inferred"},
    )
    store.infer_and_backfill_direction(conn)
    row = conn.execute("SELECT is_sender FROM chat_messages").fetchone()
    assert row["is_sender"] is None

def test_list_conversations_respects_limit(conn) -> None:
    file_id = store.register_file(conn, "chat.json", 5, "t", "snapchat", "known")
    store.insert_chat_records(
        conn,
        [{"conversation": f"c{i}", "text": "hi", "timestamp": f"t{i}", "source": "chat.json", "metadata": {}} for i in range(5)],
        {"chat.json": file_id}, {"chat.json": "known"},
    )
    assert len(store.list_conversations(conn, limit=2)) == 2
