#!/usr/bin/env python3
"""
Build the chapters tables from the manuscript.

Reads   <novel-dir>/<N_beat_name>/<chapter or chapter_N>/act_<A>_ch_<C>_<title>.txt
        csv_data/characters.csv   (optional; uses canonical_character_id / is_character if present)

Writes  csv_data/chapters.csv            one row per chapter (key: chapter)
        csv_data/scenes.csv              scenes found inside each chapter
        csv_data/chapter_characters.csv  characters mentioned per chapter
        csv_data/chapter_weather.csv     weather categories and the words used, per chapter

Usage
    python3 extract_chapters.py --probe                    # FIRST: show which short lines repeat (scene-break clues)
    python3 extract_chapters.py                            # run it
    python3 extract_chapters.py --blank-lines 3            # also treat 3+ blank lines in a row as a scene break

Scenes: your files are chunks, not scenes, so scenes are DETECTED. A scene break is a line that is only
*, * * *, ***, ---, ###, "scene break", or "Scene 2" (see BREAK_RES). Chapters with no break are one scene.
Scene ids are regenerated on every run (they are not permanent like character/place ids).

Summaries are not generated here. The `summary` column is kept across runs; fill it by hand or with
summary_worksheet.py. Also kept across runs: summary_source and notes. Old chapters.csv is saved as .bak.
"""
import argparse
import csv
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

from novel_common import CharacterIndex, clean_beat_name, humanize, read_chapters

# ---------------------------------------------------------------------------
# Config you can edit
# ---------------------------------------------------------------------------
BREAK_RES = [
    re.compile(r"^\s*(?:[*•]\s*)+$"),                              # *   * * *   ***
    re.compile(r"^\s*[-–—_=~#]{3,}\s*$"),                          # ---  ___  ===  ###
    re.compile(r"^\s*#\s*$"),
    re.compile(r"^\s*\[?\s*(?:scene\s*)?break\s*\]?\s*$", re.I),   # scene break / [break]
    re.compile(r"^\s*(?:#+\s*)?scene\s+\d+\b.{0,40}$", re.I),      # Scene 2 / Scene 2: The Zoo
]

# category -> regex alternatives. Matching is case-insensitive on whole words.
# Words that are usually NOT weather (plain "sun", "shower", "wind" as a verb) are left out on purpose.
WEATHER = {
    "rain": r"rain(?:s|ed|ing|y|drops?|fall|storms?)?|drizzl\w*|downpours?",
    "snow": r"snow(?:s|ed|ing|y|fall|storms?|flakes?|drifts?|banks?)?|blizzards?|flurr(?:y|ies)",
    "ice/hail": r"sleet|freezing rain|icy|black ice|ice storm|hailstones?|hailstorm",
    "fog/mist": r"fog|foggy|mist|misty|haze|hazy",
    "storm": r"thunder(?:storms?|ed|ing|s|clap)?|lightning|storms?|stormy|tornado(?:es)?|hurricanes?",
    "wind": (r"windy|gusts?|gusty|breez(?:e|es|y)|"
             r"(?:the|a|cold|warm|strong|biting|howling|north|south|east|west|chill|stiff|sharp|hard|cool|icy|bitter|wicked)\s+winds?"),
    "clouds": r"clouds?|cloudy|overcast|cloudless",
    "sun/clear": r"sunny|sunshine|sunlit|clear skies?|blue skies?",
    "heat": r"heat ?wave|humid(?:ity)?|muggy|sweltering|scorching|swelter\w*",
    "cold": r"frost(?:y|ed)?|freezing|frigid|chilly|cold snap|cold front|subzero|below zero|bitter cold",
    "flood/drought": r"floods?|flooded|flooding|drought",
    "dew": r"dew|dewy",
}
WEATHER_RES = {k: re.compile(rf"\b(?:{v})\b", re.I) for k, v in WEATHER.items()}

TOP_CHARACTERS = 8          # characters listed in chapters.csv
HAND_COLUMNS = ["summary", "summary_source", "notes"]   # never overwritten
CHAPTER_FIELDS = [
    "chapter", "title", "beat_no", "beat_name", "act", "file_count", "files", "word_count",
    "scene_count", "dialogue_pct", "character_count", "characters", "has_weather", "weather_terms",
    "summary", "summary_source", "notes",
]

QUOTE_RES = [re.compile(r"“[^”]*”"), re.compile(r'"[^"\n]*"')]


# ---------------------------------------------------------------------------
def is_break(line: str) -> bool:
    return any(r.match(line) for r in BREAK_RES)


