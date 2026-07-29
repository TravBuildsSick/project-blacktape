from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, TextIO

from blacktape_brain import analytics, explore, mapview, planner, search, signals, store, timeline
from blacktape_brain.classify import SourceSystem

PARSER_BINARIES = [
    "bt-parse-gps",
    "bt-parse-friends",
    "bt-parse-google",
    "bt-parse-chat",
]

PROVIDER_REQUIRED_SOURCE = {
    "bt-parse-friends": SourceSystem.SNAPCHAT,
    "bt-parse-google": SourceSystem.GOOGLE,
}

PROJECT_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_DB_SUBPATH = Path(".blacktape") / "store.db"

def default_db_path(export_dir: Path) -> Path:
    return Path(export_dir) / DEFAULT_DB_SUBPATH

def _common_root(files: list[Path]) -> Path:
    """The directory used as `relative_filename`'s root when ingesting an
    explicit file list (single/multi-file upload) instead of a directory
    walk — the common parent of every selected file, or that file's own
    parent when only one was picked. Plays the same role `export_dir`
    plays for the walk-based path, just derived from the selection
    instead of asked for up front. Also doubles as the directory the
    resulting database is placed under via `default_db_path`, when the
    caller doesn't pass an explicit `--db`.
    """
    resolved = [Path(f).resolve() for f in files]
    if len(resolved) == 1:
        return resolved[0].parent
    return Path(os.path.commonpath([str(f) for f in resolved]))

def _candidate_target_dirs() -> list[Path]:

    dirs = []
    env_target_dir = os.environ.get("CARGO_TARGET_DIR")
    if env_target_dir:
        dirs.append(Path(env_target_dir))
    dirs.append(PROJECT_ROOT / "parsers" / "target")
    return dirs

def _find_binary(name: str) -> str:
    on_path = shutil.which(name)
    if on_path:
        return on_path

    checked = []
    for target_dir in _candidate_target_dirs():
        for profile in ("release", "debug"):
            candidate = target_dir / profile / name
            if candidate.exists():
                return str(candidate)
            checked.append(candidate)

    checked_str = ", ".join(str(c) for c in checked)
    raise FileNotFoundError(
        f"could not locate parser binary '{name}' on PATH or at any of: {checked_str}"
    )

def _run_parser(name: str, export_dir: Path, files: list[Path]) -> Any:
    """Runs one bt-parse-* binary. `files`, if non-empty, is passed as an
    explicit file list (see parsers/common/src/lib.rs::read_json_files) so
    the binary parses exactly this batch instead of self-walking
    `export_dir`. An empty `files` list falls back to the binary's own
    directory walk — callers must only pass `files=[]` when that
    self-walk fallback is actually intended (see `_files_for_binary`'s
    caller in `stream_events`), since calling with an empty list to mean
    "nothing in this batch applies to this binary" would make it silently
    re-walk and re-process the *entire* export directory.
    """
    binary = _find_binary(name)
    cmd = [binary, str(export_dir), *(str(f) for f in files)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)

def _empty_raw(name: str) -> Any:
    if name == "bt-parse-friends":
        return {"categories": {}, "ranking": None}
    return []

def _files_for_binary(name: str, batch: list[planner.ClassifiedFile]) -> list[planner.ClassifiedFile]:
    required = PROVIDER_REQUIRED_SOURCE.get(name)
    if required is None:
        return batch
    return [f for f in batch if f.source_system is required]

def _confidence(source_system: SourceSystem) -> str:
    return "known" if source_system is not SourceSystem.UNKNOWN else "inferred"

def _table_count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]

