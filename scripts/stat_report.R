#!/usr/bin/env Rscript
# ShadeMatch — statisztikai riport a /stat oldal "Következtetés" füléhez.
#
# Miért R és miért itt fut: a Render csak Pythont futtat, ezért az elemzés nem a
# szerveren él. Ugyanaz a minta, mint a scripts/skip_gamut_plots.R-nél: az adat
# CSV-ből jön, a szkript artifactokat ír, az oldal pedig csak megjeleníti őket.
#
# Blokkonként HÁROM fájl készül, szándékos redundanciával:
#   <blokk>.png          ggplot ábra — lehetőleg MINDEN adatponttal
#   <blokk>_output.txt   a modell NYERS kimenete (summary / confint / print)
#   <blokk>.json         a kulcsszámok, amiket az oldal értelmező szövege idéz
# Így ugyanaz a szám háromszor szerepel: az ábrán, a nyers outputban és a
# szövegben. Ha a modell el sem fut, az a nyers outputban azonnal látszik —
# az artifacts/mixed_models_clean_full/continuous_lmm.txt pont ezért árulkodó.
#
# Modellválasztás: blokkonként EGY, mindenki által ismert modell. Nem cél a
# becslők halmozása; cél, hogy egy bíráló ránézésre tudja, mit lát.
#
# Bemenet: a scripts/export_db_to_csv.py által kiírt CSV-k. A helyét a
#   SHADE_DATA env-változó adja meg — ugyanaz a konvenció, mint shiny_eda/global.R.
#
# Futtatás a repo gyökeréből:
#   SHADE_DATA=data/shadematch_v2 Rscript scripts/stat_report.R

suppressPackageStartupMessages({
  library(dplyr); library(readr); library(tidyr)
  library(ggplot2); library(lme4); library(lmerTest)
  library(survival); library(MASS); library(jsonlite)
})

select <- dplyr::select   # a MASS::select elfedné

# ── UTF-8 ────────────────────────────────────────────────────────────────────
# A magyar ékezetek csak UTF-8 locale-lal és cairo-alapú eszközzel jelennek meg
# az ábrákon; "C" locale alatt minden ékezetes betű pontra cserélődik. Ezt a
# szkript magától rendezi, hogy ne kelljen env-változókkal indítani.
for (loc in c("C.UTF-8", "en_US.UTF-8", "hu_HU.UTF-8")) {
  if (!is.na(suppressWarnings(Sys.setlocale("LC_CTYPE", loc)))) break
}
if (!grepl("UTF-8", Sys.getlocale("LC_CTYPE"), fixed = TRUE)) {
  warning("Nincs UTF-8 locale (", Sys.getlocale("LC_CTYPE"),
          "); az ábrákon az ékezetek hibásak lehetnek.", call. = FALSE)
}
if (!isTRUE(capabilities("cairo"))) {
  warning("Az R cairo támogatás nélkül fordult; az ékezetek hibásak lehetnek.",
          call. = FALSE)
}

# ── beállítások ──────────────────────────────────────────────────────────────
# A hosszú köröket kizárjuk: nem hosszú gondolkodás, hanem otthagyott böngészőfül.
DURATION_CAP_SEC <- 300
# Paravina et al. ΔE₀₀-küszöbei (világos / sötét minta).
PT_LIGHT <- 0.7; PT_DARK <- 1.2
AT_LIGHT <- 2.1; AT_DARK <- 3.1
# Ez alá már pontos receptnek számít (csak lebegőpontos zaj).
PERFECT_DE <- 0.01
# Az első átlépés küszöbe (a /stat régóta ezt a mérföldkövet mutatja).
CROSS_DE <- 2.0

OUT_DIR <- "artifacts/stat_r"
dir.create(OUT_DIR, recursive = TRUE, showWarnings = FALSE)

theme_set(theme_minimal(base_size = 12))
COL_ACC <- "#2a78d6"   # pontosság — a /stat oldallal azonos két sorozatszín
COL_DUR <- "#eb6834"   # köridő
COL_REF <- "#94a3b8"   # referenciavonalak

