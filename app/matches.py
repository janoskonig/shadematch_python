"""Match service: 10-round matches, one target per frozen colour cluster.

Design (study protocol):
  * A match = 10 rounds, exactly one target from each of the 10 FROZEN
    clusters (app/clusters.py match_cluster_*, artifact
    data/match_clusters_<version>.json). Cluster membership is fixed and
    versioned for the whole study period; each match records the version it
    was drawn under (Match.clusters_fingerprint).
  * Xiao skin-zone targets are excluded from matches entirely — the clusters
    partition the even-coverage background gamut only.
  * Cluster ORDER is shuffled uniformly per match. The target WITHIN a
    cluster is random but drawn from a per-participant no-repeat cycle:
    among the cluster's members, only those the participant has been
    ASSIGNED least often are eligible, so a colour cannot repeat for a
    participant until its whole cluster is exhausted. The draw depends only
    on assignment history, never on performance, so a new match's
    composition is independent of how the previous one went.
  * Every assigned round is stored (MatchRound rows exist from the draw),
    whether or not it was ever started; skips and match abandonment are
    recorded as distinct outcomes ('skipped' / 'abandoned').
  * Abandonment rule: an active match not continued for ABANDON_AFTER_DAYS
    (last activity = match start or latest played round) is marked
    'abandoned' and its unplayed rounds get outcome='abandoned', so no match
    stays statistically undecided forever. Applied lazily when the player
    next asks for a match, and sweepable via scripts/mark_abandoned_matches.py.
  * Primary estimand (declared in the protocol): the EQUAL-WEIGHT average of
    the per-cluster expected outcomes over the 10 frozen clusters —
    (1/10) * sum_c E(Y | cluster = c) — i.e. every completed match is one
    complete, blocked, ten-cluster repeated measurement. Daily-challenge,
    head-to-head and probe rounds are non-randomised channels: they carry no
    match fields and are analysed separately.

Round accounting is server-authoritative: the save endpoints (and the
unmixed-skip endpoint) advance current_round; saves without match fields
never touch a match. All functions add to db.session without committing
(caller commits), matching the probe.py convention.
"""
import random
from datetime import datetime, timedelta

from sqlalchemy import func

from . import db
from .models import Match, MatchRound, MixingSession, TargetColor
from .clusters import (
    MATCH_CLUSTERS_VERSION, MATCH_CLUSTER_ORDER,
    match_cluster_assignments, match_cluster_names,
)
from .gamification import target_color_sum_drop

ROUNDS_PER_MATCH = 10
ABANDON_AFTER_DAYS = 3

# Outcomes that count as "made it": a perfect finish, or a skip the player
# judged identical/acceptable. Big-difference skips and legacy stops do not.
# Lives here (not routes.py) so match_summary can flag challengeable rounds
# without a circular import; routes.py imports it from here.
COMPLETED_MATCH_CATEGORIES = (
    'perfect',
    'no_perceivable_difference',
    'acceptable_difference',
)


def _drawable_targets():
    """Frozen-cluster members present in the catalog with a full recipe."""
    assign = match_cluster_assignments()
    return [
        tc for tc in TargetColor.query.filter_by(color_type='gamut').all()
        if tc.id in assign and target_color_sum_drop(tc) is not None
    ]


def _assignment_counts(user_id: str) -> dict:
    """How many times each target has ever been ASSIGNED to this user in a
    match (played or not) — the basis of the no-repeat cycle."""
    rows = (db.session.query(MatchRound.target_color_id, func.count(MatchRound.id))
            .join(Match, Match.id == MatchRound.match_id)
            .filter(Match.user_id == user_id)
            .group_by(MatchRound.target_color_id)
            .all())
    return {tid: int(n) for tid, n in rows}


def create_match(user_id: str) -> Match:
    """Draw a fresh match: shuffled cluster order; within each cluster a
    uniform draw among the participant's least-assigned members (no-repeat
    cycle until the cluster is exhausted)."""
    pool = _drawable_targets()
    assign = match_cluster_assignments()
    by_cluster = {code: [] for code in MATCH_CLUSTER_ORDER}
    for tc in pool:
        code = assign.get(tc.id)
        if code in by_cluster:
            by_cluster[code].append(tc)
    counts = _assignment_counts(user_id)

    order = list(MATCH_CLUSTER_ORDER)
    random.shuffle(order)

    match = Match(
        user_id=user_id,
        status='active',
        current_round=0,
        round_count=ROUNDS_PER_MATCH,
        clusters_fingerprint=MATCH_CLUSTERS_VERSION,
    )
    db.session.add(match)
    db.session.flush()  # match.id for the rounds

    for idx, code in enumerate(order):
        members = by_cluster.get(code) or pool  # defensive: empty cluster → whole pool
        low = min(counts.get(tc.id, 0) for tc in members)
        cycle_pool = [tc for tc in members if counts.get(tc.id, 0) == low]
        tc = random.choice(cycle_pool)
        counts[tc.id] = counts.get(tc.id, 0) + 1
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


def _last_activity(match: Match, rounds) -> datetime:
    played = [r.played_at for r in rounds if r.played_at is not None]
    return max([match.started_at] + played)


