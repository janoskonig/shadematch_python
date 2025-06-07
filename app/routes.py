from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
from . import db
from .models import User, MixingSession
import random
import string
from .utils import calculate_delta_e

main = Blueprint('main', __name__)

def generate_user_id():
    """Generate a random 6-character user ID"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@main.route('/')
def index():
    return render_template('index.html')

@main.route('/spectral')
def spectral():
    return render_template('spectral_mixer.html')

@main.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    birthdate = datetime.strptime(data['birthdate'], '%Y-%m-%d').date()
    gender = data['gender']
    
    # Generate a unique user ID
    user_id = generate_user_id()
    while User.query.get(user_id) is not None:
        user_id = generate_user_id()
    
    # Create new user
    user = User(
        id=user_id,
        birthdate=birthdate,
        gender=gender
    )
    
    db.session.add(user)
    db.session.commit()
    
    return jsonify({
        'status': 'success',
        'userId': user_id
    })

@main.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    user_id = data['userId']
    
    user = User.query.get(user_id)
    if user:
        return jsonify({
            'status': 'success',
            'birthdate': user.birthdate.isoformat(),
            'gender': user.gender
        })
    else:
        return jsonify({
            'status': 'error',
            'message': 'Invalid user ID'
        }), 404

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
    print('Received session data:', data)
    
    try:
        session = MixingSession(
            user_id=data['user_id'],
            target_r=data['target_r'],
            target_g=data['target_g'],
            target_b=data['target_b'],
            drop_white=data['drop_white'],
            drop_black=data['drop_black'],
            drop_red=data['drop_red'],
            drop_yellow=data['drop_yellow'],
            drop_blue=data['drop_blue'],
            delta_e=data['delta_e'],
            time_sec=data['time_sec'],
            timestamp=datetime.fromisoformat(data['timestamp'])
        )
        db.session.add(session)
        db.session.commit()
        print('Session saved successfully')
        return jsonify({'status': 'success'})
    except Exception as e:
        print('Error saving session:', str(e))
        db.session.rollback()
        return jsonify({'status': 'error', 'error': str(e)}), 500