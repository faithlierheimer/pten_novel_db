"""
Build a character table for the whole novel using spaCy NER.

Walks the novel folder (beat folders -> chapter folders -> .txt files), finds
PERSON entities in every file, and combines them into ONE row per character,
no matter how many files they show up in.

Setup:
    pip install spacy
    python3 -m spacy download en_core_web_trf   # most accurate, slower
    # or: python3 -m spacy download en_core_web_lg  (faster, a bit less accurate)

Usage:
    python3 build_character_table.py /path/to/novel
    python3 build_character_table.py /path/to/novel characters.csv appearances.csv

Outputs:
    characters.csv   one row per character (name, total mentions, where they appear)
    appearances.csv  one row per character per file (handy for scene work later)
"""

import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import spacy

# ---- settings -------------------------------------------------------------
NOVEL_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
CHARACTERS_CSV = sys.argv[2] if len(sys.argv) > 2 else "characters.csv"
APPEARANCES_CSV = sys.argv[3] if len(sys.argv) > 3 else "appearances.csv"

MODEL = "en_core_web_trf"   # swap for "en_core_web_lg" if it's too slow
MIN_MENTIONS = 2            # across the WHOLE novel; raise to drop more NER junk
MERGE_PARTIAL_NAMES = True  # fold "Charlotte" into "Charlotte Lucas" if unambiguous

# Manual merges for nicknames / variants. Keys are lowercase.
# Example: {"charlie": "Charlotte", "lottie": "Charlotte"}
ALIASES = {}

TITLES = re.compile(
    r"^(Mr|Mrs|Ms|Miss|Dr|Sir|Lady|Lord|Captain|Professor)\.?\s+", re.I
)
STOPLIST = {"god", "sir", "madam", "lord", "lady"}  # add your own junk here

FILE_PATTERN = re.compile(r"^act_(\d+)_ch_(\d+)_(.*)$", re.I)


# ---- helpers --------------------------------------------------------------
def clean_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"(’s|'s)$", "", name)   # possessives
    name = re.sub(r"\s+", " ", name)       # stray newlines / double spaces
    name = TITLES.sub("", name)            # "Mr. Darcy" -> "Darcy"
    return name.strip(" ,.;:-\"'“”‘’")


def leading_number(text: str) -> int:
    m = re.match(r"(\d+)", text)
    return int(m.group(1)) if m else 10**9


def file_sort_key(path: Path):
    """Story order: beat number, then chapter number, then filename."""
    chapter_dir = path.parent
    beat_dir = chapter_dir.parent
    return (leading_number(beat_dir.name), leading_number(chapter_dir.name), path.name)


def describe_file(path: Path) -> dict:
    """Pull beat / chapter / act info out of the folder and file names."""
    m = FILE_PATTERN.match(path.stem)
    return {
        "file": path.name,
        "beat": path.parent.parent.name,
        "chapter": path.parent.name,
        "act": m.group(1) if m else "",
        "chapter_num": int(m.group(2)) if m else leading_number(path.parent.name),
    }


def apply_aliases(per_file: dict) -> dict:
    """per_file: {name: Counter({file_path: n})}. Rename aliases to canonical names."""
    out = defaultdict(Counter)
    for name, files in per_file.items():
        out[ALIASES.get(name.lower(), name)].update(files)
    return out


def merge_partials(per_file: dict, variants: dict) -> dict:
    """Fold a single-word name into a longer name when exactly one longer name contains it."""
    names = list(per_file)
    merged = defaultdict(Counter)
    for name, files in per_file.items():
        target = name
        if len(name.split()) == 1:
            matches = [m for m in names if m != name and name in m.split()]
            if len(matches) == 1:
                target = matches[0]
        merged[target].update(files)
        if target != name:
            variants[target].add(name)
    return merged


# ---- main -----------------------------------------------------------------
def main():
    files = sorted(NOVEL_ROOT.rglob("*.txt"), key=file_sort_key)
    if not files:
        sys.exit(f"No .txt files found under {NOVEL_ROOT.resolve()}")

    nlp = spacy.load(MODEL)
    print(f"Found {len(files)} text files. Processing with {MODEL}...")

    # name -> Counter({file_path: mentions in that file})
    per_file = defaultdict(Counter)
    variants = defaultdict(set)   # canonical name -> other spellings folded into it

    for i, path in enumerate(files, 1):
        text = path.read_text(encoding="utf-8", errors="replace")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        print(f"  [{i}/{len(files)}] {path.relative_to(NOVEL_ROOT)}")

        for doc in nlp.pipe(paragraphs, batch_size=16):
            for ent in doc.ents:
                if ent.label_ != "PERSON":
                    continue
                name = clean_name(ent.text)
                if name and name.lower() not in STOPLIST:
                    per_file[name][path] += 1

    # Combine duplicates ACROSS files, so each character gets exactly one entry
    per_file = apply_aliases(per_file)
    if MERGE_PARTIAL_NAMES:
        per_file = merge_partials(per_file, variants)

    info = {p: describe_file(p) for p in files}
    order = {p: i for i, p in enumerate(files)}   # story order

    character_rows = []
    appearance_rows = []
    for name, file_counts in per_file.items():
        total = sum(file_counts.values())
        if total < MIN_MENTIONS:
            continue

        appearing = sorted(file_counts, key=order.get)
        first, last = info[appearing[0]], info[appearing[-1]]
        chapters = sorted({info[p]["chapter_num"] for p in appearing})

        character_rows.append({
            "name": name,
            "total_mentions": total,
            "files_appeared_in": len(appearing),
            "chapters_appeared_in": len(chapters),
            "first_appearance": f'{first["file"]} (ch {first["chapter_num"]})',
            "last_appearance": f'{last["file"]} (ch {last["chapter_num"]})',
            "also_seen_as": "; ".join(sorted(variants.get(name, []))),
        })

        for p in appearing:
            d = info[p]
            appearance_rows.append({
                "name": name,
                "beat": d["beat"],
                "chapter": d["chapter"],
                "act": d["act"],
                "file": d["file"],
                "mentions": file_counts[p],
            })

    character_rows.sort(key=lambda r: -r["total_mentions"])

    # utf-8-sig so Excel displays accented characters correctly
    with open(CHARACTERS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(character_rows[0]))
        w.writeheader()
        w.writerows(character_rows)

    with open(APPEARANCES_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(appearance_rows[0]))
        w.writeheader()
        w.writerows(appearance_rows)

    print(f"Wrote {len(character_rows)} distinct characters to {CHARACTERS_CSV}")
    print(f"Wrote {len(appearance_rows)} character/file appearances to {APPEARANCES_CSV}")


if __name__ == "__main__":
    main()