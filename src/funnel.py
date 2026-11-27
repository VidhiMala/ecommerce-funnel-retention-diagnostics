"""
funnel.py - Day 5: conversion funnel analysis (SQL 05-08 + user-clustered statistics).

What it does
------------
1. Runs sql/05-08 on the single `events` view (cleaned Parquet). `-- name: _x` blocks are
   internal tables (bootstrap inputs) and are not exported.
2. Builds per-user BASE counts and the RATES defined in docs/metric_definitions.md §3b
   (session, user, recovery, monthly, segment and segment-vs-rest rates).
3. Runs the approved user-clustered Poisson bootstrap (B=1,000, fixed seed): percentile CIs,
   bootstrap-SE Wald tests, Benjamini-Hochberg per family, F7 (>= 1 pp AND significant).
4. Validates everything (reconciliation with Day 4, internal identities, recovery computed
   two independent ways, price bands, bootstrap reproducibility and interval bounds).
5. Only if every check passes: writes reports/day05_funnel/*.csv and
   reports/day05_funnel_report.md. On failure: validations + FAILED report only; exit 1.

Usage (project root, venv active)
---------------------------------
    python -m src.funnel
    python -m src.pipeline --steps verify,funnel
"""
from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from src import config, stats
from src.audit_raw import _display_path, df_to_markdown
from src.clean import OUTPUT_NAME, Check, _ordered, _tidy
from src.kpis import SQL_DIR, connect_events, run_sql_files

FUNNEL_SQL_FILES = ["05_funnel_overall.sql", "06_funnel_category.sql", "07_funnel_brand.sql",
                    "08_funnel_price_band.sql"]
FAMILIES = ["category", "brand", "price_band"]


BOOTSTRAP_INPUTS = ["_user_index", "_user_session_counts", "_user_flags", "_user_recovery", "_user_month_counts"] + [
    f"_{f}_{t}" for f in FAMILIES for t in ("segment_map", "user_segment_counts", "user_family_counts")]


def _handoff_inputs(con, work_dir: Path) -> duckdb.DuckDBPyConnection:
    """Write the bootstrap inputs to Parquet, close the big SQL connection (freeing its memory),
    and return a fresh light connection exposing the same q__* tables as views."""
    work_dir.mkdir(parents=True, exist_ok=True)
    for n in BOOTSTRAP_INPUTS:
        con.execute(f"COPY q_{n} TO '{(work_dir / (n + '.parquet')).as_posix()}' (FORMAT parquet)")
    con.close()
    gc.collect()
    light = duckdb.connect()
    for n in BOOTSTRAP_INPUTS:
        light.execute(f"CREATE VIEW q_{n} AS SELECT * FROM read_parquet('{(work_dir / (n + '.parquet')).as_posix()}')")
    return light


def _one(con, sql):
    return con.execute(sql).fetchone()[0]


def _arr(con, sql) -> dict:
    return con.execute(sql).fetchnumpy()


# --------------------------------------------------------------------------- bases and rates

