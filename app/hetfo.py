"""Live dashboard for one registration-day cohort (the /hetfo page).

*Hétfő* is Hungarian for Monday, *szerda* for Wednesday. The QR share page
went live for the Monday recruitment session on 2026-08-31, with a second
session the Wednesday after (2026-09-02); each session has its own page
(``/hetfo``, ``/szerda``) following the people who registered that day: how
many are playing right now, what they are mixing, how far they got, and
whether they came back on later days.

The cohort day is a Europe/Budapest calendar day. ``User.created_at`` is
naive UTC, so the day is converted to a half-open UTC window before it is
compared. Each page's day defaults to its session date, overridable per
deployment (``HETFO_COHORT_DATE`` / ``SZERDA_COHORT_DATE``), and any day can
be viewed with ``?date=YYYY-MM-DD``.

Everything here is read-only, portable SQL (no PostgreSQL-only functions), so
it also runs against the SQLite fallback used by the tests. A cohort is tens
of players, so the per-user figures are a handful of GROUP BY queries
restricted to the cohort's ids, assembled into the JSON the page polls.
"""
from __future__ import annotations

import os
import statistics
import threading
import time
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import case, func

from . import db
from .gamification import _xp_level
from .matches import COMPLETED_MATCH_CATEGORIES
from .models import (
    AnalyticsEvent,
    CalibrationSession,
    CalibrationTrial,
    Match,
    MixingAttempt,
    MixingAttemptEvent,
    MixingSession,
    TargetColor,
    User,
    UserAward,
    UserProgress,
)

COHORT_TZ_NAME = 'Europe/Budapest'

# Named cohort pages: URL slug -> the session day it follows, and the env var
# that re-points it without a deploy. One template + one API serve them all.
COHORT_PAGES = {
    'hetfo': {'date': date(2026, 8, 31), 'env': 'HETFO_COHORT_DATE'},    # Monday session
    'szerda': {'date': date(2026, 9, 2), 'env': 'SZERDA_COHORT_DATE'},   # Wednesday session
}
DEFAULT_PAGE = 'hetfo'
DEFAULT_COHORT_DATE = COHORT_PAGES[DEFAULT_PAGE]['date']

# Heading / week-step labels follow the weekday of the day on screen (the
# Hungarian adjective forms differ per day, so these are whole t() keys).
# Index = date.weekday().
WEEKDAY_LABEL_KEYS = (
    ('Monday cohort — live', 'Previous Monday', 'Next Monday'),
    ('Tuesday cohort — live', 'Previous Tuesday', 'Next Tuesday'),
    ('Wednesday cohort — live', 'Previous Wednesday', 'Next Wednesday'),
    ('Thursday cohort — live', 'Previous Thursday', 'Next Thursday'),
    ('Friday cohort — live', 'Previous Friday', 'Next Friday'),
    ('Saturday cohort — live', 'Previous Saturday', 'Next Saturday'),
    ('Sunday cohort — live', 'Previous Sunday', 'Next Sunday'),
)

ONLINE_WINDOW_SEC = 180          # "playing now": the server heard from them in the last 3 min
RECENT_WINDOW_SEC = 3600         # "active in the last hour"
OPEN_ATTEMPT_MAX_AGE_SEC = 1800  # an unfinished attempt older than this is a closed tab, not play
DAY_BUCKET_MIN = 15              # registration-day activity resolution
SESSION_TIME_CAP_SEC = 1800.0    # per-round cap on play time, as on the leaderboard
REFRESH_SECONDS_DEFAULT = 5
REFRESH_SECONDS_MIN = 3
REFRESH_SECONDS_MAX = 120

# The live layer: what happened in the last little while, at minute resolution.
LIVE_LOOKBACK_SEC = 2 * 3600     # the event feed and per-player "last round" look back this far
LIVE_FEED_MAX = 80               # newest events kept in the feed
LIVE_MINUTES = 60                # rounds-per-minute series length
LIVE_WINDOWS_MIN = (5, 15, 60)   # rolling "rounds in the last N minutes" tiles
TRAJECTORY_MAX = 24              # ΔE steps kept per open attempt (the card sparkline)

# /live: everyone the server heard from in the last N hours, any cohort.
LIVE_DEFAULT_HOURS = 24
LIVE_MAX_HOURS = 168


# ── Time zone & cohort day ───────────────────────────────────────────────────

def cohort_tz():
    """The cohort calendar's time zone. zoneinfo needs the host's tzdata, so fall
    back to pytz (a pandas dependency, so always installed) and finally to CET."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(COHORT_TZ_NAME)
    except Exception:  # pragma: no cover - depends on host tzdata
        try:
            import pytz
            return pytz.timezone(COHORT_TZ_NAME)
        except Exception:
            return timezone(timedelta(hours=1), 'CET')


def _local_midnight_utc(day: date, tz) -> datetime:
    """Naive-UTC instant of local midnight on ``day`` (pytz needs localize())."""
    naive = datetime.combine(day, datetime.min.time())
    localize = getattr(tz, 'localize', None)
    aware = localize(naive) if localize else naive.replace(tzinfo=tz)
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def cohort_window_utc(day: date, tz=None):
    """Half-open naive-UTC window [start, end) covering the local calendar day."""
    tz = tz or cohort_tz()
    return _local_midnight_utc(day, tz), _local_midnight_utc(day + timedelta(days=1), tz)


def utc_to_local(naive_utc: datetime, tz=None) -> datetime:
    tz = tz or cohort_tz()
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(tz)


def parse_cohort_date(raw):
    """'YYYY-MM-DD' -> date. None when absent/blank; ValueError when malformed."""
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    return date.fromisoformat(raw)


def normalize_page(slug) -> str:
    """A known page slug, else the default page (unknown slugs never 404 the API)."""
    slug = str(slug or '').strip().lower()
    return slug if slug in COHORT_PAGES else DEFAULT_PAGE


def default_cohort_date(page: str = DEFAULT_PAGE) -> date:
    """The page's env override (e.g. HETFO_COHORT_DATE), else its session day."""
    spec = COHORT_PAGES[normalize_page(page)]
    try:
        configured = parse_cohort_date(os.environ.get(spec['env']))
    except ValueError:
        configured = None
    return configured or spec['date']


