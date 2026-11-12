#!/usr/bin/env bash
# Open the MangaTL window (uses the translator's venv, which ships Tk).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/vendor/manga-image-translator/.venv/bin/python" "$ROOT/gui.py" "$@"
