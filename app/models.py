from . import db
from datetime import datetime


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.String(6), primary_key=True)
    birthdate = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(20), nullable=False)
    # Optional public display name (leaderboard, challenge links). Uniqueness is
    # case-insensitive: enforced app-side (see routes._nickname_taken) and by a
    # partial unique index on LOWER(nickname) created in migrate_add_nickname.py.
    nickname = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(255), nullable=True, unique=True, index=True)
    email_verified_at = db.Column(db.DateTime, nullable=True)
    email_opt_in_reminders = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64))
    target_color = db.Column(db.String(32))
    final_mix = db.Column(db.String(32))
    drop_counts = db.Column(db.JSON)
    delta_e = db.Column(db.Float)
    elapsed_time = db.Column(db.Float)


class TargetColor(db.Model):
    __tablename__ = 'target_colors'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    color_type = db.Column(db.String(32), nullable=False)
    classification = db.Column(db.String(64), nullable=True)
    r = db.Column(db.Integer, nullable=False)
    g = db.Column(db.Integer, nullable=False)
    b = db.Column(db.Integer, nullable=False)
    catalog_order = db.Column(db.Integer, nullable=False, unique=True)
    # Optional recipe from lab saves (null for seeded catalog colors).
    drop_white = db.Column(db.Integer, nullable=True)
    drop_black = db.Column(db.Integer, nullable=True)
    drop_red = db.Column(db.Integer, nullable=True)
    drop_yellow = db.Column(db.Integer, nullable=True)
    drop_blue = db.Column(db.Integer, nullable=True)
    # Which mixing model + input mode produced this saved recipe (lab tagging).
    mixing_model = db.Column(db.String(16), nullable=True)   # 'rgb' | 'spectral'
    input_mode = db.Column(db.String(16), nullable=True)     # 'integer' | 'dialer'


class MixingSession(db.Model):
    __tablename__ = 'mixing_sessions'

    id = db.Column(db.Integer, primary_key=True)
    attempt_uuid = db.Column(db.String(36), nullable=True, unique=True)
    user_id = db.Column(db.String(6), nullable=False)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=True)
    target_r = db.Column(db.Integer)
    target_g = db.Column(db.Integer)
    target_b = db.Column(db.Integer)
    drop_white = db.Column(db.Integer)
    drop_black = db.Column(db.Integer)
    drop_red = db.Column(db.Integer)
    drop_yellow = db.Column(db.Integer)
    drop_blue = db.Column(db.Integer)
    delta_e = db.Column(db.Float)
    time_sec = db.Column(db.Float)
    timestamp = db.Column(db.DateTime)
    skipped = db.Column(db.Boolean, default=False)
    skip_perception = db.Column(db.String(32), nullable=True)
    # perfect | no_perceivable_difference | acceptable_difference | big_difference | stopped
    match_category = db.Column(db.String(40), nullable=True)
    # Mixing configuration used for this attempt.
    mixing_model = db.Column(db.String(16), nullable=True)   # 'rgb' | 'spectral'
    input_mode = db.Column(db.String(16), nullable=True)     # 'integer' | 'dialer'


class MixingAttempt(db.Model):
    __tablename__ = 'mixing_attempts'

    attempt_uuid = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=True)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=True)
    target_r = db.Column(db.Integer, nullable=True)
    target_g = db.Column(db.Integer, nullable=True)
    target_b = db.Column(db.Integer, nullable=True)

    initial_drop_white = db.Column(db.Integer, nullable=False, default=0)
    initial_drop_black = db.Column(db.Integer, nullable=False, default=0)
    initial_drop_red = db.Column(db.Integer, nullable=False, default=0)
    initial_drop_yellow = db.Column(db.Integer, nullable=False, default=0)
    initial_drop_blue = db.Column(db.Integer, nullable=False, default=0)
    initial_mixed_r = db.Column(db.Integer, nullable=False, default=255)
    initial_mixed_g = db.Column(db.Integer, nullable=False, default=255)
    initial_mixed_b = db.Column(db.Integer, nullable=False, default=255)
    initial_delta_e = db.Column(db.Float, nullable=True)

    attempt_started_client_ts_ms = db.Column(db.BigInteger, nullable=True)
    attempt_started_server_ts = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    first_action_client_ts_ms = db.Column(db.BigInteger, nullable=True)
    first_action_server_ts = db.Column(db.DateTime, nullable=True)
    attempt_ended_client_ts_ms = db.Column(db.BigInteger, nullable=True)
    attempt_ended_server_ts = db.Column(db.DateTime, nullable=True)
    end_reason = db.Column(db.String(32), nullable=True)
    app_version = db.Column(db.String(64), nullable=True)
    final_delta_e = db.Column(db.Float, nullable=True)
    duration_sec = db.Column(db.Float, nullable=True)
    num_steps = db.Column(db.Integer, nullable=True)
    # Passive client environment snapshot (screen, viewport, color_gamut,
    # fullscreen, locale, timezone, ...) — first-write-wins per attempt so we
    # capture the environment at attempt-start time and ignore later updates.
    client_env_json = db.Column(db.JSON, nullable=True)
    # Mixing configuration used for this attempt.
    mixing_model = db.Column(db.String(16), nullable=True)   # 'rgb' | 'spectral'
    input_mode = db.Column(db.String(16), nullable=True)     # 'integer' | 'dialer'


