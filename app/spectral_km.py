"""Single-constant Kubelka–Munk engine + recipe solver — a faithful Python port of the
JS engine in static/spectral.js, so a recipe found here reproduces *exactly* what the
/spectral lab renders.

Why this exists: the old /reverse_engineer (app.utils.reverse_engineer_recipe) linearly
averaged reflectance curves and minimised XYZ Euclidean distance — physically wrong, and
not the model /spectral uses. This module mixes pigments the way /spectral does
(Kubelka–Munk in K/S space with measured tinting strengths) and inverts that model with a
global search against a perceptual ΔE2000 objective.

Reference — https://github.com/yargo13/color-formulation
  The inverse-solve approach here is adapted from yargo13/color-formulation, a
  two-constant Kubelka–Munk paint-formulation engine that solves with a genetic algorithm
  and dual-illuminant (D65 + A) colorimetric matching. We borrow two ideas from it:
    - its solver *strategy* — pigment-subset selection + several recipe options trading
      simplicity against accuracy (see solve_recipe);
    - its multi-illuminant fitness, to resist metamerism (see the scoring section below).
  We keep our own single-constant forward model (so a recipe stays consistent with what
  /spectral renders); its two-constant physics is the deferred FUTURE spike noted below.

Ported verbatim from static/spectral.js (line refs are to that file):
  - 38-bin grid 380–750 nm @10 nm        (SIZE = 38, :32)
  - D65-weighted CMF, R→XYZ = CMF·R       (CIE.CMF :746, R_to_XYZ :570)
  - XYZ→linear-sRGB matrix + sRGB gamma   (XYZ_RGB :786, compand :456, GAMMA=2.4 :33)
  - KS(R) = (1-R)²/(2R), KM(ks)=1+ks-√(ks²+2ks)            (:322, :343)
  - mix: concentration = amount²·tinting²·luminance        (:373)
Tinting strengths are the measured Kremer-ladder values from static/spectral_mixer.js:43
(already stored as the √-strength the engine squares — do NOT re-sqrt them here).

FUTURE (two-constant KM): for real-paint accuracy beyond what /spectral renders, derive
per-pigment K(λ) and S(λ) from the Kremer shade ladders (sh-1..sh-4 in static/pigments/
*.csv) and add a Saunderson surface correction (K1≈0.039, K2≈0.540) + finite-thickness
optics, mirroring yargo13/color-formulation (MainActivity.spectral_curve_calculation /
calculate_R). That needs the ladder concentration RATIOS, which our CSVs don't document,
so it requires a calibration assumption to validate first — kept as a separate spike
because it diverges from the /spectral rendering this module is deliberately consistent
with.
"""
from itertools import combinations

import numpy as np
from scipy.optimize import minimize
from colormath.color_objects import LabColor
from colormath import spectral_constants as _spectral_constants

from .utils import delta_e_cie2000


# ── Engine grid & constants (ported from spectral.js) ───────────────────────
SIZE = 38
WAVELENGTHS = np.array([380 + 10 * i for i in range(SIZE)], dtype=float)
GAMMA = 2.4

# Palette order shared with /spectral (mixing-core.js / spectral_mixer.js).
ORDER = ['white', 'black', 'red', 'yellow', 'blue']

# Measured relative tinting strengths (spectral_mixer.js:43). These are the values the
# engine squares in the concentration weight, i.e. already √strength — used as-is.
TINTING = {'white': 0.1, 'black': 2.7, 'red': 2.1, 'yellow': 1.0, 'blue': 1.7}

