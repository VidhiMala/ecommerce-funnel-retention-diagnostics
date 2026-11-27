"""
stats.py - statistics for the funnel analysis (approved Day 5 decisions F6/F7, S).

Why user-clustered?
-------------------
The same user contributes several sessions (~2.7 per visitor) and a session can touch
several segments. Ordinary chi-square / two-proportion z-tests assume independent sessions
and would understate uncertainty. We resample USERS: each user gets one Poisson(1) weight
per bootstrap replicate, and every rate is recomputed from user-weighted counts. All rates
in a run share the same weights, so dependence between overlapping comparisons is kept.

Design (for 1.3M users x B=1,000 on a 16 GB laptop)
-------------------------------------------------
* BASE COUNTS: sparse per-user count vectors (e.g. sessions, purchasing sessions,
  sessions touching segment X). Only non-zero users are stored.
* RATES: numerator / denominator, each a LINEAR combination of base counts. A "rest of
  family" rate is (family total - segment X) without materialising a new vector.
  Because weighted sums are linear, the bootstrap only weights each base count once.
* WEIGHTS: Poisson(1) by inverse-CDF on float32 uniforms (P(w >= 11) ~ 1e-8, negligible),
  drawn in batches from one seeded generator -> results depend only on (data, B, seed, batch).
* Resampled sums use float32 (speed/memory); every POINT ESTIMATE uses exact float64 sums.

Public API
----------
Base, Rate                   data definitions
build_matrix                 sparse (n_bases x n_users) float32 matrix
bootstrap_base_sums          (B x n_bases) user-weighted sums
rate_values                  rates from base sums (point or bootstrap)
check_rates                  per-user validity: 0 <= numerator <= denominator
summarise_rates              point, percentile CI, bootstrap SE, Wilson CI, design effect
compare_rates                differences: Wald z with bootstrap SE, percentile CI, BH, F7 flag
wilson_ci, bh_adjust         helpers
RatioSpec, poisson_bootstrap simple one-numerator/one-denominator convenience wrapper
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.stats import norm, poisson
from statsmodels.stats.multitest import multipletests

ALPHA = 0.05
PRACTICAL_PP = 1.0          # F7: |difference| >= 1 percentage point
DEFAULT_B = 1000            # decision S
DEFAULT_SEED = 20191001     # fixed seed: reproducible results
DEFAULT_BATCH = 20          # replicates per batch
_POISSON1_CDF = poisson.cdf(np.arange(0, 20), 1.0)


# --------------------------------------------------------------------------- helpers

def wilson_ci(k, n, alpha: float = ALPHA):
    """Wilson score interval (assumes independent trials - shown for reference only)."""
    k, n = np.asarray(k, dtype=float), np.asarray(n, dtype=float)
    z = norm.ppf(1 - alpha / 2)
    with np.errstate(invalid="ignore", divide="ignore"):
        p = k / n
        den = 1 + z * z / n
        centre = (p + z * z / (2 * n)) / den
        half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return centre - half, centre + half


def bh_adjust(pvals) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (false discovery rate)."""
    pvals = np.asarray(pvals, dtype=float)
    return multipletests(pvals, alpha=ALPHA, method="fdr_bh")[1] if len(pvals) else pvals


def poisson_weights(rng: np.random.Generator, n_users: int, b: int) -> np.ndarray:
    """(n_users x b) float32 Poisson(1) weights by inverse CDF."""
    u = rng.random((n_users, b), dtype=np.float32)
    return np.searchsorted(_POISSON1_CDF, u, side="right").astype(np.float32)


# --------------------------------------------------------------------------- definitions

@dataclass
class Base:
    """Sparse per-user count vector: values at user positions idx (0..n_users-1)."""
    name: str
    idx: np.ndarray
    val: np.ndarray

    def __post_init__(self):
        self.idx = np.asarray(self.idx, dtype=np.int64)
        self.val = np.asarray(self.val, dtype=np.float64)
        if len(self.idx) != len(self.val):
            raise ValueError(f"{self.name}: idx and val lengths differ")
        if len(np.unique(self.idx)) != len(self.idx):
            raise ValueError(f"{self.name}: duplicate user positions")
        if np.any(self.val < 0):
            raise ValueError(f"{self.name}: negative counts")


@dataclass
class Rate:
    """numerator / denominator, each a linear combination {base_name: coefficient}."""
    name: str
    num: dict
    den: dict
    group: str = ""
    meta: dict = field(default_factory=dict)


