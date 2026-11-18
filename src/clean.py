"""
clean.py - Day 3: apply the approved cleaning rules (docs/cleaning_rules.md).

What it does
------------
1. Reuses the Day 2 staging copy of the raw CSVs (data/interim/raw_stage.duckdb,
   rebuilt automatically if the CSVs changed). Raw CSVs are only ever read.
2. Builds the cleaned event table. Exact duplicates are merged into one row
   with `event_copies`; every other rule only ADDS flag columns. The original
   values stay alongside the flags.
3. Writes Parquet, one folder per month, to a TEMPORARY folder.
4. Re-reads that Parquet and validates it against the raw data, using counts
   computed independently from the raw rows (see VALIDATIONS in the log).
5. Only if every validation passes does it replace data/processed/events_clean/.
   On any failure it keeps the previous output, saves the failed output in
   events_clean.__failed__ for inspection, writes the log, and exits 1.

Outputs
-------
- data/processed/events_clean/event_month=YYYY-MM/*.parquet   (git-ignored)
- reports/day03_cleaning_log.md                                (commit this)
- reports/day03_cleaning/*.csv                                 (commit this)

Usage (project root, venv active)
---------------------------------
    python -m src.clean
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pandas as pd

from src import config
from src.audit_raw import _display_path, _sql_str, connect, df_to_markdown, stage_raw

OUTPUT_NAME = "events_clean"

# Expected output schema: column -> DuckDB type (as read back from Parquet).
CLEAN_SCHEMA = {
    "event_time": "TIMESTAMP",            # UTC (approved definition)
    "event_type": "VARCHAR",
    "product_id": "BIGINT",
    "category_id": "BIGINT",
    "primary_category_id": "BIGINT",      # C9
    "multi_category_product": "BOOLEAN",  # C9
    "category_code": "VARCHAR",
    "brand_raw": "VARCHAR",               # C8: original value
    "brand": "VARCHAR",                   # C8: after single-brand fill
    "brand_filled": "BOOLEAN",            # C8
    "price": "DOUBLE",
    "is_valid_price": "BOOLEAN",          # C4: price > 0
    "user_id": "BIGINT",
    "user_session": "VARCHAR",
    "session_key": "VARCHAR",             # C7: user_id:user_session
    "has_session": "BOOLEAN",             # C6
    "session_id_shared": "BOOLEAN",       # C7: user_session used by 2+ users
    "session_over_24h": "BOOLEAN",        # C7: per session_key
    "event_copies": "INTEGER",            # C1: identical raw rows merged
    "is_duplicate_merged": "BOOLEAN",     # C1: event_copies > 1
    "source_file": "VARCHAR",
    "event_month": "VARCHAR",             # Parquet partition, 'YYYY-MM' (UTC)
}

# Columns that must never be NULL in the output.
NOT_NULL = ["event_time", "event_type", "product_id", "category_id", "primary_category_id",
            "multi_category_product", "brand_filled", "price", "is_valid_price", "user_id",
            "has_session", "session_id_shared", "session_over_24h", "event_copies",
            "is_duplicate_merged", "source_file", "event_month"]

DEC = "DECIMAL(18,2)"  # exact money arithmetic for revenue checks

# --------------------------------------------------------------------------
# Cleaning SQL: every rule is visible here.
# --------------------------------------------------------------------------
CLEAN_SQL = f"""
WITH
-- C1: merge exact duplicates. Grouping on all raw columns (NULLs group together).
dedup AS (
    SELECT source_file, event_ts, event_type, product_id, category_id, category_code,
           brand, price, user_id, user_session,
           CAST(COUNT(*) AS INTEGER) AS event_copies
    FROM raw_events
    GROUP BY ALL
),
-- C8: a product's brand, only when it has exactly one known brand.
product_brand AS (
    SELECT product_id, MIN(brand) AS fill_brand
    FROM raw_events WHERE brand IS NOT NULL
    GROUP BY product_id HAVING COUNT(DISTINCT brand) = 1
),
-- C9: primary category = most frequent category_id over raw rows; ties -> lowest id.
product_category AS (
    SELECT product_id,
           first(category_id ORDER BY n DESC, category_id ASC) AS primary_category_id,
           COUNT(*) AS n_categories
    FROM (SELECT product_id, category_id, COUNT(*) AS n FROM raw_events GROUP BY 1, 2)
    GROUP BY product_id
),
-- C7: session IDs shared by 2+ users.
shared_session AS (
    SELECT user_session FROM raw_events WHERE user_session IS NOT NULL
    GROUP BY user_session HAVING COUNT(DISTINCT user_id) > 1
),
-- C7: duration per session_key (user_id + user_session).
session_span AS (
    SELECT user_id, user_session,
           date_diff('second', MIN(event_ts), MAX(event_ts)) > 86400 AS over_24h
    FROM raw_events WHERE user_session IS NOT NULL GROUP BY 1, 2
)
SELECT
    d.event_ts                                                AS event_time,
    d.event_type,
    d.product_id,
    d.category_id,
    pc.primary_category_id,
    pc.n_categories > 1                                       AS multi_category_product,
    d.category_code,
    d.brand                                                   AS brand_raw,
    COALESCE(d.brand, pb.fill_brand)                          AS brand,
    (d.brand IS NULL AND pb.fill_brand IS NOT NULL)           AS brand_filled,
    d.price,
    d.price > 0                                               AS is_valid_price,
    d.user_id,
    d.user_session,
    CASE WHEN d.user_session IS NOT NULL
         THEN CAST(d.user_id AS VARCHAR) || ':' || d.user_session END AS session_key,
    d.user_session IS NOT NULL                                AS has_session,
    COALESCE(sh.user_session IS NOT NULL, false)              AS session_id_shared,
    COALESCE(ss.over_24h, false)                              AS session_over_24h,
    d.event_copies,
    d.event_copies > 1                                        AS is_duplicate_merged,
    d.source_file,
    strftime(d.event_ts, '%Y-%m')                             AS event_month
