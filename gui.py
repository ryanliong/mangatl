#!/usr/bin/env python3
"""MangaTL: a small desktop window for starting/stopping Ollama and translating a folder of manga pages.

It runs scripts/run_baseline.sh and turns its log output into a progress bar, so behaviour matches the CLI.
Launch with scripts/gui.sh, or build a double-clickable MangaTL.app with scripts/make_app.sh.
"""
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import pagesel

ROOT = Path(__file__).resolve().parent
SETTINGS = ROOT / ".gui_settings.json"
OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "huihui_ai/qwen3.5-abliterated:9b"
IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}
# (log name after "[local] Running", label shown in the window)
STAGES = [
    ("text detection", "Detecting text"),
    ("ocr", "Reading Japanese (OCR)"),
    ("text translation", "Translating"),
    ("mask refinement", "Erasing original text"),
    ("rendering", "Typesetting English"),
]
SOURCES = {"Japanese": "ja", "Chinese edition of Japanese manga": "zh"}
FONT_SIZES = {"Smaller (~22px)": "0.017", "Normal (~26px)": "0.02", "Larger (~31px)": "0.024"}
ERROR_RE = re.compile(r"Traceback \(most recent|^ERROR|ERROR:|^error:|Error: |isn't running|does not apply")


def subprocess_env():
    """Apps launched from Finder get a minimal PATH; make Homebrew tools (ollama, uv) findable."""
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    return env


def ollama_get(path, timeout=1.5):
    try:
        with urllib.request.urlopen(OLLAMA_URL + path, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def swap_used_gb():
    try:
        out = subprocess.run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=2).stdout
        m = re.search(r"used = ([\d.]+)M", out)
        return float(m.group(1)) / 1024 if m else None
    except Exception:
        return None


