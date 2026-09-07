"""Checks for the /hetfo cohort dashboard (``app.hetfo``).

Runs against a throwaway SQLite file, so no PostgreSQL is needed: the module
deliberately sticks to portable SQL. Covers the Budapest-day -> UTC window
(including DST), cohort-day resolution, cohort membership at the window edges,
the per-player figures and live status, the two timelines, and the HTTP routes.

Run with:  pytest tests/test_hetfo.py
"""
import importlib
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import hetfo  # noqa: E402

COHORT_DAY = date(2026, 8, 31)                 # the Monday session (CEST, UTC+2)
WINDOW_START = datetime(2026, 8, 30, 22, 0)    # local midnight in UTC
WINDOW_END = datetime(2026, 8, 31, 22, 0)
NOW = datetime(2026, 9, 7, 10, 0, 0)           # the following Monday, 12:00 local


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
def test_summer_day_maps_to_a_utc_plus_two_window():
    assert hetfo.cohort_window_utc(COHORT_DAY) == (WINDOW_START, WINDOW_END)


def test_winter_day_maps_to_a_utc_plus_one_window():
    assert hetfo.cohort_window_utc(date(2026, 1, 5)) == (
        datetime(2026, 1, 4, 23, 0), datetime(2026, 1, 5, 23, 0))


def test_clock_change_day_is_twenty_five_hours_long():
    # Clocks go back on 2026-10-25, so that local day spans 25 hours.
    start, end = hetfo.cohort_window_utc(date(2026, 10, 25))
    assert end - start == timedelta(hours=25)
    series = hetfo._day_series([], start, end, hetfo.cohort_tz())
    assert len(series['points']) == 100
    assert series['points'][0]['label'] == '00:00'


def test_parse_cohort_date():
    assert hetfo.parse_cohort_date('2026-08-31') == COHORT_DAY
    assert hetfo.parse_cohort_date(' 2026-08-31 ') == COHORT_DAY
    assert hetfo.parse_cohort_date('') is None
    assert hetfo.parse_cohort_date(None) is None
    with pytest.raises(ValueError):
        hetfo.parse_cohort_date('last monday')
    with pytest.raises(ValueError):
        hetfo.parse_cohort_date('2026-13-01')


def test_default_cohort_date_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv('HETFO_COHORT_DATE', raising=False)
    assert hetfo.default_cohort_date() == hetfo.DEFAULT_COHORT_DATE == COHORT_DAY
    monkeypatch.setenv('HETFO_COHORT_DATE', '2026-09-07')
    assert hetfo.default_cohort_date() == date(2026, 9, 7)
    assert hetfo.resolve_cohort_date(None) == date(2026, 9, 7)
    assert hetfo.resolve_cohort_date('2026-08-24') == date(2026, 8, 24)
    monkeypatch.setenv('HETFO_COHORT_DATE', 'garbage')
    assert hetfo.default_cohort_date() == COHORT_DAY


def test_refresh_seconds_are_clamped():
    assert hetfo.resolve_refresh_seconds(None) == hetfo.REFRESH_SECONDS_DEFAULT
    assert hetfo.resolve_refresh_seconds('x') == hetfo.REFRESH_SECONDS_DEFAULT
    assert hetfo.resolve_refresh_seconds('3') == hetfo.REFRESH_SECONDS_MIN
    assert hetfo.resolve_refresh_seconds('999') == hetfo.REFRESH_SECONDS_MAX
    assert hetfo.resolve_refresh_seconds('30') == 30


def test_page_context_steps_a_week(monkeypatch):
    monkeypatch.delenv('HETFO_COHORT_DATE', raising=False)
    ctx = hetfo.page_context(COHORT_DAY, '20')
    assert ctx['page'] == 'hetfo'
    assert ctx['prev'] == '2026-08-24'
    assert ctx['next'] == '2026-09-07'
    assert ctx['is_default'] is True
    assert ctx['refresh_seconds'] == 20
    assert ctx['tz'] == 'Europe/Budapest'
    assert ctx['title_key'] == 'Monday cohort — live'
    assert hetfo.page_context(date(2026, 9, 7))['is_default'] is False


