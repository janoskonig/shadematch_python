# app.R — ShadeMatch EDA interaktív felfedező (Shiny)
# A global.R (azonos mappa) tölti be az adatot és a segédfüggvényeket.
# Futtatás lokálisan:  shiny::runApp("shiny_eda")
# Az adat helye a SHADE_DATA env-változóval állítható (l. global.R).

# A global.R betöltése (a Shiny egyfájlos app.R-nél nem mindig teszi meg automatikusan).
if (!exists("DUR_MAX")) source("global.R")

# --- UI ---------------------------------------------------------------------
ui <- fluidPage(
  titlePanel("ShadeMatch — interaktív EDA"),
  tags$p(style = "color:#666;margin-top:-0.6em;",
         "A bal oldali szűrők minden ábrára hatnak. Ugyanaz az adat és jellemzők, mint a ",
         "statikus riportban — itt tekergethetők a küszöbök."),
  sidebarLayout(
    sidebarPanel(
      width = 3,
      sliderInput("durcap", "Időtartam felső határ (mp)",
                  min = 60, max = DUR_MAX, value = min(3600, DUR_MAX), step = 60),
      helpText("A fölötte lévő (jellemzően nyitva hagyott fül) próbálkozásokat kihagyja."),
      sliderInput("decap", "ΔE felső határ (megjelenítés)",
                  min = 1, max = DE_MAX, value = DE_MAX, step = 1),
      checkboxGroupInput("outcomes", "Kimenet (feladás fokozatai)",
                         choices = OUTCOMES, selected = OUTCOMES),
      sliderInput("key", "Megoldókulcs cseppszáma (nehézség)",
                  min = 0, max = KEY_MAX, value = c(0, KEY_MAX), step = 1),
      hr(),
      sliderInput("minnt", "Min. nem-triviális próbálkozás / felhasználó (PCA-fül)",
                  min = 1, max = 20, value = 5, step = 1),
      hr(),
      helpText(sprintf("Betöltve: %d értékelt próbálkozás, %d felhasználó, %d célszín.",
                       nrow(v2), dplyr::n_distinct(v2$user_id),
                       dplyr::n_distinct(v2$target_color_id)))
    ),
    mainPanel(
      width = 9,
      tabsetPanel(
        tabPanel("Ráfordítás & kimenet",
                 br(),
                 fluidRow(column(6, plotOutput("p_steps_de", height = 320)),
                          column(6, plotOutput("p_dur_de", height = 320))),
                 br(),
                 plotOutput("p_steps_dur", height = 380)),
        tabPanel("Stratégia",
                 br(),
                 plotOutput("p_violin", height = 340),
                 br(),
                 plotOutput("p_best_final", height = 340)),
        tabPanel("Trajektória",
                 br(),
                 plotOutput("p_traj", height = 420)),
        tabPanel("Célszín & nehézség",
                 br(),
                 plotOutput("p_lab2d", height = 320),
                 br(),
                 plotlyOutput("p_lab3d", height = 480)),
        tabPanel("Korreláció",
                 br(),
                 plotOutput("p_cor", height = 560)),
        tabPanel("Felhasználó (PCA)",
                 br(),
                 plotOutput("p_pca", height = 480),
                 helpText("Egy pont = egy felhasználó (a fenti 'min. nem-triviális' küszöb felett)."))
      )
    )
  )
)

