#!/usr/bin/env python3
"""
Attempt-level mixed models for ShadeMatch.

Models:
1) Continuous LMM: log1p(final_delta_e)
2) Bounded LMM: logit(similarity)
3) Binary mixed model: perfect_ratio (similarity == 1)

Usage:
  python scripts/mixed_models_analysis.py
  python scripts/mixed_models_analysis.py --max-attempt-no 15 --output-dir artifacts/mixed_models
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Dict, Optional, List

import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from sqlalchemy import text
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
from statsmodels.stats.outliers_influence import variance_inflation_factor

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import create_app, db
from app.stat_eda import get_dataframes, build_attempt_recipe_similarity


def _safe_logit(x: pd.Series, eps: float = 1e-6) -> pd.Series:
    z = pd.to_numeric(x, errors="coerce").clip(lower=eps, upper=1.0 - eps)
    return np.log(z / (1.0 - z))


def _compute_recipe_complexity(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add recipe complexity features from target recipe drops.
    """
    drop_cols = [
        "target_drop_red",
        "target_drop_yellow",
        "target_drop_white",
        "target_drop_blue",
        "target_drop_black",
    ]
    out = df.copy()
    for c in drop_cols:
        out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0).clip(lower=0.0)

    drop_arr = out[drop_cols].to_numpy(dtype=float)
    drop_sum = np.maximum(drop_arr.sum(axis=1), 1e-12)
    ratios = drop_arr / drop_sum[:, None]
    entropy = -(ratios * np.log(np.clip(ratios, 1e-12, 1.0))).sum(axis=1)
    n_nonzero = (drop_arr > 0).sum(axis=1)

    out["target_total_drops"] = drop_sum
    out["target_recipe_entropy"] = entropy
    out["target_recipe_n_components"] = n_nonzero
    return out


def build_model_dataframe(max_attempt_no: Optional[int] = 15) -> pd.DataFrame:
    """
    Build attempt-level dataframe with repeated-measure structure:
      row = one attempt
      grouping = user_id and target_color_id
    """
    attempts_sql = text(
        """
        WITH base AS (
          SELECT
            ma.attempt_uuid,
            ma.user_id,
            ma.target_color_id,
            ma.final_delta_e,
            ma.initial_delta_e,
            ma.duration_sec,
            ma.num_steps,
            ma.end_reason,
            ma.attempt_started_server_ts,
            ROW_NUMBER() OVER (
              PARTITION BY ma.user_id, ma.target_color_id
              ORDER BY ma.attempt_started_server_ts NULLS LAST, ma.attempt_uuid
            ) AS attempt_no
          FROM mixing_attempts ma
          WHERE ma.user_id IS NOT NULL
            AND ma.target_color_id IS NOT NULL
        )
        SELECT
          b.attempt_uuid,
          b.user_id,
          b.target_color_id,
          b.final_delta_e,
          b.initial_delta_e,
          b.duration_sec,
          b.num_steps,
          b.end_reason,
          b.attempt_started_server_ts,
          b.attempt_no,
          COALESCE(tc.name, '(unknown)') AS target_name,
          COALESCE(tc.color_type, '(unknown)') AS color_type,
          COALESCE(tc.drop_red, 0) AS target_drop_red,
          COALESCE(tc.drop_yellow, 0) AS target_drop_yellow,
          COALESCE(tc.drop_white, 0) AS target_drop_white,
          COALESCE(tc.drop_blue, 0) AS target_drop_blue,
          COALESCE(tc.drop_black, 0) AS target_drop_black
        FROM base b
        JOIN target_colors tc ON tc.id = b.target_color_id
        """
    )

    with db.engine.connect() as conn:
        attempts = pd.read_sql(attempts_sql, conn)

    if len(attempts) == 0:
        return attempts

    att, ev = get_dataframes()
    sim = build_attempt_recipe_similarity(att, ev)[
        ["attempt_uuid", "similarity", "ratio_is_perfect"]
    ].copy()
    sim["attempt_uuid"] = sim["attempt_uuid"].astype(str)

    attempts["attempt_uuid"] = attempts["attempt_uuid"].astype(str)
    df = attempts.merge(sim, on="attempt_uuid", how="left")
    df = _compute_recipe_complexity(df)

    if max_attempt_no is not None:
        df = df[pd.to_numeric(df["attempt_no"], errors="coerce") <= int(max_attempt_no)].copy()

    # Core derived fields for model formulas.
    df["log_final_delta_e"] = np.log1p(pd.to_numeric(df["final_delta_e"], errors="coerce").clip(lower=0.0))
    df["log_duration_sec"] = np.log1p(pd.to_numeric(df["duration_sec"], errors="coerce").clip(lower=0.0))
    df["log_num_steps"] = np.log1p(pd.to_numeric(df["num_steps"], errors="coerce").clip(lower=0.0))
    df["similarity_logit"] = _safe_logit(df["similarity"])
    df["perfect_ratio"] = pd.to_numeric(df["ratio_is_perfect"], errors="coerce").fillna(0).astype(int)
    df["perfect_color"] = (
        pd.to_numeric(df["final_delta_e"], errors="coerce").fillna(np.inf) <= 0.01
    ).astype(int)

    return df