def test_each_page_has_its_own_session_day(monkeypatch):
    monkeypatch.delenv('HETFO_COHORT_DATE', raising=False)
    monkeypatch.delenv('SZERDA_COHORT_DATE', raising=False)
    assert hetfo.default_cohort_date('hetfo') == date(2026, 8, 31)
    assert hetfo.default_cohort_date('szerda') == date(2026, 9, 2)
    assert hetfo.normalize_page(' SZERDA ') == 'szerda'
    assert hetfo.normalize_page('csutortok') == 'hetfo'
    assert hetfo.normalize_page(None) == 'hetfo'
    assert hetfo.default_cohort_date('csutortok') == date(2026, 8, 31)
    monkeypatch.setenv('SZERDA_COHORT_DATE', '2026-09-09')
    assert hetfo.default_cohort_date('szerda') == date(2026, 9, 9)
    assert hetfo.default_cohort_date('hetfo') == date(2026, 8, 31)
    assert hetfo.resolve_cohort_date(None, page='szerda') == date(2026, 9, 9)
    assert hetfo.resolve_cohort_date('2026-09-02', page='szerda') == date(2026, 9, 2)


def test_labels_follow_the_weekday_on_screen():
    ctx = hetfo.page_context(date(2026, 9, 2), page='szerda')
    assert ctx['page'] == 'szerda'
    assert ctx['prev'] == '2026-08-26' and ctx['next'] == '2026-09-09'
    assert (ctx['title_key'], ctx['prev_key'], ctx['next_key']) == (
        'Wednesday cohort — live', 'Previous Wednesday', 'Next Wednesday')
    # A Monday viewed on the Wednesday page is still labelled as a Monday.
    assert hetfo.page_context(date(2026, 8, 31), page='szerda')['title_key'] == 'Monday cohort — live'
    assert hetfo.page_context(date(2026, 9, 6))['title_key'] == 'Sunday cohort — live'
    assert hetfo.page_context(date(2026, 9, 6))['prev_key'] == 'Previous Sunday'


