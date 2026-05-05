#!/usr/bin/env python3
"""
Aggressive recompute of `user_progress.level` and `max_sum_drop_unlocked`
under the Option C (30-level) tier-driven mapping.

Use after deploying the new gamification module so that legacy users (who
were stuck at level 9 with the old mapping) land on the correct slot in the
new 30-level system.

Idempotent: safe to re-run. Writes only when a value actually changes.

Usage:
  python scripts/recompute_levels.py             # commit changes
  python scripts/recompute_levels.py --dry-run   # report only

Loads DATABASE_URL from repo-root .env (via app.create_app → load_dotenv).
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app import create_app, db  # noqa: E402
from app.models import UserProgress  # noqa: E402
from app.gamification import (  # noqa: E402
    DEFAULT_CAP,
    LEVEL_COUNT,
    _catalog_sum_drops_sorted,
    _cap_advance_steps_for,
    _derived_max_sum_drop_unlocked,
    compute_level_from_quota,
    compute_quota_progress,
)


def recompute_for_user(up: UserProgress, sum_drops_sorted, cap_advance_steps):
    """Compute new level + cap for one user. Returns (changed, summary_dict)."""
    quota = compute_quota_progress(up.user_id)
    new_level = compute_level_from_quota(
        quota['colors_at_quota_total'],
        quota['total_recipe_colors'],
        cap_advance_steps,
        quota['is_maxed_out'],
    )
    new_cap = _derived_max_sum_drop_unlocked(
        quota['colors_at_quota_total'],
        sum_drops_sorted,
    )
    old_level = int(up.level or 1)
    old_cap = int(up.max_sum_drop_unlocked or DEFAULT_CAP)
    changed = (new_level != old_level) or (new_cap != old_cap)
    return changed, {
        'user_id': up.user_id,
        'colors_at_quota_total': quota['colors_at_quota_total'],
        'total_recipe_colors': quota['total_recipe_colors'],
        'old_level': old_level,
        'new_level': new_level,
        'old_cap': old_cap,
        'new_cap': new_cap,
        'is_maxed_out': quota['is_maxed_out'],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report intended changes without writing.',
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=0,
        help='Stop after N users (0 = all).',
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        sum_drops_sorted = _catalog_sum_drops_sorted()
        cap_advance_steps = _cap_advance_steps_for(sum_drops_sorted)
        cap_phase_max_level = 1 + cap_advance_steps

        print(f'Catalog snapshot:')
        print(f'  distinct sum_drop bands : {len(sum_drops_sorted)} {sum_drops_sorted}')
        print(f'  cap_advance_steps       : {cap_advance_steps}')
        print(f'  cap-phase max level     : {cap_phase_max_level}')
        print(f'  level count             : {LEVEL_COUNT}')
        print()

        progress_rows = UserProgress.query.order_by(UserProgress.user_id.asc()).all()
        print(f'Found {len(progress_rows)} user_progress rows.')
        if args.limit > 0:
            progress_rows = progress_rows[: args.limit]
            print(f'(Limited to first {len(progress_rows)} for this run.)')
        print()

        changed_count = 0
        unchanged_count = 0
        level_up_count = 0
        level_down_count = 0
        cap_changed_count = 0

        for up in progress_rows:
            changed, info = recompute_for_user(up, sum_drops_sorted, cap_advance_steps)
            if not changed:
                unchanged_count += 1
                continue

            changed_count += 1
            level_delta = info['new_level'] - info['old_level']
            cap_delta = info['new_cap'] - info['old_cap']
            if level_delta > 0:
                level_up_count += 1
            elif level_delta < 0:
                level_down_count += 1
            if cap_delta != 0:
                cap_changed_count += 1

            print(
                f'  {info["user_id"]}: '
                f'level {info["old_level"]} → {info["new_level"]} '
                f'(Δ{level_delta:+d}), '
                f'cap {info["old_cap"]} → {info["new_cap"]} '
                f'(Δ{cap_delta:+d}), '
                f'colors_at_quota={info["colors_at_quota_total"]}'
                f'/{info["total_recipe_colors"]}'
                + (' [MAXED]' if info['is_maxed_out'] else '')
            )

            if not args.dry_run:
                up.level = info['new_level']
                up.max_sum_drop_unlocked = info['new_cap']

        if args.dry_run:
            db.session.rollback()
            print()
            print('DRY RUN — no changes committed.')
        else:
            db.session.commit()
            print()
            print('Changes committed.')

        print()
        print('Summary:')
        print(f'  changed   : {changed_count}')
        print(f'  unchanged : {unchanged_count}')
        print(f'  level up  : {level_up_count}')
        print(f'  level down: {level_down_count}')
        print(f'  cap changed: {cap_changed_count}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
