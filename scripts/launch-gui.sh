#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"

parsers_target_dir="$project_root/parsers/target"
gui_target_dir="$project_root/gui/target"

echo "==> Building parsers/ (release)"
CARGO_TARGET_DIR="$parsers_target_dir" cargo build --release --manifest-path "$project_root/parsers/Cargo.toml"

echo "==> Building gui/ (release)"
CARGO_TARGET_DIR="$gui_target_dir" cargo build --release --manifest-path "$project_root/gui/Cargo.toml"

parsers_release_dir="$parsers_target_dir/release"
gui_release_dir="$gui_target_dir/release"

if [ ! -x "$gui_release_dir/bt-gui" ]; then
  echo "error: expected bt-gui at $gui_release_dir/bt-gui after build, not found" >&2
  exit 1
fi

if [ ! -x "$gui_release_dir/bt-mapview" ]; then
  echo "error: expected bt-mapview at $gui_release_dir/bt-mapview after build, not found" >&2
  exit 1
fi

for bin in bt-parse-gps bt-parse-friends bt-parse-google bt-parse-chat; do
  if [ ! -x "$parsers_release_dir/$bin" ]; then
    echo "error: expected $bin at $parsers_release_dir/$bin after build, not found" >&2
    exit 1
  fi
done

brain_bin="$project_root/brain/.venv/bin/blacktape-brain"
if ! command -v blacktape-brain >/dev/null 2>&1 && [ ! -x "$brain_bin" ]; then
  echo "==> Setting up brain/ venv (blacktape-brain not found on PATH or at $brain_bin)"
  python3 -m venv "$project_root/brain/.venv"
  "$project_root/brain/.venv/bin/pip" install -e "$project_root/brain[dev]"

  if ! command -v blacktape-brain >/dev/null 2>&1 && [ ! -x "$brain_bin" ]; then
    echo "error: blacktape-brain still not found at $brain_bin after venv setup" >&2
    exit 1
  fi
fi

echo "==> Launching bt-gui (PATH includes $parsers_release_dir and $gui_release_dir)"
export PATH="$parsers_release_dir:$gui_release_dir:$PATH"
exec "$gui_release_dir/bt-gui"