# --------------------------------------------------------------------------- #
# database-backed checks (SQLite)
# --------------------------------------------------------------------------- #
def _seed(db):
    from app.models import (
        AnalyticsEvent, CalibrationSession, CalibrationTrial, Match, MixingAttempt,
        MixingAttemptEvent, MixingSession, TargetColor, User, UserProgress,
    )

    def user(uid, created_at, gender, birthdate, **kw):
        db.session.add(User(id=uid, created_at=created_at, gender=gender,
                            birthdate=birthdate, **kw))

    # In the cohort:
    user('AAAAA1', datetime(2026, 8, 31, 8, 0), 'female', date(2004, 5, 5),
         nickname='Zsófi', email='a@example.com', email_verified_at=datetime(2026, 8, 31, 8, 5),
         locale='hu')
    user('CCCCC3', WINDOW_START, 'male', date(2000, 9, 8))            # first instant of the day
    user('EEEEE5', datetime(2026, 8, 31, 12, 0), 'male', date(1990, 1, 1),
         email='e@example.com', locale='en')
    user('FFFFF6', datetime(2026, 8, 31, 15, 0), 'female', date(2008, 8, 31), locale='hu')
    # Outside the cohort:
    user('BBBBB2', WINDOW_START - timedelta(seconds=1), 'male', date(1995, 1, 1))
    user('DDDDD4', WINDOW_END, 'female', date(1995, 1, 1))              # end is exclusive
    # The Wednesday session (/szerda), two days later:
    user('GGGGG7', datetime(2026, 9, 2, 10, 0), 'female', date(2001, 2, 3),
         nickname='Szerda', locale='hu')

    db.session.add(TargetColor(id=1, name='Merlot', name_hu='bordó', color_type='gamut',
                               r=120, g=30, b=50, catalog_order=1))

    def session(uid, ts, delta_e, time_sec, skipped=False, perception=None, category=None):
        db.session.add(MixingSession(
            user_id=uid, target_color_id=1, target_r=120, target_g=30, target_b=50,
            drop_white=1, drop_black=0, drop_red=2, drop_yellow=0, drop_blue=0,
            delta_e=delta_e, time_sec=time_sec, timestamp=ts, skipped=skipped,
            skip_perception=perception, match_category=category,
        ))

    # AAAAA1: three rounds on the day (11:10, 11:20, 11:40 local) + one two days later.
    session('AAAAA1', datetime(2026, 8, 31, 9, 10), 0.0, 120.0, category='perfect')
    session('AAAAA1', datetime(2026, 8, 31, 9, 20), 1.5, 200.0, True, 'acceptable',
            'acceptable_difference')
    session('AAAAA1', datetime(2026, 8, 31, 9, 40), 6.0, 3000.0, True, 'unacceptable',
            'big_difference')
    session('AAAAA1', datetime(2026, 9, 2, 10, 0), 0.9, 60.0, True, 'identical',
            'no_perceivable_difference')
    # ... and two today: a skip 40 minutes ago, a perfect round 3 minutes ago.
    session('AAAAA1', NOW - timedelta(minutes=40), 1.2, 80.0, True, 'acceptable',
            'acceptable_difference')
    session('AAAAA1', NOW - timedelta(minutes=3), 0.0, 45.0, category='perfect')
    # GGGGG7 (Wednesday cohort): one perfect round half an hour after signing up.
    session('GGGGG7', datetime(2026, 9, 2, 10, 30), 0.0, 90.0, category='perfect')
    db.session.add(UserProgress(user_id='AAAAA1', xp=500, level=2, current_streak=2,
                                longest_streak=2, updated_at=datetime(2026, 9, 2, 10, 0)))
    db.session.add(Match(user_id='AAAAA1', status='completed', current_round=10,
                         started_at=datetime(2026, 8, 31, 9, 0),
                         completed_at=datetime(2026, 8, 31, 11, 0)))
    db.session.add(Match(user_id='AAAAA1', status='active', current_round=4,
                         started_at=NOW - timedelta(minutes=20)))
    # ... and is mixing right now: an open attempt with a step 20 s ago.
    db.session.add(MixingAttempt(
        attempt_uuid='att-open-a', user_id='AAAAA1', target_color_id=1,
        target_r=120, target_g=30, target_b=50, initial_delta_e=40.0,
        attempt_started_server_ts=NOW - timedelta(seconds=60), num_steps=7,
    ))
    db.session.add(MixingAttemptEvent(
        attempt_uuid='att-open-a', seq=7, event_type='action', client_ts_ms=0,
        server_ts=NOW - timedelta(seconds=20), state_before_json={}, state_after_json={},
        delta_e_after=3.2,
    ))
    # A closed attempt with a later boundary event must not count as "mixing".
    db.session.add(MixingAttempt(
        attempt_uuid='att-done-a', user_id='AAAAA1', target_color_id=1,
        attempt_started_server_ts=NOW - timedelta(minutes=10),
        attempt_ended_server_ts=NOW - timedelta(minutes=5), end_reason='saved_match',
    ))

    # EEEEE5: opened the app half an hour ago, never played, calibrated three days ago.
    db.session.add(AnalyticsEvent(user_id='EEEEE5', event='app_opened',
                                  ts=NOW - timedelta(minutes=30),
                                  received_at=NOW - timedelta(minutes=30), metadata_json={}))
    db.session.add(CalibrationSession(
        session_uuid='cal-e-done', user_id='EEEEE5', n_trials=20,
        started_at=NOW - timedelta(days=3), ended_at=NOW - timedelta(days=3, minutes=-2),
        perceptibility_de=0.9, acceptability_de=1.8,
    ))

    # FFFFF6: in the middle of a calibration block right now (5 of 20 answered).
    db.session.add(CalibrationSession(
        session_uuid='cal-f-open', user_id='FFFFF6', n_trials=20,
        started_at=NOW - timedelta(seconds=100),
    ))
    for seq in range(20):
        answered = seq < 5
        db.session.add(CalibrationTrial(
            session_uuid='cal-f-open', seq=seq, is_catch=False,
            judgment='acceptable' if answered else None,
            responded_at=(NOW - timedelta(seconds=30 + (4 - seq) * 10)) if answered else None,
        ))
    db.session.commit()


