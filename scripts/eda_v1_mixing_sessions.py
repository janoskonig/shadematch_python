#!/usr/bin/env python3
"""EDA on the v1 `mixing_sessions` data only. Saves figures + summary CSVs."""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DATA = Path(__file__).resolve().parents[1] / "data" / "mixing_sessions"
OUT = Path(__file__).resolve().parents[1] / "artifacts" / "eda_v1_mixing_sessions"
OUT.mkdir(parents=True, exist_ok=True)
plt.rcParams.update({"figure.dpi": 110, "font.size": 10})

s = pd.read_csv(DATA / "mixing_sessions.csv")
u = pd.read_csv(DATA / "users.csv").rename(columns={"id": "user_id"})
tc = pd.read_csv(DATA / "target_colors.csv").rename(columns={"id": "target_color_id"})

s["timestamp"] = pd.to_datetime(s["timestamp"], errors="coerce")
s["delta_e"] = pd.to_numeric(s["delta_e"], errors="coerce")
s["time_sec"] = pd.to_numeric(s["time_sec"], errors="coerce")

# demographics
u["age"] = (pd.Timestamp("2026-06-24") - pd.to_datetime(u["birthdate"], errors="coerce")).dt.days / 365.25
s = s.merge(u[["user_id", "age", "gender"]], on="user_id", how="left")
s = s.merge(tc[["target_color_id", "color_type", "classification"]], on="target_color_id", how="left")

# attempt order within user
s = s.sort_values(["user_id", "timestamp"])
s["attempt_no"] = s.groupby("user_id").cumcount() + 1

# outcome helpers
s["solved"] = s["match_category"].isin(["perfect", "no_perceivable_difference", "acceptable_difference"])
s["total_drops"] = s[["drop_white", "drop_black", "drop_red", "drop_yellow", "drop_blue"]].clip(lower=0).sum(axis=1)

summary = {
    "n_rows": int(len(s)),
    "n_users": int(s["user_id"].nunique()),
    "n_targets": int(s["target_color_id"].nunique()),
    "date_from": str(s["timestamp"].min()), "date_to": str(s["timestamp"].max()),
    "deltae_zero_share": float((s["delta_e"] == 0).mean()),
    "deltae_median": float(s["delta_e"].median()),
    "time_median_sec": float(s["time_sec"].median()),
    "stopped_share": float((s["match_category"] == "stopped").mean()),
    "users_ge5": int((s["user_id"].value_counts() >= 5).sum()),
    "users_ge20": int((s["user_id"].value_counts() >= 20).sum()),
    "users_ge50": int((s["user_id"].value_counts() >= 50).sum()),
    "gender": u["gender"].value_counts().to_dict(),
    "age_median": float(u["age"].median()),
    "color_type_rows": s["color_type"].value_counts().to_dict(),
}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))

# 1. delta_e distribution (zoom 0-20)
fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(s["delta_e"].clip(upper=20), bins=40, color="#4c72b0", edgecolor="white")
ax.set_title(f"ΔE eloszlás (záró)\nΔE=0 aránya: {summary['deltae_zero_share']:.0%}, medián: {summary['deltae_median']:.2f}")
ax.set_xlabel("ΔE (20-nál levágva)"); ax.set_ylabel("próbálkozások")
fig.tight_layout(); fig.savefig(OUT / "01_deltae_eloszlas.png"); plt.close(fig)

# 2. time_sec log
fig, ax = plt.subplots(figsize=(7, 4))
t = s["time_sec"].replace(0, np.nan).dropna()
ax.hist(np.log10(t), bins=40, color="#55a868", edgecolor="white")
ax.set_title(f"Megoldási idő (log10 mp), medián {summary['time_median_sec']:.0f} mp")
ax.set_xlabel("log10(idő mp)"); ax.set_ylabel("próbálkozások")
fig.tight_layout(); fig.savefig(OUT / "02_ido_eloszlas.png"); plt.close(fig)

# 3. engagement: attempts per user
fig, ax = plt.subplots(figsize=(7, 4))
vc = s["user_id"].value_counts()
ax.hist(vc.clip(upper=100), bins=40, color="#c44e52", edgecolor="white")
ax.set_title(f"Próbálkozások száma felhasználónként (n={summary['n_users']} user)\n"
             f"≥5: {summary['users_ge5']}, ≥20: {summary['users_ge20']}, ≥50: {summary['users_ge50']}")