# ── adat ─────────────────────────────────────────────────────────────────────
data_dir <- Sys.getenv("SHADE_DATA", unset = NA)
if (is.na(data_dir) || !nzchar(data_dir)) {
  cand <- c("data/shadematch_v2", "../data/shadematch_v2", "shadematch_v2", "data")
  hit <- cand[file.exists(file.path(cand, "mixing_attempts.csv"))]
  data_dir <- if (length(hit)) hit[1] else "data/shadematch_v2"
}
if (!file.exists(file.path(data_dir, "mixing_attempts.csv"))) {
  stop("Nem találom az adatokat itt: ", normalizePath(data_dir, mustWork = FALSE),
       "\nÁllítsd be a SHADE_DATA env-változót a CSV-k mappájára ",
       "(scripts/export_db_to_csv.py írja ki őket).")
}
message("Adatkönyvtár: ", data_dir)

targets <- read_csv(file.path(data_dir, "target_colors.csv"), show_col_types = FALSE) |>
  transmute(target_color_id = id,
            target_name = name,
            color_type = tolower(coalesce(color_type, "unknown")))

# A mixing_attempts a kör szintű tábla: ez adja a végső ΔE-t, az időt és a
# lépésszámot. (A mixing_sessions a párhuzamos, régebbi tábla; onnan csak a
# kilépéskori észlelési ítéletet vesszük át a 4. blokkhoz.)
att <- read_csv(file.path(data_dir, "mixing_attempts.csv"), show_col_types = FALSE) |>
  select(attempt_uuid, user_id, target_color_id, final_delta_e, duration_sec,
         num_steps, attempt_started_server_ts) |>
  filter(!is.na(user_id), !is.na(final_delta_e), is.finite(final_delta_e)) |>
  left_join(targets, by = "target_color_id") |>
  arrange(user_id, attempt_started_server_ts, attempt_uuid) |>
  group_by(user_id) |>
  mutate(trial_index = row_number()) |>              # a játékos hányadik köre összesen
  ungroup() |>
  group_by(user_id, target_color_id) |>
  mutate(attempt_no = row_number()) |>               # hányadszor játssza EZT a színt
  ungroup() |>
  mutate(user_id = factor(user_id),
         target_color_id = factor(target_color_id),
         log2_trial = log2(trial_index))

timed <- att |> filter(!is.na(duration_sec), duration_sec > 0,
                       duration_sec <= DURATION_CAP_SEC)

message(sprintf("Körök: %d (időmérésbe bevonva: %d) · játékos: %d · célszín: %d",
                nrow(att), nrow(timed), n_distinct(att$user_id),
                n_distinct(att$target_color_id)))

# ── segédek ──────────────────────────────────────────────────────────────────
# Egy blokk = egy ábra + egy nyers output + egy JSON. Mindig ebben a sorrendben.
write_block <- function(name, plot, output_lines, values,
                        width = 9, height = 5.2, dpi = 150) {
  ggsave(file.path(OUT_DIR, paste0(name, ".png")), plot,
         width = width, height = height, dpi = dpi, bg = "white",
         device = grDevices::png, type = "cairo")
  writeLines(output_lines, file.path(OUT_DIR, paste0(name, "_output.txt")))
  write_json(values, file.path(OUT_DIR, paste0(name, ".json")),
             auto_unbox = TRUE, digits = 6, pretty = TRUE, na = "null")
  message("  → ", name, ".png / _output.txt / .json")
}

# A nyers kimenet fejléce: melyik modell, milyen adaton, mikor.
header <- function(title, formula_txt, n, extra = character()) {
  c(strrep("=", 78), title, strrep("=", 78),
    paste("Modell:  ", formula_txt),
    paste("Adat:    ", sprintf("n = %d kör", n)),
    paste("Készült: ", format(Sys.time(), "%Y-%m-%d %H:%M:%S %Z")),
    extra, "")
}

# lmer fixhatás egy sorban: becslés, CI, p — a log-skálán szorzótényezővé alakítva.
fixed_effect <- function(model, term) {
  co <- summary(model)$coefficients
  ci <- suppressMessages(confint(model, parm = term, method = "Wald"))
  list(term = term,
       estimate = unname(co[term, "Estimate"]),
       se = unname(co[term, "Std. Error"]),
       df = unname(co[term, "df"]),
       t = unname(co[term, "t value"]),
       p = unname(co[term, "Pr(>|t|)"]),
       ci_low = unname(ci[1, 1]), ci_high = unname(ci[1, 2]),
       ratio = exp(unname(co[term, "Estimate"])),
       ratio_ci_low = exp(unname(ci[1, 1])),
       ratio_ci_high = exp(unname(ci[1, 2])))
}

