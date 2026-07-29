from __future__ import annotations

import json
from pathlib import Path

from blacktape_brain.classify import SourceSystem
from blacktape_brain.planner import (
    classify_discovered_files,
    classify_files,
    discover_json_files,
    plan_batches,
    plan_batches_for_files,
    relative_filename,
)

def _write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)

def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))

def test_discover_json_files_ignores_non_json(tmp_path: Path) -> None:
    _write(tmp_path / "a.json", 10)
    _write(tmp_path / "notes.txt", 10)

    found = {f.path.name for f in discover_json_files(tmp_path)}
    assert found == {"a.json"}

def test_discover_json_files_respects_max_walk_depth(tmp_path: Path) -> None:

    _write(tmp_path / "shallow.json", 1)

    _write(tmp_path / "a" / "at_max.json", 1)

    _write(tmp_path / "a" / "b" / "too_deep.json", 1)

    found = {f.path.name for f in discover_json_files(tmp_path)}
    assert found == {"shallow.json", "at_max.json"}
    assert "too_deep.json" not in found

def test_discover_json_files_does_not_filter_by_content(tmp_path: Path) -> None:

    _write_json(tmp_path / "some_other_apps_export.json", {"message": "not export data"})

    found = {f.path.name for f in discover_json_files(tmp_path)}
    assert found == {"some_other_apps_export.json"}

def test_relative_filename_strips_root_prefix(tmp_path: Path) -> None:
    nested = tmp_path / "snapchat" / "friends.json"
    assert relative_filename(nested, tmp_path) == "snapchat/friends.json"

def test_relative_filename_falls_back_to_file_name_when_root_is_the_file_itself(
    tmp_path: Path,
) -> None:
    f = tmp_path / "friends.json"
    assert relative_filename(f, f) == "friends.json"

def test_classify_discovered_files_tags_known_and_unknown(tmp_path: Path) -> None:
    _write_json(tmp_path / "chat_history.json", {"text": "hi", "From": "alice"})
    _write_json(tmp_path / "some_other_apps_export.json", {"message": "not export data"})

    by_name = {f.path.name: f.source_system for f in classify_discovered_files(tmp_path)}
    assert by_name == {
        "chat_history.json": SourceSystem.SNAPCHAT,
        "some_other_apps_export.json": SourceSystem.UNKNOWN,
    }

def test_classify_discovered_files_drops_invalid_json(tmp_path: Path) -> None:
    _write(tmp_path / "friends.json", 10)

    assert classify_discovered_files(tmp_path) == []

def test_classify_discovered_files_uses_content_markers_when_filename_is_ambiguous(
    tmp_path: Path,
) -> None:

    _write_json(
        tmp_path / "location_history.json",
        {
            "Frequent Locations": [],
            "Latest Location": {},
            "Daily Top Locations": [],
        },
    )

    found = classify_discovered_files(tmp_path)
    assert len(found) == 1
    assert found[0].source_system is SourceSystem.SNAPCHAT

def test_plan_batches_groups_small_files_together(tmp_path: Path) -> None:
    _write_json(tmp_path / "a_friends.json", {"Friends": []})
    _write_json(tmp_path / "b_friends.json", {"Friends": []})
    _write_json(tmp_path / "c_friends.json", {"Friends": []})

    batches = plan_batches(tmp_path, batch_bytes=1000)
    assert len(batches) == 1
    assert len(batches[0]) == 3

def test_plan_batches_isolates_files_smallest_first(tmp_path: Path) -> None:
    _write_json(tmp_path / "small1_friends.json", {"Friends": []})
    _write_json(tmp_path / "small2_friends.json", {"Friends": []})

    _write_json(
        tmp_path / "big_friends.json",
        {"Friends": [], "notes": "x" * 2000},
    )

    batches = plan_batches(tmp_path, batch_bytes=200)

    assert {f.path.name for f in batches[0]} == {"small1_friends.json", "small2_friends.json"}
    assert [f.path.name for f in batches[1]] == ["big_friends.json"]

def test_plan_batches_never_drops_a_file_larger_than_the_cap(tmp_path: Path) -> None:
    _write_json(tmp_path / "huge_friends.json", {"Friends": [], "notes": "x" * 2000})

    batches = plan_batches(tmp_path, batch_bytes=10)
    assert sum(len(b) for b in batches) == 1
    assert batches[0][0].path.name == "huge_friends.json"

def test_plan_batches_no_longer_excludes_unrecognized_files(tmp_path: Path) -> None:

    _write_json(tmp_path / "chat_history.json", {"text": "hi"})
    _write_json(tmp_path / "some_other_apps_export.json", {"message": "not export data"})

    batches = plan_batches(tmp_path, batch_bytes=1000)
    names = {f.path.name for batch in batches for f in batch}
    assert names == {"chat_history.json", "some_other_apps_export.json"}

    by_name = {f.path.name: f.source_system for batch in batches for f in batch}
    assert by_name["some_other_apps_export.json"] is SourceSystem.UNKNOWN

def test_classify_files_skips_no_directory_walk_and_drops_invalid_json(tmp_path: Path) -> None:
    good = tmp_path / "sub" / "chat_history.json"
    _write_json(good, {"text": "hi"})
    bad = tmp_path / "sub" / "broken.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not json")

    deep = tmp_path / "a" / "b" / "c" / "d" / "friends.json"
    _write_json(deep, {"Friends": []})

    classified = classify_files([good, bad, deep], root=tmp_path)
    names = {f.path.name for f in classified}
    assert names == {"chat_history.json", "friends.json"}

def test_plan_batches_for_files_buckets_smallest_first(tmp_path: Path) -> None:
    small1 = tmp_path / "small1_friends.json"
    small2 = tmp_path / "small2_friends.json"
    big = tmp_path / "big_friends.json"
    _write_json(small1, {"Friends": []})
    _write_json(big, {"Friends": [], "notes": "x" * 500})
    _write_json(small2, {"Friends": []})

    batches = plan_batches_for_files([small1, big, small2], root=tmp_path, batch_bytes=200)
    assert {f.path.name for f in batches[0]} == {"small1_friends.json", "small2_friends.json"}
    assert [f.path.name for f in batches[1]] == ["big_friends.json"]
