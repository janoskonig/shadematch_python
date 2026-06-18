# Voiceover narration — ShadeMatch pitch deck

Word-for-word spoken script, keyed to `demo/pitch.html` (**18 slides**). Advance with **→**.

**Three depths, mirroring the deck toggle** (`5-year-old / high-schooler / color scientist`,
keys 1/2/3). For the **10 concept slides** the on-screen text changes with the toggle, so each
has three spoken variants below — read the one matching what's on screen. The **8 framing
slides** (title, evolution, then & now, three surfaces, two engines, concrete asks, sources,
close) read the same at any depth, so they have one line.

Run time: ELI5 pass ≈ **4:30**, high-schooler ≈ **5:30**, color-scientist ≈ **6:30**.
Tone: confident and direct, but precise — every claim is cited on the slide and in `SOURCES.md`.

> Refreshed for the 2026-06-18 build: 327-pigment catalog, Gamut Lab, palette-aware solving,
> real measured skin targets (Xiao 2017).

---

### Slide 1 · Title — *framing*
"This is ShadeMatch. It started as a colour-matching game — but it's become a real pigment
instrument: a 327-pigment spectral catalog, Kubelka–Munk mixing, gamut analysis, and targets
that are real measured human skin. I'll show you where it came from, how it works, and then
ask you for something."

### Slide 2 · Evolution timeline — *framing*
"Fifteen months, 168 commits. A weekend prototype — two boxes and five swatches. Then a
touch-first redesign. And now a spectral *and* gamut lab: 327 pigments, measured reachable
gamut, and a read on how much of human skin a palette can cover. The goal sharpened from
'match a colour' to 'match real human skin.'"

### Slide 3 · Then & now — *framing*
"Here's the jump. 2025: plain HTML, empty boxes. 2026: the targets are *real measured skin
tones* — Xiao's 2017 study, four ethnicities across four body sites — reproduced with a chosen
palette under live Kubelka–Munk. Same idea, a completely different machine."

### Slide 4 · The big idea
- **5-year-old:** "Screens make colour by adding light together. Paint makes colour by soaking
  light up. They're opposites — so mixing paint needs its own kind of maths."
- **High-schooler:** "Displays are additive — red, green and blue light add to white. Pigments
  are subtractive — each absorbs part of the spectrum. Average RGB and you've modelled the wrong
  process; that's why naive tools get paint mixes wrong."
- **Color scientist:** "It's additive tristimulus versus subtractive spectral colour formation.
  Pigment mixing is wavelength-dependent absorption and scattering, so we model it in the
  reflectance domain with Kubelka–Munk, not by interpolating RGB."

### Slide 5 · Three surfaces — *framing*
"Three surfaces. Spectral: pick a real skin tone, reproduce it with your palette, read the ΔE
live. Reverse-engineer: feed a measured colour, get palette-aware recipes with an honest
verdict. And the Gamut Lab: pick pigments and see the range of colours they can mix — and how
much of human skin that covers."

### Slide 6 · The light-fingerprint
- **5-year-old:** "A colour here isn't just 'red.' It's a wiggly line showing how much light
  bounces back at every colour of the rainbow — a fingerprint for that paint. And we have 327."
- **High-schooler:** "Each pigment is a reflectance spectrum — reflectance across 38 wavelength
  bands. Far more than an RGB code. The catalog holds 327 measured pigments from Deborah's
  dataset, off Kremer charts. And these five aren't the usual cadmiums — they're the
  widest-gamut set the optimizer found, one per primary. Palettes scale from 5 up to 16."
- **Color scientist:** "Pigments are spectral reflectance R(λ) on a 38-band grid; working
  spectrally preserves metameric distinctions tristimulus discards. 327 pigments from the
  Deborah Zenodo library, masstone resampled to the engine grid. The default five are the
  gamut-optimal W/K/R/Y/B — cassel brown, cadmium orange, bismuth-vanadate yellow, cobalt blue."

### Slide 7 · The mixing maths
- **5-year-old:** "Turn each colour into one number — how thirsty it is for light. Blend the
  thirst, then turn it back into a colour."
- **High-schooler:** "Convert reflectance to K-over-S — absorption over scattering — blend the
  K/S values by amount, then invert back to reflectance. Single-constant Kubelka–Munk, from the
  1931 paper via van Wijnen's Spectral.js."
- **Color scientist:** "Single-constant KM: per-band K/S is (1−R)² over 2R, concentration-
  weighted additivity in K/S space, inverse back to R. Tinting strengths measured from the
  dilution ladder, not fitted."

### Slide 8 · Glare / Saunderson
- **5-year-old:** "Some light glares off the shiny top before it even reaches the paint. We
  subtract that glare so the maths sees the real colour."
- **High-schooler:** "The Saunderson correction removes surface and internal boundary
  reflections, mapping measured reflectance to the internal value the model needs. 1942."
- **Color scientist:** "Saunderson k1 external-specular and k2 internal terms relate measured
  and internal reflectance; identity at zero, exposed as live parameters for substrate-aware
  matching."

### Slide 9 · CIE eye model + ΔE
- **5-year-old:** "To get the colour you actually see, we run the wiggly line through a model of
  the human eye in daylight."
- **High-schooler:** "Multiply reflectance by the CIE 1931 colour-matching functions under D65
  daylight to get XYZ, then Lab; ΔE is perceived difference — zero is identical."
- **Color scientist:** "R(λ) to XYZ via the CIE 1931 2° observer under D65, then to CIELAB with
  no sRGB round-trip so out-of-gamut targets stay unclipped; the objective is CIEDE2000."

