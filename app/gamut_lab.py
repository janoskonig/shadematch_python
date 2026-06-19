"""Runtime gamut engine for the interactive Gamut Lab (/gamut).

Same model as scripts/gamut/optimize.py, but self-contained and fast enough to run
inside a web request: it loads the pre-built pigment library (app/data/
pigments_library.json — 327 Kremer pigments from Zenodo 5592485 + Titanium White) and,
for any chosen pigment set, measures the CIELAB convex-hull volume of all Kubelka–Munk
mixtures (pure + pairwise), using the exact /spectral engine (app/spectral_km.py).

Why pure + pairwise: in this single-constant KM model a mixture's K/S is a
concentration-weighted average of the pigments' K/S, so the reachable gamut depends only
on the reflectance curves (not tinting strength); the hull boundary in a subtractive
system is driven by pure pigments and 2-pigment mixes. This makes one set's gamut a
handful of cheap matrix ops + a 3-D ConvexHull.

Public API:
  catalog()                      -> [pigment dicts] for the picker
  shipped_baseline()             -> {volume, pnumbers}
  gamut_volume(pnumbers)         -> float
  gamut_detail(pnumbers)         -> {volume, ab_hull, pigment_points, ...} for plotting
  greedy(locked, size, pool)     -> ordered [pigment + volume_after + delta]
"""
import json
import os

import numpy as np
from scipy.spatial import ConvexHull, Delaunay, QhullError

from . import spectral_km as E

_DATA = os.path.join(os.path.dirname(__file__), 'data', 'pigments_library.json')
_RATIOS = np.linspace(0.1, 0.9, 9)   # interior pairwise-mix steps

# ΔE2000 reachability thresholds reported by the coverage metric (imperceptible / very
# good / edge-of-gamut — the same bands the /reverse_engineer reachability verdict uses).
_DE_THRESHOLDS = (1.0, 3.0, 6.0)

_STATE = None
_CATALOG_LAB = None   # (n,3) masstone CIELAB of every catalog pigment — the coverage targets
_REF_VOLUME = None    # CIELAB convex-hull volume of all catalog masstones (coverage reference)


def _load():
    """Load + cache the library: pigment metadata, KS matrix, pnumber→index."""
    global _STATE
    if _STATE is not None:
        return _STATE
    lib = json.load(open(_DATA))
    P = lib['pigments']
    R = np.clip(np.array([p['R'] for p in P], dtype=float), 1e-4, 1.0)
    KS = E.ks(R)                                  # (n, 38)
    index = {str(p['pnumber']): i for i, p in enumerate(P)}
    # Shipped five (for the baseline comparison line).
    shipped_pn = [str(lib['shipped_bases'][k]) for k in ('white', 'black', 'red', 'yellow', 'blue')]
    _STATE = {'lib': lib, 'P': P, 'KS': KS, 'index': index, 'shipped_pn': shipped_pn}
    return _STATE


def _idx(pnumbers):
    """Resolve pnumbers → unique library indices, preserving order, skipping unknowns."""
    index = _load()['index']
    seen, out = set(), []
    for pn in pnumbers:
        i = index.get(str(pn))
        if i is not None and i not in seen:
            seen.add(i)
            out.append(i)
    return out


# ── Gamut geometry ──────────────────────────────────────────────────────────
def _labs_from_ks(ks_rows):
    """(m,38) K/S → (m,3) CIELAB under the engine D65 white (vectorised)."""
    R = E.km(ks_rows)
    XYZ = R @ E.CMF.T
    ratios = XYZ / E.WHITE_XYZ
    f = np.where(ratios > 0.008856451679035631, np.cbrt(ratios),
                 7.787037037037037 * ratios + 16.0 / 116.0)
    return np.stack([116.0 * f[:, 1] - 16.0,
                     500.0 * (f[:, 0] - f[:, 1]),
                     200.0 * (f[:, 1] - f[:, 2])], axis=1)


def _sample_labs(idx):
    """Lab samples of the reachable gamut for indices `idx`: pures + pairwise mixes."""
    KS = _load()['KS']
    ks = KS[idx]
    chunks = [ks]
    if len(idx) >= 2:
        ii, jj = np.triu_indices(len(idx), k=1)
        mix = (_RATIOS[None, :, None] * ks[ii][:, None, :]
               + (1 - _RATIOS)[None, :, None] * ks[jj][:, None, :])
        chunks.append(mix.reshape(-1, ks.shape[1]))
    return _labs_from_ks(np.concatenate(chunks, axis=0))


