# Provenance & sources — ShadeMatch

The point of this file: **separate what I built from what I'm standing on.** Every
borrowed idea, dataset, model, and library is credited below with a real reference; the
second half lists what is original to ShadeMatch so the line is unambiguous. Nothing here
is uncredited.

> Verification status: academic citations and the Zenodo dataset were checked online
> (June 2026); DOIs/links are live. Two licensing caveats are flagged explicitly.

---

## Part A — Prior work I build on (not mine)

### Colour-science theory
- **Kubelka–Munk theory (single-constant).** Kubelka, P. & Munk, F. (1931). *Ein Beitrag
  zur Optik der Farbanstriche.* Zeitschrift für technische Physik 12: 593–601. — The
  forward mixing model (K/S = (1−R)²/2R, concentration-weighted K/S, invert to R).
- **Saunderson correction.** Saunderson, J.L. (1942). *Calculation of the color of
  pigmented plastics.* JOSA 32(12): 727–736. DOI: 10.1364/JOSA.32.000727. — Surface /
  internal boundary reflection correction (k1, k2), exposed as live parameters.
- **CIEDE2000.** Sharma, G., Wu, W. & Dalal, E.N. (2005). *The CIEDE2000 color-difference
  formula.* Color Research & Application 30(1): 21–30. DOI: 10.1002/col.20070. — The ΔE
  objective used everywhere.
- **CIE 1931 2° standard observer / D65, A, F11 illuminants.** CIE colorimetry (CIE 15). —
  R(λ)→XYZ→CIELAB pipeline and the viewing-light re-scoring.

### Engines & code libraries
- **Spectral.js** — Ronald van Wijnen (2025), MIT License.
  https://github.com/rvanwijnen/spectral.js — The single-constant KM forward engine that
  `static/spectral.js` and the Python port `app/spectral_km.py` are built on. *(MIT — free
  to use/modify with attribution; attribution retained in `spectral_by_wijnen.js`.)*
- **Scott Allen Burns** — *Generating Reflectance Curves from sRGB Triplets* (LHTSS
  method). http://scottburns.us/reflectance-curves-from-srgb/ — Used by Spectral.js to
  build primary reflectance curves.
- **Color.js** — https://colorjs.io/ — Colour-conversion matrices / structural inspiration
  (via Spectral.js).
- **Mixbox** — Sochorová, Š. & Jamriška, O. (2021). *Practical Pigment Mixing for Digital
  Painting.* ACM TOG 40(6):234 (SIGGRAPH Asia 2021). DOI: 10.1145/3478513.3480549.
  https://scrtwpns.com/mixbox/ — The original game's pigment mixing.
  ⚠️ **License: CC BY-NC** — non-commercial use only. Keep that in mind for any
  commercial path; the open KM side has no such restriction.
- **yargo13/color-formulation** — https://github.com/yargo13/color-formulation — A
  two-constant KM **silicone-elastomer** colour-formulation engine (genetic algorithm,
  dual-illuminant D65+A matching) — notably, *maxillofacial-prosthetics* colour matching.
  I adapted two **ideas** from it: the solver *strategy* (pigment-subset selection +
  simplicity-vs-accuracy recipe options) and multi-illuminant fitness.
  ⚠️ **No license file** → all rights reserved by default. I therefore adapted the
  *approach*, not the code (my forward model is my own single-constant port). The
  attribution is in `app/spectral_km.py`'s header.

