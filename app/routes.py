from flask import Blueprint, render_template, request, jsonify, send_from_directory, Response, current_app
from datetime import datetime, date, timedelta
import hashlib
import random as _random
import secrets
import re
import smtplib
from email.message import EmailMessage
from . import db
from .models import (
    User, MixingSession, TargetColor,
    UserProgress, UserTargetColorStats, UserAward,
    DailyChallengeRun, DailyChallengeWinner, PushSubscription,
    AnalyticsEvent, MixingAttempt, MixingAttemptEvent, EmailVerificationToken,
)
import string
from .utils import calculate_delta_e, spectrum_to_xyz, xyz_to_rgb, reverse_engineer_recipe
import pandas as pd
import os
import numpy as np
import json
from sqlalchemy import func
from dotenv import dotenv_values

from .gamification import (
    process_progression,
    build_progress_response,
    get_quota_ordered_catalog,
    grant_daily_champion,
    grant_daily_mission_awards,
    grant_daily_performance_awards,
    build_daily_missions,
    get_user_profile,
    compute_quota_progress,
    compute_level_from_quota,
    COVERAGE_QUOTA,
    STREAK_FREEZE_CAP,
)
from .next_action import build_next_action
from .stat_eda import ALLOWED_PLOT_IDS, build_strategy_summary_by_target, get_plot_png

main = Blueprint('main', __name__)


@main.app_context_processor
def inject_client_storage_version():
    return {
        'client_storage_version': current_app.config.get('CLIENT_STORAGE_VERSION', '1'),
    }


MATCH_PERFECT_DELTA_E = 0.01
EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
EMAIL_VERIFY_TTL_HOURS = 24
EMAIL_RECOVERY_TTL_MINUTES = 20
_RATE_LIMIT_BUCKETS = {}


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


def _normalize_email(raw_email):
    if not raw_email:
        return None
    email = str(raw_email).strip().lower()
    if not email:
        return None
    if len(email) > 255 or not EMAIL_REGEX.match(email):
        return None
    return email


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _rate_limit_allow(key, max_hits=5, window_sec=600):
    now = datetime.utcnow().timestamp()
    bucket = _RATE_LIMIT_BUCKETS.setdefault(key, [])
    bucket[:] = [ts for ts in bucket if now - ts < window_sec]
    if len(bucket) >= max_hits:
        return False
    bucket.append(now)
    return True


def _resolve_email_settings():
    settings = {
        'host': os.environ.get('SMTP_HOST', '').strip(),
        'port': int(os.environ.get('SMTP_PORT', '587') or '587'),
        'user': os.environ.get('SMTP_USER', '').strip(),
        'password': (
            os.environ.get('SMTP_PASSWORD', '').strip()
            or os.environ.get('SMTP_PASS', '').strip()
        ),
        'sender': (
            os.environ.get('SMTP_SENDER_EMAIL', '').strip()
            or os.environ.get('SMTP_FROM', '').strip()
        ),
        'use_tls': (os.environ.get('SMTP_USE_TLS', 'true').strip().lower() != 'false'),
        'use_ssl': (os.environ.get('SMTP_USE_SSL', 'false').strip().lower() == 'true'),
    }
    if all([settings['host'], settings['user'], settings['password'], settings['sender']]):
        return settings

    # Fallback: import config values from neighboring maxillofacialisrehabilitacio .env
    candidate_paths = [
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'maxillofacialisrehabilitacio', '.env')),
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'maxillofacialisrehabilitacio', '.env')),
    ]
    for candidate in candidate_paths:
        if not os.path.exists(candidate):
            continue
        values = dotenv_values(candidate)
        settings['host'] = settings['host'] or (values.get('SMTP_HOST') or '').strip()
        settings['port'] = settings['port'] if settings['port'] else int(values.get('SMTP_PORT') or 587)
        settings['user'] = settings['user'] or (values.get('SMTP_USER') or '').strip()
        settings['password'] = settings['password'] or (
            (values.get('SMTP_PASSWORD') or '').strip()
            or (values.get('SMTP_PASS') or '').strip()
        )
        settings['sender'] = settings['sender'] or (
            (values.get('SMTP_SENDER_EMAIL') or '').strip()
            or (values.get('SMTP_FROM') or '').strip()
            or (values.get('SMTP_USER') or '').strip()
        )
        if values.get('SMTP_USE_TLS') is not None:
            settings['use_tls'] = str(values.get('SMTP_USE_TLS')).strip().lower() != 'false'
        if values.get('SMTP_USE_SSL') is not None:
            settings['use_ssl'] = str(values.get('SMTP_USE_SSL')).strip().lower() == 'true'
        break
    return settings


def _send_email_message(to_email, subject, plain_text):
    settings = _resolve_email_settings()
    if not all([settings['host'], settings['user'], settings['password'], settings['sender']]):
        raise RuntimeError('Email sender is not configured')

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = settings['sender']
    message['To'] = to_email
    message.set_content(plain_text)

    smtp_cls = smtplib.SMTP_SSL if settings['use_ssl'] else smtplib.SMTP
    with smtp_cls(settings['host'], settings['port'], timeout=15) as server:
        if settings['use_tls'] and not settings['use_ssl']:
            server.starttls()
        server.login(settings['user'], settings['password'])
        server.send_message(message)


def _issue_email_token(user_id, purpose, ttl_minutes):
    token_plain = secrets.token_urlsafe(32)
    token_hash = _sha256(token_plain)
    expiry = datetime.utcnow() + timedelta(minutes=ttl_minutes)
    token_row = EmailVerificationToken(
        user_id=user_id,
        purpose=purpose,
        token_hash=token_hash,
        expires_at=expiry,
    )
    db.session.add(token_row)
    return token_plain, expiry


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


@main.route('/stat')
def stat_page():
    return render_template('stat.html')


@main.route('/api/stat/plot/<string:plot_id>', methods=['GET'])
def stat_plot(plot_id: str):
    """PNG figures from pandas/matplotlib (server-side EDA)."""
    pid = plot_id[:-4] if plot_id.lower().endswith('.png') else plot_id
    if pid not in ALLOWED_PLOT_IDS:
        return jsonify({'status': 'error', 'message': 'unknown plot id'}), 404
    plot_options = None
    if pid == 'fw_attempt_network':
        plot_options = {}
        au = request.args.get('attempt_uuid')
        if au and str(au).strip():
            plot_options['attempt_uuid'] = str(au).strip()
        tid = request.args.get('target_color_id', type=int)
        if tid is not None:
            plot_options['target_color_id'] = tid
    try:
        png = get_plot_png(pid, plot_options=plot_options)
    except Exception as e:
        print(f'stat_plot error ({pid}): {e}')
        if not refresh_db_connection():
            pass
        return jsonify({'status': 'error', 'message': str(e)}), 500
    return Response(png, mimetype='image/png')


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
    data = request.get_json() or {}
    birthdate = datetime.strptime(data['birthdate'], '%Y-%m-%d').date()
    gender = data['gender']
    email = _normalize_email(data.get('email'))
    email_opt_in_reminders = bool(data.get('email_opt_in_reminders', False))

    if birthdate.year >= 2015:
        return jsonify({'status': 'error', 'message': 'You must be born before 2015 to participate.'}), 400
    if not email:
        return jsonify({'status': 'error', 'message': 'A valid email is required to register.'}), 400
    if email and User.query.filter_by(email=email).first():
        return jsonify({'status': 'error', 'message': 'This email is already in use'}), 409

    user_id = generate_user_id()
    while User.query.get(user_id) is not None:
        user_id = generate_user_id()

    user = User(
        id=user_id,
        birthdate=birthdate,
        gender=gender,
        email=email,
        email_opt_in_reminders=email_opt_in_reminders,
    )
    db.session.add(user)
    db.session.commit()

    if email:
        try:
            token_plain, _ = _issue_email_token(
                user_id=user.id,
                purpose='verify_email',
                ttl_minutes=EMAIL_VERIFY_TTL_HOURS * 60,
            )
            verify_url = request.url_root.rstrip('/') + f"/email/verify?token={token_plain}"
            _send_email_message(
                to_email=email,
                subject='Verify your ShadeMatch email',
                plain_text=(
                    "Thanks for joining ShadeMatch.\n\n"
                    "To enable email reminders, verify your email here:\n"
                    f"{verify_url}\n\n"
                    "If you did not request this, you can ignore this email."
                ),
            )
            db.session.commit()
        except Exception as exc:
            db.session.rollback()
            print(f'email verify send failed for user {user.id}: {exc}')

    return jsonify({'status': 'success', 'userId': user_id, 'email_verification_pending': bool(email)})


@main.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    user_id = (data.get('userId') or '').strip().upper()
    if not user_id:
        return jsonify({'status': 'error', 'message': 'userId is required'}), 400
    try:
        user = User.query.get(user_id)
        if user:
            return jsonify({
                'status': 'success',
                'birthdate': user.birthdate.isoformat(),
                'gender': user.gender,
                'email': user.email,
                'email_verified': bool(user.email_verified_at),
                'email_opt_in_reminders': bool(user.email_opt_in_reminders),
            })

        session = MixingSession.query.filter_by(user_id=user_id).first()
        if session:
            return jsonify({'status': 'success', 'birthdate': '2000-01-01', 'gender': 'male'})

        return jsonify({'status': 'error', 'message': 'Invalid user ID'}), 404
    except Exception as e:
        return jsonify({'status': 'error', 'message': 'Database error'}), 500