def _hull_volume(points):
    try:
        return float(ConvexHull(points).volume)
    except (QhullError, ValueError):
        return 0.0


def _spread(points):
    c = points.mean(axis=0)
    return float(np.linalg.norm(points - c, axis=1).mean())


# ── Coverage error (ΔE2000 reachability of the catalog) ─────────────────────
def _catalog_lab():
    """Cache + return the (n,3) masstone CIELAB of every catalog pigment, and as a side
    effect the convex-hull volume of that cloud — the reference gamut coverage is measured
    against. These masstones are real, achievable surface colours, so they make an honest
    "can this palette reach the colours that exist?" target set."""
    global _CATALOG_LAB, _REF_VOLUME
    if _CATALOG_LAB is None:
        P = _load()['P']
        _CATALOG_LAB = np.array([p['lab'] for p in P], dtype=float)
        _REF_VOLUME = _hull_volume(_CATALOG_LAB)
    return _CATALOG_LAB


def _coverage_from_samples(samples, volume):
    """Coverage of the catalog masstones by a reachable gamut given as its Lab sample cloud.

    Two complementary numbers, per the colour-reproduction literature:
      • volume_coverage_pct — this palette's 3-D CIELAB gamut volume as a share of the whole
        catalog's gamut volume (extent).
      • a ΔE2000 reachability distribution — a target masstone counts as reachable if it lies
        *inside* the reachable convex hull (gamut-containment); for targets outside the hull
        the coverage error is the ΔE2000 to the nearest reachable sample. Reported as
        mean/median/p90/max over all targets (inside-hull targets scored 0) plus the share of
        targets reachable within each ΔE band. mean_delta_e is the headline coverage error.
    """
    targets = _catalog_lab()
    n = int(targets.shape[0])
    out = {
        'targets': n, 'reference_volume': round(_REF_VOLUME, 1),
        'volume_coverage_pct': 0.0, 'containment_pct': 0.0,
        'mean_delta_e': None, 'median_delta_e': None, 'p90_delta_e': None, 'max_delta_e': None,
        'within': {str(t): 0.0 for t in _DE_THRESHOLDS},
    }
    if samples is None or len(samples) < 4 or not _REF_VOLUME:
        return out
    out['volume_coverage_pct'] = round(100.0 * volume / _REF_VOLUME, 1)

    # Gamut-containment: targets inside the reachable hull are reachable (ΔE → 0).
    try:
        inside = Delaunay(samples).find_simplex(targets) >= 0
    except (QhullError, ValueError):
        inside = np.zeros(n, dtype=bool)

    # ΔE2000 from each *outside* target to its nearest reachable sample = the coverage error.
    de = np.zeros(n, dtype=float)
    out_idx = np.where(~inside)[0]
    if out_idx.size:
        d = E.ciede2000(targets[out_idx][:, None, :], samples[None, :, :])   # (k, s)
        de[out_idx] = d.min(axis=1)

    out['containment_pct'] = round(100.0 * float(inside.mean()), 1)
    out['mean_delta_e'] = round(float(de.mean()), 2)
    out['median_delta_e'] = round(float(np.median(de)), 2)
    out['p90_delta_e'] = round(float(np.percentile(de, 90)), 2)
    out['max_delta_e'] = round(float(de.max()), 2)
    out['within'] = {str(t): round(100.0 * float((de <= t).mean()), 1) for t in _DE_THRESHOLDS}
    return out


def coverage(pnumbers):
    """ΔE2000 + volume coverage of the catalog masstones by the chosen pigment set."""
    idx = _idx(pnumbers)
    if len(idx) < 4:
        return _coverage_from_samples(None, 0.0)
    labs = _sample_labs(idx)
    return _coverage_from_samples(labs, _hull_volume(labs))


