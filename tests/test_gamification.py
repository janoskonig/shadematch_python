"""Characterization tests for the gamification engine.

Pins the observable behaviour of process_progression() and
compute_quota_progress() against a small deterministic catalog (see conftest),
so the planned service-layer extraction (move logic out of routes, compute quota
once per save, batch award grants) can be verified to preserve behaviour.

Values here were captured from the current implementation, not hand-derived —
they document "what the app does today".
"""
from datetime import date

import pytest

from app import db
from app import gamification as g
from app.models import UserProgress
from .conftest import SEED_USER_ID


def test_constants_are_stable():
    assert g.COVERAGE_QUOTA == 10
    assert g.DEFAULT_CAP == 4
    assert g.XP_TABLE["perfect"] == 130
    assert g.XP_TABLE["acceptable_difference"] == 38


def test_fresh_quota_progress(session):
    q = g.compute_quota_progress(SEED_USER_ID)
    assert q["colors_at_quota_total"] == 0
    assert q["total_recipe_colors"] == 4
    # Catalog sum-drop bands are {4, 6, 8}; two are above DEFAULT_CAP (4).
    assert q["cap_advance_steps"] == 2
    assert q["is_maxed_out"] is False


def test_first_perfect_match(session, target_ids):
    xp, awards, streak_event, level_up = g.process_progression(
        SEED_USER_ID, "perfect", skipped=False,
        target_color_id=target_ids[0], delta_e=0.0, today=date(2026, 6, 27),
    )
    db.session.commit()

    assert xp == 130
    assert streak_event == "started"
    assert level_up is None
    assert len(awards) == 1  # first-perfect-match award

    up = UserProgress.query.filter_by(user_id=SEED_USER_ID).first()
    assert up.xp == 130
    assert up.level == 1
    assert up.current_streak == 1
    assert up.max_sum_drop_unlocked == g.DEFAULT_CAP


def test_second_match_same_day_does_not_grow_streak(session, target_ids):
    g.process_progression(SEED_USER_ID, "perfect", False, target_ids[0], 0.0,
                          today=date(2026, 6, 27))
    db.session.commit()

    xp, _awards, streak_event, _lvl = g.process_progression(
        SEED_USER_ID, "acceptable_difference", False, target_ids[1], 1.5,
        today=date(2026, 6, 27),
    )
    db.session.commit()

    assert xp == 38
    assert streak_event == "same_day"
    up = UserProgress.query.filter_by(user_id=SEED_USER_ID).first()
    assert up.current_streak == 1


def test_next_day_increments_streak(session, target_ids):
    g.process_progression(SEED_USER_ID, "perfect", False, target_ids[0], 0.0,
                          today=date(2026, 6, 27))
    db.session.commit()
    _xp, _awards, streak_event, _lvl = g.process_progression(
        SEED_USER_ID, "perfect", False, target_ids[2], 0.0, today=date(2026, 6, 28),
    )
    db.session.commit()

    assert streak_event == "incremented"
    up = UserProgress.query.filter_by(user_id=SEED_USER_ID).first()
    assert up.current_streak == 2
    assert up.longest_streak == 2


def test_skip_does_not_qualify_for_streak(session, target_ids):
    _xp, _awards, streak_event, _lvl = g.process_progression(
        SEED_USER_ID, "big_difference", skipped=True,
        target_color_id=target_ids[0], delta_e=9.0, today=date(2026, 6, 27),
    )
    db.session.commit()
    # A skip never starts/advances a streak.
    assert streak_event is None
    up = UserProgress.query.filter_by(user_id=SEED_USER_ID).first()
    assert up.current_streak == 0
