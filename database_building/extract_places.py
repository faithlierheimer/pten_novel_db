#!/usr/bin/env python3
"""
Build the places tables from the novel's text files.

Reads   novel/<N_beat_name>/<chapter>/act_<A>_ch_<C>_<title>.txt
        csv_data/characters.csv   (optional; enables "who is there often")

Writes  csv_data/places.csv              one row per distinct place (permanent place_id)
        csv_data/place_descriptors.csv   descriptors used for each place, with counts
        csv_data/place_appearances.csv   place x text file, with mention counts
        csv_data/place_characters.csv    characters who co-occur with each place

Usage
    python3 extract_places.py                  # defaults: novel/ -> csv_data/
    python3 extract_places.py --min-mentions 3
    python3 extract_places.py --no-generic     # skip kitchen / bedroom / porch style places
    python3 extract_places.py --novel-dir PTEN_manuscript

Setup
    pip install spacy && python3 -m spacy download en_core_web_sm

Re-running is safe: place_ids and hand-edited columns (place_type, parent_place,
notes) in an existing places.csv are carried forward, and the old file is saved
as places.csv.bak first. The three child tables are fully derived and are rebuilt.
"""
import argparse
import csv
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------
# Hand-maintained config (edit these as you review the output)
# --------------------------------------------------------------------------

# Variant spelling (lowercase, no leading "the") -> canonical place name.
# Same idea as ALIASES in the character script.
ALIASES = {
    # "the heron": "Blue Heron Inn",
}

# Lowercase names to drop (NER false positives).
IGNORE = {
    # "god", "earth",
}

# Words that mark a capitalized phrase as a place NER often misses,
# e.g. "Blue Heron Inn", "Miller Street".
PLACE_NOUNS = [
    "House", "Inn", "Hotel", "Motel", "Cafe", "Café", "Diner", "Bar", "Pub", "Tavern",
    "Street", "Road", "Avenue", "Lane", "Drive", "Bridge", "Park", "Lake", "River",
    "Creek", "Hill", "Mountain", "Woods", "Forest", "Beach", "Island", "Church",
    "School", "Hospital", "Library", "Station", "Market", "Store", "Shop", "Hall",
    "Manor", "Farm", "Ranch", "Cabin", "Lodge", "Mill", "Factory", "Warehouse",
    "Cemetery", "Harbor", "Dock", "Pier", "Square", "Plaza", "Theater", "Theatre",
    "Apartments", "Building", "Tower", "Castle", "Academy", "University", "College",
    "Falls", "Springs", "Heights", "Valley", "Point", "Bay", "Junction", "Hollow", "Ridge",
    "Village", "Township", "County", "Gardens", "Estates", "Heath", "Cove", "Canyon",
]

# Fictional places NER can't know. Exact, case-sensitive strings; always treated as places.
SEED_PLACES = [
    # "Harlow", "The Pit",
]

# Everyday rooms/spots. Matched only after "the / a / my / his / her / ..." and
# tagged place_kind=generic, because "the kitchen" may be several different kitchens.
GENERIC_PLACES = [
    "kitchen", "bedroom", "living room", "bathroom", "basement", "attic", "garage",
    "porch", "yard", "garden", "office", "classroom", "hallway", "stairwell",
    "cellar", "barn", "shed", "driveway", "balcony", "rooftop", "dining room",
    "study", "nursery", "pantry", "lobby", "courtyard", "alley", "parking lot",
]

# NER labels treated as places, and what they mean.
NER_PLACE_LABELS = {"GPE": "city/region", "LOC": "landscape", "FAC": "facility"}

# Descriptor filtering: adjectives that say nothing about a place.
DESCRIPTOR_STOP = {
    "other", "same", "own", "next", "last", "first", "second", "third", "few", "many",
    "much", "more", "most", "such", "certain", "several", "various", "whole", "entire",
    "only", "main", "different", "another", "previous", "similar", "available",
    "possible", "ready", "able", "sure", "due", "left", "right", "local",
}
COPULAS = {"be", "seem", "feel", "look", "smell", "sound", "stay", "remain", "become", "appear", "taste"}