def resolve_cohort_date(raw, page: str = DEFAULT_PAGE) -> date:
    """The requested day (?date=) or the page's default; ValueError on garbage."""
    return parse_cohort_date(raw) or default_cohort_date(page)


def resolve_refresh_seconds(raw) -> int:
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        return REFRESH_SECONDS_DEFAULT
    return max(REFRESH_SECONDS_MIN, min(seconds, REFRESH_SECONDS_MAX))


def page_context(day: date, refresh_raw=None, date_error=False,
                 page: str = DEFAULT_PAGE) -> dict:
    """Everything the template needs to boot the poller on a cohort page."""
    page = normalize_page(page)
    title_key, prev_key, next_key = WEEKDAY_LABEL_KEYS[day.weekday()]
    return {
        'kind': 'cohort',
        'page': page,
        'date': day.isoformat(),
        'prev': (day - timedelta(days=7)).isoformat(),
        'next': (day + timedelta(days=7)).isoformat(),
        'is_default': day == default_cohort_date(page),
        'title_key': title_key,
        'prev_key': prev_key,
        'next_key': next_key,
        'hours': None,
        'api_url': '/api/hetfo/live?date=' + day.isoformat(),
        'tz': COHORT_TZ_NAME,
        'refresh_seconds': resolve_refresh_seconds(refresh_raw),
        'online_window_min': ONLINE_WINDOW_SEC // 60,
        'date_error': bool(date_error),
    }


def resolve_hours(raw) -> int:
    """?hours= for /live: an int clamped to 1..LIVE_MAX_HOURS, default 24."""
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return LIVE_DEFAULT_HOURS
    return max(1, min(hours, LIVE_MAX_HOURS))


def live_page_context(hours_raw=None, refresh_raw=None) -> dict:
    """Everything the template needs to boot the poller on /live."""
    hours = resolve_hours(hours_raw)
    return {
        'kind': 'live',
        'page': 'live',
        'date': None,
        'prev': None,
        'next': None,
        'is_default': hours == LIVE_DEFAULT_HOURS,
        'title_key': 'Live — everyone playing now',
        'prev_key': None,
        'next_key': None,
        'hours': hours,
        'api_url': '/api/live?hours=%d' % hours,
        'tz': COHORT_TZ_NAME,
        'refresh_seconds': resolve_refresh_seconds(refresh_raw),
        'online_window_min': ONLINE_WINDOW_SEC // 60,
        'date_error': False,
    }


# ── Small helpers ────────────────────────────────────────────────────────────

def _iso(dt):
    """Naive UTC -> 'YYYY-MM-DDTHH:MM:SSZ' (None passes through)."""
    if dt is None:
        return None
    return dt.replace(microsecond=0).isoformat() + 'Z'


def _seconds_since(now, dt):
    if dt is None:
        return None
    return max(0, int((now - dt).total_seconds()))


def _round(x, digits=2):
    if x is None:
        return None
    try:
        return round(float(x), digits)
    except (TypeError, ValueError):
        return None


def _count_if(condition):
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


def _age_on(birthdate, today):
    if not birthdate:
        return None
    return today.year - birthdate.year - (
        (today.month, today.day) < (birthdate.month, birthdate.day)
    )


class _LastSeen:
    """Latest server-side sighting per user, with the source that produced it."""

    def __init__(self):
        self._best = {}

    def bump(self, user_id, dt, source):
        if dt is None:
            return
        current = self._best.get(user_id)
        if current is None or dt > current[0]:
            self._best[user_id] = (dt, source)

    def get(self, user_id):
        return self._best.get(user_id, (None, None))

    def latest(self):
        """The most recent sighting across the whole cohort, or None."""
        return max((dt for dt, _src in self._best.values()), default=None)


# ── Queries (all restricted to the cohort's ids) ─────────────────────────────

def _cohort_users(start, end):
    return (
        User.query
        .filter(User.created_at.isnot(None), User.created_at >= start, User.created_at < end)
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )


def _active_user_ids(since):
    """Everyone the server heard from since ``since``: a saved round, a round
    started, an app event, a calibration block, or a registration."""
    ids = set()
    for column, stamp in (
        (MixingSession.user_id, MixingSession.timestamp),
        (MixingAttempt.user_id, MixingAttempt.attempt_started_server_ts),
        (AnalyticsEvent.user_id, AnalyticsEvent.ts),
        (CalibrationSession.user_id, CalibrationSession.started_at),
        (User.id, User.created_at),
    ):
        rows = (db.session.query(column)
                .filter(stamp >= since, column.isnot(None))
                .distinct()
                .all())
        ids.update(r[0] for r in rows)
    return ids


