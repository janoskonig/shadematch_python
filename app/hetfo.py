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
REFRESH_SECONDS_DEFAULT = 15
REFRESH_SECONDS_MIN = 5
REFRESH_SECONDS_MAX = 120


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
    """Everything the template needs to boot the poller."""
    page = normalize_page(page)
    title_key, prev_key, next_key = WEEKDAY_LABEL_KEYS[day.weekday()]
    return {
        'page': page,
        'date': day.isoformat(),
        'prev': (day - timedelta(days=7)).isoformat(),
        'next': (day + timedelta(days=7)).isoformat(),
        'is_default': day == default_cohort_date(page),
        'title_key': title_key,
        'prev_key': prev_key,
        'next_key': next_key,
        'tz': COHORT_TZ_NAME,
        'refresh_seconds': resolve_refresh_seconds(refresh_raw),
        'online_window_min': ONLINE_WINDOW_SEC // 60,
        'date_error': bool(date_error),
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


# ── Queries (all restricted to the cohort's ids) ─────────────────────────────

def _cohort_users(start, end):
    return (
        User.query
        .filter(User.created_at.isnot(None), User.created_at >= start, User.created_at < end)
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


def _last_event_per_attempt(attempt_uuids):
    """Latest step (highest seq) carrying a post-action ΔE, per open attempt."""
    if not attempt_uuids:
        return {}
    rows = (
        MixingAttemptEvent.query
        .filter(
            MixingAttemptEvent.attempt_uuid.in_(attempt_uuids),
            MixingAttemptEvent.delta_e_after.isnot(None),
        )
        .order_by(MixingAttemptEvent.attempt_uuid.asc(), MixingAttemptEvent.seq.desc())
        .all()
    )
    latest = {}
    for r in rows:
        latest.setdefault(r.attempt_uuid, r)
    return latest


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
    return (
        db.session.query(MixingSession.user_id, MixingSession.timestamp, MixingSession.match_category)
        .filter(MixingSession.user_id.in_(ids), MixingSession.timestamp >= start)
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
    for uid, ts, _category in rows:
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
    for uid, ts, category in rows:
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


# ── Payload ──────────────────────────────────────────────────────────────────

def build_live_payload(day: date, now: datetime | None = None) -> dict:
    """The whole dashboard in one JSON document: cohort summary, one row per
    player with their live status, and the two activity timelines."""
    now = now or datetime.utcnow()
    tz = cohort_tz()
    start, end = cohort_window_utc(day, tz)
    today_local = utc_to_local(now, tz).date()

    users = _cohort_users(start, end)
    ids = [u.id for u in users]

    if ids:
        progress = _progress_by_user(ids)
        sessions = _sessions_by_user(ids)
        matches = _matches_by_user(ids)
        awards = _awards_by_user(ids)
        attempts = _attempts_by_user(ids)
        open_attempts = _open_attempts(ids, now)
        step_stamps = _recent_step_stamps(ids, now)
        last_events = _last_event_per_attempt([a.attempt_uuid for a in open_attempts.values()])
        analytics = _analytics_stamps(ids, start)
        (calib, calib_last_trial, calib_thresholds,
         calib_open, calib_open_progress) = _calibration_by_user(ids, now)
        timeline_rows = _timeline_rows(ids, start)
        target_names = _target_names({a.target_color_id for a in open_attempts.values()
                                      if a.target_color_id is not None})
    else:
        progress = sessions = matches = awards = attempts = {}
        open_attempts = step_stamps = last_events = analytics = {}
        calib = calib_last_trial = calib_thresholds = calib_open = calib_open_progress = {}
        timeline_rows = []
        target_names = {}

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

    came_back_ids = {uid for uid, ts, _c in timeline_rows if ts is not None and ts >= end}

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
                ev = last_events.get(open_attempt.attempt_uuid)
                name, name_hu = target_names.get(open_attempt.target_color_id, (None, None))
                activity = {
                    'kind': 'mixing',
                    'target': name,
                    'target_hu': name_hu,
                    'target_rgb': (
                        [open_attempt.target_r, open_attempt.target_g, open_attempt.target_b]
                        if open_attempt.target_r is not None else None
                    ),
                    'delta_e': _round(ev.delta_e_after if ev is not None else open_attempt.initial_delta_e),
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
        rows.append({
            'user_id': u.id,
            'nickname': u.nickname,
            'registered_at': _iso(u.created_at),
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
        'came_back': sum(1 for r in rows if r['came_back']),
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

    return {
        'status': 'success',
        'generated_at': _iso(now),
        'cohort': {
            'date': day.isoformat(),
            'tz': COHORT_TZ_NAME,
            'window_utc': {'start': _iso(start), 'end': _iso(end)},
            'online_window_sec': ONLINE_WINDOW_SEC,
            'recent_window_sec': RECENT_WINDOW_SEC,
        },
        'summary': summary,
        'users': rows,
        'timeline': {
            'day': _day_series(timeline_rows, start, end, tz),
            'days': _daily_series(timeline_rows, day, today_local, tz),
        },
    }


# ── Short-lived cache (several admins polling at once share one query set) ──

_CACHE_TTL_SEC = float(os.environ.get('HETFO_CACHE_SECONDS', '5') or 5)
_cache_lock = threading.Lock()
_cache = {}  # date iso -> (monotonic ts, payload)


def live_payload_cached(day: date) -> dict:
    key = day.isoformat()
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and (now - entry[0]) <= _CACHE_TTL_SEC:
            return entry[1]
    payload = build_live_payload(day)
    with _cache_lock:
        _cache[key] = (time.monotonic(), payload)
        if len(_cache) > 32:
            for stale in [k for k, v in list(_cache.items()) if (now - v[0]) > _CACHE_TTL_SEC]:
                _cache.pop(stale, None)
    return payload
