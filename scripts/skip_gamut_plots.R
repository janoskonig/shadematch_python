#!/usr/bin/env Rscript
# Visualise the skip (give-up) rows against the reachable Mixbox gamut.
#
# Input  (built by scripts/skip_gamut_prep.py):
#   artifacts/skip_gamut/skips_enriched.csv   target+achieved CIELAB + RGB per skip
#   artifacts/skip_gamut/mixbox_gamut_ab.csv  a*-b* hull of the Mixbox gamut
# Output: artifacts/skip_gamut/skip_gamut_plots.png (+ .pdf)
#
# Run:  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 Rscript scripts/skip_gamut_plots.R

suppressPackageStartupMessages({
  library(ggplot2); library(dplyr); library(readr); library(patchwork)
})

dir  <- "artifacts/skip_gamut"
d    <- read_csv(file.path(dir, "skips_enriched.csv"), show_col_types = FALSE) |>
  filter(mixing_model != "spectral" | is.na(mixing_model))
gam  <- read_csv(file.path(dir, "mixbox_gamut_ab.csv"), show_col_types = FALSE)

# Each point painted its own (target) colour so the a*-b* plane reads as a colour map.
d$col <- rgb(d$target_r, d$target_g, d$target_b, maxColorValue = 255)

# Radial error component: negative = achieved pulled toward the neutral axis (gray).
n <- sqrt(d$ta^2 + d$tb^2); n[n == 0] <- 1
d$radial <- ((d$aa - d$ta) * d$ta + (d$ab - d$tb) * d$tb) / n

close_ring <- function(df) rbind(df, df[1, ])   # close the hull polygon
gam <- close_ring(gam)

# ── Average deviation-vector field (weather-map style) ──────────────────────
# Bin the a*-b* plane into square cells; each cell's arrow = mean error vector
# (achieved - target) of the skips whose *target* falls in that cell.
BS  <- 12    # cell size in CIELAB units
AMP <- 3     # arrow amplification (mean vectors are small; scale up for legibility)
field <- d |>
  mutate(abin = floor(ta / BS) * BS + BS / 2,
         bbin = floor(tb / BS) * BS + BS / 2) |>
  group_by(abin, bbin) |>
  summarise(du = mean(aa - ta), dv = mean(ab - tb),
            mag = sqrt(mean(aa - ta)^2 + mean(ab - tb)^2), n = n(), .groups = "drop") |>
  filter(n >= 8)

theme_set(theme_minimal(base_size = 12) +
  theme(panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold"),
        plot.background = element_rect(fill = "white", colour = NA),
        panel.background = element_rect(fill = "white", colour = NA)))

# ── (1) a*-b* colour map + mean deviation field ─────────────────────────────
p1 <- ggplot() +
  geom_polygon(data = gam, aes(a, b), fill = NA, colour = "grey35", linewidth = .6) +
  geom_hline(yintercept = 0, colour = "grey85") +
  geom_vline(xintercept = 0, colour = "grey85") +
  geom_point(data = d, aes(ta, tb, fill = col), shape = 21, size = 1.7,
             stroke = .12, colour = "grey30", alpha = .9) +
  scale_fill_identity() +
  # weather-style mean-vector field
  geom_segment(data = field,
               aes(abin, bbin, xend = abin + du * AMP, yend = bbin + dv * AMP),
               arrow = arrow(length = unit(.18, "cm"), type = "closed"),
               linewidth = .7, colour = "black") +
  geom_point(data = field, aes(abin, bbin), size = .7, colour = "black") +
  coord_equal() +
  labs(title = "Feladott célok színtérképe és az átlagos eltérésvektor-mező",
       subtitle = sprintf("pont = cél a saját színén · fekete nyíl = cellánkénti átlagos hiba (%d×), cellaméret %d, n≥8",
                          AMP, BS),
       x = "a*", y = "b*")

# ── (2) chroma contraction: achieved vs target chroma ───────────────────────
lim <- c(0, max(d$tC, d$aC))
p2 <- ggplot(d, aes(tC, aC)) +
  geom_abline(slope = 1, intercept = 0, colour = "grey40", linetype = "dashed") +
  geom_point(aes(fill = col), shape = 21, size = 1.7, stroke = .12,
             colour = "grey30", alpha = .9) +
  scale_fill_identity() +
  coord_equal(xlim = lim, ylim = lim) +
  labs(title = "Telítettség: elért vs. cél (C*)",
       subtitle = "átló alatt = a szürke felé húz (kontrakció) · felett = túltelít",
       x = "cél C*", y = "elért C*")

# ── (3) where do people give up? give-up ΔE by target chroma ────────────────
p3 <- ggplot(d, aes(tC, delta_e_stored)) +
  geom_point(aes(fill = col), shape = 21, size = 1.7, stroke = .12,
             colour = "grey30", alpha = .9) +
  scale_fill_identity() +
  geom_smooth(method = "lm", se = TRUE, colour = "grey20", linewidth = .6) +
  labs(title = "Feladási hiba a cél telítettsége szerint",
       subtitle = "nő-e a feladási ΔE a gamut széle (nagy C*) felé?",
       x = "cél C*", y = "feladási ΔE2000")

p <- (p1 | (p2 / p3)) + plot_annotation(
  title = "S.H.A.D.E. — feladott (skip) körök a Mixbox-gamutban",
  subtitle = sprintf("n = %d feladott kör · a feladási hibák ~%.0f%%-a mutat a szürke felé (nincs szisztematikus szűkülés)",
                     nrow(d), 100 * mean(d$radial < 0)),
  theme = theme(plot.title = element_text(face = "bold", size = 15)))

ggsave(file.path(dir, "skip_gamut_plots.png"), p, width = 15, height = 8.5, dpi = 140,
       bg = "white", device = ragg::agg_png)
ggsave(file.path(dir, "skip_gamut_plots.pdf"), p, width = 15, height = 8.5,
       bg = "white", device = cairo_pdf)

cat("wrote", file.path(dir, "skip_gamut_plots.png"), "\n")
cat(sprintf("n=%d  toward-gray=%.1f%%  median(tC-aC)=%.2f  field-cells=%d  mean|field|=%.2f\n",
            nrow(d), 100 * mean(d$radial < 0), median(d$tC - d$aC),
            nrow(field), mean(field$mag)))
