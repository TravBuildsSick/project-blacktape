"""Decides which `.json` files a brain-driven ingest run hands to the
`bt-parse-*` binaries, and in what batches.

`discover_json_files` mirrors the walk bound `common::MAX_WALK_DEPTH`
(`parsers/common/src/lib.rs`) so this only ever proposes files the Rust
side would also have found on its own directory-walk mode — the brain
isn't inventing new files to parse, just choosing what order and grouping
to hand them over in.

`classify_discovered_files` tags each file with `classify.classify_file`'s
verdict (Google/Snapchat/Unknown) but, unlike the old
`recognized_json_files`, no longer drops Unknown files — `bt-parse-gps`
and `bt-parse-chat` are source-agnostic extractors (lat/lon hunt,
message-shaped recursive search) that don't need to know the provider, so
an unrecognized file still gets run through them; the brain just tags
those records `source_confidence: 'inferred'` instead of `'known'` at
insert time (see `store.py`, `cli.py`). A file that fails to open or isn't
valid JSON is still dropped here — that's a hard failure, not a
classification call. `bt-parse-friends`/`bt-parse-google` can't
generalize the same way (there's no structural heuristic for "this is a
friends list" the way there is for "this dict has lat/lon"), so `cli.py`
still filters each batch down to Snapchat/Google-classified files before
handing it to those two binaries specifically.

`plan_batches` sorts all classified files smallest-first and greedily
buckets them so each batch's combined size stays under `batch_bytes`
where possible. That gets small files streaming out fast (many small
files fit in one early batch) while a handful of large files each end up
isolated in their own later batch, instead of one blocking run that tries
to hold an entire large export in memory at once — the "many files that
individually pass the per-file cap but add up" case that the Rust-side
`MAX_TOTAL_JSON_BYTES` cap used to just skip outright.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from blacktape_brain.classify import SourceSystem, classify_file

MAX_WALK_DEPTH = 2

DEFAULT_BATCH_BYTES = 5 * 1024 * 1024

@dataclass(frozen=True)
class DiscoveredFile:
    path: Path
    size: int

@dataclass(frozen=True)
class ClassifiedFile:
    path: Path
    size: int
    source_system: SourceSystem

def relative_filename(path: Path, root: Path) -> str:
    """`path` relative to `root`, with forward slashes. Falls back to the
    path's own file name when `root` points directly at `path` itself —
    mirrors `common::relative_filename` (parsers/common/src/lib.rs), so
    `classify_file` sees the same filename here that a `bt-parse-*`
    binary's own filename checks would see for the same input.
    """
    try:
        stripped = path.relative_to(root)
    except ValueError:
        stripped = None

    text = str(stripped) if stripped is not None and str(stripped) != "." else path.name
    return text.replace("\\", "/")

def discover_json_files(export_dir: Path) -> list[DiscoveredFile]:
    """Finds `.json` files under `export_dir`, bounded to `MAX_WALK_DEPTH`
    levels below it (`export_dir` itself is depth 0, its direct children
    are depth 1, and so on — matching `WalkDir::max_depth`'s convention).
    """
    export_dir = export_dir.resolve()
    found: list[DiscoveredFile] = []

    for root, dirnames, filenames in os.walk(export_dir):
        root_path = Path(root)
        dir_depth = len(root_path.relative_to(export_dir).parts)
        file_depth = dir_depth + 1

        if file_depth > MAX_WALK_DEPTH:
            dirnames[:] = []
            continue
        if file_depth >= MAX_WALK_DEPTH:

            dirnames[:] = []

        for filename in filenames:
            if not filename.endswith(".json"):
                continue
            path = root_path / filename
            try:
                size = path.stat().st_size
            except OSError:
                continue
            found.append(DiscoveredFile(path=path, size=size))

    return found

def classify_discovered_files(export_dir: Path) -> list[ClassifiedFile]:
    """`discover_json_files`, tagged with `classify.classify_file`'s
    verdict for each file. Unlike the old `recognized_json_files`, files
    that classify as `SourceSystem.UNKNOWN` are kept, not dropped —
    `bt-parse-gps`/`bt-parse-chat` run over them anyway (see this module's
    docstring). A file that fails to open or isn't valid JSON is still
    dropped here, not passed on to a `bt-parse-*` binary that would just
    fail to parse it anyway.
    """
    export_dir = export_dir.resolve()
    classified: list[ClassifiedFile] = []
    for f in discover_json_files(export_dir):
        try:
            with f.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        filename = relative_filename(f.path, export_dir)
        source_system = classify_file(filename, data)
        classified.append(ClassifiedFile(path=f.path, size=f.size, source_system=source_system))

    return classified

def _bucket(files: list[ClassifiedFile], batch_bytes: int) -> list[list[ClassifiedFile]]:
    """Shared smallest-first bucketing for both directory-walk mode
    (`plan_batches`) and explicit-file-list mode (`plan_batches_for_files`).
    """
    files = sorted(files, key=lambda f: (f.size, str(f.path)))

    batches: list[list[ClassifiedFile]] = []
    current: list[ClassifiedFile] = []
    current_bytes = 0
    for f in files:
        if current and current_bytes + f.size > batch_bytes:
            batches.append(current)
            current = []
            current_bytes = 0
        current.append(f)
        current_bytes += f.size
    if current:
        batches.append(current)

    return batches

def plan_batches(
    export_dir: Path, batch_bytes: int = DEFAULT_BATCH_BYTES
) -> list[list[ClassifiedFile]]:
    """Buckets all classified files, smallest first, into batches whose
    combined size stays under `batch_bytes` where possible.

    A single file larger than `batch_bytes` still gets its own batch
    rather than being dropped — unlike the Rust-side aggregate cap,
    nothing here is ever silently skipped for size reasons; an oversized
    file just means a slower batch of its own. (A file can still be
    dropped for not being valid JSON at all — see
    `classify_discovered_files`.)
    """
    return _bucket(classify_discovered_files(export_dir), batch_bytes)

def classify_files(paths: list[Path], root: Path) -> list[ClassifiedFile]:
    """`classify_discovered_files`'s per-file classification step, but for
    an explicit list of paths handed in directly (single/multi-file
    upload from the GUI) instead of a directory walk. `root` is used only
    for `relative_filename`'s filename-substring checks in
    `classify_file` — see `_common_root` in `cli.py` for how the GUI's
    file picker's selection turns into one. A path that fails to open or
    isn't valid JSON is dropped, same as the walk-based path.
    """
    classified: list[ClassifiedFile] = []
    for path in paths:
        path = Path(path)
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            size = path.stat().st_size
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue

        filename = relative_filename(path, root)
        source_system = classify_file(filename, data)
        classified.append(ClassifiedFile(path=path, size=size, source_system=source_system))

    return classified

def plan_batches_for_files(
    paths: list[Path], root: Path, batch_bytes: int = DEFAULT_BATCH_BYTES
) -> list[list[ClassifiedFile]]:
    """`plan_batches`'s bucketing, sourced from an explicit file list
    (`classify_files`) instead of a directory walk — the single/multi-file
    upload path. No `MAX_WALK_DEPTH` bound applies here since there's no
    walk: the caller (the GUI's file picker) already decided exactly which
    files to include.
    """
    return _bucket(classify_files(paths, root), batch_bytes)
