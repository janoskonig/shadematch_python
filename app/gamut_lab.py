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
from scipy.spatial import ConvexHull, QhullError

from . import spectral_km as E

_DATA = os.path.join(os.path.dirname(__file__), 'data', 'pigments_library.json')
_RATIOS = np.linspace(0.1, 0.9, 9)   # interior pairwise-mix steps

_STATE = None


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
    return {'volume': d['volume'], 'pnumbers': st['shipped_pn'], 'ab_hull': d['ab_hull']}


def gamut_detail(pnumbers):
    """Volume + a*b* hull polygon (+ pure-pigment points) for the chosen set, for plotting."""
    idx = _idx(pnumbers)
    st = _load()
    out = {'volume': 0.0, 'n': len(idx), 'ab_hull': [],
           'pigment_points': [{'a': st['P'][i]['lab'][1], 'b': st['P'][i]['lab'][2],
                               'srgb': st['P'][i]['srgb'], 'name': st['P'][i]['name']} for i in idx]}
    if len(idx) < 2:
        return out
    labs = _sample_labs(idx)
    out['volume'] = round(_hull_volume(labs), 1) if len(idx) >= 4 else 0.0
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
            'baseline': shipped_baseline(),
            'ab_hull': detail['ab_hull'], 'pigment_points': detail['pigment_points']}
