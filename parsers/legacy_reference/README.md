# Legacy Black Tape scanner source — for Rust port

Pulled from `Project-Blacktape-1/src/black_tape_engine/`. These are validated
against real export data — port logic close to verbatim, don't redesign.

- `gps_scanner.py` → port into `bt-parse-gps`
- `friends_scanner.py` → port into `bt-parse-friends`
- `google_signal_scanner.py` → port into `bt-parse-google`
- `chat_scanner.py` → port into `bt-parse-chat`
- `base_scanner.py` — trivial interface, just shows the shared `scan(filename, data)`
  contract the above four all followed. Not needed in Rust, included for context.
- `scanner.py` — the dispatcher that walked export files and called each scanner
  per file. Useful reference for how `walkdir` traversal in each Rust bin should
  behave (source system detection by filename, per-file dispatch).
- `data_aligner.py` — NOT part of the Rust port. This is cross-source identity
  linking and belongs in the Python brain (`blacktape_brain/align.py`).
- `view_shaping_reference.py` — NOT part of the Rust port either. This is the
  timeline/analytics/explore/friends-summary/search logic, already stripped of
  its `job_id`/cache lookups down to lines 298–581 of the original
  `vault_service.py`. Belongs in the Python brain (`timeline.py`, `analytics.py`,
  `explore.py`, `search.py`).