def _prepare_common_covariates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ["attempt_no", "initial_delta_e", "log_duration_sec", "log_num_steps", "target_recipe_n_components"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out["attempt_no"] = out["attempt_no"].fillna(1.0)
    init_med = out["initial_delta_e"].median()
    dur_med = out["log_duration_sec"].median()
    steps_med = out["log_num_steps"].median()
    comp_med = out["target_recipe_n_components"].median()
    out["initial_delta_e"] = out["initial_delta_e"].fillna(0.0 if pd.isna(init_med) else float(init_med))
    out["log_duration_sec"] = out["log_duration_sec"].fillna(0.0 if pd.isna(dur_med) else float(dur_med))
    out["log_num_steps"] = out["log_num_steps"].fillna(0.0 if pd.isna(steps_med) else float(steps_med))
    out["target_recipe_n_components"] = out["target_recipe_n_components"].fillna(
        1.0 if pd.isna(comp_med) else float(comp_med)
    )
    out["color_type"] = out["color_type"].fillna("(unknown)").astype(str)
    # Standardized covariates for better numerical conditioning.
    for col in ["attempt_no", "log_duration_sec", "log_num_steps", "target_recipe_n_components", "similarity"]:
        s = pd.to_numeric(out[col], errors="coerce")
        if s.isna().any():
            med = s.median()
            s = s.fillna(0.0 if pd.isna(med) else float(med))
        m = s.mean()
        sd = s.std(ddof=0)
        if pd.isna(sd) or sd <= 1e-12:
            out[f"z_{col}"] = 0.0
        else:
            out[f"z_{col}"] = (s - m) / sd
    return out


def _build_formula(
    outcome: str,
    *,
    stable_spec: bool = True,
    include_interaction: bool = False,
    include_similarity: bool = False,
) -> str:
    rhs: List[str] = [
        "z_attempt_no",
        "z_log_duration_sec",
        "z_log_num_steps",
        "C(color_type)",
        "z_target_recipe_n_components",
    ]
    if not stable_spec:
        rhs.append("initial_delta_e")
    if include_similarity:
        rhs.append("z_similarity")
    if include_interaction:
        rhs.append("z_attempt_no:z_target_recipe_n_components")
    return f"{outcome} ~ " + " + ".join(rhs)


def _compute_vif_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "z_attempt_no",
        "z_log_duration_sec",
        "z_log_num_steps",
        "z_target_recipe_n_components",
        "z_similarity",
    ]
    x = df[cols].apply(pd.to_numeric, errors="coerce").dropna()
    if len(x) < 10:
        return pd.DataFrame(columns=["feature", "vif"])
    out = []
    arr = x.to_numpy(dtype=float)
    for i, c in enumerate(cols):
        out.append({"feature": c, "vif": float(variance_inflation_factor(arr, i))})
    return pd.DataFrame(out).sort_values("vif", ascending=False)


