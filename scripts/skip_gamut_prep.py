#!/usr/bin/env python3
"""Prepare skip-row data for the R gamut plots.

For every skipped mixing session we reconstruct the *achieved* colour exactly the
way the frontend did (Mixbox latent-space average of the base-paint drops,
static/main.js:1355), convert both target and achieved to CIELAB, and validate the
reconstruction against the ΔE2000 the app stored at give-up. Also dumps the Mixbox
palette gamut boundary and the skin-colour reference gamut for the a*-b* overlay.

Outputs (artifacts/skip_gamut/):
  skips_enriched.csv   one row per validated skip: target/achieved Lab + chroma + ΔE
  mixbox_gamut_ab.csv  a*-b* convex-hull vertices of the reachable Mixbox gamut

Run:  PYTHONPATH=. python3 scripts/skip_gamut_prep.py
"""
from __future__ import annotations
from pathlib import Path

import numpy as np
import pandas as pd
import mixbox
from colormath.color_objects import sRGBColor, LabColor
from colormath.color_conversions import convert_color
from scipy.spatial import ConvexHull

from app.utils import delta_e_cie2000

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "artifacts" / "skip_gamut"
OUT.mkdir(parents=True, exist_ok=True)

BASE = {  # static/main.js baseColors
    "white": (255, 255, 255), "black": (0, 0, 0),
    "red": (255, 0, 0), "yellow": (255, 255, 0), "blue": (0, 0, 255),
}
DROP_COLS = {"drop_white": "white", "drop_black": "black",
             "drop_red": "red", "drop_yellow": "yellow", "drop_blue": "blue"}

# Pre-compute the base-paint latents once.
BASE_LATENT = {k: np.asarray(mixbox.rgb_to_latent(v), dtype=float) for k, v in BASE.items()}


def mixbox_rgb(drops: dict) -> tuple[int, int, int] | None:
    total = sum(drops.values())
    if total <= 0:
        return None
    z = np.zeros_like(next(iter(BASE_LATENT.values())))
    for col, key in DROP_COLS.items():
        c = drops[col]
        if c:
            z += (c / total) * BASE_LATENT[key]
    rgb = mixbox.latent_to_rgb(z.tolist())
    return tuple(int(round(x)) for x in rgb)


def rgb_to_lab(rgb) -> np.ndarray:
    lab = convert_color(sRGBColor(*[x / 255.0 for x in rgb]), LabColor)
    return np.array([lab.lab_l, lab.lab_a, lab.lab_b])


def de2000(lab1, lab2) -> float:
    return float(delta_e_cie2000(LabColor(*lab1), LabColor(*lab2)))


def main() -> None:
    df = pd.read_csv(REPO / "data" / "shadematch_v2" / "mixing_sessions.csv")
    sk = df[df["skipped"] == True].copy()  # noqa: E712
    for c in DROP_COLS:
        sk[c] = sk[c].fillna(0).astype(int)

    rows = []
    for _, r in sk.iterrows():
        drops = {c: int(r[c]) for c in DROP_COLS}
        arb = mixbox_rgb(drops)
        if arb is None:
            continue
        tgt = (int(r.target_r), int(r.target_g), int(r.target_b))
        tlab, alab = rgb_to_lab(tgt), rgb_to_lab(arb)
        de_recon = de2000(tlab, alab)
        rows.append({
            "user_id": r.user_id,
            "target_r": tgt[0], "target_g": tgt[1], "target_b": tgt[2],
            "achieved_r": arb[0], "achieved_g": arb[1], "achieved_b": arb[2],
            "tL": tlab[0], "ta": tlab[1], "tb": tlab[2],
            "aL": alab[0], "aa": alab[1], "ab": alab[2],
            "tC": float(np.hypot(tlab[1], tlab[2])),
            "aC": float(np.hypot(alab[1], alab[2])),
            "delta_e_stored": float(r.delta_e) if pd.notna(r.delta_e) else np.nan,
            "delta_e_recon": de_recon,
            "skip_perception": r.skip_perception,
            "mixing_model": r.mixing_model,
        })
    out = pd.DataFrame(rows)

    # Validation: recomputed ΔE should track the stored ΔE for mixbox rows.
    ok = out.dropna(subset=["delta_e_stored"])
    diff = (ok["delta_e_recon"] - ok["delta_e_stored"]).abs()
    print(f"skips reconstructed : {len(out)}")
    print(f"ΔE match |recon-stored|: median={diff.median():.3f}  "
          f"p90={diff.quantile(.9):.3f}  max={diff.max():.3f}")
    for m, g in ok.groupby(out["mixing_model"].fillna("NaN")):
        d = (g["delta_e_recon"] - g["delta_e_stored"]).abs()
        print(f"  model={m:8s} n={len(g):5d}  median|Δ|={d.median():.3f}")

    out.to_csv(OUT / "skips_enriched.csv", index=False)

    # Mixbox reachable gamut: sample random 5-way barycentric mixes → a*-b* hull.
    rng = np.random.default_rng(0)
    W = rng.dirichlet(np.ones(5), size=6000)
    keys = list(BASE_LATENT)
    Z = W @ np.array([BASE_LATENT[k] for k in keys])
    labs = np.array([rgb_to_lab(mixbox.latent_to_rgb(z.tolist())) for z in Z])
    ab = labs[:, 1:]
    hull = ab[ConvexHull(ab).vertices]
    pd.DataFrame(hull, columns=["a", "b"]).to_csv(OUT / "mixbox_gamut_ab.csv", index=False)

    print("wrote:", OUT)


if __name__ == "__main__":
    main()