# CIE D65-weighted colour-matching functions (spectral.js CIE.CMF, :746), 3×38.
CMF = np.array([
    [0.0000646919989576, 0.0002194098998132, 0.0011205743509343, 0.0037666134117111, 0.011880553603799, 0.0232864424191771, 0.0345594181969747, 0.0372237901162006,
     0.0324183761091486, 0.021233205609381, 0.0104909907685421, 0.0032958375797931, 0.0005070351633801, 0.0009486742057141, 0.0062737180998318, 0.0168646241897775,
     0.028689649025981, 0.0426748124691731, 0.0562547481311377, 0.0694703972677158, 0.0830531516998291, 0.0861260963002257, 0.0904661376847769, 0.0850038650591277,
     0.0709066691074488, 0.0506288916373645, 0.035473961885264, 0.0214682102597065, 0.0125164567619117, 0.0068045816390165, 0.0034645657946526, 0.0014976097506959,
     0.000769700480928, 0.0004073680581315, 0.0001690104031614, 0.0000952245150365, 0.0000490309872958, 0.0000199961492222],
    [0.000001844289444, 0.0000062053235865, 0.0000310096046799, 0.0001047483849269, 0.0003536405299538, 0.0009514714056444, 0.0022822631748318, 0.004207329043473,
     0.0066887983719014, 0.0098883960193565, 0.0152494514496311, 0.0214183109449723, 0.0334229301575068, 0.0513100134918512, 0.070402083939949, 0.0878387072603517,
     0.0942490536184085, 0.0979566702718931, 0.0941521856862608, 0.0867810237486753, 0.0788565338632013, 0.0635267026203555, 0.05374141675682, 0.042646064357412,
     0.0316173492792708, 0.020885205921391, 0.0138601101360152, 0.0081026402038399, 0.004630102258803, 0.0024913800051319, 0.0012593033677378, 0.000541646522168,
     0.0002779528920067, 0.0001471080673854, 0.0000610327472927, 0.0000343873229523, 0.0000177059860053, 0.000007220974913],
    [0.000305017147638, 0.0010368066663574, 0.0053131363323992, 0.0179543925899536, 0.0570775815345485, 0.113651618936287, 0.17335872618355, 0.196206575558657,
     0.186082370706296, 0.139950475383207, 0.0891745294268649, 0.0478962113517075, 0.0281456253957952, 0.0161376622950514, 0.0077591019215214, 0.0042961483736618,
     0.0020055092122156, 0.0008614711098802, 0.0003690387177652, 0.0001914287288574, 0.0001495555858975, 0.0000923109285104, 0.0000681349182337, 0.0000288263655696,
     0.0000157671820553, 0.0000039406041027, 0.000001584012587, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
])

# XYZ → linear sRGB (spectral.js CONVERSION.XYZ_RGB, :786).
XYZ_RGB = np.array([
    [3.2409699419045226, -1.537383177570094, -0.4986107602930034],
    [-0.9692436362808796, 1.8759675015077202, 0.04155505740717559],
    [0.05563007969699366, -0.20397695888897652, 1.0569715142428786],
])

# Engine white point = CMF · ones (the reflectance of a perfect diffuser on this grid).
WHITE_XYZ = CMF @ np.ones(SIZE)
EPS = np.finfo(float).eps


# ── Multi-illuminant scoring (metamerism) ───────────────────────────────────
# A recipe that matches the target under D65 can drift apart under other lights —
# that's metamerism, and it's the usual reason a "perfect" match looks wrong in
# the room. To account for it we re-score every candidate under several lights.
# Adapted from the dual-illuminant (D65 + A) fitness in yargo13/color-formulation
# (MainActivity.calculate_fitting — https://github.com/yargo13/color-formulation);
# we add F11 as an extra narrow-band stressor.
#
# colormath ships the CIE 1931 2° observer and standard illuminant SPDs on a
# 340–830 nm @10 nm grid; our 380–750 nm engine grid is exactly indices 4–41 of
# it. Reconstructing the D65 weighting this way reproduces the engine CMF to
# ΔE 0.0 (verified), so D65 stays the headline match and the other lights are
# layered on purely to expose metameric drift.
_CM_I0, _CM_I1 = 4, 4 + SIZE  # slice of the 340–830 grid covering 380–750 nm
_OBS = np.vstack([
    np.asarray(_spectral_constants.STDOBSERV_X2, dtype=float),
    np.asarray(_spectral_constants.STDOBSERV_Y2, dtype=float),
    np.asarray(_spectral_constants.STDOBSERV_Z2, dtype=float),
])[:, _CM_I0:_CM_I1]

# Reference light first (D65 = what /spectral renders under), then test lights
# that surface metamerism: A = warm incandescent, F11 = narrow-band fluorescent
# (the classic metameric-failure stressor).
ILLUMINANTS = ['D65', 'A', 'F11']
_ILLUM_SPD = {
    'D65': _spectral_constants.REFERENCE_ILLUM_D65,
    'A': _spectral_constants.REFERENCE_ILLUM_A,
    'F11': _spectral_constants.REFERENCE_ILLUM_F11,
}
# Per-illuminant weighting matrix W (3×38) and its white point, so XYZ = W·R.
_ILLUM = {}
for _name in ILLUMINANTS:
    _W = _OBS * np.asarray(_ILLUM_SPD[_name], dtype=float)[_CM_I0:_CM_I1]
    _ILLUM[_name] = (_W, _W @ np.ones(SIZE))
# White points stacked in ILLUMINANTS order, shape (n_illum, 3), for the batched Lab transform.
_ILLUM_WHITE = np.stack([_ILLUM[n][1] for n in ILLUMINANTS])


def render_cmfs():
    """Per-illuminant CMF (3×38) for *displaying* a reflectance curve under each light.

    A swatch is drawn by R → XYZ → sRGB. Under a non-D65 light the colour a viewer
    actually perceives is the chromatically-adapted one: a neutral still reads neutral
    (the eye adapts to the light), but a metameric pair drifts apart. We bake that
    von-Kries adaptation into the matrix so the client just swaps which 3×38 it
    convolves R against:

        XYZ = diag(WHITE_XYZ / white_illum) · (OBS · SPD_illum) · R

    This maps the perfect diffuser (R≡1) to the engine D65 white under *every* light,
    so white stays white and only genuine spectral mismatch shows. The 'D65' entry is
    the engine CMF unchanged, so existing swatches render byte-for-byte identically.
    Returns {illuminant: 3×38 list}, keys in ILLUMINANTS order.
    """
    out = {'D65': CMF.tolist()}
    for name in ILLUMINANTS:
        if name == 'D65':
            continue
        W, white = _ILLUM[name]
        adapt = (WHITE_XYZ / white)[:, None]   # (3,1) von-Kries diagonal in XYZ
        out[name] = (adapt * W).tolist()
    return out


# How hard the solver leans on the test lights relative to the D65 headline. D65
# stays dominant (weight 1), so the headline match is preserved; this only steers
# otherwise-comparable recipes toward ones that also hold up under A/F11.
METAMERISM_WEIGHT = 0.5


# ── Spectral / colorimetric helpers ─────────────────────────────────────────
def ks(R):
    """Single-constant Kubelka–Munk K/S (spectral.js KS, :322)."""
    return (1.0 - R) ** 2 / (2.0 * R)


def km(ksv):
    """Inverse: K/S → reflectance (spectral.js KM, :343)."""
    return 1.0 + ksv - np.sqrt(ksv ** 2 + 2.0 * ksv)


def _compand(x):
    """Linear → sRGB gamma (spectral.js compand, :456)."""
    return np.where(x > 0.0031308, 1.055 * np.power(np.clip(x, 0, None), 1.0 / GAMMA) - 0.055, x * 12.92)


def resample_to_grid(wavelengths, reflectances):
    """Linear-interpolate a measured curve onto the 38-bin engine grid, clamp to (0,1].

    Mirrors resampleToGrid in static/mixing-core.js:35 (constant extrapolation at the
    ends, floor at 1e-4 so K/S stays finite).
    """
    x = np.asarray(wavelengths, dtype=float)
    y = np.asarray(reflectances, dtype=float)
    v = np.interp(WAVELENGTHS, x, y)  # np.interp clamps to endpoints outside [x0, xN]
    return np.clip(v, 1e-4, 1.0)


def xyz_to_lab(XYZ):
    """XYZ → CIELAB under the engine's D65 white (WHITE_XYZ). No sRGB round-trip, so
    out-of-gamut targets aren't clipped before scoring."""
    xr, yr, zr = np.asarray(XYZ) / WHITE_XYZ

    def f(t):
        return np.cbrt(t) if t > 0.008856451679035631 else (7.787037037037037 * t + 16.0 / 116.0)

    fx, fy, fz = f(xr), f(yr), f(zr)
    return np.array([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)])


