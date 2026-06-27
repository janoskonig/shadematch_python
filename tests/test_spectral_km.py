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


def test_ciede2000_agrees_with_colormath_path_sample():
    # On typical pairs the vectorised ΔE matches the colormath-backed path tightly.
    pairs = [((50, 2, -3), (52, 1, -1)),
             ((80, -10, 20), (75, -5, 18)),
             ((30, 40, -50), (31, 38, -48))]
    for a, b in pairs:
        mine = float(s.ciede2000(a, b))
        ref = float(utils_delta_e(LabColor(*a), LabColor(*b)))
        assert abs(mine - ref) < 1e-9


def test_ciede2000_agrees_with_colormath_path_randomized():
    # Guard against silent divergence between the two CIEDE2000 representations:
    # the fast vectorised one (solver hot path) and the authoritative
    # colormath-backed one (RGB scoring). They agree to ~1e-4 across the Lab
    # gamut — small edge-case differences in the hue-average branch, well below
    # any perceptual or threshold significance.
    import numpy as np

    rng = np.random.default_rng(12345)
    max_diff = 0.0
    for _ in range(1000):
        a = (rng.uniform(0, 100), rng.uniform(-90, 90), rng.uniform(-90, 90))
        b = (rng.uniform(0, 100), rng.uniform(-90, 90), rng.uniform(-90, 90))
        mine = float(s.ciede2000(a, b))
        ref = float(utils_delta_e(LabColor(*a), LabColor(*b)))
        max_diff = max(max_diff, abs(mine - ref))
    assert max_diff < 2e-4, f"CIEDE2000 implementations diverged: {max_diff}"


def test_km_ks_are_inverses():
    for r in (0.05, 0.3, 0.5, 0.9):
        assert abs(float(s.km(s.ks(r))) - r) < 1e-9


def test_xyz_to_lab_d65_white():
    lab = [round(float(v), 4) for v in s.xyz_to_lab((0.95047, 1.0, 1.08883))]
    assert lab == [100.0, 0.0537, -0.0427]
