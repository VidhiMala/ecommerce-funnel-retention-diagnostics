"""
kpis.py - Day 4: business performance KPIs (SQL 01-04) on the cleaned events.

What it does
------------
1. Defines ONE view, `events`, over data/processed/events_clean (the single
   source every SQL file reads).
2. Runs the named queries in sql/01..04 in order. Each `-- name: x` block becomes
   a temp table `q_x`, so later queries (and the validations) can reuse it.
3. Validates the results (sum-of-parts = totals, distinct-count sanity, ratio
   consistency, UTC labelling) and, when Day 3 outputs are present, reconciles
   revenue, purchasing sessions and event counts with reports/day03_cleaning/.
4. Only if every check passes: writes reports/day04_kpis/<query>.csv and
   reports/day04_kpi_report.md. On any failure: writes the validation table and
   a FAILED report, leaves previous KPI CSVs untouched and exits 1.

Usage (project root, venv active)
---------------------------------
    python -m src.kpis
    python -m src.pipeline --steps verify,kpis
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

from src import config
from src.audit_raw import _display_path, _sql_str, df_to_markdown
from src.clean import OUTPUT_NAME, Check, _ordered, _parquet_rel, _tidy

SQL_DIR = config.PROJECT_ROOT / "sql"
KPI_SQL_FILES = ["01_kpi_overview.sql", "02_monthly_daily_trends.sql",
                 "03_weekday_hour_patterns.sql", "04_category_brand_performance.sql"]
NAME_RE = re.compile(r"^--\s*name:\s*(\w+)\s*$", re.MULTILINE)


# --------------------------------------------------------------------------
# SQL runner (reused by later days)
# --------------------------------------------------------------------------


def parse_named_queries(sql_text: str) -> list[tuple[str, str]]:
    """Split a SQL file into (name, query) pairs at `-- name: x` markers."""
    parts = NAME_RE.split(sql_text)
    out = []
    for i in range(1, len(parts), 2):
        query = parts[i + 1].strip().rstrip(";").strip()
        if not query:
            raise ValueError(f"Query '{parts[i]}' is empty")
        out.append((parts[i], query))
    if not out:
        raise ValueError("No `-- name:` blocks found")
    return out


def connect_events(clean_dir: Path) -> duckdb.DuckDBPyConnection:
    if not any(clean_dir.rglob("*.parquet")):
        raise FileNotFoundError(f"No cleaned data in {_display_path(clean_dir)}; run `python -m src.clean` first.")
    con = duckdb.connect()
    con.execute(f"SET memory_limit = {_sql_str(config.DUCKDB_MEMORY_LIMIT)}")
    con.execute(f"CREATE VIEW events AS SELECT * FROM {_parquet_rel(clean_dir)}")
    return con


def run_sql_files(con, sql_dir: Path, files: list[str]) -> list[str]:
    """Execute every named query, materialising each as TEMP TABLE q_<name>. Returns names in order."""
    names = []
    for f in files:
        for name, query in parse_named_queries((sql_dir / f).read_text(encoding="utf-8")):
            if name in names:
                raise ValueError(f"Duplicate query name '{name}' in {f}")
            con.execute(f"CREATE OR REPLACE TEMP TABLE q_{name} AS {query}")
            names.append(name)
    return names


# --------------------------------------------------------------------------
# Validations
# --------------------------------------------------------------------------


def _one(con, sql):
    return con.execute(sql).fetchone()[0]


def validate(con, day3_dir: Path | None) -> list[Check]:
    checks: list[Check] = []
    add = lambda n, e, a: checks.append(Check(n, e, a))  # noqa: E731
    ov = lambda c: _one(con, f"SELECT {c} FROM q_kpi_overview")  # noqa: E731

    # Overview equals a direct recomputation from `events`
    add("overview revenue = direct SUM over valid purchases",
        _one(con, "SELECT SUM(CAST(price AS DECIMAL(18,2))) FROM events WHERE event_type='purchase' AND is_valid_price"),
        ov("est_revenue"))
    add("overview events_raw = SUM(event_copies); events_dedup = COUNT(*)",
        _one(con, "SELECT SUM(event_copies) || '/' || COUNT(*) FROM events"),
        ov("events_raw || '/' || events_dedup"))

    # Sum of parts = totals (revenue exact DECIMAL; counts exact)
    for tbl, label in [("q_monthly_kpis", "months"), ("q_daily_kpis", "days"), ("q_weekday_hour", "weekday x hour cells"),
                       ("q_weekday_summary", "weekdays"), ("q_hour_summary", "hours"),
                       ("q_category_top10", "category rows"), ("q_brand_top10", "brand rows incl. unknown")]:
        add(f"revenue: sum over {label} = total", ov("est_revenue"), _one(con, f"SELECT SUM(est_revenue) FROM {tbl}"))
        if tbl not in ("q_weekday_summary", "q_hour_summary"):  # these two don't carry the column
            add(f"valid purchase events: sum over {label} = total", ov("valid_purchase_events"),
                _one(con, f"SELECT SUM(valid_purchase_events) FROM {tbl}"))
    for tbl, label in [("q_monthly_kpis", "months"), ("q_daily_kpis", "days"), ("q_weekday_hour", "weekday x hour cells")]:
        add(f"purchasing sessions: sum over {label} = total", ov("purchasing_sessions"),
            _one(con, f"SELECT SUM(purchasing_sessions) FROM {tbl}"))
    add("sessions: sum over months = total", ov("sessions"), _one(con, "SELECT SUM(sessions) FROM q_monthly_kpis"))
    add("sessions: sum over days = total", ov("sessions"), _one(con, "SELECT SUM(sessions) FROM q_daily_kpis"))
    add("sessions: sum over weekday x hour = total", ov("sessions"), _one(con, "SELECT SUM(sessions_started) FROM q_weekday_hour"))
    for c in ["events_dedup", "events_raw"]:
        add(f"{c}: sum over months = total", ov(c), _one(con, f"SELECT SUM({c}) FROM q_monthly_kpis"))
        add(f"{c}: sum over days = total", ov(c), _one(con, f"SELECT SUM({c}) FROM q_daily_kpis"))
    add("event_counts table sums to totals (dedup/raw)", ov("events_dedup || '/' || events_raw"),
        _one(con, "SELECT SUM(events_dedup) || '/' || SUM(events_raw) FROM q_event_counts"))

    # Distinct counts: never additive. Total must lie between the largest period and the sum of periods.
    for c in ["visitors", "buyers"]:
        add(f"{c}: max(month) <= total <= sum(months)", True, _one(con, f"""
            SELECT MAX({c}) <= (SELECT {c} FROM q_kpi_overview) AND (SELECT {c} FROM q_kpi_overview) <= SUM({c})
            FROM q_monthly_kpis"""))

    # Ratios consistent with their parts
    add("aov_proxy = revenue / purchasing sessions", True, _one(con, """
        SELECT aov_proxy = ROUND(est_revenue / purchasing_sessions, 2) FROM q_kpi_overview"""))
    add("conversion rates within [0, 100]", True, _one(con, """
        SELECT session_conversion_pct BETWEEN 0 AND 100 AND user_conversion_pct BETWEEN 0 AND 100 FROM q_kpi_overview"""))
    add("buyers <= visitors and purchasing sessions <= sessions", True, _one(con, """
        SELECT buyers <= visitors AND purchasing_sessions <= sessions FROM q_kpi_overview"""))

    # Shapes required by the approved decisions
    add("weekday x hour grid has 168 cells", 168, _one(con, "SELECT COUNT(*) FROM q_weekday_hour"))
    add("category table: <= 10 ranked rows + 'other'", True, _one(con, """
        SELECT COUNT(*) FILTER (WHERE category_group <> 'other') <= 10
           AND COUNT(*) FILTER (WHERE category_group = 'other') = 1 FROM q_category_top10"""))
    add("brand table: <= 10 ranked rows + 'other' + 'unknown' (unknown never ranked)", True, _one(con, """
        SELECT COUNT(*) FILTER (WHERE brand_group NOT IN ('other','unknown')) <= 10
           AND COUNT(*) FILTER (WHERE brand_group = 'other') = 1
           AND COUNT(*) FILTER (WHERE brand_group = 'unknown' AND rank IS NULL) = 1 FROM q_brand_top10"""))
    add("brand 'unknown' revenue = revenue with no brand", _one(con, """
        SELECT COALESCE(SUM(CAST(price AS DECIMAL(18,2))), 0) FROM events
        WHERE event_type='purchase' AND is_valid_price AND brand IS NULL"""),
        _one(con, "SELECT est_revenue FROM q_brand_top10 WHERE brand_group = 'unknown'"))
    # R1-R3 columns (added 2026-11-24)
    add("dup copies: all = valid-price + non-positive (direct count)", _one(con, """
        SELECT COALESCE(SUM(event_copies - 1), 0) FROM events WHERE event_type = 'purchase'"""),
        ov("dup_purchase_copies_all"))
    add("dup copies: valid-price copies <= all copies", True,
        ov("dup_purchase_copies_excluded <= dup_purchase_copies_all"))
    add("known-brand share: NULL only on the 'unknown' row", 0, _one(con, """
        SELECT COUNT(*) FROM q_brand_top10
        WHERE (brand_group = 'unknown') <> (share_of_known_brand_revenue_pct IS NULL)"""))
    add("known-brand share = row revenue / known-brand revenue (4 dp)", 0, _one(con, """
        SELECT COUNT(*) FROM q_brand_top10, (SELECT SUM(est_revenue) AS k FROM q_brand_top10
                                               WHERE brand_group <> 'unknown') t
        WHERE brand_group <> 'unknown' AND t.k > 0
          AND share_of_known_brand_revenue_pct <> ROUND(100.0 * est_revenue / t.k, 4)"""))
    add("distinct_products_purchased: no row exceeds all distinct purchased products", True, _one(con, """
        SELECT MAX(x) <= (SELECT COUNT(DISTINCT product_id) FROM events
                          WHERE event_type = 'purchase' AND is_valid_price)
        FROM (SELECT distinct_products_purchased AS x FROM q_category_top10
              UNION ALL SELECT distinct_products_purchased FROM q_brand_top10)"""))

    time_cols = [r[0] for t in ["q_kpi_overview", "q_monthly_kpis", "q_daily_kpis", "q_weekday_hour",
                                "q_weekday_summary", "q_hour_summary"]
                 for r in con.execute(f"DESCRIBE {t}").fetchall()
                 if any(k in r[0] for k in ("month", "day", "hour", "weekday", "period_")) and not r[0].startswith(("n_days", "avg_"))]
    add("every time column is labelled _utc (or is a weekday number / observed_period label)",
        [], [c for c in time_cols if not (c.endswith("_utc") or c in ("weekday_num", "observed_period"))])

    # Reconcile with Day 3 cleaning outputs (real-data runs)
    if day3_dir is not None and (day3_dir / "03_revenue_reconciliation.csv").is_file():
        d = day3_dir.as_posix()
        add("Day 3: revenue (A1) = cleaning log", _one(con, f"""
            SELECT CAST(value AS DECIMAL(18,2)) FROM read_csv('{d}/03_revenue_reconciliation.csv')
            WHERE line LIKE '= Estimated purchase revenue%'"""), ov("est_revenue"))
        add("Day 3: monthly revenue = cleaning log (all months)", 0, _one(con, f"""
            SELECT COUNT(*) FROM read_csv('{d}/04_revenue_by_month.csv', types={{'month_utc': 'VARCHAR'}}) d
            FULL JOIN q_monthly_kpis m USING (month_utc)
            WHERE CAST(d.est_revenue_a1 AS DECIMAL(18,2)) IS DISTINCT FROM m.est_revenue"""))
        add("Day 3: purchasing sessions (A2) = cleaning log", _one(con, f"""
            SELECT sessions FROM read_csv('{d}/05_a2_purchasing_sessions.csv') WHERE step LIKE 'A2:%'"""),
            ov("purchasing_sessions"))
        add("Day 3: monthly purchasing sessions = cleaning log", 0, _one(con, f"""
            SELECT COUNT(*) FROM read_csv('{d}/06_a2_by_month.csv', types={{'month_utc': 'VARCHAR'}}) d
            FULL JOIN q_monthly_kpis m USING (month_utc)
            WHERE d.purchasing_sessions IS DISTINCT FROM m.purchasing_sessions"""))
        add("Day 3: dup purchase copies (all) = cleaning log merged extras for purchase", _one(con, f"""
            SELECT merged_extra_rows FROM read_csv('{d}/01_event_counts.csv', types={{'month_utc': 'VARCHAR'}})
            WHERE month_utc = 'ALL' AND event_type = 'purchase'"""), ov("dup_purchase_copies_all"))
        add("Day 3: dup purchase copies (valid-price) and value = cleaning log", _one(con, f"""
            SELECT purchase_rows || '/' || CAST(value AS DECIMAL(18,2))
            FROM read_csv('{d}/03_revenue_reconciliation.csv') WHERE line LIKE 'Less: duplicate%'"""),
            ov("dup_purchase_copies_excluded || '/' || dup_purchase_value_excluded"))
        add("Day 3: event counts by type (raw and dedup) = cleaning log", 0, _one(con, f"""
            SELECT COUNT(*) FROM (SELECT * FROM read_csv('{d}/01_event_counts.csv', types={{'month_utc': 'VARCHAR'}})
                                  WHERE month_utc = 'ALL') d
            FULL JOIN q_event_counts e USING (event_type)
            WHERE d.raw_events IS DISTINCT FROM e.events_raw OR d.dedup_events IS DISTINCT FROM e.events_dedup"""))
    return checks


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

REPORT_SECTIONS = [
    ("kpi_overview", "Period overview (Oct 2019 – Jan 2020, UTC)"),
    ("event_counts", "Events by type: deduplicated (default) and raw"),
    ("monthly_kpis", "Monthly KPIs (UTC)"),
    ("weekday_summary", "By weekday (UTC)"),
    ("hour_summary", "By hour of day (UTC)"),
    ("category_top10", "Top 10 categories by estimated revenue"),
    ("brand_top10", "Top 10 brands by estimated revenue"),
    ("brand_coverage", "Brand and category coverage"),
]

NOTES = """
## How to read these tables

