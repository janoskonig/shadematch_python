#!/usr/bin/env python3
"""
End-to-end verification of the probe (learning-effect study) pipeline against
a throwaway SQLite database. Run with:

  DATABASE_URL=sqlite:////tmp/probe_verify.db PYTHONPATH=. python3 scripts/verify_probe_pipeline.py

Covers: slot assignment (band constraint, snapshots, idempotence, interval),
arm invariants, quota neutrality of process_progression(is_probe=True),
save-time resolution, and the probe_schedule daily override.
"""
import os
import sys
from datetime import datetime, timedelta, date

assert os.environ.get('DATABASE_URL', '').startswith('sqlite'), \
    'Refusing to run: set DATABASE_URL to a throwaway sqlite:/// URL first.'

from app import create_app, db                      # noqa: E402
from app.models import (                            # noqa: E402
    User, TargetColor, MixingSession, UserProgress, UserTargetColorStats,
    ProbeSlot, ProbeSchedule,
)
from app.probe import (                             # noqa: E402
    maybe_assign_flow_probe, resolve_probe_for_attempt, _rng_for,
    MIN_REPEAT_GAP_ROUNDS, MIN_ROUNDS_BEFORE_PROBES,
)
from app.gamification import (                      # noqa: E402
    process_progression, compute_quota_progress, target_color_sum_drop,
    _effective_sum_cap,
)

CHECKS = []


def check(name, cond, detail=''):
    CHECKS.append((name, bool(cond), detail))
    print(('✅' if cond else '❌'), name, ('— ' + str(detail)) if detail and not cond else '')


