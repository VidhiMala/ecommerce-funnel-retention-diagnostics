"""
Tests for sql/05-08 and src/funnel.py.  SYNTHETIC data only (never project results).

Part A - hand-worked fixture (SQL logic, every expected value derived by hand below).
Part B - medium random fixture (full pipeline incl. bootstrap): pass, reproducibility, failure path.

Part A fixture (UTC):
  u1 S1a  Oct 1   view, cart, purchase p100 (6.00)                 -> V C P (in order)
  u2 S2a  Oct 2   view, cart p200 | S2b Oct 3 purchase p200 (4.00) -> ordered cross-session recovery
  u3 S3a  Oct 5   purchase p300 (3.00) | S3b Oct 6 view, cart p300 -> unordered only (bought before cart)
  u4 S4a  Oct 31 23:59 view p400 ; Nov 1 cart + purchase (8.00)    -> session crosses months (fe Oct, fp Nov)
  u5 S5a  Nov 10  cart + purchase p500 (2.00) | S5b Nov 12 cart     -> within AND unordered (overlap), not ordered
  u6 S6a  Dec 5   view p600 (4.00) ; + a cart WITHOUT a session     -> user-level cart, not a carting user
  u7 S7a  Jan 10  view + purchase p700 at price 0                   -> invalid purchase; p700 has no positive price
  u8 S8a  Jan 15  view p100, p900 (12.00), p1000 (5.00); cart p100  -> V C
Product medians 2,3,4,4,5,6,8,12 -> boundaries 3.4 / 4.0 / 5.2 / 7.2; the two 4.00 products sit ON a boundary.
"""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src import clean as clean_mod
from src import config, funnel, kpis
from src.clean import Check
from tests.test_clean import HEADER


def sid(n: int) -> str:
    return f"{n:08x}-0000-0000-0000-000000000000"


ROWS = {
    "2019-Oct.csv": [
        f"2019-10-01 10:00:00 UTC,view,100,10,,a,6.00,1,{sid(11)}",
        f"2019-10-01 10:01:00 UTC,cart,100,10,,a,6.00,1,{sid(11)}",
        f"2019-10-01 10:02:00 UTC,purchase,100,10,,a,6.00,1,{sid(11)}",
        f"2019-10-02 09:00:00 UTC,view,200,20,,b,4.00,2,{sid(21)}",
        f"2019-10-02 09:01:00 UTC,cart,200,20,,b,4.00,2,{sid(21)}",
        f"2019-10-03 09:00:00 UTC,purchase,200,20,,b,4.00,2,{sid(22)}",
        f"2019-10-05 09:00:00 UTC,purchase,300,30,,c,3.00,3,{sid(31)}",
        f"2019-10-06 09:00:00 UTC,view,300,30,,c,3.00,3,{sid(32)}",
        f"2019-10-06 09:01:00 UTC,cart,300,30,,c,3.00,3,{sid(32)}",
        f"2019-10-31 23:59:00 UTC,view,400,40,,,8.00,4,{sid(41)}",
    ],
    "2019-Nov.csv": [
        f"2019-11-01 00:01:00 UTC,cart,400,40,,,8.00,4,{sid(41)}",
        f"2019-11-01 00:02:00 UTC,purchase,400,40,,,8.00,4,{sid(41)}",
        f"2019-11-10 09:00:00 UTC,cart,500,50,,a,2.00,5,{sid(51)}",
        f"2019-11-10 09:01:00 UTC,purchase,500,50,,a,2.00,5,{sid(51)}",
        f"2019-11-12 09:00:00 UTC,cart,500,50,,a,2.00,5,{sid(52)}",
    ],
    "2019-Dec.csv": [
        f"2019-12-05 09:00:00 UTC,view,600,60,,b,4.00,6,{sid(61)}",
        "2019-12-06 09:00:00 UTC,cart,600,60,,b,4.00,6,",
    ],
    "2020-Jan.csv": [
        f"2020-01-10 09:00:00 UTC,view,700,70,,c,0,7,{sid(71)}",
        f"2020-01-10 09:01:00 UTC,purchase,700,70,,c,0,7,{sid(71)}",
        f"2020-01-15 09:00:00 UTC,view,100,10,,a,6.00,8,{sid(81)}",
        f"2020-01-15 09:01:00 UTC,view,900,90,,d,12.00,8,{sid(81)}",
        f"2020-01-15 09:02:00 UTC,view,1000,100,,,5.00,8,{sid(81)}",
        f"2020-01-15 09:03:00 UTC,cart,100,10,,a,6.00,8,{sid(81)}",
    ],
}


