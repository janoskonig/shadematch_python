"""
Gamification engine: XP, levels, ranks, streaks, freeze, awards, coverage stats.

Gameplay is match-based (app/matches.py): a match = 10 rounds, one target from
each of the 10 macro-clusters (app/clusters.py). There is no content gate — the
old sum-drop band ladder and the played-twice coverage quota were removed;
every gamut colour can be drawn in any match.

Levels/ranks are XP-driven; XP and streaks are reinforcement only. Coverage is
purely descriptive: how many distinct colours the player has completed at
least once (compute_coverage_progress).
"""
import re
from datetime import date, datetime, timedelta, time
from flask import has_request_context
from .models import (
    UserProgress, UserAward, UserTargetColorStats, TargetColor, MixingSession,
    MixingAttempt, Match,
)
from . import db
from .i18n import t as _t_request
from .regions import region_of_target


def _t(text, **kwargs):
    """Translate for the current request locale; identity (English) outside a
    request context (scripts like verify_probe_pipeline call the engine
    directly). Constants such as RANK_TIERS / DAILY_MISSIONS stay English in
    code and DB — translation happens only here, at the response boundary."""
    if has_request_context():
        return _t_request(text, **kwargs)
    return text.format(**kwargs) if kwargs else text

STREAK_FREEZE_CAP = 3

# Main gameplay is the even-gamut / skin target set (color_type='gamut'); the older
# basic/skin/lab catalog is retired from serving, coverage and levels.
GAMUT_TYPE = 'gamut'

# Total leveling slots (levels/ranks are XP-driven, see _xp_level).
LEVEL_COUNT = 30

XP_TABLE = {
    'perfect': 130,
    'no_perceivable_difference': 78,
    'acceptable_difference': 38,
    'big_difference': 14,
    'stopped': 7,
}

# Match categories that count as a "completed" session for user-facing tallies
# and daily performance awards. Skips marked unacceptable (big difference) and
# legacy 'stopped' rows are excluded.
COMPLETED_MATCH_CATEGORIES = (
    'perfect',
    'no_perceivable_difference',
    'acceptable_difference',
)

# Rank tiers — 6 ranks × 5 levels each over 30 levels.
RANK_TIERS = [
    {'min_level': 1,  'max_level': 5,  'rank': 'Bronze',   'color': '#CD7F32'},
    {'min_level': 6,  'max_level': 10, 'rank': 'Silver',   'color': '#C0C0C0'},
    {'min_level': 11, 'max_level': 15, 'rank': 'Gold',     'color': '#FFD700'},
    {'min_level': 16, 'max_level': 20, 'rank': 'Platinum', 'color': '#E5E4E2'},
    {'min_level': 21, 'max_level': 25, 'rank': 'Diamond',  'color': '#B9F2FF'},
    {'min_level': 26, 'max_level': 30, 'rank': 'Master',   'color': '#FF6F61'},
]

STREAK_MILESTONES = [3, 7, 14, 30, 60, 100]

# ── In-session "heat" (consecutive completions within one sitting) ──────────
# From the HEAT_MIN_CONSECUTIVE-th consecutive completed round inside the
# rolling window, XP gets a bonus that grows per additional completion.
# Server-authoritative: computed from persisted MixingSession rows, never
# from a client-supplied value.
HEAT_WINDOW_MINUTES = 30
HEAT_MIN_CONSECUTIVE = 3
HEAT_STEP_PCT = 0.10
HEAT_MAX_BONUS_PCT = 0.50

# Global coverage % milestones (share of catalog colours completed at least once).
QUOTA_GLOBAL_MILESTONE_PCTS = [25, 50, 75]


# One mission, matching the unit of play: finish a whole 10-round match today.
# (The old per-colour micro-missions — perfect hit / fast finish / few steps —
# were retired with match-based gameplay; their historical awards keep their
# keys and labels on the results page.)
DAILY_MISSIONS = [
    {
        'id': 'complete_match',
        'label': 'Daily Match',
        'description': 'Finish one full 10-round match today.',
        'award_name': 'Daily Match Complete',
        'icon': '🏁',
    },
]


# ---------------------------------------------------------------------------
# XP-based helpers kept for backward-compat XP display (never drive level/completion)
# ---------------------------------------------------------------------------

