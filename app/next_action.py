"""
Next-action policy: single server-authoritative CTA envelope.

Policy version: v1
Policy order:
  1. daily_unfinished  — final run for today not yet submitted
  2. streak_at_risk    — active streak may lapse today (predicates below)
  3. quota_under_150   — deterministic first unlocked + under-quota target
  4. progress_xp       — XP/coverage-focused fallback

streak_start replaces quota_under_150 when last_activity_date is None
(never qualified before; "save streak" copy would be nonsensical).

Streak-at-risk predicates (ALL must hold):
  - last_activity_date is not None
  - last_activity_date != today  (no qualifying save yet today)
  - current_streak > 0
  - next qualifying save will increment or consume a freeze, NOT reset:
      last_activity_date == yesterday  OR
      (last_activity_date == today - 2 AND streak_freeze_available > 0)

Guest (no user_id): returns {'next_action': None}
"""
from datetime import date, datetime, timedelta, timezone
from .models import UserProgress, UserTargetColorStats, DailyChallengeRun, TargetColor
from .gamification import COVERAGE_QUOTA, compute_level, compute_level_progress

POLICY_VERSION = 'v1'


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


def _first_under_quota_target(user_id: str, user_level: int):
    """
    Return the first TargetColor that is:
      - unlocked (level_required <= user_level)
      - under quota (attempt_count < COVERAGE_QUOTA)
    ordered by catalog_order. Returns None when all are at quota.
    """
    stats_map = {
        s.target_color_id: s.attempt_count
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
    }
    candidates = (
        TargetColor.query
        .filter(TargetColor.level_required <= user_level)
        .order_by(TargetColor.catalog_order.asc())
        .all()
    )
    for tc in candidates:
        if stats_map.get(tc.id, 0) < COVERAGE_QUOTA:
            return tc
    return None


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
    user_level = compute_level(up.xp) if up else 1

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
        tc = _first_under_quota_target(user_id, user_level)
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

    # ── 3. Under-quota target (or first-time streak start) ─────────────────
    if primary is None:
        tc = _first_under_quota_target(user_id, user_level)
        if tc:
            never_qualified = up is None or up.last_activity_date is None
            if never_qualified:
                action_id = 'streak_start'
                label = 'Start your streak'
                reason = f'Match {tc.name} to begin your first streak'
            else:
                action_id = 'quota_under_150'
                label = f'Practice {tc.name}'
                reason = 'Build coverage — this color is under quota'
            primary = {
                'id': action_id,
                'type': 'practice',
                'label': label,
                'reason': reason,
                'payload': {'route': 'free_play', 'target_color_id': tc.id},
            }

    # ── 4. Progress-focused fallback ──────────────────────────────────────
    if primary is None:
        _, xp_to_next = compute_level_progress(up.xp) if up else (0, 0)
        label = f'{xp_to_next} XP to next level' if xp_to_next else 'Keep practicing'
        primary = {
            'id': 'progress_xp',
            'type': 'practice',
            'label': label,
            'reason': 'Improve your coverage and level up',
            'payload': {'route': 'free_play', 'target_color_id': None},
        }

    return {
        'next_action': {
            'primary': primary,
            'secondary': secondary,
            'generated_at': generated_at,
            'policy_day': policy_day,
            'policy_version': POLICY_VERSION,
        }
    }
