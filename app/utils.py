from colormath.color_objects import sRGBColor, LabColor
from colormath.color_conversions import convert_color
from colormath import color_diff_matrix
import numpy as np
from scipy.optimize import minimize
from scipy.interpolate import interp1d


def load_cie_data():
    # Wavelength range 400-700nm in 10nm steps (31 points)
    wavelengths = np.arange(400, 701, 10)
    
    # CIE 1931 color matching functions (31 points for 400-700nm in 10nm steps)
    # These values are interpolated to match the 31 wavelength points
    x_bar = np.array([0.0143, 0.0435, 0.1344, 0.2839, 0.3483, 0.3362, 0.2908, 0.1954, 0.0956, 0.0320, 0.0049, 0.0093, 0.0633, 0.1655, 0.2904, 0.4334, 0.5945, 0.7621, 0.9163, 1.0263, 1.0622, 1.0026, 0.8544, 0.6424, 0.4479, 0.2835, 0.1649, 0.0874, 0.0468, 0.0227, 0.0114])
    y_bar = np.array([0.0004, 0.0012, 0.0040, 0.0116, 0.023, 0.038, 0.060, 0.091, 0.139, 0.208, 0.323, 0.503, 0.710, 0.862, 0.954, 0.995, 0.995, 0.952, 0.870, 0.757, 0.631, 0.503, 0.381, 0.265, 0.175, 0.107, 0.061, 0.032, 0.017, 0.0082, 0.0041])
    z_bar = np.array([0.0679, 0.2074, 0.6456, 1.3856, 1.7471, 1.7721, 1.6692, 1.2876, 0.8130, 0.4652, 0.2720, 0.1582, 0.0782, 0.0422, 0.0203, 0.0087, 0.0039, 0.0021, 0.0017, 0.0011, 0.0008, 0.0003, 0.0002, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000, 0.0000])
    
    # Ensure arrays have the same length
    assert len(wavelengths) == len(x_bar) == len(y_bar) == len(z_bar), "CIE data arrays must have the same length"
    
    return wavelengths, x_bar, y_bar, z_bar


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
            recipe[pigment_name] = int(round(drops))  # Convert to integer
    
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