class SpectralColor:
    """A colour as a 38-bin reflectance R, with derived XYZ / sRGB / Lab / KS, mirroring
    spectral.js's Color built from R."""

    def __init__(self, R, tinting=1.0):
        self.R = np.clip(np.asarray(R, dtype=float), 1e-4, 1.0)
        self.tinting = tinting
        self.XYZ = CMF @ self.R
        self.KS = ks(self.R)
        self.luminance = max(EPS, float(self.XYZ[1]))

    @property
    def sRGB(self):
        lrgb = XYZ_RGB @ self.XYZ
        return [int(round(c)) for c in np.clip(_compand(lrgb) * 255.0, 0, 255)]

    @property
    def lab(self):
        return xyz_to_lab(self.XYZ)


def km_mix(entries):
    """Mix [(SpectralColor, amount), …] in K/S space exactly as spectral.js mix (:373):
    concentration = amount²·tinting²·luminance (constant per pigment across bands),
    R[i] = KM(Σ KS_p[i]·c_p / Σ c_p). Returns a SpectralColor."""
    conc = np.array([
        (amount ** 2) * (color.tinting ** 2) * color.luminance
        for color, amount in entries
    ])
    total = conc.sum()
    if total <= 0:
        return SpectralColor(np.ones(SIZE))
    ks_stack = np.stack([color.KS for color, _ in entries])  # (n, 38)
    ks_mix = (ks_stack * conc[:, None]).sum(axis=0) / total
    return SpectralColor(km(ks_mix))