class MixingAttemptEvent(db.Model):
    __tablename__ = 'mixing_attempt_events'

    id = db.Column(db.Integer, primary_key=True)
    attempt_uuid = db.Column(
        db.String(36),
        db.ForeignKey('mixing_attempts.attempt_uuid', ondelete='CASCADE'),
        nullable=False,
    )
    seq = db.Column(db.Integer, nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    action_color = db.Column(db.String(32), nullable=True)
    client_ts_ms = db.Column(db.BigInteger, nullable=False)
    server_ts = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    state_before_json = db.Column(db.JSON, nullable=False)
    state_after_json = db.Column(db.JSON, nullable=False)
    metadata_json = db.Column(db.JSON, nullable=True)
    step_index = db.Column(db.Integer, nullable=True)
    time_since_prev_step_ms = db.Column(db.BigInteger, nullable=True)
    action_type = db.Column(db.String(16), nullable=True)
    amount = db.Column(db.Integer, nullable=True)
    delta_e_before = db.Column(db.Float, nullable=True)
    delta_e_after = db.Column(db.Float, nullable=True)
    mix_before_r = db.Column(db.SmallInteger, nullable=True)
    mix_before_g = db.Column(db.SmallInteger, nullable=True)
    mix_before_b = db.Column(db.SmallInteger, nullable=True)
    mix_after_r = db.Column(db.SmallInteger, nullable=True)
    mix_after_g = db.Column(db.SmallInteger, nullable=True)
    mix_after_b = db.Column(db.SmallInteger, nullable=True)
    # Mixing configuration (denormalised from the parent attempt for easy per-event analysis).
    mixing_model = db.Column(db.String(16), nullable=True)   # 'mixbox' | 'spectral'
    input_mode = db.Column(db.String(16), nullable=True)     # 'integer' | 'dialer'

    __table_args__ = (
        db.UniqueConstraint('attempt_uuid', 'seq', name='uq_mixing_attempt_events_attempt_seq'),
        db.Index('idx_mixing_attempt_events_attempt_client_ts', 'attempt_uuid', 'client_ts_ms'),
        db.Index('idx_mixing_attempt_events_type_server_ts', 'event_type', 'server_ts'),
    )


class UserProgress(db.Model):
    __tablename__ = 'user_progress'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False, unique=True)
    xp = db.Column(db.Integer, nullable=False, default=0)
    level = db.Column(db.Integer, nullable=False, default=1)
    current_streak = db.Column(db.Integer, nullable=False, default=0)
    longest_streak = db.Column(db.Integer, nullable=False, default=0)
    last_activity_date = db.Column(db.Date, nullable=True)
    streak_freeze_available = db.Column(db.Integer, nullable=False, default=0)
    max_sum_drop_unlocked = db.Column(db.Integer, nullable=False, default=4)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)


class UserTargetColorStats(db.Model):
    __tablename__ = 'user_target_color_stats'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=False)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    completed_count = db.Column(db.Integer, nullable=False, default=0)
    best_delta_e = db.Column(db.Float, nullable=True)
    last_attempt_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'target_color_id', name='uq_user_target_color_stats'),
    )


class UserAward(db.Model):
    __tablename__ = 'user_awards'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    award_key = db.Column(db.String(128), nullable=False)
    award_scope = db.Column(db.String(16), nullable=False, default='lifetime')
    award_scope_key = db.Column(db.String(32), nullable=False, default='lifetime')
    metadata_json = db.Column(db.JSON, nullable=True)
    unlocked_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'award_key', 'award_scope', 'award_scope_key',
            name='uq_user_award',
        ),
    )


