from flask import Blueprint, render_template, request, jsonify, send_from_directory
from datetime import datetime, date, timedelta
import hashlib
import random as _random
from . import db
from .models import (
    User, MixingSession, TargetColor,
    UserProgress, UserTargetColorStats, UserAward,
    DailyChallengeRun, DailyChallengeWinner, PushSubscription,
    AnalyticsEvent, MixingAttempt, MixingAttemptEvent,
)
import string
from .utils import calculate_delta_e, spectrum_to_xyz, xyz_to_rgb, reverse_engineer_recipe
import pandas as pd
import os
import numpy as np
import json
from sqlalchemy import func

from .gamification import (
    process_progression,
    build_progress_response,
    get_quota_ordered_catalog,
    grant_daily_champion,
    get_user_profile,
    compute_quota_progress,
    compute_level_from_quota,
    COVERAGE_QUOTA,
    STREAK_FREEZE_CAP,
)
from .next_action import build_next_action

main = Blueprint('main', __name__)

MATCH_PERFECT_DELTA_E = 0.01


def derive_match_category(delta_e, skipped, skip_perception=None):
    """
    Non-null category for each row:
    perfect | no_perceivable_difference | acceptable_difference | big_difference | stopped
    """
    if delta_e is None:
        de_not_perfect = True
    else:
        try:
            de_not_perfect = float(delta_e) > MATCH_PERFECT_DELTA_E
        except (TypeError, ValueError):
            de_not_perfect = True

    if not de_not_perfect:
        return 'perfect'

    if not skipped:
        return 'stopped'

    if skip_perception == 'identical':
        return 'no_perceivable_difference'
    if skip_perception == 'acceptable':
        return 'acceptable_difference'
    if skip_perception == 'unacceptable':
        return 'big_difference'

    return 'stopped'


def generate_user_id():
    return ''.join(_random.choices(string.ascii_uppercase + string.digits, k=6))


def refresh_db_connection():
    try:
        db.session.execute(db.text('SELECT 1'))
        return True
    except Exception as e:
        print(f'⚠️ DB connection stale, refreshing… Error: {e}')
        try:
            db.engine.dispose()
            return True
        except Exception as refresh_error:
            print(f'❌ Failed to refresh DB: {refresh_error}')
            return False


def _catalog_size():
    return TargetColor.query.count()


# ── Pages ──────────────────────────────────────────────────────────────────

@main.route('/')
def index():
    return render_template('index.html')


@main.route('/results')
def results_page():
    return render_template('results.html')


@main.route('/spectral')
def spectral():
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    pigments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'pigments')
    spectrum_plots = {}

    for filename in os.listdir(pigments_dir):
        if filename.endswith(('.csv', '.txt')):
            file_path = os.path.join(pigments_dir, filename)
            try:
                if filename.endswith('.csv'):
                    df = pd.read_csv(file_path)
                    pigment_wavelengths = df['Wavelength'].tolist()
                    reflectances = df.iloc[:, 1:].mean(axis=1).tolist()
                else:
                    data = []
                    with open(file_path, 'r') as f:
                        for line in f:
                            parts = line.strip().split()
                            if len(parts) == 2:
                                try:
                                    data.append([float(parts[0]), float(parts[1]) / 100.0])
                                except ValueError:
                                    continue
                    if not data:
                        continue
                    data = sorted(data, key=lambda x: x[0])
                    pigment_wavelengths = [r[0] for r in data]
                    reflectances = [r[1] for r in data]

                X, Y, Z = spectrum_to_xyz(reflectances, pigment_wavelengths, x_bar, y_bar, z_bar)
                rgb = xyz_to_rgb(X, Y, Z)
                color_key = os.path.splitext(filename)[0].lower()
                spectrum_plots[color_key] = {
                    'wavelengths': pigment_wavelengths,
                    'reflectances': reflectances,
                    'rgb': rgb.tolist(),
                    'name': os.path.splitext(filename)[0].replace('_', ' ').title(),
                }
            except Exception as e:
                print(f"Error loading {filename}: {e}")

    return render_template('spectral_mixer.html', spectrum_plots=spectrum_plots)


# ── Auth ───────────────────────────────────────────────────────────────────

@main.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    birthdate = datetime.strptime(data['birthdate'], '%Y-%m-%d').date()
    gender = data['gender']

    if birthdate.year >= 2015:
        return jsonify({'status': 'error', 'message': 'You must be born before 2015 to participate.'}), 400

    user_id = generate_user_id()
    while User.query.get(user_id) is not None:
        user_id = generate_user_id()

    user = User(id=user_id, birthdate=birthdate, gender=gender)
    db.session.add(user)
    db.session.commit()

    return jsonify({'status': 'success', 'userId': user_id})


