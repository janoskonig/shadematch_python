#!/usr/bin/env python3
"""
Generate Hungarian exploratory EDA figures for ShadeMatch.

The figures are designed to support these hypotheses:
1) Final DeltaE decreases with repeated attempts, while required time does not.
2) Median DeltaE is around the perceptual threshold (~1.5).
3) Identify the first attempt index where median final DeltaE drops below 2.0.
4) Strategy archetypes are distinguishable (oscillator, random searcher, backslider,
   fast/slow convergers).
"""

from __future__ import annotations

from pathlib import Path
from matplotlib.lines import Line2D
from typing import Dict, List, Set, Tuple
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app, db
from app.stat_eda import _dashboard_attempts_with_attempt_no, build_attempt_archetypes, get_dataframes


OUT_DIR = Path("artifacts/eda_figures_hu")

ARC_HU = {
    "oscillator": "Oszcilláló",
    "random_searcher": "Random kereső",
    "backslider": "Backslider",
    "direct_converger": "Gyors konvergáló",
    "slow_and_steady": "Lassan konvergáló",
    "coarse_then_fine": "Durvából finomító",
}

PIGMENT_HEX = {
    "red": "#ef4444",
    "yellow": "#f59e0b",
    "white": "#f8fafc",
    "blue": "#2563eb",
    "black": "#111827",
}

PIGMENT_HU = {
    "red": "piros",
    "yellow": "sarga",
    "white": "feher",
    "blue": "kek",
    "black": "fekete",
}


def _base_style() -> None:
    plt.rcParams.update(
        {
            "figure.dpi": 150,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.alpha": 0.18,
            "grid.linestyle": "--",
            "font.size": 10,
        }
    )


def _attempt_series(att_no: pd.DataFrame, max_attempt_no: int = 15) -> pd.DataFrame:
    sub = att_no[
        (att_no["attempt_no"].notna())
        & (pd.to_numeric(att_no["attempt_no"], errors="coerce") >= 1)
        & (pd.to_numeric(att_no["attempt_no"], errors="coerce") <= max_attempt_no)
    ].copy()
    sub["attempt_no"] = pd.to_numeric(sub["attempt_no"], errors="coerce")
    sub["final_delta_e"] = pd.to_numeric(sub["final_delta_e"], errors="coerce")
    sub["duration_sec"] = pd.to_numeric(sub["duration_sec"], errors="coerce")
    sub["target_name"] = sub["target_name"].fillna("(unknown)").astype(str)
    sub = sub[sub["final_delta_e"].notna() & sub["target_name"].notna()]
    return sub


def _per_color_attempt_table(att_series: pd.DataFrame, max_attempt_no: int = 15) -> pd.DataFrame:
    return (
        att_series[att_series["attempt_no"] <= max_attempt_no]
        .groupby(["target_name", "attempt_no"], sort=True, as_index=False)
        .agg(
            median_de=("final_delta_e", "median"),
            median_time=("duration_sec", "median"),
            n=("attempt_uuid", "nunique"),
        )
    )

def fig1_color_attempt_heatmap(per_color: pd.DataFrame, out_dir: Path) -> List[str]:
    color_volume = (
        per_color.groupby("target_name", as_index=False)["n"]
        .sum()
        .sort_values("n", ascending=False)
    )
    top_colors = color_volume.head(20)["target_name"].tolist()
    part = per_color[per_color["target_name"].isin(top_colors)].copy()
    if part.empty:
        return []
    mat = (
        part.pivot_table(
            index="target_name",
            columns="attempt_no",
            values="median_de",
            aggfunc="median",
        )
        .reindex(top_colors)
        .sort_index(axis=1)
    )
    mat_t = np.log1p(mat.to_numpy(dtype=float))
    fig, ax = plt.subplots(figsize=(11.2, 6.2), constrained_layout=True)
    im = ax.imshow(mat_t, aspect="auto", cmap="viridis")
    ax.set_yticks(np.arange(len(mat.index)))
    ax.set_yticklabels(mat.index.tolist(), fontsize=8)
    ax.set_xticks(np.arange(len(mat.columns)))
    ax.set_xticklabels([str(int(c)) for c in mat.columns], fontsize=8)
    ax.set_xlabel("Próbálkozás sorszáma")
    ax.set_ylabel("Célszín")
    ax.set_title("Medián DeltaE hőtérkép (színenként, próbálkozásonként; log-skála)")
    cb = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cb.set_label("log(1 + medián DeltaE)")
    fig.savefig(out_dir / "01_deltae_hotterkep_szinenkent_probalkozasonkent.png", bbox_inches="tight")
    plt.close(fig)
    return top_colors


