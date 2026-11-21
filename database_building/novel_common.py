"""Shared helpers for the novel database scripts (chapters, and anything else that reads the manuscript)."""
import csv
import re
import sys
from collections import Counter
from pathlib import Path

BEAT_RE = re.compile(r"^(\d+)_(.+)$")
CHAPTER_DIR_RE = re.compile(r"^(?:chapter[_ -]?)?(\d+)$", re.I)   # "25" or "chapter_25"
FILE_RE = re.compile(r"^act_(\d+)_ch_(\d+)_(.+)\.txt$", re.I)


def clean_beat_name(name: str) -> str:
    """'setup_act_1' -> 'setup'"""
    return re.sub(r"_?act_\d+$", "", name, flags=re.I).strip("_")


def humanize(slug: str) -> str:
    return slug.replace("_", " ").strip()


def iter_text_files(novel_dir: Path):
    """Yield dicts of file metadata in story order (beat number, chapter number, then file name)."""
    beats = []
    for d in novel_dir.iterdir():
        m = BEAT_RE.match(d.name)
        if d.is_dir() and m:
            beats.append((int(m.group(1)), m.group(2), d))
    for beat_no, beat_name, bdir in sorted(beats):
        for stray in sorted(bdir.glob("*.txt")):
            print(f"  ! skipping text file not inside a chapter folder: {stray}", file=sys.stderr)
        chapters = sorted((int(CHAPTER_DIR_RE.match(c.name).group(1)), c) for c in bdir.iterdir()
                          if c.is_dir() and CHAPTER_DIR_RE.match(c.name))
        for ch_folder_no, cdir in chapters:
            for f in sorted(p for p in cdir.iterdir() if p.suffix.lower() == ".txt"):
                m = FILE_RE.match(f.name)
                if not m:
                    print(f"  ! skipping unrecognised filename: {f}", file=sys.stderr)
                    continue
                act, ch_in_name, title = int(m.group(1)), int(m.group(2)), m.group(3)
                if ch_in_name != ch_folder_no:
                    print(f"  ! chapter mismatch: folder {ch_folder_no} vs filename {ch_in_name} ({f})",
                          file=sys.stderr)
                yield {"path": f, "beat_no": beat_no, "beat_name": beat_name, "chapter": ch_folder_no,
                       "act": act, "file": f.name, "title": title}


def read_chapters(novel_dir: Path):
    """{chapter_number: {'chunks': [(filename, text)], 'beats': set, 'acts': set, 'titles': [..], 'meta': first file meta}}
    Files within a chapter are in file-name order, which may not be story order if a chapter has several chunks."""
    chapters = {}
    for meta in iter_text_files(novel_dir):
        c = chapters.setdefault(meta["chapter"], {"chunks": [], "beats": set(), "acts": set(),
                                                  "titles": [], "meta": meta})
        text = meta["path"].read_text(encoding="utf-8-sig", errors="replace")
        c["chunks"].append((meta["file"], text))
        c["beats"].add(meta["beat_no"])
        c["acts"].add(meta["act"])
        c["titles"].append(meta["title"])
    return chapters


class CharacterIndex:
    """Finds characters in text. Uses columns from characters.csv:
         character_id, name                      (required; name column guessed)
         aliases                                 (optional, separated by | or ;)
         canonical_character_id                  (optional; variants roll up to this id)
         is_character                            (optional; 0 = ignore this row)
       Longest name wins, so 'Charlotte Byers' is one hit, not also 'Charlotte'."""

    def __init__(self, csv_path):
        self.names, self._to_canon, self.rx = {}, {}, None
        path = Path(csv_path)
        if not path.exists():
            print(f"  (no {path}; character columns will be empty)")
            return
        with open(path, newline="", encoding="utf-8-sig") as fh:
            first = fh.readline()
            delim = "\t" if first.count("\t") > first.count(",") else ","
            fh.seek(0)
            rows = list(csv.DictReader(fh, delimiter=delim))
        if not rows:
            return
        cols = rows[0].keys()
        name_col = next((c for c in ("name", "character_name", "canonical_name", "full_name") if c in cols), None)
        if "character_id" not in cols or not name_col:
            print(f"  ! characters.csv needs character_id and a name column (found {list(cols)}); skipping characters",
                  file=sys.stderr)
            return
        rows = [r for r in rows if (r.get("is_character") or "1").strip().lower() not in ("0", "false", "no")]
        ids = {r["character_id"] for r in rows}
        for r in rows:
            self.names[r["character_id"]] = r[name_col].strip()
        canon = {}
        for r in rows:
            c = (r.get("canonical_character_id") or "").strip()
            canon[r["character_id"]] = c if c in ids else r["character_id"]
        first_tokens = Counter(r[name_col].split()[0].lower() for r in rows)
        for r in rows:
            name = r[name_col].strip()
            variants = {name}
            for a in re.split(r"[|;]", r.get("aliases", "") or ""):
                if a.strip():
                    variants.add(a.strip())
            first = name.split()[0]
            if len(first) > 2 and first_tokens[first.lower()] == 1:
                variants.add(first)
            for v in variants:
                if len(v) < 3:
                    continue
                target = canon[r["character_id"]]
                if v in self._to_canon and self._to_canon[v] != target:
                    print(f"  ! '{v}' maps to two different characters; keeping id {self._to_canon[v]}",
                          file=sys.stderr)
                    continue
                self._to_canon[v] = target
        if self._to_canon:
            alts = "|".join(re.escape(v) for v in sorted(self._to_canon, key=len, reverse=True))
            self.rx = re.compile(rf"(?<!\w)(?:{alts})(?!\w)")

    def count(self, text: str) -> Counter:
        """Counter of canonical character_id -> number of mentions."""
        if not self.rx:
            return Counter()
        return Counter(self._to_canon[m.group(0)] for m in self.rx.finditer(text))

    def name(self, cid: str) -> str:
        return self.names.get(cid, cid)