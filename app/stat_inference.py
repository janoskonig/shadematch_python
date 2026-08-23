"""
Inferential statistics for the /stat dashboard.

The rest of the dashboard is descriptive: counts, means, medians and scatter
plots computed straight over ``mixing_attempts``. That leaves three recurring
problems, which this module exists to fix:

1. **No uncertainty.** A mean with no n, no spread and no interval cannot be
   argued with. Every number here ships with an interval (Wilson for
   proportions, distribution-free order statistics for medians, percentile
   bootstrap for everything else) and an explicit n.

2. **Pseudo-replication.** Attempts are not independent: a handful of players
   contribute hundreds of rounds each, so any attempt-pooled average is mostly
   a statement about the most prolific players. Every headline quantity is
   therefore reported twice — attempt-pooled *and* clustered by player
   (per-player statistic first, then aggregated across players, with the
   bootstrap resampling players rather than attempts).

3. **Selection effects.** "How many attempts until ΔE < 2?" silently drops the
   player/colour pairs that never got there, which biases the answer downward.
   That question is survival data, so it is answered with Kaplan–Meier and a
   reach rate instead of a complete-case mean.

Everything is plain numpy/pandas: no scipy, no statsmodels, no matplotlib, so
the endpoint stays cheap enough to serve on page load. The normal quantile is a
constant (95%), the normal CDF comes from ``math.erf`` and the binomial CDF from
``math.lgamma``.

Conventions
-----------
* ΔE means CIEDE2000 (ΔE₀₀) throughout, as stored in ``final_delta_e``.
* Reference thresholds are Paravina et al. (2009/2015) for ΔE₀₀:
  perceptibility 0.7 (light) / 1.2 (dark), acceptability 2.1 / 3.1.
* "Experience" is a player's own round counter (1st, 2nd, … round ever played),
  and learning slopes are per *doubling* of it, matching the headline results
  tab.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
Z95 = 1.959963984540054

#: Attempts longer than this are treated as "walked away from the screen" and
#: excluded from every timing statistic. Kept identical to the descriptive
#: endpoints so both halves of the page describe the same attempt set.
DURATION_CAP_SEC = 300.0

#: A ΔE this small is an exact recipe hit (float noise only).
PERFECT_DELTA_E = 0.01

#: Paravina et al. ΔE₀₀ thresholds, used as the reporting grid. The key is the
#: stable identifier the dashboard labels from; the text is the fallback.
DELTA_E_THRESHOLDS: Tuple[Tuple[str, str, float], ...] = (
    ('perfect', 'perfect match (ΔE₀₀ ≤ 0.01)', PERFECT_DELTA_E),
    ('pt_light', 'perceptibility, light (≤ 0.7)', 0.7),
    ('pt_dark', 'perceptibility, dark (≤ 1.2)', 1.2),
    ('at_light', 'acceptability, light (≤ 2.1)', 2.1),
    ('at_dark', 'acceptability, dark (≤ 3.1)', 3.1),
)

#: Players with fewer rounds than this cannot support a within-player slope.
MIN_ATTEMPTS_FOR_SLOPE = 10

#: Experience axis for the speed–accuracy curves. Doubling-width bins, because
#: practice effects are logarithmic in trials: equal-width bins would spend most
#: of the axis on a region where nothing changes.
EXPERIENCE_BASELINE_ROUNDS = 4
EXPERIENCE_BINS: Tuple[Tuple[int, Optional[int], str], ...] = (
    (1, 4, '1–4'),
    (5, 8, '5–8'),
    (9, 16, '9–16'),
    (17, 32, '17–32'),
    (33, 64, '33–64'),
    (65, 128, '65–128'),
    (129, None, '129+'),
)

#: Bootstrap replicates. 2000 is enough for a 95% percentile interval and keeps
#: the whole endpoint well under a second on the current data volume.
N_BOOT = 2000
BOOT_SEED = 20260822


# --------------------------------------------------------------------------- #
# tiny distribution helpers (no scipy)
# --------------------------------------------------------------------------- #
def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(float(x) / math.sqrt(2.0)))


def _two_sided_p(z: float) -> float:
    """Two-sided p-value for a standard-normal test statistic."""
    if not np.isfinite(z):
        return float('nan')
    return float(min(1.0, 2.0 * (1.0 - _norm_cdf(abs(float(z))))))


def _log_binom_pmf(k: int, n: int, p: float = 0.5) -> float:
    if k < 0 or k > n:
        return float('-inf')
    return (
        math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
        + k * math.log(p) + (n - k) * math.log1p(-p)
    )


def _binom_cdf_half(k: int, n: int) -> float:
    """P(X ≤ k) for X ~ Binomial(n, 0.5), summed in log space."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return float(sum(math.exp(_log_binom_pmf(i, n)) for i in range(0, k + 1)))


def _clean(values: Iterable[float]) -> np.ndarray:
    """Finite float array, NaN/inf dropped."""
    arr = np.asarray(list(values), dtype=float) if not isinstance(values, np.ndarray) \
        else np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    return arr[np.isfinite(arr)]


