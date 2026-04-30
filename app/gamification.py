"""
Gamification engine: XP, levels, ranks, streaks, freeze, awards, coverage stats.

Quota-first design:
  - Level 1..9 is derived from tier quota coverage ratio (thresholds tuned for faster early progression).
  - Level 10 when fully maxed; is_maxed_out is the completion signal for that tier.
  - XP/streaks are secondary reinforcement (toasts / XP bar), not tier unlock.
  - Awards are split: quota_major (per-color COVERAGE_QUOTA, global %, final) vs reinforcement.
"""
from datetime import date, datetime, timedelta, time
from .models import UserProgress, UserAward, UserTargetColorStats, TargetColor, MixingSession, MixingAttempt
from . import db

COVERAGE_QUOTA = 10
STREAK_FREEZE_CAP = 3

# Sum-drop tier: quota and games only count colors with a full recipe and
# MIN_SUM_DROP_BAND <= sum(drops) <= user's max_sum_drop_unlocked (capped).
MIN_SUM_DROP_BAND = 2
MAX_SUM_DROP_CATALOG_CAP = 28

# Nonlinear level starts for quota coverage ratio (levels 1..9).
# Lower early thresholds than legacy so levels 2–5 arrive sooner (less idle time).
# Level 10 still requires is_maxed_out=True (all recipe colors at quota, cap open).
LEVEL_START_THRESHOLDS = [0.00, 0.018, 0.048, 0.095, 0.155, 0.24, 0.35, 0.48, 0.62]

XP_TABLE = {
    'perfect': 130,
    'no_perceivable_difference': 78,
    'acceptable_difference': 38,
    'big_difference': 14,
    'stopped': 7,
}

RANK_TIERS = [
    {'min_level': 1,  'max_level': 2,  'rank': 'Bronze',   'color': '#CD7F32'},
    {'min_level': 3,  'max_level': 4,  'rank': 'Silver',   'color': '#C0C0C0'},
    {'min_level': 5,  'max_level': 6,  'rank': 'Gold',     'color': '#FFD700'},
    {'min_level': 7,  'max_level': 8,  'rank': 'Platinum', 'color': '#E5E4E2'},
    {'min_level': 9,  'max_level': 10, 'rank': 'Diamond',  'color': '#B9F2FF'},
]

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]

