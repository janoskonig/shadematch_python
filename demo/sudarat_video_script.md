# Video script — for Prof. Sudarat Kiat-amnuay

A personal ~6-minute screen-recording demo with facecam, addressed directly to her.
Single track, **color-scientist register** (she's an expert — no dumbing down).
Pace ≈ 150 wpm; the spoken text below runs ≈ 6:00 with demo pauses.

**Setup before you hit record**
- App running locally; have these tabs/screens ready in order: `/spectral`, `/reverse_engineer`, the **Gamut Lab**, `/calibration`.
- On `/spectral`, pre-load a real skin target (Xiao 2017) and have the **Viewing light** chooser visible.
- Facecam in a corner. **Look at camera** on the framing beats (open, asks, close); **look at screen** while pointing at curves/recipes/gamut.
- Fallback stills if a screen misbehaves: `demo/screenshots/desktop-slide-*.png`, `demo/evolution/now-*.png`.

---

## 1 · Open — *to camera* (≈0:00–0:40)

"Prof. Kiat-amnuay — thank you for taking a look at this. I'm János König, from Semmelweis. I built this tool, ShadeMatch, and I'm showing it to you specifically because it lives in the seam between your work and Rade Paravina's: he built the science of shade-matching thresholds, and you do maxillofacial skin-tone prosthetics — colouring silicone to match real human skin, with metamerism and colour stability as the hard problems. This app sits exactly there. Let me show you what it does, and then I'll ask you for one concrete thing."

## 2 · Where it came from (≈0:40–1:05)

*[show `demo/evolution/then-prototype-home.png` → `now-spectral.png`, or flip the home screen]*

"It started as a colour-matching game — two boxes and five swatches. Over about fifteen months it turned into a real pigment instrument: a 327-pigment spectral catalog, Kubelka–Munk mixing, a gamut lab, and targets that are real measured human skin. The goal sharpened from 'match a colour' to 'match real human skin.'"

## 3 · The premise (≈1:05–1:35)

"The premise you already know better than I do: displays are additive tristimulus, pigments are subtractive spectral colour formation — wavelength-dependent absorption and scattering. So I never average RGB. Everything happens in the reflectance domain. Each pigment is a reflectance spectrum on a 38-band grid, 380 to 730 nanometres — 327 of them, from Deborah's WHISPERS dataset, masstone-resampled to the engine grid."

## 4 · The engine (≈1:35–2:05)

"Mixing is single-constant Kubelka–Munk: per band, K-over-S is (1−R)² over 2R, concentrations blend additively in K/S space, then I invert back to reflectance. Tinting strengths are measured from a dilution ladder, not fitted. There's a live Saunderson correction for surface and internal boundary reflections, and reflectance goes to XYZ through the CIE 1931 2-degree observer, then to CIELAB with no sRGB round-trip — so out-of-gamut targets stay unclipped. The objective everywhere is CIEDE2000."

## 5 · Surface 1 — Spectral, and metamerism made visible (≈2:05–3:00)

*[on `/spectral`: pick a Xiao skin target; show target vs mix swatch and live ΔE]*

"First surface. I pick a real skin tone — these are Xiao's 2017 means, four ethnicities across four body sites — and reproduce it with a chosen palette under live Kubelka–Munk, with the ΔE scored as I go."

*[switch the **Viewing light** chooser: D65 → A → F11, point at the ΔE changing]*

"And this is the part I think matters most for you. This is a viewing-light chooser. The same recipe, re-rendered and re-scored under D65 daylight, incandescent A, and fluorescent F11. Watch the ΔE move — a match that reads 2.4 under daylight can drift to 3.0 under fluorescent. That's metamerism made visible, on screen, for a prosthetic-relevant target. The CMFs are von-Kries-adapted to the engine white, so a neutral stays neutral — only genuine spectral mismatch shifts."

## 6 · Surface 2 — Reverse-engineer, with an honest verdict (≈3:00–3:40)

*[on `/reverse_engineer`: feed a measured colour, show the Pareto recipes + verdict]*

"Second surface: the inverse solver. I feed it a measured colour and my palette, and it searches every pigment subset — multi-start L-BFGS-B — and returns a Pareto front of recipes trading simplicity against accuracy, scored under several illuminants to dodge metamerism. The part I'm proud of is the honesty: when the colour is out of the palette's gamut, it says *out of gamut* — and tells you a wider palette could reach it, rather than quietly returning a bad match."

## 7 · Surface 3 — Gamut Lab (≈3:40–4:15)

*[Gamut Lab: show the reachable hull + the human-skin overlay]*

"Third surface, the Gamut Lab. For any pigment set it computes the reachable CIELAB gamut — the convex hull of all pure and pairwise KM mixtures — reports its volume and how much of the 327-pigment catalog it covers, and overlays the human-skin hull from Xiao on the a*–b* plane. So the question becomes literal: does your palette's shape contain the skin tones you need to make? A greedy search builds the widest five-pigment set, and it roughly doubles the coverage of the shipped five."

## 8 · The calibration game — *your* acceptability question (≈4:15–4:55)

*[on `/calibration`: show a pair-judgment, then the threshold fit]*

"There's also an eye-calibration game. It shows colour-difference pairs and fits the observer's own perceptibility and acceptability thresholds — Paravina's PT and AT, measured per person. And I anchored the acceptability question to your clinical one, literally: *would it be acceptable to wear this colour difference on your face?* That's the embodied referent — a prosthesis mismatch a patient would actually wear in public. It runs under a Semmelweis ethics approval, RKEB 167/2025, so the psychophysics is captured properly."

## 9 · Honesty beat — *to camera* (≈4:55–5:15)

"One honest caveat, because you'd catch it anyway. The skin targets are Xiao's published *means* — and the spectrum I draw for them is a metameric reconstruction from that mean, not a measured curve. I draw it dashed, on purpose, so no one mistakes it for real data. Which is exactly why I'm here."

## 9b · Provenance — *to camera* (optional, ≈+0:20)

"And I want to be explicit about what's mine versus what I'm standing on, because it
matters. The physics is the literature — Kubelka–Munk, Saunderson, CIEDE2000 — the mixing
engine is van Wijnen's Spectral.js, the skin data is your colleague-adjacent Xiao 2017,
the thresholds are Paravina's, and the solver strategy I adapted from a silicone-elastomer
formulation project. All cited. What's mine is the *instrument*: the skin-coverage gamut
lab, the honest out-of-gamut verdict, the visible-metamerism view, and the skin-centred
calibration. I build on the literature, not around it."

## 10 · The ask (≈5:15–5:45)

*[back to camera, or show slide-15/16 stills]*

"So, two concrete asks. First, pigment data: each silicone pigment at full strength plus a few dilutions, measured **over both black and white backings** — because that's what lets me solve absorption K and scattering S independently and move from single- to two-constant Kubelka–Munk, which is what real opacity and tinting strength need. Second, real measured skin spectra — per-subject, not means. Same format for both: a CSV of wavelength against reflectance, 380 to 730 nanometres in 10-nanometre steps, reflectance nought to one."

## 11 · Close — *to camera* (≈5:45–6:00)

"That's the whole pitch. Colour by physics, not faked pixels — and it's already aimed at human skin: foundation, cosmetics, and your world, prosthetics. The physics is built. I just need your measurements to point it at something real. Thank you — I'd love to hear what you think."

---

### Delivery notes
- **One depth, all the way:** color-scientist. Don't simplify for her.
- **The four lines to nail:** §5 "metamerism made visible," §6 "it says *out of gamut* — honestly," §8 "would it be acceptable to wear this on your face," §10 "over both black and white backings… K and S independently."
- **Facecam:** to camera on §1, §9, §10, §11; to screen while pointing on §5, §6, §7, §8.
- **If you cut to ~3 min:** keep §1, §5 (metamerism), §8 (acceptability), §10 (asks), §11. Those are the beats that are *for her*.
- **Numbers to keep current:** 327 pigments, Xiao 2017 (4×4), D65/A/F11, RKEB 167/2025. Update if the build changes before you record.
- Full provenance with DOIs is in **`demo/SOURCES.md`** (Part A = prior work, Part B = my
  contributions) — keep it open in case she asks "what's actually new here?".