def _f(x: Any) -> Optional[float]:
    """JSON-safe float (NaN/inf → None)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


# --------------------------------------------------------------------------- #
# interval estimators
# --------------------------------------------------------------------------- #
def wilson_ci(k: int, n: int, z: float = Z95) -> Tuple[Optional[float], Optional[float]]:
    """Wilson score interval for a proportion.

    Preferred over the Wald interval because several of the rates on this page
    sit near 0 or 1 (perfect-match rate, threshold attainment), where Wald
    intervals run outside [0, 1] and undercover badly.
    """
    if n <= 0:
        return (None, None)
    k = float(k)
    n = float(n)
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    lo = 0.0 if k <= 0 else max(0.0, centre - half)     # exact at the edges;
    hi = 1.0 if k >= n else min(1.0, centre + half)     # rounding can leave 1e-17
    return (lo, hi)


def median_ci(values: Sequence[float], conf: float = 0.95) -> Tuple[Optional[float], Optional[float]]:
    """Distribution-free CI for the median from binomial order statistics.

    Makes no assumption about the shape of the distribution, which matters here:
    ΔE is zero-inflated and heavily right-skewed, so a normal-theory interval
    around the median would be wrong in both directions.
    """
    v = np.sort(_clean(values))
    n = v.size
    if n == 0:
        return (None, None)
    if n < 6:  # no non-trivial distribution-free interval exists this small
        return (float(v[0]), float(v[-1]))
    alpha = (1.0 - conf) / 2.0
    # Coverage of [v[k], v[n-1-k]] (0-based) is 1 − 2·P(Bin(n, ½) ≤ k), so take
    # the largest k that still satisfies P ≤ alpha. The CDF is accumulated in a
    # single pass — recomputing it per k is quadratic and n runs into thousands.
    best = -1
    cum = math.exp(_log_binom_pmf(0, n))
    k = 0
    while cum <= alpha and k < n // 2:
        best = k
        k += 1
        cum += math.exp(_log_binom_pmf(k, n))
    k = max(best, 0)
    return (float(v[k]), float(v[n - 1 - k]))


def bootstrap_ci(
    values: Sequence[float],
    stat_fn: Callable[[np.ndarray], float] = np.mean,
    *,
    n_boot: int = N_BOOT,
    conf: float = 0.95,
    seed: int = BOOT_SEED,
) -> Tuple[Optional[float], Optional[float]]:
    """Percentile bootstrap CI for an arbitrary statistic.

    The seed is fixed so the dashboard does not wobble between reloads; the
    interval is a genuine resampling interval, not a normal approximation.
    """
    v = _clean(values)
    n = v.size
    if n < 3:
        return (None, None)
    rng = np.random.default_rng(seed)
    # Drawn in blocks: one (n_boot × n) index matrix would be tens of MB for the
    # attempt-level samples, and this endpoint runs on a small web dyno.
    block = max(1, min(n_boot, 2_000_000 // max(n, 1)))
    chunks: List[np.ndarray] = []
    drawn = 0
    while drawn < n_boot:
        size = min(block, n_boot - drawn)
        idx = rng.integers(0, n, size=(size, n))
        sample = v[idx]
        try:  # np.mean / np.median reduce the whole block in one call
            block_stats = np.asarray(stat_fn(sample, axis=1), dtype=float)
        except TypeError:
            block_stats = np.array([stat_fn(row) for row in sample], dtype=float)
        chunks.append(block_stats)
        drawn += size
    stats = np.concatenate(chunks) if chunks else np.array([])
    stats = stats[np.isfinite(stats)]
    if stats.size == 0:
        return (None, None)
    lo, hi = np.percentile(stats, [100 * (1 - conf) / 2, 100 * (1 + conf) / 2])
    return (float(lo), float(hi))


def cluster_bootstrap_ci(
    groups: Sequence[Any],
    values: Sequence[float],
    stat_fn: Callable[[List[np.ndarray]], float],
    *,
    n_boot: int = N_BOOT,
    conf: float = 0.95,
    seed: int = BOOT_SEED,
) -> Tuple[Optional[float], Optional[float]]:
    """Percentile bootstrap that resamples *clusters* (players), not rows.

    This is the correction for pseudo-replication: rounds within one player are
    correlated, so resampling rounds pretends there is far more independent
    information than the data hold.
    """
    df = pd.DataFrame({'g': list(groups), 'v': list(values)})
    df = df[np.isfinite(pd.to_numeric(df['v'], errors='coerce'))]
    if df.empty:
        return (None, None)
    by_group = [g['v'].to_numpy(dtype=float) for _, g in df.groupby('g', sort=True)]
    n_groups = len(by_group)
    if n_groups < 3:
        return (None, None)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n_groups, size=(n_boot, n_groups))
    stats = []
    for row in idx:
        try:
            s = stat_fn([by_group[i] for i in row])
        except Exception:
            continue
        if np.isfinite(s):
            stats.append(float(s))
    if not stats:
        return (None, None)
    lo, hi = np.percentile(np.asarray(stats, dtype=float),
                           [100 * (1 - conf) / 2, 100 * (1 + conf) / 2])
    return (float(lo), float(hi))


# --------------------------------------------------------------------------- #
# descriptive block with uncertainty
# --------------------------------------------------------------------------- #
def describe(values: Sequence[float], *, label: str = '', with_ci: bool = True) -> Dict[str, Any]:
    """Location, spread, shape and intervals for one metric.

    Reports the median as the headline (the distributions on this page are
    skewed enough that the mean is not a typical value) but keeps the mean so
    the skew is visible rather than hidden.
    """
    v = _clean(values)
    n = int(v.size)
    out: Dict[str, Any] = {'label': label, 'n': n}
    if n == 0:
        return out
    q1, med, q3, p90 = (float(x) for x in np.percentile(v, [25, 50, 75, 90]))
    mean = float(np.mean(v))
    sd = float(np.std(v, ddof=1)) if n > 1 else float('nan')
    out.update({
        'mean': _f(mean),
        'sd': _f(sd),
        'median': _f(med),
        'q1': _f(q1),
        'q3': _f(q3),
        'iqr': _f(q3 - q1),
        'p90': _f(p90),
        'min': _f(np.min(v)),
        'max': _f(np.max(v)),
        # Fisher–Pearson skew; > 1 means "the mean is not the typical value".
        'skew': _f(float(np.mean(((v - mean) / sd) ** 3))) if n > 2 and sd > 0 else None,
    })
    if with_ci:
        lo, hi = median_ci(v)
        out['median_ci_low'], out['median_ci_high'] = _f(lo), _f(hi)
        blo, bhi = bootstrap_ci(v, np.mean)
        out['mean_ci_low'], out['mean_ci_high'] = _f(blo), _f(bhi)
    return out


# --------------------------------------------------------------------------- #
# association
# --------------------------------------------------------------------------- #
def _pearson(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    if x.size < 3 or y.size != x.size:
        return None
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    r = float(np.corrcoef(x, y)[0, 1])
    return r if np.isfinite(r) else None


def _rank(a: np.ndarray) -> np.ndarray:
    """Average ranks, ties shared (the Spearman convention)."""
    order = np.argsort(a, kind='mergesort')
    ranks = np.empty(a.size, dtype=float)
    ranks[order] = np.arange(1, a.size + 1, dtype=float)
    # average tied groups
    sorted_a = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sorted_a[j + 1] == sorted_a[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = np.mean(ranks[order[i:j + 1]])
        i = j + 1
    return ranks


def correlation(
    x: Sequence[float],
    y: Sequence[float],
    *,
    x_label: str,
    y_label: str,
    groups: Optional[Sequence[Any]] = None,
) -> Dict[str, Any]:
    """Pearson **and** Spearman with Fisher-z intervals, p-values and n.

    Spearman is the one to read: ΔE and duration are both heavy-tailed, and a
    handful of long rounds can move Pearson's r on their own. When ``groups``
    (player ids) are supplied, a cluster-aware Spearman is added — the median of
    the within-player correlations — which answers "does this hold inside a
    player?" rather than "do fast players differ from slow players?".
    """
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = int(xa.size)
    out: Dict[str, Any] = {'x': x_label, 'y': y_label, 'n': n}
    if n < 4:
        return out
    r_p = _pearson(xa, ya)
    r_s = _pearson(_rank(xa), _rank(ya))
    for key, r in (('pearson', r_p), ('spearman', r_s)):
        if r is None:
            continue
        out[key] = _f(r)
        r_c = min(max(r, -0.999999), 0.999999)
        z = math.atanh(r_c)
        se = 1.0 / math.sqrt(n - 3) if n > 3 else float('nan')
        if np.isfinite(se):
            out[f'{key}_ci_low'] = _f(math.tanh(z - Z95 * se))
            out[f'{key}_ci_high'] = _f(math.tanh(z + Z95 * se))
            out[f'{key}_p'] = _f(_two_sided_p(z / se))
    if groups is not None:
        g = np.asarray(list(groups), dtype=object)[mask]
        within = []
        for gid in pd.unique(g):
            sel = g == gid
            if int(sel.sum()) < 8:
                continue
            r = _pearson(_rank(xa[sel]), _rank(ya[sel]))
            if r is not None:
                within.append(r)
        if len(within) >= 3:
            arr = np.asarray(within, dtype=float)
            lo, hi = bootstrap_ci(arr, np.median)
            out['within_player_spearman_median'] = _f(float(np.median(arr)))
            out['within_player_ci_low'] = _f(lo)
            out['within_player_ci_high'] = _f(hi)
            out['within_player_n'] = len(within)
    return out


# --------------------------------------------------------------------------- #
# effect sizes
# --------------------------------------------------------------------------- #
def cliffs_delta(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """P(A > B) − P(A < B): a rank effect size that needs no distribution.

    Reported alongside the median shift because "how far apart" and "how
    separated" are different questions, and only the second one survives the
    skew in these distributions.
    """
    xa, xb = _clean(a), _clean(b)
    if xa.size == 0 or xb.size == 0:
        return None
    # O(n log n) via ranks of the pooled sample rather than the O(n·m) pairing.
    pooled = np.concatenate([xa, xb])
    ranks = _rank(pooled)
    r_a = float(np.sum(ranks[:xa.size]))
    n, m = xa.size, xb.size
    u = r_a - n * (n + 1) / 2.0            # Mann–Whitney U for A
    return _f(2.0 * u / (n * m) - 1.0)


def hodges_lehmann(a: Sequence[float], b: Sequence[float], *, max_pairs: int = 4_000_000,
                   seed: int = BOOT_SEED) -> Optional[float]:
    """Median of all pairwise differences a − b (robust location shift)."""
    xa, xb = _clean(a), _clean(b)
    if xa.size == 0 or xb.size == 0:
        return None
    if xa.size * xb.size <= max_pairs:
        diffs = xa[:, None] - xb[None, :]
        return _f(float(np.median(diffs)))
    rng = np.random.default_rng(seed)
    ia = rng.integers(0, xa.size, size=max_pairs)
    ib = rng.integers(0, xb.size, size=max_pairs)
    return _f(float(np.median(xa[ia] - xb[ib])))


def sign_test(values: Sequence[float]) -> Dict[str, Any]:
    """Exact two-sided sign test for "is the typical value below zero?".

    Used on the per-player learning slopes: it asks only whether more players
    improve than not, so one player with a wild slope cannot carry the result.
    """
    v = _clean(values)
    v = v[v != 0]
    n = int(v.size)
    if n == 0:
        return {'n': 0}
    k = int(np.sum(v < 0))
    p = min(1.0, 2.0 * _binom_cdf_half(min(k, n - k), n))
    return {'n': n, 'n_negative': k, 'share_negative': _f(k / n), 'p': _f(p)}


# --------------------------------------------------------------------------- #
# variance decomposition
# --------------------------------------------------------------------------- #
def icc_oneway(values: Sequence[float], groups: Sequence[Any]) -> Dict[str, Any]:
    """One-way random-effects ICC: share of variance that sits between groups.

    Answers the question the scatter plots cannot: is the spread in ΔE about
    *who is playing* or about *which colour they drew*? ICC near 0 means the
    grouping explains nothing; near 1 means it explains almost everything.
    """
    df = pd.DataFrame({'g': list(groups), 'v': pd.to_numeric(pd.Series(list(values)), errors='coerce')})
    df = df.dropna()
    df = df[np.isfinite(df['v'])]
    k_groups = df['g'].nunique()
    n = len(df)
    if n < 10 or k_groups < 2 or k_groups >= n:
        return {'n': int(n), 'n_groups': int(k_groups)}
    grand = float(df['v'].mean())
    agg = df.groupby('g')['v'].agg(['count', 'mean'])
    ss_between = float(np.sum(agg['count'] * (agg['mean'] - grand) ** 2))
    ss_within = float(np.sum((df['v'] - df['g'].map(agg['mean'])) ** 2))
    df_b = k_groups - 1
    df_w = n - k_groups
    ms_b = ss_between / df_b
    ms_w = ss_within / df_w if df_w > 0 else float('nan')
    # Unequal group sizes → the usual n0 correction.
    counts = agg['count'].to_numpy(dtype=float)
    n0 = (counts.sum() - np.sum(counts ** 2) / counts.sum()) / (k_groups - 1)
    if not np.isfinite(ms_w) or n0 <= 0:
        return {'n': int(n), 'n_groups': int(k_groups)}
    var_between = max(0.0, (ms_b - ms_w) / n0)
    var_within = max(0.0, ms_w)
    total = var_between + var_within
    return {
        'n': int(n),
        'n_groups': int(k_groups),
        'var_between': _f(var_between),
        'var_within': _f(var_within),
        'icc': _f(var_between / total) if total > 0 else None,
    }


# --------------------------------------------------------------------------- #
# survival (first crossing of a ΔE threshold)
# --------------------------------------------------------------------------- #
def kaplan_meier(times: Sequence[int], events: Sequence[bool]) -> Dict[str, Any]:
    """Kaplan–Meier estimate with Greenwood standard errors.

    ``times`` is the attempt number at which the threshold was first crossed
    (event) or the last attempt observed (censored). Dropping the censored rows
    — which is what a complete-case mean does — throws away exactly the players
    who found the colour hardest, so the naive answer is biased optimistic.
    """
    t = np.asarray(list(times), dtype=float)
    e = np.asarray(list(events), dtype=bool)
    mask = np.isfinite(t)
    t, e = t[mask], e[mask]
    if t.size == 0:
        return {'n': 0, 'curve': []}
    order = np.argsort(t, kind='mergesort')
    t, e = t[order], e[order]
    n_at_risk = t.size
    surv = 1.0
    greenwood = 0.0
    curve: List[Dict[str, Any]] = []
    for tv in np.unique(t):
        at_risk = int(np.sum(t >= tv))
        d = int(np.sum((t == tv) & e))
        c = int(np.sum((t == tv) & ~e))
        if at_risk > 0 and d > 0:
            surv *= (1.0 - d / at_risk)
            greenwood += d / (at_risk * (at_risk - d)) if at_risk > d else 0.0
        se = surv * math.sqrt(greenwood) if greenwood > 0 else 0.0
        curve.append({
            'attempt_no': int(tv),
            'at_risk': at_risk,
            'crossed': d,
            'censored': c,
            'survival': _f(surv),
            'reached': _f(1.0 - surv),
            'reached_ci_low': _f(max(0.0, 1.0 - min(1.0, surv + Z95 * se))),
            'reached_ci_high': _f(min(1.0, 1.0 - max(0.0, surv - Z95 * se))),
        })
    median = None
    for row in curve:
        if row['survival'] is not None and row['survival'] <= 0.5:
            median = row['attempt_no']
            break
    naive = t[e]
    return {
        'n': int(t.size),
        'n_events': int(np.sum(e)),
        'n_censored': int(np.sum(~e)),
        'median_attempts_to_cross': median,
        'final_reached': curve[-1]['reached'] if curve else None,
        'naive_complete_case_mean': _f(float(np.mean(naive))) if naive.size else None,
        'naive_complete_case_median': _f(float(np.median(naive))) if naive.size else None,
        'curve': curve[:20],
    }


# --------------------------------------------------------------------------- #
# logistic threshold (shared shape with app.calibration._logistic_threshold)
# --------------------------------------------------------------------------- #
def logistic_threshold(x: Sequence[float], y: Sequence[float]) -> Optional[Tuple[float, float]]:
    """ΔE at the 50% crossing of P(y=1) ~ logistic(b0 + b1·ΔE), via IRLS.

    Same estimator as the calibration instrument uses, so the in-game number
    and the calibration number are directly comparable.
    """
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    if xa.size < 6 or ya.min() == ya.max():
        return None
    X = np.column_stack([np.ones_like(xa), xa])
    beta = np.zeros(2)
    for _ in range(50):
        eta = X @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        W = np.clip(p * (1 - p), 1e-6, None)
        try:
            step = np.linalg.solve(X.T @ (X * W[:, None]) + 1e-6 * np.eye(2), X.T @ (ya - p))
        except np.linalg.LinAlgError:
            return None
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break
    b0, b1 = beta
    if b1 <= 1e-6:
        return None
    return float(-b0 / b1), float(b1)


def logistic_threshold_ci(
    x: Sequence[float], y: Sequence[float], *, n_boot: int = 400, seed: int = BOOT_SEED
) -> Dict[str, Any]:
    """Point estimate + percentile-bootstrap CI for a 50% crossing."""
    fit = logistic_threshold(x, y)
    out: Dict[str, Any] = {'threshold': _f(fit[0]) if fit else None,
                           'slope': _f(fit[1]) if fit else None,
                           'n': int(len(list(x)))}
    if not fit:
        return out
    xa = np.asarray(list(x), dtype=float)
    ya = np.asarray(list(y), dtype=float)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(n_boot):
        idx = rng.integers(0, xa.size, size=xa.size)
        f = logistic_threshold(xa[idx], ya[idx])
        if f and np.isfinite(f[0]):
            draws.append(f[0])
    if len(draws) >= 20:
        lo, hi = np.percentile(np.asarray(draws, dtype=float), [2.5, 97.5])
        out['ci_low'], out['ci_high'] = _f(lo), _f(hi)
    return out


# --------------------------------------------------------------------------- #
# data loading
# --------------------------------------------------------------------------- #
_ATTEMPTS_SQL = """
    SELECT
      ma.attempt_uuid,
      ma.user_id,
      ma.target_color_id,
      COALESCE(tc.name, '(unknown)') AS target_name,
      lower(COALESCE(tc.color_type, 'unknown')) AS color_type,
      ma.final_delta_e,
      ma.initial_delta_e,
      ma.duration_sec,
      ma.num_steps,
      ma.attempt_started_server_ts
    FROM mixing_attempts ma
    LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