# ── Base palette wiring ─────────────────────────────────────────────────────
def bases_from_spectrum_plots(spectrum_plots):
    """Build the 5 base SpectralColors from routes.build_spectrum_plots() output, each
    resampled to the engine grid and tagged with its measured tinting strength."""
    bases = {}
    for key in ORDER:
        data = spectrum_plots.get(key)
        if not data:
            continue
        R = resample_to_grid(data['wavelengths'], data['reflectances'])
        bases[key] = SpectralColor(R, tinting=TINTING.get(key, 1.0))
    return bases


def mix_amounts(bases, amounts):
    """amounts: {key: number} → mixed SpectralColor (white if everything is zero)."""
    entries = [(bases[k], amounts[k]) for k in bases if amounts.get(k, 0) > 0]
    if not entries:
        return SpectralColor(np.ones(SIZE))
    return km_mix(entries)


# ── Solver ──────────────────────────────────────────────────────────────────
def _delta_e(lab1, lab2):
    return delta_e_cie2000(LabColor(*lab1), LabColor(*lab2))


def ciede2000(lab1, lab2):
    """Vectorised CIEDE2000 (Sharma et al. 2005), kL=kC=kH=1. lab1/lab2 are (…, 3)
    arrays and the colour difference is taken along the last axis, so a whole batch
    of Lab pairs scores in one call. Matches colormath.delta_e_cie2000 to ~1e-5 —
    this is the solver's hot path (called thousands of times), and colormath's
    per-call object construction made the multi-illuminant solve ~3× too slow."""
    lab1 = np.asarray(lab1, dtype=float)
    lab2 = np.asarray(lab2, dtype=float)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    G = 0.5 * (1 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(C1p * C2p == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)
    Lbarp = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    hsum, habs = h1p + h2p, np.abs(h1p - h2p)
    hbarp = np.where(C1p * C2p == 0, hsum,
                     np.where(habs <= 180, hsum / 2.0,
                              np.where(hsum < 360, (hsum + 360) / 2.0, (hsum - 360) / 2.0)))
    T = (1 - 0.17 * np.cos(np.radians(hbarp - 30)) + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6)) - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25.0) ** 2))
    RC = 2 * np.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25.0 ** 7))
    SL = 1 + (0.015 * (Lbarp - 50) ** 2) / np.sqrt(20 + (Lbarp - 50) ** 2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2 * dtheta)) * RC
    return np.sqrt((dLp / SL) ** 2 + (dCp / SC) ** 2 + (dHp / SH) ** 2
                   + RT * (dCp / SC) * (dHp / SH))


