"""Unit tests for src/stats.py (synthetic data generated in the tests; never project data)."""
import numpy as np
import pytest
from statsmodels.stats.proportion import proportion_confint

from src import stats


def spec_from_dense(name, num, den, group="g"):
    num, den = np.asarray(num, float), np.asarray(den, float)
    keep = den > 0
    return stats.RatioSpec(name, np.flatnonzero(keep), num[keep], den[keep], group)


def test_wilson_matches_statsmodels():
    for k, n in [(0, 10), (3, 10), (50, 1000), (999, 1000), (126503, 3614959)]:
        lo, hi = stats.wilson_ci(k, n)
        ref = proportion_confint(k, n, alpha=0.05, method="wilson")
        assert np.allclose([lo, hi], ref, rtol=0, atol=1e-12)


def test_bh_hand_example_and_statsmodels():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    # sorted 0.005,0.01,0.03,0.04 -> 0.02,0.02,0.04,0.04 (monotone step-up)
    assert np.allclose(stats.bh_adjust(p), [0.02, 0.04, 0.04, 0.02])
    rng = np.random.default_rng(1)
    q = rng.uniform(size=50)
    adj = stats.bh_adjust(q)
    assert np.all(adj >= q - 1e-15) and np.all(adj <= 1)


def test_spec_validation():
    with pytest.raises(ValueError):
        stats.RatioSpec("bad", [0, 1], [2, 0], [1, 1])          # numerator > denominator
    with pytest.raises(ValueError):
        stats.RatioSpec("bad", [0, 0], [0, 0], [1, 1])          # duplicate user
    with pytest.raises(ValueError):
        stats.RatioSpec("bad", [0], [0], [0])                   # zero denominator


def _toy(n_users=500, seed=0):
    rng = np.random.default_rng(seed)
    den = rng.integers(1, 6, n_users)
    num = rng.binomial(den, 0.3)
    den2 = rng.integers(0, 4, n_users)
    num2 = rng.binomial(den2, 0.1)
    return [spec_from_dense("a", num, den), spec_from_dense("b", num2, den2)], n_users


def test_weights_one_reproduce_point_estimates():
    specs, n = _toy()
    b = stats.poisson_bootstrap(specs, n, B=3, weights_one=True)
    assert np.allclose(b, stats.point_estimates(specs), rtol=1e-6, atol=0)   # float32 resampling sums


def test_reproducible_and_batch_independent_of_reruns():
    specs, n = _toy()
    b1 = stats.poisson_bootstrap(specs, n, B=40, seed=7, batch=10)
    b2 = stats.poisson_bootstrap(specs, n, B=40, seed=7, batch=10)
    b3 = stats.poisson_bootstrap(specs, n, B=40, seed=8, batch=10)
    assert np.array_equal(b1, b2) and not np.array_equal(b1, b3)


def test_sparse_engine_matches_plain_loop():
    specs, n = _toy(n_users=200)
    B, seed, batch = 30, 3, 7
    fast = stats.poisson_bootstrap(specs, n, B=B, seed=seed, batch=batch)
    rng = np.random.default_rng(seed)
    dense = [(np.zeros(n), np.zeros(n)) for _ in specs]
    for (numv, denv), s in zip(dense, specs):
        numv[s.idx], denv[s.idx] = s.num, s.den
    slow, done = np.empty((B, len(specs))), 0
    while done < B:
        b = min(batch, B - done)
        w = stats.poisson_weights(rng, n, b)                            # same draws as the engine
        for r in range(b):
            for j, (numv, denv) in enumerate(dense):
                slow[done + r, j] = sum(w[u, r] * numv[u] for u in range(n)) / sum(w[u, r] * denv[u] for u in range(n))
        done += b
    assert np.allclose(fast, slow, rtol=1e-5, atol=0)                  # float32 vs float64 loop


def test_independent_data_close_to_wilson():
    rng = np.random.default_rng(11)
    y = rng.binomial(1, 0.1, 20000)                                   # one session per user
    spec = spec_from_dense("indep", y, np.ones_like(y))
    boot = stats.poisson_bootstrap([spec], len(y), B=400, seed=5)
    r = stats.summarise_specs([spec], boot).iloc[0]
    boot_w, wil_w = r.ci_high - r.ci_low, r.wilson_high - r.wilson_low
    assert 0.85 < boot_w / wil_w < 1.15
    assert 0.75 < r.design_effect < 1.3


def test_clustered_data_wider_than_wilson():
    rng = np.random.default_rng(12)
    users = 2000
    y_user = rng.binomial(1, 0.1, users)                              # 10 identical sessions per user
    spec = spec_from_dense("clustered", 10 * y_user, np.full(users, 10))
    boot = stats.poisson_bootstrap([spec], users, B=400, seed=5)
    r = stats.summarise_specs([spec], boot).iloc[0]
    assert (r.ci_high - r.ci_low) > 2.5 * (r.wilson_high - r.wilson_low)
    assert r.design_effect > 6