def stream_events(
    export_dir: Path | None,
    db_path: Path | None = None,
    batch_bytes: int = planner.DEFAULT_BATCH_BYTES,
    tz: str | None = None,
    files: list[Path] | None = None,
) -> Iterator[dict[str, Any]]:
    """Runs all four parsers over `export_dir` in smallest-file-first
    batches (see `planner.plan_batches`), inserting each batch's records
    into the SQLite database at `db_path` as they're parsed and yielding a
    thin progress event per batch — record counts, not the records
    themselves. Callers that need the actual data (map/timeline/
    analytics/search) query the database afterward (see
    `timeline.py`/`analytics.py`/`explore.py`/`search.py`/`mapview.py`, and
    the `query` CLI subcommand below) instead of consuming it from these
    events, which is what makes this incremental rather than
    accumulate-and-recompute: nothing here holds more than one batch's
    parsed records in memory at a time.

    If `files` is given (single/multi-file upload from the GUI, as
    opposed to picking a whole export directory), `export_dir` is not
    walked at all — `planner.plan_batches_for_files` batches exactly the
    given paths, and `_common_root(files)` stands in for `export_dir`
    wherever it's otherwise used below (relative-filename computation,
    the default database location). `export_dir` itself may be `None` in
    that case.
    """
    if files:
        resolved_files = [Path(f) for f in files]
        export_dir = _common_root(resolved_files)
        batches = planner.plan_batches_for_files(resolved_files, export_dir, batch_bytes=batch_bytes) or [[]]
    else:
        assert export_dir is not None, "export_dir is required when files is not given"
        export_dir = Path(export_dir)
        batches = planner.plan_batches(export_dir, batch_bytes=batch_bytes) or [[]]

    db_path = Path(db_path) if db_path is not None else default_db_path(export_dir)
    conn = store.init_db(db_path)
    try:
        total_batches = len(batches)

        for index, batch in enumerate(batches):
            ingested_at = datetime.now(timezone.utc).isoformat()

            file_ids: dict[str, int] = {}
            confidence_by_file: dict[str, str] = {}
            for cf in batch:
                filename = planner.relative_filename(cf.path, export_dir)
                confidence = _confidence(cf.source_system)
                file_ids[filename] = store.register_file(
                    conn, filename, cf.size, ingested_at, cf.source_system.value, confidence
                )
                confidence_by_file[filename] = confidence

            inserted = {"gps": 0, "chats": 0, "google_signals": 0, "friend_records": 0}

            for name in PARSER_BINARIES:
                files = _files_for_binary(name, batch)
                if not files and batch:

                    result = _empty_raw(name)
                else:
                    result = _run_parser(name, export_dir, [f.path for f in files])

                if name == "bt-parse-gps":
                    inserted["gps"] = store.insert_gps_records(
                        conn, result or [], file_ids, confidence_by_file, tz=tz
                    )
                elif name == "bt-parse-chat":
                    inserted["chats"] = store.insert_chat_records(conn, result or [], file_ids, confidence_by_file)
                elif name == "bt-parse-google":
                    inserted["google_signals"] = store.insert_google_records(
                        conn, result or [], file_ids, confidence_by_file, tz=tz
                    )
                elif name == "bt-parse-friends":
                    result = result or {}
                    inserted["friend_records"] = store.insert_friend_records(
                        conn, result.get("categories") or {}, file_ids, confidence_by_file
                    )
                    ranking = result.get("ranking")
                    if ranking is not None and files:
                        ranking_filename = planner.relative_filename(files[0].path, export_dir)
                        store.upsert_ranking(conn, ranking, file_ids[ranking_filename], ingested_at)

            store.infer_and_backfill_direction(conn)
            conn.commit()

            totals = {
                "gps": _table_count(conn, "gps_points"),
                "chats": _table_count(conn, "chat_messages"),
                "google_signals": _table_count(conn, "google_signals"),
                "friend_records": _table_count(conn, "friend_records"),
            }

            yield {
                "phase": index,
                "phases": total_batches,
                "done": index == total_batches - 1,
                "files_in_batch": len(batch),
                "db_path": str(db_path),
                "inserted": inserted,
                "totals": totals,
            }
    finally:
        conn.close()

def ingest_stream(
    export_dir: Path | None,
    out: TextIO,
    db_path: Path | None = None,
    batch_bytes: int = planner.DEFAULT_BATCH_BYTES,
    tz: str | None = None,
    files: list[Path] | None = None,
) -> None:
    """Writes one NDJSON line per `stream_events` batch to `out`, flushing
    after each so a subprocess caller can consume progress as it lands
    instead of waiting for the whole export to finish. See
    `stream_events` for what `files` (single/multi-file upload) changes.
    """
    for event in stream_events(export_dir, db_path=db_path, batch_bytes=batch_bytes, tz=tz, files=files):
        out.write(json.dumps(event) + "\n")
        out.flush()

