#!/usr/bin/env python3
"""How much of sRGB can the five game pigments actually mix?

Voxelizes CIELAB at 1 dE resolution and compares two sets:
  - the full sRGB gamut (all 256^3 colours), and
  - everything reachable as a mixbox convex combination of the game's five
    pigments (white/black/red/yellow/blue), rounded to 8-bit like the game does.

Result (2026-07-17): 527k of 859k dE^3 = 61.4% of sRGB. The missing 39% is
almost entirely high-chroma green, magenta and cyan -- the three sRGB primaries
that are not pigments. Coverage of the near-neutral / skin core is far higher
(93% within chroma <= 10).

Modes:
  (no flag)     artifacts/gamut_coverage/gamut_vs_srgb.png  -- a*b* slices + coverage
  --emit-json   app/data/gamut_coverage.json  -- 3D surfaces + stats for /stat/riport
  --palettes    print what a 6th/7th pigment would buy (stdout only)

Run:  python3 scripts/gamut_coverage_figure.py [--emit-json|--palettes]
      (anaconda python3, needs mixbox)
"""
import json
import sys
from itertools import combinations
from pathlib import Path

import numpy as np
import mixbox

BASE = [(255, 255, 255), (0, 0, 0), (255, 0, 0), (255, 255, 0), (0, 0, 255)]
BL = np.array([mixbox.rgb_to_latent(c) for c in BASE])

COEF = np.array([
    [+0.07717053, +0.02826978, +0.24832992], [+0.95912302, +0.80256528, +0.03561839],
    [+0.74683774, +0.04868586, +0.00000000], [+0.99518138, +0.99978149, +0.99704802],
    [+0.04819146, +0.83363781, +0.32515377], [-0.68146950, +1.46107803, +1.06980936],
    [+0.27058419, -0.15324870, +1.98735057], [+0.80478189, +0.67093710, +0.18424500],
    [-0.35031003, +1.37855826, +3.68865000], [+1.05128046, +1.97815239, +2.82989073],
    [+3.21607125, +0.81270228, +1.03384539], [+2.78893374, +0.41565549, -0.04487295],
    [+3.02162577, +2.55374103, +0.32766114], [+2.95124691, +2.81201112, +1.17578442],
    [+2.82677043, +0.79933038, +1.81715262], [+2.99691099, +1.22593053, +1.80653661],
    [+1.87394106, +2.05027182, -0.29835996], [+2.56609566, +7.03428198, +0.62575374],
    [+4.08329484, -1.40408358, +2.14995522], [+6.00078678, +2.55552042, +1.90739502]])


def eval_poly(c):
    c0, c1, c2, c3 = c[:, 0], c[:, 1], c[:, 2], c[:, 3]
    t = np.stack([c0**3, c1**3, c2**3, c3**3, c0**2*c1, c0*c1**2, c0**2*c2, c0*c2**2,
                  c0**2*c3, c0*c3**2, c1**2*c2, c1*c2**2, c1**2*c3, c1*c3**2, c2**2*c3,
                  c2*c3**2, c0*c1*c2, c0*c1*c3, c0*c2*c3, c1*c2*c3], 1)
    return t @ COEF


def latents_to_rgb(Z):
    return np.clip(eval_poly(Z[:, :4]) + Z[:, 4:7], 0.0, 1.0)


def srgb_to_lab(c):
    lin = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = lin @ M.T
    r = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(r > 0.008856451679035631, np.cbrt(r), 7.787037037037037 * r + 16/116)
    return np.stack([116*f[:, 1]-16, 500*(f[:, 0]-f[:, 1]), 200*(f[:, 1]-f[:, 2])], 1)


def lab_to_srgb(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16) / 116; fx = fy + a / 500; fz = fy - b / 200

    def g(t):
        return np.where(t > 6/29, t**3, 3*(6/29)**2*(t - 4/29))

    xyz = np.stack([g(fx)*0.95047, g(fy)*1.0, g(fz)*1.08883], -1)
    M = np.array([[3.2404542, -1.5371385, -0.4985314],
                  [-0.9692660, 1.8760108, 0.0415560],
                  [0.0556434, -0.2040259, 1.0572252]])
    lin = np.clip(xyz @ M.T, 0, 1)
    return np.where(lin <= 0.0031308, 12.92*lin, 1.055*lin**(1/2.4) - 0.055)