def build_bases_and_rates(con) -> tuple[list[stats.Base], list[stats.Rate], list[tuple], int, list[str]]:
    n_users = _one(con, "SELECT COUNT(*) FROM q__user_index")
    bases: list[stats.Base] = []
    rates: list[stats.Rate] = []

    def add_base(name, idx, val):
        idx, val = np.asarray(idx), np.asarray(val, dtype=float)
        keep = val != 0
        bases.append(stats.Base(name, idx[keep], val[keep]))

    # session-level counts per user
    s = _arr(con, "SELECT * FROM q__user_session_counts ORDER BY u_pos")
    for col in ["sessions", "v", "c", "p", "vc", "vcp", "cp", "vc_chron", "vcp_chron"]:
        add_base(f"s_{col}", s["u_pos"], s[col])
    # user-level flags
    u = _arr(con, """SELECT i.u_pos, f.uv::INT uv, f.uc::INT uc, f.up::INT up,
                            (f.uv AND f.uc)::INT uvc, (f.uv AND f.uc AND f.up)::INT uvcp
                     FROM q__user_flags f JOIN q__user_index i USING (user_id) ORDER BY i.u_pos""")
    add_base("u_visitor", u["u_pos"], np.ones(len(u["u_pos"])))
    for col in ["uv", "uvc", "uvcp", "up"]:
        add_base(f"u_{col}", u["u_pos"], u[col])
    # recovery (ordered = primary, unordered alongside)
    r = _arr(con, """SELECT i.u_pos, within_session_buyer::INT w, unordered_a::INT un, ordered_a::INT od
                     FROM q__user_recovery r JOIN q__user_index i USING (user_id) ORDER BY i.u_pos""")
    add_base("r_carting", r["u_pos"], np.ones(len(r["u_pos"])))
    add_base("r_within", r["u_pos"], r["w"])
    add_base("r_unordered", r["u_pos"], r["un"])
    add_base("r_ordered", r["u_pos"], r["od"])

    R = lambda name, num, den, group: rates.append(stats.Rate(name, num, den, group))  # noqa: E731
    R("session_conversion", {"s_p": 1}, {"s_sessions": 1}, "session")
    R("session_view_to_cart_nested", {"s_vc": 1}, {"s_v": 1}, "session")
    R("session_cart_to_purchase_nested", {"s_vcp": 1}, {"s_vc": 1}, "session")
    R("session_view_to_purchase_nested", {"s_vcp": 1}, {"s_v": 1}, "session")
    R("session_view_to_cart_chronological", {"s_vc_chron": 1}, {"s_v": 1}, "session")
    R("session_cart_to_purchase_chronological", {"s_vcp_chron": 1}, {"s_vc_chron": 1}, "session")
    R("session_view_to_purchase_chronological", {"s_vcp_chron": 1}, {"s_v": 1}, "session")
    R("session_cart_to_purchase_any_view", {"s_cp": 1}, {"s_c": 1}, "session")
    R("session_purchase_outside_nested_share", {"s_p": 1, "s_vcp": -1}, {"s_p": 1}, "session")
    R("user_conversion", {"u_up": 1}, {"u_visitor": 1}, "user")
    R("user_view_to_cart_nested", {"u_uvc": 1}, {"u_uv": 1}, "user")
    R("user_cart_to_purchase_nested", {"u_uvcp": 1}, {"u_uvc": 1}, "user")
    R("user_view_to_purchase_nested", {"u_uvcp": 1}, {"u_uv": 1}, "user")
    R("recovery_within_session_rate", {"r_within": 1}, {"r_carting": 1}, "recovery")
    R("recovery_ordered_rate", {"r_ordered": 1}, {"r_carting": 1}, "recovery")
    R("recovery_unordered_rate", {"r_unordered": 1}, {"r_carting": 1}, "recovery")

    # monthly (N4 session attribution; N5 user months)
    months = [m for (m,) in con.execute("SELECT DISTINCT month_utc FROM q__user_month_counts ORDER BY 1").fetchall()]
    pairs: list[tuple] = []
    for m in months:                      # one numeric query per month (no per-row strings in memory)
        d = _arr(con, f"""SELECT u_pos, sessions, p, visitor, buyer FROM q__user_month_counts
                          WHERE month_utc = '{m}' ORDER BY u_pos""")
        for col in ["sessions", "p", "visitor", "buyer"]:
            add_base(f"m{m}_{col}", d["u_pos"], d[col])
        R(f"session_conversion_{m}", {f"m{m}_p": 1}, {f"m{m}_sessions": 1}, "monthly_session")
        R(f"user_conversion_{m}", {f"m{m}_buyer": 1}, {f"m{m}_visitor": 1}, "monthly_user")
    names = [x.name for x in rates]
    for a, b in zip(months[:-1], months[1:]):
        for kind in ["session", "user"]:
            pairs.append((f"{kind} conversion {b} vs {a}", names.index(f"{kind}_conversion_{b}"),
                          names.index(f"{kind}_conversion_{a}"), f"monthly:{kind}_conversion"))

    # segments (F3/F5): segment X vs rest of family, for cart rate and purchase rate
    for f in FAMILIES:
        fam = _arr(con, f"SELECT * FROM q__{f}_user_family_counts ORDER BY u_pos")
        for col in ["t", "tc", "tp"]:
            add_base(f"{f}:{col}", fam["u_pos"], fam[col])
        labels = dict(con.execute(f"""SELECT DISTINCT segment_order, segment FROM q__{f}_segment_map
                                       WHERE in_family""").fetchall())
        seg = _arr(con, f"""SELECT l.segment_order AS code, c.u_pos, c.x, c.xc, c.xp, c.xca, c.xpa
                            FROM q__{f}_user_segment_counts c
                            JOIN (SELECT DISTINCT segment, segment_order FROM q__{f}_segment_map) l USING (segment)
                            ORDER BY code, c.u_pos""")
        cuts = np.flatnonzero(np.diff(seg["code"])) + 1
        for chunk in np.split(np.arange(len(seg["code"])), cuts):
            if not len(chunk):
                continue
            x = labels[int(seg["code"][chunk[0]])]
            for col in ["x", "xc", "xp", "xca", "xpa"]:
                add_base(f"{f}:{x}:{col}", seg["u_pos"][chunk], seg[col][chunk])
            for stage, k, ka, kt in [("cart", "xc", "xca", "tc"), ("purchase", "xp", "xpa", "tp")]:
                R(f"{f}:{x}:{stage}_rate", {f"{f}:{x}:{k}": 1}, {f"{f}:{x}:x": 1}, f"{f}:{stage}")
                R(f"{f}:{x}:{stage}_rate_rest", {f"{f}:{kt}": 1, f"{f}:{x}:{ka}": -1},
                  {f"{f}:t": 1, f"{f}:{x}:x": -1}, f"{f}:{stage}")
                names = [x_.name for x_ in rates]
                pairs.append((f"{f} {x}: {stage} rate vs rest", names.index(f"{f}:{x}:{stage}_rate"),
                              names.index(f"{f}:{x}:{stage}_rate_rest"), f"{f}:{stage}_rate"))
    # bases whose vectors are all zero are dropped by add_base; register empty ones so rates resolve
    have = {b.name for b in bases}
    for r_ in rates:
        for nm in list(r_.num) + list(r_.den):
            if nm not in have:
                bases.append(stats.Base(nm, np.array([], dtype=np.int64), np.array([])))
                have.add(nm)
    return bases, rates, pairs, n_users, months