def ingest(
    export_dir: Path | None,
    db_path: Path | None = None,
    batch_bytes: int = planner.DEFAULT_BATCH_BYTES,
    out_path: Path | None = None,
    tz: str | None = None,
    files: list[Path] | None = None,
) -> Path:
    """Runs ingestion to completion, populating the SQLite database.
    Returns the resolved database path. If `out_path` is given, also
    writes a one-shot timeline/analytics/explore JSON snapshot there
    (queried from the finished database) for manual/debugging use — the
    database itself, not this file, is the durable result. See
    `stream_events` for what `files` (single/multi-file upload) changes.
    """
    final_event = None
    for final_event in stream_events(export_dir, db_path=db_path, batch_bytes=batch_bytes, tz=tz, files=files):
        pass
    assert final_event is not None
    resolved_db_path = Path(final_event["db_path"])

    if out_path is not None:
        conn = store.connect(resolved_db_path)
        try:
            snapshot = {
                "timeline": timeline.build_timeline(conn),
                "analytics": analytics.compute_analytics(conn),
                "explore": explore.build_explore(conn),
            }
        finally:
            conn.close()
        out_path.write_text(json.dumps(snapshot, indent=2))

    return resolved_db_path

def purge(export_dir: Path | None = None, db_path: Path | None = None) -> Path | None:
    """Deletes a previously ingested database (and its SQLite `-wal`/`-shm`
    sidecar files, if present from an interrupted run), resetting an
    export directory back to "no data loaded". Just removes the file
    rather than issuing `DELETE FROM` per table — simpler, and there's no
    undo requirement for a personal-data tool where the source export
    files themselves are untouched. Returns the path removed, or `None`
    if there was nothing to purge.
    """
    resolved = Path(db_path) if db_path is not None else default_db_path(export_dir)
    existed = resolved.exists()
    for candidate in (resolved, resolved.with_name(resolved.name + "-wal"), resolved.with_name(resolved.name + "-shm")):
        candidate.unlink(missing_ok=True)
    return resolved if existed else None

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="blacktape-brain")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser(
        "ingest", help="Run parsers over an export dir, populating a SQLite database."
    )
    ingest_parser.add_argument("export_dir", type=Path, nargs="?", default=None)
    ingest_parser.add_argument(
        "--files", type=Path, nargs="+", default=None,
        help="Ingest exactly these files instead of walking export_dir (single/multi-file upload)",
    )
    ingest_parser.add_argument("--db", type=Path, default=None, help="Defaults to <export_dir>/.blacktape/store.db")
    ingest_parser.add_argument("--out", type=Path, default=None, help="Optional one-shot JSON snapshot path")
    ingest_parser.add_argument("--batch-bytes", type=int, default=planner.DEFAULT_BATCH_BYTES)
    ingest_parser.add_argument(
        "--tz", default=None, help="IANA zone (e.g. America/New_York) to convert GPS timestamps into; defaults to the system's local timezone"
    )

    stream_parser = subparsers.add_parser(
        "ingest-stream",
        help=(
            "Run parsers over an export dir in smallest-file-first batches, writing one "
            "NDJSON progress event to stdout per batch as it's inserted into the database."
        ),
    )
    stream_parser.add_argument("export_dir", type=Path, nargs="?", default=None)
    stream_parser.add_argument(
        "--files", type=Path, nargs="+", default=None,
        help="Ingest exactly these files instead of walking export_dir (single/multi-file upload)",
    )
    stream_parser.add_argument("--db", type=Path, default=None, help="Defaults to <export_dir>/.blacktape/store.db")
    stream_parser.add_argument("--batch-bytes", type=int, default=planner.DEFAULT_BATCH_BYTES)
    stream_parser.add_argument(
        "--tz", default=None, help="IANA zone (e.g. America/New_York) to convert GPS timestamps into; defaults to the system's local timezone"
    )

    purge_parser = subparsers.add_parser(
        "purge", help="Delete a previously ingested database, resetting an export dir to no-data-loaded."
    )
    purge_parser.add_argument("export_dir", type=Path, nargs="?", default=None)
    purge_parser.add_argument("--db", type=Path, default=None, help="Defaults to <export_dir>/.blacktape/store.db")

    query_parser = subparsers.add_parser("query", help="Query a previously ingested database, printing JSON to stdout.")
    query_sub = query_parser.add_subparsers(dest="query_command", required=True)

    query_timeline = query_sub.add_parser("timeline")
    query_timeline.add_argument("--db", type=Path, required=True)
    query_timeline.add_argument("--since", default=None)
    query_timeline.add_argument("--until", default=None)
    query_timeline.add_argument("--limit", type=int, default=None)

    query_sub.add_parser("analytics").add_argument("--db", type=Path, required=True)
    query_sub.add_parser("explore").add_argument("--db", type=Path, required=True)

    query_friends = query_sub.add_parser("friends")
    query_friends.add_argument("--db", type=Path, required=True)
    query_friends.add_argument("--limit", type=int, default=None)

    query_friends_grouped = query_sub.add_parser(
        "friends-grouped",
        help="One row per friend identity (username/display_name) with all their categories combined, for a grouped-table UI.",
    )
    query_friends_grouped.add_argument("--db", type=Path, required=True)
    query_friends_grouped.add_argument("--limit", type=int, default=None)

    query_conversations = query_sub.add_parser(
        "conversations",
        help="Grouped per-conversation summaries (count, timestamp range) for a collapsed-box UI.",
    )
    query_conversations.add_argument("--db", type=Path, required=True)
    query_conversations.add_argument("--limit", type=int, default=None)

    query_chats = query_sub.add_parser("chats")
    query_chats.add_argument("--db", type=Path, required=True)
    query_chats.add_argument("--conversation", default=None, help="Narrow to one conversation's messages")
    query_chats.add_argument("--limit", type=int, default=None)

    query_search = query_sub.add_parser("search")
    query_search.add_argument("--db", type=Path, required=True)
    query_search.add_argument("text")
    query_search.add_argument("--limit", type=int, default=None)

    query_signals = query_sub.add_parser(
        "signals",
        help="Every google_signals row (wifi scans, detected activity), each with its nearest GPS point attached by time.",
    )
    query_signals.add_argument("--db", type=Path, required=True)
    query_signals.add_argument("--max-gap-seconds", type=int, default=signals.DEFAULT_MAX_GAP_SECONDS)
    query_signals.add_argument("--limit", type=int, default=None)

    query_map = query_sub.add_parser("map")
    query_map.add_argument("--db", type=Path, required=True)
    query_map.add_argument("--min-lat", type=float, required=True)
    query_map.add_argument("--max-lat", type=float, required=True)
    query_map.add_argument("--min-lon", type=float, required=True)
    query_map.add_argument("--max-lon", type=float, required=True)
    query_map.add_argument("--since", default=None)
    query_map.add_argument("--until", default=None)
    query_map.add_argument("--confidence", choices=["known", "inferred"], default=None)
    query_map.add_argument("--limit", type=int, default=None)
    query_map.add_argument(
        "--group-by-day", action="store_true", help="Return points bucketed into {day: [points]} instead of a flat list"
    )

    args = parser.parse_args(argv)

    if args.command in ("ingest", "ingest-stream"):
        if not args.export_dir and not args.files:
            parser.error(f"{args.command} requires either export_dir or --files")
        if args.export_dir and args.files:
            parser.error(f"{args.command} takes export_dir or --files, not both")

    if args.command == "ingest":
        ingest(args.export_dir, db_path=args.db, batch_bytes=args.batch_bytes, out_path=args.out, tz=args.tz, files=args.files)
    elif args.command == "ingest-stream":
        ingest_stream(args.export_dir, sys.stdout, db_path=args.db, batch_bytes=args.batch_bytes, tz=args.tz, files=args.files)
    elif args.command == "purge":
        if not args.export_dir and not args.db:
            parser.error("purge requires either export_dir or --db")
        removed = purge(args.export_dir, db_path=args.db)
        print(str(removed) if removed else "(nothing to purge)")
    elif args.command == "query":
        conn = store.connect(args.db)
        try:
            if args.query_command == "timeline":
                result: Any = timeline.build_timeline(conn, since=args.since, until=args.until, limit=args.limit)
            elif args.query_command == "analytics":
                result = analytics.compute_analytics(conn)
            elif args.query_command == "explore":
                result = explore.build_explore(conn)
            elif args.query_command == "friends":
                result = store.list_friend_records(conn, limit=args.limit)
            elif args.query_command == "friends-grouped":
                result = store.list_friends_grouped(conn, limit=args.limit)
            elif args.query_command == "conversations":
                result = store.list_conversations(conn, limit=args.limit)
            elif args.query_command == "chats":
                result = store.list_chat_messages(conn, conversation=args.conversation, limit=args.limit)
            elif args.query_command == "signals":
                result = signals.query_google_signals_with_location(
                    conn, max_gap_seconds=args.max_gap_seconds, limit=args.limit
                )
            elif args.query_command == "search":
                result = search.search(conn, args.text, limit=args.limit)
            elif args.query_command == "map":
                query_fn = mapview.query_bbox_by_day if args.group_by_day else mapview.query_bbox
                result = query_fn(
                    conn,
                    args.min_lat,
                    args.max_lat,
                    args.min_lon,
                    args.max_lon,
                    since=args.since,
                    until=args.until,
                    confidence=args.confidence,
                    limit=args.limit,
                )
            else:
                raise ValueError(f"unknown query command {args.query_command!r}")
        finally:
            conn.close()
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
