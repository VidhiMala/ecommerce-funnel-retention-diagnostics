"""
inspect_raw.py - Day 1: profile raw event CSVs WITHOUT loading them into RAM.

Why: we never assume the schema. DuckDB streams each file from disk, infers
column types from the full file, and reports schema, sample rows, raw text
format, per-column stats and event mix, so the cleaning rules (Day 2) are
based on what the data actually contains.

Usage (from the project root):
    python src/inspect_raw.py data/raw
    python src/inspect_raw.py data/raw --out reports/day01_raw_profile.txt

--out writes a UTF-8 report directly. Prefer it over shell redirection ('>')
on Windows, where console encodings can break DuckDB's table borders.
"""
import argparse
import sys
import time
from pathlib import Path

import duckdb


def profile_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    src = path.as_posix()
    # sample_size=-1 -> infer types from the whole file, not just the first rows
    rel = f"read_csv('{src}', header=true, sample_size=-1)"
    t0 = time.perf_counter()
    print(f"\n{'=' * 80}\nFILE: {path.name}   size on disk: {path.stat().st_size / 1e6:,.1f} MB\n{'=' * 80}")

    print("\n-- Schema (column names and inferred types)")
    schema = con.sql(f"DESCRIBE SELECT * FROM {rel}").fetchall()
    for col, dtype, *_ in schema:
        print(f"   {col:<20} {dtype}")
    columns = {c[0] for c in schema}

    print("\n-- First 3 rows as RAW TEXT (exact formats before any type parsing)")
    print(con.sql(f"SELECT * FROM read_csv('{src}', header=true, all_varchar=true) LIMIT 3"))

    print("\n-- First 5 rows (parsed)")
    print(con.sql(f"SELECT * FROM {rel} LIMIT 5"))

    print("\n-- Column profile (min / max / approx distinct / null % / row count)")
    print(con.sql(
        f"SELECT column_name, column_type, min, max, approx_unique, null_percentage, count "
        f"FROM (SUMMARIZE SELECT * FROM {rel})"
    ))

    # Only run the event-level breakdown if that column really exists.
    if "event_type" in columns:
        print("\n-- Rows per event_type")
        print(con.sql(
            f"SELECT event_type, COUNT(*) AS n, "
            f"ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct "
            f"FROM {rel} GROUP BY 1 ORDER BY 2 DESC"
        ))
    else:
        print("\n(!) No 'event_type' column found - check the schema above.")

    print(f"\n(profiled in {time.perf_counter() - t0:,.1f}s)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Profile raw REES46 event files.")
    parser.add_argument("raw_dir", nargs="?", default="data/raw", help="folder with *.csv / *.csv.gz")
    parser.add_argument("--out", help="write the report to this UTF-8 text file")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        sys.exit(f"Folder not found: {str(raw_dir)!r} - run from the project root, or pass the path.")
    files = sorted(p for p in raw_dir.iterdir() if p.name.endswith((".csv", ".csv.gz")))
    if not files:
        sys.exit(f"No .csv / .csv.gz files found in {str(raw_dir)!r}")

    out_handle = None
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        out_handle = open(args.out, "w", encoding="utf-8")
        sys.stdout = out_handle
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid UnicodeEncodeError on Windows consoles

    con = duckdb.connect()                     # in-memory DB; the data itself stays on disk
    con.sql("SET memory_limit='4GB'")          # keep RAM bounded on a 16 GB laptop
    t_start = time.perf_counter()
    try:
        for f in files:
            print(f"profiling {f.name} ...", file=sys.stderr, flush=True)
            profile_file(con, f)
        print(f"\nTotal time: {time.perf_counter() - t_start:,.1f}s for {len(files)} file(s)")
    finally:
        if out_handle:
            sys.stdout = sys.__stdout__
            out_handle.close()
            print(f"Report written to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
