"""Legacy spectrum→sRGB pipeline (kept verbatim for output compatibility).

These two functions previously lived inline at the bottom of ``app/routes.py``,
where they *shadowed* the same-named imports from ``utils`` — so several routes
silently used this pipeline instead of the canonical one in
:mod:`app.color.convert`. They are genuinely different and are preserved exactly:

* :func:`spectrum_to_chromaticity_xyz` normalises X/Y/Z by their sum
  (chromaticity), unlike :func:`app.color.convert.spectrum_to_xyz`;
* :func:`xyz_to_srgb8` returns 0–255 integers, unlike the 0–1 floats from
  :func:`app.color.convert.xyz_to_rgb`.

They feed the *displayed base-pigment swatch colours* on /spectral, /lab and the
game, plus the /color_inspector and /mix_colors demo endpoints. Folding them into
the canonical pipeline would change those colours, so they are quarantined here
under honest names rather than left inline. Covered by characterization goldens.
"""
import numpy as np


def spectrum_to_chromaticity_xyz(spectrum, wavelengths, x_bar, y_bar, z_bar):
    """Reflectance → chromaticity-normalised XYZ (X+Y+Z scaled to 1 when positive)."""
    x_interp = np.interp(wavelengths, np.arange(400, 701, 10), x_bar)
    y_interp = np.interp(wavelengths, np.arange(400, 701, 10), y_bar)
    z_interp = np.interp(wavelengths, np.arange(400, 701, 10), z_bar)
    X = np.sum(spectrum * x_interp)
    Y = np.sum(spectrum * y_interp)
    Z = np.sum(spectrum * z_interp)
    s = X + Y + Z
    if s > 0:
        X, Y, Z = X / s, Y / s, Z / s
    return X, Y, Z


def xyz_to_srgb8(X, Y, Z):
    """XYZ → gamma-companded sRGB as a clipped 0–255 integer array."""
    M = np.array([[3.2406, -1.5372, -0.4986],
                  [-0.9689,  1.8758,  0.0415],
                  [0.0557, -0.2040,  1.0570]])
    rgb = np.dot(M, np.array([X, Y, Z]))
    rgb = np.where(rgb > 0.0031308, 1.055 * np.power(np.clip(rgb, 0, None), 1 / 2.4) - 0.055, 12.92 * rgb)
    return np.clip(rgb * 255, 0, 255).astype(int)
