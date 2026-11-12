#!/usr/bin/env bash
# One-time setup: clone manga-image-translator at the pinned commit, build its venv, apply our patches.
# Prereqs: Homebrew, `brew install uv ollama`, Xcode Command Line Tools (see README).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIT="$ROOT/vendor/manga-image-translator"
MIT_REPO="https://github.com/zyddnys/manga-image-translator"
MIT_COMMIT="d6de6ab62a7be6afc0d07a3184149cdefbcd32f2"   # 2026-10-11; patches are written against this

command -v uv >/dev/null || { echo "uv not found: brew install uv" >&2; exit 1; }
xcode-select -p >/dev/null 2>&1 || { echo "Xcode Command Line Tools missing (needed to build pydensecrf); see README" >&2; exit 1; }

if [ ! -d "$MIT/.git" ]; then
  echo "Cloning manga-image-translator @ ${MIT_COMMIT:0:7}"
  mkdir -p "$MIT"
  git -C "$MIT" init -q
  git -C "$MIT" remote add origin "$MIT_REPO"
  git -C "$MIT" fetch -q --depth 1 origin "$MIT_COMMIT"
  git -C "$MIT" checkout -q FETCH_HEAD
elif [ "$(git -C "$MIT" rev-parse HEAD)" != "$MIT_COMMIT" ]; then
  echo "warning: vendor/manga-image-translator is not at the pinned commit; patches may not apply" >&2
fi

if [ ! -x "$MIT/.venv/bin/python" ]; then
  uv venv -q --python 3.12 "$MIT/.venv"
fi
# unsafe-best-match = pip's behaviour: the repo's extra wheel index is stale, PyPI has the pinned version.
VIRTUAL_ENV="$MIT/.venv" uv pip install -q --index-strategy unsafe-best-match -r "$MIT/requirements.txt"

for patch in "$ROOT"/patches/*.patch; do
  if git -C "$MIT" apply -R --check "$patch" 2>/dev/null; then
    echo "patch already applied: $(basename "$patch")"
  else
    git -C "$MIT" apply "$patch" && echo "patch applied:         $(basename "$patch")"
  fi
done

"$MIT/.venv/bin/python" -c "import torch; print('torch', torch.__version__, '| MPS available:', torch.backends.mps.is_available())"
echo "Setup done. Next: scripts/start_ollama.sh, then ollama pull huihui_ai/qwen3.5-abliterated:9b"
