#!/usr/bin/env Rscript
# Per-hue-sector spread of the give-up (skip) errors.
#
# Splits the a*-b* plane into K hue sectors (hue = atan2(b*, a*) of the TARGET),
# and per sector measures:
#   spread       = sqrt(var(du) + var(dv))  — 2-D dispersion of the error cloud
#   mean_radial  = mean projection of the error on the target's radius
#                  (negative = pulled toward the neutral/grey axis = contraction)
# Left: a wind-rose (spoke length = spread, in true a*-b* orientation, coloured by
# the sector's mean colour). Right: diverging bars of the grey-ward bias per sector.
#
# Run:  LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 Rscript scripts/skip_hue_sectors.R

suppressPackageStartupMessages({
  library(ggplot2); library(dplyr); library(readr); library(patchwork)
})

dir <- "artifacts/skip_gamut"
K   <- 8L                      # hue sectors
W   <- 360 / K
d   <- read_csv(file.path(dir, "skips_enriched.csv"), show_col_types = FALSE) |>
  filter(mixing_model != "spectral" | is.na(mixing_model)) |>
  mutate(hue = (atan2(tb, ta) * 180 / pi) %% 360,
         sec = floor(hue / W),
         du  = aa - ta, dv = ab - tb,
         radial = (du * ta + dv * tb) / pmax(sqrt(ta^2 + tb^2), 1))

sec <- d |>
  group_by(sec) |>
  summarise(hue_mid = (first(sec) + 0.5) * W, n = n(),
            spread = sqrt(var(du) + var(dv)),
            mean_radial = mean(radial),
            R = mean(target_r), G = mean(target_g), B = mean(target_b),
            .groups = "drop") |>
  mutate(col = rgb(R, G, B, maxColorValue = 255),
         ang = hue_mid * pi / 180,
         low = n < 15,                      # flag under-sampled sectors
         lbl = sprintf("%d°\nn=%d", round(hue_mid), n))

theme_set(theme_minimal(base_size = 12) +
  theme(panel.grid.minor = element_blank(),
        plot.title = element_text(face = "bold"),
        plot.background = element_rect(fill = "white", colour = NA),
        panel.background = element_rect(fill = "white", colour = NA)))

# ── Left: wind-rose of the error spread by hue sector ───────────────────────
rings <- c(10, 20, 30)
circle <- do.call(rbind, lapply(rings, function(r)
  data.frame(r = r, t = seq(0, 2 * pi, length.out = 100),
             x = r * cos(seq(0, 2 * pi, length.out = 100)),
             y = r * sin(seq(0, 2 * pi, length.out = 100)))))
rmax <- max(sec$spread) * 1.15

p1 <- ggplot(sec) +
  geom_path(data = circle, aes(x, y, group = r), colour = "grey85", linewidth = .3) +
  annotate("text", x = rings, y = 0, label = rings, colour = "grey60",
           size = 3, hjust = -0.1, vjust = -0.3) +
  geom_hline(yintercept = 0, colour = "grey85") +
  geom_vline(xintercept = 0, colour = "grey85") +
  geom_spoke(aes(x = 0, y = 0, angle = ang, radius = spread, colour = col,
                 alpha = ifelse(low, .45, 1)), linewidth = 3,
             arrow = arrow(length = unit(.2, "cm"), type = "closed")) +
  geom_text(aes(x = (spread + rmax * .07) * cos(ang),
                y = (spread + rmax * .07) * sin(ang), label = lbl),
            size = 3, lineheight = .85) +
  scale_colour_identity() + scale_alpha_identity() +
  coord_equal(xlim = c(-rmax, rmax), ylim = c(-rmax, rmax)) +
  labs(title = "Feladási hiba szórása hue-szektoronként (szél-rózsa)",
       subtitle = "küllő hossza = a hibafelhő szórása (CIELAB egység) · irány = a szektor hue-ja · szín = a szektor átlagszíne\nhalvány küllő: n < 15 (bizonytalan)",
       x = "a*", y = "b*")

# ── Right: grey-ward bias per sector ────────────────────────────────────────
sec_ord <- sec |> arrange(hue_mid) |>
  mutate(lab = factor(sprintf("%d°", round(hue_mid)),
                      levels = sprintf("%d°", round(sort(hue_mid)))))
p2 <- ggplot(sec_ord, aes(lab, mean_radial, fill = col, alpha = ifelse(low, .45, 1))) +
  geom_col(colour = "grey30", width = .8) +
  geom_hline(yintercept = 0, colour = "grey40") +
  geom_text(aes(label = sprintf("n=%d", n),
                vjust = ifelse(mean_radial >= 0, -0.4, 1.2)), size = 2.9, alpha = 1) +
  scale_fill_identity() + scale_alpha_identity() +
  labs(title = "Szürke-felé torzítás szektoronként",
       subtitle = "negatív = a hiba a semleges tengely felé húz (kontrakció) · pozitív = kifelé",
       x = "hue szektor közepe", y = "átlagos radiális hiba")

p <- (p1 | p2) + plot_layout(widths = c(1.25, 1)) + plot_annotation(
  title = "S.H.A.D.E. — feladási hibák hue-szektoronként",
  subtitle = sprintf("n = %d feladott kör · %d szektor (%g°) · a szórás a jól benépesült meleg hue-kban kicsi, a ritka hideg hue-kban nagy",
                     nrow(d), K, W),
  theme = theme(plot.title = element_text(face = "bold", size = 15)))

ggsave(file.path(dir, "skip_hue_sectors.png"), p, width = 15, height = 7.5, dpi = 140,
       bg = "white", device = ragg::agg_png)
ggsave(file.path(dir, "skip_hue_sectors.pdf"), p, width = 15, height = 7.5,
       bg = "white", device = cairo_pdf)

cat("wrote", file.path(dir, "skip_hue_sectors.png"), "\n")
print(as.data.frame(sec_ord[, c("hue_mid", "n", "spread", "mean_radial")]) |>
      (\(x) { x[, -2] <- round(x[, -2], 1); x })())
