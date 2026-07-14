"""Shared 10-macro-cluster assignment of the gamut catalog.

Single source of truth for the colour "families" used both by the /stat/riport
learning regions and by match drawing (a match = one target from each cluster).

Partition of the ACTUAL gamut, mirroring how the target catalog was generated
(even coverage of the reachable gamut, densified skin zone):
  * background targets (classification != 'even_gamut_v2_skin'): k-means over
    their CIELAB coordinates (fixed seed → deterministic), BG_K clusters,
    relabelled 'k0'..'k5' by centroid (L, a, b) sort;
  * skin targets (even_gamut_v2_skin): assigned to the ORIGINAL Xiao et al.
    (2017) clusters — each goes to the ethnicity (Caucasian/Chinese/Kurdish/
    Thai) whose nearest published site-mean (4 ethnicities × 4 body sites = 16
    Lab means, see gamut_lab._SKIN_MEANS) is closest in CIELAB → 'sk0'..'sk3'.

Nothing is persisted: the assignment is a pure function of the gamut rows,
cached in-process keyed by a catalog fingerprint. A gamut reload changes the
fingerprint and triggers a recompute; match rounds snapshot their cluster code
at match creation, so in-flight matches are unaffected by re-clustering.
"""
from __future__ import annotations

import hashlib
import math
from typing import Dict, List, Tuple

import numpy as np

from .models import TargetColor
from .regions import _srgb_to_lab

BG_K = 6      # background gamut clusters
XIAO_ETHNICITIES = ['Caucasian', 'Chinese', 'Kurdish', 'Thai']
XIAO_NAME_HU = {'Caucasian': 'bőrszín – kaukázusi', 'Chinese': 'bőrszín – kínai',
                'Kurdish': 'bőrszín – kurd', 'Thai': 'bőrszín – thai'}

MACRO_ORDER = (['k%d' % i for i in range(BG_K)]
               + ['sk%d' % i for i in range(len(XIAO_ETHNICITIES))])


def _region_name(L, a, b):
    light = 'sötét' if L < 35 else ('közepes' if L <= 65 else 'világos')
    C = math.hypot(a, b)
    if C < 15:
        hue = 'szürkés-semleges'
    else:
        h = math.degrees(math.atan2(b, a)) % 360
        if h < 25 or h >= 345:
            hue = 'piros'
        elif h < 70:
            hue = 'narancs–barna'
        elif h < 105:
            hue = 'sárga'
        elif h < 135:
            hue = 'sárgászöld'
        elif h < 190:
            hue = 'zöld'
        elif h < 230:
            hue = 'kékeszöld'
        elif h < 290:
            hue = 'kék'
        else:
            hue = 'lila–bíbor'
    return '%s %s' % (light, hue)


def _kmeans(X, K, seed=42):
    rng = np.random.default_rng(seed)
    cen = [X[rng.integers(len(X))]]
    for _ in range(K - 1):
        d2 = np.min([((X - c) ** 2).sum(1) for c in cen], axis=0)
        cen.append(X[rng.choice(len(X), p=d2 / d2.sum())])
    C = np.array(cen)
    assign = np.zeros(len(X), dtype=int)
    for _ in range(200):
        na = np.argmin(((X[:, None, :] - C[None, :, :]) ** 2).sum(2), axis=1)
        nC = np.array([X[na == k].mean(0) if (na == k).any() else C[k]
                       for k in range(K)])
        if np.array_equal(na, assign) and np.allclose(nC, C):
            break
        assign, C = na, nC
    return assign, C


def _gamut_rows() -> List[TargetColor]:
    return (TargetColor.query
            .filter_by(color_type='gamut')
            .order_by(TargetColor.id.asc())
            .all())


def catalog_fingerprint(rows: List[TargetColor] | None = None) -> str:
    """Deterministic digest of the clustering-relevant gamut catalog state."""
    if rows is None:
        rows = _gamut_rows()
    payload = ';'.join(
        '%d,%d,%d,%d,%s' % (t.id, t.r, t.g, t.b, t.classification or '')
        for t in rows
    )
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def _compute(rows: List[TargetColor]) -> Dict:
    skin = [t for t in rows if t.classification == 'even_gamut_v2_skin']
    bg = [t for t in rows if t.classification != 'even_gamut_v2_skin']
    assignments: Dict[int, str] = {}
    labs: Dict[str, List[Tuple[float, float, float]]] = {c: [] for c in MACRO_ORDER}
    names: Dict[str, str] = {}

    if bg:
        Xb = np.array([_srgb_to_lab(t.r, t.g, t.b) for t in bg])
        assign_b, Cb = _kmeans(Xb, BG_K)
        # stable relabel by centroid, verbal light+hue names
        order_b = sorted(range(BG_K), key=lambda k: (Cb[k][0], Cb[k][1], Cb[k][2]))
        relabel_b = {old: 'k%d' % new for new, old in enumerate(order_b)}
        seen: Dict[str, int] = {}
        for i in range(BG_K):
            Lc, ac, bc = Cb[order_b[i]]
            nm = _region_name(float(Lc), float(ac), float(bc))
            seen[nm] = seen.get(nm, 0) + 1
            if seen[nm] > 1:
                nm = '%s %d.' % (nm, seen[nm])
            names['k%d' % i] = nm
        for t, lab, k in zip(bg, Xb, assign_b):
            reg = relabel_b[int(k)]
            assignments[t.id] = reg
            labs[reg].append(tuple(float(v) for v in lab))

    if skin:
        from .gamut_lab import skin_gamut
        xiao_pts = skin_gamut()['points']
        xiao_P = np.array([[q['L'], q['a'], q['b']] for q in xiao_pts])
        xiao_eth = [q['ethnicity'] for q in xiao_pts]
        skin_reg_of = {eth: 'sk%d' % i for i, eth in enumerate(XIAO_ETHNICITIES)}
        Xs = np.array([_srgb_to_lab(t.r, t.g, t.b) for t in skin])
        for t, lab in zip(skin, Xs):
            eth = xiao_eth[int(np.argmin(((xiao_P - lab) ** 2).sum(1)))]
            reg = skin_reg_of[eth]
            assignments[t.id] = reg
            labs[reg].append(tuple(float(v) for v in lab))
    for i, eth in enumerate(XIAO_ETHNICITIES):
        names['sk%d' % i] = XIAO_NAME_HU[eth]

    return {'assignments': assignments, 'names': names, 'labs': labs}


_CACHE: Dict[str, Dict] = {}


def _bundle() -> Dict:
    rows = _gamut_rows()
    fp = catalog_fingerprint(rows)
    if fp not in _CACHE:
        _CACHE.clear()   # one catalog at a time; drop stale clusterings
        _CACHE[fp] = _compute(rows)
        _CACHE[fp]['fingerprint'] = fp
    return _CACHE[fp]


def cluster_assignments() -> Dict[int, str]:
    """target_color_id → 'k0'..'k5' | 'sk0'..'sk3' for every gamut target."""
    return _bundle()['assignments']


def cluster_display_names() -> Dict[str, str]:
    """cluster code → Hungarian display name."""
    return _bundle()['names']


def cluster_labs() -> Dict[str, List[Tuple[float, float, float]]]:
    """cluster code → CIELAB tuples of its member targets (for centroid swatches)."""
    return _bundle()['labs']


def current_fingerprint() -> str:
    return _bundle()['fingerprint']