# XP thresholds to reach each level (index 0 = L1). Level/rank are XP-driven: every
# mix earns quality-weighted XP (XP_TABLE), so leveling is continuous and a player's
# standing reflects their whole history. Calibrated to the live XP range (~7..70k):
# fast early levels (L2 ≈ 2 rounds), the median player around L6, p90 ≈ Gold, the most
# dedicated ≈ Diamond, with Master (L26-30) left as aspirational headroom.
LEVEL_THRESHOLDS = [
    0,      150,    400,    800,    1400,   2200,   3300,   4800,   6700,   9000,
    11800,  15200,  19300,  24200,  30000,  36800,  44700,  53800,  64200,  76000,
    89400,  104500, 121400, 140300, 161400, 184900, 211000, 240000, 272200, 308000,
]


def _xp_level(xp: int) -> int:
    """XP → level index (1..LEVEL_COUNT). Used only for legacy XP bar display."""
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
    """Return today's mission board with completion flags. The single mission
    is finishing one whole match (all 10 rounds resolved) today."""
    if not user_id:
        return {'date': (day or date.today()).isoformat(), 'missions': []}
    day = day or date.today()
    start_dt, end_dt = _day_window(day)
    finished_today = (
        Match.query
        .filter_by(user_id=user_id, status='completed')
        .filter(Match.completed_at >= start_dt, Match.completed_at < end_dt)
        .count()
    )
    completed_ids = {'complete_match'} if finished_today > 0 else set()

    missions = []
    for m in DAILY_MISSIONS:
        missions.append({
            'id': m['id'],
            'label': _t(m['label']),
            'description': _t(m['description']),
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
                'name': _t(mission_def['award_name']),
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
        .filter(MixingSession.match_category.in_(COMPLETED_MATCH_CATEGORIES))
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


def _catalog_rows():
    """Ordered main-gameplay catalog rows (gamut targets only)."""
    return (TargetColor.query
            .filter_by(color_type=GAMUT_TYPE)
            .order_by(TargetColor.catalog_order.asc())
            .all())


# ---------------------------------------------------------------------------
# Coverage (descriptive only — no gating)
# ---------------------------------------------------------------------------

def compute_coverage_progress(user_id: str) -> dict:
    """
    Descriptive catalog coverage: how many distinct recipe-complete gamut
    colours the player has completed (saved, completed_count > 0) at least
    once. Nothing here gates gameplay — matches draw from the whole catalog.

    Returns:
      completed_colors     – distinct recipe colours completed >= 1x
      total_tracked_colors – recipe-complete gamut colours in the catalog
      coverage_ratio       – completed / total (0.0 when catalog empty)
      catalog_coverage_pct – coverage_ratio * 100, rounded to 1 dp
      is_maxed_out         – every recipe colour completed at least once
    """
    recipe_ids = {
        tc.id for tc in _catalog_rows()
        if target_color_sum_drop(tc) is not None
    }
    total = len(recipe_ids)

    completed = 0
    if user_id:
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all():
            if int(s.completed_count or 0) > 0 and s.target_color_id in recipe_ids:
                completed += 1

    ratio = (completed / total) if total else 0.0
    return {
        'completed_colors': completed,
        'total_tracked_colors': total,
        'coverage_ratio': ratio,
        'catalog_coverage_pct': round(ratio * 100, 1),
        'is_maxed_out': bool(total > 0 and completed >= total),
    }


# ---------------------------------------------------------------------------
# Gamut-region mastery (self-serve diploma eligibility)
# ---------------------------------------------------------------------------

# A player "masters" one gamut region by completing at least this many of the
# colours that fall inside it (see app/regions.py for the CIELAB grid cells).
# 1 = breadth ("touched every corner of the gamut"); raise it later for a
# depth-based diploma without changing any callers.
REGION_MASTERY_MIN_COMPLETED = 1


def compute_region_mastery(user_id: str) -> dict:
    """
    Per-region gamut coverage for the printable mastery diploma.

    Every gamut target maps to one CIELAB region (region_of_target); the ~332
    targets fall into ~54 regions. A region is *mastered* once the player has
    completed (completed_count > 0) at least REGION_MASTERY_MIN_COMPLETED of its
    colours. `is_gamut_master` is true when every region that has ≥1 catalog
    target is mastered.

    Returns:
      total_regions        – distinct regions present in the gamut catalog
      regions_mastered     – regions meeting the completion threshold
      is_gamut_master      – regions_mastered >= total_regions (and >0)
      region_mastery_pct   – regions_mastered / total_regions * 100 (1 dp)
    """
    region_of = {}
    region_ids = set()
    for tc in _catalog_rows():
        rid = region_of_target(tc)
        region_of[tc.id] = rid
        region_ids.add(rid)
    total_regions = len(region_ids)

    completed_per_region = {}
    if user_id:
        for s in UserTargetColorStats.query.filter_by(user_id=user_id).all():
            if int(s.completed_count or 0) <= 0:
                continue
            rid = region_of.get(s.target_color_id)
            if rid is not None:
                completed_per_region[rid] = completed_per_region.get(rid, 0) + 1

    regions_mastered = sum(
        1 for rid in region_ids
        if completed_per_region.get(rid, 0) >= REGION_MASTERY_MIN_COMPLETED
    )
    is_gamut_master = total_regions > 0 and regions_mastered >= total_regions
    pct = round(100.0 * regions_mastered / total_regions, 1) if total_regions else 0.0
    return {
        'total_regions': total_regions,
        'regions_mastered': regions_mastered,
        'is_gamut_master': is_gamut_master,
        'region_mastery_pct': pct,
    }


# ---------------------------------------------------------------------------
# Progress response builder
# ---------------------------------------------------------------------------

def _matches_completed(user_id: str) -> int:
    if not user_id:
        return 0
    return Match.query.filter_by(user_id=user_id, status='completed').count()


def build_progress_response(user_id: str, user_progress, _catalog_size_ignored=None) -> dict:
    """
    Build the canonical progress JSON object.
    Shape is identical across save_session, save_skip, and get_user_progress.
    Levels/ranks are XP-driven; coverage fields are descriptive only.
    """
    up = user_progress
    xp = up.xp if up else 0

    quota = compute_coverage_progress(user_id)
    # Level/rank are XP-driven (continuous, quality-weighted, history-preserving).
    # XP only grows, so this is monotonic; floor by the cached level as a safety net.
    computed_lv = _xp_level(xp)
    peak_lv = up.level if up else 1
    level = max(peak_lv, computed_lv)
    # Progress toward the next level, measured in XP within the current level.
    xp_in, xp_to = _xp_level_progress(xp)
    span = xp_in + xp_to
    level_progress_pct = 100.0 if xp_to <= 0 else round(100.0 * xp_in / span, 1)
    rank, rank_color = compute_rank(level)

    xp_in_level, xp_to_next = _xp_level_progress(xp)

    return {
        # ── Level / coverage (descriptive) ──────────────────────────
        'level': level,
        'level_name': _t('Level {level}', level=level),
        'level_progress_pct': level_progress_pct,
        'rank': _t(rank),
        'rank_color': rank_color,
        'completed_colors': quota['completed_colors'],
        'total_tracked_colors': quota['total_tracked_colors'],
        'coverage_ratio': round(quota['coverage_ratio'], 6),
        'catalog_coverage_pct': quota['catalog_coverage_pct'],
        'is_maxed_out': quota['is_maxed_out'],
        'matches_completed': _matches_completed(user_id),
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
# In-session heat
# ---------------------------------------------------------------------------

def compute_heat_consecutive(user_id, now=None):
    """
    Consecutive completed sessions counting back from the most recent one,
    all within the rolling HEAT_WINDOW_MINUTES window.

    Called from process_progression AFTER the current round's MixingSession
    has been added to the db.session: SQLAlchemy autoflush makes the query
    see it, so the returned count includes the round being scored.
    """
    if not user_id:
        return 0
    now = now or datetime.utcnow()
    window_start = now - timedelta(minutes=HEAT_WINDOW_MINUTES)
    rows = (
        MixingSession.query
        .filter(MixingSession.user_id == user_id)
        .filter(MixingSession.timestamp >= window_start)
        .order_by(MixingSession.timestamp.desc())
        .limit(20)
        .all()
    )
    streak = 0
    for r in rows:
        if r.match_category in COMPLETED_MATCH_CATEGORIES:
            streak += 1
        else:
            break
    return streak


def heat_bonus_pct(consecutive: int) -> float:
    """Bonus fraction for a given consecutive-completion count (0.0 when cold)."""
    if consecutive < HEAT_MIN_CONSECUTIVE:
        return 0.0
    return min(HEAT_STEP_PCT * (consecutive - HEAT_MIN_CONSECUTIVE + 1), HEAT_MAX_BONUS_PCT)


# ---------------------------------------------------------------------------
# Core progression engine
# ---------------------------------------------------------------------------

def process_progression(user_id, match_category, skipped, target_color_id, delta_e, today=None,
                        is_probe=False, is_challenge=False):
    """
    Must be called inside an open db.session transaction.
    Returns (xp_earned, new_awards, streak_event, level_up_event, heat_info).

    heat_info is None when the round is cold, else
        {'consecutive': int, 'bonus_pct': float, 'xp_bonus': int}.

    is_challenge: head-to-head challenge rounds are stats-neutral like probes
    (XP, streak and missions accrue; UserTargetColorStats does not move) —
    challenge links on hand-picked colours must not distort coverage stats.

    is_probe: experimental probe rounds (learning-effect study) are
    stats-neutral — XP, streak and daily missions accrue normally, but the
    round must not touch UserTargetColorStats (exposure snapshots for the
    study read MixingSession instead).

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

    # ── Snapshot old coverage state before any writes ─────────────────────
    old_coverage = compute_coverage_progress(user_id)
    old_is_maxed = old_coverage['is_maxed_out']

    # ── Get or create UserProgress ───────────────────────────────────────
    up = UserProgress.query.filter_by(user_id=user_id).first()
    old_level = _xp_level(up.xp if up else 0)   # XP-driven level, pre-round
    if not up:
        up = UserProgress(
            user_id=user_id, xp=0, level=old_level,
            current_streak=0, longest_streak=0, streak_freeze_available=0,
        )
        db.session.add(up)

    old_display_level = up.level

    # ── XP (secondary; kept for reinforcement toasts) ────────────────────
    base_xp = XP_TABLE.get(match_category, 5)
    heat_info = None
    if match_category in COMPLETED_MATCH_CATEGORIES:
        consecutive = compute_heat_consecutive(user_id)
        bonus_pct = heat_bonus_pct(consecutive)
        if bonus_pct > 0:
            heat_info = {
                'consecutive': consecutive,
                'bonus_pct': bonus_pct,
                'xp_bonus': int(round(base_xp * bonus_pct)),
            }
    xp_earned = base_xp + (heat_info['xp_bonus'] if heat_info else 0)
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
                        'name': _t('{n}-Day Streak!', n=milestone),
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
                'name': _t('First Perfect Match!'),
                'type': 'achievement',
                'award_class': 'reinforcement',
                'icon': '🎯',
            })

    # ── Color stats (descriptive bookkeeping: results page, awards) ───────
    # Attempts landing in a completed bucket (perfect match, or a skip rated
    # "identical" / "acceptable small difference") count as attempts; skips
    # marked "unacceptable big difference" and legacy 'stopped' rows are
    # persisted for analytics but do not move the stats.
    counts_as_attempt = match_category in COMPLETED_MATCH_CATEGORIES

    if target_color_id is not None and not is_probe and not is_challenge:
        stats = UserTargetColorStats.query.filter_by(
            user_id=user_id, target_color_id=target_color_id,
        ).first()
        if not stats:
            stats = UserTargetColorStats(
                user_id=user_id, target_color_id=target_color_id,
                attempt_count=0, completed_count=0,
            )
            db.session.add(stats)

        if counts_as_attempt:
            stats.attempt_count += 1
        if not skipped:
            stats.completed_count += 1
        if delta_e is not None and (stats.best_delta_e is None or delta_e < stats.best_delta_e):
            stats.best_delta_e = delta_e
        stats.last_attempt_at = datetime.utcnow()

    db.session.flush()
    new_coverage = compute_coverage_progress(user_id)

    old_ratio = old_coverage['coverage_ratio']
    new_ratio = new_coverage['coverage_ratio']
    new_is_maxed = new_coverage['is_maxed_out']

    # up.xp was already incremented above, so this is the post-round XP level.
    computed_new = _xp_level(up.xp)
    # XP only grows, so this is monotonic; floor by the cached level as a safety net.
    final_level = max(up.level, computed_new)

    # ── Global coverage milestone awards (award keys unchanged) ───────────
    for milestone_pct in QUOTA_GLOBAL_MILESTONE_PCTS:
        milestone_ratio = milestone_pct / 100.0
        if old_ratio < milestone_ratio <= new_ratio:
            key = f'quota_milestone_{milestone_pct}pct'
            _, is_new = _grant_award(user_id, key, metadata={'pct': milestone_pct})
            if is_new:
                new_awards.append({
                    'key': key,
                    'name': _t('{pct}% of All Colors Complete!', pct=milestone_pct),
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
                'name': _t('All Colors Mastered!'),
                'type': 'quota_complete',
                'award_class': 'quota_major',
                'icon': '🎊',
            })

    # ── XP-based level-up (monotonic display level) ───────────────────────
    if final_level > old_display_level:
        level_up = {'from': old_display_level, 'to': final_level}
        for lvl in range(old_display_level + 1, final_level + 1):
            _, is_new = _grant_award(user_id, f'level_{lvl}', metadata={'level': lvl})
            if is_new:
                new_awards.append({
                    'key': f'level_{lvl}',
                    'name': _t('Level {n} Reached!', n=lvl),
                    'type': 'level',
                    'award_class': 'reinforcement',
                    'icon': '⬆️',
                })
    up.level = final_level

    return xp_earned, new_awards, streak_event, level_up, heat_info


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
# Full profile for results page
# ---------------------------------------------------------------------------

# Legacy pre-gamut target names that are machine strings, not color names.
_UGLY_NAME_RE = re.compile(r'^(#[0-9A-Fa-f]{6}$|Lab RGB\()')

_NTC = None            # ([(name, (L,a,b)), ...], {name_en: name_hu})
_NEAREST_CACHE = {}    # (r,g,b) -> NTC name


def _ntc_lookup():
    """Lazy-loaded 'Name That Color' dictionary (Lab) + Hungarian glosses."""
    global _NTC
    if _NTC is None:
        import csv
        from pathlib import Path
        from .regions import _srgb_to_lab
        repo = Path(__file__).resolve().parents[1]
        entries = []
        with open(repo / 'data' / 'colornames_ntc.csv', newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                h = row['hex'].lstrip('#')
                rgb = (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
                entries.append((row['name'], _srgb_to_lab(*rgb)))
        glosses = {}
        gloss_csv = repo / 'translations' / 'color_names_hu.csv'
        if gloss_csv.exists():
            with open(gloss_csv, newline='', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    glosses[row['name_en']] = row['name_hu']
        _NTC = (entries, glosses)
    return _NTC


def _nearest_color_name(r, g, b):
    """Nearest NTC color name by Lab distance (display only, collisions OK)."""
    key = (r, g, b)
    if key not in _NEAREST_CACHE:
        from .regions import _srgb_to_lab
        L, a, bb = _srgb_to_lab(r, g, b)
        entries = _ntc_lookup()[0]
        _NEAREST_CACHE[key] = min(
            entries, key=lambda e: (e[1][0] - L) ** 2 + (e[1][1] - a) ** 2 + (e[1][2] - bb) ** 2
        )[0]
    return _NEAREST_CACHE[key]


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
    # Friendly color names for the coverage bars ('Merlot — bordó' for hu).
    # Legacy pre-gamut targets are named '#RRGGBB' / 'Lab RGB(r,g,b)' in the DB;
    # those get a nearest-NTC display name instead.
    from .i18n import get_locale
    hu = has_request_context() and get_locale() == 'hu'
    stat_ids = [s.target_color_id for s in color_stats]
    color_names = {}
    if stat_ids:
        rows = (db.session.query(TargetColor.id, TargetColor.name, TargetColor.name_hu,
                                 TargetColor.r, TargetColor.g, TargetColor.b)
                .filter(TargetColor.id.in_(stat_ids)).all())
        for tc_id, name, name_hu, r, g, b in rows:
            if _UGLY_NAME_RE.match(name):
                name = _nearest_color_name(r, g, b)
                name_hu = _ntc_lookup()[1].get(name)
            color_names[tc_id] = f'{name} — {name_hu}' if hu and name_hu else name
    color_stats_list = [
        {
            'target_color_id': s.target_color_id,
            'name': color_names.get(s.target_color_id),
            'attempt_count': s.attempt_count,
            'completed_count': s.completed_count,
            'best_delta_e': s.best_delta_e,
            'last_attempt_at': s.last_attempt_at.isoformat() if s.last_attempt_at else None,
        }
        for s in color_stats
    ]

    return progress, awards_list, color_stats_list