# ─────────────────────────────────────────────────────────────────────────────
# 1) Sebesség–pontosság szétválás
#
#    Két kevert modell, azonos szerkezettel; a különbség csak a kimenetben van.
#    Véletlen tengelymetszet játékosra ÉS célszínre: a köröket ugyanaz a néhány
#    ember és ugyanaz a néhány szín adja, ezt a modell kezeli — nem kell hozzá
#    külön klaszter-bootstrap. A rutin log2-n van, tehát a becslés a rutin
#    MEGDUPLÁZÓDÁSÁRA vonatkozik.
# ─────────────────────────────────────────────────────────────────────────────
message("1) sebesség–pontosság")

m_acc <- lmer(log1p(final_delta_e) ~ log2_trial + (1 | user_id) + (1 | target_color_id),
              data = att, REML = TRUE)
m_dur <- lmer(log(duration_sec) ~ log2_trial + (1 | user_id) + (1 | target_color_id),
              data = timed, REML = TRUE)

fe_acc <- fixed_effect(m_acc, "log2_trial")
fe_dur <- fixed_effect(m_dur, "log2_trial")

sa_out <- c(
  header("1) SEBESSÉG–PONTOSSÁG SZÉTVÁLÁS — pontosság",
         "lmer(log1p(final_delta_e) ~ log2(trial_index) + (1|user_id) + (1|target_color_id))",
         nrow(att),
         extra = c("", "A log1p-skálán a meredekség exponenciálva (1+ΔE) szorzótényező",
                   "a rutin megduplázódására.")),
  capture.output(summary(m_acc)),
  "", "-- 95% Wald-konfidenciaintervallum a fixhatásra --",
  capture.output(print(suppressMessages(confint(m_acc, parm = "log2_trial", method = "Wald")))),
  "",
  header("1) SEBESSÉG–PONTOSSÁG SZÉTVÁLÁS — köridő",
         "lmer(log(duration_sec) ~ log2(trial_index) + (1|user_id) + (1|target_color_id))",
         nrow(timed),
         extra = c("", sprintf("Kizárva: %d s fölötti körök.", DURATION_CAP_SEC))),
  capture.output(summary(m_dur)),
  "", "-- 95% Wald-konfidenciaintervallum a fixhatásra --",
  capture.output(print(suppressMessages(confint(m_dur, parm = "log2_trial", method = "Wald"))))
)

# Ábra: MINDEN kör pontként, a modell populációs egyenesével. Két panel, közös
# x-tengely; a két kimenet skálája más, ezért nem egy panelbe zsúfoljuk őket.
sa_points <- bind_rows(
  att |> transmute(trial_index, ertek = final_delta_e, panel = "Végső ΔE₀₀"),
  timed |> transmute(trial_index, ertek = duration_sec, panel = "Köridő (s)")
) |>
  mutate(panel = factor(panel, levels = c("Végső ΔE₀₀", "Köridő (s)")))

sa_fit <- bind_rows(
  tibble(trial_index = sort(unique(att$trial_index))) |>
    mutate(ertek = expm1(fixef(m_acc)[["(Intercept)"]] +
                           fixef(m_acc)[["log2_trial"]] * log2(trial_index)),
           panel = "Végső ΔE₀₀"),
  tibble(trial_index = sort(unique(timed$trial_index))) |>
    mutate(ertek = exp(fixef(m_dur)[["(Intercept)"]] +
                         fixef(m_dur)[["log2_trial"]] * log2(trial_index)),
           panel = "Köridő (s)")
) |>
  mutate(panel = factor(panel, levels = c("Végső ΔE₀₀", "Köridő (s)")))