def fit_continuous_lmm(df: pd.DataFrame, *, stable_spec: bool = True, include_interaction: bool = False):
    """
    Continuous outcome model:
      log1p(final_delta_e) ~ fixed effects + random(user intercept+slope) + random(target intercept)
    """
    need = ["log_final_delta_e", "user_id", "target_color_id"]
    d = _prepare_common_covariates(df)
    d = d[
        [
            *need,
            "z_attempt_no",
            "initial_delta_e",
            "z_log_duration_sec",
            "z_log_num_steps",
            "color_type",
            "z_target_recipe_n_components",
            "z_similarity",
        ]
    ]
    formula = _build_formula(
        "log_final_delta_e",
        stable_spec=stable_spec,
        include_interaction=include_interaction,
        include_similarity=True,
    )
    d = d.dropna(subset=["log_final_delta_e", "user_id", "target_color_id"])
    if len(d) < 30:
        raise ValueError("Not enough rows for continuous LMM after NA filtering.")
    if d["user_id"].nunique(dropna=True) < 5:
        ols = smf.ols(formula, data=d)
        return ols.fit(cov_type="HC3")

    try:
        model = smf.mixedlm(
            formula,
            data=d,
            groups="user_id",
            re_formula="1 + attempt_no",
            vc_formula={"target_color": "0 + C(target_color_id)"},
            missing="drop",
        )
        return model.fit(reml=False, method="lbfgs", maxiter=400)
    except Exception:
        ols = smf.ols(formula, data=d)
        return ols.fit(cov_type="HC3")


def fit_similarity_lmm(df: pd.DataFrame):
    """
    Bounded similarity model via logit transform + LMM.
    """
    d = _prepare_common_covariates(df)
    d["similarity_logit"] = pd.to_numeric(d["similarity_logit"], errors="coerce")
    formula = _build_formula(
        "similarity_logit",
        stable_spec=True,
        include_interaction=False,
        include_similarity=False,
    )
    d = d[
        [
            "similarity_logit",
            "z_attempt_no",
            "z_log_duration_sec",
            "z_log_num_steps",
            "color_type",
            "z_target_recipe_n_components",
            "user_id",
            "target_color_id",
        ]
    ].dropna(subset=["similarity_logit", "user_id", "target_color_id"])
    if len(d) < 30:
        raise ValueError("Not enough rows for similarity LMM after NA filtering.")
    if d["user_id"].nunique(dropna=True) < 5:
        ols = smf.ols(formula, data=d)
        return ols.fit(cov_type="HC3")

    try:
        model = smf.mixedlm(
            formula,
            data=d,
            groups="user_id",
            re_formula="1 + attempt_no",
            vc_formula={"target_color": "0 + C(target_color_id)"},
            missing="drop",
        )
        return model.fit(reml=False, method="lbfgs", maxiter=400)
    except Exception:
        ols = smf.ols(formula, data=d)
        return ols.fit(cov_type="HC3")


def fit_perfect_ratio_glmm(df: pd.DataFrame):
    """
    Binary mixed model for perfect ratio.
    """
    d = _prepare_common_covariates(df)
    d["perfect_ratio"] = pd.to_numeric(d["perfect_ratio"], errors="coerce")
    d["perfect_color"] = pd.to_numeric(d["perfect_color"], errors="coerce")
    d = d[
        [
            "perfect_ratio",
            "perfect_color",
            "z_attempt_no",
            "z_log_duration_sec",
            "z_log_num_steps",
            "color_type",
            "z_target_recipe_n_components",
            "user_id",
            "target_color_id",
        ]
    ].dropna(subset=["user_id", "target_color_id"])
    outcome = "perfect_ratio"
    if d["perfect_ratio"].nunique(dropna=True) < 2:
        outcome = "perfect_color"
    if d[outcome].nunique(dropna=True) < 2:
        raise ValueError("Neither perfect_ratio nor perfect_color has class variation.")

    # Variational Bayes fit; robust for moderate sample sizes.
    formula = _build_formula(
        outcome,
        stable_spec=True,
        include_interaction=False,
        include_similarity=False,
    )
    if len(d) < 30:
        raise ValueError("Not enough rows for binary mixed model after NA filtering.")
    if d["user_id"].nunique(dropna=True) < 5:
        try:
            glm = smf.glm(formula, data=d, family=sm.families.Binomial())
            return glm.fit(cov_type="HC3")
        except Exception:
            simple_formula = (
                f"{outcome} ~ z_attempt_no + z_log_duration_sec + z_log_num_steps + z_target_recipe_n_components"
            )
            glm = smf.glm(simple_formula, data=d, family=sm.families.Binomial())
            return glm.fit(cov_type="HC3")
    try:
        model = BinomialBayesMixedGLM.from_formula(
            formula,
            vc_formulas={
                "user_re": "0 + C(user_id)",
                "target_re": "0 + C(target_color_id)",
            },
            data=d,
        )
        return model.fit_vb()
    except Exception:
        try:
            glm = smf.glm(formula, data=d, family=sm.families.Binomial())
            return glm.fit(cov_type="HC3")
        except Exception:
            simple_formula = (
                f"{outcome} ~ z_attempt_no + z_log_duration_sec + z_log_num_steps + z_target_recipe_n_components"
            )
            glm = smf.glm(simple_formula, data=d, family=sm.families.Binomial())
            return glm.fit(cov_type="HC3")


