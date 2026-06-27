"""Tests that the shared app/color package is the single source of truth.

These pin the consolidation: the legacy modules must delegate to app.color
(same function objects), so there is exactly one implementation behind each name.
"""
import numpy as np

import app.color as color
import app.spectral_km as spectral_km
import app.utils as utils


def test_spectral_km_ciede2000_is_shared():
    assert spectral_km.ciede2000 is color.distance.ciede2000


def test_utils_conversion_stack_is_shared():
    assert utils.spectrum_to_xyz is color.convert.spectrum_to_xyz
    assert utils.xyz_to_rgb is color.convert.xyz_to_rgb
    assert utils.load_cie_data is color.convert.load_cie_data


def test_cie_data_single_definition_matches_legacy_values():
    # The shared CIE arrays are the ones utils (and formerly routes) exposed.
    wl, x_bar, y_bar, z_bar = utils.load_cie_data()
    assert np.array_equal(wl, color.CIE_WAVELENGTHS)
    assert np.array_equal(x_bar, color.CIE_X_BAR)
    assert len(wl) == 31


def test_srgb_compand_matches_inline_formula():
    # srgb_compand must reproduce the exact piecewise expression utils.xyz_to_rgb
    # used inline before extraction.
    x = np.array([0.0, 0.001, 0.0031308, 0.05, 0.5, 1.0])
    expected = np.where(
        x > 0.0031308, 1.055 * np.power(x, 1.0 / 2.4) - 0.055, 12.92 * x
    )
    assert np.array_equal(color.srgb_compand(x), expected)