"""

_SKIP_SQL = """
    SELECT
      ms.user_id,
      ms.skip_perception,
      ms.delta_e
    FROM mixing_sessions ms
    WHERE ms.skipped IS TRUE
      AND ms.skip_perception IS NOT NULL
      AND ms.delta_e IS NOT NULL
"""


def _load_attempts() -> pd.DataFrame:
    with db.engine.connect() as conn:
        df = pd.read_sql(text(_ATTEMPTS_SQL), conn)
    if 'attempt_started_server_ts' in df.columns:
        df['attempt_started_server_ts'] = pd.to_datetime(df['attempt_started_server_ts'], utc=True)
    return df


def _load_skips() -> pd.DataFrame:
    with db.engine.connect() as conn:
        return pd.read_sql(text(_SKIP_SQL), conn)


def _with_indices(df: pd.DataFrame) -> pd.DataFrame:
    """Add per-player round counter and per-(player, colour) attempt number."""
    out = df[df['user_id'].notna()].copy()
    if out.empty:
        out['trial_index'] = pd.Series(dtype='int64')
        out['attempt_no'] = pd.Series(dtype='int64')
        return out
    out = out.sort_values(['user_id', 'attempt_started_server_ts', 'attempt_uuid'],
                          na_position='last')
    out['trial_index'] = out.groupby('user_id', sort=False).cumcount() + 1
    out['attempt_no'] = out.groupby(['user_id', 'target_color_id'], sort=False).cumcount() + 1
    return out


# --------------------------------------------------------------------------- #
# blocks
# --------------------------------------------------------------------------- #
def _clustered_block(df: pd.DataFrame, col: str, *, label: str) -> Dict[str, Any]:
    """Attempt-pooled and player-clustered summary of one metric side by side.

    The gap between the two rows *is* the pseudo-replication warning: when they
    disagree, the pooled number is describing the heaviest players.
    """
    vals = df[col].to_numpy(dtype=float)
    pooled = describe(vals, label=label)
    per_user = (df[['user_id', col]].dropna()
                .groupby('user_id')[col]
                .agg(['count', 'median'])
                .rename(columns={'count': 'n', 'median': 'value'}))
    clustered: Dict[str, Any] = {'n_players': int(len(per_user))}
    if len(per_user):
        med = per_user['value'].to_numpy(dtype=float)
        clustered.update({
            'median_of_player_medians': _f(float(np.median(med))),
            'q1': _f(float(np.percentile(med, 25))),
            'q3': _f(float(np.percentile(med, 75))),
            'min': _f(float(np.min(med))),
            'max': _f(float(np.max(med))),
        })
        lo, hi = bootstrap_ci(med, np.median)
        clustered['ci_low'], clustered['ci_high'] = _f(lo), _f(hi)
        # How lopsided is the contribution? (share of rounds from the top player)
        counts = per_user['n'].to_numpy(dtype=float)
        if counts.sum() > 0:
            clustered['top_player_share_of_rounds'] = _f(float(counts.max() / counts.sum()))
    return {'pooled': pooled, 'clustered': clustered}


def _threshold_block(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Attainment of each ΔE₀₀ threshold, pooled and clustered by player."""
    rows: List[Dict[str, Any]] = []
    de = df['final_delta_e'].to_numpy(dtype=float)
    de = de[np.isfinite(de)]
    n = de.size
    if n == 0:
        return rows
    sub = df[['user_id', 'final_delta_e']].dropna()
    for key, label, thr in DELTA_E_THRESHOLDS:
        k = int(np.sum(de <= thr))
        lo, hi = wilson_ci(k, n)
        row: Dict[str, Any] = {
            'key': key,
            'label': label,
            'threshold': thr,
            'n': int(n),
            'k': k,
            'rate': _f(k / n),
            'ci_low': _f(lo),
            'ci_high': _f(hi),
        }
        if not sub.empty:
            per_user = sub.groupby('user_id')['final_delta_e'].apply(
                lambda s, t=thr: float(np.mean(s.to_numpy(dtype=float) <= t))
            ).to_numpy(dtype=float)
            if per_user.size:
                row['player_median_rate'] = _f(float(np.median(per_user)))
                clo, chi = bootstrap_ci(per_user, np.median)
                row['player_ci_low'], row['player_ci_high'] = _f(clo), _f(chi)
                row['n_players'] = int(per_user.size)
        rows.append(row)
    return rows


