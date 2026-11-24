"""
Tests for src/kpis.py and sql/01-04.

Synthetic part: cleans the tests/test_clean.py fixture (test data only), runs the KPI SQL,
and checks values worked out by hand from that fixture:
  visitors 8 · buyers 5 · sessions 8 · purchasing sessions 6 · revenue 33.00 · AOV 5.50
  Oct: visitors 7, buyers 4, sessions 6, purchasing sessions 4, revenue 18.00, events 14 dedup / 18 raw
Real-data part: runs the full KPI step against data/processed/events_clean with Day 3
reconciliation; skipped when the cleaned data is absent (CI, fresh clone).
"""
from decimal import Decimal

import pandas as pd
import pytest

from src import clean as clean_mod
from src import config, kpis
from tests.test_clean import FIXTURE, _write_fixture


@pytest.fixture(scope="module")
def kpi(tmp_path_factory):
    root = tmp_path_factory.mktemp("kpi")
    _write_fixture(root / "raw", FIXTURE)
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in FIXTURE.items()})
    assert clean_mod.run_clean(root / "raw", root / "s.duckdb", root / "out", root / "clog.md", root / "ccsv")
    mp.undo()
    clean_dir = root / "out" / clean_mod.OUTPUT_NAME
    ok = kpis.run_kpis(clean_dir, root / "kpi.md", root / "kcsv", day3_dir=None)
    read = lambda n: pd.read_csv(root / "kcsv" / f"{n}.csv")  # noqa: E731
    return dict(ok=ok, root=root, clean_dir=clean_dir, read=read)


def test_parse_named_queries():
    q = kpis.parse_named_queries("-- header\n-- name: a\nSELECT 1;\n\n-- name: b\nSELECT 2\n")
    assert q == [("a", "SELECT 1"), ("b", "SELECT 2")]
    with pytest.raises(ValueError):
        kpis.parse_named_queries("SELECT 1")


def test_runs_and_passes(kpi):
    assert kpi["ok"] is True
    report = (kpi["root"] / "kpi.md").read_text(encoding="utf-8")
    assert "**Validation: PASSED**" in report and "not an exact order count" in report
    assert "/home/" not in report and ":\\" not in report


def test_overview_values(kpi):
    o = kpi["read"]("kpi_overview").iloc[0]
    assert (o.visitors, o.buyers, o.sessions, o.purchasing_sessions) == (8, 5, 8, 6)
    assert (o.est_revenue, o.valid_purchase_events, o.events_dedup, o.events_raw) == (33.0, 7, 17, 21)
    assert (o.aov_proxy, o.revenue_per_visitor) == (5.5, 4.125)
    assert (o.session_conversion_pct, o.user_conversion_pct) == (75.0, 62.5)
    assert (o.dup_purchase_copies_excluded, o.dup_purchase_value_excluded) == (1, 5.0)
    assert o.dup_purchase_copies_all == 1           # fixture's only duplicate purchase has a valid price
    assert (o.nonpositive_purchase_rows, o.nonpositive_purchase_value) == (1, -3.0)


def test_monthly_values_and_attribution(kpi):
    m = kpi["read"]("monthly_kpis").set_index("month_utc")
    assert list(m.index) == ["2019-10", "2019-11", "2019-12", "2020-01"]
    oct_ = m.loc["2019-10"]
    assert (oct_.visitors, oct_.buyers, oct_.sessions, oct_.purchasing_sessions) == (7, 4, 6, 4)
    assert (oct_.est_revenue, oct_.events_dedup, oct_.events_raw) == (18.0, 14, 18)
    # Session S1 started in Oct and has a Nov event: counted once, in Oct
    assert m.loc["2019-11", "sessions"] == 0 and m.loc["2019-11", "visitors"] == 1
    assert m.visitors.sum() > kpi["read"]("kpi_overview").visitors[0]          # distinct: not additive


def test_daily_observed_labels(kpi):
    d = kpi["read"]("daily_kpis")
    assert d.est_revenue.sum() == 33.0 and d.purchasing_sessions.sum() == 6
    assert d.observed_period.isna().all()            # fixture has no dates in the labelled periods
    assert set(d.columns) >= {"day_utc", "weekday_utc", "observed_period"}


def test_weekday_hour(kpi):
    wh = kpi["read"]("weekday_hour")
    assert len(wh) == 168 and wh.est_revenue.sum() == 33.0
    tue = kpi["read"]("weekday_summary").set_index("weekday_utc").loc["Tuesday"]
    assert tue.est_revenue == 17.5                    # 2019-10-01 (10.00) + 2019-12-10 (7.50)
    h = kpi["read"]("hour_summary").set_index("hour_utc")
    assert h.loc[9, "est_revenue"] == 17.0 and h.loc[10, "est_revenue"] == 10.0


