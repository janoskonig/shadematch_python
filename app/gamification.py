"""
Gamification engine: XP, levels, ranks, streaks, freeze, awards, coverage stats.

Quota-first design:
  - Level 1..10 is derived entirely from quota coverage ratio.
  - is_maxed_out is the only valid completion signal.
  - XP/streaks are retained as secondary reinforcement, never as completion.
  - Awards are split: quota_major (per-color 150, global %, final) vs reinforcement.
"""
from datetime import date, datetime, timedelta
from .models import UserProgress, UserAward, UserTargetColorStats, TargetColor
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

RANK_TIERS = [
    {'min_level': 1,  'max_level': 2,  'rank': 'Bronze',   'color': '#CD7F32'},
    {'min_level': 3,  'max_level': 4,  'rank': 'Silver',   'color': '#C0C0C0'},
    {'min_level': 5,  'max_level': 6,  'rank': 'Gold',     'color': '#FFD700'},
    {'min_level': 7,  'max_level': 8,  'rank': 'Platinum', 'color': '#E5E4E2'},
    {'min_level': 9,  'max_level': 10, 'rank': 'Diamond',  'color': '#B9F2FF'},
]

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]

# Per-color reinforcement milestones (< COVERAGE_QUOTA only; 150 is quota_major)
PER_COLOR_REINFORCEMENT_MILESTONES = [
    (25,  'Quarter Coverage'),
    (75,  'Three Quarters'),
]

# Global quota coverage % milestones (quota_major)
QUOTA_GLOBAL_MILESTONE_PCTS = [25, 50, 75]


# ---------------------------------------------------------------------------
# Quota-level helpers (pure functions)
# ---------------------------------------------------------------------------

def compute_level_from_quota(coverage_ratio: float, is_maxed_out: bool) -> int:
    """
    Fixed range 1..10 derived from quota coverage ratio.
      - Maxed users: level 10.
      - Others: min(9, floor(ratio * 9) + 1).
    """
    if is_maxed_out:
        return 10
    return min(9, int(coverage_ratio * 9) + 1)


def compute_level_progress_pct_from_quota(coverage_ratio: float, is_maxed_out: bool) -> float:
    """Fractional progress within the current level (0.0 – 100.0)."""
    if is_maxed_out:
        return 100.0
    level = compute_level_from_quota(coverage_ratio, is_maxed_out)
    level_start = (level - 1) / 9.0
    level_end = level / 9.0
    span = level_end - level_start  # always 1/9
    if span <= 0:
        return 100.0
    pct = (coverage_ratio - level_start) / span * 100.0
    return round(min(100.0, max(0.0, pct)), 2)


# ---------------------------------------------------------------------------
# XP-based helpers kept for backward-compat XP display (never drive level/completion)
# ---------------------------------------------------------------------------

# Kept only for XP secondary display; level is now quota-derived.
LEVEL_THRESHOLDS = [0, 200, 500, 1000, 2000, 4000, 8000, 16000, 32000, 64000]


def _xp_level(xp: int) -> int:
    """XP → level index (1..10). Used only for legacy XP bar display."""
    level = 1
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp >= threshold:
            level = i + 1
        else:
            break
    return min(level, len(LEVEL_THRESHOLDS))


def _xp_level_progress(xp: int):
    """Returns (xp_in_level, xp_to_next) for the XP reinforcement bar."""
    level = _xp_level(xp)
    idx = level - 1
    level_start = LEVEL_THRESHOLDS[idx]
    if level < len(LEVEL_THRESHOLDS):
        level_end = LEVEL_THRESHOLDS[level]
        return xp - level_start, level_end - xp
    return xp - level_start, 0


# ---------------------------------------------------------------------------
# Rank (still derived from quota-based level)
# ---------------------------------------------------------------------------

def compute_rank(level: int):
    for tier in RANK_TIERS:
        if tier['min_level'] <= level <= tier['max_level']:
            return tier['rank'], tier['color']
    last = RANK_TIERS[-1]
    return last['rank'], last['color']


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
# Canonical quota helpers
# ---------------------------------------------------------------------------

def get_tracked_color_ids_for_user(user_id) -> list:
    """
    Deterministic global policy: all catalog colors are tracked for every user.
    Returns ordered list of TargetColor IDs (catalog_order ascending).
    """
    return [tc.id for tc in TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()]


