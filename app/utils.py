from colormath.color_objects import sRGBColor, LabColor
from colormath.color_conversions import convert_color
from colormath import color_diff_matrix
import numpy as np


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