FROM dedup d
LEFT JOIN product_brand    pb USING (product_id)
LEFT JOIN product_category pc USING (product_id)
LEFT JOIN shared_session   sh ON sh.user_session = d.user_session
LEFT JOIN session_span     ss ON ss.user_id = d.user_id AND ss.user_session = d.user_session
ORDER BY event_time, user_id, product_id, event_type
"""


@dataclass
class Check:
    name: str
    expected: object
    actual: object

    @property
    def passed(self) -> bool:
        return self.expected == self.actual


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _safe_rmtree(path: Path, allowed_root: Path) -> None:
    """Delete a directory only if it lies inside allowed_root (never raw data)."""
    path, allowed_root = path.resolve(), allowed_root.resolve()
    raw = config.RAW_DIR.resolve()
    if allowed_root not in path.parents or path == raw or raw in path.parents:
        raise RuntimeError(f"Refusing to delete {path}: outside {allowed_root} or inside raw data")
    if path.exists():
        shutil.rmtree(path)


def _parquet_rel(path: Path) -> str:
    glob = _sql_str((path / "**" / "*.parquet").as_posix())
    return f"read_parquet({glob}, hive_partitioning = true, hive_types = {{'event_month': 'VARCHAR'}})"


def _one(con, sql: str):
    return con.execute(sql).fetchone()[0]


def _to_int(v) -> int:
    return int(v) if v is not None else 0


def _ordered(df: pd.DataFrame) -> pd.DataFrame:
    """Stacked (UNION ALL) tables carry an 'ord' column: sort by it, then drop it."""
    if "ord" in df.columns:
        df = df.sort_values("ord").drop(columns="ord").reset_index(drop=True)
    return df


def _tidy(df: pd.DataFrame) -> pd.DataFrame:
    """Turn float count columns that hold only whole numbers back into integers (SUM returns HUGEINT/float)."""
    df = df.copy()
    for c in df.columns:
        if pd.api.types.is_float_dtype(df[c]) and df[c].dropna().apply(float.is_integer).all() \
                and not any(k in c for k in ("value", "revenue", "price")):
            df[c] = df[c].astype("Int64")
    return df


# --------------------------------------------------------------------------
# Validation: clean Parquet vs raw staging table (independent computations)
# --------------------------------------------------------------------------


def validate(con, clean: str) -> list[Check]:
    checks: list[Check] = []
    add = lambda n, e, a: checks.append(Check(n, e, a))  # noqa: E731

    raw_total = _one(con, "SELECT COUNT(*) FROM raw_events")
    expected_total = sum(config.EXPECTED_ROWS[f] for f in config.RAW_FILES)
    add("raw rows = Day 1 profile total (config.EXPECTED_ROWS)", expected_total, raw_total)
    add("SUM(event_copies) = raw rows", raw_total, _to_int(_one(con, f"SELECT SUM(event_copies) FROM {clean}")))

    # Exact-duplicate extras computed directly from raw (same grouping as the Day 2 audit).
    dup = con.execute("""
        SELECT event_type, SUM(cnt - 1) AS extra
        FROM (SELECT event_type, price, COUNT(*) AS cnt FROM raw_events
              GROUP BY event_time_raw, event_type, product_id, category_id, category_code,
                       brand, price, user_id, user_session)
        GROUP BY event_type
    """).df().set_index("event_type")
    raw_extra = int(dup["extra"].sum())
    add("clean rows = raw rows - exact-duplicate extra rows", raw_total - raw_extra,
        _one(con, f"SELECT COUNT(*) FROM {clean}"))

    raw_by_type = dict(con.execute("SELECT event_type, COUNT(*) FROM raw_events GROUP BY 1").fetchall())
    cl_by_type = {r[0]: (int(r[1]), int(r[2])) for r in con.execute(
        f"SELECT event_type, SUM(event_copies), COUNT(*) FROM {clean} GROUP BY 1").fetchall()}
    for et in config.EVENT_TYPES:
        add(f"{et}: SUM(event_copies) = raw rows", raw_by_type.get(et, 0), cl_by_type.get(et, (0, 0))[0])
        add(f"{et}: clean rows = raw rows - duplicate extras",
            raw_by_type.get(et, 0) - int(dup["extra"].get(et, 0)), cl_by_type.get(et, (0, 0))[1])

    raw_by_file = dict(con.execute("SELECT source_file, COUNT(*) FROM raw_events GROUP BY 1").fetchall())
    cl_by_month = {r[0]: int(r[1]) for r in con.execute(
        f"SELECT event_month, SUM(event_copies) FROM {clean} GROUP BY 1").fetchall()}
    for f in config.RAW_FILES:
        month = config.FILE_MONTH[f][:7]
        add(f"{month}: SUM(event_copies) = raw rows of {f}", raw_by_file.get(f, 0), cl_by_month.get(month, 0))
    add("event_month matches each row's source file month", 0, _one(con, f"""
        SELECT COUNT(*) FROM {clean} c JOIN (VALUES {", ".join(
            f"({_sql_str(f)}, {_sql_str(config.FILE_MONTH[f][:7])})" for f in config.RAW_FILES)}) AS t(source_file, m)
            ON c.source_file = t.source_file
        WHERE c.event_month <> t.m OR strftime(c.event_time, '%Y-%m') <> c.event_month"""))

    # Every raw row preserved: order-independent fingerprint of the 9 raw values, weighted by copies.
    raw_fp = _one(con, """SELECT SUM(CAST(hash(event_ts, event_type, product_id, category_id, category_code,
                                         brand, price, user_id, user_session) AS HUGEINT)) FROM raw_events""")
    cl_fp = _one(con, f"""SELECT SUM(CAST(hash(event_time, event_type, product_id, category_id, category_code,
                                         brand_raw, price, user_id, user_session) AS HUGEINT) * event_copies)
                         FROM {clean}""")
    add("row fingerprint (all raw values x copies) = raw fingerprint", int(raw_fp), int(cl_fp))

    # Money: prices must have <= 2 decimals for exact DECIMAL arithmetic.
    add("prices with more than 2 decimals (raw)", 0, _one(con, "SELECT COUNT(*) FROM raw_events WHERE price <> round(price, 2)"))
    raw_rev = _one(con, f"SELECT SUM(CAST(price AS {DEC})) FROM raw_events WHERE event_type='purchase' AND price > 0")
    cl_rev_weighted = _one(con, f"""SELECT SUM(CAST(price AS {DEC}) * event_copies) FROM {clean}
                                    WHERE event_type='purchase' AND is_valid_price""")
    cl_rev_a1 = _one(con, f"SELECT SUM(CAST(price AS {DEC})) FROM {clean} WHERE event_type='purchase' AND is_valid_price")
    dup_purchase_value = _one(con, f"""
        SELECT COALESCE(SUM((cnt - 1) * CAST(price AS {DEC})), 0)
        FROM (SELECT price, COUNT(*) AS cnt FROM raw_events WHERE event_type = 'purchase' AND price > 0
              GROUP BY event_time_raw, event_type, product_id, category_id, category_code,
                       brand, price, user_id, user_session)""")  # exact Decimal, independent of pandas
    add("revenue: SUM(price x copies) on clean = raw revenue", raw_rev, cl_rev_weighted)
    add("revenue: A1 (merged purchases once) = raw revenue - duplicate purchase value",
        (raw_rev or 0) - dup_purchase_value, cl_rev_a1)

    np_raw = con.execute(f"""SELECT COUNT(*), COALESCE(SUM(CAST(price AS {DEC})), 0) FROM raw_events
                             WHERE event_type='purchase' AND price <= 0""").fetchone()
    np_cl = con.execute(f"""SELECT COALESCE(SUM(event_copies), 0), COALESCE(SUM(CAST(price AS {DEC}) * event_copies), 0)
                            FROM {clean} WHERE event_type='purchase' AND NOT is_valid_price""").fetchone()
    add("non-positive purchase rows (raw count) preserved", int(np_raw[0]), int(np_cl[0]))
    add("non-positive purchase value (raw) preserved", np_raw[1], np_cl[1])
    add("is_valid_price = (price > 0)", 0, _one(con, f"SELECT COUNT(*) FROM {clean} WHERE is_valid_price <> (price > 0)"))
    add("non-positive price rows (raw count, all events)",
        _one(con, "SELECT COUNT(*) FROM raw_events WHERE price <= 0"),
        _to_int(_one(con, f"SELECT SUM(event_copies) FROM {clean} WHERE NOT is_valid_price")))

    # Brand (C8)
    add("brand_raw NULL rows (raw count) = raw brand NULLs",
        _one(con, "SELECT COUNT(*) FROM raw_events WHERE brand IS NULL"),
        _to_int(_one(con, f"SELECT SUM(event_copies) FROM {clean} WHERE brand_raw IS NULL")))
    add("brand_filled rows (raw count) = raw back-fillable NULL rows", _to_int(_one(con, """
        WITH p AS (SELECT product_id, COUNT(*) FILTER (WHERE brand IS NULL) AS nulls,
                          COUNT(DISTINCT brand) AS brands FROM raw_events GROUP BY 1)
        SELECT SUM(nulls) FROM p WHERE brands = 1""")),
        _to_int(_one(con, f"SELECT SUM(event_copies) FROM {clean} WHERE brand_filled")))
    add("brand unchanged wherever brand_raw is present", 0,
        _one(con, f"SELECT COUNT(*) FROM {clean} WHERE brand_raw IS NOT NULL AND brand IS DISTINCT FROM brand_raw"))
    add("brand_filled rows have a brand", 0, _one(con, f"SELECT COUNT(*) FROM {clean} WHERE brand_filled AND brand IS NULL"))

    # Session (C6, C7)
    add("has_session = false rows (raw count) = raw NULL sessions",
        _one(con, "SELECT COUNT(*) FROM raw_events WHERE user_session IS NULL"),
        _to_int(_one(con, f"SELECT SUM(event_copies) FROM {clean} WHERE NOT has_session")))
    add("session_key NULL exactly when user_session NULL", 0, _one(con, f"""
        SELECT COUNT(*) FROM {clean} WHERE (session_key IS NULL) <> (user_session IS NULL)
            OR (session_key IS NOT NULL AND session_key <> CAST(user_id AS VARCHAR) || ':' || user_session)"""))
    add("shared session IDs flagged = raw sessions with 2+ users",
        _one(con, """SELECT COUNT(*) FROM (SELECT user_session FROM raw_events WHERE user_session IS NOT NULL
                     GROUP BY 1 HAVING COUNT(DISTINCT user_id) > 1)"""),
        _one(con, f"SELECT COUNT(DISTINCT user_session) FROM {clean} WHERE session_id_shared"))

    # Category (C9)
    add("multi-category products = raw products in 2+ category_ids",
        _one(con, "SELECT COUNT(*) FROM (SELECT product_id FROM raw_events GROUP BY 1 HAVING COUNT(DISTINCT category_id) > 1)"),
        _one(con, f"SELECT COUNT(DISTINCT product_id) FROM {clean} WHERE multi_category_product"))
    add("primary_category_id is one of the product's category_ids", 0, _one(con, f"""
        SELECT COUNT(*) FROM (SELECT DISTINCT product_id, primary_category_id FROM {clean}) p
        WHERE NOT EXISTS (SELECT 1 FROM raw_events r WHERE r.product_id = p.product_id
                          AND r.category_id = p.primary_category_id)"""))
    add("one primary_category_id per product", 0, _one(con, f"""
        SELECT COUNT(*) FROM (SELECT product_id FROM {clean} GROUP BY 1 HAVING COUNT(DISTINCT primary_category_id) > 1)"""))

    # Schema + required non-null columns
    schema = {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {clean}").fetchall()}
    add("output schema (columns and types)", CLEAN_SCHEMA, schema)
    nulls_expr = " + ".join(f"COUNT(*) FILTER (WHERE {c} IS NULL)" for c in NOT_NULL)
    add("NULLs in required columns", 0, _one(con, f"SELECT {nulls_expr} FROM {clean}"))
    add("event_copies >= 1 and is_duplicate_merged = (event_copies > 1)", 0, _one(con, f"""
        SELECT COUNT(*) FROM {clean} WHERE event_copies < 1 OR is_duplicate_merged <> (event_copies > 1)"""))
    return checks


# --------------------------------------------------------------------------
# Results for the cleaning log (reported, not pass/fail)
# --------------------------------------------------------------------------


def results(con, clean: str) -> list[tuple[str, pd.DataFrame, str]]:
    out = []

    counts = con.execute(f"""
        SELECT event_month AS month_utc, event_type,
               SUM(event_copies) AS raw_events, COUNT(*) AS dedup_events,
               SUM(event_copies) - COUNT(*) AS merged_extra_rows
        FROM {clean} GROUP BY 1, 2
        ORDER BY 1, CASE event_type WHEN 'view' THEN 1 WHEN 'cart' THEN 2
                                    WHEN 'remove_from_cart' THEN 3 ELSE 4 END
    """).df()
    tot = counts.groupby("event_type", sort=False)[["raw_events", "dedup_events", "merged_extra_rows"]].sum().reset_index()
    tot.insert(0, "month_utc", "ALL")
    out.append(("event_counts", pd.concat([counts, tot], ignore_index=True),
                "Event counts: raw (SUM(event_copies)) and deduplicated (COUNT(*)), per month (UTC) and type"))

    out.append(("c1_duplicates", con.execute(f"""
        SELECT event_type,
               COUNT(*) FILTER (WHERE is_duplicate_merged)                         AS merged_rows,
               SUM(event_copies - 1)                                               AS extra_raw_rows_merged,
               MAX(event_copies)                                                   AS max_copies,
               SUM(CAST(price AS {DEC}) * (event_copies - 1)) FILTER (WHERE is_valid_price) AS extra_rows_valid_price_value
        FROM {clean} GROUP BY 1 ORDER BY 3 DESC
    """).df(), "C1: exact duplicates merged (value = positive prices of the extra copies)"))

    out.append(("revenue_reconciliation", con.execute(f"""
        WITH p AS (SELECT * FROM {clean} WHERE event_type = 'purchase')
        SELECT 1 AS ord, 'Raw: purchase rows with price > 0 (before merging)' AS line,
               SUM(event_copies) FILTER (WHERE is_valid_price) AS purchase_rows,
               SUM(CAST(price AS {DEC}) * event_copies) FILTER (WHERE is_valid_price) AS value
        FROM p
        UNION ALL
        SELECT 2, 'Less: duplicate purchase copies merged (A1, reported separately)',
               SUM(event_copies - 1) FILTER (WHERE is_valid_price),
               SUM(CAST(price AS {DEC}) * (event_copies - 1)) FILTER (WHERE is_valid_price) FROM p
        UNION ALL
        SELECT 3, '= Estimated purchase revenue (A1)',
               COUNT(*) FILTER (WHERE is_valid_price),
               SUM(CAST(price AS {DEC})) FILTER (WHERE is_valid_price) FROM p
        UNION ALL
        SELECT 4, 'Excluded separately: non-positive price purchases (raw rows, C4)',
               COALESCE(SUM(event_copies) FILTER (WHERE NOT is_valid_price), 0),
               COALESCE(SUM(CAST(price AS {DEC}) * event_copies) FILTER (WHERE NOT is_valid_price), 0) FROM p
    """).df(), "Revenue reconciliation (approved definitions; currency as in source data)"))

    out.append(("revenue_by_month", con.execute(f"""
        SELECT event_month AS month_utc,
               COUNT(*) FILTER (WHERE is_valid_price)                         AS valid_purchase_events,
               SUM(CAST(price AS {DEC})) FILTER (WHERE is_valid_price)        AS est_revenue_a1,
               SUM(event_copies - 1) FILTER (WHERE is_valid_price)            AS dup_purchase_rows_excluded,
               COALESCE(SUM(CAST(price AS {DEC}) * (event_copies - 1)) FILTER (WHERE is_valid_price), 0) AS dup_value_excluded,
               COALESCE(SUM(event_copies) FILTER (WHERE NOT is_valid_price), 0) AS non_positive_rows,
               COALESCE(SUM(CAST(price AS {DEC}) * event_copies) FILTER (WHERE NOT is_valid_price), 0) AS non_positive_value
        FROM {clean} WHERE event_type = 'purchase' GROUP BY 1 ORDER BY 1
    """).df(), "Revenue by event month (UTC), A1 definition"))

    out.append(("a2_purchasing_sessions", con.execute(f"""
        SELECT 1 AS ord, 'Raw: distinct user_session with >= 1 purchase (audit definition)' AS step,
               COUNT(DISTINCT user_session) FILTER (WHERE event_type = 'purchase') AS sessions FROM {clean}
        UNION ALL
        SELECT 2, 'Keyed by user_id + user_session, >= 1 purchase (C7 split)',
               COUNT(DISTINCT session_key) FILTER (WHERE event_type = 'purchase') FROM {clean}
        UNION ALL
        SELECT 3, 'A2: keyed, >= 1 VALID purchase (price > 0)',
               COUNT(DISTINCT session_key) FILTER (WHERE event_type = 'purchase' AND is_valid_price) FROM {clean}
    """).df(), "A2 purchasing sessions: approximates orders; NOT an exact order count (no order ID in the data)"))

    out.append(("a2_by_month", con.execute(f"""
        WITH s AS (SELECT session_key, MIN(event_time) AS first_valid_purchase
                   FROM {clean} WHERE event_type = 'purchase' AND is_valid_price GROUP BY 1)
        SELECT strftime(first_valid_purchase, '%Y-%m') AS month_utc, COUNT(*) AS purchasing_sessions
        FROM s GROUP BY 1 ORDER BY 1
    """).df(), "A2 purchasing sessions by month of the session's first valid purchase (UTC)"))

    out.append(("c3_repeat_purchases", con.execute(f"""
        WITH sp AS (SELECT session_key, product_id, COUNT(*) AS n,
                           SUM(CAST(price AS {DEC})) AS value
                    FROM {clean} WHERE event_type = 'purchase' AND is_valid_price AND has_session
                    GROUP BY 1, 2)
        SELECT COUNT(*)                          AS purchased_session_product_pairs,
               COUNT(*) FILTER (WHERE n > 1)     AS pairs_with_2plus_purchase_events,
               SUM(n - 1) FILTER (WHERE n > 1)   AS extra_purchase_events,
               SUM(value) FILTER (WHERE n > 1)   AS total_value_of_those_pairs
        FROM sp
    """).df(), "C3: same product purchased in 2+ separate events in one session (kept; valid prices, after merging)"))

    out.append(("flags_summary", con.execute(f"""
        SELECT 1 AS ord, 'C4 is_valid_price = false' AS flag, SUM(event_copies) FILTER (WHERE NOT is_valid_price) AS raw_rows,
               COUNT(*) FILTER (WHERE NOT is_valid_price) AS dedup_rows FROM {clean}
        UNION ALL SELECT 2, 'C6 has_session = false', SUM(event_copies) FILTER (WHERE NOT has_session),
               COUNT(*) FILTER (WHERE NOT has_session) FROM {clean}
        UNION ALL SELECT 3, 'C7 session_id_shared', SUM(event_copies) FILTER (WHERE session_id_shared),
               COUNT(*) FILTER (WHERE session_id_shared) FROM {clean}
        UNION ALL SELECT 4, 'C7 session_over_24h', SUM(event_copies) FILTER (WHERE session_over_24h),
               COUNT(*) FILTER (WHERE session_over_24h) FROM {clean}
        UNION ALL SELECT 5, 'C8 brand_filled', SUM(event_copies) FILTER (WHERE brand_filled),
               COUNT(*) FILTER (WHERE brand_filled) FROM {clean}
        UNION ALL SELECT 6, 'C8 brand still NULL', SUM(event_copies) FILTER (WHERE brand IS NULL),
               COUNT(*) FILTER (WHERE brand IS NULL) FROM {clean}
        UNION ALL SELECT 7, 'C9 multi_category_product', SUM(event_copies) FILTER (WHERE multi_category_product),
               COUNT(*) FILTER (WHERE multi_category_product) FROM {clean}
        UNION ALL SELECT 8, 'C9 category_id <> primary_category_id', SUM(event_copies) FILTER (WHERE category_id <> primary_category_id),
               COUNT(*) FILTER (WHERE category_id <> primary_category_id) FROM {clean}
    """).df(), "Flag columns: rows affected (raw count and deduplicated count)"))

    out.append(("entities", con.execute(f"""
        SELECT COUNT(DISTINCT session_key)                                                AS session_keys,
               COUNT(DISTINCT session_key) FILTER (WHERE session_over_24h)                AS session_keys_over_24h,
               COUNT(DISTINCT user_session) FILTER (WHERE session_id_shared)              AS shared_session_ids,
               COUNT(DISTINCT product_id) FILTER (WHERE brand_filled)                     AS products_brand_filled,
               COUNT(DISTINCT product_id) FILTER (WHERE multi_category_product)           AS multi_category_products,
               COUNT(DISTINCT product_id) FILTER (WHERE NOT is_valid_price)               AS products_with_nonpositive_price
        FROM {clean}
    """).df(), "Entities affected by C7, C8, C9 and C4"))
    return out


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_clean(raw_dir: Path, db_path: Path, out_root: Path, log_md: Path, csv_dir: Path) -> bool:
    """Run cleaning + validation. Returns True on success; output replaced only on success."""
    t0 = time.perf_counter()
    out_root.mkdir(parents=True, exist_ok=True)
    final, tmp, failed = (out_root / OUTPUT_NAME, out_root / f"{OUTPUT_NAME}.__tmp__",
                          out_root / f"{OUTPUT_NAME}.__failed__")
    csv_dir.mkdir(parents=True, exist_ok=True)
    log_md.parent.mkdir(parents=True, exist_ok=True)

    con = connect(db_path)
    try:
        print("checking staging copy of raw CSVs ...", file=sys.stderr, flush=True)
        built = stage_raw(con, raw_dir)
        bad_ts = _one(con, "SELECT COUNT(*) FROM raw_events WHERE event_ts IS NULL")
        if bad_ts:
            raise RuntimeError(f"{bad_ts} raw rows have no parsable timestamp; cleaning rules assume 0 (C5). Stopping.")

        _safe_rmtree(tmp, out_root)
        print("writing cleaned Parquet to a temporary folder ...", file=sys.stderr, flush=True)
        t = time.perf_counter()
        con.execute(f"COPY ({CLEAN_SQL}) TO {_sql_str(tmp.as_posix())} "
                    f"(FORMAT parquet, PARTITION_BY (event_month), COMPRESSION zstd)")
        write_s = time.perf_counter() - t

        clean = _parquet_rel(tmp)
        print("validating output against raw data ...", file=sys.stderr, flush=True)
        t = time.perf_counter()
        checks = validate(con, clean)
        validate_s = time.perf_counter() - t
        ok = all(c.passed for c in checks)

        res = [(k, _tidy(_ordered(df)), t) for k, df, t in results(con, clean)] if ok else []
        files = sorted(tmp.rglob("*.parquet"))
        size_mb = sum(f.stat().st_size for f in files) / 1e6

        if ok:
            old = out_root / f"{OUTPUT_NAME}.__old__"
            _safe_rmtree(old, out_root)
            if final.exists():
                final.rename(old)
            tmp.rename(final)
            _safe_rmtree(old, out_root)
            _safe_rmtree(failed, out_root)
            location = final
        else:
            _safe_rmtree(failed, out_root)
            tmp.rename(failed)
            location = failed

        for i, (key, df, _) in enumerate(res, start=1):
            df.to_csv(csv_dir / f"{i:02d}_{key}.csv", index=False)
        pd.DataFrame([{"validation": c.name, "expected": str(c.expected), "actual": str(c.actual),
                       "result": "PASS" if c.passed else "FAIL"} for c in checks]).to_csv(
            csv_dir / "00_validations.csv", index=False)

        write_log(log_md, ok, checks, res, location, files, size_mb, built,
                  {"write": write_s, "validate": validate_s, "total": time.perf_counter() - t0})
        status = "PASSED" if ok else "FAILED"
        print(f"validation {status}: {sum(c.passed for c in checks)}/{len(checks)} checks. Log: {log_md}",
              file=sys.stderr)
        return ok
    finally:
        con.close()


def write_log(log_md: Path, ok: bool, checks: list[Check], res, location: Path, files, size_mb: float,
              built: bool, secs: dict) -> None:
    n_pass = sum(c.passed for c in checks)
    lines = [
        "# Day 3 Cleaning Log",
        "",
        "Generated by `python -m src.clean`. Rules: `docs/cleaning_rules.md` (approved 2026-11-18). "
        "Raw CSVs were only read, never modified.",
        "",
        f"**Validation: {'PASSED' if ok else 'FAILED'}** ({n_pass}/{len(checks)} checks). "
        + ("Output accepted." if ok else
           "**Output NOT accepted**; previous output (if any) left unchanged. Failed output kept for inspection."),
        "",
        f"- Output: `{_display_path(location)}` · {len(files)} Parquet file(s) · {size_mb:,.1f} MB (zstd)",
        f"- Staging copy: {'rebuilt' if built else 'reused (CSVs unchanged since staging)'} · DuckDB {duckdb.__version__}",
        f"- Timings: write {secs['write']:,.1f}s · validate {secs['validate']:,.1f}s · total {secs['total']:,.1f}s",
        "- All timestamps UTC. Money in the source data's currency. Revenue checks use exact DECIMAL(18,2) arithmetic.",
        "- CSV copies of every table: `reports/day03_cleaning/`.",
        "",
        "## Validations",
        "",
        df_to_markdown(pd.DataFrame([{
            "#": i, "check": c.name, "result": "PASS" if c.passed else "**FAIL**",
            "expected": "(see CSV)" if isinstance(c.expected, dict) else str(c.expected),
            "actual": ("(matches)" if c.passed else "(differs; see CSV)") if isinstance(c.actual, dict) else str(c.actual),
        } for i, c in enumerate(checks, start=1)])),
    ]
    if ok:
        for i, (_, df, title) in enumerate(res, start=1):
            lines += ["", f"## {i}. {title}", "", df_to_markdown(df)]
    else:
        lines += ["", "Results are not reported because validation failed. Fix the failing checks first."]
    log_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Apply approved cleaning rules and validate the output.")
    p.add_argument("--raw-dir", default=str(config.RAW_DIR))
    p.add_argument("--db", default=str(config.INTERIM_DIR / "raw_stage.duckdb"))
    p.add_argument("--out-root", default=str(config.PROCESSED_DIR))
    p.add_argument("--log", default=str(config.REPORTS_DIR / "day03_cleaning_log.md"))
    p.add_argument("--csv-dir", default=str(config.REPORTS_DIR / "day03_cleaning"))
    a = p.parse_args(argv)
    if not Path(a.raw_dir).is_dir():
        sys.exit(f"Raw folder not found: {a.raw_dir}. Run from the project root.")
    ok = run_clean(Path(a.raw_dir), Path(a.db), Path(a.out_root), Path(a.log), Path(a.csv_dir))
    if not ok:
        sys.exit("Cleaning validation FAILED - output not accepted. See the log; do not proceed.")


if __name__ == "__main__":
    main()
