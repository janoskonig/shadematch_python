from flask import Blueprint, render_template, request, jsonify
from .models import db, Session
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