@pytest.fixture(scope='module')
def app(tmp_path_factory):
    db_path = tmp_path_factory.mktemp('hetfo') / 'hetfo.db'
    os.environ['DATABASE_URL'] = f'sqlite:///{db_path}'
    os.environ.pop('HETFO_COHORT_DATE', None)
    import config
    importlib.reload(config)          # Config reads DATABASE_URL at import time
    from app import create_app, db
    application = create_app()
    assert db.get_engine(application).dialect.name == 'sqlite'
    with application.app_context():
        db.drop_all()
        db.create_all()
        _seed(db)
    yield application


@pytest.fixture()
def payload(app):
    from app import db
    with app.app_context():
        data = hetfo.build_live_payload(COHORT_DAY, now=NOW)
        db.session.remove()
    return data


def _row(payload, uid):
    return next(r for r in payload['users'] if r['user_id'] == uid)


def test_cohort_membership_uses_the_local_day_window(payload):
    assert payload['cohort'] == {
        'date': '2026-08-31', 'tz': 'Europe/Budapest',
        'window_utc': {'start': '2026-08-30T22:00:00Z', 'end': '2026-08-31T22:00:00Z'},
        'online_window_sec': hetfo.ONLINE_WINDOW_SEC,
        'recent_window_sec': hetfo.RECENT_WINDOW_SEC,
    }
    assert {r['user_id'] for r in payload['users']} == {'AAAAA1', 'CCCCC3', 'EEEEE5', 'FFFFF6'}


def test_rows_are_ordered_online_first_then_most_recently_seen(payload):
    assert [r['user_id'] for r in payload['users']] == ['AAAAA1', 'FFFFF6', 'EEEEE5', 'CCCCC3']


def test_player_who_is_mixing_right_now(payload):
    a = _row(payload, 'AAAAA1')
    assert a['nickname'] == 'Zsófi'
    assert a['registered_at'] == '2026-08-31T08:00:00Z'
    assert a['email_verified'] is True
    assert a['locale'] == 'hu'
    assert a['rounds'] == 6
    assert a['completed'] == 5          # 2 perfect + 2 acceptable + 1 identical skip
    assert a['perfect'] == 2
    assert a['skipped'] == 4
    assert a['best_delta_e'] == 0.0
    assert a['mean_delta_e'] == pytest.approx(1.6)
    assert a['play_time_sec'] == pytest.approx(120 + 200 + 1800 + 60 + 80 + 45)   # 3000 s capped
    assert a['rounds_last_15m'] == 1 and a['rounds_last_60m'] == 2
    assert a['last_round'] == {
        't': '2026-09-07T09:57:00Z', 'sec_ago': 180, 'category': 'perfect', 'delta_e': 0.0,
        'time_sec': 45.0, 'skipped': False, 'target': 'Merlot', 'target_hu': 'bordó',
        'target_rgb': [120, 30, 50],
    }
    assert a['xp'] == 500
    assert a['level'] == 3               # XP-derived (500 >= 400), above the stale stored 2
    assert a['current_streak'] == 2
    assert a['attempts'] == 2
    assert a['matches_completed'] == 1
    assert a['active_match_round'] == 4
    assert a['active_match_rounds'] == 10
    assert a['came_back'] is True
    assert a['online'] is True and a['recent'] is True
    assert a['last_seen'] == '2026-09-07T09:59:40Z'
    assert a['last_seen_sec_ago'] == 20
    assert a['last_seen_source'] == 'step'
    assert a['activity'] == {
        'kind': 'mixing', 'target': 'Merlot', 'target_hu': 'bordó',
        'target_rgb': [120, 30, 50], 'delta_e': 3.2, 'initial_delta_e': 40.0,
        'trajectory': [3.2], 'steps': 7, 'since_sec': 60,
    }


