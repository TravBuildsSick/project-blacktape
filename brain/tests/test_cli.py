from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from blacktape_brain import cli, store
from blacktape_brain.classify import SourceSystem
from blacktape_brain.planner import ClassifiedFile

def _cf(path: Path, source_system: SourceSystem, size: int = 10) -> ClassifiedFile:
    return ClassifiedFile(path=path, size=size, source_system=source_system)

def test_files_for_binary_passes_all_files_to_gps_and_chat() -> None:
    batch = [
        _cf(Path("known.json"), SourceSystem.SNAPCHAT),
        _cf(Path("unknown.json"), SourceSystem.UNKNOWN),
    ]
    assert cli._files_for_binary("bt-parse-gps", batch) == batch
    assert cli._files_for_binary("bt-parse-chat", batch) == batch

def test_files_for_binary_filters_friends_and_google_to_their_provider() -> None:
    snap = _cf(Path("friends.json"), SourceSystem.SNAPCHAT)
    google = _cf(Path("timeline.json"), SourceSystem.GOOGLE)
    unknown = _cf(Path("junk.json"), SourceSystem.UNKNOWN)
    batch = [snap, google, unknown]

    assert cli._files_for_binary("bt-parse-friends", batch) == [snap]
    assert cli._files_for_binary("bt-parse-google", batch) == [google]

def test_confidence_known_for_classified_inferred_for_unknown() -> None:
    assert cli._confidence(SourceSystem.SNAPCHAT) == "known"
    assert cli._confidence(SourceSystem.GOOGLE) == "known"
    assert cli._confidence(SourceSystem.UNKNOWN) == "inferred"

def test_stream_events_inserts_records_and_tags_confidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    known_file = tmp_path / "chat_history.json"
    unknown_file = tmp_path / "junk.json"
    batch = [
        _cf(known_file, SourceSystem.SNAPCHAT),
        _cf(unknown_file, SourceSystem.UNKNOWN),
    ]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [
                {"conversation": "c", "text": "from known", "timestamp": "t", "source": "chat_history.json", "metadata": {}},
                {"conversation": "c", "text": "from unknown", "timestamp": "t", "source": "junk.json", "metadata": {}},
            ]
        if name == "bt-parse-friends":
            return {"categories": {}, "ranking": None}
        return []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    events = list(cli.stream_events(tmp_path, db_path=tmp_path / "store.db"))
    assert len(events) == 1
    event = events[0]
    assert event["phase"] == 0
    assert event["phases"] == 1
    assert event["done"] is True
    assert event["files_in_batch"] == 2
    assert event["inserted"]["chats"] == 2
    assert event["totals"]["chats"] == 2

    conn = store.connect(tmp_path / "store.db")
    rows = {row["content"]: row["source_confidence"] for row in conn.execute("SELECT content, source_confidence FROM chat_messages")}
    conn.close()
    assert rows == {"from known": "known", "from unknown": "inferred"}

def test_stream_events_does_not_self_walk_when_a_binary_has_no_applicable_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    unknown_file = tmp_path / "some_other_apps_export.json"
    batch = [_cf(unknown_file, SourceSystem.UNKNOWN)]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    calls: list[tuple[str, list[Path]]] = []

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        calls.append((name, files))
        if name == "bt-parse-chat":
            return [{"conversation": "c", "text": "hi", "timestamp": "t", "source": "some_other_apps_export.json", "metadata": {}}]
        return []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    list(cli.stream_events(tmp_path, db_path=tmp_path / "store.db"))

    called_names = {name for name, _files in calls}
    assert called_names == {"bt-parse-gps", "bt-parse-chat"}

def test_stream_events_self_walks_when_the_whole_directory_has_no_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        assert files == []
        return {"categories": {}, "ranking": None} if name == "bt-parse-friends" else []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    events = list(cli.stream_events(tmp_path, db_path=tmp_path / "store.db"))
    assert len(events) == 1
    assert events[0]["done"] is True
    assert events[0]["totals"] == {"gps": 0, "chats": 0, "google_signals": 0, "friend_records": 0}

def test_stream_events_yields_one_event_per_batch_and_flags_the_last(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batches = [
        [_cf(tmp_path / "small.json", SourceSystem.SNAPCHAT)],
        [_cf(tmp_path / "big.json", SourceSystem.SNAPCHAT)],
    ]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: batches)

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-friends":
            return {"categories": {}, "ranking": None}
        if name == "bt-parse-gps":
            return [{"timestamp": "t", "lat": 1, "lon": 2, "source": files[0].name}]
        return []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    events = list(cli.stream_events(tmp_path, db_path=tmp_path / "store.db"))
    assert len(events) == 2
    assert [e["phase"] for e in events] == [0, 1]
    assert [e["done"] for e in events] == [False, True]
    assert events[0]["totals"]["gps"] == 1
    assert events[1]["totals"]["gps"] == 2

def test_ingest_populates_db_and_returns_its_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch = [_cf(tmp_path / "chat_history.json", SourceSystem.SNAPCHAT)]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [{"conversation": "c", "text": "hi", "timestamp": "2024-01-01 00:00:00", "source": "chat_history.json", "metadata": {}}]
        return {"categories": {}, "ranking": None} if name == "bt-parse-friends" else []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    db_path = tmp_path / "store.db"
    out_path = tmp_path / "snapshot.json"
    result = cli.ingest(tmp_path, db_path=db_path, out_path=out_path)
    assert result == db_path

    snapshot = json.loads(out_path.read_text())
    assert snapshot["analytics"]["overview"]["messages"] == 1
    assert len(snapshot["timeline"]) == 1

