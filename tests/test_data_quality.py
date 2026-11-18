"""
Data-quality tests on the REAL cleaned data (data/processed/events_clean/).

Expected values are read from the committed Day 2 audit CSVs (reports/day02_audit/),
so every assertion is traceable to an audit figure - nothing is hard-coded from memory.

These tests SKIP automatically when the cleaned Parquet or the audit CSVs are absent
(e.g. in CI or a fresh clone before the data is downloaded and cleaned).
"""
from decimal import Decimal

import duckdb
import pandas as pd
import pytest

from src import config
from src.clean import CLEAN_SCHEMA, NOT_NULL, OUTPUT_NAME, _parquet_rel

CLEAN_DIR = config.PROCESSED_DIR / OUTPUT_NAME
AUDIT = config.REPORTS_DIR / "day02_audit"

pytestmark = pytest.mark.skipif(
    not any(CLEAN_DIR.rglob("*.parquet")) if CLEAN_DIR.exists() else True,
    reason="cleaned data not present (run `python -m src.clean` after downloading the raw data)",
)


@pytest.fixture(scope="module")
def db():
    if not AUDIT.is_dir():
        pytest.skip("Day 2 audit CSVs not present")
    con = duckdb.connect()
    yield con, _parquet_rel(CLEAN_DIR)
    con.close()


def audit(name: str) -> pd.DataFrame:
    return pd.read_csv(AUDIT / name)


def n(v) -> int:
    """Audit CSV number -> int; an empty SQL SUM (no qualifying rows) is written as blank -> 0."""
    return 0 if pd.isna(v) else int(v)


def one(db, sql):
    con, rel = db
    return con.execute(sql.format(t=rel)).fetchone()[0]


def test_schema(db):
    con, rel = db
    assert {r[0]: r[1] for r in con.execute(f"DESCRIBE SELECT * FROM {rel}").fetchall()} == CLEAN_SCHEMA


def test_no_nulls_in_required_columns(db):
    for c in NOT_NULL:
        assert one(db, f"SELECT COUNT(*) FROM {{t}} WHERE {c} IS NULL") == 0, c


def test_event_copies_reproduce_raw_rows(db):
    expected = sum(config.EXPECTED_ROWS.values())                       # 16,536,158 (Day 1 profile)
    assert one(db, "SELECT SUM(event_copies) FROM {t}") == expected
    extras = int(audit("05_duplicates_1.csv").extra_rows.sum())         # audit: exact-duplicate extras
    assert one(db, "SELECT COUNT(*) FROM {t}") == expected - extras


def test_duplicates_per_event_type_match_audit(db):
    con, rel = db
    got = dict(con.execute(f"SELECT event_type, SUM(event_copies - 1) FROM {rel} GROUP BY 1").fetchall())
    for _, r in audit("05_duplicates_1.csv").iterrows():
        assert got[r.event_type] == int(r.extra_rows), r.event_type


def test_raw_event_counts_match_audit(db):
    con, rel = db
    got = dict(con.execute(f"SELECT event_type, SUM(event_copies) FROM {rel} GROUP BY 1").fetchall())
    assert got == {r.event_type: int(r.rows) for _, r in audit("01_load_2.csv").iterrows()}


def test_timestamps_inside_period(db):
    assert one(db, "SELECT MIN(event_time) FROM {t}") >= pd.Timestamp("2019-10-01")
    assert one(db, "SELECT MAX(event_time) FROM {t}") < pd.Timestamp("2020-02-01")
    assert one(db, "SELECT COUNT(*) FROM {t} WHERE strftime(event_time, '%Y-%m') <> event_month") == 0


def test_revenue_raw_and_a1(db):
    raw_rev = Decimal(str(round(audit("03_prices_2.csv").est_revenue_raw.sum(), 2)))
    dup = audit("05_duplicates_1.csv").set_index("event_type").loc["purchase", "extra_rows_positive_price_sum"]
    dup = Decimal(str(round(dup, 2)))
    assert one(db, "SELECT SUM(CAST(price AS DECIMAL(18,2)) * event_copies) FROM {t} "
                   "WHERE event_type='purchase' AND is_valid_price") == raw_rev
    assert one(db, "SELECT SUM(CAST(price AS DECIMAL(18,2))) FROM {t} "
                   "WHERE event_type='purchase' AND is_valid_price") == raw_rev - dup


def test_non_positive_purchases_reported_not_lost(db):
    m = audit("03_prices_2.csv")
    assert one(db, "SELECT SUM(event_copies) FROM {t} WHERE event_type='purchase' AND NOT is_valid_price") \
        == int(m.non_positive_rows.sum())
    assert one(db, "SELECT SUM(CAST(price AS DECIMAL(18,2)) * event_copies) FROM {t} "
                   "WHERE event_type='purchase' AND NOT is_valid_price") \
        == Decimal(str(round(m.non_positive_value.sum(), 2)))


def test_brand_fill_matches_audit(db):
    b = audit("08_brand_2.csv").iloc[0]
    assert (one(db, "SELECT SUM(event_copies) FROM {t} WHERE brand_raw IS NULL") or 0) == n(b.null_rows_total)
    assert (one(db, "SELECT SUM(event_copies) FROM {t} WHERE brand_filled") or 0) == n(b.null_rows_backfillable)
    assert one(db, "SELECT COUNT(*) FROM {t} WHERE brand_raw IS NOT NULL AND brand <> brand_raw") == 0


def test_sessions_match_audit(db):
    n = audit("02_nulls_1.csv").set_index("column").loc["user_session", "total_nulls"]
    assert one(db, "SELECT SUM(event_copies) FROM {t} WHERE NOT has_session") == int(n)
    shared = audit("11_sessions_1.csv").iloc[0].sessions_with_2plus_users
    assert one(db, "SELECT COUNT(DISTINCT user_session) FROM {t} WHERE session_id_shared") == int(shared)
    raw_purchasing = audit("10_distinct_1.csv").set_index("scope").loc["ALL 4 MONTHS", "purchasing_sessions"]
    assert one(db, "SELECT COUNT(DISTINCT user_session) FROM {t} WHERE event_type='purchase'") == int(raw_purchasing)


def test_multi_category_products_match_audit(db):
    expected = audit("09_category_1.csv").iloc[0].products_in_2plus_categories
    assert one(db, "SELECT COUNT(DISTINCT product_id) FROM {t} WHERE multi_category_product") == int(expected)


def test_distinct_entities_match_audit(db):
    a = audit("10_distinct_1.csv").set_index("scope").loc["ALL 4 MONTHS"]
    assert one(db, "SELECT COUNT(DISTINCT user_id) FROM {t}") == int(a.users)
    assert one(db, "SELECT COUNT(DISTINCT product_id) FROM {t}") == int(a.products)
    assert one(db, "SELECT COUNT(DISTINCT user_session) FROM {t}") == int(a.sessions)