### Slide 10 · Mixbox mechanics
- **5-year-old:** "The classic game uses a giant pre-made cheat-sheet: look the colour up, blend,
  look the answer up. Super fast — but you can't change the paints."
- **High-schooler:** "Mixbox encodes RGB as seven latent numbers — four pigment amounts plus a
  residual — blends them linearly, decodes with a fixed cubic polynomial. The KM physics is
  baked in offline. SIGGRAPH Asia 2021."
- **Color scientist:** "RGB to a 7-D latent via a trained 64³ LUT — four primary concentrations
  plus an RGB residual — linear blend, cubic-polynomial decode. KM solved offline; the runtime
  is closed and non-tweakable. CC BY-NC."

### Slide 11 · Two engines — *framing*
"So, two engines. Mixbox — learned, closed, frozen, non-commercial. The spectral side —
explicit Kubelka–Munk on real curves, every parameter live, open to your data. A closed box
versus a glass box."

### Slide 12 · The inverse solver
- **5-year-old:** "Give it a real measured colour, pick your paints, and it finds the recipe —
  and tells you honestly if those paints can even make it."
- **High-schooler:** "For your chosen palette it tries every pigment combination, returns a
  Pareto front of recipes trading simplicity for accuracy, scores them under several lights to
  dodge metamerism, and gives a reachability verdict — 'a wider palette can' when it can't. On
  this example it says *out of gamut*, and says so honestly."
- **Color scientist:** "Palette-aware exhaustive subset solve with multi-start L-BFGS-B,
  collapsed to effective sets, Pareto front over pigment-count and cost; objective is multi-
  illuminant CIEDE2000 — D65 headline plus A and F11 stressors — outputting continuous and
  integer-drop recipes and a gamut-reachability band. Strategy adapted from yargo13."

### Slide 13 · The Gamut Lab
- **5-year-old:** "Pick a handful of paints and it draws the range of colours they can mix, then
  lays real human skin tones on top. If your shape covers the skins, you can mix them."
- **High-schooler:** "For any pigment set it measures the reachable CIELAB gamut — volume and how
  much of the 327-pigment catalog it covers — and overlays the human-skin gamut. A greedy search
  builds the widest palette; the best five roughly double the shipped five."
- **Color scientist:** "Convex-hull volume in CIELAB of all pure and pairwise KM mixtures for a
  set; ΔE2000 and volume coverage of the catalog masstones; greedy gamut-maximising
  construction; an a*–b* overlay of the Xiao 2017 skin-mean hull for containment checks."

### Slide 14 · Honesty beat
- **5-year-old:** "Being honest: the skin targets are average colours from a study, and their
  wiggly line is rebuilt from the colour, not really measured. We draw it dashed so nobody's
  fooled."
- **High-schooler:** "The skin targets are Xiao 2017 mean chromaticities; their displayed
  spectrum is a metameric reconstruction from that mean — drawn dashed, not a measured curve."
- **Color scientist:** "Skin targets are literature mean CIELAB per ethnicity and site; the
  overlaid curve is one of infinitely many metamers mapping to that mean. Real per-subject
  measured spectra would replace both the means and the reconstruction."

### Slide 15 · The core ask — black & white backing
- **5-year-old:** "To really know a paint we need two numbers: how much light it eats and how
  much it scatters. Paint it over a black card and a white card and you get both."
- **High-schooler:** "Measuring a film over black and white backings lets you solve absorption K
  and scattering S separately — the jump from single- to two-constant Kubelka–Munk."
- **Color scientist:** "Over-black and over-white reflectance at known film thickness inverts the
  KM hyperbolic layer equations for K(λ) and S(λ) independently, enabling two-constant mixing and
  physical opacity and tinting strength."

### Slide 16 · Concrete asks — *framing*
"So, two things. Pigment data: each paint at full strength plus a few dilutions, ideally over
black and white backings, 380 to 730 nanometres in 10-nanometre steps. And skin spectra: today
it uses Xiao's published means — real *measured* skin spectra become true targets. Same format:
a CSV of wavelength and reflectance, nought to one."

### Slide 17 · Sources — *framing*
"None of this is hand-waving. Pigments: Deborah, WHISPERS 2022, on Zenodo. Skin gamut: Xiao,
2017. Model: Kubelka–Munk and Spectral.js. Solver: yargo13's. Mixbox: the SIGGRAPH paper. It's
all in SOURCES.md, with DOIs. I'm building on the literature, not around it."

### Slide 18 · Close — *framing*
"That's it. Colour by physics, not faked pixels. 327 measured pigments and a gamut lab that
scores skin coverage. A solver honest enough to say when a colour's out of reach. And it's
already aimed at human skin — foundation, cosmetics, prosthetics. That's the paper. The physics
is built. I just need your measurements to point it at something real."

---

### Delivery notes
- **Match the spoken depth to the screen.** Set the toggle (or keys 1/2/3) and read that
  variant. Easiest is to commit to one depth for the whole run — "color scientist" for the
  fellow, "high-schooler" if the recording is for a broader audience.
- **Pace:** ~150 words/min.
- **Facecam beats:** look at camera on the framing slides 1, 4, 14, 18; look at the screen while
  pointing at curves/recipes/gamut on 3, 6, 7, 12, 13.
- **The lines to nail** (any depth): slide 12 "*out of gamut* … a wider palette can," slide 13
  "if your filled area covers it, your palette can mix those skin tones," slide 14 "we draw it
  dashed, on purpose," and the close "point it at something real."
