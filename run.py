from flask import Flask, render_template, request, jsonify
from app import app
import os

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/spectral')
def spectral():
    return render_template('spectral_mixer.html')

@app.route('/calculate', methods=['POST'])
def calculate():
    data = request.get_json()
    target = data['target']
    mixed = data['mixed']
    
    # Calculate Delta E using colormath
    from colormath.color_objects import LabColor, sRGBColor
    from colormath.color_conversions import convert_color
    from colormath.color_diff import delta_e_cie2000
    
    # Convert RGB to Lab
    target_rgb = sRGBColor(target[0]/255, target[1]/255, target[2]/255)
    mixed_rgb = sRGBColor(mixed[0]/255, mixed[1]/255, mixed[2]/255)
    
    target_lab = convert_color(target_rgb, LabColor)
    mixed_lab = convert_color(mixed_rgb, LabColor)
    
    # Calculate Delta E
    delta_e = delta_e_cie2000(target_lab, mixed_lab)
    
    return jsonify({'delta_e': delta_e})

print("DATABASE_URL:", os.getenv("DATABASE_URL"))

if __name__ == '__main__':
    app.run(debug=True)