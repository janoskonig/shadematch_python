"""Checks for the pure statistics in ``app.stat_inference``.

These cover only the estimators, not the SQL: every function below is fed
synthetic data with a known answer, so a regression in the maths shows up
without a PostgreSQL instance. Reference values are the textbook ones (Wilson,
Fisher-z, Kaplan–Meier, Cliff's δ) or are cross-checked against scipy where it
is installed.

Run with:  pytest tests/test_stat_inference.py
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The module must import without a Flask app context; it only touches ``db``
# inside the loader functions, which these tests do not call.
from app import stat_inference as si  # noqa: E402


# --------------------------------------------------------------------------- #
# proportions
# --------------------------------------------------------------------------- #
def test_wilson_matches_published_value():
    # Wilson 95% interval for 3/10 is (0.1078, 0.6032) to 4 dp.
    lo, hi = si.wilson_ci(3, 10)
    assert lo == pytest.approx(0.1078, abs=1e-3)
    assert hi == pytest.approx(0.6032, abs=1e-3)


def test_wilson_stays_inside_unit_interval_at_the_edges():
    lo, hi = si.wilson_ci(0, 25)
    assert lo == 0.0 and 0 < hi < 1
    lo, hi = si.wilson_ci(25, 25)
    assert hi == 1.0 and 0 < lo < 1


def test_wilson_empty_sample():
    assert si.wilson_ci(0, 0) == (None, None)


# --------------------------------------------------------------------------- #
# medians
# --------------------------------------------------------------------------- #
def test_median_ci_covers_the_truth_at_the_nominal_rate():
    rng = np.random.default_rng(7)
    covered = 0
    trials = 300
    for _ in range(trials):
        # Exponential: median = ln 2 / rate, and nothing about it is normal.
        sample = rng.exponential(scale=1.0, size=61)
        lo, hi = si.median_ci(sample)
        if lo <= math.log(2) <= hi:
            covered += 1
    assert covered / trials > 0.90


def test_median_ci_brackets_the_sample_median():
    values = list(range(1, 102))
    lo, hi = si.median_ci(values)
    assert lo < 51 < hi


def test_median_ci_degenerate_inputs():
    assert si.median_ci([]) == (None, None)
    assert si.median_ci([3.0, 1.0]) == (1.0, 3.0)


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #
def test_bootstrap_ci_is_deterministic_and_brackets_the_mean():
    rng = np.random.default_rng(3)
    sample = rng.normal(10, 2, size=400)
    first = si.bootstrap_ci(sample, np.mean)
    second = si.bootstrap_ci(sample, np.mean)
    assert first == second                     # fixed seed → stable dashboard
    assert first[0] < sample.mean() < first[1]
    # Roughly the analytic width: ±1.96·σ/√n.
    analytic = 1.96 * sample.std(ddof=1) / math.sqrt(sample.size)
    assert (first[1] - first[0]) == pytest.approx(2 * analytic, rel=0.25)


def test_cluster_bootstrap_is_wider_than_naive_when_clusters_are_correlated():
    # 12 players, 60 rounds each, all of a player's rounds share one offset:
    # the attempt-level bootstrap sees 720 "independent" values, the cluster
    # bootstrap correctly sees 12.
    rng = np.random.default_rng(11)
    groups, values = [], []
    for p in range(12):
        offset = rng.normal(0, 3.0)
        for _ in range(60):
            groups.append(p)
            values.append(offset + rng.normal(0, 0.2))
    naive = si.bootstrap_ci(values, np.mean)
    clustered = si.cluster_bootstrap_ci(
        groups, values, lambda arrays: float(np.mean(np.concatenate(arrays)))
    )
    assert (clustered[1] - clustered[0]) > 3 * (naive[1] - naive[0])


# --------------------------------------------------------------------------- #
# describe
# --------------------------------------------------------------------------- #
def test_describe_flags_right_skew():
    rng = np.random.default_rng(5)
    block = si.describe(rng.lognormal(0, 1.0, size=2000), label='x')
    assert block['n'] == 2000
    assert block['skew'] > 1.0
    assert block['mean'] > block['median']      # the point of reporting both
    assert block['q1'] < block['median'] < block['q3']
    assert block['median_ci_low'] <= block['median'] <= block['median_ci_high']


def test_describe_empty():
    assert si.describe([], label='x') == {'label': 'x', 'n': 0}


# --------------------------------------------------------------------------- #
# association
# --------------------------------------------------------------------------- #
def test_correlation_recovers_a_known_linear_relationship():
    rng = np.random.default_rng(2)
    x = rng.normal(size=500)
    y = 0.8 * x + rng.normal(scale=0.6, size=500)
    out = si.correlation(x, y, x_label='x', y_label='y')
    assert out['n'] == 500
    assert out['pearson'] == pytest.approx(np.corrcoef(x, y)[0, 1], abs=1e-9)
    assert out['pearson_ci_low'] < out['pearson'] < out['pearson_ci_high']
    assert out['pearson_p'] < 1e-10


def test_spearman_is_robust_to_the_outlier_that_moves_pearson():
    x = np.arange(1.0, 51.0)
    y = x.copy()
    y[-1] = 5000.0                              # one wild value
    out = si.correlation(x, y, x_label='x', y_label='y')
    assert out['spearman'] == pytest.approx(1.0, abs=1e-9)
    assert out['pearson'] < out['spearman']


def test_spearman_matches_scipy_with_ties():
    scipy_stats = pytest.importorskip('scipy.stats')
    rng = np.random.default_rng(4)
    x = rng.integers(0, 5, size=200).astype(float)   # lots of ties
    y = rng.integers(0, 4, size=200).astype(float)
    out = si.correlation(x, y, x_label='x', y_label='y')
    assert out['spearman'] == pytest.approx(scipy_stats.spearmanr(x, y).statistic, abs=1e-9)


def test_correlation_within_player_ignores_between_player_shifts():
    # Inside every player the relationship is negative; across players the
    # cloud slopes upward because the fast players are also the accurate ones.
    xs, ys, gs = [], [], []
    for p in range(10):
        base = p * 10.0
        for i in range(20):
            xs.append(base + i)
            ys.append(base * 3 - i)
            gs.append(f'P{p}')
    out = si.correlation(xs, ys, x_label='x', y_label='y', groups=gs)
    assert out['pearson'] > 0.5                              # pooled: positive
    assert out['within_player_spearman_median'] == pytest.approx(-1.0, abs=1e-9)
    assert out['within_player_n'] == 10


def test_correlation_too_few_points():
    assert si.correlation([1, 2], [1, 2], x_label='x', y_label='y') == {
        'x': 'x', 'y': 'y', 'n': 2}


# --------------------------------------------------------------------------- #
# effect sizes
# --------------------------------------------------------------------------- #
def test_cliffs_delta_endpoints_and_sign():
    assert si.cliffs_delta([4, 5, 6], [1, 2, 3]) == pytest.approx(1.0)
    assert si.cliffs_delta([1, 2, 3], [4, 5, 6]) == pytest.approx(-1.0)
    assert si.cliffs_delta([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)


def test_cliffs_delta_matches_the_brute_force_definition():
    rng = np.random.default_rng(8)
    a = rng.normal(0.4, 1, size=120)
    b = rng.normal(0.0, 1, size=90)
    brute = np.mean(np.sign(a[:, None] - b[None, :]))
    assert si.cliffs_delta(a, b) == pytest.approx(brute, abs=1e-9)


def test_hodges_lehmann_recovers_a_known_shift():
    rng = np.random.default_rng(9)
    a = rng.normal(5.0, 1.0, size=300)
    b = rng.normal(3.0, 1.0, size=300)
    assert si.hodges_lehmann(a, b) == pytest.approx(2.0, abs=0.25)


def test_sign_test_matches_the_exact_binomial():
    out = si.sign_test([-1, -1, -1, -1, -1, -1, -1, -1, 1, 1])
    assert out['n'] == 10 and out['n_negative'] == 8
    # 2 · P(X ≤ 2) for X ~ Bin(10, 0.5) = 2 · 56/1024
    assert out['p'] == pytest.approx(2 * 56 / 1024, abs=1e-9)


def test_sign_test_drops_exact_zeros():
    assert si.sign_test([0.0, 0.0])['n'] == 0


# --------------------------------------------------------------------------- #
# variance decomposition
# --------------------------------------------------------------------------- #
def test_icc_is_high_when_groups_differ_and_low_when_they_do_not():
    rng = np.random.default_rng(1)
    values, groups = [], []
    for g in range(15):
        centre = g * 5.0
        for _ in range(20):
            values.append(centre + rng.normal(0, 0.5))
            groups.append(g)
    assert si.icc_oneway(values, groups)['icc'] > 0.95

    flat = rng.normal(0, 1, size=300)
    labels = np.repeat(np.arange(15), 20)
    assert si.icc_oneway(flat, labels)['icc'] < 0.15


def test_icc_degenerate_inputs():
    assert si.icc_oneway([1, 2, 3], [1, 1, 1])['n_groups'] == 1


# --------------------------------------------------------------------------- #
# survival
# --------------------------------------------------------------------------- #
def test_kaplan_meier_matches_a_hand_computed_curve():
    # 5 pairs: cross at 1, 2, 2; censored at 2 and 3.
    times = [1, 2, 2, 2, 3]
    events = [True, True, True, False, False]
    km = si.kaplan_meier(times, events)
    curve = {row['attempt_no']: row for row in km['curve']}
    # t=1: S = 1 − 1/5 = 0.8 ; t=2: S = 0.8 · (1 − 2/4) = 0.4
    assert curve[1]['survival'] == pytest.approx(0.8)
    assert curve[2]['survival'] == pytest.approx(0.4)
    assert km['median_attempts_to_cross'] == 2
    assert km['n_events'] == 3 and km['n_censored'] == 2


def test_kaplan_meier_beats_the_complete_case_mean_when_censoring_bites():
    # 10 pairs cross early, 90 never cross within 6 attempts. The complete-case
    # mean says "about 2 attempts"; the reach rate says only 10% ever got there.
    times = [2] * 10 + [6] * 90
    events = [True] * 10 + [False] * 90
    km = si.kaplan_meier(times, events)
    assert km['naive_complete_case_mean'] == pytest.approx(2.0)
    assert km['final_reached'] == pytest.approx(0.10, abs=1e-9)
    assert km['median_attempts_to_cross'] is None       # never reaches 50%


def test_kaplan_meier_empty():
    assert si.kaplan_meier([], [])['n'] == 0


# --------------------------------------------------------------------------- #
# logistic threshold
# --------------------------------------------------------------------------- #
def test_logistic_threshold_recovers_a_planted_50_percent_point():
    rng = np.random.default_rng(6)
    x = np.repeat(np.array([0.3, 0.6, 0.9, 1.2, 1.6, 2.2, 3.0, 4.0]), 200)
    true_threshold, slope = 1.8, 2.5
    p = 1.0 / (1.0 + np.exp(-slope * (x - true_threshold)))
    y = (rng.random(x.size) < p).astype(float)
    fit = si.logistic_threshold(x, y)
    assert fit is not None
    assert fit[0] == pytest.approx(true_threshold, abs=0.15)
    assert fit[1] == pytest.approx(slope, rel=0.25)


def test_logistic_threshold_rejects_degenerate_input():
    assert si.logistic_threshold([1, 2, 3], [0, 0, 0]) is None          # too few
    assert si.logistic_threshold(np.arange(20.0), np.zeros(20)) is None  # one class


def test_logistic_threshold_ci_brackets_the_point_estimate():
    rng = np.random.default_rng(10)
    x = np.repeat(np.array([0.5, 1.0, 1.5, 2.0, 2.5, 3.0]), 120)
    p = 1.0 / (1.0 + np.exp(-3.0 * (x - 1.7)))
    y = (rng.random(x.size) < p).astype(float)
    out = si.logistic_threshold_ci(x, y, n_boot=120)
    assert out['ci_low'] < out['threshold'] < out['ci_high']


# --------------------------------------------------------------------------- #
# multiplicity
# --------------------------------------------------------------------------- #
def test_holm_is_monotone_and_order_preserving():
    adjusted = si.holm([0.01, 0.04, 0.03])
    assert adjusted[0] == pytest.approx(0.03)   # 3 × 0.01
    assert adjusted[2] == pytest.approx(0.06)   # 2 × 0.03
    assert adjusted[1] == pytest.approx(0.06)   # step-down: cannot drop below
    assert all(a >= b for a, b in zip(adjusted, [0.01, 0.04, 0.03]))


def test_holm_handles_missing_values():
    assert si.holm([None, 0.02]) == [None, pytest.approx(0.02)]
    assert si.holm([None, None]) == [None, None]


# --------------------------------------------------------------------------- #
# speed–accuracy curves
# --------------------------------------------------------------------------- #
def _experience_frame(accuracy_factor, duration_factor, n_players=12, n_rounds=160):
    """Synthetic rounds where every player's outcome is a known function of
    their own round counter, so the recovered index has a right answer."""
    import pandas as pd
    rows = []
    for p in range(n_players):
        base_de, base_dur = 2.0 + 0.1 * p, 30.0 + 2.0 * p
        for i in range(1, n_rounds + 1):
            doublings = math.log2(i)
            rows.append({
                'user_id': 'P%02d' % p,
                'trial_index': i,
                'final_delta_e': base_de * (accuracy_factor ** doublings),
                'duration_sec': base_dur * (duration_factor ** doublings),
            })
    return pd.DataFrame(rows)


def test_speed_accuracy_curve_is_flat_when_nothing_improves():
    df = _experience_frame(1.0, 1.0)
    out = si._speed_accuracy_block(df, df)
    for row in out['bins']:
        if row.get('accuracy_index') is None:
            continue
        assert row['accuracy_index'] == pytest.approx(1.0, abs=0.02)
        assert row['duration_index'] == pytest.approx(1.0, abs=0.02)


def test_speed_accuracy_curve_separates_a_planted_dissociation():
    # Time falls 10% per doubling; accuracy is untouched. This is the shape the
    # dashboard claims to show, so the estimator has to reproduce it.
    df = _experience_frame(1.0, 0.9)
    out = si._speed_accuracy_block(df, df)
    last = [r for r in out['bins'] if r.get('duration_index') is not None][-1]
    assert last['accuracy_index'] == pytest.approx(1.0, abs=0.03)
    assert last['duration_index'] < 0.75          # ~5 doublings of −10%
    assert last['duration_ci_high'] < 1.0         # and detectably below baseline
    assert last['accuracy_ci_low'] <= 1.0 <= last['accuracy_ci_high']


def test_speed_accuracy_baseline_bin_anchors_at_one():
    out = si._speed_accuracy_block(_experience_frame(0.9, 0.9), _experience_frame(0.9, 0.9))
    first = out['bins'][0]
    assert first['label'] == '1–4'
    assert first['accuracy_index'] == pytest.approx(1.0, abs=1e-9)
    assert first['duration_index'] == pytest.approx(1.0, abs=1e-9)


def test_speed_accuracy_survives_a_zero_delta_e_baseline():
    # A player whose first rounds are exact hits must not be dropped — the
    # (1 + ΔE) shift exists precisely so the best starters stay in the sample.
    import pandas as pd
    df = _experience_frame(0.95, 0.95, n_players=6)
    perfect = pd.DataFrame([
        {'user_id': 'PERFECT', 'trial_index': i, 'final_delta_e': 0.0, 'duration_sec': 20.0}
        for i in range(1, 40)
    ])
    both = pd.concat([df, perfect], ignore_index=True)
    out = si._speed_accuracy_block(both, both)
    counted = [r['accuracy_n_players'] for r in out['bins'] if r.get('accuracy_n_players')]
    assert max(counted) == 7          # six normal players + the perfect one


def test_speed_accuracy_empty_input():
    import pandas as pd
    empty = pd.DataFrame(columns=['user_id', 'trial_index', 'final_delta_e', 'duration_sec'])
    out = si._speed_accuracy_block(empty, empty)
    assert out['baseline_rounds'] == si.EXPERIENCE_BASELINE_ROUNDS
    assert all('accuracy_index' not in b for b in out['bins'])
