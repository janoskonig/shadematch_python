# global.R — ShadeMatch EDA Shiny app: adatbetöltés, jellemzők, palettafüggvények.
# A Shiny automatikusan betölti az app.R előtt (azonos mappában).

suppressPackageStartupMessages({
  library(shiny)
  library(tidyverse)
  library(lubridate)
  library(scales)
  library(plotly)
})

# --- adatkönyvtár -----------------------------------------------------------
# Elsődlegesen a SHADE_DATA env-változó; különben néhány szokásos hely.
DATA_DIR <- Sys.getenv("SHADE_DATA", unset = NA)
if (is.na(DATA_DIR) || !nzchar(DATA_DIR)) {
  cand <- c("data/shadematch_v2", "../data/shadematch_v2", "shadematch_v2", "data")
  hit  <- cand[file.exists(file.path(cand, "mixing_sessions.csv"))]
  DATA_DIR <- if (length(hit)) hit[1] else "../data/shadematch_v2"
}
if (!file.exists(file.path(DATA_DIR, "mixing_sessions.csv"))) {
  stop("Nem találom az adatokat itt: ", normalizePath(DATA_DIR, mustWork = FALSE),
       "\nÁllítsd be a SHADE_DATA env-változót a shadematch_v2 CSV-k mappájára.")
}

drop_cols <- c("drop_white", "drop_black", "drop_red", "drop_yellow", "drop_blue")
TODAY <- as.Date("2026-07-05")

# --- kimenet-kódolás és paletták (a riporttal azonos) -----------------------
outcome_levels <- c("tökéletes", "azonos", "elfogadható", "elfogadhatatlan", "értékelés nélkül")
pal_kimenet <- c(`tökéletes` = "#55a868", `azonos` = "#4c72b0", `elfogadható` = "#dd8452",
                 `elfogadhatatlan` = "#c44e52", `értékelés nélkül` = "#8172b3")
recode_outcome <- function(mc) factor(recode(mc,
  perfect = "tökéletes", no_perceivable_difference = "azonos",
  acceptable_difference = "elfogadható", big_difference = "elfogadhatatlan",
  stopped = "értékelés nélkül"), levels = outcome_levels)

cor_heatmap <- function(d) {
  cm <- cor(d, use = "pairwise.complete.obs", method = "spearman")
  as_tibble(cm, rownames = "x") |>
    pivot_longer(-x, names_to = "y", values_to = "rho") |>
    mutate(x = factor(x, levels = colnames(cm)),
           y = factor(y, levels = rev(colnames(cm)))) |>
    ggplot(aes(x, y, fill = rho)) +
    geom_tile(colour = "white") +
    geom_text(aes(label = sprintf("%.2f", rho)), size = 3) +
    scale_fill_gradient2(low = "#c44e52", mid = "white", high = "#4c72b0", limits = c(-1, 1)) +
    labs(x = NULL, y = NULL, fill = "Spearman ρ") +
    theme(axis.text.x = element_text(angle = 45, hjust = 1))
}

# --- betöltés ---------------------------------------------------------------
s2 <- read_csv(file.path(DATA_DIR, "mixing_sessions.csv"), show_col_types = FALSE)
a2 <- read_csv(file.path(DATA_DIR, "mixing_attempts.csv"), show_col_types = FALSE) |>
  select(attempt_uuid, end_reason, duration_sec, num_steps)
u2 <- read_csv(file.path(DATA_DIR, "users.csv"), show_col_types = FALSE) |>
  rename(user_id = id) |>
  mutate(age = as.numeric(TODAY - as.Date(birthdate)) / 365.25)
t2 <- read_csv(file.path(DATA_DIR, "target_colors.csv"), show_col_types = FALSE) |>
  transmute(target_color_id = id, color_type, classification,
            key_drops = sum_drop_count,
            key_pigments = rowSums(across(all_of(drop_cols), ~ .x > 0)),
            catalog_order, szin_nev = name)

v2 <- s2 |>
  mutate(timestamp = ymd_hms(timestamp), delta_e = as.numeric(delta_e)) |>
  left_join(a2, by = "attempt_uuid") |>
  left_join(select(u2, user_id, age, gender), by = "user_id") |>
  left_join(t2, by = "target_color_id") |>
  arrange(user_id, timestamp) |>
  group_by(user_id) |> mutate(attempt_no = row_number()) |> ungroup() |>
  mutate(is_perfect  = match_category == "perfect",
         n_pigments  = rowSums(across(all_of(drop_cols), ~ .x > 0)),
         female      = as.integer(gender == "female"))

# lépésszintű eseménynaplóból származtatott stratégia-jellemzők
ev <- read_csv(file.path(DATA_DIR, "mixing_attempt_events.csv"),
               col_select = c(attempt_uuid, event_type, action_type, action_color,
                              step_index, time_since_prev_step_ms, amount,
                              delta_e_before, delta_e_after),
               show_col_types = FALSE)
acts <- ev |>
  filter(event_type %in% c("action_add", "action_remove")) |>
  arrange(attempt_uuid, step_index)
step_feats <- acts |>
  group_by(attempt_uuid) |>
  summarise(
    n_act           = n(),
    share_remove    = mean(action_type == "remove"),
    switch_rate     = ifelse(n() > 1,
                             sum(action_color != lag(action_color), na.rm = TRUE) / (n() - 1),
                             NA_real_),
    median_step_ms  = median(time_since_prev_step_ms, na.rm = TRUE),
    overshoot_share = mean(delta_e_after > delta_e_before, na.rm = TRUE),
    best_de         = ifelse(all(is.na(delta_e_after)), NA_real_,
                             min(delta_e_after, na.rm = TRUE)),
    .groups = "drop")
v2 <- v2 |> left_join(step_feats, by = "attempt_uuid")

# a "stopped" (értékelés nélkül leállt) érvénytelen keverék -> kihagyjuk
v2_all <- v2
v2 <- v2_all |> filter(match_category != "stopped")

# CIELAB-jellemzők a célszínekhez
lab_of <- function(r, g, b) {
  m <- grDevices::convertColor(cbind(r, g, b) / 255, from = "sRGB", to = "Lab")
  tibble(Lstar = m[, 1], astar = m[, 2], bstar = m[, 3],
         Cab = sqrt(m[, 2]^2 + m[, 3]^2),
         hab = (atan2(m[, 3], m[, 2]) * 180 / pi) %% 360)
}
tc_rgb <- read_csv(file.path(DATA_DIR, "target_colors.csv"), show_col_types = FALSE) |>
  transmute(target_color_id = id, r, g, b, szin_nev = name)
tc_lab <- bind_cols(tc_rgb, lab_of(tc_rgb$r, tc_rgb$g, tc_rgb$b)) |>
  mutate(hex = rgb(r, g, b, maxColorValue = 255))

theme_set(theme_minimal(base_size = 13))

# UI-hoz hasznos konstansok
KEY_MAX  <- max(v2$key_drops, na.rm = TRUE)
DUR_MAX  <- as.integer(ceiling(max(v2$duration_sec, na.rm = TRUE)))
DE_MAX   <- as.integer(ceiling(max(v2$delta_e, na.rm = TRUE)))
OUTCOMES <- c("tökéletes", "azonos", "elfogadható", "elfogadhatatlan")
