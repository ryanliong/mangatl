#!/usr/bin/env python3
"""Page selection: turn "1-31, 61-" style ranges into the files to translate.

Page N is the Nth image in the folder in natural order (1, 2, ... 10, 11), the same order the translator and
manga readers use, so for folders of 1.webp, 2.webp, ... the page number is the file number.

    python3 pagesel.py <folder> [only] [skip]    # prints the selected filenames, one per line

Ranges: "5", "1-31", "61-" (to the end), "-10" (from the start); separate with commas.
"""
import re
import sys
from pathlib import Path

IMAGE_EXTS = {".webp", ".jpg", ".jpeg", ".png"}


def natural_key(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


def list_pages(folder):
    files = [p.name for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    return sorted(files, key=natural_key)


def parse_ranges(spec, total):
    """Page numbers (1-based) named by `spec`. Raises ValueError with a readable message on bad input."""
    pages = set()
    for item in re.split(r"[,，;]", spec):
        item = item.strip().replace("–", "-").replace("—", "-").replace(" ", "")
        if not item:
            continue
        m = re.fullmatch(r"(\d*)-(\d*)|(\d+)", item)
        if not m or item == "-":
            raise ValueError(f'"{item}" isn\'t a page or range (use e.g. 5, 1-31, 61-)')
        if m.group(3):
            start = end = int(m.group(3))
        else:
            start = int(m.group(1)) if m.group(1) else 1
            end = int(m.group(2)) if m.group(2) else total
        if start < 1 or end < start:
            raise ValueError(f'"{item}" is not a valid range')
        pages.update(range(start, min(end, total) + 1))
    return pages


def select(folder, only="", skip=""):
    """Filenames to translate, in reading order."""
    files = list_pages(folder)
    numbers = parse_ranges(only, len(files)) if only.strip() else set(range(1, len(files) + 1))
    if skip.strip():
        numbers -= parse_ranges(skip, len(files))
    return [f for i, f in enumerate(files, 1) if i in numbers]


def describe(folder, only="", skip=""):
    """One-line summary for the GUI, e.g. "Translating 372 of 400 pages"."""
    total = len(list_pages(folder))
    chosen = select(folder, only, skip)
    return f"Translating {len(chosen)} of {total} pages" if (only.strip() or skip.strip()) else f"{total} pages found"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        chosen = select(sys.argv[1], *(sys.argv[2:4] + ["", ""])[:2])
    except ValueError as e:
        sys.exit(f"Bad page selection: {e}")
    if not chosen:
        sys.exit("Bad page selection: no pages left to translate")
    print("\n".join(chosen))
