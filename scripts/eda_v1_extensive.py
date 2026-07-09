#!/usr/bin/env python3
"""Extensive EDA on v1 mixing_sessions. Generates figures + stats JSON + CSV tables."""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA = Path(__file__).resolve().parents[1] / "data" / "mixing_sessions"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "eda_v1_extensive"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"figure.dpi": 130, "font.size": 10, "axes.grid": True,
                     "grid.alpha": 0.25, "axes.spines.top": False, "axes.spines.right": False})
BLUE, GREEN, RED, BROWN, PURP = "#4c72b0", "#55a868", "#c44e52", "#d08c60", "#8172b3"

s = pd.read_csv(DATA / "mixing_sessions.csv")
u = pd.read_csv(DATA / "users.csv").rename(columns={"id": "user_id"})
tc = pd.read_csv(DATA / "target_colors.csv").rename(columns={"id": "target_color_id"})

s["timestamp"] = pd.to_datetime(s["timestamp"], errors="coerce")
s["delta_e"] = pd.to_numeric(s["delta_e"], errors="coerce")
s["time_sec"] = pd.to_numeric(s["time_sec"], errors="coerce")
u["age"] = (pd.Timestamp("2026-06-24") - pd.to_datetime(u["birthdate"], errors="coerce")).dt.days / 365.25
s = s.merge(u[["user_id", "age", "gender"]], on="user_id", how="left")
s = s.merge(tc[["target_color_id", "color_type", "classification"]], on="target_color_id", how="left")
s = s.sort_values(["user_id", "timestamp"])
s["attempt_no"] = s.groupby("user_id").cumcount() + 1
drop_cols = ["drop_white", "drop_black", "drop_red", "drop_yellow", "drop_blue"]
s["total_drops"] = s[drop_cols].clip(lower=0).sum(axis=1)
s["n_pigments"] = (s[drop_cols] > 0).sum(axis=1)
s["is_perfect"] = s["match_category"] == "perfect"
s["is_stopped"] = s["match_category"] == "stopped"
players = u[u["user_id"].isin(s["user_id"].unique())].copy()

S = {}
S["n_rows"] = int(len(s)); S["n_users"] = int(s["user_id"].nunique())
S["n_targets"] = int(s["target_color_id"].nunique())
S["date_from"] = str(s["timestamp"].min().date()); S["date_to"] = str(s["timestamp"].max().date())
S["zero_share"] = float((s["delta_e"] == 0).mean()); S["deltae_mean"] = float(s["delta_e"].mean())
S["stopped_share"] = float(s["is_stopped"].mean()); S["time_median"] = float(s["time_sec"].median())
S["time_over_10min_share"] = float((s["time_sec"] > 600).mean()); S["time_max"] = float(s["time_sec"].max())
S["players_female"] = int((players["gender"] == "female").sum())
S["players_male"] = int((players["gender"] == "male").sum())
S["age_median"] = float(players["age"].median())
S["age_implausible"] = int(((players["age"] < 10) | (players["age"] > 90)).sum())
vc = s["user_id"].value_counts()
S["users_ge5"] = int((vc >= 5).sum()); S["users_ge20"] = int((vc >= 20).sum()); S["users_ge50"] = int((vc >= 50).sum())
S["top1_user_share"] = float(vc.iloc[0] / len(s)); S["top10_user_share"] = float(vc.iloc[:10].sum() / len(s))

# color-type comparison table
ct = s.groupby("color_type").apply(lambda d: pd.Series({
    "n": len(d), "perfect_pct": d["is_perfect"].mean()*100, "stopped_pct": d["is_stopped"].mean()*100,
    "mean_deltae": d["delta_e"].mean(),
    "median_deltae_completed": d.loc[~d["is_stopped"], "delta_e"].median(),
    "median_time": d["time_sec"].median()})).reset_index()
ct.to_csv(OUT / "table_color_type.csv", index=False)

# per-color table
pc = s.groupby("target_color_id").apply(lambda d: pd.Series({
    "color_type": d["color_type"].iloc[0], "n": len(d), "perfect_pct": d["is_perfect"].mean()*100,
    "stopped_pct": d["is_stopped"].mean()*100, "mean_deltae": d["delta_e"].mean()})).reset_index()
