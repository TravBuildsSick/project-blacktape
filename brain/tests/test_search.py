from __future__ import annotations

from pathlib import Path

import pytest

from blacktape_brain import store
from blacktape_brain.search import search

@pytest.fixture
def conn(tmp_path: Path):
    connection = store.init_db(tmp_path / "store.db")
    yield connection
    connection.close()

def _file(conn, path="a.json"):
    return store.register_file(conn, path, 10, "2024-01-01T00:00:00", "snapchat", "known")

def test_search_empty_query_returns_empty(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn, [{"conversation": "c", "text": "hello", "timestamp": "t", "source": "a.json", "metadata": {}}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    assert search(conn, "") == []

def test_search_empty_db_returns_empty(conn) -> None:
    assert search(conn, "hello") == []

def test_search_matches_case_insensitive_substring(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [
            {"conversation": "convo-1", "text": "Let's grab coffee tomorrow", "timestamp": "2024-01-01 00:00:00",
             "sender": "alice", "is_sender_flag": True, "source": "a.json", "metadata": {}},
            {"conversation": "convo-1", "text": "sounds good", "timestamp": "2024-01-01 00:01:00",
             "sender": "bob", "is_sender_flag": False, "source": "a.json", "metadata": {}},
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    results = search(conn, "COFFEE")
    assert len(results) == 1
    assert results[0]["conversation"] == "convo-1"
    assert results[0]["content"] == "Let's grab coffee tomorrow"
    assert results[0]["sender"] == "alice"
    assert results[0]["is_sender"] is True

def test_search_returns_no_matches_when_not_found(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn, [{"conversation": "c", "text": "hello", "timestamp": "t", "source": "a.json", "metadata": {}}],
        {"a.json": file_id}, {"a.json": "known"},
    )
    assert search(conn, "nonexistent") == []

def test_search_matches_across_multiple_conversations_sorted_by_timestamp(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [
            {"conversation": "convo-a", "text": "find me", "timestamp": "2024-01-02 00:00:00", "source": "a.json", "metadata": {}},
            {"conversation": "convo-b", "text": "find me too", "timestamp": "2024-01-01 00:00:00", "source": "a.json", "metadata": {}},
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    results = search(conn, "find me")
    assert [r["conversation"] for r in results] == ["convo-b", "convo-a"]

def test_search_escapes_like_wildcards_literally(conn) -> None:
    file_id = _file(conn)
    store.insert_chat_records(
        conn,
        [
            {"conversation": "c", "text": "100% done_deal", "timestamp": "t1", "source": "a.json", "metadata": {}},
            {"conversation": "c", "text": "totally unrelated", "timestamp": "t2", "source": "a.json", "metadata": {}},
        ],
        {"a.json": file_id}, {"a.json": "known"},
    )
    results = search(conn, "100% done_deal")
    assert len(results) == 1
    assert results[0]["content"] == "100% done_deal"