def _labs_under_all(R):
    """Stack the CIELAB of reflectance R under every illuminant, shape (n_illum, 3).

    XYZ = W·R per illuminant (its own white point), then the standard f() lab transform —
    all illuminants at once so it's a couple of small matmuls, no Python loop in the hot path."""
    XYZ = np.stack([W @ R for W, _ in (_ILLUM[n] for n in ILLUMINANTS)])      # (n, 3)
    ratios = XYZ / _ILLUM_WHITE                                              # (n, 3)
    fr = np.where(ratios > 0.008856451679035631,
                  np.cbrt(ratios),
                  7.787037037037037 * ratios + 16.0 / 116.0)
    fx, fy, fz = fr[:, 0], fr[:, 1], fr[:, 2]
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=1)


def delta_e_by_illuminant(target, sample_R):
    """ΔE2000 between a target and a sample reflectance curve under each illuminant.

    `target` may be a reflectance array or a pre-computed (n_illum, 3) Lab stack (from
    _labs_under_all) — the stacked form lets the solver compute the fixed target Labs
    once and reuse them across thousands of objective evaluations. The 'D65' entry
    equals the engine headline ΔE (same observer/white point)."""
    target_labs = target if isinstance(target, np.ndarray) else _labs_under_all(target)
    des = ciede2000(target_labs, _labs_under_all(sample_R))   # (n_illum,)
    return {name: float(des[i]) for i, name in enumerate(ILLUMINANTS)}


def _metamerism_index(des):
    """How much the match degrades under the worst test light vs. the D65 reference:
    max(test ΔE) − D65 ΔE, floored at 0. ~0 means the recipe holds across lights;
    a large value flags a metameric pair that will look off under A/F11."""
    ref = des[ILLUMINANTS[0]]
    worst_test = max(des[n] for n in ILLUMINANTS[1:]) if len(ILLUMINANTS) > 1 else ref
    return max(0.0, worst_test - ref)


def _metameric_cost(des):
    """Solver objective: the D65 reference match plus a weighted pull toward also
    matching under the test lights. D65 dominates (weight 1), so the headline match
    is preserved while ties break toward metamerism-robust recipes."""
    ref = des[ILLUMINANTS[0]]
    tests = [des[n] for n in ILLUMINANTS[1:]]
    return ref + METAMERISM_WEIGHT * (sum(tests) / len(tests) if tests else 0.0)


def _recipe_result(bases, keys, amounts, target):
    """Package a recipe (dict key→amount) with its achieved colour, headline ΔE
    (under D65), the per-illuminant ΔE breakdown, and the metamerism index."""
    mixed = mix_amounts(bases, amounts)
    total = sum(amounts.values())
    des = delta_e_by_illuminant(target, mixed.R)
    return {
        'amounts': amounts,
        'percentages': {k: (100.0 * amounts[k] / total if total else 0.0) for k in amounts},
        'achieved_rgb': mixed.sRGB,
        'delta_e': des[ILLUMINANTS[0]],
        'delta_e_by_illuminant': des,
        'metamerism_index': _metamerism_index(des),
    }


