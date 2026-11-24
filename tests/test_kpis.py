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
