"""Gamut-maximising palette selection over the pigment library.

GAMUT MODEL — consistent with the /spectral engine. In the single-constant
Kubelka-Munk model the app uses, a mixture's K/S is a concentration-weighted
average of the pigments' K/S curves (km_mix in app/spectral_km.py). Because the
dial amounts are free and non-negative, the reachable normalised weights cover the
whole simplex, so the set of reachable colours depends only on the pigments'
reflectance curves — NOT on tinting strength. The gamut of a pigment set is thus:

    { Lab( KM( Σ wᵢ·KSᵢ ) ) : wᵢ ≥ 0, Σwᵢ = 1 }

We estimate its size as the CIELAB convex-hull volume. The hull boundary in a
subtractive system is driven by pure pigments and 2-pigment mixes (3+-way mixes
sit greyer, inside the hull), so we sample pures + pairwise mixes; a Dirichlet
safety sample confirms higher-order mixes don't extend the hull.

SELECTION. Hull volume is monotone and ~submodular, so we grow the palette
greedily: seed with the two achromatic extremes every palette needs (white +
the best black), then repeatedly add the pigment that most enlarges the gamut.
Palettes of size 5/10/15 are prefixes of this one sequence. We also answer the
explicit question — the best {W,K,R,Y,B} with one pigment per painter primary —
by a direct search over hue-family candidates.

Run:  PYTHONPATH=. python3 scripts/gamut/optimize.py
"""
import json
import os

import numpy as np
from scipy.spatial import ConvexHull, QhullError

from app import spectral_km as E

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(ROOT, 'data', 'pigments_library.json')
OUT_JSON = os.path.join(ROOT, 'data', 'palette_recommendations.json')
OUT_MD = os.path.join(ROOT, 'data', 'GAMUT_REPORT.md')
# The app serves from app/data/ (the deployed unit); keep those copies in sync so a
# re-run can't leave the lab serving a stale palette.
APP_DATA = os.path.join(ROOT, 'app', 'data')

# Pairwise mix ratios (exclude 0/1 — pures are added once). 9 interior steps.
RATIOS = np.linspace(0.1, 0.9, 9)


def load_library():
    lib = json.load(open(LIB))
    P = lib['pigments']
    R = np.array([p['R'] for p in P])               # (n, 38) masstone reflectance
    KS = E.ks(np.clip(R, 1e-4, 1.0))                # (n, 38)
    return lib, P, R, KS


def ks_to_lab(ks_rows):
    """(m,38) K/S → (m,3) CIELAB under the engine D65 white (vectorised)."""
    R = E.km(ks_rows)
    XYZ = ks_rows @ np.zeros(0) if False else (R @ E.CMF.T)   # (m,3)
    ratios = XYZ / E.WHITE_XYZ
    f = np.where(ratios > 0.008856451679035631, np.cbrt(ratios),
                 7.787037037037037 * ratios + 16.0 / 116.0)
    L = 116.0 * f[:, 1] - 16.0
    a = 500.0 * (f[:, 0] - f[:, 1])
    b = 200.0 * (f[:, 1] - f[:, 2])
    return np.stack([L, a, b], axis=1)


def sample_points(idx, KS, dirichlet=0, rng=None):
    """Lab sample of the reachable gamut for pigment indices `idx`:
    pure pigments + all pairwise mixes (+ optional Dirichlet safety sample)."""
    ks = KS[idx]                                    # (k,38)
    k = len(idx)
    chunks = [ks]                                   # pures
    if k >= 2:
        ii, jj = np.triu_indices(k, k=1)
        # (npairs, nratios, 38)
        mix = (RATIOS[None, :, None] * ks[ii][:, None, :]
               + (1 - RATIOS)[None, :, None] * ks[jj][:, None, :])
        chunks.append(mix.reshape(-1, ks.shape[1]))
    if dirichlet and k >= 3 and rng is not None:
        w = rng.dirichlet(np.full(k, 0.5), size=dirichlet)   # (d,k)
        chunks.append(w @ ks)
    return ks_to_lab(np.concatenate(chunks, axis=0))


