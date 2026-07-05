#!/usr/bin/env python3
"""Interactive 3D CIELAB view of the generated target set inside the Mixbox gamut.

Renders the N targets as spheres painted their own colour, wrapped in the semi-
transparent convex hull of the achievable gamut, plus a faint achievable-colour
cloud so the true (slightly non-convex) fill is visible. Self-contained HTML
(Plotly inlined), no network needed.

Run:  PYTHONPATH=. python3 scripts/plot_gamut_targets_3d.py [N]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from scipy.spatial import ConvexHull

from generate_gamut_targets import (build_candidates, rgb_int_from_counts,
                                     rgb_to_lab, OUT)


def main():
    df = pd.read_csv(OUT / "gamut_targets.csv")
    n = len(df)

    # achievable gamut cloud + convex hull (context)
    rng = np.random.default_rng(0)
    counts = build_candidates(rng)
    rgb = rgb_int_from_counts(counts)
    _, uniq = np.unique(rgb, axis=0, return_index=True)
    rgb = rgb[uniq]
    labs = rgb_to_lab(rgb)
    hull = ConvexHull(labs)
    hv = labs[hull.vertices]
    vmap = {v: i for i, v in enumerate(hull.vertices)}
    faces = np.array([[vmap[s] for s in simp] for simp in hull.simplices])

    # faint achievable cloud (subsample so the browser stays smooth)
    idx = rng.choice(len(labs), size=6000, replace=False)
    cloud = labs[idx]
    cloud_rgb = ['rgb(%d,%d,%d)' % tuple(c) for c in rgb[idx]]

    tgt_rgb = ['rgb(%d,%d,%d)' % (r, g, b) for r, g, b in zip(df.R, df.G, df.B)]
    hover = [f"RGB({r},{g},{b})<br>Lab({L:.0f},{a:.0f},{bb:.0f})<br>"
             f"drops w{w} k{k} r{rd} y{y} bl{bl} (Σ{t})"
             for r, g, b, L, a, bb, w, k, rd, y, bl, t in zip(
                 df.R, df.G, df.B, df.L, df.a, df.b, df.drop_white, df.drop_black,
                 df.drop_red, df.drop_yellow, df.drop_blue, df.total_drops)]

    fig = go.Figure()
    # gamut hull (semi-transparent envelope)
    fig.add_trace(go.Mesh3d(
        x=hv[:, 1], y=hv[:, 2], z=hv[:, 0],
        i=faces[:, 0], j=faces[:, 1], k=faces[:, 2],
        color='lightgrey', opacity=0.12, name='gamut hull',
        hoverinfo='skip', flatshading=True))
    # faint achievable cloud (true fill)
    fig.add_trace(go.Scatter3d(
        x=cloud[:, 1], y=cloud[:, 2], z=cloud[:, 0], mode='markers',
        marker=dict(size=1.6, color=cloud_rgb, opacity=0.18),
        name='achievable colours', hoverinfo='skip'))
    # the N targets, painted their own colour
    fig.add_trace(go.Scatter3d(
        x=df.a, y=df.b, z=df.L, mode='markers',
        marker=dict(size=5, color=tgt_rgb, line=dict(width=0.5, color='#333')),
        name=f'{n} targets (ΔE=0)', text=hover, hoverinfo='text'))

    fig.update_layout(
        title=f'{n} evenly spaced ΔE=0 targets in the Mixbox CIELAB gamut',
        scene=dict(xaxis_title='a*', yaxis_title='b*', zaxis_title='L*',
                   aspectmode='data'),
        legend=dict(x=0, y=1), margin=dict(l=0, r=0, t=40, b=0))

    path = OUT / "gamut_targets_3d.html"
    fig.write_html(str(path), include_plotlyjs='inline', full_html=True)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