pc = pc.sort_values("mean_deltae", ascending=False); pc.to_csv(OUT / "table_per_color.csv", index=False)

def save(fig, name):
    fig.tight_layout(); fig.savefig(OUT / name, bbox_inches="tight"); plt.close(fig)

# 1 deltae overall
fig, ax = plt.subplots(figsize=(6.4, 3.6))
ax.hist(s["delta_e"].clip(upper=20), bins=40, color=BLUE, edgecolor="white")
ax.axvline(0, color=RED, lw=1)
ax.set_title(f"1. Záró ΔE eloszlása — ΔE=0: {S['zero_share']:.0%}")
ax.set_xlabel("ΔE (20-nál levágva)"); ax.set_ylabel("próbálkozás"); save(fig, "01_deltae.png")

# 2 positive part log
fig, ax = plt.subplots(figsize=(6.4, 3.6))
pos = s.loc[s["delta_e"] > 0, "delta_e"]
ax.hist(np.log10(pos), bins=40, color=GREEN, edgecolor="white")
ax.set_title("2. A pozitív rész (ΔE>0) erősen jobbra ferde (log10)")
ax.set_xlabel("log10(ΔE)"); ax.set_ylabel("próbálkozás"); save(fig, "02_deltae_pos.png")

# 3 ECDF
fig, ax = plt.subplots(figsize=(6.4, 3.6))
x = np.sort(s["delta_e"]); y = np.arange(1, len(x)+1)/len(x)
ax.step(x, y, color=BLUE)
for v in (1, 2, 5):
    ax.axvline(v, color="#999", ls=":", lw=0.8)
ax.set_xlim(0, 15); ax.set_title("3. ΔE empirikus eloszlásfüggvénye (ECDF)")
ax.set_xlabel("ΔE"); ax.set_ylabel("kumulált arány"); save(fig, "03_ecdf.png")

# 4 match_category
fig, ax = plt.subplots(figsize=(6.4, 3.6))
order = ["perfect", "no_perceivable_difference", "acceptable_difference", "big_difference", "stopped"]
mc = s["match_category"].value_counts().reindex(order).fillna(0)
ax.bar(range(len(mc)), mc.values, color=BLUE)
ax.set_xticks(range(len(mc))); ax.set_xticklabels(["perfect", "no_perceiv.", "acceptable", "big_diff", "stopped"], rotation=20, ha="right")
ax.set_title("4. match_category megoszlás"); ax.set_ylabel("próbálkozás"); save(fig, "04_matchcat.png")

# 5 skin vs basic grouped
fig, ax = plt.subplots(figsize=(6.4, 3.6))
cats = ["basic", "skin"]; xp = np.arange(len(cats)); w = 0.35
perf = [ct.loc[ct.color_type == c, "perfect_pct"].values[0] for c in cats]
stop = [ct.loc[ct.color_type == c, "stopped_pct"].values[0] for c in cats]
ax.bar(xp - w/2, perf, w, label="tökéletes %", color=GREEN)
ax.bar(xp + w/2, stop, w, label="feladott %", color=RED)
ax.set_xticks(xp); ax.set_xticklabels(["alapszín", "bőrtónus"]); ax.legend()
ax.set_title("5. Alapszín vs. bőrtónus — találat és feladás"); ax.set_ylabel("%"); save(fig, "05_skin_basic.png")

# 6 per-color difficulty barh
g = pc.sort_values("mean_deltae")
fig, ax = plt.subplots(figsize=(6.4, 6.8))
ax.barh(range(len(g)), g["mean_deltae"], color=[BROWN if t == "skin" else BLUE for t in g["color_type"]])
ax.set_yticks(range(len(g))); ax.set_yticklabels([f"#{int(i)}" for i in g["target_color_id"]], fontsize=5)
ax.set_title("6. Célszínenkénti átlag ΔE\n(barna=bőrtónus, kék=alapszín)"); ax.set_xlabel("átlag ΔE"); save(fig, "06_per_color.png")

