import numpy as np
from flask import Flask, render_template, jsonify, request, send_file
import json
import matplotlib.pyplot as plt
import io
import base64

app = Flask(__name__)

# CIE 1931 color matching functions
def load_cie_data():
    # Wavelength range 400-700nm
    wavelengths = np.arange(400, 701)
    
    # CIE 1931 color matching functions (simplified)
    x_bar = np.array([0.0143, 0.0435, 0.1344, 0.2839, 0.3483, 0.3362, 0.2908, 0.1954, 0.0956, 0.0320, 0.0049, 0.0093, 0.0633, 0.1655, 0.2904, 0.4334, 0.5945, 0.7621, 0.9163, 1.0263, 1.0622, 1.0026, 0.8544, 0.6424, 0.4479, 0.2835, 0.1649, 0.0874, 0.0468, 0.0227, 0.0114])
    y_bar = np.array([0.0004, 0.0012, 0.0040, 0.0116, 0.023, 0.038, 0.060, 0.091, 0.139, 0.208, 0.323, 0.503, 0.710, 0.862, 0.954, 0.995, 0.995, 0.952, 0.870, 0.757, 0.631, 0.503, 0.381, 0.265, 0.175, 0.107, 0.061, 0.032, 0.017, 0.0082, 0.0041])
    z_bar = np.array([0.0679, 0.2074, 0.6456, 1.3856, 1.7471, 1.7721, 1.6692, 1.2876, 0.8130, 0.4652, 0.2720, 0.1582, 0.0782, 0.0422, 0.0203, 0.0087, 0.0039, 0.0021, 0.0017, 0.0011, 0.0008, 0.0003, 0.0002, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000])
    
    return wavelengths, x_bar, y_bar, z_bar

# Real pigment reflectance data
def load_pigment_data():
    pigments = {
        'red': {
            'name': 'Bengal Rose (PR169)',
            'wavelengths': [400, 450, 500, 550, 600, 650, 700],
            'reflectances': [0.15, 0.20, 0.25, 0.30, 0.85, 0.95, 0.98]
        },
        'green': {
            'name': 'Phthalo Green (PG7)',
            'wavelengths': [400, 450, 500, 550, 600, 650, 700],
            'reflectances': [0.10, 0.15, 0.90, 0.95, 0.20, 0.15, 0.10]
        },
        'blue': {
            'name': 'Phthalo Blue (PB15)',
            'wavelengths': [400, 450, 500, 550, 600, 650, 700],
            'reflectances': [0.90, 0.95, 0.20, 0.15, 0.10, 0.05, 0.05]
        }
    }
    return pigments

def interpolate_spectrum(wavelengths, values, target_wavelengths):
    """Interpolate spectrum to match target wavelengths"""
    return np.interp(target_wavelengths, wavelengths, values)

def spectrum_to_rgb(spectrum, wavelengths, x_bar, y_bar, z_bar):
    """Convert spectrum to RGB using CIE color matching functions"""
    # Calculate XYZ values
    X = np.sum(spectrum * x_bar)
    Y = np.sum(spectrum * y_bar)
    Z = np.sum(spectrum * z_bar)
    
    # Convert XYZ to RGB (simplified)
    R = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    G = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    B = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    
    # Clamp values to [0, 255]
    R = max(0, min(255, R * 255))
    G = max(0, min(255, G * 255))
    B = max(0, min(255, B * 255))
    
    return [int(R), int(G), int(B)]

def plot_spectrum(wavelengths, reflectances, color, title):
    """Create a spectrum plot using matplotlib"""
    plt.figure(figsize=(4, 2))
    plt.plot(wavelengths, reflectances, color=color, linewidth=2)
    plt.fill_between(wavelengths, reflectances, alpha=0.2, color=color)
    plt.title(title, fontsize=10)
    plt.xlabel('Wavelength (nm)', fontsize=8)
    plt.ylabel('Reflectance', fontsize=8)
    plt.grid(True, alpha=0.3)
    plt.xticks(fontsize=8)
    plt.yticks(fontsize=8)
    plt.tight_layout()
    
    # Save plot to a bytes buffer
    buf = io.BytesIO()
    plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    
    # Convert to base64
    img_str = base64.b64encode(buf.read()).decode('utf-8')
    return img_str

def calculate_mixed_color(drop_counts, pigments, wavelengths, x_bar, y_bar, z_bar):
    """Calculate mixed color based on drop counts"""
    total_drops = sum(drop_counts.values())
    if total_drops == 0:
        return [0, 0, 0], None
    
    # Initialize mixed spectrum
    mixed_spectrum = np.zeros_like(wavelengths, dtype=float)
    
    # Mix spectra using weighted average
    for color, count in drop_counts.items():
        if count > 0:
            weight = count / total_drops
            pigment = pigments[color]
            spectrum = interpolate_spectrum(
                pigment['wavelengths'],
                pigment['reflectances'],
                wavelengths
            )
            mixed_spectrum += spectrum * weight
    
    # Convert to RGB
    rgb = spectrum_to_rgb(mixed_spectrum, wavelengths, x_bar, y_bar, z_bar)
    
    # Create mixed spectrum plot
    mixed_plot = plot_spectrum(wavelengths, mixed_spectrum, 'purple', 'Mixed Spectrum')
    
    return rgb, mixed_plot

@app.route('/')
def index():
    # Load data
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    pigments = load_pigment_data()
    
    # Generate individual spectrum plots
    spectrum_plots = {}
    for color, pigment in pigments.items():
        spectrum = interpolate_spectrum(
            pigment['wavelengths'],
            pigment['reflectances'],
            wavelengths
        )
        spectrum_plots[color] = plot_spectrum(
            wavelengths,
            spectrum,
            color,
            f"{pigment['name']} Spectrum"
        )
    
    return render_template('spectral_mixer.html', spectrum_plots=spectrum_plots)

@app.route('/mix_colors', methods=['POST'])
def mix_colors():
    data = request.get_json()
    drop_counts = data.get('dropCounts', {})
    
    # Load data
    wavelengths, x_bar, y_bar, z_bar = load_cie_data()
    pigments = load_pigment_data()
    
    # Calculate mixed color and spectrum
    rgb, mixed_plot = calculate_mixed_color(drop_counts, pigments, wavelengths, x_bar, y_bar, z_bar)
    
    return jsonify({
        'rgb': rgb,
        'mixedSpectrum': mixed_plot
    })

if __name__ == '__main__':
    app.run(debug=True) 