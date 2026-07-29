# project-blacktape

Local, offline personal-data forensics tool. No web server, no hosting.
Two layers talking only through JSON on stdout/stdin:

- **`parsers/`** — Rust workspace. One bin crate per source type
  (`bt-parse-gps`, `bt-parse-friends`, `bt-parse-google`, `bt-parse-chat`),
  sharing a `common` crate (JSON walking, timestamp/number normalization,
  source-system detection). Each binary takes one positional arg (an export
  directory) and either walks it recursively itself (no further args —
  direct/manual CLI use), or, given extra positional args, treats those as
  an explicit, caller-curated file list and parses exactly those instead
  (`common::read_json_files`) — this is what `blacktape_brain`'s batching
  orchestration uses (see the `brain/` bullet below) instead of always
  handing a binary the whole export dir at once. Either way, prints a
  single JSON value to stdout; no files written, no other side effects.
  `common::walk_and_parse_json_files` centralizes the walk+read+parse+skip
  loop all four binaries used to duplicate inline, and bounds it:
  `MAX_WALK_DEPTH` (2) caps how far `WalkDir` recurses, `MAX_JSON_FILE_BYTES`
  (25 MiB) skips — silently, no confirmation prompt — any single `.json`
  file larger than that rather than reading it into memory (this per-file
  cap also applies in explicit-file-list mode, as a safety net in case a
  batch was curated incorrectly), and `MAX_TOTAL_JSON_BYTES` (256 MiB, walk
  mode only) caps the *combined* size of files actually read in one run,
  skipping further individual files (not aborting the whole run) once the
  running total would exceed it. All three exist because pointing the tool
  at a non-export directory (e.g. `~/Downloads` instead of an actual export
  dir) used to walk and fully buffer everything under it with no bound,
  which OOM'd and crashed the machine it ran on; see the GUI note below for
  the rest of that fix. Walk mode's `MAX_TOTAL_JSON_BYTES` is a blunt
  backstop for direct/manual invocation — it drops data past the cap rather
  than processing it. The GUI path avoids hitting it at all: `blacktape_brain`
  now decides what to hand each binary (see below), so the binaries
  themselves rarely self-walk during normal GUI use.
- **`brain/`** — Python package `blacktape_brain`. Takes each parser's JSON
  output, cross-links by identity, builds derived views: `align.py`
  (cross-source identity linking), `timeline.py`, `analytics.py`,
  `explore.py`, `search.py`. `planner.py` walks an export dir itself
  (mirroring `common::MAX_WALK_DEPTH`, kept in sync by hand) to discover
  `.json` files and their sizes, then `plan_batches` sorts them
  smallest-first and greedily buckets them so each batch's combined size
  stays under a threshold (`DEFAULT_BATCH_BYTES`, 5 MiB) — a file bigger
  than the threshold still gets its own (later, slower) batch rather than
  being dropped, unlike the Rust-side `MAX_TOTAL_JSON_BYTES` cap.
  `cli.py`'s `stream_events` runs all four `bt-parse-*` binaries once per
  batch (passing that batch's files as an explicit file list — see the
  `parsers/` bullet above), merging each binary's output into a running
  total (list concatenation for gps/google/chat, per-category
  concatenation + "last batch wins" ranking for friends) and yielding one
  fully-aligned result after every batch. `ingest` (writes a single
  `result.json`) and the new `ingest-stream` CLI subcommand (writes one
  NDJSON line per batch to stdout, flushed immediately) both consume this;
  `ingest-stream` is what the GUI now drives instead of calling the
  `bt-parse-*` binaries itself. `_find_binary`/`_run_parser` still locate
  and shell out to the parser binaries — PATH first, then each of
  `$CARGO_TARGET_DIR` (if set) and `parsers/target`, across both
  release/debug profiles — unchanged from before this batching work.
