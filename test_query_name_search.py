"""
Extract a distinct list of person names from a novel using spaCy NER,
and write them to a CSV (name, mentions), most frequent first.

Setup:
    pip install spacy
    python3 -m spacy download en_core_web_trf   # most accurate, slower
    # or: python3 -m spacy download en_core_web_lg  (faster, a bit less accurate)

Usage:
    python3 test_query_name_search.py act_2_ch_27_begrudgingly_work_together.txt characters.csv
"""

import csv
import re
import sys
from collections import Counter

import spacy

# ---- settings -------------------------------------------------------------
INPUT_PATH = sys.argv[1] if len(sys.argv) > 1 else "act_2_ch_27_begrudgingly_work_together.txt"
OUTPUT_PATH = sys.argv[2] if len(sys.argv) > 2 else "characters.csv"
MODEL = "en_core_web_trf"   # swap for "en_core_web_lg" if it's too slow
MIN_MENTIONS = 1            # raise to 2-3 to drop most one-off NER mistakes
MERGE_PARTIAL_NAMES = True  # fold "Darcy" into "Fitzwilliam Darcy" if unambiguous

TITLES = re.compile(
    r"^(Mr|Mrs|Ms|Miss|Dr|Sir|Lady|Lord|Captain|Professor)\.?\s+", re.I
)
STOPLIST = {"god", "sir", "madam", "lord", "lady"}  # add your own junk here


# ---- helpers --------------------------------------------------------------
def clean_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r"(’s|'s)$", "", name)   # possessives
    name = re.sub(r"\s+", " ", name)       # stray newlines / double spaces
    name = TITLES.sub("", name)            # "Mr. Darcy" -> "Darcy"
    return name.strip(" ,.;:-\"'“”‘’")


def merge_partials(counts: Counter) -> Counter:
    """Fold single-word names into a longer name when exactly one longer name contains them."""
    names = list(counts)
    merged = Counter()
    for name, n in counts.items():
        if len(name.split()) == 1:
            matches = [m for m in names if m != name and name in m.split()]
            if len(matches) == 1:
                merged[matches[0]] += n
                continue
        merged[name] += n
    return merged


# ---- main -----------------------------------------------------------------
def main():
    nlp = spacy.load(MODEL)

    with open(INPUT_PATH, encoding="utf-8") as f:
        text = f.read()

    # Paragraph-by-paragraph keeps memory low and gives the model sensible context
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    print(f"Processing {len(paragraphs)} paragraphs with {MODEL}...")

    counts = Counter()
    for doc in nlp.pipe(paragraphs, batch_size=16):
        for ent in doc.ents:
            if ent.label_ != "PERSON":
                continue
            name = clean_name(ent.text)
            if name and name.lower() not in STOPLIST:
                counts[name] += 1

    if MERGE_PARTIAL_NAMES:
        counts = merge_partials(counts)

    rows = [(name, n) for name, n in counts.most_common() if n >= MIN_MENTIONS]

    # utf-8-sig so Excel displays accented characters correctly
    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "mentions"])
        writer.writerows(rows)

    print(f"Wrote {len(rows)} distinct names to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()