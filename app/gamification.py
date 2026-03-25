"""
Gamification engine: XP, levels, ranks, streaks, freeze, awards, coverage stats.

All presentation-computed fields are derived here; only durable state lives in DB.
"""
from datetime import date, datetime, timedelta
from .models import UserProgress, UserAward, UserTargetColorStats
from . import db

COVERAGE_QUOTA = 150
STREAK_FREEZE_CAP = 3

XP_TABLE = {
    'perfect': 100,
    'no_perceivable_difference': 60,
    'acceptable_difference': 30,
    'big_difference': 10,
    'stopped': 5,
}

# Index = level - 1; value = cumulative XP required to reach that level
LEVEL_THRESHOLDS = [0, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000]

RANK_TIERS = [
    {'min_level': 1, 'max_level': 2, 'rank': 'Bronze',   'color': '#CD7F32'},
    {'min_level': 3, 'max_level': 4, 'rank': 'Silver',   'color': '#C0C0C0'},
    {'min_level': 5, 'max_level': 6, 'rank': 'Gold',     'color': '#FFD700'},
    {'min_level': 7, 'max_level': 8, 'rank': 'Platinum', 'color': '#E5E4E2'},
    {'min_level': 9, 'max_level': 10, 'rank': 'Diamond', 'color': '#B9F2FF'},
]

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]
COVERAGE_MILESTONES = [
    (25,  'Quarter Coverage'),
    (75,  'Three Quarters'),
    (150, 'Full Coverage'),
]


# ---------------------------------------------------------------------------
# Presentation helpers (pure functions — no DB access)
# ---------------------------------------------------------------------------

def compute_level(xp):
    level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
        else:
            break
    return min(level, len(LEVEL_THRESHOLDS))


def compute_rank(level):
    for tier in RANK_TIERS:
        if tier['min_level'] <= level <= tier['max_level']:
            return tier['rank'], tier['color']
    last = RANK_TIERS[-1]
    return last['rank'], last['color']


def compute_level_progress(xp):
    """Returns (xp_in_current_level, xp_to_next_level)."""
    level = compute_level(xp)
    idx = level - 1
    level_start = LEVEL_THRESHOLDS[idx]
    if level < len(LEVEL_THRESHOLDS):
        level_end = LEVEL_THRESHOLDS[level]
        return xp - level_start, level_end - xp
    return xp - level_start, 0


# ---------------------------------------------------------------------------
# Award helper
# ---------------------------------------------------------------------------

def _grant_award(user_id, award_key, award_scope='lifetime', award_scope_key='lifetime', metadata=None):
    """Idempotent. Returns (award_obj, is_new)."""
    existing = UserAward.query.filter_by(
        user_id=user_id,
        award_key=award_key,
        award_scope=award_scope,
        award_scope_key=award_scope_key,
    ).first()
    if existing:
        return existing, False
    award = UserAward(
        user_id=user_id,
        award_key=award_key,
        award_scope=award_scope,
        award_scope_key=award_scope_key,
        metadata_json=metadata or {},
    )
    db.session.add(award)
    return award, True


# ---------------------------------------------------------------------------
# Progress response builder
# ---------------------------------------------------------------------------

def build_progress_response(user_id, user_progress, catalog_size):
    """
    Build the canonical progress JSON object.
    Shape is identical across save_session, save_skip, and get_user_progress.
    """
    up = user_progress
    xp = up.xp if up else 0
    level = compute_level(xp)
    rank, rank_color = compute_rank(level)
    xp_in_level, xp_to_next = compute_level_progress(xp)

    coverage_pct = 0.0
    if catalog_size > 0 and up:
        qualified = UserTargetColorStats.query.filter(
            UserTargetColorStats.user_id == user_id,
            UserTargetColorStats.attempt_count >= COVERAGE_QUOTA,
        ).count()
        coverage_pct = round(qualified / catalog_size * 100, 1)

    return {
        'xp': xp,
        'level': level,
        'level_name': f'Level {level}',
        'xp_in_level': xp_in_level,
        'xp_to_next_level': xp_to_next,
        'rank': rank,
        'rank_color': rank_color,
        'current_streak': up.current_streak if up else 0,
        'longest_streak': up.longest_streak if up else 0,
        'streak_freeze_available': up.streak_freeze_available if up else 0,
        'last_activity_date': (up.last_activity_date.isoformat()
                               if up and up.last_activity_date else None),
        'catalog_coverage_pct': coverage_pct,
    }


