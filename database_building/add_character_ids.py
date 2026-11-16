"""
Assign a unique, permanent id to every row in characters.csv, and copy those ids
into appearances.csv so the two tables can be linked.

Safe to re-run: rows that already have an id keep it, and only new rows get new ids
(numbered after the highest existing id). That way ids stay stable while you clean
up the data by hand.

Usage:
    python3 add_ids.py
    python3 add_ids.py characters.csv appearances.csv
"""

import csv
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "csv_data"   # csv_data sits next to database_building
CHARACTERS_CSV = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA_DIR / "characters.csv"
APPEARANCES_CSV = Path(sys.argv[2]) if len(sys.argv) > 2 else DATA_DIR / "appearances.csv"

ID_COLUMN = "character_id"


def read_csv(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def write_csv(path: Path, rows: list, fieldnames: list):
    # utf-8-sig so Excel displays accented characters correctly
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not CHARACTERS_CSV.exists():
        sys.exit(f"Can't find {CHARACTERS_CSV}")

    shutil.copy(CHARACTERS_CSV, CHARACTERS_CSV.with_suffix(".csv.bak"))

    rows, fields = read_csv(CHARACTERS_CSV)

    # Keep any ids already assigned; only fill in the blanks
    used = [int(r[ID_COLUMN]) for r in rows if str(r.get(ID_COLUMN, "")).strip().isdigit()]
    next_id = max(used, default=0) + 1

    new_count = 0
    for row in rows:
        if not str(row.get(ID_COLUMN, "")).strip().isdigit():
            row[ID_COLUMN] = next_id
            next_id += 1
            new_count += 1

    if ID_COLUMN in fields:
        fields.remove(ID_COLUMN)
    fields.insert(0, ID_COLUMN)   # id goes in the first column

    write_csv(CHARACTERS_CSV, rows, fields)
    print(f"{CHARACTERS_CSV.name}: {new_count} new ids assigned, {len(rows)} rows total")

    # Link the appearances table by name
    if APPEARANCES_CSV.exists():
        shutil.copy(APPEARANCES_CSV, APPEARANCES_CSV.with_suffix(".csv.bak"))
        app_rows, app_fields = read_csv(APPEARANCES_CSV)
        id_by_name = {r["name"]: r[ID_COLUMN] for r in rows}

        missing = set()
        for r in app_rows:
            cid = id_by_name.get(r["name"], "")
            r[ID_COLUMN] = cid
            if not cid:
                missing.add(r["name"])

        if ID_COLUMN in app_fields:
            app_fields.remove(ID_COLUMN)
        app_fields.insert(0, ID_COLUMN)

        write_csv(APPEARANCES_CSV, app_rows, app_fields)
        print(f"{APPEARANCES_CSV.name}: ids linked for {len(app_rows)} rows")
        if missing:
            print(f"  No match for {len(missing)} names (renamed or deleted in characters.csv?): "
                  + ", ".join(sorted(missing)[:10]) + (" ..." if len(missing) > 10 else ""))
    else:
        print(f"{APPEARANCES_CSV.name} not found, skipped")


if __name__ == "__main__":
    main()