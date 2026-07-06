"""
Probe assignment service for the learning-effect study.

Design (see notes/ShadeMatch_tanulasi_AB_terv.qmd):
  - Every ~8-10th round a probe slot opens (never in the user's first 5 rounds).
  - Round-level randomization decides the arm:
      'repeat'      — a colour the user played ≥ MIN_REPEAT_GAP_ROUNDS rounds ago
      'matched_new' — an unplayed colour, difficulty-matched, inside the user's
                      unlocked sum-drop band (never a locked colour)
      'repeat_short' / 'repeat_long' — fallback contrast (recency of repetition)
        when the unlocked band has no unplayed colour left.
  - Assignment-time snapshots (exposure history, cumulative rounds, level/cap)
    are stored on the slot so the randomization stays auditable.
  - Probe rounds are quota-neutral (gamification.process_progression is_probe=True):
    XP/streak/daily missions accrue normally, quota/level does not move.
  - The arm is never sent to the client: the payload only carries the target
    colour, so a probe round is visually indistinguishable from a normal one.

Exposure snapshots are computed from MixingSession (which includes probe
rounds), NOT from UserTargetColorStats (which excludes them by design).
"""
import hashlib
import random as _random
from datetime import datetime, date, timedelta

from . import db
from .models import ProbeSlot, MixingSession, TargetColor, UserProgress
from .gamification import (
    MIN_SUM_DROP_BAND,
    target_color_sum_drop,
    _effective_sum_cap,
)

POLICY_VERSION = 'probe-v1'
MIN_ROUNDS_BEFORE_PROBES = 5
PROBE_INTERVAL_MIN = 8
PROBE_INTERVAL_MAX = 10
MIN_REPEAT_GAP_ROUNDS = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rng_for(user_id: str, slot_index: int) -> _random.Random:
    """Deterministic, auditable RNG per (policy, user, slot)."""
    seed_material = f'{POLICY_VERSION}:{user_id}:{slot_index}'
    digest = hashlib.sha256(seed_material.encode()).hexdigest()
    return _random.Random(int(digest, 16)), digest[:64]


def _user_rounds(user_id: str):
    """All saved rounds of the user, oldest first (includes probe rounds)."""
    return (
        MixingSession.query
        .filter_by(user_id=user_id)
        .order_by(MixingSession.timestamp.asc())
        .all()
    )


def _exposure_snapshot(rounds, target_color_id):
    """(count, last_at, rounds_since_last) of the user's exposure to a colour."""
    count = 0
    last_at = None
    last_idx = None
    for i, r in enumerate(rounds):
        if r.target_color_id == target_color_id:
            count += 1
            last_at = r.timestamp
            last_idx = i
    rounds_since = (len(rounds) - 1 - last_idx) if last_idx is not None else None
    return count, last_at, rounds_since


def _recipe_colors_in_band(cap: int):
    """Gamut colours with a full recipe inside [MIN_SUM_DROP_BAND, effective cap]."""
    eff = _effective_sum_cap(cap)
    out = []
    for tc in TargetColor.query.filter_by(color_type='gamut').all():
        s = target_color_sum_drop(tc)
        if s is not None and MIN_SUM_DROP_BAND <= s <= eff:
            out.append(tc)
    return out


def get_pending_slot(user_id: str):
    """The user's not-yet-played probe slot, if any."""
    return (
        ProbeSlot.query
        .filter_by(user_id=user_id)
        .filter(ProbeSlot.status.in_(('assigned', 'served')))
        .order_by(ProbeSlot.assigned_at.desc())
        .first()
    )


