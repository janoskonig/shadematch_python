"""Checks for the pure trial-block generator in ``app.calibration``.

Like ``test_stat_inference``, these stay off the database: ``build_block`` is a
pure function of (seed, center_pool), so the family-randomisation contract —
every colour family appears in each session, the family×level pairing
re-randomises per session, and the block is reproducible from the stored seed —
is testable without a Flask app or SQL. The pool query itself
(routes._calibration_center_pool) is a thin catalog filter left to runtime.

Run with:  pytest tests/test_calibration_block.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import calibration  # noqa: E402

FAMILIES = ['c%d' % i for i in range(10)] + ['skin']


def _pool(families=None, per_family=5):
    """Synthetic grouped pool: mid-gamut Lab centres (so pairs stay inside sRGB)."""
    families = FAMILIES if families is None else families
    pool = {}
    for fi, fam in enumerate(families):
        pool[fam] = [
            ('%s target %d' % (fam, i),
             (45.0 + fi * 2.0 + i, 8.0 + (fi % 4) * 3.0, 10.0 + (i % 3) * 2.0))
            for i in range(per_family)
        ]
    return pool


# --------------------------------------------------------------------------- #
# block composition
# --------------------------------------------------------------------------- #
def test_block_composition_is_unchanged():
    block = calibration.build_block(42, center_pool=_pool())
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
# families
# --------------------------------------------------------------------------- #
def test_no_pool_falls_back_to_static_anchors():
    block = calibration.build_block(42)
    assert {t['center_name'] for t in block} == {name for name, _ in calibration.CENTERS}
    assert all(t['center_group'] == 'skin' for t in block)
    assert calibration.build_block(42, center_pool={}) == block
    assert calibration.build_block(42, center_pool=None) == block


def test_every_family_gets_a_real_trial_each_session():
    pool = _pool()   # 11 families vs 16 real slots → each appears 1-2 times
    for seed in range(5):
        block = calibration.build_block(seed, center_pool=pool)
        real_fams = {t['center_group'] for t in block if not t['is_catch']}
        assert real_fams == set(FAMILIES)


def test_trials_use_only_their_familys_candidates():
    pool = _pool()
    names_of = {fam: {name for name, _ in cands} for fam, cands in pool.items()}
    block = calibration.build_block(42, center_pool=pool)
    for t in block:
        assert t['center_group'] in names_of
        assert t['center_name'] in names_of[t['center_group']]


def test_fewer_families_than_slots_still_works():
    pool = _pool(families=['c3', 'skin'])
    block = calibration.build_block(7, center_pool=pool)
    assert {t['center_group'] for t in block} == {'c3', 'skin'}


# --------------------------------------------------------------------------- #
# seeding
# --------------------------------------------------------------------------- #
def test_same_seed_and_pool_reproduce_the_block():
    pool = _pool()
    assert calibration.build_block(42, center_pool=pool) == \
        calibration.build_block(42, center_pool=pool)


def test_family_level_pairing_varies_by_seed():
    # The per-session family permutation must reshuffle which ΔE levels a family
    # meets — a frozen pairing would confound family with difficulty at the
    # population level. Compare the level-set seen by each family across seeds.
    pool = _pool()

    def pairing(seed):
        block = calibration.build_block(seed, center_pool=pool)
        return {fam: frozenset(t['target_de'] for t in block
                               if not t['is_catch'] and t['center_group'] == fam)
                for fam in FAMILIES}

    assert len({tuple(sorted(pairing(s).items())) for s in range(6)}) > 1
