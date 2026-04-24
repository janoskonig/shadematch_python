"""
Next-action policy: single server-authoritative CTA envelope.

Policy version: v2  (quota-first)
Policy order:
  1. daily_unfinished  — final run for today not yet submitted
  2. streak_at_risk    — active streak may lapse today
  3. quota_deficit     — nearest actionable (unlocked) under-quota color
                         tie-break: smallest remaining, then lowest catalog_order
  4. quota_maxed       — all colors mastered: maintenance goal
  4. quota_locked      — no unlocked deficit (shouldn't normally occur)

streak_start replaces quota_deficit when last_activity_date is None.

Streak-at-risk predicates (ALL must hold):
  - last_activity_date is not None
  - last_activity_date != today
  - current_streak > 0
  - next qualifying save will increment or consume a freeze, NOT reset:
      last_activity_date == yesterday  OR
      (last_activity_date == today - 2 AND streak_freeze_available > 0)

Guest (no user_id): returns {'next_action': None}
"""
from datetime import date, datetime, timedelta, timezone
from .models import UserProgress, UserTargetColorStats, DailyChallengeRun, TargetColor
from .gamification import (
    COVERAGE_QUOTA,
    compute_quota_progress,
    MIN_SUM_DROP_BAND,
    target_color_sum_drop,
    _effective_sum_cap,
)

POLICY_VERSION = 'v2'


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _streak_at_risk(up: UserProgress, today: date) -> bool:
    """True iff the at-risk predicate holds — never fires for brand-new users."""
    if up is None or up.last_activity_date is None:
        return False
    if up.last_activity_date == today:
        return False
    if up.current_streak <= 0:
        return False
    yesterday = today - timedelta(days=1)
    two_days_ago = today - timedelta(days=2)
    salvageable = (
        up.last_activity_date == yesterday
        or (up.last_activity_date == two_days_ago and up.streak_freeze_available > 0)
    )
    return salvageable