def compute_quota_progress(user_id: str) -> dict:
    """
    Canonical quota progress contract. All downstream consumers must read this.

    Returns:
      tracked_color_ids        – all catalog color IDs (deterministic)
      completed_attempt_units  – sum(min(attempt_count, 150)) across all tracked
      required_attempt_units   – total_tracked_colors * 150
      completed_colors         – count with attempt_count >= 150
      total_tracked_colors     – len(tracked)
      remaining_attempts_total – required - completed
      coverage_ratio           – completed / required  (0.0 if required == 0)
      catalog_coverage_pct     – coverage_ratio * 100 rounded to 1 dp
      is_maxed_out             – completed_colors == total_tracked_colors (and > 0)
      nearest_deficit_color_id – smallest positive remaining, tiebreak: lowest catalog_order
      nearest_deficit_remaining
      color_quota_map          – {color_id: {attempt_count, quota_contribution, remaining}}
    """
    tracked = TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
    tracked_color_ids = [tc.id for tc in tracked]
    total_tracked_colors = len(tracked_color_ids)

    stats_map = {}
    if user_id:
        stats_map = {
            s.target_color_id: s.attempt_count
            for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
        }

    required_attempt_units = total_tracked_colors * COVERAGE_QUOTA
    completed_attempt_units = 0
    completed_colors = 0
    color_quota_map = {}
    nearest_deficit_color_id = None
    nearest_deficit_remaining = None

    for tc in tracked:
        attempt_count = stats_map.get(tc.id, 0)
        quota_contribution = min(attempt_count, COVERAGE_QUOTA)
        remaining = max(0, COVERAGE_QUOTA - attempt_count)
        completed_attempt_units += quota_contribution
        if attempt_count >= COVERAGE_QUOTA:
            completed_colors += 1
        color_quota_map[tc.id] = {
            'attempt_count': attempt_count,
            'quota_contribution': quota_contribution,
            'remaining': remaining,
        }
        # Nearest deficit: smallest positive remaining; tiebreak: lowest catalog_order (natural sort)
        if remaining > 0:
            if nearest_deficit_color_id is None or remaining < nearest_deficit_remaining:
                nearest_deficit_color_id = tc.id
                nearest_deficit_remaining = remaining

    if required_attempt_units > 0:
        coverage_ratio = completed_attempt_units / required_attempt_units
    else:
        coverage_ratio = 0.0

    catalog_coverage_pct = round(coverage_ratio * 100, 1)
    is_maxed_out = (completed_colors == total_tracked_colors and total_tracked_colors > 0)
    remaining_attempts_total = required_attempt_units - completed_attempt_units

    return {
        'tracked_color_ids': tracked_color_ids,
        'completed_attempt_units': completed_attempt_units,
        'required_attempt_units': required_attempt_units,
        'completed_colors': completed_colors,
        'total_tracked_colors': total_tracked_colors,
        'remaining_attempts_total': remaining_attempts_total,
        'coverage_ratio': coverage_ratio,
        'catalog_coverage_pct': catalog_coverage_pct,
        'is_maxed_out': is_maxed_out,
        'nearest_deficit_color_id': nearest_deficit_color_id,
        'nearest_deficit_remaining': nearest_deficit_remaining,
        'color_quota_map': color_quota_map,
    }


# ---------------------------------------------------------------------------
# Progress response builder
# ---------------------------------------------------------------------------