def fig2_colorwise_trajectories(per_color: pd.DataFrame, out_dir: Path) -> List[str]:
    a1 = per_color[per_color["attempt_no"] == 1].copy()
    if a1.empty:
        return []
    a1 = a1.sort_values("median_de", ascending=False)
    selected = a1.head(8)["target_name"].tolist()
    if not selected:
        return []
    part = per_color[per_color["target_name"].isin(selected)].copy()

    n_panels = len(selected)
    ncols = 4
    nrows = int(np.ceil(n_panels / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(14.5, 3.3 * nrows), constrained_layout=True)
    axes = np.atleast_1d(axes).reshape(nrows, ncols)
    for i, name in enumerate(selected):
        r, c = divmod(i, ncols)
        ax = axes[r][c]
        g = part[part["target_name"] == name].sort_values("attempt_no")
        x = g["attempt_no"].to_numpy(dtype=float)
        y_de = g["median_de"].to_numpy(dtype=float)
        y_t = g["median_time"].to_numpy(dtype=float)
        ax.plot(x, y_de, marker="o", color="#1d4ed8", linewidth=1.8, label="medián DeltaE")
        ax.axhline(2.0, color="#dc2626", linestyle="--", linewidth=1.0, alpha=0.8)
        ax.set_title(str(name), fontsize=9)
        ax.set_xlabel("Próbálkozás")
        ax.set_ylabel("DeltaE", color="#1d4ed8")
        ax.tick_params(axis="y", labelcolor="#1d4ed8")
        ax2 = ax.twinx()
        ax2.plot(x, y_t, marker="s", color="#7c3aed", linewidth=1.5, alpha=0.8, label="medián idő")
        ax2.set_ylabel("Idő (s)", color="#7c3aed")
        ax2.tick_params(axis="y", labelcolor="#7c3aed")
    for j in range(n_panels, nrows * ncols):
        r, c = divmod(j, ncols)
        axes[r][c].axis("off")
    fig.suptitle("Színenkénti pályák: medián DeltaE és medián idő próbálkozásonként", fontsize=12)
    fig.savefig(out_dir / "02_szinenkenti_deltae_ido_trajectoriak.png", bbox_inches="tight")
    plt.close(fig)
    return selected


def fig3_first_under2_by_color(per_color: pd.DataFrame, out_dir: Path) -> Tuple[float, int]:
    rows: List[Dict[str, object]] = []
    for color, g in per_color.groupby("target_name", sort=False):
        gg = g.sort_values("attempt_no")
        under = gg[gg["median_de"] < 2.0]
        first_attempt = int(under.iloc[0]["attempt_no"]) if len(under) else np.nan
        rows.append({"target_name": color, "first_under2": first_attempt})
    df = pd.DataFrame(rows)
    if df.empty:
        return np.nan, 0
    ok = df["first_under2"].dropna()
    median_first = float(ok.median()) if len(ok) else np.nan

    dist = (
        ok.value_counts()
        .sort_index()
        .rename_axis("attempt_no")
        .reset_index(name="n_colors")
    )
    fig, ax = plt.subplots(figsize=(9.0, 4.6), constrained_layout=True)
    if len(dist):
        ax.bar(dist["attempt_no"].astype(int).astype(str), dist["n_colors"], color="#0f766e", alpha=0.85)
    ax.set_xlabel("Első próbálkozás, ahol medián DeltaE < 2.0 (színenként)")
    ax.set_ylabel("Színek száma")
    title = "Színenkénti 'első jó egyezés' eloszlás"
    if np.isfinite(median_first):
        title += f" (medián: {median_first:.1f})"
    ax.set_title(title)
    fig.savefig(out_dir / "03_elso_2_alatti_probalkozas_szinenkent.png", bbox_inches="tight")
    plt.close(fig)
    return median_first, int(len(ok))


def _median_trajectory_by_archetype(ev: pd.DataFrame, tags: pd.DataFrame, archetype: str) -> pd.DataFrame:
    ids = tags.loc[tags["archetype"] == archetype, "attempt_uuid"].dropna().astype(str).unique().tolist()
    if not ids:
        return pd.DataFrame(columns=["step", "median_de"])
    rows = ev[ev["attempt_uuid"].astype(str).isin(set(ids))].copy()
    rows["step_index"] = pd.to_numeric(rows["step_index"], errors="coerce")
    rows["delta_e_after"] = pd.to_numeric(rows["delta_e_after"], errors="coerce")
    rows = rows[rows["step_index"].notna() & rows["delta_e_after"].notna()]
    if rows.empty:
        return pd.DataFrame(columns=["step", "median_de"])
    grp = (
        rows.groupby("step_index", sort=True)["delta_e_after"]
        .median()
        .reset_index()
        .rename(columns={"step_index": "step", "delta_e_after": "median_de"})
    )
    grp["step"] = grp["step"] + 1.0
    return grp


def fig4_strategy_trajectories(ev: pd.DataFrame, archetype_rows: List[Dict[str, object]], out_dir: Path) -> List[str]:
    tags = pd.DataFrame(archetype_rows)
    if tags.empty or "archetype" not in tags.columns:
        return []
    selected = ["oscillator", "random_searcher", "backslider", "direct_converger", "slow_and_steady"]
    present = [a for a in selected if a in set(tags["archetype"].astype(str).unique())]
    if not present:
        return []

    fig, ax = plt.subplots(figsize=(9.2, 5.2), constrained_layout=True)
    cmap = plt.get_cmap("tab10")
    for i, arc in enumerate(present):
        tdf = _median_trajectory_by_archetype(ev, tags, arc)
        if tdf.empty:
            continue
        ax.plot(
            tdf["step"].to_numpy(dtype=float),
            tdf["median_de"].to_numpy(dtype=float),
            linewidth=2.2,
            alpha=0.95,
            color=cmap(i % 10),
            label=ARC_HU.get(arc, arc),
        )
    ax.axhline(2.0, color="#16a34a", linestyle="--", linewidth=1.2, alpha=0.85)
    ax.set_xlabel("Lépésszám (akciók)")
    ax.set_ylabel("Medián DeltaE az adott lépés után")
    ax.set_title("Stratégia-archetípusok: elkülönülő konvergencia mintázatok")
    ax.legend(loc="upper right", fontsize=8)
    fig.savefig(out_dir / "04_strategia_archetipus_trajectoriak.png", bbox_inches="tight")
    plt.close(fig)
    return present


def fig5_archetype_share_by_attempt(archetype_by_attempt: List[Dict[str, object]], out_dir: Path) -> None:
    df = pd.DataFrame(archetype_by_attempt)
    if df.empty:
        return
    df["attempt_no"] = pd.to_numeric(df["attempt_no"], errors="coerce")
    df["share_within_attempt_no"] = pd.to_numeric(df["share_within_attempt_no"], errors="coerce")
    df = df[df["attempt_no"].notna() & df["share_within_attempt_no"].notna()]
    if df.empty:
        return

    pivot = df.pivot_table(
        index="attempt_no",
        columns="archetype",
        values="share_within_attempt_no",
        aggfunc="sum",
        fill_value=0.0,
    ).sort_index()
    pivot = pivot.rename(columns={c: ARC_HU.get(str(c), str(c)) for c in pivot.columns})
    pivot = pivot.head(15)
    if pivot.empty:
        return

    fig, ax = plt.subplots(figsize=(10.2, 4.8), constrained_layout=True)
    x = np.arange(len(pivot))
    bottom = np.zeros(len(pivot), dtype=float)
    cmap = plt.get_cmap("tab20")
    for i, col in enumerate(pivot.columns):
        vals = pivot[col].to_numpy(dtype=float)
        ax.bar(x, vals, bottom=bottom, color=cmap(i), width=0.82, label=col, edgecolor="white", linewidth=0.4)
        bottom += vals
    ax.set_xticks(x)
    ax.set_xticklabels([str(int(v)) for v in pivot.index.to_numpy(dtype=float)])
    ax.set_ylim(0, 1.0)
    ax.set_xlabel("Próbálkozás sorszáma")
    ax.set_ylabel("Arány a próbálkozáson belül")
    ax.set_title("Archetípus-keverék a próbálkozásszám mentén")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8)
    fig.savefig(out_dir / "05_archetipus_keverek_probalkozasszam_szerint.png", bbox_inches="tight")
    plt.close(fig)


