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
import json
import math
from pathlib import Path
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


# ---------------------------------------------------------------------------
# FROZEN match clusters (protocol: fixed, versioned for the study period)
# ---------------------------------------------------------------------------
# Match drawing does NOT use the runtime partition above (that one serves the
# /stat/riport report and recomputes when the catalog changes). Matches use a
# frozen, versioned artifact generated once by scripts/freeze_match_clusters.py:
# 10 k-means clusters over the even-coverage background gamut ONLY — the Xiao
# skin-zone targets (even_gamut_v2_skin) are excluded from matches by design
# (their densified cluster would distort the cluster-balanced estimand; skin
# returns in the spectral-mixing version). Entries are keyed by
# catalog_order + RGB so the same file works across DBs; RGB mismatches are
# treated as "not in the frozen set" (silent catalog drift cannot re-cluster).

MATCH_CLUSTERS_VERSION = 'mc-v1'
_MATCH_CLUSTERS_FILE = Path(__file__).resolve().parents[1] / 'data' / (
    'match_clusters_%s.json' % MATCH_CLUSTERS_VERSION)

_MATCH_FILE_CACHE: Dict | None = None
_MATCH_ASSIGN_CACHE: Dict[str, Dict[int, str]] = {}

MATCH_CLUSTER_ORDER = ['c%d' % i for i in range(10)]


def _match_file() -> Dict:
    global _MATCH_FILE_CACHE
    if _MATCH_FILE_CACHE is None:
        with open(_MATCH_CLUSTERS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        data['_by_order'] = {
            e['catalog_order']: e for e in data['entries']
        }
        _MATCH_FILE_CACHE = data
    return _MATCH_FILE_CACHE


def match_cluster_assignments() -> Dict[int, str]:
    """target_color_id → 'c0'..'c9' from the FROZEN artifact, for the current
    catalog rows whose (catalog_order, rgb) match a frozen entry. Cached per
    catalog fingerprint."""
    rows = _gamut_rows()
    fp = catalog_fingerprint(rows)
    if fp not in _MATCH_ASSIGN_CACHE:
        by_order = _match_file()['_by_order']
        out = {}
        for tc in rows:
            e = by_order.get(tc.catalog_order)
            if e is not None and e['rgb'] == [tc.r, tc.g, tc.b]:
                out[tc.id] = e['cluster']
        _MATCH_ASSIGN_CACHE.clear()
        _MATCH_ASSIGN_CACHE[fp] = out
    return _MATCH_ASSIGN_CACHE[fp]


def match_cluster_names() -> Dict[str, str]:
    """'c0'..'c9' → Hungarian display name (frozen with the assignment)."""
    return _match_file()['names']