# --------------------------------------------------------------------------- validation

def validate_sql(con, day4_dir: Path | None) -> list[Check]:
    checks: list[Check] = []
    add = lambda n, e, a: checks.append(Check(n, e, a))  # noqa: E731

    add("sessions: one user per session_key", 0, _one(con, "SELECT COUNT(*) FROM q__session_flags WHERE users_in_session <> 1"))
    add("session_key = user_id ':' user_session prefix for every session", 0, _one(con, """
        SELECT COUNT(*) FROM q__session_flags WHERE NOT starts_with(session_key, CAST(user_id AS VARCHAR) || ':')"""))
    add("session paths sum to all sessions", _one(con, "SELECT COUNT(*) FROM q__session_flags"),
        _one(con, "SELECT SUM(sessions) FROM q_session_paths"))
    add("user paths sum to all users", _one(con, "SELECT COUNT(*) FROM q__user_flags"),
        _one(con, "SELECT SUM(users) FROM q_user_paths"))
    for t, col in [("q_session_nested_funnel", "sessions"), ("q_user_nested_funnel", "users"),
                   ("q_session_chronological_funnel", "sessions")]:
        add(f"{t[2:]}: never increases", True, _one(con, f"""
            SELECT bool_and(x <= prev) FROM (SELECT {col} AS x, LAG({col}) OVER (ORDER BY stage_order) AS prev FROM {t})
            WHERE prev IS NOT NULL"""))
        add(f"{t[2:]}: conversion + drop-off = 100 at every stage", 0, _one(con, f"""
            SELECT COUNT(*) FROM {t} WHERE stage_order > 1
              AND abs(conversion_from_previous_pct + dropoff_from_previous_pct - 100) > 1e-9"""))
    add("session purchases = A2 purchasing sessions (direct count)", _one(con, """
        SELECT COUNT(DISTINCT session_key) FROM events WHERE event_type='purchase' AND is_valid_price AND has_session"""),
        _one(con, "SELECT sessions_with_valid_purchase FROM q_session_stage_reach"))

    # Chronological funnel (approved 2026-11-27)
    add("chronological: two independent path computations agree for every session", 0, _one(con, """
        SELECT COUNT(*) FROM q__session_chrono WHERE chron_vcp <> path_b"""))
    add("chronological: each stage is a subset of the nested (presence-based) stage", 0, _one(con, """
        SELECT COUNT(*) FROM q__session_chrono c JOIN q__session_flags f USING (session_key)
        WHERE (c.chron_vc AND NOT (f.v AND f.c)) OR (c.chron_vcp AND NOT (f.v AND f.c AND f.p))
           OR (c.chron_vcp AND NOT c.chron_vc)"""))
    add("chronological: one row per session", _one(con, "SELECT COUNT(*) FROM q__session_flags"),
        _one(con, "SELECT COUNT(*) FROM q__session_chrono"))
    add("order diagnostic: first-occurrence order <= chronological path <= nested converters", True, _one(con, """
        SELECT in_first_occurrence_order <= with_chronological_path
           AND with_chronological_path <= view_cart_purchase_sessions FROM q_order_diagnostic"""))
    add("order diagnostic: chronological-path count = chronological funnel stage 3", _one(con, """
        SELECT sessions FROM q_session_chronological_funnel WHERE stage_order = 3"""),
        _one(con, "SELECT with_chronological_path FROM q_order_diagnostic"))
    add("order diagnostic: out of first-occurrence order = cart-before-view + purchase-before-cart - both", 0, _one(con, """
        SELECT COUNT(*) FROM q__session_flags WHERE v AND c AND p
          AND NOT ((first_view_utc <= first_cart_utc) AND (first_cart_utc <= first_purchase_utc))
          AND NOT (first_cart_utc < first_view_utc OR first_purchase_utc < first_cart_utc)"""))

    # Recovery: independent methods, subset, identity
    add("recovery: unordered method A = method B (every user)", 0,
        _one(con, "SELECT COUNT(*) FROM q__user_recovery WHERE unordered_a <> unordered_b"))
    add("recovery: ordered method A = method B (every user)", 0,
        _one(con, "SELECT COUNT(*) FROM q__user_recovery WHERE ordered_a <> ordered_b"))
    add("recovery: ordered is a subset of unordered", 0,
        _one(con, "SELECT COUNT(*) FROM q__user_recovery WHERE ordered_a AND NOT unordered_a"))
    add("recovery: carting buyers = within-session + unordered cross-session - overlap", True, _one(con, """
        SELECT carting_buyers = within_session_buyers + unordered_cross_session_buyers - overlap_within_and_unordered
        FROM q_recovery_summary"""))
    add("recovery: every recovered user is a carting buyer", 0, _one(con, """
        SELECT COUNT(*) FROM q__user_recovery WHERE (unordered_a OR within_session_buyer) AND NOT carting_buyer"""))

    # Monthly reconciliation (correction 2)
    add("monthly: P(first event) - P(first purchase) = incoming - outgoing, every month", 0, _one(con, """
        SELECT COUNT(*) FROM q_monthly_session_funnel WHERE difference <> incoming - outgoing"""))
    add("monthly: sum incoming = sum outgoing", True, _one(con, "SELECT SUM(incoming) = SUM(outgoing) FROM q_monthly_session_funnel"))
    add("monthly: both attributions total the same purchasing sessions", True, _one(con, """
        SELECT SUM(purchasing_sessions) = SUM(purchasing_sessions_by_first_purchase)
           AND SUM(purchasing_sessions) = (SELECT sessions_with_valid_purchase FROM q_session_stage_reach)
        FROM q_monthly_session_funnel"""))
    add("monthly: sessions sum to all sessions", _one(con, "SELECT COUNT(*) FROM q__session_flags"),
        _one(con, "SELECT SUM(sessions) FROM q_monthly_session_funnel"))

    # Price bands (correction 4)
    add("price bands: boundaries strictly increasing", True, _one(con, """
        SELECT bool_and(upper_inclusive > COALESCE(lower_exclusive, -1e18)) AND
               bool_and(lower_exclusive IS NULL OR lower_exclusive = prev_upper)
        FROM (SELECT *, LAG(upper_inclusive) OVER (ORDER BY band) AS prev_upper FROM q_price_band_boundaries)"""))
    add("price bands: every product in exactly one band or 'no valid price'",
        _one(con, "SELECT COUNT(DISTINCT product_id) FROM events"), _one(con, "SELECT SUM(products) FROM q_price_band_products"))
    add("price bands: one row per product", 0, _one(con, """
        SELECT COUNT(*) - COUNT(DISTINCT product_id) FROM q__product_band"""))
    add("price bands: 'no valid price' = products with no positive price", _one(con, """
        SELECT COUNT(*) FROM (SELECT product_id FROM events GROUP BY 1 HAVING NOT bool_or(price > 0))"""),
        _one(con, "SELECT COALESCE(SUM(products), 0) FROM q_price_band_products WHERE segment = 'no valid price'"))
    add("price bands: each band agrees with its median and the boundaries", 0, _one(con, """
        SELECT COUNT(*) FROM q__product_band p JOIN q_price_band_boundaries b ON p.band = b.band
        WHERE NOT (p.median_price <= b.upper_inclusive AND (b.lower_exclusive IS NULL OR p.median_price > b.lower_exclusive))"""))

    # Segments
    for f in FAMILIES:
        add(f"{f}: rates within [0, 100]", 0, _one(con, f"""
            SELECT COUNT(*) FROM q_{f}_funnel WHERE NOT (cart_rate_pct BETWEEN 0 AND 100 AND purchase_rate_pct BETWEEN 0 AND 100
              AND COALESCE(rest_cart_rate_pct, 0) BETWEEN 0 AND 100 AND COALESCE(rest_purchase_rate_pct, 0) BETWEEN 0 AND 100)"""))
        add(f"{f}: each segment's sessions <= family sessions", 0, _one(con, f"""
            SELECT COUNT(*) FROM q_{f}_funnel WHERE sessions_touched > (SELECT COUNT(*) FROM q__{f}_session_family)"""))
    add("category/brand families cover every session", True, _one(con, """
        SELECT (SELECT COUNT(*) FROM q__category_session_family) = (SELECT COUNT(*) FROM q__session_flags)
           AND (SELECT COUNT(*) FROM q__brand_session_family) = (SELECT COUNT(*) FROM q__session_flags)"""))

    # Reconcile with Day 4
    if day4_dir is not None and (day4_dir / "kpi_overview.csv").is_file():
        d = day4_dir.as_posix()
        add("Day 4: sessions, purchasing sessions, visitors, buyers", _one(con, f"""
            SELECT sessions || '/' || purchasing_sessions || '/' || visitors || '/' || buyers
            FROM read_csv('{d}/kpi_overview.csv')"""), _one(con, """
            SELECT s.sessions || '/' || s.sessions_with_valid_purchase || '/' || u.visitors || '/' || u.buyers
            FROM q_session_stage_reach s, q_user_stage_reach u"""))
        add("Day 4: session and user conversion (4 dp)", _one(con, f"""
            SELECT ROUND(session_conversion_pct, 4) || '/' || ROUND(user_conversion_pct, 4) FROM read_csv('{d}/kpi_overview.csv')"""),
            _one(con, "SELECT s.session_conversion_pct || '/' || u.user_conversion_pct FROM q_session_stage_reach s, q_user_stage_reach u"))
        add("Day 4: monthly sessions, visitors, buyers; purchasing sessions by first purchase", 0, _one(con, f"""
            SELECT COUNT(*) FROM read_csv('{d}/monthly_kpis.csv', types={{'month_utc': 'VARCHAR'}}) k
            FULL JOIN q_monthly_session_funnel s USING (month_utc)
            FULL JOIN q_monthly_user_funnel u USING (month_utc)
            WHERE k.sessions IS DISTINCT FROM s.sessions OR k.visitors IS DISTINCT FROM u.visitors
               OR k.buyers IS DISTINCT FROM u.buyers
               OR k.purchasing_sessions IS DISTINCT FROM s.purchasing_sessions_by_first_purchase"""))
        for fam, col in [("category", "category_group"), ("brand", "brand_group")]:
            f4 = day4_dir / f"{fam}_top10.csv"
            if f4.is_file():
                d4 = pd.read_csv(f4, dtype={col: str})
                ranked4 = set(d4.loc[~d4[col].isin(["other", "unknown"]), col])
                ranked5 = {r[0] for r in con.execute(f"SELECT segment FROM q_{fam}_funnel").fetchall()} - {"other", "unknown"}
                add(f"Day 4: {fam} top-10 segments identical to Day 4", sorted(ranked4), sorted(ranked5))
    return checks