def _last_slot(user_id: str):
    return (
        ProbeSlot.query
        .filter_by(user_id=user_id)
        .order_by(ProbeSlot.slot_index.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def maybe_assign_flow_probe(user_id: str):
    """
    Assign (or return the pending) flow-channel probe slot when one is due.
    Adds to the session but does not commit. Returns ProbeSlot or None.
    """
    if not user_id:
        return None

    pending = get_pending_slot(user_id)
    if pending:
        return pending

    rounds = _user_rounds(user_id)
    n = len(rounds)
    if n < MIN_ROUNDS_BEFORE_PROBES:
        return None

    last = _last_slot(user_id)
    slot_index = (last.slot_index + 1) if last else 1
    rng, seed = _rng_for(user_id, slot_index)

    # Due check: at least interval rounds since the previous probe assignment
    # (interval is per-slot deterministic in [MIN, MAX]).
    interval = rng.randint(PROBE_INTERVAL_MIN, PROBE_INTERVAL_MAX)
    rounds_since_last_probe = n - (last.cumulative_prior_rounds if last else 0)
    if last and rounds_since_last_probe < interval:
        return None

    up = UserProgress.query.filter_by(user_id=user_id).first()
    cap = int(up.max_sum_drop_unlocked) if up else MIN_SUM_DROP_BAND
    level = int(up.level) if up else 1

    played_ids = {r.target_color_id for r in rounds if r.target_color_id is not None}
    band_colors = _recipe_colors_in_band(cap)
    band_ids = {tc.id for tc in band_colors}

    # Repeat candidates: a gamut colour in the current band the user played long
    # enough ago. Restricting to band_ids keeps repeats on the live (gamut) catalog
    # — retired basic/skin colours in the play history are never re-served.
    repeat_candidates = []
    for cid in sorted(played_ids & band_ids):
        _, _, since = _exposure_snapshot(rounds, cid)
        if since is not None and since >= MIN_REPEAT_GAP_ROUNDS:
            repeat_candidates.append((cid, since))

    new_candidates = [tc for tc in band_colors if tc.id not in played_ids]

    # Arm decision (1:1), with deterministic fallbacks.
    arm = 'repeat' if rng.random() < 0.5 else 'matched_new'
    target_id = None

    if arm == 'matched_new' and not new_candidates:
        arm = 'repeat'  # band exhausted → recency-contrast fallback below
    if arm == 'repeat' and not repeat_candidates:
        arm = 'matched_new' if new_candidates else None
    if arm is None:
        return None  # nothing eligible on either arm

    if arm == 'matched_new':
        # Difficulty reference: median sum-drop of the user's played in-band colours.
        played_sums = sorted(
            s for cid in played_ids if cid in band_ids
            if (s := target_color_sum_drop(TargetColor.query.get(cid))) is not None
        )
        ref = played_sums[len(played_sums) // 2] if played_sums else MIN_SUM_DROP_BAND
        best_gap = min(abs(target_color_sum_drop(tc) - ref) for tc in new_candidates)
        closest = [tc for tc in new_candidates
                   if abs(target_color_sum_drop(tc) - ref) == best_gap]
        target_id = rng.choice(sorted(tc.id for tc in closest))
    else:
        if new_candidates:
            # Regular repeat arm.
            target_id = rng.choice(sorted(cid for cid, _ in repeat_candidates))
        else:
            # Fallback contrast: short vs long recency among repeat candidates.
            by_recency = sorted(repeat_candidates, key=lambda t: t[1])
            half = max(1, len(by_recency) // 2)
            if rng.random() < 0.5:
                arm = 'repeat_short'
                pool = by_recency[:half]
            else:
                arm = 'repeat_long'
                pool = by_recency[-half:]
            target_id = rng.choice(sorted(cid for cid, _ in pool))

    exp_count, exp_last_at, exp_since = _exposure_snapshot(rounds, target_id)

    slot = ProbeSlot(
        user_id=user_id,
        channel='flow',
        slot_index=slot_index,
        arm=arm,
        target_color_id=target_id,
        seed=seed,
        policy_version=POLICY_VERSION,
        prior_exposure_count=exp_count,
        last_exposure_at=exp_last_at,
        rounds_since_last_exposure=exp_since,
        cumulative_prior_rounds=n,
        level_at_assignment=level,
        cap_at_assignment=cap,
        status='assigned',
    )
    db.session.add(slot)
    return slot


def assign_daily_probe(user_id: str, target_color_id: int, today=None):
    """
    Create (or rebind) today's daily-channel probe slot for the user. The
    daily challenge is the primary probe carrier: everyone plays the same
    colour on the same day, and the round is quota-neutral like any probe.

    One slot per user per day. A retry within the day reuses the slot: the
    status is reset to 'assigned' and the attempt binding cleared, so the new
    attempt can bind via /api/probe/start. Adds to the session, no commit.
    """
    if not user_id:
        return None
    today = today or date.today()
    day_start = datetime(today.year, today.month, today.day)
    day_end = day_start + timedelta(days=1)

    slot = (
        ProbeSlot.query
        .filter_by(user_id=user_id, channel='daily')
        .filter(ProbeSlot.assigned_at >= day_start, ProbeSlot.assigned_at < day_end)
        .order_by(ProbeSlot.assigned_at.desc())
        .first()
    )
    if slot:
        slot.status = 'assigned'
        slot.attempt_uuid = None
        return slot

    rounds = _user_rounds(user_id)
    up = UserProgress.query.filter_by(user_id=user_id).first()
    last = _last_slot(user_id)
    slot_index = (last.slot_index + 1) if last else 1
    _, seed = _rng_for(user_id, slot_index)
    exp_count, exp_last_at, exp_since = _exposure_snapshot(rounds, target_color_id)

    slot = ProbeSlot(
        user_id=user_id,
        channel='daily',
        slot_index=slot_index,
        arm='daily',
        target_color_id=target_color_id,
        seed=seed,
        policy_version=POLICY_VERSION,
        prior_exposure_count=exp_count,
        last_exposure_at=exp_last_at,
        rounds_since_last_exposure=exp_since,
        cumulative_prior_rounds=len(rounds),
        level_at_assignment=int(up.level) if up else 1,
        cap_at_assignment=int(up.max_sum_drop_unlocked) if up else MIN_SUM_DROP_BAND,
        status='assigned',
    )
    db.session.add(slot)
    return slot


# ---------------------------------------------------------------------------
# Binding and resolution
# ---------------------------------------------------------------------------

def bind_probe_attempt(slot_id: int, attempt_uuid: str, user_id: str):
    """Bind a starting attempt to its probe slot (status → served)."""
    slot = ProbeSlot.query.get(slot_id)
    if not slot or slot.user_id != user_id:
        return None
    if slot.status not in ('assigned', 'served'):
        return None
    slot.attempt_uuid = attempt_uuid
    slot.status = 'served'
    return slot


def resolve_probe_for_attempt(attempt_uuid, user_id, target_color_id, skipped):
    """
    Called from the save endpoints. Returns True iff this attempt is a probe
    round (and marks the slot completed/skipped). Falls back to matching the
    user's pending slot by target colour when the client did not call
    /api/probe/start.
    """
    if not user_id:
        return False

    slot = None
    if attempt_uuid:
        slot = ProbeSlot.query.filter_by(attempt_uuid=attempt_uuid).first()
        if slot and slot.user_id != user_id:
            return False
    if slot is None and target_color_id is not None:
        slot = (
            ProbeSlot.query
            .filter_by(user_id=user_id, target_color_id=target_color_id, status='assigned')
            .filter(ProbeSlot.attempt_uuid.is_(None))
            .order_by(ProbeSlot.assigned_at.desc())
            .first()
        )
        if slot is not None and attempt_uuid:
            slot.attempt_uuid = attempt_uuid

    if slot is None:
        return False

    slot.status = 'skipped' if skipped else 'completed'
    return True


def probe_payload(slot):
    """Client-facing payload. Deliberately arm-free."""
    if slot is None:
        return {'probe': None}
    tc = TargetColor.query.get(slot.target_color_id)
    if tc is None:
        return {'probe': None}
    return {
        'probe': {
            'slot_id': slot.id,
            'target_color': {
                'id': tc.id,
                'name': tc.name,
                'type': tc.color_type,
                'rgb': [tc.r, tc.g, tc.b],
            },
        }
    }
