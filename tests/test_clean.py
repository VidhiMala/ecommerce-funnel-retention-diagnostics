"""
Tests for src/clean.py on a tiny SYNTHETIC fixture with planted cases for every rule.
Test data only - never used for project results.

Expected values (worked out by hand from FIXTURE):
  raw rows 21 · exact-duplicate extras 4 (cart x2, purchase x1, view x1) -> 17 clean rows
  raw revenue (purchase, price > 0) 38.00 · duplicate purchase value 5.00 -> A1 revenue 33.00
  non-positive purchase: 1 row, -3.00
  purchasing sessions: raw user_session 6 -> keyed 7 (shared session split) -> valid 6
"""
import hashlib
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from src import clean as clean_mod
from src import config

HEADER = "event_time,event_type,product_id,category_id,category_code,brand,price,user_id,user_session"
S1, S2, S3 = "a" * 8 + "-aaaa-aaaa-aaaa-" + "a" * 12, "b" * 8 + "-bbbb-bbbb-bbbb-" + "b" * 12, "e" * 8 + "-eeee-eeee-eeee-" + "e" * 12
SX, S7, S8, S9 = ("f" * 8 + "-ffff-ffff-ffff-" + "f" * 12, "1" * 8 + "-1111-1111-1111-" + "1" * 12,
                  "8" * 8 + "-8888-8888-8888-" + "8" * 12, "9" * 8 + "-9999-9999-9999-" + "9" * 12)

FIXTURE = {
    "2019-Oct.csv": [
        f"2019-10-01 10:00:00 UTC,view,100,10,,a,5.00,1,{S1}",
        f"2019-10-01 10:00:05 UTC,cart,100,10,,a,5.00,1,{S1}",
        f"2019-10-01 10:00:05 UTC,cart,100,10,,a,5.00,1,{S1}",       # C1 duplicate (3 copies)
        f"2019-10-01 10:00:05 UTC,cart,100,10,,a,5.00,1,{S1}",
        f"2019-10-01 10:01:00 UTC,purchase,100,10,,a,5.00,1,{S1}",
        f"2019-10-01 10:01:00 UTC,purchase,100,10,,a,5.00,1,{S1}",   # C1 duplicate purchase (A1)
        f"2019-10-01 10:05:00 UTC,purchase,100,10,,a,5.00,1,{S1}",   # C3 repeat purchase, other time
        f"2019-10-02 11:00:00 UTC,cart,200,20,acc.bag,,-3.00,2,{S2}",     # C4 negative
        f"2019-10-02 11:00:10 UTC,remove_from_cart,300,20,,b,0,2,{S2}",   # C4 zero
        f"2019-10-02 11:00:20 UTC,purchase,200,20,acc.bag,,-3.00,2,{S2}", # C4 non-positive purchase
        f"2019-10-03 12:00:00 UTC,purchase,100,11,,,6.00,3,{S3}",    # C8 fillable brand; C9 2nd category
        "2019-10-04 13:00:00 UTC,view,400,30,,c,10.00,4,",            # C6 null session
        "2019-10-04 13:00:00 UTC,view,400,30,,c,10.00,4,",            # C1 duplicate with NULL session
        f"2019-10-05 09:00:00 UTC,purchase,500,40,,d,1.00,5,{SX}",   # C7 shared session id
        f"2019-10-05 09:00:01 UTC,purchase,500,40,,d,1.00,6,{SX}",
        f"2019-10-06 08:00:00 UTC,view,600,50,,e,2.00,7,{S7}",       # C8 conflicting brands -> no fill
        f"2019-10-06 08:00:01 UTC,view,600,50,,f,2.00,7,{S7}",
        f"2019-10-06 08:00:02 UTC,view,600,50,,,2.00,7,{S7}",
    ],
    "2019-Nov.csv": [f"2019-11-10 09:00:00 UTC,view,100,10,,a,5.00,1,{S1}"],   # C7 session > 24h, spans months
    "2019-Dec.csv": [f"2019-12-10 09:00:00 UTC,purchase,700,60,,g,7.50,8,{S8}"],
    "2020-Jan.csv": [f"2020-01-10 09:00:00 UTC,purchase,700,60,,g,7.50,8,{S9}"],
}