### Data
- **Pigment catalog (327 pigments).** *Hyperspectral Pigment Dataset* (Kremer colour
  charts), WHISPERS 2022. Zenodo: DOI 10.5281/zenodo.5592485.
  https://zenodo.org/records/5592485 — Masstone reflectances resampled from the native
  186-band / 3.26 nm grid to the engine's 38-band / 10 nm grid (`app/data/
  pigments_library.json`). *(Check the Zenodo record's license terms before redistributing
  the curves.)*
- **Human-skin targets & gamut.** Xiao, K., Yates, J.M., Zardawi, F., Sueeprasan, S.,
  Liao, N., Gill, L., Li, C. & Wuerger, S. (2017). *Characterising the variations in
  ethnic skin colours: a new calibrated data base for human skin.* Skin Research and
  Technology 23(1): 21–29. DOI: 10.1111/srt.12295. — Mean CIELAB per ethnicity (Caucasian,
  Chinese, Kurdish, Thai) × body site (forehead, cheek, inner arm, hand). The targets and
  the skin-hull overlay.

### Psychophysics reference
- **Perceptibility / acceptability thresholds.** Paravina, R.D. et al. (2015). *Color
  Difference Thresholds in Dentistry.* J Esthet Restor Dent 27(S1): S1–S9. DOI:
  10.1111/jerd.12149. — The PT/AT framework (ΔE00 PT≈0.8, AT≈1.8 for tooth colour) the
  calibration game anchors to. *(Mine are skin-centred with a sharp split field, so values
  may differ — which is itself the finding.)*

---

## Part B — Original to ShadeMatch (my contributions)

These are mine. Some are *new science*, most are **novel integration / application** of
the prior work above — and I label which, honestly, because for a paper that distinction
matters.

1. **The instrument as a whole** *(synthesis).* One tool unifying spectral KM mixing, an
   inverse recipe solver, a reachable-gamut lab, real measured-skin targets, gamified
   training, and ethics-approved psychophysical capture — aimed specifically at **human
   skin / maxillofacial prosthetics**. Each piece exists in the literature; combining all
   of them into one research-instrumented loop is the contribution.

2. **A consistency-guaranteed Python port** *(engineering).* `app/spectral_km.py` is a
   faithful port of the JS render engine, so a recipe the solver finds reproduces *exactly*
   what `/spectral` displays — no model drift between what you solve and what you see. The
   port is faithful to van Wijnen; the cross-surface consistency guarantee is my design.

3. **Gamut Lab skin-coverage framing** *(novel application).* Computing a palette's
   reachable CIELAB hull (pure + pairwise KM mixtures) and asking the literal clinical
   question — does it *contain* the human-skin hull, and what fraction of the catalog and
   of skin does it cover? Greedy gamut-maximising palette construction; A-vs-B palette
   comparison with **no privileged baseline**.

4. **Honest reverse-engineer verdict** *(novel UX/method).* Palette-aware Pareto recipes
   (simplicity vs accuracy) plus an explicit **out-of-gamut reachability verdict** ("a
   wider palette can") instead of silently returning a bad match. Multi-illuminant scoring
   to resist metamerism (strategy adapted from yargo13, model my own).

5. **Viewing-light chooser — metamerism made visible** *(novel feature).* Per-target
   re-rendering and ΔE re-scoring under D65 / A / F11, with display CMFs **von-Kries-adapted
   to the engine white** so neutrals stay neutral and only genuine spectral mismatch
   shifts. Built on the standard CIE machinery; the per-target visualisation is mine.

6. **Calibration instrument design** *(novel method + framing).* An imposed-ΔE
   method-of-constant-stimuli **trichotomy** (identical / acceptable / unacceptable) that
   yields *both* PT and AT from one ordinal judgment, with the acceptability referent
   anchored to the embodied clinical question — *"on a face, is this an acceptable match?"*
   Chunked self-contained sessions + hierarchical pooling across a user's sessions.
   Skin-centred, not tooth-centred (extends Paravina's framework to a new domain).

7. **Ethics-approved capture.** Semmelweis SE RKEB 167/2025 — the psychophysics is
   collected under formal approval.

---

### How to use this when challenged
If anyone asks "what's actually new here?": point to **Part B**, and note that everything
it rests on is named in **Part A** with a citation. The borrowed pieces are the *physics
and the data* (which should be standard and shared); the original work is the *instrument,
the skin-coverage and honest-verdict methods, and the skin-centred calibration design.*
