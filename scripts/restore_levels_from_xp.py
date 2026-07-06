#!/usr/bin/env python3
"""One-off: set every user's display level from their (intact) accumulated XP.

Levels are now XP-driven. The band-ladder reset zeroed the cached `level` column to 1,
but XP was never touched, so this restores each user's standing: level = _xp_level(xp).
Leaves xp and max_sum_drop_unlocked (the band content-gate) alone. Idempotent.

Run AFTER the XP-level code is deployed.

Usage:
    python3 scripts/restore_levels_from_xp.py --env shadestudy.env [--commit]
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
    from app.gamification import _xp_level

    app = create_app()
    with app.app_context():
        rows = UserProgress.query.all()
        changes = []
        for up in rows:
            target = _xp_level(int(up.xp or 0))
            if int(up.level or 1) != target:
                changes.append((up.user_id, int(up.level or 1), target, int(up.xp or 0)))
        changes.sort(key=lambda c: -c[3])
        print(f'user_progress rows: {len(rows)}  |  levels to update: {len(changes)}')
        for uid, old, new, xp in changes[:10]:
            print(f'  {uid}: L{old} -> L{new}  (xp={xp})')
        if len(changes) > 10:
            print(f'  ... and {len(changes) - 10} more')
        if not args.commit:
            print('DRY-RUN: re-run with --commit to write.')
            return
        for up in rows:
            up.level = _xp_level(int(up.xp or 0))
        db.session.commit()
        print(f'updated {len(changes)} levels from XP.')


if __name__ == '__main__':
    main()