p_sa <- ggplot(sa_points, aes(trial_index, ertek)) +
  geom_point(aes(colour = panel), alpha = 0.16, size = 0.7,
             position = position_jitter(width = 0.06, height = 0)) +
  geom_line(data = sa_fit, aes(colour = panel), linewidth = 1.1) +
  facet_wrap(~ panel, scales = "free_y") +
  scale_x_continuous(trans = "log2", breaks = c(1, 2, 4, 8, 16, 32, 64, 128, 256, 512)) +
  scale_y_continuous(trans = "log1p", breaks = c(0, 1, 2, 5, 10, 20, 50, 100, 200)) +
  scale_colour_manual(values = c("Végső ΔE₀₀" = COL_ACC, "Köridő (s)" = COL_DUR),
                      guide = "none") +
  labs(x = "Hányadik köre a játékosnak (log2-skála)", y = NULL,
       title = "Minden kör, a kevert modell populációs illesztésével",
       subtitle = sprintf(
         "ΔE₀₀: %+.1f%% / rutinduplázódás (95%% CI %+.1f%% … %+.1f%%, p = %.3f)   |   köridő: %+.1f%% (95%% CI %+.1f%% … %+.1f%%, p = %.3g)",
         100 * (fe_acc$ratio - 1), 100 * (fe_acc$ratio_ci_low - 1),
         100 * (fe_acc$ratio_ci_high - 1), fe_acc$p,
         100 * (fe_dur$ratio - 1), 100 * (fe_dur$ratio_ci_low - 1),
         100 * (fe_dur$ratio_ci_high - 1), fe_dur$p))

write_block("sebesseg_pontossag", p_sa, sa_out,
            list(n_attempts = nrow(att), n_timed = nrow(timed),
                 n_players = n_distinct(att$user_id),
                 n_targets = n_distinct(att$target_color_id),
                 duration_cap_sec = DURATION_CAP_SEC,
                 accuracy = fe_acc, duration = fe_dur),
            width = 10, height = 4.8)

# ─────────────────────────────────────────────────────────────────────────────
# 2) Küszöb-elérés
#
#    Az empirikus eloszlásfüggvény (ECDF) minden kört megjelenít, és egyetlen
#    görbén leolvasható róla az ÖSSZES küszöbhöz tartozó arány — nem kell hozzá
#    öt külön oszlop. A számszerű arányokat binomiális teszt adja (exact CI).
# ─────────────────────────────────────────────────────────────────────────────
message("2) küszöb-elérés")

thresholds <- tibble(
  kulcs = c("perfect", "pt_light", "pt_dark", "at_light", "at_dark"),
  cimke = c(sprintf("Pontos találat (≤ %.2f)", PERFECT_DE),
            sprintf("Észlelhetőség, világos (≤ %.1f)", PT_LIGHT),
            sprintf("Észlelhetőség, sötét (≤ %.1f)", PT_DARK),
            sprintf("Elfogadhatóság, világos (≤ %.1f)", AT_LIGHT),
            sprintf("Elfogadhatóság, sötét (≤ %.1f)", AT_DARK)),
  # Az ábrán rövid címke kell: a teljes név a táblázatban és a JSON-ban van.
  rovid = c("pontos", sprintf("PT %.1f", PT_LIGHT), sprintf("PT %.1f", PT_DARK),
            sprintf("AT %.1f", AT_LIGHT), sprintf("AT %.1f", AT_DARK)),
  ertek = c(PERFECT_DE, PT_LIGHT, PT_DARK, AT_LIGHT, AT_DARK))

thr_rows <- thresholds |>
  rowwise() |>
  mutate(k = sum(att$final_delta_e <= ertek), n = nrow(att),
         bt = list(binom.test(k, n)),
         arany = bt$estimate, ci_low = bt$conf.int[1], ci_high = bt$conf.int[2]) |>
  ungroup() |>
  select(-bt)

thr_out <- c(
  header("2) KÜSZÖB-ELÉRÉS", "binom.test(k, n) küszöbönként", nrow(att),
         extra = c("", "Paravina et al. ΔE₀₀-küszöbei. Az arányok körre vannak számolva;",
                   "a körök nem függetlenek, ezért a CI a valóságnál szűkebb lehet —",
                   "a játékos szerinti bontás a JSON-ban és az 1) blokk modelljében van.")),
  capture.output(print(as.data.frame(thr_rows |> select(cimke, k, n, arany, ci_low, ci_high)),
                       row.names = FALSE, digits = 4)),
  "", "-- részletes binomiális teszt küszöbönként --",
  unlist(lapply(seq_len(nrow(thresholds)), function(i) {
    c(paste0("### ", thresholds$cimke[i]),
      capture.output(print(binom.test(sum(att$final_delta_e <= thresholds$ertek[i]), nrow(att)))),
      "")
  })))