def _speed_accuracy_block(analysed: pd.DataFrame, timed: pd.DataFrame) -> Dict[str, Any]:
    """Accuracy and time on ONE indexed axis, as experience accumulates.

    This is the headline finding of the study, so it gets the estimator that can
    actually carry it. Three things make it different from a plain "metric by
    round number" chart:

    * **Within player.** Each player is indexed to *their own* first
      ``EXPERIENCE_BASELINE_ROUNDS`` rounds (= 100%), so the curve cannot be
      produced by persistent players simply being better than one-off visitors.
    * **One scale.** Both outcomes become percentages of their own baseline, so
      accuracy and seconds share a single y-axis. Plotting ΔE and seconds against
      two y-scales would invent whatever relationship the scaling implied.
    * **Ratios of (1 + ΔE).** A player whose first rounds are exact hits has a
      zero baseline; the shift keeps them in the sample instead of dropping the
      strongest starters. Time uses the plain ratio.

    ``n_players`` per bin is returned and shown: the far end of the experience
    axis rests on very few players, and the chart has to admit that.
    """
    specs = (('accuracy', 'final_delta_e', analysed, True),
             ('duration', 'duration_sec', timed, False))
    bins: List[Dict[str, Any]] = [
        {'label': label, 'from': lo, 'to': hi} for lo, hi, label in EXPERIENCE_BINS
    ]
    out: Dict[str, Any] = {
        'baseline_rounds': EXPERIENCE_BASELINE_ROUNDS,
        'bins': bins,
        'shifted_metrics': ['accuracy'],
    }
    for key, col, df, shift in specs:
        sub = df[['user_id', 'trial_index', col]].dropna()
        sub = sub[np.isfinite(sub[col].to_numpy(dtype=float))]
        if sub.empty:
            continue
        values = sub[col].to_numpy(dtype=float) + (1.0 if shift else 0.0)
        work = pd.DataFrame({'user_id': sub['user_id'].to_numpy(),
                             'trial_index': sub['trial_index'].to_numpy(dtype=float),
                             'v': values})
        base = (work[work['trial_index'] <= EXPERIENCE_BASELINE_ROUNDS]
                .groupby('user_id')['v'].median())
        base = base[base > 0]
        if base.empty:
            continue
        for row in bins:
            lo, hi = row['from'], row['to']
            sel = work[work['trial_index'] >= lo]
            if hi is not None:
                sel = sel[sel['trial_index'] <= hi]
            if sel.empty:
                continue
            per_user = sel.groupby('user_id')['v'].median()
            per_user = per_user[per_user.index.isin(base.index)]
            if per_user.empty:
                continue
            ratio = (per_user / base.reindex(per_user.index)).to_numpy(dtype=float)
            ratio = ratio[np.isfinite(ratio) & (ratio > 0)]
            row[f'{key}_n_players'] = int(ratio.size)
            row[f'{key}_n_rounds'] = int(len(sel))
            if ratio.size < 3:
                continue
            # Median of the log-ratios: ratios are multiplicative, so an
            # arithmetic average of them would be pulled up by the players who
            # got worse and could never be pulled symmetrically down.
            med = float(np.exp(np.median(np.log(ratio))))
            lo_ci, hi_ci = bootstrap_ci(np.log(ratio), np.median)
            row[f'{key}_index'] = _f(med)
            row[f'{key}_ci_low'] = _f(math.exp(lo_ci)) if lo_ci is not None else None
            row[f'{key}_ci_high'] = _f(math.exp(hi_ci)) if hi_ci is not None else None
    return out