def _write_fixture(raw: Path, fixture: dict) -> None:
    raw.mkdir(parents=True, exist_ok=True)
    for name, rows in fixture.items():
        (raw / name).write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def run(tmp_path_factory, monkeypatch_module):
    root = tmp_path_factory.mktemp("clean")
    raw = root / "raw"
    _write_fixture(raw, FIXTURE)
    # The fixture is not the real data: point the expected Day 1 row counts at it.
    monkeypatch_module.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in FIXTURE.items()})
    hashes = {p.name: _sha(p) for p in raw.iterdir()}
    paths = dict(raw_dir=raw, db_path=root / "stage.duckdb", out_root=root / "processed",
                 log_md=root / "log.md", csv_dir=root / "csv")
    ok = clean_mod.run_clean(**paths)
    con = duckdb.connect()
    rel = clean_mod._parquet_rel(paths["out_root"] / clean_mod.OUTPUT_NAME)
    return dict(ok=ok, root=root, raw=raw, hashes=hashes, paths=paths, con=con, rel=rel)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def q(run, sql):
    return run["con"].execute(sql.format(t=run["rel"])).fetchall()


def q1(run, sql):
    return q(run, sql)[0][0]


def test_run_passes_and_output_in_place(run):
    assert run["ok"] is True
    out = run["paths"]["out_root"]
    assert (out / "events_clean").is_dir()
    assert not (out / "events_clean.__tmp__").exists() and not (out / "events_clean.__failed__").exists()
    months = sorted(p.name for p in (out / "events_clean").iterdir())
    assert months == ["event_month=2019-10", "event_month=2019-11", "event_month=2019-12", "event_month=2020-01"]
    log = run["paths"]["log_md"].read_text(encoding="utf-8")
    assert "**Validation: PASSED**" in log and "FAIL" not in log.split("## 1.")[0].replace("FAILED", "")


def test_raw_files_unchanged(run):
    assert {p.name: _sha(p) for p in run["raw"].iterdir()} == run["hashes"]


def test_c1_copies_preserve_raw_count(run):
    assert q1(run, "SELECT SUM(event_copies) FROM {t}") == 21
    assert q1(run, "SELECT COUNT(*) FROM {t}") == 17
    assert dict(q(run, "SELECT event_type, SUM(event_copies - 1) FROM {t} GROUP BY 1")) == \
        {"view": 1, "cart": 2, "remove_from_cart": 0, "purchase": 1}
    assert q1(run, "SELECT MAX(event_copies) FROM {t}") == 3


def test_a1_revenue_and_c4(run):
    raw_rev = q1(run, "SELECT SUM(CAST(price AS DECIMAL(18,2)) * event_copies) FROM {t} WHERE event_type='purchase' AND is_valid_price")
    a1 = q1(run, "SELECT SUM(CAST(price AS DECIMAL(18,2))) FROM {t} WHERE event_type='purchase' AND is_valid_price")
    assert (raw_rev, a1) == (Decimal("38.00"), Decimal("33.00"))
    assert q(run, "SELECT SUM(event_copies), SUM(price) FROM {t} WHERE event_type='purchase' AND NOT is_valid_price") == [(1, -3.0)]
    assert q1(run, "SELECT SUM(event_copies) FROM {t} WHERE NOT is_valid_price") == 3


def test_c6_c7_sessions(run):
    assert q1(run, "SELECT SUM(event_copies) FROM {t} WHERE NOT has_session") == 2
    assert q1(run, "SELECT COUNT(*) FROM {t} WHERE NOT has_session AND session_key IS NOT NULL") == 0
    assert q1(run, "SELECT COUNT(DISTINCT session_key) FROM {t}") == 8
    assert sorted(r[0] for r in q(run, "SELECT DISTINCT user_id FROM {t} WHERE session_id_shared")) == [5, 6]
    assert [r[0] for r in q(run, "SELECT DISTINCT session_key FROM {t} WHERE session_over_24h")] == [f"1:{S1}"]


def test_a2_purchasing_sessions(run):
    raw_sessions = q1(run, "SELECT COUNT(DISTINCT user_session) FROM {t} WHERE event_type='purchase'")
    keyed = q1(run, "SELECT COUNT(DISTINCT session_key) FROM {t} WHERE event_type='purchase'")
    valid = q1(run, "SELECT COUNT(DISTINCT session_key) FROM {t} WHERE event_type='purchase' AND is_valid_price")
    assert (raw_sessions, keyed, valid) == (6, 7, 6)


