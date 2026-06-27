"""sRGB display-conversion stack (spectrum → XYZ → sRGB).

Moved verbatim from ``app/utils.py`` so there is a single canonical copy;
``utils`` re-exports these names for backward compatibility.
"""
import numpy as np
from scipy.interpolate import interp1d

from .constants import (
    CIE_WAVELENGTHS,
    CIE_X_BAR,
    CIE_Y_BAR,
    CIE_Z_BAR,
    SRGB_GAMMA,
    SRGB_LINEAR_THRESHOLD,
    XYZ_TO_LINEAR_SRGB,
)


def load_cie_data():
    """Return the CIE 1931 2° observer data: (wavelengths, x_bar, y_bar, z_bar)."""
    return CIE_WAVELENGTHS, CIE_X_BAR, CIE_Y_BAR, CIE_Z_BAR


def srgb_compand(linear):
    """Linear → gamma-companded sRGB (per-channel), matching the previous inline
    expression in utils.xyz_to_rgb exactly (no pre-clipping of the input)."""
    return np.where(
        linear > SRGB_LINEAR_THRESHOLD,
        1.055 * np.power(linear, 1.0 / SRGB_GAMMA) - 0.055,
        12.92 * linear,
    )


def spectrum_to_xyz(spectrum, wavelengths, x_bar, y_bar, z_bar):
    """Convert a reflectance spectrum to XYZ (interpolated onto the CIE grid)."""
    cie_wavelengths = np.arange(400, 701, 10)

    if len(wavelengths) != len(spectrum):
        raise ValueError(
            f"Wavelength and spectrum arrays must have the same length. "
            f"Got {len(wavelengths)} and {len(spectrum)}"
        )

    valid_mask = np.isfinite(spectrum) & np.isfinite(wavelengths)
    if not np.any(valid_mask):
        raise ValueError("No valid data points found")

    wavelengths_clean = np.array(wavelengths)[valid_mask]
    spectrum_clean = np.array(spectrum)[valid_mask]

    if len(wavelengths_clean) < 2:
        raise ValueError("Need at least 2 valid data points for interpolation")

    try:
        spectrum_interp = interp1d(
            wavelengths_clean, spectrum_clean, bounds_error=False, fill_value=0
        )
        spectrum_cie = spectrum_interp(cie_wavelengths)
    except Exception:
        # Fallback: use nearest neighbor interpolation
        spectrum_interp = interp1d(
            wavelengths_clean, spectrum_clean,
            kind="nearest", bounds_error=False, fill_value=0,
        )
        spectrum_cie = spectrum_interp(cie_wavelengths)

    spectrum_cie = np.clip(spectrum_cie, 0, 1)

    if len(spectrum_cie) != len(x_bar):
        raise ValueError(
            f"Spectrum interpolation result length ({len(spectrum_cie)}) doesn't "
            f"match CIE data length ({len(x_bar)})"
        )

    X = np.sum(spectrum_cie * x_bar)
    Y = np.sum(spectrum_cie * y_bar)
    Z = np.sum(spectrum_cie * z_bar)

    return X, Y, Z


def xyz_to_rgb(X, Y, Z):
    """Convert XYZ to gamma-companded sRGB in 0–1 (clipped)."""
    rgb_linear = XYZ_TO_LINEAR_SRGB @ np.array([X, Y, Z])
    rgb = srgb_compand(rgb_linear)
    return np.clip(rgb, 0, 1)