def _learning_block(df: pd.DataFrame, col: str, *, label: str,
                    log_transform: bool = True) -> Dict[str, Any]:
    """Within-player slope of an outcome against log₂(rounds played).

    Three deliberate choices, each fixing a flaw in the descriptive
    "metric by attempt number" charts:

    * the slope is fitted **inside** each player, so it cannot be produced by
      persistent players simply being better than one-off visitors;
    * the outcome is **centred on its colour's median** first, so drawing an
      easy colour late does not read as improvement;
    * the summary across players is the **median slope with a sign test**, not
      a mean, so one erratic player cannot carry it.

    A slope on a log outcome exponentiates to a multiplicative factor per
    doubling of experience, which is the same scale as the headline results tab.
    """
    need = ['user_id', 'target_color_id', 'trial_index', col]
    sub = df[need].dropna()
    sub = sub[np.isfinite(sub[col].to_numpy(dtype=float))]
    sub = sub[sub['trial_index'] > 0]
    if log_transform:
        sub = sub[sub[col] >= 0]
        y = np.log1p(sub[col].to_numpy(dtype=float))
    else:
        y = sub[col].to_numpy(dtype=float)
    if sub.empty:
        return {'label': label, 'n_players': 0}
    work = pd.DataFrame({
        'user_id': sub['user_id'].to_numpy(),
        'target_color_id': sub['target_color_id'].to_numpy(),
        'x': np.log2(sub['trial_index'].to_numpy(dtype=float)),
        'y': y,
    })
    # Colour-centre the outcome: subtract each target's median.
    work['y'] = work['y'] - work.groupby('target_color_id')['y'].transform('median')
    slopes: List[float] = []
    weights: List[int] = []
    for _, g in work.groupby('user_id'):
        if len(g) < MIN_ATTEMPTS_FOR_SLOPE:
            continue
        xv = g['x'].to_numpy(dtype=float)
        yv = g['y'].to_numpy(dtype=float)
        if np.std(xv) == 0:
            continue
        slope = float(np.polyfit(xv, yv, 1)[0])
        if np.isfinite(slope):
            slopes.append(slope)
            weights.append(len(g))
    out: Dict[str, Any] = {
        'label': label,
        'n_players': len(slopes),
        'min_rounds_per_player': MIN_ATTEMPTS_FOR_SLOPE,
        'log_transform': bool(log_transform),
    }
    if len(slopes) < 3:
        return out
    arr = np.asarray(slopes, dtype=float)
    med = float(np.median(arr))
    lo, hi = bootstrap_ci(arr, np.median)
    out.update({
        'median_slope': _f(med),
        'slope_ci_low': _f(lo),
        'slope_ci_high': _f(hi),
        'n_rounds': int(np.sum(weights)),
        'sign_test': sign_test(arr),
    })
    if log_transform:
        out['ratio_per_doubling'] = _f(math.exp(med))
        out['ratio_ci_low'] = _f(math.exp(lo)) if lo is not None else None
        out['ratio_ci_high'] = _f(math.exp(hi)) if hi is not None else None
        out['pct_change_per_doubling'] = _f(100.0 * (math.exp(med) - 1.0))
    return out


