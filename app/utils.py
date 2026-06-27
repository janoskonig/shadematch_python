"""Colour helpers.

The colour-matching data and the spectrum→XYZ→sRGB conversion stack now live in
the shared :mod:`app.color` package (single source of truth); this module
re-exports them under their historical names so existing imports keep working.

CIEDE2000 has two callers with different needs, kept deliberately distinct:

* :func:`delta_e_cie2000` / :func:`calculate_delta_e` here are the *authoritative*
  RGB scoring path used by the game (``/calculate``, ``save_session``). They stay
  on colormath's reference implementation so stored ΔE values are unchanged.
* The recipe solver's hot path uses the fast vectorised
  :func:`app.color.distance.ciede2000` (batched, no per-call object creation).

The two agree to ~1e-4 (asserted by the tests), so they cannot silently diverge.
"""
import numpy as np
from colormath.color_objects import sRGBColor, LabColor
from colormath.color_conversions import convert_color
from colormath import color_diff_matrix

# Re-exported for backward compatibility (e.g. `from .utils import spectrum_to_xyz`).
from .color.convert import load_cie_data, spectrum_to_xyz, xyz_to_rgb  # noqa: F401

__all__ = [
    "load_cie_data", "spectrum_to_xyz", "xyz_to_rgb",
    "delta_e_cie2000", "calculate_delta_e",
]


def delta_e_cie2000(color1, color2, Kl=1, Kc=1, Kh=1):
    """CIE2000 ΔE between two ``LabColor`` objects, returned as a float.

    colormath's own ``delta_e_cie2000`` wrapper calls the removed
    ``numpy.asscalar`` (broken on numpy>=1.20); this uses the working
    ``color_diff_matrix`` path with a scalar conversion, exactly as before.
    """
    color1_vector = np.array([color1.lab_l, color1.lab_a, color1.lab_b])
    color2_matrix = np.array([(color2.lab_l, color2.lab_a, color2.lab_b)])

    delta_e = color_diff_matrix.delta_e_cie2000(
        color1_vector, color2_matrix, Kl=Kl, Kc=Kc, Kh=Kh
    )[0]

    return delta_e.item() if hasattr(delta_e, 'item') else float(delta_e)


def calculate_delta_e(rgb1, rgb2):
    """ΔE (CIE2000) between two RGB colours given as [R, G, B] in 0–255."""
    color1_rgb = sRGBColor(*[x / 255.0 for x in rgb1])
    color2_rgb = sRGBColor(*[x / 255.0 for x in rgb2])

    color1_lab = convert_color(color1_rgb, LabColor)
    color2_lab = convert_color(color2_rgb, LabColor)

    return delta_e_cie2000(color1_lab, color2_lab)
