from flask import Blueprint, render_template, request, jsonify, send_from_directory, Response, current_app, redirect, url_for
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
from sqlalchemy import case, func, text
from sqlalchemy.exc import IntegrityError
from dotenv import dotenv_values

from .gamification import (
    process_progression,
    build_progress_response,
    get_quota_ordered_catalog,
    compute_quota_progress,
    grant_daily_champion,
    grant_daily_mission_awards,
    grant_daily_performance_awards,
    build_daily_missions,
    get_user_profile,
    compute_quota_progress,
    COVERAGE_QUOTA,
    STREAK_FREEZE_CAP,
    MIN_SUM_DROP_BAND,
    target_color_sum_drop,
    _effective_sum_cap,
)
from .next_action import build_next_action
from .stat_eda import (
    ALLOWED_PLOT_IDS,
    build_attempt_archetypes,
    build_recipe_similarity_summary,
    get_plot_png,
)
from .mixed_models_stat import get_mixed_models_summary

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


@main.route('/lab')
def lab_page():
    return render_template('lab.html')


@main.route('/performance')
def performance_page_redirect():
    return redirect(url_for('main.leaderboard_page'), code=302)


@main.route('/results')
def results_page():
    return render_template('results.html')


@main.route('/leaderboard')
def leaderboard_page():
    return render_template('leaderboard.html')


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
    if pid in ('fw_attempt_network', 'attempt_deltae_timeline', 'archetype_deltae_trajectories'):
        plot_options = {}
        au = request.args.get('attempt_uuid')
        if au and str(au).strip():
            plot_options['attempt_uuid'] = str(au).strip()
        tid = request.args.get('target_color_id', type=int)
        if tid is not None:
            plot_options['target_color_id'] = tid
        archetype = request.args.get('archetype')
        if archetype and str(archetype).strip():
            plot_options['archetype'] = str(archetype).strip()
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
            if not user.email_verified_at:
                return jsonify({
                    'status': 'error',
                    'message': 'Please verify your email before logging in.',
                    'code': 'EMAIL_NOT_VERIFIED',
                    'user_id': user.id,
                    'email': user.email,
                }), 403
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

def _target_color_public_dict(tc):
    """Shape for /api/target-colors."""
    entry = {
        'id': tc.id,
        'name': tc.name,
        'type': tc.color_type,
        'classification': tc.classification,
        'rgb': [tc.r, tc.g, tc.b],
        'catalog_order': tc.catalog_order,
    }
    s = target_color_sum_drop(tc)
    if s is not None:
        entry['sum_drop_count'] = s
    drops = _target_color_drops_for_api(tc)
    if drops is not None:
        entry['drops'] = drops
    return entry


@main.route('/api/target-colors', methods=['GET'])
def get_target_colors():
    user_id = request.args.get('user_id')
    rows = TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
    colors = [_target_color_public_dict(tc) for tc in rows]

    next_action_data = {}
    if user_id:
        quota = compute_quota_progress(user_id)
        colors = get_quota_ordered_catalog(user_id, colors, quota=quota)
        next_action_data = build_next_action(user_id, quota=quota)

    return jsonify({'status': 'success', 'colors': colors, **next_action_data})


def _target_color_drops_for_api(tc):
    """Lab recipe if present; otherwise omit from API shape."""
    raw = (
        tc.drop_white,
        tc.drop_black,
        tc.drop_red,
        tc.drop_yellow,
        tc.drop_blue,
    )
    if all(v is None for v in raw):
        return None
    keys = ('white', 'black', 'red', 'yellow', 'blue')
    return {keys[i]: (raw[i] if raw[i] is not None else 0) for i in range(5)}


def _parse_lab_drops_payload(data):
    """
    Accept drops: { white, black, red, yellow, blue } (integers).
    Returns tuple of five ints, or None if key absent (store SQL NULLs).
    """
    raw = data.get('drops')
    if not isinstance(raw, dict):
        raw = data.get('drop_counts')
    if not isinstance(raw, dict):
        return None

    def _one(key):
        v = _coerce_int_or_none(raw.get(key))
        if v is None:
            return 0
        return max(0, min(v, 50_000))

    return (
        _one('white'),
        _one('black'),
        _one('red'),
        _one('yellow'),
        _one('blue'),
    )