class ChallengeLink(db.Model):
    """Head-to-head challenge minted from a completed round.

    The creator's result is snapshotted (immutable) at creation; the target RGB
    is denormalised so the link keeps working even if the catalog row changes.
    Challenge rounds are quota-neutral (see gamification.process_progression
    is_challenge) so links can't be used to farm band unlocks.
    """
    __tablename__ = 'challenge_links'

    code = db.Column(db.String(8), primary_key=True)
    creator_user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    source_attempt_uuid = db.Column(db.String(36), nullable=True, unique=True)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=True)
    target_r = db.Column(db.Integer, nullable=False)
    target_g = db.Column(db.Integer, nullable=False)
    target_b = db.Column(db.Integer, nullable=False)
    creator_delta_e = db.Column(db.Float, nullable=True)
    creator_drops = db.Column(db.Integer, nullable=True)
    creator_time_sec = db.Column(db.Float, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    accept_count = db.Column(db.Integer, nullable=False, default=0)
    beat_count = db.Column(db.Integer, nullable=False, default=0)


class DailyChallengeRun(db.Model):
    __tablename__ = 'daily_challenge_runs'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    challenge_date = db.Column(db.Date, nullable=False)
    attempt_uuid = db.Column(db.String(36), nullable=False, unique=True)
    score_primary = db.Column(db.Float, nullable=True)
    score_secondary = db.Column(db.Integer, nullable=True)
    is_final = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            'user_id', 'challenge_date', 'attempt_uuid',
            name='uq_daily_run_uuid',
        ),
    )


class DailyChallengeWinner(db.Model):
    __tablename__ = 'daily_challenge_winners'

    id = db.Column(db.Integer, primary_key=True)
    challenge_date = db.Column(db.Date, nullable=False, unique=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    score_primary = db.Column(db.Float, nullable=True)
    score_secondary = db.Column(db.Integer, nullable=True)
    resolved_at = db.Column(db.DateTime, default=datetime.utcnow)


class PushSubscription(db.Model):
    __tablename__ = 'push_subscriptions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmailVerificationToken(db.Model):
    __tablename__ = 'email_verification_tokens'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False, index=True)
    purpose = db.Column(db.String(32), nullable=False, default='verify_email')
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ConsentRecord(db.Model):
    """Auditable record that a participant gave research informed consent.

    Written atomically at /register for new participants, and via
    /research-consent for participants who registered before consent
    capture existed. One row per (user_id, consent_version) agreement.
    """
    __tablename__ = 'consent_records'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False, index=True)
    purpose = db.Column(db.String(64), nullable=False, default='research_informed_consent')
    consent_version = db.Column(db.String(32), nullable=False)
    consent_text_hash = db.Column(db.String(64), nullable=True)  # sha256 of exact text shown
    locale = db.Column(db.String(8), nullable=True)
    user_agent = db.Column(db.String(255), nullable=True)
    consented_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # agreement moment
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)    # row write

    __table_args__ = (
        db.UniqueConstraint('user_id', 'consent_version', name='uq_consent_user_version'),
    )


class AnalyticsEvent(db.Model):
    """
    Lightweight client-side event log.
    Events: app_opened | app_ready | first_palette_interaction | save_attempt
            | instruction_acknowledged | fullscreen_change | visibility_change
    Metadata must always include client_session_id (UUID per browser session).
    Metadata typically also carries `device` — a passive environment snapshot
    (screen, viewport, color_gamut, fullscreen, locale, timezone, etc.).
    """
    __tablename__ = 'analytics_events'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), nullable=True)
    event = db.Column(db.String(64), nullable=False)
    ts = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    metadata_json = db.Column(db.JSON, nullable=True)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)


class CalibrationSession(db.Model):
    """One run of the /calibration psychophysics game: a block of controlled-ΔE colour
    pairs judged identical/acceptable/unacceptable. The standalone instrument for estimating
    the 50:50 perceptibility/acceptability thresholds (see app/calibration.py)."""
    __tablename__ = 'calibration_sessions'

    session_uuid = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=True)
    seed = db.Column(db.BigInteger, nullable=True)            # RNG seed → reproducible block
    mode = db.Column(db.String(24), nullable=False, default='constant_stimuli')
    illuminant = db.Column(db.String(8), nullable=False, default='D65')
    n_trials = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ended_at = db.Column(db.DateTime, nullable=True)
    # Summary written at finish (app/calibration.summarize): thresholds + QC.
    perceptibility_de = db.Column(db.Float, nullable=True)
    acceptability_de = db.Column(db.Float, nullable=True)
    catch_pass_rate = db.Column(db.Float, nullable=True)
    low_quality = db.Column(db.Boolean, nullable=True)
    summary_json = db.Column(db.JSON, nullable=True)
    # Passive client environment snapshot (screen, viewport, color_gamut, ...).
    client_env_json = db.Column(db.JSON, nullable=True)

    __table_args__ = (
        # Progress lookup: completed sessions for a user, oldest-first.
        db.Index('idx_calibration_sessions_user_started', 'user_id', 'started_at'),
    )


