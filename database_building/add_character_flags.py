#!/usr/bin/env python3
"""
Add two columns to csv_data/characters.csv:

    canonical_character_id   the id this row should roll up to (its own id if it is the main entry)
    is_character             1 = person/animal in the story, 0 = real-world person or non-character

character_id values are never changed and no rows are added or removed.
Safe to re-run: values already in the file are kept, only blanks are filled.
The original is saved as characters.csv.bak before writing.

    python3 add_character_flags.py                    # uses csv_data/characters.csv
    python3 add_character_flags.py --csv path/to/characters.csv
    python3 add_character_flags.py --dry-run          # show what would change, write nothing

Edit ALIAS_OF and NOT_CHARACTERS below, then re-run with --force to apply edits
over values that are already in the file.
"""
import argparse
import csv
import shutil
import sys
from pathlib import Path

# variant name -> main name. Names must match the `name` column exactly.
ALIAS_OF = {
    "Char": "Charlotte",
    "Charlotte Byers": "Charlotte",     
    "Oli": "Oliver",
    "Oliver Baker": "Oliver",           
    "Sutt": "Sutton",
    "Lace": "Laci Peters",
    "Boessers": "Boesser",              # GUESS: plural = the family; rolled into the singular
    "Girards": "Girard",                # GUESS: same
    "Mansons": "Manson",                # GUESS: same
    # "Soderbergs": "Merle Soderberg",  # left separate on purpose: a family is not one person
}

# real-world people / references that are not characters in the story
NOT_CHARACTERS = {
    "Bruce Springsteen", "David Attenborough", "Hilary Duff", "Bradley Cooper",
    "Zooey Deschanel", "Jesus", "Charles Manson", "Lizzie McGuire Miranda",
}

# printed at the end as a reminder; not changed by the script
CHECK_BY_HAND = ["Song", "Blazer", "Calathea", "Kay", "Mason", "Scout", "Mom", "Manson", "Soderbergs"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="csv_data/characters.csv")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="overwrite values already in the new columns")
    a = ap.parse_args()

    path = Path(a.csv)
    if not path.exists():
        sys.exit(f"Can't find {path}. Run from the repo root or pass --csv.")

    with open(path, newline="", encoding="utf-8-sig") as fh:
        first = fh.readline()
        delim = "\t" if first.count("\t") > first.count(",") else ","
        fh.seek(0)
        reader = csv.DictReader(fh, delimiter=delim)
        fields = list(reader.fieldnames)
        rows = list(reader)

    if "character_id" not in fields or "name" not in fields:
        sys.exit(f"Expected character_id and name columns, found {fields}")

    id_by_name = {}
    for r in rows:
        id_by_name.setdefault(r["name"].strip(), r["character_id"])

    problems = [n for n in list(ALIAS_OF) + list(ALIAS_OF.values()) + list(NOT_CHARACTERS) if n not in id_by_name]
    if problems:
        print("Not found in characters.csv (check spelling):", ", ".join(sorted(set(problems))))

    for col in ("canonical_character_id", "is_character"):
        if col not in fields:
            fields.append(col)

    changes = []
    for r in rows:
        name = r["name"].strip()
        canon = id_by_name.get(ALIAS_OF.get(name, name), r["character_id"])
        is_char = "0" if name in NOT_CHARACTERS else "1"
        for col, val in (("canonical_character_id", canon), ("is_character", is_char)):
            if a.force or not (r.get(col) or "").strip():
                r[col] = val
        if r["canonical_character_id"] != r["character_id"] or r["is_character"] == "0":
            changes.append(r)

    print(f"{len(rows)} rows. Rolled up to another id, or flagged not-a-character:")
    for r in changes:
        tgt = next((x["name"] for x in rows if x["character_id"] == r["canonical_character_id"]), "?")
        note = "NOT A CHARACTER" if r["is_character"] == "0" else f"-> {r['canonical_character_id']} ({tgt})"
        print(f"  {r['character_id']:>3}  {r['name']:<24} {note}")
    print("Worth a look by hand:", ", ".join(CHECK_BY_HAND))

    if a.dry_run:
        print("\nDry run: nothing written.")
        return
    shutil.copy(path, str(path) + ".bak")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, delimiter=delim, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {path} (backup: {path}.bak)")


if __name__ == "__main__":
    main()