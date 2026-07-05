#!/usr/bin/env python3
"""Generate the main-gameplay target set for the Mixbox palette.

Every target is the rounded 8-bit RGB output of an integer recipe (≤20 drops per
pigment, total in [MIN_TOTAL, MAX_TOTAL]), so it is exactly reproducible (ΔE=0).
Each colour is assigned its MINIMAL-sum recipe → the recipe sum is its sum-drop
"band" (used by the band-ladder progression).

Three objectives, satisfied together:
  1. band coverage   — every band MIN_TOTAL..MAX_TOTAL has ≥1 target (ladder never stalls)
  2. even background — coarse farthest-point cover of the whole CIELAB gamut
  3. skin densify    — a finer cover inside the Xiao et al. (2017) skin region + margin

Output CSV: artifacts/gamut_targets/gamut_targets.csv
  drop_white,drop_black,drop_red,drop_yellow,drop_blue,total_drops,band,skin_zone,R,G,B,L,a,b

Run:  PYTHONPATH=. python3 scripts/generate_gamut_targets.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
import mixbox
from scipy.spatial import cKDTree

from app.gamut_lab import skin_gamut

MAXD = 20              # max drops per pigment
MIN_TOTAL = 2          # game MIN_SUM_DROP_BAND
MAX_TOTAL = 28         # game MAX_SUM_DROP_CATALOG_CAP
BG_SPACING = 12.0      # background farthest-point spacing (ΔE)
SKIN_MARGIN = 15.0     # skin zone = within this of the Xiao hull (ΔE)
SKIN_SPACING = 6.0     # denser spacing inside the skin zone (ΔE)

BASE = [(255, 255, 255), (0, 0, 0), (255, 0, 0), (255, 255, 0), (0, 0, 255)]
BL = np.array([mixbox.rgb_to_latent(c) for c in BASE])
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "gamut_targets"


def rgb_int_from_counts(counts):
    W = np.asarray(counts, float) / np.asarray(counts, float).sum(1, keepdims=True)
    Z = W @ BL
    rgb = np.array([mixbox.latent_to_rgb(z.tolist()) for z in Z])
    return np.clip(np.round(rgb), 0, 255).astype(int)


def rgb_to_lab(rgb):
    c = np.asarray(rgb, float) / 255.0
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T
    r = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(r > 0.008856451679035631, np.cbrt(r), 7.787037037037037 * r + 16 / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]),
                     200 * (f[:, 1] - f[:, 2])], 1)


def build_candidates(rng):
    rows = []
    for i in range(5):                                   # pures at every band
        for t in range(MIN_TOTAL, MAX_TOTAL + 1):
            c = np.zeros(5, int); c[i] = t; rows.append(c)
    for i in range(5):                                   # all 2-pigment edges, fine
        for j in range(i + 1, 5):
            for a in range(0, MAXD + 1):
                for b in range(0, MAXD + 1):
                    if MIN_TOTAL <= a + b <= MAX_TOTAL:
                        c = np.zeros(5, int); c[i] = a; c[j] = b; rows.append(c)
    r = rng.integers(0, MAXD + 1, size=(5000000, 5))     # random 3+ pigment interior
    r = r[(r > 0).sum(1) >= 3]
    tot = r.sum(1)
    r = r[(tot >= MIN_TOTAL) & (tot <= MAX_TOTAL)]
    return np.vstack([np.array(rows), r])


def achievable_pool(rng):
    """Distinct 8-bit colours, each tagged with its MINIMAL recipe (→ band) + Lab."""
    counts = build_candidates(rng)
    rgb = rgb_int_from_counts(counts)
    tot = counts.sum(1)
    order = np.argsort(tot, kind="stable")               # ascending sum → minimal first
    counts, rgb, tot = counts[order], rgb[order], tot[order]
    key = rgb[:, 0].astype(np.int64) * 65536 + rgb[:, 1] * 256 + rgb[:, 2]
    _, first = np.unique(key, return_index=True)
    return counts[first], rgb[first], tot[first], rgb_to_lab(rgb[first])


def skin_region_tree(rng):
    """KDTree over the Xiao skin hull (16 means + interior samples) for distance queries."""
    sg = skin_gamut()
    means = np.array([[p["L"], p["a"], p["b"]] for p in sg["points"]], float)
    w = rng.dirichlet(np.ones(len(means)), size=3000)
    interior = w @ means
    return cKDTree(np.vstack([means, interior]))


def fps_spacing(labs, spacing, init_d=None, cap=20000):
    """Farthest-point sampling until the frontier gap drops below `spacing`."""
    n = len(labs)
    d = np.full(n, np.inf) if init_d is None else init_d.astype(float).copy()
    sel = []
    while len(sel) < cap:
        i = int(np.argmax(d))
        if d[i] < spacing:
            break
        sel.append(i)
        d = np.minimum(d, np.linalg.norm(labs - labs[i], axis=1))
    return sel


def main():
    rng = np.random.default_rng(0)
    counts, rgb, band, labs = achievable_pool(rng)
    tree = skin_region_tree(rng)
    skin_dist = tree.query(labs)[0]
    in_skin = skin_dist <= SKIN_MARGIN

    # (1) dense skin-zone cover
    skin_ids = np.where(in_skin)[0]
    sub = fps_spacing(labs[skin_ids], SKIN_SPACING)
    chosen = set(skin_ids[sub].tolist())

    # (2) coarse background everywhere else (seed distances from the skin picks so the
    #     background does not re-cover the already-dense skin region)
    picks = np.array(sorted(chosen))
    init_d = cKDTree(labs[picks]).query(labs)[0] if len(picks) else None
    for i in fps_spacing(labs, BG_SPACING, init_d=init_d):
        chosen.add(i)

    # (3) band guarantee — every band MIN..MAX must hold ≥1 target
    chosen_bands = {int(band[i]) for i in chosen}
    for bnd in range(MIN_TOTAL, MAX_TOTAL + 1):
        if bnd in chosen_bands:
            continue
        pool = np.where(band == bnd)[0]
        if len(pool):                                    # add the band's most central colour
            c = labs[pool].mean(0)
            chosen.add(int(pool[np.argmin(np.linalg.norm(labs[pool] - c, axis=1))]))

    idx = np.array(sorted(chosen))
    c, g, L, bnd, sk = counts[idx], rgb[idx], labs[idx], band[idx], in_skin[idx]

    # ΔE==0 guarantee: recompute colour from each recipe, must equal stored RGB
    assert np.array_equal(rgb_int_from_counts(c), g), "recipe does not reproduce RGB!"
    assert (c.max(1) <= MAXD).all() and (MIN_TOTAL <= bnd).all() and (bnd <= MAX_TOTAL).all()
    assert set(range(MIN_TOTAL, MAX_TOTAL + 1)) <= set(bnd.tolist()), "a band is empty!"

    df = pd.DataFrame({
        "drop_white": c[:, 0], "drop_black": c[:, 1], "drop_red": c[:, 2],
        "drop_yellow": c[:, 3], "drop_blue": c[:, 4], "total_drops": bnd,
        "band": bnd, "skin_zone": sk.astype(int),
        "R": g[:, 0], "G": g[:, 1], "B": g[:, 2],
        "L": L[:, 0].round(2), "a": L[:, 1].round(2), "b": L[:, 2].round(2),
    }).sort_values(["band", "L"]).reset_index(drop=True)

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "gamut_targets.csv"
    df.to_csv(path, index=False)

    per_band = df.band.value_counts().reindex(range(MIN_TOTAL, MAX_TOTAL + 1)).astype(int)
    print(f"wrote {path}")
    print(f"targets: {len(df)}  |  skin-zone: {int(df.skin_zone.sum())}  "
          f"background: {int((~df.skin_zone.astype(bool)).sum())}")
    print(f"bands {MIN_TOTAL}..{MAX_TOTAL} all populated: {per_band.min() >= 1}  "
          f"(min {per_band.min()}, median {int(per_band.median())}, max {per_band.max()} per band)")


if __name__ == "__main__":
    main()
