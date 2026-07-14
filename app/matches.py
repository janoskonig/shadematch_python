"""Match service: 10-round matches, one target per macro-cluster.

A match is drawn server-side at creation: the 10 cluster codes
(app/clusters.py MACRO_ORDER) are shuffled, and for each cluster one target is
drawn uniformly at random from its recipe-complete members (repeats across
matches are allowed by design — the draw is memoryless). Round accounting is
server-authoritative: the save endpoints (and the unmixed-skip endpoint)
advance current_round; saves without match fields never touch a match, which
keeps daily-challenge, head-to-head and probe rounds match-neutral.

All functions add to db.session without committing (caller commits), matching
the probe.py convention.
"""
import random
from datetime import datetime

from . import db
from .models import Match, MatchRound, MixingSession, TargetColor
from .clusters import (
    MACRO_ORDER, cluster_assignments, cluster_display_names, current_fingerprint,
)
from .gamification import target_color_sum_drop

ROUNDS_PER_MATCH = 10


def _recipe_complete_gamut_targets():
    """Every served target must be mixable: full 5-channel recipe required."""
    return [
        tc for tc in TargetColor.query.filter_by(color_type='gamut').all()
        if target_color_sum_drop(tc) is not None
    ]


def create_match(user_id: str) -> Match:
    """Draw a fresh match: shuffled cluster order, one uniform draw per cluster."""
    pool = _recipe_complete_gamut_targets()
    assign = cluster_assignments()
    by_cluster = {code: [] for code in MACRO_ORDER}
    for tc in pool:
        code = assign.get(tc.id)
        if code in by_cluster:
            by_cluster[code].append(tc)

    order = list(MACRO_ORDER)
    random.shuffle(order)

    match = Match(
        user_id=user_id,
        status='active',
        current_round=0,
        round_count=ROUNDS_PER_MATCH,
        clusters_fingerprint=current_fingerprint(),
    )
    db.session.add(match)
    db.session.flush()  # match.id for the rounds

    for idx, code in enumerate(order):
        members = by_cluster.get(code) or pool  # defensive: empty cluster → whole pool
        tc = random.choice(members)
        db.session.add(MatchRound(
            match_id=match.id,
            round_index=idx,
            cluster_code=code,
            target_color_id=tc.id,
        ))
    return match


def _rounds_of(match: Match):
    return (MatchRound.query
            .filter_by(match_id=match.id)
            .order_by(MatchRound.round_index.asc())
            .all())


def get_or_create_active_match(user_id: str) -> Match:
    """Resume the user's active match, or draw a new one. A stale match whose
    targets no longer exist (gamut reload) is abandoned and redrawn."""
    match = (Match.query
             .filter_by(user_id=user_id, status='active')
             .order_by(Match.started_at.desc())
             .first())
    if match is not None:
        rounds = _rounds_of(match)
        target_ids = [r.target_color_id for r in rounds]
        existing = {
            tc.id for tc in TargetColor.query
            .filter(TargetColor.id.in_(target_ids)).all()
        } if target_ids else set()
        if len(rounds) == match.round_count and all(t in existing for t in target_ids):
            return match
        match.status = 'abandoned'
    return create_match(user_id)


def match_payload(match: Match, public_dict) -> dict:
    """Client JSON for a match. *public_dict* is routes._target_color_public_dict
    (recipes stay withheld)."""
    names = cluster_display_names()
    rounds = _rounds_of(match)
    targets = {
        tc.id: tc for tc in TargetColor.query
        .filter(TargetColor.id.in_([r.target_color_id for r in rounds])).all()
    }
    out = []
    for r in rounds:
        if r.outcome is not None:
            state = r.outcome
        elif r.round_index == match.current_round and match.status == 'active':
            state = 'current'
        else:
            state = 'pending'
        tc = targets.get(r.target_color_id)
        out.append({
            'round_index': r.round_index,
            'cluster_code': r.cluster_code,
            'cluster_name': names.get(r.cluster_code, r.cluster_code),
            'state': state,
            'target': public_dict(tc) if tc is not None else None,
        })
    return {
        'match_id': match.id,
        'status': match.status,
        'round_count': match.round_count,
        'current_round': match.current_round,
        'rounds': out,
    }


