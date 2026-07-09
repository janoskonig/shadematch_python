#!/usr/bin/env python3
"""Assemble the extensive EDA PDF from figures + tables in artifacts/eda_v1_extensive/."""
import json
import os
from pathlib import Path
import matplotlib
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

ART = Path(__file__).resolve().parents[1] / "artifacts" / "eda_v1_extensive"
OUTPDF = Path(__file__).resolve().parents[1] / "notes" / "ShadeMatch_EDA_v1.pdf"
FONTDIR = Path(os.path.dirname(matplotlib.__file__)) / "mpl-data" / "fonts" / "ttf"
pdfmetrics.registerFont(TTFont("DejaVu", str(FONTDIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(FONTDIR / "DejaVuSans-Bold.ttf")))

S = json.loads((ART / "summary.json").read_text())
ctab = pd.read_csv(ART / "table_color_type.csv")
pcol = pd.read_csv(ART / "table_per_color.csv")

ss = getSampleStyleSheet()
body = ParagraphStyle("body", parent=ss["Normal"], fontName="DejaVu", fontSize=10,
                      leading=14, alignment=TA_JUSTIFY, spaceAfter=6)
h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName="DejaVu-Bold", fontSize=15,
                    spaceBefore=10, spaceAfter=6, textColor=colors.HexColor("#2c3e50"))
h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="DejaVu-Bold", fontSize=12,
                    spaceBefore=8, spaceAfter=4, textColor=colors.HexColor("#34495e"))
title = ParagraphStyle("title", parent=ss["Title"], fontName="DejaVu-Bold", fontSize=22, leading=26)
sub = ParagraphStyle("sub", parent=ss["Normal"], fontName="DejaVu", fontSize=11,
                     alignment=TA_CENTER, textColor=colors.HexColor("#666"))
cap = ParagraphStyle("cap", parent=ss["Normal"], fontName="DejaVu", fontSize=8.5,
                     textColor=colors.HexColor("#777"), spaceAfter=10)

E = []
def P(t): E.append(Paragraph(t, body))
def img(name, w=15*cm):
    p = ART / name
    from PIL import Image as PImage
    iw, ih = PImage.open(p).size
    E.append(Image(str(p), width=w, height=w*ih/iw))

