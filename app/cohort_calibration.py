"""Daily calibration prerequisite for the Monday and Wednesday study cohorts."""
from datetime import datetime

from sqlalchemy import case, func

from . import db, calibration
from .models import CalibrationSession, CalibrationTrial, User


CALIBRATION_URL = '/calibration?return_to=match'
SESSION_TRIALS = len(calibration.DELTA_LEVELS) + 2  # eight ladder pairs + two catches
LEGACY_SESSION_TRIALS = (len(calibration.DELTA_LEVELS) * calibration.REPS_PER_LEVEL
                         + calibration.CATCH_IDENTICAL + calibration.CATCH_OBVIOUS)
JUDGMENTS = ('identical', 'acceptable', 'unacceptable')


def is_study_cohort_member(user):
    # Import lazily: the dashboard also imports match services at module load.
    from .hetfo import COHORT_MEMBER_ADDITIONS, COHORT_PAGES, utc_to_local

    if user is None:
        return False
    if any(user.id in ids for ids in COHORT_MEMBER_ADDITIONS.values()):
        return True
    return bool(user.created_at and utc_to_local(user.created_at).date()
                in {spec['date'] for spec in COHORT_PAGES.values()})


def has_full_calibration_today(user_id, now=None):
    """All 10 judgments must be stored, in a session finished today in Budapest.

    An ended_at timestamp alone is insufficient: legacy sessions could finish
    before the last answer was saved. Attention-check failures do not block
    completion; their quality flags remain available for analysis.
    """
    from .hetfo import cohort_window_utc, utc_to_local

    now = now or datetime.utcnow()
    start, end = cohort_window_utc(utc_to_local(now).date())
    answered = func.sum(case((CalibrationTrial.judgment.in_(JUDGMENTS), 1), else_=0))
    return (
        db.session.query(CalibrationSession.session_uuid)
        .join(CalibrationTrial, CalibrationTrial.session_uuid == CalibrationSession.session_uuid)
        .filter(CalibrationSession.user_id == user_id,
                CalibrationSession.ended_at >= start, CalibrationSession.ended_at < end,
                CalibrationSession.n_trials.in_((SESSION_TRIALS, LEGACY_SESSION_TRIALS)))
        .group_by(CalibrationSession.session_uuid, CalibrationSession.n_trials)
        .having((func.count(CalibrationTrial.id) == CalibrationSession.n_trials)
                & (answered == CalibrationSession.n_trials))
        .first() is not None
    )


def required_before_match(user_id, now=None):
    user = db.session.get(User, user_id) if user_id else None
    return is_study_cohort_member(user) and not has_full_calibration_today(user_id, now)