def _active_users(since):
    ids = _active_user_ids(since)
    if not ids:
        return []
    return (
        User.query
        .filter(User.id.in_(list(ids)))
        .order_by(User.created_at.asc(), User.id.asc())
        .all()
    )


def _progress_by_user(ids):
    rows = UserProgress.query.filter(UserProgress.user_id.in_(ids)).all()
    return {r.user_id: r for r in rows}


def _sessions_by_user(ids):
    capped_time = case(
        (MixingSession.time_sec > SESSION_TIME_CAP_SEC, SESSION_TIME_CAP_SEC),
        else_=MixingSession.time_sec,
    )
    rows = (
        db.session.query(
            MixingSession.user_id.label('uid'),
            func.count(MixingSession.id).label('rounds'),
            _count_if(MixingSession.match_category.in_(COMPLETED_MATCH_CATEGORIES)).label('completed'),
            _count_if(MixingSession.match_category == 'perfect').label('perfect'),
            _count_if(MixingSession.skipped.is_(True)).label('skipped'),
            func.min(MixingSession.delta_e).label('best_de'),
            func.avg(MixingSession.delta_e).label('mean_de'),
            func.coalesce(func.sum(capped_time), 0.0).label('play_time'),
            func.max(MixingSession.timestamp).label('last_round'),
        )
        .filter(MixingSession.user_id.in_(ids))
        .group_by(MixingSession.user_id)
        .all()
    )
    return {r.uid: r for r in rows}


def _matches_by_user(ids):
    rows = (
        db.session.query(
            Match.user_id.label('uid'),
            Match.status.label('status'),
            func.count(Match.id).label('n'),
            func.max(Match.current_round).label('round'),
            func.max(Match.round_count).label('round_count'),
        )
        .filter(Match.user_id.in_(ids))
        .group_by(Match.user_id, Match.status)
        .all()
    )
    out = {}
    for r in rows:
        entry = out.setdefault(r.uid, {'completed': 0, 'abandoned': 0, 'active': 0,
                                       'active_round': None, 'round_count': None})
        n = int(r.n or 0)
        if r.status in ('completed', 'abandoned', 'active'):
            entry[r.status] = n
        if r.status == 'active' and n > 0:
            entry['active_round'] = int(r.round or 0)
            entry['round_count'] = int(r.round_count or 0) or None
    return out


def _awards_by_user(ids):
    rows = (
        db.session.query(UserAward.user_id, func.count(UserAward.id))
        .filter(UserAward.user_id.in_(ids))
        .group_by(UserAward.user_id)
        .all()
    )
    return {uid: int(n or 0) for uid, n in rows}


def _attempts_by_user(ids):
    rows = (
        db.session.query(
            MixingAttempt.user_id.label('uid'),
            func.count(MixingAttempt.attempt_uuid).label('n'),
            func.max(MixingAttempt.attempt_started_server_ts).label('last_started'),
            func.max(MixingAttempt.attempt_ended_server_ts).label('last_ended'),
        )
        .filter(MixingAttempt.user_id.in_(ids))
        .group_by(MixingAttempt.user_id)
        .all()
    )
    return {r.uid: r for r in rows}


def _open_attempts(ids, now):
    """Most recent unfinished attempt per user, if it started recently enough to
    still be a live round (abandoned tabs never get their terminal flush)."""
    since = now - timedelta(seconds=OPEN_ATTEMPT_MAX_AGE_SEC)
    rows = (
        MixingAttempt.query
        .filter(
            MixingAttempt.user_id.in_(ids),
            MixingAttempt.end_reason.is_(None),
            MixingAttempt.attempt_started_server_ts >= since,
        )
        .order_by(MixingAttempt.attempt_started_server_ts.desc())
        .all()
    )
    latest = {}
    for r in rows:
        latest.setdefault(r.user_id, r)
    return latest


def _recent_step_stamps(ids, now):
    """Latest mixing-step timestamp per user, from attempts started in the last
    two hours (the events table is large; go through the attempt index)."""
    since = now - timedelta(seconds=2 * RECENT_WINDOW_SEC)
    rows = (
        db.session.query(
            MixingAttempt.user_id.label('uid'),
            func.max(MixingAttemptEvent.server_ts).label('last_step'),
        )
        .join(MixingAttemptEvent, MixingAttemptEvent.attempt_uuid == MixingAttempt.attempt_uuid)
        .filter(
            MixingAttempt.user_id.in_(ids),
            MixingAttempt.attempt_started_server_ts >= since,
        )
        .group_by(MixingAttempt.user_id)
        .all()
    )
    return {r.uid: r.last_step for r in rows}


def _trajectories(attempt_uuids):
    """Post-action ΔE per step, in seq order, per open attempt (the last
    TRAJECTORY_MAX of them). The game client flushes steps mid-round every few
    seconds, so this is what the player sees on screen, give or take a flush."""
    if not attempt_uuids:
        return {}
    rows = (
        db.session.query(MixingAttemptEvent.attempt_uuid, MixingAttemptEvent.delta_e_after)
        .filter(
            MixingAttemptEvent.attempt_uuid.in_(attempt_uuids),
            MixingAttemptEvent.delta_e_after.isnot(None),
        )
        .order_by(MixingAttemptEvent.attempt_uuid.asc(), MixingAttemptEvent.seq.asc())
        .all()
    )
    out = {}
    for uuid, de in rows:
        out.setdefault(uuid, []).append(round(float(de), 2))
    return {uuid: vals[-TRAJECTORY_MAX:] for uuid, vals in out.items()}