def _best_for_subset(target, bases, keys, rng, light=False):
    """Best continuous mix of exactly `keys` (a pigment subset), as normalised fractions.

    Low-dimensional and fairly smooth once the forward model and objective are correct, so a
    multi-start L-BFGS-B (each base alone, the centroid, plus a few seeded random starts)
    finds the subset optimum reliably without a full global search per subset. Minimises the
    metameric cost (D65 match + test-light penalty). Returns (fractions array aligned to
    `keys`, metameric cost).

    `light=True` drops the per-pigment-alone starts (centroid + a few random only). Used by
    solve_mix for the wide first pass over a big palette, where n single-pigment L-BFGS-B
    runs would dominate the cost; the subsequent re-solve on the reduced subset uses the
    full start set."""
    n = len(keys)

    def objective(x):
        s = x.sum()
        if s <= 1e-9:
            return 1e6
        amounts = {keys[i]: float(x[i]) for i in range(n)}
        return _metameric_cost(delta_e_by_illuminant(target, mix_amounts(bases, amounts).R))

    starts = [np.full(n, 1.0 / n)]                 # centroid
    if not light:
        starts += [np.eye(n)[i] for i in range(n)]  # each pigment alone
    starts += [rng.random(n) for _ in range(4 if light else 3)]    # seeded random

    bounds = [(0.0, 1.0)] * n
    best_x, best_f = None, np.inf
    for s0 in starts:
        res = minimize(objective, s0, method='L-BFGS-B', bounds=bounds)
        if res.fun < best_f:
            best_f, best_x = res.fun, res.x

    total = best_x.sum() or 1.0
    return best_x / total, float(best_f)


# Gamut-reachability ladder, in headline (D65) ΔE2000. Five fixed pigments span a
# limited gamut, so an arbitrary target may simply be unreachable — these bands turn
# the best achievable ΔE into an honest verdict instead of a silently-large number.
REACHABILITY_BANDS = [
    (1.0, 'exact', 'Reachable — the match is essentially perfect (ΔE < 1, imperceptible).'),
    (3.0, 'close', 'Reachable — a very good match (ΔE < 3, only a trained eye sees a difference).'),
    (6.0, 'approximate', 'Approximate — a noticeable difference remains (ΔE 3–6); this is near the edge of what this palette can mix.'),
    (float('inf'), 'out_of_gamut', 'Out of gamut — this colour is outside what this palette can mix (ΔE > 6); the recipe below is only the closest reachable approximation.'),
]


def _reachability(best_delta_e):
    """Classify the closest achievable D65 match into a reachability verdict."""
    for threshold, status, message in REACHABILITY_BANDS:
        if best_delta_e < threshold:
            return {'status': status, 'closest_delta_e': float(best_delta_e), 'message': message}
    # unreachable fall-through (REACHABILITY_BANDS ends with inf, so this is defensive)
    last = REACHABILITY_BANDS[-1]
    return {'status': last[1], 'closest_delta_e': float(best_delta_e), 'message': last[2]}


# Palettes with at most this many pigments are solved by exhaustive subset enumeration
# (2ⁿ); wider ones use forward-greedy selection so a solve stays sub-second.
EXHAUSTIVE_MAX = 7


def _effsets_exhaustive(target_labs, bases, keys_all, rng, significance):
    """Solve every non-empty pigment subset; collapse to one entry per *effective* set
    (pigments left after dropping near-zero ones), keeping the lowest-cost solve for each."""
    by_effset = {}
    for k in range(1, len(keys_all) + 1):
        for subset in combinations(keys_all, k):
            fracs, cost = _best_for_subset(target_labs, bases, list(subset), rng)
            eff = [(subset[i], fracs[i]) for i in range(len(subset)) if fracs[i] >= significance]
            if not eff:
                continue
            effset = frozenset(p for p, _ in eff)
            if effset not in by_effset or cost < by_effset[effset][1]:
                by_effset[effset] = (eff, cost)
    return by_effset