CHAR_WINDOW = 2          # sentences either side of a place mention to look for characters
FREQ_MIN_CO = 3          # co-mentions needed to call a character "often there"
FREQ_MIN_CHAPTERS = 2    # ...across at least this many chapters
FREQ_TOP_N = 5           # how many characters to list in places.csv

HAND_COLUMNS = ["place_type", "parent_place", "notes"]  # never overwritten

BEAT_RE = re.compile(r"^(\d+)_(.+)$")
FILE_RE = re.compile(r"^act_(\d+)_ch_(\d+)_(.+)\.txt$", re.I)


# --------------------------------------------------------------------------
# Reading the manuscript
# --------------------------------------------------------------------------
def iter_text_files(novel_dir: Path):
    """Yield dicts of file metadata in story order. Act/chapter come from the filename;
    beat and chapter folder come from the path (and are cross-checked)."""
    beats = []
    for d in novel_dir.iterdir():
        m = BEAT_RE.match(d.name)
        if d.is_dir() and m:
            beats.append((int(m.group(1)), m.group(2), d))
    for beat_no, beat_name, bdir in sorted(beats):
        chapters = sorted((int(c.name), c) for c in bdir.iterdir() if c.is_dir() and c.name.isdigit())
        for ch_folder_no, cdir in chapters:
            for f in sorted(cdir.glob("*.txt")):
                m = FILE_RE.match(f.name)
                if not m:
                    print(f"  ! skipping unrecognised filename: {f}", file=sys.stderr)
                    continue
                act, ch_in_name, title = int(m.group(1)), int(m.group(2)), m.group(3)
                if ch_in_name != ch_folder_no:
                    print(f"  ! chapter mismatch: folder {ch_folder_no} vs filename {ch_in_name} ({f})",
                          file=sys.stderr)
                yield {
                    "path": f, "beat_no": beat_no, "beat_name": beat_name,
                    "chapter": ch_folder_no, "act": act, "file": f.name,
                }


def load_characters(csv_dir: Path):
    """Return {character_id: (display_name, [name variants])}. Column names are guessed."""
    path = csv_dir / "characters.csv"
    if not path.exists():
        print(f"  (no {path}; skipping the 'who is there often' step)")
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        return {}
    cols = rows[0].keys()
    name_col = next((c for c in ("name", "character_name", "canonical_name", "full_name") if c in cols), None)
    if "character_id" not in cols or not name_col:
        print(f"  ! characters.csv needs character_id and one of name/character_name/canonical_name/full_name "
              f"(found {list(cols)}); skipping characters", file=sys.stderr)
        return {}
    chars = {}
    first_tokens = Counter()
    for r in rows:
        first_tokens[r[name_col].split()[0].lower()] += 1
    for r in rows:
        name = r[name_col].strip()
        variants = {name}
        for a in re.split(r"[|;]", r.get("aliases", "") or ""):
            if a.strip():
                variants.add(a.strip())
        first = name.split()[0]
        if len(first) > 2 and first_tokens[first.lower()] == 1:  # unique first name only
            variants.add(first)
        chars[r["character_id"]] = (name, sorted(variants, key=len, reverse=True))
    return chars


# --------------------------------------------------------------------------
# Place detection
# --------------------------------------------------------------------------
def norm_key(surface: str) -> str:
    s = re.sub(r"\s+", " ", surface.strip())
    s = re.sub(r"^(the|a|an|my|his|her|their|our|your)\s+", "", s, flags=re.I)
    s = re.sub(r"(’s|'s)$", "", s)
    return s.lower()


def build_patterns():
    nouns = "|".join(PLACE_NOUNS)
    named = re.compile(
        rf"\b((?:[A-Z][\w'’-]*\s+){{1,3}}(?:{nouns}))\b"          # Blue Heron Inn
        rf"|\b((?:{nouns})\s+of\s+(?:[A-Z][\w'’-]*\s*){{1,3}})"   # Lake of the Woods-style
    )
    gen = "|".join(sorted(map(re.escape, GENERIC_PLACES), key=len, reverse=True))
    generic = re.compile(rf"\b(?:the|a|an|my|his|her|their|our|your)\s+(?:[\w'’-]+\s+){{0,2}}?({gen})\b", re.I)
    return named, generic


