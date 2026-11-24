"""
audit_raw.py - Day 2: measure every data-quality issue in the raw events. READ-ONLY.

Why this module exists
----------------------
Day 1 profiling told us *what kinds* of problems exist (DQ-01..DQ-14 in
docs/data_quality_notes.md). Before any cleaning rule is proposed we need
*exact* numbers for each one: how many rows, which event types, how much
revenue is affected. Cleaning rules will then be justified by these numbers.

What it does
------------
1. Stages the four raw CSVs into a local DuckDB file
   (data/interim/raw_stage.duckdb). This is a typed, value-for-value copy:
   no row is dropped and no value is changed. It exists only so the ~12
   checks below don't each re-parse 2 GB of CSV. The raw CSVs are never
   written to.
2. Runs the audit checks, each tied to a DQ issue.
3. Writes a Markdown report (default reports/day02_audit_report.md) and one
   CSV per table (reports/day02_audit/), so every number is traceable.

Usage (from the project root, venv active)
------------------------------------------
    python -m src.audit_raw
    python -m src.audit_raw --rebuild           # force re-staging of the CSVs
    python -m src.audit_raw --raw-dir data/raw --out reports/day02_audit_report.md
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from src import config

# --------------------------------------------------------------------------
# Result container + Markdown rendering
# --------------------------------------------------------------------------


@dataclass
class Section:
    """One audit check: a title, the DQ issues it addresses, and its tables."""

    key: str                      # short id, used for CSV file names
    title: str
    dq_refs: str                  # e.g. "DQ-03, DQ-11"
    tables: list[tuple[str, pd.DataFrame]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _fmt(v, col: str = "") -> str:
    """Format one cell for Markdown: thousands separators, trimmed floats.

    Identifier columns (*_id) are printed without separators.
    """
    if col.endswith("_id") and v is not None and not (isinstance(v, float) and pd.isna(v)):
        return str(v)
    if v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int) or (hasattr(v, "dtype") and "int" in str(getattr(v, "dtype", ""))):
        return f"{int(v):,}"
    if isinstance(v, float):
        if v.is_integer() and abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:,.4f}".rstrip("0").rstrip(".") if abs(v) < 1000 else f"{v:,.2f}"
    return str(v).replace("|", "\\|")


def df_to_markdown(df: pd.DataFrame) -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table (no extra deps)."""
    if df.empty:
        return "_(no rows)_"
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(v, c) for v, c in zip(row, cols)) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Staging (typed copy of raw CSVs; values unchanged)
# --------------------------------------------------------------------------


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def connect(db_path: Path) -> duckdb.DuckDBPyConnection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(f"SET memory_limit = {_sql_str(config.DUCKDB_MEMORY_LIMIT)}")
    con.execute(f"SET temp_directory = {_sql_str((db_path.parent / 'duckdb_tmp').as_posix())}")
    con.execute("SET preserve_insertion_order = false")  # lower memory for big GROUP BYs
    return con


