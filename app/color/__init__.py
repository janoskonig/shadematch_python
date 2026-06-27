"""Shared colour-science library (no Flask, no DB — a pure leaf package).

This is the single home for colour maths that was previously duplicated across
``app/utils.py``, ``app/spectral_km.py``, ``app/routes.py`` and the orphan
top-level ``spectral_mixer.py``:

* one CIEDE2000 implementation (``distance.ciede2000``) — there used to be two
  (a colormath wrapper in utils, a hand-vectorised copy in spectral_km) that
  could silently diverge;
* one copy of the CIE 1931 2° colour-matching data + sRGB transform constants
  (``constants``) — there were three byte-identical copies;
* the sRGB display-conversion stack (``convert``: ``load_cie_data``,
  ``spectrum_to_xyz``, ``xyz_to_rgb``, ``srgb_compand``).

Modules that need colour maths import from here; the old call sites in utils /
spectral_km re-export these names so existing imports keep working unchanged.

Deliberately *not* unified here (they are tuned to different pipelines and
merging them would change output):
  - spectral_km's 38-bin Kubelka–Munk engine grid + its engine-white ``xyz_to_lab``;
  - routes.py's legacy chromaticity-normalised ``spectrum_to_xyz`` / 0–255
    ``xyz_to_rgb`` used by the colour-inspector/mix-colors demo endpoints.
"""
from .constants import (
    CIE_WAVELENGTHS,
    CIE_X_BAR,
    CIE_Y_BAR,
    CIE_Z_BAR,
    SRGB_GAMMA,
    SRGB_LINEAR_THRESHOLD,
    XYZ_TO_LINEAR_SRGB,
)
from .convert import load_cie_data, spectrum_to_xyz, srgb_compand, xyz_to_rgb
from .distance import ciede2000

__all__ = [
    "CIE_WAVELENGTHS", "CIE_X_BAR", "CIE_Y_BAR", "CIE_Z_BAR",
    "SRGB_GAMMA", "SRGB_LINEAR_THRESHOLD", "XYZ_TO_LINEAR_SRGB",
    "load_cie_data", "spectrum_to_xyz", "srgb_compand", "xyz_to_rgb",
    "ciede2000",
]
