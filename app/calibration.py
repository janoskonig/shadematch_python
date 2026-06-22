"""Calibration game — psychophysical perceptibility/acceptability threshold probe.

A standalone instrument (reached at /calibration) that presents colour *pairs* at
experimenter-controlled ΔE₀₀ and records a three-way appearance judgment
(identical / acceptable / unacceptable) per pair, with the ΔE hidden from the player.

Why this exists (see the design discussion): the main game lets players self-select the
ΔE at which they judge a match, which range-restricts the data right where the 50:50
perceptibility (PT) and acceptability (AT) thresholds live. Here the ΔE is *imposed* via a
method-of-constant-stimuli ladder spanning the transition zone, so the judgment data can
actually reproduce the 50:50 thresholds (cf. Paravina et al. 2015: PT ΔE₀₀≈0.8, AT≈1.8 —
for tooth colour; ours are skin-centred and use a sharp split field, so values may differ,
which is itself the finding).

The trichotomy yields *both* thresholds from one ordinal judgment. The acceptability
referent is concrete, not abstract ("good enough for what?"): *on a face, is this an
acceptable match?* — the clinical question for a facial prosthesis match, which keeps the
acceptability judgment stable and meaningful.
  - perceptibility: identical            vs {acceptable, unacceptable}   (saw a difference?)
  - acceptability:  {identical, acceptable} vs unacceptable              (acceptable match on a face?)

This module is pure colour science + trial generation; persistence + HTTP live in routes.
"""
from __future__ import annotations

import numpy as np

from . import spectral_km

# ── Stimulus design ──────────────────────────────────────────────────────────
# Constant-stimuli ΔE₀₀ ladder: spans well below the perceptibility threshold to above
# the acceptability threshold, so a logistic fit has data on both sides of each 50% point.
#
# CHUNKED PROTOCOL: each session is a short, *self-contained* mini-block that spans the WHOLE
# ladder (every ΔE level appears each session, with catch trials), so session never gets
# confounded with difficulty. The powered estimate comes from pooling a user's ~5 sessions
# hierarchically (random intercept per session + session-order as a learning covariate) — the
# CalibrationSessions are already linked by user_id and ordered by started_at, so nothing extra
# is needed to stitch them. 8 levels × 2 reps + 4 catches = 20 trials/session (~1.5 min);
# five sessions ≈ 100 trials, spread across days/conditions.
DELTA_LEVELS = (0.3, 0.6, 0.9, 1.2, 1.6, 2.2, 3.0, 4.0)
REPS_PER_LEVEL = 2            # 8 levels × 2 = 16 real trials per session
TARGET_SESSIONS = 5          # chunked protocol: ~5 sessions × 20 ≈ 100 trials per user

# Catch trials (quality control, excluded from the threshold fit):
#   - 'identical': a true zero-ΔE pair → the only correct answer is "no difference".
#   - 'obvious':   a large-ΔE pair    → must read as "too different".
# Failing these flags an inattentive / random-clicking session.
CATCH_IDENTICAL = 2
CATCH_OBVIOUS = 2
CATCH_OBVIOUS_DE = 11.0

# Colour centres the pairs are built around: skin means (Xiao et al. 2017), so the threshold
# is measured right at the game's operating point — thresholds vary across colour space, and
# "would this be acceptable on your face?" only makes sense for skin tones. (All skin-centred
# by design; a neutral/grey centre would contradict the framing and is omitted.)
CENTERS = (
    ('Caucasian · cheek', (59.6, 11.8, 14.6)),
    ('Chinese · cheek', (58.9, 11.4, 14.2)),
    ('Kurdish · forehead', (56.1, 11.3, 16.4)),
    ('Thai · cheek', (60.7, 10.5, 17.2)),
)

# ── CIELAB (D65, 2°) ↔ sRGB, with an in-gamut test ───────────────────────────
_WHITE = np.array([0.95047, 1.0, 1.08883])   # D65 2° reference white, Y=1
_EPS = 0.008856451679035631


def _lab_to_xyz(lab):
    L, a, b = lab
    fy = (L + 16.0) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0
    def inv(t):
        return t ** 3 if t ** 3 > _EPS else (t - 16.0 / 116.0) / 7.787037037037037
    return _WHITE * np.array([inv(fx), inv(fy), inv(fz)])


def lab_to_srgb(lab):
    """CIELAB → 8-bit sRGB plus an in-gamut flag (True if no channel needed clamping)."""
    lrgb = spectral_km.XYZ_RGB @ _lab_to_xyz(lab)
    in_gamut = bool(np.all(lrgb >= -1e-4) and np.all(lrgb <= 1 + 1e-4))
    srgb = spectral_km._compand(np.clip(lrgb, 0.0, 1.0)) * 255.0
    return [int(round(float(c))) for c in np.clip(srgb, 0, 255)], in_gamut


def _delta_e(lab1, lab2):
    return float(spectral_km.ciede2000(np.asarray(lab1, float), np.asarray(lab2, float)))