def test_player_who_never_played(payload):
    c = _row(payload, 'CCCCC3')
    assert c['registered_at'] == '2026-08-30T22:00:00Z'
    assert c['rounds'] == 0
    assert c['best_delta_e'] is None
    assert c['play_time_sec'] == 0.0
    assert c['level'] == 1 and c['xp'] == 0
    assert c['last_seen'] is None and c['last_seen_sec_ago'] is None
    assert c['online'] is False and c['recent'] is False
    assert c['activity'] == {'kind': 'never'}
    assert c['came_back'] is False
    assert c['last_round'] is None
    assert c['rounds_last_15m'] == 0 and c['rounds_last_60m'] == 0


def test_player_seen_recently_via_analytics_with_a_finished_calibration(payload):
    e = _row(payload, 'EEEEE5')
    assert e['online'] is False and e['recent'] is True
    assert e['last_seen_sec_ago'] == 30 * 60
    assert e['last_seen_source'] == 'app'
    assert e['activity'] == {'kind': 'idle'}
    assert e['calibration_completed'] == 1
    assert e['calibration_pt'] == 0.9
    assert e['calibration_at'] == 1.8


def test_player_in_the_middle_of_a_calibration_block(payload):
    f = _row(payload, 'FFFFF6')
    assert f['online'] is True
    assert f['last_seen_source'] == 'calibration'
    assert f['last_seen_sec_ago'] == 30
    assert f['calibration_completed'] == 0
    assert f['activity'] == {'kind': 'calibrating', 'answered': 5, 'total': 20}


def test_summary(payload):
    s = payload['summary']
    assert s['registered'] == 4
    assert s['email_verified'] == 1
    assert s['played'] == 1
    assert s['online_now'] == 2
    assert s['online_mixing'] == 1 and s['online_calibrating'] == 1 and s['online_in_app'] == 0
    assert s['active_last_hour'] == 3
    assert s['came_back'] == 1
    assert s['rounds'] == 6
    assert s['completed'] == 5
    assert s['perfect'] == 2
    assert s['skipped'] == 4
    assert s['play_time_sec'] == pytest.approx(2305.0)
    assert s['median_rounds_per_player'] == 6
    assert s['matches_completed'] == 1
    assert s['matches_active'] == 1
    assert s['calibration_sessions'] == 1
    assert s['calibration_players'] == 1
    assert s['gender'] == {'female': 2, 'male': 2}
    assert s['age'] == {'n': 4, 'median': 23.5, 'min': 18, 'max': 36}
    assert s['locale'] == {'hu': 2, 'en': 1, 'unset': 1}


def test_registration_day_timeline_buckets_in_local_time(payload):
    day = payload['timeline']['day']
    assert day['bucket_minutes'] == 15
    points = day['points']
    assert len(points) == 96
    assert points[0] == {'t': '2026-08-30T22:00:00Z', 'label': '00:00', 'rounds': 0, 'players': 0}
    busy = [(p['label'], p['rounds'], p['players']) for p in points if p['rounds']]
    assert busy == [('11:00', 1, 1), ('11:15', 1, 1), ('11:30', 1, 1)]


