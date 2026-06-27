"""Characterization tests for app/spectral_km.py colour math.

Covers the deterministic core (ΔE, Kubelka-Munk transforms, Lab conversion).
The recipe *solver* (solve_recipe/solve_mix) is intentionally not snapshotted
here because it runs a multi-start optimiser; lock that separately once a seed is
threaded through.
"""
from colormath.color_objects import LabColor

from app import spectral_km as s
from app.utils import delta_e_cie2000 as utils_delta_e


def test_size_constant():
    assert s.SIZE == 38


def test_ciede2000_zero_on_identical():
    assert float(s.ciede2000((50, 2, -3), (50, 2, -3))) == 0.0


def test_ciede2000_symmetric():
    a, b = (80, -10, 20), (75, -5, 18)
    assert float(s.ciede2000(a, b)) == float(s.ciede2000(b, a))


def test_ciede2000_agrees_with_colormath_path():
    # spectral_km's hand-vectorised CIEDE2000 must match the colormath-backed
    # implementation in utils to high precision (the duplication this documents).
    pairs = [((50, 2, -3), (52, 1, -1)),
             ((80, -10, 20), (75, -5, 18)),
             ((30, 40, -50), (31, 38, -48))]
    for a, b in pairs:
        mine = float(s.ciede2000(a, b))
        ref = float(utils_delta_e(LabColor(*a), LabColor(*b)))
        assert mine == ref or abs(mine - ref) < 1e-9


def test_km_ks_are_inverses():
    for r in (0.05, 0.3, 0.5, 0.9):
        assert abs(float(s.km(s.ks(r))) - r) < 1e-9


def test_xyz_to_lab_d65_white():
    lab = [round(float(v), 4) for v in s.xyz_to_lab((0.95047, 1.0, 1.08883))]
    assert lab == [100.0, 0.0537, -0.0427]
