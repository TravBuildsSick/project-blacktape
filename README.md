# project-blacktape

An easier way to visualize the data Google or Snapchat hand you when you hit
"Download my data" — a local, offline data-forensics tool split into two
layers plus a desktop GUI. No web server, no hosting — everything runs
against files on disk.

This document describes what the repo does and how to build/run it today.
For the longer-term pitch and planned features, see [VISION.md](VISION.md).
The initial commit ships without source comments; the rationale they held
(crash-driven size/depth caps, filter edge cases, parser fixes, etc.) is
preserved in [docs/removed-comments.html](docs/removed-comments.html).

## Layout

```
project-blacktape/
  parsers/                    Rust workspace: one bin crate per data source
    common/                    shared lib: json walking, timestamp/number
                                normalization, source-system detection
    bt-parse-gps/               GPS / location history export -> JSON
    bt-parse-friends/           friends list / relationships export -> JSON
    bt-parse-google/            Google activity export -> JSON
    bt-parse-chat/              chat/message export -> JSON
    legacy_reference/           legacy Python scanners the above were ported from
  brain/                       Python package: cross-source analysis
    blacktape_brain/
      classify.py                source-system detection (Python side)
      planner.py                  file discovery + batching for ingest
      align.py                    cross-source identity linking
      store.py                    SQLite-backed incremental storage
      signals.py                  attaches google_signals to nearby gps points
      mapview.py                  bounding-box GPS queries for the map view
      timeline.py                 unified chronological timeline
      analytics.py                summary statistics
      explore.py                  browsable views
      search.py                   ad-hoc search
      cli.py                      entrypoint (ingest / ingest-stream / query / purge)
  gui/                        Rust/Slint desktop shell (bt-gui), standalone
                              crate (not a parsers/ workspace member)
    src/
      main.rs                     app entrypoint, background ingest thread
      parsers.rs                  locates + drives the blacktape-brain CLI
      mapview_client.rs           talks to the bt-mapview child process
      bin/bt-mapview.rs           separate native window: Leaflet map view
    ui/
      app.slint                   main window UI
      mapview.html                Leaflet map page rendered by bt-mapview
  scripts/
    launch-gui.sh                builds parsers/ + gui/ and launches bt-gui
    hooks/pre-commit             blocks force-adding real export samples
```

## Why two layers

Parsing raw export formats (walking directories, coercing inconsistent
timestamps/numbers, detecting which app/export a file came from) is I/O-heavy
and benefits from Rust's speed and strict typing. Cross-source analysis
(linking identities across sources, building timelines, computing stats) is
exploratory and benefits from Python's flexibility. The two layers only talk
through JSON on stdout/stdin — no shared runtime, no network.

Each `bt-parse-*` binary takes one positional argument (an export directory
or, from the brain, an explicit curated file list) and prints a single JSON
value to stdout. No files are written by the parsers themselves, and there
are no other side effects.

The GUI (`gui/`) doesn't call the `bt-parse-*` binaries directly — it drives
the `blacktape-brain` CLI, which batches an export dir's files (smallest
first) and streams one aligned result back per batch, so the GUI can render
progressively instead of blocking until an entire export finishes.

## One-time setup: personal-data safety hook

This repo works with real personal-data export samples (Snapchat, Google
Takeout, etc.) as local test fixtures. `.gitignore` covers accidental
`git add .`, but a pre-commit hook is also provided to block a forced
add (`git add -f`) of anything matching a known export pattern. Enable
it once per clone:

```sh
ln -sf ../../scripts/hooks/pre-commit .git/hooks/pre-commit
```

## Building the parsers (Rust)

```sh
cd parsers
cargo build --release
cargo test
```

Binaries land in `parsers/target/release/`.

## Running the brain (Python)

```sh
cd brain
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

Run an ingest (parser binaries must be on `PATH`, or left in
`parsers/target/release/` where `cli.py` will find them automatically):

```sh
python -m blacktape_brain.cli ingest /path/to/export --out result.json
```

Ingested data is written incrementally to a SQLite database at
`<export_dir>/.blacktape/store.db`.

## Running the GUI

```sh
./scripts/launch-gui.sh
```

Builds `parsers/` and `gui/` in release mode and launches the `bt-gui`
desktop shell with `PATH` set up so it can find the `bt-parse-*` binaries
and the `blacktape-brain` console script. `bt-gui` has a hard runtime
dependency on the `brain/` Python venv being set up (see above) — the
launcher checks for this and fails with setup instructions if it's missing.

Clicking a day on the GPS nav page opens a separate native map window
(`bt-mapview`) rendered with Leaflet + live OpenStreetMap tiles — the one
part of this tool that isn't offline-only.

## Status

All four parsers (`bt-parse-gps`, `bt-parse-friends`, `bt-parse-google`,
`bt-parse-chat`) are ported from the legacy Python scanners and passing
tests; `bt-parse-gps` and `bt-parse-friends` are also verified against real
export samples, `bt-parse-google` and `bt-parse-chat` are not yet.

All `blacktape_brain` analysis functions (`align`, `timeline`, `analytics`,
`explore`, `search`) are implemented and passing tests, verified end-to-end
against both synthetic and real export data. The desktop GUI (`bt-gui`) —
including the batched/streaming ingest flow and the separate GPS map
window — builds and runs under a real display.

## License

MIT — see [LICENSE](LICENSE).