def build_matrix(bases: list[Base], n_users: int) -> tuple[sparse.csr_matrix, dict, np.ndarray]:
    """Sparse float32 matrix (n_bases x n_users), name->row map, exact float64 totals."""
    names = [b.name for b in bases]
    if len(set(names)) != len(names):
        raise ValueError("duplicate base names")
    rows = (np.concatenate([np.full(len(b.idx), i, dtype=np.int32) for i, b in enumerate(bases)])
            if bases else np.array([], dtype=np.int32))
    cols = np.concatenate([b.idx for b in bases]).astype(np.int32) if bases else np.array([], dtype=np.int32)
    if len(cols) and (cols.min() < 0 or cols.max() >= n_users):
        raise ValueError("user positions out of range")
    vals = np.concatenate([b.val for b in bases]) if bases else np.array([])
    m = sparse.csr_matrix((vals.astype(np.float32), (rows, cols)), shape=(len(bases), n_users))
    m.sum_duplicates()
    m.sort_indices()          # canonical layout: results never depend on the order counts were supplied in
    totals = np.array([b.val.sum() for b in bases], dtype=np.float64)
    return m, {n: i for i, n in enumerate(names)}, totals


def _coef_matrix(rates: list[Rate], index: dict, part: str) -> np.ndarray:
    c = np.zeros((len(index), len(rates)))
    for j, r in enumerate(rates):
        for name, coef in getattr(r, part).items():
            if name not in index:
                raise KeyError(f"rate {r.name}: unknown base '{name}'")
            c[index[name], j] += coef
    return c


def rate_values(sums: np.ndarray, rates: list[Rate], index: dict) -> np.ndarray:
    """Rates from base sums. sums: (n_bases,) or (B, n_bases) -> (k,) or (B, k)."""
    cn, cd = _coef_matrix(rates, index, "num"), _coef_matrix(rates, index, "den")
    with np.errstate(invalid="ignore", divide="ignore"):
        return (sums @ cn) / (sums @ cd)


def check_rates(matrix: sparse.csr_matrix, rates: list[Rate], index: dict) -> list[str]:
    """Per-user validity of every rate: numerator >= 0 and denominator - numerator >= 0."""
    bad = []
    cn, cd = _coef_matrix(rates, index, "num"), _coef_matrix(rates, index, "den")
    m64 = matrix.astype(np.float64)
    for j, r in enumerate(rates):
        num = sparse.csr_matrix(cn[:, j]) @ m64
        gap = sparse.csr_matrix(cd[:, j] - cn[:, j]) @ m64
        if (num.min() if num.nnz else 0) < -1e-9 or (gap.min() if gap.nnz else 0) < -1e-9:
            bad.append(r.name)
        elif ((sparse.csr_matrix(cd[:, j]) @ m64).sum()) <= 0:
            bad.append(r.name + " (zero denominator)")
    return bad


def bootstrap_base_sums(matrix: sparse.csr_matrix, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
                        batch: int = DEFAULT_BATCH, weights_one: bool = False) -> np.ndarray:
    """(B x n_bases) user-weighted sums. weights_one=True: all weights 1 (a reproduction check)."""
    n_users = matrix.shape[1]
    rng = np.random.default_rng(seed)
    out = np.empty((B, matrix.shape[0]))
    done = 0
    while done < B:
        b = min(batch, B - done)
        w = np.ones((n_users, b), dtype=np.float32) if weights_one else poisson_weights(rng, n_users, b)
        out[done:done + b] = (matrix @ w).T
        done += b
    return out


# --------------------------------------------------------------------------- summaries

def summarise_rates(rates: list[Rate], point_sums: np.ndarray, boot: np.ndarray, index: dict,
                    alpha: float = ALPHA) -> pd.DataFrame:
    """point_sums: exact (n_bases,) totals; boot: (B x k) bootstrap rates."""
    p = rate_values(point_sums, rates, index)
    k = point_sums @ _coef_matrix(rates, index, "num")
    n = point_sums @ _coef_matrix(rates, index, "den")
    lo, hi = np.nanpercentile(boot, [100 * alpha / 2, 100 * (1 - alpha / 2)], axis=0)
    se = np.nanstd(boot, axis=0, ddof=1)
    w_lo, w_hi = wilson_ci(k, n, alpha)
    with np.errstate(invalid="ignore", divide="ignore"):
        deff = se ** 2 / (p * (1 - p) / n)
    return pd.DataFrame({
        "metric": [r.name for r in rates], "group": [r.group for r in rates],
        "numerator": k, "denominator": n, "rate": p,
        "ci_low": lo, "ci_high": hi, "se_boot": se,
        "wilson_low": w_lo, "wilson_high": w_hi, "design_effect": deff,
        "nan_replicates": np.isnan(boot).sum(axis=0),
    })