- **`gui/`** — Rust/Slint desktop shell (`bt-gui`), sibling crate to the
  `parsers/` workspace (not a member of it — has its own `Cargo.toml`/
  `Cargo.lock`). It no longer shells out to the `bt-parse-*` binaries
  directly — `src/parsers.rs::find_brain_binary` locates the
  `blacktape-brain` console script instead (PATH first, then
  `brain/.venv/bin/blacktape-brain`), and `spawn_ingest_stream` runs
  `blacktape-brain ingest-stream <export_dir>` with stdout piped. This
  means **`bt-gui` now has a hard runtime dependency on Python and the
  `brain/` venv being set up** — a real tradeoff accepted deliberately so
  the file-batching smartness (small-first, large-files-in-their-own-later-batch)
  lives once in `blacktape_brain` rather than being duplicated in Rust;
  `scripts/launch-gui.sh` checks for `blacktape-brain` before launching and
  fails with setup instructions if it's missing, rather than launching a
  GUI that can't do anything. `src/main.rs` runs the "Insert Data" flow
  (pick an export dir, run `blacktape-brain ingest-stream` against it) on a
  background thread rather than the UI thread: each NDJSON line read from
  the brain's stdout is one completed batch's aligned result, sent over an
  `mpsc` channel to a Slint `Timer` on the UI thread, which drains at most
  one message per tick — fully re-rendering with one batch's data before it
  ever looks at the next message — instead of blocking the window for the
  whole run and updating once at the end. `src/parsers.rs::spawn_ingest_stream`
  also caps the spawned `blacktape-brain` process's virtual memory via
  `setrlimit(RLIMIT_AS, 512 MiB)` (Unix-only, `pre_exec`) — Unix rlimits are
  inherited across fork/exec, so this bounds every `bt-parse-*` subprocess
  the brain spawns in turn too, not just the brain process's own footprint.
  Renders the brain's aligned JSON (keyed `gps`/`friends`/`google_signals`/`chats`
  — `blacktape_brain.align`'s output shape, not the raw `bt-parse-*` binary
  names) via `ui/app.slint`. `scripts/launch-gui.sh` builds `parsers/` and
  `gui/` in release mode, resolves the actual output dir (same
  `$CARGO_TARGET_DIR`-aware logic), verifies all five binaries exist there
  plus `blacktape-brain`, and execs `bt-gui` with `PATH` set accordingly —
  the one-command way to get a working GUI regardless of where Cargo
  happens to put build output on a given machine. `cargo build`/`cargo
  build --release` from `gui/` and the launcher script both verified
  working, including a real launch under an active display.
- **GPS map (`gui/src/bin/bt-mapview.rs`, `gui/src/mapview_client.rs`,
  `gui/ui/mapview.html`)** — the GPS nav page (index 1) no longer dumps raw
  point JSON as text; it lists one row per day the export has GPS points
  for (`blacktape_brain.mapview.query_bbox_by_day`, exposed via
  `blacktape-brain query map --group-by-day` — see `brain/src/blacktape_brain/mapview.py`),
  and clicking a row toggles it into/out of a selection whose union of
  points is pushed to a map. Day grouping only means anything in the
  user's own timezone, not the source export's UTC timestamps — see
  `align.to_local_time`, applied at ingest in `store.insert_gps_records`
  (optional `--tz`/`tz=` override, e.g. for a batch ingested from a
  different timezone than the one the GUI is currently running in;
  defaults to the system's local timezone) — below.
  The map itself is a **separate native window/process** (`bt-mapview`),
  not a webview embedded inside `bt-gui`'s own Slint window: Slint's
  winit backend doesn't officially support attaching a child native view
  to part of its window, so a second, independently-driven `tao`+`wry`
  window was chosen over fighting that integration. `bt-gui` spawns it
  lazily (first day-group click) via `mapview_client::ensure_running_and_send`,
  found next to `bt-gui`'s own executable (same target dir, since it's a
  second `[[bin]]` in the same crate) or on PATH, and drives it over piped
  stdin — one JSON points-array per line, full-replace each time — mirroring
  how `bt-gui` already drives `blacktape-brain` over piped stdout. If the
  user closes the map window, the next toggle detects the dead child
  (`MapviewHandle::is_alive`) and respawns fresh with just the
  currently-selected points, rather than erroring. Renders via Leaflet +
  live OpenStreetMap tiles (`gui/ui/mapview.html`) — the one part of
  project-blacktape that isn't offline-only, a deliberate tradeoff (chosen
  over an offline-tiles or no-basemap alternative) so the map has real
  street/imagery context. Verified: both binaries build clean
  (`cargo build`/`cargo build --release` from `gui/`), and `bt-mapview` run
  standalone under a real display accepts a points array over stdin
  without erroring.