ax.set_xlabel("próbálkozás / user (100-nál levágva)"); ax.set_ylabel("felhasználók")
fig.tight_layout(); fig.savefig(OUT / "03_elkotelezodes.png"); plt.close(fig)

# 4. per-color difficulty
g = s.groupby("target_color_id").agg(
    n=("delta_e", "size"), mean_deltae=("delta_e", "mean"),
    share_perfect=("match_category", lambda x: (x == "perfect").mean()),
    color_type=("color_type", "first")).reset_index().sort_values("mean_deltae")
g.to_csv(OUT / "per_color_stats.csv", index=False)
fig, ax = plt.subplots(figsize=(8, 5))
colors = {"skin": "#d08c60", "basic": "#6699cc"}
ax.barh(range(len(g)), g["mean_deltae"], color=[colors.get(c, "#999") for c in g["color_type"]])
ax.set_yticks(range(len(g))); ax.set_yticklabels([f"#{i}" for i in g["target_color_id"]], fontsize=6)
ax.set_title("Célszínenkénti átlag ΔE (nehézség)\nbarna=bőrtónus, kék=alapszín")
ax.set_xlabel("átlag ΔE"); ax.set_ylabel("célszín id")
fig.tight_layout(); fig.savefig(OUT / "04_szin_nehezseg.png"); plt.close(fig)

# 5. learning curves (attempt_no <= 30)
sub = s[s["attempt_no"] <= 30]
lc = sub.groupby("attempt_no").agg(med_deltae=("delta_e", "median"),
                                    med_time=("time_sec", "median"), n=("delta_e", "size"))
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
a1.plot(lc.index, lc["med_deltae"], "-o", color="#4c72b0"); a1.set_title("Medián ΔE vs. hányadik próbálkozás")
a1.set_xlabel("attempt_no"); a1.set_ylabel("medián ΔE")
a2.plot(lc.index, lc["med_time"], "-o", color="#55a868"); a2.set_title("Medián idő vs. hányadik próbálkozás")
a2.set_xlabel("attempt_no"); a2.set_ylabel("medián idő (mp)")
fig.tight_layout(); fig.savefig(OUT / "05_tanulasi_gorbe.png"); plt.close(fig)

# 6. demographics
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4))
a1.hist(u["age"].dropna(), bins=30, color="#8172b3", edgecolor="white")
a1.set_title(f"Életkor (medián {summary['age_median']:.0f})"); a1.set_xlabel("év"); a1.set_ylabel("felhasználók")
gv = u["gender"].value_counts()
a2.bar(gv.index, gv.values, color="#937860"); a2.set_title("Nem szerinti megoszlás (regisztrált userek)")
fig.tight_layout(); fig.savefig(OUT / "06_demografia.png"); plt.close(fig)

# 7. match_category
fig, ax = plt.subplots(figsize=(7, 4))
mc = s["match_category"].value_counts()
ax.bar(mc.index, mc.values, color="#4c72b0"); ax.set_title("match_category megoszlás")
plt.xticks(rotation=20, ha="right"); fig.tight_layout(); fig.savefig(OUT / "07_match_category.png"); plt.close(fig)

# 8. delta_e by color_type
fig, ax = plt.subplots(figsize=(6, 4))
data = [s.loc[s["color_type"] == ct, "delta_e"].clip(upper=20).dropna() for ct in ["basic", "skin"]]
ax.boxplot(data, labels=["alapszín", "bőrtónus"], showfliers=False)
ax.set_title("ΔE szín-típus szerint"); ax.set_ylabel("ΔE (20-nál levágva)")
fig.tight_layout(); fig.savefig(OUT / "08_deltae_szintipus.png"); plt.close(fig)

# per-user stats
pu = s.groupby("user_id").agg(n=("delta_e", "size"), med_deltae=("delta_e", "median"),
                              stopped_share=("match_category", lambda x: (x == "stopped").mean()),
                              age=("age", "first"), gender=("gender", "first")).reset_index()
pu.to_csv(OUT / "per_user_stats.csv", index=False)

print(json.dumps(summary, indent=2, ensure_ascii=False))
print("\nFigures + CSVs written to:", OUT)
EOF_GUARD = None
