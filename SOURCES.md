# Sources & Citations

This document records the external work the ShadeMatch spectral engine builds on:
data, models, and code. The spectral colour-matching lab (`/spectral`,
`/reverse_engineer`) rests on three pillars — a **pigment reflectance dataset**, a
**Kubelka–Munk mixing model**, and an **inverse-solve strategy** — each credited below.

---

## 1. Pigment reflectance data

### Hyperspectral Pigment Dataset (the per-pigment CSVs)

The reflectance curves in [`static/pigments/*.csv`](static/pigments/) come from this
dataset. The column headers carry the Kremer Pigmente article numbers (e.g. `23402` =
Quindo Pink, `24100` = aniline black), and the `sh-1 … sh-4` columns are the four steps
of each pigment's **Kremer shade ladder** (masstone → tints). The sampling grid
(405.37–995.83 nm) and band spacing match the dataset exactly.

> H. Deborah, **"Hyperspectral Pigment Dataset,"** *2022 12th Workshop on Hyperspectral
> Imaging and Signal Processing: Evolution in Remote Sensing (WHISPERS)*, Rome, Italy,
> 2022, pp. 1–5. IEEE. doi: 10.1109/WHISPERS56178.2022.9955067.

- IEEE Xplore: <https://ieeexplore.ieee.org/document/9955067/>
- Dataset (Zenodo), DOI `10.5281/zenodo.5592485`: <https://zenodo.org/records/5592485>
- Interactive spectral-library browser (HypPigments): <https://hyppigments.streamlit.app/>

The dataset comprises hyperspectral images of 195 pigment patches and spectral libraries
from 327 unique pigments, 186 bands at ~3.26 nm spacing over 405.37–995.83 nm. **The
pigment patches were measured from Kremer Pigmente colour charts.**

#### Expanded palette (the full 327-pigment spectral library)

The original five bases above are a hand-picked subset. The **selectable gamut palettes**
on `/spectral` (5 / 8 / 10 / 12 / 16 pigments) are built from the dataset's *averages*
spectral library — `__speclib_averages.sli` + `.hdr`, the small (~1.2 MB) ENVI files that
hold all 327 pigments' average reflectance, so there is no need for the 39 GB of
hyperspectral images. Pipeline:

- `scripts/gamut/envi_speclib.py` — parses the ENVI `.hdr`/`.sli`.
- `scripts/gamut/build_pigments.py` — takes each pigment's masstone (sh-1), resamples to
  the 38-bin engine grid, derives a tinting strength from the most-dilute tint (sh-4), and
  writes `data/pigments_library.json` (mirrored to `app/data/`). The dataset contains **no
  white pigment**, so Titanium White is kept from `static/pigments/titanium white.txt`.
- `scripts/gamut/optimize.py` — measures each candidate set's **CIELAB convex-hull gamut
  volume** under the exact `/spectral` Kubelka–Munk model (pure + pairwise mixtures), finds
  the gamut-optimal white/black/red/yellow/blue, then **greedily** adds the pigment that
  most enlarges the gamut. Writes `data/palette_recommendations.json` + `data/GAMUT_REPORT.md`.
  The widest-gamut W/K/R/Y/B roughly **doubles** the shipped five's gamut. (In the
  single-constant KM model the reachable gamut depends only on the reflectance curves, not
  tinting strength.)

Raw downloads are cached under `data/zenodo_5592485/` (re-fetch via the Zenodo API URLs
above). `app/routes.build_spectral_palettes()` serves these sets to the `/spectral`
palette-size selector.

The **Gamut Lab** (`/gamut`) runs the same algorithm interactively: `app/gamut_lab.py`
computes the CIELAB convex-hull gamut of any chosen pigment set at request time and
greedily grows a palette from user-locked pigments within a chosen candidate pool. Routes:
`GET /gamut/catalog`, `POST /gamut/optimize`, `POST /gamut/score`; UI in
`templates/gamut_lab.html` + `static/gamut_lab.js`.

### Kremer Pigmente

The physical pigments behind the dataset above, identified throughout the code by Kremer
article numbers and shade-ladder steps (`sh-1 … sh-4`).

- <https://www.kremer-pigmente.com/>

---

## 2. Colour-mixing model (Kubelka–Munk)

### Spectral.js — Ronald van Wijnen