def test_category_and_brand_tables(kpi):
    c = kpi["read"]("category_top10")
    assert list(c.category_group) == ["60", "10", "11", "40", "other"]
    assert list(c.est_revenue) == [15.0, 10.0, 6.0, 2.0, 0.0]
    b = kpi["read"]("brand_top10")
    assert list(b.brand_group) == ["a", "g", "d", "other", "unknown"]
    assert b.set_index("brand_group").loc["a", "est_revenue_from_filled_brand"] == 6.0
    assert b.set_index("brand_group").loc["unknown", "rank"] != b.set_index("brand_group").loc["unknown", "rank"]  # NaN
    assert b.est_revenue.sum() == 33.0
    # R2: share of known-brand revenue (known brands total 33.00 here; 'unknown' has none)
    kb = b.set_index("brand_group").share_of_known_brand_revenue_pct
    assert kb["a"] == round(100 * 16 / 33, 4) and pd.isna(kb["unknown"])
    # R1: distinct products bought at least once, not units; non-additive across category rows
    assert list(c.distinct_products_purchased) == [1, 1, 1, 1, 0]
    assert "products_sold" not in c.columns and "products_sold" not in b.columns


def test_known_brand_share_excludes_unknown(tmp_path, monkeypatch):
    """R2 with real 'unknown' revenue: the known-brand share divides by known-brand revenue only."""
    fx = {k: list(v) for k, v in FIXTURE.items()}
    fx["2019-Oct.csv"].append("2019-10-09 10:00:00 UTC,purchase,900,80,,,10.00,10,"
                              "cccccccc-0000-0000-0000-000000000000")       # 10.00 with no brand
    _clean_and_kpis(tmp_path, monkeypatch, fx)
    b = pd.read_csv(tmp_path / "kcsv" / "brand_top10.csv").set_index("brand_group")
    assert b.loc["unknown", "est_revenue"] == 10.0
    assert b.loc["a", "revenue_share_pct"] == round(100 * 16 / 43, 4)          # of all revenue
    assert b.loc["a", "share_of_known_brand_revenue_pct"] == round(100 * 16 / 33, 4)  # of known-brand revenue
    assert pd.isna(b.loc["unknown", "share_of_known_brand_revenue_pct"])


def test_dup_copies_all_vs_valid(tmp_path, monkeypatch):
    """R3: a duplicated NON-POSITIVE purchase counts in dup_purchase_copies_all but not in the
    valid-price column; revenue and purchasing sessions are unchanged by it."""
    fx = {k: list(v) for k, v in FIXTURE.items()}
    row = "2019-10-02 11:00:20 UTC,purchase,200,20,acc.bag,,-3.00,2,bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    assert row in fx["2019-Oct.csv"]
    fx["2019-Oct.csv"].append(row)                                            # duplicate of the -3.00 purchase
    _clean_and_kpis(tmp_path, monkeypatch, fx)
    o = pd.read_csv(tmp_path / "kcsv" / "kpi_overview.csv").iloc[0]
    assert (o.dup_purchase_copies_all, o.dup_purchase_copies_excluded) == (2, 1)
    assert (o.est_revenue, o.purchasing_sessions, o.nonpositive_purchase_rows) == (33.0, 6, 2)


def _clean_and_kpis(tmp_path, monkeypatch, fx):
    monkeypatch.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in fx.items()})
    _write_fixture(tmp_path / "raw", fx)
    assert clean_mod.run_clean(tmp_path / "raw", tmp_path / "s.duckdb", tmp_path / "out",
                               tmp_path / "clog.md", tmp_path / "ccsv")
    assert kpis.run_kpis(tmp_path / "out" / clean_mod.OUTPUT_NAME, tmp_path / "k.md", tmp_path / "kcsv",
                         day3_dir=None)


def test_failure_keeps_previous_outputs(kpi, monkeypatch):
    csv = kpi["root"] / "kcsv" / "kpi_overview.csv"
    before = csv.read_bytes()
    monkeypatch.setattr(kpis, "validate", lambda con, d: [kpis.Check("forced failure", 1, 2)])
    assert kpis.run_kpis(kpi["clean_dir"], kpi["root"] / "fail.md", kpi["root"] / "kcsv", day3_dir=None) is False
    assert csv.read_bytes() == before
    assert "**Validation: FAILED**" in (kpi["root"] / "fail.md").read_text(encoding="utf-8")


def test_missing_clean_data_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="run `python -m src.clean` first"):
        kpis.connect_events(tmp_path / "nothing")


REAL = config.PROCESSED_DIR / clean_mod.OUTPUT_NAME
DAY3 = config.REPORTS_DIR / "day03_cleaning"


@pytest.mark.skipif(not (REAL.exists() and any(REAL.rglob("*.parquet")) and DAY3.is_dir()),
                    reason="cleaned data / Day 3 outputs not present")
def test_real_data_kpis_reconcile_with_day3(tmp_path):
    assert kpis.run_kpis(REAL, tmp_path / "r.md", tmp_path / "csv", day3_dir=DAY3) is True
    o = pd.read_csv(tmp_path / "csv" / "kpi_overview.csv").iloc[0]
    rec = pd.read_csv(DAY3 / "03_revenue_reconciliation.csv")
    assert Decimal(str(o.est_revenue)) == Decimal(str(rec.value[2]))
    counts = pd.read_csv(DAY3 / "01_event_counts.csv")
    all_dups = counts[(counts.month_utc == "ALL") & (counts.event_type == "purchase")].merged_extra_rows.iloc[0]
    assert (o.dup_purchase_copies_all, o.dup_purchase_copies_excluded) == (all_dups, rec.purchase_rows[1])