def _first_crossing_block(df: pd.DataFrame, threshold: float = 2.0) -> Dict[str, Any]:
    """Attempts until a player first drops a colour below ``threshold``.

    Each (player, colour) pair contributes one observation: the attempt number
    of the first crossing (event) or the number of attempts actually made
    (censored). That censoring is the whole point — the descriptive tile drops
    those pairs, which removes precisely the hardest cases.
    """
    need = ['user_id', 'target_color_id', 'attempt_no', 'final_delta_e']
    sub = df[need].dropna()
    if sub.empty:
        return {'threshold': threshold, 'n': 0, 'curve': []}
    keys = ['user_id', 'target_color_id']
    grouped = sub.groupby(keys)
    # First attempt below the threshold per pair (NaN when never crossed), and
    # the last attempt actually played, which is where a non-crosser is censored.
    first_hit = sub['attempt_no'].where(sub['final_delta_e'] < threshold).groupby(
        [sub[k] for k in keys]).min()
    last_seen = grouped['attempt_no'].max()
    events = first_hit.notna().reindex(last_seen.index, fill_value=False)
    times = first_hit.reindex(last_seen.index).fillna(last_seen).astype(int)
    out = kaplan_meier(times.to_numpy(), events.to_numpy())
    out['threshold'] = threshold
    out['n_pairs'] = out.get('n')
    return out


