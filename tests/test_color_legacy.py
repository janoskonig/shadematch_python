"""Characterization tests for the legacy spectrum→sRGB pipeline.

These pin the exact output of the functions extracted from routes.py
(app/color/legacy.py), which feed the displayed base-pigment swatch colours on
/spectral, /lab and the game, plus /color_inspector and /mix_colors. Locking
them lets the extraction be proven behaviour-preserving and guards the colours
against future drift.
"""
import numpy as np
import pytest

from app.color import load_cie_data
from app.color.legacy import spectrum_to_chromaticity_xyz, xyz_to_srgb8


def _cie():
    return load_cie_data()


def test_chromaticity_xyz_ramp_golden():
    wl, x, y, z = _cie()
    spec = np.linspace(0.05, 0.95, 31)
    X, Y, Z = spectrum_to_chromaticity_xyz(spec, wl, x, y, z)
    assert (round(float(X), 8), round(float(Y), 8), round(float(Z), 8)) == (
        0.43184573, 0.40586585, 0.16228841,
    )
    # Chromaticity-normalised: components sum to 1 when total is positive.
    assert float(X + Y + Z) == pytest.approx(1.0, abs=1e-9)


def test_chromaticity_xyz_flat_golden():
    wl, x, y, z = _cie()
    X, Y, Z = spectrum_to_chromaticity_xyz(np.full(31, 0.5), wl, x, y, z)
    assert (round(float(X), 8), round(float(Y), 8), round(float(Z), 8)) == (
        0.33334688, 0.33381252, 0.3328406,
    )


def test_chromaticity_xyz_zero_spectrum_not_normalised():
    wl, x, y, z = _cie()
    X, Y, Z = spectrum_to_chromaticity_xyz(np.zeros(31), wl, x, y, z)
    # s <= 0: values are returned unscaled (all zero), not divided.
    assert (float(X), float(Y), float(Z)) == (0.0, 0.0, 0.0)


def test_xyz_to_srgb8_golden_values():
    assert xyz_to_srgb8(0.43184573, 0.40586585, 0.16228841).tolist() == [217, 159, 94]
    assert xyz_to_srgb8(0.33334688, 0.33381252, 0.3328406).tolist() == [169, 152, 149]
    assert xyz_to_srgb8(0.0, 0.0, 0.0).tolist() == [0, 0, 0]
    assert xyz_to_srgb8(0.95, 1.0, 1.089).tolist() == [254, 255, 254]


def test_xyz_to_srgb8_returns_clipped_int_array():
    out = xyz_to_srgb8(2.0, 2.0, 2.0)  # way out of gamut → clipped to 255
    assert np.issubdtype(out.dtype, np.integer)
    assert out.tolist() == [255, 255, 255]