def test_daily_timeline_runs_from_the_cohort_day_to_today(payload):
    days = payload['timeline']['days']['points']
    assert [p['day'] for p in days] == [
        (COHORT_DAY + timedelta(days=i)).isoformat() for i in range(8)]
    assert days[0] == {'day': '2026-08-31', 'rounds': 3, 'completed': 2, 'players': 1}
    assert days[2] == {'day': '2026-09-02', 'rounds': 1, 'completed': 1, 'players': 1}
    assert days[7] == {'day': '2026-09-07', 'rounds': 2, 'completed': 2, 'players': 1}
    assert all(p['rounds'] == 0 and p['players'] == 0 for i, p in enumerate(days) if i not in (0, 2, 7))


# --------------------------------------------------------------------------- #
# the live layer
# --------------------------------------------------------------------------- #
def test_rolling_windows_count_recent_rounds_and_active_players(payload):
    windows = {w['minutes']: w for w in payload['live']['windows']}
    assert list(windows) == [5, 15, 60]
    assert windows[5] == {'minutes': 5, 'rounds': 1, 'completed': 1, 'perfect': 1, 'players': 1,
                          'active_players': 2, 'mean_delta_e': 0.0}
    assert windows[15]['rounds'] == 1 and windows[15]['active_players'] == 2
    assert windows[60] == {'minutes': 60, 'rounds': 2, 'completed': 2, 'perfect': 1, 'players': 1,
                           'active_players': 3, 'mean_delta_e': 0.6}


def test_minute_series_covers_the_last_hour_ending_now(payload):
    points = payload['live']['minutes']['points']
    assert len(points) == 60
    assert points[0]['t'] == '2026-09-07T09:01:00Z' and points[0]['label'] == '11:01'
    assert points[-1]['t'] == '2026-09-07T10:00:00Z' and points[-1]['label'] == '12:00'
    busy = [(p['label'], p['rounds'], p['players'], p['perfect']) for p in points if p['rounds']]
    assert busy == [('11:20', 1, 1, 0), ('11:57', 1, 1, 1)]


def test_feed_lists_the_last_two_hours_newest_first(payload):
    feed = payload['live']['feed']
    assert [(f['kind'], f['user_id'], f['sec_ago']) for f in feed] == [
        ('calibration_started', 'FFFFF6', 100),
        ('round', 'AAAAA1', 180),
        ('app_opened', 'EEEEE5', 1800),
        ('round', 'AAAAA1', 2400),
    ]
    assert feed[0]['nickname'] is None and feed[1]['nickname'] == 'Zsófi'
    assert feed[1]['round']['category'] == 'perfect' and feed[1]['round']['target'] == 'Merlot'
    assert feed[3]['round']['category'] == 'acceptable_difference'
    assert feed[3]['round']['delta_e'] == 1.2
    assert payload['live']['lookback_sec'] == 7200
    assert payload['live']['last_activity'] == '2026-09-07T09:59:40Z'
    assert payload['live']['last_activity_sec_ago'] == 20


def test_registration_and_match_completion_show_up_in_the_feed(app):
    # On the registration day itself the feed is where new sign-ups appear.
    # Replayed at two moments of that morning: only what had happened by then.
    from app import db

    def feed_at(now):
        with app.app_context():
            data = hetfo.build_live_payload(COHORT_DAY, now=now)
            db.session.remove()
        return [(f['kind'], f['user_id']) for f in data['live']['feed']]

    assert feed_at(datetime(2026, 8, 31, 9, 45)) == [
        ('round', 'AAAAA1'),                # 09:40
        ('round', 'AAAAA1'),                # 09:20
        ('round', 'AAAAA1'),                # 09:10
        ('registered', 'AAAAA1'),           # 08:00
    ]
    assert feed_at(datetime(2026, 8, 31, 11, 30)) == [
        ('match_completed', 'AAAAA1'),      # 11:00
        ('round', 'AAAAA1'),                # 09:40 (the rest is older than 2 h)
    ]


