#!/usr/bin/env python3
"""
Write chapter summaries by hand in a text file instead of editing chapters.csv cell by cell.
No API, no network.

    python3 summary_worksheet.py export                 # writes summaries_worksheet.md
    python3 summary_worksheet.py export --only-blank    # only chapters with no summary yet
    python3 summary_worksheet.py import                 # reads it back into chapters.csv

Edit the worksheet in any text editor: type one sentence after each `SUMMARY:` line.

Safe to repeat:
  * export never loses typed-but-not-yet-imported text: anything you wrote in an existing worksheet is
    carried into the new one, and the old worksheet is saved as summaries_worksheet.md.bak
  * import only writes non-blank summaries; a blank one never erases an existing summary
  * summaries you typed get summary_source = manual; chapters.csv.bak is saved before each import
  * keep the `## Chapter N` lines as they are: they tell import which chapter a summary belongs to
"""
import argparse
import csv
import re
import shutil
import sys
from pathlib import Path

from extract_chapters import is_break
from novel_common import read_chapters

SENT_SPLIT = re.compile(r"(?<=[.!?…”\"'’])\s+(?=[A-Z“\"‘'])")
HEADER_RE = re.compile(r"^##\s+Chapter\s+(\d+)\b", re.I)
MAX_WORDS = 40


def first_last_sentences(chunks):
    lines = [ln for _, text in chunks for ln in text.splitlines() if ln.strip() and not is_break(ln)]
    flat = re.sub(r"\s+", " ", " ".join(lines)).strip()
    if not flat:
        return "", ""
    sents = SENT_SPLIT.split(flat)
    clip = lambda s: s if len(s) <= 200 else s[:197].rstrip() + "..."  # noqa: E731
    return clip(sents[0]), clip(sents[-1])


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        return list(rd.fieldnames), list(rd)


def parse_worksheet(path):
    """{chapter: summary text (may be '')}. A summary runs from the SUMMARY: line to the next chapter header."""
    out, cur, collecting, buf = {}, None, False, []

    def close():
        if cur is not None:
            out[cur] = " ".join(" ".join(buf).split())

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = HEADER_RE.match(line)
        if m:
            close()
            cur, collecting, buf = int(m.group(1)), False, []
            continue
        if cur is None:
            continue
        if line.startswith("SUMMARY:"):
            collecting, buf = True, [line[len("SUMMARY:"):]]
        elif collecting and line.strip() != "---":
            buf.append(line)
    close()
    return out


def cmd_export(a):
    fields, rows = read_csv(Path(a.csv_dir) / "chapters.csv")
    chapters = read_chapters(Path(a.novel_dir))
    ws_path = Path(a.worksheet)

    typed = {}
    if ws_path.exists():
        typed = {n: t for n, t in parse_worksheet(ws_path).items() if t}
        shutil.copy(ws_path, str(ws_path) + ".bak")

    blocks, carried, written = [], 0, 0
    for r in rows:
        n = int(r["chapter"])
        saved = (r.get("summary") or "").strip()
        text = typed.get(n, saved)
        if n in typed and typed[n] != saved:
            carried += 1
        if a.only_blank and text:
            continue
        opens, ends = first_last_sentences(chapters[n]["chunks"]) if n in chapters else ("", "")
        meta = (f"Beat {r['beat_no']} {r['beat_name']} · Act {r['act']} · "
                f"{int(r['word_count'] or 0):,} words · {r['scene_count']} scenes")
        block = [f"## Chapter {n}: {r['title']}", meta]
        if r.get("characters"):
            block.append(f"Characters: {r['characters']}")
        if r.get("weather_terms"):
            block.append(f"Weather: {r['weather_terms']}")
        if opens:
            block.append(f"Opens: {opens}")
            block.append(f"Ends:  {ends}")
        block += ["", f"SUMMARY: {text}".rstrip(), "", "---", ""]
        blocks.append("\n".join(block))
        written += 1

    header = ("# Chapter summary worksheet\n\n"
              "Type ONE sentence after each `SUMMARY:` (aim for under 35 words). Leave a chapter blank to skip it.\n"
              "Keep the `## Chapter N` lines unchanged. When done: `python3 summary_worksheet.py import`\n\n---\n\n")
    ws_path.write_text(header + "\n".join(blocks), encoding="utf-8")
    print(f"Wrote {ws_path}: {written} chapters"
          + (" (blank summaries only)" if a.only_blank else "")
          + (f"; carried forward {carried} typed-but-not-imported summaries" if carried else "") + ".")
    if typed:
        print(f"Previous worksheet saved as {ws_path}.bak")


def cmd_import(a):
    ws_path = Path(a.worksheet)
    if not ws_path.exists():
        sys.exit(f"Can't find {ws_path}. Run `export` first.")
    csv_path = Path(a.csv_dir) / "chapters.csv"
    fields, rows = read_csv(csv_path)
    for col in ("summary", "summary_source"):
        if col not in fields:
            fields.append(col)
    sheet = parse_worksheet(ws_path)
    by_num = {int(r["chapter"]): r for r in rows}

    unknown = sorted(n for n in sheet if n not in by_num)
    if unknown:
        print(f"! worksheet has chapters not in chapters.csv (ignored): {unknown}")

    changed, warnings = 0, []
    for n, text in sorted(sheet.items()):
        r = by_num.get(n)
        if r is None or not text:
            continue
        words = len(text.split())
        if words > MAX_WORDS:
            warnings.append(f"ch {n}: {words} words (over {MAX_WORDS})")
        if len(SENT_SPLIT.split(text)) > 1:
            warnings.append(f"ch {n}: looks like more than one sentence")
        old = (r.get("summary") or "").strip()
        if text != old or not (r.get("summary_source") or "").strip():
            r["summary"], r["summary_source"] = text, "manual"
            changed += 1

    shutil.copy(csv_path, str(csv_path) + ".bak")
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    filled = sum(1 for r in rows if (r.get("summary") or "").strip())
    print(f"Updated {changed} summaries. {filled}/{len(rows)} chapters now have a summary. "
          f"(backup: {csv_path}.bak)")
    blank = [r["chapter"] for r in rows if not (r.get("summary") or "").strip()]
    if blank:
        print(f"Still blank: chapters {', '.join(blank)}")
    for w_ in warnings:
        print(f"  note: {w_}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["export", "import"])
    ap.add_argument("--novel-dir", default="PTEN_manuscript")
    ap.add_argument("--csv-dir", default="csv_data")
    ap.add_argument("--worksheet", default="summaries_worksheet.md")
    ap.add_argument("--only-blank", action="store_true", help="export: skip chapters that already have a summary")
    a = ap.parse_args()
    if not (Path(a.csv_dir) / "chapters.csv").exists():
        sys.exit(f"Can't find {a.csv_dir}/chapters.csv. Run extract_chapters.py first.")
    (cmd_export if a.action == "export" else cmd_import)(a)


if __name__ == "__main__":
    main()