def _effsets_greedy(target_labs, bases, keys_all, rng, significance, max_pigments=8):
    """Forward-greedy subset search for wide palettes: start empty, repeatedly add the
    pigment that most lowers the metameric cost, recording the effective recipe at each
    step. Yields a simplicity/accuracy ladder (one entry per effective pigment count)
    without the 2ⁿ blow-up. Stops once extra pigments stop helping or the cap is hit."""
    by_effset = {}
    chosen, last_cost = [], np.inf
    cap = min(max_pigments, len(keys_all))
    while len(chosen) < cap:
        remaining = [k for k in keys_all if k not in chosen]
        best = None
        for c in remaining:
            trial = chosen + [c]
            fracs, cost = _best_for_subset(target_labs, bases, trial, rng, light=len(trial) > 6)
            if best is None or cost < best[1]:
                best = (trial, cost, fracs)
        trial, cost, fracs = best
        chosen = trial
        eff = [(chosen[i], fracs[i]) for i in range(len(chosen)) if fracs[i] >= significance]
        if eff:
            effset = frozenset(p for p, _ in eff)
            if effset not in by_effset or cost < by_effset[effset][1]:
                by_effset[effset] = (eff, cost)
        if cost > last_cost - 1e-3:    # adding another pigment no longer meaningfully helps
            break
        last_cost = cost
    return by_effset


def solve_recipe(target_color, bases, seed=0, max_options=3, significance=0.02):
    """Match target_color (a SpectralColor) with the measured bases, minimising ΔE2000.

    Adopts the reference engine's solver *strategy* (yargo13/color-formulation): instead of
    one recipe that uses every pigment, it does subset selection and returns several recipe
    options trading simplicity against accuracy. With only five bases we can be exhaustive
    rather than greedy — every non-empty pigment subset is solved, each result reduced to the
    pigments that actually carry weight, and the **Pareto front** over (pigment count, cost)
    is surfaced: for each level of simplicity, the best match achievable.

    The per-subset objective is multi-illuminant (D65 reference + A/F11 test lights) so the
    chosen recipes resist metamerism, and each option reports its per-illuminant ΔE breakdown.
    A `reachability` verdict states how close the best recipe can actually get, since five
    fixed pigments span a limited gamut.

    Returns {'options': [opt, …], 'reachability': {…}} ordered best-match first (so options[0]
    is the headline recipe). Each opt = {pigments, num_pigments, continuous:{…}, rounded:{…}}.

    Works for any palette: the original five (and any set ≤ EXHAUSTIVE_MAX pigments) are
    solved exhaustively over every subset; wider palettes (the 8/10/12/16-pigment gamut sets)
    use a forward-greedy subset search so the solve stays fast instead of 2ⁿ-exploding.
    """
    # All bases, painter-primaries first; an arbitrary palette may add p6, p7, ….
    keys_all = [k for k in ORDER if k in bases] + [k for k in bases if k not in ORDER]
    # Target curve is fixed for the whole solve — pre-compute its per-illuminant Labs
    # once and reuse them across the thousands of objective evaluations below.
    target_labs = _labs_under_all(target_color.R)
    rng = np.random.default_rng(seed)

    if len(keys_all) <= EXHAUSTIVE_MAX:
        by_effset = _effsets_exhaustive(target_labs, bases, keys_all, rng, significance)
    else:
        by_effset = _effsets_greedy(target_labs, bases, keys_all, rng, significance)

    # Pareto front over (number of pigments, cost): keep a recipe only if no simpler-or-equal
    # recipe also matches better. This is the simplicity/accuracy trade-off ladder.
    entries = sorted(by_effset.values(), key=lambda e: (len(e[0]), e[1]))
    front, best_cost_so_far = [], np.inf
    for eff, cost in entries:
        if cost < best_cost_so_far - 1e-9:    # str* improvement over anything simpler
            front.append((eff, cost))
            best_cost_so_far = cost

    # Build option dicts (best match first), capped at max_options.
    front.sort(key=lambda e: e[1])
    options = []
    for eff, _ in front[:max_options]:
        keys = [p for p, _ in sorted(eff, key=lambda kv: -kv[1])]
        total = sum(f for _, f in eff) or 1.0
        cont_amounts = {p: f / total for p, f in eff}
        fractions = np.array([cont_amounts[p] for p in keys])
        options.append({
            'pigments': keys,
            'num_pigments': len(keys),
            'continuous': _recipe_result(bases, keys, cont_amounts, target_labs),
            'rounded': _round_recipe(bases, keys, fractions, target_labs),
        })

    # Reachability is the closest achievable D65 match — the best continuous recipe's
    # headline ΔE (rounding to drops can only add error, so the continuous solve is the
    # true gamut-distance verdict).
    best_de = min((o['continuous']['delta_e'] for o in options), default=float('inf'))
    return {'options': options, 'reachability': _reachability(best_de)}


