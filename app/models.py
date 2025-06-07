from . import db

class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(64))
    target_color = db.Column(db.String(32))
    final_mix = db.Column(db.String(32))
    drop_counts = db.Column(db.JSON)
    delta_e = db.Column(db.Float)
    elapsed_time = db.Column(db.Float)

class MixingSession(db.Model):
    __tablename__ = 'mixing_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(6), nullable=False)
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