def validate_bootstrap(summary: pd.DataFrame, comps: pd.DataFrame, point_sql: dict, invalid_rates: list,
                       ones_ok: bool, repro_ok: bool) -> list[Check]:
    c: list[Check] = []
    add = lambda n, e, a: c.append(Check(n, e, a))  # noqa: E731
    add("rates: every rate valid per user (0 <= numerator <= denominator)", [], invalid_rates)
    add("bootstrap: all-ones weights reproduce the exact totals (rel. 1e-5)", True, ones_ok)
    add("bootstrap: first batch identical when re-run with the same seed", True, repro_ok)
    add("bootstrap: no failed replicates (NaN)", 0, int(summary.nan_replicates.sum()))
    add("bootstrap: CI bounds finite, low <= high, within [0, 1]", 0, int((~(
        np.isfinite(summary.ci_low) & np.isfinite(summary.ci_high) & (summary.ci_low <= summary.ci_high)
        & summary.ci_low.between(0, 1) & summary.ci_high.between(0, 1))).sum()))
    add("bootstrap: standard errors finite and > 0", 0, int((~(np.isfinite(summary.se_boot) & (summary.se_boot > 0))).sum()))
    add("comparisons: difference CI bounds finite, low <= high, within [-100, 100] pp", 0, int((~(
        np.isfinite(comps.diff_ci_low_pp) & np.isfinite(comps.diff_ci_high_pp) & (comps.diff_ci_low_pp <= comps.diff_ci_high_pp)
        & comps.diff_ci_low_pp.between(-100, 100) & comps.diff_ci_high_pp.between(-100, 100))).sum()))
    lp, la = comps.log10_p_value, comps.log10_p_adj_bh
    add("comparisons: log10 p finite and <= 0; log10 BH-adjusted >= log10 raw and <= 0", 0, int((~(
        np.isfinite(lp) & np.isfinite(la) & (lp <= 0) & (la <= 0) & (la >= lp - 1e-12))).sum()))
    sm_bad = 0
    for _, g in comps.groupby("family"):
        ref = stats.bh_adjust(g.p_value.to_numpy())                 # statsmodels on the float p-values
        rep = g.p_value.to_numpy() > 1e-300                         # representable (not underflowed)
        sm_bad += int((np.abs(np.log10(ref[rep]) - g.log10_p_adj_bh.to_numpy()[rep]) > 1e-9).sum())
    add("comparisons: log-space BH = statsmodels wherever p is representable", 0, sm_bad)
    add("comparisons: p_value / p_adj_bh = 10 ** log10 columns (0.0 only where < ~1e-308)", 0, int((~(
        np.isclose(comps.p_value, np.power(10.0, lp), rtol=1e-12, atol=0)
        & np.isclose(comps.p_adj_bh, np.power(10.0, la), rtol=1e-12, atol=0))).sum()))
    add("comparisons: 'meaningful' = BH-significant AND |difference| >= 1 pp", 0, int((comps.meaningful != (
        (la < np.log10(stats.ALPHA)) & (comps.diff_pp.abs() >= stats.PRACTICAL_PP))).sum()))
    shown = [stats.format_p(v) for v in list(comps.p_value) + list(comps.p_adj_bh)]
    add("report: no p-value displayed as an exact-looking zero", 0,
        sum(1 for t in shown if t.replace("0", "").replace(".", "") == "" or t in ("0", "0.0", "-0")))
    s = summary.set_index("metric")
    for name, sql_value in point_sql.items():
        add(f"point estimate = SQL table: {name}", round(sql_value, 4), round(100 * s.loc[name, "rate"], 4))
    return c