def test_wednesday_cohort_is_a_separate_day(app):
    from app import db
    with app.app_context():
        data = hetfo.build_live_payload(date(2026, 9, 2), now=NOW)
        db.session.remove()
    assert data['cohort']['window_utc'] == {
        'start': '2026-09-01T22:00:00Z', 'end': '2026-09-02T22:00:00Z'}
    assert [r['user_id'] for r in data['users']] == ['GGGGG7']
    g = data['users'][0]
    assert g['nickname'] == 'Szerda'
    assert g['rounds'] == 1 and g['perfect'] == 1 and g['completed'] == 1
    assert g['came_back'] is False
    assert data['summary']['registered'] == 1 and data['summary']['played'] == 1
    days = data['timeline']['days']['points']
    assert [p['day'] for p in days] == [
        (date(2026, 9, 2) + timedelta(days=i)).isoformat() for i in range(6)]
    assert days[0] == {'day': '2026-09-02', 'rounds': 1, 'completed': 1, 'players': 1}


def test_empty_cohort(app):
    from app import db
    with app.app_context():
        data = hetfo.build_live_payload(date(2026, 8, 24), now=NOW)
        db.session.remove()
    assert data['users'] == []
    assert data['summary']['registered'] == 0
    assert data['summary']['median_rounds_per_player'] is None
    assert data['summary']['age'] == {'n': 0, 'median': None, 'min': None, 'max': None}
    assert len(data['timeline']['day']['points']) == 96
    assert len(data['timeline']['days']['points']) == 15    # 08-24 .. 09-07
    assert data['live']['feed'] == []
    assert data['live']['last_activity'] is None
    assert [w['rounds'] for w in data['live']['windows']] == [0, 0, 0]
    assert len(data['live']['minutes']['points']) == 60


# --------------------------------------------------------------------------- #
# HTTP routes
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(app):
    hetfo._cache.clear()
    with app.test_client() as c:
        yield c


def test_live_api_returns_the_cohort(client):
    res = client.get('/api/hetfo/live?date=2026-08-31')
    assert res.status_code == 200
    data = res.get_json()
    assert data['status'] == 'success'
    assert data['cohort']['date'] == '2026-08-31'
    assert data['summary']['registered'] == 4
    assert {r['user_id'] for r in data['users']} == {'AAAAA1', 'CCCCC3', 'EEEEE5', 'FFFFF6'}
    assert 'email' not in data['users'][0] and 'birthdate' not in data['users'][0]


def test_live_api_defaults_to_the_monday_session(client):
    data = client.get('/api/hetfo/live').get_json()
    assert data['cohort']['date'] == '2026-08-31'
    assert data['summary']['registered'] == 4


def test_live_api_rejects_a_malformed_date(client):
    res = client.get('/api/hetfo/live?date=last-monday')
    assert res.status_code == 400
    assert res.get_json()['status'] == 'error'


def test_page_renders_with_its_config(client):
    html = client.get('/hetfo').get_data(as_text=True)
    assert 'id="hfRoot"' in html
    assert '"date": "2026-08-31"' in html
    assert '/hetfo?date=2026-08-24' in html and '/hetfo?date=2026-09-07' in html

    html = client.get('/hetfo?date=2026-09-07&refresh=30').get_data(as_text=True)
    assert '"date": "2026-09-07"' in html
    assert '"refresh_seconds": 30' in html

    html = client.get('/hetfo?date=nope').get_data(as_text=True)
    assert '"date": "2026-08-31"' in html
    assert 'That date was not understood' in html


def test_page_is_translated_for_hungarian_viewers(client):
    html = client.get('/hetfo?lang=hu').get_data(as_text=True)
    assert 'Hétfői kohorsz — élőben' in html
    assert 'Játékosok' in html