**Definitions** (full column dictionary: `docs/metric_definitions.md` §3a)
- **Estimated revenue** follows A1: positive prices on purchase events, one unit per row, merged duplicate purchases counted once. Currency is that of the source data.
- **Purchasing sessions** (A2) approximate orders. They are **not an exact order count**: the data has no order ID. **AOV (proxy)** = estimated revenue ÷ purchasing sessions.
- **Duplicate purchase copies:** `dup_purchase_copies_all` counts every merged duplicate purchase copy, at any price. `dup_purchase_copies_excluded` counts only the valid-price copies that A1 revenue leaves out; their value is `dup_purchase_value_excluded`. The difference is copies with price ≤ 0, which are already counted in `nonpositive_purchase_rows`.

**Additive vs non-additive columns**
- **Additive** (rows add up to the total): `est_revenue`, `valid_purchase_events`, event counts, and monthly / daily / weekday × hour `sessions` and `purchasing_sessions`.
- **Not additive**, because they are distinct counts: `visitors`, `buyers`, and in the category / brand tables `purchasing_sessions`, `buyers` and `distinct_products_purchased`. A person, session or product can appear in several rows. In particular, a product listed under 2+ category IDs appears in each of those rows. **Never sum these columns** (in Excel or Power BI, use the period totals instead).
- `distinct_products_purchased` is the number of distinct products bought at least once, **not units**. Units, at one per row, are `valid_purchase_events`.
- `avg_price` = estimated revenue ÷ valid purchase events: the average price per unit bought, **not** an order value.

