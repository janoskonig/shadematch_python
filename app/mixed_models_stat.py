"""
Attempt-level regression models for /stat (stable spec, HC3 robust SE when few users).

Used by:
- scripts/mixed_models_analysis.py (CLI export)
- app/routes.py stat_summary + stat plot PNGs
"""
from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
import statsmodels.formula.api as smf
from sqlalchemy import text
from statsmodels.genmod.bayes_mixed_glm import BinomialBayesMixedGLM
from statsmodels.stats.outliers_influence import variance_inflation_factor

from . import db
from .stat_eda import build_attempt_recipe_similarity, get_dataframes

CACHE_TTL_SEC = int(__import__('os').environ.get('STAT_MIXED_MODELS_CACHE_SECONDS', '120'))
_cache_lock = threading.Lock()
_cache_ts: float = 0.0
_cache_key: Optional[Tuple[int, str]] = None
_cache_payload: Optional[Dict[str, Any]] = None


def _json_float(x: Any) -> Optional[float]:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_logit(x: pd.Series, eps: float = 1e-6) -> pd.Series:
    z = pd.to_numeric(x, errors='coerce').clip(lower=eps, upper=1.0 - eps)
    return np.log(z / (1.0 - z))


def _compute_recipe_complexity(df: pd.DataFrame) -> pd.DataFrame:
    drop_cols = [
        'target_drop_red',
        'target_drop_yellow',
        'target_drop_white',
        'target_drop_blue',
        'target_drop_black',
    ]
    out = df.copy()
    for c in drop_cols:
        out[c] = pd.to_numeric(out[c], errors='coerce').fillna(0.0).clip(lower=0.0)
    drop_arr = out[drop_cols].to_numpy(dtype=float)
    drop_sum = np.maximum(drop_arr.sum(axis=1), 1e-12)
    ratios = drop_arr / drop_sum[:, None]
    entropy = -(ratios * np.log(np.clip(ratios, 1e-12, 1.0))).sum(axis=1)
    n_nonzero = (drop_arr > 0).sum(axis=1)
    out['target_total_drops'] = drop_sum
    out['target_recipe_entropy'] = entropy
    out['target_recipe_n_components'] = n_nonzero
    return out


def build_model_dataframe(max_attempt_no: Optional[int] = 15) -> pd.DataFrame:
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
    sim = build_attempt_recipe_similarity(att, ev)[['attempt_uuid', 'similarity', 'ratio_is_perfect']].copy()
    sim['attempt_uuid'] = sim['attempt_uuid'].astype(str)
    attempts['attempt_uuid'] = attempts['attempt_uuid'].astype(str)
    df = attempts.merge(sim, on='attempt_uuid', how='left')
    df = _compute_recipe_complexity(df)
    if max_attempt_no is not None:
        df = df[pd.to_numeric(df['attempt_no'], errors='coerce') <= int(max_attempt_no)].copy()
    df['log_final_delta_e'] = np.log1p(pd.to_numeric(df['final_delta_e'], errors='coerce').clip(lower=0.0))
    df['log_duration_sec'] = np.log1p(pd.to_numeric(df['duration_sec'], errors='coerce').clip(lower=0.0))
    df['log_num_steps'] = np.log1p(pd.to_numeric(df['num_steps'], errors='coerce').clip(lower=0.0))
    df['similarity_logit'] = _safe_logit(df['similarity'])
    df['perfect_ratio'] = pd.to_numeric(df['ratio_is_perfect'], errors='coerce').fillna(0).astype(int)
    df['perfect_color'] = (
        pd.to_numeric(df['final_delta_e'], errors='coerce').fillna(np.inf) <= 0.01
    ).astype(int)
    return df


def _prepare_common_covariates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in ['attempt_no', 'initial_delta_e', 'log_duration_sec', 'log_num_steps', 'target_recipe_n_components']:
        out[col] = pd.to_numeric(out[col], errors='coerce')
    out['attempt_no'] = out['attempt_no'].fillna(1.0)
    init_med = out['initial_delta_e'].median()
    dur_med = out['log_duration_sec'].median()
    steps_med = out['log_num_steps'].median()
    comp_med = out['target_recipe_n_components'].median()
    out['initial_delta_e'] = out['initial_delta_e'].fillna(0.0 if pd.isna(init_med) else float(init_med))
    out['log_duration_sec'] = out['log_duration_sec'].fillna(0.0 if pd.isna(dur_med) else float(dur_med))
    out['log_num_steps'] = out['log_num_steps'].fillna(0.0 if pd.isna(steps_med) else float(steps_med))
    out['target_recipe_n_components'] = out['target_recipe_n_components'].fillna(
        1.0 if pd.isna(comp_med) else float(comp_med)
    )
    out['color_type'] = out['color_type'].fillna('(unknown)').astype(str)
    for col in ['attempt_no', 'log_duration_sec', 'log_num_steps', 'target_recipe_n_components', 'similarity']:
        s = pd.to_numeric(out[col], errors='coerce')
        if s.isna().any():
            med = s.median()
            s = s.fillna(0.0 if pd.isna(med) else float(med))
        m = s.mean()
        sd = s.std(ddof=0)
        if pd.isna(sd) or sd <= 1e-12:
            out[f'z_{col}'] = 0.0
        else:
            out[f'z_{col}'] = (s - m) / sd
    return out