def _nearest_deficit_unlocked_target(user_id: str):
    """
    Return the TargetColor with the smallest positive remaining quota attempts
    among colors with a full recipe and sum_drop in the user's current tier band.

    Tie-break: lowest catalog_order (iterated first).
    Returns None when all eligible colors are at or above quota.
    """
    stats_map = {
        s.target_color_id: s.attempt_count
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
    }
    up = UserProgress.query.filter_by(user_id=user_id).first()
    cap = int(up.max_sum_drop_unlocked) if up else 4
    eff = _effective_sum_cap(cap)
    candidates = [
        tc for tc in TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
        if (s := target_color_sum_drop(tc)) is not None
        and MIN_SUM_DROP_BAND <= s <= eff
    ]
    best = None
    best_remaining = None
    for tc in candidates:
        remaining = max(0, COVERAGE_QUOTA - stats_map.get(tc.id, 0))
        if remaining > 0:
            # Smaller remaining wins; catalog_order tiebreak is already the natural iter order
            if best is None or remaining < best_remaining:
                best = tc
                best_remaining = remaining
    return best, best_remaining


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_next_action(user_id: str, today: date = None):
    """
    Build and return the next_action envelope for *user_id*.

    Always returns a dict; callers merge it into the response object.
    Shape:
        {'next_action': None}                        — guest / unauthenticated
        {'next_action': {primary, secondary, ...}}   — logged-in user
    """
    if today is None:
        today = date.today()

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    policy_day = today.isoformat()

    if not user_id:
        return {'next_action': None}

    up = UserProgress.query.filter_by(user_id=user_id).first()

    # Quota state (one canonical query)
    quota = compute_quota_progress(user_id)
    is_maxed_out = quota['is_maxed_out']

    # Secondary is always a non-coercive escape hatch
    secondary = {
        'id': 'escape_free_play',
        'type': 'navigate',
        'label': 'Browse colors',
        'reason': 'Pick any color to practice',
        'payload': {'route': 'free_play'},
    }

    primary = None

    # ── 1. Daily unfinished ───────────────────────────────────────────────
    final_today = (
        DailyChallengeRun.query
        .filter_by(user_id=user_id, challenge_date=today, is_final=True)
        .first()
    )
    if not final_today:
        primary = {
            'id': 'daily_unfinished',
            'type': 'daily_challenge',
            'label': "Today's challenge",
            'reason': "Complete today's daily color challenge",
            'payload': {'route': 'daily_challenge', 'challenge_date': policy_day},
        }

    # ── 2. Streak at risk ─────────────────────────────────────────────────
    if primary is None and _streak_at_risk(up, today):
        tc, remaining = _nearest_deficit_unlocked_target(user_id)
        primary = {
            'id': 'streak_at_risk',
            'type': 'practice',
            'label': 'Save your streak',
            'reason': (
                f'Your {up.current_streak}-day streak is on the line'
                ' — play once today to keep it'
            ),
            'payload': {
                'route': 'free_play',
                'target_color_id': tc.id if tc else None,
            },
        }

    # ── 3. Nearest unlocked quota deficit ────────────────────────────────
    if primary is None and not is_maxed_out:
        tc, remaining = _nearest_deficit_unlocked_target(user_id)
        if tc:
            never_qualified = up is None or up.last_activity_date is None
            if never_qualified:
                action_id = 'streak_start'
                label = 'Start your streak'
                reason = f'Match {tc.name} to begin — {remaining} attempts to quota'
            else:
                action_id = 'quota_deficit'
                label = f'Practice {tc.name}'
                reason = (
                    f'{remaining} attempt{"s" if remaining != 1 else ""} to '
                    f'complete this color ({quota["completed_colors"]} of '
                    f'{quota["total_tracked_colors"]} done)'
                )
            primary = {
                'id': action_id,
                'type': 'practice',
                'label': label,
                'reason': reason,
                'payload': {
                    'route': 'free_play',
                    'target_color_id': tc.id,
                    'remaining': remaining,
                },
            }
        else:
            # All unlocked colors at quota but not globally maxed (locked colors remain)
            primary = {
                'id': 'quota_locked_colors',
                'type': 'practice',
                'label': 'Build your coverage',
                'reason': (
                    f'Complete your current sum-drop tier to unlock harder shades — '
                    f'{quota["remaining_attempts_total"]:,} attempts left in this tier'
                ),
                'payload': {'route': 'free_play', 'target_color_id': None},
            }

    # ── 4. Maxed-out maintenance ──────────────────────────────────────────
    if primary is None and is_maxed_out:
        primary = {
            'id': 'maintenance_delta_e',
            'type': 'practice',
            'label': 'Refine your precision',
            'reason': 'All colors mastered — keep improving your delta-E accuracy',
            'payload': {'route': 'free_play', 'target_color_id': None},
        }

    # ── 4. Fallback (should not normally reach here) ──────────────────────
    if primary is None:
        total_remaining = quota.get('remaining_attempts_total', 0)
        primary = {
            'id': 'quota_progress',
            'type': 'practice',
            'label': 'Build palette coverage',
            'reason': (
                f'{total_remaining:,} attempts remaining to complete all colors'
                if total_remaining > 0 else 'Keep practicing!'
            ),
            'payload': {'route': 'free_play', 'target_color_id': quota.get('nearest_deficit_color_id')},
        }

    return {
        'next_action': {
            'primary': primary,
            'secondary': secondary,
            'generated_at': generated_at,
            'policy_day': policy_day,
            'policy_version': POLICY_VERSION,
            'quota_summary': {
                'completed_colors': quota['completed_colors'],
                'total_tracked_colors': quota['total_tracked_colors'],
                'remaining_attempts_total': quota['remaining_attempts_total'],
                'is_maxed_out': is_maxed_out,
            },
        }
    }