# --------------------------------------------------------------------------- report

REPORT_TABLES = [
    ("Session funnel: stage reach (all sessions)", "session_stage_reach"),
    ("Session funnel: nested (presence-based) funnel (primary)", "session_nested_funnel"),
    ("Session funnel: chronological (alongside the primary): view → cart at/after first view → valid purchase at/after that cart",
     "session_chronological_funnel"),
    ("Session funnel: all 8 paths", "session_paths"),
    ("Session funnel: supporting counts", "session_funnel_metrics"),
    ("Order diagnostic: first-occurrence order, and sessions with any chronological path (nested-funnel converters)",
     "order_diagnostic"),
    ("User funnel: stage reach", "user_stage_reach"),
    ("User funnel: nested (presence-based) funnel", "user_nested_funnel"),
    ("User funnel: all 8 paths", "user_paths"),
    ("Cross-session recovery (carting users)", "recovery_summary"),
    ("Monthly session funnel (session month = month of first event) with Day 4 reconciliation", "monthly_session_funnel"),
    ("Monthly user funnel (users counted in each month they were active)", "monthly_user_funnel"),
    ("Category funnel (sessions that touched the category)", "category_funnel"),
    ("Brand funnel (sessions that touched the brand)", "brand_funnel"),
    ("Price-band boundaries (product median positive price, quintiles across products)", "price_band_boundaries"),
    ("Products per price band", "price_band_products"),
    ("Price-band funnel (sessions that touched the band)", "price_band_funnel"),
    ("Sessions touching products without a positive price (not banded)", "price_band_no_valid_price_sessions"),
    ("Sensitivity (nothing removed from the data)", "sensitivity"),
    ("Start-of-data check", "start_of_data_check"),
]

