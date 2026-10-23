"""The cohort visit is ten saved calibration pairs, then a ten-round match."""
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import calibration, cohort_calibration, db, matches, routes  # noqa: E402
from app.i18n import init_i18n  # noqa: E402
from app.models import CalibrationSession, CalibrationTrial, Match, MixingSession, TargetColor, User  # noqa: E402


@pytest.fixture()
def application(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    app = Flask(__name__, template_folder=str(root / 'templates'), static_folder=str(root / 'static'))
    app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_TRACK_MODIFICATIONS=False,
                      SQLALCHEMY_DATABASE_URI='sqlite:///' + str(tmp_path / 'cohort.db'))
    db.init_app(app)
    init_i18n(app)
    app.register_blueprint(routes.main)
    monkeypatch.setattr(routes, '_calibration_center_pool', lambda: None)
    monkeypatch.setattr(matches, 'match_cluster_assignments', lambda: {i + 1: 'c%d' % i for i in range(10)})
    with app.app_context():
        db.create_all()
        for uid, stamp in [('MONDAY', datetime(2026, 8, 31, 10)),
                           ('WEDNES', datetime(2026, 9, 2, 10)),
                           ('OTHER1', datetime(2026, 9, 1, 10)),
                           ('T0BS6V', datetime(2026, 10, 1, 10))]:
            db.session.add(User(id=uid, created_at=stamp, gender='male', birthdate=date(1990, 1, 1)))
        for i in range(10):
            db.session.add(TargetColor(id=i + 1, name='Test %d' % i, color_type='gamut',
                                       catalog_order=i + 1, r=120 + i, g=80, b=60,
                                       drop_white=1, drop_black=0, drop_red=1,
                                       drop_yellow=0, drop_blue=0))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def stored_session(uid, *, ended=None, n=10, answered=None):
    sid = str(uuid.uuid4())
    db.session.add(CalibrationSession(session_uuid=sid, user_id=uid, n_trials=n,
                                      mode='constant_stimuli_gamut',
                                      started_at=(ended or datetime.utcnow()) - timedelta(minutes=2),
                                      ended_at=ended))
    block = calibration.build_block(42, short=(n == 10))
    for seq, pair in enumerate(block[:n]):
        judgment = ('identical' if pair['actual_de'] < 1 else 'unacceptable')
        db.session.add(CalibrationTrial(session_uuid=sid, seq=seq, actual_de=pair['actual_de'],
                                        is_catch=pair['is_catch'], catch_kind=pair['catch_kind'],
                                        judgment=judgment if answered is None or seq < answered else None))
    db.session.commit()
    return sid


@pytest.mark.parametrize('uid', ['MONDAY', 'WEDNES', 'T0BS6V'])
def test_each_cohort_member_must_calibrate_before_a_match(application, uid):
    with application.test_client() as client:
        res = client.post('/api/match/current', json={'user_id': uid})
        assert res.status_code == 409
        assert res.get_json()['code'] == 'CALIBRATION_REQUIRED'
        assert res.get_json()['calibration']['session_pairs'] == 10
        with application.app_context():
            assert Match.query.count() == 0
        session = client.post('/calibration/start', json={'user_id': uid}).get_json()
        assert session['n_trials'] == len(session['trials']) == 10


def test_other_players_keep_their_twenty_pair_optional_calibration(application):
    with application.test_client() as client:
        res = client.post('/api/match/current', json={'user_id': 'OTHER1'})
        assert res.status_code == 200, res.get_json()
        assert not res.get_json()['calibration']['required_before_match']
        session = client.post('/calibration/start', json={'user_id': 'OTHER1'}).get_json()
        assert session['n_trials'] == 20


