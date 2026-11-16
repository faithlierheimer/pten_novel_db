"""
Load any new CSVs from csv_data into a DuckDB database.

For every .csv in csv_data, the table name is the file name (characters.csv ->
characters). If the database already has that table, the CSV is skipped. If not,
it gets loaded. So dropping a new CSV into csv_data and re-running is all it takes.

The database file lives next to this script (in database_building) and is added
to a .gitignore in the same folder so it doesn't get committed.

Setup:
    pip install duckdb

Usage:
    python3 load_duckdb.py                       # load only CSVs that have no table yet
    python3 load_duckdb.py --reload characters   # drop and reload specific tables
    python3 load_duckdb.py --reload-all          # drop and reload everything
"""

import re
import sys
from pathlib import Path

import duckdb

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "csv_data"
DB_PATH = SCRIPT_DIR / "pten_novel.duckdb"

GITIGNORE_LINES = ["*.duckdb", "*.duckdb.wal"]


def ensure_gitignore():
    """Append the database patterns to .gitignore if they aren't already there."""
    path = SCRIPT_DIR / ".gitignore"
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    missing = [line for line in GITIGNORE_LINES if line not in existing]
    if missing:
        with open(path, "a", encoding="utf-8") as f:
            if existing and existing[-1].strip():
                f.write("\n")
            f.write("\n".join(missing) + "\n")
        print(f"Added to {path.name}: {', '.join(missing)}")


def table_name(csv_path: Path) -> str:
    """File name -> safe table name: lowercase, letters/digits/underscores only."""
    name = re.sub(r"[^0-9a-zA-Z]+", "_", csv_path.stem).strip("_").lower()
    return f"t_{name}" if not name or name[0].isdigit() else name


def parse_args():
    args = sys.argv[1:]
    reload_all = "--reload-all" in args
    reload_tables = set()
    if "--reload" in args:
        reload_tables = {a.lower() for a in args[args.index("--reload") + 1:] if not a.startswith("--")}
    return reload_all, reload_tables


def main():
    reload_all, reload_tables = parse_args()

    if not DATA_DIR.exists():
        raise SystemExit(f"Can't find {DATA_DIR}")
    csv_files = sorted(DATA_DIR.glob("*.csv"))
    if not csv_files:
        raise SystemExit(f"No .csv files found in {DATA_DIR}")

    ensure_gitignore()

    con = duckdb.connect(str(DB_PATH))
    existing = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}

    loaded, skipped = [], []
    for csv_path in csv_files:
        table = table_name(csv_path)
        force = reload_all or table in reload_tables

        if table in existing and not force:
            skipped.append(table)
            continue

        con.execute(
            f'CREATE OR REPLACE TABLE "{table}" AS '
            f"SELECT * FROM read_csv('{csv_path.as_posix()}', header = true)"
        )
        count = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        print(f"Loaded {count} rows into {table}" + (" (reloaded)" if table in existing else ""))
        loaded.append(table)

    for table in skipped:
        print(f"Skipped {table} (already in database)")

    unknown = reload_tables - {table_name(p) for p in csv_files}
    if unknown:
        print(f"No CSV found for: {', '.join(sorted(unknown))}")

    # Sanity checks, only when both tables are present
    tables_now = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if {"characters", "appearances"} <= tables_now:
        dupes = con.execute(
            "SELECT count(*) FROM (SELECT character_id FROM characters "
            "GROUP BY character_id HAVING count(*) > 1)"
        ).fetchone()[0]
        orphans = con.execute(
            "SELECT count(*) FROM appearances a "
            "LEFT JOIN characters c USING (character_id) WHERE c.character_id IS NULL"
        ).fetchone()[0]
        print(f"Duplicate character ids: {dupes}")
        print(f"Appearance rows with no matching character: {orphans}")

    con.close()
    print(f"Done. {len(loaded)} loaded, {len(skipped)} skipped. Database: {DB_PATH}")


if __name__ == "__main__":
    main()