# 7 time log
fig, ax = plt.subplots(figsize=(6.4, 3.6))
t = s["time_sec"].replace(0, np.nan).dropna()
ax.hist(np.log10(t), bins=40, color=GREEN, edgecolor="white")
ax.axvline(np.log10(600), color=RED, ls="--", lw=1, label="10 perc")
ax.set_title(f"7. Megoldási idő (log10 mp), medián {S['time_median']:.0f} mp"); ax.legend()
ax.set_xlabel("log10(idő)"); ax.set_ylabel("próbálkozás"); save(fig, "07_time.png")

# 8 engagement + cumulative
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.6))
a1.hist(vc.clip(upper=100), bins=40, color=RED, edgecolor="white")
a1.set_title("8a. Próbálkozás / felhasználó"); a1.set_xlabel("próbálkozás (100+ levágva)"); a1.set_ylabel("user")
cumshare = np.cumsum(np.sort(vc.values)[::-1]) / vc.sum()
a2.plot(np.arange(1, len(cumshare)+1), cumshare, color=BLUE)
a2.set_title(f"8b. Kumulált adatrész\n(top 10 user = {S['top10_user_share']:.0%})")
a2.set_xlabel("felhasználók (aktivitás szerint)"); a2.set_ylabel("kumulált adatrész"); save(fig, "08_engagement.png")

# 9 demographics
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.6))
a1.hist(players["age"].clip(upper=70).dropna(), bins=25, color=PURP, edgecolor="white")
a1.set_title(f"9a. Életkor (játszók, medián {S['age_median']:.0f})"); a1.set_xlabel("év"); a1.set_ylabel("user")
gv = players["gender"].value_counts()
a2.bar(gv.index, gv.values, color=BROWN); a2.set_title("9b. Nem (játszó userek)"); save(fig, "09_demo.png")

# 10 practice effect: P(perfect) vs attempt_no
fig, ax = plt.subplots(figsize=(6.4, 3.6))
sub = s[s["attempt_no"] <= 30]
pe = sub.groupby("attempt_no")["is_perfect"].mean()
ax.plot(pe.index, pe.values, "-o", color=BLUE, ms=4)
ax.set_ylim(0, 1); ax.set_title("10. Tökéletes találat aránya vs. hányadik próbálkozás")
ax.set_xlabel("attempt_no (user-en belül)"); ax.set_ylabel("P(tökéletes)"); save(fig, "10_practice.png")

# 11 effort vs outcome
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.6))
eff = s.groupby("n_pigments").agg(perfect=("is_perfect", "mean"), n=("is_perfect", "size"))
eff = eff[eff["n"] >= 20]
a1.plot(eff.index, eff["perfect"], "-o", color=GREEN); a1.set_ylim(0, 1)
a1.set_title("11a. Tökéletes találat vs. használt pigmentek"); a1.set_xlabel("használt pigmentek száma"); a1.set_ylabel("P(tökéletes)")
a2.scatter(s["total_drops"].clip(upper=60), s["delta_e"].clip(upper=20), s=4, alpha=0.15, color=BLUE)
a2.set_title("11b. Összes csepp vs. ΔE"); a2.set_xlabel("összcsepp (60+ levágva)"); a2.set_ylabel("ΔE (20+ levágva)"); save(fig, "11_effort.png")

# 12 temporal
fig, ax = plt.subplots(figsize=(6.4, 3.6))
mth = s.set_index("timestamp").resample("ME").size()
ax.bar([d.strftime("%Y-%m") for d in mth.index], mth.values, color=PURP)
ax.set_title("12. Próbálkozások havonta"); plt.xticks(rotation=45, ha="right"); ax.set_ylabel("próbálkozás"); save(fig, "12_temporal.png")

(OUT / "summary.json").write_text(json.dumps(S, indent=2, ensure_ascii=False))
print(json.dumps(S, indent=2, ensure_ascii=False))
print("\ncolor_type table:\n", ct.to_string(index=False))
print("\nfigures ->", OUT)
