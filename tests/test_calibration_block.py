"""Checks for the pure trial-block generator in ``app.calibration``.

Like ``test_stat_inference``, these stay off the database: ``build_block`` is a
pure function of (seed, center_pool), so the centre-randomisation contract —
sessions draw their colour centres from the catalog's skin pool, reproducibly
from the stored seed — is testable without a Flask app or SQL. The pool query
itself (routes._calibration_center_pool) is a one-line filter left to runtime.

Run with:  pytest tests/test_calibration_block.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import calibration  # noqa: E402


def _pool(n=30):
    # Synthetic skin-like Lab centres (well inside sRGB, so pairs stay in gamut).
    return [('skin %d' % i, (55.0 + i * 0.35, 11.0 + (i % 3) * 0.4, 14.0 + (i % 5) * 0.5))
            for i in range(n)]


# --------------------------------------------------------------------------- #
# block composition
# --------------------------------------------------------------------------- #
def test_block_composition_is_unchanged():
    block = calibration.build_block(42)
    n_real = len(calibration.DELTA_LEVELS) * calibration.REPS_PER_LEVEL
    n_catch = calibration.CATCH_IDENTICAL + calibration.CATCH_OBVIOUS
    assert len(block) == n_real + n_catch
    assert sum(t['is_catch'] for t in block) == n_catch
    # Every ladder level appears REPS_PER_LEVEL times among the real trials.
    reals = [t for t in block if not t['is_catch']]
    for de in calibration.DELTA_LEVELS:
        assert sum(1 for t in reals if t['target_de'] == de) == calibration.REPS_PER_LEVEL


def test_real_trials_hit_their_target_delta_e():
    block = calibration.build_block(7, center_pool=_pool())
    for t in block:
        if not t['is_catch']:
            assert abs(t['actual_de'] - t['target_de']) < 0.01


# --------------------------------------------------------------------------- #
# centre pool
# --------------------------------------------------------------------------- #
def test_no_pool_falls_back_to_static_anchors():
    block = calibration.build_block(42)
    names = {t['center_name'] for t in block}
    assert names == {name for name, _ in calibration.CENTERS}
    assert calibration.build_block(42, center_pool=[]) == block
    assert calibration.build_block(42, center_pool=None) == block


def test_pool_block_uses_only_sampled_pool_centres():
    pool = _pool()
    block = calibration.build_block(42, center_pool=pool)
    names = {t['center_name'] for t in block}
    assert len(names) == calibration.CENTERS_PER_SESSION
    assert names <= {name for name, _ in pool}


def test_small_pool_uses_every_centre():
    pool = _pool(2)
    block = calibration.build_block(7, center_pool=pool)
    assert {t['center_name'] for t in block} == {name for name, _ in pool}


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #
def test_same_seed_and_pool_reproduce_the_block():
    pool = _pool()
    assert calibration.build_block(42, center_pool=pool) == \
        calibration.build_block(42, center_pool=pool)


def test_different_seeds_vary_the_centres():
    pool = _pool()
    picks = {frozenset(t['center_name'] for t in calibration.build_block(s, center_pool=pool))
             for s in range(6)}
    assert len(picks) > 1