app = create_app()
with app.app_context():
    db.drop_all()
    db.create_all()

    # ── Seed: 8 recipe colours (sums 2..20), one user, played history ──────
    recipes = [
        (1, 'A', 'basic', 1, 1, 0, 0, 0),   # sum 2
        (2, 'B', 'basic', 2, 2, 0, 0, 0),   # sum 4
        (3, 'C', 'basic', 2, 0, 2, 0, 0),   # sum 4
        (4, 'D', 'skin',  2, 2, 1, 1, 0),   # sum 6
        (5, 'E', 'skin',  3, 2, 2, 1, 0),   # sum 8
        (6, 'F', 'skin',  4, 2, 2, 1, 1),   # sum 10
        (7, 'G', 'skin',  6, 4, 3, 2, 1),   # sum 16 (locked at cap 8)
        (8, 'H', 'skin',  8, 5, 4, 2, 1),   # sum 20 (locked at cap 8)
    ]
    for cid, name, ctype, w, b, r, y, bl in recipes:
        db.session.add(TargetColor(
            id=cid, name=name, color_type=ctype, catalog_order=cid,
            r=120, g=100, b=90,
            drop_white=w, drop_black=b, drop_red=r, drop_yellow=y, drop_blue=bl,
        ))
    db.session.add(User(id='TEST01', email='t@example.com',
                        birthdate=date(1990, 1, 1), gender='female'))
    db.session.add(UserProgress(
        user_id='TEST01', xp=0, level=3, current_streak=0, longest_streak=0,
        streak_freeze_available=0, max_sum_drop_unlocked=8,
    ))

    # Played history: colour 2 early (so its last exposure is far back),
    # then rounds on colours 3/4/5. 14 rounds total.
    t0 = datetime(2026, 6, 1, 10, 0, 0)
    seq = [2, 2, 3, 4, 5, 3, 4, 5, 3, 4, 5, 3, 4, 5]
    for i, cid in enumerate(seq):
        db.session.add(MixingSession(
            attempt_uuid=f'uuid-hist-{i}', user_id='TEST01', target_color_id=cid,
            target_r=120, target_g=100, target_b=90,
            drop_white=1, drop_black=1, drop_red=0, drop_yellow=0, drop_blue=0,
            delta_e=0.0, time_sec=30, timestamp=t0 + timedelta(minutes=i),
            skipped=False, match_category='perfect',
        ))
    db.session.commit()

    # ── 1. Assignment ──────────────────────────────────────────────────────
    slot = maybe_assign_flow_probe('TEST01')
    db.session.commit()
    check('slot assigned', slot is not None)
    if slot:
        tc = TargetColor.query.get(slot.target_color_id)
        s = target_color_sum_drop(tc)
        eff = _effective_sum_cap(8)
        check('probe colour inside unlocked band', 2 <= s <= eff, f'sum={s}, eff={eff}')
        check('cumulative_prior_rounds snapshot', slot.cumulative_prior_rounds == len(seq),
              slot.cumulative_prior_rounds)
        check('level/cap snapshot', slot.level_at_assignment == 3 and slot.cap_at_assignment == 8,
              (slot.level_at_assignment, slot.cap_at_assignment))
        played = set(seq)
        if slot.arm == 'repeat':
            check('repeat arm: colour was played, gap ok',
                  slot.target_color_id in played
                  and slot.rounds_since_last_exposure >= MIN_REPEAT_GAP_ROUNDS,
                  (slot.target_color_id, slot.rounds_since_last_exposure))
        elif slot.arm == 'matched_new':
            check('matched_new arm: colour unplayed', slot.target_color_id not in played,
                  slot.target_color_id)
        else:
            check('fallback arm only when band exhausted', False, slot.arm)

        # Idempotence: pending slot is returned, not duplicated.
        again = maybe_assign_flow_probe('TEST01')
        check('pending slot idempotent', again is not None and again.id == slot.id)
        check('single slot row', ProbeSlot.query.count() == 1, ProbeSlot.query.count())

    # Determinism of the seeded RNG.
    r1, s1 = _rng_for('TEST01', 1)
    r2, s2 = _rng_for('TEST01', 1)
    check('seed deterministic', s1 == s2 and r1.random() == r2.random())

    # ── 2. Quota neutrality ────────────────────────────────────────────────
    probe_color = slot.target_color_id
    stats_before = {
        (st.user_id, st.target_color_id): st.attempt_count
        for st in UserTargetColorStats.query.all()
    }
    quota_before = compute_quota_progress('TEST01')
    xp_before = UserProgress.query.filter_by(user_id='TEST01').first().xp

    xp, awards, streak_ev, level_up = process_progression(
        user_id='TEST01', match_category='perfect', skipped=False,
        target_color_id=probe_color, delta_e=0.0, is_probe=True,
    )
    db.session.commit()
    stats_after = {
        (st.user_id, st.target_color_id): st.attempt_count
        for st in UserTargetColorStats.query.all()
    }
    quota_after = compute_quota_progress('TEST01')
    xp_after = UserProgress.query.filter_by(user_id='TEST01').first().xp

    check('probe: stats untouched', stats_before == stats_after,
          (stats_before, stats_after))
    check('probe: quota unchanged',
          quota_before['colors_at_quota_total'] == quota_after['colors_at_quota_total']
          and quota_before['coverage_ratio'] == quota_after['coverage_ratio'])
    check('probe: no level_up event', level_up is None, level_up)
    check('probe: XP still granted', xp_after == xp_before + xp and xp > 0, (xp_before, xp_after, xp))

    # Control: a normal round DOES move the stats.
    process_progression(
        user_id='TEST01', match_category='perfect', skipped=False,
        target_color_id=3, delta_e=0.0, is_probe=False,
    )
    db.session.commit()
    st3 = UserTargetColorStats.query.filter_by(user_id='TEST01', target_color_id=3).first()
    check('normal round: stats written', st3 is not None and st3.attempt_count == 1,
          getattr(st3, 'attempt_count', None))

    # ── 3. Save-time resolution (fallback binding by colour) ──────────────
    ok = resolve_probe_for_attempt('uuid-probe-1', 'TEST01', probe_color, skipped=False)
    db.session.commit()
    slot2 = ProbeSlot.query.get(slot.id)
    check('resolution marks slot completed',
          ok and slot2.status == 'completed' and slot2.attempt_uuid == 'uuid-probe-1',
          (ok, slot2.status, slot2.attempt_uuid))

    non_probe = resolve_probe_for_attempt('uuid-normal-1', 'TEST01', 3, skipped=False)
    check('normal attempt is not a probe', non_probe is False, non_probe)

    # ── 4. Interval guard: no new slot right after the previous one ───────
    slot3 = maybe_assign_flow_probe('TEST01')
    check('interval guard blocks immediate re-assignment', slot3 is None,
          getattr(slot3, 'id', None))

    # ── 5. Daily schedule override ─────────────────────────────────────────
    from app.routes import _daily_target_ids
    base_ids = _daily_target_ids(date(2026, 7, 10))
    db.session.add(ProbeSchedule(challenge_date=date(2026, 7, 10),
                                 target_color_id=5, position=0, rotation_cycle=1))
    db.session.commit()
    sched_ids = _daily_target_ids(date(2026, 7, 10))
    check('daily override puts scheduled colour first',
          sched_ids[0] == 5 and set(base_ids) <= set(sched_ids) | {5},
          (base_ids, sched_ids))
    other_day = _daily_target_ids(date(2026, 7, 11))
    check('other days unaffected', 5 not in other_day[:1] or other_day == base_ids,
          other_day[:3])

    # ── 6. Band-exhausted fallback (repeat_short / repeat_long) ────────────
    db.session.add(User(id='TEST02', email='t2@example.com',
                        birthdate=date(1985, 5, 5), gender='male'))
    db.session.add(UserProgress(
        user_id='TEST02', xp=0, level=2, current_streak=0, longest_streak=0,
        streak_freeze_available=0, max_sum_drop_unlocked=6,
    ))
    # Cap 6 band = colours 1..4, all played (band exhausted). Colour 1 was
    # played early and never again → it is a stale repeat candidate.
    seq2 = [1, 1, 2, 3, 4, 2, 3, 4, 2, 3, 4, 2, 3, 4]
    for i, cid in enumerate(seq2):
        db.session.add(MixingSession(
            attempt_uuid=f'uuid2-{i}', user_id='TEST02', target_color_id=cid,
            target_r=120, target_g=100, target_b=90,
            drop_white=1, drop_black=1, drop_red=0, drop_yellow=0, drop_blue=0,
            delta_e=0.0, time_sec=20, timestamp=t0 + timedelta(minutes=i),
            skipped=False, match_category='perfect',
        ))
    db.session.commit()

    slot_b = maybe_assign_flow_probe('TEST02')
    db.session.commit()
    check('band-exhausted user gets a slot', slot_b is not None)
    if slot_b:
        check('fallback/repeat arm when no new colour in band',
              slot_b.arm in ('repeat', 'repeat_short', 'repeat_long'), slot_b.arm)
        check('fallback colour was played', slot_b.target_color_id in set(seq2),
              slot_b.target_color_id)
        check('fallback colour inside band',
              target_color_sum_drop(TargetColor.query.get(slot_b.target_color_id))
              <= _effective_sum_cap(6))
        check('exposure snapshot filled',
              slot_b.prior_exposure_count > 0
              and slot_b.rounds_since_last_exposure is not None,
              (slot_b.prior_exposure_count, slot_b.rounds_since_last_exposure))

failed = [c for c in CHECKS if not c[1]]
print(f'\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed.')
sys.exit(1 if failed else 0)