NOTES = """
## How to read this report
- **Levels and denominators.** *Session* rates use sessions with a session ID (3,614,959 in Day 4); *user* rates use users (1,341,499). The two answer different questions and are always labelled.
- **Nested (presence-based) funnel (primary):** nested groups view → view+cart → view+cart+valid purchase. Conversion + drop-off = 100% at each step. It checks that the events are **present** in the session, **not their order**. Purchases that skip a stage within the session are not hidden: see *paths* and `session_purchase_outside_nested_share`.
- **Chronological funnel (alongside):** view → a cart at or after the first view → a valid purchase at or after that cart (same-second ties count as ordered). It is a subset of the nested funnel at every stage. Sessions where the view wasn't logged before the cart (a known logging gap, audit DQ-07) fall out of it, so neither funnel is "the truth": the gap between them is the share of activity whose order can't be confirmed.
- **First-occurrence order** (diagnostic): first view ≤ first cart ≤ first purchase. It's stricter than "an ordered path exists"; both counts are shown.
- **Overall session conversion** = purchasing sessions ÷ all sessions: the same figure as Day 4.
- **Purchasing sessions** approximate orders; they are **not an exact order count** (no order ID in the data).
- **Cross-session recovery** (decision R): among users with a cart in a session, the share who made a valid purchase in a *different* session. **Ordered** (primary) also requires the purchase at or after the cart. **Unordered** is shown alongside, and ordered ⊆ unordered. The data ends 2020-01-31, so late carts have little time: recovery is a lower bound.
- **Monthly session funnel:** each session counts in the month of its first event. Purchasing sessions therefore differ slightly from Day 4's monthly figures (month of first valid purchase): *difference = incoming − outgoing*, totals equal.
- **Segment funnels:** rates are per session that **touched** the segment. "Rest" = sessions in the family that did not touch it. Segment rows are **not additive**.
- **p-values:** computed in log space to avoid numerical underflow. The report shows p < 0.0001 as **"<0.0001"** (raw and adjusted), never as 0. `comparisons.csv` has exact `log10_p_value` and `log10_p_adj_bh`. The `p_value` / `p_adj_bh` columns are kept for compatibility, but they underflow to 0.0 below about 1e-308 (|z| > ~37.5); use the log10 columns there.
- **Uncertainty and tests** (decision S): user-clustered Poisson bootstrap, B = 1,000, fixed seed. CIs are 95% percentile intervals. Tests are Wald z using the bootstrap SE, with Benjamini–Hochberg per family. **Meaningful = BH-adjusted p < 0.05 AND |difference| ≥ 1 percentage point** (F7). Wilson intervals assume independent sessions and are shown only for reference; the **design effect** shows how much clustering widens uncertainty.
- **Sensitivity rows** recompute rates with some sessions left out; the cleaned data is unchanged.
- **All times UTC.** Observed patterns only; no causes are claimed.
"""