def count_pages(folder):
    try:
        return sum(1 for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    except OSError:
        return 0


def done_pages(out, names):
    """How many of `names` already have a same-named file in `out` (what a resumed run will skip)."""
    try:
        outs = {p.name for p in Path(out).iterdir()}
    except OSError:
        return 0
    return sum(1 for n in names if n in outs)


def fmt_duration(seconds):
    seconds = int(round(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class Progress:
    """Turns run_baseline.sh output into progress state. Kept free of Tk so it can be tested on a saved log."""

    def __init__(self, total, done=0):
        self.total = total
        self.done = done           # counts pages skipped by a resume too, so labels show the real page number
        self.start_done = done
        self.page = None
        self.stage = None          # index into STAGES, None between pages
        self.loading = False
        self.page_started = None
        self.started = time.time()
        self.durations = []
        self.errors = []
        self.skipped = 0
        self.summary = {}

    def feed(self, line):
        """Consume one output line; return (tag, text) to show in the log pane, or None."""
        line = line.rstrip("\n")
        m = re.match(r'\[local\] Translating: "(.+)"', line)
        if m:
            self.page, self.stage, self.page_started = Path(m.group(1)).name, None, time.time()
            return ("info", f"Page {self.done + 1} of {self.total}: {self.page}")
        if line.startswith("[local] Loading models"):
            # Logged for every page, but only the first one actually loads anything.
            if self.done == 0:
                self.loading = True
                return ("muted", "  Loading models (the first page takes longer)")
            return None
        m = re.match(r"\[local\] Running (.+)$", line)
        if m:
            for i, (key, _) in enumerate(STAGES):
                if m.group(1).strip() == key:
                    self.stage, self.loading = i, False
            return None
        if line.startswith("[local] Saving"):
            self.done += 1
            took = time.time() - self.page_started if self.page_started else 0
            self.durations.append(took)
            self.stage = None
            return ("ok", f"  Done in {took:.1f}s")
        if "Reason: Horizontal text block" in line:
            self.skipped += 1
            return ("muted", "  Left a credits/contact block untranslated")
        if ERROR_RE.search(line):
            self.errors.append(line)
            return ("error", line)
        m = re.match(r"(pages|total time|time per page|translator peak|ollama resident|log):\s+(.*)", line)
        if m:
            self.summary[m.group(1)] = m.group(2).strip()
        return None

    def fraction(self):
        if not self.total:
            return 0.0
        part = 0.0 if self.stage is None else (self.stage + 1) / (len(STAGES) + 1)
        return min(1.0, (self.done + part) / self.total)

    def stage_label(self):
        if self.loading:
            return "Loading models"
        if self.stage is not None:
            return STAGES[self.stage][1]
        if self.done == 0:
            return "Starting"
        return "Finishing up" if self.done >= self.total else "Saving"

    def eta(self):
        if not self.durations:
            return None
        recent = self.durations[1:] or self.durations   # first page includes model loading
        avg = sum(recent) / len(recent)
        in_page = time.time() - self.page_started if self.page_started and self.stage is not None else 0
        return max(0.0, avg * (self.total - self.done) - in_page)

    def avg_page(self):
        recent = self.durations[1:] or self.durations
        return sum(recent) / len(recent) if recent else None


class App:
    def __init__(self, root):
        self.root = root
        self.ollama_proc = None      # only set when this window started Ollama
        self.proc = None             # running translation
        self.progress = None
        self.cancelled = False
        self.output_custom = False
        self.closing = False
        self.lines = queue.Queue()
        self.status = queue.Queue()
        self.models = []
        settings = self._load_settings()

        root.title("MangaTL")
        root.minsize(620, 560)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        main = ttk.Frame(root, padding=14)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)

        # --- Ollama ---------------------------------------------------------------------------------
        box = ttk.LabelFrame(main, text="Ollama (local translator)", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.dot = tk.Canvas(box, width=12, height=12, highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0, padx=(0, 8))
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill="#8b8d98", outline="")
        self.ollama_label = ttk.Label(box, text="Checking…")
        self.ollama_label.grid(row=0, column=1, sticky="w")
        self.start_btn = ttk.Button(box, text="Start", command=self.start_ollama)
        self.start_btn.grid(row=0, column=2, padx=(8, 0))
        self.stop_btn = ttk.Button(box, text="Stop", command=self.stop_ollama)
        self.stop_btn.grid(row=0, column=3, padx=(6, 0))
        self.mem_label = ttk.Label(box, text="", foreground="#8b8d98")
        self.mem_label.grid(row=1, column=1, columnspan=3, sticky="w", pady=(4, 0))

        # --- Translate --------------------------------------------------------------------------------
        form = ttk.LabelFrame(main, text="Translate a folder", padding=10)
        form.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        form.columnconfigure(1, weight=1)
        self.input_var = tk.StringVar(value=settings.get("input", ""))
        self.output_var = tk.StringVar(value=self._default_output(self.input_var.get()))
        self.model_var = tk.StringVar(value=settings.get("model", DEFAULT_MODEL))
        self.font_var = tk.StringVar(value=settings.get("font", "Normal (~26px)"))
        self.source_var = tk.StringVar(value=settings.get("source", "Japanese"))
        self.skip_var = tk.BooleanVar(value=settings.get("skip_credits", True))
        self.only_pages_var = tk.StringVar()      # page ranges belong to one book, so they aren't remembered
        self.skip_pages_var = tk.StringVar()

        ttk.Label(form, text="Pages folder").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(form, text="Choose…", command=self.choose_input).grid(row=0, column=2)
        self.pages_label = ttk.Label(form, text="", foreground="#8b8d98")
        self.pages_label.grid(row=1, column=1, sticky="w", padx=8)

        ttk.Label(form, text="Save to").grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(form, textvariable=self.output_var).grid(row=2, column=1, sticky="ew", padx=8, pady=(6, 0))
        ttk.Button(form, text="Choose…", command=self.choose_output).grid(row=2, column=2, pady=(6, 0))

        ttk.Label(form, text="Source text").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(form, textvariable=self.source_var, values=list(SOURCES), state="readonly", width=32) \
            .grid(row=3, column=1, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(form, text="Model").grid(row=4, column=0, sticky="w", pady=(6, 0))
        self.model_box = ttk.Combobox(form, textvariable=self.model_var, values=[DEFAULT_MODEL])
        self.model_box.grid(row=4, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=(6, 0))

        ttk.Label(form, text="Font size").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(form, textvariable=self.font_var, values=list(FONT_SIZES), state="readonly", width=18) \
            .grid(row=5, column=1, sticky="w", padx=8, pady=(6, 0))
        ttk.Checkbutton(form, text="Leave credits / contact-detail blocks untranslated", variable=self.skip_var) \
            .grid(row=6, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 0))

        ttk.Label(form, text="Pages").grid(row=7, column=0, sticky="w", pady=(6, 0))
        pages_row = ttk.Frame(form)
        pages_row.grid(row=7, column=1, columnspan=2, sticky="w", padx=8, pady=(6, 0))
        ttk.Label(pages_row, text="Only").pack(side="left")
        ttk.Entry(pages_row, textvariable=self.only_pages_var, width=14).pack(side="left", padx=(4, 12))
        ttk.Label(pages_row, text="Skip").pack(side="left")
        ttk.Entry(pages_row, textvariable=self.skip_pages_var, width=14).pack(side="left", padx=(4, 12))
        ttk.Label(pages_row, text="e.g. 32-60, 75  ·  blank = all", foreground="#8b8d98").pack(side="left")

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        self.go_btn = ttk.Button(buttons, text="Translate", command=self.start_translation, default="active")
        self.go_btn.pack(side="left")
        self.cancel_btn = ttk.Button(buttons, text="Cancel", command=self.cancel_translation, state="disabled")
        self.cancel_btn.pack(side="left", padx=6)
        self.open_btn = ttk.Button(buttons, text="Open output folder", command=self.open_output)
        self.open_btn.pack(side="right")
        self.log_btn = ttk.Button(buttons, text="Open full log", command=self.open_log, state="disabled")
        self.log_btn.pack(side="right", padx=6)

        # --- Progress ---------------------------------------------------------------------------------
        prog = ttk.Frame(main)
        prog.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        prog.columnconfigure(0, weight=1)
        self.page_label = ttk.Label(prog, text="Idle")
        self.page_label.grid(row=0, column=0, sticky="w")
        self.bar = ttk.Progressbar(prog, maximum=1000)
        self.bar.grid(row=1, column=0, sticky="ew", pady=4)
        self.time_label = ttk.Label(prog, text="", foreground="#8b8d98")
        self.time_label.grid(row=2, column=0, sticky="w")

        # --- Log --------------------------------------------------------------------------------------
        self.log = ScrolledText(main, height=12, wrap="word", state="disabled", relief="flat",
                                font=("Menlo", 11), padx=8, pady=6)
        self.log.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        main.rowconfigure(4, weight=1)
        self.log.tag_configure("error", foreground="#e5484d")
        self.log.tag_configure("ok", foreground="#30a46c")
        self.log.tag_configure("muted", foreground="#8b8d98")

        self.input_var.trace_add("write", lambda *_: self._on_folder_changed())
        self.only_pages_var.trace_add("write", lambda *_: self._on_input_changed())
        self.skip_pages_var.trace_add("write", lambda *_: self._on_input_changed())
        # Chinese editions put phone/email text in wide horizontal blocks, so skipping those loses plot text.
        self.source_var.trace_add("write", lambda *_: self.skip_var.set(SOURCES.get(self.source_var.get()) == "ja"))
        self._on_input_changed()
        threading.Thread(target=self._poll_status, daemon=True).start()
        self.root.after(100, self._drain)
        self.root.after(1000, self._tick)

    # --- settings ---------------------------------------------------------------------------------------
    def _load_settings(self):
        try:
            return json.loads(SETTINGS.read_text())
        except Exception:
            return {}

    def _save_settings(self):
        data = {"input": self.input_var.get(), "model": self.model_var.get(),
                "font": self.font_var.get(), "source": self.source_var.get(), "skip_credits": self.skip_var.get()}
        try:
            SETTINGS.write_text(json.dumps(data, indent=2))
        except OSError:
            pass

    # --- folders ----------------------------------------------------------------------------------------
    @staticmethod
    def _default_output(inp):
        return f"{inp.rstrip('/')}-translated" if inp else ""

    def _on_folder_changed(self):
        self.only_pages_var.set("")
        self.skip_pages_var.set("")
        self._on_input_changed()

    def _on_input_changed(self):
        inp = self.input_var.get().strip()
        if not self.output_custom:
            self.output_var.set(self._default_output(inp))
        if not inp or not Path(inp).is_dir():
            self.pages_label.config(text="", foreground="#8b8d98")
            return
        try:
            text, color = pagesel.describe(inp, self.only_pages_var.get(), self.skip_pages_var.get()), "#8b8d98"
        except ValueError as e:
            text, color = f"Pages: {e}", "#e5484d"
        self.pages_label.config(text=text, foreground=color)

    def choose_input(self):
        start = self.input_var.get() or str(ROOT / "samples")
        path = filedialog.askdirectory(title="Folder of Japanese pages", initialdir=start)
        if path:
            self.output_custom = False
            self.input_var.set(path)

    def choose_output(self):
        path = filedialog.askdirectory(title="Save translated pages to",
                                       initialdir=str(Path(self.input_var.get() or ROOT).parent))
        if path:
            self.output_custom = True
            self.output_var.set(path)

    def open_output(self):
        out = self.output_var.get()
        if out and Path(out).is_dir():
            subprocess.run(["open", out])
        else:
            messagebox.showinfo("MangaTL", "The output folder doesn't exist yet.")

    def open_log(self):
        log = self.progress.summary.get("log") if self.progress else None
        if log and Path(log).exists():
            subprocess.run(["open", "-t", log])

    # --- Ollama -----------------------------------------------------------------------------------------
    def _poll_status(self):
        while not self.closing:
            tags = ollama_get("/api/tags")
            ps = ollama_get("/api/ps") if tags is not None else None
            self.status.put((tags, ps, swap_used_gb()))
            time.sleep(2)

    def _apply_status(self, tags, ps, swap):
        running = tags is not None
        self.ollama_running = running
        self.dot.itemconfig(self.dot_id, fill="#30a46c" if running else "#8b8d98")
        if running:
            loaded = [f"{m['name']} ({m['size'] / 1e9:.1f} GB)" for m in (ps or {}).get("models", [])]
            self.ollama_label.config(text="Running · " + (", ".join(loaded) + " loaded" if loaded else "no model loaded yet"))
            models = sorted(m["name"] for m in tags.get("models", []))
            if models != self.models:
                self.models = models
                self.model_box.config(values=models or [DEFAULT_MODEL])
        else:
            starting = self.ollama_proc is not None and self.ollama_proc.poll() is None
            self.ollama_label.config(text="Starting…" if starting else "Stopped")
        self.start_btn.config(state="disabled" if running else "normal")
        self.stop_btn.config(state="normal" if running else "disabled")
        self.mem_label.config(text=f"Mac swap in use: {swap:.1f} GB" + ("  (high: close other apps for speed)" if swap > 8 else "")
                              if swap is not None else "")

    def start_ollama(self):
        log = open(ROOT / "logs" / "ollama.log", "a") if (ROOT / "logs").is_dir() else subprocess.DEVNULL
        try:
            self.ollama_proc = subprocess.Popen([str(ROOT / "scripts" / "start_ollama.sh")], stdout=log,
                                                stderr=subprocess.STDOUT, start_new_session=True, env=subprocess_env())
        except OSError as e:
            messagebox.showerror("MangaTL", f"Couldn't start Ollama:\n{e}")
            return
        self.ollama_label.config(text="Starting…")
        self.start_btn.config(state="disabled")

    def stop_ollama(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("MangaTL", "A translation is running. Stop Ollama anyway? The run will fail."):
                return
        if self.ollama_proc and self.ollama_proc.poll() is None:
            self.ollama_proc.terminate()
        else:
            if not messagebox.askyesno("MangaTL", "Ollama was started outside this window. Stop it anyway?"):
                return
            subprocess.run(["pkill", "-f", "ollama serve"])
        self.ollama_label.config(text="Stopping…")

    # --- translation ------------------------------------------------------------------------------------
    def start_translation(self):
        inp = self.input_var.get().strip().rstrip("/")
        out = self.output_var.get().strip().rstrip("/")
        if not inp or not Path(inp).is_dir():
            messagebox.showwarning("MangaTL", "Choose a folder of pages first.")
            return
        if count_pages(inp) == 0:
            messagebox.showwarning("MangaTL", "No .webp, .jpg or .png pages in that folder.")
            return
        only, skip = self.only_pages_var.get().strip(), self.skip_pages_var.get().strip()
        try:
            selected = pagesel.select(inp, only, skip)
        except ValueError as e:
            messagebox.showwarning("MangaTL", f"Check the page ranges: {e}")
            return
        if not selected:
            messagebox.showwarning("MangaTL", "That page selection leaves no pages to translate.")
            return
        total = len(selected)
        which = "selected pages" if (only or skip) else "pages"
        if not getattr(self, "ollama_running", False):
            if messagebox.askyesno("MangaTL", "Ollama isn't running. Start it and translate?"):
                self.start_ollama()
                self._wait_for_ollama(time.time())
            return
        if self.models and self.model_var.get() not in self.models:
            messagebox.showwarning("MangaTL", f"Model '{self.model_var.get()}' isn't installed.\n"
                                              f"Run: ollama pull {self.model_var.get()}")
            return
        done = done_pages(out, selected)
        overwrite = False
        if done:
            choice = messagebox.askyesnocancel(
                "MangaTL", f"{done} of {total} {which} are already translated in {Path(out).name}.\n\n"
                           "Yes: resume (skip finished pages, keep the same character names)\n"
                           f"No: start over (re-translate all {total} {which})")
            if choice is None:
                return
            overwrite = not choice
            if not overwrite and done >= total:
                messagebox.showinfo("MangaTL", "Every page is already translated. Choose No to start over.")
                return

        env = subprocess_env()
        env.update({
            "PYTHONUNBUFFERED": "1",
            "MANGATL_OUTPUT": out,
            "CUSTOM_OPENAI_MODEL": self.model_var.get(),
            "MANGATL_MIN_FONT_RATIO": FONT_SIZES.get(self.font_var.get(), "0.02"),
            "MANGATL_SKIP_HBLOCKS": "1" if self.skip_var.get() else "0",
            "MANGATL_SOURCE": SOURCES.get(self.source_var.get(), "ja"),
            "MANGATL_OVERWRITE": "1" if overwrite else "0",
            "MANGATL_PAGES": only,
            "MANGATL_SKIP_PAGES": skip,
        })
        self._save_settings()
        skip = 0 if overwrite else done
        self.progress = Progress(total, done=skip)
        self.cancelled = False
        self._clear_log()
        self._log("info", f"Translating {total} {which} from {Path(inp).name}"
                          + (f" (only: {only or 'all'}, skip: {skip or 'none'})" if (only or skip) else ""))
        if skip:
            self._log("muted", f"  Resuming: skipping {skip} pages that are already done")
        try:
            self.proc = subprocess.Popen(["bash", str(ROOT / "scripts" / "run_baseline.sh"), inp], cwd=ROOT, env=env,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                         errors="replace", start_new_session=True)
        except OSError as e:
            messagebox.showerror("MangaTL", f"Couldn't start the translation:\n{e}")
            return
        threading.Thread(target=self._read_output, args=(self.proc,), daemon=True).start()
        self.go_btn.config(state="disabled")
        self.cancel_btn.config(state="normal")
        self.log_btn.config(state="disabled")

    def _wait_for_ollama(self, since):
        if getattr(self, "ollama_running", False):
            self.start_translation()
        elif time.time() - since < 30:
            self.root.after(500, self._wait_for_ollama, since)
        else:
            messagebox.showerror("MangaTL", "Ollama didn't start within 30s. See logs/ollama.log.")

    def _read_output(self, proc):
        for line in proc.stdout:
            self.lines.put(line)
        self.lines.put(("exit", proc.wait()))

    def cancel_translation(self):
        if self.proc and self.proc.poll() is None:
            self.cancelled = True
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.cancel_btn.config(state="disabled")
            self.page_label.config(text="Cancelling…")

    def _finish(self, code):
        p = self.progress
        self.go_btn.config(state="normal")
        self.cancel_btn.config(state="disabled")
        self.log_btn.config(state="normal" if p.summary.get("log") else "disabled")
        elapsed = time.time() - p.started
        now = p.done - p.start_done
        resumed = f" ({p.start_done} were already done)" if p.start_done else ""
        if self.cancelled:
            msg, tag = f"Cancelled at {p.done} of {p.total} pages. Translate again to resume.", "muted"
        elif code == 0 and p.done == p.total:
            avg = p.avg_page()
            msg, tag = (f"Done: translated {now} pages{resumed} in {fmt_duration(elapsed)}"
                        + (f", {avg:.1f}s per page" if avg else ""), "ok")
            self.bar["value"] = 1000
        else:
            msg, tag = (f"Stopped with errors at {p.done} of {p.total} pages (exit {code}). "
                        "Fix the cause, then Translate again to resume. See the log."), "error"
        if p.skipped:
            msg += f" · {p.skipped} credits block{'s' * (p.skipped != 1)} left untranslated"
        self.page_label.config(text=msg)
        self.time_label.config(text=f"Elapsed {fmt_duration(elapsed)}")
        self._log(tag, msg)
        self.root.bell()
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "MangaTL"'], capture_output=True)
        self.proc = None

    # --- UI plumbing ------------------------------------------------------------------------------------
    def _drain(self):
        try:
            while True:
                self._apply_status(*self.status.get_nowait())
        except queue.Empty:
            pass
        try:
            for _ in range(500):
                item = self.lines.get_nowait()
                if isinstance(item, tuple):
                    self._finish(item[1])
                    break
                shown = self.progress.feed(item)
                if shown:
                    self._log(*shown)
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _tick(self):
        p = self.progress
        if self.proc and p:
            self.bar["value"] = int(p.fraction() * 1000)
            if p.page and not self.cancelled:
                self.page_label.config(text=f"Page {min(p.done + 1, p.total)} of {p.total} · {p.page} · {p.stage_label()}")
            eta, avg = p.eta(), p.avg_page()
            parts = [f"Elapsed {fmt_duration(time.time() - p.started)}"]
            parts.append(f"about {fmt_duration(eta)} left" if eta is not None else "estimating time left…")
            if avg:
                parts.append(f"{avg:.1f}s per page")
            self.time_label.config(text=" · ".join(parts))
        self.root.after(1000, self._tick)

    def _log(self, tag, text):
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", tag)
        self.log.see("end")
        self.log.config(state="disabled")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("MangaTL", "A translation is running. Cancel it and quit?"):
                return
            self.cancel_translation()
        if self.ollama_proc and self.ollama_proc.poll() is None:
            if messagebox.askyesno("MangaTL", "Stop Ollama too? (It frees about 6 GB of memory.)"):
                self.ollama_proc.terminate()
        self._save_settings()
        self.closing = True
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--replay-log":
        # Debug aid: run a saved log through the progress parser without opening a window.
        prog = Progress(total=0)
        for line in open(sys.argv[2], errors="replace"):
            if line.startswith("Input:"):
                m = re.search(r"\((\d+) pages\)", line)
                prog.total = int(m.group(1)) if m else 0
            shown = prog.feed(line)
            if shown:
                print(f"[{shown[0]:5}] {shown[1]}   ({prog.fraction():.0%})")
        print("summary:", prog.summary, "| errors:", len(prog.errors), "| skipped:", prog.skipped)
    else:
        main()
