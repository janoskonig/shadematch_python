"""
Relative tinting strength of the /spectral base pigments, derived from the Kremer
shade ladder (sh-1 = masstone … sh-4 = most diluted) in static/pigments/.

Identifiable from the data we have (no concentration ratios needed): under the
assumption that the printed card uses the SAME shade protocol for every pigment,
the single-constant K/S a pigment still shows at a given dilution ranks its
colouring power. We report K/S at masstone and at the most-dilute tint, and a
normalised strength index (geometric mean of the five bases = 1).
"""
import os, numpy as np, pandas as pd

PIG = os.path.join('static', 'pigments')
BASES = [('white', 'titanium white.txt'), ('black', 'aniline_black.csv'),
         ('red', 'cadmium_red.csv'), ('yellow', 'cadmium_yellow.csv'),
         ('blue', 'ultramarine_blue.csv')]
VIS = (405, 700)

def ks(R):
    R = np.clip(R, 1e-4, 1.0)
    return (1 - R) ** 2 / (2 * R)

def load(fn):
    p = os.path.join(PIG, fn)
    if fn.endswith('.csv'):
        df = pd.read_csv(p)
        wl = df['Wavelength'].to_numpy()
        shades = [df.iloc[:, i].to_numpy() for i in range(1, df.shape[1])]  # sh-1..sh-4
        return wl, shades
    # white .txt: single curve, reflectance 0-100, wide range
    data = []
    with open(p) as f:
        for line in f:
            t = line.split()
            if len(t) == 2:
                try: data.append((float(t[0]), float(t[1]) / 100.0))
                except ValueError: pass
    data.sort()
    wl = np.array([d[0] for d in data]); R = np.array([d[1] for d in data])
    return wl, [R]  # treat as a single "shade" (no ladder)

rows = []
for key, fn in BASES:
    wl, shades = load(fn)
    m = (wl >= VIS[0]) & (wl <= VIS[1])
    ks_masstone = ks(shades[0][m]).mean()
    ks_dilute = ks(shades[-1][m]).mean()
    rows.append((key, ks_masstone, ks_dilute, len(shades)))

masstone = np.array([r[1] for r in rows])
dilute = np.array([r[2] for r in rows])
# Strength index from the most-dilute tint (the classic "who survives dilution" test);
# fall back to masstone for white (no ladder). Normalise geo-mean of 5 bases = 1.
strength_raw = np.array([r[2] if r[3] > 1 else r[1] for r in rows])
gm = np.exp(np.log(strength_raw).mean())
strength = strength_raw / gm

print(f"{'pigment':8s} {'KS_masstone':>12s} {'KS_dilute':>10s} {'shades':>7s} {'TS_index':>9s}")
for (key, ksm, ksd, n), s in zip(rows, strength):
    print(f"{key:8s} {ksm:12.3f} {ksd:10.4f} {n:7d} {s:9.3f}")
print("\nJS map (geo-mean=1):")
print("{ " + ", ".join(f"{r[0]}: {s:.3f}" for r, s in zip(rows, strength)) + " }")