def build_progress_response(user_id: str, user_progress, _catalog_size_ignored=None) -> dict:
    """
    Build the canonical progress JSON object.
    Shape is identical across save_session, save_skip, and get_user_progress.
    Level and completion state are derived from quota only.
    XP and streak are retained as secondary reinforcement fields.
    """
    up = user_progress
    xp = up.xp if up else 0

    quota = compute_quota_progress(user_id)
    level = compute_level_from_quota(quota['coverage_ratio'], quota['is_maxed_out'])
    level_progress_pct = compute_level_progress_pct_from_quota(quota['coverage_ratio'], quota['is_maxed_out'])
    rank, rank_color = compute_rank(level)

    xp_in_level, xp_to_next = _xp_level_progress(xp)

    return {
        # ── Quota-first fields ──────────────────────────────────────
        'level': level,
        'level_name': f'Level {level}',
        'level_progress_pct': level_progress_pct,
        'rank': rank,
        'rank_color': rank_color,
        'completed_colors': quota['completed_colors'],
        'total_tracked_colors': quota['total_tracked_colors'],
        'remaining_attempts_total': quota['remaining_attempts_total'],
        'coverage_ratio': round(quota['coverage_ratio'], 6),
        'catalog_coverage_pct': quota['catalog_coverage_pct'],
        'is_maxed_out': quota['is_maxed_out'],
        'nearest_deficit_color_id': quota['nearest_deficit_color_id'],
        'nearest_deficit_remaining': quota['nearest_deficit_remaining'],
        # ── Streak / freeze ─────────────────────────────────────────
        'current_streak': up.current_streak if up else 0,
        'longest_streak': up.longest_streak if up else 0,
        'streak_freeze_available': up.streak_freeze_available if up else 0,
        'last_activity_date': (up.last_activity_date.isoformat()
                               if up and up.last_activity_date else None),
        # ── XP (secondary reinforcement, never drives completion) ───
        'xp': xp,
        'xp_in_level': xp_in_level,
        'xp_to_next_level': xp_to_next,
    }


# ---------------------------------------------------------------------------
# Core progression engine
# ---------------------------------------------------------------------------