- **`parsers/legacy_reference/`** — original Python scanners
  (`gps_scanner.py`, `friends_scanner.py`, `google_signal_scanner.py`,
  `chat_scanner.py`, `data_aligner.py`, `vault_service.py` excerpts, etc).
  Porting reference only — not maintained going forward, do not edit.

## Porting process (apply to every parser and brain module)

1. Port close to verbatim from the matching `legacy_reference/*.py` file.
   Don't "improve" logic while porting — structural cleanup comes later,
   once behavior is verified.
2. Write unit tests covering the ported logic, including any edge cases
   called out in the legacy code's comments or the current known-issues
   list below.
3. **Sanity-check against a real export sample before moving on.**
   Passing unit tests is not sufficient — both real bugs found in
   `bt-parse-gps` were only caught by testing against actual exported
   data, not by the unit tests. Do not mark a parser done until it's been
   run against at least one real export.
4. Update the Status section below (and the known-issues list) when a
   parser or module changes state.

## Status

- **`bt-parse-gps`** — done and verified. Ported source-system detection,
  layer classification, timestamp normalization, the
  `google_timeline_edits` special case (E7 coords, activity/wifi context
  merging), recursive `hunt()` for nested lat/lon, Location History
  fallback. Verified twice against a real Snapchat `location_history.json`
  export. 4/4 tests passing including a regression test for known issue
  #3 below.
- **`bt-parse-friends`** — done and verified. Ported all 8 `FRIEND_KEYS`
  categories plus account ranking/statistics from `friends_scanner.py`.
  6/6 tests passing. Verified against a real Snapchat `friends.json`
  export (25 friends, categories and ranking all correct).
- **`bt-parse-google`** — ported from `google_signal_scanner.py`
  (`activity` and `wifi_scan` events from Timeline Edits). 7/7 tests
  passing, but **not yet verified against a real Google Takeout export**
  — this scanner's marker/key names were never confirmed against real
  data even in the original Python. Do this before trusting its output.
- **`bt-parse-chat`** — ported from `chat_scanner.py` (recursive
  message-shaped-dict search). 14/14 tests passing, but **not yet
  verified against a real chat export** — the generic `CONTENT_INDICATORS`
  keys (`"data"`, `"message"`, etc.) are broad enough that false
  positives are a real risk on real data. Do this before trusting its
  output.
- **`blacktape_brain`** — done. `align.py` cross-links each parser's raw
  JSON (keyed by `bt-parse-*` binary name) into the aligned
  `gps`/`friends`/`chats`/`google_signals`/`identity` shape the views
  consume; ported `parse_timestamp` from `data_aligner.py`, extended to
  handle epoch int/float/numeric-string timestamps (bt-parse-chat's `time`
  field can carry a raw epoch int) so mixed epoch/ISO conversations sort
  chronologically instead of as strings. `identity` is always `{}` — the
  legacy `GenericScanner` (`parsers/legacy_reference/scanner.py`) that
  populated it was never ported to a `bt-parse-*` binary. `timeline.py`,
  `analytics.py`, `explore.py` ported close to verbatim from
  `get_timeline`/`get_analytics`/`get_explore` in
  `view_shaping_reference.py`; `analytics.py` inlines a `_friend_summary`
  helper since the legacy version's `self.get_friends(job_id)` call isn't
  part of this package. `search.py` is a from-scratch substring search
  over chat content — the legacy `get_explore.search` delegated to an
  `EngineSearch` class that isn't defined anywhere in `legacy_reference`
  (an external, unported dependency). 36/36 tests passing. Verified
  end-to-end via `python -m blacktape_brain.cli ingest <dir> --out
  result.json` against a synthetic export dir (friends/blocked users,
  chat conversation with a mixed epoch+ISO timestamp, one Google Timeline
  Edits activity signal, one Snapchat Location History point) — aligned
  output, timeline ordering, analytics counts, explore, and search all
  correct. `planner.py` (file discovery/batching) and `cli.py`'s
  `stream_events`/`ingest`/`ingest-stream` (batched orchestration,
  per-binary merge logic, NDJSON streaming) added on top of that — 47/47
  tests passing across the package. Verified end-to-end against real
  `bt-parse-*` binaries via `blacktape-brain ingest-stream <dir>`
  (multiple batches, `--batch-bytes` forced small to exercise more than
  one batch), and via the GUI's "Insert Data" flow launched under a real
  display.

