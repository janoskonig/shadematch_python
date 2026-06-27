"""Canonical CIE / sRGB constants.

The CIE 1931 2° colour-matching functions below are byte-for-byte the arrays
that were previously inlined in ``app/utils.load_cie_data`` and (a second copy)
in ``app/routes.load_cie_data`` — verified identical before consolidation.
"""
import numpy as np

# CIE 1931 2° observer colour-matching functions, 400–700 nm in 10 nm steps
# (31 sample points).
CIE_WAVELENGTHS = np.arange(400, 701, 10)

CIE_X_BAR = np.array([
    0.0143, 0.0435, 0.1344, 0.2839, 0.3483, 0.3362, 0.2908, 0.1954, 0.0956,
    0.0320, 0.0049, 0.0093, 0.0633, 0.1655, 0.2904, 0.4334, 0.5945, 0.7621,
    0.9163, 1.0263, 1.0622, 1.0026, 0.8544, 0.6424, 0.4479, 0.2835, 0.1649,
    0.0874, 0.0468, 0.0227, 0.0114,
])
CIE_Y_BAR = np.array([
    0.0004, 0.0012, 0.0040, 0.0116, 0.023, 0.038, 0.060, 0.091, 0.139, 0.208,
    0.323, 0.503, 0.710, 0.862, 0.954, 0.995, 0.995, 0.952, 0.870, 0.757,
    0.631, 0.503, 0.381, 0.265, 0.175, 0.107, 0.061, 0.032, 0.017, 0.0082,
    0.0041,
])
CIE_Z_BAR = np.array([
    0.0679, 0.2074, 0.6456, 1.3856, 1.7471, 1.7721, 1.6692, 1.2876, 0.8130,
    0.4652, 0.2720, 0.1582, 0.0782, 0.0422, 0.0203, 0.0087, 0.0039, 0.0021,
    0.0017, 0.0011, 0.0008, 0.0003, 0.0002, 0.0000, 0.0000, 0.0000, 0.0000,
    0.0000, 0.0000, 0.0000, 0.0000,
])

assert (
    len(CIE_WAVELENGTHS)
    == len(CIE_X_BAR)
    == len(CIE_Y_BAR)
    == len(CIE_Z_BAR)
    == 31
), "CIE data arrays must all have 31 points"

# Linear sRGB ← XYZ (D65). Kept at the 4-decimal precision previously used by
# ``utils.xyz_to_rgb`` so the display pipeline reproduces the same RGB values.
# (spectral_km keeps its own full-precision XYZ_RGB for the spectral engine.)
XYZ_TO_LINEAR_SRGB = np.array([
    [3.2406, -1.5372, -0.4986],
    [-0.9689, 1.8758, 0.0415],
    [0.0557, -0.2040, 1.0570],
])

# sRGB gamma companding.
SRGB_GAMMA = 2.4
SRGB_LINEAR_THRESHOLD = 0.0031308