NL, NA = 101, 257


def mark(grid, lab):
    L = np.floor(lab[:, 0]).astype(int)
    a = np.floor(lab[:, 1]).astype(int) + 128
    b = np.floor(lab[:, 2]).astype(int) + 128
    ok = (L >= 0) & (L < NL) & (a >= 0) & (a < NA) & (b >= 0) & (b < NA)
    grid[L[ok], a[ok], b[ok]] = True


def srgb_mask():
    """Every 8-bit sRGB colour, voxelized."""
    g = np.zeros((NL, NA, NA), bool)
    lv = np.arange(256) / 255.0
    G, Bc = np.meshgrid(lv, lv, indexing="ij")
    for rch in range(256):
        mark(g, srgb_to_lab(np.stack([np.full(G.size, rch/255.0), G.ravel(), Bc.ravel()], 1)))
    return g


def grid_comp(n):
    """All 5-pigment weight vectors i/n on the simplex."""
    out = []
    for a in range(n+1):
        for b in range(n+1-a):
            for c in range(n+1-a-b):
                for d in range(n+1-a-b-c):
                    out.append((a, b, c, d, n-a-b-c-d))
    return np.array(out, float) / n


def mixbox_mask(palette=None, seed=0, verbose=True, max_iter=40):
    """Everything mixable from `palette` (default: the game's five), voxelized.

    Samples the weight simplex: the low-order faces (1-3 pigments) carry the gamut
    boundary, the Dirichlet interior fills the body. Sampling can only ever
    under-count, so it runs until the volume stops growing (<0.02% per round)
    rather than for a fixed budget -- an n-pigment simplex needs more samples than
    a 5-pigment one, and a fixed budget would silently under-report the wider
    palettes it is meant to compare.
    """
    pal = palette or BASE
    bl = np.array([mixbox.rgb_to_latent(tuple(c)) for c in pal])
    n = len(pal)
    g = np.zeros((NL, NA, NA), bool)
    rng = np.random.default_rng(seed)

    def add(W):
        mark(g, srgb_to_lab(np.round(latents_to_rgb(W @ bl) * 255) / 255.0))

    if n == 5:
        add(grid_comp(48))                       # deterministic backbone
    for k in (1, 2, 3):
        for idx in combinations(range(n), k):
            W = np.zeros((100_000, n))
            W[:, idx] = rng.dirichlet(np.ones(k), 100_000)
            add(W)
    prev = 0
    for it in range(max_iter):
        add(rng.dirichlet(np.ones(n) * 0.5, 2_000_000))   # mass near the faces
        add(rng.dirichlet(np.ones(n) * 1.0, 1_000_000))   # uniform interior
        for k in (2, 3):                                  # the boundary sheets, every round
            for idx in combinations(range(n), k):
                W = np.zeros((100_000, n))
                W[:, idx] = rng.dirichlet(np.ones(k), 100_000)
                add(W)
        cur = int(g.sum())
        if verbose:
            print(f"  iter {it}: {cur:,} (+{cur - prev:,})")
        if cur - prev < max(50, cur // 20000):
            break
        prev = cur
    return g


def core_pct(mask, ref, max_chroma):
    """Coverage of the reference restricted to chroma <= max_chroma (the near-neutral core)."""
    a = np.arange(NA)[None, :, None] - 127.5
    b = np.arange(NA)[None, None, :] - 127.5
    core = (np.hypot(a, b) <= max_chroma)
    return 100.0 * (mask & ref & core).sum() / max((ref & core).sum(), 1)


# --------------------------------------------------------------------------- #
# mode: --emit-json  (surfaces + stats for the /stat/riport Limitációk section)
# --------------------------------------------------------------------------- #
def emit_json(srgb, mixb, palettes):
    from scipy import ndimage
    from skimage import measure

    def surface(mask, step, colored):
        """Voxel mask -> Mesh3d-ready surface: smooth the voxel staircase, then
        marching-cubes at `step` dE."""
        v = ndimage.gaussian_filter(mask.astype(np.float32), sigma=1.0)
        verts, faces, _, _ = measure.marching_cubes(v, level=0.5, step_size=step)
        lab = np.stack([verts[:, 0], verts[:, 1] - 128.0, verts[:, 2] - 128.0], 1)
        out = {
            'L': [round(float(x), 1) for x in lab[:, 0]],
            'a': [round(float(x), 1) for x in lab[:, 1]],
            'b': [round(float(x), 1) for x in lab[:, 2]],
            'i': faces[:, 0].tolist(), 'j': faces[:, 1].tolist(), 'k': faces[:, 2].tolist(),
        }
        if colored:
            rgb = np.clip(np.round(lab_to_srgb(lab) * 255), 0, 255).astype(int)
            out['c'] = ['#%02x%02x%02x' % tuple(x) for x in rgb]
        return out

    # Close the sampling pinholes before differencing, or every unsampled interior
    # voxel would surface as a speckle of "unmixable" inside the gamut.
    mix_clean = ndimage.binary_fill_holes(ndimage.binary_closing(mixb, iterations=1))
    miss = srgb & ~mix_clean

    payload = {
        'stats': {
            'srgb_voxels': int(srgb.sum()),
            'mix_voxels': int(mixb.sum()),
            'pct': round(100.0 * mixb.sum() / srgb.sum(), 1),
            'core10_pct': round(core_pct(mixb, srgb, 10), 1),
        },
        # The two solids tile the whole space: colour = mixable, grey = not. That
        # reads far better than a translucent sRGB envelope, which at the opacity
        # needed to see the body through it is essentially invisible.
        'mesh_mix5': surface(mix_clean, 4, True),
        'mesh_miss': surface(miss, 5, False),
        'palettes': palettes,
        'note': 'Generated by scripts/gamut_coverage_figure.py --emit-json. '
                'CIELAB voxels at 1 dE; surfaces via marching cubes.',
    }
    out = Path(__file__).resolve().parents[1] / "app" / "data" / "gamut_coverage.json"
    out.write_text(json.dumps(payload, separators=(',', ':')))
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({kb:.0f} KB)")
    print(f"  stats: {payload['stats']}")
    print(f"  mesh_mix5: {len(payload['mesh_mix5']['i']):,} faces, "
          f"mesh_miss: {len(payload['mesh_miss']['i']):,} faces")


# --------------------------------------------------------------------------- #
# mode: --palettes  (what would a 6th/7th pigment buy?)
# --------------------------------------------------------------------------- #
W_, K_, R_, Y_, B_ = BASE
G_, M_, C_ = (0, 255, 0), (255, 0, 255), (0, 255, 255)
CANDIDATES = [
    ("jelenlegi 5 (F/K/P/S/K)", [W_, K_, R_, Y_, B_]),
    ("+ cián (6)", [W_, K_, R_, Y_, B_, C_]),
    ("+ magenta (6)", [W_, K_, R_, Y_, B_, M_]),
    ("+ cián + magenta (7)", [W_, K_, R_, Y_, B_, M_, C_]),
    ("mind a 8 sRGB sarok", [W_, K_, R_, Y_, B_, M_, C_, G_]),
]


def palette_table(srgb):
    rows = []
    for label, pal in CANDIDATES:
        m = mixbox_mask(pal, verbose=False)
        rows.append({'label': label, 'n': len(pal),
                     'pct': round(100.0 * m.sum() / srgb.sum(), 1),
                     'core10_pct': round(core_pct(m, srgb, 10), 1)})
        print(f"  {label:26s} n={len(pal)}  {rows[-1]['pct']:5.1f}% of sRGB   "
              f"C<=10: {rows[-1]['core10_pct']:5.1f}%")
    return rows


# --------------------------------------------------------------------------- #
# mode: (default) the a*b*-slice figure
# --------------------------------------------------------------------------- #
def make_png(srgb, mixb):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    OUT = Path(__file__).resolve().parents[1] / "artifacts" / "gamut_coverage"
    OUT.mkdir(parents=True, exist_ok=True)
    pures = srgb_to_lab(np.array([[1, 1, 1], [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 0, 1]], float))
    prim = srgb_to_lab(np.array([[0, 1, 0], [1, 0, 1], [0, 1, 1]], float))

    AB = np.arange(-128, 129)
    A, B = np.meshgrid(AB, AB, indexing="ij")
    SLICES = [30, 50, 65, 80]

    fig = plt.figure(figsize=(14.5, 7.6))
    gs = fig.add_gridspec(2, 4, height_ratios=[1.35, 1.0], hspace=0.32, wspace=0.22,
                          left=0.045, right=0.985, top=0.88, bottom=0.09)

    for k, Lv in enumerate(SLICES):
        ax = fig.add_subplot(gs[0, k])
        ms, mm = srgb[Lv], mixb[Lv]
        lab = np.stack([np.full_like(A, Lv, float), A, B], -1)
        rgb = lab_to_srgb(lab)
        img = np.ones((257, 257, 4))
        img[..., 3] = 0.0
        gray = rgb.mean(-1, keepdims=True) * 0.35 + 0.62      # washed-out for sRGB-only
        img[ms, :3] = np.repeat(gray, 3, -1)[ms]
        img[ms, 3] = 1.0
        img[mm, :3] = rgb[mm]
        img[mm, 3] = 1.0
        ax.imshow(np.transpose(img, (1, 0, 2)), origin="lower", extent=[-128, 128, -128, 128],
                  interpolation="nearest")
        ax.contour(A, B, ms.astype(float), levels=[0.5], colors="#444", linewidths=0.9)
        ax.contour(A, B, mm.astype(float), levels=[0.5], colors="k", linewidths=1.6)
        cov = 100 * mm.sum() / max(ms.sum(), 1)
        ax.set_title(f"L* = {Lv}   —   {cov:.0f}% elérhető", fontsize=11, pad=6)
        ax.set_xlim(-110, 110); ax.set_ylim(-115, 115)
        ax.set_xlabel("a*  (zöld → piros)", fontsize=8.5)
        if k == 0:
            ax.set_ylabel("b*  (kék → sárga)", fontsize=8.5)
        ax.axhline(0, color="#999", lw=0.4); ax.axvline(0, color="#999", lw=0.4)
        ax.tick_params(labelsize=7.5)
        for lab_pt, name in zip(prim, ["G", "M", "C"]):
            if abs(lab_pt[0] - Lv) < 9:
                ax.plot(lab_pt[1], lab_pt[2], "x", color="k", ms=7, mew=1.8)
                ax.annotate(name, (lab_pt[1], lab_pt[2]), textcoords="offset points",
                            xytext=(6, 4), fontsize=9, fontweight="bold")
        for lab_pt, name in zip(pures[2:], ["R", "Y", "B"]):
            if abs(lab_pt[0] - Lv) < 9:
                ax.plot(lab_pt[1], lab_pt[2], "o", mfc="none", mec="k", ms=7, mew=1.8)
                ax.annotate(name, (lab_pt[1], lab_pt[2]), textcoords="offset points",
                            xytext=(6, 4), fontsize=9, fontweight="bold")

    # --- coverage vs L ---
    ax = fig.add_subplot(gs[1, 0:2])
    Ls = np.arange(1, 100)
    cov = np.array([100 * mixb[L].sum() / max(srgb[L].sum(), 1) for L in Ls])
    ax.fill_between(Ls, 0, cov, color="#5b8def", alpha=0.28)
    ax.plot(Ls, cov, color="#2b5fd0", lw=2)
    ax.axhline(61.4, color="#d1495b", ls="--", lw=1.2)
    ax.annotate("teljes gamut: 61%", (4, 63.5), color="#d1495b", fontsize=9)
    for Lv in SLICES:
        ax.axvline(Lv, color="#999", ls=":", lw=0.9)
    ax.set_xlabel("L*  (világosság)", fontsize=9.5)
    ax.set_ylabel("lefedettség az sRGB-ből (%)", fontsize=9.5)
    ax.set_title("Lefedettség világosság szerint", fontsize=11)
    ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.tick_params(labelsize=8)
    ax.grid(alpha=0.25)

    # --- unreachable volume by hue ---
    ax = fig.add_subplot(gs[1, 2:4], projection="polar")
    Lg, Ag, Bg = np.nonzero(srgb & ~mixb)
    hue = (np.degrees(np.arctan2(Bg - 128 + 0.5, Ag - 128 + 0.5)) + 360) % 360
    Ls2, As2, Bs2 = np.nonzero(srgb)
    hue_all = (np.degrees(np.arctan2(Bs2 - 128 + 0.5, As2 - 128 + 0.5)) + 360) % 360
    edges = np.arange(0, 361, 15)
    miss_h, _ = np.histogram(hue, bins=edges)
    all_h, _ = np.histogram(hue_all, bins=edges)
    frac = 100 * miss_h / np.maximum(all_h, 1)
    centers = np.radians(edges[:-1] + 7.5)
    hcol = lab_to_srgb(np.stack([np.full(24, 60.0), 70*np.cos(centers), 70*np.sin(centers)], -1))
    ax.bar(centers, frac, width=np.radians(14), color=hcol, edgecolor="#333", linewidth=0.5)
    ax.set_theta_zero_location("E")
    ax.set_title("Az sRGB hány %-a esik ki — színezet szerint", fontsize=11, pad=14)
    ax.set_ylim(0, 100)
    ax.set_yticks([25, 50, 75, 100])
    ax.set_yticklabels(["25%", "50%", "75%", ""], fontsize=7)
    ax.set_xticks(np.radians([0, 60, 120, 180, 240, 300]))
    ax.set_xticklabels(["piros", "sárga", "zöld", "cián-zöld", "kék", "magenta"], fontsize=8.5)
    ax.grid(alpha=0.3)

    handles = [Line2D([], [], marker="s", ls="", mfc="#b9b9b9", mec="#444", ms=10,
                      label="sRGB, de pigmentekkel NEM keverhető"),
               Line2D([], [], marker="s", ls="", mfc="#c94f7c", mec="k", ms=10,
                      label="az 5 pigmenttel elérhető (valódi szín)"),
               Line2D([], [], marker="o", ls="", mfc="none", mec="k", ms=8, mew=1.8,
                      label="pigment (R/Y/B)"),
               Line2D([], [], marker="x", ls="", mec="k", ms=8, mew=1.8,
                      label="sRGB alapszín (G/M/C)")]
    fig.legend(handles=handles, loc="upper center", ncol=4, fontsize=9.5, frameon=False,
               bbox_to_anchor=(0.5, 0.975))
    fig.suptitle("A teljes sRGB színtér vs. az 5 pigment mixbox-gamutja  —  "
                 "526 000 / 859 000 ΔE³ = 61,4%", fontsize=13.5, y=0.995)
    fig.savefig(OUT / "gamut_vs_srgb.png", dpi=150, facecolor="white")
    print("wrote", OUT / "gamut_vs_srgb.png")
    print("total coverage:", 100 * mixb.sum() / srgb.sum())


# Voxelizing + sampling every palette to convergence takes ~15 min, which makes
# iterating on the figure painful. The masks are a pure function of mixbox + sRGB,
# so cache them; --fresh recomputes.
CACHE = Path(__file__).resolve().parents[1] / "artifacts" / "gamut_coverage" / "masks_cache.npz"


def load_masks(fresh=False):
    if not fresh and CACHE.exists():
        d = np.load(CACHE)
        print(f"masks from cache ({CACHE.name}); --fresh to recompute")
        return d['srgb'], d['mixb'], json.loads(str(d['palettes']))
    srgb = srgb_mask()
    print(f"sRGB voxels: {srgb.sum():,}")
    mixb = mixbox_mask()
    print(f"mixbox voxels: {mixb.sum():,}  ({100*mixb.sum()/srgb.sum():.1f}% of sRGB)")
    print("palette comparison:")
    rows = palette_table(srgb)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(CACHE, srgb=srgb, mixb=mixb, palettes=json.dumps(rows))
    return srgb, mixb, rows


if __name__ == "__main__":
    args = sys.argv[1:]
    mode = next((a for a in args if not a.startswith("--fresh")), "")
    srgb, mixb, rows = load_masks(fresh="--fresh" in args)
    if mode == "--palettes":
        for r in rows:
            print(f"  {r['label']:26s} n={r['n']}  {r['pct']:5.1f}% of sRGB   "
                  f"C<=10: {r['core10_pct']:5.1f}%")
    elif mode == "--emit-json":
        emit_json(srgb, mixb, rows)
    else:
        make_png(srgb, mixb)