def write_report(path: Path, ok: bool, checks: list[Check], tables: dict, summary, comps, secs: float, boot_s: float,
                 B: int, seed: int, batch: int):
    n_pass = sum(c.passed for c in checks)
    lines = ["# Day 5 Funnel Analysis", "",
             "Generated by `python -m src.funnel` from `sql/05`–`sql/08`. Definitions and approved decisions: "
             "`docs/metric_definitions.md` §3b.", "",
             f"**Validation: {'PASSED' if ok else 'FAILED'}** ({n_pass}/{len(checks)} checks)"
             + ("." if ok else ": **results not published**; previous outputs left unchanged."), "",
             f"- DuckDB {duckdb.__version__} · bootstrap B = {B}, seed {seed}, batch {batch} "
             f"({boot_s:,.1f}s) · total {secs:,.1f}s · CSVs: `reports/day05_funnel/`"]
    if ok:
        head = summary[summary.group.isin(["session", "user", "recovery"])].copy()
        for c_ in ["rate", "ci_low", "ci_high", "wilson_low", "wilson_high"]:
            head[c_] = (100 * head[c_]).round(4)
        head = head.rename(columns={"rate": "rate_pct", "ci_low": "ci_low_pct", "ci_high": "ci_high_pct",
                                    "wilson_low": "wilson_low_pct", "wilson_high": "wilson_high_pct"})
        lines += ["", "## Headline rates with user-clustered 95% confidence intervals", "",
                  df_to_markdown(head[["metric", "numerator", "denominator", "rate_pct", "ci_low_pct", "ci_high_pct",
                                       "wilson_low_pct", "wilson_high_pct", "design_effect"]].round(4))]
        for title, key in REPORT_TABLES:
            lines += ["", f"## {title}", "", df_to_markdown(tables[key])]
        show = ["comparison", "rate_a", "rate_b", "diff_pp", "diff_ci_low_pp", "diff_ci_high_pp", "p_value",
                "p_adj_bh", "significant_bh", "meaningful"]
        for fam in ["monthly:session_conversion", "monthly:user_conversion", "category:cart_rate",
                    "category:purchase_rate", "brand:cart_rate", "brand:purchase_rate", "price_band:cart_rate",
                    "price_band:purchase_rate"]:
            sub = comps[comps.family == fam][show].copy()
            sub[["rate_a", "rate_b"]] = (100 * sub[["rate_a", "rate_b"]]).round(4)
            sub[["diff_pp", "diff_ci_low_pp", "diff_ci_high_pp"]] = sub[["diff_pp", "diff_ci_low_pp", "diff_ci_high_pp"]].round(4)
            sub["p_value"] = sub["p_value"].map(stats.format_p)        # never a rounded / underflowed 0
            sub["p_adj_bh"] = sub["p_adj_bh"].map(stats.format_p)
            sub = sub.rename(columns={"rate_a": "rate_a_pct", "rate_b": "rate_b_pct"})
            lines += ["", f"## Tests: {fam}", "", df_to_markdown(sub)]
        lines += [NOTES]
    lines += ["", "## Validations", "", df_to_markdown(pd.DataFrame([{
        "#": i, "check": c.name, "result": "PASS" if c.passed else "**FAIL**",
        "expected": str(c.expected), "actual": str(c.actual)} for i, c in enumerate(checks, start=1)]))]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- orchestration

