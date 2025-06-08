from flask import Blueprint, render_template, request, jsonify
from datetime import datetime
from . import db
from .models import User, MixingSession
import random
import string
from .utils import calculate_delta_e
import pandas as pd
import os
import numpy as np

main = Blueprint('main', __name__)

def generate_user_id():
    """Generate a random 6-character user ID"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

@main.route('/')
def index():
    return render_template('index.html')

@main.route('/spectral')
def spectral():
    # Load CIE data
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    
    # Define pigment spectra for all colors
    pigments = {
        'red': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 600 else 0.9 for w in wavelengths]  # High reflectance in red region
        },
        'yellow': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 500 else 0.9 for w in wavelengths]  # High reflectance in yellow region
        },
        'blue': {
            'wavelengths': wavelengths,
            'reflectances': [0.9 if w < 500 else 0.1 for w in wavelengths]  # High reflectance in blue region
        },
        'orange': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 550 else 0.9 for w in wavelengths]  # High reflectance in orange region
        },
        'brown': {
            'wavelengths': wavelengths,
            'reflectances': [0.3 if w < 500 else 0.7 for w in wavelengths]  # Moderate reflectance across spectrum
        },
        'green': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 500 or w > 600 else 0.9 for w in wavelengths]  # High reflectance in green region
        },
        'purple': {
            'wavelengths': wavelengths,
            'reflectances': [0.9 if w < 450 or w > 650 else 0.1 for w in wavelengths]  # High reflectance in purple region
        }
    }
    
    # Generate individual spectrum plots and calculate RGB values
    spectrum_plots = {}
    for color, data in pigments.items():
        # Convert spectrum to XYZ
        X, Y, Z = spectrum_to_xyz(data['reflectances'], wavelengths, x_bar, y_bar, z_bar)
        # Convert XYZ to RGB
        r, g, b = xyz_to_rgb(X, Y, Z)
        
        spectrum_plots[color] = {
            'wavelengths': data['wavelengths'].tolist(),
            'reflectances': data['reflectances'],
            'rgb': [int(r), int(g), int(b)]
        }
    
    return render_template('spectral_mixer.html', spectrum_plots=spectrum_plots)

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
        # Debug database connection
        print('Database URI:', db.engine.url)
        print('Database connected:', db.engine.pool.checkedin())
        
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
        print('Created session object:', session)
        db.session.add(session)
        print('Added session to db.session')
        db.session.commit()
        print('Session saved successfully')
        return jsonify({'status': 'success'})
    except Exception as e:
        print('Error saving session:', str(e))
        print('Error type:', type(e).__name__)
        db.session.rollback()
        return jsonify({'status': 'error', 'error': str(e)}), 500

def load_cie_data():
    # Wavelength range 400-700nm
    wavelengths = np.arange(400, 701)
    
    # CIE 1931 color matching functions (simplified)
    x_bar = np.array([0.0143, 0.0435, 0.1344, 0.2839, 0.3483, 0.3362, 0.2908, 0.1954, 0.0956, 0.0320, 0.0049, 0.0093, 0.0633, 0.1655, 0.2904, 0.4334, 0.5945, 0.7621, 0.9163, 1.0263, 1.0622, 1.0026, 0.8544, 0.6424, 0.4479, 0.2835, 0.1649, 0.0874, 0.0468, 0.0227, 0.0114])
    y_bar = np.array([0.0004, 0.0012, 0.0040, 0.0116, 0.023, 0.038, 0.060, 0.091, 0.139, 0.208, 0.323, 0.503, 0.710, 0.862, 0.954, 0.995, 0.995, 0.952, 0.870, 0.757, 0.631, 0.503, 0.381, 0.265, 0.175, 0.107, 0.061, 0.032, 0.017, 0.0082, 0.0041])
    z_bar = np.array([0.0679, 0.2074, 0.6456, 1.3856, 1.7471, 1.7721, 1.6692, 1.2876, 0.8130, 0.4652, 0.2720, 0.1582, 0.0782, 0.0422, 0.0203, 0.0087, 0.0039, 0.0021, 0.0017, 0.0011, 0.0008, 0.0003, 0.0002, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000])
    
    return wavelengths, x_bar, y_bar, z_bar

def spectrum_to_xyz(spectrum, wavelengths, x_bar, y_bar, z_bar):
    """Convert spectrum to XYZ using CIE color matching functions"""
    # Interpolate color matching functions to match spectrum wavelengths
    x_interp = np.interp(wavelengths, np.arange(400, 701, 10), x_bar)
    y_interp = np.interp(wavelengths, np.arange(400, 701, 10), y_bar)
    z_interp = np.interp(wavelengths, np.arange(400, 701, 10), z_bar)
    
    # Calculate XYZ values
    X = np.sum(spectrum * x_interp)
    Y = np.sum(spectrum * y_interp)
    Z = np.sum(spectrum * z_interp)
    
    # Normalize
    sum_xyz = X + Y + Z
    if sum_xyz > 0:
        X = X / sum_xyz
        Y = Y / sum_xyz
        Z = Z / sum_xyz
    
    return X, Y, Z

def xyz_to_rgb(X, Y, Z):
    """Convert XYZ to RGB using sRGB transformation matrix"""
    # sRGB transformation matrix
    M = np.array([
        [ 3.2406, -1.5372, -0.4986],
        [-0.9689,  1.8758,  0.0415],
        [ 0.0557, -0.2040,  1.0570]
    ])
    
    # Transform XYZ to RGB
    rgb = np.dot(M, np.array([X, Y, Z]))
    
    # Gamma correction
    rgb = np.where(rgb > 0.0031308,
                   1.055 * np.power(rgb, 1/2.4) - 0.055,
                   12.92 * rgb)
    
    # Scale to 0-255 and clamp
    rgb = np.clip(rgb * 255, 0, 255)
    
    return rgb.astype(int)

@main.route('/color_inspector')
def color_inspector():
    # Load CIE data
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    
    # Read the Excel file
    excel_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'salata_csonkanyag_reflektancia.xlsx')
    if not os.path.exists(excel_path):
        return "Excel file not found", 404
    
    # Read the Excel file
    df = pd.read_excel(excel_path)
    
    # Get wavelengths and reflectance data
    # Strip 'nm' suffix and convert to float
    wavelengths = [float(col.replace('nm', '')) for col in df.columns[1:]]
    samples = []
    
    for _, row in df.iterrows():
        sample_name = row[0]
        # Convert percentage reflectances to 0-1 range
        reflectances = [r/100.0 for r in row[1:].tolist()]
        
        # Convert spectrum to XYZ
        X, Y, Z = spectrum_to_xyz(reflectances, wavelengths, x_bar, y_bar, z_bar)
        
        # Convert XYZ to RGB
        rgb = xyz_to_rgb(X, Y, Z)
        
        samples.append({
            'name': sample_name,
            'wavelengths': wavelengths,
            'reflectances': reflectances,
            'rgb': rgb.tolist()
        })
    
    return render_template('color_inspector.html', samples=samples)

@main.route('/mix_colors', methods=['POST'])
def mix_colors():
    data = request.get_json()
    drop_counts = data.get('dropCounts', {})
    
    # Load CIE data
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    
    # Define pigment spectra (same as in spectral route)
    pigments = {
        'red': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 600 else 0.9 for w in wavelengths]
        },
        'yellow': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 500 else 0.9 for w in wavelengths]
        },
        'blue': {
            'wavelengths': wavelengths,
            'reflectances': [0.9 if w < 500 else 0.1 for w in wavelengths]
        },
        'orange': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 550 else 0.9 for w in wavelengths]
        },
        'brown': {
            'wavelengths': wavelengths,
            'reflectances': [0.3 if w < 500 else 0.7 for w in wavelengths]
        },
        'green': {
            'wavelengths': wavelengths,
            'reflectances': [0.1 if w < 500 or w > 600 else 0.9 for w in wavelengths]
        },
        'purple': {
            'wavelengths': wavelengths,
            'reflectances': [0.9 if w < 450 or w > 650 else 0.1 for w in wavelengths]
        }
    }
    
    # Calculate mixed spectrum using subtractive mixing
    mixed_spectrum = np.ones(len(wavelengths))
    total_drops = sum(drop_counts.values())
    
    if total_drops > 0:
        for color, count in drop_counts.items():
            if count > 0 and color in pigments:
                # Apply subtractive mixing with normalized drop count
                # This prevents the values from becoming too small
                normalized_count = count / (total_drops * 0.5)  # Scale factor to prevent too rapid darkening
                mixed_spectrum *= np.array(pigments[color]['reflectances']) ** normalized_count
    
    # Ensure the spectrum doesn't get too dark
    mixed_spectrum = np.clip(mixed_spectrum, 0.01, 1.0)
    
    # Convert mixed spectrum to XYZ
    X, Y, Z = spectrum_to_xyz(mixed_spectrum, wavelengths, x_bar, y_bar, z_bar)
    # Convert XYZ to RGB
    r, g, b = xyz_to_rgb(X, Y, Z)
    
    return jsonify({
        'rgb': [int(r), int(g), int(b)],
        'spectrum': {
            'wavelengths': wavelengths.tolist(),
            'reflectances': mixed_spectrum.tolist()
        }
    })

@main.route('/color-test')
def color_test():
    return render_template('color_test.html')