def _advance(match: Match, rnd: MatchRound, outcome: str,
             attempt_uuid=None, mixing_session_id=None):
    rnd.outcome = outcome
    rnd.played_at = datetime.utcnow()
    rnd.attempt_uuid = attempt_uuid
    rnd.mixing_session_id = mixing_session_id
    match.current_round = rnd.round_index + 1
    completed = match.current_round >= match.round_count
    if completed:
        match.status = 'completed'
        match.completed_at = datetime.utcnow()
    return {
        'match_id': match.id,
        'round_index': rnd.round_index,
        'current_round': match.current_round,
        'match_completed': completed,
        'summary': match_summary(match) if completed else None,
    }


def _validated_current_round(user_id, match_id, round_index):
    """The (match, round) pair iff it is the user's active match's current
    unplayed round; None otherwise (never raises into the save path)."""
    try:
        match_id = int(match_id)
        round_index = int(round_index)
    except (TypeError, ValueError):
        return None
    match = Match.query.get(match_id)
    if match is None or match.user_id != user_id or match.status != 'active':
        return None
    if round_index != match.current_round:
        return None
    rnd = MatchRound.query.filter_by(match_id=match.id, round_index=round_index).first()
    if rnd is None or rnd.outcome is not None:
        return None
    return match, rnd


def record_round_result(user_id, match_id, round_index, attempt_uuid,
                        mixing_session, skipped: bool):
    """Bind a saved round to its match round and advance. Returns the
    match-state dict for the save response, or None when the payload carried
    no/invalid match fields (the save itself is unaffected)."""
    if match_id is None or round_index is None:
        return None
    pair = _validated_current_round(user_id, match_id, round_index)
    if pair is None:
        return None
    match, rnd = pair
    # The saved round must be about the drawn target.
    if mixing_session is not None and mixing_session.target_color_id is not None \
            and mixing_session.target_color_id != rnd.target_color_id:
        return None
    return _advance(
        match, rnd,
        outcome='skipped' if skipped else 'completed',
        attempt_uuid=attempt_uuid,
        mixing_session_id=mixing_session.id if mixing_session is not None else None,
    )


def skip_round_unmixed(user_id, match_id, round_index):
    """Advance past a round the player skipped without mixing anything
    (no MixingSession row exists for it)."""
    pair = _validated_current_round(user_id, match_id, round_index)
    if pair is None:
        return None
    match, rnd = pair
    return _advance(match, rnd, outcome='skipped')


def match_summary(match: Match) -> dict:
    """Per-round outcomes joined to their mixing sessions (ΔE, category)."""
    names = cluster_display_names()
    rounds = _rounds_of(match)
    session_ids = [r.mixing_session_id for r in rounds if r.mixing_session_id]
    sessions = {
        s.id: s for s in MixingSession.query
        .filter(MixingSession.id.in_(session_ids)).all()
    } if session_ids else {}
    targets = {
        tc.id: tc for tc in TargetColor.query
        .filter(TargetColor.id.in_([r.target_color_id for r in rounds])).all()
    }
    out = []
    delta_es = []
    completed = skipped = 0
    for r in rounds:
        s = sessions.get(r.mixing_session_id)
        tc = targets.get(r.target_color_id)
        de = float(s.delta_e) if s is not None and s.delta_e is not None else None
        if r.outcome == 'completed':
            completed += 1
        elif r.outcome == 'skipped':
            skipped += 1
        if de is not None:
            delta_es.append(de)
        out.append({
            'round_index': r.round_index,
            'cluster_code': r.cluster_code,
            'cluster_name': names.get(r.cluster_code, r.cluster_code),
            'target_name': (tc.name_hu or tc.name) if tc is not None else None,
            'target_rgb': [tc.r, tc.g, tc.b] if tc is not None else None,
            'outcome': r.outcome,
            'delta_e': de,
            'match_category': s.match_category if s is not None else None,
        })
    return {
        'match_id': match.id,
        'rounds': out,
        'completed_rounds': completed,
        'skipped_rounds': skipped,
        'mean_delta_e': (sum(delta_es) / len(delta_es)) if delta_es else None,
        'best_delta_e': min(delta_es) if delta_es else None,
    }


def matches_completed_count(user_id: str) -> int:
    return Match.query.filter_by(user_id=user_id, status='completed').count()
