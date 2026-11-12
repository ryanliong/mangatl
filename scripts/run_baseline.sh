#!/usr/bin/env bash
# Run manga-image-translator (folder mode) against a local Ollama model.
# Usage: scripts/run_baseline.sh <input_folder> [extra manga_translator local args...]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MIT="$ROOT/vendor/manga-image-translator"
INPUT="$(cd "${1:?usage: run_baseline.sh <input_folder>}" && pwd)"; shift
OUTPUT="${MANGATL_OUTPUT:-${INPUT}-translated}"
LOG_DIR="$ROOT/logs"; mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/baseline-$(basename "$INPUT")-$STAMP.log"

# Translator: local Ollama via its OpenAI-compatible API. Text only; images never leave the machine.
export CUSTOM_OPENAI_API_BASE="${CUSTOM_OPENAI_API_BASE:-http://localhost:11434/v1}"
export CUSTOM_OPENAI_MODEL="${CUSTOM_OPENAI_MODEL:-huihui_ai/qwen3.5-abliterated:9b}"
export CUSTOM_OPENAI_REASONING_EFFORT="${CUSTOM_OPENAI_REASONING_EFFORT:-none}"
export HF_HUB_DISABLE_TELEMETRY=1
# Optional per-folder name glossary ("三枝 = Saegusa", one per line); seeds the rolling chapter context.
[ -f "$INPUT/glossary.txt" ] && export MANGATL_GLOSSARY="$INPUT/glossary.txt"
export PYTORCH_ENABLE_MPS_FALLBACK=1   # run unsupported MPS ops on CPU instead of crashing

# Re-apply our patches to the vendored repo (skips any already applied).
for patch in "$ROOT"/patches/*.patch; do
  git -C "$MIT" apply -R --check "$patch" 2>/dev/null || git -C "$MIT" apply "$patch"
done

curl -sf "${CUSTOM_OPENAI_API_BASE%/v1}/api/tags" >/dev/null \
  || { echo "Ollama isn't running. Start it with: ollama serve" >&2; exit 1; }

GPU_FLAG="${MANGATL_GPU_FLAG:---use-gpu}"   # set MANGATL_GPU_FLAG= to force CPU

# Page selection (optional): MANGATL_PAGES="1-31, 61-" and/or MANGATL_SKIP_PAGES="32-60". Page N is the Nth image
# in natural order. The chosen pages are linked into a temp folder that the translator reads instead of INPUT;
# output still goes to OUTPUT under the original filenames.
SELECTED="$(python3 "$ROOT/pagesel.py" "$INPUT" "${MANGATL_PAGES:-}" "${MANGATL_SKIP_PAGES:-}")" || exit 1
SOURCE_DIR="$INPUT"
if [ -n "${MANGATL_PAGES:-}${MANGATL_SKIP_PAGES:-}" ]; then
  SOURCE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mangatl-pages.XXXXXX")"
  trap 'rm -rf "$SOURCE_DIR"' EXIT
  while IFS= read -r f; do ln -s "$INPUT/$f" "$SOURCE_DIR/$f"; done <<< "$SELECTED"
fi
PAGES=$(printf '%s\n' "$SELECTED" | grep -c .)
TOTAL_PAGES=$(find "$INPUT" -maxdepth 1 -type f \( -iname '*.webp' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \) | wc -l | tr -d ' ')

# Resume by default: pages already in the output folder are skipped, and the rolling chapter context
# (names, summary) is saved there after every page so a resumed run keeps the same names.
# MANGATL_OVERWRITE=1 re-translates everything and starts the context fresh.
export MANGATL_CONTEXT_FILE="$OUTPUT/.mangatl_context.json"
OVERWRITE_FLAG=()
if [ "${MANGATL_OVERWRITE:-0}" = 1 ]; then
  OVERWRITE_FLAG=(--overwrite)
  rm -f "$MANGATL_CONTEXT_FILE"
fi
DONE=0
if [ "${MANGATL_OVERWRITE:-0}" != 1 ] && [ -d "$OUTPUT" ]; then
  DONE=$(printf '%s\n' "$SELECTED" | while read -r f; do if [ -f "$OUTPUT/$f" ]; then echo; fi; done | wc -l | tr -d ' ')
fi
[ "$PAGES" != "$TOTAL_PAGES" ] && echo "Page selection: $PAGES of $TOTAL_PAGES pages (only: ${MANGATL_PAGES:-all}, skip: ${MANGATL_SKIP_PAGES:-none})"
echo "Input: $INPUT ($PAGES pages) -> $OUTPUT | model: $CUSTOM_OPENAI_MODEL | source: ${MANGATL_SOURCE:-ja} | gpu: ${GPU_FLAG:-off}"
[ "$DONE" -gt 0 ] && echo "Resuming: $DONE of $PAGES pages already translated (MANGATL_OVERWRITE=1 to redo them)"

# Source language: ja = Japanese manga (default), zh = Chinese fan translation of Japanese manga.
case "${MANGATL_SOURCE:-ja}" in
  ja) DEFAULT_CONFIG="$ROOT/config/baseline.json" ;;
  zh) DEFAULT_CONFIG="$ROOT/config/chinese.json"
      # Chinese editions typeset phone/email screens and narration horizontally; credits sit on their own pages.
      export MANGATL_SKIP_HBLOCKS="${MANGATL_SKIP_HBLOCKS:-0}" ;;
  *)  echo "MANGATL_SOURCE must be ja or zh" >&2; exit 1 ;;
esac

# Resolve the config's relative gpt_config path, since we run from inside the vendored repo.
CFG="$LOG_DIR/baseline.resolved.json"
python3 -c 'import json,sys; c=json.load(open(sys.argv[1])); c["translator"]["gpt_config"]=sys.argv[2]+"/"+c["translator"]["gpt_config"]; json.dump(c,open(sys.argv[3],"w"),indent=2)' \
  "${MANGATL_CONFIG:-$DEFAULT_CONFIG}" "$ROOT" "$CFG"

cd "$MIT"
START=$(date +%s)
# /usr/bin/time -l reports peak RSS of the translator process (Ollama is measured separately below).
/usr/bin/time -l "$MIT/.venv/bin/python" -m manga_translator -v $GPU_FLAG local \
  -i "$SOURCE_DIR" -o "$OUTPUT" --config-file "$CFG" ${OVERWRITE_FLAG[@]+"${OVERWRITE_FLAG[@]}"} "$@" \
  2>&1 | tee "$LOG"
END=$(date +%s)

ELAPSED=$((END - START))
TRANSLATED=$(grep -c '^\[local\] Saving' "$LOG" || true)
PEAK=$(grep "maximum resident set size" "$LOG" | awk '{printf "%.2f GB", $1/1073741824}')
OLLAMA_MEM=$(curl -s "${CUSTOM_OPENAI_API_BASE%/v1}/api/ps" | python3 -c 'import sys,json; m=json.load(sys.stdin)["models"]; print(", ".join(f"{x["name"]} {x["size"]/1e9:.1f} GB" for x in m) or "not loaded")')
{
  echo "----- baseline summary -----"
  echo "pages:            $PAGES"
  echo "translated now:   $TRANSLATED (skipped $DONE already done)"
  echo "total time:       ${ELAPSED}s (includes first-run model downloads/loads)"
  echo "time per page:    $(python3 -c "print(f'{$ELAPSED/max($TRANSLATED,1):.1f}s')")"
  echo "translator peak:  $PEAK (RSS)"
  echo "ollama resident:  $OLLAMA_MEM"
  echo "log:              $LOG"
} | tee -a "$LOG"