p_thr <- ggplot(att, aes(final_delta_e)) +
  stat_ecdf(geom = "step", linewidth = 1, colour = COL_ACC, pad = FALSE) +
  geom_rug(alpha = 0.06, colour = COL_ACC, length = unit(0.02, "npc")) +
  geom_vline(data = thr_rows, aes(xintercept = ertek), colour = COL_REF,
             linetype = "dashed", linewidth = 0.4) +
  geom_text(data = thr_rows, aes(x = ertek, y = 1.0, label = rovid),
            hjust = -0.12, vjust = 0.5, size = 3, colour = "grey30") +
  scale_x_continuous(trans = "log1p", breaks = c(0, 0.5, 1, 2, 3, 5, 10, 20, 50)) +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1),
                     breaks = seq(0, 1, 0.25)) +
  labs(x = "Végső ΔE₀₀ (log1p-skála)", y = "Körök kumulált aránya",
       title = "Minden kör empirikus eloszlásfüggvénye a küszöbökkel",
       subtitle = sprintf("n = %d kör; a görbe magassága a küszöbnél = a küszöböt teljesítő körök aránya",
                          nrow(att)))

write_block("kuszob_eleres", p_thr, thr_out,
            list(n = nrow(att), thresholds = thr_rows), height = 5)

# ─────────────────────────────────────────────────────────────────────────────
# 3) Első átlépés: hány próbálkozás a ΔE < 2-höz?
#
#    Túlélési adat: aki adott számú próbálkozásig nem lépte át, az CENZORÁLT
#    megfigyelés, nem hiányzó. A komplett esetek átlaga pont a legnehezebb
#    párokat dobná ki, ezért lenne optimista.
# ─────────────────────────────────────────────────────────────────────────────
message("3) első átlépés")

cross <- att |>
  group_by(user_id, target_color_id) |>
  summarise(
    esemeny = as.integer(any(final_delta_e < CROSS_DE)),
    ido = if (any(final_delta_e < CROSS_DE)) min(attempt_no[final_delta_e < CROSS_DE])
          else max(attempt_no),
    .groups = "drop")

km <- survfit(Surv(ido, esemeny) ~ 1, data = cross, conf.type = "log-log")

km_out <- c(
  header(sprintf("3) ELSŐ ÁTLÉPÉS (ΔE < %.1f)", CROSS_DE),
         "survfit(Surv(attempt_no, event) ~ 1)  [Kaplan–Meier]",
         nrow(cross),
         extra = c("", "Egy megfigyelés = egy (játékos, célszín) pár.",
                   sprintf("Esemény: %d pár átlépte · cenzorált: %d pár még nem.",
                           sum(cross$esemeny), sum(cross$esemeny == 0)))),
  capture.output(print(km)),
  "", "-- a görbe próbálkozásonként --",
  capture.output(print(summary(km))),
  "", "-- összehasonlításul: a komplett esetek (torzított) átlaga --",
  capture.output(print(summary(cross$ido[cross$esemeny == 1]))))

km_df <- tibble(ido = km$time, surv = km$surv, lower = km$lower, upper = km$upper,
                n_event = km$n.event, n_censor = km$n.censor) |>
  mutate(elerte = 1 - surv, elerte_lo = 1 - upper, elerte_hi = 1 - lower)

# A konfidenciasáv is lépcsős: a geom_ribbon egyenesen kötné össze a lépcsőfokokat,
# ami a KM-görbénél nem létező köztes értékeket sugallna. Ezért minden szakaszhoz
# két pontot adunk (a szakasz eleje és vége azonos magassággal).
km_band <- tibble(
  x = as.vector(rbind(km_df$ido, c(km_df$ido[-1], max(km_df$ido)))),
  lo = rep(km_df$elerte_lo, each = 2),
  hi = rep(km_df$elerte_hi, each = 2))