def _recent_app_opens(ids, since):
    rows = (
        db.session.query(
            AnalyticsEvent.user_id,
            func.coalesce(AnalyticsEvent.received_at, AnalyticsEvent.ts).label('t'),
        )
        .filter(
            AnalyticsEvent.user_id.in_(ids),
            AnalyticsEvent.event == 'app_opened',
            AnalyticsEvent.ts >= since,
        )
        .all()
    )
    return [(uid, t) for uid, t in rows if t is not None]


def _recent_calibration_sessions(ids, since):
    return (
        CalibrationSession.query
        .filter(
            CalibrationSession.user_id.in_(ids),
            (CalibrationSession.started_at >= since) | (CalibrationSession.ended_at >= since),
        )
        .all()
    )


def _recent_completed_matches(ids, since):
    return (
        db.session.query(Match.user_id, Match.completed_at)
        .filter(Match.user_id.in_(ids), Match.status == 'completed', Match.completed_at >= since)
        .all()
    )


def _analytics_stamps(ids, start):
    rows = (
        db.session.query(
            AnalyticsEvent.user_id.label('uid'),
            func.max(func.coalesce(AnalyticsEvent.received_at, AnalyticsEvent.ts)).label('last'),
        )
        .filter(AnalyticsEvent.user_id.in_(ids), AnalyticsEvent.ts >= start)
        .group_by(AnalyticsEvent.user_id)
        .all()
    )
    return {r.uid: r.last for r in rows}


def _calibration_by_user(ids, now):
    rows = (
        db.session.query(
            CalibrationSession.user_id.label('uid'),
            _count_if(CalibrationSession.ended_at.isnot(None)).label('completed'),
            func.max(CalibrationSession.started_at).label('last_started'),
            func.max(CalibrationSession.ended_at).label('last_ended'),
        )
        .filter(CalibrationSession.user_id.in_(ids))
        .group_by(CalibrationSession.user_id)
        .all()
    )
    summary = {r.uid: r for r in rows}

    trial_rows = (
        db.session.query(
            CalibrationSession.user_id.label('uid'),
            func.max(CalibrationTrial.responded_at).label('last_trial'),
        )
        .join(CalibrationTrial, CalibrationTrial.session_uuid == CalibrationSession.session_uuid)
        .filter(CalibrationSession.user_id.in_(ids))
        .group_by(CalibrationSession.user_id)
        .all()
    )
    last_trial = {r.uid: r.last_trial for r in trial_rows}

    # Latest completed session's thresholds (the number the player was shown).
    completed = (
        CalibrationSession.query
        .filter(CalibrationSession.user_id.in_(ids), CalibrationSession.ended_at.isnot(None))
        .order_by(CalibrationSession.ended_at.desc())
        .all()
    )
    thresholds = {}
    for s in completed:
        thresholds.setdefault(s.user_id, s)

    # Sessions still open and young enough to be in progress right now.
    since = now - timedelta(seconds=OPEN_ATTEMPT_MAX_AGE_SEC)
    open_sessions = (
        CalibrationSession.query
        .filter(
            CalibrationSession.user_id.in_(ids),
            CalibrationSession.ended_at.is_(None),
            CalibrationSession.started_at >= since,
        )
        .order_by(CalibrationSession.started_at.desc())
        .all()
    )
    open_by_user = {}
    for s in open_sessions:
        open_by_user.setdefault(s.user_id, s)
    open_progress = {}
    if open_by_user:
        uuids = [s.session_uuid for s in open_by_user.values()]
        prog = (
            db.session.query(
                CalibrationTrial.session_uuid.label('sid'),
                func.count(CalibrationTrial.judgment).label('answered'),
                func.count(CalibrationTrial.id).label('total'),
                func.max(CalibrationTrial.responded_at).label('last'),
            )
            .filter(CalibrationTrial.session_uuid.in_(uuids))
            .group_by(CalibrationTrial.session_uuid)
            .all()
        )
        open_progress = {r.sid: r for r in prog}

    return summary, last_trial, thresholds, open_by_user, open_progress


def _timeline_rows(ids, start):
    """Every saved round of the cohort since its registration day: feeds the
    two history charts, the rolling live windows and each player's last round."""
    return (
        db.session.query(
            MixingSession.user_id, MixingSession.timestamp, MixingSession.match_category,
            MixingSession.delta_e, MixingSession.time_sec, MixingSession.skipped,
            MixingSession.target_color_id,
            MixingSession.target_r, MixingSession.target_g, MixingSession.target_b,
        )
        .filter(MixingSession.user_id.in_(ids), MixingSession.timestamp >= start)
        .order_by(MixingSession.timestamp.asc())
        .all()
    )


def _target_names(ids):
    if not ids:
        return {}
    rows = TargetColor.query.filter(TargetColor.id.in_(list(ids))).all()
    return {tc.id: (tc.name, getattr(tc, 'name_hu', None)) for tc in rows}


# ── Timelines ────────────────────────────────────────────────────────────────