def process_progression(user_id, match_category, skipped, target_color_id, delta_e, today=None):
    """
    Must be called inside an open db.session transaction.
    Returns (xp_earned, new_awards, streak_event, level_up_event).

    level_up_event is now quota-driven: fires when quota coverage crosses a
    level boundary. XP is still accumulated but never drives level or maxed state.

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

    # ── Snapshot old quota state before any writes ───────────────────────
    old_quota = compute_quota_progress(user_id)
    old_level = compute_level_from_quota(old_quota['coverage_ratio'], old_quota['is_maxed_out'])
    old_is_maxed = old_quota['is_maxed_out']

    # ── Get or create UserProgress ───────────────────────────────────────
    up = UserProgress.query.filter_by(user_id=user_id).first()
    if not up:
        up = UserProgress(
            user_id=user_id, xp=0, level=old_level,
            current_streak=0, longest_streak=0, streak_freeze_available=0,
        )
        db.session.add(up)

    # ── XP (secondary; kept for reinforcement toasts) ────────────────────
    xp_earned = XP_TABLE.get(match_category, 5)
    up.xp += xp_earned
    up.updated_at = datetime.utcnow()

    # ── Streak (only non-skip qualifies) ─────────────────────────────────
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
                        'award_class': 'reinforcement',
                        'icon': '🔥',
                    })

    # ── First perfect match ───────────────────────────────────────────────
    if match_category == 'perfect':
        _, is_new = _grant_award(user_id, 'first_perfect_match')
        if is_new:
            new_awards.append({
                'key': 'first_perfect_match',
                'name': 'First Perfect Match!',
                'type': 'achievement',
                'award_class': 'reinforcement',
                'icon': '🎯',
            })

    # ── Color stats + quota awards ────────────────────────────────────────
    delta_quota_units = 0
    color_crossed_quota = False

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

        # Quota delta (capped contribution)
        old_contrib = min(old_count, COVERAGE_QUOTA)
        new_contrib = min(stats.attempt_count, COVERAGE_QUOTA)
        delta_quota_units = new_contrib - old_contrib
        color_crossed_quota = (old_count < COVERAGE_QUOTA <= stats.attempt_count)

        # Per-color reinforcement milestones (25, 75 attempts)
        for threshold, label in PER_COLOR_REINFORCEMENT_MILESTONES:
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
                        'award_class': 'reinforcement',
                        'icon': '🎨',
                        'threshold': threshold,
                    })

        # Per-color quota completion (quota_major)
        if color_crossed_quota:
            _, is_new = _grant_award(
                user_id, f'coverage_{COVERAGE_QUOTA}_{target_color_id}',
                metadata={'target_color_id': target_color_id, 'threshold': COVERAGE_QUOTA},
            )
            if is_new:
                new_awards.append({
                    'key': f'coverage_{COVERAGE_QUOTA}_{target_color_id}',
                    'name': f'Color #{target_color_id} — {COVERAGE_QUOTA} Attempts!',
                    'type': 'coverage',
                    'award_class': 'quota_major',
                    'icon': '✅',
                    'threshold': COVERAGE_QUOTA,
                })

    # ── Compute new quota state from delta (no second DB query) ──────────
    old_ratio = old_quota['coverage_ratio']
    old_completed_units = old_quota['completed_attempt_units']
    old_required_units = old_quota['required_attempt_units']
    old_completed_colors = old_quota['completed_colors']
    total_tc = old_quota['total_tracked_colors']

    new_completed_units = old_completed_units + delta_quota_units
    new_completed_colors = old_completed_colors + (1 if color_crossed_quota else 0)
    new_ratio = new_completed_units / old_required_units if old_required_units > 0 else 0.0
    new_is_maxed = (new_completed_colors == total_tc and total_tc > 0)
    new_level = compute_level_from_quota(new_ratio, new_is_maxed)

    # ── Global quota milestone awards (quota_major) ───────────────────────
    for milestone_pct in QUOTA_GLOBAL_MILESTONE_PCTS:
        milestone_ratio = milestone_pct / 100.0
        if old_ratio < milestone_ratio <= new_ratio:
            key = f'quota_milestone_{milestone_pct}pct'
            _, is_new = _grant_award(user_id, key, metadata={'pct': milestone_pct})
            if is_new:
                new_awards.append({
                    'key': key,
                    'name': f'{milestone_pct}% of All Colors Complete!',
                    'type': 'quota_milestone',
                    'award_class': 'quota_major',
                    'icon': '🏆',
                    'pct': milestone_pct,
                })

    # ── Final completion award (quota_major) ──────────────────────────────
    if not old_is_maxed and new_is_maxed:
        _, is_new = _grant_award(user_id, 'all_colors_complete',
                                 metadata={'completed_date': today.isoformat()})
        if is_new:
            new_awards.append({
                'key': 'all_colors_complete',
                'name': 'All Colors Mastered!',
                'type': 'quota_complete',
                'award_class': 'quota_major',
                'icon': '🎊',
            })

    # ── Quota-based level-up ──────────────────────────────────────────────
    if new_level > old_level:
        level_up = {'from': old_level, 'to': new_level}
        for lvl in range(old_level + 1, new_level + 1):
            _, is_new = _grant_award(user_id, f'level_{lvl}', metadata={'level': lvl})
            if is_new:
                new_awards.append({
                    'key': f'level_{lvl}',
                    'name': f'Level {lvl} Reached!',
                    'type': 'level',
                    'award_class': 'reinforcement',
                    'icon': '⬆️',
                })
        up.level = new_level

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
            'award_class': 'reinforcement',
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
    Annotate each catalog entry with:
      - attempt_count  : per-user plays (from UserTargetColorStats)
      - under_quota    : attempt_count < COVERAGE_QUOTA
      - unlocked       : user's quota level >= color's level_required
    """
    if not user_id:
        return full_catalog

    stats_map = {
        s.target_color_id: s.attempt_count
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
    }

    # Compute quota-based level without a second UserProgress query
    total_tc = len(full_catalog)
    if total_tc > 0:
        required_units = total_tc * COVERAGE_QUOTA
        completed_units = sum(min(stats_map.get(c['id'], 0), COVERAGE_QUOTA) for c in full_catalog)
        cov_ratio = completed_units / required_units
        completed_cols = sum(1 for c in full_catalog if stats_map.get(c['id'], 0) >= COVERAGE_QUOTA)
        is_maxed = (completed_cols == total_tc)
    else:
        cov_ratio = 0.0
        is_maxed = False

    user_level = compute_level_from_quota(cov_ratio, is_maxed)

    result = []
    for c in full_catalog:
        c_copy = dict(c)
        c_copy['attempt_count'] = stats_map.get(c['id'], 0)
        c_copy['under_quota'] = c_copy['attempt_count'] < COVERAGE_QUOTA
        c_copy['unlocked'] = user_level >= c.get('level_required', 1)
        result.append(c_copy)
    return result


# ---------------------------------------------------------------------------
# Full profile for results page
# ---------------------------------------------------------------------------

def get_user_profile(user_id, _catalog_size_ignored=None):
    """Returns (progress_dict, awards_list, color_stats_list)."""
    up = UserProgress.query.filter_by(user_id=user_id).first()
    progress = build_progress_response(user_id, up)

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