def _odds_ratio_table_from_glm(result) -> pd.DataFrame:
    params = result.params
    ci = result.conf_int()
    out = pd.DataFrame(
        {
            "term": params.index,
            "coef_logit": params.values,
            "odds_ratio": np.exp(params.values),
            "ci_low_or": np.exp(ci[0].values),
            "ci_high_or": np.exp(ci[1].values),
            "p_value": result.pvalues.values,
        }
    )
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Fit mixed models on ShadeMatch attempts.")
    parser.add_argument(
        "--max-attempt-no",
        type=int,
        default=15,
        help="Keep attempts up to this within-user-target index (default: 15).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/mixed_models"),
        help="Directory where model summaries are saved.",
    )
    parser.add_argument(
        "--spec",
        choices=["stable", "full"],
        default="stable",
        help="stable: reduced low-collinearity spec; full: includes initial_delta_e and interaction.",
    )
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        df = build_model_dataframe(max_attempt_no=args.max_attempt_no)

    if len(df) == 0:
        raise SystemExit("No rows available for model fitting.")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "model_dataframe_preview.csv").write_text(
        df.head(200).to_csv(index=False),
        encoding="utf-8",
    )

    meta = {
        "n_rows": int(len(df)),
        "n_users": int(df["user_id"].nunique(dropna=True)),
        "n_targets": int(df["target_color_id"].nunique(dropna=True)),
        "max_attempt_no": int(args.max_attempt_no),
        "perfect_ratio_rate": float(pd.to_numeric(df["perfect_ratio"], errors="coerce").mean()),
        "spec": args.spec,
    }
    (args.output_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    prep = _prepare_common_covariates(df)
    vif = _compute_vif_table(prep)
    if len(vif):
        (args.output_dir / "vif_table.csv").write_text(vif.to_csv(index=False), encoding="utf-8")

    results: Dict[str, str] = {}
    try:
        cont = fit_continuous_lmm(
            df,
            stable_spec=(args.spec == "stable"),
            include_interaction=(args.spec == "full"),
        )
        results["continuous_lmm"] = str(cont.summary())
    except Exception as e:
        results["continuous_lmm"] = f"FAILED: {e}"

    try:
        sim = fit_similarity_lmm(df)
        results["similarity_lmm"] = str(sim.summary())
    except Exception as e:
        results["similarity_lmm"] = f"FAILED: {e}"

    try:
        binm = fit_perfect_ratio_glmm(df)
        results["perfect_ratio_glmm"] = str(binm.summary())
        # Save OR table when GLM-like result is returned.
        if hasattr(binm, "params") and hasattr(binm, "conf_int"):
            try:
                ort = _odds_ratio_table_from_glm(binm)
                (args.output_dir / "perfect_ratio_odds_ratios.csv").write_text(
                    ort.to_csv(index=False), encoding="utf-8"
                )
            except Exception:
                pass
    except Exception as e:
        results["perfect_ratio_glmm"] = f"FAILED: {e}"

    for key, txt in results.items():
        p = args.output_dir / f"{key}.txt"
        p.write_text(txt + "\n", encoding="utf-8")
        print(f"\n=== {key} ===\n{txt[:1200]}\n")

    print(f"Saved artifacts to: {args.output_dir}")


if __name__ == "__main__":
    main()