def _build_formula(
    outcome: str,
    *,
    stable_spec: bool = True,
    include_interaction: bool = False,
    include_similarity: bool = False,
) -> str:
    rhs: List[str] = [
        'z_attempt_no',
        'z_log_duration_sec',
        'z_log_num_steps',
        'C(color_type)',
        'z_target_recipe_n_components',
    ]
    if not stable_spec:
        rhs.append('initial_delta_e')
    if include_similarity:
        rhs.append('z_similarity')
    if include_interaction:
        rhs.append('z_attempt_no:z_target_recipe_n_components')
    return f'{outcome} ~ ' + ' + '.join(rhs)


def _compute_vif_table(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        'z_attempt_no',
        'z_log_duration_sec',
        'z_log_num_steps',
        'z_target_recipe_n_components',
        'z_similarity',
    ]
    x = df[cols].apply(pd.to_numeric, errors='coerce').dropna()
    if len(x) < 10:
        return pd.DataFrame(columns=['feature', 'vif'])
    out = []
    arr = x.to_numpy(dtype=float)
    for i, c in enumerate(cols):
        out.append({'feature': c, 'vif': float(variance_inflation_factor(arr, i))})
    return pd.DataFrame(out).sort_values('vif', ascending=False)


def fit_continuous_lmm(df: pd.DataFrame, *, stable_spec: bool = True, include_interaction: bool = False):
    need = ['log_final_delta_e', 'user_id', 'target_color_id']
    d = _prepare_common_covariates(df)
    d = d[
        [
            *need,
            'z_attempt_no',
            'initial_delta_e',
            'z_log_duration_sec',
            'z_log_num_steps',
            'color_type',
            'z_target_recipe_n_components',
            'z_similarity',
        ]
    ]
    formula = _build_formula(
        'log_final_delta_e',
        stable_spec=stable_spec,
        include_interaction=include_interaction,
        include_similarity=True,
    )
    d = d.dropna(subset=['log_final_delta_e', 'user_id', 'target_color_id'])
    if len(d) < 30:
        raise ValueError('Not enough rows for continuous model after NA filtering.')
    if d['user_id'].nunique(dropna=True) < 5:
        return smf.ols(formula, data=d).fit(cov_type='HC3')
    try:
        model = smf.mixedlm(
            formula,
            data=d,
            groups='user_id',
            re_formula='1 + attempt_no',
            vc_formula={'target_color': '0 + C(target_color_id)'},
            missing='drop',
        )
        return model.fit(reml=False, method='lbfgs', maxiter=400)
    except Exception:
        return smf.ols(formula, data=d).fit(cov_type='HC3')


def fit_similarity_lmm(df: pd.DataFrame):
    d = _prepare_common_covariates(df)
    d['similarity_logit'] = pd.to_numeric(d['similarity_logit'], errors='coerce')
    formula = _build_formula(
        'similarity_logit',
        stable_spec=True,
        include_interaction=False,
        include_similarity=False,
    )
    d = d[
        [
            'similarity_logit',
            'z_attempt_no',
            'z_log_duration_sec',
            'z_log_num_steps',
            'color_type',
            'z_target_recipe_n_components',
            'user_id',
            'target_color_id',
        ]
    ].dropna(subset=['similarity_logit', 'user_id', 'target_color_id'])
    if len(d) < 30:
        raise ValueError('Not enough rows for similarity model after NA filtering.')
    if d['user_id'].nunique(dropna=True) < 5:
        return smf.ols(formula, data=d).fit(cov_type='HC3')
    try:
        model = smf.mixedlm(
            formula,
            data=d,
            groups='user_id',
            re_formula='1 + attempt_no',
            vc_formula={'target_color': '0 + C(target_color_id)'},
            missing='drop',
        )
        return model.fit(reml=False, method='lbfgs', maxiter=400)
    except Exception:
        return smf.ols(formula, data=d).fit(cov_type='HC3')


