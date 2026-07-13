"""
Plot-ready JSON specs for the /stat dashboard.

Instead of rendering matplotlib PNGs per request, we return small,
JSON-serializable "specs" that the browser draws with Plotly.js. Aggregation
stays server-side (reusing the cached ``get_dataframes()`` bundle and the same
pandas prep as the old matplotlib builders in ``stat_eda.py``); only the final
render moves to the client.

Spec contract (consumed by ``renderPlot`` in templates/stat.html):

    {
      "kind": "bar" | "line" | "scatter" | "strip" | "heatmap",
      "title": str,
      "x_title": str,
      "y_title": str,
      "traces": [ {...trace fields...} ],
      "meta": { ...optional hints (log axis, ref lines, bar mode)... },
      "empty": bool,          # optional; true when there is nothing to draw
      "message": str,         # optional; shown when empty
    }

Charts already covered by ``/api/stat/summary`` (age pyramid, plays-per-user,
attempts-per-color, controlled-by-attempt, recipe similarity, mixed models,
archetypes) are rendered client-side from that payload and are intentionally
NOT duplicated here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db
from .stat_eda import (
    MATCH_PERFECT_DELTA_E,
    _dashboard_attempts_df,
    _ensure_trial_index,
    _events_with_trial,
    _pearson_corr,
    get_dataframes,
)

# Colors mirror the previous matplotlib palette for visual continuity.
_C = {
    'final_de': '#3949ab',
    'log_de': '#00897b',
    'duration': '#6d4c41',
    'daily': '#5c6bc0',
    'bucket': '#6d4c41',
    'trial_de': '#c62828',
    'trial_success': '#2e7d32',
    'trial_dur': '#6a1b9a',
    'oscillation': '#ef6c00',
    'trajectory': '#00695c',
    'steps': '#8a2be2',
    'improving': '#1f8a70',
    'gain': '#2f80ed',
    'stop': '#e76f51',
    'match': '#2a9d8f',
    'deltae_color': '#7c3aed',
    'elapsed_color': '#b45309',
    'scatter': '#1d4ed8',
    'pos': '#2563eb',
    'neg': '#dc2626',
}

_MV_LABELS = {
    'final_delta_e': 'final_dE',
    'duration_sec': 'duration_s',
    'num_steps': 'num_steps',
    'initial_delta_e': 'initial_dE',
}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _num_list(values) -> List[Optional[float]]:
    """numpy/pandas → JSON floats with NaN/inf → None."""
    out: List[Optional[float]] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(None)
            continue
        out.append(f if np.isfinite(f) else None)
    return out


def _empty(title: str, message: str = 'No data') -> Dict[str, Any]:
    return {'kind': 'empty', 'title': title, 'empty': True, 'message': message}


def _hist_bars(values: np.ndarray, *, bins, color: str) -> Dict[str, Any]:
    """Precompute a histogram server-side and return it as a bar trace.

    Precomputing keeps binning identical to the old matplotlib output and the
    payload tiny (bin centers + counts) regardless of row count.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return {'x': [], 'y': [], 'width': None, 'color': color}
    counts, edges = np.histogram(v, bins=bins)
    centers = (edges[:-1] + edges[1:]) / 2.0
    width = float(edges[1] - edges[0]) if len(edges) > 1 else None
    return {
        'x': _num_list(centers),
        'y': [int(c) for c in counts],
        'width': width,
        'color': color,
    }