# ---------------------------------------------------------------------------
# Core progression engine
# ---------------------------------------------------------------------------

def process_progression(user_id, match_category, skipped, target_color_id, delta_e, today=None):
    """
    Must be called inside an open db.session transaction.
    Returns (xp_earned, new_awards, streak_event, level_up_event).

    streak_event values:
        None | 'started' | 'same_day' | 'incremented' | 'freeze_consumed' | 'reset'
    level_up_event:
        None | {'from': int, 'to': int}
    """
    if today is None:
        today = date.today()

    new_awards = []
    level_up = None
    streak_event = None

    # Get or create UserProgress
    up = UserProgress.query.filter_by(user_id=user_id).first()
    if not up:
        up = UserProgress(
            user_id=user_id, xp=0, level=1,
            current_streak=0, longest_streak=0, streak_freeze_available=0,
        )
        db.session.add(up)

    old_level = compute_level(up.xp)

    # --- XP ---
    xp_earned = XP_TABLE.get(match_category, 5)
    up.xp += xp_earned
    up.updated_at = datetime.utcnow()

    new_level = compute_level(up.xp)
    if new_level > old_level:
        level_up = {'from': old_level, 'to': new_level}
        for lvl in range(old_level + 1, new_level + 1):
            _, is_new = _grant_award(user_id, f'level_{lvl}', metadata={'level': lvl})
            if is_new:
                new_awards.append({
                    'key': f'level_{lvl}',
                    'name': f'Level {lvl} Reached!',
                    'type': 'level',
                    'icon': '⬆️',
                })
        up.level = new_level

    # --- Streak (only non-skip qualifies) ---
    qualifying = not skipped

    if qualifying:
        last = up.last_activity_date
        if last is None:
            up.current_streak = 1
            streak_event = 'started'
            up.last_activity_date = today
        elif last == today:
            streak_event = 'same_day'
        elif last == today - timedelta(days=1):
            up.current_streak += 1
            streak_event = 'incremented'
            up.last_activity_date = today
        elif last == today - timedelta(days=2) and up.streak_freeze_available > 0:
            up.streak_freeze_available -= 1
            up.current_streak += 1
            streak_event = 'freeze_consumed'
            up.last_activity_date = today
        else:
            up.current_streak = 1
            streak_event = 'reset'
            up.last_activity_date = today

        up.longest_streak = max(up.longest_streak, up.current_streak)

        for milestone in STREAK_MILESTONES:
            if up.current_streak >= milestone:
                _, is_new = _grant_award(user_id, f'streak_{milestone}',
                                         metadata={'days': milestone})
                if is_new:
                    new_awards.append({
                        'key': f'streak_{milestone}',
                        'name': f'{milestone}-Day Streak!',
                        'type': 'streak',
                        'icon': '🔥',
                    })

    # --- First perfect match ---
    if match_category == 'perfect':
        _, is_new = _grant_award(user_id, 'first_perfect_match')
        if is_new:
            new_awards.append({
                'key': 'first_perfect_match',
                'name': 'First Perfect Match!',
                'type': 'achievement',
                'icon': '🎯',
            })

    # --- Coverage stats ---
    if target_color_id is not None:
        stats = UserTargetColorStats.query.filter_by(
            user_id=user_id, target_color_id=target_color_id,
        ).first()
        if not stats:
            stats = UserTargetColorStats(
                user_id=user_id, target_color_id=target_color_id,
                attempt_count=0, completed_count=0,
            )
            db.session.add(stats)

        old_count = stats.attempt_count
        stats.attempt_count += 1
        if not skipped:
            stats.completed_count += 1
        if delta_e is not None and (stats.best_delta_e is None or delta_e < stats.best_delta_e):
            stats.best_delta_e = delta_e
        stats.last_attempt_at = datetime.utcnow()

        for threshold, label in COVERAGE_MILESTONES:
            if old_count < threshold <= stats.attempt_count:
                _, is_new = _grant_award(
                    user_id, f'coverage_{threshold}_{target_color_id}',
                    metadata={'target_color_id': target_color_id, 'threshold': threshold},
                )
                if is_new:
                    new_awards.append({
                        'key': f'coverage_{threshold}_{target_color_id}',
                        'name': f'{label} — Color #{target_color_id}',
                        'type': 'coverage',
                        'icon': '🎨',
                        'threshold': threshold,
                    })

    return xp_earned, new_awards, streak_event, level_up