def tbl(data, colw=None, fs=8.5):
    t = Table(data, colWidths=colw, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "DejaVu"), ("FONTSIZE", (0, 0), (-1, -1), fs),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVu-Bold"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#34495e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f6")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("ALIGN", (1, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    E.append(t)

# ---- Title page ----
E.append(Spacer(1, 4*cm))
E.append(Paragraph("ShadeMatch — feltáró adatelemzés (EDA)", title))
E.append(Spacer(1, 0.5*cm))
E.append(Paragraph("A v1 <i>mixing_sessions</i> színillesztési adatbázis", sub))
E.append(Spacer(1, 0.3*cm))
E.append(Paragraph(f"{S['n_rows']} próbálkozás · {S['n_users']} felhasználó · {S['n_targets']} célszín · "
                   f"{S['date_from']} – {S['date_to']}", sub))
E.append(Spacer(1, 1.2*cm))
E.append(Paragraph("Készítette: König János · 2026-06-24", sub))
E.append(PageBreak())

# 1 overview
E.append(Paragraph("1. Az adat áttekintése", h1))
P(f"Az elemzés a ShadeMatch színkeverő alkalmazás v1 <b>mixing_sessions</b> tábláján alapul. "
  f"A felhasználók néhány alappigment (fehér, fekete, vörös, sárga, kék) arányos keverésével "
  f"próbálnak reprodukálni egy célszínt; minden próbálkozásnál rögzül a célszíntől való "
  f"észlelt eltérés (ΔE), a megoldási idő, a használt recept és a célszín azonosítója. "
  f"Az adat <b>{S['n_rows']} próbálkozást</b> tartalmaz <b>{S['n_users']} felhasználótól</b>, "
  f"{S['n_targets']} célszínen, a {S['date_from']} – {S['date_to']} időszakból.")
tbl([["változó", "jelentés"],
     ["user_id", "felhasználó azonosító (202 egyedi)"],
     ["target_color_id", "célszín azonosító (40 egyedi; alapszín / bőrtónus)"],
     ["drop_white … drop_blue", "a használt recept: cseppszám pigmentenként"],
     ["delta_e", "záró észlelt színeltérés (ΔE) — fő kimenet"],
     ["time_sec", "megoldási idő másodpercben"],
     ["match_category", "perfect / no_perceivable / acceptable / big_difference / stopped"],
     ["skipped", "feladta-e a próbálkozást"],
     ["timestamp", "a próbálkozás időpontja"]],
    colw=[5*cm, 10*cm])
E.append(Spacer(1, 0.2*cm))
P(f"Demográfia (a ténylegesen játszó {S['n_users']} felhasználóra): "
  f"<b>{S['players_female']} nő / {S['players_male']} férfi</b>, medián életkor "
  f"<b>{S['age_median']:.0f} év</b>.")

# 2 data quality
E.append(Paragraph("2. Adatminőség", h1))
P(f"Az alap mezők (azonosítók, recept, ΔE, idő) hiánytalanok. A megoldási idő erősen ferde, "
  f"és tartalmaz irreális kiugró értékeket (max {S['time_max']:.0f} mp ≈ {S['time_max']/3600:.1f} óra; "
  f"a próbálkozások {S['time_over_10min_share']:.1%}-a 10 percnél hosszabb — feltehetően "
  f"félrerakott munkamenetek). A megoldási időt log-skálán és a felső farok kezelésével érdemes "
  f"nézni. Az életkor lényegében rendben van (mindössze {S['age_implausible']} valószínűtlen érték). "
  f"Két oszlop (attempt_uuid, skip_perception) a v1-ben jórészt üres, ezért a lépésenkénti "
  f"telemetria itt nem használható — a kimenet-szintű elemzés viszont a teljes mintára igen.")

# 3 outcome distribution
E.append(Paragraph("3. A fő kimenet: nullára koncentrált ΔE", h1))
P(f"A záró ΔE <b>félfolytonos</b>: a próbálkozások <b>{S['zero_share']:.0%}-ában pontosan nulla</b> "
  f"(tökéletes találat), a pozitív értékek pedig erősen jobbra ferde eloszlást mutatnak. "
  f"Ez a nulla-pontmassza + ferde pozitív rész szerkezet a teljes további elemzés központi "
  f"sajátossága, és önmagában is meghatározza, milyen modellcsalád jöhet szóba.")
img("01_deltae.png"); E.append(Paragraph("1. ábra — A záró ΔE eloszlása; a tömeg nagy része pontosan nullán.", cap))
img("02_deltae_pos.png"); E.append(Paragraph("2. ábra — Csak a pozitív rész (log10): erősen jobbra ferde.", cap))
img("03_ecdf.png"); E.append(Paragraph("3. ábra — ΔE empirikus eloszlásfüggvénye.", cap))

# 4 completion
E.append(Paragraph("4. Befejezés és feladás", h1))
P(f"A próbálkozások <b>{S['stopped_share']:.0%}-át a felhasználó feladja</b> (stopped) tökéletes "
  f"találat nélkül. A feladott eseteknél a rögzített ΔE a feladáskori hiba, nem a ténylegesen "
  f"elérhető legjobb találat — ezt a modellezésnél külön szelekciós kérdésként kell kezelni.")
img("04_matchcat.png"); E.append(Paragraph("4. ábra — A match_category kategóriák megoszlása.", cap))

# 5 skin vs basic
E.append(Paragraph("5. A legfontosabb mintázat: alapszín vs. bőrtónus", h1))
P("A feladat nehézsége drámaian eltér a célszín típusa szerint. A bőrtónusoknál a tökéletes "
  "találat sokkal ritkább, a feladás sokkal gyakoribb, és a megoldás lényegesen tovább tart. "
  "Ez azért fontos statisztikailag, mert a nulla-pontmassza nagysága maga is a vizsgálni "
  "kívánt kovariánstól (szín-típus) függ — vagyis nem pusztán technikai mellékkörülmény.")
tbl([["szín-típus", "n", "tökéletes %", "feladott %", "átlag ΔE", "medián idő (mp)"]] +
    [[r["color_type"], f"{int(r['n'])}", f"{r['perfect_pct']:.0f}", f"{r['stopped_pct']:.0f}",
      f"{r['mean_deltae']:.2f}", f"{r['median_time']:.0f}"] for _, r in ctab.iterrows()],
    colw=[3.2*cm, 2*cm, 2.6*cm, 2.6*cm, 2.3*cm, 3*cm])
E.append(Spacer(1, 0.2*cm))
img("05_skin_basic.png", w=12*cm)
E.append(Paragraph("5. ábra — Tökéletes találat és feladás aránya szín-típusonként.", cap))
img("06_per_color.png", w=11*cm)
E.append(Paragraph("6. ábra — Mind a 40 célszín átlagos ΔE-je; a nehéz vég szinte csak bőrtónus.", cap))

# 6 time
E.append(Paragraph("6. Megoldási idő", h1))
P(f"A megoldási idő mediánja {S['time_median']:.0f} mp, de erősen ferde és van benne irreális "
  f"farok. Bőrtónusnál a medián idő nagyságrenddel hosszabb, mint alapszínnél (ld. 5. szakasz).")
img("07_time.png"); E.append(Paragraph("7. ábra — Megoldási idő (log10), 10 perces jelölővel.", cap))

# 7 engagement + demo
E.append(Paragraph("7. Elköteleződés és demográfia", h1))
P(f"Az aktivitás egyenetlen: néhány felhasználó adja az adat jelentős részét (a 10 "
  f"legaktívabb user a próbálkozások {S['top10_user_share']:.0%}-át). Ugyanakkor a minta "
  f"ismételt méréses elemzéshez bőven elég: <b>{S['users_ge5']} felhasználónak ≥5</b>, "
  f"{S['users_ge20']}-nek ≥20, {S['users_ge50']}-nek ≥50 próbálkozása van. Az egyenetlenség "
  f"miatt érzékenységi elemzés indokolt (pl. ≥5 / ≥10 próbálkozású szűrés).")
img("08_engagement.png"); E.append(Paragraph("8. ábra — Próbálkozás/felhasználó és kumulált adatrész.", cap))
img("09_demo.png"); E.append(Paragraph("9. ábra — A játszó felhasználók életkora és neme.", cap))

# 8 practice
E.append(Paragraph("8. Gyakorlás (tanulási hatás)", h1))
P("A tökéletes találat aránya a felhasználón belüli próbálkozás-sorszám függvényében nézve "
  "csak gyenge/zajos trendet mutat; a magas sorszámokat ráadásul csak a kitartó felhasználók "
  "érik el (túlélési torzítás), amit egy megfelelő, felhasználót figyelembe vevő modell tud "
  "kezelni.")
img("10_practice.png"); E.append(Paragraph("10. ábra — Tökéletes találat aránya a próbálkozás sorszáma szerint.", cap))

# 9 effort
E.append(Paragraph("9. Recept / erőfeszítés és a kimenet", h1))
P("A használt pigmentek száma és az összes cseppszám az erőfeszítés közelítő mérőszáma. "
  "Több pigment és több csepp jellemzően nehezebb (kevésbé tökéletes) próbálkozásokhoz társul "
  "— ezek a recept-jellemzők lehetséges magyarázó változók egy későbbi modellben.")
img("11_effort.png"); E.append(Paragraph("11. ábra — Tökéletes találat a pigmentszám függvényében, és összcsepp vs. ΔE.", cap))

# 10 temporal
E.append(Paragraph("10. Időbeli lefedettség", h1))
img("12_temporal.png"); E.append(Paragraph("12. ábra — Próbálkozások havi eloszlása a 7 hónapos időszakban.", cap))

# 11 implications
E.append(Paragraph("11. Következtetések a modellezésre", h1))
P("Az EDA három olyan tulajdonságot emel ki, amelyet egy érdemi elemzésnek kezelnie kell: "
  "(1) a fő kimenet nullára koncentrált, félfolytonos, ezért a ΔE egyszerű (pl. log-transzformált) "
  "lineáris modellezése félrevezető lehet; (2) az adat ismételt méréses és klaszterezett "
  "(felhasználó és célszín szerint), ami a megfigyelések függőségét jelenti; (3) a nulla-arány "
  "és a feladás maga is összefügg a kovariánsokkal (szín-típus). E három jellemző együtt teszi a "
  "megfelelő elemzési mód kiválasztását nem triviálissá — ez a tervezett szakdolgozat kiindulópontja.")

# Appendix
E.append(PageBreak())
E.append(Paragraph("Függelék — célszínenkénti statisztika (mind a 40)", h1))
rows = [["szín id", "típus", "n", "tökéletes %", "feladott %", "átlag ΔE"]]
for _, r in pcol.iterrows():
    rows.append([f"#{int(r['target_color_id'])}", r["color_type"], f"{int(r['n'])}",
                 f"{r['perfect_pct']:.0f}", f"{r['stopped_pct']:.0f}", f"{r['mean_deltae']:.2f}"])
tbl(rows, colw=[2.2*cm, 2.6*cm, 1.8*cm, 2.8*cm, 2.6*cm, 2.4*cm], fs=7.5)

SimpleDocTemplate(str(OUTPDF), pagesize=A4, topMargin=1.6*cm, bottomMargin=1.6*cm,
                  leftMargin=2*cm, rightMargin=2*cm,
                  title="ShadeMatch EDA — v1 mixing_sessions").build(E)
print("PDF written:", OUTPDF)