def test_c8_brand(run):
    assert q(run, "SELECT DISTINCT product_id, brand FROM {t} WHERE brand_filled") == [(100, "a")]
    assert q1(run, "SELECT COUNT(*) FROM {t} WHERE product_id = 600 AND brand_raw IS NULL AND brand IS NULL") == 1
    assert q1(run, "SELECT COUNT(*) FROM {t} WHERE product_id = 200 AND brand IS NULL") == 2
    assert q1(run, "SELECT COUNT(*) FROM {t} WHERE brand_raw IS NOT NULL AND brand <> brand_raw") == 0


def test_c9_category(run):
    assert q(run, "SELECT DISTINCT product_id, primary_category_id FROM {t} WHERE multi_category_product") == [(100, 10)]
    assert q1(run, "SELECT COUNT(*) FROM {t} WHERE category_id <> primary_category_id") == 1


def test_c3_repeat_purchase_kept(run):
    assert q1(run, f"SELECT COUNT(*) FROM {{t}} WHERE session_key = '1:{S1}' AND event_type = 'purchase'") == 2


def test_log_and_csvs(run):
    log = run["paths"]["log_md"].read_text(encoding="utf-8")
    for heading in ["Revenue reconciliation", "A2 purchasing sessions", "Event counts: raw", "C1: exact duplicates"]:
        assert heading in log
    csvs = {p.name for p in run["paths"]["csv_dir"].iterdir()}
    assert "00_validations.csv" in csvs and any(n.endswith("revenue_reconciliation.csv") for n in csvs)
    assert "/home/" not in log and ":\\" not in log  # no absolute paths in a public repo


def test_failed_validation_keeps_previous_output(run, monkeypatch):
    out = run["paths"]["out_root"] / "events_clean"
    before = sorted((p.relative_to(out), _sha(p)) for p in out.rglob("*.parquet"))
    monkeypatch.setattr(clean_mod, "validate",
                        lambda con, rel: [clean_mod.Check("forced failure", 1, 2)])
    log = run["root"] / "fail_log.md"
    ok = clean_mod.run_clean(run["paths"]["raw_dir"], run["paths"]["db_path"], run["paths"]["out_root"],
                             log, run["root"] / "fail_csv")
    assert ok is False
    assert sorted((p.relative_to(out), _sha(p)) for p in out.rglob("*.parquet")) == before
    assert (run["paths"]["out_root"] / "events_clean.__failed__").is_dir()
    assert "**Validation: FAILED**" in log.read_text(encoding="utf-8")


def test_unparsable_timestamp_stops(tmp_path, monkeypatch):
    bad = {k: list(v) for k, v in FIXTURE.items()}
    bad["2019-Oct.csv"].append(f"2019/10/07 10:00:00,view,100,10,,a,5.00,1,{S1}")
    monkeypatch.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in bad.items()})
    _write_fixture(tmp_path / "raw", bad)
    with pytest.raises(RuntimeError, match="no parsable timestamp"):
        clean_mod.run_clean(tmp_path / "raw", tmp_path / "s.duckdb", tmp_path / "out", tmp_path / "l.md", tmp_path / "c")
    assert not (tmp_path / "out" / "events_clean").exists()


def test_safe_rmtree_refuses_outside_root(tmp_path):
    with pytest.raises(RuntimeError):
        clean_mod._safe_rmtree(tmp_path / "elsewhere", tmp_path / "processed")


def test_purchase_without_session_not_counted_as_a_session(tmp_path, monkeypatch):
    """Regression (found on Day 4): a valid purchase with no user_session must not form a NULL
    'purchasing session' in the monthly A2 table, so monthly figures add up to the A2 total."""
    fx = {k: list(v) for k, v in FIXTURE.items()}
    fx["2019-Oct.csv"].append("2019-10-08 10:00:00 UTC,purchase,800,70,,h,4.00,9,")
    monkeypatch.setattr(config, "EXPECTED_ROWS", {f: len(r) for f, r in fx.items()})
    _write_fixture(tmp_path / "raw", fx)
    assert clean_mod.run_clean(tmp_path / "raw", tmp_path / "s.duckdb", tmp_path / "out",
                               tmp_path / "log.md", tmp_path / "csv") is True
    import pandas as pd
    by_month = pd.read_csv(tmp_path / "csv" / "06_a2_by_month.csv").purchasing_sessions.sum()
    total = pd.read_csv(tmp_path / "csv" / "05_a2_purchasing_sessions.csv").sessions.iloc[-1]
    assert by_month == total == 6