def _day_series(rows, start, end, tz):
    """Rounds and distinct players per DAY_BUCKET_MIN bucket over the cohort day."""
    bucket = timedelta(minutes=DAY_BUCKET_MIN)
    n_buckets = max(1, int((end - start) / bucket))
    rounds = [0] * n_buckets
    players = [set() for _ in range(n_buckets)]
    for row in rows:
        uid, ts = row[0], row[1]
        if ts is None or ts < start or ts >= end:
            continue
        i = min(n_buckets - 1, int((ts - start) / bucket))
        rounds[i] += 1
        players[i].add(uid)
    points = []
    for i in range(n_buckets):
        t0 = start + i * bucket
        points.append({
            't': _iso(t0),
            'label': utc_to_local(t0, tz).strftime('%H:%M'),
            'rounds': rounds[i],
            'players': len(players[i]),
        })
    return {'bucket_minutes': DAY_BUCKET_MIN, 'points': points}


def _daily_series(rows, day, today_local, tz):
    """Rounds, completed rounds and distinct players per local calendar day from
    the cohort day through today (zero-filled), for the retention view."""
    per_day = {}
    for row in rows:
        uid, ts, category = row[0], row[1], row[2]
        if ts is None:
            continue
        d = utc_to_local(ts, tz).date()
        entry = per_day.setdefault(d, {'rounds': 0, 'completed': 0, 'players': set()})
        entry['rounds'] += 1
        if category in COMPLETED_MATCH_CATEGORIES:
            entry['completed'] += 1
        entry['players'].add(uid)
    last = max([today_local, day] + list(per_day.keys()))
    points = []
    d = day
    while d <= last:
        entry = per_day.get(d)
        points.append({
            'day': d.isoformat(),
            'rounds': entry['rounds'] if entry else 0,
            'completed': entry['completed'] if entry else 0,
            'players': len(entry['players']) if entry else 0,
        })
        d += timedelta(days=1)
    return {'points': points}


# ── The live layer ───────────────────────────────────────────────────────────

def _round_dict(row, now, target_names):
    """A saved round as the feed / card shows it."""
    name, name_hu = target_names.get(row.target_color_id, (None, None))
    return {
        't': _iso(row.timestamp),
        'sec_ago': _seconds_since(now, row.timestamp),
        'category': row.match_category,
        'delta_e': _round(row.delta_e),
        'time_sec': _round(row.time_sec, 1),
        'skipped': bool(row.skipped),
        'target': name,
        'target_hu': name_hu,
        'target_rgb': ([row.target_r, row.target_g, row.target_b]
                       if row.target_r is not None else None),
    }


def _live_windows(recent_rounds, seen, ids, now):
    """Rolling 'last N minutes' tallies: rounds finished, who finished them,
    and who was active at all (any sighting) in the window."""
    out = []
    for minutes in LIVE_WINDOWS_MIN:
        since = now - timedelta(minutes=minutes)
        rows = [r for r in recent_rounds if r.timestamp is not None and r.timestamp >= since]
        des = [float(r.delta_e) for r in rows if r.delta_e is not None]
        active = 0
        for uid in ids:
            stamp, _source = seen.get(uid)
            if stamp is not None and stamp >= since:
                active += 1
        out.append({
            'minutes': minutes,
            'rounds': len(rows),
            'completed': sum(1 for r in rows if r.match_category in COMPLETED_MATCH_CATEGORIES),
            'perfect': sum(1 for r in rows if r.match_category == 'perfect'),
            'players': len({r.user_id for r in rows}),
            'active_players': active,
            'mean_delta_e': round(sum(des) / len(des), 2) if des else None,
        })
    return out


