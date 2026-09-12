#!/usr/bin/env bash
# Run ON THE PI: every enrichment step in sequence (same order as the nightly timer).
cd "$(dirname "$0")/.."
for step in sync tracks wiki musicbrainz market; do
  echo "== $step"
  .venv/bin/python -m stereotv.discogs "$step"
done
