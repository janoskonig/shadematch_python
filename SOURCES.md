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
