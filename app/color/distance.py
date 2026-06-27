"""CIEDE2000 colour difference — the single implementation for the whole app.

Vectorised (Sharma et al. 2005). ``lab1``/``lab2`` are ``(…, 3)`` arrays and the
difference is taken along the last axis, so a whole batch of Lab pairs scores in
one call (the recipe solver's hot path).

This is the implementation that previously lived as ``spectral_km.ciede2000``;
``utils.delta_e_cie2000`` now delegates here too, so there is exactly one
CIEDE2000 in the codebase. It matches colormath's ``delta_e_cie2000`` to ~1e-9
(see tests), without colormath's per-call object construction (which made the
multi-illuminant solve several times slower) and without depending on the
(unmaintained, numpy-incompatible) colormath distance code.
"""
import numpy as np


def ciede2000(lab1, lab2, kL=1.0, kC=1.0, kH=1.0):
    """CIEDE2000 ΔE between Lab colours along the last axis.

    ``kL``/``kC``/``kH`` are the lightness/chroma/hue parametric weighting
    factors (all 1.0 for the standard metric).
    """
    lab1 = np.asarray(lab1, dtype=float)
    lab2 = np.asarray(lab2, dtype=float)
    L1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    L2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0
    G = 0.5 * (1 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    dLp = L2 - L1
    dCp = C2p - C1p
    dhp = h2p - h1p
    dhp = np.where(dhp > 180, dhp - 360, dhp)
    dhp = np.where(dhp < -180, dhp + 360, dhp)
    dhp = np.where(C1p * C2p == 0, 0.0, dhp)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dhp) / 2.0)
    Lbarp = (L1 + L2) / 2.0
    Cbarp = (C1p + C2p) / 2.0
    hsum, habs = h1p + h2p, np.abs(h1p - h2p)
    hbarp = np.where(C1p * C2p == 0, hsum,
                     np.where(habs <= 180, hsum / 2.0,
                              np.where(hsum < 360, (hsum + 360) / 2.0, (hsum - 360) / 2.0)))
    T = (1 - 0.17 * np.cos(np.radians(hbarp - 30)) + 0.24 * np.cos(np.radians(2 * hbarp))
         + 0.32 * np.cos(np.radians(3 * hbarp + 6)) - 0.20 * np.cos(np.radians(4 * hbarp - 63)))
    dtheta = 30 * np.exp(-(((hbarp - 275) / 25.0) ** 2))
    RC = 2 * np.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25.0 ** 7))
    SL = 1 + (0.015 * (Lbarp - 50) ** 2) / np.sqrt(20 + (Lbarp - 50) ** 2)
    SC = 1 + 0.045 * Cbarp
    SH = 1 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2 * dtheta)) * RC
    dLp_term = dLp / (kL * SL)
    dCp_term = dCp / (kC * SC)
    dHp_term = dHp / (kH * SH)
    return np.sqrt(dLp_term ** 2 + dCp_term ** 2 + dHp_term ** 2
                   + RT * dCp_term * dHp_term)
