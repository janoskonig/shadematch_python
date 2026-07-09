#!/usr/bin/env python3
"""Rename the gamut targets from 'Gamut NNN' to human-friendly color names.

Each color_type='gamut' row in target_colors gets the nearest color name from
the classic "Name That Color" dictionary (data/colornames_ntc.csv, 1566 names
like Amethyst, Mauve, Alice Blue, Burgundy). Nearest = CIEDE2000 in Lab.
Names are assigned uniquely (greedy on global distance) and never collide with
names already used by non-gamut catalog rows.

Notifications, emails and the catalog UI all read target_colors.name, so this
single rename fixes ">Gamut 003< is waiting for you" everywhere.

Usage:
    python3 scripts/rename_gamut_targets.py --env shadestudy.env [--commit] [--revert]

Without --commit it runs a dry-run (prints the proposed mapping, no write).
--revert restores the original 'Gamut NNN' names (numbered by catalog_order).
On --commit the applied mapping is also written to
artifacts/gamut_targets/gamut_name_mapping.csv.

NOTE: scripts/load_gamut_targets_to_db.py still inserts 'Gamut NNN' names —
rerun this script after any --replace reload of the gamut set.
"""
import argparse
import csv
import os
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
NAMES_CSV = REPO / 'data' / 'colornames_ntc.csv'


def load_env(env_path):
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_name_dictionary():
    names, rgbs = [], []
    with open(NAMES_CSV, newline='') as f:
        for row in csv.DictReader(f):
            h = row['hex'].lstrip('#')
            names.append(row['name'])
            rgbs.append((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    return names, np.array(rgbs, dtype=float)


def rgb_to_lab(rgb_array):
    """sRGB (0-255, Nx3) -> CIE Lab (D65), vectorized."""
    rgb = rgb_array / 255.0
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = linear @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    L = 116 * f[:, 1] - 16
    a = 500 * (f[:, 0] - f[:, 1])
    b = 200 * (f[:, 1] - f[:, 2])
    return np.column_stack([L, a, b])


def assign_unique_names(target_rgbs, dict_names, dict_rgbs, reserved):
    """Greedy globally-nearest unique assignment; returns list of names."""
    from colormath import color_diff_matrix

    target_labs = rgb_to_lab(np.array(target_rgbs, dtype=float))
    dict_labs = rgb_to_lab(dict_rgbs)

    n_t = len(target_labs)
    dist = np.empty((n_t, len(dict_labs)))
    for i in range(n_t):
        dist[i] = color_diff_matrix.delta_e_cie2000(target_labs[i], dict_labs)

    reserved_lower = {r.lower() for r in reserved}
    blocked = np.array([n.lower() in reserved_lower for n in dict_names])
    dist[:, blocked] = np.inf

    assigned = [None] * n_t
    order = np.argsort(dist, axis=None)
    done = 0
    for flat in order:
        i, j = divmod(int(flat), dist.shape[1])
        if assigned[i] is not None or not np.isfinite(dist[i, j]):
            continue
        assigned[i] = (dict_names[j], float(dist[i, j]))
        dist[:, j] = np.inf
        done += 1
        if done == n_t:
            break
    return assigned


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', required=True, help='env file with DATABASE_URL')
    ap.add_argument('--commit', action='store_true', help='actually write (else dry-run)')
    ap.add_argument('--revert', action='store_true', help="restore 'Gamut NNN' names")
    args = ap.parse_args()

    load_env(REPO / args.env)
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL not set from env file')

    from app import create_app, db
    from app.models import TargetColor

    app = create_app()
    with app.app_context():
        gamut = (TargetColor.query.filter_by(color_type='gamut')
                 .order_by(TargetColor.catalog_order).all())
        if not gamut:
            raise SystemExit('no gamut rows found')
        print(f'gamut rows: {len(gamut)}')

        if args.revert:
            for i, tc in enumerate(gamut):
                new = f'Gamut {i + 1:03d}'
                if args.commit:
                    tc.name = new
                else:
                    print(f'DRY-RUN: {tc.name!r} -> {new!r}')
            if args.commit:
                db.session.commit()
                print('reverted to Gamut NNN names')
            return

        others = TargetColor.query.filter(TargetColor.color_type != 'gamut').all()
        reserved = {tc.name for tc in others}

        names, dict_rgbs = load_name_dictionary()
        assigned = assign_unique_names(
            [(tc.r, tc.g, tc.b) for tc in gamut], names, dict_rgbs, reserved)

        rows = []
        for tc, (new_name, de) in zip(gamut, assigned):
            rows.append((tc.id, tc.name, new_name, tc.r, tc.g, tc.b, round(de, 2)))
            print(f'{tc.name:>10s} -> {new_name:<28s} rgb=({tc.r},{tc.g},{tc.b}) dE00={de:5.2f}')

        des = [r[-1] for r in rows]
        print(f'\ndE00 to assigned name: median={np.median(des):.2f} '
              f'max={max(des):.2f}')

        if not args.commit:
            print(f'DRY-RUN: would rename {len(rows)} gamut targets. '
                  f'Re-run with --commit to write.')
            return

        for tc, (new_name, _) in zip(gamut, assigned):
            tc.name = new_name
        db.session.commit()

        out = REPO / 'artifacts' / 'gamut_targets' / 'gamut_name_mapping.csv'
        with open(out, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['id', 'old_name', 'new_name', 'r', 'g', 'b', 'de00'])
            w.writerows(rows)
        print(f'RENAMED {len(rows)} gamut targets. mapping saved to {out}')
        print("revert: python3 scripts/rename_gamut_targets.py --env <env> --revert --commit")


if __name__ == '__main__':
    main()