@main.route('/email/verification/request', methods=['POST'])
def email_verification_request():
    data = request.get_json() or {}
    user_id = (data.get('user_id') or '').strip().upper()
    email = _normalize_email(data.get('email'))
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id is required'}), 400
    if data.get('email') and not email:
        return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400
    if not _rate_limit_allow(f'verify:{request.remote_addr}:{user_id}', max_hits=5, window_sec=3600):
        return jsonify({'status': 'error', 'message': 'Too many verification requests'}), 429

    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404
    if email and user.email and user.email != email:
        return jsonify({'status': 'error', 'message': 'Email mismatch for this user'}), 409
    if email and not user.email:
        if User.query.filter_by(email=email).first():
            return jsonify({'status': 'error', 'message': 'This email is already in use'}), 409
        user.email = email
    if not user.email:
        return jsonify({'status': 'error', 'message': 'No email on record'}), 400

    token_plain, _ = _issue_email_token(
        user_id=user.id,
        purpose='verify_email',
        ttl_minutes=EMAIL_VERIFY_TTL_HOURS * 60,
    )
    verify_url = request.url_root.rstrip('/') + f"/email/verify?token={token_plain}"
    try:
        _send_email_message(
            to_email=user.email,
            subject='Verify your ShadeMatch email',
            plain_text=(
                "Please verify your email to receive ShadeMatch reminders.\n\n"
                f"Verify link: {verify_url}\n\n"
                "If you did not request this, please ignore this email."
            ),
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Failed to send verification email: {exc}'}), 500
    return jsonify({'status': 'success'})


@main.route('/email/verify', methods=['GET'])
def email_verify():
    token_plain = (request.args.get('token') or '').strip()
    if not token_plain:
        return render_template('email_verify_result.html', success=False, message='Missing verification token.')
    token_hash = _sha256(token_plain)
    token_row = EmailVerificationToken.query.filter_by(token_hash=token_hash, purpose='verify_email').first()
    if not token_row:
        return render_template('email_verify_result.html', success=False, message='Invalid verification token.')
    if token_row.used_at is not None:
        return render_template('email_verify_result.html', success=False, message='Verification token already used.')
    if token_row.expires_at < datetime.utcnow():
        return render_template('email_verify_result.html', success=False, message='Verification token has expired.')

    user = User.query.get(token_row.user_id)
    if not user:
        return render_template('email_verify_result.html', success=False, message='User not found for token.')
    user.email_verified_at = datetime.utcnow()
    token_row.used_at = datetime.utcnow()
    db.session.commit()
    return render_template('email_verify_result.html', success=True, message='Email verified successfully. You can return to ShadeMatch.')


@main.route('/email/recover-id', methods=['POST'])
def email_recover_id():
    data = request.get_json() or {}
    email = _normalize_email(data.get('email'))
    if not email:
        return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400
    if not _rate_limit_allow(f'recover:{request.remote_addr}:{email}', max_hits=4, window_sec=1800):
        return jsonify({'status': 'error', 'message': 'Too many recovery requests'}), 429

    user = User.query.filter_by(email=email).first()
    # Return success even when no user exists to avoid user enumeration.
    if not user:
        return jsonify({'status': 'success'})

    token_plain, _ = _issue_email_token(
        user_id=user.id,
        purpose='recover_id',
        ttl_minutes=EMAIL_RECOVERY_TTL_MINUTES,
    )
    verify_url = request.url_root.rstrip('/') + f"/email/recover-id/confirm?token={token_plain}"
    try:
        _send_email_message(
            to_email=user.email,
            subject='ShadeMatch ID recovery',
            plain_text=(
                "We received an ID recovery request.\n\n"
                f"Open this link to view your ShadeMatch ID: {verify_url}\n\n"
                f"This link expires in {EMAIL_RECOVERY_TTL_MINUTES} minutes."
            ),
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': f'Failed to send recovery email: {exc}'}), 500
    return jsonify({'status': 'success'})


@main.route('/email/recover-id/confirm', methods=['GET'])
def email_recover_id_confirm():
    token_plain = (request.args.get('token') or '').strip()
    if not token_plain:
        return render_template('email_verify_result.html', success=False, message='Missing recovery token.')
    token_hash = _sha256(token_plain)
    token_row = EmailVerificationToken.query.filter_by(token_hash=token_hash, purpose='recover_id').first()
    if not token_row:
        return render_template('email_verify_result.html', success=False, message='Invalid recovery token.')
    if token_row.used_at is not None:
        return render_template('email_verify_result.html', success=False, message='Recovery token already used.')
    if token_row.expires_at < datetime.utcnow():
        return render_template('email_verify_result.html', success=False, message='Recovery token has expired.')
    user = User.query.get(token_row.user_id)
    if not user:
        return render_template('email_verify_result.html', success=False, message='User not found for token.')
    token_row.used_at = datetime.utcnow()
    db.session.commit()
    return render_template(
        'email_verify_result.html',
        success=True,
        message=f'Your ShadeMatch ID is: {user.id}',
    )


@main.route('/api/user/email-settings', methods=['POST'])
def user_email_settings():
    data = request.get_json() or {}
    user_id = (data.get('user_id') or '').strip().upper()
    if not user_id:
        return jsonify({'status': 'error', 'message': 'user_id is required'}), 400
    user = User.query.get(user_id)
    if not user:
        return jsonify({'status': 'error', 'message': 'User not found'}), 404

    if 'email_opt_in_reminders' in data:
        user.email_opt_in_reminders = bool(data.get('email_opt_in_reminders'))
    if 'email' in data:
        email = _normalize_email(data.get('email'))
        if data.get('email') and not email:
            return jsonify({'status': 'error', 'message': 'Invalid email format'}), 400
        if email and user.email != email and User.query.filter_by(email=email).first():
            return jsonify({'status': 'error', 'message': 'This email is already in use'}), 409
        if user.email != email:
            user.email = email
            user.email_verified_at = None
    db.session.commit()

    return jsonify({
        'status': 'success',
        'email': user.email,
        'email_verified': bool(user.email_verified_at),
        'email_opt_in_reminders': bool(user.email_opt_in_reminders),
    })


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


def _coerce_float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_delta(snapshot):
    if not isinstance(snapshot, dict):
        return None
    return _coerce_float_or_none(snapshot.get('delta_e'))


def _extract_rgb(snapshot):
    if not isinstance(snapshot, dict):
        return (None, None, None)
    rgb = snapshot.get('mixed_rgb')
    if not (isinstance(rgb, list) and len(rgb) == 3):
        return (None, None, None)
    vals = [_coerce_int_or_none(v) for v in rgb]
    if any(v is None for v in vals):
        return (None, None, None)
    return tuple(vals)


def _derive_action_type(event_type, metadata_json):
    if event_type == 'action_add':
        return 'add'
    if event_type == 'action_remove':
        return 'remove'
    if event_type == 'boundary_reset':
        return 'reset'
    if event_type == 'boundary_skip':
        return 'skip'
    if event_type == 'boundary_save':
        terminal_reason = (metadata_json or {}).get('terminal_end_reason')
        if terminal_reason == 'saved_match':
            return 'success'
        if terminal_reason == 'saved_stop':
            return 'stop'
    return None


def _is_decision_event(action_type):
    return action_type in {'add', 'remove', 'reset', 'stop', 'skip', 'success'}


def _state_from_gameplay_payload(data):
    mr = _coerce_int_or_none(data.get('mixed_r'))
    mg = _coerce_int_or_none(data.get('mixed_g'))
    mb = _coerce_int_or_none(data.get('mixed_b'))
    if mr is not None and mg is not None and mb is not None:
        mixed_rgb = [mr, mg, mb]
    else:
        # Legacy clients: no mixed RGB on save payload — keep drops-consistent analytics via optional mixed_*.
        mixed_rgb = [255, 255, 255]
    return {
        'drops': {
            'white': int(data.get('drop_white', 0) or 0),
            'black': int(data.get('drop_black', 0) or 0),
            'red': int(data.get('drop_red', 0) or 0),
            'yellow': int(data.get('drop_yellow', 0) or 0),
            'blue': int(data.get('drop_blue', 0) or 0),
        },
        'mixed_rgb': mixed_rgb,
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
            'step_index': event_like.get('step_index'),
            'time_since_prev_step_ms': event_like.get('time_since_prev_step_ms'),
            'action_type': event_like.get('action_type'),
            'amount': event_like.get('amount'),
            'delta_e_before': event_like.get('delta_e_before'),
            'delta_e_after': event_like.get('delta_e_after'),
            'mix_before_r': event_like.get('mix_before_r'),
            'mix_before_g': event_like.get('mix_before_g'),
            'mix_before_b': event_like.get('mix_before_b'),
            'mix_after_r': event_like.get('mix_after_r'),
            'mix_after_g': event_like.get('mix_after_g'),
            'mix_after_b': event_like.get('mix_after_b'),
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

    derived_action_type = _derive_action_type(event_type, metadata_json)
    action_type = raw.get('action_type') or derived_action_type
    if action_type is not None and action_type not in {'add', 'remove', 'reset', 'stop', 'skip', 'success'}:
        raise ValueError('action_type must be one of add|remove|reset|stop|skip|success or null')

    amount = _coerce_int_or_none(raw.get('amount'))
    if amount is None and action_type in {'add', 'remove'}:
        amount = 1

    step_index = _coerce_int_or_none(raw.get('step_index'))
    if step_index is not None and step_index <= 0:
        raise ValueError('step_index must be positive when provided')

    time_since_prev_step_ms = _coerce_int_or_none(raw.get('time_since_prev_step_ms'))
    if time_since_prev_step_ms is not None and time_since_prev_step_ms < 0:
        raise ValueError('time_since_prev_step_ms must be >= 0 when provided')

    delta_e_before = _coerce_float_or_none(raw.get('delta_e_before'))
    if delta_e_before is None:
        delta_e_before = _extract_delta(state_before)
    delta_e_after = _coerce_float_or_none(raw.get('delta_e_after'))
    if delta_e_after is None:
        delta_e_after = _extract_delta(state_after)

    mix_before_r = _coerce_int_or_none(raw.get('mix_before_r'))
    mix_before_g = _coerce_int_or_none(raw.get('mix_before_g'))
    mix_before_b = _coerce_int_or_none(raw.get('mix_before_b'))
    if mix_before_r is None or mix_before_g is None or mix_before_b is None:
        mix_before_r, mix_before_g, mix_before_b = _extract_rgb(state_before)

    mix_after_r = _coerce_int_or_none(raw.get('mix_after_r'))
    mix_after_g = _coerce_int_or_none(raw.get('mix_after_g'))
    mix_after_b = _coerce_int_or_none(raw.get('mix_after_b'))
    if mix_after_r is None or mix_after_g is None or mix_after_b is None:
        mix_after_r, mix_after_g, mix_after_b = _extract_rgb(state_after)

    return {
        'attempt_uuid': attempt_uuid,
        'seq': seq,
        'event_type': event_type,
        'action_color': action_color,
        'client_ts_ms': client_ts_ms,
        'state_before_json': state_before,
        'state_after_json': state_after,
        'metadata_json': metadata_json,
        'step_index': step_index,
        'time_since_prev_step_ms': time_since_prev_step_ms,
        'action_type': action_type,
        'amount': amount,
        'delta_e_before': delta_e_before,
        'delta_e_after': delta_e_after,
        'mix_before_r': mix_before_r,
        'mix_before_g': mix_before_g,
        'mix_before_b': mix_before_b,
        'mix_after_r': mix_after_r,
        'mix_after_g': mix_after_g,
        'mix_after_b': mix_after_b,
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

    final_delta_e = _coerce_float_or_none(payload.get('final_delta_e'))
    if final_delta_e is not None:
        row.final_delta_e = final_delta_e

    payload_duration_sec = _coerce_float_or_none(payload.get('duration_sec'))
    if payload_duration_sec is not None:
        row.duration_sec = max(0.0, payload_duration_sec)

    payload_num_steps = _coerce_int_or_none(payload.get('num_steps'))
    if payload_num_steps is not None:
        row.num_steps = max(0, payload_num_steps)

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
        if (
            ended_client is not None
            and ended_client > 0
            and row.attempt_ended_client_ts_ms is None
        ):
            row.attempt_ended_client_ts_ms = ended_client
        if row.attempt_ended_server_ts is None:
            row.attempt_ended_server_ts = _utcnow()

    # Keep summary fields up to date when both timestamps are known.
    if row.attempt_started_server_ts is not None and row.attempt_ended_server_ts is not None:
        row.duration_sec = max(
            0.0,
            (row.attempt_ended_server_ts - row.attempt_started_server_ts).total_seconds(),
        )

    return row


def _refresh_mixing_attempt_num_steps(attempt_uuid):
    row = MixingAttempt.query.get(attempt_uuid)
    if row is None:
        return
    db.session.flush()
    max_step = (
        db.session.query(func.max(MixingAttemptEvent.step_index))
        .filter(MixingAttemptEvent.attempt_uuid == attempt_uuid)
        .scalar()
    )
    if max_step is not None:
        row.num_steps = int(max_step)


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
    max_existing_step_index = (
        db.session.query(func.max(MixingAttemptEvent.step_index))
        .filter(MixingAttemptEvent.attempt_uuid == attempt_uuid)
        .scalar()
    ) or 0
    last_existing_decision_ts = (
        db.session.query(MixingAttemptEvent.client_ts_ms)
        .filter(
            MixingAttemptEvent.attempt_uuid == attempt_uuid,
            MixingAttemptEvent.step_index.isnot(None),
        )
        .order_by(MixingAttemptEvent.step_index.desc())
        .limit(1)
        .scalar()
    )
    next_step_index = max_existing_step_index + 1
    last_decision_client_ts = _coerce_int_or_none(last_existing_decision_ts)

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
                'step_index': existing.step_index,
                'time_since_prev_step_ms': existing.time_since_prev_step_ms,
                'action_type': existing.action_type,
                'amount': existing.amount,
                'delta_e_before': existing.delta_e_before,
                'delta_e_after': existing.delta_e_after,
                'mix_before_r': existing.mix_before_r,
                'mix_before_g': existing.mix_before_g,
                'mix_before_b': existing.mix_before_b,
                'mix_after_r': existing.mix_after_r,
                'mix_after_g': existing.mix_after_g,
                'mix_after_b': existing.mix_after_b,
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

        action_type = ev.get('action_type')
        is_decision = _is_decision_event(action_type)
        if is_decision:
            if ev.get('step_index') is None:
                ev['step_index'] = next_step_index
            if ev.get('time_since_prev_step_ms') is None:
                if last_decision_client_ts is None:
                    ev['time_since_prev_step_ms'] = None
                else:
                    ev['time_since_prev_step_ms'] = max(0, ev['client_ts_ms'] - last_decision_client_ts)
            next_step_index = max(next_step_index, ev['step_index'] + 1)
            last_decision_client_ts = ev['client_ts_ms']
        else:
            ev['step_index'] = None
            ev['time_since_prev_step_ms'] = None

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
        'final_delta_e': _coerce_float_or_none(data.get('delta_e')),
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
    max_step_index = (
        db.session.query(func.max(MixingAttemptEvent.step_index))
        .filter(MixingAttemptEvent.attempt_uuid == attempt_uuid)
        .scalar()
    ) or 0
    prev_decision_ts = (
        db.session.query(MixingAttemptEvent.client_ts_ms)
        .filter(
            MixingAttemptEvent.attempt_uuid == attempt_uuid,
            MixingAttemptEvent.step_index.isnot(None),
        )
        .order_by(MixingAttemptEvent.step_index.desc())
        .limit(1)
        .scalar()
    )
    end_client_ts = _coerce_int_or_none(data.get('attempt_ended_client_ts_ms'))
    if end_client_ts is None or end_client_ts <= 0:
        end_client_ts = int(_utcnow().timestamp() * 1000)
    mapped_action_type = 'success' if end_reason == 'saved_match' else ('stop' if end_reason == 'saved_stop' else 'skip')
    state = _state_from_gameplay_payload(data)
    synthetic_event = MixingAttemptEvent(
        attempt_uuid=attempt_uuid,
        seq=max_seq + 1,
        event_type=boundary_type,
        action_color=None,
        client_ts_ms=end_client_ts,
        state_before_json=state,
        state_after_json=state,
        metadata_json={'source': 'server_reconciliation', 'terminal_end_reason': end_reason},
        step_index=max_step_index + 1,
        time_since_prev_step_ms=(
            max(0, end_client_ts - prev_decision_ts)
            if _coerce_int_or_none(prev_decision_ts) is not None else None
        ),
        action_type=mapped_action_type,
        amount=None,
        delta_e_before=_extract_delta(state),
        delta_e_after=_extract_delta(state),
        mix_before_r=state['mixed_rgb'][0],
        mix_before_g=state['mixed_rgb'][1],
        mix_before_b=state['mixed_rgb'][2],
        mix_after_r=state['mixed_rgb'][0],
        mix_after_g=state['mixed_rgb'][1],
        mix_after_b=state['mixed_rgb'][2],
    )
    db.session.add(synthetic_event)
    _refresh_mixing_attempt_num_steps(attempt_uuid)


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
        _refresh_mixing_attempt_num_steps(attempt_uuid)
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
        row = _upsert_attempt_header(header)
        result = _ingest_mixing_events(attempt_uuid, events)
        _refresh_mixing_attempt_num_steps(attempt_uuid)
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
                    'daily_missions': build_daily_missions(user_id),
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
        new_awards.extend(grant_daily_mission_awards(user_id))

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
            'daily_missions': build_daily_missions(user_id),
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
                    'daily_missions': build_daily_missions(user_id),
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
        new_awards.extend(grant_daily_mission_awards(user_id))

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
            'daily_missions': build_daily_missions(user_id),
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
            'daily_missions': build_daily_missions(user_id),
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
            'daily_missions': build_daily_missions(user_id),
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
        next_action_data['daily_missions'] = build_daily_missions(user_id)

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
        daily_perf_awards = grant_daily_performance_awards(resolve_date)
        db.session.commit()

        return jsonify({
            'status': 'success',
            'winner_user_id': best.user_id,
            'new_awards': new_awards,
            'daily_performance_awards': daily_perf_awards,
        })

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


@main.route('/api/stat/summary', methods=['GET'])
def stat_summary():
    """
    Exploratory summaries from mixing telemetry (EDA only — no fitted models).
    """
    try:
        overview_row = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS attempts,
                  COUNT(DISTINCT user_id) FILTER (WHERE user_id IS NOT NULL)::bigint AS users,
                  COUNT(*) FILTER (WHERE user_id IS NULL)::bigint AS attempts_without_user,
                  AVG(NULLIF(num_steps, 0))::double precision AS avg_num_steps,
                  AVG(final_delta_e)::double precision AS avg_final_delta_e,
                  MIN(attempt_started_server_ts)::text AS first_attempt_server_ts,
                  MAX(attempt_started_server_ts)::text AS last_attempt_server_ts
                FROM mixing_attempts
                """
            )
        ).mappings().first()

        events_overview = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS events_total,
                  COUNT(*) FILTER (WHERE step_index IS NOT NULL)::bigint AS decision_rows,
                  COUNT(*) FILTER (WHERE step_index IS NULL)::bigint AS non_decision_rows,
                  AVG(step_index) FILTER (WHERE step_index IS NOT NULL)::double precision AS avg_step_index
                FROM mixing_attempt_events
                """
            )
        ).mappings().first()

        missingness_row = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS n_attempts,
                  AVG((user_id IS NULL)::int)::double precision AS pct_missing_user_id,
                  AVG((final_delta_e IS NULL)::int)::double precision AS pct_missing_final_delta_e,
                  AVG((num_steps IS NULL)::int)::double precision AS pct_missing_num_steps,
                  AVG((duration_sec IS NULL)::int)::double precision AS pct_missing_duration_sec,
                  AVG((end_reason IS NULL)::int)::double precision AS pct_missing_end_reason
                FROM mixing_attempts
                """
            )
        ).mappings().first()

        event_missingness_row = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS n_events,
                  AVG((step_index IS NULL)::int)::double precision AS pct_null_step_index,
                  AVG((delta_e_before IS NULL)::int)::double precision AS pct_null_delta_e_before,
                  AVG((delta_e_after IS NULL)::int)::double precision AS pct_null_delta_e_after,
                  AVG(
                    CASE
                      WHEN step_index IS NOT NULL AND time_since_prev_step_ms IS NULL THEN 1.0
                      ELSE 0.0
                    END
                  )::double precision AS pct_null_time_among_decisions
                FROM mixing_attempt_events
                """
            )
        ).mappings().first()

        end_reason_rows = db.session.execute(
            db.text(
                """
                SELECT end_reason, COUNT(*)::bigint AS n
                FROM mixing_attempts
                WHERE end_reason IS NOT NULL
                GROUP BY end_reason
                ORDER BY n DESC
                """
            )
        ).mappings().all()

        attempt_percentiles = db.session.execute(
            db.text(
                """
                SELECT
                  (SELECT COUNT(*)::bigint FROM mixing_attempts WHERE final_delta_e IS NOT NULL) AS n_final_de,
                  (SELECT percentile_cont(0.05) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM mixing_attempts WHERE final_delta_e IS NOT NULL)::double precision AS final_de_p05,
                  (SELECT percentile_cont(0.25) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM mixing_attempts WHERE final_delta_e IS NOT NULL)::double precision AS final_de_p25,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM mixing_attempts WHERE final_delta_e IS NOT NULL)::double precision AS final_de_p50,
                  (SELECT percentile_cont(0.75) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM mixing_attempts WHERE final_delta_e IS NOT NULL)::double precision AS final_de_p75,
                  (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM mixing_attempts WHERE final_delta_e IS NOT NULL)::double precision AS final_de_p95,
                  (SELECT COUNT(*)::bigint FROM mixing_attempts
                   WHERE num_steps IS NOT NULL AND num_steps > 0) AS n_num_steps,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY num_steps)
                   FROM mixing_attempts WHERE num_steps IS NOT NULL AND num_steps > 0)::double precision AS num_steps_p50,
                  (SELECT percentile_cont(0.90) WITHIN GROUP (ORDER BY num_steps)
                   FROM mixing_attempts WHERE num_steps IS NOT NULL AND num_steps > 0)::double precision AS num_steps_p90,
                  (SELECT COUNT(*)::bigint FROM mixing_attempts WHERE duration_sec IS NOT NULL) AS n_duration,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)
                   FROM mixing_attempts WHERE duration_sec IS NOT NULL)::double precision AS duration_p50,
                  (SELECT percentile_cont(0.90) WITHIN GROUP (ORDER BY duration_sec)
                   FROM mixing_attempts WHERE duration_sec IS NOT NULL)::double precision AS duration_p90
                """
            )
        ).mappings().first()

        step_percentiles = db.session.execute(
            db.text(
                """
                WITH d AS (
                  SELECT
                    (delta_e_before - delta_e_after) AS step_gain,
                    delta_e_before,
                    time_since_prev_step_ms
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                )
                SELECT
                  COUNT(*)::bigint AS n_steps,
                  AVG(CASE WHEN step_gain > 0 THEN 1.0 ELSE 0.0 END)::double precision AS pct_improving,
                  AVG(CASE WHEN step_gain < 0 THEN 1.0 ELSE 0.0 END)::double precision AS pct_worsening,
                  AVG(CASE WHEN step_gain = 0 THEN 1.0 ELSE 0.0 END)::double precision AS pct_flat,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY step_gain)::double precision AS gain_p50,
                  percentile_cont(0.90) WITHIN GROUP (ORDER BY step_gain)::double precision AS gain_p90,
                  percentile_cont(0.10) WITHIN GROUP (ORDER BY step_gain)::double precision AS gain_p10,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY delta_e_before)::double precision AS de_before_p50,
                  (SELECT percentile_cont(0.90) WITHIN GROUP (ORDER BY time_since_prev_step_ms)
                   FROM mixing_attempt_events
                   WHERE step_index IS NOT NULL
                     AND time_since_prev_step_ms IS NOT NULL)::double precision AS interstep_ms_p90
                FROM d
                """
            )
        ).mappings().first()

        daily_rows = db.session.execute(
            db.text(
                """
                SELECT
                  date_trunc('day', attempt_started_server_ts)::date::text AS day,
                  COUNT(*)::bigint AS n_attempts
                FROM mixing_attempts
                WHERE attempt_started_server_ts IS NOT NULL
                GROUP BY 1
                ORDER BY 1 DESC
                LIMIT 120
                """
            )
        ).mappings().all()

        user_bucket_rows = db.session.execute(
            db.text(
                """
                WITH per_user AS (
                  SELECT user_id, COUNT(*)::bigint AS n
                  FROM mixing_attempts
                  WHERE user_id IS NOT NULL
                  GROUP BY user_id
                )
                SELECT
                  CASE
                    WHEN n = 1 THEN '1'
                    WHEN n BETWEEN 2 AND 5 THEN '2-5'
                    WHEN n BETWEEN 6 AND 10 THEN '6-10'
                    WHEN n BETWEEN 11 AND 25 THEN '11-25'
                    ELSE '26+'
                  END AS attempt_count_bucket,
                  COUNT(*)::bigint AS n_users
                FROM per_user
                GROUP BY 1
                ORDER BY MIN(
                  CASE
                    WHEN n = 1 THEN 1
                    WHEN n BETWEEN 2 AND 5 THEN 2
                    WHEN n BETWEEN 6 AND 10 THEN 3
                    WHEN n BETWEEN 11 AND 25 THEN 4
                    ELSE 5
                  END
                )
                """
            )
        ).mappings().all()

        top_targets_rows = db.session.execute(
            db.text(
                """
                SELECT
                  ma.target_color_id,
                  tc.name AS target_name,
                  COUNT(*)::bigint AS n_attempts
                FROM mixing_attempts ma
                LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                WHERE ma.target_color_id IS NOT NULL
                GROUP BY ma.target_color_id, tc.name
                ORDER BY n_attempts DESC
                LIMIT 25
                """
            )
        ).mappings().all()

        event_type_rows = db.session.execute(
            db.text(
                """
                SELECT event_type, COUNT(*)::bigint AS n
                FROM mixing_attempt_events
                GROUP BY event_type
                ORDER BY n DESC
                """
            )
        ).mappings().all()

        action_type_rows = db.session.execute(
            db.text(
                """
                SELECT action_type, COUNT(*)::bigint AS n
                FROM mixing_attempt_events
                WHERE step_index IS NOT NULL
                GROUP BY action_type
                ORDER BY n DESC NULLS LAST
                """
            )
        ).mappings().all()

        corr_row = db.session.execute(
            db.text(
                """
                WITH attempts_ranked AS (
                  SELECT
                    ma.attempt_uuid,
                    ma.num_steps,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS trial_index
                  FROM mixing_attempts ma
                  WHERE ma.user_id IS NOT NULL
                    AND ma.num_steps IS NOT NULL
                    AND ma.num_steps > 0
                )
                SELECT
                  corr(ar.trial_index::double precision, ar.num_steps::double precision)
                    AS corr_trial_index_num_steps
                FROM attempts_ranked ar
                """
            )
        ).mappings().first()

        corr_step_row = db.session.execute(
            db.text(
                """
                SELECT
                  corr(
                    me.time_since_prev_step_ms::double precision,
                    (me.delta_e_before - me.delta_e_after)::double precision
                  ) AS corr_interstep_ms_step_gain
                FROM mixing_attempt_events me
                WHERE me.step_index IS NOT NULL
                  AND me.time_since_prev_step_ms IS NOT NULL
                  AND me.delta_e_before IS NOT NULL
                  AND me.delta_e_after IS NOT NULL
                """
            )
        ).mappings().first()

        overview = dict(overview_row or {})
        overview.update(dict(events_overview or {}))

        trial_rows = db.session.execute(
            db.text(
                """
                WITH attempts_ranked AS (
                  SELECT
                    ma.attempt_uuid,
                    ma.user_id,
                    ma.num_steps,
                    ma.attempt_started_server_ts,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS trial_index
                  FROM mixing_attempts ma
                  WHERE ma.user_id IS NOT NULL
                ),
                step_rows AS (
                  SELECT
                    ar.trial_index,
                    me.delta_e_before,
                    me.delta_e_after
                  FROM attempts_ranked ar
                  JOIN mixing_attempt_events me ON me.attempt_uuid = ar.attempt_uuid
                  WHERE me.step_index IS NOT NULL
                    AND me.delta_e_before IS NOT NULL
                    AND me.delta_e_after IS NOT NULL
                ),
                attempt_rows AS (
                  SELECT trial_index, num_steps
                  FROM attempts_ranked
                  WHERE num_steps IS NOT NULL
                )
                SELECT
                  s.trial_index,
                  COUNT(*)::bigint AS n_steps,
                  AVG(CASE WHEN s.delta_e_after < s.delta_e_before THEN 1.0 ELSE 0.0 END)::double precision AS improving_rate,
                  AVG(CASE WHEN s.delta_e_after > s.delta_e_before THEN 1.0 ELSE 0.0 END)::double precision AS worsening_rate,
                  AVG(s.delta_e_before - s.delta_e_after)::double precision AS mean_step_gain,
                  (
                    SELECT AVG(a.num_steps)::double precision
                    FROM attempt_rows a
                    WHERE a.trial_index = s.trial_index
                  ) AS avg_steps_to_completion
                FROM step_rows s
                GROUP BY s.trial_index
                ORDER BY s.trial_index
                LIMIT 60
                """
            )
        ).mappings().all()

        difficulty_rows = db.session.execute(
            db.text(
                """
                WITH step_rows AS (
                  SELECT
                    me.delta_e_before,
                    me.delta_e_after,
                    (me.delta_e_before - me.delta_e_after) AS step_gain,
                    CASE
                      WHEN me.delta_e_before < 1 THEN '[0,1)'
                      WHEN me.delta_e_before < 2 THEN '[1,2)'
                      WHEN me.delta_e_before < 4 THEN '[2,4)'
                      WHEN me.delta_e_before < 8 THEN '[4,8)'
                      ELSE '[8,+)'
                    END AS de_bucket
                  FROM mixing_attempt_events me
                  WHERE me.step_index IS NOT NULL
                    AND me.delta_e_before IS NOT NULL
                    AND me.delta_e_after IS NOT NULL
                )
                SELECT
                  de_bucket,
                  COUNT(*)::bigint AS n_steps,
                  AVG(CASE WHEN delta_e_after < delta_e_before THEN 1.0 ELSE 0.0 END)::double precision AS improving_rate,
                  AVG(step_gain)::double precision AS mean_step_gain
                FROM step_rows
                GROUP BY de_bucket
                ORDER BY
                  CASE de_bucket
                    WHEN '[0,1)' THEN 1
                    WHEN '[1,2)' THEN 2
                    WHEN '[2,4)' THEN 3
                    WHEN '[4,8)' THEN 4
                    ELSE 5
                  END
                """
            )
        ).mappings().all()

        random_rows = db.session.execute(
            db.text(
                """
                WITH attempts_ranked AS (
                  SELECT
                    ma.attempt_uuid,
                    ma.user_id,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS trial_index
                  FROM mixing_attempts ma
                  WHERE ma.user_id IS NOT NULL
                ),
                step_rows AS (
                  SELECT
                    ar.trial_index,
                    (me.delta_e_before - me.delta_e_after) AS step_gain,
                    CASE WHEN me.delta_e_after < me.delta_e_before THEN 1 ELSE 0 END AS improving
                  FROM attempts_ranked ar
                  JOIN mixing_attempt_events me ON me.attempt_uuid = ar.attempt_uuid
                  WHERE me.step_index IS NOT NULL
                    AND me.delta_e_before IS NOT NULL
                    AND me.delta_e_after IS NOT NULL
                )
                SELECT
                  trial_index,
                  COUNT(*)::bigint AS n_steps,
                  AVG(improving::double precision)::double precision AS improving_rate,
                  AVG(step_gain)::double precision AS mean_gain,
                  STDDEV_POP(step_gain)::double precision AS sd_gain
                FROM step_rows
                GROUP BY trial_index
                ORDER BY trial_index
                LIMIT 60
                """
            )
        ).mappings().all()

        time_rows = db.session.execute(
            db.text(
                """
                WITH step_rows AS (
                  SELECT
                    me.time_since_prev_step_ms,
                    me.delta_e_before,
                    me.delta_e_after,
                    (me.delta_e_before - me.delta_e_after) AS step_gain,
                    CASE
                      WHEN me.time_since_prev_step_ms IS NULL THEN 'first_step'
                      WHEN me.time_since_prev_step_ms < 1000 THEN '<1s'
                      WHEN me.time_since_prev_step_ms < 3000 THEN '1-3s'
                      WHEN me.time_since_prev_step_ms < 7000 THEN '3-7s'
                      ELSE '7s+'
                    END AS time_bucket
                  FROM mixing_attempt_events me
                  WHERE me.step_index IS NOT NULL
                    AND me.delta_e_before IS NOT NULL
                    AND me.delta_e_after IS NOT NULL
                )
                SELECT
                  time_bucket,
                  COUNT(*)::bigint AS n_steps,
                  AVG(CASE WHEN delta_e_after < delta_e_before THEN 1.0 ELSE 0.0 END)::double precision AS improving_rate,
                  AVG(step_gain)::double precision AS mean_step_gain
                FROM step_rows
                GROUP BY time_bucket
                ORDER BY
                  CASE time_bucket
                    WHEN 'first_step' THEN 1
                    WHEN '<1s' THEN 2
                    WHEN '1-3s' THEN 3
                    WHEN '3-7s' THEN 4
                    ELSE 5
                  END
                """
            )
        ).mappings().all()

        stop_rows = db.session.execute(
            db.text(
                """
                WITH ended AS (
                  SELECT
                    attempt_uuid,
                    end_reason,
                    final_delta_e,
                    num_steps,
                    CASE
                      WHEN final_delta_e IS NULL THEN 'unknown'
                      WHEN final_delta_e < 1 THEN '[0,1)'
                      WHEN final_delta_e < 2 THEN '[1,2)'
                      WHEN final_delta_e < 4 THEN '[2,4)'
                      WHEN final_delta_e < 8 THEN '[4,8)'
                      ELSE '[8,+)'
                    END AS de_bucket
                  FROM mixing_attempts
                  WHERE end_reason IS NOT NULL
                )
                SELECT
                  de_bucket,
                  COUNT(*)::bigint AS n_attempts,
                  AVG(CASE WHEN end_reason = 'saved_stop' THEN 1.0 ELSE 0.0 END)::double precision AS stop_probability,
                  AVG(CASE WHEN end_reason = 'saved_match' THEN 1.0 ELSE 0.0 END)::double precision AS success_probability,
                  AVG(num_steps)::double precision AS avg_num_steps
                FROM ended
                GROUP BY de_bucket
                ORDER BY
                  CASE de_bucket
                    WHEN '[0,1)' THEN 1
                    WHEN '[1,2)' THEN 2
                    WHEN '[2,4)' THEN 3
                    WHEN '[4,8)' THEN 4
                    WHEN '[8,+)' THEN 5
                    ELSE 6
                  END
                """
            )
        ).mappings().all()

        stop_dispersion = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS n_stops,
                  AVG(final_delta_e)::double precision AS mean_stop_delta_e,
                  STDDEV_POP(final_delta_e)::double precision AS sd_stop_delta_e
                FROM mixing_attempts
                WHERE end_reason = 'saved_stop' AND final_delta_e IS NOT NULL
                """
            )
        ).mappings().first()

        perfect_threshold = float(MATCH_PERFECT_DELTA_E)

        attempt_outcome_flags = db.session.execute(
            db.text(
                """
                SELECT
                  AVG(
                    CASE
                      WHEN final_delta_e IS NOT NULL AND final_delta_e <= :perfect_t THEN 1.0
                      ELSE 0.0
                    END
                  )::double precision AS rate_perfect,
                  AVG(
                    CASE
                      WHEN final_delta_e IS NOT NULL AND final_delta_e <= 1.0 THEN 1.0
                      ELSE 0.0
                    END
                  )::double precision AS rate_near_delta_e_le_1,
                  AVG(
                    CASE
                      WHEN initial_delta_e IS NOT NULL AND final_delta_e IS NOT NULL
                        AND num_steps IS NOT NULL AND num_steps > 0
                      THEN (initial_delta_e - final_delta_e) / num_steps::double precision
                    END
                  )::double precision AS avg_delta_e_improvement_per_step,
                  AVG(
                    CASE
                      WHEN initial_delta_e IS NOT NULL AND final_delta_e IS NOT NULL
                        AND duration_sec IS NOT NULL AND duration_sec > 0
                      THEN (initial_delta_e - final_delta_e) / duration_sec::double precision
                    END
                  )::double precision AS avg_delta_e_improvement_per_sec,
                  AVG(
                    CASE
                      WHEN first_action_client_ts_ms IS NOT NULL
                        AND attempt_started_client_ts_ms IS NOT NULL
                      THEN (first_action_client_ts_ms - attempt_started_client_ts_ms) / 1000.0
                    END
                  )::double precision AS avg_first_action_latency_sec
                FROM mixing_attempts
                """
            ),
            {'perfect_t': perfect_threshold},
        ).mappings().first()

        hist_final_de_rows = db.session.execute(
            db.text(
                """
                SELECT
                  CASE
                    WHEN final_delta_e <= :perfect_t THEN 'a_perfect_le_match_threshold'
                    WHEN final_delta_e <= 0.1 THEN 'b_(perfect,0.1]'
                    WHEN final_delta_e <= 0.5 THEN 'c_(0.1,0.5]'
                    WHEN final_delta_e <= 1.0 THEN 'd_(0.5,1]'
                    WHEN final_delta_e <= 2.0 THEN 'e_(1,2]'
                    WHEN final_delta_e <= 4.0 THEN 'f_(2,4]'
                    WHEN final_delta_e <= 8.0 THEN 'g_(4,8]'
                    ELSE 'h_(8,+]'
                  END AS bin_key,
                  COUNT(*)::bigint AS n,
                  MIN(final_delta_e)::double precision AS bin_min,
                  MAX(final_delta_e)::double precision AS bin_max
                FROM mixing_attempts
                WHERE final_delta_e IS NOT NULL
                GROUP BY 1
                ORDER BY MIN(final_delta_e)
                """
            ),
            {'perfect_t': perfect_threshold},
        ).mappings().all()

        hist_log_final_de_rows = db.session.execute(
            db.text(
                """
                SELECT
                  CASE
                    WHEN final_delta_e <= 0 THEN 'non_positive'
                    WHEN ln(final_delta_e + 1e-9) < -2 THEN 'ln<-2'
                    WHEN ln(final_delta_e + 1e-9) < 0 THEN 'ln[-2,0)'
                    WHEN ln(final_delta_e + 1e-9) < 2 THEN 'ln[0,2)'
                    WHEN ln(final_delta_e + 1e-9) < 4 THEN 'ln[2,4)'
                    ELSE 'ln>=4'
                  END AS log_bin,
                  COUNT(*)::bigint AS n
                FROM mixing_attempts
                WHERE final_delta_e IS NOT NULL
                GROUP BY 1
                ORDER BY MIN(ln(GREATEST(final_delta_e, 1e-9)))
                """
            )
        ).mappings().all()

        hist_duration_rows = db.session.execute(
            db.text(
                """
                SELECT
                  CASE
                    WHEN duration_sec IS NULL THEN 'missing'
                    WHEN duration_sec < 0 THEN 'negative'
                    WHEN duration_sec <= 5 THEN '0-5s'
                    WHEN duration_sec <= 15 THEN '5-15s'
                    WHEN duration_sec <= 30 THEN '15-30s'
                    WHEN duration_sec <= 60 THEN '30-60s'
                    WHEN duration_sec <= 120 THEN '60-120s'
                    WHEN duration_sec <= 300 THEN '120-300s'
                    ELSE '300s+'
                  END AS dur_bin,
                  COUNT(*)::bigint AS n
                FROM mixing_attempts
                GROUP BY 1
                ORDER BY MIN(CASE
                  WHEN duration_sec IS NULL THEN -1
                  WHEN duration_sec < 0 THEN -0.5
                  ELSE duration_sec
                END)
                """
            )
        ).mappings().all()

        duration_by_perfect_rows = db.session.execute(
            db.text(
                """
                SELECT
                  CASE
                    WHEN final_delta_e IS NULL THEN 'unknown_de'
                    WHEN final_delta_e <= :perfect_t THEN 'perfect'
                    ELSE 'non_perfect'
                  END AS outcome_band,
                  COUNT(*)::bigint AS n,
                  AVG(duration_sec)::double precision AS mean_duration_sec
                FROM mixing_attempts
                GROUP BY 1
                ORDER BY 1
                """
            ),
            {'perfect_t': perfect_threshold},
        ).mappings().all()

        joint_corr_attempts = db.session.execute(
            db.text(
                """
                SELECT
                  corr(final_delta_e::double precision, duration_sec::double precision)
                    AS corr_final_de_duration,
                  corr(final_delta_e::double precision, num_steps::double precision)
                    AS corr_final_de_num_steps,
                  corr(duration_sec::double precision, num_steps::double precision)
                    AS corr_duration_num_steps
                FROM mixing_attempts
                WHERE final_delta_e IS NOT NULL
                  AND duration_sec IS NOT NULL
                  AND num_steps IS NOT NULL
                  AND num_steps > 0
                """
            )
        ).mappings().first()

        trial_outcome_curves = db.session.execute(
            db.text(
                """
                WITH attempts_ranked AS (
                  SELECT
                    ma.attempt_uuid,
                    ma.final_delta_e,
                    ma.duration_sec,
                    ma.end_reason,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS trial_index
                  FROM mixing_attempts ma
                  WHERE ma.user_id IS NOT NULL
                ),
                idx AS (
                  SELECT trial_index FROM attempts_ranked GROUP BY trial_index
                )
                SELECT
                  idx.trial_index,
                  (SELECT COUNT(*)::bigint FROM attempts_ranked a WHERE a.trial_index = idx.trial_index)
                    AS n_attempts,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)
                   FROM attempts_ranked a
                   WHERE a.trial_index = idx.trial_index AND a.final_delta_e IS NOT NULL)::double precision
                    AS median_final_delta_e,
                  (SELECT AVG(
                    CASE
                      WHEN a.final_delta_e IS NOT NULL AND a.final_delta_e <= :perfect_t THEN 1.0
                      WHEN a.end_reason = 'saved_match' THEN 1.0
                      ELSE 0.0
                    END
                  )::double precision
                   FROM attempts_ranked a WHERE a.trial_index = idx.trial_index
                  ) AS success_rate,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)
                   FROM attempts_ranked a
                   WHERE a.trial_index = idx.trial_index AND a.duration_sec IS NOT NULL)::double precision
                    AS median_duration_sec
                FROM idx
                ORDER BY idx.trial_index
                LIMIT 60
                """
            ),
            {'perfect_t': perfect_threshold},
        ).mappings().all()

        target_difficulty_rows = db.session.execute(
            db.text(
                """
                SELECT
                  ma.target_color_id,
                  tc.name AS target_name,
                  tc.color_type,
                  tc.classification,
                  COUNT(*)::bigint AS n_attempts,
                  AVG(ma.final_delta_e)::double precision AS mean_final_delta_e,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY ma.final_delta_e)::double precision
                    AS median_final_delta_e,
                  AVG(ma.duration_sec)::double precision AS mean_duration_sec,
                  AVG(
                    CASE
                      WHEN ma.final_delta_e IS NOT NULL AND ma.final_delta_e <= :perfect_t THEN 1.0
                      WHEN ma.end_reason = 'saved_match' THEN 1.0
                      ELSE 0.0
                    END
                  )::double precision AS success_rate
                FROM mixing_attempts ma
                LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                WHERE ma.target_color_id IS NOT NULL
                  AND ma.final_delta_e IS NOT NULL
                GROUP BY ma.target_color_id, tc.name, tc.color_type, tc.classification
                HAVING COUNT(*) >= 3
                ORDER BY mean_final_delta_e DESC NULLS LAST
                LIMIT 60
                """
            ),
            {'perfect_t': perfect_threshold},
        ).mappings().all()

        action_effectiveness_rows = db.session.execute(
            db.text(
                """
                SELECT
                  COALESCE(action_color, '(null)') AS action_color,
                  COALESCE(action_type, '(null)') AS action_type,
                  COUNT(*)::bigint AS n,
                  AVG(me.delta_e_before - me.delta_e_after)::double precision AS mean_delta_e_gain,
                  AVG(ABS(me.delta_e_before - me.delta_e_after))::double precision AS mean_abs_delta_e_change
                FROM mixing_attempt_events me
                WHERE me.step_index IS NOT NULL
                  AND me.delta_e_before IS NOT NULL
                  AND me.delta_e_after IS NOT NULL
                  AND me.action_type IN ('add', 'remove')
                GROUP BY me.action_color, me.action_type
                ORDER BY n DESC
                LIMIT 40
                """
            )
        ).mappings().all()

        step_gain_by_action_rows = db.session.execute(
            db.text(
                """
                SELECT
                  COALESCE(action_type, '(null)') AS action_type,
                  COUNT(*)::bigint AS n,
                  AVG(me.delta_e_before - me.delta_e_after)::double precision AS mean_gain,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY (me.delta_e_before - me.delta_e_after))::double precision AS median_gain
                FROM mixing_attempt_events me
                WHERE me.step_index IS NOT NULL
                  AND me.delta_e_before IS NOT NULL
                  AND me.delta_e_after IS NOT NULL
                GROUP BY me.action_type
                ORDER BY n DESC NULLS LAST
                """
            )
        ).mappings().all()

        phase_behavior_rows = db.session.execute(
            db.text(
                """
                WITH scored AS (
                  SELECT
                    NTILE(3) OVER (PARTITION BY attempt_uuid ORDER BY step_index) AS phase,
                    (delta_e_before - delta_e_after) AS gain,
                    ABS(delta_e_before - delta_e_after) AS abs_gain,
                    CASE WHEN delta_e_after < delta_e_before THEN 1.0 ELSE 0.0 END AS improving
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                )
                SELECT
                  phase,
                  COUNT(*)::bigint AS n_steps,
                  AVG(abs_gain)::double precision AS mean_abs_gain,
                  STDDEV_POP(gain)::double precision AS sd_gain,
                  AVG(improving)::double precision AS improving_rate
                FROM scored
                GROUP BY phase
                ORDER BY phase
                """
            )
        ).mappings().all()

        oscillation_summary = db.session.execute(
            db.text(
                """
                WITH step_gains AS (
                  SELECT
                    attempt_uuid,
                    seq,
                    (delta_e_before - delta_e_after) AS g,
                    LAG(delta_e_before - delta_e_after) OVER (
                      PARTITION BY attempt_uuid ORDER BY seq
                    ) AS g_prev
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                ),
                per_attempt AS (
                  SELECT
                    attempt_uuid,
                    SUM(
                      CASE
                        WHEN g_prev IS NOT NULL AND g IS NOT NULL
                          AND g_prev <> 0 AND g <> 0
                          AND (
                            (g_prev > 0 AND g < 0) OR (g_prev < 0 AND g > 0)
                          )
                        THEN 1
                        ELSE 0
                      END
                    )::bigint AS sign_changes
                  FROM step_gains
                  GROUP BY attempt_uuid
                )
                SELECT
                  COUNT(*)::bigint AS n_attempts,
                  AVG(sign_changes)::double precision AS mean_sign_changes,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY sign_changes)::double precision AS median_sign_changes,
                  percentile_cont(0.90) WITHIN GROUP (ORDER BY sign_changes)::double precision AS p90_sign_changes
                FROM per_attempt
                """
            )
        ).mappings().first()

        hist_oscillation_rows = db.session.execute(
            db.text(
                """
                WITH step_gains AS (
                  SELECT
                    attempt_uuid,
                    seq,
                    (delta_e_before - delta_e_after) AS g,
                    LAG(delta_e_before - delta_e_after) OVER (
                      PARTITION BY attempt_uuid ORDER BY seq
                    ) AS g_prev
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                ),
                per_attempt AS (
                  SELECT
                    attempt_uuid,
                    SUM(
                      CASE
                        WHEN g_prev IS NOT NULL AND g IS NOT NULL
                          AND g_prev <> 0 AND g <> 0
                          AND (
                            (g_prev > 0 AND g < 0) OR (g_prev < 0 AND g > 0)
                          )
                        THEN 1
                        ELSE 0
                      END
                    )::bigint AS sign_changes
                  FROM step_gains
                  GROUP BY attempt_uuid
                )
                SELECT
                  LEAST(sign_changes, 15)::int AS sign_changes_capped,
                  COUNT(*)::bigint AS n_attempts
                FROM per_attempt
                GROUP BY 1
                ORDER BY 1
                """
            )
        ).mappings().all()

        trajectory_shape_rows = db.session.execute(
            db.text(
                """
                WITH bounds AS (
                  SELECT attempt_uuid, MAX(step_index)::double precision AS max_s
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                  GROUP BY attempt_uuid
                  HAVING MAX(step_index) > 0
                ),
                pts AS (
                  SELECT
                    me.attempt_uuid,
                    me.step_index::double precision / b.max_s AS t_norm,
                    me.delta_e_after
                  FROM mixing_attempt_events me
                  JOIN bounds b ON b.attempt_uuid = me.attempt_uuid
                  WHERE me.step_index IS NOT NULL
                    AND me.delta_e_after IS NOT NULL
                )
                SELECT
                  WIDTH_BUCKET(t_norm::numeric, 0::numeric, 1::numeric, 10) AS position_decile,
                  COUNT(*)::bigint AS n_points,
                  AVG(delta_e_after)::double precision AS mean_delta_e_after
                FROM pts
                WHERE t_norm >= 0 AND t_norm <= 1
                GROUP BY 1
                ORDER BY 1
                """
            )
        ).mappings().all()

        archetype_feature_percentiles = db.session.execute(
            db.text(
                """
                WITH step_gains AS (
                  SELECT
                    attempt_uuid,
                    seq,
                    (delta_e_before - delta_e_after) AS g,
                    LAG(delta_e_before - delta_e_after) OVER (
                      PARTITION BY attempt_uuid ORDER BY seq
                    ) AS g_prev
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                ),
                osc AS (
                  SELECT
                    attempt_uuid,
                    SUM(
                      CASE
                        WHEN g_prev IS NOT NULL AND g IS NOT NULL
                          AND g_prev <> 0 AND g <> 0
                          AND (
                            (g_prev > 0 AND g < 0) OR (g_prev < 0 AND g > 0)
                          )
                        THEN 1
                        ELSE 0
                      END
                    )::bigint AS sign_changes
                  FROM step_gains
                  GROUP BY attempt_uuid
                ),
                step_avg AS (
                  SELECT
                    attempt_uuid,
                    AVG(ABS(delta_e_before - delta_e_after))::double precision AS mean_abs_step_change
                  FROM mixing_attempt_events
                  WHERE step_index IS NOT NULL
                    AND delta_e_before IS NOT NULL
                    AND delta_e_after IS NOT NULL
                  GROUP BY attempt_uuid
                ),
                feat AS (
                  SELECT
                    ma.attempt_uuid,
                    ma.num_steps,
                    ma.duration_sec,
                    ma.final_delta_e,
                    COALESCE(o.sign_changes, 0)::double precision AS sign_changes,
                    s.mean_abs_step_change,
                    CASE
                      WHEN ma.duration_sec IS NOT NULL AND ma.duration_sec > 0
                        AND ma.num_steps IS NOT NULL AND ma.num_steps > 0
                      THEN ma.duration_sec / ma.num_steps::double precision
                    END AS sec_per_step
                  FROM mixing_attempts ma
                  LEFT JOIN osc o ON o.attempt_uuid = ma.attempt_uuid
                  LEFT JOIN step_avg s ON s.attempt_uuid = ma.attempt_uuid
                  WHERE ma.num_steps IS NOT NULL AND ma.num_steps > 0
                )
                SELECT
                  (SELECT percentile_cont(0.10) WITHIN GROUP (ORDER BY num_steps) FROM feat)::double precision
                    AS num_steps_p10,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY num_steps) FROM feat)::double precision
                    AS num_steps_p50,
                  (SELECT percentile_cont(0.90) WITHIN GROUP (ORDER BY num_steps) FROM feat)::double precision
                    AS num_steps_p90,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY mean_abs_step_change) FROM feat
                   WHERE mean_abs_step_change IS NOT NULL)::double precision AS mean_abs_step_p50,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY sign_changes) FROM feat)::double precision
                    AS oscillation_p50,
                  (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY sec_per_step) FROM feat
                   WHERE sec_per_step IS NOT NULL)::double precision AS sec_per_step_p50
                """
            )
        ).mappings().first()

        mixing_sessions_overview = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*)::bigint AS n_sessions,
                  AVG(CASE WHEN skipped THEN 1.0 ELSE 0.0 END)::double precision AS skip_rate,
                  AVG(delta_e)::double precision AS mean_delta_e,
                  AVG(time_sec)::double precision AS mean_time_sec
                FROM mixing_sessions
                """
            )
        ).mappings().first()

        skip_by_target_rows = db.session.execute(
            db.text(
                """
                SELECT
                  ms.target_color_id,
                  tc.name AS target_name,
                  COUNT(*)::bigint AS n,
                  AVG(CASE WHEN ms.skipped THEN 1.0 ELSE 0.0 END)::double precision AS skip_rate,
                  AVG(ms.delta_e)::double precision AS mean_delta_e
                FROM mixing_sessions ms
                LEFT JOIN target_colors tc ON tc.id = ms.target_color_id
                WHERE ms.target_color_id IS NOT NULL
                GROUP BY ms.target_color_id, tc.name
                HAVING COUNT(*) >= 5
                ORDER BY skip_rate DESC NULLS LAST
                LIMIT 40
                """
            )
        ).mappings().all()

        skip_perception_rows = db.session.execute(
            db.text(
                """
                SELECT
                  COALESCE(skip_perception, '(null)') AS skip_perception,
                  COUNT(*)::bigint AS n,
                  AVG(delta_e)::double precision AS mean_delta_e_at_skip
                FROM mixing_sessions
                WHERE skipped
                GROUP BY skip_perception
                ORDER BY n DESC
                """
            )
        ).mappings().all()

        user_skill_distribution = db.session.execute(
            db.text(
                """
                WITH u AS (
                  SELECT
                    user_id,
                    COUNT(*)::bigint AS n_attempts,
                    AVG(final_delta_e)::double precision AS mean_final_de,
                    MIN(final_delta_e)::double precision AS best_final_de,
                    STDDEV_POP(final_delta_e)::double precision AS sd_final_de
                  FROM mixing_attempts
                  WHERE user_id IS NOT NULL AND final_delta_e IS NOT NULL
                  GROUP BY user_id
                  HAVING COUNT(*) >= 3
                )
                SELECT
                  COUNT(*)::bigint AS n_users,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY mean_final_de)::double precision AS median_user_mean_de,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY best_final_de)::double precision AS median_user_best_de,
                  percentile_cont(0.90) WITHIN GROUP (ORDER BY mean_final_de)::double precision AS p90_user_mean_de
                FROM u
                """
            )
        ).mappings().first()

        data_integrity_row = db.session.execute(
            db.text(
                """
                SELECT
                  COUNT(*) FILTER (WHERE duration_sec IS NOT NULL AND duration_sec < 0)::bigint AS n_negative_duration,
                  COUNT(*) FILTER (WHERE num_steps IS NOT NULL AND num_steps < 0)::bigint AS n_negative_num_steps,
                  COUNT(*) FILTER (
                    WHERE final_delta_e IS NOT NULL AND initial_delta_e IS NOT NULL
                      AND final_delta_e > initial_delta_e + 0.0001
                  )::bigint AS n_final_gt_initial_worse_match
                FROM mixing_attempts
                """
            )
        ).mappings().first()

        seq_gap_row = db.session.execute(
            db.text(
                """
                WITH o AS (
                  SELECT
                    attempt_uuid,
                    seq,
                    seq - LAG(seq) OVER (PARTITION BY attempt_uuid ORDER BY seq) AS gap
                  FROM mixing_attempt_events
                )
                SELECT
                  COUNT(*) FILTER (WHERE gap IS NOT NULL AND gap > 1)::bigint AS n_seq_gaps,
                  COUNT(*)::bigint AS n_events_with_prev
                FROM o
                """
            )
        ).mappings().first()

        client_server_offset_row = db.session.execute(
            db.text(
                """
                SELECT
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY (
                    (client_ts_ms::double precision / 1000.0)
                    - EXTRACT(EPOCH FROM server_ts)
                  ))::double precision AS median_client_minus_server_sec
                FROM mixing_attempt_events
                """
            )
        ).mappings().first()

        def _f_corr(key, row):
            if not row or row.get(key) is None:
                return None
            return float(row[key])

        correlations = {
            'corr_trial_index_num_steps': _f_corr('corr_trial_index_num_steps', corr_row),
            'corr_interstep_ms_step_gain': _f_corr('corr_interstep_ms_step_gain', corr_step_row),
            'corr_final_delta_e_duration_sec': _f_corr('corr_final_de_duration', joint_corr_attempts),
            'corr_final_delta_e_num_steps': _f_corr('corr_final_de_num_steps', joint_corr_attempts),
            'corr_duration_sec_num_steps': _f_corr('corr_duration_num_steps', joint_corr_attempts),
        }

        strategy_by_target = build_strategy_summary_by_target()

        eda_framework = {
            'data_model': {
                'outcome_tables': ['mixing_attempts', 'mixing_sessions'],
                'process_table': 'mixing_attempt_events',
                'user_tables': ['users', 'user_progress', 'user_target_color_stats'],
                'join_spine': 'attempt_uuid  (attempts.events.sessions)',
            },
            'attempt_derived': dict(attempt_outcome_flags or {}),
            'histogram_final_delta_e': [dict(r) for r in hist_final_de_rows],
            'histogram_log_final_delta_e': [dict(r) for r in hist_log_final_de_rows],
            'histogram_duration_sec': [dict(r) for r in hist_duration_rows],
            'duration_by_perfect_band': [dict(r) for r in duration_by_perfect_rows],
            'trial_outcome_curves': [dict(r) for r in trial_outcome_curves],
            'target_difficulty_ranking': [dict(r) for r in target_difficulty_rows],
            'action_effectiveness_matrix': [dict(r) for r in action_effectiveness_rows],
            'step_gain_by_action_type': [dict(r) for r in step_gain_by_action_rows],
            'early_mid_late_step_behavior': [dict(r) for r in phase_behavior_rows],
            'reversal_oscillation': {
                'summary': dict(oscillation_summary or {}),
                'histogram_sign_changes_capped': [dict(r) for r in hist_oscillation_rows],
            },
            'trajectory_shape_mean_delta_e': [dict(r) for r in trajectory_shape_rows],
            'archetype_feature_percentiles': dict(archetype_feature_percentiles or {}),
            'mixing_sessions': {
                'overview': dict(mixing_sessions_overview or {}),
                'skip_by_target': [dict(r) for r in skip_by_target_rows],
                'skip_perception_calibration': [dict(r) for r in skip_perception_rows],
            },
            'user_heterogeneity': dict(user_skill_distribution or {}),
            'system_validation': {
                'counts': dict(data_integrity_row or {}),
                'sequence_gaps': dict(seq_gap_row or {}),
                'median_client_minus_server_sec': (client_server_offset_row or {}).get(
                    'median_client_minus_server_sec'
                ),
            },
        }

        return jsonify({
            'status': 'success',
            'overview': overview,
            'eda': {
                'missingness': {
                    'attempts': dict(missingness_row or {}),
                    'events': dict(event_missingness_row or {}),
                },
                'percentiles': {
                    'attempts': dict(attempt_percentiles or {}),
                    'decision_steps': dict(step_percentiles or {}),
                },
                'end_reasons': [dict(r) for r in end_reason_rows],
                'daily_attempt_volume': list(reversed([dict(r) for r in daily_rows])),
                'user_attempt_histogram': [dict(r) for r in user_bucket_rows],
                'top_target_colors': [dict(r) for r in top_targets_rows],
                'event_type_counts': [dict(r) for r in event_type_rows],
                'action_type_counts': [dict(r) for r in action_type_rows],
                'pairwise_correlations': correlations,
                'strategy_by_target': strategy_by_target,
                'framework': eda_framework,
            },
            'h1_learning': [dict(r) for r in trial_rows],
            'h2_difficulty': [dict(r) for r in difficulty_rows],
            'h3_random_search_check': [dict(r) for r in random_rows],
            'h4_time_cost': [dict(r) for r in time_rows],
            'h5_stopping_rule': {
                'by_final_delta_e_bucket': [dict(r) for r in stop_rows],
                'stop_dispersion': dict(stop_dispersion or {}),
            },
        })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


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
    email_sent = 0
    email_failed = 0
    email_skipped_unverified = 0

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

    reminder_users = User.query.filter(
        User.email.isnot(None),
        User.email_opt_in_reminders.is_(True),
    ).all()
    for user in reminder_users:
        if not user.email_verified_at:
            email_skipped_unverified += 1
            continue
        payload_dict = _build_push_payload(user.id)
        try:
            _send_email_message(
                to_email=user.email,
                subject=payload_dict.get('title') or 'ShadeMatch Daily Challenge',
                plain_text=(payload_dict.get('body') or "Today's daily challenge is live!") + "\n\nhttps://shadematch.app/",
            )
            email_sent += 1
        except Exception as exc:
            print(f'email reminder send failed for user {user.id}: {exc}')
            email_failed += 1

    if dead_endpoints:
        PushSubscription.query.filter(PushSubscription.endpoint.in_(dead_endpoints)).delete(synchronize_session=False)
        db.session.commit()

    return jsonify({
        'status': 'success',
        'sent': sent,
        'failed': failed,
        'cleaned': len(dead_endpoints),
        'email_sent': email_sent,
        'email_failed': email_failed,
        'email_skipped_unverified': email_skipped_unverified,
    })


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