def _clean(tmp: Path, rows: dict, monkeypatch) -> Path:
    raw = tmp / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for name, r in rows.items():
        (raw / name).write_text(HEADER + "\n" + "\n".join(r) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in rows.items()})
    assert clean_mod.run_clean(raw, tmp / "s.duckdb", tmp / "out", tmp / "clog.md", tmp / "ccsv")
    return tmp / "out" / clean_mod.OUTPUT_NAME


@pytest.fixture(scope="module")
def sqlres(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    clean_dir = _clean(tmp_path_factory.mktemp("funnelA"), ROWS, mp)
    mp.undo()
    con = kpis.connect_events(clean_dir)
    kpis.run_sql_files(con, kpis.SQL_DIR, funnel.FUNNEL_SQL_FILES)
    yield con
    con.close()


def df(con, name):
    return con.execute(f"SELECT * FROM q_{name}").df()


# ------------------------------------------------------------------ Part A: SQL logic

def test_all_sql_validations_pass(sqlres):
    failed = [c.name for c in funnel.validate_sql(sqlres, None) if not c.passed]
    assert failed == []


def test_session_stage_reach_and_strict_funnel(sqlres):
    r = df(sqlres, "session_stage_reach").iloc[0]
    assert (r.sessions, r.sessions_with_view, r.sessions_with_cart, r.sessions_with_valid_purchase) == (11, 7, 7, 5)
    f = df(sqlres, "session_strict_funnel")
    assert list(f.sessions) == [7, 5, 2]
    assert f.conversion_from_previous_pct.iloc[1] == round(100 * 5 / 7, 4) and f.conversion_from_previous_pct.iloc[2] == 40.0


def test_session_paths(sqlres):
    p = df(sqlres, "session_paths")
    got = {(r.has_view, r.has_cart, r.has_valid_purchase): r.sessions for r in p.itertuples()}
    assert got == {(True, True, True): 2, (True, True, False): 3, (True, False, False): 2,
                   (False, True, True): 1, (False, True, False): 1, (False, False, True): 2}
    m = df(sqlres, "session_funnel_metrics").iloc[0]
    assert (m.purchasing_sessions_outside_strict_funnel, m.purchasing_sessions_without_cart) == (3, 2)


def test_order_diagnostic(sqlres):
    o = df(sqlres, "order_diagnostic").iloc[0]
    assert (o.view_cart_purchase_sessions, o.in_time_order) == (2, 2)


def test_user_funnel(sqlres):
    u = df(sqlres, "user_stage_reach").iloc[0]
    assert (u.visitors, u.users_with_view, u.users_with_cart, u.buyers) == (8, 7, 7, 5)
    assert list(df(sqlres, "user_strict_funnel").users) == [7, 6, 4]


def test_cross_session_recovery(sqlres):
    r = df(sqlres, "recovery_summary").iloc[0]
    assert (r.carting_users, r.carting_buyers, r.within_session_buyers) == (6, 5, 3)
    assert (r.unordered_cross_session_buyers, r.ordered_cross_session_recovered) == (3, 1)
    assert (r.overlap_within_and_unordered, r.unordered_but_not_ordered) == (1, 2)
    assert r.carting_buyers == r.within_session_buyers + r.unordered_cross_session_buyers - r.overlap_within_and_unordered
    per_user = df(sqlres, "_user_recovery").set_index("user_id")
    assert per_user.loc[2, "ordered_a"] and per_user.loc[2, "ordered_b"]               # cart then later purchase
    assert per_user.loc[3, "unordered_a"] and not per_user.loc[3, "ordered_a"]          # bought before carting
    assert per_user.loc[5, "within_session_buyer"] and per_user.loc[5, "unordered_a"] and not per_user.loc[5, "ordered_a"]
    assert 6 not in per_user.index                                                       # cart only without a session


def test_monthly_reconciliation(sqlres):
    m = df(sqlres, "monthly_session_funnel").set_index("month_utc")
    assert list(m.sessions) == [6, 2, 1, 2]
    assert list(m.purchasing_sessions) == [4, 1, 0, 0]
    assert list(m.purchasing_sessions_by_first_purchase) == [3, 2, 0, 0]
    assert (m.loc["2019-10", "incoming"], m.loc["2019-11", "outgoing"]) == (1, 1)
    assert (m.difference == m.incoming - m.outgoing).all() and m.difference.sum() == 0
    u = df(sqlres, "monthly_user_funnel").set_index("month_utc")
    assert list(u.visitors) == [4, 2, 1, 2] and list(u.buyers) == [3, 2, 0, 0]


def test_price_bands(sqlres):
    b = df(sqlres, "price_band_boundaries")
    assert list(b.upper_inclusive.round(6)) == [3.4, 4.0, 5.2, 7.2, 12.0]
    pb = df(sqlres, "_product_band").set_index("product_id")
    assert pb.loc[200, "band"] == pb.loc[600, "band"] == 2                                # tie ON the boundary
    assert [pb.loc[p, "band"] for p in (500, 300, 1000, 100, 400, 900)] == [1, 1, 3, 4, 5, 5]
    assert pd.isna(pb.loc[700, "band"])                                                   # no positive price
    counts = df(sqlres, "price_band_products").set_index("segment").products
    assert counts.to_dict() == {"band_1": 2, "band_2": 2, "band_3": 1, "band_4": 1, "band_5": 2, "no valid price": 1}


def test_segment_funnels_shape(sqlres):
    cat = df(sqlres, "category_funnel")
    assert list(cat.segment[:5]) == ["40", "10", "20", "30", "50"] and cat.segment.iloc[-1] == "other"
    brand = df(sqlres, "brand_funnel").set_index("segment")
    assert "unknown" in brand.index and brand.loc["unknown", "sessions_touched"] == 2        # S4a, S8a (p1000)
    band = df(sqlres, "price_band_funnel").set_index("segment")
    assert "no valid price" not in band.index                                              # reported separately
    assert df(sqlres, "price_band_no_valid_price_sessions").iloc[0].sessions_touching_no_valid_price_products == 1


# ------------------------------------------------------------------ Part B: full pipeline

def _random_rows(n_users=1500, seed=3):
    rng = np.random.default_rng(seed)
    months = [("2019-Oct.csv", "2019-10", 31), ("2019-Nov.csv", "2019-11", 30),
              ("2019-Dec.csv", "2019-12", 31), ("2020-Jan.csv", "2020-01", 31)]
    rows = {m[0]: [] for m in months}
    n_sess = 0
    for u in range(1, n_users + 1):
        for _ in range(rng.integers(1, 5)):
            f, ym, days = months[rng.integers(0, 4)]
            n_sess += 1
            day, sec = rng.integers(1, days + 1), rng.integers(0, 80000)
            for _e in range(rng.integers(1, 6)):
                prod = int(rng.integers(1, 60))
                cat, brand = 1000 + prod % 14, ("" if prod % 5 == 0 else f"b{prod % 13}")
                price = f"{(prod % 23) * 1.5 + (0 if prod == 7 else 0.5):.2f}" if prod != 7 else "0"
                etype = rng.choice(["view", "cart", "purchase"], p=[0.6, 0.28, 0.12])
                sec += int(rng.integers(1, 120))
                ts = f"{ym}-{day:02d} {sec // 3600 % 24:02d}:{sec // 60 % 60:02d}:{sec % 60:02d} UTC"
                rows[f].append(f"{ts},{etype},{prod},{cat},,{brand},{price},{u},{sid(n_sess)}")
    return rows


@pytest.fixture(scope="module")
def full(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("funnelB")
    mp = pytest.MonkeyPatch()
    clean_dir = _clean(tmp, _random_rows(), mp)
    mp.undo()
    ok = funnel.run_funnel(clean_dir, tmp / "r1.md", tmp / "c1", day4_dir=None, B=200, batch=50, work_dir=tmp / "w")
    return dict(ok=ok, tmp=tmp, clean_dir=clean_dir)


def _hashes(d: Path):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.glob("*.csv"))}