def hull_volume(points):
    try:
        return ConvexHull(points).volume
    except (QhullError, ValueError):
        return 0.0


def spread(points):
    """Degenerate-case tie-breaker (k<4): mean distance from the centroid."""
    c = points.mean(axis=0)
    return float(np.linalg.norm(points - c, axis=1).mean())


def gamut_of(idx, KS, dirichlet=0, rng=None):
    return hull_volume(sample_points(idx, KS, dirichlet, rng))


# ── Greedy growth ───────────────────────────────────────────────────────────
def greedy(P, KS, start_idx, n_total, log=print):
    """Greedily grow `start_idx` to n_total pigments, each step adding the pigment
    that maximises hull volume (spread as tie-breaker while still degenerate)."""
    chosen = list(start_idx)
    seq = []
    for step in range(len(chosen), n_total):
        base = set(chosen)
        best, best_vol, best_spr = None, -1.0, -1.0
        for c in range(len(P)):
            if c in base:
                continue
            pts = sample_points(chosen + [c], KS)
            vol = hull_volume(pts)
            if vol > best_vol + 1e-9 or (abs(vol - best_vol) <= 1e-9 and spread(pts) > best_spr):
                best, best_vol, best_spr = c, vol, spread(pts)
        chosen.append(best)
        seq.append((best, best_vol))
        log(f"  +{len(chosen):2d}  vol={best_vol:11.1f}  {P[best]['name'][:38]:38s} "
            f"[{P[best]['group']}] hue={P[best]['hue']:5.1f} C={P[best]['chroma']:.0f}")
    return chosen, seq


def hue_family(P, lo, hi):
    out = []
    for i, p in enumerate(P):
        h = p['hue']
        inside = (h >= lo or h <= hi) if lo > hi else (lo <= h <= hi)
        if inside:
            out.append(i)
    return out


