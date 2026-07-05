#!/usr/bin/env python3
"""a*-b* verification view of the target set — shows the Xiao skin-zone densification.

Left: every target at its own colour (a*-b*), with the Xiao skin hull outlined.
Right: same, but skin-zone targets highlighted vs background.

Run:  PYTHONPATH=. python3 scripts/plot_gamut_targets_ab.py
"""
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon

from app.gamut_lab import skin_gamut

OUT = Path(__file__).resolve().parents[1] / "artifacts" / "gamut_targets"


def main():
    df = pd.read_csv(OUT / "gamut_targets.csv")
    cols = np.clip(df[["R", "G", "B"]].to_numpy() / 255.0, 0, 1)
    hull = np.array(skin_gamut()["hull"])

    fig, ax = plt.subplots(1, 2, figsize=(13, 6.2))
    for a in ax:
        a.add_patch(Polygon(hull, closed=True, fill=False, ec="#c0392b",
                            lw=1.6, ls="--", zorder=5))
        a.axhline(0, color="#ddd", lw=.6); a.axvline(0, color="#ddd", lw=.6)
        a.set_xlabel("a*"); a.set_ylabel("b*"); a.set_aspect("equal")

    ax[0].scatter(df.a, df.b, c=cols, s=26, edgecolors="#333", linewidths=.3)
    ax[0].set_title(f"{len(df)} targets at their own colour\n(dashed = Xiao skin hull)")

    sk = df.skin_zone.astype(bool)
    ax[1].scatter(df.a[~sk], df.b[~sk], c="#bbb", s=22, label=f"background ({(~sk).sum()})")
    ax[1].scatter(df.a[sk], df.b[sk], c=cols[sk.to_numpy()], s=40, edgecolors="#111",
                  linewidths=.5, label=f"skin zone ({sk.sum()})", zorder=4)
    ax[1].legend(loc="upper left", fontsize=9)
    ax[1].set_title("Skin-zone densification (Xiao 2017 + 15 ΔE)")

    fig.tight_layout()
    p = OUT / "gamut_targets_ab.png"
    fig.savefig(p, dpi=140, facecolor="white")
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
