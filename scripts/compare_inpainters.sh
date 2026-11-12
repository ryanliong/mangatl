#!/usr/bin/env bash
# Run each config in config/variants/ on samples/inpaint-test/input and time the pipeline stages.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$ROOT/samples/inpaint-test"
for cfg in "$ROOT"/config/variants/*.json; do
  name="$(basename "$cfg" .json)"
  echo "===== $name"
  MANGATL_CONFIG="$cfg" MANGATL_OUTPUT="$T/$name" "$ROOT/scripts/run_baseline.sh" "$T/input" 2>&1 | python3 -u -c '
import sys, time, re
t0 = time.time(); last = None; stages = {}
for line in sys.stdin:
    m = re.match(r"\[local\] Running (.+)$", line.strip()) or re.match(r"\[(\w+Inpainter)\] Inpainting resolution", line.strip())
    if m or line.startswith("[local] Saving"):
        now = time.time() - t0
        if last: stages.setdefault(last[0], []).append(now - last[1])
        name = "inpainting" if m and "Inpainter" in m.group(1) else (m.group(1) if m else None)
        last = (name, now) if name else None
    if line.startswith(("time per page", "translator peak", "ollama resident")): print("  " + line.strip())
for k, v in stages.items(): print(f"  {k:<20} " + "  ".join(f"{x:5.1f}s" for x in v))
'
done