class CalibrationTrial(db.Model):
    """A single colour-pair trial in a CalibrationSession. The *true* ΔE and catch flag are
    stored here at generation time and never sent to the client (the ΔE is hidden from the
    player); the judgment + reaction time are written when the player responds."""
    __tablename__ = 'calibration_trials'

    id = db.Column(db.Integer, primary_key=True)
    session_uuid = db.Column(
        db.String(36),
        db.ForeignKey('calibration_sessions.session_uuid', ondelete='CASCADE'),
        nullable=False,
    )
    seq = db.Column(db.Integer, nullable=False)              # presentation order (0-based)
    center_name = db.Column(db.String(48), nullable=True)
    center_lab_json = db.Column(db.JSON, nullable=True)      # [L, a, b] of colour 1
    lab2_json = db.Column(db.JSON, nullable=True)            # [L, a, b] of colour 2
    target_de = db.Column(db.Float, nullable=True)          # requested ΔE₀₀
    actual_de = db.Column(db.Float, nullable=True)          # achieved ΔE₀₀ (used in the fit)
    rgb1_json = db.Column(db.JSON, nullable=True)
    rgb2_json = db.Column(db.JSON, nullable=True)
    in_gamut = db.Column(db.Boolean, nullable=True)
    is_catch = db.Column(db.Boolean, nullable=False, default=False)
    catch_kind = db.Column(db.String(16), nullable=True)    # 'identical' | 'obvious'
    # Response (null until judged):
    judgment = db.Column(db.String(16), nullable=True)      # identical|acceptable|unacceptable
    reaction_ms = db.Column(db.Integer, nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint('session_uuid', 'seq', name='uq_calibration_trial_seq'),
        db.Index('idx_calibration_trials_session', 'session_uuid'),
    )

class ProbeSlot(db.Model):
    """
    Experimental probe-round ledger for the learning-effect study.

    A probe slot is assigned server-side (seeded randomization) and later bound
    to the mixing attempt that fulfils it. Probe rounds are quota-neutral: they
    never touch UserTargetColorStats and are excluded from quota/level
    computation (see gamification.process_progression(is_probe=True)).
    Assignment-time snapshots are stored so the randomization stays auditable
    even if the surrounding logic changes later.

    arm: 'repeat'        — a colour the user has played before
         'matched_new'   — an unplayed colour matched on difficulty, within the
                           user's unlocked sum-drop band
         'repeat_short' / 'repeat_long' — fallback contrast (recency of the
                           repeated exposure) when the band has no unplayed colour
    channel: 'flow' (in-game probe slot) | 'daily' (daily-challenge carrier)
    """
    __tablename__ = 'probe_slots'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), db.ForeignKey('users.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    channel = db.Column(db.String(8), nullable=False, default='flow')
    slot_index = db.Column(db.Integer, nullable=False)
    arm = db.Column(db.String(16), nullable=False)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=False)
    seed = db.Column(db.String(64), nullable=False)
    policy_version = db.Column(db.String(16), nullable=False, default='probe-v1')

    # Assignment-time snapshots (computed from mixing_sessions, NOT from
    # user_target_color_stats — the latter excludes probe rounds by design,
    # while exposure/memory dose must include them).
    prior_exposure_count = db.Column(db.Integer, nullable=False, default=0)
    last_exposure_at = db.Column(db.DateTime, nullable=True)
    rounds_since_last_exposure = db.Column(db.Integer, nullable=True)
    cumulative_prior_rounds = db.Column(db.Integer, nullable=False, default=0)
    level_at_assignment = db.Column(db.Integer, nullable=True)
    cap_at_assignment = db.Column(db.Integer, nullable=True)

    attempt_uuid = db.Column(
        db.String(36), db.ForeignKey('mixing_attempts.attempt_uuid'),
        nullable=True, unique=True,
    )
    # assigned -> served (attempt bound) -> completed | skipped; 'expired' if
    # superseded before being played.
    status = db.Column(db.String(16), nullable=False, default='assigned')

    __table_args__ = (
        db.Index('idx_probe_slots_user_status', 'user_id', 'status'),
    )


class ProbeSchedule(db.Model):
    """
    Planned daily-challenge probe rotation: which probe colour appears on which
    date (and the how-manyth return of that colour this is). Multiple rows per
    date are allowed (position orders them); dates without rows fall back to
    the regular seeded daily selection.
    """
    __tablename__ = 'probe_schedule'

    id = db.Column(db.Integer, primary_key=True)
    challenge_date = db.Column(db.Date, nullable=False)
    target_color_id = db.Column(db.Integer, db.ForeignKey('target_colors.id'), nullable=False)
    position = db.Column(db.Integer, nullable=False, default=0)
    rotation_cycle = db.Column(db.Integer, nullable=True)
    notes = db.Column(db.String(255), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('challenge_date', 'target_color_id',
                            name='uq_probe_schedule_date_color'),
        db.Index('idx_probe_schedule_date', 'challenge_date'),
    )
