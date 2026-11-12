# mangatl

Translates a folder of Japanese manga pages (`.webp`, `.jpg`, `.jpeg`, `.png`) into English, fully on
a Mac: text detection, OCR, translation with a local LLM, erasing the original text and typesetting the English
back into the bubbles. Also reads Chinese editions of Japanese manga. It wraps [manga-image-translator](https://github.com/zyddnys/manga-image-translator) (vendored at a
pinned commit, with patches) and uses a local LLM through Ollama.

**Privacy:** images never leave the machine. The only network calls during a run go to the local Ollama
server, and they contain only the OCR'd text. The first run downloads detector, OCR and inpainting model weights
(about 750MB) from GitHub and Hugging Face. Hugging Face telemetry is turned off.

## Setup from scratch (Apple Silicon)

1. **Homebrew tools**
   ```sh
   brew install uv ollama
   ```
2. **Xcode Command Line Tools** (needed to compile `pydensecrf`, which the translator imports on startup)
   ```sh
   xcode-select --install
   ```
   If the dialog's estimate is hours, cancel it and install from Terminal instead:
   ```sh
   touch /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
   softwareupdate --list | grep -i "command line"      # copy the newest label
   sudo softwareupdate -i "Command Line Tools for Xcode <version>" --verbose
   rm /tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress
   ```
   Without the `touch` line, `softwareupdate` reports "no such update".
3. **Translator + patches**: clones manga-image-translator at the pinned commit, creates a Python 3.12 venv,
   installs requirements and applies everything in `patches/`. It's safe to re-run.
   ```sh
   scripts/setup.sh
   ```
4. **Ollama + model**: start the server in its own terminal, then pull the model (6.6GB):
   ```sh
   scripts/start_ollama.sh
   ollama pull huihui_ai/qwen3.5-abliterated:9b
   ```
5. **Check the model translates faithfully** (text only, no images):
   ```sh
   python3 scripts/test_translator.py
   ```

## Usage

### Desktop window

```sh
scripts/make_app.sh      # once: builds MangaTL.app in this folder (drag it to /Applications or the Dock)
open MangaTL.app         # or: scripts/gui.sh
```

The window starts and stops Ollama, lets you pick the pages folder, model, font size and whether to skip
credits blocks, and shows a progress bar with the current page and stage, time left, and seconds per page.
- It runs the same `run_baseline.sh` under the hood, so the output and logs match the command line.
- When a run finishes you get a macOS notification, and buttons to open the output folder or the full log.
- If you quit after the window started Ollama, it asks whether to stop Ollama too, which frees about 6GB.
- Settings are remembered in `.gui_settings.json`, which is gitignored.
- Rebuild the app with `scripts/make_app.sh` if you move the project folder, because it stores an absolute path.

### Command line

```sh
scripts/run_baseline.sh <input_folder>
```

**Interrupted runs resume.** If a run stops partway (Ollama quit, Mac slept, Ctrl-C), run the same command
again:
- Pages already in the output folder are skipped.
- The character names and story summary are reloaded from `<output>/.mangatl_context.json`, so names stay the
  same across the break.

Use `MANGATL_OVERWRITE=1` to redo everything. In the GUI, pressing Translate on a folder that already has output
asks whether to **Resume** or **Start over**.

**Translating part of a book.** To skip pages you've already read in a compilation:

```sh
MANGATL_SKIP_PAGES="32-60" scripts/run_baseline.sh <input_folder>
```

In the GUI, use the **Pages → Only / Skip** fields. Page N is the Nth image in reading order (natural sort:
1, 2, … 10, 11), which matches the page number in your reader. For files named `1.webp`, `2.webp`, … it's also
the file number. Skipped pages aren't copied to the output folder. A later run with a different selection
resumes normally and only translates pages that aren't done yet. `python3 pagesel.py <folder> "" "32-60"` lists
which files a selection picks.

**For long runs, start Ollama from the GUI or your own terminal.** Don't use a server started by a tool or agent
session that may stop it.

- Output goes to `<input_folder>-translated/` with the **same filenames and formats**.
- Each run writes a log to `logs/`.
- The run ends with a summary: pages, time per page, peak memory of the translator and of Ollama.
- Put test chapters under `samples/`, which is gitignored, as are `logs/` and `vendor/`.