def test_full_pipeline_passes(full):
    assert full["ok"] is True
    rep = (full["tmp"] / "r1.md").read_text(encoding="utf-8")
    assert "**Validation: PASSED**" in rep and "not an exact order count" in rep and "/home/" not in rep
    assert "bootstrap B = 200, seed 20191001, batch 50" in rep                    # header states what actually ran
    rates = pd.read_csv(full["tmp"] / "c1" / "rates_with_ci.csv").set_index("metric")
    assert {"session_conversion", "recovery_ordered_rate", "recovery_unordered_rate"} <= set(rates.index)
    assert rates.loc["recovery_ordered_rate", "rate"] <= rates.loc["recovery_unordered_rate", "rate"]
    comps = pd.read_csv(full["tmp"] / "c1" / "comparisons.csv")
    assert set(comps.family) >= {"category:purchase_rate", "brand:cart_rate", "price_band:purchase_rate",
                                 "monthly:session_conversion"}


def test_full_pipeline_reproducible(full):
    tmp = full["tmp"]
    assert funnel.run_funnel(full["clean_dir"], tmp / "r2.md", tmp / "c2", day4_dir=None, B=200, batch=50, work_dir=tmp / "w")
    assert _hashes(tmp / "c1") == _hashes(tmp / "c2")