def run_funnel(clean_dir: Path, out_md: Path, csv_dir: Path, day4_dir: Path | None,
               B: int = stats.DEFAULT_B, seed: int = stats.DEFAULT_SEED, batch: int = stats.DEFAULT_BATCH,
               sql_dir: Path = SQL_DIR, work_dir: Path | None = None) -> bool:
    """work_dir: scratch folder for bootstrap inputs (default data/interim/funnel_inputs, git-ignored)."""
    t0 = time.perf_counter()
    work_dir = work_dir or (config.INTERIM_DIR / "funnel_inputs")
    con = connect_events(clean_dir)
    try:
        print("running SQL 05-08 ...", file=sys.stderr, flush=True)
        names = run_sql_files(con, sql_dir, FUNNEL_SQL_FILES)
        print("validating SQL results ...", file=sys.stderr, flush=True)
        checks = validate_sql(con, day4_dir)

        point_sql = {
            "session_conversion": _one(con, "SELECT session_conversion_pct FROM q_session_stage_reach"),
            "user_conversion": _one(con, "SELECT user_conversion_pct FROM q_user_stage_reach"),
            "session_view_to_cart_nested": _one(con, "SELECT conversion_from_previous_pct FROM q_session_nested_funnel WHERE stage_order = 2"),
            "session_cart_to_purchase_nested": _one(con, "SELECT conversion_from_previous_pct FROM q_session_nested_funnel WHERE stage_order = 3"),
            "session_view_to_cart_chronological": _one(con, "SELECT conversion_from_previous_pct FROM q_session_chronological_funnel WHERE stage_order = 2"),
            "session_cart_to_purchase_chronological": _one(con, "SELECT conversion_from_previous_pct FROM q_session_chronological_funnel WHERE stage_order = 3"),
            "recovery_ordered_rate": _one(con, "SELECT ordered_recovery_rate_pct FROM q_recovery_summary"),
            "recovery_unordered_rate": _one(con, "SELECT unordered_recovery_rate_pct FROM q_recovery_summary"),
        }
        public = [n for n in names if not n.startswith("_")]
        tables = {n: _tidy(_ordered(con.execute(f"SELECT * FROM q_{n}").df())) for n in public}
        print("building per-user base counts ...", file=sys.stderr, flush=True)
        con = _handoff_inputs(con, work_dir)      # frees the SQL phase's memory before Python arrays are built
        bases, rates, pairs, n_users, _ = build_bases_and_rates(con)
        con.close()
        con = None
        gc.collect()
        matrix, index, totals = stats.build_matrix(bases, n_users)
        del bases
        invalid = stats.check_rates(matrix, rates, index)
        ones = stats.bootstrap_base_sums(matrix, B=1, weights_one=True)[0]
        ones_ok = bool(np.allclose(ones, totals, rtol=1e-5, atol=1e-6))
        print(f"bootstrap B={B} over {n_users:,} users, {matrix.shape[0]} base counts, {len(rates)} rates ...",
              file=sys.stderr, flush=True)
        tb = time.perf_counter()
        sums = stats.bootstrap_base_sums(matrix, B=B, seed=seed, batch=batch)
        boot_s = time.perf_counter() - tb
        first = stats.bootstrap_base_sums(matrix, B=min(batch, B), seed=seed, batch=batch)
        repro_ok = bool(np.array_equal(first, sums[:min(batch, B)]))
        boot = stats.rate_values(sums, rates, index)
        point = stats.rate_values(totals, rates, index)
        summary = stats.summarise_rates(rates, totals, boot, index)
        comps = stats.compare_rates(pairs, point, boot)
        checks += validate_bootstrap(summary, comps, point_sql, invalid, ones_ok, repro_ok)
        ok = all(c.passed for c in checks)

        csv_dir.mkdir(parents=True, exist_ok=True)
        out_md.parent.mkdir(parents=True, exist_ok=True)
        if ok:
            # publish a complete, current set: remove CSVs from earlier runs first (e.g. files whose
            # names were changed), so no stale output survives next to the new one
            for old in csv_dir.glob("*.csv"):
                old.unlink()
            for n in public:
                tables[n].to_csv(csv_dir / f"{n}.csv", index=False)
            summary.to_csv(csv_dir / "rates_with_ci.csv", index=False)
            comps.to_csv(csv_dir / "comparisons.csv", index=False)
        pd.DataFrame([{"validation": c.name, "expected": str(c.expected), "actual": str(c.actual),
                       "result": "PASS" if c.passed else "FAIL"} for c in checks]).to_csv(csv_dir / "00_validations.csv", index=False)
        write_report(out_md, ok, checks, tables, summary, comps, time.perf_counter() - t0, boot_s, B, seed, batch)
        print(f"validation {'PASSED' if ok else 'FAILED'}: {sum(c.passed for c in checks)}/{len(checks)} checks. "
              f"Report: {_display_path(out_md)}", file=sys.stderr)
        return ok
    finally:
        if con is not None:
            con.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Day 5 funnel analysis.")
    p.add_argument("--clean-dir", default=str(config.PROCESSED_DIR / OUTPUT_NAME))
    p.add_argument("--out", default=str(config.REPORTS_DIR / "day05_funnel_report.md"))
    p.add_argument("--csv-dir", default=str(config.REPORTS_DIR / "day05_funnel"))
    p.add_argument("--day4-dir", default=str(config.REPORTS_DIR / "day04_kpis"),
                   help="Day 4 KPI CSVs to reconcile against ('' to skip)")
    p.add_argument("--B", type=int, default=stats.DEFAULT_B)
    p.add_argument("--seed", type=int, default=stats.DEFAULT_SEED)
    p.add_argument("--batch", type=int, default=stats.DEFAULT_BATCH)
    a = p.parse_args(argv)
    ok = run_funnel(Path(a.clean_dir), Path(a.out), Path(a.csv_dir), Path(a.day4_dir) if a.day4_dir else None,
                    B=a.B, seed=a.seed, batch=a.batch)
    if not ok:
        sys.exit("Funnel validation FAILED - results not published. See the report; do not proceed.")


if __name__ == "__main__":
    main()