def _minute_series(recent_rounds, now, tz):
    """Rounds finished per minute over the last LIVE_MINUTES minutes, ending
    with the current (still running) minute."""
    end_minute = now.replace(second=0, microsecond=0)
    first = end_minute - timedelta(minutes=LIVE_MINUTES - 1)
    rounds = [0] * LIVE_MINUTES
    perfect = [0] * LIVE_MINUTES
    players = [set() for _ in range(LIVE_MINUTES)]
    for r in recent_rounds:
        if r.timestamp is None or r.timestamp < first:
            continue
        i = int((r.timestamp - first).total_seconds() // 60)
        if i < 0 or i >= LIVE_MINUTES:
            continue
        rounds[i] += 1
        if r.match_category == 'perfect':
            perfect[i] += 1
        players[i].add(r.user_id)
    points = []
    for i in range(LIVE_MINUTES):
        t0 = first + timedelta(minutes=i)
        points.append({
            't': _iso(t0),
            'label': utc_to_local(t0, tz).strftime('%H:%M'),
            'rounds': rounds[i],
            'perfect': perfect[i],
            'players': len(players[i]),
        })
    return {'points': points}


def _hour_series(rows, now, tz, hours):
    """Rounds finished per hour over the last ``hours`` hours, ending with the
    current (still running) hour — the /live history chart."""
    end_hour = now.replace(minute=0, second=0, microsecond=0)
    first = end_hour - timedelta(hours=hours - 1)
    rounds = [0] * hours
    perfect = [0] * hours
    players = [set() for _ in range(hours)]
    for r in rows:
        if r.timestamp is None or r.timestamp < first:
            continue
        i = int((r.timestamp - first).total_seconds() // 3600)
        if i < 0 or i >= hours:
            continue
        rounds[i] += 1
        if r.match_category == 'perfect':
            perfect[i] += 1
        players[i].add(r.user_id)
    points = []
    for i in range(hours):
        t0 = first + timedelta(hours=i)
        points.append({
            't': _iso(t0),
            'label': utc_to_local(t0, tz).strftime('%H:%M'),
            'rounds': rounds[i],
            'perfect': perfect[i],
            'players': len(players[i]),
        })
    return {'hours': hours, 'points': points}


def _live_feed(users, recent_rounds, app_opens, calib_sessions, completed_matches,
               target_names, now, since):
    """Newest-first log of what the cohort did in the last LIVE_LOOKBACK_SEC:
    rounds finished (with the result), app opens, calibration blocks started
    and finished, matches completed, and registrations (live on the day)."""
    names = {u.id: u.nickname for u in users}
    items = []

    def add(t, uid, kind, **detail):
        if t is None or t < since or t > now:
            return
        item = {'t': _iso(t), 'sec_ago': _seconds_since(now, t), 'user_id': uid,
                'nickname': names.get(uid), 'kind': kind}
        item.update(detail)
        items.append((t, item))

    for r in recent_rounds:
        add(r.timestamp, r.user_id, 'round', round=_round_dict(r, now, target_names))
    for uid, t in app_opens:
        add(t, uid, 'app_opened')
    for s in calib_sessions:
        add(s.started_at, s.user_id, 'calibration_started')
        if s.ended_at is not None:
            add(s.ended_at, s.user_id, 'calibration_finished',
                pt=_round(s.perceptibility_de), at=_round(s.acceptability_de))
    for uid, t in completed_matches:
        add(t, uid, 'match_completed')
    for u in users:
        add(u.created_at, u.id, 'registered')

    items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _t, item in items[:LIVE_FEED_MAX]]


# ── Payload ──────────────────────────────────────────────────────────────────

def _assemble(users, now, tz, rows_since):
    """The shared body of every dashboard payload: one row per player with
    their live status, the summary tallies and the live layer. Returns
    ``(payload, timeline_rows)``; the caller adds its scope and history
    charts. ``rows_since`` bounds the individual rounds fetched — the cohort
    day for a cohort page, the lookback window for /live."""
    today_local = utc_to_local(now, tz).date()
    ids = [u.id for u in users]
    live_since = now - timedelta(seconds=LIVE_LOOKBACK_SEC)
    start = rows_since

    if ids:
        progress = _progress_by_user(ids)
        sessions = _sessions_by_user(ids)
        matches = _matches_by_user(ids)
        awards = _awards_by_user(ids)
        attempts = _attempts_by_user(ids)
        open_attempts = _open_attempts(ids, now)
        step_stamps = _recent_step_stamps(ids, now)
        trajectories = _trajectories([a.attempt_uuid for a in open_attempts.values()])
        analytics = _analytics_stamps(ids, start)
        (calib, calib_last_trial, calib_thresholds,
         calib_open, calib_open_progress) = _calibration_by_user(ids, now)
        timeline_rows = _timeline_rows(ids, start)
        recent_rounds = [r for r in timeline_rows
                         if r.timestamp is not None and r.timestamp >= live_since]
        app_opens = _recent_app_opens(ids, live_since)
        calib_recent = _recent_calibration_sessions(ids, live_since)
        matches_recent = _recent_completed_matches(ids, live_since)
        target_ids = {a.target_color_id for a in open_attempts.values()
                      if a.target_color_id is not None}
        target_ids |= {r.target_color_id for r in recent_rounds if r.target_color_id is not None}
        target_names = _target_names(target_ids)
    else:
        progress = sessions = matches = awards = attempts = {}
        open_attempts = step_stamps = trajectories = analytics = {}
        calib = calib_last_trial = calib_thresholds = calib_open = calib_open_progress = {}
        timeline_rows = recent_rounds = app_opens = calib_recent = matches_recent = []
        target_names = {}

    recent_by_user = {}
    for r in recent_rounds:
        recent_by_user.setdefault(r.user_id, []).append(r)
    since_15 = now - timedelta(minutes=15)
    since_60 = now - timedelta(minutes=60)

    seen = _LastSeen()
    for uid, stamp in step_stamps.items():
        seen.bump(uid, stamp, 'step')
    for uid, row in attempts.items():
        seen.bump(uid, row.last_started, 'round')
        seen.bump(uid, row.last_ended, 'round')
    for uid, row in sessions.items():
        seen.bump(uid, row.last_round, 'round')
    for uid, stamp in analytics.items():
        seen.bump(uid, stamp, 'app')
    for uid, stamp in calib_last_trial.items():
        seen.bump(uid, stamp, 'calibration')
    for uid, row in calib.items():
        seen.bump(uid, row.last_started, 'calibration')
        seen.bump(uid, row.last_ended, 'calibration')
    for uid, up in progress.items():
        seen.bump(uid, up.updated_at, 'round')

    # "Came back": a round on a later local day than the player's registration
    # day (for a cohort page that is any round after the cohort day).
    reg_day = {u.id: utc_to_local(u.created_at, tz).date() for u in users if u.created_at}
    came_back_ids = {
        r.user_id for r in timeline_rows
        if r.timestamp is not None and r.user_id in reg_day
        and utc_to_local(r.timestamp, tz).date() > reg_day[r.user_id]
    }

    rows = []
    genders = {}
    ages = []
    locales = {}
    for u in users:
        s = sessions.get(u.id)
        up = progress.get(u.id)
        m = matches.get(u.id, {})
        c = calib.get(u.id)
        last_seen, source = seen.get(u.id)
        ago = _seconds_since(now, last_seen)
        online = ago is not None and ago <= ONLINE_WINDOW_SEC
        recent = ago is not None and ago <= RECENT_WINDOW_SEC
        rounds = int(s.rounds or 0) if s else 0

        xp = int(up.xp or 0) if up else 0
        level = max(int(up.level or 1) if up else 1, _xp_level(xp))

        activity = {'kind': 'never' if (rounds == 0 and last_seen is None) else 'idle'}
        open_attempt = open_attempts.get(u.id)
        open_calib = calib_open.get(u.id)
        if online and open_attempt is not None:
            last_step = step_stamps.get(u.id) or open_attempt.attempt_started_server_ts
            step_ago = _seconds_since(now, last_step)
            if step_ago is not None and step_ago <= ONLINE_WINDOW_SEC:
                name, name_hu = target_names.get(open_attempt.target_color_id, (None, None))
                trajectory = trajectories.get(open_attempt.attempt_uuid, [])
                initial_de = _round(open_attempt.initial_delta_e)
                # Current ΔE: the last flushed step; else the header's running
                # value (the client re-posts it with every mid-round flush);
                # else the untouched starting distance.
                header_de = _round(open_attempt.final_delta_e)
                current_de = trajectory[-1] if trajectory else (
                    header_de if header_de is not None else initial_de)
                activity = {
                    'kind': 'mixing',
                    'target': name,
                    'target_hu': name_hu,
                    'target_rgb': (
                        [open_attempt.target_r, open_attempt.target_g, open_attempt.target_b]
                        if open_attempt.target_r is not None else None
                    ),
                    'delta_e': current_de,
                    'initial_delta_e': initial_de,
                    'trajectory': trajectory,
                    'steps': int(open_attempt.num_steps or 0),
                    'since_sec': _seconds_since(now, open_attempt.attempt_started_server_ts),
                }
        if activity['kind'] != 'mixing' and online and open_calib is not None:
            prog = calib_open_progress.get(open_calib.session_uuid)
            last_trial = (prog.last if prog is not None and prog.last is not None
                          else open_calib.started_at)
            trial_ago = _seconds_since(now, last_trial)
            if trial_ago is not None and trial_ago <= ONLINE_WINDOW_SEC:
                activity = {
                    'kind': 'calibrating',
                    'answered': int(prog.answered or 0) if prog is not None else 0,
                    'total': int(prog.total or 0) if prog is not None else (open_calib.n_trials or 0),
                }
        if activity['kind'] == 'idle' and online:
            activity = {'kind': 'online'}

        threshold = calib_thresholds.get(u.id)
        mine = recent_by_user.get(u.id, [])
        rows.append({
            'user_id': u.id,
            'nickname': u.nickname,
            'registered_at': _iso(u.created_at),
            'registered_day': reg_day[u.id].isoformat() if u.id in reg_day else None,
            'new_today': reg_day.get(u.id) == today_local,
            'locale': u.locale,
            'email_verified': u.email_verified_at is not None,
            'xp': xp,
            'level': level,
            'current_streak': int(up.current_streak or 0) if up else 0,
            'longest_streak': int(up.longest_streak or 0) if up else 0,
            'awards': awards.get(u.id, 0),
            'rounds': rounds,
            'completed': int(s.completed or 0) if s else 0,
            'perfect': int(s.perfect or 0) if s else 0,
            'skipped': int(s.skipped or 0) if s else 0,
            'best_delta_e': _round(s.best_de) if s else None,
            'mean_delta_e': _round(s.mean_de) if s else None,
            'play_time_sec': _round(s.play_time, 1) if s else 0.0,
            'attempts': int(attempts[u.id].n or 0) if u.id in attempts else 0,
            'matches_completed': int(m.get('completed', 0)),
            'matches_abandoned': int(m.get('abandoned', 0)),
            'active_match_round': m.get('active_round'),
            'active_match_rounds': m.get('round_count'),
            'calibration_completed': int(c.completed or 0) if c else 0,
            'calibration_pt': _round(threshold.perceptibility_de) if threshold else None,
            'calibration_at': _round(threshold.acceptability_de) if threshold else None,
            'came_back': u.id in came_back_ids,
            'last_round': _round_dict(mine[-1], now, target_names) if mine else None,
            'rounds_last_15m': sum(1 for r in mine if r.timestamp >= since_15),
            'rounds_last_60m': sum(1 for r in mine if r.timestamp >= since_60),
            'last_seen': _iso(last_seen),
            'last_seen_sec_ago': ago,
            'last_seen_source': source,
            'online': online,
            'recent': recent,
            'activity': activity,
        })

        genders[u.gender or 'unknown'] = genders.get(u.gender or 'unknown', 0) + 1
        age = _age_on(u.birthdate, today_local)
        if age is not None:
            ages.append(age)
        loc = u.locale or 'unset'
        locales[loc] = locales.get(loc, 0) + 1

    # Online players first, then the most recently seen, then registration order.
    rows.sort(key=lambda r: (
        0 if r['online'] else 1,
        r['last_seen_sec_ago'] if r['last_seen_sec_ago'] is not None else float('inf'),
        r['registered_at'] or '',
    ))

    played = [r for r in rows if r['rounds'] > 0]
    summary = {
        'registered': len(rows),
        'email_verified': sum(1 for r in rows if r['email_verified']),
        'played': len(played),
        'online_now': sum(1 for r in rows if r['online']),
        'active_last_hour': sum(1 for r in rows if r['recent']),
        'online_mixing': sum(1 for r in rows if r['activity']['kind'] == 'mixing'),
        'online_calibrating': sum(1 for r in rows if r['activity']['kind'] == 'calibrating'),
        'online_in_app': sum(1 for r in rows if r['activity']['kind'] == 'online'),
        'came_back': sum(1 for r in rows if r['came_back']),
        'new_today': sum(1 for r in rows if r['new_today']),
        'returning': sum(1 for r in rows if not r['new_today']),
        'rounds': sum(r['rounds'] for r in rows),
        'completed': sum(r['completed'] for r in rows),
        'perfect': sum(r['perfect'] for r in rows),
        'skipped': sum(r['skipped'] for r in rows),
        'play_time_sec': round(sum(r['play_time_sec'] or 0.0 for r in rows), 1),
        'median_rounds_per_player': (statistics.median(r['rounds'] for r in played)
                                     if played else None),
        'matches_completed': sum(r['matches_completed'] for r in rows),
        'matches_active': sum(1 for r in rows if r['active_match_round'] is not None),
        'calibration_sessions': sum(r['calibration_completed'] for r in rows),
        'calibration_players': sum(1 for r in rows if r['calibration_completed'] > 0),
        'gender': genders,
        'age': {
            'n': len(ages),
            'median': statistics.median(ages) if ages else None,
            'min': min(ages) if ages else None,
            'max': max(ages) if ages else None,
        },
        'locale': locales,
    }

    payload = {
        'status': 'success',
        'generated_at': _iso(now),
        'summary': summary,
        'live': {
            'lookback_sec': LIVE_LOOKBACK_SEC,
            'last_activity': _iso(seen.latest()),
            'last_activity_sec_ago': _seconds_since(now, seen.latest()),
            'windows': _live_windows(recent_rounds, seen, ids, now),
            'minutes': _minute_series(recent_rounds, now, tz),
            'feed': _live_feed(users, recent_rounds, app_opens, calib_recent, matches_recent,
                               target_names, now, live_since),
        },
        'users': rows,
    }
    return payload, timeline_rows


def build_live_payload(day: date, now: datetime | None = None) -> dict:
    """A cohort page (/hetfo, /szerda): everyone registered on ``day``, with
    the registration-day and day-by-day history charts."""
    now = now or datetime.utcnow()
    tz = cohort_tz()
    start, end = cohort_window_utc(day, tz)
    users = _cohort_users(start, end)
    payload, timeline_rows = _assemble(users, now, tz, rows_since=start)
    payload['scope'] = {'kind': 'cohort', 'date': day.isoformat()}
    payload['cohort'] = {
        'date': day.isoformat(),
        'tz': COHORT_TZ_NAME,
        'window_utc': {'start': _iso(start), 'end': _iso(end)},
        'online_window_sec': ONLINE_WINDOW_SEC,
        'recent_window_sec': RECENT_WINDOW_SEC,
    }
    payload['timeline'] = {
        'day': _day_series(timeline_rows, start, end, tz),
        'days': _daily_series(timeline_rows, day, utc_to_local(now, tz).date(), tz),
    }
    return payload


def build_global_payload(hours=LIVE_DEFAULT_HOURS, now: datetime | None = None) -> dict:
    """The /live page: everyone the server heard from in the last ``hours``,
    whatever day they registered, with a rounds-per-hour history chart."""
    now = now or datetime.utcnow()
    tz = cohort_tz()
    hours = resolve_hours(hours)
    since = now - timedelta(hours=hours)
    users = _active_users(since)
    payload, timeline_rows = _assemble(users, now, tz, rows_since=since)
    payload['scope'] = {'kind': 'live', 'hours': hours, 'since': _iso(since)}
    payload['cohort'] = {
        'tz': COHORT_TZ_NAME,
        'online_window_sec': ONLINE_WINDOW_SEC,
        'recent_window_sec': RECENT_WINDOW_SEC,
    }
    payload['timeline'] = {'hours': _hour_series(timeline_rows, now, tz, hours)}
    return payload


# ── Short-lived cache (several admins polling at once share one query set) ──

_CACHE_TTL_SEC = float(os.environ.get('HETFO_CACHE_SECONDS', '3') or 3)
_cache_lock = threading.Lock()
_cache = {}  # scope key -> (monotonic ts, payload)


def _cached(key, build):
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) <= _CACHE_TTL_SEC:
            return entry[1]
    payload = build()
    with _cache_lock:
        _cache[key] = (time.monotonic(), payload)
        if len(_cache) > 32:
            for stale in [k for k, v in list(_cache.items()) if (now - v[0]) > _CACHE_TTL_SEC]:
                _cache.pop(stale, None)
    return payload


def live_payload_cached(day: date) -> dict:
    return _cached('cohort:' + day.isoformat(), lambda: build_live_payload(day))


def global_payload_cached(hours) -> dict:
    hours = resolve_hours(hours)
    return _cached('live:%d' % hours, lambda: build_global_payload(hours))