# --------------------------------------------------------------------------- #
# Group 1 — distributions & learning curves (from the att/ev bundle)
# --------------------------------------------------------------------------- #
def data_fw_hist_final_de(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    s = att['final_delta_e'].dropna().to_numpy(dtype=float)
    return {
        'kind': 'bar',
        'title': 'Histogram: final ΔE',
        'x_title': 'final ΔE',
        'y_title': 'Count',
        'traces': [_hist_bars(s, bins=50, color=_C['final_de'])],
    }


def data_fw_hist_log_de(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    s = att['final_delta_e'].dropna()
    s = s[s > 0]
    lx = np.log10(np.clip(s.to_numpy(dtype=float), 1e-12, None)) if len(s) else np.array([])
    return {
        'kind': 'bar',
        'title': 'Histogram: log10(final ΔE)',
        'x_title': 'log10(final ΔE)',
        'y_title': 'Count',
        'traces': [_hist_bars(lx, bins=40, color=_C['log_de'])],
    }


def data_fw_hist_duration(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    s = att['duration_sec'].dropna()
    s = s[s >= 0]
    if len(s) > 20:
        s = s[s < s.quantile(0.99)]
    return {
        'kind': 'bar',
        'title': 'Histogram: attempt duration',
        'x_title': 'duration (s)',
        'y_title': 'Count',
        'traces': [_hist_bars(s.to_numpy(dtype=float), bins=40, color=_C['duration'])],
    }


def data_daily_volume(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    ts = att['attempt_started_server_ts'].dropna()
    if len(ts) == 0:
        return _empty('Daily attempt volume (UTC)')
    vc = ts.dt.floor('D').value_counts().sort_index()
    return {
        'kind': 'line',
        'title': 'Daily attempt volume (UTC)',
        'x_title': 'Day',
        'y_title': 'Attempts',
        'traces': [{
            'x': [d.isoformat() for d in vc.index],
            'y': [int(v) for v in vc.values],
            'color': _C['daily'],
            'markers': False,
        }],
        'meta': {'x_is_date': True},
    }


def data_user_bucket(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    u = att.groupby('user_id', dropna=True).size()
    u = u[u.index.notna()]
    if len(u) == 0:
        return _empty('User activity buckets', 'No per-user counts')

    def bucket(n):
        if n == 1:
            return '1'
        if n <= 5:
            return '2–5'
        if n <= 10:
            return '6–10'
        if n <= 25:
            return '11–25'
        return '26+'

    cats = ['1', '2–5', '6–10', '11–25', '26+']
    bc = u.map(bucket).value_counts().reindex(cats, fill_value=0)
    return {
        'kind': 'bar',
        'title': 'User activity buckets',
        'x_title': 'Lifetime attempts / user',
        'y_title': 'Users',
        'traces': [{'x': cats, 'y': [int(v) for v in bc.values], 'color': _C['bucket']}],
    }


def _trial_line(att, col, agg, *, scale=1.0, title, y_title, color) -> Dict[str, Any]:
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _empty(title)
    g = getattr(a.groupby('trial_index', sort=True)[col], agg)().head(60)
    return {
        'kind': 'line',
        'title': title,
        'x_title': 'Trial index (per user)',
        'y_title': y_title,
        'traces': [{
            'x': [int(i) for i in g.index],
            'y': _num_list(g.values * scale),
            'color': color,
            'markers': True,
        }],
    }


def data_fw_trial_median_de(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    return _trial_line(
        att, 'final_delta_e', 'median',
        title='Learning: median final ΔE by trial',
        y_title='Median final ΔE', color=_C['trial_de'],
    )


def data_fw_trial_success(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _empty('Success rate by trial')
    succ = (
        (a['final_delta_e'].fillna(99) <= MATCH_PERFECT_DELTA_E)
        | (a['end_reason'] == 'saved_match')
    ).astype(float)
    g = a.assign(_s=succ).groupby('trial_index', sort=True)['_s'].mean().head(60)
    spec = {
        'kind': 'line',
        'title': 'Success rate by trial',
        'x_title': 'Trial index',
        'y_title': 'Success rate (%)',
        'traces': [{
            'x': [int(i) for i in g.index],
            'y': _num_list(g.values * 100.0),
            'color': _C['trial_success'],
            'markers': True,
        }],
        'meta': {'y_range': [0, 100]},
    }
    return spec


def data_fw_trial_dur(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    return _trial_line(
        att, 'duration_sec', 'median',
        title='Median duration by trial',
        y_title='Median duration (s)', color=_C['trial_dur'],
    )


def data_h1_steps(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    return _trial_line(
        att, 'num_steps', 'mean',
        title='Avg steps to completion by trial index',
        y_title='Mean num_steps', color=_C['steps'],
    )


def data_fw_oscillation(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    if len(ev) == 0:
        return _empty('Oscillation / reversal per attempt')
    df = ev.sort_values(['attempt_uuid', 'seq']).copy()
    df['gain'] = df['delta_e_before'] - df['delta_e_after']
    df['prev'] = df.groupby('attempt_uuid', sort=False)['gain'].shift(1)

    def flip(pr, g):
        if pd.isna(pr) or pd.isna(g) or pr == 0 or g == 0:
            return 0
        return int((pr > 0 and g < 0) or (pr < 0 and g > 0))

    df['_f'] = [flip(p, g) for p, g in zip(df['prev'], df['gain'])]
    osc = df.groupby('attempt_uuid', sort=False)['_f'].sum().clip(upper=15)
    counts = osc.value_counts().reindex(range(0, 16), fill_value=0).sort_index()
    return {
        'kind': 'bar',
        'title': 'Oscillation / reversal per attempt',
        'x_title': 'Sign-change count (capped 15)',
        'y_title': 'Attempts',
        'traces': [{
            'x': [int(i) for i in counts.index],
            'y': [int(v) for v in counts.values],
            'color': _C['oscillation'],
        }],
    }


def data_fw_trajectory(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    if len(ev) == 0:
        return _empty('Typical trajectory shape')
    df = ev.copy()
    mx = df.groupby('attempt_uuid', sort=False)['step_index'].transform('max')
    df['t_norm'] = np.where(mx > 0, df['step_index'] / mx, np.nan)
    df = df[(df['t_norm'] >= 0) & (df['t_norm'] <= 1)]
    if len(df) == 0:
        return _empty('Typical trajectory shape')
    df['dec'] = pd.cut(df['t_norm'], bins=np.linspace(0, 1, 11), include_lowest=True)
    g = df.groupby('dec', observed=True)['delta_e_after'].mean()
    return {
        'kind': 'line',
        'title': 'Typical trajectory shape',
        'x_title': 'Path decile (0=start → end)',
        'y_title': 'Mean ΔE after',
        'traces': [{
            'x': [int(i) for i in range(len(g))],
            'y': _num_list(g.values),
            'color': _C['trajectory'],
            'markers': True,
        }],
    }


# --------------------------------------------------------------------------- #
# per-color distributions (swarm → Plotly strip)
# --------------------------------------------------------------------------- #
def _per_color_strip(
    att: pd.DataFrame, metric: str, *, title, y_title, color,
    log_y: bool = False, ref_line: Optional[float] = None,
    per_category_cap: int = 350,
) -> Dict[str, Any]:
    att = att[att[metric].notna()].copy()
    if len(att) == 0:
        return _empty(title)
    counts = att.groupby('target_name', dropna=False).size().sort_values(ascending=False).head(20)
    order = [str(c) for c in counts.index]
    groups: List[Dict[str, Any]] = []
    for name in order:
        vals = pd.to_numeric(att.loc[att['target_name'] == name, metric], errors='coerce').dropna()
        if len(vals) == 0:
            continue
        if len(vals) > per_category_cap:
            vals = vals.sample(per_category_cap, random_state=42)
        y = vals.to_numpy(dtype=float)
        groups.append({
            'name': name,
            'y': _num_list(y),
            'median': float(np.nanmedian(y)),
            'mean': float(np.nanmean(y)),
        })
    if not groups:
        return _empty(title)
    return {
        'kind': 'strip',
        'title': title,
        'x_title': 'Target color',
        'y_title': y_title,
        'order': order,
        'groups': groups,
        'point_color': color,
        'meta': {'log_y': bool(log_y), 'ref_line': ref_line},
    }


def data_deltae_per_color(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    d = _dashboard_attempts_df()
    return _per_color_strip(
        d, 'final_delta_e',
        title='Final ΔE per color (top 20 by volume, log Y)',
        y_title='Final ΔE (log scale)', color=_C['deltae_color'],
        log_y=True, ref_line=2.0,
    )


def data_elapsed_per_color(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    d = _dashboard_attempts_df()
    d = d[d['duration_sec'].notna() & (d['duration_sec'] <= 300)]
    return _per_color_strip(
        d, 'duration_sec',
        title='Elapsed time per color (≤300s, top 20 by volume)',
        y_title='Elapsed time (s)', color=_C['elapsed_color'],
    )


def data_deltae_elapsed_scatter(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    d = _dashboard_attempts_df()
    d = d[d['final_delta_e'].notna() & d['duration_sec'].notna() & (d['duration_sec'] <= 300)]
    if len(d) == 0:
        return _empty('Final ΔE vs elapsed time')
    if len(d) > 15000:
        d = d.sample(15000, random_state=42)
    x = pd.to_numeric(d['duration_sec'], errors='coerce').to_numpy(dtype=float)
    y = pd.to_numeric(d['final_delta_e'], errors='coerce').to_numpy(dtype=float)
    return {
        'kind': 'scatter',
        'title': 'Final ΔE vs elapsed time (log Y)',
        'x_title': 'Elapsed time (s, ≤300)',
        'y_title': 'Final ΔE (log scale)',
        'traces': [{'x': _num_list(x), 'y': _num_list(y), 'color': _C['scatter'], 'opacity': 0.2}],
        'meta': {'log_y': True},
    }


# --------------------------------------------------------------------------- #
# Group 2 — step-level hypotheses & correlations
# --------------------------------------------------------------------------- #
def _bucket_bar(m, values_col, agg, *, bins, labels, title, x_title, y_title, color,
                scale=1.0, y_range=None) -> Dict[str, Any]:
    m = m.copy()
    m['b'] = pd.cut(m['delta_e_before'], bins=bins, labels=labels)
    g = getattr(m.groupby('b', observed=True)[values_col], agg)().reindex(labels, fill_value=np.nan)
    spec = {
        'kind': 'bar',
        'title': title,
        'x_title': x_title,
        'y_title': y_title,
        'traces': [{'x': list(labels), 'y': _num_list(g.values * scale), 'color': color}],
    }
    if y_range:
        spec['meta'] = {'y_range': y_range}
    return spec


_DIFF_BINS = [-np.inf, 1, 2, 4, 8, np.inf]
_DIFF_LABELS = ['[0,1)', '[1,2)', '[2,4)', '[4,8)', '[8,+)']
_TIME_ORDER = ['first_step', '<1s', '1–3s', '3–7s', '7s+']


def data_h1_improving(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _empty('Step-level: improving rate by trial')
    g = m.groupby('trial_index', sort=True)['improving'].mean().head(60)
    return {
        'kind': 'line',
        'title': 'Step-level: improving rate by trial',
        'x_title': 'Trial index',
        'y_title': 'Improving step rate (%)',
        'traces': [{
            'x': [int(i) for i in g.index],
            'y': _num_list(g.values * 100.0),
            'color': _C['improving'],
            'markers': True,
        }],
        'meta': {'y_range': [0, 100]},
    }


def data_h2_improving(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _empty('Improving rate by difficulty (ΔE before)')
    return _bucket_bar(
        m, 'improving', 'mean', bins=_DIFF_BINS, labels=_DIFF_LABELS,
        title='Improving rate by difficulty (ΔE before)',
        x_title='ΔE before (bucket)', y_title='Improving (%)',
        color=_C['improving'], scale=100.0, y_range=[0, 100],
    )


def data_h2_gain(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _empty('Mean step gain by ΔE before')
    return _bucket_bar(
        m, 'gain', 'mean', bins=_DIFF_BINS, labels=_DIFF_LABELS,
        title='Mean step gain by ΔE before',
        x_title='ΔE before (bucket)', y_title='Mean step gain (ΔE)',
        color=_C['gain'],
    )


def _time_bucket_bar(att, ev, values_col, agg, *, title, y_title, color, scale=1.0, y_range=None):
    m = _events_with_trial(att, ev).copy()
    if len(m) == 0:
        return _empty(title)

    def tb(x):
        if pd.isna(x):
            return 'first_step'
        if x < 1000:
            return '<1s'
        if x < 3000:
            return '1–3s'
        if x < 7000:
            return '3–7s'
        return '7s+'

    m['tb'] = m['time_since_prev_step_ms'].map(tb)
    g = getattr(m.groupby('tb', sort=False)[values_col], agg)().reindex(_TIME_ORDER, fill_value=np.nan)
    spec = {
        'kind': 'bar',
        'title': title,
        'x_title': 'Decision time (bucket)',
        'y_title': y_title,
        'traces': [{'x': list(_TIME_ORDER), 'y': _num_list(g.values * scale), 'color': color}],
    }
    if y_range:
        spec['meta'] = {'y_range': y_range}
    return spec


def data_h4_improving(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    return _time_bucket_bar(
        att, ev, 'improving', 'mean',
        title='Improving rate by decision time', y_title='Improving (%)',
        color=_C['improving'], scale=100.0, y_range=[0, 100],
    )


def data_h4_gain(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    return _time_bucket_bar(
        att, ev, 'gain', 'mean',
        title='Mean gain by decision time', y_title='Mean step gain',
        color=_C['gain'],
    )


def data_h5_stop_success(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    a = att[att['end_reason'].notna()].copy()
    if len(a) == 0:
        return _empty('Outcome mix by final ΔE bucket')

    def band(x):
        if pd.isna(x):
            return 'unknown'
        if x < 1:
            return '[0,1)'
        if x < 2:
            return '[1,2)'
        if x < 4:
            return '[2,4)'
        if x < 8:
            return '[4,8)'
        return '[8,+)'

    order = ['[0,1)', '[1,2)', '[2,4)', '[4,8)', '[8,+)', 'unknown']
    a['b'] = a['final_delta_e'].map(band)
    stop = a.groupby('b', sort=False)['end_reason'].apply(lambda s: (s == 'saved_stop').mean()).reindex(order, fill_value=np.nan)
    ok = a.groupby('b', sort=False)['end_reason'].apply(lambda s: (s == 'saved_match').mean()).reindex(order, fill_value=np.nan)
    return {
        'kind': 'bar',
        'title': 'Outcome mix by final ΔE bucket',
        'x_title': 'Final ΔE bucket',
        'y_title': 'Share (%)',
        'traces': [
            {'name': 'saved_stop', 'x': order, 'y': _num_list(stop.values * 100.0), 'color': _C['stop']},
            {'name': 'saved_match', 'x': order, 'y': _num_list(ok.values * 100.0), 'color': _C['match']},
        ],
        'meta': {'barmode': 'group', 'y_range': [0, 100]},
    }


def _scatter_with_corr(df, x_col, y_col, *, x_label, y_label, title,
                       x_clip_q=None, y_clip_q=None) -> Dict[str, Any]:
    if len(df) == 0:
        return _empty(title)
    part = df[[x_col, y_col]].copy()
    part[x_col] = pd.to_numeric(part[x_col], errors='coerce')
    part[y_col] = pd.to_numeric(part[y_col], errors='coerce')
    part = part.dropna()
    if len(part) == 0:
        return _empty(title)
    if x_clip_q is not None and len(part) > 20:
        part = part[part[x_col] <= part[x_col].quantile(float(x_clip_q))]
    if y_clip_q is not None and len(part) > 20:
        part = part[part[y_col] <= part[y_col].quantile(float(y_clip_q))]
    if len(part) > 18000:
        part = part.sample(18000, random_state=42)
    r = _pearson_corr(part[x_col], part[y_col])
    rt = 'n/a' if r is None else f'{r:.3f}'
    return {
        'kind': 'scatter',
        'title': f'{title} (Pearson r={rt})',
        'x_title': x_label,
        'y_title': y_label,
        'traces': [{
            'x': _num_list(part[x_col].to_numpy(dtype=float)),
            'y': _num_list(part[y_col].to_numpy(dtype=float)),
            'color': _C['scatter'], 'opacity': 0.2,
        }],
    }


def data_scatter_deltae_vs_steps(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    d = _dashboard_attempts_df()
    d = d[d['final_delta_e'].notna() & d['duration_sec'].notna()]
    if 'num_steps' not in d.columns:
        with db.engine.connect() as conn:
            d = pd.read_sql(text(
                "SELECT final_delta_e, duration_sec, num_steps FROM mixing_attempts "
                "WHERE final_delta_e IS NOT NULL AND num_steps IS NOT NULL"
            ), conn)
    return _scatter_with_corr(
        d, 'num_steps', 'final_delta_e',
        x_label='Num steps', y_label='Final ΔE',
        title='Final ΔE vs number of steps', x_clip_q=0.995, y_clip_q=0.995,
    )


def data_scatter_duration_vs_steps(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    with db.engine.connect() as conn:
        d = pd.read_sql(text(
            "SELECT duration_sec, num_steps FROM mixing_attempts "
            "WHERE duration_sec IS NOT NULL AND num_steps IS NOT NULL"
        ), conn)
    return _scatter_with_corr(
        d, 'num_steps', 'duration_sec',
        x_label='Num steps', y_label='Elapsed time (s)',
        title='Elapsed time vs number of steps', x_clip_q=0.995, y_clip_q=0.995,
    )


def _corr_frame() -> pd.DataFrame:
    with db.engine.connect() as conn:
        df = pd.read_sql(text(
            "SELECT final_delta_e, duration_sec, num_steps, initial_delta_e FROM mixing_attempts "
            "WHERE final_delta_e IS NOT NULL OR duration_sec IS NOT NULL "
            "OR num_steps IS NOT NULL OR initial_delta_e IS NOT NULL"
        ), conn)
    return df.apply(pd.to_numeric, errors='coerce')


def data_correlation_heatmap(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    num = _corr_frame()
    if len(num) == 0:
        return _empty('Correlation heatmap')
    base_cols = [c for c in ['final_delta_e', 'duration_sec', 'num_steps'] if c in num.columns]
    present = [c for c in base_cols if num[c].notna().sum() >= 3 and num[c].nunique(dropna=True) >= 2]
    if 'initial_delta_e' in num.columns:
        s = num['initial_delta_e']
        if s.notna().sum() >= 3 and s.nunique(dropna=True) >= 2:
            present.append('initial_delta_e')
    if len(present) < 2:
        return _empty('Correlation heatmap', 'Not enough populated metrics')
    corr = num[present].corr(numeric_only=True)
    if corr.empty:
        return _empty('Correlation heatmap', 'Empty correlation matrix')
    cols = list(corr.columns)
    names = [_MV_LABELS.get(c, c) for c in cols]
    z = [[None if not np.isfinite(v) else round(float(v), 4) for v in row] for row in corr.values]
    return {
        'kind': 'heatmap',
        'title': 'Correlation heatmap (attempt-level metrics)',
        'x_title': '',
        'y_title': '',
        'x': names,
        'y': names,
        'z': z,
        'meta': {'zmin': -1, 'zmax': 1, 'colorscale': 'RdBu', 'reversescale': True, 'annotate': True},
    }


def data_correlation_league(att: pd.DataFrame, ev: pd.DataFrame) -> Dict[str, Any]:
    num = _corr_frame()
    if len(num) == 0 or 'final_delta_e' not in num.columns:
        return _empty('Correlation league')
    if num['final_delta_e'].notna().sum() < 3 or num['final_delta_e'].nunique(dropna=True) < 2:
        return _empty('Correlation league', 'Not enough final_delta_e variation')
    candidates = [c for c in ['duration_sec', 'num_steps'] if c in num.columns]
    if 'initial_delta_e' in num.columns:
        s = num['initial_delta_e']
        if s.notna().sum() >= 3 and s.nunique(dropna=True) >= 2:
            candidates.append('initial_delta_e')
    rows = []
    for c in candidates:
        r = _pearson_corr(num['final_delta_e'], num[c])
        if r is not None:
            rows.append((_MV_LABELS.get(c, c), float(r)))
    if not rows:
        return _empty('Correlation league', 'No valid pairwise correlations')
    rows.sort(key=lambda t: abs(t[1]), reverse=True)
    names = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    colors = [_C['neg'] if v < 0 else _C['pos'] for v in vals]
    return {
        'kind': 'bar',
        'title': 'Correlation league (ranked by |r|)',
        'x_title': 'Pearson r with final_dE',
        'y_title': '',
        'traces': [{'x': vals, 'y': names, 'colors': colors}],
        'meta': {'orientation': 'h', 'x_range': [-1, 1]},
    }


# --------------------------------------------------------------------------- #
# section registry
# --------------------------------------------------------------------------- #
CORE_PLOTS: Dict[str, Any] = {
    'fw_hist_final_de': data_fw_hist_final_de,
    'fw_hist_duration': data_fw_hist_duration,
    'daily_volume': data_daily_volume,
    'user_bucket': data_user_bucket,
    'fw_trial_median_de': data_fw_trial_median_de,
    'fw_trial_success': data_fw_trial_success,
    'fw_trial_dur': data_fw_trial_dur,
    'h1_steps': data_h1_steps,
    'fw_trajectory': data_fw_trajectory,
    'deltae_per_color': data_deltae_per_color,
    'elapsed_per_color': data_elapsed_per_color,
    'deltae_elapsed_scatter': data_deltae_elapsed_scatter,
}

ANALYSIS_PLOTS: Dict[str, Any] = {
    'h1_improving': data_h1_improving,
    'h2_improving': data_h2_improving,
    'h2_gain': data_h2_gain,
    'h4_improving': data_h4_improving,
    'h4_gain': data_h4_gain,
    'h5_stop_success': data_h5_stop_success,
    'scatter_deltae_vs_steps': data_scatter_deltae_vs_steps,
    'scatter_duration_vs_steps': data_scatter_duration_vs_steps,
    'correlation_heatmap': data_correlation_heatmap,
    'correlation_league': data_correlation_league,
}

SECTIONS: Dict[str, Dict[str, Any]] = {
    'core': CORE_PLOTS,
    'analysis': ANALYSIS_PLOTS,
}


def build_section(section: str) -> Dict[str, Any]:
    """Compute every Plotly spec for a dashboard section in one bundle pass."""
    builders = SECTIONS.get(section)
    if builders is None:
        raise ValueError(f'unknown chart section: {section}')
    att, ev = get_dataframes()
    out: Dict[str, Any] = {}
    for plot_id, fn in builders.items():
        try:
            out[plot_id] = fn(att, ev)
        except Exception as exc:  # keep one bad chart from killing the section
            out[plot_id] = {'kind': 'empty', 'title': plot_id, 'empty': True,
                            'message': f'error: {exc}'}
    return out
