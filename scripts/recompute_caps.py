#!/usr/bin/env python3
"""One-off: recompute every user's sum-drop cap under the current band-ladder rule.

Use after changing STARTING_BAND: existing users were pinned to the old starting cap by
the earlier reset, so their persisted max_sum_drop_unlocked lags the new open window.
recompute_max_sum_drop_unlocked() re-derives it from the user's solved gamut bands (=
STARTING_BAND for anyone who hasn't solved a band at/above it). Idempotent.

Run AFTER the new code is deployed.

Usage:
    python3 scripts/recompute_caps.py --env shadestudy.env [--commit]
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
    from app.gamification import _band_ladder_cap, _solved_bands_for_user, STARTING_BAND

    app = create_app()
    with app.app_context():
        rows = UserProgress.query.all()
        changes = []
        for up in rows:
            target = _band_ladder_cap(_solved_bands_for_user(up.user_id))
            if int(up.max_sum_drop_unlocked or 0) != target:
                changes.append((up.user_id, int(up.max_sum_drop_unlocked or 0), target))
        print(f'STARTING_BAND={STARTING_BAND} | user_progress rows: {len(rows)} | caps to update: {len(changes)}')
        for uid, old, new in changes[:10]:
            print(f'  {uid}: cap {old} -> {new}')
        if len(changes) > 10:
            print(f'  ... and {len(changes) - 10} more')
        if not args.commit:
            print('DRY-RUN: re-run with --commit to write.')
            return
        for up in rows:
            up.max_sum_drop_unlocked = _band_ladder_cap(_solved_bands_for_user(up.user_id))
        db.session.commit()
        print(f'updated {len(changes)} caps.')


if __name__ == '__main__':
    main()