def test_mid_round_flush_lands_in_the_live_trajectory(client, app):
    """The game client posts mixing steps every few seconds while a round is
    open (static/main.js flushTelemetryLive). The ingest must accept partial
    batches, treat a re-sent prefix as duplicates, and the dashboard must then
    show the ΔE path of the open round."""
    def snap(de):
        return {'drops': {'white': 1, 'black': 0, 'red': 1, 'yellow': 0, 'blue': 0},
                'mixed_rgb': [200, 120, 110], 'delta_e': de, 'timer_sec': 12.5}

    def ev(seq, de_before, de_after):
        # Shaped like static/main.js enqueueTelemetryEvent: the client stamps
        # step_index / time_since_prev_step_ms itself, so a re-send is identical.
        return {'seq': seq, 'event_type': 'action_add', 'action_color': 'red',
                'client_ts_ms': 1_700_000_000_000 + seq * 1000,
                'state_before_json': snap(de_before), 'state_after_json': snap(de_after),
                'metadata_json': {'step_id': seq}, 'step_index': seq,
                'time_since_prev_step_ms': None if seq == 1 else 1000,
                'action_type': 'add', 'amount': 1,
                'delta_e_before': de_before, 'delta_e_after': de_after}

    header = {'attempt_uuid': 'att-live-g', 'user_id': 'GGGGG7', 'target_color_id': 1,
              'target_r': 120, 'target_g': 30, 'target_b': 50, 'initial_delta_e': 38.0,
              'final_delta_e': 12.5, 'num_steps': 2}
    batch = [ev(1, 38.0, 20.0), ev(2, 20.0, 12.5)]
    first = client.post('/api/mixing-attempt/ingest', json={'attempt': header, 'events': batch})
    assert first.status_code == 200, first.get_json()
    assert first.get_json()['inserted'] == 2
    # A retried flush re-sends the same prefix: idempotent, nothing conflicts.
    again = client.post('/api/mixing-attempt/ingest', json={'attempt': header, 'events': batch})
    assert again.status_code == 200
    assert again.get_json() == {'status': 'success', 'inserted': 0, 'duplicates': 2}
    header.update(final_delta_e=4.1, num_steps=3)
    more = client.post('/api/mixing-attempt/ingest',
                       json={'attempt': header, 'events': [ev(3, 12.5, 4.1)]})
    assert more.status_code == 200 and more.get_json()['inserted'] == 1

    from app import db
    with app.app_context():
        data = hetfo.build_live_payload(date(2026, 9, 2), now=datetime.utcnow())
        db.session.remove()
    g = _row(data, 'GGGGG7')
    assert g['online'] is True and g['last_seen_source'] == 'step'
    assert g['activity']['kind'] == 'mixing'
    assert g['activity']['target'] == 'Merlot'
    assert g['activity']['trajectory'] == [20.0, 12.5, 4.1]
    assert g['activity']['delta_e'] == 4.1
    assert g['activity']['initial_delta_e'] == 38.0
    assert g['activity']['steps'] == 3
    assert data['summary']['online_mixing'] == 1


def test_szerda_page_follows_the_wednesday_session(client):
    html = client.get('/szerda').get_data(as_text=True)
    assert '"page": "szerda"' in html
    assert '"date": "2026-09-02"' in html
    assert 'Wednesday cohort — live' in html
    assert '/szerda?date=2026-08-26' in html and '/szerda?date=2026-09-09' in html
    assert 'Szerdai kohorsz — élőben' in client.get('/szerda?lang=hu').get_data(as_text=True)

    data = client.get('/api/hetfo/live?page=szerda').get_json()
    assert data['status'] == 'success'
    assert data['cohort']['date'] == '2026-09-02'
    assert [r['user_id'] for r in data['users']] == ['GGGGG7']
    # An explicit date always wins over the page's default day.
    data = client.get('/api/hetfo/live?page=szerda&date=2026-08-31').get_json()
    assert data['cohort']['date'] == '2026-08-31'
    assert data['summary']['registered'] == 4
    # Unknown page slugs fall back to the Monday session instead of erroring.
    assert client.get('/api/hetfo/live?page=nope').get_json()['cohort']['date'] == '2026-08-31'
