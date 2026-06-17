"""Single-constant Kubelka–Munk engine + recipe solver — a faithful Python port of the
JS engine in static/spectral.js, so a recipe found here reproduces *exactly* what the
/spectral lab renders.

Why this exists: the old /reverse_engineer (app.utils.reverse_engineer_recipe) linearly
averaged reflectance curves and minimised XYZ Euclidean distance — physically wrong, and
not the model /spectral uses. This module mixes pigments the way /spectral does
(Kubelka–Munk in K/S space with measured tinting strengths) and inverts that model with a
global search against a perceptual ΔE2000 objective.

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


def _recipe_result(bases, keys, amounts, target_lab):
    """Package a recipe (dict key→amount) with its achieved colour and ΔE."""
    mixed = mix_amounts(bases, amounts)
    total = sum(amounts.values())
    return {
        'amounts': amounts,
        'percentages': {k: (100.0 * amounts[k] / total if total else 0.0) for k in amounts},
        'achieved_rgb': mixed.sRGB,
        'delta_e': _delta_e(target_lab, mixed.lab),
    }


def _best_for_subset(target_lab, bases, keys, rng):
    """Best continuous mix of exactly `keys` (a pigment subset), as normalised fractions.

    Low-dimensional and fairly smooth once the forward model and objective are correct, so a
    multi-start L-BFGS-B (each base alone, the centroid, plus a few seeded random starts)
    finds the subset optimum reliably without a full global search per subset. Returns
    (fractions array aligned to `keys`, ΔE)."""
    n = len(keys)

    def objective(x):
        s = x.sum()
        if s <= 1e-9:
            return 1e6
        amounts = {keys[i]: float(x[i]) for i in range(n)}
        return _delta_e(target_lab, mix_amounts(bases, amounts).lab)

    starts = [np.full(n, 1.0 / n)]                 # centroid
    starts += [np.eye(n)[i] for i in range(n)]     # each pigment alone
    starts += [rng.random(n) for _ in range(3)]    # seeded random

    bounds = [(0.0, 1.0)] * n
    best_x, best_f = None, np.inf
    for s0 in starts:
        res = minimize(objective, s0, method='L-BFGS-B', bounds=bounds)
        if res.fun < best_f:
            best_f, best_x = res.fun, res.x

    total = best_x.sum() or 1.0
    return best_x / total, float(best_f)


def solve_recipe(target_color, bases, seed=0, max_options=3, significance=0.02):
    """Match target_color (a SpectralColor) with the measured bases, minimising ΔE2000.

    Adopts the reference engine's solver *strategy* (yargo13/color-formulation): instead of
    one recipe that uses every pigment, it does subset selection and returns several recipe
    options trading simplicity against accuracy. With only five bases we can be exhaustive
    rather than greedy — every non-empty pigment subset is solved, each result reduced to the
    pigments that actually carry weight, and the **Pareto front** over (pigment count, ΔE) is
    surfaced: for each level of simplicity, the best match achievable.

    Returns {'options': [opt, …]} ordered best-ΔE first (so options[0] is the headline
    recipe). Each opt = {pigments, num_pigments, continuous:{…}, rounded:{…}}.
    """
    keys_all = [k for k in ORDER if k in bases]
    target_lab = target_color.lab
    rng = np.random.default_rng(seed)

    # Solve every subset, then collapse to one entry per *effective* pigment set (the
    # pigments left after dropping near-zero ones), keeping the lowest-ΔE solve for each.
    by_effset = {}
    for k in range(1, len(keys_all) + 1):
        for subset in combinations(keys_all, k):
            fracs, de = _best_for_subset(target_lab, bases, list(subset), rng)
            eff = [(subset[i], fracs[i]) for i in range(len(subset)) if fracs[i] >= significance]
            if not eff:
                continue
            effset = frozenset(p for p, _ in eff)
            if effset not in by_effset or de < by_effset[effset][1]:
                by_effset[effset] = (eff, de)

    # Pareto front over (number of pigments, ΔE): keep a recipe only if no simpler-or-equal
    # recipe also matches better. This is the simplicity/accuracy trade-off ladder.
    entries = sorted(by_effset.values(), key=lambda e: (len(e[0]), e[1]))
    front, best_de_so_far = [], np.inf
    for eff, de in entries:
        if de < best_de_so_far - 1e-9:        # str* improvement over anything simpler
            front.append((eff, de))
            best_de_so_far = de

    # Build option dicts (best ΔE first), capped at max_options.
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
            'continuous': _recipe_result(bases, keys, cont_amounts, target_lab),
            'rounded': _round_recipe(bases, keys, fractions, target_lab),
        })
    return {'options': options}


def _round_recipe(bases, keys, fractions, target_lab, max_total=12):
    """Snap continuous fractions to practical integer 'drops'. Try every small total,
    re-score ΔE on the rounded recipe, then do a ±1-drop local search — keeping the
    lowest-ΔE integer recipe (rounding the optimum naively can hurt the match)."""
    fractions = np.asarray(fractions)

    def score(vec):
        amounts = {keys[i]: int(vec[i]) for i in range(len(keys))}
        if sum(amounts.values()) == 0:
            return 1e6, amounts
        mixed = mix_amounts(bases, amounts)
        return _delta_e(target_lab, mixed.lab), amounts

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
    return _recipe_result(bases, list(amounts.keys()), amounts, target_lab)