def _pick_representative_attempt(
    ev: pd.DataFrame,
    tags: pd.DataFrame,
    archetype: str,
    preferred_steps: int = 10,
) -> str | None:
    part = tags[tags["archetype"] == archetype].copy()
    if part.empty:
        return None
    part["attempt_uuid"] = part["attempt_uuid"].astype(str)
    part["n_actions"] = pd.to_numeric(part["n_actions"], errors="coerce")
    part["final_delta_e"] = pd.to_numeric(part["final_delta_e"], errors="coerce")

    action_rows = ev[
        ev["event_type"].isin(["action_add", "action_remove"])
        & ev["attempt_uuid"].astype(str).isin(set(part["attempt_uuid"]))
    ].copy()
    if action_rows.empty:
        return None
    counts = action_rows.groupby(action_rows["attempt_uuid"].astype(str)).size().rename("n_steps").reset_index()
    counts = counts.rename(columns={"attempt_uuid": "attempt_uuid"})
    part = part.merge(counts, on="attempt_uuid", how="left")
    part["n_steps"] = pd.to_numeric(part["n_steps"], errors="coerce").fillna(0)
    part = part[part["n_steps"] >= 3].copy()
    if part.empty:
        return None

    med_final = float(part["final_delta_e"].median()) if part["final_delta_e"].notna().any() else np.nan
    part["score_steps"] = (part["n_steps"] - preferred_steps).abs()
    part["score_final"] = (
        (part["final_delta_e"] - med_final).abs() if np.isfinite(med_final) else 0.0
    )
    part = part.sort_values(
        ["score_final", "score_steps", "n_steps"],
        ascending=[True, True, False],
    )
    return str(part.iloc[0]["attempt_uuid"])