# Per-color reinforcement milestones (must be < COVERAGE_QUOTA).
PER_COLOR_REINFORCEMENT_MILESTONES = [
    (2, 'Warm-Up'),
    (4, 'Building'),
    (6, 'Strong push'),
    (8, 'Almost there'),
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
    ratio = min(1.0, max(0.0, coverage_ratio))
    level = 1
    for idx, threshold in enumerate(LEVEL_START_THRESHOLDS[1:], start=2):
        if ratio >= threshold:
            level = idx
        else:
            break
    return min(9, level)


def compute_level_progress_pct_from_quota(coverage_ratio: float, is_maxed_out: bool) -> float:
    """Fractional progress within the current level (0.0 – 100.0)."""
    if is_maxed_out:
        return 100.0
    level = compute_level_from_quota(coverage_ratio, is_maxed_out)
    level_start = LEVEL_START_THRESHOLDS[level - 1]
    level_end = 1.0 if level >= 9 else LEVEL_START_THRESHOLDS[level]
    span = level_end - level_start
    if span <= 0:
        return 100.0
    pct = (coverage_ratio - level_start) / span * 100.0
    return round(min(100.0, max(0.0, pct)), 2)


DAILY_MISSIONS = [
    {
        'id': 'precision_hit',
        'label': 'Precision Hit',
        'description': 'Get at least one perfect match today.',
        'award_name': 'Daily Precision Hit',
        'icon': '🎯',
    },
    {
        'id': 'fast_finish',
        'label': 'Fast Finish',
        'description': 'Complete one color in 25 seconds or less.',
        'award_name': 'Daily Fast Finish',
        'icon': '⚡',
    },
    {
        'id': 'efficient_mixer',
        'label': 'Efficient Mixer',
        'description': 'Complete one color in 12 steps or fewer.',
        'award_name': 'Daily Efficient Mixer',
        'icon': '🧪',
    },
]


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


def _day_window(day: date):
    start_dt = datetime.combine(day, time.min)
    end_dt = start_dt + timedelta(days=1)
    return start_dt, end_dt


def _build_day_session_step_map(sessions):
    uuids = [s.attempt_uuid for s in sessions if s.attempt_uuid]
    if not uuids:
        return {}
    rows = MixingAttempt.query.filter(MixingAttempt.attempt_uuid.in_(uuids)).all()
    return {r.attempt_uuid: r.num_steps for r in rows}


def _effective_steps_for_session(session, step_map):
    tracked_steps = step_map.get(session.attempt_uuid)
    if tracked_steps is not None and tracked_steps >= 0:
        return tracked_steps
    # Fallback for older rows lacking telemetry step count.
    return (
        (session.drop_white or 0)
        + (session.drop_black or 0)
        + (session.drop_red or 0)
        + (session.drop_yellow or 0)
        + (session.drop_blue or 0)
    )


def build_daily_missions(user_id: str, day: date = None):
    """Return today's mission board with completion flags."""
    if not user_id:
        return {'date': (day or date.today()).isoformat(), 'missions': []}
    day = day or date.today()
    start_dt, end_dt = _day_window(day)
    sessions = (
        MixingSession.query
        .filter(MixingSession.user_id == user_id)
        .filter(MixingSession.timestamp >= start_dt, MixingSession.timestamp < end_dt)
        .all()
    )
    step_map = _build_day_session_step_map(sessions)
    completed_ids = set()

    for s in sessions:
        if s.match_category == 'perfect':
            completed_ids.add('precision_hit')
        if (not s.skipped) and s.time_sec is not None and s.time_sec <= 25:
            completed_ids.add('fast_finish')
        if not s.skipped:
            steps = _effective_steps_for_session(s, step_map)
            if steps <= 12:
                completed_ids.add('efficient_mixer')

    missions = []
    for m in DAILY_MISSIONS:
        missions.append({
            'id': m['id'],
            'label': m['label'],
            'description': m['description'],
            'completed': m['id'] in completed_ids,
            'icon': m['icon'],
        })
    return {'date': day.isoformat(), 'missions': missions}


def grant_daily_mission_awards(user_id: str, day: date = None):
    """
    Grant mission completion awards for the given day.
    Idempotent via (award_key, daily scope, day key).
    """
    day = day or date.today()
    mission_state = build_daily_missions(user_id, day=day)
    new_awards = []

    mission_map = {m['id']: m for m in DAILY_MISSIONS}
    for item in mission_state['missions']:
        if not item.get('completed'):
            continue
        mission_id = item['id']
        mission_def = mission_map[mission_id]
        award_key = f'daily_mission_{mission_id}'
        _, is_new = _grant_award(
            user_id,
            award_key,
            award_scope='daily',
            award_scope_key=mission_state['date'],
            metadata={'date': mission_state['date'], 'mission_id': mission_id},
        )
        if is_new:
            new_awards.append({
                'key': award_key,
                'name': mission_def['award_name'],
                'type': 'daily_mission',
                'award_class': 'daily',
                'icon': mission_def['icon'],
                'date': mission_state['date'],
            })
    return new_awards


def grant_daily_performance_awards(day: date):
    """
    Resolve daily performance awards across all users for a date:
      - Fastest completed match
      - Fewest steps completed match
    Returns a list of newly granted awards with winner user IDs.
    """
    start_dt, end_dt = _day_window(day)
    sessions = (
        MixingSession.query
        .filter(MixingSession.timestamp >= start_dt, MixingSession.timestamp < end_dt)
        .filter(MixingSession.skipped.is_(False))
        .all()
    )
    if not sessions:
        return []

    new_awards = []

    fastest_candidates = [s for s in sessions if s.time_sec is not None]
    if fastest_candidates:
        fastest = min(
            fastest_candidates,
            key=lambda s: (s.time_sec, s.timestamp or datetime.max),
        )
        _, is_new = _grant_award(
            fastest.user_id,
            'daily_fastest_match',
            award_scope='daily',
            award_scope_key=day.isoformat(),
            metadata={'date': day.isoformat(), 'time_sec': fastest.time_sec},
        )
        if is_new:
            new_awards.append({
                'user_id': fastest.user_id,
                'key': 'daily_fastest_match',
                'name': 'Fastest Color Match of the Day',
                'type': 'daily_performance',
                'award_class': 'daily',
                'icon': '⚡',
                'date': day.isoformat(),
            })

    step_map = _build_day_session_step_map(sessions)
    step_candidates = []
    for s in sessions:
        steps = _effective_steps_for_session(s, step_map)
        step_candidates.append((steps, s.timestamp or datetime.max, s))

    if step_candidates:
        _, _, best_steps_session = min(step_candidates, key=lambda t: (t[0], t[1]))
        best_steps = _effective_steps_for_session(best_steps_session, step_map)
        _, is_new = _grant_award(
            best_steps_session.user_id,
            'daily_fewest_steps',
            award_scope='daily',
            award_scope_key=day.isoformat(),
            metadata={'date': day.isoformat(), 'steps': best_steps},
        )
        if is_new:
            new_awards.append({
                'user_id': best_steps_session.user_id,
                'key': 'daily_fewest_steps',
                'name': 'Fewest Steps of the Day',
                'type': 'daily_performance',
                'award_class': 'daily',
                'icon': '🪄',
                'date': day.isoformat(),
            })

    return new_awards


# ---------------------------------------------------------------------------
# Sum-drop helpers
# ---------------------------------------------------------------------------

def target_color_sum_drop(tc) -> int | None:
    """Total recipe drops, or None if any channel is unset (incomplete recipe)."""
    vals = [tc.drop_white, tc.drop_black, tc.drop_red, tc.drop_yellow, tc.drop_blue]
    if any(v is None for v in vals):
        return None
    return int(sum(int(v or 0) for v in vals))


def _catalog_recipe_max_sum() -> int:
    m = 0
    for tc in TargetColor.query.all():
        s = target_color_sum_drop(tc)
        if s is not None:
            m = max(m, s)
    return m


def _effective_sum_cap_from_catalog_max(max_sum_drop_unlocked: int, catalog_max: int) -> int:
    """User cap clamped to catalog content and global ceiling (catalog_max from a preloaded row set)."""
    top = catalog_max if catalog_max > 0 else MAX_SUM_DROP_CATALOG_CAP
    return min(int(max_sum_drop_unlocked), MAX_SUM_DROP_CATALOG_CAP, max(top, MIN_SUM_DROP_BAND))


def _effective_sum_cap(max_sum_drop_unlocked: int) -> int:
    """User cap clamped to catalog content and global ceiling."""
    return _effective_sum_cap_from_catalog_max(max_sum_drop_unlocked, _catalog_recipe_max_sum())


def _eligible_target_colors(max_sum_drop_unlocked: int) -> list:
    """Colors with complete recipe and sum in [MIN_SUM_DROP_BAND, effective_cap]."""
    cap = _effective_sum_cap(max_sum_drop_unlocked)
    out = []
    for tc in TargetColor.query.order_by(TargetColor.catalog_order.asc()).all():
        s = target_color_sum_drop(tc)
        if s is None:
            continue
        if MIN_SUM_DROP_BAND <= s <= cap:
            out.append(tc)
    return out


def advance_max_sum_drop_unlocked(user_id: str) -> int:
    """
    While every eligible color (for current cap) is at quota, raise cap by 1.
    Returns number of steps the cap advanced (0 or more).
    """
    up = UserProgress.query.filter_by(user_id=user_id).first()
    if not up:
        return 0
    steps = 0
    stats_map = {
        s.target_color_id: s.attempt_count
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
    }
    catalog_max = _catalog_recipe_max_sum()
    ceiling = min(MAX_SUM_DROP_CATALOG_CAP, catalog_max) if catalog_max > 0 else MAX_SUM_DROP_CATALOG_CAP
    while up.max_sum_drop_unlocked < ceiling:
        eligible = _eligible_target_colors(up.max_sum_drop_unlocked)
        if not eligible:
            break
        if not all(stats_map.get(tc.id, 0) >= COVERAGE_QUOTA for tc in eligible):
            break
        up.max_sum_drop_unlocked += 1
        steps += 1
    return steps


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

    Tier-only quota: only colors with a complete recipe and
    MIN_SUM_DROP_BAND <= sum_drop <= effective_cap(max_sum_drop_unlocked) count.

    Returns:
      tracked_color_ids        – eligible color IDs only (same order as catalog)
      eligible_color_ids       – same as tracked_color_ids (explicit alias)
      completed_attempt_units  – sum(min(attempt_count, COVERAGE_QUOTA)) across eligible
      required_attempt_units   – len(eligible) * COVERAGE_QUOTA
      completed_colors         – eligible count with attempt_count >= COVERAGE_QUOTA
      total_tracked_colors     – len(eligible)
      max_sum_drop_unlocked    – user's current cap (default 4 if no row)
      remaining_attempts_total – required - completed
      coverage_ratio           – completed / required  (0.0 if required == 0)
      catalog_coverage_pct     – coverage_ratio * 100 rounded to 1 dp
      is_maxed_out             – all recipe colors at quota AND cap fully opened
      nearest_deficit_color_id – among eligible only
      nearest_deficit_remaining
      color_quota_map          – all catalog IDs; ineligible have remaining 0 contribution
    """
    max_sum_unlocked = 4  # default tier cap when no user_progress row
    if user_id:
        up = UserProgress.query.filter_by(user_id=user_id).first()
        if up is not None:
            max_sum_unlocked = int(up.max_sum_drop_unlocked)

    # Single ordered catalog load: max recipe sum, effective cap, and eligible colors
    # (avoids 3–4 full-table scans from _eligible_target_colors + duplicate _catalog_recipe_max_sum).
    all_tracked = TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
    catalog_max = 0
    for tc in all_tracked:
        s = target_color_sum_drop(tc)
        if s is not None:
            catalog_max = max(catalog_max, s)
    effective = _effective_sum_cap_from_catalog_max(max_sum_unlocked, catalog_max)
    eligible = []
    for tc in all_tracked:
        s = target_color_sum_drop(tc)
        if s is None:
            continue
        if MIN_SUM_DROP_BAND <= s <= effective:
            eligible.append(tc)

    tracked_color_ids = [tc.id for tc in eligible]
    eligible_ids = set(tracked_color_ids)
    total_tracked_colors = len(tracked_color_ids)

    stats_map = {}
    if user_id:
        stats_map = {
            s.target_color_id: s.attempt_count
            for s in UserTargetColorStats.query.filter_by(user_id=user_id).all()
        }
    color_quota_map = {}
    for tc in all_tracked:
        attempt_count = stats_map.get(tc.id, 0)
        in_eligible = tc.id in eligible_ids
        if in_eligible:
            quota_contribution = min(attempt_count, COVERAGE_QUOTA)
            remaining = max(0, COVERAGE_QUOTA - attempt_count)
        else:
            quota_contribution = 0
            remaining = 0
        color_quota_map[tc.id] = {
            'attempt_count': attempt_count,
            'quota_contribution': quota_contribution,
            'remaining': remaining,
        }

    required_attempt_units = total_tracked_colors * COVERAGE_QUOTA
    completed_attempt_units = 0
    completed_colors = 0
    nearest_deficit_color_id = None
    nearest_deficit_remaining = None

    for tc in eligible:
        attempt_count = stats_map.get(tc.id, 0)
        quota_contribution = min(attempt_count, COVERAGE_QUOTA)
        remaining = max(0, COVERAGE_QUOTA - attempt_count)
        completed_attempt_units += quota_contribution
        if attempt_count >= COVERAGE_QUOTA:
            completed_colors += 1
        if remaining > 0:
            if nearest_deficit_color_id is None or remaining < nearest_deficit_remaining:
                nearest_deficit_color_id = tc.id
                nearest_deficit_remaining = remaining

    if required_attempt_units > 0:
        coverage_ratio = completed_attempt_units / required_attempt_units
    else:
        coverage_ratio = 0.0

    catalog_coverage_pct = round(coverage_ratio * 100, 1)

    recipe_colors = [tc for tc in all_tracked if target_color_sum_drop(tc) is not None]
    all_recipe_at_quota = (
        len(recipe_colors) > 0
        and all(stats_map.get(tc.id, 0) >= COVERAGE_QUOTA for tc in recipe_colors)
    )
    cap_fully_open = (
        catalog_max <= 0
        or max_sum_unlocked >= min(MAX_SUM_DROP_CATALOG_CAP, catalog_max)
    )
    is_maxed_out = bool(all_recipe_at_quota and cap_fully_open and len(recipe_colors) > 0)

    remaining_attempts_total = required_attempt_units - completed_attempt_units

    return {
        'tracked_color_ids': tracked_color_ids,
        'eligible_color_ids': tracked_color_ids,
        'completed_attempt_units': completed_attempt_units,
        'required_attempt_units': required_attempt_units,
        'completed_colors': completed_colors,
        'total_tracked_colors': total_tracked_colors,
        'max_sum_drop_unlocked': max_sum_unlocked,
        'effective_sum_cap': effective,
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
    computed_lv = compute_level_from_quota(quota['coverage_ratio'], quota['is_maxed_out'])
    peak_lv = up.level if up else 1
    level = max(peak_lv, computed_lv)
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
        'max_sum_drop_unlocked': quota['max_sum_drop_unlocked'],
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
            max_sum_drop_unlocked=4,
        )
        db.session.add(up)

    old_display_level = up.level

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

        color_crossed_quota = (old_count < COVERAGE_QUOTA <= stats.attempt_count)

        # Per-color reinforcement milestones (incremental chunks before quota completion)
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

    db.session.flush()
    advance_max_sum_drop_unlocked(user_id)
    new_quota = compute_quota_progress(user_id)

    old_ratio = old_quota['coverage_ratio']
    new_ratio = new_quota['coverage_ratio']
    new_is_maxed = new_quota['is_maxed_out']

    computed_new = compute_level_from_quota(new_ratio, new_is_maxed)
    final_level = max(up.level, computed_new)

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

    # ── Quota-based level-up (monotonic display level) ────────────────────
    if final_level > old_display_level:
        level_up = {'from': old_display_level, 'to': final_level}
        for lvl in range(old_display_level + 1, final_level + 1):
            _, is_new = _grant_award(user_id, f'level_{lvl}', metadata={'level': lvl})
            if is_new:
                new_awards.append({
                    'key': f'level_{lvl}',
                    'name': f'Level {lvl} Reached!',
                    'type': 'level',
                    'award_class': 'reinforcement',
                    'icon': '⬆️',
                })
    up.level = final_level

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

def get_quota_ordered_catalog(user_id, full_catalog, quota=None):
    """
    Annotate each catalog entry with:
      - attempt_count  : per-user plays (from UserTargetColorStats)
      - under_quota    : attempt_count < COVERAGE_QUOTA
      - unlocked       : full recipe and MIN_SUM_DROP_BAND <= sum <= effective user cap
    """
    if not user_id:
        return full_catalog

    if quota is None:
        quota = compute_quota_progress(user_id)
    effective = int(quota['effective_sum_cap'])
    cq = quota['color_quota_map']

    result = []
    for c in full_catalog:
        c_copy = dict(c)
        meta = cq.get(c['id'], {})
        c_copy['attempt_count'] = int(meta.get('attempt_count', 0))
        c_copy['under_quota'] = c_copy['attempt_count'] < COVERAGE_QUOTA
        s = c.get('sum_drop_count')
        c_copy['unlocked'] = (
            s is not None
            and MIN_SUM_DROP_BAND <= int(s) <= effective
        )
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