p_km <- ggplot(km_df, aes(ido, elerte)) +
  geom_ribbon(data = km_band, aes(x = x, ymin = lo, ymax = hi),
              inherit.aes = FALSE, fill = COL_ACC, alpha = 0.15) +
  geom_step(colour = COL_ACC, linewidth = 1) +
  geom_point(colour = COL_ACC, size = 1.8) +
  scale_y_continuous(labels = scales::percent_format(accuracy = 1), limits = c(0, 1)) +
  scale_x_continuous(breaks = scales::pretty_breaks()) +
  labs(x = sprintf("Hányadszor játssza ugyanazt a színt (%d pár)", nrow(cross)),
       y = sprintf("ΔE < %.1f elérve (kumulált)", CROSS_DE),
       title = "Kaplan–Meier: az első átlépés a cenzorált párokat is beszámítja",
       subtitle = sprintf("%d pár átlépte, %d még nem; a szalag 95%% CI (log-log)",
                          sum(cross$esemeny), sum(cross$esemeny == 0)))

write_block("elso_atlepes", p_km, km_out,
            list(threshold = CROSS_DE, n_pairs = nrow(cross),
                 n_events = sum(cross$esemeny), n_censored = sum(cross$esemeny == 0),
                 median_attempts = unname(summary(km)$table[["median"]]),
                 naive_complete_case_mean = mean(cross$ido[cross$esemeny == 1]),
                 curve = km_df), height = 5)