def _sync_target_colors_id_sequence_postgresql():
    """
    If rows were inserted with explicit ids (migrations, COPY), the SERIAL/IDENTITY
    sequence can lag behind MAX(id) and the next INSERT reuses an existing id.
    """
    if db.engine.dialect.name != 'postgresql':
        return
    db.session.execute(
        text(
            "SELECT setval("
            "pg_get_serial_sequence('target_colors', 'id'), "
            "COALESCE((SELECT MAX(id) FROM target_colors), 0)"
            ")"
        )
    )


@main.route('/api/lab/save-target-color', methods=['POST'])
def lab_save_target_color():
    """
    Append the current mixed RGB as a new catalog row (for experiments / custom targets).
    Rate-limited per IP to reduce abuse of open writes.
    """
    ip = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip() or (request.remote_addr or 'unknown')
    if not _rate_limit_allow(f'lab_save_target:{ip}', max_hits=40, window_sec=3600):
        return jsonify({'status': 'error', 'message': 'Too many saves from this address. Try again later.'}), 429

    data = request.get_json() or {}
    r = _coerce_int_or_none(data.get('r'))
    g = _coerce_int_or_none(data.get('g'))
    b = _coerce_int_or_none(data.get('b'))
    if r is None or g is None or b is None:
        return jsonify({'status': 'error', 'message': 'Integer fields r, g, b are required.'}), 400

    r = max(0, min(255, r))
    g = max(0, min(255, g))
    b = max(0, min(255, b))

    name_raw = (data.get('name') or '').strip()
    if not name_raw:
        name = f'Lab RGB({r},{g},{b})'
    else:
        name = name_raw[:128]

    max_order = db.session.query(func.max(TargetColor.catalog_order)).scalar()
    next_order = (int(max_order) if max_order is not None else -1) + 1

    drops_tuple = _parse_lab_drops_payload(data)

    def _build_row():
        kwargs = dict(
            name=name,
            color_type='lab',
            classification='custom',
            r=r,
            g=g,
            b=b,
            catalog_order=next_order,
        )
        if drops_tuple is not None:
            dw, dbn, dr, dy, dbl = drops_tuple
            kwargs['drop_white'] = dw
            kwargs['drop_black'] = dbn
            kwargs['drop_red'] = dr
            kwargs['drop_yellow'] = dy
            kwargs['drop_blue'] = dbl
        return TargetColor(**kwargs)

    _sync_target_colors_id_sequence_postgresql()
    tc = _build_row()
    db.session.add(tc)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        _sync_target_colors_id_sequence_postgresql()
        tc = _build_row()
        db.session.add(tc)
        db.session.commit()

    out = {
        'id': tc.id,
        'name': tc.name,
        'rgb': [r, g, b],
        'catalog_order': tc.catalog_order,
    }
    d = _target_color_drops_for_api(tc)
    if d is not None:
        out['drops'] = d

    return jsonify({
        'status': 'success',
        'target_color': out,
    })


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