def _perception_block(skips: pd.DataFrame) -> Dict[str, Any]:
    """What players call identical / acceptable / unacceptable, and where the
    boundary between those judgments actually sits.

    The descriptive tab prints the ΔE distribution for each rating and stops
    there. Two things are added:

    * **contrasts** — the median shift between adjacent ratings with a bootstrap
      interval, plus Cliff's δ, so "are these ratings really separated?" gets a
      number rather than an eyeball;
    * **an in-game threshold** — the same logistic 50% crossing the calibration
      instrument fits, so the game's own acceptability boundary can be compared
      with Paravina's 2.1 / 3.1 and with the calibration probe. It carries a
      large caveat: players choose when to stop, so the ΔE values here are
      self-selected rather than imposed, which range-restricts the fit.
    """
    out: Dict[str, Any] = {'by_rating': [], 'contrasts': []}
    if skips is None or skips.empty:
        return out
    df = skips.copy()
    df['delta_e'] = pd.to_numeric(df['delta_e'], errors='coerce')
    df = df.dropna(subset=['delta_e'])
    df = df[np.isfinite(df['delta_e'])]
    if df.empty:
        return out
    order = ['identical', 'acceptable', 'unacceptable']
    groups: Dict[str, np.ndarray] = {}
    for rating in order:
        vals = df.loc[df['skip_perception'] == rating, 'delta_e'].to_numpy(dtype=float)
        groups[rating] = vals
        block = describe(vals, label=rating)
        if vals.size:
            for thr_label, thr in (('≤ 2.1 (AT light)', 2.1), ('≤ 3.1 (AT dark)', 3.1)):
                k = int(np.sum(vals <= thr))
                lo, hi = wilson_ci(k, vals.size)
                block.setdefault('threshold_shares', []).append({
                    'label': thr_label, 'rate': _f(k / vals.size),
                    'ci_low': _f(lo), 'ci_high': _f(hi),
                })
        out['by_rating'].append(block)
    for a, b in (('acceptable', 'identical'), ('unacceptable', 'acceptable')):
        va, vb = groups.get(a, np.array([])), groups.get(b, np.array([]))
        if va.size < 3 or vb.size < 3:
            continue
        shift = hodges_lehmann(va, vb)
        # Bootstrap the median shift by resampling each arm independently.
        rng = np.random.default_rng(BOOT_SEED)
        draws = []
        for _ in range(N_BOOT):
            sa = va[rng.integers(0, va.size, va.size)]
            sb = vb[rng.integers(0, vb.size, vb.size)]
            draws.append(float(np.median(sa) - np.median(sb)))
        lo, hi = np.percentile(np.asarray(draws, dtype=float), [2.5, 97.5])
        out['contrasts'].append({
            'comparison': f'{a} − {b}',
            'n_a': int(va.size), 'n_b': int(vb.size),
            'median_shift': _f(float(np.median(va) - np.median(vb))),
            'shift_ci_low': _f(lo), 'shift_ci_high': _f(hi),
            'hodges_lehmann': shift,
            'cliffs_delta': cliffs_delta(va, vb),
        })
    rated = df[df['skip_perception'].isin(order)]
    if len(rated) >= 12:
        x = rated['delta_e'].to_numpy(dtype=float)
        out['in_game_perceptibility'] = logistic_threshold_ci(
            x, (rated['skip_perception'] != 'identical').to_numpy(dtype=float))
        out['in_game_acceptability'] = logistic_threshold_ci(
            x, (rated['skip_perception'] == 'unacceptable').to_numpy(dtype=float))
    out['reference'] = {
        'source': 'Paravina et al. — CIEDE2000 thresholds',
        'perceptibility_light': 0.7, 'perceptibility_dark': 1.2,
        'acceptability_light': 2.1, 'acceptability_dark': 3.1,
    }
    return out


def holm(pvals: Sequence[Optional[float]]) -> List[Optional[float]]:
    """Holm–Bonferroni step-down adjustment, preserving input order."""
    idx = [i for i, p in enumerate(pvals) if p is not None and np.isfinite(p)]
    out: List[Optional[float]] = [None] * len(pvals)
    if not idx:
        return out
    m = len(idx)
    ordered = sorted(idx, key=lambda i: pvals[i])
    running = 0.0
    for rank, i in enumerate(ordered):
        adj = min(1.0, (m - rank) * float(pvals[i]))
        running = max(running, adj)
        out[i] = _f(running)
    return out