def find_place_spans(doc, text, named_re, generic_re, use_generic, seed_re=None, char_name_keys=frozenset()):
    """Return list of (start_char, end_char, place_kind, ner_label) for one sentence span."""
    found = []
    for ent in doc.ents:
        if ent.label_ in NER_PLACE_LABELS:
            found.append((ent.start_char, ent.end_char, "named", ent.label_))
    for m in named_re.finditer(text):
        g = 1 if m.group(1) else 2
        found.append((m.start(g), m.end(g), "named", "PATTERN"))
    if seed_re:
        for m in seed_re.finditer(text):
            found.append((m.start(), m.end(), "named", "SEED"))
    if use_generic:
        for m in generic_re.finditer(text):
            found.append((m.start(1), m.end(1), "generic", "GENERIC"))
    # keep the longest span among overlaps
    found = [f for f in found if norm_key(text[f[0]:f[1]]) not in char_name_keys]  # NER calls people places
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    kept, last_end = [], -1
    for s in found:
        if s[0] >= last_end:
            kept.append(s)
            last_end = s[1]
    return kept


def descriptors_for(span):
    """Adjectives describing the place in this sentence: 'the cold cabin', 'the cabin was damp and dark'."""
    head = span.root
    out = set()
    for c in head.children:
        if c.dep_ == "amod" and c.pos_ == "ADJ":
            out.add(c)
            out.update(x for x in c.conjuncts if x.pos_ == "ADJ")
    for ap_ in head.children:                      # "the Inn, a cold, crooked building"
        if ap_.dep_ == "appos":
            for c in ap_.children:
                if c.dep_ == "amod" and c.pos_ == "ADJ":
                    out.add(c)
                    out.update(x for x in c.conjuncts if x.pos_ == "ADJ")
    verb = head.head
    if head.dep_ in ("nsubj", "nsubjpass") and verb.lemma_ in COPULAS:
        for c in verb.children:
            if c.dep_ in ("acomp", "attr") and c.pos_ == "ADJ":
                out.add(c)
                out.update(x for x in c.conjuncts if x.pos_ == "ADJ")
    words = []
    for t in out:
        w = t.lemma_.lower()
        if w.isalpha() and len(w) > 2 and w not in DESCRIPTOR_STOP and not t.is_stop:
            words.append(w)
    return words