def test_ten_answers_unlock_a_complete_ten_round_mixing_match(application):
    with application.test_client() as client:
        session = client.post('/calibration/start', json={'user_id': 'MONDAY'}).get_json()
        sid = session['session_id']
        for trial in session['trials'][:-1]:
            res = client.post('/calibration/respond', json={'session_id': sid,
                              'trial_id': trial['trial_id'], 'judgment': 'acceptable'})
            assert res.status_code == 200
        unfinished = client.post('/calibration/finish', json={'session_id': sid})
        assert unfinished.status_code == 409
        assert unfinished.get_json()['answered'] == 9
        assert client.post('/api/match/current', json={'user_id': 'MONDAY'}).status_code == 409
        with application.app_context():
            assert db.session.get(CalibrationSession, sid).ended_at is None
        client.post('/calibration/respond', json={'session_id': sid,
                    'trial_id': session['trials'][-1]['trial_id'], 'judgment': 'identical'})
        finished = client.post('/calibration/finish', json={'session_id': sid})
        assert finished.status_code == 200, finished.get_json()
        assert finished.get_json()['summary']['n_real'] == 8
        assert finished.get_json()['summary']['n_catch'] == 2
        assert not finished.get_json()['progress']['required_today']
        match_res = client.post('/api/match/current', json={'user_id': 'MONDAY'})
        assert match_res.status_code == 200, match_res.get_json()
        match = match_res.get_json()['match']
        assert match['round_count'] == len(match['rounds']) == 10
        assert match_res.get_json()['next_action']['primary']['id'] == 'continue_match'
        for i, rnd in enumerate(match['rounds']):
            target = rnd['target']
            result = client.post('/save_skip', json={
                'user_id': 'MONDAY', 'attempt_uuid': str(uuid.uuid4()),
                'match_id': match['match_id'], 'match_round_index': i,
                'target_color_id': target['id'],
                'target_r': target['rgb'][0], 'target_g': target['rgb'][1], 'target_b': target['rgb'][2],
                'drop_white': 1, 'drop_black': 0, 'drop_red': 1, 'drop_yellow': 0, 'drop_blue': 0,
                'delta_e': 1, 'skip_perception': 'acceptable', 'time_sec': 60,
                'timestamp': datetime.utcnow().isoformat(),
            })
            assert result.status_code == 200, result.get_json()
            assert result.get_json()['match']['current_round'] == i + 1
        assert result.get_json()['match']['match_completed']
        with application.app_context():
            assert db.session.get(Match, match['match_id']).status == 'completed'
            assert MixingSession.query.filter_by(user_id='MONDAY').count() == 10


def test_match_save_and_skip_cannot_bypass_required_calibration(application):
    with application.test_client() as client:
        for url in ['/save_session', '/save_skip', '/api/match/skip-round']:
            res = client.post(url, json={'user_id': 'MONDAY', 'match_id': 1, 'round_index': 0})
            assert res.status_code == 409 and res.get_json()['code'] == 'CALIBRATION_REQUIRED'
        with application.app_context():
            assert MixingSession.query.count() == 0


def test_after_five_sessions_a_new_daily_session_is_still_required(application):
    with application.app_context():
        now = datetime.utcnow()
        for i in range(5):
            stored_session('MONDAY', ended=now - timedelta(days=i + 1))
        assert cohort_calibration.required_before_match('MONDAY', now)
        stored_session('MONDAY', ended=now)
        assert not cohort_calibration.required_before_match('MONDAY', now)
        assert cohort_calibration.required_before_match('MONDAY', now + timedelta(days=1))


def test_old_partial_sessions_do_not_unlock_the_match(application):
    with application.app_context():
        stored_session('MONDAY', ended=datetime.utcnow(), answered=9)
        assert cohort_calibration.required_before_match('MONDAY')


def test_an_already_completed_legacy_twenty_pair_run_counts_for_today(application):
    with application.app_context():
        stored_session('MONDAY', ended=datetime.utcnow(), n=20)
        assert not cohort_calibration.required_before_match('MONDAY')


def test_reposting_an_old_finish_does_not_retimestamp_or_unlock_it(application):
    ended = datetime.utcnow() - timedelta(days=1)
    with application.app_context():
        sid = stored_session('MONDAY', ended=ended)
    with application.test_client() as client:
        assert client.post('/calibration/finish', json={'session_id': sid}).status_code == 200
        assert client.post('/api/match/current', json={'user_id': 'MONDAY'}).status_code == 409
    with application.app_context():
        assert db.session.get(CalibrationSession, sid).ended_at == ended


def test_daily_boundary_is_budapest_midnight(application):
    with application.app_context():
        stored_session('MONDAY', ended=datetime(2026, 10, 23, 21, 59))
        assert cohort_calibration.has_full_calibration_today('MONDAY', datetime(2026, 10, 23, 21, 59, 30))
        assert not cohort_calibration.has_full_calibration_today('MONDAY', datetime(2026, 10, 23, 22, 1))


def test_current_match_resumes_after_calibration_without_resetting_it(application):
    with application.app_context():
        active = matches.create_match('MONDAY')
        active.current_round = 3
        for rnd in matches._rounds_of(active)[:3]:
            rnd.outcome = 'completed'
        db.session.commit()
        mid = active.id
    with application.test_client() as client:
        assert client.post('/api/match/current', json={'user_id': 'MONDAY'}).status_code == 409
        with application.app_context():
            stored_session('MONDAY', ended=datetime.utcnow())
        resumed = client.post('/api/match/current', json={'user_id': 'MONDAY'}).get_json()['match']
        assert resumed['match_id'] == mid and resumed['current_round'] == 3


def test_cohort_calibration_is_available_on_the_registration_day(application, monkeypatch):
    class RegistrationDay(datetime):
        @classmethod
        def utcnow(cls):
            return datetime(2026, 8, 31, 12)

    monkeypatch.setattr(routes, 'datetime', RegistrationDay)
    monkeypatch.setattr(cohort_calibration, 'datetime', RegistrationDay)
    with application.app_context():
        status = routes._calibration_status('MONDAY')
        assert status['required_today'] and status['available_today']
        assert status['session_pairs'] == 10