def split_scenes(chunks, blank_run):
    """chunks: [(filename, text)] -> (scenes, method). Scene = {'lines': [...], 'start_file': str}.
    A file boundary is not a scene break (files are chunks of a chapter)."""
    scenes, cur, methods = [], None, set()

    def flush():
        nonlocal cur
        if cur and "".join(cur["lines"]).strip():
            scenes.append(cur)
        cur = None

    for fname, text in chunks:
        run = 0
        for line in text.splitlines():
            if is_break(line):
                flush()
                methods.add("marker")
                run = 0
                continue
            if not line.strip():
                run += 1
                continue
            if blank_run and run >= blank_run and cur is not None:
                flush()
                methods.add("blank_lines")
            run = 0
            if cur is None:
                cur = {"lines": [], "start_file": fname}
            cur["lines"].append(line)
    flush()
    method = "+".join(sorted(methods)) if methods else "none"
    return scenes, method


def weather_in(text):
    """Return ({category: count}, {category: Counter(words)}, {category: example sentence})."""
    counts, words, examples = Counter(), defaultdict(Counter), {}
    for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
        for cat, rx in WEATHER_RES.items():
            for m in rx.finditer(sent):
                counts[cat] += 1
                words[cat][m.group(0).lower()] += 1
                examples.setdefault(cat, sent.strip()[:220])
    return counts, words, examples


def dialogue_words(text):
    n = 0
    for rx in QUOTE_RES:
        for m in rx.finditer(text):
            n += len(m.group(0).split())
    return n


