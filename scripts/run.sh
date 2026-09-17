#!/usr/bin/env bash
# Linux / macOS desktop launcher: creates the venv on first run, then starts stereo-tv in a window.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/pip install -q -r requirements.txt
    .venv/bin/python -m stereotv.setup
fi
exec .venv/bin/python -m stereotv --windowed "$@"