# --------------------------------------------------------------------------- #
# public entry point
# --------------------------------------------------------------------------- #
def build_inference_summary() -> Dict[str, Any]:
    """Everything the /stat "Következtetés" tab shows, in one payload."""
    raw = _load_attempts()
    n_rows_total = int(len(raw))
    df = _with_indices(raw)

    de_missing = int(df['final_delta_e'].isna().sum())
    dur_over_cap = int((df['duration_sec'] > DURATION_CAP_SEC).sum())

    # One attempt set for the whole tab, so every number below describes the
    # same rounds — unlike the descriptive endpoints, where the ΔE tables and
    # the timing tables silently use different filters.
    analysed = df[df['final_delta_e'].notna()].copy()
    analysed = analysed[np.isfinite(analysed['final_delta_e'].to_numpy(dtype=float))]
    timed = analysed[analysed['duration_sec'].notna()
                     & (analysed['duration_sec'] <= DURATION_CAP_SEC)
                     & (analysed['duration_sec'] >= 0)].copy()

    sample = {
        'rows_in_table': n_rows_total,
        'rows_with_user': int(len(df)),
        'analysed_attempts': int(len(analysed)),
        'timed_attempts': int(len(timed)),
        'players': int(analysed['user_id'].nunique()),
        'targets': int(analysed['target_color_id'].nunique()),
        'excluded_missing_delta_e': de_missing,
        'excluded_duration_over_cap': dur_over_cap,
        'duration_cap_sec': DURATION_CAP_SEC,
        'first_play': (analysed['attempt_started_server_ts'].min().isoformat()
                       if len(analysed) and analysed['attempt_started_server_ts'].notna().any() else None),
        'last_play': (analysed['attempt_started_server_ts'].max().isoformat()
                      if len(analysed) and analysed['attempt_started_server_ts'].notna().any() else None),
    }
    log_de = np.log1p(analysed['final_delta_e'].to_numpy(dtype=float))
    # Computed once: it feeds both the design effect below and the variance block.
    icc_user = icc_oneway(log_de, analysed['user_id'].to_numpy())
    if len(analysed):
        per_user_counts = analysed.groupby('user_id').size().to_numpy(dtype=float)
        sample['rounds_per_player_median'] = _f(float(np.median(per_user_counts)))
        sample['rounds_per_player_max'] = int(per_user_counts.max())
        sample['top_player_share'] = _f(float(per_user_counts.max() / per_user_counts.sum()))
        # Effective sample size under clustering, with an ICC of ρ:
        # n_eff = n / (1 + (m̄ − 1)·ρ). Reported next to n so the raw attempt
        # count is not mistaken for independent information.
        rho = icc_user.get('icc')
        m_bar = float(np.mean(per_user_counts))
        if rho is not None and m_bar > 0:
            design_effect = 1.0 + (m_bar - 1.0) * float(rho)
            sample['design_effect'] = _f(design_effect)
            sample['effective_n'] = _f(len(analysed) / design_effect) if design_effect > 0 else None

    payload: Dict[str, Any] = {
        'status': 'success',
        'sample': sample,
        'accuracy': _clustered_block(analysed, 'final_delta_e', label='final ΔE₀₀'),
        'duration': _clustered_block(timed, 'duration_sec', label='round duration (s)'),
        'steps': _clustered_block(analysed[analysed['num_steps'].notna()], 'num_steps',
                                  label='actions per round'),
        'thresholds': _threshold_block(analysed),
        'variance': {
            'by_player': icc_user,
            'by_target': icc_oneway(log_de, analysed['target_color_id'].to_numpy()),
            'outcome': 'log1p(final ΔE₀₀)',
        },
        'correlations': [
            correlation(timed['duration_sec'], timed['final_delta_e'],
                        x_label='duration (s)', y_label='final ΔE₀₀',
                        groups=timed['user_id']),
            correlation(analysed['num_steps'], analysed['final_delta_e'],
                        x_label='actions', y_label='final ΔE₀₀',
                        groups=analysed['user_id']),
            correlation(analysed['initial_delta_e'], analysed['final_delta_e'],
                        x_label='starting ΔE₀₀', y_label='final ΔE₀₀',
                        groups=analysed['user_id']),
            correlation(timed['num_steps'], timed['duration_sec'],
                        x_label='actions', y_label='duration (s)',
                        groups=timed['user_id']),
        ],
        'speed_accuracy': _speed_accuracy_block(analysed, timed),
        'learning': {
            'accuracy': _learning_block(analysed, 'final_delta_e', label='final ΔE₀₀'),
            'duration': _learning_block(timed, 'duration_sec', label='round duration (s)'),
            'steps': _learning_block(analysed[analysed['num_steps'].notna()], 'num_steps',
                                     label='actions per round'),
        },
        'first_crossing': _first_crossing_block(analysed, threshold=2.0),
    }

    # Holm correction across the three learning tests: they are one family of
    # hypotheses ("does practice move this outcome?"), so the raw p-values would
    # be optimistic taken together.
    fam = [(k, payload['learning'][k].get('sign_test', {}).get('p'))
           for k in ('accuracy', 'duration', 'steps')]
    for key, p_adj in zip([k for k, _ in fam], holm([p for _, p in fam])):
        payload['learning'][key]['p_holm'] = p_adj

    try:
        payload['perception'] = _perception_block(_load_skips())
    except Exception as exc:  # a missing skip column must not kill the tab
        payload['perception'] = {'error': str(exc), 'by_rating': [], 'contrasts': []}

    return payload