# ---------------------------------------------------------------------------
# Daily champion reward
# ---------------------------------------------------------------------------

def grant_daily_champion(user_id, challenge_date_str):
    """
    Grant daily champion badge + 1 streak freeze (capped at STREAK_FREEZE_CAP).
    Idempotent. Returns list of new_awards.
    """
    new_awards = []
    _, is_new = _grant_award(
        user_id, 'daily_champion',
        award_scope='daily', award_scope_key=challenge_date_str,
        metadata={'date': challenge_date_str},
    )
    if is_new:
        new_awards.append({
            'key': 'daily_champion',
            'name': 'Daily Champion!',
            'type': 'daily',
            'icon': '🏆',
            'date': challenge_date_str,
        })
        up = UserProgress.query.filter_by(user_id=user_id).first()
        if up and up.streak_freeze_available < STREAK_FREEZE_CAP:
            up.streak_freeze_available += 1
    return new_awards


# ---------------------------------------------------------------------------
# Quota-aware catalog helper
# ---------------------------------------------------------------------------

def get_quota_ordered_catalog(user_id, full_catalog):
    """
    Annotate each catalog entry with attempt_count and under_quota flag.
    The client uses this for quota-priority selection.
    """
    if not user_id:
        return full_catalog

    stats_map = {
        s.target_color_id: s.attempt_count
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
    }

    result = []
    for c in full_catalog:
        c_copy = dict(c)
        c_copy['attempt_count'] = stats_map.get(c['id'], 0)
        c_copy['under_quota'] = c_copy['attempt_count'] < COVERAGE_QUOTA
        result.append(c_copy)
    return result


# ---------------------------------------------------------------------------
# Full profile for results page
# ---------------------------------------------------------------------------

def get_user_profile(user_id, catalog_size):
    """Returns (progress_dict, awards_list, color_stats_list)."""
    up = UserProgress.query.filter_by(user_id=user_id).first()
    progress = build_progress_response(user_id, up, catalog_size)

    awards = (
        UserAward.query
        .filter_by(user_id=user_id)
        .order_by(UserAward.unlocked_at.desc())
        .all()
    )
    awards_list = [
        {
            'key': a.award_key,
            'scope': a.award_scope,
            'scope_key': a.award_scope_key,
            'metadata': a.metadata_json,
            'unlocked_at': a.unlocked_at.isoformat() if a.unlocked_at else None,
        }
        for a in awards
    ]

    color_stats = (
        UserTargetColorStats.query
        .filter_by(user_id=user_id)
        .all()
    )
    color_stats_list = [
        {
            'target_color_id': s.target_color_id,
            'attempt_count': s.attempt_count,
            'completed_count': s.completed_count,
            'best_delta_e': s.best_delta_e,
            'last_attempt_at': s.last_attempt_at.isoformat() if s.last_attempt_at else None,
        }
        for s in color_stats
    ]

    return progress, awards_list, color_stats_list