# ─────────────────────────────────────────────────────────────────────────────
# 4) Észlelési ítéletek — a játékon belüli 50%-os küszöb
#
#    A kilépéskor a játékos minősíti az eredményt (azonos / elfogadható / nem
#    elfogadható). Logisztikus regresszió a ΔE-re, a küszöb a MASS::dose.p
#    50%-os keresztezése. FONTOS korlát: a játékban a játékos maga választja meg,
#    milyen ΔE-nél áll meg, tehát ezek nem előírt ingerek — a küszöb környéki
#    tartomány alulmintázott. Az előírt ΔE-vel mért becslés a /calibration.
# ─────────────────────────────────────────────────────────────────────────────
sessions_path <- file.path(data_dir, "mixing_sessions.csv")
if (file.exists(sessions_path)) {
  message("4) észlelési ítéletek")
  skips <- read_csv(sessions_path, show_col_types = FALSE) |>
    filter(!is.na(skip_perception), !is.na(delta_e), is.finite(delta_e),
           skip_perception %in% c("identical", "acceptable", "unacceptable")) |>
    mutate(delta_e = as.numeric(delta_e),
           latott_kulonbseget = as.integer(skip_perception != "identical"),
           nem_elfogadhato = as.integer(skip_perception == "unacceptable"),
           itelet = factor(skip_perception,
                           levels = c("identical", "acceptable", "unacceptable"),
                           labels = c("Nincs különbség", "Elfogadható", "Nem elfogadható")))

  if (nrow(skips) >= 20 &&
      length(unique(skips$latott_kulonbseget)) == 2 &&
      length(unique(skips$nem_elfogadhato)) == 2) {

    g_pt <- glm(latott_kulonbseget ~ delta_e, family = binomial, data = skips)
    g_at <- glm(nem_elfogadhato ~ delta_e, family = binomial, data = skips)
    d_pt <- dose.p(g_pt, p = 0.5)
    d_at <- dose.p(g_at, p = 0.5)
    pt <- as.numeric(d_pt); pt_se <- as.numeric(attr(d_pt, "SE"))
    at <- as.numeric(d_at); at_se <- as.numeric(attr(d_at, "SE"))

    perc_out <- c(
      header("4) ÉSZLELÉSI KÜSZÖB — észlelhetőség (PT)",
             "glm(saw_difference ~ delta_e, family = binomial)", nrow(skips),
             extra = c("", "A játékos maga választja a kilépési ΔE-t: önválasztott inger,",
                       "range-restrikció. Előírt ΔE-vel: /calibration.")),
      capture.output(summary(g_pt)),
      "", "-- 50%-os keresztezés (MASS::dose.p) --",
      capture.output(print(d_pt)),
      "",
      header("4) ÉSZLELÉSI KÜSZÖB — elfogadhatóság (AT)",
             "glm(unacceptable ~ delta_e, family = binomial)", nrow(skips)),
      capture.output(summary(g_at)),
      "", "-- 50%-os keresztezés (MASS::dose.p) --",
      capture.output(print(d_at)),
      "", "-- ítéletenkénti ΔE-eloszlás --",
      capture.output(print(as.data.frame(
        skips |> group_by(itelet) |>
          summarise(n = n(), median = median(delta_e), q1 = quantile(delta_e, .25),
                    q3 = quantile(delta_e, .75), .groups = "drop")),
        row.names = FALSE, digits = 4)))

    grid <- tibble(delta_e = seq(0, quantile(skips$delta_e, 0.99), length.out = 200))
    grid$pt <- predict(g_pt, newdata = grid, type = "response")
    grid$at <- predict(g_at, newdata = grid, type = "response")
    curves <- grid |>
      pivot_longer(c(pt, at), names_to = "gorbe", values_to = "p") |>
      mutate(gorbe = factor(gorbe, levels = c("pt", "at"),
                            labels = c("Látott különbséget", "Nem elfogadható")))

    # Minden ítélet pontként a 0/1 sávban (jitterrel), fölötte a két illesztés.
    pts <- bind_rows(
      skips |> transmute(delta_e, y = latott_kulonbseget, gorbe = "Látott különbséget"),
      skips |> transmute(delta_e, y = nem_elfogadhato, gorbe = "Nem elfogadható")) |>
      mutate(gorbe = factor(gorbe, levels = c("Látott különbséget", "Nem elfogadható")))

    p_perc <- ggplot(curves, aes(delta_e, p, colour = gorbe)) +
      geom_point(data = pts, aes(y = y), alpha = 0.12, size = 0.8,
                 position = position_jitter(height = 0.03, width = 0)) +
      geom_line(linewidth = 1.1) +
      geom_hline(yintercept = 0.5, colour = COL_REF, linewidth = 0.4) +
      geom_vline(xintercept = c(pt, at), colour = c(COL_ACC, COL_DUR),
                 linetype = "dotted", linewidth = 0.6) +
      scale_colour_manual(values = c("Látott különbséget" = COL_ACC,
                                     "Nem elfogadható" = COL_DUR), name = NULL) +
      scale_y_continuous(labels = scales::percent_format(accuracy = 1)) +
      labs(x = "ΔE₀₀ a kilépéskor", y = "Válaszarány",
           title = "Minden ítélet pontként, a logisztikus illesztéssel",
           subtitle = sprintf("PT = %.2f (SE %.2f) · AT = %.2f (SE %.2f) · n = %d ítélet · önválasztott ΔE!",
                              pt, pt_se, at, at_se, nrow(skips))) +
      theme(legend.position = "top")

    write_block("eszlelesi_kuszob", p_perc, perc_out,
                list(n = nrow(skips),
                     perceptibility = list(threshold = pt, se = pt_se,
                                           ci_low = pt - 1.96 * pt_se,
                                           ci_high = pt + 1.96 * pt_se),
                     acceptability = list(threshold = at, se = at_se,
                                          ci_low = at - 1.96 * at_se,
                                          ci_high = at + 1.96 * at_se),
                     reference = list(pt_light = PT_LIGHT, pt_dark = PT_DARK,
                                      at_light = AT_LIGHT, at_dark = AT_DARK)),
                height = 5)
  } else {
    message("   kihagyva: kevés vagy egynemű észlelési ítélet")
  }
} else {
  message("4) észlelési ítéletek — kihagyva (nincs mixing_sessions.csv)")
}

# ── futásjegyzék ─────────────────────────────────────────────────────────────
# Az oldal ebből tudja, mikori adatból dolgozik, és melyik blokk készült el.
write_json(list(
  generated_at = format(Sys.time(), "%Y-%m-%dT%H:%M:%S%z"),
  r_version = as.character(getRversion()),
  data_dir = normalizePath(data_dir, mustWork = FALSE),
  n_attempts = nrow(att), n_timed = nrow(timed),
  n_players = n_distinct(att$user_id), n_targets = n_distinct(att$target_color_id),
  blocks = sub("\\.json$", "", basename(Sys.glob(file.path(OUT_DIR, "*.json"))))
), file.path(OUT_DIR, "meta.json"), auto_unbox = TRUE, pretty = TRUE)

message("Kész: ", normalizePath(OUT_DIR))