# --- SERVER -----------------------------------------------------------------
server <- function(input, output, session) {

  # próbálkozás-szintű, szűrt adat
  fdat <- reactive({
    v2 |>
      filter(!is.na(key_drops),
             key_drops >= input$key[1], key_drops <= input$key[2]) |>
      mutate(kimenet = recode_outcome(match_category)) |>
      filter(as.character(kimenet) %in% input$outcomes) |>
      mutate(kimenet = fct_drop(kimenet))
  })

  need_rows <- function(d) validate(need(nrow(d) > 0, "Nincs adat a jelenlegi szűrőkhöz."))

  # --- Ráfordítás & kimenet ---
  output$p_steps_de <- renderPlot({
    d <- fdat() |> filter(num_steps > 0); need_rows(d)
    ggplot(d, aes(num_steps, delta_e)) +
      geom_jitter(height = 0.25, alpha = 0.2, size = 1, colour = "#4c72b0") +
      scale_x_log10() + coord_cartesian(ylim = c(0, input$decap)) +
      labs(x = "lépésszám (log10)", y = "ΔE", title = "Lépésszám vs. ΔE")
  })

  output$p_dur_de <- renderPlot({
    d <- fdat() |> filter(duration_sec > 0, duration_sec <= input$durcap); need_rows(d)
    ggplot(d, aes(duration_sec, delta_e)) +
      geom_jitter(height = 0.25, alpha = 0.2, size = 1, colour = "#4c72b0") +
      scale_x_log10(labels = label_number()) + coord_cartesian(ylim = c(0, input$decap)) +
      labs(x = "időtartam (mp, log10)", y = "ΔE", title = "Időtartam vs. ΔE")
  })

  output$p_steps_dur <- renderPlot({
    d <- fdat() |> filter(num_steps > 0, duration_sec > 0, duration_sec <= input$durcap)
    need_rows(d)
    ggplot(d, aes(num_steps, duration_sec, colour = kimenet)) +
      geom_point(alpha = 0.4, size = 1.2) +
      scale_x_log10() + scale_y_log10(labels = label_number()) +
      scale_colour_manual(values = pal_kimenet, drop = TRUE) +
      guides(colour = guide_legend(override.aes = list(alpha = 1, size = 3))) +
      labs(x = "lépésszám (log10)", y = "időtartam (mp, log10)", colour = NULL,
           title = "Lépésszám vs. időtartam (kimenet szerint)")
  })

  # --- Stratégia ---
  output$p_violin <- renderPlot({
    d <- fdat() |> filter(n_act >= 3); need_rows(d)
    d |> select(kimenet, `elvétel-arány` = share_remove,
                `pigmentváltás-ráta` = switch_rate, `túllövés-arány` = overshoot_share) |>
      pivot_longer(-kimenet, names_to = "jellemzo", values_to = "ertek") |>
      filter(!is.na(ertek)) |>
      ggplot(aes(kimenet, ertek, fill = kimenet)) +
      geom_violin(scale = "width", alpha = 0.55, colour = NA) +
      geom_boxplot(width = 0.12, outlier.shape = NA, alpha = 0.8) +
      facet_wrap(~ jellemzo) +
      scale_fill_manual(values = pal_kimenet, drop = TRUE) +
      labs(x = NULL, y = NULL, title = "Stratégia-jellemzők a kimenet szerint") +
      theme(legend.position = "none", axis.text.x = element_text(angle = 20, hjust = 1))
  })

  output$p_best_final <- renderPlot({
    d <- fdat() |> filter(!is_perfect, !is.na(best_de)); need_rows(d)
    ggplot(d, aes(best_de, delta_e)) +
      geom_abline(slope = 1, intercept = 0, linetype = "dashed", colour = "grey50") +
      geom_point(alpha = 0.3, size = 1.4, colour = "#c44e52") +
      coord_cartesian(ylim = c(0, input$decap)) +
      labs(x = "legjobb közbeni ΔE", y = "feladáskori ΔE",
           title = "Feladók: legjobb vs. feladáskori ΔE")
  })

  # --- Trajektória ---
  output$p_traj <- renderPlot({
    keep <- fdat() |> select(attempt_uuid, match_category)
    validate(need(nrow(keep) > 0, "Nincs adat a jelenlegi szűrőkhöz."))
    d <- acts |> filter(!is.na(delta_e_after)) |>
      semi_join(keep, by = "attempt_uuid") |>
      group_by(attempt_uuid) |> filter(n() >= 5) |>
      mutate(prog = row_number() / n()) |> ungroup() |>
      left_join(keep, by = "attempt_uuid") |>
      mutate(kimenet = fct_drop(recode_outcome(match_category)),
             bin = cut(prog, breaks = seq(0, 1, 0.05), include.lowest = TRUE,
                       labels = seq(0.025, 0.975, 0.05))) |>
      group_by(kimenet, bin) |>
      summarise(md = median(delta_e_after), .groups = "drop") |>
      mutate(prog = as.numeric(as.character(bin)))
    need_rows(d)
    ggplot(d, aes(prog, md, colour = kimenet)) +
      geom_line(linewidth = 1) +
      scale_colour_manual(values = pal_kimenet, drop = TRUE) +
      labs(x = "előrehaladás a próbálkozáson belül", y = "ΔE (medián)", colour = NULL,
           title = "Medián ΔE-trajektória (min. 5 lépéses próbálkozások)")
  })

  # --- Célszín & nehézség ---
  nehez <- reactive({
    fdat() |> group_by(target_color_id) |>
      summarise(p = mean(is_perfect), n = n(), .groups = "drop") |>
      inner_join(tc_lab, by = "target_color_id")
  })

  output$p_lab2d <- renderPlot({
    d <- nehez(); need_rows(d)
    d |> select(p, `L* (világosság)` = Lstar, `C*ab (telítettség)` = Cab, `h° (színezet)` = hab) |>
      pivot_longer(-p, names_to = "tulajdonsag", values_to = "ertek") |>
      ggplot(aes(ertek, p)) +
      geom_point(alpha = 0.6, size = 2, colour = "#d08c60") +
      facet_wrap(~ tulajdonsag, scales = "free_x") +
      scale_y_continuous(labels = percent) +
      labs(x = NULL, y = "P(tökéletes) célszínenként",
           title = "Célszín-tulajdonságok vs. nehézség")
  })

  output$p_lab3d <- renderPlotly({
    d <- nehez()
    validate(need(nrow(d) > 0, "Nincs adat a jelenlegi szűrőkhöz."))
    d <- d |> mutate(psize = scales::rescale(1 - p, to = c(5, 18)))
    plot_ly(d, x = ~astar, y = ~bstar, z = ~Lstar, type = "scatter3d", mode = "markers",
            marker = list(color = ~hex, size = ~psize, opacity = 0.95,
                          line = list(color = "rgba(70,70,70,0.55)", width = 0.5)),
            text = ~paste0(szin_nev, "<br>L*: ", round(Lstar), "  C*ab: ", round(Cab),
                           "<br>P(tökéletes): ", scales::percent(p, 1), "  n: ", n),
            hoverinfo = "text") |>
      layout(scene = list(xaxis = list(title = "a* (zöld–piros)"),
                          yaxis = list(title = "b* (kék–sárga)"),
                          zaxis = list(title = "L* (világosság)")))
  })

  # --- Korreláció ---
  output$p_cor <- renderPlot({
    d <- fdat(); need_rows(d)
    d |> transmute(`ΔE` = delta_e, `időtartam` = duration_sec, `lépésszám` = num_steps,
                   `elvétel-arány` = share_remove, `pigmentváltás` = switch_rate,
                   `medián lépésidő` = median_step_ms, `túllövés-arány` = overshoot_share,
                   `legjobb ΔE` = best_de, `kulcs cseppszám` = key_drops,
                   `próbálkozás sorszáma` = attempt_no, `életkor` = age, `nő (=1)` = female) |>
      cor_heatmap() + labs(title = "Spearman-rangkorrelációk (próbálkozás-szint)")
  })

  # --- Felhasználó (PCA) ---
  output$p_pca <- renderPlot({
    us <- fdat() |>
      group_by(user_id) |>
      summarise(n = n(), n_nt = sum(key_drops > 2, na.rm = TRUE),
                perfect_rate = mean(is_perfect),
                median_steps = median(num_steps, na.rm = TRUE),
                median_dur = median(duration_sec, na.rm = TRUE),
                share_remove = mean(share_remove, na.rm = TRUE),
                switch_rate = mean(switch_rate, na.rm = TRUE),
                median_step_ms = median(median_step_ms, na.rm = TRUE),
                overshoot = mean(overshoot_share, na.rm = TRUE), .groups = "drop") |>
      filter(n_nt >= input$minnt)
    validate(need(nrow(us) >= 4, "Túl kevés felhasználó a jelenlegi küszöbökhöz (lazíts a szűrőn)."))
    pca_d <- us |>
      transmute(perfect_rate,
                `log lépésszám` = log10(median_steps),
                `log időtartam` = log10(median_dur),
                `elvétel-arány` = share_remove,
                `pigmentváltás` = switch_rate,
                `log lépésidő` = log10(median_step_ms),
                `túllövés-arány` = overshoot) |>
      filter(if_all(everything(), is.finite))
    validate(need(nrow(pca_d) >= 4, "Túl kevés érvényes felhasználó a PCA-hoz."))
    pc <- prcomp(pca_d |> select(-perfect_rate), scale. = TRUE)
    ve <- round(100 * pc$sdev^2 / sum(pc$sdev^2))
    scores <- as_tibble(pc$x[, 1:2]) |> mutate(perfect_rate = pca_d$perfect_rate)
    load <- as_tibble(pc$rotation[, 1:2], rownames = "valtozo")
    arr <- 3.2
    ggplot(scores, aes(PC1, PC2)) +
      geom_hline(yintercept = 0, colour = "grey88") +
      geom_vline(xintercept = 0, colour = "grey88") +
      geom_point(aes(colour = perfect_rate), size = 3, alpha = 0.85) +
      geom_segment(data = load, aes(x = 0, y = 0, xend = PC1 * arr, yend = PC2 * arr),
                   arrow = grid::arrow(length = grid::unit(0.18, "cm")),
                   colour = "#c44e52", linewidth = 0.5) +
      geom_text(data = load, aes(PC1 * arr * 1.14, PC2 * arr * 1.14, label = valtozo),
                colour = "#c44e52", size = 3.5) +
      scale_colour_gradient(low = "#c44e52", high = "#55a868", labels = percent) +
      labs(x = paste0("PC1 (", ve[1], "%)"), y = paste0("PC2 (", ve[2], "%)"),
           colour = "tökéletes-arány", title = "Viselkedési stratégia-tér (PCA-biplot)")
  })
}

shinyApp(ui, server)
