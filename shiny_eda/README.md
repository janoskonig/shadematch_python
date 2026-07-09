# ShadeMatch — interaktív EDA (Shiny)

A statikus riport (`notes/ShadeMatch_EDA_v4_statisztikus.html`) *olvasásra* való, fix nézetekkel.
Ez a Shiny-app a *felfedezős* párja: ugyanaz az adat és ugyanazok a származtatott jellemzők, de a
küszöbök tekergethetők (időtartam-plafon, ΔE-plafon, kimenet-szűrő, nehézség-tartomány, PCA-küszöb).

## Fájlok
- `global.R` — adatbetöltés + jellemzők (a riporttal azonos logika), paletták, segédfüggvények.
- `app.R` — UI (sidebar-szűrők + tabok) és szerver.

## Adat
Az app a `shadematch_v2` CSV-ket olvassa: `mixing_sessions`, `mixing_attempts`, `users`,
`target_colors`, `mixing_attempt_events`. A helyét a **`SHADE_DATA`** környezeti változó adja meg;
ha nincs beállítva, ezeket próbálja: `data/shadematch_v2`, `../data/shadematch_v2`, `shadematch_v2`.

A repo gyökeréből futtatva a `../data/shadematch_v2` alapból megtalálja. Deploynál a legegyszerűbb,
ha a CSV-ket bemásolod egy `shiny_eda/data/` mappába (akkor `SHADE_DATA` sem kell).

## Lokális futtatás
```r
# a repo gyökeréből:
SHADE_DATA=data/shadematch_v2 R -e 'shiny::runApp("shiny_eda", launch.browser=TRUE)'
```
Szükséges csomagok: `shiny`, `tidyverse`, `lubridate`, `scales`, `plotly`.

## Deploy (a szervert te oldod meg)
- **shinyapps.io / Posit Connect:** `rsconnect::deployApp("shiny_eda")` — előbb másold a CSV-ket
  `shiny_eda/data/`-ba, hogy felkerüljenek.
- **Render / Docker / bármilyen R-szerver:** egy `R -e 'shiny::runApp("shiny_eda", host="0.0.0.0",
  port=as.integer(Sys.getenv("PORT","8080")))'` indítás; a CSV-ket bundle-öld vagy `SHADE_DATA`-val
  mutass rájuk.
- **shinylive (statikus, WASM):** ha mégis Netlify-ra kell szerver nélkül, a `shinylive::export()`
  kimenete statikusan tárolható — de nehéz payload és webR-csomagkorlátok (l. a korábbi jegyzeteket).

## Fülök
- **Ráfordítás & kimenet** — lépésszám/időtartam vs. ΔE, és lépésszám vs. időtartam (kimenet szerint).
- **Stratégia** — elvétel-/pigmentváltás-/túllövés-arány a kimenet szerint; legjobb vs. feladáskori ΔE.
- **Trajektória** — medián ΔE-lefutás a próbálkozáson belül.
- **Célszín & nehézség** — L\*/C\*ab/h° vs. P(tökéletes), és a CIELAB-színtér 3D-ben.
- **Korreláció** — Spearman-rangkorrelációs hőtérkép.
- **Felhasználó (PCA)** — viselkedési stratégia-tér biplot.
