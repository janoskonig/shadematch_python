from colormath.color_objects import sRGBColor, LabColor
from colormath.color_conversions import convert_color
from colormath import color_diff_matrix
import numpy as np
from scipy.optimize import minimize
from scipy.interpolate import interp1d


def delta_e_cie2000(color1, color2, Kl=1, Kc=1, Kh=1):
    """
    Drop-in replacement for colormath's delta_e_cie2000 with NumPy compatibility.
    Accepts two LabColor objects and returns the CIE2000 delta E as a float.
    """
    def _get_lab_vector(color):
        return np.array([color.lab_l, color.lab_a, color.lab_b])

    color1_vector = _get_lab_vector(color1)
    color2_matrix = np.array([(color2.lab_l, color2.lab_a, color2.lab_b)])
    
    delta_e = color_diff_matrix.delta_e_cie2000(
        color1_vector, color2_matrix, Kl=Kl, Kc=Kc, Kh=Kh
    )[0]

    return delta_e.item() if hasattr(delta_e, 'item') else float(delta_e)


def calculate_delta_e(rgb1, rgb2):
    """
    Accepts two RGB colors as lists [R, G, B] in 0–255 range,
    converts them to Lab, and returns the CIE2000 delta E.
    """
    color1_rgb = sRGBColor(*[x / 255.0 for x in rgb1])
    color2_rgb = sRGBColor(*[x / 255.0 for x in rgb2])

    color1_lab = convert_color(color1_rgb, LabColor)
    color2_lab = convert_color(color2_rgb, LabColor)

    return delta_e_cie2000(color1_lab, color2_lab)


def spectrum_to_xyz(spectrum, wavelengths, x_bar, y_bar, z_bar):
    """
    Convert reflectance spectrum to XYZ color space.
    """
    # Create a common wavelength range for interpolation
    cie_wavelengths = np.arange(400, 701, 10)
    
    # Ensure we have valid data for interpolation
    if len(wavelengths) != len(spectrum):
        raise ValueError(f"Wavelength and spectrum arrays must have the same length. Got {len(wavelengths)} and {len(spectrum)}")
    
    # Filter out any invalid data points
    valid_mask = np.isfinite(spectrum) & np.isfinite(wavelengths)
    if not np.any(valid_mask):
        raise ValueError("No valid data points found")
    
    wavelengths_clean = np.array(wavelengths)[valid_mask]
    spectrum_clean = np.array(spectrum)[valid_mask]
    
    # Ensure we have at least 2 points for interpolation
    if len(wavelengths_clean) < 2:
        raise ValueError("Need at least 2 valid data points for interpolation")
    
    # Interpolate spectrum to match CIE wavelength range
    try:
        spectrum_interp = interp1d(wavelengths_clean, spectrum_clean, 
                                 bounds_error=False, fill_value=0)
        spectrum_cie = spectrum_interp(cie_wavelengths)
    except Exception as e:
        # Fallback: use nearest neighbor interpolation
        spectrum_interp = interp1d(wavelengths_clean, spectrum_clean, 
                                 kind='nearest', bounds_error=False, fill_value=0)
        spectrum_cie = spectrum_interp(cie_wavelengths)
    
    # Normalize spectrum to 0-1 range
    spectrum_cie = np.clip(spectrum_cie, 0, 1)
    
    # Ensure spectrum_cie has the same length as CIE data
    if len(spectrum_cie) != len(x_bar):
        raise ValueError(f"Spectrum interpolation result length ({len(spectrum_cie)}) doesn't match CIE data length ({len(x_bar)})")
    
    # Calculate XYZ using CIE color matching functions
    X = np.sum(spectrum_cie * x_bar)
    Y = np.sum(spectrum_cie * y_bar)
    Z = np.sum(spectrum_cie * z_bar)
    
    return X, Y, Z


def xyz_to_rgb(X, Y, Z):
    """
    Convert XYZ to RGB using sRGB color space.
    """
    # sRGB transformation matrix
    matrix = np.array([
        [3.2406, -1.5372, -0.4986],
        [-0.9689, 1.8758, 0.0415],
        [0.0557, -0.2040, 1.0570]
    ])
    
    # Apply transformation
    rgb_linear = matrix @ np.array([X, Y, Z])
    
    # Gamma correction
    rgb = np.where(rgb_linear > 0.0031308,
                   1.055 * np.power(rgb_linear, 1/2.4) - 0.055,
                   12.92 * rgb_linear)
    
    # Clip to 0-1 range
    rgb = np.clip(rgb, 0, 1)
    
    return rgb


