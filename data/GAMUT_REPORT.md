# Widest-gamut pigment palettes — Hyperspectral Pigments (Zenodo 5592485)

Gamut = CIELAB convex-hull volume of all Kubelka–Munk mixtures, computed with the exact `/spectral` engine (`app/spectral_km.py`). Higher = more colours reachable.

- **Shipped 5** (W/K/cad-red/cad-yellow/ultramarine): gamut **59,105**
- **Best W/K/R/Y/B** (one pigment per primary, gamut-optimal): gamut **116,635**  (+97% vs shipped)

## Best 5 — one pigment per painter primary (W/K/R/Y/B)

| role | pigment | Kremer | group | hue° | chroma | sRGB |
|------|---------|--------|-------|-----:|-------:|------|
| white | titanium white | white | White | 232 | 2 | rgb(235,241,243) |
| black | cassel brown, wood stain | 41050 | Earth-Brown-Black | 324 | 3 | rgb(57,54,57) |
| red | cadmium orange no.1, medium | 21100 | Cadmium | 37 | 54 | rgb(186,76,60) |
| yellow | bismuth-vanadate yellow, light | 43915 | Yellow-3 | 106 | 58 | rgb(175,180,66) |
| blue | cobalt blue dark, greenish | 45701 |  | 280 | 45 | rgb(25,89,161) |

## Growth sequence — what to add next

The first five rows are the best W/K/R/Y/B above. Each later row adds the pigment that most enlarges the CIELAB gamut given everything above it. A palette of size *k* is the first *k* rows.

| # | role | pigment | Kremer | family | hue° | chroma | gamut after | Δ gamut |
|--:|------|---------|--------|--------|-----:|-------:|------------:|--------:|
| 1 | white | titanium white | white | neutral | 232 | 2 | 0 | — |
| 2 | black | cassel brown, wood stain | 41050 | neutral | 324 | 3 | 5 | — |
| 3 | red | cadmium orange no.1, medium | 21100 | orange | 37 | 54 | 2,832 | — |
| 4 | yellow | bismuth-vanadate yellow, light | 43915 | yellow | 106 | 58 | 48,897 | — |
| 5 | blue | cobalt blue dark, greenish | 45701 | blue | 280 | 45 | 116,961 | — |
| 6 | p6 | cobalt blue turquoise, light | 45750 | cyan | 215 | 30 | 146,668 | +29,706 |
| 7 | p7 | studio pigment egg yolk yellow | 55125 | yellow | 82 | 81 | 174,729 | +28,061 |
| 8 | p8 | fluorescent pigment violet | 56450 | violet | 327 | 37 | 202,463 | +27,734 |
| 9 | p9 | cadmium green, dark | 44510 | green | 157 | 37 | 211,225 | +8,762 |
| 10 | p10 | studio pigment yellow | 55100 | yellow | 99 | 57 | 217,404 | +6,179 |
| 11 | p11 | cadmium yellow no.9, dark | 21060 | yellow | 95 | 50 | 220,707 | +3,304 |
| 12 | p12 | ultramarine blue, greenish light | 45040 | blue | 285 | 29 | 223,297 | +2,590 |
| 13 | p13 | irgazine® red DPP BO | 23180 | orange | 24 | 39 | 224,828 | +1,531 |
| 14 | p14 | cadmium orange no.0.5, light | 21090 | orange | 46 | 54 | 225,771 | +943 |
| 15 | p15 | cadmium orange no.2, vermilion | 21110 | orange | 25 | 47 | 226,608 | +837 |
| 16 | p16 | fluorescent pigment magenta red | 56400 | red | 359 | 32 | 227,202 | +594 |

## Recommended palette sizes (prefixes of the sequence)

- **5 pigments** — gamut 116,961: titanium white, cassel brown, wood stain, cadmium orange no.1, medium, bismuth-vanadate yellow, light, cobalt blue dark, greenish
- **8 pigments** — gamut 202,463: titanium white, cassel brown, wood stain, cadmium orange no.1, medium, bismuth-vanadate yellow, light, cobalt blue dark, greenish, cobalt blue turquoise, light, studio pigment egg yolk yellow, fluorescent pigment violet
- **10 pigments** — gamut 217,404: titanium white, cassel brown, wood stain, cadmium orange no.1, medium, bismuth-vanadate yellow, light, cobalt blue dark, greenish, cobalt blue turquoise, light, studio pigment egg yolk yellow, fluorescent pigment violet, cadmium green, dark, studio pigment yellow
- **12 pigments** — gamut 223,297: titanium white, cassel brown, wood stain, cadmium orange no.1, medium, bismuth-vanadate yellow, light, cobalt blue dark, greenish, cobalt blue turquoise, light, studio pigment egg yolk yellow, fluorescent pigment violet, cadmium green, dark, studio pigment yellow, cadmium yellow no.9, dark, ultramarine blue, greenish light
- **16 pigments** — gamut 227,202: titanium white, cassel brown, wood stain, cadmium orange no.1, medium, bismuth-vanadate yellow, light, cobalt blue dark, greenish, cobalt blue turquoise, light, studio pigment egg yolk yellow, fluorescent pigment violet, cadmium green, dark, studio pigment yellow, cadmium yellow no.9, dark, ultramarine blue, greenish light, irgazine® red DPP BO, cadmium orange no.0.5, light, cadmium orange no.2, vermilion, fluorescent pigment magenta red

## Caveats

- **Diminishing returns.** 5→8 pigments is the big jump (the violet/cyan/high-chroma-yellow corners the cadmium set misses); past ~12 each pigment adds <2% — pick 5 or 8 for a working palette, 10–12 for a "full" set.
- **Gamut ignores tinting strength.** In the single-constant KM model the reachable colours depend only on the reflectance curves; tinting strength only changes how much dial you turn, not what is reachable. Greedy growth is a near-optimal heuristic for this monotone, ~submodular objective.
- **Fluorescent pigments** (the "fluorescent …" rows) extend the violet/magenta corner, but KM models reflectance only — their real-world punch comes from fluorescence we do not simulate, and their measured reflectance was clipped from >1. Treat them as optional gamut-stretchers.
- **No true crimson masstone.** This dataset is cadmium/earth-heavy; its warm corner is best anchored by cadmium *orange*, and a high-chroma true red only appears late (fluorescent magenta, #16). White is fixed to the shipped Titanium White — the dataset contains no white pigment.
