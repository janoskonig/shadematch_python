"""
Next-action policy: single server-authoritative CTA envelope.

Policy version: v3  (match-first)
Policy order:
  1. daily_unfinished  — final run for today not yet submitted
  2. streak_at_risk    — active streak may lapse today
  3. continue_match    — an active match is waiting (round i of n)
  4. start_match       — no active match: start a fresh 10-round match

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
from .models import UserProgress, DailyChallengeRun, Match
from .gamification import compute_coverage_progress, _matches_completed
from .i18n import t

POLICY_VERSION = 'v3'


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


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------

def build_next_action(user_id: str, today: date = None, quota=None):
    """
    Build and return the next_action envelope for *user_id*.

    Always returns a dict; callers merge it into the response object.
    Shape:
        {'next_action': None}                        — guest / unauthenticated
        {'next_action': {primary, secondary, ...}}   — logged-in user

    Pass *quota* from compute_coverage_progress when already computed to avoid
    duplicate work.
    """
    if today is None:
        today = date.today()

    generated_at = datetime.now(tz=timezone.utc).isoformat()
    policy_day = today.isoformat()

    if not user_id:
        return {'next_action': None}

    up = UserProgress.query.filter_by(user_id=user_id).first()

    if quota is None:
        quota = compute_coverage_progress(user_id)

    # Secondary is always a non-coercive escape hatch
    secondary = {
        'id': 'escape_free_play',
        'type': 'navigate',
        'label': t('Browse colors'),
        'reason': t('Pick any color to practice'),
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
            'label': t("Today's challenge"),
            'reason': t("Complete today's daily color challenge"),
            'payload': {'route': 'daily_challenge', 'challenge_date': policy_day},
        }

    # ── 2. Streak at risk ─────────────────────────────────────────────────
    if primary is None and _streak_at_risk(up, today):
        primary = {
            'id': 'streak_at_risk',
            'type': 'practice',
            'label': t('Save your streak'),
            'reason': t(
                'Your {n}-day streak is on the line — play once today to keep it',
                n=up.current_streak,
            ),
            'payload': {'route': 'match'},
        }

    # ── 3./4. Continue or start a match ───────────────────────────────────
    if primary is None:
        active = (Match.query
                  .filter_by(user_id=user_id, status='active')
                  .order_by(Match.started_at.desc())
                  .first())
        if active is not None:
            primary = {
                'id': 'continue_match',
                'type': 'practice',
                'label': t('Continue your match'),
                'reason': t('Round {i} of {n}',
                            i=active.current_round + 1, n=active.round_count),
                'payload': {'route': 'match', 'match_id': active.id},
            }
        else:
            primary = {
                'id': 'start_match',
                'type': 'practice',
                'label': t('Start a match'),
                'reason': t('10 rounds, one from every color family'),
                'payload': {'route': 'match'},
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
                'matches_completed': _matches_completed(user_id),
                'is_maxed_out': quota['is_maxed_out'],
            },
        },
        # Compact state for the header badge: has today's challenge been
        # submitted? (final_today was computed for the policy above.)
        'daily_status': {
            'challenge_date': policy_day,
            'submitted': final_today is not None,
        },
    }