def _target_names_with_total_drops_over(threshold: int = 10) -> Set[str]:
    with db.engine.connect() as conn:
        tc = pd.read_sql(
            text(
                """
                SELECT
                  name,
                  COALESCE(drop_red, 0) AS drop_red,
                  COALESCE(drop_yellow, 0) AS drop_yellow,
                  COALESCE(drop_white, 0) AS drop_white,
                  COALESCE(drop_blue, 0) AS drop_blue,
                  COALESCE(drop_black, 0) AS drop_black
                FROM target_colors
                """
            ),
            conn,
        )
    if tc.empty:
        return set()
    for c in ["drop_red", "drop_yellow", "drop_white", "drop_blue", "drop_black"]:
        tc[c] = pd.to_numeric(tc[c], errors="coerce").fillna(0)
    tc["total_drops"] = tc[["drop_red", "drop_yellow", "drop_white", "drop_blue", "drop_black"]].sum(axis=1)
    return set(tc.loc[tc["total_drops"] > float(threshold), "name"].astype(str).tolist())


def fig6_strategy_bubble_steps(
    ev: pd.DataFrame,
    archetype_rows: List[Dict[str, object]],
    out_dir: Path,
    allowed_target_names: Set[str] | None = None,
    perfect_match_only: bool = False,
    min_steps: int = 15,
    max_final_delta_e: float | None = 2.0,
) -> List[str]:
    tags = pd.DataFrame(archetype_rows)
    if tags.empty or "archetype" not in tags.columns:
        return []
    tags["n_actions"] = pd.to_numeric(tags["n_actions"], errors="coerce")
    tags["final_delta_e"] = pd.to_numeric(tags["final_delta_e"], errors="coerce")
    tags = tags[tags["n_actions"] >= float(min_steps)].copy()
    if tags.empty:
        return []
    if max_final_delta_e is not None:
        tags = tags[tags["final_delta_e"] < float(max_final_delta_e)].copy()
        if tags.empty:
            return []
    if allowed_target_names:
        tags["target_name"] = tags["target_name"].fillna("(unknown)").astype(str)
        tags = tags[tags["target_name"].isin(allowed_target_names)].copy()
        if tags.empty:
            return []
    if perfect_match_only:
        tags["final_delta_e"] = pd.to_numeric(tags["final_delta_e"], errors="coerce")
        tags = tags[np.isclose(tags["final_delta_e"], 0.0, atol=1e-9)].copy()
        if tags.empty:
            return []

    selected = ["oscillator", "random_searcher", "backslider", "direct_converger", "slow_and_steady"]
    present = [a for a in selected if a in set(tags["archetype"].astype(str).unique())]
    if not present:
        return []

    ncols = 1
    nrows = int(np.ceil(len(present) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(24.0, 4.2 * nrows), constrained_layout=True)
    axes = np.atleast_1d(axes).reshape(nrows, ncols)
    shown: List[str] = []

    for i, arc in enumerate(present):
        r, c = divmod(i, ncols)
        ax = axes[r][c]

        rep_attempt = _pick_representative_attempt(ev, tags, arc, preferred_steps=10)
        if rep_attempt is None:
            ax.axis("off")
            continue

        rows = ev[
            (ev["attempt_uuid"].astype(str) == rep_attempt)
            & ev["event_type"].isin(["action_add", "action_remove"])
        ].copy()
        if rows.empty:
            ax.axis("off")
            continue

        rows["seq"] = pd.to_numeric(rows["seq"], errors="coerce")
        rows["step_index"] = pd.to_numeric(rows["step_index"], errors="coerce")
        rows["delta_e_before"] = pd.to_numeric(rows["delta_e_before"], errors="coerce")
        rows["delta_e_after"] = pd.to_numeric(rows["delta_e_after"], errors="coerce")
        rows["action_type"] = rows["action_type"].fillna("").astype(str).str.lower().str.strip()
        rows["action_color"] = rows["action_color"].fillna("").astype(str).str.lower().str.strip()
        rows = rows[
            rows["delta_e_before"].notna()
            & rows["delta_e_after"].notna()
            & rows["action_type"].isin(["add", "remove"])
        ].copy()
        rows = rows.sort_values(["seq", "step_index"], na_position="last").reset_index(drop=True)
        if rows.empty:
            ax.axis("off")
            continue

        x = np.arange(1, len(rows) + 1, dtype=float)
        y = rows["delta_e_after"].to_numpy(dtype=float)
        delta_change = (rows["delta_e_after"] - rows["delta_e_before"]).to_numpy(dtype=float)
        action_type = rows["action_type"].tolist()
        action_color = rows["action_color"].tolist()
        signs = ["+" if t == "add" else "-" for t in action_type]
        bubble_colors = [PIGMENT_HEX.get(col, "#64748b") for col in action_color]
        bubble_sizes = 230.0 + np.clip(np.abs(delta_change) * 260.0, 0, 420)

        ax.plot(x, y, color="#94a3b8", linewidth=1.2, alpha=0.7, zorder=1)
        ax.scatter(
            x,
            y,
            s=bubble_sizes,
            c=bubble_colors,
            edgecolors="#0f172a",
            linewidths=0.8,
            alpha=0.95,
            zorder=2,
        )

        for xi, yi, sign, dch, col in zip(x, y, signs, delta_change, action_color):
            txt_color = "black" if col in ("yellow", "white") else "white"
            ax.text(xi, yi, sign, ha="center", va="center", fontsize=9, fontweight="bold", color=txt_color, zorder=3)
            action_txt = f"{sign}{PIGMENT_HU.get(col, col)}"
            dy = 13 if int(xi) % 2 == 0 else -17
            ax.annotate(
                f"{action_txt}\nΔE {dch:+.2f}",
                xy=(xi, yi),
                xytext=(0, dy),
                textcoords="offset points",
                ha="center",
                va="bottom" if dy > 0 else "top",
                fontsize=7,
                color="#111827",
            )

        ax.axhline(2.0, color="#16a34a", linestyle="--", linewidth=1.0, alpha=0.85)
        ax.set_xlabel("Lepes sorszama")
        ax.set_ylabel("DeltaE a lepes utan")

        trow = tags[tags["attempt_uuid"].astype(str) == rep_attempt].head(1)
        target_name = str(trow.iloc[0]["target_name"]) if not trow.empty else "(unknown)"
        final_de = pd.to_numeric(trow.iloc[0]["final_delta_e"], errors="coerce") if not trow.empty else np.nan
        title = f"{ARC_HU.get(arc, arc)} | cel: {target_name}"
        if np.isfinite(final_de):
            title += f" | vegso DeltaE: {final_de:.2f}"
        ax.set_title(title, fontsize=10)
        shown.append(arc)

    for j in range(len(present), nrows * ncols):
        r, c = divmod(j, ncols)
        axes[r][c].axis("off")

    legend_items = [
        Line2D([0], [0], marker="o", color="none", label=f"{PIGMENT_HU[k]}", markerfacecolor=v, markeredgecolor="#0f172a", markersize=8)
        for k, v in PIGMENT_HEX.items()
    ]
    fig.legend(
        handles=legend_items,
        loc="lower center",
        ncol=5,
        frameon=False,
        bbox_to_anchor=(0.5, -0.01),
        fontsize=8,
    )
    fig.suptitle("Strategia-buborekabra: lepesek, +/- akcio es DeltaE valtozas", fontsize=13)
    fig.savefig(out_dir / "06_strategia_buborek_lepesek_deltae.png", bbox_inches="tight")
    plt.close(fig)
    return shown


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _base_style()

    app = create_app()
    with app.app_context():
        att, ev = get_dataframes()
        att_no = _dashboard_attempts_with_attempt_no()
        archetypes = build_attempt_archetypes()

    att_series = _attempt_series(att_no, max_attempt_no=15)
    per_color = _per_color_attempt_table(att_series, max_attempt_no=15)
    top_colors = fig1_color_attempt_heatmap(per_color, OUT_DIR)
    shown_color_traj = fig2_colorwise_trajectories(per_color, OUT_DIR)
    median_first_under2, n_colors_under2 = fig3_first_under2_by_color(per_color, OUT_DIR)
    shown_archetypes = fig4_strategy_trajectories(ev, archetypes.get("per_attempt_tags", []), OUT_DIR)
    fig5_archetype_share_by_attempt(archetypes.get("archetype_by_attempt_no", []), OUT_DIR)
    shown_bubble_archetypes = fig6_strategy_bubble_steps(
        ev,
        archetypes.get("per_attempt_tags", []),
        OUT_DIR,
        min_steps=15,
        max_final_delta_e=2.0,
    )

    # Global trend is kept only as a secondary summary metric.
    g_global = (
        att_series.groupby("attempt_no", sort=True)
        .agg(median_de=("final_delta_e", "median"), median_time=("duration_sec", "median"))
        .reset_index()
    )
    slope_de = (
        float(np.polyfit(g_global["attempt_no"].to_numpy(dtype=float), g_global["median_de"].to_numpy(dtype=float), 1)[0])
        if len(g_global) >= 2
        else np.nan
    )
    slope_t = (
        float(np.polyfit(g_global["attempt_no"].to_numpy(dtype=float), g_global["median_time"].to_numpy(dtype=float), 1)[0])
        if len(g_global) >= 2
        else np.nan
    )

    summary_lines = [
        "EDA abra-csomag (HU) - automatikus osszegzes",
        "",
        f"Adatpontok (attempt sorok): {len(att_series)}",
        f"Egyedi szinek szama a tablaban: {per_color['target_name'].nunique()}",
        f"Globalis DeltaE trend meredekseg (median vs attempt_no): {slope_de:.4f} / probalkozas",
        f"Globalis ido trend meredekseg (median sec vs attempt_no): {slope_t:.4f} mp / probalkozas",
        f"Szinek szama, ahol van <2.0 medián: {n_colors_under2}",
        (
            f"Szinenkenti elso <2.0 probalkozas mediánja: {median_first_under2:.2f}"
            if np.isfinite(median_first_under2)
            else "Nincs olyan szin, ahol medián DeltaE < 2.0 1-15 probalkozason belul"
        ),
        "Hotterkepen szereplo top szinek: " + (", ".join(top_colors) if top_colors else "n/a"),
        "Kulon panelen szereplo szinek: " + (", ".join(shown_color_traj) if shown_color_traj else "n/a"),
        "Megjelenitett archetipusok: " + (", ".join([ARC_HU.get(a, a) for a in shown_archetypes]) if shown_archetypes else "n/a"),
        "Buborekstrategia-abran szereplo archetipusok: "
        + (", ".join([ARC_HU.get(a, a) for a in shown_bubble_archetypes]) if shown_bubble_archetypes else "n/a"),
    ]
    (OUT_DIR / "README_hu_osszegzes.txt").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
    print(f"Kesz. Kimenet: {OUT_DIR}")


if __name__ == "__main__":
    main()