# --------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--novel-dir", default="novel")
    ap.add_argument("--csv-dir", default="csv_data")
    ap.add_argument("--min-mentions", type=int, default=2,
                    help="drop places mentioned fewer times than this (cuts NER noise)")
    ap.add_argument("--no-generic", action="store_true", help="skip generic rooms like 'the kitchen'")
    ap.add_argument("--model", default="en_core_web_sm")
    args = ap.parse_args()

    novel_dir, csv_dir = Path(args.novel_dir), Path(args.csv_dir)
    if not novel_dir.is_dir():
        sys.exit(f"Can't find {novel_dir}/. Run from the folder that contains it, or pass --novel-dir.")
    csv_dir.mkdir(exist_ok=True)

    try:
        import spacy
        nlp = spacy.load(args.model)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"spaCy model '{args.model}' not available ({e}).\n"
                 f"  pip install spacy && python3 -m spacy download {args.model}")

    chars = load_characters(csv_dir)
    char_res = {cid: re.compile(r"\b(?:" + "|".join(re.escape(v) for v in variants) + r")\b")
                for cid, (_, variants) in chars.items()}
    named_re, generic_re = build_patterns()
    seed_re = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in SEED_PLACES) + r")\b") if SEED_PLACES else None
    char_name_keys = frozenset(norm_key(v) for _, vs in chars.values() for v in vs)

    mentions = []            # one dict per place mention
    surfaces = defaultdict(Counter)    # key -> surface form counts
    kinds = defaultdict(Counter)       # key -> place_kind counts
    labels = defaultdict(Counter)      # key -> ner label counts
    canonical = {}                     # key -> canonical display name set via ALIASES
    n_files = 0

    for meta in iter_text_files(novel_dir):
        n_files += 1
        text = meta["path"].read_text(encoding="utf-8", errors="replace")
        doc = nlp(text)
        sents = list(doc.sents)
        sent_char_hits = []  # per sentence: set of character_ids present
        for s in sents:
            st = s.text
            sent_char_hits.append({cid for cid, rx in char_res.items() if rx.search(st)})

        for si, sent in enumerate(sents):
            spans = find_place_spans(sent.as_doc(), sent.text, named_re, generic_re, not args.no_generic,
                                    seed_re, char_name_keys)
            for (a, b, kind, label) in spans:
                surface = sent.text[a:b]
                key = norm_key(surface)
                if key in ALIASES:                      # variant -> canonical place
                    canonical[ALIASES[key].lower()] = ALIASES[key]
                    key = ALIASES[key].lower()
                if not key or key in IGNORE or len(key) < 3:
                    continue
                span = doc.char_span(sent.start_char + a, sent.start_char + b, alignment_mode="expand")
                descs = descriptors_for(span) if span is not None else []
                lo, hi = max(0, si - CHAR_WINDOW), min(len(sents), si + CHAR_WINDOW + 1)
                present = set().union(*sent_char_hits[lo:hi]) if chars else set()
                surfaces[key][surface.strip()] += 1
                kinds[key][kind] += 1
                labels[key][label] += 1
                mentions.append({
                    "key": key, "meta": meta, "sentence": sent.text.strip().replace("\n", " "),
                    "descs": descs, "chars": present,
                })
    print(f"Read {n_files} text files, found {len(mentions)} raw place mentions.")

    # ---- aggregate per place -------------------------------------------------
    by_key = defaultdict(list)
    for m in mentions:
        by_key[m["key"]].append(m)
    by_key = {k: v for k, v in by_key.items() if len(v) >= args.min_mentions}

    def display_name(key):
        if key in canonical:
            return canonical[key]
        # prefer the most common surface form, stripped of leading article, title-cased if it was named
        surface = surfaces[key].most_common(1)[0][0]
        surface = re.sub(r"^(the|a|an|my|his|her|their|our|your)\s+", "", surface, flags=re.I)
        surface = re.sub(r"(’s|'s)$", "", surface)
        return surface if kinds[key].most_common(1)[0][0] == "named" else surface.lower()

    # ---- permanent ids: reuse from existing places.csv --------------------------
    places_path = csv_dir / "places.csv"
    existing, name_to_id, max_id = {}, {}, 0
    if places_path.exists():
        with open(places_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                existing[r["place_id"]] = r
                name_to_id[r["place_name"].lower()] = r["place_id"]
                max_id = max(max_id, int(r["place_id"].lstrip("P") or 0))
        shutil.copy(places_path, str(places_path) + ".bak")

    place_rows, desc_rows, app_rows, char_rows = [], [], [], []
    seen_ids = set()
    for key, ms in sorted(by_key.items(), key=lambda kv: -len(kv[1])):
        name = display_name(key)
        pid = name_to_id.get(name.lower()) or name_to_id.get(key)
        if not pid:
            max_id += 1
            pid = f"P{max_id:04d}"
        seen_ids.add(pid)

        chapters = sorted({m["meta"]["chapter"] for m in ms})
        beats = sorted({m["meta"]["beat_no"] for m in ms})
        acts = sorted({m["meta"]["act"] for m in ms})
        first = min(ms, key=lambda m: (m["meta"]["chapter"], m["meta"]["file"]))

        # descriptors
        dcount, dchap, dex = Counter(), defaultdict(set), {}
        for m in ms:
            for d in set(m["descs"]):
                dcount[d] += 1
                dchap[d].add(m["meta"]["chapter"])
                dex.setdefault(d, m["sentence"][:220])
        for d, n in dcount.most_common():
            desc_rows.append({"place_id": pid, "descriptor": d, "count": n,
                              "chapter_count": len(dchap[d]), "example": dex[d]})

        # appearances (place x file)
        per_file = Counter()
        file_meta = {}
        for m in ms:
            k = (m["meta"]["chapter"], m["meta"]["file"])
            per_file[k] += 1
            file_meta[k] = m["meta"]
        for k, n in sorted(per_file.items()):
            fm = file_meta[k]
            app_rows.append({"place_id": pid, "chapter": fm["chapter"], "act": fm["act"],
                             "beat_no": fm["beat_no"], "beat_name": fm["beat_name"],
                             "file": fm["file"], "mention_count": n})

        # characters
        co, cchap = Counter(), defaultdict(set)
        for m in ms:
            for cid in m["chars"]:
                co[cid] += 1
                cchap[cid].add(m["meta"]["chapter"])
        often = []
        for cid, n in co.most_common():
            char_rows.append({"place_id": pid, "character_id": cid, "character_name": chars[cid][0],
                              "co_mentions": n, "chapter_count": len(cchap[cid]),
                              "often_there": int(n >= FREQ_MIN_CO and len(cchap[cid]) >= FREQ_MIN_CHAPTERS)})
            if n >= FREQ_MIN_CO and len(cchap[cid]) >= FREQ_MIN_CHAPTERS and len(often) < FREQ_TOP_N:
                often.append(f"{chars[cid][0]} ({n})")

        old = existing.get(pid, {})
        place_rows.append({
            "place_id": pid,
            "place_name": name,
            "place_kind": kinds[key].most_common(1)[0][0],
            "ner_label": labels[key].most_common(1)[0][0],
            "place_type": old.get("place_type", ""),
            "parent_place": old.get("parent_place", ""),
            "aliases_seen": "; ".join(dict.fromkeys(
                re.sub(r"^(the|a|an|my|his|her|their|our|your)\s+", "", s_, flags=re.I)
                for s_, _ in surfaces[key].most_common()
                if norm_key(s_) != name.lower()))[:200],
            "mention_count": len(ms),
            "file_count": len(per_file),
            "chapter_count": len(chapters),
            "first_chapter": chapters[0],
            "last_chapter": chapters[-1],
            "beats": ",".join(map(str, beats)),
            "acts": ",".join(map(str, acts)),
            "top_descriptors": "; ".join(f"{d} ({n})" for d, n in dcount.most_common(8)),
            "frequent_characters": "; ".join(often),
            "first_mention": first["sentence"][:250],
            "notes": old.get("notes", ""),
        })

    # keep hand-entered places that this run didn't find (e.g. places you added manually)
    kept_manual = 0
    fields = list(place_rows[0].keys()) if place_rows else list(next(iter(existing.values()), {}).keys())
    for pid, r in existing.items():
        if pid not in seen_ids:
            row = {c: "" for c in fields}
            row.update({k: v for k, v in r.items() if k in fields})
            place_rows.append(row)
            kept_manual += 1

    def write(name, rows, fields=None):
        fields = fields or (list(rows[0].keys()) if rows else [])
        with open(csv_dir / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  wrote {csv_dir / name}: {len(rows)} rows")

    write("places.csv", place_rows)
    write("place_descriptors.csv", desc_rows)
    write("place_appearances.csv", app_rows)
    write("place_characters.csv", char_rows)
    if existing:
        print(f"  {len(existing)} places were already in places.csv: ids kept, hand columns "
              f"({', '.join(HAND_COLUMNS)}) preserved, {kept_manual} not re-found but retained. "
              f"Old file saved as places.csv.bak")

    print("\nTop places by mentions (eyeball these for false positives, then add to IGNORE / ALIASES):")
    for r in place_rows[:25]:
        print(f"  {r['place_id']}  {r['place_name']:<28} {r['place_kind']:<8} "
              f"{r['mention_count']:>4}x  ch {r['first_chapter']}-{r['last_chapter']}")


if __name__ == "__main__":
    main()