def _round_recipe(bases, keys, fractions, target, max_total=12):
    """Snap continuous fractions to practical integer 'drops'. Try every small total,
    re-score the metameric cost on the rounded recipe, then do a ±1-drop local search —
    keeping the lowest-cost integer recipe (rounding the optimum naively can hurt the match)."""
    fractions = np.asarray(fractions)

    def score(vec):
        amounts = {keys[i]: int(vec[i]) for i in range(len(keys))}
        if sum(amounts.values()) == 0:
            return 1e6, amounts
        mixed = mix_amounts(bases, amounts)
        return _metameric_cost(delta_e_by_illuminant(target, mixed.R)), amounts

    best_vec, best_de = None, np.inf
    for total in range(2, max_total + 1):
        vec = np.round(fractions * total).astype(int)
        vec = np.clip(vec, 0, None)
        if vec.sum() == 0:
            continue
        d, _ = score(vec)
        if d < best_de:
            best_de, best_vec = d, vec

    if best_vec is None:  # degenerate: put 1 drop on the strongest fraction
        best_vec = np.zeros(len(keys), dtype=int)
        best_vec[int(np.argmax(fractions))] = 1

    # ±1-drop hill climb around the best integer vector.
    improved = True
    while improved:
        improved = False
        base_de, _ = score(best_vec)
        for i in range(len(keys)):
            for delta in (-1, 1):
                cand = best_vec.copy()
                cand[i] += delta
                if cand[i] < 0 or cand.sum() == 0:
                    continue
                d, _ = score(cand)
                if d < base_de - 1e-9:
                    best_vec, base_de, improved = cand, d, True

    amounts = {keys[i]: int(best_vec[i]) for i in range(len(keys)) if best_vec[i] > 0}
    return _recipe_result(bases, list(amounts.keys()), amounts, target)


def solve_mix(target_color, bases, seed=0, significance=0.03):
    """A single best continuous recipe for `target_color` over ALL of `bases` — the fast,
    palette-agnostic "give me a mix" solve.

    Unlike solve_recipe (exhaustive 2ⁿ subset sweep + Pareto front, tuned for the fixed five),
    this does one multi-start continuous solve over every pigment in the palette, drops the
    insignificant ones, and re-solves on what's left for a clean recipe. That stays fast for
    the larger gamut palettes (8–16 pigments) where the subset sweep would blow up. Same
    multi-illuminant objective, so the recipe resists metamerism.

    Returns a _recipe_result dict (amounts as normalised fractions summing to 1, achieved
    sRGB, headline ΔE, per-illuminant breakdown, metamerism index) plus a 'reachability'
    verdict — or None if there are no bases.
    """
    keys = list(bases.keys())
    if not keys:
        return None
    target_labs = _labs_under_all(target_color.R)
    rng = np.random.default_rng(seed)

    fracs, _ = _best_for_subset(target_labs, bases, keys, rng, light=len(keys) > 6)
    eff = [keys[i] for i in range(len(keys)) if fracs[i] >= significance]
    if not eff:
        eff = [keys[int(np.argmax(fracs))]]
    if len(eff) < len(keys):                      # tighten on the pigments that matter
        fracs2, _ = _best_for_subset(target_labs, bases, eff, rng)
        amounts = {eff[i]: float(fracs2[i]) for i in range(len(eff)) if fracs2[i] > 1e-6}
    else:
        amounts = {keys[i]: float(fracs[i]) for i in range(len(keys)) if fracs[i] > 1e-6}

    total = sum(amounts.values()) or 1.0
    amounts = {k: v / total for k, v in amounts.items()}
    result = _recipe_result(bases, list(amounts.keys()), amounts, target_labs)
    result['reachability'] = _reachability(result['delta_e'])
    return result