**Speed** (M5, 24GB, 1280×1800 pages): about **23s per page**, so a 20-page chapter takes about 8 minutes.
Per page, roughly: OCR 5–11s, translation 7s, inpainting 4–5s, detection 1.5s. Peak memory is about 8–9GB
for the translator plus about 5.7GB for Ollama. Close memory-heavy apps (browsers) before long runs, or macOS
will swap and slow everything down.

## Configuration

`config/baseline.json` holds the manga-image-translator settings (run `config-help` for the full schema):

| Setting | Value | Why |
|---|---|---|
| `translator` | `custom_openai` | Ollama's OpenAI-compatible API (`localhost:11434/v1`) |
| `detector` | `default` | The repo's manga text detector |
| `ocr` | `mocr` (manga-ocr) + `use_mocr_merge` | Best for Japanese manga |
| `inpainter` | `lama_large`, size 1024, fp16 | Best erase quality at about 5s per page. 2048 took 25–41s per page, and was the only setting where no bubble went missing in testing |
| `renderer` | `manga2eng`, centered | English-specific layout inside the detected bubble |

`config/gpt_config.yaml` sets the LLM temperature (0.3).

Environment variables (all optional):

| Variable | Default | Effect |
|---|---|---|
| `CUSTOM_OPENAI_MODEL` | `huihui_ai/qwen3.5-abliterated:9b` | Ollama model to translate with |
| `CUSTOM_OPENAI_REASONING_EFFORT` | `none` | Turns off Qwen's thinking mode (see patches) |
| `MANGATL_SOURCE` | `ja` | `ja` = Japanese pages. `zh` = Chinese fan translations of Japanese manga (vertical, right to left): uses `config/chinese.json` (OCR `48px`, which reads simplified and traditional Chinese) and `config/gpt_config_zh.yaml` (repo prompt + Japanese-romanization rule for names, family name first, Name-honorific format). Also turns `MANGATL_SKIP_HBLOCKS` off by default, since Chinese editions typeset phone and email screens horizontally |
| `MANGATL_GPU_FLAG` | `--use-gpu` | Set to empty to force CPU |
| `MANGATL_CONFIG` / `MANGATL_OUTPUT` | `config/baseline.json` / `<input>-translated` | Override config file / output folder |
| `MANGATL_MIN_FONT_RATIO` | `0.02` | Minimum font size as a fraction of page width (26px at 1280). Lower it, e.g. `0.017` (about 22px), if text overflows small bubbles; raise it if text is hard to read |
| `MANGATL_DOWNSCALE_CONSTRAINT` | `0.3` | How far the renderer may initially shrink text relative to the Japanese glyph size |
| `MANGATL_MERGE_FRAGMENTS` | `1` | Merge side-by-side columns that belong to one bubble |
| `MANGATL_SKIP_HBLOCKS` | `1` | Leave wide horizontal text blocks (credits, contact details, afterwords) untranslated |
| `MANGATL_SKIP_HBLOCK_MIN_WIDTH` | `0.3` | A horizontal block at least this fraction of the page width is skipped |
| `MANGATL_PAGES` | all | Translate only these pages, e.g. `1-31, 61-` (`61-` means to the end, `-10` means from the start) |
| `MANGATL_SKIP_PAGES` | none | Leave these pages out, e.g. `32-60, 75`. Can be combined with `MANGATL_PAGES` |
| `MANGATL_OVERWRITE` | `0` | Runs resume by default: pages already in the output folder are skipped. `1` re-translates everything and starts the name list fresh |
| `MANGATL_CONTEXT` | `1` | Rolling chapter context: each page is translated with the names used so far, a short story summary and the previous page. Costs about 1–2s per page |
| `MANGATL_SUMMARY_EVERY` | `4` | Refresh the story summary every N pages (it costs most of the extra time) |
| `MANGATL_RENDER_DEBUG` | unset | Print per-region font size, bubble size and position to the log |

## Model

**Why an "abliterated" model:** stock `qwen3.5:9b` over-refuses on fiction: fights, threats and crude dialogue
often came back as "I can't help with that", even with an explicit "translate faithfully" system prompt, and the
refusal text then got typeset into the bubbles. `huihui_ai/qwen3.5-abliterated:9b` is the same model with its
refusal behaviour removed, so every line gets translated.