# ── Human skin-colour gamut (a*–b* reference overlay) ───────────────────────
# Measured mean CIELAB skin colour from Xiao et al. (2017, Skin Research & Technology
# 23(1):21–29; n=960), Table 2 — one value per ethnicity × body site. The paper
# spectrophotometrically sampled four ethnic groups (Caucasian, Chinese, Kurdish, Thai)
# at four body sites (forehead, cheek = facial; back of hand, inner arm), so the skin
# "gamut" here is the convex hull of those 16 mean chromaticities in the a*–b* plane.
# (L* is carried for the tooltip but this plot's axes are a*–b*. Individual person-to-
# person spread is wider than the means — see their Fig. 1.)
_SKIN_MEANS = {   # ethnicity → site → (L*, a*, b*)
    'Caucasian': {'forehead': (59.2, 11.6, 15.1), 'cheek': (59.6, 11.8, 14.6),
                  'back of hand': (60.1, 7.4, 14.7), 'inner arm': (63.0, 5.6, 14.0)},
    'Chinese':   {'forehead': (56.4, 11.7, 16.3), 'cheek': (58.9, 11.4, 14.2),
                  'back of hand': (57.9, 9.3, 16.1), 'inner arm': (60.9, 7.0, 15.0)},
    'Kurdish':   {'forehead': (56.1, 11.3, 16.4), 'cheek': (58.0, 11.7, 15.8),
                  'back of hand': (57.3, 8.6, 16.5), 'inner arm': (60.6, 6.5, 16.4)},
    'Thai':      {'forehead': (56.8, 11.6, 17.7), 'cheek': (60.7, 10.5, 17.2),
                  'back of hand': (57.6, 9.4, 19.0), 'inner arm': (61.9, 7.1, 17.4)},
}
_FACIAL_SITES = {'forehead', 'cheek'}


def skin_gamut():
    """The human skin-colour gamut for the a*–b* plot: the 16 measured mean chromaticities
    (per ethnicity × body site) from Xiao et al. 2017, plus their convex hull + citation."""
    points = [
        {'ethnicity': eth, 'site': site, 'L': L, 'a': a, 'b': b,
         'facial': site in _FACIAL_SITES}
        for eth, sites in _SKIN_MEANS.items()
        for site, (L, a, b) in sites.items()
    ]
    ab = np.array([[p['a'], p['b']] for p in points])
    try:
        h = ConvexHull(ab)
        hull = [[round(float(ab[v, 0]), 2), round(float(ab[v, 1]), 2)] for v in h.vertices]
    except (QhullError, ValueError):
        hull = []
    return {
        'points': points, 'hull': hull,
        'label': 'human skin (Xiao 2017)',
        'cite': 'Mean skin chromaticities — 4 ethnicities (Caucasian/Chinese/Kurdish/Thai) '
                '× 4 body sites (forehead, cheek, hand, arm): Xiao et al. 2017, Skin Res. '
                'Technol. 23(1):21–29, Table 2 (n=960). Dashed hull = convex hull of the 16 '
                'site means; individual spread is wider (their Fig. 1).',
    }


def _lab_to_srgb(L, a, b):
    """Standard CIELAB (D65, 2° observer) → 8-bit sRGB, for display swatches and the
    metameric target-curve reconstruction the spectral mixer does from sRGB. Uses the
    sRGB D65 reference white (not the engine's 38-bin white) so the swatch matches how a
    spectrophotometer's CIELAB reading would render on screen."""
    Xn, Yn, Zn = 0.95047, 1.0, 1.08883   # D65 2° white, Y normalised to 1
    fy = (L + 16.0) / 116.0
    fx, fz = fy + a / 500.0, fy - b / 200.0

    def inv(t):
        return t ** 3 if t ** 3 > 0.008856451679035631 else (t - 16.0 / 116.0) / 7.787037037037037

    xyz = np.array([Xn * inv(fx), Yn * inv(fy), Zn * inv(fz)])
    srgb = E._compand(np.clip(E.XYZ_RGB @ xyz, 0.0, 1.0))
    return [int(round(c)) for c in np.clip(srgb * 255.0, 0, 255)]


def skin_targets():
    """The Xiao 2017 mean skin chromaticities as spectral-mixer target colours: one per
    ethnicity × body site, each {id, rgb, name, …} ready to drop into the mixer catalog."""
    out = []
    for eth, sites in _SKIN_MEANS.items():
        for site, (L, a, b) in sites.items():
            out.append({
                'id': 'skin-%s-%s' % (eth.lower(), site.replace(' ', '_')),
                'rgb': _lab_to_srgb(L, a, b),
                'name': '%s · %s' % (eth, site),
                'ethnicity': eth, 'site': site, 'facial': site in _FACIAL_SITES,
                'lab': [L, a, b],
            })
    return out


def gamut_volume(pnumbers):
    idx = _idx(pnumbers)
    if len(idx) < 4:
        return 0.0
    return _hull_volume(_sample_labs(idx))


# ── Public, picker-facing ───────────────────────────────────────────────────
def _rec(i):
    p = _load()['P'][i]
    return {'pnumber': p['pnumber'], 'name': p['name'], 'group': p['group'] or 'other',
            'family': p.get('family', ''), 'hue': p['hue'], 'chroma': p['chroma'],
            'lab': p['lab'], 'srgb': p['srgb'], 'tinting': p.get('tinting', 1.0)}


