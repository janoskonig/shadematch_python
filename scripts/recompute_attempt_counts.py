#!/usr/bin/env python3
"""
Recompute UserTargetColorStats from the canonical MixingSession history under
the new color-quota rule: only sessions whose `match_category` falls in
COMPLETED_MATCH_CATEGORIES (perfect, no_perceivable_difference,
acceptable_difference) advance a color toward its quota.

Why this exists:
  process_progression historically incremented `attempt_count` on every save
  and skip — including big-difference skips and 'stopped' rows. The new policy
  excludes those, so legacy attempt_count values overstate quota progress.

What this script does:
  1. For every user/target_color pair, replays the MixingSession history and
     rewrites attempt_count + completed_count from scratch.
       attempt_count   = #rows with match_category IN COMPLETED_MATCH_CATEGORIES
       completed_count = #rows with skipped = false
       best_delta_e    = MIN(delta_e) over all rows where delta_e IS NOT NULL
       last_attempt_at = MAX(timestamp) over all rows
  2. Recomputes UserProgress.level + max_sum_drop_unlocked from the corrected
     stats (mirrors scripts/recompute_levels.py).

Idempotent: safe to re-run. Writes only when a value actually changes.

Usage:
  python scripts/recompute_attempt_counts.py             # commit changes
  python scripts/recompute_attempt_counts.py --dry-run   # report only
  python scripts/recompute_attempt_counts.py --user-id ABC123  # one user only

Loads DATABASE_URL from repo-root .env (via app.create_app → load_dotenv).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app import create_app, db  # noqa: E402
from app.models import MixingSession, UserProgress, UserTargetColorStats  # noqa: E402
from app.gamification import (  # noqa: E402
    COMPLETED_MATCH_CATEGORIES,
    DEFAULT_CAP,
    _catalog_sum_drops_sorted,
    _cap_advance_steps_for,
    _derived_max_sum_drop_unlocked,
    compute_level_from_quota,
    compute_quota_progress,
)


def _replay_sessions(user_id: str | None):
    """Aggregate MixingSession history into per-(user, color) totals."""
    q = MixingSession.query.filter(MixingSession.target_color_id.isnot(None))
    if user_id:
        q = q.filter(MixingSession.user_id == user_id)

    totals = defaultdict(lambda: {
        'attempt_count': 0,
        'completed_count': 0,
        'best_delta_e': None,
        'last_attempt_at': None,
    })
    for s in q.yield_per(2000):
        key = (s.user_id, s.target_color_id)
        agg = totals[key]
        if s.match_category in COMPLETED_MATCH_CATEGORIES:
            agg['attempt_count'] += 1
        if not s.skipped:
            agg['completed_count'] += 1
        if s.delta_e is not None:
            if agg['best_delta_e'] is None or s.delta_e < agg['best_delta_e']:
                agg['best_delta_e'] = s.delta_e
        if s.timestamp is not None:
            if agg['last_attempt_at'] is None or s.timestamp > agg['last_attempt_at']:
                agg['last_attempt_at'] = s.timestamp
    return totals


def _diff_summary(old, new) -> str:
    parts = []
    if old.attempt_count != new['attempt_count']:
        parts.append(f'att {old.attempt_count}→{new["attempt_count"]}')
    if old.completed_count != new['completed_count']:
        parts.append(f'comp {old.completed_count}→{new["completed_count"]}')
    return ', '.join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true',
                        help='Report intended changes without writing.')
    parser.add_argument('--user-id', default=None,
                        help='Limit to a single user_id (case-sensitive).')
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        sum_drops_sorted = _catalog_sum_drops_sorted()
        cap_advance_steps = _cap_advance_steps_for(sum_drops_sorted)

        print('Catalog snapshot:')
        print(f'  distinct sum_drop bands : {len(sum_drops_sorted)} {sum_drops_sorted}')
        print(f'  cap_advance_steps       : {cap_advance_steps}')
        print()

        totals = _replay_sessions(args.user_id)
        print(f'Replayed sessions for {len({k[0] for k in totals})} users / '
              f'{len(totals)} (user, color) pairs.')
        print()

        # ── Phase 1: rewrite stats rows ──────────────────────────────────────
        stats_q = UserTargetColorStats.query
        if args.user_id:
            stats_q = stats_q.filter_by(user_id=args.user_id)

        existing = {(s.user_id, s.target_color_id): s for s in stats_q.all()}
        touched_users = set()
        rows_changed = 0
        rows_zeroed = 0
        rows_created = 0

        for key, agg in totals.items():
            user_id, target_color_id = key
            touched_users.add(user_id)
            stats = existing.get(key)
            if stats is None:
                if (
                    agg['attempt_count'] == 0
                    and agg['completed_count'] == 0
                    and agg['best_delta_e'] is None
                ):
                    continue
                stats = UserTargetColorStats(
                    user_id=user_id,
                    target_color_id=target_color_id,
                    attempt_count=0,
                    completed_count=0,
                )
                db.session.add(stats)
                rows_created += 1

            diff = _diff_summary(stats, agg)
            if (
                stats.attempt_count != agg['attempt_count']
                or stats.completed_count != agg['completed_count']
                or stats.best_delta_e != agg['best_delta_e']
                or stats.last_attempt_at != agg['last_attempt_at']
            ):
                stats.attempt_count = agg['attempt_count']
                stats.completed_count = agg['completed_count']
                stats.best_delta_e = agg['best_delta_e']
                stats.last_attempt_at = agg['last_attempt_at']
                rows_changed += 1
                if diff:
                    print(f'  {user_id} / color {target_color_id}: {diff}')

        # Stats rows that exist but no longer have any qualifying history get
        # zeroed out so the level recompute reads the new truth.
        for key, stats in existing.items():
            if key in totals:
                continue
            touched_users.add(stats.user_id)
            if (
                stats.attempt_count == 0
                and stats.completed_count == 0
                and stats.best_delta_e is None
                and stats.last_attempt_at is None
            ):
                continue
            print(f'  {stats.user_id} / color {stats.target_color_id}: '
                  f'no qualifying sessions — zeroing '
                  f'(att {stats.attempt_count}→0, comp {stats.completed_count}→0)')
            stats.attempt_count = 0
            stats.completed_count = 0
            stats.best_delta_e = None
            stats.last_attempt_at = None
            rows_zeroed += 1

        # Need stats writes visible to the level recompute below.
        db.session.flush()

        # ── Phase 2: recompute UserProgress.level + cap ──────────────────────
        level_changed = 0
        cap_changed = 0
        progress_q = UserProgress.query
        if args.user_id:
            progress_q = progress_q.filter_by(user_id=args.user_id)
        for up in progress_q.all():
            if args.user_id is None and up.user_id not in touched_users:
                # User has no MixingSession history → nothing to recompute.
                continue
            quota = compute_quota_progress(up.user_id)
            new_level = compute_level_from_quota(
                quota['colors_at_quota_total'],
                quota['total_recipe_colors'],
                quota['cap_advance_steps'],
                quota['is_maxed_out'],
            )
            new_cap = _derived_max_sum_drop_unlocked(
                quota['colors_at_quota_total'],
                sum_drops_sorted,
            )
            old_level = int(up.level or 1)
            old_cap = int(up.max_sum_drop_unlocked or DEFAULT_CAP)
            if new_level != old_level:
                print(f'  {up.user_id}: level {old_level} → {new_level} '
                      f'(colors_at_quota={quota["colors_at_quota_total"]}/'
                      f'{quota["total_recipe_colors"]})')
                up.level = new_level
                level_changed += 1
            if new_cap != old_cap:
                print(f'  {up.user_id}: cap {old_cap} → {new_cap}')
                up.max_sum_drop_unlocked = new_cap
                cap_changed += 1

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
        print(f'  stats rows changed : {rows_changed}')
        print(f'  stats rows created : {rows_created}')
        print(f'  stats rows zeroed  : {rows_zeroed}')
        print(f'  levels changed     : {level_changed}')
        print(f'  caps changed       : {cap_changed}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