def abandon_match(match: Match, rounds=None) -> None:
    """Mark a match abandoned; unplayed rounds get a definite outcome so no
    assigned round stays undecided."""
    match.status = 'abandoned'
    for r in (rounds if rounds is not None else _rounds_of(match)):
        if r.outcome is None:
            r.outcome = 'abandoned'


def abandon_stale_matches(now=None) -> int:
    """Sweep: abandon every active match idle for > ABANDON_AFTER_DAYS.
    Used by scripts/mark_abandoned_matches.py (cron-able); the same rule is
    applied lazily in get_or_create_active_match."""
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=ABANDON_AFTER_DAYS)
    n = 0
    for match in Match.query.filter_by(status='active').all():
        rounds = _rounds_of(match)
        if _last_activity(match, rounds) < cutoff:
            abandon_match(match, rounds)
            n += 1
    return n


def get_or_create_active_match(user_id: str) -> Match:
    """Resume the user's active match, or draw a new one. Every active match
    is inspected (duplicates can only arise defensively — the DB enforces one
    active per user on Postgres): stale ones (> ABANDON_AFTER_DAYS idle) and
    ones whose targets no longer exist (gamut reload) are abandoned; the
    newest healthy one is resumed, extras are abandoned."""
    actives = (Match.query
               .filter_by(user_id=user_id, status='active')
               .order_by(Match.started_at.desc())
               .all())
    cutoff = datetime.utcnow() - timedelta(days=ABANDON_AFTER_DAYS)
    resumable = None
    for match in actives:
        rounds = _rounds_of(match)
        if resumable is not None or _last_activity(match, rounds) < cutoff:
            abandon_match(match, rounds)
            continue
        target_ids = [r.target_color_id for r in rounds]
        existing = {
            tc.id for tc in TargetColor.query
            .filter(TargetColor.id.in_(target_ids)).all()
        } if target_ids else set()
        if len(rounds) == match.round_count and all(t in existing for t in target_ids):
            resumable = match
        else:
            abandon_match(match, rounds)
    return resumable if resumable is not None else create_match(user_id)


def match_payload(match: Match, public_dict) -> dict:
    """Client JSON for a match. *public_dict* is routes._target_color_public_dict
    (recipes stay withheld)."""
    names = match_cluster_names()
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
    match = db.session.get(Match, match_id)
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
    names = match_cluster_names()
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
        drops = (sum(int(v or 0) for v in (
            s.drop_white, s.drop_black, s.drop_red, s.drop_yellow, s.drop_blue,
        )) if s is not None else None)
        out.append({
            'round_index': r.round_index,
            'cluster_code': r.cluster_code,
            'cluster_name': names.get(r.cluster_code, r.cluster_code),
            'target_name': (tc.name_hu or tc.name) if tc is not None else None,
            'target_rgb': [tc.r, tc.g, tc.b] if tc is not None else None,
            'outcome': r.outcome,
            'delta_e': de,
            'match_category': s.match_category if s is not None else None,
            # For the summary's "challenge a friend" button: which round can be
            # minted into a challenge, and what it would carry. Mirrors the
            # /api/challenge/create gate so the client never offers a dead link.
            'attempt_uuid': s.attempt_uuid if s is not None else None,
            'drops': drops,
            'time_sec': (float(s.time_sec) if s is not None and s.time_sec is not None else None),
            'challengeable': (s is not None and s.attempt_uuid is not None
                              and s.match_category in COMPLETED_MATCH_CATEGORIES),
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


def active_match_snapshot(user_id: str):
    """Read-only view of the user's resumable active match for reminder copy
    (push/email): respects the staleness rule but never mutates. Returns
    {'round_no' (1-based), 'round_count', 'target_name', 'target_rgb'} or None."""
    match = (Match.query
             .filter_by(user_id=user_id, status='active')
             .order_by(Match.started_at.desc())
             .first())
    if match is None:
        return None
    rounds = _rounds_of(match)
    if _last_activity(match, rounds) < datetime.utcnow() - timedelta(days=ABANDON_AFTER_DAYS):
        return None  # will be abandoned at the next open — advertise a fresh match instead
    rnd = next((r for r in rounds if r.round_index == match.current_round), None)
    tc = db.session.get(TargetColor, rnd.target_color_id) if rnd is not None else None
    return {
        'match_id': match.id,
        'round_no': match.current_round + 1,
        'round_count': match.round_count,
        'target_name': (tc.name_hu or tc.name) if tc is not None else None,
        'target_rgb': [tc.r, tc.g, tc.b] if tc is not None else None,
    }


def match_history(user_id: str, limit: int = 20) -> list:
    """The user's recent matches (newest first) with their round summaries —
    the results page's match ledger."""
    rows = (Match.query
            .filter_by(user_id=user_id)
            .order_by(Match.started_at.desc())
            .limit(limit)
            .all())
    out = []
    for match in rows:
        s = match_summary(match)
        out.append({
            'match_id': match.id,
            'status': match.status,
            'current_round': match.current_round,
            'round_count': match.round_count,
            'started_at': match.started_at.isoformat() if match.started_at else None,
            'completed_at': match.completed_at.isoformat() if match.completed_at else None,
            'rounds': s['rounds'],
            'completed_rounds': s['completed_rounds'],
            'skipped_rounds': s['skipped_rounds'],
            'mean_delta_e': s['mean_delta_e'],
            'best_delta_e': s['best_delta_e'],
        })
    return out