def pair_at_delta_e(center, target_de, rng, max_dir_tries=16):
    """A second Lab point at ΔE₀₀ ≈ target_de from `center`, in a random Lab direction.

    ΔE₀₀ is non-Euclidean, so we fix a random unit direction in Lab and binary-search the
    step length until the colour difference hits the target. Directions whose endpoint falls
    outside sRGB are rejected and re-rolled (skin centres sit well inside the gamut, so small
    ΔE almost always succeeds). Returns (lab2, rgb2, in_gamut)."""
    center = np.asarray(center, float)
    if target_de <= 1e-6:
        rgb, ig = lab_to_srgb(center)
        return center.tolist(), rgb, ig
    last = None
    for _ in range(max_dir_tries):
        d = rng.normal(size=3)
        n = np.linalg.norm(d)
        if n < 1e-9:
            continue
        d = d / n
        # Bracket: grow until ΔE exceeds the target, then bisect.
        hi = 1.0
        while _delta_e(center, center + hi * d) < target_de and hi < 200.0:
            hi *= 2.0
        lo = 0.0
        for _ in range(40):
            mid = 0.5 * (lo + hi)
            if _delta_e(center, center + mid * d) < target_de:
                lo = mid
            else:
                hi = mid
        lab2 = center + 0.5 * (lo + hi) * d
        rgb, ig = lab_to_srgb(lab2)
        last = (lab2.tolist(), rgb, ig)
        if ig:
            return last
    return last   # all directions clipped — accept the nearest (rare for skin centres)


def build_block(seed):
    """Assemble a randomised trial block: REPS_PER_LEVEL real pairs per ΔE level (rotating
    colour centres) plus identical/obvious catch trials. Each trial is a dict with the *true*
    ΔE and catch flag — the caller stores these server-side and never sends them to the client.
    """
    rng = np.random.default_rng(seed)
    trials = []
    ci = 0

    def add(center_name, center_lab, target_de, is_catch, catch_kind):
        nonlocal ci
        lab2, rgb2, ig = pair_at_delta_e(center_lab, target_de, rng)
        rgb1, _ = lab_to_srgb(center_lab)
        actual = _delta_e(center_lab, lab2)
        trials.append({
            'center_name': center_name,
            'center_lab': [round(float(x), 3) for x in center_lab],
            'lab2': [round(float(x), 3) for x in lab2],
            'target_de': round(float(target_de), 3),
            'actual_de': round(float(actual), 4),
            'rgb1': rgb1, 'rgb2': rgb2,
            'in_gamut': bool(ig),
            'is_catch': bool(is_catch),
            'catch_kind': catch_kind,
        })

    for de in DELTA_LEVELS:
        for _ in range(REPS_PER_LEVEL):
            name, lab = CENTERS[ci % len(CENTERS)]
            ci += 1
            add(name, lab, de, False, None)
    for _ in range(CATCH_IDENTICAL):
        name, lab = CENTERS[ci % len(CENTERS)]; ci += 1
        add(name, lab, 0.0, True, 'identical')
    for _ in range(CATCH_OBVIOUS):
        name, lab = CENTERS[ci % len(CENTERS)]; ci += 1
        add(name, lab, CATCH_OBVIOUS_DE, True, 'obvious')

    order = rng.permutation(len(trials))
    return [trials[i] for i in order]


# ── Threshold estimation (single-session, for the end screen) ────────────────
# A short block gives a noisy per-session estimate; the population/replication threshold
# comes from pooling all sessions hierarchically offline. Here we just fit a logistic per
# session for immediate feedback ("your perceptibility today ≈ …").
def _logistic_threshold(x, y):
    """ΔE at the 50% crossing of P(y=1) ~ logistic(b0 + b1·x), via Newton-Raphson IRLS.
    Returns (threshold, slope) or None if degenerate (single class / non-increasing)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if x.size < 6 or y.min() == y.max():
        return None
    X = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(50):
        eta = X @ beta
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -30, 30)))
        W = np.clip(p * (1 - p), 1e-6, None)
        grad = X.T @ (y - p)
        H = X.T @ (X * W[:, None])
        try:
            step = np.linalg.solve(H + 1e-6 * np.eye(2), grad)
        except np.linalg.LinAlgError:
            return None
        beta += step
        if np.max(np.abs(step)) < 1e-7:
            break
    b0, b1 = beta
    if b1 <= 1e-6:
        return None
    return float(-b0 / b1), float(b1)


def summarize(responded_trials):
    """Compute the session summary from stored trials that have a judgment.

    Each trial dict needs: actual_de, is_catch, catch_kind, judgment ∈
    {identical, acceptable, unacceptable}. Catch trials drive the quality check; real trials
    drive the two logistic fits. Returns a JSON-ready dict (thresholds may be None)."""
    real = [t for t in responded_trials if t.get('judgment') and not t.get('is_catch')]
    catch = [t for t in responded_trials if t.get('judgment') and t.get('is_catch')]

    def perceived_diff(t):
        return 0.0 if t['judgment'] == 'identical' else 1.0

    def unacceptable(t):
        return 1.0 if t['judgment'] == 'unacceptable' else 0.0

    # Catch QC: identical → should read "no difference"; obvious → should read "too different".
    passed = 0
    for t in catch:
        if t['catch_kind'] == 'identical' and t['judgment'] == 'identical':
            passed += 1
        elif t['catch_kind'] == 'obvious' and t['judgment'] == 'unacceptable':
            passed += 1
    catch_pass_rate = (passed / len(catch)) if catch else None

    pt = at = None
    if real:
        x = [t['actual_de'] for t in real]
        fp = _logistic_threshold(x, [perceived_diff(t) for t in real])
        fa = _logistic_threshold(x, [unacceptable(t) for t in real])
        # Only report a crossing that lands inside the tested ΔE span (else it's extrapolation).
        span = (min(DELTA_LEVELS) * 0.5, max(DELTA_LEVELS) * 1.25)
        if fp and span[0] <= fp[0] <= span[1]:
            pt = round(fp[0], 2)
        if fa and span[0] <= fa[0] <= span[1]:
            at = round(fa[0], 2)

    return {
        'n_real': len(real),
        'n_catch': len(catch),
        'catch_pass_rate': None if catch_pass_rate is None else round(catch_pass_rate, 3),
        'low_quality': (catch_pass_rate is not None and catch_pass_rate < 0.7),
        'perceptibility_de': pt,
        'acceptability_de': at,
    }