**Brand shares**
- `revenue_share_pct` = row revenue ÷ **all** revenue, including the "unknown" brand row.
- `share_of_known_brand_revenue_pct` = row revenue ÷ revenue **with a known brand**; empty for "unknown". Use it to compare brands; use `revenue_share_pct` to size a brand against the whole business.
- `est_revenue_from_filled_brand` is the part of a brand's revenue whose brand was filled by rule C8 (missing in the raw data, filled from the product's single known brand).

**Time attribution and limitations**
- Revenue and events count in the period they happened. Sessions count in the period of their first event; purchasing sessions in the period of their first valid purchase.
- **Monthly AOV is approximate:** it divides revenue by event month by purchasing sessions by first-purchase month. Sessions with valid purchases in two months (670 in the Day 3 real data) put second-month revenue over a session counted in the earlier month. The period-level AOV is exact.
- **All times are UTC.** The store's local time zone is unknown, so local shopping hours shouldn't be read from the hour table.
- **Observed patterns:** `observed_period` in `daily_kpis.csv` marks the 2019-11-21 to 2019-11-28 peak and the 2019-12-26 to 2020-01-01 trough. These are **observed only; no cause is assumed** (decision D4).
- Conversion rates are given for the whole period only; monthly trends come on Day 5 (decision D1).
- Categories are numeric store-taxonomy IDs; a readable `category_code` exists for only a few, and some of those are non-cosmetic. "Other" rows contain only categories / brands with revenue. Revenue rankings show volume, not conversion performance (Day 5).
"""


MONEY_HINTS = ("revenue", "_value", "aov")


def _money_cols(df: pd.DataFrame) -> pd.DataFrame:
    """Show money with exactly 2 decimals in the Markdown report (CSVs keep full values)."""
    df = df.copy()
    for c in df.columns:
        if any(h in c for h in MONEY_HINTS) and not c.endswith("_pct") and pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].map(lambda v: "" if pd.isna(v) else f"{v:,.2f}")
    return df


def _overview_long(df: pd.DataFrame) -> pd.DataFrame:
    r = _money_cols(df).iloc[0]
    return pd.DataFrame({"metric": list(df.columns), "value": [str(r[c]) if not isinstance(r[c], str) else r[c]
                                                              for c in df.columns]})


def write_report(path: Path, ok: bool, checks, tables: dict, clean_dir: Path, secs: float) -> None:
    n_pass = sum(c.passed for c in checks)
    lines = ["# Day 4 Business Performance KPIs", "",
             "Generated by `python -m src.kpis` from `sql/01`–`sql/04` on "
             f"`{_display_path(clean_dir)}`. Definitions: `docs/metric_definitions.md`.", "",
             f"**Validation: {'PASSED' if ok else 'FAILED'}** ({n_pass}/{len(checks)} checks)"
             + ("." if ok else ": **results not published**; previous KPI CSVs left unchanged."), "",
             f"- DuckDB {duckdb.__version__} · run time {secs:,.1f}s · CSVs: `reports/day04_kpis/`"]
    if ok:
        for key, title in REPORT_SECTIONS:
            df = tables[key]
            shown = _overview_long(df) if key in ("kpi_overview", "brand_coverage") else _money_cols(df)
            lines += ["", f"## {title}", "", df_to_markdown(shown)]
        lines += ["", "Daily KPIs (123 rows) and the 168-cell weekday × hour grid are in the CSVs only.", NOTES]
    lines += ["", "## Validations", "", df_to_markdown(pd.DataFrame([{
        "#": i, "check": c.name, "result": "PASS" if c.passed else "**FAIL**",
        "expected": str(c.expected), "actual": str(c.actual)} for i, c in enumerate(checks, start=1)]))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------


def run_kpis(clean_dir: Path, out_md: Path, csv_dir: Path, day3_dir: Path | None, sql_dir: Path = SQL_DIR) -> bool:
    t0 = time.perf_counter()
    con = connect_events(clean_dir)
    try:
        print("running SQL 01-04 ...", file=sys.stderr, flush=True)
        names = run_sql_files(con, sql_dir, KPI_SQL_FILES)
        print("validating ...", file=sys.stderr, flush=True)
        checks = validate(con, day3_dir)
        ok = all(c.passed for c in checks)
        tables = {n: _tidy(_ordered(con.execute(f"SELECT * FROM q_{n}").df())) for n in names}
        csv_dir.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        if ok:
            for n in names:
                tables[n].to_csv(csv_dir / f"{n}.csv", index=False)
        pd.DataFrame([{"validation": c.name, "expected": str(c.expected), "actual": str(c.actual),
                       "result": "PASS" if c.passed else "FAIL"} for c in checks]).to_csv(
            csv_dir / "00_validations.csv", index=False)
        write_report(out_md, ok, checks, tables, clean_dir, time.perf_counter() - t0)
        print(f"validation {'PASSED' if ok else 'FAILED'}: {sum(c.passed for c in checks)}/{len(checks)} checks. "
              f"Report: {_display_path(out_md)}", file=sys.stderr)
        return ok
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Day 4 business performance KPIs.")
    p.add_argument("--clean-dir", default=str(config.PROCESSED_DIR / OUTPUT_NAME))
    p.add_argument("--out", default=str(config.REPORTS_DIR / "day04_kpi_report.md"))
    p.add_argument("--csv-dir", default=str(config.REPORTS_DIR / "day04_kpis"))
    p.add_argument("--day3-dir", default=str(config.REPORTS_DIR / "day03_cleaning"),
                   help="Day 3 cleaning CSVs to reconcile against ('' to skip)")
    a = p.parse_args(argv)
    ok = run_kpis(Path(a.clean_dir), Path(a.out), Path(a.csv_dir), Path(a.day3_dir) if a.day3_dir else None)
    if not ok:
        sys.exit("KPI validation FAILED - results not published. See the report; do not proceed.")


if __name__ == "__main__":
    main()