## Known issues / things to watch for

- **(Fixed — real gap: no parser reliably ignored non-export data)**
  `common::detect_source_system` was dead code (filename-only, and never
  called), while `bt-parse-gps` had its own separate, better filename-
  plus-content-marker version that wasn't shared with the other three
  binaries. Worse, `bt-parse-chat` had no source-system check at all —
  it matched any dict with a generic `"data"`/`"message"`/etc.-shaped key
  (`CONTENT_INDICATORS`), which is exactly the false-positive shape a
  non-export directory can produce. Consolidated onto one detector in
  `common::detect_source_system(filename, data)`, ported to
  `blacktape_brain.classify.classify_file` for the Python side. Filename
  checks first (`timeline edits`, `takeout`, `mydata`, `chat_history`,
  `friends`/`ranking`, `snapchat`/`google` prefixes), falling back to
  top-level JSON key markers for ambiguous filenames. Deliberately no
  filename rule for `location_history` — Snapchat's own location export
  uses that exact filename too, so an earlier version of this
  consolidation that matched it by filename alone misdetected real
  Snapchat location exports as Google; caught by
  `detects_real_snapchat_export_by_key_set_when_filename_is_ambiguous`
  before it shipped.
  **Where the gating actually lives**: initially added directly to all
  four `bt-parse-*` binaries (early-return `Unknown` → skip), then pulled
  back out on request — "the brain should handle the thinking, the
  parser should just parse." The binaries now only use
  `detect_source_system` for tagging the `source_system` output field
  (`bt-parse-gps` only); the actual "should this file be processed"
  decision lives in `blacktape_brain.planner.recognized_json_files`,
  which `plan_batches` filters through before ever building a batch. A
  file that fails to open or isn't valid JSON is dropped the same way. 45
  Rust tests / 55 Python tests passing. Verified end-to-end via
  `blacktape-brain ingest` against a synthetic export dir with a
  recognized friends file, chat file, and Google Timeline Edits file
  alongside an unrecognized junk file — the junk file never reached any
  parser. See `parsers/common/src/lib.rs::detect_source_system`,
  `brain/src/blacktape_brain/classify.py`, and
  `brain/src/blacktape_brain/planner.py::recognized_json_files`.
- **(Fixed in gps, check for it in every other parser)** Timestamp
  fallback bug: a missing/invalid timestamp must default to
  `1970-01-01 00:00:00 UTC`, not fall back to itself.
- **(Fixed in gps, check for it in every other parser)** Filename must be
  threaded through to wherever the output record's `source` field is set
  — it's easy to leave `source` empty by forgetting to pass it down a
  call chain.
- **(Fixed in gps, inherited from legacy code — check other parsers too)**
  `GOOGLE_MARKERS`-style key lists can accidentally include keys that are
  actually Snapchat section names (or vice versa). Snapchat markers
  should be checked first; verify marker lists against a real export,
  not just against the legacy source.
- **Unverified**: gps's `GOOGLE_MARKERS` (`Home & Work`, `Timeline Edits`,
  `timelineEdits`) hasn't been tested against a real Google Takeout
  sample yet. Worth doing if/when one's available — the same
  misclassification bug could be hiding there too.
