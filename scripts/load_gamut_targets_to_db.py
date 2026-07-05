#!/usr/bin/env python3
"""Insert the generated even-gamut target set into the target_colors table.

Reads artifacts/gamut_targets/gamut_targets_n{N}.csv and appends each row as a
TargetColor with color_type='gamut'. Idempotent: aborts if 'gamut' rows already
exist (rerun-safe). Rollback is a one-liner:
    DELETE FROM target_colors WHERE color_type = 'gamut';

Usage:
    python3 scripts/load_gamut_targets_to_db.py --env shadestudy.env [--replace] [--commit]

--replace deletes existing color_type='gamut' rows first (for reloading a new set).
Without --commit it runs a dry-run (reports what it *would* do, no write).
"""
import argparse
import os
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CLASSIFICATION = 'even_gamut_v2'   # v2 = band-guaranteed + Xiao skin-zone densified


def load_env(env_path):
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', required=True, help='env file with DATABASE_URL')
    ap.add_argument('--replace', action='store_true', help='delete existing gamut rows first')
    ap.add_argument('--commit', action='store_true', help='actually write (else dry-run)')
    args = ap.parse_args()

    load_env(REPO / args.env)
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL not set from env file')

    df = pd.read_csv(REPO / 'artifacts' / 'gamut_targets' / 'gamut_targets.csv')
    assert (df.total_drops <= 28).all() and (df.total_drops >= 2).all(), 'recipe sums out of [2,28]'

    from app import create_app, db
    from app.models import TargetColor
    from app.routes import _sync_target_colors_id_sequence_postgresql
    from sqlalchemy import func

    app = create_app()
    with app.app_context():
        total_before = TargetColor.query.count()
        existing_gamut = TargetColor.query.filter_by(color_type='gamut').count()
        max_order = db.session.query(func.max(TargetColor.catalog_order)).scalar()
        next_order = (int(max_order) if max_order is not None else -1) + 1

        print(f'catalog rows before : {total_before}')
        print(f'existing gamut rows : {existing_gamut}')

        if existing_gamut and not args.replace:
            raise SystemExit('ABORT: gamut targets already present — pass --replace to swap them.')

        if existing_gamut and args.replace:
            if args.commit:
                TargetColor.query.filter_by(color_type='gamut').delete()
                db.session.commit()
                print(f'deleted {existing_gamut} existing gamut rows')
            else:
                print(f'DRY-RUN: would delete {existing_gamut} existing gamut rows first')
            max_order = db.session.query(func.max(TargetColor.catalog_order)).scalar()
            next_order = (int(max_order) if max_order is not None else -1) + 1
        print(f'next catalog_order  : {next_order}')

        rows = []
        for i, rec in df.reset_index(drop=True).iterrows():
            skin = int(rec.get('skin_zone', 0)) == 1
            rows.append(TargetColor(
                name=f'Gamut {i + 1:03d}',
                color_type='gamut',
                classification=CLASSIFICATION + ('_skin' if skin else ''),
                r=int(rec.R), g=int(rec.G), b=int(rec.B),
                catalog_order=next_order + i,
                drop_white=int(rec.drop_white), drop_black=int(rec.drop_black),
                drop_red=int(rec.drop_red), drop_yellow=int(rec.drop_yellow),
                drop_blue=int(rec.drop_blue),
                mixing_model='mixbox', input_mode='integer',
            ))

        if not args.commit:
            print(f'DRY-RUN: would insert {len(rows)} gamut targets '
                  f'(catalog_order {next_order}..{next_order + len(rows) - 1}). '
                  f'Re-run with --commit to write.')
            return

        _sync_target_colors_id_sequence_postgresql()
        db.session.add_all(rows)
        db.session.commit()

        total_after = TargetColor.query.count()
        print(f'INSERTED {len(rows)} gamut targets. catalog rows now: {total_after}')
        print("rollback: DELETE FROM target_colors WHERE color_type = 'gamut';")


if __name__ == '__main__':
    main()
