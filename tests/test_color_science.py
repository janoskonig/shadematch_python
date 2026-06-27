"""Characterization tests for app/utils.py colour conversions.

These pin the *current* numeric behaviour (golden values), so a future move into
a shared app/color/ library can be proven equivalent. They also document a real
finding: colormath's own delta_e_cie2000 is broken on numpy>=1.20 (it calls the
removed numpy.asscalar), which is exactly why utils.delta_e_cie2000 exists.
"""
import numpy as np
import pytest

from app.utils import (
    calculate_delta_e,
    delta_e_cie2000,
    load_cie_data,
    spectrum_to_xyz,
    xyz_to_rgb,
)


def test_delta_e_identical_is_zero():
    assert calculate_delta_e([255, 0, 0], [255, 0, 0]) == 0.0


def test_delta_e_red_vs_blue_golden():
    assert calculate_delta_e([255, 0, 0], [0, 0, 255]) == pytest.approx(52.8801, abs=1e-3)


def test_delta_e_is_symmetric():
    a, b = [12, 200, 130], [40, 190, 120]
    assert calculate_delta_e(a, b) == pytest.approx(calculate_delta_e(b, a), abs=1e-9)


def test_cie_data_shape():
    wl, x_bar, y_bar, z_bar = load_cie_data()
    assert len(wl) == 31
    assert len(wl) == len(x_bar) == len(y_bar) == len(z_bar)


def test_spectrum_to_xyz_flat_reflector_golden():
    wl, x_bar, y_bar, z_bar = load_cie_data()
    flat = np.ones(len(wl))
    X, Y, Z = spectrum_to_xyz(flat, wl, x_bar, y_bar, z_bar)
    assert (round(X, 4), round(Y, 4), round(Z, 4)) == (10.6666, 10.6815, 10.6504)


def test_xyz_to_rgb_white_clips_to_one():
    rgb = xyz_to_rgb(0.9505, 1.0, 1.089)
    assert [round(float(v), 4) for v in rgb] == [1.0, 1.0, 1.0]


def test_xyz_to_rgb_black_is_zero():
    rgb = xyz_to_rgb(0.0, 0.0, 0.0)
    assert [round(float(v), 6) for v in rgb] == [0.0, 0.0, 0.0]