def test_interval_bounds_valid():
    specs, n = _toy()
    boot = stats.poisson_bootstrap(specs, n, B=200, seed=2)
    s = stats.summarise_specs(specs, boot)
    assert (s.ci_low <= s.ci_high).all() and s.ci_low.between(0, 1).all() and s.ci_high.between(0, 1).all()
    assert (s.nan_replicates == 0).all() and (s.se_boot > 0).all()


def test_compare_rates_bh_per_family_and_meaningful_flag():
    rng = np.random.default_rng(21)
    n = 6000
    # a vs b: 30% vs 20% (large, real difference); c vs d: identical 25% (no difference)
    mk = lambda name, p: spec_from_dense(name, rng.binomial(1, p, n), np.ones(n))  # noqa: E731
    specs = [mk("a", 0.30), mk("b", 0.20), mk("c", 0.25), mk("d", 0.25)]
    boot = stats.poisson_bootstrap(specs, n, B=300, seed=4)
    cmp = stats.compare_rates([("a-b", 0, 1, "f1"), ("c-d", 2, 3, "f1"), ("a-c", 0, 2, "f2")],
                              stats.point_estimates(specs), boot)
    row = cmp.set_index("comparison")
    assert row.loc["a-b", "meaningful"] and row.loc["a-b", "diff_pp"] > 5
    assert not row.loc["c-d", "meaningful"]
    assert (cmp.p_adj_bh >= cmp.p_value - 1e-15).all() and (cmp.p_adj_bh <= 1).all()
    assert row.loc["a-c", "p_adj_bh"] == pytest.approx(row.loc["a-c", "p_value"])   # alone in its family
    assert (cmp.diff_ci_low_pp <= cmp.diff_ci_high_pp).all()


def test_meaningful_needs_both_conditions():
    rng = np.random.default_rng(31)
    n = 400000
    specs = [spec_from_dense("a", rng.binomial(1, 0.105, n), np.ones(n)),
             spec_from_dense("b", rng.binomial(1, 0.100, n), np.ones(n))]
    boot = stats.poisson_bootstrap(specs, n, B=200, seed=9, batch=50)
    r = stats.compare_rates([("a-b", 0, 1, "f")], stats.point_estimates(specs), boot).iloc[0]
    assert r.significant_bh and abs(r.diff_pp) < 1 and not r.meaningful   # significant but < 1 pp


def test_poisson_weights_distribution():
    w = stats.poisson_weights(np.random.default_rng(0), 200000, 5)
    assert w.dtype == np.float32 and abs(w.mean() - 1) < 0.01 and abs(w.var() - 1) < 0.01


def test_rest_rate_as_linear_combination_equals_explicit_rest():
    """'Rest of family' = family total - segment X must equal a rate built from explicit rest counts."""
    rng = np.random.default_rng(5)
    n = 3000
    touched_x = rng.integers(0, 3, n)
    extra = rng.integers(0, 3, n)                         # sessions in the family that did not touch X
    total = touched_x + extra
    buy_x = rng.binomial(touched_x, 0.2)
    buy_rest = rng.binomial(extra, 0.1)
    all_users = np.arange(n)
    bases = [stats.Base("T", all_users, total), stats.Base("TB", all_users, buy_x + buy_rest),
             stats.Base("X", all_users, touched_x), stats.Base("XB", all_users, buy_x),
             stats.Base("R", all_users, extra), stats.Base("RB", all_users, buy_rest)]
    rates = [stats.Rate("rest_combo", {"TB": 1, "XB": -1}, {"T": 1, "X": -1}),
             stats.Rate("rest_explicit", {"RB": 1}, {"R": 1})]
    m, index, totals = stats.build_matrix(bases, n)
    assert stats.check_rates(m, rates, index) == []
    sums = stats.bootstrap_base_sums(m, B=50, seed=3)
    r = stats.rate_values(sums, rates, index)
    assert np.allclose(r[:, 0], r[:, 1], rtol=1e-5)
    assert np.isclose(stats.rate_values(totals, rates, index)[0], buy_rest.sum() / extra.sum())


def test_check_rates_flags_invalid():
    n = 4
    bases = [stats.Base("A", [0, 1], [2, 1]), stats.Base("Bx", [0, 1], [1, 3])]
    m, index, _ = stats.build_matrix(bases, n)
    bad = stats.check_rates(m, [stats.Rate("ok", {"Bx": 1}, {"Bx": 1}),
                                stats.Rate("num>den", {"Bx": 1}, {"A": 1})], index)
    assert bad == ["num>den"]
    with pytest.raises(ValueError):
        stats.build_matrix([stats.Base("A", [0], [1]), stats.Base("A", [1], [1])], n)
    with pytest.raises(ValueError):
        stats.Base("neg", [0], [-1])