def probe(chapters):
    """Show the most common short lines so you can see how scene breaks are written."""
    c = Counter()
    for ch in chapters.values():
        for _, text in ch["chunks"]:
            for line in text.splitlines():
                s = line.strip()
                if s and len(s) <= 14:
                    c[s] += 1
    print("Most common short lines across the manuscript (scene-break marker candidates):")
    for s, n in c.most_common(15):
        flag = "  <- recognised as a scene break" if is_break(s) else ""
        print(f"  {n:>5}x  {s!r}{flag}")
    print("\nIf your break marker isn't flagged, add a pattern to BREAK_RES, or use --blank-lines N.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--novel-dir", default="PTEN_manuscript")
    ap.add_argument("--csv-dir", default="csv_data")
    ap.add_argument("--blank-lines", type=int, default=0,
                    help="treat this many blank lines in a row as a scene break (0 = off)")
    ap.add_argument("--expect-chapters", type=int, default=42)
    ap.add_argument("--probe", action="store_true", help="only show common short lines, write nothing")
    a = ap.parse_args()

    novel_dir, csv_dir = Path(a.novel_dir), Path(a.csv_dir)
    if not novel_dir.is_dir():
        sys.exit(f"Can't find {novel_dir}/. Run from the folder that contains it, or pass --novel-dir.")
    chapters = read_chapters(novel_dir)
    if not chapters:
        sys.exit(f"No text files matched under {novel_dir}. Run extract_places.py to see the layout diagnostics.")
    if a.probe:
        probe(chapters)
        return
    csv_dir.mkdir(exist_ok=True)
    index = CharacterIndex(csv_dir / "characters.csv")

    chapter_rows, scene_rows, cc_rows, cw_rows = [], [], [], []
    methods = Counter()
    for num in sorted(chapters):
        ch = chapters[num]
        meta = ch["meta"]
        if len(ch["beats"]) > 1:
            print(f"  ! chapter {num} appears in more than one beat folder: {sorted(ch['beats'])}", file=sys.stderr)
        if len(ch["acts"]) > 1:
            print(f"  ! chapter {num} has more than one act in its filenames: {sorted(ch['acts'])}", file=sys.stderr)

        scenes, method = split_scenes(ch["chunks"], a.blank_lines)
        methods[method] += 1
        ch_text = "\n".join("\n".join(s["lines"]) for s in scenes)
        ch_words = len(ch_text.split())

        ch_counts, ch_words_by_cat, ch_examples = weather_in(ch_text)
        char_total, char_scenes = Counter(), Counter()
        for i, s in enumerate(scenes, 1):
            text = "\n".join(s["lines"])
            sc_chars = index.count(text)
            char_total.update(sc_chars)
            char_scenes.update(sc_chars.keys())
            sc_weather, _, _ = weather_in(text)
            first = re.sub(r"\s+", " ", s["lines"][0]).strip()
            scene_rows.append({
                "scene_id": f"{num:02d}.{i:02d}", "chapter": num, "scene_number": i,
                "start_file": s["start_file"], "word_count": len(text.split()),
                "characters": "; ".join(f"{index.name(c)} ({n})" for c, n in sc_chars.most_common(8)),
                "weather_terms": "; ".join(f"{k} ({n})" for k, n in sc_weather.most_common()),
                "split_method": method, "first_line": first[:120],
            })
        for cat, n in ch_counts.most_common():
            cw_rows.append({"chapter": num, "category": cat, "count": n,
                            "words": "; ".join(f"{w} ({k})" for w, k in ch_words_by_cat[cat].most_common()),
                            "example": ch_examples[cat]})
        for cid, n in char_total.most_common():
            cc_rows.append({"chapter": num, "character_id": cid, "character_name": index.name(cid),
                            "mention_count": n, "scene_count": char_scenes[cid]})

        dpct = round(100 * dialogue_words(ch_text) / ch_words, 1) if ch_words else 0
        titles = list(dict.fromkeys(ch["titles"]))
        chapter_rows.append({
            "chapter": num, "title": humanize(titles[0]), "beat_no": meta["beat_no"],
            "beat_name": clean_beat_name(meta["beat_name"]), "act": min(ch["acts"]),
            "file_count": len(ch["chunks"]), "files": " | ".join(f for f, _ in ch["chunks"]),
            "word_count": ch_words, "scene_count": len(scenes), "dialogue_pct": dpct,
            "character_count": len(char_total),
            "characters": "; ".join(f"{index.name(c)} ({n})" for c, n in char_total.most_common(TOP_CHARACTERS)),
            "has_weather": int(bool(ch_counts)),
            "weather_terms": "; ".join(f"{k} ({n})" for k, n in ch_counts.most_common()),
            "summary": "", "summary_source": "", "notes": "",
        })

    # keep hand-entered columns from an existing chapters.csv
    path = csv_dir / "chapters.csv"
    extra_fields = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            old = {int(r["chapter"]): r for r in rd if (r.get("chapter") or "").isdigit()}
            extra_fields = [f for f in (rd.fieldnames or []) if f not in CHAPTER_FIELDS]
        shutil.copy(path, str(path) + ".bak")
        for r in chapter_rows:
            o = old.get(r["chapter"], {})
            for col in HAND_COLUMNS + extra_fields:
                if o.get(col):
                    r[col] = o[col]
            for col in extra_fields:
                r.setdefault(col, o.get(col, ""))
        have = {r["chapter"] for r in chapter_rows}
        for n, o in old.items():          # chapters in the old file that this run didn't find
            if n not in have:
                chapter_rows.append({**{f: "" for f in CHAPTER_FIELDS + extra_fields}, **o})
        chapter_rows.sort(key=lambda r: int(r["chapter"]))
        print(f"  existing chapters.csv: kept {', '.join(HAND_COLUMNS)}; old file saved as chapters.csv.bak")

    def write(name, rows, fields):
        with open(csv_dir / name, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"  wrote {csv_dir / name}: {len(rows)} rows")

    print(f"\nRead {len(chapters)} chapters from {sum(len(c['chunks']) for c in chapters.values())} files.")
    write("chapters.csv", chapter_rows, CHAPTER_FIELDS + extra_fields)
    write("scenes.csv", scene_rows,
          ["scene_id", "chapter", "scene_number", "start_file", "word_count", "characters", "weather_terms",
           "split_method", "first_line"])
    write("chapter_characters.csv", cc_rows, ["chapter", "character_id", "character_name", "mention_count", "scene_count"])
    write("chapter_weather.csv", cw_rows, ["chapter", "category", "count", "words", "example"])

    # sanity report
    missing = [n for n in range(1, max(chapters) + 1) if n not in chapters]
    print(f"\nChapters found: {len(chapters)} (expected {a.expect_chapters})"
          + (f"; missing numbers: {missing}" if missing else ""))
    print("Scene splitting by chapter: " + ", ".join(f"{k}: {v}" for k, v in methods.most_common()))
    if methods.get("none", 0) > len(chapters) / 2:
        print("  -> Most chapters had no scene break, so they are 1 scene each. "
              "Run with --probe to see how your breaks are written.")
    words = sorted(chapter_rows, key=lambda r: int(r["word_count"] or 0))
    tot = sum(int(r["word_count"] or 0) for r in chapter_rows)
    print(f"Total words: {tot:,}. Shortest: ch {words[0]['chapter']} ({words[0]['word_count']}), "
          f"longest: ch {words[-1]['chapter']} ({words[-1]['word_count']}).")
    if not index.names:
        print("No characters loaded, so chapter_characters.csv is empty.")


if __name__ == "__main__":
    main()