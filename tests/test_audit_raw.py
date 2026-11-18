"""
Tests for src/audit_raw.py.

The fixture is a tiny SYNTHETIC dataset with known, deliberately planted problems.
It is test data only - never used for any project result. Each assertion checks
that the audit measures a planted problem exactly.

Run from the project root:  python -m pytest -q
"""
import hashlib
from pathlib import Path

import pytest

from src import audit_raw, config

HEADER = "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session"
S1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
S2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
S3 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
SC = "cccccccc-cccc-cccc-cccc-cccccccccccc"
SD = "dddddddd-dddd-dddd-dddd-dddddddddddd"

FIXTURE = {
    "2019-Oct.csv": [
        f"2019-10-01 10:00:00 UTC,view,100,10,,branda,5.0,1,{S1}",
        f"2019-10-01 10:00:05 UTC,cart,100,10,,branda,5.0,1,{S1}",
        f"2019-10-01 10:00:05 UTC,cart,100,10,,branda,5.0,1,{S1}",          # exact duplicate
        f"2019-10-01 10:01:00 UTC,purchase,100,10,,branda,5.0,1,{S1}",
        f"2019-10-02 11:00:00 UTC,cart,200,20,acc.bag,,-3.0,2,{S2}",        # negative price, no brand, cart w/o view
        f"2019-10-02 11:00:10 UTC,remove_from_cart,300,20,,brandb,0,2,{S2}",  # zero price, removed w/o cart of product
        f"2019-10-03 12:00:00 UTC,purchase,100,10,,,6.0,3,{S3}",            # purchase w/o cart/view; brand back-fillable
        "2019-10-04 13:00:00 UTC,view,400,30,,brandc,10.0,4,",              # NULL session
        f"2019/10/05 14:00:00,view,400,30,,brandc,10.0,4,{SC}",             # bad timestamp format
        f"2019-11-01 00:00:00 UTC,view,400,30,,brandc,10.0,4,{SC}",         # outside the file's month
        f"2019-10-01 10:00:00 UTC,view,100,10,,branda,5.5,1,{S1}",          # near-duplicate of row 1 (price differs)
    ],
    "2019-Nov.csv": [f"2019-11-10 09:00:00 UTC,view,500,40,,brandd,1.0,5,{S1}"],  # session reused by another user
    "2019-Dec.csv": [f"2019-12-10 09:00:00 UTC,view,500,40,,brandd,1.0,6,{SD}"],
    "2020-Jan.csv": [f"2020-01-10 09:00:00 UTC,purchase,500,40,,brandd,1.0,6,{SD}"],  # session spans Dec-Jan
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def audit(tmp_path_factory):
    root = tmp_path_factory.mktemp("audit")
    raw = root / "raw"
    raw.mkdir()
    for name, rows in FIXTURE.items():
        (raw / name).write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    hashes = {p.name: _sha(p) for p in raw.iterdir()}
    sections = audit_raw.run_audit(raw, root / "stage.duckdb", root / "report.md", root / "csv")
    by_key = {s.key: s for s in sections}
    return {"root": root, "raw": raw, "hashes": hashes, "s": by_key}


def table(audit, key, i=0):
    return audit["s"][key].tables[i][1]


def row0(audit, key, i=0):
    return table(audit, key, i).iloc[0]


def test_raw_files_unchanged(audit):
    assert {p.name: _sha(p) for p in audit["raw"].iterdir()} == audit["hashes"]


def test_report_and_csvs_written(audit):
    report = (audit["root"] / "report.md").read_text(encoding="utf-8")
    assert report.startswith("# Day 2 Raw-Data Audit")
    assert "Read-only" in report
    assert (audit["root"] / "csv" / "07_daily_event_counts_full.csv").is_file()


def test_load_integrity(audit):
    df = table(audit, "01_load").set_index("source_file")
    assert df.loc["2019-Oct.csv", "rows_loaded"] == 11
    assert df.loc["2019-Oct.csv", "event_time_bad_format"] == 1
    assert df.loc["2019-Oct.csv", "outside_file_month"] == 1
    assert df.loc["TOTAL", "rows_loaded"] == 14
    assert not df.loc["TOTAL", "rows_match"]  # fixture sizes differ from the real Day 1 counts
    labels = table(audit, "01_load", 1)
    assert labels.expected_label.all()


def test_nulls(audit):
    n = table(audit, "02_nulls").set_index("column")["total_nulls"]
    assert n["brand"] == 2
    assert n["user_session"] == 1
    assert n["category_code"] == 13
    assert n["event_time"] == 0


def test_prices(audit):
    p = table(audit, "03_prices").set_index("event_type")
    assert p.loc["cart", "price_negative_rows"] == 1
    assert p.loc["cart", "price_negative_sum"] == -3.0
    assert p.loc["remove_from_cart", "price_zero_rows"] == 1
    purch = table(audit, "03_prices", 1).set_index("source_file")
    assert purch.loc["2019-Oct.csv", "purchase_rows"] == 2
    assert purch.loc["2019-Oct.csv", "est_revenue_raw"] == 11.0
    assert "1 total" in audit["s"]["03_prices"].tables[2][0]


def test_price_variation(audit):
    v = table(audit, "04_price_variation").set_index("price_points")["products"]
    assert v["1 price"] == 2 and v["3-5 prices"] == 1


def test_duplicates(audit):
    ex = table(audit, "05_duplicates").set_index("event_type")
    assert list(ex.index) == ["cart"]
    assert ex.loc["cart", "extra_rows"] == 1
    assert ex.loc["cart", "extra_rows_positive_price_sum"] == 5.0
    near = table(audit, "05_duplicates", 2).set_index("event_type")
    assert near.loc["view", "key_groups_with_differing_attributes"] == 1
    assert near.loc["view", "rows_in_those_groups"] == 2
    rep = row0(audit, "05_duplicates", 3)
    assert rep.session_product_pairs_purchased == 3
    assert rep.pairs_purchased_at_2plus_distinct_times == 0


def test_funnel_consistency(audit):
    s = row0(audit, "06_funnel_consistency")
    assert (s.sessions, s.with_view, s.with_cart, s.with_purchase) == (5, 3, 2, 3)
    assert (s.cart_without_view, s.purchase_without_cart, s.purchase_without_view, s.remove_without_cart) == (1, 2, 1, 0)
    p = row0(audit, "06_funnel_consistency", 1)
    assert p.session_product_pairs == 7
    assert p.carted_without_view_of_product == 1
    assert p.purchased_without_cart_of_product == 2
    assert p.removed_without_cart_in_session == 1


def test_brand_backfill(audit):
    b = row0(audit, "08_brand", 1)
    assert (b.products, b.products_never_branded, b.products_always_branded, b.products_mixed) == (5, 1, 3, 1)
    assert b.null_rows_backfillable == 1 and b.null_rows_total == 2


def test_category(audit):
    c = row0(audit, "09_category")
    assert c.products_in_2plus_categories == 0
    assert c.category_ids == 4 and c.category_ids_with_a_code == 1
    assert c.category_ids_mixed_null == 1 and c.code_null_rows_backfillable == 1


def test_distinct(audit):
    d = table(audit, "10_distinct").set_index("scope").loc["ALL 4 MONTHS"]
    assert (d.users, d.sessions, d.buyers, d.purchasing_sessions) == (6, 5, 3, 3)


def test_sessions(audit):
    s = row0(audit, "11_sessions")
    assert s.sessions_with_2plus_users == 1
    assert s.sessions_spanning_2plus_months == 2
    assert s.sessions_longer_than_24h == 2


def test_cache_reused_and_rebuilt(audit, tmp_path):
    con = audit_raw.connect(audit["root"] / "stage.duckdb")
    try:
        assert audit_raw.stage_raw(con, audit["raw"]) is False          # unchanged files -> cache reused
        assert audit_raw.stage_raw(con, audit["raw"], rebuild=True) is True
    finally:
        con.close()


def test_config_order_is_chronological():
    assert config.RAW_FILES == ["2019-Oct.csv", "2019-Nov.csv", "2019-Dec.csv", "2020-Jan.csv"]
