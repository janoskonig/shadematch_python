from . import db
from datetime import datetime


class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.String(6), primary_key=True)
    birthdate = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(20), nullable=False)
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
    frequency = db.Column(db.Integer, nullable=False, default=1)
    catalog_order = db.Column(db.Integer, nullable=False, unique=True)
    level_required = db.Column(db.Integer, nullable=False, default=1)


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