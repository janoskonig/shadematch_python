#!/usr/bin/env python3
"""One-off: reset every user's band-ladder progression for the gamut main-mode switch.

The band ladder derives the sum-drop cap from *gamut* completions, which every existing
user has zero of, so everyone starts at band 2. Cached caps (from the old basic/skin/lab
catalog) and cached display levels must be reset so they don't float above the new ladder.

Resets per UserProgress row: max_sum_drop_unlocked -> MIN_SUM_DROP_BAND (2), level -> 1.
Leaves XP, streaks, freezes and awards untouched. Idempotent.

MUST run AFTER the new gamification code is deployed (the deployed code's recompute must be
the band-ladder version, else it will re-derive caps from the old rule on next play).

Usage:
    python3 scripts/reset_progression_for_band_ladder.py --env shadestudy.env [--commit]
"""
import argparse
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def load_env(env_path):
    for line in Path(env_path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--env', required=True)
    ap.add_argument('--commit', action='store_true')
    args = ap.parse_args()

    load_env(REPO / args.env)
    if not os.environ.get('DATABASE_URL'):
        raise SystemExit('DATABASE_URL not set')

    from app import create_app, db
    from app.models import UserProgress
    from app.gamification import MIN_SUM_DROP_BAND

    app = create_app()
    with app.app_context():
        rows = UserProgress.query.all()
        need = [up for up in rows
                if int(up.max_sum_drop_unlocked or 0) != MIN_SUM_DROP_BAND or int(up.level or 1) != 1]
        print(f'user_progress rows: {len(rows)}  |  to reset: {len(need)}')
        if not args.commit:
            print('DRY-RUN: re-run with --commit to reset. No writes made.')
            return
        for up in need:
            up.max_sum_drop_unlocked = MIN_SUM_DROP_BAND
            up.level = 1
        db.session.commit()
        print(f'reset {len(need)} rows to cap={MIN_SUM_DROP_BAND}, level=1')


if __name__ == '__main__':
    main()
