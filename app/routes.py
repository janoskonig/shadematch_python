from flask import Blueprint, render_template, request, jsonify
from .models import db, Session, MixingSession
from .utils import calculate_delta_e

main = Blueprint('main', __name__)

@main.route('/')
def index():
    return render_template('index.html')

@main.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()
    target = data['target']  # RGB: [r, g, b]
    mix = data['mixed']        # RGB: [r, g, b]

    delta_e = calculate_delta_e(target, mix)
    return jsonify({'delta_e': delta_e})

@main.route('/save_session', methods=['POST'])
def save_session():
    data = request.get_json()
    session = MixingSession(
        user_id=data['userId'],
        target_r=data['target'][0],
        target_g=data['target'][1],
        target_b=data['target'][2],
        drop_white=data['drops']['white'],
        drop_black=data['drops']['black'],
        drop_red=data['drops']['red'],
        drop_yellow=data['drops']['yellow'],
        drop_blue=data['drops']['blue'],
        delta_e=data['deltaE'],
        time_sec=data['time'],
        timestamp=data['timestamp']
    )
    db.session.add(session)
    db.session.commit()
    return jsonify({'status': 'success'})