def fit_perfect_ratio_glmm(df: pd.DataFrame):
    d = _prepare_common_covariates(df)
    d['perfect_ratio'] = pd.to_numeric(d['perfect_ratio'], errors='coerce')
    d['perfect_color'] = pd.to_numeric(d['perfect_color'], errors='coerce')
    d = d[
        [
            'perfect_ratio',
            'perfect_color',
            'z_attempt_no',
            'z_log_duration_sec',
            'z_log_num_steps',
            'color_type',
            'z_target_recipe_n_components',
            'user_id',
            'target_color_id',
        ]
    ].dropna(subset=['user_id', 'target_color_id'])
    outcome = 'perfect_ratio'
    if d['perfect_ratio'].nunique(dropna=True) < 2:
        outcome = 'perfect_color'
    if d[outcome].nunique(dropna=True) < 2:
        raise ValueError('Neither perfect_ratio nor perfect_color has class variation.')
    formula = _build_formula(
        outcome,
        stable_spec=True,
        include_interaction=False,
        include_similarity=False,
    )
    if len(d) < 30:
        raise ValueError('Not enough rows for binary model after NA filtering.')
    if d['user_id'].nunique(dropna=True) < 5:
        try:
            return smf.glm(formula, data=d, family=sm.families.Binomial()).fit(cov_type='HC3')
        except Exception:
            simple = (
                f'{outcome} ~ z_attempt_no + z_log_duration_sec + z_log_num_steps + z_target_recipe_n_components'
            )
            return smf.glm(simple, data=d, family=sm.families.Binomial()).fit(cov_type='HC3')
    try:
        model = BinomialBayesMixedGLM.from_formula(
            formula,
            vc_formulas={
                'user_re': '0 + C(user_id)',
                'target_re': '0 + C(target_color_id)',
            },
            data=d,
        )
        return model.fit_vb()
    except Exception:
        try:
            return smf.glm(formula, data=d, family=sm.families.Binomial()).fit(cov_type='HC3')
        except Exception:
            simple = (
                f'{outcome} ~ z_attempt_no + z_log_duration_sec + z_log_num_steps + z_target_recipe_n_components'
            )
            return smf.glm(simple, data=d, family=sm.families.Binomial()).fit(cov_type='HC3')


def _odds_ratio_table_from_glm(result) -> pd.DataFrame:
    params = result.params
    ci = result.conf_int()
    return pd.DataFrame(
        {
            'term': params.index,
            'coef_logit': params.values,
            'odds_ratio': np.exp(params.values),
            'ci_low_or': np.exp(ci[0].values),
            'ci_high_or': np.exp(ci[1].values),
            'p_value': result.pvalues.values,
        }
    )


def _ols_coef_rows(result) -> List[Dict[str, Any]]:
    params = result.params
    bse = result.bse
    pvals = result.pvalues
    ci = result.conf_int()
    rows = []
    for name in params.index:
        rows.append(
            {
                'term': str(name),
                'coef': _json_float(params[name]),
                'std_err': _json_float(bse[name] if name in bse.index else np.nan),
                'p_value': _json_float(pvals[name] if name in pvals.index else np.nan),
                'ci_low': _json_float(ci.loc[name, 0]) if name in ci.index else None,
                'ci_high': _json_float(ci.loc[name, 1]) if name in ci.index else None,
            }
        )
    return rows