def mix_spectra(recipe, pigments):
    """
    Mix spectra based on pigment recipe (drops).
    """
    # Normalize recipe to sum to 1
    total_drops = sum(recipe.values())
    if total_drops == 0:
        return np.zeros(31)  # 400-700nm range in 10nm steps (31 points)
    
    normalized_recipe = {k: v / total_drops for k, v in recipe.items()}
    
    # Create a common wavelength range matching CIE data (400-700nm in 10nm steps)
    wavelengths = np.arange(400, 701, 10)  # 31 points
    
    # Mix spectra
    mixed_spectrum = np.zeros(len(wavelengths))
    
    for pigment_name, ratio in normalized_recipe.items():
        pigment_data = pigments[pigment_name]
        
        # Ensure we have valid data for interpolation
        if len(pigment_data['wavelengths']) != len(pigment_data['reflectances']):
            continue
            
        valid_mask = np.isfinite(pigment_data['reflectances']) & np.isfinite(pigment_data['wavelengths'])
        if not np.any(valid_mask):
            continue
            
        wavelengths_clean = np.array(pigment_data['wavelengths'])[valid_mask]
        reflectances_clean = np.array(pigment_data['reflectances'])[valid_mask]
        
        # Interpolate pigment spectrum to common wavelength range
        try:
            pigment_interp = interp1d(wavelengths_clean, reflectances_clean,
                                    bounds_error=False, fill_value=0)
            pigment_spectrum = pigment_interp(wavelengths)
        except Exception as e:
            # Fallback: use nearest neighbor interpolation
            pigment_interp = interp1d(wavelengths_clean, reflectances_clean,
                                    kind='nearest', bounds_error=False, fill_value=0)
            pigment_spectrum = pigment_interp(wavelengths)
        
        mixed_spectrum += ratio * pigment_spectrum
    
    return mixed_spectrum


def objective_function(recipe_vector, target_xyz, pigments, x_bar, y_bar, z_bar):
    """
    Objective function for optimization - minimize color difference.
    """
    # Convert vector back to recipe dict
    pigment_names = list(pigments.keys())
    recipe = {pigment_names[i]: max(0, recipe_vector[i]) for i in range(len(pigment_names))}
    
    # Mix spectra
    mixed_spectrum = mix_spectra(recipe, pigments)
    
    # Create wavelength array matching CIE data
    wavelengths = np.arange(400, 701, 10)  # 31 points
    
    # Convert mixed spectrum to XYZ
    mixed_xyz = spectrum_to_xyz(mixed_spectrum, wavelengths, x_bar, y_bar, z_bar)
    
    # Calculate color difference
    color_diff = np.sqrt(np.sum((np.array(target_xyz) - np.array(mixed_xyz))**2))
    
    return color_diff


def reverse_engineer_recipe(target_xyz, pigments, x_bar, y_bar, z_bar):
    """
    Reverse engineer pigment recipe from target XYZ color.
    """
    pigment_names = list(pigments.keys())
    
    # Initial guess: equal amounts of all pigments
    initial_recipe = np.ones(len(pigment_names)) / len(pigment_names)
    
    # Bounds: non-negative amounts
    bounds = [(0, 10) for _ in pigment_names]
    
    # Optimize
    result = minimize(
        objective_function,
        initial_recipe,
        args=(target_xyz, pigments, x_bar, y_bar, z_bar),
        bounds=bounds,
        method='L-BFGS-B'
    )
    
    # Convert result to recipe dict
    recipe = {}
    for i, pigment_name in enumerate(pigment_names):
        drops = max(0, result.x[i])
        if drops > 0.01:  # Only include pigments with significant amounts
            recipe[pigment_name] = round(drops, 2)
    
    # Calculate final Delta E
    final_spectrum = mix_spectra(recipe, pigments)
    wavelengths = np.arange(400, 701, 10)  # 31 points
    final_xyz = spectrum_to_xyz(final_spectrum, wavelengths, x_bar, y_bar, z_bar)
    final_rgb = xyz_to_rgb(*final_xyz)
    target_rgb = xyz_to_rgb(*target_xyz)
    
    # Convert to 0-255 range for Delta E calculation
    final_rgb_255 = (final_rgb * 255).astype(int)
    target_rgb_255 = (target_rgb * 255).astype(int)
    
    delta_e = calculate_delta_e(target_rgb_255.tolist(), final_rgb_255.tolist())
    
    return recipe, delta_e