#!/usr/bin/env python3
"""Generate the ADVANCED ring of gamut targets (post-completion content).

Seeds farthest-point distances with the deployed base set
(artifacts/gamut_targets/gamut_targets.csv, 332 colours), then continues
sampling the achievable-recipe pool at a tighter spacing. Every new colour
therefore fills a gap in the existing cover: it is at least the chosen spacing
away from every base target and from every other advanced target.

Same guarantees as the base set: each target is the rounded 8-bit RGB of an
integer Mixbox recipe (<=20 drops/pigment, total 2..28, minimal-sum recipe),
so ΔE=0 is achievable. No band-guarantee step — the advanced ring assumes the
band ladder is already fully open.

Run:  PYTHONPATH=. python3 scripts/generate_gamut_targets_advanced.py [--scan]

--scan prints the ring size for several candidate spacings and exits.
Default writes artifacts/gamut_targets/gamut_targets_advanced.csv
with BG_SPACING_ADV / SKIN_SPACING_ADV below.
"""
import argparse

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from scripts.generate_gamut_targets import (
    achievable_pool, skin_region_tree, fps_spacing, rgb_int_from_counts,
    rgb_to_lab, MIN_TOTAL, MAX_TOTAL, MAXD, SKIN_MARGIN, OUT,
)

BG_SPACING_ADV = 9.0     # base set used 12.0
SKIN_SPACING_ADV = 5.0   # base set used 6.0


def select_ring(labs, in_skin, init_d_existing, bg_spacing, skin_spacing):
    """FPS continuation: dense skin pass first, then background, both seeded
    with distances to the already-deployed targets."""
    chosen = set()
    skin_ids = np.where(in_skin)[0]
    sub = fps_spacing(labs[skin_ids], skin_spacing,
                      init_d=init_d_existing[skin_ids])
    chosen.update(skin_ids[sub].tolist())

    init_d = init_d_existing.copy()
    if chosen:
        picks = np.array(sorted(chosen))
        init_d = np.minimum(init_d, cKDTree(labs[picks]).query(labs)[0])
    for i in fps_spacing(labs, bg_spacing, init_d=init_d):
        chosen.add(i)
    return np.array(sorted(chosen))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--scan', action='store_true',
                    help='print ring sizes for candidate spacings and exit')
    args = ap.parse_args()

    rng = np.random.default_rng(0)
    counts, rgb, band, labs = achievable_pool(rng)
    tree = skin_region_tree(rng)
    in_skin = tree.query(labs)[0] <= SKIN_MARGIN

    base = pd.read_csv(OUT / 'gamut_targets.csv')
    base_labs = rgb_to_lab(base[['R', 'G', 'B']].values)
    init_d_existing = cKDTree(base_labs).query(labs)[0]
    print(f'pool: {len(labs)} achievable colours | base set: {len(base)}')

    if args.scan:
        for bg, sk in [(10.0, 5.5), (9.0, 5.0), (8.0, 4.5), (7.0, 4.0)]:
            idx = select_ring(labs, in_skin, init_d_existing, bg, sk)
            n_skin = int(in_skin[idx].sum())
            print(f'bg={bg:4.1f} skin={sk:3.1f} -> {len(idx):4d} new targets '
                  f'({n_skin} skin-zone, {len(idx) - n_skin} background)')
        return

    idx = select_ring(labs, in_skin, init_d_existing,
                      BG_SPACING_ADV, SKIN_SPACING_ADV)
    c, g, L, bnd, sk = counts[idx], rgb[idx], labs[idx], band[idx], in_skin[idx]

    # ΔE==0 guarantee + recipe constraints + true gap-filling (min dist to base)
    assert np.array_equal(rgb_int_from_counts(c), g), 'recipe does not reproduce RGB!'
    assert (c.max(1) <= MAXD).all() and (MIN_TOTAL <= bnd).all() and (bnd <= MAX_TOTAL).all()
    d_to_base = cKDTree(base_labs).query(L)[0]
    assert d_to_base.min() > 1.0, 'advanced target collides with a base target!'

    df = pd.DataFrame({
        'drop_white': c[:, 0], 'drop_black': c[:, 1], 'drop_red': c[:, 2],
        'drop_yellow': c[:, 3], 'drop_blue': c[:, 4], 'total_drops': bnd,
        'band': bnd, 'skin_zone': sk.astype(int),
        'R': g[:, 0], 'G': g[:, 1], 'B': g[:, 2],
        'L': L[:, 0].round(2), 'a': L[:, 1].round(2), 'b': L[:, 2].round(2),
    }).sort_values(['band', 'L']).reset_index(drop=True)

    path = OUT / 'gamut_targets_advanced.csv'
    df.to_csv(path, index=False)
    print(f'wrote {path}')
    print(f'advanced targets: {len(df)} | skin-zone: {int(df.skin_zone.sum())} '
          f'background: {int((~df.skin_zone.astype(bool)).sum())}')
    print(f'min ΔE(Lab76) to base set: {d_to_base.min():.2f} '
          f'median: {np.median(d_to_base):.2f}')


if __name__ == '__main__':
    main()