def _model_fit_meta(result, *, kind: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {'kind': kind, 'model_class': type(result).__name__}
    try:
        out['nobs'] = int(result.nobs)
    except Exception:
        pass
    if kind == 'ols':
        try:
            out['r_squared'] = _json_float(result.rsquared)
            out['r_squared_adj'] = _json_float(result.rsquared_adj)
        except Exception:
            pass
    if kind == 'glm_binomial':
        try:
            out['pseudo_r2_cs'] = _json_float(result.pseudo_rsquared('cs'))
        except Exception:
            pass
    return out


def run_mixed_models_bundle(max_attempt_no: int = 15, spec: str = 'stable') -> Dict[str, Any]:
    df = build_model_dataframe(max_attempt_no=max_attempt_no)
    if len(df) == 0:
        return {'status': 'empty', 'message': 'No attempt rows for mixed models.'}
    stable = spec != 'full'
    include_interaction = spec == 'full'
    prep = _prepare_common_covariates(df)
    vif_df = _compute_vif_table(prep)
    vif_rows = vif_df.to_dict('records') if len(vif_df) else []

    meta = {
        'n_rows': int(len(df)),
        'n_users': int(df['user_id'].nunique(dropna=True)),
        'n_targets': int(df['target_color_id'].nunique(dropna=True)),
        'max_attempt_no': int(max_attempt_no),
        'perfect_ratio_rate': _json_float(pd.to_numeric(df['perfect_ratio'], errors='coerce').mean()),
        'spec': spec,
    }

    out: Dict[str, Any] = {
        'status': 'success',
        'meta': meta,
        'vif': vif_rows,
        'notes': [],
        'text_summaries': {},
        'continuous': None,
        'similarity': None,
        'perfect_ratio': None,
    }

    if meta['n_users'] < 5:
        out['notes'].append(
            'Few distinct users: models use OLS/GLM with HC3 robust SEs instead of full mixed-effects RE.'
        )

    try:
        cont = fit_continuous_lmm(df, stable_spec=stable, include_interaction=include_interaction)
        out['continuous'] = {
            'meta': _model_fit_meta(cont, kind='ols'),
            'coefs': _ols_coef_rows(cont),
        }
        out['text_summaries']['continuous_lmm'] = str(cont.summary())
    except Exception as e:
        out['continuous'] = {'error': str(e)}

    try:
        sim = fit_similarity_lmm(df)
        out['similarity'] = {
            'meta': _model_fit_meta(sim, kind='ols'),
            'coefs': _ols_coef_rows(sim),
        }
        out['text_summaries']['similarity_lmm'] = str(sim.summary())
    except Exception as e:
        out['similarity'] = {'error': str(e)}

    try:
        binm = fit_perfect_ratio_glmm(df)
        if hasattr(binm, 'conf_int') and hasattr(binm, 'params'):
            ort = _odds_ratio_table_from_glm(binm)
            or_rows = ort.to_dict('records')
            for r in or_rows:
                for k in list(r.keys()):
                    if k != 'term':
                        r[k] = _json_float(r[k]) if k != 'term' else r[k]
        else:
            or_rows = []
        pr_meta = _model_fit_meta(binm, kind='glm_binomial')
        out['perfect_ratio'] = {'meta': pr_meta, 'odds_ratios': or_rows}
        out['text_summaries']['perfect_ratio_glmm'] = str(binm.summary())
    except Exception as e:
        out['perfect_ratio'] = {'error': str(e)}

    return out


def get_mixed_models_summary(max_attempt_no: int = 15, spec: str = 'stable') -> Dict[str, Any]:
    global _cache_ts, _cache_key, _cache_payload
    key = (int(max_attempt_no), str(spec))
    now = time.time()
    with _cache_lock:
        if _cache_payload is not None and _cache_key == key and (now - _cache_ts) <= CACHE_TTL_SEC:
            return _cache_payload
    payload = run_mixed_models_bundle(max_attempt_no=max_attempt_no, spec=spec)
    with _cache_lock:
        _cache_payload = payload
        _cache_key = key
        _cache_ts = time.time()
    return payload


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _coef_forest_png(title: str, coef_rows: List[Dict[str, Any]], *, log_scale: bool = False) -> bytes:
    rows = [r for r in coef_rows if r.get('term') and str(r['term']) != 'Intercept']
    if not rows:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, 'No coefficients to plot', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    terms = [str(r['term']) for r in rows]
    coefs = np.array([float(r['coef'] or 0) for r in rows])
    lo = np.array([float(r['ci_low']) if r.get('ci_low') is not None else np.nan for r in rows])
    hi = np.array([float(r['ci_high']) if r.get('ci_high') is not None else np.nan for r in rows])
    err_lo = coefs - lo
    err_hi = hi - coefs
    y = np.arange(len(terms))
    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.35 * len(terms) + 1.2)))
    ax.errorbar(coefs, y, xerr=[err_lo, err_hi], fmt='o', color='#2563eb', ecolor='#64748b', capsize=3, markersize=5)
    ax.axvline(0, color='#94a3b8', linestyle='--', linewidth=1)
    ax.set_yticks(y)
    ax.set_yticklabels(terms, fontsize=8)
    ax.set_xlabel('Coefficient (95% CI)')
    ax.set_title(title)
    if log_scale:
        ax.set_xscale('symlog', linthresh=1e-6)
    fig.tight_layout()
    return _fig_to_png(fig)