def test_stream_events_files_mode_batches_explicit_paths_not_a_walk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Single/multi-file upload path: `files=` should route through
    `plan_batches_for_files`, never touch `plan_batches` (the directory
    walk), and still tag confidence / insert records the same way.
    """
    picked_file = tmp_path / "chat_history.json"
    batch = [_cf(picked_file, SourceSystem.SNAPCHAT)]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("plan_batches (directory walk) should not run in files= mode")

    monkeypatch.setattr(cli.planner, "plan_batches", fail_if_called)
    monkeypatch.setattr(cli.planner, "plan_batches_for_files", lambda paths, root, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [{"conversation": "c", "text": "hi", "timestamp": "t", "source": "chat_history.json", "metadata": {}}]
        if name == "bt-parse-friends":
            return {"categories": {}, "ranking": None}
        return []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    events = list(cli.stream_events(None, db_path=tmp_path / "store.db", files=[picked_file]))
    assert len(events) == 1
    assert events[0]["inserted"]["chats"] == 1

def test_common_root_single_file_is_its_parent(tmp_path: Path) -> None:
    f = tmp_path / "a" / "chat_history.json"
    assert cli._common_root([f]) == (tmp_path / "a")

def test_common_root_multiple_files_is_shared_parent(tmp_path: Path) -> None:
    a = tmp_path / "sub1" / "chat_history.json"
    b = tmp_path / "sub2" / "friends.json"
    assert cli._common_root([a, b]) == tmp_path

def test_purge_removes_existing_db_and_reports_none_when_absent(tmp_path: Path) -> None:
    db_path = tmp_path / ".blacktape" / "store.db"
    store.init_db(db_path).close()
    assert db_path.exists()

    removed = cli.purge(db_path=db_path)
    assert removed == db_path
    assert not db_path.exists()

    assert cli.purge(db_path=db_path) is None

def test_ingest_stream_writes_ndjson_one_line_per_batch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_stream_events(export_dir: Path, db_path=None, batch_bytes=0, tz=None, files=None):
        yield {"phase": 0, "phases": 2, "done": False}
        yield {"phase": 1, "phases": 2, "done": True}

    monkeypatch.setattr(cli, "stream_events", fake_stream_events)

    out = io.StringIO()
    cli.ingest_stream(tmp_path, out)

    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines == [
        {"phase": 0, "phases": 2, "done": False},
        {"phase": 1, "phases": 2, "done": True},
    ]

def test_query_search_cli_subcommand_prints_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    batch = [_cf(tmp_path / "chat_history.json", SourceSystem.SNAPCHAT)]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [{"conversation": "c", "text": "find the coffee", "timestamp": "t", "source": "chat_history.json", "metadata": {}}]
        return {"categories": {}, "ranking": None} if name == "bt-parse-friends" else []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    db_path = tmp_path / "store.db"
    cli.ingest(tmp_path, db_path=db_path)

    exit_code = cli.main(["query", "search", "--db", str(db_path), "coffee"])
    assert exit_code == 0

def test_query_conversations_cli_subcommand_groups_by_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = [_cf(tmp_path / "chat_history.json", SourceSystem.SNAPCHAT)]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [
                {"conversation": "alice", "text": "hi", "timestamp": "t1", "source": "chat_history.json", "metadata": {}},
                {"conversation": "alice", "text": "bye", "timestamp": "t2", "source": "chat_history.json", "metadata": {}},
                {"conversation": "bob", "text": "yo", "timestamp": "t1", "source": "chat_history.json", "metadata": {}},
            ]
        return {"categories": {}, "ranking": None} if name == "bt-parse-friends" else []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    db_path = tmp_path / "store.db"
    cli.ingest(tmp_path, db_path=db_path)

    exit_code = cli.main(["query", "conversations", "--db", str(db_path)])
    assert exit_code == 0

    result = json.loads(capsys.readouterr().out)
    counts = {row["conversation"]: row["message_count"] for row in result}
    assert counts == {"alice": 2, "bob": 1}

def test_query_chats_conversation_filter_narrows_to_one_conversation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    batch = [_cf(tmp_path / "chat_history.json", SourceSystem.SNAPCHAT)]
    monkeypatch.setattr(cli.planner, "plan_batches", lambda export_dir, batch_bytes: [batch])

    def fake_run_parser(name: str, export_dir: Path, files: list[Path]) -> object:
        if name == "bt-parse-chat":
            return [
                {"conversation": "alice", "text": "hi", "timestamp": "t1", "source": "chat_history.json", "metadata": {}},
                {"conversation": "bob", "text": "yo", "timestamp": "t1", "source": "chat_history.json", "metadata": {}},
            ]
        return {"categories": {}, "ranking": None} if name == "bt-parse-friends" else []

    monkeypatch.setattr(cli, "_run_parser", fake_run_parser)

    db_path = tmp_path / "store.db"
    cli.ingest(tmp_path, db_path=db_path)

    exit_code = cli.main(["query", "chats", "--db", str(db_path), "--conversation", "alice"])
    assert exit_code == 0

    result = json.loads(capsys.readouterr().out)
    assert [row["conversation"] for row in result] == ["alice"]