@main.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user_id = data['userId']
    try:
        user = User.query.get(user_id)
        if user:
            return jsonify({'status': 'success', 'birthdate': user.birthdate.isoformat(), 'gender': user.gender})

        session = MixingSession.query.filter_by(user_id=user_id).first()
        if session:
            return jsonify({'status': 'success', 'birthdate': '2000-01-01', 'gender': 'male'})

        return jsonify({'status': 'error', 'message': 'Invalid user ID'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500


# ── Target colors ──────────────────────────────────────────────────────────

@main.route('/api/target-colors', methods=['GET'])
def get_target_colors():
    user_id = request.args.get('user_id')
    rows = TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
    colors = [
        {
            'id': tc.id,
            'name': tc.name,
            'type': tc.color_type,
            'classification': tc.classification,
            'rgb': [tc.r, tc.g, tc.b],
            'frequency': tc.frequency,
            'catalog_order': tc.catalog_order,
            'level_required': tc.level_required,
        }
        for tc in rows
    ]

    next_action_data = {}
    if user_id:
        colors = get_quota_ordered_catalog(user_id, colors)
        next_action_data = build_next_action(user_id)

    return jsonify({'status': 'success', 'colors': colors, **next_action_data})


# ── Calculate ──────────────────────────────────────────────────────────────

@main.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()
    delta_e = calculate_delta_e(data['target'], data['mixed'])
    return jsonify({'delta_e': delta_e})


MIXING_EVENT_TYPES = frozenset({
    'action_add',
    'action_remove',
    'boundary_start',
    'boundary_target_shown',
    'boundary_save',
    'boundary_skip',
    'boundary_reset',
    'boundary_restart',
    'boundary_next_target',
})

MIXING_END_REASONS = frozenset({
    'saved_match',
    'saved_stop',
    'skipped',
    'reset',
    'restart',
    'abandoned',
})

PALETTE_COLORS = ('white', 'black', 'red', 'yellow', 'blue')


def _utcnow():
    return datetime.utcnow()


def _coerce_int_or_none(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _state_from_gameplay_payload(data):
    return {
        'drops': {
            'white': int(data.get('drop_white', 0) or 0),
            'black': int(data.get('drop_black', 0) or 0),
            'red': int(data.get('drop_red', 0) or 0),
            'yellow': int(data.get('drop_yellow', 0) or 0),
            'blue': int(data.get('drop_blue', 0) or 0),
        },
        'mixed_rgb': [255, 255, 255],  # gameplay payload does not currently send final mixed RGB
        'delta_e': data.get('delta_e'),
        'timer_sec': data.get('time_sec', 0),
    }


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return False, 'snapshot must be an object'

    drops = snapshot.get('drops')
    mixed_rgb = snapshot.get('mixed_rgb')

    if not isinstance(drops, dict):
        return False, 'snapshot.drops is required'
    for color in PALETTE_COLORS:
        if color not in drops:
            return False, f'snapshot.drops.{color} is required'
        if not isinstance(drops[color], int):
            return False, f'snapshot.drops.{color} must be int'

    if not (isinstance(mixed_rgb, list) and len(mixed_rgb) == 3 and all(isinstance(v, int) for v in mixed_rgb)):
        return False, 'snapshot.mixed_rgb must be [r,g,b] ints'

    if 'delta_e' not in snapshot:
        return False, 'snapshot.delta_e key is required'
    delta_e = snapshot.get('delta_e')
    if delta_e is not None and not isinstance(delta_e, (int, float)):
        return False, 'snapshot.delta_e must be number or null'

    timer_sec = snapshot.get('timer_sec')
    if not isinstance(timer_sec, (int, float)):
        return False, 'snapshot.timer_sec must be number'

    return True, None


def _canonical_event_payload(event_like):
    return json.dumps(
        {
            'attempt_uuid': event_like['attempt_uuid'],
            'seq': int(event_like['seq']),
            'event_type': event_like['event_type'],
            'action_color': event_like.get('action_color'),
            'client_ts_ms': int(event_like['client_ts_ms']),
            'state_before_json': event_like['state_before_json'],
            'state_after_json': event_like['state_after_json'],
            'metadata_json': event_like.get('metadata_json'),
        },
        sort_keys=True,
        separators=(',', ':'),
    )


def _normalize_event_payload(raw, attempt_uuid_default=None):
    if not isinstance(raw, dict):
        raise ValueError('event must be an object')

    attempt_uuid = raw.get('attempt_uuid') or attempt_uuid_default
    if not attempt_uuid:
        raise ValueError('attempt_uuid is required')

    seq = raw.get('seq')
    if not isinstance(seq, int) or seq <= 0:
        raise ValueError('seq must be a positive integer')

    event_type = raw.get('event_type')
    if event_type not in MIXING_EVENT_TYPES:
        raise ValueError(f'invalid event_type: {event_type}')

    client_ts_ms = raw.get('client_ts_ms')
    if not isinstance(client_ts_ms, int):
        raise ValueError('client_ts_ms must be int')

    state_before = raw.get('state_before_json')
    state_after = raw.get('state_after_json')
    ok_before, err_before = _validate_snapshot(state_before)
    if not ok_before:
        raise ValueError(f'state_before_json invalid: {err_before}')
    ok_after, err_after = _validate_snapshot(state_after)
    if not ok_after:
        raise ValueError(f'state_after_json invalid: {err_after}')

    action_color = raw.get('action_color')
    if action_color is not None and action_color not in PALETTE_COLORS:
        raise ValueError('action_color must be one of white|black|red|yellow|blue or null')

    metadata_json = raw.get('metadata_json')
    if metadata_json is not None and not isinstance(metadata_json, dict):
        raise ValueError('metadata_json must be an object when provided')

    return {
        'attempt_uuid': attempt_uuid,
        'seq': seq,
        'event_type': event_type,
        'action_color': action_color,
        'client_ts_ms': client_ts_ms,
        'state_before_json': state_before,
        'state_after_json': state_after,
        'metadata_json': metadata_json,
    }


def _upsert_attempt_header(payload):
    attempt_uuid = payload.get('attempt_uuid')
    if not attempt_uuid:
        raise ValueError('attempt_uuid is required')

    row = MixingAttempt.query.get(attempt_uuid)
    created = False
    if row is None:
        row = MixingAttempt(attempt_uuid=attempt_uuid, attempt_started_server_ts=_utcnow())
        db.session.add(row)
        created = True

    # Start context / immutable-ish fields are only set if empty
    for attr, key in (
        ('user_id', 'user_id'),
        ('target_color_id', 'target_color_id'),
        ('target_r', 'target_r'),
        ('target_g', 'target_g'),
        ('target_b', 'target_b'),
        ('attempt_started_client_ts_ms', 'attempt_started_client_ts_ms'),
        ('initial_delta_e', 'initial_delta_e'),
        ('app_version', 'app_version'),
    ):
        val = payload.get(key)
        if val is not None and getattr(row, attr) is None:
            setattr(row, attr, val)

    # Initial drops/rgb should reflect baseline state; keep first-write-wins.
    if created:
        row.initial_drop_white = int(payload.get('initial_drop_white', 0) or 0)
        row.initial_drop_black = int(payload.get('initial_drop_black', 0) or 0)
        row.initial_drop_red = int(payload.get('initial_drop_red', 0) or 0)
        row.initial_drop_yellow = int(payload.get('initial_drop_yellow', 0) or 0)
        row.initial_drop_blue = int(payload.get('initial_drop_blue', 0) or 0)
        row.initial_mixed_r = int(payload.get('initial_mixed_r', 255) or 255)
        row.initial_mixed_g = int(payload.get('initial_mixed_g', 255) or 255)
        row.initial_mixed_b = int(payload.get('initial_mixed_b', 255) or 255)

    # first action: earliest client ts + first server ts latch
    first_action_client = _coerce_int_or_none(payload.get('first_action_client_ts_ms'))
    if first_action_client is not None:
        if row.first_action_client_ts_ms is None or first_action_client < row.first_action_client_ts_ms:
            row.first_action_client_ts_ms = first_action_client
            if row.first_action_server_ts is None:
                row.first_action_server_ts = _utcnow()

    end_reason = payload.get('end_reason')
    if end_reason is not None:
        if end_reason not in MIXING_END_REASONS:
            raise ValueError(f'invalid end_reason: {end_reason}')
        if row.end_reason is None:
            row.end_reason = end_reason
        ended_client = _coerce_int_or_none(payload.get('attempt_ended_client_ts_ms'))
        if ended_client is not None and row.attempt_ended_client_ts_ms is None:
            row.attempt_ended_client_ts_ms = ended_client
        if row.attempt_ended_server_ts is None:
            row.attempt_ended_server_ts = _utcnow()

    return row


def _ingest_mixing_events(attempt_uuid, raw_events):
    if not isinstance(raw_events, list):
        raise ValueError('events must be an array')
    if len(raw_events) == 0:
        return {'inserted': 0, 'duplicates': 0}

    normalized = [_normalize_event_payload(e, attempt_uuid_default=attempt_uuid) for e in raw_events]
    seqs = [e['seq'] for e in normalized]

    if seqs != sorted(seqs):
        raise ValueError('events must be sorted by seq ascending')
    if len(set(seqs)) != len(seqs):
        raise ValueError('duplicate seq values in payload are not allowed')

    existing_rows = (
        MixingAttemptEvent.query
        .filter(
            MixingAttemptEvent.attempt_uuid == attempt_uuid,
            MixingAttemptEvent.seq.in_(seqs),
        )
        .all()
    )
    existing_by_seq = {r.seq: r for r in existing_rows}

    existing_max_seq = (
        db.session.query(func.max(MixingAttemptEvent.seq))
        .filter(MixingAttemptEvent.attempt_uuid == attempt_uuid)
        .scalar()
    ) or 0

    next_expected_new_seq = existing_max_seq + 1
    to_insert = []
    duplicates = 0

    for ev in normalized:
        current_seq = ev['seq']
        existing = existing_by_seq.get(current_seq)
        if existing is not None:
            existing_payload = _canonical_event_payload({
                'attempt_uuid': existing.attempt_uuid,
                'seq': existing.seq,
                'event_type': existing.event_type,
                'action_color': existing.action_color,
                'client_ts_ms': existing.client_ts_ms,
                'state_before_json': existing.state_before_json,
                'state_after_json': existing.state_after_json,
                'metadata_json': existing.metadata_json,
            })
            incoming_payload = _canonical_event_payload(ev)
            if existing_payload != incoming_payload:
                raise ValueError(f'conflicting duplicate for seq={current_seq}')
            duplicates += 1
            continue

        # Disallow introducing non-idempotent past writes.
        if current_seq <= existing_max_seq:
            raise ValueError(f'out-of-order seq={current_seq}; existing_max_seq={existing_max_seq}')

        # Disallow gaps in new sequence insertion.
        if current_seq != next_expected_new_seq:
            raise ValueError(f'seq gap at seq={current_seq}; expected={next_expected_new_seq}')

        to_insert.append(MixingAttemptEvent(**ev))
        next_expected_new_seq += 1

    if to_insert:
        db.session.add_all(to_insert)

    return {'inserted': len(to_insert), 'duplicates': duplicates}


def _ensure_terminal_telemetry_from_gameplay(data, end_reason):
    attempt_uuid = data.get('attempt_uuid')
    if not attempt_uuid:
        return

    header_payload = {
        'attempt_uuid': attempt_uuid,
        'user_id': data.get('user_id'),
        'target_color_id': data.get('target_color_id'),
        'target_r': data.get('target_r'),
        'target_g': data.get('target_g'),
        'target_b': data.get('target_b'),
        'attempt_ended_client_ts_ms': _coerce_int_or_none(data.get('attempt_ended_client_ts_ms')),
        'end_reason': end_reason,
    }
    _upsert_attempt_header(header_payload)

    boundary_type = 'boundary_save' if end_reason in ('saved_match', 'saved_stop') else 'boundary_skip'
    existing_terminal = (
        MixingAttemptEvent.query
        .filter_by(attempt_uuid=attempt_uuid, event_type=boundary_type)
        .first()
    )
    if existing_terminal:
        return

    max_seq = (
        db.session.query(func.max(MixingAttemptEvent.seq))
        .filter(MixingAttemptEvent.attempt_uuid == attempt_uuid)
        .scalar()
    ) or 0
    state = _state_from_gameplay_payload(data)
    synthetic_event = MixingAttemptEvent(
        attempt_uuid=attempt_uuid,
        seq=max_seq + 1,
        event_type=boundary_type,
        action_color=None,
        client_ts_ms=_coerce_int_or_none(data.get('attempt_ended_client_ts_ms')) or 0,
        state_before_json=state,
        state_after_json=state,
        metadata_json={'source': 'server_reconciliation'},
    )
    db.session.add(synthetic_event)


@main.route('/api/mixing-attempt/start-or-update', methods=['POST'])
def mixing_attempt_start_or_update():
    try:
        data = request.get_json(silent=True) or {}
        _upsert_attempt_header(data)
        db.session.commit()
        return jsonify({'status': 'success'})
    except ValueError as ve:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/api/mixing-attempt/events', methods=['POST'])
def mixing_attempt_events():
    try:
        data = request.get_json(silent=True) or {}
        attempt_uuid = data.get('attempt_uuid')
        if not attempt_uuid:
            return jsonify({'status': 'error', 'message': 'attempt_uuid required'}), 400

        exists = MixingAttempt.query.get(attempt_uuid)
        if not exists:
            return jsonify({'status': 'error', 'message': 'unknown attempt_uuid'}), 404

        result = _ingest_mixing_events(attempt_uuid, data.get('events'))
        db.session.commit()
        return jsonify({'status': 'success', **result})
    except ValueError as ve:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/api/mixing-attempt/ingest', methods=['POST'])
def mixing_attempt_ingest():
    try:
        data = request.get_json(silent=True) or {}
        header = data.get('attempt') or {}
        events = data.get('events') or []

        attempt_uuid = header.get('attempt_uuid') or data.get('attempt_uuid')
        if not attempt_uuid:
            return jsonify({'status': 'error', 'message': 'attempt_uuid required'}), 400

        header['attempt_uuid'] = attempt_uuid
        _upsert_attempt_header(header)
        result = _ingest_mixing_events(attempt_uuid, events)
        db.session.commit()
        return jsonify({'status': 'success', **result})
    except ValueError as ve:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(ve)}), 400
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Save session (completed attempt) ──────────────────────────────────────

@main.route('/save_session', methods=['POST'])
def save_session():
    data = request.get_json()

    if not refresh_db_connection():
        return jsonify({'status': 'error', 'error': 'Database connection failed'}), 500

    try:
        user_id = data['user_id']
        attempt_uuid = data.get('attempt_uuid')

        # Idempotency: if this uuid was already persisted, return current progress
        if attempt_uuid:
            existing = MixingSession.query.filter_by(attempt_uuid=attempt_uuid).first()
            if existing:
                up = UserProgress.query.filter_by(user_id=user_id).first()
                return jsonify({
                    'status': 'success',
                    'duplicate': True,
                    'progress': build_progress_response(user_id, up),
                    'new_awards': [],
                    'xp_earned': 0,
                })

        skipped = data.get('skipped', False)
        mc = derive_match_category(data.get('delta_e'), skipped, skip_perception=None)

        session = MixingSession(
            attempt_uuid=attempt_uuid,
            user_id=user_id,
            target_color_id=data.get('target_color_id'),
            target_r=data['target_r'], target_g=data['target_g'], target_b=data['target_b'],
            drop_white=data['drop_white'], drop_black=data['drop_black'],
            drop_red=data['drop_red'], drop_yellow=data['drop_yellow'], drop_blue=data['drop_blue'],
            delta_e=data['delta_e'],
            time_sec=data['time_sec'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            skipped=skipped,
            match_category=mc,
        )
        db.session.add(session)

        xp_earned, new_awards, streak_event, level_up = process_progression(
            user_id=user_id,
            match_category=mc,
            skipped=skipped,
            target_color_id=data.get('target_color_id'),
            delta_e=data.get('delta_e'),
        )

        try:
            delta_for_reason = float(data.get('delta_e'))
        except (TypeError, ValueError):
            delta_for_reason = None
        end_reason = 'saved_match' if (delta_for_reason is not None and delta_for_reason <= MATCH_PERFECT_DELTA_E) else 'saved_stop'
        _ensure_terminal_telemetry_from_gameplay(data, end_reason=end_reason)

        db.session.commit()

        up = UserProgress.query.filter_by(user_id=user_id).first()
        return jsonify({
            'status': 'success',
            'xp_earned': xp_earned,
            'new_awards': new_awards,
            'streak_event': streak_event,
            'level_up': level_up,
            'progress': build_progress_response(user_id, up),
            **build_next_action(user_id),
        })

    except Exception as e:
        print('Error saving session:', str(e))
        db.session.rollback()
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ── Save skip ──────────────────────────────────────────────────────────────

@main.route('/save_skip', methods=['POST'])
def save_skip():
    data = request.get_json()

    if not refresh_db_connection():
        return jsonify({'status': 'error', 'error': 'Database connection failed'}), 500

    try:
        user_id = data['user_id']
        attempt_uuid = data.get('attempt_uuid')

        if attempt_uuid:
            existing = MixingSession.query.filter_by(attempt_uuid=attempt_uuid).first()
            if existing:
                up = UserProgress.query.filter_by(user_id=user_id).first()
                return jsonify({
                    'status': 'success',
                    'duplicate': True,
                    'progress': build_progress_response(user_id, up),
                    'new_awards': [],
                    'xp_earned': 0,
                })

        delta_e = data.get('delta_e')
        allowed_skip = {'identical', 'acceptable', 'unacceptable'}
        raw_perception = data.get('skip_perception')
        skip_perception = raw_perception if raw_perception in allowed_skip else None

        mc = derive_match_category(delta_e, True, skip_perception=skip_perception)

        session = MixingSession(
            attempt_uuid=attempt_uuid,
            user_id=user_id,
            target_color_id=data.get('target_color_id'),
            target_r=data['target_r'], target_g=data['target_g'], target_b=data['target_b'],
            drop_white=data.get('drop_white', 0), drop_black=data.get('drop_black', 0),
            drop_red=data.get('drop_red', 0), drop_yellow=data.get('drop_yellow', 0),
            drop_blue=data.get('drop_blue', 0),
            delta_e=delta_e,
            time_sec=data['time_sec'],
            timestamp=datetime.fromisoformat(data['timestamp']),
            skipped=True,
            skip_perception=skip_perception,
            match_category=mc,
        )
        db.session.add(session)

        xp_earned, new_awards, streak_event, level_up = process_progression(
            user_id=user_id,
            match_category=mc,
            skipped=True,
            target_color_id=data.get('target_color_id'),
            delta_e=delta_e,
        )

        _ensure_terminal_telemetry_from_gameplay(data, end_reason='skipped')

        db.session.commit()

        up = UserProgress.query.filter_by(user_id=user_id).first()
        return jsonify({
            'status': 'success',
            'xp_earned': xp_earned,
            'new_awards': new_awards,
            'streak_event': streak_event,
            'level_up': level_up,
            'progress': build_progress_response(user_id, up),
            **build_next_action(user_id),
        })

    except Exception as e:
        print('Error saving skip:', str(e))
        db.session.rollback()
        return jsonify({'status': 'error', 'error': str(e)}), 500


# ── User progress ──────────────────────────────────────────────────────────

@main.route('/api/user-progress', methods=['POST'])
def get_user_progress_route():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id required'}), 400
    try:
        up = UserProgress.query.filter_by(user_id=user_id).first()
        return jsonify({
            'status': 'success',
            'progress': build_progress_response(user_id, up),
            **build_next_action(user_id),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/api/user-profile', methods=['POST'])
def get_user_profile_route():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id required'}), 400
    try:
        progress, awards, color_stats = get_user_profile(user_id)
        return jsonify({
            'status': 'success',
            'progress': progress,
            'awards': awards,
            'color_stats': color_stats,
            'coverage_quota': COVERAGE_QUOTA,
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Results ────────────────────────────────────────────────────────────────

@main.route('/get_user_results', methods=['POST'])
def get_user_results():
    data = request.get_json()
    user_id = data.get('user_id')
    if not user_id:
        return jsonify({'status': 'error', 'message': 'User ID is required'}), 400

    try:
        sessions = (
            MixingSession.query
            .filter_by(user_id=user_id)
            .order_by(MixingSession.timestamp.desc())
            .all()
        )
        results = [
            {
                'id': s.id,
                'target_color_id': s.target_color_id,
                'target_color': f'RGB({s.target_r}, {s.target_g}, {s.target_b})',
                'drops': {
                    'white': s.drop_white, 'black': s.drop_black,
                    'red': s.drop_red, 'yellow': s.drop_yellow, 'blue': s.drop_blue,
                },
                'delta_e': s.delta_e if s.delta_e is not None else 'N/A',
                'time_sec': s.time_sec,
                'timestamp': s.timestamp.isoformat() if s.timestamp else None,
                'skipped': s.skipped,
                'skip_perception': s.skip_perception,
                'match_category': s.match_category,
            }
            for s in sessions
        ]
        return jsonify({'status': 'success', 'results': results, 'total_sessions': len(results)})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Daily challenge ────────────────────────────────────────────────────────

def _daily_seed(d=None):
    """Deterministic integer seed from a date string."""
    d = d or date.today()
    return int(hashlib.sha256(d.isoformat().encode()).hexdigest(), 16)


def _daily_target_ids(d=None):
    """Return a stable ordered list of target_color IDs for the day."""
    rows = TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
    sorted_basic = [r for r in rows if r.color_type == 'basic']
    sorted_skin = [r for r in rows if r.color_type == 'skin']

    seed = _daily_seed(d)
    rng = _random.Random(seed)

    first_three = sorted_basic[:3]
    remaining_basic = sorted_basic[3:11]
    selected_basic = rng.sample(remaining_basic, min(3, len(remaining_basic)))
    selected_skin = rng.sample(sorted_skin, min(5, len(sorted_skin)))

    return [c.id for c in first_three + selected_basic + selected_skin]


@main.route('/api/daily-challenge/today', methods=['GET'])
def daily_challenge_today():
    user_id = request.args.get('user_id')
    today = date.today()
    target_ids = _daily_target_ids(today)

    colors_by_id = {tc.id: tc for tc in TargetColor.query.filter(TargetColor.id.in_(target_ids)).all()}
    target_colors = [
        {
            'id': colors_by_id[cid].id,
            'name': colors_by_id[cid].name,
            'type': colors_by_id[cid].color_type,
            'rgb': [colors_by_id[cid].r, colors_by_id[cid].g, colors_by_id[cid].b],
        }
        for cid in target_ids if cid in colors_by_id
    ]

    already_submitted = False
    next_action_data = {}
    if user_id:
        final_run = (
            DailyChallengeRun.query
            .filter_by(user_id=user_id, challenge_date=today, is_final=True)
            .first()
        )
        already_submitted = final_run is not None
        next_action_data = build_next_action(user_id)

    return jsonify({
        'status': 'success',
        'challenge_date': today.isoformat(),
        'target_colors': target_colors,
        'already_submitted': already_submitted,
        **next_action_data,
    })


@main.route('/api/daily-challenge/submit', methods=['POST'])
def daily_challenge_submit():
    data = request.get_json()
    user_id = data.get('user_id')
    attempt_uuid = data.get('attempt_uuid')
    score_primary = data.get('score_primary')
    score_secondary = data.get('score_secondary')
    is_final = data.get('is_final', False)

    if not user_id or not attempt_uuid:
        return jsonify({'status': 'error', 'message': 'user_id and attempt_uuid required'}), 400

    today = date.today()

    try:
        existing = DailyChallengeRun.query.filter_by(attempt_uuid=attempt_uuid).first()
        if existing:
            return jsonify({'status': 'success', 'duplicate': True})

        # Enforce one-final-per-user-per-day
        if is_final:
            prior_final = DailyChallengeRun.query.filter_by(
                user_id=user_id, challenge_date=today, is_final=True
            ).first()
            if prior_final:
                is_final = False  # Downgrade to non-final; winner already set

        run = DailyChallengeRun(
            user_id=user_id,
            challenge_date=today,
            attempt_uuid=attempt_uuid,
            score_primary=score_primary,
            score_secondary=score_secondary,
            is_final=is_final,
        )
        db.session.add(run)
        db.session.commit()
        return jsonify({'status': 'success', 'run_id': run.id})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/api/daily-challenge/resolve', methods=['POST'])
def daily_challenge_resolve():
    """Resolve winner for a given date. Protected by PUSH_CRON_SECRET."""
    secret = request.headers.get('X-Cron-Secret') or request.get_json({}).get('secret')
    if secret != os.environ.get('PUSH_CRON_SECRET', ''):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    data = request.get_json()
    resolve_date_str = data.get('date', date.today().isoformat())
    resolve_date = date.fromisoformat(resolve_date_str)

    try:
        existing_winner = DailyChallengeWinner.query.filter_by(challenge_date=resolve_date).first()
        if existing_winner:
            return jsonify({'status': 'success', 'already_resolved': True, 'user_id': existing_winner.user_id})

        # Best final run: lower score_primary → fewer score_secondary → earliest created_at
        best = (
            DailyChallengeRun.query
            .filter_by(challenge_date=resolve_date, is_final=True)
            .order_by(
                DailyChallengeRun.score_primary.asc().nullslast(),
                DailyChallengeRun.score_secondary.asc().nullslast(),
                DailyChallengeRun.created_at.asc(),
            )
            .first()
        )

        if not best:
            return jsonify({'status': 'success', 'no_runs': True})

        winner = DailyChallengeWinner(
            challenge_date=resolve_date,
            user_id=best.user_id,
            score_primary=best.score_primary,
            score_secondary=best.score_secondary,
        )
        db.session.add(winner)

        new_awards = grant_daily_champion(best.user_id, resolve_date_str)
        db.session.commit()

        return jsonify({'status': 'success', 'winner_user_id': best.user_id, 'new_awards': new_awards})

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ---------------------------------------------------------------------------
# Daily run comparison — shared sort key so standings and resolve never drift.
#
# "Better" = lower score_primary, then lower score_secondary, then earlier
# created_at (same order as daily_challenge_resolve ORDER BY clause).
# ---------------------------------------------------------------------------

def _daily_run_sort_key(run):
    """
    Comparable sort key: (score_primary, score_secondary, created_at).
    None values sort last for numeric fields (treat as +inf).
    """
    sp = run.score_primary if run.score_primary is not None else float('inf')
    ss = run.score_secondary if run.score_secondary is not None else float('inf')
    ca = run.created_at or datetime.max
    return (sp, ss, ca)


@main.route('/api/daily-challenge/standings', methods=['GET'])
def daily_challenge_standings():
    """
    Aggregated standings for a daily challenge date.

    Query params:
      user_id  — optional; enables user_best / user_rank / user_submitted_final_today
      date     — optional ISO date string; defaults to today
    """
    user_id = request.args.get('user_id')
    date_str = request.args.get('date')
    try:
        target_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        return jsonify({'status': 'error', 'message': 'Invalid date format'}), 400

    try:
        final_runs = (
            DailyChallengeRun.query
            .filter_by(challenge_date=target_date, is_final=True)
            .all()
        )

        # Best final run per user (lowest sort key wins)
        best_by_user = {}
        for run in final_runs:
            existing = best_by_user.get(run.user_id)
            if existing is None or _daily_run_sort_key(run) < _daily_run_sort_key(existing):
                best_by_user[run.user_id] = run

        participant_count = len(best_by_user)

        top_run = None
        if best_by_user:
            top_run = min(best_by_user.values(), key=_daily_run_sort_key)

        top_score = None
        if top_run:
            top_score = {
                'score_primary': top_run.score_primary,
                'score_secondary': top_run.score_secondary,
            }

        user_best = None
        user_rank = None
        user_submitted_final_today = False

        if user_id:
            user_best_run = best_by_user.get(user_id)
            user_submitted_final_today = user_best_run is not None
            if user_best_run:
                user_best = {
                    'score_primary': user_best_run.score_primary,
                    'score_secondary': user_best_run.score_secondary,
                }
                user_key = _daily_run_sort_key(user_best_run)
                # rank = count of users with a strictly better score + 1
                user_rank = sum(
                    1 for r in best_by_user.values()
                    if _daily_run_sort_key(r) < user_key
                ) + 1

        return jsonify({
            'status': 'success',
            'challenge_date': target_date.isoformat(),
            'participant_count': participant_count,
            'top_score': top_score,
            'user_best': user_best,
            'user_rank': user_rank,
            'user_submitted_final_today': user_submitted_final_today,
        })

    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Analytics ──────────────────────────────────────────────────────────────

ALLOWED_EVENTS = frozenset({
    'app_opened',
    'app_ready',
    'first_palette_interaction',
    'save_attempt',
})


@main.route('/api/analytics/event', methods=['POST'])
def analytics_event():
    """
    Ingest a single client analytics event.

    Body: { event, ts (ISO-8601), user_id (optional), metadata (object) }
    metadata MUST include client_session_id for funnel stitching.
    """
    try:
        data = request.get_json(silent=True) or {}
        event = data.get('event', '')
        if event not in ALLOWED_EVENTS:
            return jsonify({'status': 'error', 'message': f'Unknown event: {event}'}), 400

        ts_raw = data.get('ts')
        try:
            ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.utcnow()
        except (ValueError, TypeError):
            ts = datetime.utcnow()

        ev = AnalyticsEvent(
            user_id=data.get('user_id') or None,
            event=event,
            ts=ts,
            metadata_json=data.get('metadata') or {},
        )
        db.session.add(ev)
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ── Push notifications ─────────────────────────────────────────────────────

@main.route('/push/subscribe', methods=['POST'])
def push_subscribe():
    data = request.get_json()
    user_id = data.get('user_id')
    endpoint = data.get('endpoint')
    p256dh = data.get('p256dh')
    auth = data.get('auth')

    if not all([user_id, endpoint, p256dh, auth]):
        return jsonify({'status': 'error', 'message': 'Missing fields'}), 400

    try:
        existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
        if existing:
            existing.user_id = user_id
            existing.p256dh = p256dh
            existing.auth = auth
        else:
            sub = PushSubscription(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth)
            db.session.add(sub)
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/push/unsubscribe', methods=['POST'])
def push_unsubscribe():
    data = request.get_json()
    endpoint = data.get('endpoint')
    if not endpoint:
        return jsonify({'status': 'error', 'message': 'endpoint required'}), 400
    try:
        PushSubscription.query.filter_by(endpoint=endpoint).delete()
        db.session.commit()
        return jsonify({'status': 'success'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500


@main.route('/push/send-daily', methods=['POST'])
def push_send_daily():
    """Cron-triggered endpoint to send daily challenge reminders."""
    secret = request.headers.get('X-Cron-Secret') or (request.get_json({}) or {}).get('secret')
    if secret != os.environ.get('PUSH_CRON_SECRET', ''):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        return jsonify({'status': 'error', 'message': 'pywebpush not installed'}), 500

    vapid_private = os.environ.get('VAPID_PRIVATE_KEY')
    vapid_public = os.environ.get('VAPID_PUBLIC_KEY')
    if not vapid_private or not vapid_public:
        return jsonify({'status': 'error', 'message': 'VAPID keys not configured'}), 500

    subs = PushSubscription.query.all()
    sent = 0
    failed = 0
    dead_endpoints = []

    def _build_push_payload(user_id):
        """Return personalized push payload dict for this user."""
        try:
            quota = compute_quota_progress(user_id)
            if quota['is_maxed_out']:
                return {
                    'title': 'ShadeMatch',
                    'body': "All colors mastered — keep your streak alive with today's challenge!",
                    'url': '/',
                    'icon': '/static/icons/icon-192.png',
                }
            # Find nearest actionable (unlocked) deficit color
            user_level = compute_level_from_quota(quota['coverage_ratio'], quota['is_maxed_out'])
            color_map = quota['color_quota_map']
            tc_rows = (
                TargetColor.query
                .filter(TargetColor.level_required <= user_level)
                .order_by(TargetColor.catalog_order.asc())
                .all()
            )
            best_tc = None
            best_rem = None
            for tc in tc_rows:
                rem = color_map.get(tc.id, {}).get('remaining', COVERAGE_QUOTA)
                if rem > 0 and (best_rem is None or rem < best_rem):
                    best_tc = tc
                    best_rem = rem

            completed = quota['completed_colors']
            total = quota['total_tracked_colors']
            if best_tc:
                body = (
                    f"Today's challenge is live — {best_tc.name} needs "
                    f"{best_rem} more attempt{'s' if best_rem != 1 else ''} "
                    f"({completed}/{total} colors done)"
                )
            else:
                body = (
                    f"Today's palette challenge is live — "
                    f"{completed}/{total} colors complete so far!"
                )
            return {
                'title': 'ShadeMatch Daily Challenge',
                'body': body,
                'url': '/',
                'icon': '/static/icons/icon-192.png',
            }
        except Exception:
            return {
                'title': 'ShadeMatch Daily Challenge',
                'body': "Today's palette challenge is live — can you match every shade?",
                'url': '/',
                'icon': '/static/icons/icon-192.png',
            }

    for sub in subs:
        payload_dict = _build_push_payload(sub.user_id)
        try:
            webpush(
                subscription_info={
                    'endpoint': sub.endpoint,
                    'keys': {'p256dh': sub.p256dh, 'auth': sub.auth},
                },
                data=json.dumps(payload_dict),
                vapid_private_key=vapid_private,
                vapid_claims={'sub': 'mailto:admin@shadematch.app'},
            )
            sent += 1
        except WebPushException as ex:
            status_code = ex.response.status_code if ex.response else None
            if status_code in (404, 410):
                dead_endpoints.append(sub.endpoint)
            else:
                failed += 1
        except Exception:
            failed += 1

    if dead_endpoints:
        PushSubscription.query.filter(PushSubscription.endpoint.in_(dead_endpoints)).delete(synchronize_session=False)
        db.session.commit()

    return jsonify({'status': 'success', 'sent': sent, 'failed': failed, 'cleaned': len(dead_endpoints)})


@main.route('/push/vapid-public-key', methods=['GET'])
def vapid_public_key():
    key = os.environ.get('VAPID_PUBLIC_KEY', '')
    return jsonify({'vapid_public_key': key})


@main.route('/sw.js')
def service_worker():
    """Serve the service worker from the root path so scope '/' is valid."""
    from flask import make_response
    sw_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'sw.js')
    with open(sw_path, 'r') as f:
        content = f.read()
    response = make_response(content)
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


# ── Misc / existing routes ─────────────────────────────────────────────────

@main.route('/refresh_connection', methods=['POST'])
def refresh_connection():
    if refresh_db_connection():
        return jsonify({'status': 'success'})
    return jsonify({'status': 'error', 'message': 'Failed to refresh connection'}), 500


@main.route('/color_inspector')
def color_inspector():
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    pigments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'pigments')
    samples = []
    for filename in os.listdir(pigments_dir):
        if filename.endswith('.csv'):
            df = pd.read_csv(os.path.join(pigments_dir, filename))
            pigment_wavelengths = df['Wavelength'].tolist()
            reflectances = df.iloc[:, 1:].mean(axis=1).tolist()
            X, Y, Z = spectrum_to_xyz(reflectances, pigment_wavelengths, x_bar, y_bar, z_bar)
            rgb = xyz_to_rgb(X, Y, Z)
            samples.append({
                'name': os.path.splitext(filename)[0].replace('_', ' ').title(),
                'wavelengths': pigment_wavelengths,
                'reflectances': reflectances,
                'rgb': rgb.tolist(),
            })
    return render_template('color_inspector.html', samples=samples)


@main.route('/mix_colors', methods=['POST'])
def mix_colors():
    data = request.get_json()
    drop_counts = data.get('dropCounts', {})
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    pigments = {
        'red':    {'reflectances': [0.1 if w < 600 else 0.9 for w in wavelengths]},
        'yellow': {'reflectances': [0.1 if w < 500 else 0.9 for w in wavelengths]},
        'blue':   {'reflectances': [0.9 if w < 500 else 0.1 for w in wavelengths]},
    }
    mixed_spectrum = np.ones(len(wavelengths))
    total_drops = sum(drop_counts.values())
    if total_drops > 0:
        for color, count in drop_counts.items():
            if count > 0 and color in pigments:
                n = count / (total_drops * 0.5)
                mixed_spectrum *= np.array(pigments[color]['reflectances']) ** n
    mixed_spectrum = np.clip(mixed_spectrum, 0.01, 1.0)
    X, Y, Z = spectrum_to_xyz(mixed_spectrum, wavelengths, x_bar, y_bar, z_bar)
    r, g, b = xyz_to_rgb(X, Y, Z)
    return jsonify({'rgb': [int(r), int(g), int(b)],
                    'spectrum': {'wavelengths': wavelengths.tolist(), 'reflectances': mixed_spectrum.tolist()}})


@main.route('/color-test')
def color_test():
    return render_template('color_test.html')


@main.route('/ishihara-test')
def ishihara_test():
    from flask import make_response
    response = make_response(render_template('ishihara_test.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@main.route('/spectral_mixer')
def spectral_mixer():
    return render_template('spectral_mixer.html')


@main.route('/reverse_engineer')
def reverse_engineer_page():
    return render_template('reverse_engineer.html')


@main.route('/reverse_engineer', methods=['POST'])
def reverse_engineer():
    try:
        if 'spectrum_file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
        file = request.files['spectrum_file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
        spectrum_data = pd.read_csv(file)
        if 'Wavelength' not in spectrum_data.columns or 'Reflectance' not in spectrum_data.columns:
            return jsonify({'error': 'File must have Wavelength and Reflectance columns'}), 400

        pigments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'pigments')
        pigments = {}
        for filename in os.listdir(pigments_dir):
            if filename.endswith('.csv'):
                pigment_name = os.path.splitext(filename)[0].replace('_', ' ').title()
                pigment_data = pd.read_csv(os.path.join(pigments_dir, filename))
                refl_cols = [c for c in pigment_data.columns if c != 'Wavelength']
                pigment_data['Reflectance'] = pigment_data[refl_cols].mean(axis=1)
                pigments[pigment_name] = {
                    'wavelengths': pigment_data['Wavelength'].tolist(),
                    'reflectances': pigment_data['Reflectance'].tolist(),
                }

        wavelengths, x_bar, y_bar, z_bar = load_cie_data()
        target_xyz = spectrum_to_xyz(
            spectrum_data['Reflectance'].tolist(),
            spectrum_data['Wavelength'].tolist(),
            x_bar, y_bar, z_bar,
        )
        target_rgb = xyz_to_rgb(*target_xyz)
        recipe, delta_e = reverse_engineer_recipe(target_xyz, pigments, x_bar, y_bar, z_bar)
        return jsonify({'recipe': recipe, 'delta_e': delta_e, 'target_rgb': target_rgb.tolist()})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@main.route('/ishihara/<filename>')
def serve_ishihara_image(filename):
    ishihara_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ishihara')
    return send_from_directory(ishihara_dir, filename)


@main.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy_policy.html')


@main.route('/cookie-consent', methods=['POST'])
def save_cookie_consent():
    try:
        data = request.get_json()
        print(f"Cookie consent: {data.get('consent', {})}")
        return jsonify({'status': 'success', 'message': 'Cookie preferences saved successfully'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Failed to save cookie preferences'}), 500


@main.route('/cookie-consent', methods=['GET'])
def get_cookie_consent():
    return jsonify({
        'status': 'success',
        'consent_required': True,
        'categories': {'necessary': True, 'analytics': False, 'preferences': False},
    })


# ── CIE helpers (local copies, identical to utils.py versions) ─────────────

def load_cie_data():
    wavelengths = np.arange(400, 701, 10)
    x_bar = np.array([0.0143,0.0435,0.1344,0.2839,0.3483,0.3362,0.2908,0.1954,0.0956,0.0320,
                      0.0049,0.0093,0.0633,0.1655,0.2904,0.4334,0.5945,0.7621,0.9163,1.0263,
                      1.0622,1.0026,0.8544,0.6424,0.4479,0.2835,0.1649,0.0874,0.0468,0.0227,0.0114])
    y_bar = np.array([0.0004,0.0012,0.0040,0.0116,0.023,0.038,0.060,0.091,0.139,0.208,
                      0.323,0.503,0.710,0.862,0.954,0.995,0.995,0.952,0.870,0.757,
                      0.631,0.503,0.381,0.265,0.175,0.107,0.061,0.032,0.017,0.0082,0.0041])
    z_bar = np.array([0.0679,0.2074,0.6456,1.3856,1.7471,1.7721,1.6692,1.2876,0.8130,0.4652,
                      0.2720,0.1582,0.0782,0.0422,0.0203,0.0087,0.0039,0.0021,0.0017,0.0011,
                      0.0008,0.0003,0.0002,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000,0.0000])
    return wavelengths, x_bar, y_bar, z_bar


def spectrum_to_xyz(spectrum, wavelengths, x_bar, y_bar, z_bar):
    x_interp = np.interp(wavelengths, np.arange(400, 701, 10), x_bar)
    y_interp = np.interp(wavelengths, np.arange(400, 701, 10), y_bar)
    z_interp = np.interp(wavelengths, np.arange(400, 701, 10), z_bar)
    X = np.sum(spectrum * x_interp)
    Y = np.sum(spectrum * y_interp)
    Z = np.sum(spectrum * z_interp)
    s = X + Y + Z
    if s > 0:
        X, Y, Z = X / s, Y / s, Z / s
    return X, Y, Z


def xyz_to_rgb(X, Y, Z):
    M = np.array([[3.2406, -1.5372, -0.4986],
                  [-0.9689,  1.8758,  0.0415],
                  [0.0557, -0.2040,  1.0570]])
    rgb = np.dot(M, np.array([X, Y, Z]))
    rgb = np.where(rgb > 0.0031308, 1.055 * np.power(np.clip(rgb, 0, None), 1 / 2.4) - 0.055, 12.92 * rgb)
    return np.clip(rgb * 255, 0, 255).astype(int)