def catalog():
    P = _load()['P']
    return [_rec(i) for i in range(len(P))]


def shipped_baseline():
    st = _load()
    d = gamut_detail(st['shipped_pn'])
    return {'volume': d['volume'], 'pnumbers': st['shipped_pn'],
            'ab_hull': d['ab_hull'], 'coverage': d['coverage']}


def gamut_detail(pnumbers):
    """Volume + a*b* hull polygon (+ pure-pigment points) for the chosen set, for plotting."""
    idx = _idx(pnumbers)
    st = _load()
    out = {'volume': 0.0, 'n': len(idx), 'ab_hull': [],
           'coverage': _coverage_from_samples(None, 0.0),
           'pigment_points': [{'a': st['P'][i]['lab'][1], 'b': st['P'][i]['lab'][2],
                               'srgb': st['P'][i]['srgb'], 'name': st['P'][i]['name']} for i in idx]}
    if len(idx) < 2:
        return out
    labs = _sample_labs(idx)
    out['volume'] = round(_hull_volume(labs), 1) if len(idx) >= 4 else 0.0
    out['coverage'] = _coverage_from_samples(labs if len(idx) >= 4 else None, out['volume'])
    ab = labs[:, 1:3]
    try:
        h = ConvexHull(ab)
        out['ab_hull'] = [[round(float(ab[v, 0]), 2), round(float(ab[v, 1]), 2)] for v in h.vertices]
    except (QhullError, ValueError):
        pass
    return out


# ── Greedy widest-gamut search ──────────────────────────────────────────────
def greedy(locked=None, size=8, pool=None, max_pool=400):
    """Grow a palette to `size` pigments, maximising CIELAB gamut volume at each step.

    locked : pnumbers to force-include first (in order). If fewer than 2, the search
             seeds with the lightest + darkest pigment in the pool so the hull is
             non-degenerate.
    pool   : candidate pnumbers to choose from (default: whole catalog).
    Returns the ordered chosen pigments, each annotated with the gamut volume reached
    and the marginal gain it added.
    """
    st = _load()
    P, KS = st['P'], st['KS']
    n = len(P)
    pool_idx = _idx(pool) if pool else list(range(n))
    pool_idx = pool_idx[:max_pool]
    locked_idx = _idx(locked or [])
    # Locked pigments must be selectable even if not in the pool. Sorted for determinism.
    pool_set = sorted(set(pool_idx) | set(locked_idx))

    chosen = list(locked_idx)
    # Seed to 2 non-degenerate extremes if needed (lightest then darkest available).
    if len(chosen) < 2:
        avail = [i for i in pool_set if i not in chosen]
        avail_light = sorted(avail, key=lambda i: -P[i]['lab'][0])
        avail_dark = sorted(avail, key=lambda i: P[i]['lab'][0] + 2.0 * P[i]['chroma'])
        for cand in (avail_light[:1] + avail_dark[:1]):
            if len(chosen) >= 2:
                break
            if cand not in chosen:
                chosen.append(cand)

    size = max(len(chosen), min(int(size), len(pool_set)))
    seq = []
    prev_vol = gamut_volume([P[i]['pnumber'] for i in chosen]) if len(chosen) >= 4 else 0.0
    # Record the seed/locked pigments first.
    for i in chosen:
        v = _hull_volume(_sample_labs(chosen[:chosen.index(i) + 1])) if chosen.index(i) + 1 >= 4 else 0.0
        seq.append({**_rec(i), 'volume_after': round(v, 1), 'delta': None, 'locked': i in locked_idx})

    while len(chosen) < size:
        base = set(chosen)
        best, best_vol, best_spr = None, -1.0, -1.0
        for c in pool_set:
            if c in base:
                continue
            pts = _sample_labs(chosen + [c])
            vol = _hull_volume(pts)
            if vol > best_vol + 1e-9 or (abs(vol - best_vol) <= 1e-9 and _spread(pts) > best_spr):
                best, best_vol, best_spr = c, vol, _spread(pts)
        if best is None:
            break
        chosen.append(best)
        seq.append({**_rec(best), 'volume_after': round(best_vol, 1),
                    'delta': round(best_vol - prev_vol, 1) if len(chosen) >= 5 else None,
                    'locked': False})
        prev_vol = best_vol

    detail = gamut_detail([P[i]['pnumber'] for i in chosen])
    return {'sequence': seq, 'total_volume': round(prev_vol, 1),
            'baseline': shipped_baseline(), 'coverage': detail['coverage'],
            'ab_hull': detail['ab_hull'], 'pigment_points': detail['pigment_points']}