def _vif_bar_png(vif_rows: List[Dict[str, Any]]) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 3.5))
    if not vif_rows:
        ax.text(0.5, 0.5, 'No VIF data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    names = [str(r.get('feature', '')) for r in vif_rows]
    vals = [float(r.get('vif', 0) or 0) for r in vif_rows]
    ax.barh(names[::-1], vals[::-1], color='#0f766e', edgecolor='white')
    ax.axvline(5.0, color='#dc2626', linestyle='--', linewidth=1, label='VIF=5')
    ax.set_xlabel('Variance inflation factor')
    ax.set_title('Predictor collinearity (VIF)')
    ax.legend(loc='lower right', fontsize=8)
    fig.tight_layout()
    return _fig_to_png(fig)


def _or_forest_png(title: str, or_rows: List[Dict[str, Any]]) -> bytes:
    rows = [r for r in or_rows if r.get('term') and str(r['term']) != 'Intercept']
    if not rows:
        fig, ax = plt.subplots(figsize=(8, 3))
        ax.text(0.5, 0.5, 'No odds ratios to plot', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    terms = [str(r['term']) for r in rows]
    ors = np.array([max(float(r.get('odds_ratio') or 1), 1e-12) for r in rows])
    lo = np.array([max(float(r.get('ci_low_or') or 1e-12), 1e-12) for r in rows])
    hi = np.array([max(float(r.get('ci_high_or') or 1e-12), 1e-12) for r in rows])
    y = np.arange(len(terms))
    fig, ax = plt.subplots(figsize=(9, max(3.5, 0.35 * len(terms) + 1.2)))
    ax.scatter(ors, y, color='#7c3aed', s=36, zorder=3)
    for yi, o, a, b in zip(y, ors, lo, hi):
        ax.plot([a, b], [yi, yi], color='#94a3b8', linewidth=2, zorder=1)
    ax.axvline(1.0, color='#16a34a', linestyle='--', linewidth=1)
    ax.set_xscale('log')
    ax.set_yticks(y)
    ax.set_yticklabels(terms, fontsize=8)
    ax.set_xlabel('Odds ratio (log scale, 95% CI)')
    ax.set_title(title)
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_mixed_models_coef_logde(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    data = get_mixed_models_summary()
    cont = (data or {}).get('continuous') or {}
    coefs = cont.get('coefs') or []
    return _coef_forest_png('log₁p(final ΔE): standardized coefficients (HC3)', coefs)


def plot_mixed_models_coef_similarity(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    data = get_mixed_models_summary()
    sim = (data or {}).get('similarity') or {}
    coefs = sim.get('coefs') or []
    return _coef_forest_png('logit(similarity): standardized coefficients (HC3)', coefs)


def plot_mixed_models_perfect_ratio_or(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    data = get_mixed_models_summary()
    pr = (data or {}).get('perfect_ratio') or {}
    ors = pr.get('odds_ratios') or []
    return _or_forest_png('Perfect recipe ratio: odds ratios (95% CI)', ors)


def plot_mixed_models_vif(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    data = get_mixed_models_summary()
    vif = (data or {}).get('vif') or []
    return _vif_bar_png(vif)


def export_to_dir(output_dir: Path, max_attempt_no: int = 15, spec: str = 'stable') -> None:
    """CLI: write same artifacts as before."""
    payload = run_mixed_models_bundle(max_attempt_no=max_attempt_no, spec=spec)
    output_dir.mkdir(parents=True, exist_ok=True)
    df = build_model_dataframe(max_attempt_no=max_attempt_no)
    if len(df):
        output_dir.joinpath('model_dataframe_preview.csv').write_text(df.head(200).to_csv(index=False), encoding='utf-8')
    output_dir.joinpath('summary.json').write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    meta = payload.get('meta') or {}
    output_dir.joinpath('meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    vif = payload.get('vif') or []
    if vif:
        pd.DataFrame(vif).to_csv(output_dir / 'vif_table.csv', index=False)
    for key in ('continuous', 'similarity', 'perfect_ratio'):
        block = payload.get(key)
        if isinstance(block, dict) and block.get('coefs'):
            pd.DataFrame(block['coefs']).to_csv(output_dir / f'{key}_coefs.csv', index=False)
        if isinstance(block, dict) and block.get('odds_ratios'):
            pd.DataFrame(block['odds_ratios']).to_csv(output_dir / 'perfect_ratio_odds_ratios.csv', index=False)
    ts = payload.get('text_summaries') or {}
    for fname, txt in ts.items():
        output_dir.joinpath(f'{fname}.txt').write_text(str(txt) + '\n', encoding='utf-8')