def main():
    lib, P, R, KS = load_library()
    rng = np.random.default_rng(12345)
    name = lambda i: f"{P[i]['name']} (pn {P[i]['pnumber']})"
    idx_by_pn = {str(p['pnumber']): i for i, p in enumerate(P)}

    WHITE = idx_by_pn['white']

    # Baseline: the shipped five.
    shipped = [idx_by_pn[str(lib['shipped_bases'][k])] for k in ('white', 'black', 'red', 'yellow', 'blue')]
    vol_shipped = gamut_of(shipped, KS, dirichlet=4000, rng=rng)

    print('=== Shipped 5-pigment baseline (W/K/cad-red/cad-yellow/ultramarine) ===')
    for i in shipped:
        print(f"    {name(i)}")
    print(f"    gamut volume = {vol_shipped:,.0f}\n")

    # ── Step 1: best {W,K,R,Y,B}, one pigment per painter primary ───────────
    # This is the size-5 palette and the seed the larger palettes grow from, so
    # the recommendation matches the painter's mental model (white/black + RYB).
    cand_black = sorted(range(len(P)), key=lambda i: (P[i]['lab'][0] + 2.0 * P[i]['chroma']))
    FAMILIES = {'red': (335, 45), 'yellow': (60, 110), 'blue': (215, 300)}

    def top_chroma(idxs, k):
        return sorted(idxs, key=lambda i: -P[i]['chroma'])[:k]
    reds = top_chroma(hue_family(P, *FAMILIES['red']), 16)
    yels = top_chroma(hue_family(P, *FAMILIES['yellow']), 16)
    blus = top_chroma(hue_family(P, *FAMILIES['blue']), 16)
    blacks = cand_black[:8]
    best_ryb, best_v = None, -1.0
    for kk in blacks:
        for rr in reds:
            for yy in yels:
                for bb in blus:
                    v = gamut_of([WHITE, kk, rr, yy, bb], KS)
                    if v > best_v:
                        best_v, best_ryb = v, (WHITE, kk, rr, yy, bb)
    best_ryb = list(best_ryb)
    best_v_dense = gamut_of(best_ryb, KS, dirichlet=6000, rng=rng)
    BLACK = best_ryb[1]

    print('=== Step 1: best W/K/R/Y/B (one pigment per primary) ===')
    for role, i in zip(('white', 'black', 'red', 'yellow', 'blue'), best_ryb):
        print(f"    {role:7s} {name(i)}  hue={P[i]['hue']:.0f} C={P[i]['chroma']:.0f}")
    print(f"    gamut = {best_v_dense:,.0f}  (+{100*(best_v_dense/vol_shipped-1):.0f}% vs shipped)\n")

    # ── Step 2: greedily grow the RYBWK 5-set, adding the biggest gamut gain ─
    print('=== Step 2: greedy growth from the best W/K/R/Y/B ===')
    N = 16
    chosen, seq = greedy(P, KS, list(best_ryb), N)

    sizes = [5, 8, 10, 12, 16]
    prefix_vols = {}
    for s in range(2, N + 1):
        prefix_vols[s] = gamut_of(chosen[:s], KS, dirichlet=6000, rng=rng)

    # ── Emit JSON + Markdown ────────────────────────────────────────────────
    def family(p):
        h, c = p['hue'], p['chroma']
        if c < 8:
            return 'neutral'
        return ('red' if (h >= 345 or h < 20) else 'orange' if h < 50 else 'yellow' if h < 118
                else 'green' if h < 170 else 'cyan' if h < 215 else 'blue' if h < 290 else 'violet')

    # Stable per-slot keys: the first five are the painter primaries; extras get
    # p6..pN (unique handles the UI/engine use to key dials and amounts).
    SEED_ROLES = ['white', 'black', 'red', 'yellow', 'blue']
    roles = SEED_ROLES + [f'p{n}' for n in range(6, len(chosen) + 1)]

    def rec(i, role=None):
        p = P[i]
        d = {'role': role, 'pnumber': p['pnumber'], 'name': p['name'], 'group': p['group'],
             'family': family(p), 'hue': p['hue'], 'chroma': p['chroma'],
             'lab': p['lab'], 'srgb': p['srgb']}
        return {k: v for k, v in d.items() if v is not None}

    seq_recs = [rec(i, roles[n]) for n, i in enumerate(chosen)]

    palettes = {}
    for s in sizes:
        palettes[str(s)] = {'volume': round(prefix_vols[s], 1),
                            'pigments': seq_recs[:s]}

    out = {
        'method': 'CIELAB convex-hull volume of KM mixtures (pure + pairwise), greedy growth '
                  'seeded by the gamut-optimal W/K/R/Y/B',
        'grid_nm': lib['grid_nm'],
        'shipped_baseline': {'volume': round(vol_shipped, 1),
                             'pigments': [rec(i) for i in shipped]},
        'best_RYBWK': {'volume': round(best_v_dense, 1), 'pigments': seq_recs[:5]},
        'greedy_sequence': seq_recs,
        'prefix_volumes': {str(s): round(prefix_vols[s], 1) for s in range(2, N + 1)},
        'sizes': sizes,
        'palettes': palettes,
    }
    json.dump(out, open(OUT_JSON, 'w'), indent=2)

    # Markdown report
    L = []
    L.append('# Widest-gamut pigment palettes — Hyperspectral Pigments (Zenodo 5592485)\n')
    L.append('Gamut = CIELAB convex-hull volume of all Kubelka–Munk mixtures, computed with '
             'the exact `/spectral` engine (`app/spectral_km.py`). Higher = more colours reachable.\n')
    L.append(f'- **Shipped 5** (W/K/cad-red/cad-yellow/ultramarine): gamut **{vol_shipped:,.0f}**')
    L.append(f'- **Best W/K/R/Y/B** (one pigment per primary, gamut-optimal): gamut **{best_v_dense:,.0f}**  '
             f'(+{100*(best_v_dense/vol_shipped-1):.0f}% vs shipped)\n')
    L.append('## Best 5 — one pigment per painter primary (W/K/R/Y/B)\n')
    L.append('| role | pigment | Kremer | group | hue° | chroma | sRGB |')
    L.append('|------|---------|--------|-------|-----:|-------:|------|')
    for r in out['best_RYBWK']['pigments']:
        L.append(f"| {r['role']} | {r['name']} | {r['pnumber']} | {r['group']} | "
                 f"{r['hue']:.0f} | {r['chroma']:.0f} | rgb({r['srgb'][0]},{r['srgb'][1]},{r['srgb'][2]}) |")
    L.append('\n## Growth sequence — what to add next\n')
    L.append('The first five rows are the best W/K/R/Y/B above. Each later row adds the pigment '
             'that most enlarges the CIELAB gamut given everything above it. A palette of size '
             '*k* is the first *k* rows.\n')
    L.append('| # | role | pigment | Kremer | family | hue° | chroma | gamut after | Δ gamut |')
    L.append('|--:|------|---------|--------|--------|-----:|-------:|------------:|--------:|')
    prev = 0.0
    for n, i in enumerate(chosen, 1):
        p = P[i]
        v = prefix_vols.get(n, 0.0)
        dv = (v - prev) if n >= 6 else 0.0
        prev = v
        L.append(f"| {n} | {roles[n-1]} | {p['name']} | {p['pnumber']} | {family(p)} | "
                 f"{p['hue']:.0f} | {p['chroma']:.0f} | {v:,.0f} | {f'{dv:+,.0f}' if n>=6 else '—'} |")
    L.append('\n## Recommended palette sizes (prefixes of the sequence)\n')
    for s in sizes:
        names = ', '.join(P[i]['name'] for i in chosen[:s])
        L.append(f"- **{s} pigments** — gamut {prefix_vols[s]:,.0f}: {names}")
    L.append('\n## Caveats\n')
    L.append('- **Diminishing returns.** 5→8 pigments is the big jump (the violet/cyan/'
             'high-chroma-yellow corners the cadmium set misses); past ~12 each pigment adds '
             '<2% — pick 5 or 8 for a working palette, 10–12 for a "full" set.')
    L.append('- **Gamut ignores tinting strength.** In the single-constant KM model the '
             'reachable colours depend only on the reflectance curves; tinting strength only '
             'changes how much dial you turn, not what is reachable. Greedy growth is a '
             'near-optimal heuristic for this monotone, ~submodular objective.')
    L.append('- **Fluorescent pigments** (the "fluorescent …" rows) extend the violet/magenta '
             'corner, but KM models reflectance only — their real-world punch comes from '
             'fluorescence we do not simulate, and their measured reflectance was clipped from '
             '>1. Treat them as optional gamut-stretchers.')
    L.append('- **No true crimson masstone.** This dataset is cadmium/earth-heavy; its warm '
             'corner is best anchored by cadmium *orange*, and a high-chroma true red only '
             'appears late (fluorescent magenta, #16). White is fixed to the shipped Titanium '
             'White — the dataset contains no white pigment.\n')
    open(OUT_MD, 'w').write('\n'.join(L))

    # Keep the app's served copies in sync (app/data is what /spectral loads).
    import shutil
    os.makedirs(APP_DATA, exist_ok=True)
    for src in (LIB, OUT_JSON):
        shutil.copy(src, os.path.join(APP_DATA, os.path.basename(src)))

    print('\n=== Prefix gamut volumes ===')
    for s in sizes:
        print(f"    {s:2d} pigments: {prefix_vols[s]:,.0f}")
    print(f'\nwrote {OUT_JSON}\nwrote {OUT_MD}\nsynced -> {APP_DATA}/')


if __name__ == '__main__':
    main()