- **(Fixed, found across all four parsers via real-export testing)**
  All four parsers independently duplicated the same filename-relative
  logic (`path.strip_prefix(&export_dir)`) for their filename-substring
  checks (`"friends"`/`"ranking"` in bt-parse-friends, `"timeline
  edits"` in bt-parse-google, source-system detection in bt-parse-gps,
  etc.). If `export_dir` pointed directly at a single file rather than
  its containing directory, the strip produced an empty string, so every
  filename check silently matched nothing — no error, just empty output.
  Fixed by extracting a shared `common::relative_filename()` (falls back
  to the file's own name when stripping yields empty) and switching all
  four parsers to it instead of the duplicated inline version. Note: the
  tool's actual usage contract is still "pass the export directory," not
  a single file — but pointing it at one file now works correctly too
  rather than failing silently.
- **(Fixed — real incident, not just a theoretical risk)** Pointing the
  GUI's "Insert Data" at a non-export directory (a real report: the
  user's `~/Downloads`) crashed the machine it ran on. `walk_json_files`
  had no recursion-depth bound and `read_json_file` read every matched
  `.json` file into memory with no size cap, and the GUI ran all four
  parsers synchronously against the same directory — unbounded memory
  growth, times four, on the UI thread. Fixed with layered bounds: `common`
  now caps walk depth (`MAX_WALK_DEPTH`) and skips oversized files
  (`MAX_JSON_FILE_BYTES`) before ever reading them; the GUI additionally
  caps each parser subprocess's virtual memory via `setrlimit` and runs
  parsing on a background thread instead of the UI thread. None of these
  alone was considered sufficient — the size/depth caps protect the
  parser binaries (and thus the CLI/brain path too, which shares
  `common`), the rlimit is a backstop in case something still slips
  through, and moving off the UI thread only fixes GUI responsiveness, not
  memory use. See `parsers/common/src/lib.rs`, `gui/src/parsers.rs`, and
  `gui/src/main.rs`.
- **(Fixed — real report: GUI froze on a directory with many files that
  each individually passed the per-file cap)** `MAX_JSON_FILE_BYTES`
  bounded each `.json` file but not the combined size of every file
  `walk_and_parse_json_files` actually read in one run. A directory with
  many files each just under the 25 MiB per-file cap could still sum to
  enough resident memory (parsed `serde_json::Value` trees run several
  times the raw file size) to make the GUI look hung, even with the
  per-file cap and the GUI's per-subprocess rlimit in place. First fixed
  with a Rust-side `MAX_TOTAL_JSON_BYTES` aggregate cap that skipped
  further files once a run's running total got too large — but that
  silently dropped real export data past the cap, which isn't
  acceptable for a forensics tool. Superseded by moving the file-batching
  decision into `blacktape_brain` instead of capping-and-dropping in Rust:
  `planner.py` sorts an export dir's files smallest-first and buckets
  them so nothing is ever skipped — a batch just gets slower once it
  contains a large file, instead of the file being dropped — and
  `cli.py`'s `ingest-stream` streams one merged result per batch back to
  the GUI, so small files' results render immediately while larger
  batches are still being worked through. `MAX_TOTAL_JSON_BYTES` still
  exists in `parsers/common` as a backstop for a `bt-parse-*` binary
  invoked directly in its own self-walk mode (manual/CLI use bypassing
  the brain), but the GUI path no longer relies on it. See
  `brain/src/blacktape_brain/planner.py`, `brain/src/blacktape_brain/cli.py`,
  `parsers/common/src/lib.rs::read_json_files`, and `gui/src/main.rs`.
- **(Fixed — real report: GUI showed an error at the bottom of the log
  when pointed at `~/Downloads`)** `align.py`'s `parse_timestamp` called
  `datetime.fromtimestamp()` on any numeric value with no bounds check.
  `bt-parse-chat`'s generic message-shaped-dict heuristic is broad enough
  that scanning a non-export directory can pick up an unrelated large
  number as a "timestamp" field; `fromtimestamp` raised (`year 57242` is
  out of `datetime`'s range) and crashed the whole `blacktape-brain`
  process — taking down every batch's already-parsed data with it, not
  just the one bad record, since `stream_events` had no per-record error
  isolation. Fixed by catching `OverflowError`/`OSError`/`ValueError`
  around both the raw int/float and numeric-string epoch-conversion paths
  in `parse_timestamp` and falling back to `str(value)`, matching the
  function's existing fallback for other unrecognized formats. Verified
  against the real `~/Downloads` directory that triggered the original
  report — previously crashed with a traceback, now completes and
  produces output. See `brain/src/blacktape_brain/align.py::parse_timestamp`.

## Build / test commands

```sh
# Rust parsers
cd parsers && cargo build --release && cargo test

# Python brain
cd brain && python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Handling real export data

This repo works with real personal data exports (Snapchat, Google
Takeout, etc.) as test fixtures during parser development. Never commit
real export samples or their derived output — see `.gitignore`. If a
task involves adding a new local sample for testing, keep it out of any
tracked path.