Vendored as [`spectral_by_wijnen.js`](spectral_by_wijnen.js); the single-constant
Kubelka–Munk forward model in [`app/spectral_km.py`](app/spectral_km.py) and
`static/spectral.js` follows its approach (K/S space, `KS(R) = (1−R)²/2R`).

- Repo (MIT License, © 2025 Ronald van Wijnen): <https://github.com/rvanwijnen/spectral.js>

### Kubelka–Munk theory (foundational)

> P. Kubelka and F. Munk, "Ein Beitrag zur Optik der Farbanstriche,"
> *Zeitschrift für technische Physik*, vol. 12, pp. 593–601, 1931.

> P. Kubelka, "New Contributions to the Optics of Intensely Light-Scattering Materials.
> Part I," *Journal of the Optical Society of America*, vol. 38, no. 5, pp. 448–457, 1948.

### Saunderson surface correction (referenced for the future two-constant spike)

> J. L. Saunderson, "Calculation of the Color of Pigmented Plastics,"
> *Journal of the Optical Society of America*, vol. 32, no. 12, pp. 727–736, 1942.

---

## 3. Inverse-solve / recipe formulation

### yargo13/color-formulation

The recipe solver in [`app/spectral_km.py`](app/spectral_km.py) adapts its **strategy**
(pigment-subset selection + Pareto-style recipe options trading simplicity vs. accuracy)
and its **dual-illuminant fitness** (D65 + A, to resist metamerism) from this project. It
is also the reference for the deferred two-constant K(λ)/S(λ) spike noted in the module.

The same solver powers two entry points, both palette-aware: `/reverse_engineer` (upload a
measured curve and pick a palette → `solve_recipe`) and the **"Give me a mix"** button on
`/spectral` (`solve_mix` via `POST /spectral/solve` → one fast continuous multi-illuminant
solve over the active palette). `solve_recipe` solves the five (and any set ≤ 7 pigments)
exhaustively over every subset for a true Pareto front; the wider gamut palettes
(8/10/12/16) use a forward-greedy subset search so the solve stays sub-second instead of
2ⁿ-exploding. Choosing a wider palette in `/reverse_engineer` therefore reaches target
colours the classic five cannot (e.g. a saturated cyan drops from ΔE ≈ 22 to ≈ 12).

- Repo: <https://github.com/yargo13/color-formulation>
- Two-constant Kubelka–Munk paint-formulation engine (Java) solving with a genetic
  algorithm and colorimetric matching under D65 and A illuminants. Pigments/materials:
  SilcPig pigments and Dragon Skin 10 silicone elastomer (Smooth-On), Dim Clay flockings.

---

## 4. Colour science (general references)

> G. Wyszecki and W. S. Stiles, *Color Science: Concepts and Methods, Quantitative Data
> and Formulae*, 2nd ed. Wiley, 2000.

> R. W. G. Hunt, *Measuring Colour*, 4th ed. Wiley, 2011.

> M. D. Fairchild, *Color Appearance Models*, 3rd ed. Wiley, 2013.

- CIE ΔE2000 colour-difference formula (used as the perceptual matching objective).
- CIE standard illuminants D65 and A, and the CIE 1931 2° colour-matching functions.

---

## 5. Mixbox (classic game-mode mixing)

The **classic** (non-spectral) game mode mixes colours with **Mixbox** — a learned
latent-space pigment-mixing model (RGB → 7-number latent → cubic polynomial → RGB). It is
distinct from the `/spectral` Kubelka–Munk engine: Mixbox bakes the pigment physics
offline into a lookup table + polynomial (fast, but the spectra are frozen — no new
pigments), whereas `/spectral` mixes live in reflectance space. See
[`demo/pitch.html`](demo/pitch.html).

> Š. Sochorová and O. Jamriška, **"Practical Pigment Mixing for Digital Painting,"**
> *ACM Transactions on Graphics (Proc. SIGGRAPH Asia)*, vol. 40, no. 6, article 234,
> 2021. doi: 10.1145/3478513.3480549.

- Project page: <https://scrtwpns.com/mixbox/>
- Repo: <https://github.com/scrtwpns/mixbox>
- License: **CC BY-NC 4.0** (non-commercial); commercial licensing via Secret Weapons.

---

## 6. Software libraries

- **colormath** — Lab/XYZ colour objects and ΔE2000 (`app/spectral_km.py`).
- **NumPy / SciPy** — numerics and `scipy.optimize.minimize` for the recipe inversion.

---

*Last updated: 2026-06-17.*