def compare_rates(pairs: list[tuple[str, int, int, str]], point: np.ndarray, boot: np.ndarray,
                  alpha: float = ALPHA, practical_pp: float = PRACTICAL_PP) -> pd.DataFrame:
    """Differences rate[a] - rate[b] for (label, a, b, family): Wald z with bootstrap SE
    (two-sided), percentile CI, Benjamini-Hochberg within family, F7 'meaningful' flag."""
    rows = []
    for label, a, b, family in pairs:
        d_boot = boot[:, a] - boot[:, b]
        diff = point[a] - point[b]
        se = np.nanstd(d_boot, ddof=1)
        z = diff / se if se > 0 else np.nan
        pval = 2 * norm.sf(abs(z)) if np.isfinite(z) else np.nan
        lo, hi = np.nanpercentile(d_boot, [100 * alpha / 2, 100 * (1 - alpha / 2)])
        rows.append({"comparison": label, "family": family, "rate_a": point[a], "rate_b": point[b],
                     "diff_pp": 100 * diff, "diff_ci_low_pp": 100 * lo, "diff_ci_high_pp": 100 * hi,
                     "se_boot_pp": 100 * se, "z": z, "p_value": pval})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["p_adj_bh"] = np.nan
    for _, idx in df.groupby("family").groups.items():
        sub = df.loc[idx, "p_value"]
        ok = sub.notna()
        if ok.any():
            df.loc[sub[ok].index, "p_adj_bh"] = bh_adjust(sub[ok].to_numpy())
    df["significant_bh"] = df["p_adj_bh"] < alpha
    df["meaningful"] = df["significant_bh"] & (df["diff_pp"].abs() >= practical_pp)
    return df


# --------------------------------------------------------------------------- convenience

@dataclass
class RatioSpec:
    """Simple rate: one numerator and one denominator vector over the same users."""
    name: str
    idx: np.ndarray
    num: np.ndarray
    den: np.ndarray
    group: str = ""

    def __post_init__(self):
        self.idx = np.asarray(self.idx, dtype=np.int64)
        self.num = np.asarray(self.num, dtype=np.float64)
        self.den = np.asarray(self.den, dtype=np.float64)
        if not (len(self.idx) == len(self.num) == len(self.den)):
            raise ValueError(f"{self.name}: idx, num and den must have equal length")
        if len(np.unique(self.idx)) != len(self.idx):
            raise ValueError(f"{self.name}: duplicate user positions")
        if np.any(self.num < 0) or np.any(self.den <= 0) or np.any(self.num > self.den):
            raise ValueError(f"{self.name}: need 0 <= numerator <= denominator and denominator > 0")


def _from_specs(specs: list[RatioSpec]):
    bases, rates = [], []
    for s in specs:
        bases += [Base(f"{s.name}#num", s.idx, s.num), Base(f"{s.name}#den", s.idx, s.den)]
        rates.append(Rate(s.name, {f"{s.name}#num": 1}, {f"{s.name}#den": 1}, s.group))
    return bases, rates


def point_estimates(specs: list[RatioSpec]) -> np.ndarray:
    return np.array([s.num.sum() / s.den.sum() for s in specs])


def poisson_bootstrap(specs: list[RatioSpec], n_users: int, B: int = DEFAULT_B, seed: int = DEFAULT_SEED,
                      batch: int = DEFAULT_BATCH, weights_one: bool = False) -> np.ndarray:
    """(B x k) bootstrap rates for simple specs (same engine as the full analysis)."""
    bases, rates = _from_specs(specs)
    m, index, _ = build_matrix(bases, n_users)
    sums = bootstrap_base_sums(m, B, seed, batch, weights_one)
    return rate_values(sums, rates, index)


def summarise_specs(specs: list[RatioSpec], boot: np.ndarray, alpha: float = ALPHA) -> pd.DataFrame:
    bases, rates = _from_specs(specs)
    _, index, totals = build_matrix(bases, int(max(s.idx.max() for s in specs) + 1))
    return summarise_rates(rates, totals, boot, index, alpha)