@main.route('/api/leaderboard', methods=['POST'])
def get_leaderboard():
    data = request.get_json(silent=True) or {}
    current_user_id = (data.get('user_id') or '').strip().upper()
    limit = data.get('limit', 25)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 25

    try:
        rows = (
            db.session.query(
                User.id.label('user_id'),
                func.coalesce(UserProgress.xp, 0).label('xp'),
                func.coalesce(UserProgress.level, 1).label('level'),
                func.coalesce(UserProgress.current_streak, 0).label('current_streak'),
                func.count(MixingSession.id).label('total_sessions'),
                func.coalesce(func.sum(case((MixingSession.skipped.is_(False), 1), else_=0)), 0).label('completed_sessions'),
                func.coalesce(func.sum(
                    case((MixingSession.match_category == 'perfect', 1), else_=0)
                ), 0).label('perfect_count'),
                func.coalesce(func.sum(
                    case((MixingSession.match_category == 'no_perceivable_difference', 1), else_=0)
                ), 0).label('no_perceivable_diff_count'),
            )
            .select_from(User)
            .outerjoin(UserProgress, UserProgress.user_id == User.id)
            .outerjoin(MixingSession, MixingSession.user_id == User.id)
            .group_by(User.id, UserProgress.xp, UserProgress.level, UserProgress.current_streak)
            .all()
        )

        active_rows = [
            r for r in rows
            if int(r.total_sessions or 0) > 0 or int(r.xp or 0) > 0 or r.user_id == current_user_id
        ]

        def sort_key(row):
            return (
                -int(row.level or 1),
                -int(row.xp or 0),
                -int(row.completed_sessions or 0),
                -int(row.perfect_count or 0),
                -int(row.no_perceivable_diff_count or 0),
                row.user_id,
            )

        ranked_rows = sorted(active_rows, key=sort_key)
        entries_by_user = {}
        entries = []
        current_user_rank = None

        for rank, row in enumerate(ranked_rows, start=1):
            is_current_user = bool(current_user_id and row.user_id == current_user_id)
            if is_current_user:
                current_user_rank = rank

            entry = {
                'rank': rank,
                'display_name': f'You ({row.user_id})' if is_current_user else f'Player #{rank}',
                'is_current_user': is_current_user,
                'level': int(row.level or 1),
                'xp': int(row.xp or 0),
                'current_streak': int(row.current_streak or 0),
                'total_sessions': int(row.total_sessions or 0),
                'completed_sessions': int(row.completed_sessions or 0),
                'perfect_count': int(row.perfect_count or 0),
                'no_perceivable_diff_count': int(row.no_perceivable_diff_count or 0),
            }
            entries_by_user[row.user_id] = entry
            if rank <= limit:
                entries.append(entry)

        if current_user_id and current_user_id in entries_by_user:
            own_entry = entries_by_user[current_user_id]
            if not any(e['is_current_user'] for e in entries):
                own_entry = dict(own_entry)
                own_entry['outside_top'] = True
                entries.append(own_entry)

        return jsonify({
            'status': 'success',
            'leaderboard': entries,
            'total_ranked_users': len(ranked_rows),
            'current_user_rank': current_user_rank,
        })
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
    """Focused dashboard summary for /stat."""
    try:
        overview = db.session.execute(
            db.text(
                """
                SELECT
                  (SELECT COUNT(*)::bigint FROM users) AS registered_users,
                  (SELECT COUNT(*)::bigint FROM mixing_attempts) AS total_plays,
                  (SELECT COUNT(DISTINCT user_id)::bigint FROM mixing_attempts WHERE user_id IS NOT NULL) AS users_with_plays,
                  (SELECT MIN(attempt_started_server_ts)::text FROM mixing_attempts) AS first_play_ts,
                  (SELECT MAX(attempt_started_server_ts)::text FROM mixing_attempts) AS last_play_ts
                """
            )
        ).mappings().first()

        age_pyramid = db.session.execute(
            db.text(
                """
                WITH u AS (
                  SELECT
                    CASE
                      WHEN lower(coalesce(gender, 'unknown')) LIKE 'm%' THEN 'male'
                      WHEN lower(coalesce(gender, 'unknown')) LIKE 'f%' THEN 'female'
                      ELSE 'other'
                    END AS gender_group,
                    EXTRACT(YEAR FROM age(CURRENT_DATE, birthdate))::int AS age_years
                  FROM users
                  WHERE birthdate IS NOT NULL
                ),
                b AS (
                  SELECT
                    CASE
                      WHEN age_years < 18 THEN '<18'
                      WHEN age_years <= 24 THEN '18-24'
                      WHEN age_years <= 34 THEN '25-34'
                      WHEN age_years <= 44 THEN '35-44'
                      WHEN age_years <= 54 THEN '45-54'
                      WHEN age_years <= 64 THEN '55-64'
                      ELSE '65+'
                    END AS age_bucket,
                    gender_group
                  FROM u
                  WHERE age_years IS NOT NULL AND age_years >= 0
                )
                SELECT
                  age_bucket,
                  gender_group,
                  COUNT(*)::bigint AS n_users
                FROM b
                GROUP BY age_bucket, gender_group
                ORDER BY
                  CASE age_bucket
                    WHEN '<18' THEN 1
                    WHEN '18-24' THEN 2
                    WHEN '25-34' THEN 3
                    WHEN '35-44' THEN 4
                    WHEN '45-54' THEN 5
                    WHEN '55-64' THEN 6
                    ELSE 7
                  END,
                  gender_group
                """
            )
        ).mappings().all()

        plays_per_user = db.session.execute(
            db.text(
                """
                SELECT
                  user_id,
                  COUNT(*)::bigint AS n_plays
                FROM mixing_attempts
                WHERE user_id IS NOT NULL
                GROUP BY user_id
                ORDER BY n_plays DESC, user_id
                LIMIT 500
                """
            )
        ).mappings().all()

        attempts_per_color = db.session.execute(
            db.text(
                """
                SELECT
                  ma.target_color_id,
                  COALESCE(tc.name, '(unknown)') AS target_name,
                  COUNT(*)::bigint AS n_attempts
                FROM mixing_attempts ma
                LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                GROUP BY ma.target_color_id, COALESCE(tc.name, '(unknown)')
                ORDER BY n_attempts DESC, target_name
                """
            )
        ).mappings().all()

        delta_e_per_color = db.session.execute(
            db.text(
                """
                SELECT
                  COALESCE(tc.name, '(unknown)') AS target_name,
                  COUNT(*)::bigint AS n_attempts,
                  AVG(ma.final_delta_e)::double precision AS mean_delta_e,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY ma.final_delta_e)::double precision AS median_delta_e
                FROM mixing_attempts ma
                LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                WHERE ma.final_delta_e IS NOT NULL
                GROUP BY COALESCE(tc.name, '(unknown)')
                ORDER BY n_attempts DESC, target_name
                """
            )
        ).mappings().all()

        elapsed_per_color = db.session.execute(
            db.text(
                """
                SELECT
                  COALESCE(tc.name, '(unknown)') AS target_name,
                  COUNT(*)::bigint AS n_attempts,
                  AVG(ma.duration_sec)::double precision AS mean_elapsed_sec,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY ma.duration_sec)::double precision AS median_elapsed_sec
                FROM mixing_attempts ma
                LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                WHERE ma.duration_sec IS NOT NULL
                  AND ma.duration_sec <= 300
                GROUP BY COALESCE(tc.name, '(unknown)')
                ORDER BY n_attempts DESC, target_name
                """
            )
        ).mappings().all()

        controlled_by_attempt = db.session.execute(
            db.text(
                """
                WITH base AS (
                  SELECT
                    ma.user_id,
                    COALESCE(tc.name, '(unknown)') AS target_name,
                    ma.attempt_uuid,
                    ma.final_delta_e,
                    ma.duration_sec,
                    ma.attempt_started_server_ts,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id, COALESCE(tc.name, '(unknown)')
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS attempt_no
                  FROM mixing_attempts ma
                  LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
                  WHERE ma.user_id IS NOT NULL
                )
                SELECT
                  target_name,
                  attempt_no,
                  COUNT(*)::bigint AS n_attempts,
                  AVG(final_delta_e)::double precision AS mean_delta_e,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)::double precision AS median_delta_e,
                  AVG(duration_sec)::double precision AS mean_elapsed_sec,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)::double precision AS median_elapsed_sec
                FROM base
                WHERE attempt_no <= 10
                  AND (duration_sec IS NULL OR duration_sec <= 300)
                GROUP BY target_name, attempt_no
                HAVING COUNT(*) >= 2
                ORDER BY target_name, attempt_no
                """
            )
        ).mappings().all()

        archetypes = build_attempt_archetypes()
        recipe_similarity = build_recipe_similarity_summary()
        try:
            mm_raw = get_mixed_models_summary()
            mixed_models = {k: v for k, v in (mm_raw or {}).items() if k != 'text_summaries'}
        except Exception as mm_err:
            mixed_models = {'status': 'error', 'message': str(mm_err)}

        _skip_delta_e_sql = """
                SELECT
                  COUNT(*)::bigint AS n,
                  AVG(ms.delta_e)::double precision AS mean_delta_e,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY ms.delta_e)::double precision AS median_delta_e
                FROM mixing_sessions ms
                WHERE ms.skipped IS TRUE
                  AND ms.skip_perception = :perception
                  AND ms.delta_e IS NOT NULL
                """
        skip_identical_row = db.session.execute(
            db.text(_skip_delta_e_sql), {'perception': 'identical'}
        ).mappings().first()
        skip_acceptable_row = db.session.execute(
            db.text(_skip_delta_e_sql), {'perception': 'acceptable'}
        ).mappings().first()
        skip_unacceptable_row = db.session.execute(
            db.text(_skip_delta_e_sql), {'perception': 'unacceptable'}
        ).mappings().first()
        skipped_identical_delta_e = dict(skip_identical_row or {})
        skipped_acceptable_delta_e = dict(skip_acceptable_row or {})
        skipped_unacceptable_delta_e = dict(skip_unacceptable_row or {})
        first_attempt_below_2_row = db.session.execute(
            db.text(
                """
                WITH ranked AS (
                  SELECT
                    ma.user_id,
                    ma.target_color_id,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id, ma.target_color_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS attempt_no,
                    ma.final_delta_e
                  FROM mixing_attempts ma
                  WHERE ma.user_id IS NOT NULL
                    AND ma.target_color_id IS NOT NULL
                    AND ma.final_delta_e IS NOT NULL
                ),
                first_hit AS (
                  SELECT
                    user_id,
                    target_color_id,
                    MIN(attempt_no)::int AS first_attempt_no
                  FROM ranked
                  WHERE final_delta_e < 2.0
                  GROUP BY user_id, target_color_id
                )
                SELECT
                  COUNT(*)::bigint AS n,
                  AVG(first_attempt_no)::double precision AS mean_first_attempt_no,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY first_attempt_no)::double precision AS median_first_attempt_no
                FROM first_hit
                """
            )
        ).mappings().first()
        first_event_below_2_row = db.session.execute(
            db.text(
                """
                WITH first_hit AS (
                  SELECT
                    mae.attempt_uuid,
                    MIN(mae.step_index)::int AS first_step_index
                  FROM mixing_attempt_events mae
                  JOIN mixing_attempts ma ON ma.attempt_uuid = mae.attempt_uuid
                  WHERE ma.user_id IS NOT NULL
                    AND mae.step_index IS NOT NULL
                    AND mae.delta_e_after IS NOT NULL
                    AND mae.delta_e_after < 2.0
                  GROUP BY mae.attempt_uuid
                )
                SELECT
                  COUNT(*)::bigint AS n,
                  AVG(first_step_index)::double precision AS mean_first_step_index,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY first_step_index)::double precision AS median_first_step_index
                FROM first_hit
                """
            )
        ).mappings().first()
        first_attempt_below_2 = dict(first_attempt_below_2_row or {})
        first_event_below_2 = dict(first_event_below_2_row or {})
        first_attempt_below_2_by_type = db.session.execute(
            db.text(
                """
                WITH ranked AS (
                  SELECT
                    ma.user_id,
                    ma.target_color_id,
                    lower(coalesce(tc.color_type, '')) AS color_type,
                    ROW_NUMBER() OVER (
                      PARTITION BY ma.user_id, ma.target_color_id
                      ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
                    ) AS attempt_no,
                    ma.final_delta_e
                  FROM mixing_attempts ma
                  JOIN target_colors tc ON tc.id = ma.target_color_id
                  WHERE ma.user_id IS NOT NULL
                    AND ma.target_color_id IS NOT NULL
                    AND ma.final_delta_e IS NOT NULL
                ),
                first_hit AS (
                  SELECT
                    color_type,
                    user_id,
                    target_color_id,
                    MIN(attempt_no)::int AS first_attempt_no
                  FROM ranked
                  WHERE final_delta_e < 2.0
                    AND color_type IN ('basic', 'skin', 'lab')
                  GROUP BY color_type, user_id, target_color_id
                )
                SELECT
                  color_type,
                  COUNT(*)::bigint AS n,
                  AVG(first_attempt_no)::double precision AS mean_first_attempt_no,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY first_attempt_no)::double precision AS median_first_attempt_no
                FROM first_hit
                GROUP BY color_type
                ORDER BY CASE color_type WHEN 'basic' THEN 1 WHEN 'skin' THEN 2 WHEN 'lab' THEN 3 ELSE 99 END
                """
            )
        ).mappings().all()
        first_event_below_2_by_type = db.session.execute(
            db.text(
                """
                WITH first_hit AS (
                  SELECT
                    lower(coalesce(tc.color_type, '')) AS color_type,
                    mae.attempt_uuid,
                    MIN(mae.step_index)::int AS first_step_index
                  FROM mixing_attempt_events mae
                  JOIN mixing_attempts ma ON ma.attempt_uuid = mae.attempt_uuid
                  JOIN target_colors tc ON tc.id = ma.target_color_id
                  WHERE ma.user_id IS NOT NULL
                    AND mae.step_index IS NOT NULL
                    AND mae.delta_e_after IS NOT NULL
                    AND mae.delta_e_after < 2.0
                    AND lower(coalesce(tc.color_type, '')) IN ('basic', 'skin', 'lab')
                  GROUP BY lower(coalesce(tc.color_type, '')), mae.attempt_uuid
                )
                SELECT
                  color_type,
                  COUNT(*)::bigint AS n,
                  AVG(first_step_index)::double precision AS mean_first_step_index,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY first_step_index)::double precision AS median_first_step_index
                FROM first_hit
                GROUP BY color_type
                ORDER BY CASE color_type WHEN 'basic' THEN 1 WHEN 'skin' THEN 2 WHEN 'lab' THEN 3 ELSE 99 END
                """
            )
        ).mappings().all()

        return jsonify(
            {
                'status': 'success',
                'overview': dict(overview or {}),
                'age_pyramid': [dict(r) for r in age_pyramid],
                'plays_per_user': [dict(r) for r in plays_per_user],
                'attempts_per_color': [dict(r) for r in attempts_per_color],
                'delta_e_per_color': [dict(r) for r in delta_e_per_color],
                'elapsed_per_color': [dict(r) for r in elapsed_per_color],
                'controlled_by_attempt': [dict(r) for r in controlled_by_attempt],
                'archetypes': archetypes,
                'recipe_similarity': recipe_similarity,
                'mixed_models': mixed_models,
                'skipped_identical_delta_e': skipped_identical_delta_e,
                'skipped_acceptable_delta_e': skipped_acceptable_delta_e,
                'skipped_unacceptable_delta_e': skipped_unacceptable_delta_e,
                'first_attempt_below_2': first_attempt_below_2,
                'first_event_below_2': first_event_below_2,
                'first_attempt_below_2_by_type': [dict(r) for r in first_attempt_below_2_by_type],
                'first_event_below_2_by_type': [dict(r) for r in first_event_below_2_by_type],
            }
        )
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

    webpush = None
    WebPushException = None
    try:
        from pywebpush import webpush as _webpush, WebPushException as _WebPushException
        webpush = _webpush
        WebPushException = _WebPushException
    except ImportError:
        pass

    vapid_private = os.environ.get('VAPID_PRIVATE_KEY', '')
    vapid_public = os.environ.get('VAPID_PUBLIC_KEY', '')
    push_ready = bool(webpush and vapid_private and vapid_public)

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
            # Find nearest actionable deficit among sum-drop-eligible colors
            color_map = quota['color_quota_map']
            up = UserProgress.query.filter_by(user_id=user_id).first()
            cap = int(up.max_sum_drop_unlocked) if up else 4
            eff = _effective_sum_cap(cap)
            tc_rows = [
                tc for tc in TargetColor.query.order_by(TargetColor.catalog_order.asc()).all()
                if (s := target_color_sum_drop(tc)) is not None
                and MIN_SUM_DROP_BAND <= s <= eff
            ]
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

    if push_ready:
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