def test_failed_validation_publishes_nothing(full, monkeypatch):
    tmp = full["tmp"]
    monkeypatch.setattr(funnel, "validate_sql", lambda con, d: [Check("forced failure", 1, 2)])
    assert funnel.run_funnel(full["clean_dir"], tmp / "rf.md", tmp / "cf", day4_dir=None, B=20, batch=10,
                             work_dir=tmp / "w") is False
    assert [p.name for p in (tmp / "cf").glob("*.csv")] == ["00_validations.csv"]
    assert "**Validation: FAILED**" in (tmp / "rf.md").read_text(encoding="utf-8")


REAL = config.PROCESSED_DIR / clean_mod.OUTPUT_NAME
DAY4 = config.REPORTS_DIR / "day04_kpis"


@pytest.mark.skipif(not (REAL.exists() and any(REAL.rglob("*.parquet")) and DAY4.is_dir()),
                    reason="cleaned data / Day 4 outputs not present")
def test_real_data_funnel_sql_reconciles(tmp_path):
    """Real data: every SQL-level validation (incl. Day 4 reconciliation) passes. The full bootstrap
    runs in `python -m src.funnel`; here only the SQL layer is checked to keep pytest fast."""
    con = kpis.connect_events(REAL)
    try:
        kpis.run_sql_files(con, kpis.SQL_DIR, funnel.FUNNEL_SQL_FILES)
        failed = [c.name for c in funnel.validate_sql(con, DAY4) if not c.passed]
    finally:
        con.close()
    assert failed == []