def _file_fingerprint(raw_dir: Path) -> pd.DataFrame:
    rows = []
    for name in config.RAW_FILES:
        p = raw_dir / name
        if not p.is_file():
            raise FileNotFoundError(f"Expected raw file missing: {p}")
        st = p.stat()
        rows.append({"source_file": name, "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns})
    return pd.DataFrame(rows)


def stage_raw(con: duckdb.DuckDBPyConnection, raw_dir: Path, rebuild: bool = False) -> bool:
    """Create table raw_events from the CSVs unless an up-to-date copy exists.

    Returns True if the table was (re)built. Columns: source_file,
    event_time_raw (original text), event_ts (strictly parsed, NULL if the
    text doesn't match the expected format), then the 8 other raw columns.
    """
    fp = _file_fingerprint(raw_dir)
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    if not rebuild and {"raw_events", "stage_meta"} <= tables:
        old = con.execute("SELECT * FROM stage_meta ORDER BY source_file").df()
        new = fp.sort_values("source_file").reset_index(drop=True)
        if old.reset_index(drop=True).equals(new):
            return False  # cache is current

    files_sql = "[" + ", ".join(_sql_str((raw_dir / f).as_posix()) for f in config.RAW_FILES) + "]"
    cols_sql = "{" + ", ".join(f"{_sql_str(k)}: {_sql_str(v)}" for k, v in config.RAW_COLUMNS.items()) + "}"
    con.execute(f"""
        CREATE OR REPLACE TABLE raw_events AS
        SELECT
            parse_filename(filename)                                  AS source_file,
            event_time                                                AS event_time_raw,
            try_strptime(event_time, {_sql_str(config.EVENT_TIME_FORMAT)}) AS event_ts,
            event_type, product_id, category_id, category_code, brand,
            price, user_id, user_session
        FROM read_csv({files_sql}, columns = {cols_sql}, header = true, filename = true)
    """)
    con.register("fp_df", fp)
    con.execute("CREATE OR REPLACE TABLE stage_meta AS SELECT * FROM fp_df")
    con.unregister("fp_df")
    return True


def _create_file_months(con: duckdb.DuckDBPyConnection) -> None:
    rows = ", ".join(
        f"({_sql_str(f)}, DATE {_sql_str(config.FILE_MONTH[f])}, {config.EXPECTED_ROWS[f]}, {i})"
        for i, f in enumerate(config.RAW_FILES)
    )
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE file_months AS
        SELECT * FROM (VALUES {rows}) t(source_file, month_start, expected_rows, file_order)
    """)


# --------------------------------------------------------------------------
# Audit checks. Each returns a Section. SQL only reads raw_events.
# --------------------------------------------------------------------------


def check_load(con) -> Section:
    s = Section("01_load", "Load integrity: row counts, timestamp format, month boundaries, event labels",
                "DQ-14, DQ-05, Day 1 profile re-check")
    df = con.execute("""
        SELECT r.source_file,
               COUNT(*)                                                           AS rows_loaded,
               ANY_VALUE(m.expected_rows)                                         AS rows_in_day1_profile,
               COUNT(*) = ANY_VALUE(m.expected_rows)                              AS rows_match,
               COUNT(*) FILTER (WHERE r.event_time_raw IS NULL)                   AS event_time_null,
               COUNT(*) FILTER (WHERE r.event_ts IS NULL AND r.event_time_raw IS NOT NULL) AS event_time_bad_format,
               COUNT(*) FILTER (WHERE r.event_ts <  m.month_start
                                   OR r.event_ts >= m.month_start + INTERVAL 1 MONTH) AS outside_file_month,
               MIN(r.event_ts) AS first_event_utc,
               MAX(r.event_ts) AS last_event_utc
        FROM raw_events r JOIN file_months m USING (source_file)
        GROUP BY r.source_file
        ORDER BY ANY_VALUE(m.file_order)
    """).df()
    total = pd.DataFrame([{
        "source_file": "TOTAL", "rows_loaded": int(df.rows_loaded.sum()),
        "rows_in_day1_profile": int(df.rows_in_day1_profile.sum()),
        "rows_match": bool(df.rows_match.all()),
        "event_time_null": int(df.event_time_null.sum()),
        "event_time_bad_format": int(df.event_time_bad_format.sum()),
        "outside_file_month": int(df.outside_file_month.sum()),
        "first_event_utc": df.first_event_utc.min(), "last_event_utc": df.last_event_utc.max()}])
    s.tables.append(("Rows and timestamps per file", pd.concat([df, total], ignore_index=True)))

    ev = con.execute("""
        SELECT COALESCE(event_type, '<NULL>') AS event_type, COUNT(*) AS rows,
               event_type IN ('view','cart','remove_from_cart','purchase') AS expected_label
        FROM raw_events GROUP BY 1, 3 ORDER BY rows DESC
    """).df()
    s.tables.append(("Event-type labels (all files)", ev))
    return s


def check_nulls(con) -> Section:
    s = Section("02_nulls", "Exact null and empty-string counts per column", "DQ-01, DQ-02, DQ-04, DQ-05")
    cols = list(config.RAW_COLUMNS)
    staged = {"event_time": "event_time_raw"}  # staged name of the original text column
    null_exprs = ", ".join(f"COUNT(*) - COUNT(r.{staged.get(c, c)}) AS {c}" for c in cols)
    by_file = con.execute(f"""
        SELECT source_file, COUNT(*) AS total_rows, {null_exprs}
        FROM raw_events r JOIN file_months m USING (source_file)
        GROUP BY source_file ORDER BY ANY_VALUE(m.file_order)
    """).df()
    long = by_file.melt(id_vars=["source_file", "total_rows"], var_name="column", value_name="nulls")
    piv = long.pivot(index="column", columns="source_file", values="nulls")[config.RAW_FILES]
    piv["total_nulls"] = piv.sum(axis=1)
    grand_total = int(by_file.total_rows.sum())
    piv["null_pct"] = (100 * piv["total_nulls"] / grand_total).round(4)
    piv = piv.reindex(cols).reset_index()
    s.tables.append(("Null counts (exact)", piv))

    empty = con.execute("""
        SELECT
          COUNT(*) FILTER (WHERE trim(event_type)    = '') AS event_type_empty,
          COUNT(*) FILTER (WHERE trim(category_code) = '') AS category_code_empty,
          COUNT(*) FILTER (WHERE trim(brand)         = '') AS brand_empty,
          COUNT(*) FILTER (WHERE trim(user_session)  = '') AS user_session_empty,
          COUNT(*) FILTER (WHERE user_session IS NOT NULL
                           AND NOT regexp_full_match(user_session,
                           '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')) AS user_session_not_uuid
        FROM raw_events
    """).df()
    s.tables.append(("Empty strings and malformed session IDs (would hide as non-null)", empty))
    s.notes.append(f"null_pct is relative to all {grand_total:,} rows.")
    return s


def check_prices(con) -> Section:
    s = Section("03_prices", "Prices: negative, zero, distribution", "DQ-03, DQ-11")
    by_type = con.execute("""
        SELECT event_type,
               COUNT(*)                                        AS rows,
               COUNT(*) FILTER (WHERE price < 0)               AS price_negative_rows,
               ROUND(SUM(price) FILTER (WHERE price < 0), 2)   AS price_negative_sum,
               COUNT(*) FILTER (WHERE price = 0)               AS price_zero_rows,
               COUNT(*) FILTER (WHERE price > 0)               AS price_positive_rows,
               ROUND(SUM(price) FILTER (WHERE price > 0), 2)   AS price_positive_sum
        FROM raw_events GROUP BY event_type ORDER BY rows DESC
    """).df()
    s.tables.append(("Price sign by event type (all months)", by_type))

    purch = con.execute("""
        SELECT r.source_file,
               COUNT(*)                                         AS purchase_rows,
               COUNT(*) FILTER (WHERE price > 0)                AS valid_rows,
               ROUND(SUM(price) FILTER (WHERE price > 0), 2)    AS est_revenue_raw,
               COUNT(*) FILTER (WHERE price <= 0)               AS non_positive_rows,
               ROUND(COALESCE(SUM(price) FILTER (WHERE price <= 0), 0), 2) AS non_positive_value
        FROM raw_events r JOIN file_months m USING (source_file)
        WHERE event_type = 'purchase'
        GROUP BY r.source_file ORDER BY ANY_VALUE(m.file_order)
    """).df()
    s.tables.append(("Purchase rows by month: estimated revenue BEFORE de-duplication "
                     "(approved definition: price > 0; non-positive reported separately)", purch))

    neg_products = con.execute("""
        WITH neg AS (SELECT DISTINCT product_id FROM raw_events WHERE price < 0)
        SELECT r.product_id,
               COUNT(*)                                   AS rows,
               COUNT(*) FILTER (WHERE r.price < 0)        AS negative_rows,
               COUNT(*) FILTER (WHERE r.price >= 0)       AS non_negative_rows,
               MIN(r.price) AS min_price, MAX(r.price) AS max_price,
               COUNT(DISTINCT r.price)                    AS distinct_prices,
               string_agg(DISTINCT r.event_type, ', ' ORDER BY r.event_type) AS event_types,
               MIN(r.brand) AS brand_first_alpha,
               COUNT(DISTINCT r.category_id)              AS category_ids
        FROM raw_events r JOIN neg USING (product_id)
        GROUP BY r.product_id ORDER BY negative_rows DESC, r.product_id LIMIT 25
    """).df()
    n_neg_products = con.execute("SELECT COUNT(DISTINCT product_id) FROM raw_events WHERE price < 0").fetchone()[0]
    s.tables.append((f"Products with any negative price: {n_neg_products:,} total (top 25 by negative rows)",
                     neg_products))

    qs = [0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 0.999, 1.0]
    q_cols = ", ".join(f'q[{i + 1}] AS "p{q * 100:g}"' for i, q in enumerate(qs))  # p0, p1, ..., "p99.9", p100
    q_list = "[" + ", ".join(str(q) for q in qs) + "]"
    dist = con.execute(f"""
        SELECT scope, n, {q_cols} FROM (
          SELECT 'all rows, price > 0' AS scope, COUNT(*) AS n, quantile_disc(price, {q_list}) AS q
          FROM raw_events WHERE price > 0
          UNION ALL
          SELECT 'purchase rows, price > 0', COUNT(*), quantile_disc(price, {q_list})
          FROM raw_events WHERE price > 0 AND event_type = 'purchase')
        ORDER BY scope
    """).df()
    s.tables.append(("Price percentiles (exact, quantile_disc)", dist))
    return s


def check_price_variation(con) -> Section:
    s = Section("04_price_variation", "Per-product price variation (feasibility of upgrade 1B)", "DQ-11, upgrade 1B")
    df = con.execute("""
        WITH p AS (
            SELECT product_id,
                   COUNT(DISTINCT price)                            AS n_prices,
                   MIN(price) AS min_p, MAX(price) AS max_p,
                   COUNT(*) FILTER (WHERE event_type = 'view')      AS views,
                   COUNT(*) FILTER (WHERE event_type = 'purchase')  AS purchases
            FROM raw_events WHERE price > 0 GROUP BY product_id)
        SELECT CASE WHEN n_prices = 1 THEN '1 price'
                    WHEN n_prices = 2 THEN '2 prices'
                    WHEN n_prices <= 5 THEN '3-5 prices'
                    ELSE '6+ prices' END                AS price_points,
               COUNT(*)                                  AS products,
               SUM(views)                                AS views,
               SUM(purchases)                            AS purchases,
               ROUND(median(max_p / min_p), 3)           AS median_max_to_min_ratio
        FROM p GROUP BY 1 ORDER BY MIN(n_prices)
    """).df()
    s.tables.append(("Products by number of distinct positive prices (all months)", df))
    s.notes.append("A product with 2+ price points is a candidate for before/after price-change analysis. "
                   "Whether enough traffic sits on both sides of each change is a Day 6 question.")
    return s


def check_duplicates(con) -> Section:
    s = Section("05_duplicates", "Exact and near-duplicate events", "DQ-06, DQ-08")
    exact = con.execute("""
        WITH g AS (
            SELECT event_type, price, COUNT(*) AS cnt
            FROM raw_events
            GROUP BY event_time_raw, event_type, product_id, category_id, category_code,
                     brand, price, user_id, user_session
            HAVING COUNT(*) > 1)
        SELECT event_type,
               COUNT(*)                         AS duplicate_groups,
               SUM(cnt - 1)                     AS extra_rows,
               MAX(cnt)                         AS max_copies,
               ROUND(SUM((cnt - 1) * price) FILTER (WHERE price > 0), 2) AS extra_rows_positive_price_sum
        FROM g GROUP BY event_type ORDER BY extra_rows DESC
    """).df()
    s.tables.append(("Exact duplicates (all 9 raw columns identical; NULLs compared as equal)", exact))

    copies = con.execute("""
        WITH g AS (SELECT COUNT(*) AS cnt FROM raw_events
                   GROUP BY event_time_raw, event_type, product_id, category_id, category_code,
                            brand, price, user_id, user_session HAVING COUNT(*) > 1)
        SELECT CASE WHEN cnt = 2 THEN '2' WHEN cnt = 3 THEN '3' WHEN cnt <= 10 THEN '4-10' ELSE '11+' END AS copies,
               COUNT(*) AS groups, SUM(cnt - 1) AS extra_rows
        FROM g GROUP BY 1 ORDER BY MIN(cnt)
    """).df()
    s.tables.append(("Exact duplicates: copies per group", copies))

    near = con.execute("""
        WITH k AS (
            SELECT event_type,
                   COUNT(*) AS cnt,
                   COUNT(DISTINCT (category_id, category_code, brand, price)) AS variants
            FROM raw_events
            GROUP BY event_time_raw, event_type, product_id, user_id, user_session
            HAVING COUNT(*) > 1)
        SELECT event_type,
               COUNT(*) FILTER (WHERE variants > 1) AS key_groups_with_differing_attributes,
               SUM(cnt) FILTER (WHERE variants > 1) AS rows_in_those_groups
        FROM k GROUP BY event_type ORDER BY event_type
    """).df()
    s.tables.append(("Near-duplicates: same second, user, session, product and event type "
                     "but different price/brand/category", near))

    repeat = con.execute("""
        WITH d AS (SELECT DISTINCT event_time_raw, product_id, user_session
                   FROM raw_events WHERE event_type = 'purchase' AND user_session IS NOT NULL),
             sp AS (SELECT user_session, product_id, COUNT(*) AS n FROM d GROUP BY 1, 2)
        SELECT COUNT(*)                         AS session_product_pairs_purchased,
               COUNT(*) FILTER (WHERE n > 1)    AS pairs_purchased_at_2plus_distinct_times,
               SUM(n - 1) FILTER (WHERE n > 1)  AS extra_purchase_rows_in_those_pairs
        FROM sp
    """).df()
    s.tables.append(("Same product purchased at 2+ different timestamps in one session "
                     "(quantity or repeat? ambiguous without quantity column)", repeat))
    return s


def check_funnel_consistency(con) -> Section:
    s = Section("06_funnel_consistency", "Funnel consistency: stage reached without the previous stage", "DQ-07")
    sess = con.execute("""
        WITH s AS (
            SELECT user_session,
                   bool_or(event_type = 'view')             AS v,
                   bool_or(event_type = 'cart')             AS c,
                   bool_or(event_type = 'remove_from_cart') AS r,
                   bool_or(event_type = 'purchase')         AS p
            FROM raw_events WHERE user_session IS NOT NULL GROUP BY user_session)
        SELECT COUNT(*)                                  AS sessions,
               COUNT(*) FILTER (WHERE v)                 AS with_view,
               COUNT(*) FILTER (WHERE c)                 AS with_cart,
               COUNT(*) FILTER (WHERE p)                 AS with_purchase,
               COUNT(*) FILTER (WHERE c AND NOT v)       AS cart_without_view,
               COUNT(*) FILTER (WHERE p AND NOT c)       AS purchase_without_cart,
               COUNT(*) FILTER (WHERE p AND NOT v)       AS purchase_without_view,
               COUNT(*) FILTER (WHERE r AND NOT c)       AS remove_without_cart
        FROM s
    """).df()
    s.tables.append(("Session level (sessions with non-null user_session)", sess))

    prod = con.execute("""
        WITH sp AS (
            SELECT user_session, product_id,
                   bool_or(event_type = 'view')             AS v,
                   bool_or(event_type = 'cart')             AS c,
                   bool_or(event_type = 'remove_from_cart') AS r,
                   bool_or(event_type = 'purchase')         AS p
            FROM raw_events WHERE user_session IS NOT NULL GROUP BY user_session, product_id)
        SELECT COUNT(*)                                  AS session_product_pairs,
               COUNT(*) FILTER (WHERE c)                 AS carted_pairs,
               COUNT(*) FILTER (WHERE c AND NOT v)       AS carted_without_view_of_product,
               COUNT(*) FILTER (WHERE p)                 AS purchased_pairs,
               COUNT(*) FILTER (WHERE p AND NOT c)       AS purchased_without_cart_of_product,
               COUNT(*) FILTER (WHERE r)                 AS removed_pairs,
               COUNT(*) FILTER (WHERE r AND NOT c)       AS removed_without_cart_in_session
        FROM sp
    """).df()
    s.tables.append(("Session x product level", prod))
    s.notes.append("Events before 2019-10-01 are not in the data, so a few sessions active at the start "
                   "of October can show a later stage without its earlier stage.")
    return s


def check_daily(con, csv_dir: Path) -> Section:
    s = Section("07_daily", "Daily and hourly event volume (UTC): gaps and drops", "DQ-10, DQ-09")
    daily = con.execute("""
        SELECT CAST(event_ts AS DATE) AS day_utc,
               COUNT(*) AS events,
               COUNT(*) FILTER (WHERE event_type = 'view')             AS views,
               COUNT(*) FILTER (WHERE event_type = 'cart')             AS carts,
               COUNT(*) FILTER (WHERE event_type = 'remove_from_cart') AS removes,
               COUNT(*) FILTER (WHERE event_type = 'purchase')         AS purchases,
               COUNT(DISTINCT user_id)                                 AS users
        FROM raw_events WHERE event_ts IS NOT NULL GROUP BY 1 ORDER BY 1
    """).df()
    daily.to_csv(csv_dir / "07_daily_event_counts_full.csv", index=False)

    monthly = con.execute("""
        WITH d AS (SELECT CAST(event_ts AS DATE) AS day, COUNT(*) AS events
                   FROM raw_events WHERE event_ts IS NOT NULL GROUP BY 1),
             m AS (SELECT date_trunc('month', day) AS month, day, events,
                          median(events) OVER (PARTITION BY date_trunc('month', day)) AS month_median
                   FROM d)
        SELECT strftime(month, '%Y-%m') AS month_utc,
               COUNT(*)                                       AS days_with_events,
               ANY_VALUE(day(last_day(month)))                AS days_in_month,
               MIN(events) AS min_daily, CAST(ANY_VALUE(month_median) AS BIGINT) AS median_daily,
               MAX(events) AS max_daily,
               COUNT(*) FILTER (WHERE events < 0.5 * month_median) AS days_below_half_median
        FROM m GROUP BY month ORDER BY month
    """).df()
    s.tables.append(("Daily totals per month", monthly))

    low = con.execute("""
        WITH d AS (SELECT CAST(event_ts AS DATE) AS day, COUNT(*) AS events
                   FROM raw_events WHERE event_ts IS NOT NULL GROUP BY 1),
             m AS (SELECT day, events,
                          median(events) OVER (PARTITION BY date_trunc('month', day)) AS month_median FROM d)
        SELECT day AS day_utc, events, CAST(month_median AS BIGINT) AS month_median,
               ROUND(events / month_median, 3) AS ratio_to_median
        FROM m ORDER BY ratio_to_median, day LIMIT 10
    """).df()
    s.tables.append(("10 lowest days relative to their month's median", low))

    hours = con.execute("""
        WITH h AS (SELECT DISTINCT date_trunc('hour', event_ts) AS hr FROM raw_events WHERE event_ts IS NOT NULL),
             grid AS (SELECT * FROM generate_series(
                          (SELECT MIN(hr) FROM h), (SELECT MAX(hr) FROM h), INTERVAL 1 HOUR) t(hr))
        SELECT COUNT(*) AS hours_in_range,
               COUNT(*) FILTER (WHERE h.hr IS NULL) AS hours_with_zero_events,
               string_agg(strftime(grid.hr, '%Y-%m-%d %H:00'), ', ' ORDER BY grid.hr)
                   FILTER (WHERE h.hr IS NULL) AS zero_event_hours_utc
        FROM grid LEFT JOIN h USING (hr)
    """).df()
    s.tables.append(("Hours with zero events (UTC)", hours))
    s.notes.append("Full daily table: reports/day02_audit/07_daily_event_counts_full.csv")
    return s


def check_brand(con) -> Section:
    s = Section("08_brand", "Missing brand: where it occurs and whether it can be back-filled", "DQ-02")
    by_type = con.execute("""
        SELECT event_type, COUNT(*) AS rows,
               COUNT(*) FILTER (WHERE brand IS NULL) AS brand_null,
               ROUND(100.0 * COUNT(*) FILTER (WHERE brand IS NULL) / COUNT(*), 2) AS brand_null_pct,
               ROUND(SUM(price) FILTER (WHERE brand IS NULL AND price > 0), 2) AS positive_price_sum_brand_null
        FROM raw_events GROUP BY event_type ORDER BY rows DESC
    """).df()
    s.tables.append(("Brand nulls by event type", by_type))

    fill = con.execute("""
        WITH p AS (
            SELECT product_id,
                   COUNT(*) FILTER (WHERE brand IS NULL)  AS null_rows,
                   COUNT(*) FILTER (WHERE brand IS NOT NULL) AS branded_rows,
                   COUNT(DISTINCT brand)                   AS distinct_brands
            FROM raw_events GROUP BY product_id)
        SELECT COUNT(*)                                                         AS products,
               COUNT(*) FILTER (WHERE branded_rows = 0)                         AS products_never_branded,
               COUNT(*) FILTER (WHERE null_rows = 0)                            AS products_always_branded,
               COUNT(*) FILTER (WHERE null_rows > 0 AND branded_rows > 0)       AS products_mixed,
               COUNT(*) FILTER (WHERE distinct_brands > 1)                      AS products_with_2plus_brands,
               SUM(null_rows) FILTER (WHERE branded_rows > 0 AND distinct_brands = 1) AS null_rows_backfillable,
               SUM(null_rows)                                                   AS null_rows_total
        FROM p
    """).df()
    s.tables.append(("Product-level brand consistency (back-fill = take the product's single known brand)", fill))
    return s


def check_category(con) -> Section:
    s = Section("09_category", "Category consistency: product -> category_id -> category_code", "DQ-01, DQ-13")
    df = con.execute("""
        WITH pc AS (SELECT product_id, COUNT(DISTINCT category_id) AS n FROM raw_events GROUP BY 1),
             cc AS (SELECT category_id,
                           COUNT(DISTINCT category_code) AS n_codes,
                           COUNT(*) FILTER (WHERE category_code IS NULL)     AS null_rows,
                           COUNT(*) FILTER (WHERE category_code IS NOT NULL) AS coded_rows
                    FROM raw_events GROUP BY 1)
        SELECT (SELECT COUNT(*) FROM pc)                          AS products,
               (SELECT COUNT(*) FROM pc WHERE n > 1)              AS products_in_2plus_categories,
               (SELECT COUNT(*) FROM cc)                          AS category_ids,
               (SELECT COUNT(*) FROM cc WHERE coded_rows > 0)     AS category_ids_with_a_code,
               (SELECT COUNT(*) FROM cc WHERE n_codes > 1)        AS category_ids_with_2plus_codes,
               (SELECT COUNT(*) FROM cc WHERE coded_rows > 0 AND null_rows > 0) AS category_ids_mixed_null,
               (SELECT COALESCE(SUM(null_rows), 0) FROM cc WHERE coded_rows > 0 AND n_codes = 1) AS code_null_rows_backfillable
    """).df()
    s.tables.append(("Category mapping consistency", df))
    codes = con.execute("""
        SELECT category_code, COUNT(DISTINCT category_id) AS category_ids, COUNT(*) AS rows
        FROM raw_events WHERE category_code IS NOT NULL GROUP BY 1 ORDER BY rows DESC
    """).df()
    s.tables.append(("All non-null category_code values", codes))
    return s


def check_distinct(con) -> Section:
    s = Section("10_distinct", "Exact distinct counts (raw, before cleaning)", "DQ-12")
    q = """
        SELECT {label} AS scope,
               COUNT(*) AS rows,
               COUNT(DISTINCT user_id)      AS users,
               COUNT(DISTINCT user_session) AS sessions,
               COUNT(DISTINCT product_id)   AS products,
               COUNT(DISTINCT category_id)  AS category_ids,
               COUNT(DISTINCT brand)        AS brands,
               COUNT(DISTINCT user_id)      FILTER (WHERE event_type = 'purchase') AS buyers,
               COUNT(DISTINCT user_session) FILTER (WHERE event_type = 'purchase') AS purchasing_sessions
        FROM raw_events r {join} {group}
    """
    per_file = con.execute(q.format(label="r.source_file", join="JOIN file_months m USING (source_file)",
                                    group="GROUP BY r.source_file ORDER BY ANY_VALUE(m.file_order)")).df()
    total = con.execute(q.format(label="'ALL 4 MONTHS'", join="", group="")).df()
    s.tables.append(("Per file and across all 4 months", pd.concat([per_file, total], ignore_index=True)))
    s.notes.append("The 4-month figures are NOT the sum of the monthly ones: users, products and brands recur.")
    s.notes.append("purchasing_sessions is the approved order proxy, counted here on raw data; "
                   "it is not a verified order count.")
    return s


def check_sessions(con) -> Section:
    s = Section("11_sessions", "Session integrity: ownership, month span, duration", "DQ-04, DQ-08")
    df = con.execute("""
        WITH s AS (
            SELECT user_session,
                   COUNT(DISTINCT user_id)     AS users,
                   COUNT(DISTINCT source_file) AS files,
                   COUNT(*)                    AS events,
                   date_diff('second', MIN(event_ts), MAX(event_ts)) AS duration_s
            FROM raw_events WHERE user_session IS NOT NULL GROUP BY 1)
        SELECT COUNT(*)                                      AS sessions,
               COUNT(*) FILTER (WHERE users > 1)             AS sessions_with_2plus_users,
               COUNT(*) FILTER (WHERE files > 1)             AS sessions_spanning_2plus_months,
               COUNT(*) FILTER (WHERE duration_s > 86400)    AS sessions_longer_than_24h,
               quantile_disc(events, 0.5)                    AS events_p50,
               quantile_disc(events, 0.99)                   AS events_p99,
               MAX(events)                                   AS events_max,
               quantile_disc(duration_s, 0.5)                AS duration_s_p50,
               quantile_disc(duration_s, 0.95)               AS duration_s_p95,
               quantile_disc(duration_s, 0.99)               AS duration_s_p99,
               MAX(duration_s)                               AS duration_s_max
        FROM s
    """).df()
    s.tables.append(("Session statistics (non-null user_session)", df))
    nulls = con.execute("""
        SELECT event_type, COUNT(*) AS rows_with_null_session,
               COUNT(DISTINCT user_id) AS users_affected
        FROM raw_events WHERE user_session IS NULL GROUP BY 1 ORDER BY 2 DESC
    """).df()
    s.tables.append(("Rows with NULL user_session, by event type", nulls))
    return s


CHECKS = [check_load, check_nulls, check_prices, check_price_variation, check_duplicates,
          check_funnel_consistency, check_daily, check_brand, check_category, check_distinct, check_sessions]


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_audit(raw_dir: Path, db_path: Path, out_md: Path, csv_dir: Path, rebuild: bool = False) -> list[Section]:
    csv_dir.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    con = connect(db_path)
    try:
        t0 = time.perf_counter()
        print("staging raw CSVs (typed copy, values unchanged) ...", file=sys.stderr, flush=True)
        built = stage_raw(con, raw_dir, rebuild=rebuild)
        stage_s = time.perf_counter() - t0
        print(f"  {'built' if built else 'reused cached'} staging table in {stage_s:,.1f}s", file=sys.stderr)
        _create_file_months(con)

        sections = []
        for check in CHECKS:
            t = time.perf_counter()
            print(f"running {check.__name__} ...", file=sys.stderr, flush=True)
            sec = check(con, csv_dir) if check is check_daily else check(con)
            sec.seconds = time.perf_counter() - t
            sections.append(sec)
            for i, (_, df) in enumerate(sec.tables, start=1):
                df.to_csv(csv_dir / f"{sec.key}_{i}.csv", index=False)

        write_report(sections, out_md, raw_dir, built, stage_s, duckdb.__version__)
        print(f"Report written to {out_md}", file=sys.stderr)
        return sections
    finally:
        con.close()


def _display_path(p: Path) -> str:
    """Path relative to the project root; never an absolute path (keeps user names out of a public repo)."""
    try:
        return p.resolve().relative_to(config.PROJECT_ROOT).as_posix()
    except ValueError:
        return f"<outside project>/{p.name}"


def write_report(sections, out_md: Path, raw_dir: Path, built: bool, stage_s: float, duckdb_version: str) -> None:
    lines = [
        "# Day 2 Raw-Data Audit",
        "",
        "Generated by `python -m src.audit_raw`. **Read-only**: raw CSVs were not modified and no cleaning was "
        "applied. All figures are exact counts over the raw rows unless labelled otherwise.",
        "",
        f"- Files: {', '.join(config.RAW_FILES)} (from `{_display_path(raw_dir)}`)",
        f"- Staging: {'built' if built else 'reused cache'} in {stage_s:,.1f}s · DuckDB {duckdb_version}",
        "- Timestamps: UTC (approved definition). Tables per section are also saved as CSV in "
        "`reports/day02_audit/`.",
        "",
        "## Contents",
    ]
    lines += [f"{i}. {sec.title} ({sec.dq_refs})" for i, sec in enumerate(sections, start=1)]
    for i, sec in enumerate(sections, start=1):
        lines += ["", f"## {i}. {sec.title}", "", f"_Addresses: {sec.dq_refs} · ran in {sec.seconds:,.1f}s_"]
        for title, df in sec.tables:
            lines += ["", f"**{title}**", "", df_to_markdown(df)]
        for note in sec.notes:
            lines += ["", f"> {note}"]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Read-only data-quality audit of the raw REES46 CSVs.")
    parser.add_argument("--raw-dir", default=str(config.RAW_DIR))
    parser.add_argument("--db", default=str(config.INTERIM_DIR / "raw_stage.duckdb"),
                        help="DuckDB staging file (git-ignored)")
    parser.add_argument("--out", default=str(config.REPORTS_DIR / "day02_audit_report.md"))
    parser.add_argument("--csv-dir", default=str(config.REPORTS_DIR / "day02_audit"))
    parser.add_argument("--rebuild", action="store_true", help="re-stage the CSVs even if the cache is current")
    args = parser.parse_args(argv)

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        sys.exit(f"Raw folder not found: {raw_dir}. Run from the project root.")
    t = time.perf_counter()
    run_audit(raw_dir, Path(args.db), Path(args.out), Path(args.csv_dir), rebuild=args.rebuild)
    print(f"Total time: {time.perf_counter() - t:,.1f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