**Switching models:** pull the model, run the faithfulness check against it, then set `CUSTOM_OPENAI_MODEL`:
```sh
ollama pull <model>
python3 scripts/test_translator.py --model <model>
python3 scripts/test_translator.py --model <model> --from-log logs/<a previous run>.log   # replays real OCR'd lines
CUSTOM_OPENAI_MODEL=<model> scripts/run_baseline.sh <folder>
```
- **Faster / less memory:** `huihui_ai/qwen3.5-abliterated:4b`
- **Better quality:** `huihui_ai/qwen3.5-abliterated:9b-q8_0` (11GB; closes the memory headroom)

Hybrid-thinking models need `CUSTOM_OPENAI_REASONING_EFFORT=none`, which is the default. Without it, the model spends
its whole token budget reasoning and returns empty translations.

## Patches

manga-image-translator is pinned to `d6de6ab` (2026-10-11) in `scripts/setup.sh`. Each run re-applies these
patches if needed, and refuses to start if one no longer applies:

| Patch | Fixes |
|---|---|
| `custom_openai.patch` | (1) Passes `reasoning_effort` to Ollama. Without it, qwen3.5 used all 2,048 tokens thinking, so every bubble came out blank. (2) Rolling chapter context (see below) |
| `kumiko-opencv5-lines.patch` | Panel detection crashed on every page (`IndexError`) because the bundled Kumiko expects OpenCV 4's line format but the repo pins OpenCV 5. The crash forced the fallback reading-order sort |
| `manga2eng-downscale-constraint.patch` | The renderer could only shrink text to 80% of the Japanese glyph size, so English overflowed. Now 30% and configurable |
| `manga2eng-fit-stopgap.patch` | Merges split bubble fragments; adds a readable minimum font (text used to shrink to about 8px and vanish); widens sliver-shaped bubbles; shrinks text until it fits; adds the debug output |
| `skip-horizontal-blocks.patch` | Skips wide horizontal blocks *before* inpainting, so credits and contact details keep their original Japanese |

**Don't update the vendored repo casually.** The project changes often and the patches target this exact
commit. If you do update it, run `scripts/setup.sh` and fix any patch that fails to apply.

## Chapter context and glossary

Each run is treated as one chapter. Every page's prompt includes:
- the names used so far;
- a 1–2 sentence story summary covering who's who, refreshed every 4 pages;
- the previous page's lines and translations.

The model reports new names on an extra `<|names|>` line and the summary on `<|summary|>`. Both are stripped
before typesetting. The log shows the running state as `[context]` lines.

To force readings for a series, put a `glossary.txt` in the pages folder, one name per line. These entries
always win over the model's guesses:

```
三枝 = Saegusa
瀨川玲奈 = Segawa Reina
東和 = Towa
```

## Tools

- `scripts/test_translator.py`: checks a model for refusals, censoring and skipped lines, using built-in sample lines or
  replaying a run's OCR'd text. Exits 1 if any line is refused, censored or left untranslated.
- `scripts/compare_inpainters.sh`: runs every config in `config/variants/` on `samples/inpaint-test/input`
  and times each stage. This is how the inpainter setting was chosen.

## Known limitations

- Bubbles are found with an edge-detection heuristic, so very small or oddly shaped bubbles can be misjudged.
  Text sitting at the minimum font size may spill slightly past a small bubble.
- Names are consistent within a chapter but not always *correct*: the first reading the model picks sticks for
  the whole chapter (e.g. 三枝 → "Mieji" instead of Saegusa). Fix a name with a glossary (below).
- LLM output varies slightly between runs, so re-running a page can change wording and therefore layout.
- No per-page JSON or render-only mode: to fix a line, re-run the page.

Possible next steps: editable per-page JSON with `--render-only`, and a custom typesetter.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Ollama isn't running` | `scripts/start_ollama.sh` in another terminal |
| `patch does not apply` at startup | The vendored repo changed, or a patched file was edited by hand. `git -C vendor/manga-image-translator diff`, then regenerate or fix the patch |
| `ModuleNotFoundError: pydensecrf` | Command Line Tools weren't installed when you ran setup. Install them, then re-run `scripts/setup.sh` |
| Bubbles contain "I cannot translate…" or are blank | The model is refusing. Run `scripts/test_translator.py --model <model>` |
| Very slow, high swap (`sysctl vm.swapusage`) | Close browsers. Check `ollama ps` shows about 5.7GB (context cap active) |

## License

GPL-3.0, the same as manga-image-translator, since the patches in `patches/` modify its code. See `LICENSE`.
Bring your own pages: this repo ships no manga images or model weights.
