"""
Server-side EDA for /stat: pandas wrangling + matplotlib (Agg) PNG figures.
"""
from __future__ import annotations

import io
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sqlalchemy import text

from . import db

MATCH_PERFECT_DELTA_E = 0.01
CACHE_TTL_SEC = int(os.environ.get('STAT_EDA_CACHE_SECONDS', '120'))
EVENTS_ROW_CAP = int(os.environ.get('STAT_EDA_EVENTS_MAX_ROWS', '250000'))

_bundle_ts: float = 0.0
_bundle: Optional[Tuple[pd.DataFrame, pd.DataFrame]] = None
_bundle_lock = threading.Lock()


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _swarm_offsets(
    y_values: np.ndarray,
    *,
    half_bin_x: float = 0.38,
    n_bins: int = 60,
) -> np.ndarray:
    """
    Beeswarm-style x-offsets for categorical scatter so points don't pile on top.
    For each y-bin, points are placed at alternating left/right offsets (sym).
    Returns offsets in the same units as half_bin_x (max |offset| <= half_bin_x).
    """
    y = np.asarray(y_values, dtype=float)
    n = len(y)
    if n == 0:
        return np.zeros(0, dtype=float)
    finite = np.isfinite(y)
    if not finite.any():
        return np.zeros(n, dtype=float)
    yf = y[finite]
    y_min = float(yf.min())
    y_max = float(yf.max())
    if y_max <= y_min:
        bin_idx = np.zeros(n, dtype=int)
    else:
        bin_idx = np.clip(
            ((y - y_min) / (y_max - y_min) * (n_bins - 1)).astype(int),
            0,
            n_bins - 1,
        )
    bin_idx = np.where(finite, bin_idx, -1)
    offsets = np.zeros(n, dtype=float)
    for b in np.unique(bin_idx):
        if b < 0:
            continue
        idxs = np.where(bin_idx == b)[0]
        for k, idx in enumerate(idxs):
            sign = 1 if (k % 2 == 0) else -1
            offsets[idx] = sign * ((k + 1) // 2)
    max_off = float(max(np.abs(offsets).max(), 1.0))
    return (offsets / max_off) * float(half_bin_x)


def _multivariate_attempt_metrics() -> pd.DataFrame:
    """Return per-attempt numeric metrics used by exploratory multivariate plots."""
    sql = text(
        """
        SELECT
          final_delta_e,
          duration_sec,
          num_steps,
          initial_delta_e
        FROM mixing_attempts
        WHERE final_delta_e IS NOT NULL
          AND duration_sec IS NOT NULL
          AND num_steps IS NOT NULL
        """
    )
    with db.engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    df = df.apply(pd.to_numeric, errors='coerce')
    df = df.dropna(subset=['final_delta_e', 'duration_sec', 'num_steps'])
    df = df[(df['duration_sec'] >= 0) & (df['duration_sec'] <= 600)]
    return df.reset_index(drop=True)


def _select_mv_columns(df: pd.DataFrame) -> List[str]:
    """Pick metric columns with enough non-null variation for multivariate plots."""
    candidates = ['final_delta_e', 'duration_sec', 'num_steps', 'initial_delta_e']
    cols: List[str] = []
    for c in candidates:
        if c not in df.columns:
            continue
        s = pd.to_numeric(df[c], errors='coerce')
        if s.notna().sum() >= 5 and s.nunique(dropna=True) >= 2:
            cols.append(c)
    return cols


_MV_LABEL_MAP = {
    'final_delta_e': 'final_dE',
    'duration_sec': 'duration_s',
    'num_steps': 'num_steps',
    'initial_delta_e': 'initial_dE',
}


def get_dataframes() -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (attempts_df, events_df) with shared TTL cache."""
    global _bundle_ts, _bundle
    if _bundle is not None and (time.time() - _bundle_ts) <= CACHE_TTL_SEC:
        return _bundle

    with _bundle_lock:
        if _bundle is not None and (time.time() - _bundle_ts) <= CACHE_TTL_SEC:
            return _bundle

        att_sql = text(
            """
            SELECT
              attempt_uuid,
              user_id,
              target_color_id,
              final_delta_e,
              duration_sec,
              num_steps,
              initial_delta_e,
              end_reason,
              attempt_started_server_ts
            FROM mixing_attempts
            """
        )
        ev_sql = text(
            f"""
            SELECT
              attempt_uuid,
              seq,
              step_index,
              event_type,
              state_after_json,
              delta_e_before,
              delta_e_after,
              action_type,
              action_color,
              amount,
              time_since_prev_step_ms
            FROM mixing_attempt_events
            WHERE step_index IS NOT NULL
              AND delta_e_before IS NOT NULL
              AND delta_e_after IS NOT NULL
            ORDER BY attempt_uuid, seq
            LIMIT {int(EVENTS_ROW_CAP)}
            """
        )
        with db.engine.connect() as conn:
            att = pd.read_sql(att_sql, conn)
            ev = pd.read_sql(ev_sql, conn)
        if 'attempt_started_server_ts' in att.columns:
            att['attempt_started_server_ts'] = pd.to_datetime(
                att['attempt_started_server_ts'], utc=True
            )
        _bundle = (att, ev)
        _bundle_ts = time.time()
        return _bundle


def _ensure_trial_index(att: pd.DataFrame) -> pd.DataFrame:
    out = att.sort_values(['user_id', 'attempt_started_server_ts', 'attempt_uuid'], na_position='last')
    out = out[out['user_id'].notna()].copy()
    out['trial_index'] = out.groupby('user_id', sort=False).cumcount() + 1
    return out


def _events_with_trial(att: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    a = _ensure_trial_index(att)[['attempt_uuid', 'trial_index']]
    m = ev.merge(a, on='attempt_uuid', how='inner')
    m['gain'] = m['delta_e_before'] - m['delta_e_after']
    m['improving'] = (m['delta_e_after'] < m['delta_e_before']).astype(float)
    return m


PIGMENT_ORDER = ('red', 'yellow', 'white', 'blue', 'black')


def _zero_state_counts() -> Dict[str, int]:
    return {k: 0 for k in PIGMENT_ORDER}


def _state_to_tuple(counts: Dict[str, int]) -> Tuple[int, int, int, int, int]:
    return tuple(int(counts[k]) for k in PIGMENT_ORDER)


def _state_label(state: Tuple[int, int, int, int, int]) -> str:
    r, y, w, b, k = state
    return f'R{r} Y{y} W{w} B{b} K{k}'


def _state_label_compact(state: Tuple[int, int, int, int, int]) -> str:
    r, y, w, b, k = state
    return f'{r},{y},{w},{b},{k}'


def _build_edge_table_for_attempt(df_attempt: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct directed transitions for one attempt from action rows."""
    if len(df_attempt) == 0:
        return pd.DataFrame(
            columns=[
                'attempt_uuid',
                'seq',
                'from_state',
                'to_state',
                'action_color',
                'action_type',
                'action_label',
                'delta_e_before',
                'delta_e_after',
            ]
        )

    actions = df_attempt[
        df_attempt['event_type'].isin(['action_add', 'action_remove'])
    ].copy()
    if len(actions) == 0:
        return pd.DataFrame(
            columns=[
                'attempt_uuid',
                'seq',
                'from_state',
                'to_state',
                'action_color',
                'action_type',
                'action_label',
                'delta_e_before',
                'delta_e_after',
            ]
        )

    actions = actions.sort_values('seq')
    counts = _zero_state_counts()
    edge_rows = []

    for _, row in actions.iterrows():
        color = str(row.get('action_color') or '').lower().strip()
        action_type = str(row.get('action_type') or '').lower().strip()
        if color not in counts:
            continue
        if action_type not in ('add', 'remove'):
            continue

        before_state = _state_to_tuple(counts)
        amount = row.get('amount')
        amount = int(amount) if pd.notna(amount) else 1
        if amount < 1:
            amount = 1

        if action_type == 'add':
            counts[color] += amount
            action_label = f'+{color}'
        else:
            counts[color] -= amount
            action_label = f'-{color}'

        after_state = _state_to_tuple(counts)
        edge_rows.append(
            {
                'attempt_uuid': row.get('attempt_uuid'),
                'seq': row.get('seq'),
                'from_state': before_state,
                'to_state': after_state,
                'action_color': color,
                'action_type': action_type,
                'action_label': action_label,
                'delta_e_before': row.get('delta_e_before'),
                'delta_e_after': row.get('delta_e_after'),
            }
        )

    return pd.DataFrame(edge_rows)


def _best_single_attempt_uuid(ev: pd.DataFrame) -> Optional[str]:
    """Pick one representative attempt with many action rows for first plot."""
    if len(ev) == 0:
        return None
    actions = ev[ev['event_type'].isin(['action_add', 'action_remove'])].copy()
    if len(actions) == 0:
        return None
    counts = actions.groupby('attempt_uuid', dropna=True).size().sort_values(ascending=False)
    if len(counts) == 0:
        return None
    return str(counts.index[0])


def _build_graph_from_edge_df(edge_df: pd.DataFrame):
    import networkx as nx

    g = nx.DiGraph()
    for _, row in edge_df.iterrows():
        g.add_edge(
            row['from_state'],
            row['to_state'],
            action=row.get('action_label'),
            seq=row.get('seq'),
            delta_e_after=row.get('delta_e_after'),
        )
    return g


def _step_layout(
    edge_df: pd.DataFrame,
) -> Tuple[Dict[Tuple[int, int, int, int, int], Tuple[float, float]], int, int]:
    """
    Left-to-right layout: x=first seq where node appears, y=column order.
    Stretches spacing when many nodes share a column or many columns exist.
    Returns (pos, max_col_size, n_cols).
    """
    first_seq: Dict[Tuple[int, int, int, int, int], float] = {}
    for _, row in edge_df.iterrows():
        seq = float(row['seq'])
        u = row['from_state']
        v = row['to_state']
        if u not in first_seq:
            first_seq[u] = seq
        if v not in first_seq:
            first_seq[v] = seq + 0.35

    columns: Dict[int, list] = {}
    for node, x in first_seq.items():
        col = int(np.floor(x))
        columns.setdefault(col, []).append(node)

    max_col_size = max((len(v) for v in columns.values()), default=1)
    n_cols = len(columns)
    x_stretch = float(np.clip(1.15 + 0.07 * max(0, n_cols - 3), 1.15, 2.35))
    y_stretch = float(np.clip(1.2 + 0.28 * max(0, max_col_size - 2), 1.2, 4.2))

    pos: Dict[Tuple[int, int, int, int, int], Tuple[float, float]] = {}
    for col in sorted(columns.keys()):
        nodes = sorted(columns[col], key=lambda s: (sum(s), s))
        n = len(nodes)
        for idx, node in enumerate(nodes):
            y = float((n - 1) / 2.0 - idx) * y_stretch
            pos[node] = (float(col) * x_stretch, y)
    return pos, max_col_size, n_cols


def build_edge_table_for_attempt(df_attempt: pd.DataFrame) -> pd.DataFrame:
    """Public helper for one attempt edge reconstruction."""
    return _build_edge_table_for_attempt(df_attempt)


def build_edge_tables_all_attempts(ev: pd.DataFrame) -> pd.DataFrame:
    """Generalized edge table builder across all attempts."""
    if len(ev) == 0:
        return pd.DataFrame()
    out = []
    for attempt_uuid, part in ev.groupby('attempt_uuid', sort=False):
        edge_df = _build_edge_table_for_attempt(part)
        if len(edge_df):
            out.append(edge_df)
    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


def _resolve_network_attempt_uuid(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    attempt_uuid: Optional[str],
    target_color_id: Optional[int],
) -> Tuple[Optional[str], str]:
    """
    Pick which attempt to draw. Returns (uuid, error_key).
    error_key is empty when uuid is set; otherwise a short reason for empty plots.
    """
    au_in = (attempt_uuid or '').strip()
    if au_in:
        sub = ev[ev['attempt_uuid'].astype(str) == au_in]
        if len(sub) == 0:
            return None, 'unknown_attempt_uuid'
        return au_in, ''
    if target_color_id is not None:
        cand = (
            att.loc[att['target_color_id'] == target_color_id, 'attempt_uuid']
            .dropna()
            .astype(str)
        )
        if len(cand) == 0:
            return None, 'no_attempts_for_target'
        id_set = set(cand.tolist())
        ev_f = ev[ev['attempt_uuid'].astype(str).isin(id_set)]
        pick = _best_single_attempt_uuid(ev_f)
        if pick is None:
            return None, 'no_action_rows_for_target'
        return pick, ''
    pick = _best_single_attempt_uuid(ev)
    if pick is None:
        return None, 'no_actions'
    return pick, ''


def plot_fw_attempt_network(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    *,
    attempt_uuid: Optional[str] = None,
    target_color_id: Optional[int] = None,
) -> bytes:
    """Network view for one attempt (state transitions in pigment space)."""
    fig, ax = plt.subplots(figsize=(14, 7))
    try:
        import networkx as nx
    except Exception as e:
        ax.text(0.5, 0.5, f'networkx not available: {e}', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    resolved, err = _resolve_network_attempt_uuid(att, ev, attempt_uuid, target_color_id)
    if resolved is None:
        msg = {
            'unknown_attempt_uuid': 'Unknown attempt_uuid (no events in sample)',
            'no_attempts_for_target': 'No attempts for this target_color_id',
            'no_action_rows_for_target': 'No action_add/action_remove rows for this target',
            'no_actions': 'No action_add/action_remove rows found',
        }.get(err, err or 'No attempt to show')
        ax.text(0.5, 0.5, msg, ha='center', va='center', fontsize=11)
        ax.axis('off')
        return _fig_to_png(fig)

    ev_attempt = ev[ev['attempt_uuid'].astype(str) == resolved].copy()
    edge_df = _build_edge_table_for_attempt(ev_attempt)
    if len(edge_df) == 0:
        ax.text(
            0.5,
            0.5,
            f'No reconstructable actions for attempt {resolved[:8]}…',
            ha='center',
            va='center',
        )
        ax.axis('off')
        return _fig_to_png(fig)

    g = _build_graph_from_edge_df(edge_df)
    pos, max_col_size, n_cols = _step_layout(edge_df)
    n_nodes = g.number_of_nodes()
    n_edges = g.number_of_edges()

    # Figure size grows with layout extent (reduces visual cramming)
    fig_w = float(np.clip(11.0 + 0.55 * n_cols + 0.12 * n_nodes, 14.0, 30.0))
    fig_h = float(np.clip(6.0 + 0.42 * max_col_size + 0.08 * n_nodes, 7.0, 22.0))
    fig.set_size_inches(fig_w, fig_h)

    crowded = n_nodes >= 14 or n_edges >= 22 or max_col_size >= 7
    very_crowded = n_nodes >= 22 or n_edges >= 35

    node_delta = (
        edge_df.groupby('to_state', dropna=False)['delta_e_after']
        .min()
        .to_dict()
    )
    node_labels = {}
    for n in g.nodes():
        de = node_delta.get(n)
        if very_crowded:
            base = _state_label_compact(n)
            if pd.notna(de):
                node_labels[n] = f'{base}\n{float(de):.1f}'
            else:
                node_labels[n] = base
        elif crowded:
            base = _state_label_compact(n)
            if pd.notna(de):
                node_labels[n] = f'{base}\nΔE {float(de):.1f}'
            else:
                node_labels[n] = base
        else:
            base = _state_label(n)
            if pd.notna(de):
                node_labels[n] = f'{base}\nΔE*={float(de):.2f}'
            else:
                node_labels[n] = base

    edge_labels = {(u, v): d.get('action', '') for u, v, d in g.edges(data=True)}
    node_size = int(np.clip(9000.0 / max(1.0, n_nodes ** 0.55), 320, 2200))
    node_fs = float(np.clip(10.5 - 0.22 * n_nodes, 5.0, 9.0))
    edge_fs = float(np.clip(node_fs - 1.0, 4.0, 7.0))
    arr_sz = int(np.clip(26.0 - 0.35 * n_edges, 10, 22))

    nx.draw_networkx_nodes(
        g, pos, node_size=node_size, node_color='#dbeafe', edgecolors='#1e3a8a', linewidths=0.8, ax=ax
    )
    # Slight arc helps when multiple edges share similar headings
    conn_style = 'arc3,rad=0.08' if n_edges > 12 else 'arc3,rad=0.02'
    nx.draw_networkx_edges(
        g,
        pos,
        arrows=True,
        arrowsize=arr_sz,
        width=float(np.clip(2.2 - 0.02 * n_edges, 0.8, 1.6)),
        edge_color='#334155',
        connectionstyle=conn_style,
        min_source_margin=0.02,
        min_target_margin=0.02,
        ax=ax,
    )
    nx.draw_networkx_labels(g, pos, labels=node_labels, font_size=node_fs, ax=ax)
    if not very_crowded and n_edges <= 48:
        nx.draw_networkx_edge_labels(
            g, pos, edge_labels=edge_labels, font_size=edge_fs, rotate=False, label_pos=0.42, ax=ax
        )
    subtitle = []
    if target_color_id is not None:
        subtitle.append(f'filter target_id={target_color_id}')
    if (attempt_uuid or '').strip():
        subtitle.append('explicit attempt')
    sub = f' — {"; ".join(subtitle)}' if subtitle else ''
    dense_note = (
        ' — edge labels hidden (dense graph)' if (very_crowded or n_edges > 48) else ''
    )
    ax.set_title(f'State network (attempt …{resolved[-8:]}){sub}{dense_note}', fontsize=11)
    ax.margins(0.07, 0.09)
    ax.axis('off')
    return _fig_to_png(fig)


def plot_fw_hist_final_de(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    s = att['final_delta_e'].dropna()
    if len(s):
        ax.hist(s, bins=50, color='#3949ab', edgecolor='white', linewidth=0.5)
    ax.set_xlabel('final ΔE')
    ax.set_ylabel('Count')
    ax.set_title('Histogram: final ΔE')
    return _fig_to_png(fig)


def plot_fw_hist_log_de(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    s = att['final_delta_e'].dropna()
    s = s[s > 0]
    if len(s):
        lx = np.log10(s.clip(lower=1e-12))
        ax.hist(lx, bins=40, color='#00897b', edgecolor='white', linewidth=0.5)
    ax.set_xlabel('log10(final ΔE)')
    ax.set_ylabel('Count')
    ax.set_title('Histogram: log10(final ΔE)')
    return _fig_to_png(fig)


def plot_fw_hist_duration(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    s = att['duration_sec'].dropna()
    s = s[(s >= 0) & (s < s.quantile(0.99) if len(s) > 20 else s >= 0)]
    if len(s):
        ax.hist(s, bins=40, color='#6d4c41', edgecolor='white', linewidth=0.5)
    ax.set_xlabel('duration (s)')
    ax.set_ylabel('Count')
    ax.set_title('Histogram: attempt duration')
    return _fig_to_png(fig)


def plot_daily_volume(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    ts = att['attempt_started_server_ts'].dropna()
    if len(ts):
        d = ts.dt.floor('D')
        vc = d.value_counts().sort_index()
        ax.plot(vc.index, vc.values, color='#5c6bc0', linewidth=2)
        ax.tick_params(axis='x', rotation=45)
    ax.set_ylabel('Attempts')
    ax.set_title('Daily attempt volume (UTC)')
    fig.autofmt_xdate()
    return _fig_to_png(fig)


def plot_user_bucket(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    u = att.groupby('user_id', dropna=True).size()
    u = u[u.index.notna()]
    if len(u) == 0:
        ax.text(0.5, 0.5, 'No per-user counts', ha='center', va='center')
        return _fig_to_png(fig)

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
    ax.bar(range(len(bc)), bc.values, color='#6d4c41', edgecolor='white')
    ax.set_xticks(range(len(bc)))
    ax.set_xticklabels(cats)
    ax.set_xlabel('Lifetime attempts / user')
    ax.set_ylabel('Users')
    ax.set_title('User activity buckets')
    return _fig_to_png(fig)


def plot_fw_trial_median_de(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _fig_to_png(fig)
    g = a.groupby('trial_index', sort=True)['final_delta_e'].median().head(60)
    ax.plot(g.index, g.values, marker='o', color='#c62828', linewidth=2, markersize=4)
    ax.set_xlabel('Trial index (per user)')
    ax.set_ylabel('Median final ΔE')
    ax.set_title('Learning: median final ΔE by trial')
    return _fig_to_png(fig)


def plot_fw_trial_success(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _fig_to_png(fig)
    succ = (
        (a['final_delta_e'].fillna(99) <= MATCH_PERFECT_DELTA_E)
        | (a['end_reason'] == 'saved_match')
    ).astype(float)
    a = a.assign(_s=succ)
    g = a.groupby('trial_index', sort=True)['_s'].mean().head(60)
    ax.plot(g.index, g.values * 100, marker='o', color='#2e7d32', linewidth=2, markersize=4)
    ax.set_ylim(0, 100)
    ax.set_xlabel('Trial index')
    ax.set_ylabel('Success rate (%)')
    ax.set_title('Success rate by trial')
    return _fig_to_png(fig)


def plot_fw_trial_dur(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _fig_to_png(fig)
    g = a.groupby('trial_index', sort=True)['duration_sec'].median().head(60)
    ax.plot(g.index, g.values, marker='o', color='#6a1b9a', linewidth=2, markersize=4)
    ax.set_xlabel('Trial index')
    ax.set_ylabel('Median duration (s)')
    ax.set_title('Median duration by trial')
    return _fig_to_png(fig)


def plot_fw_oscillation(_: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    if len(ev) == 0:
        return _fig_to_png(fig)
    df = ev.sort_values(['attempt_uuid', 'seq']).copy()
    df['gain'] = df['delta_e_before'] - df['delta_e_after']
    df['prev'] = df.groupby('attempt_uuid', sort=False)['gain'].shift(1)

    def flip(pr, g):
        if pd.isna(pr) or pd.isna(g) or pr == 0 or g == 0:
            return 0
        return int((pr > 0 and g < 0) or (pr < 0 and g > 0))

    df['_f'] = [flip(p, g) for p, g in zip(df['prev'], df['gain'])]
    osc = df.groupby('attempt_uuid', sort=False)['_f'].sum().clip(upper=15)
    if len(osc):
        ax.hist(osc, bins=np.arange(-0.5, 16.5, 1), color='#ef6c00', edgecolor='white')
    ax.set_xlabel('Sign-change count (capped 15)')
    ax.set_ylabel('Attempts')
    ax.set_title('Oscillation / reversal per attempt')
    return _fig_to_png(fig)


def plot_fw_trajectory(_: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    if len(ev) == 0:
        return _fig_to_png(fig)
    df = ev.copy()
    mx = df.groupby('attempt_uuid', sort=False)['step_index'].transform('max')
    df['t_norm'] = np.where(mx > 0, df['step_index'] / mx, np.nan)
    df = df[(df['t_norm'] >= 0) & (df['t_norm'] <= 1)]
    if len(df) == 0:
        return _fig_to_png(fig)
    df['dec'] = pd.cut(
        df['t_norm'],
        bins=np.linspace(0, 1, 11),
        include_lowest=True,
    )
    g = df.groupby('dec', observed=True)['delta_e_after'].mean()
    x = range(len(g))
    ax.plot(x, g.values, marker='o', color='#00695c', linewidth=2)
    ax.set_xticks(x)
    ax.set_xticklabels([f'{i}' for i in range(len(g))], fontsize=8)
    ax.set_xlabel('Path decile (0=start → end)')
    ax.set_ylabel('Mean ΔE after')
    ax.set_title('Typical trajectory shape')
    return _fig_to_png(fig)


def plot_h1_improving(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _fig_to_png(fig)
    g = m.groupby('trial_index', sort=True)['improving'].mean().head(60)
    ax.plot(g.index, g.values * 100, marker='o', color='#1f8a70', linewidth=2)
    ax.set_ylim(0, 100)
    ax.set_xlabel('Trial index')
    ax.set_ylabel('Improving step rate (%)')
    ax.set_title('Step-level: improving rate by trial')
    return _fig_to_png(fig)


def plot_h1_steps(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    a = _ensure_trial_index(att)
    if len(a) == 0:
        return _fig_to_png(fig)
    g = a.groupby('trial_index', sort=True)['num_steps'].mean().head(60)
    ax.plot(g.index, g.values, marker='o', color='#8a2be2', linewidth=2)
    ax.set_xlabel('Trial index')
    ax.set_ylabel('Mean num_steps')
    ax.set_title('Avg steps to completion by trial index')
    return _fig_to_png(fig)


def plot_h2_improving(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _fig_to_png(fig)
    bins = [-np.inf, 1, 2, 4, 8, np.inf]
    labels = ['[0,1)', '[1,2)', '[2,4)', '[4,8)', '[8,+)']
    m['b'] = pd.cut(m['delta_e_before'], bins=bins, labels=labels)
    g = m.groupby('b', observed=True)['improving'].mean().reindex(labels, fill_value=np.nan)
    ax.bar(range(len(g)), g.values * 100, color='#1f8a70', edgecolor='white')
    ax.set_xticks(range(len(g)))
    ax.set_xticklabels(labels, rotation=20, ha='right')
    ax.set_ylim(0, 100)
    ax.set_ylabel('Improving (%)')
    ax.set_xlabel('ΔE before (bucket)')
    ax.set_title('Improving rate by difficulty (ΔE before)')
    return _fig_to_png(fig)


def plot_h2_gain(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    m = _events_with_trial(att, ev)
    if len(m) == 0:
        return _fig_to_png(fig)
    bins = [-np.inf, 1, 2, 4, 8, np.inf]
    labels = ['[0,1)', '[1,2)', '[2,4)', '[4,8)', '[8,+)']
    m['b'] = pd.cut(m['delta_e_before'], bins=bins, labels=labels)
    g = m.groupby('b', observed=True)['gain'].mean().reindex(labels, fill_value=np.nan)
    x = np.arange(len(g))
    ax.bar(x, g.values, color='#2f80ed', edgecolor='white')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=20, ha='right')
    ax.set_ylabel('Mean step gain (ΔE)')
    ax.set_title('Mean step gain by ΔE before')
    return _fig_to_png(fig)


def plot_h4_improving(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    m = _events_with_trial(att, ev).copy()
    if len(m) == 0:
        return _fig_to_png(fig)
    t = m['time_since_prev_step_ms']

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

    order = ['first_step', '<1s', '1–3s', '3–7s', '7s+']
    m['tb'] = t.map(tb)
    g = m.groupby('tb', sort=False)['improving'].mean().reindex(order, fill_value=np.nan)
    ax.bar(range(len(g)), g.values * 100, color='#1f8a70', edgecolor='white')
    ax.set_xticks(range(len(g)))
    ax.set_xticklabels(order, rotation=20, ha='right')
    ax.set_ylim(0, 100)
    ax.set_ylabel('Improving (%)')
    ax.set_title('Improving rate by decision time')
    return _fig_to_png(fig)


def plot_h4_gain(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    m = _events_with_trial(att, ev).copy()
    if len(m) == 0:
        return _fig_to_png(fig)
    t = m['time_since_prev_step_ms']

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

    order = ['first_step', '<1s', '1–3s', '3–7s', '7s+']
    m['tb'] = t.map(tb)
    g = m.groupby('tb', sort=False)['gain'].mean().reindex(order, fill_value=np.nan)
    ax.bar(range(len(g)), g.values, color='#2f80ed', edgecolor='white')
    ax.set_xticks(range(len(g)))
    ax.set_xticklabels(order, rotation=20, ha='right')
    ax.set_ylabel('Mean step gain')
    ax.set_title('Mean gain by decision time')
    return _fig_to_png(fig)


def plot_h5_stop_success(att: pd.DataFrame, _: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8, 4))
    a = att[att['end_reason'].notna()].copy()
    if len(a) == 0:
        return _fig_to_png(fig)
    de = a['final_delta_e']

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
    a['b'] = de.map(band)
    stop = a.groupby('b', sort=False)['end_reason'].apply(lambda s: (s == 'saved_stop').mean())
    ok = a.groupby('b', sort=False)['end_reason'].apply(lambda s: (s == 'saved_match').mean())
    stop = stop.reindex(order, fill_value=np.nan)
    ok = ok.reindex(order, fill_value=np.nan)
    x = np.arange(len(order))
    w = 0.35
    ax.bar(x - w / 2, stop.values * 100, width=w, label='saved_stop', color='#e76f51', edgecolor='white')
    ax.bar(x + w / 2, ok.values * 100, width=w, label='saved_match', color='#2a9d8f', edgecolor='white')
    ax.set_xticks(x)
    ax.set_xticklabels(order, rotation=15, ha='right')
    ax.set_ylim(0, 100)
    ax.set_ylabel('Share (%)')
    ax.legend(fontsize=8)
    ax.set_title('Outcome mix by final ΔE bucket')
    return _fig_to_png(fig)


def _dashboard_attempts_df() -> pd.DataFrame:
    sql = text(
        """
        SELECT
          ma.user_id,
          ma.attempt_uuid,
          ma.target_color_id,
          COALESCE(tc.name, '(unknown)') AS target_name,
          tc.r AS target_r,
          tc.g AS target_g,
          tc.b AS target_b,
          ma.final_delta_e,
          ma.duration_sec,
          ma.attempt_started_server_ts
        FROM mixing_attempts ma
        LEFT JOIN target_colors tc ON tc.id = ma.target_color_id
        """
    )
    with db.engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    if 'attempt_started_server_ts' in df.columns:
        df['attempt_started_server_ts'] = pd.to_datetime(df['attempt_started_server_ts'], utc=True)
    return df


def _dashboard_attempts_with_attempt_no() -> pd.DataFrame:
    df = _dashboard_attempts_df()
    if len(df) == 0:
        df['attempt_no'] = pd.Series(dtype='int64')
        return df
    out = df[df['user_id'].notna()].copy()
    if len(out) == 0:
        out['attempt_no'] = pd.Series(dtype='int64')
        return out
    out = out.sort_values(
        ['user_id', 'target_name', 'attempt_started_server_ts', 'attempt_uuid'],
        na_position='last',
    )
    out['attempt_no'] = out.groupby(['user_id', 'target_name'], sort=False).cumcount() + 1
    return out


def plot_age_pyramid(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 5))
    sql = text(
        """
        WITH u AS (
          SELECT
            CASE
              WHEN lower(coalesce(gender, 'unknown')) LIKE 'm%' THEN 'male'
              WHEN lower(coalesce(gender, 'unknown')) LIKE 'f%' THEN 'female'
              ELSE 'other'
            END AS gender_group,
            EXTRACT(YEAR FROM age(CURRENT_DATE, birthdate))::int AS age_years
          FROM users
          WHERE birthdate IS NOT NULL
        )
        SELECT
          CASE
            WHEN age_years < 18 THEN '<18'
            WHEN age_years <= 24 THEN '18-24'
            WHEN age_years <= 34 THEN '25-34'
            WHEN age_years <= 44 THEN '35-44'
            WHEN age_years <= 54 THEN '45-54'
            WHEN age_years <= 64 THEN '55-64'
            ELSE '65+'
          END AS age_bucket,
          gender_group,
          COUNT(*)::bigint AS n_users
        FROM u
        WHERE age_years IS NOT NULL AND age_years >= 0
        GROUP BY age_bucket, gender_group
        """
    )
    with db.engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    if len(df) == 0:
        ax.text(0.5, 0.5, 'No user demographics data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    order = ['<18', '18-24', '25-34', '35-44', '45-54', '55-64', '65+']
    pivot = (
        df.pivot_table(index='age_bucket', columns='gender_group', values='n_users', aggfunc='sum')
        .reindex(order)
        .fillna(0)
    )
    male = -pivot.get('male', pd.Series(0, index=order))
    female = pivot.get('female', pd.Series(0, index=order))
    other = pivot.get('other', pd.Series(0, index=order))

    y = np.arange(len(order))
    ax.barh(y, male.values, color='#2563eb', label='Male')
    ax.barh(y, female.values, color='#dc2626', label='Female')
    if other.sum() > 0:
        ax.barh(y, other.values, color='#6b7280', label='Other')

    max_abs = max(abs(male.min()), abs(female.max()), abs(other.max()), 1)
    ax.set_xlim(-max_abs * 1.2, max_abs * 1.2)
    ax.set_yticks(y)
    ax.set_yticklabels(order)
    ax.set_xlabel('Users')
    ax.set_title('Age pyramid by gender')
    ax.legend(loc='upper right', fontsize=8)
    ticks = ax.get_xticks()
    ax.set_xticklabels([str(int(abs(t))) for t in ticks])
    return _fig_to_png(fig)


def plot_plays_per_user(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_df()
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No plays available', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    per_user = att[att['user_id'].notna()].groupby('user_id', dropna=True).size().sort_values(ascending=False)
    if len(per_user) == 0:
        ax.text(0.5, 0.5, 'No user-linked plays', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    top = per_user.head(30)
    x = np.arange(len(top), dtype=float)
    y = top.to_numpy(dtype=float)
    ax.bar(x, y, color='#64748b', edgecolor='white', linewidth=0.7, alpha=0.95)
    ax.set_xticks(x)
    ax.set_xticklabels(top.index.astype(str), rotation=70, fontsize=7)
    ax.set_ylabel('Plays')
    ax.set_xlabel('User (sorted by play count)')
    ax.set_title('Bar: plays per user (top 30)')
    ax.set_ylim(bottom=0)
    return _fig_to_png(fig)


def plot_attempts_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_df()
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No attempts data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    agg = (
        att.groupby(
            ['target_color_id', 'target_name', 'target_r', 'target_g', 'target_b'],
            dropna=False,
            as_index=False,
        )
        .size()
        .rename(columns={'size': 'n_attempts'})
        .sort_values('n_attempts', ascending=False)
        .head(20)
    )
    if len(agg) == 0:
        ax.text(0.5, 0.5, 'No grouped color rows', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    y = np.arange(len(agg))
    vals = agg['n_attempts'].to_numpy(dtype=float)
    colors = []
    labels = []
    for _, r in agg.iterrows():
        tid = r.get('target_color_id')
        name = str(r.get('target_name') or '(unknown)')
        if pd.notna(tid):
            labels.append(f'id {int(tid)} · {name}')
        else:
            labels.append(f'id ? · {name}')
        rr = pd.to_numeric(r.get('target_r'), errors='coerce')
        gg = pd.to_numeric(r.get('target_g'), errors='coerce')
        bb = pd.to_numeric(r.get('target_b'), errors='coerce')
        if (
            pd.notna(rr) and pd.notna(gg) and pd.notna(bb)
            and 0 <= float(rr) <= 255
            and 0 <= float(gg) <= 255
            and 0 <= float(bb) <= 255
        ):
            colors.append((float(rr) / 255.0, float(gg) / 255.0, float(bb) / 255.0))
        else:
            colors.append('#0f766e')
    ax.barh(y, vals, color=colors, edgecolor='#e5e7eb', linewidth=0.9, alpha=0.95)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel('Attempts')
    ax.set_ylabel('Target color (id + name)')
    ax.set_title('Bar: attempts per target_color_id (top 20)')
    ax.set_xlim(left=0)
    return _fig_to_png(fig)


def _plot_per_color_swarm(
    att: pd.DataFrame,
    *,
    metric: str,
    metric_label: str,
    point_color: str,
    title: str,
    per_category_cap: int = 350,
) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.8))
    if len(att) == 0:
        ax.text(0.5, 0.5, f'No {metric_label} data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    counts = (
        att.groupby('target_name', dropna=False)
        .size()
        .sort_values(ascending=False)
        .head(20)
    )
    order = list(counts.index)
    sub = att[att['target_name'].isin(order)].copy()
    if len(sub) == 0:
        ax.text(0.5, 0.5, f'No {metric_label} rows in top colors', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    q = (
        sub.groupby('target_name', dropna=False)[metric]
        .quantile([0.10, 0.25, 0.50, 0.75, 0.90])
        .unstack()
        .reindex(order)
    )
    q.columns = ['p10', 'p25', 'p50', 'p75', 'p90']
    means = sub.groupby('target_name', dropna=False)[metric].mean().reindex(order)
    x = np.arange(len(order), dtype=float)
    for i, name in enumerate(order):
        row = q.loc[name]
        if row.isna().any():
            continue
        # p10-p90 whisker
        ax.vlines(
            x[i],
            float(row['p10']),
            float(row['p90']),
            color='#94a3b8',
            linewidth=1.2,
            alpha=0.95,
            zorder=1,
        )
        # IQR band
        ax.vlines(
            x[i],
            float(row['p25']),
            float(row['p75']),
            color=point_color,
            linewidth=6.0,
            alpha=0.78,
            zorder=2,
        )
        # Median marker
        ax.plot(
            [x[i] - 0.18, x[i] + 0.18],
            [float(row['p50']), float(row['p50'])],
            color='#111827',
            linewidth=1.4,
            zorder=3,
        )
    ax.scatter(
        x,
        means.to_numpy(dtype=float),
        s=22,
        marker='D',
        color='#111827',
        alpha=0.85,
        zorder=4,
        label='mean',
    )
    ax.set_xticks(x)
    ax.set_xticklabels([str(o) for o in order], rotation=55, ha='right', fontsize=8)
    ax.set_ylabel(metric_label)
    ax.set_xlabel('Target color')
    ax.set_title(title)
    legend_handles = [
        plt.Line2D([0], [0], color='#94a3b8', lw=1.2, label='p10-p90'),
        plt.Line2D([0], [0], color=point_color, lw=6.0, label='IQR (p25-p75)'),
        plt.Line2D([0], [0], color='#111827', lw=1.4, label='median'),
        plt.Line2D([0], [0], marker='D', color='#111827', lw=0, markersize=4.5, label='mean'),
    ]
    ax.legend(handles=legend_handles, fontsize=7.5, loc='upper right')
    return _fig_to_png(fig)


def plot_deltae_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    att = _dashboard_attempts_df()
    att = att[att['final_delta_e'].notna()]
    return _plot_per_color_swarm(
        att,
        metric='final_delta_e',
        metric_label='Final ΔE',
        point_color='#7c3aed',
        title='Quantile band: final ΔE per color (top 20 by volume)',
    )


def plot_elapsed_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    att = _dashboard_attempts_df()
    att = att[att['duration_sec'].notna() & (att['duration_sec'] <= 300)]
    return _plot_per_color_swarm(
        att,
        metric='duration_sec',
        metric_label='Elapsed time (s)',
        point_color='#b45309',
        title='Quantile band: elapsed time per color (<=300s, top 20 by volume)',
    )


def plot_controlled_deltae_by_attempt(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_with_attempt_no()
    att = att[(att['attempt_no'] <= 10) & att['final_delta_e'].notna()]
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No controlled ΔE data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    top_colors = (
        att.groupby('target_name', dropna=False)
        .size()
        .sort_values(ascending=False)
        .head(8)
        .index
    )
    palette = plt.cm.get_cmap('tab10', len(top_colors))
    x_ticks = set()
    for i, target_name in enumerate(top_colors):
        part = att[att['target_name'] == target_name]
        g = part.groupby('attempt_no', sort=True)['final_delta_e'].mean()
        if len(g) == 0:
            continue
        x = g.index.to_numpy()
        y = g.values
        ys = pd.Series(y, index=g.index).rolling(window=3, center=True, min_periods=1).mean()
        ax.plot(x, ys.values, marker='o', linewidth=2, color=palette(i), label=str(target_name))
        x_ticks.update(g.index.tolist())
    if x_ticks:
        ax.set_xticks(sorted(list(x_ticks)))
    ax.set_xlabel('Attempt number within user x color')
    ax.set_ylabel('Mean final ΔE')
    ax.set_title('Controlled final ΔE by attempt number (per target color, smoothed)')
    ax.legend(fontsize=7, ncol=2)
    return _fig_to_png(fig)


def plot_controlled_elapsed_by_attempt(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_with_attempt_no()
    att = att[(att['attempt_no'] <= 10) & att['duration_sec'].notna() & (att['duration_sec'] <= 300)]
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No controlled elapsed-time data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    top_colors = (
        att.groupby('target_name', dropna=False)
        .size()
        .sort_values(ascending=False)
        .head(8)
        .index
    )
    palette = plt.cm.get_cmap('tab10', len(top_colors))
    x_ticks = set()
    for i, target_name in enumerate(top_colors):
        part = att[att['target_name'] == target_name]
        g = part.groupby('attempt_no', sort=True)['duration_sec'].mean()
        if len(g) == 0:
            continue
        x = g.index.to_numpy()
        y = g.values
        ys = pd.Series(y, index=g.index).rolling(window=3, center=True, min_periods=1).mean()
        ax.plot(x, ys.values, marker='o', linewidth=2, color=palette(i), label=str(target_name))
        x_ticks.update(g.index.tolist())
    if x_ticks:
        ax.set_xticks(sorted(list(x_ticks)))
    ax.set_xlabel('Attempt number within user x color')
    ax.set_ylabel('Mean elapsed time (s)')
    ax.set_title('Controlled elapsed time by attempt number (<=300s, per target color, smoothed)')
    ax.legend(fontsize=7, ncol=2)
    return _fig_to_png(fig)


def plot_deltae_elapsed_scatter(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 5))
    att = _dashboard_attempts_df()
    att = att[
        att['final_delta_e'].notna()
        & att['duration_sec'].notna()
        & (att['duration_sec'] <= 300)
    ]
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No DeltaE/time data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    hb = ax.hexbin(
        att['duration_sec'].to_numpy(dtype=float),
        att['final_delta_e'].to_numpy(dtype=float),
        gridsize=46,
        mincnt=1,
        cmap='Blues',
        linewidths=0,
    )
    cbar = fig.colorbar(hb, ax=ax, pad=0.01)
    cbar.set_label('Count', fontsize=8)
    ax.set_xlabel('Elapsed time (s, <=300)')
    ax.set_ylabel('Final DeltaE')
    ax.set_title('Hexbin density: Final DeltaE vs elapsed time')
    return _fig_to_png(fig)


def _pearson_corr(x: pd.Series, y: pd.Series) -> Optional[float]:
    pair = pd.DataFrame({'x': pd.to_numeric(x, errors='coerce'), 'y': pd.to_numeric(y, errors='coerce')}).dropna()
    if len(pair) < 3:
        return None
    if pair['x'].nunique() < 2 or pair['y'].nunique() < 2:
        return None
    return float(pair['x'].corr(pair['y']))


def _plot_scatter_with_corr(
    df: pd.DataFrame,
    x_col: str,
    y_col: str,
    x_label: str,
    y_label: str,
    title: str,
    *,
    x_clip_q: Optional[float] = None,
    y_clip_q: Optional[float] = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 5))
    if len(df) == 0:
        ax.text(0.5, 0.5, 'No data available', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    part = df[[x_col, y_col]].copy()
    part[x_col] = pd.to_numeric(part[x_col], errors='coerce')
    part[y_col] = pd.to_numeric(part[y_col], errors='coerce')
    part = part.dropna()
    if len(part) == 0:
        ax.text(0.5, 0.5, 'No complete x/y rows', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    if x_clip_q is not None and len(part) > 20:
        x_cap = part[x_col].quantile(float(x_clip_q))
        part = part[part[x_col] <= x_cap]
    if y_clip_q is not None and len(part) > 20:
        y_cap = part[y_col].quantile(float(y_clip_q))
        part = part[part[y_col] <= y_cap]
    hb = ax.hexbin(
        part[x_col].to_numpy(dtype=float),
        part[y_col].to_numpy(dtype=float),
        gridsize=44,
        mincnt=1,
        cmap='Blues',
        linewidths=0,
    )
    cbar = fig.colorbar(hb, ax=ax, pad=0.01)
    cbar.set_label('Count', fontsize=8)
    r = _pearson_corr(part[x_col], part[y_col])
    rt = 'n/a' if r is None else f'{r:.3f}'
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(f'{title} (hexbin, Pearson r={rt})')
    return _fig_to_png(fig)


def plot_scatter_deltae_vs_steps(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    att = _dashboard_attempts_df()
    att = att[att['final_delta_e'].notna() & att['duration_sec'].notna()]
    if 'num_steps' not in att.columns:
        sql = text(
            """
            SELECT final_delta_e, duration_sec, num_steps
            FROM mixing_attempts
            WHERE final_delta_e IS NOT NULL
              AND num_steps IS NOT NULL
            """
        )
        with db.engine.connect() as conn:
            att = pd.read_sql(sql, conn)
    return _plot_scatter_with_corr(
        att,
        'num_steps',
        'final_delta_e',
        'Num steps',
        'Final DeltaE',
        'Scatter: Final DeltaE vs number of steps',
        x_clip_q=0.995,
        y_clip_q=0.995,
    )


def plot_scatter_duration_vs_steps(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    sql = text(
        """
        SELECT duration_sec, num_steps
        FROM mixing_attempts
        WHERE duration_sec IS NOT NULL
          AND num_steps IS NOT NULL
        """
    )
    with db.engine.connect() as conn:
        att = pd.read_sql(sql, conn)
    return _plot_scatter_with_corr(
        att,
        'num_steps',
        'duration_sec',
        'Num steps',
        'Elapsed time (s)',
        'Scatter: Elapsed time vs number of steps',
        x_clip_q=0.995,
        y_clip_q=0.995,
    )


def plot_correlation_heatmap(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(7.6, 6.4))
    sql = text(
        """
        SELECT
          final_delta_e,
          duration_sec,
          num_steps,
          initial_delta_e
        FROM mixing_attempts
        WHERE final_delta_e IS NOT NULL
           OR duration_sec IS NOT NULL
           OR num_steps IS NOT NULL
           OR initial_delta_e IS NOT NULL
        """
    )
    with db.engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    if len(df) == 0:
        ax.text(0.5, 0.5, 'No rows for correlation heatmap', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    num = df.apply(pd.to_numeric, errors='coerce')
    # Option A: render with always-available core fields, include initial_delta_e only if present.
    base_cols = [c for c in ['final_delta_e', 'duration_sec', 'num_steps'] if c in num.columns]
    if len(base_cols) < 2:
        ax.text(0.5, 0.5, 'Need at least two numeric metrics for correlation', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    present = []
    for c in base_cols:
        s = num[c]
        if s.notna().sum() >= 3 and s.nunique(dropna=True) >= 2:
            present.append(c)
    if 'initial_delta_e' in num.columns:
        s = num['initial_delta_e']
        if s.notna().sum() >= 3 and s.nunique(dropna=True) >= 2:
            present.append('initial_delta_e')
    if len(present) < 2:
        ax.text(0.5, 0.5, 'Not enough populated metrics for correlation', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    corr = num[present].corr(numeric_only=True)
    if corr.empty:
        ax.text(0.5, 0.5, 'Empty correlation matrix', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    cols = list(corr.columns)
    labels = {
        'final_delta_e': 'final_dE',
        'duration_sec': 'duration_s',
        'num_steps': 'num_steps',
        'initial_delta_e': 'initial_dE',
    }
    show_names = [labels.get(c, c) for c in cols]
    arr = corr.values.astype(float)
    im = ax.imshow(arr, vmin=-1.0, vmax=1.0, cmap='coolwarm')
    ax.set_xticks(np.arange(len(cols)))
    ax.set_yticks(np.arange(len(cols)))
    ax.set_xticklabels(show_names, rotation=30, ha='right')
    ax.set_yticklabels(show_names)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            v = arr[i, j]
            ax.text(j, i, f'{v:.2f}', ha='center', va='center', fontsize=8, color='black')
    ax.set_title('Correlation heatmap (attempt-level metrics)')
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label='Pearson r')
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_correlation_league(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    sql = text(
        """
        SELECT
          final_delta_e,
          duration_sec,
          num_steps,
          initial_delta_e
        FROM mixing_attempts
        WHERE final_delta_e IS NOT NULL
           OR duration_sec IS NOT NULL
           OR num_steps IS NOT NULL
           OR initial_delta_e IS NOT NULL
        """
    )
    with db.engine.connect() as conn:
        df = pd.read_sql(sql, conn)
    if len(df) == 0:
        ax.text(0.5, 0.5, 'No rows for correlation league', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    num = df.apply(pd.to_numeric, errors='coerce')
    if 'final_delta_e' not in num.columns:
        ax.text(0.5, 0.5, 'final_delta_e is missing', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    target_valid = num['final_delta_e'].notna().sum()
    if target_valid < 3 or num['final_delta_e'].nunique(dropna=True) < 2:
        ax.text(0.5, 0.5, 'Not enough final_delta_e variation', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    target = 'final_delta_e'
    candidates = [c for c in ['duration_sec', 'num_steps'] if c in num.columns]
    # Optional field: include initial_delta_e only when populated enough.
    if 'initial_delta_e' in num.columns:
        s = num['initial_delta_e']
        if s.notna().sum() >= 3 and s.nunique(dropna=True) >= 2:
            candidates.append('initial_delta_e')
    rows: List[Tuple[str, float]] = []
    for c in candidates:
        r = _pearson_corr(num[target], num[c])
        if r is None:
            continue
        rows.append((c, r))
    if not rows:
        ax.text(0.5, 0.5, 'No valid pairwise correlations', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    rows = sorted(rows, key=lambda t: abs(t[1]), reverse=True)
    names = [r[0] for r in rows]
    vals = np.array([r[1] for r in rows], dtype=float)
    y = np.arange(len(rows))
    colors = ['#dc2626' if v < 0 else '#2563eb' for v in vals]
    ax.barh(y, vals, color=colors, edgecolor='white')
    ax.axvline(0.0, color='#334155', linewidth=1.0)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()
    ax.set_xlim(-1.0, 1.0)
    ax.set_xlabel('Pearson r with final_delta_e')
    ax.set_title('Correlation league (ranked by |r|)')
    for yi, v in zip(y, vals):
        ax.text(
            v + (0.02 if v >= 0 else -0.02),
            yi,
            f'{v:.2f}',
            va='center',
            ha='left' if v >= 0 else 'right',
            fontsize=8,
            color='#111827',
        )
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_attempt_deltae_timeline(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    *,
    attempt_uuid: Optional[str] = None,
    target_color_id: Optional[int] = None,
    view_mode: Optional[str] = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    mode = str(view_mode or 'aggregate').strip().lower()
    mode = 'detailed' if mode == 'detailed' else 'aggregate'
    au_in = (attempt_uuid or '').strip()
    if au_in:
        resolved, err = _resolve_network_attempt_uuid(att, ev, au_in, None)
        if resolved is None:
            msg = {
                'unknown_attempt_uuid': 'Unknown attempt_uuid (no events in sample)',
            }.get(err, err or 'No attempt to show')
            ax.text(0.5, 0.5, msg, ha='center', va='center', fontsize=11)
            ax.axis('off')
            return _fig_to_png(fig)
        attempt_ids = [resolved]
        render_single = True
        mode_title = f'Attempt DeltaE trajectory (…{resolved[-8:]})'
    elif target_color_id is not None:
        if mode == 'detailed':
            resolved, err = _resolve_network_attempt_uuid(att, ev, None, target_color_id)
            if resolved is None:
                msg = {
                    'no_attempts_for_target': 'No attempts for this target_color_id',
                }.get(err, err or 'No attempt to show')
                ax.text(0.5, 0.5, msg, ha='center', va='center', fontsize=11)
                ax.axis('off')
                return _fig_to_png(fig)
            attempt_ids = [resolved]
            render_single = True
            mode_title = f'Attempt DeltaE trajectory (target_id={target_color_id}, …{resolved[-8:]})'
        else:
            candidates = (
                att.loc[att['target_color_id'] == target_color_id, 'attempt_uuid']
                .dropna()
                .astype(str)
                .tolist()
            )
            if len(candidates) == 0:
                ax.text(0.5, 0.5, 'No attempts for this target_color_id', ha='center', va='center', fontsize=11)
                ax.axis('off')
                return _fig_to_png(fig)
            attempt_ids = candidates
            render_single = False
            mode_title = f'Aggregate DeltaE timeline (target_id={target_color_id})'
    else:
        if mode == 'detailed':
            resolved, err = _resolve_network_attempt_uuid(att, ev, None, None)
            if resolved is None:
                ax.text(0.5, 0.5, err or 'No attempts to show', ha='center', va='center', fontsize=11)
                ax.axis('off')
                return _fig_to_png(fig)
            attempt_ids = [resolved]
            render_single = True
            mode_title = f'Attempt DeltaE trajectory (…{resolved[-8:]})'
        else:
            # Fall back to all attempts in-sample when no explicit filter is provided.
            attempt_ids = ev['attempt_uuid'].dropna().astype(str).unique().tolist()
            if len(attempt_ids) == 0:
                ax.text(0.5, 0.5, 'No attempts to show', ha='center', va='center', fontsize=11)
                ax.axis('off')
                return _fig_to_png(fig)
            render_single = False
            mode_title = 'Aggregate DeltaE timeline (all attempts in sample)'

    rows = ev[ev['attempt_uuid'].astype(str).isin(set(attempt_ids))].copy()
    rows = rows[rows['step_index'].notna()].copy()
    rows = rows.sort_values(['attempt_uuid', 'seq', 'step_index'])
    rows['delta_e_after'] = pd.to_numeric(rows['delta_e_after'], errors='coerce')
    rows = rows[rows['delta_e_after'].notna()]
    if len(rows) == 0:
        ax.text(0.5, 0.5, 'No DeltaE step rows for selected attempts', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    if render_single:
        p = rows.sort_values(['seq', 'step_index']).reset_index(drop=True)
        x = np.arange(1, len(p) + 1)
        y = p['delta_e_after'].to_numpy(dtype=float)
        action_type = p['action_type'].fillna('').astype(str).str.lower()
        c = np.where(action_type.eq('remove'), '#ef4444', '#2563eb')
        ax.plot(x, y, color='#334155', linewidth=1.8, alpha=0.9, zorder=1)
        ax.scatter(x, y, c=c, s=28, alpha=0.95, zorder=2)
        y_min = float(np.nanmin(y))
        y_max = float(np.nanmax(y))
        ax.axhline(2.0, color='#16a34a', linestyle='--', linewidth=1.2, alpha=0.8)
        pad = max(0.08 * (y_max - y_min), 0.25)
        ax.set_ylim(bottom=max(-0.05, y_min - pad), top=y_max + pad)
        ax.set_xlim(0.5, max(1.5, len(x) + 0.5))
        ax.set_xlabel('Action timeline (ordered step rows)')
        ax.set_ylabel('DeltaE after action')
        ax.set_title(mode_title)
        from matplotlib.lines import Line2D
        legend_items = [
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#2563eb', markersize=7, label='add / other'),
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#ef4444', markersize=7, label='remove'),
            Line2D([0], [0], color='#16a34a', linestyle='--', linewidth=1.2, label='DeltaE = 2.0'),
        ]
        ax.legend(handles=legend_items, fontsize=8, loc='upper right')
        return _fig_to_png(fig)

    # Aggregate mode: normalize each attempt timeline to progress in [0, 1] and
    # render quantile bands + median for a compact, informative population view.
    max_attempts = 350
    max_steps_per_attempt = 180
    bins = 24
    ids_unique = rows['attempt_uuid'].astype(str).dropna().unique().tolist()
    total_attempts = len(ids_unique)
    if total_attempts > max_attempts:
        ids_keep = set(ids_unique[:max_attempts])
        rows = rows[rows['attempt_uuid'].astype(str).isin(ids_keep)].copy()
    if len(rows) == 0:
        ax.text(0.5, 0.5, 'No rows after sampling', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    packed_parts = []
    first_under2 = 0
    attempts_in_plot = 0
    for _, part in rows.groupby('attempt_uuid', sort=False):
        p = part.sort_values(['seq', 'step_index']).copy()
        if len(p) == 0:
            continue
        attempts_in_plot += 1
        if (p['delta_e_after'] < 2.0).any():
            first_under2 += 1
        if len(p) > max_steps_per_attempt:
            idx = np.linspace(0, len(p) - 1, num=max_steps_per_attempt, dtype=int)
            p = p.iloc[np.unique(idx)].copy()
        n = len(p)
        if n <= 1:
            p['progress'] = 0.0
        else:
            p['progress'] = np.linspace(0.0, 1.0, num=n)
        packed_parts.append(p[['progress', 'delta_e_after']])

    if not packed_parts:
        ax.text(0.5, 0.5, 'No packed timeline rows', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    packed = pd.concat(packed_parts, ignore_index=True)
    packed['bin'] = np.clip((packed['progress'] * bins).astype(int), 0, bins - 1)
    summary = (
        packed.groupby('bin', sort=True)['delta_e_after']
        .agg(
            p10=lambda s: s.quantile(0.10),
            p25=lambda s: s.quantile(0.25),
            p50='median',
            p75=lambda s: s.quantile(0.75),
            p90=lambda s: s.quantile(0.90),
            n='count',
        )
        .reset_index()
    )
    summary = summary[summary['n'] >= 3].copy()
    if len(summary) == 0:
        ax.text(0.5, 0.5, 'No aggregate bins with enough rows', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    x = (summary['bin'].to_numpy(dtype=float) + 0.5) / float(bins)
    p10 = summary['p10'].to_numpy(dtype=float)
    p25 = summary['p25'].to_numpy(dtype=float)
    p50 = summary['p50'].to_numpy(dtype=float)
    p75 = summary['p75'].to_numpy(dtype=float)
    p90 = summary['p90'].to_numpy(dtype=float)

    ax.fill_between(x, p10, p90, color='#93c5fd', alpha=0.28, linewidth=0, label='p10-p90')
    ax.fill_between(x, p25, p75, color='#2563eb', alpha=0.32, linewidth=0, label='IQR (p25-p75)')
    ax.plot(x, p50, color='#1e293b', linewidth=2.1, label='median')
    ax.axhline(2.0, color='#16a34a', linestyle='--', linewidth=1.2, alpha=0.9, label='DeltaE = 2.0')
    ax.set_xlim(0.0, 1.0)
    ax.set_xticks(np.linspace(0.0, 1.0, 6))
    ax.set_xticklabels([f'{int(v * 100)}%' for v in np.linspace(0.0, 1.0, 6)])
    ax.set_xlabel('Normalized attempt progress')
    ax.set_ylabel('DeltaE after action')
    hit_rate = (float(first_under2) / float(max(1, attempts_in_plot))) * 100.0
    sampled_note = ''
    if total_attempts > attempts_in_plot:
        sampled_note = f' [sampled {attempts_in_plot}/{total_attempts}]'
    ax.set_title(f'{mode_title}{sampled_note} | hit(<2.0)={hit_rate:.1f}%')
    ax.legend(fontsize=8, loc='upper right')
    return _fig_to_png(fig)


def plot_archetype_deltae_trajectories(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    *,
    archetype: Optional[str] = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(11, 5.5))
    label = (archetype or '').strip()
    if not label:
        ax.text(0.5, 0.5, 'Pick an archetype from the filter to render this plot', ha='center', va='center', fontsize=11)
        ax.axis('off')
        return _fig_to_png(fig)

    tags = build_attempt_archetypes(per_attempt_limit=250000).get('per_attempt_tags') or []
    if not tags:
        ax.text(0.5, 0.5, 'No archetype tags available', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    attempt_ids = [str(r.get('attempt_uuid') or '') for r in tags if str(r.get('archetype') or '') == label]
    attempt_ids = [x for x in attempt_ids if x]
    if not attempt_ids:
        ax.text(0.5, 0.5, f'No attempts with archetype "{label}"', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    # Keep plot readable and bounded for very common archetypes.
    if len(attempt_ids) > 1200:
        attempt_ids = attempt_ids[:1200]

    rows = ev[ev['attempt_uuid'].astype(str).isin(set(attempt_ids))].copy()
    rows = rows[rows['step_index'].notna()].copy()
    rows = rows.sort_values(['attempt_uuid', 'seq', 'step_index'])
    rows['delta_e_after'] = pd.to_numeric(rows['delta_e_after'], errors='coerce')
    rows = rows[rows['delta_e_after'].notna()]
    if len(rows) == 0:
        ax.text(0.5, 0.5, 'No DeltaE step rows for selected archetype', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    y_min = float(rows['delta_e_after'].min())
    y_max = float(rows['delta_e_after'].max())
    x_max = 0
    for _, part in rows.groupby('attempt_uuid', sort=False):
        p = part.sort_values(['seq', 'step_index'])
        x = np.arange(1, len(p) + 1)
        y = p['delta_e_after'].to_numpy(dtype=float)
        x_max = max(x_max, len(x))
        ax.plot(x, y, color='#334155', linewidth=1.0, alpha=0.11, zorder=1)

    med = (
        rows.groupby('step_index', sort=True)['delta_e_after']
        .median()
        .reset_index()
        .sort_values('step_index')
    )
    if len(med):
        ax.plot(
            med['step_index'].to_numpy(dtype=float) + 1.0,
            med['delta_e_after'].to_numpy(dtype=float),
            color='#dc2626',
            linewidth=2.1,
            alpha=0.95,
            zorder=3,
            label='median trajectory',
        )
        ax.legend(fontsize=8, loc='upper right')

    ax.axhline(2.0, color='#16a34a', linestyle='--', linewidth=1.2, alpha=0.8)
    pad = max(0.08 * (y_max - y_min), 0.25)
    ax.set_ylim(bottom=max(-0.05, y_min - pad), top=y_max + pad)
    ax.set_xlim(0.5, max(1.5, x_max + 0.5))
    ax.set_xlabel('Action timeline (ordered step rows per attempt)')
    ax.set_ylabel('DeltaE after action')
    ax.set_title(f'Attempt DeltaE trajectories for archetype: {label} (n={len(attempt_ids)})')
    return _fig_to_png(fig)


def plot_archetype_compare_trajectories(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    *,
    archetypes: Optional[List[str]] = None,
) -> bytes:
    """
    Overlay median DeltaE trajectories for multiple archetypes in one chart.
    """
    fig, ax = plt.subplots(figsize=(11, 5.8))
    tags = build_attempt_archetypes(per_attempt_limit=250000).get('per_attempt_tags') or []
    if not tags:
        ax.text(0.5, 0.5, 'No archetype tags available', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    tags_df = pd.DataFrame(tags)
    if tags_df.empty or 'attempt_uuid' not in tags_df.columns or 'archetype' not in tags_df.columns:
        ax.text(0.5, 0.5, 'No archetype tags available', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    tags_df['attempt_uuid'] = tags_df['attempt_uuid'].astype(str)
    tags_df['archetype'] = tags_df['archetype'].astype(str)
    tags_df = tags_df[(tags_df['attempt_uuid'] != '') & (tags_df['archetype'] != '')].copy()
    if tags_df.empty:
        ax.text(0.5, 0.5, 'No valid archetype-tagged attempts', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    available = sorted(tags_df['archetype'].unique().tolist())
    selected = [str(a).strip() for a in (archetypes or []) if str(a).strip()]
    if selected:
        selected = [a for a in selected if a in available]
    if not selected:
        counts = (
            tags_df.groupby('archetype', sort=False)['attempt_uuid']
            .nunique()
            .sort_values(ascending=False)
        )
        selected = counts.head(5).index.tolist()
    if not selected:
        ax.text(0.5, 0.5, 'No archetypes selected', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    rows = ev[['attempt_uuid', 'seq', 'step_index', 'delta_e_after']].copy()
    rows['attempt_uuid'] = rows['attempt_uuid'].astype(str)
    rows['step_index'] = pd.to_numeric(rows['step_index'], errors='coerce')
    rows['delta_e_after'] = pd.to_numeric(rows['delta_e_after'], errors='coerce')
    rows = rows[rows['step_index'].notna() & rows['delta_e_after'].notna()].copy()
    if rows.empty:
        ax.text(0.5, 0.5, 'No DeltaE step rows', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    tags_sel = tags_df[tags_df['archetype'].isin(selected)][['attempt_uuid', 'archetype']].drop_duplicates()
    merged = rows.merge(tags_sel, on='attempt_uuid', how='inner')
    if merged.empty:
        ax.text(0.5, 0.5, 'No matching step rows for selected archetypes', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    # Keep response bounded on very large classes.
    cap_per_archetype = 1200
    kept_ids: List[str] = []
    for arc in selected:
        ids = (
            tags_sel[tags_sel['archetype'] == arc]['attempt_uuid']
            .drop_duplicates()
            .astype(str)
            .tolist()
        )
        kept_ids.extend(ids[:cap_per_archetype])
    keep_set = set(kept_ids)
    merged = merged[merged['attempt_uuid'].isin(keep_set)].copy()
    if merged.empty:
        ax.text(0.5, 0.5, 'No rows after cap/filtering', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    cmap = plt.get_cmap('tab10')
    max_step = 1
    for i, arc in enumerate(selected):
        part = merged[merged['archetype'] == arc].copy()
        if part.empty:
            continue
        grp = (
            part.groupby('step_index', sort=True)['delta_e_after']
            .agg(['median', 'count'])
            .reset_index()
            .sort_values('step_index')
        )
        grp = grp[grp['count'] >= 3]
        if grp.empty:
            continue
        x = grp['step_index'].to_numpy(dtype=float) + 1.0
        y = grp['median'].to_numpy(dtype=float)
        max_step = max(max_step, int(np.nanmax(x)))
        color = cmap(i % 10)
        n_attempts = int(part['attempt_uuid'].nunique())
        ax.plot(x, y, linewidth=2.0, alpha=0.95, color=color, label=f'{arc} (n={n_attempts})')

    ax.axhline(2.0, color='#16a34a', linestyle='--', linewidth=1.1, alpha=0.8)
    ax.set_xlim(0.5, max(2.5, max_step + 0.5))
    ax.set_xlabel('Action timeline (step index + 1)')
    ax.set_ylabel('Median DeltaE after action')
    ax.set_title('Archetype trajectory comparison (overlaid medians)')
    ax.legend(fontsize=8, loc='upper right', ncol=1)
    return _fig_to_png(fig)


def plot_archetype_share_by_attempt_no(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Stacked share of archetypes at each successive attempt_no (user × target_name)."""
    fig, ax = plt.subplots(figsize=(10, 5))
    data = build_attempt_archetypes()
    rows = data.get('archetype_by_attempt_no') or []
    if not rows:
        ax.text(0.5, 0.5, 'No archetype-by-attempt data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    dfp = pd.DataFrame(rows)
    pivot = dfp.pivot_table(
        index='attempt_no', columns='archetype', values='share_within_attempt_no', aggfunc='sum'
    ).fillna(0.0)
    if pivot.empty:
        ax.text(0.5, 0.5, 'No pivot data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    pivot.plot(kind='bar', stacked=True, ax=ax, width=0.82, colormap='tab20', legend=True)
    ax.set_xlabel('Attempt number (within user × target color)')
    ax.set_ylabel('Share of tagged attempts')
    ax.set_title('Archetype mix by successive attempt number')
    ax.legend(title='archetype', bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=7)
    ax.tick_params(axis='x', rotation=0)
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_archetype_transition_heatmap(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Heatmap: from archetype (attempt k) → to archetype (attempt k+1), consecutive attempt_no only."""
    fig, ax = plt.subplots(figsize=(9, 7))
    data = build_attempt_archetypes()
    rows = data.get('archetype_transitions') or []
    if not rows:
        ax.text(0.5, 0.5, 'No transition pairs (need consecutive tagged attempts)', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    dfp = pd.DataFrame(rows)
    mat = dfp.pivot_table(
        index='from_archetype', columns='to_archetype', values='n', aggfunc='sum', fill_value=0
    )
    if mat.empty:
        ax.text(0.5, 0.5, 'Empty transition matrix', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    arr = mat.values.astype(float)
    im = ax.imshow(arr, aspect='auto', cmap='Blues')
    ax.set_xticks(np.arange(mat.shape[1]))
    ax.set_yticks(np.arange(mat.shape[0]))
    ax.set_xticklabels(list(mat.columns), rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(list(mat.index), fontsize=8)
    ax.set_xlabel('To archetype (next attempt)')
    ax.set_ylabel('From archetype (current attempt)')
    ax.set_title('Archetype transitions (consecutive attempt_no, same user × target)')
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    thr = max(arr.max() * 0.15, 1.0)
    for i in range(arr.shape[0]):
        for j in range(arr.shape[1]):
            v = arr[i, j]
            if v <= 0:
                continue
            ax.text(j, i, int(v), ha='center', va='center', color='white' if v > thr else 'black', fontsize=7)
    fig.tight_layout()
    return _fig_to_png(fig)


def _json_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(xf):
        return None
    return xf


def _normalize_recipe_vector(arr: np.ndarray) -> Optional[np.ndarray]:
    vec = np.asarray(arr, dtype=float)
    vec = np.where(np.isfinite(vec), vec, 0.0)
    vec = np.clip(vec, 0.0, None)
    total = float(vec.sum())
    if total <= 0:
        return None
    return vec / total


def build_attempt_recipe_similarity(
    att: pd.DataFrame,
    ev: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build per-attempt, scale-invariant recipe similarity against target recipe.

    Similarity is computed on normalized recipe vectors:
      similarity = 1 - 0.5 * L1(user_ratio, target_ratio), range [0, 1].
    """
    if len(att) == 0:
        return pd.DataFrame()

    tc_sql = text(
        """
        SELECT
          id AS target_color_id,
          COALESCE(name, '(unknown)') AS target_name,
          COALESCE(drop_red, 0) AS target_drop_red,
          COALESCE(drop_yellow, 0) AS target_drop_yellow,
          COALESCE(drop_white, 0) AS target_drop_white,
          COALESCE(drop_blue, 0) AS target_drop_blue,
          COALESCE(drop_black, 0) AS target_drop_black
        FROM target_colors
        """
    )
    with db.engine.connect() as conn:
        tc = pd.read_sql(tc_sql, conn)

    if len(tc) == 0:
        return pd.DataFrame()

    tc['target_color_id'] = pd.to_numeric(tc['target_color_id'], errors='coerce')
    tc = tc[tc['target_color_id'].notna()].copy()
    if len(tc) == 0:
        return pd.DataFrame()
    tc['target_color_id'] = tc['target_color_id'].astype(int)

    # Prefer authoritative final drops from the last state_after_json of each attempt.
    # Fallback to edge reconstruction only when state payload is unavailable.
    final_by_attempt: Dict[str, Tuple[int, int, int, int, int]] = {}
    if len(ev):
        if 'state_after_json' in ev.columns:
            ev_last = ev.sort_values(['attempt_uuid', 'seq'], na_position='last')
            for au, part in ev_last.groupby('attempt_uuid', sort=False):
                raw = part.iloc[-1].get('state_after_json')
                drops = raw.get('drops') if isinstance(raw, dict) else None
                if not isinstance(drops, dict):
                    continue
                final_by_attempt[str(au)] = (
                    int(max(0, drops.get('red', 0) or 0)),
                    int(max(0, drops.get('yellow', 0) or 0)),
                    int(max(0, drops.get('white', 0) or 0)),
                    int(max(0, drops.get('blue', 0) or 0)),
                    int(max(0, drops.get('black', 0) or 0)),
                )
        if not final_by_attempt:
            edges = build_edge_tables_all_attempts(ev)
            if len(edges):
                for au, part in edges.groupby('attempt_uuid', sort=False):
                    p = part.sort_values('seq')
                    st = p.iloc[-1]['to_state']
                    if isinstance(st, tuple) and len(st) == 5:
                        final_by_attempt[str(au)] = tuple(max(0, int(x)) for x in st)

    cols = [
        'attempt_uuid',
        'target_color_id',
        'final_delta_e',
        'end_reason',
        'attempt_started_server_ts',
    ]
    a = att[[c for c in cols if c in att.columns]].copy()
    if 'attempt_uuid' not in a.columns or 'target_color_id' not in a.columns:
        return pd.DataFrame()
    a = a[a['attempt_uuid'].notna() & a['target_color_id'].notna()].copy()
    if len(a) == 0:
        return pd.DataFrame()
    a['attempt_uuid'] = a['attempt_uuid'].astype(str)
    a['target_color_id'] = pd.to_numeric(a['target_color_id'], errors='coerce')
    a = a[a['target_color_id'].notna()].copy()
    if len(a) == 0:
        return pd.DataFrame()
    a['target_color_id'] = a['target_color_id'].astype(int)

    merged = a.merge(tc, on='target_color_id', how='left')
    if len(merged) == 0:
        return pd.DataFrame()

    out_rows: List[Dict[str, Any]] = []
    for _, row in merged.iterrows():
        au = str(row.get('attempt_uuid') or '')
        if not au:
            continue
        user_state = final_by_attempt.get(au)
        if user_state is None:
            continue

        # user_state tuple order: red, yellow, white, blue, black
        user_vec = np.array(user_state, dtype=float)
        target_vec = np.array(
            [
                row.get('target_drop_red'),
                row.get('target_drop_yellow'),
                row.get('target_drop_white'),
                row.get('target_drop_blue'),
                row.get('target_drop_black'),
            ],
            dtype=float,
        )
        user_ratio = _normalize_recipe_vector(user_vec)
        target_ratio = _normalize_recipe_vector(target_vec)
        if user_ratio is None or target_ratio is None:
            continue

        similarity = float(np.clip(1.0 - 0.5 * np.abs(user_ratio - target_ratio).sum(), 0.0, 1.0))
        ratio_is_perfect = bool(np.isclose(similarity, 1.0, atol=1e-9))

        out_rows.append(
            {
                'attempt_uuid': au,
                'target_color_id': int(row['target_color_id']),
                'target_name': str(row.get('target_name') or '(unknown)'),
                'final_delta_e': _json_float(row.get('final_delta_e')),
                'end_reason': str(row.get('end_reason') or ''),
                'similarity': similarity,
                'ratio_is_perfect': ratio_is_perfect,
                'user_total_drops': int(max(0.0, user_vec.sum())),
                'target_total_drops': int(max(0.0, target_vec.sum())),
                'attempt_started_server_ts': row.get('attempt_started_server_ts'),
            }
        )

    return pd.DataFrame(out_rows)


def build_recipe_similarity_summary() -> Dict[str, Any]:
    att, ev = get_dataframes()
    df = build_attempt_recipe_similarity(att, ev)
    if len(df) == 0:
        return {
            'n_attempts_with_similarity': 0,
            'n_perfect_ratio_solutions': 0,
            'n_deviant_ratio_solutions': 0,
            'perfect_ratio_share': None,
            'mean_similarity': None,
            'median_similarity': None,
            'mean_delta_e': None,
            'scatter_rows': [],
        }

    solved = df[df['end_reason'].isin(['saved_match', 'saved_stop'])].copy()
    base = solved if len(solved) else df
    n = int(len(base))
    n_perfect = int(base['ratio_is_perfect'].sum())
    n_deviant = int(n - n_perfect)

    scatter_df = base[['target_name', 'final_delta_e', 'similarity', 'ratio_is_perfect']].copy()
    scatter_df = scatter_df[
        scatter_df['final_delta_e'].notna()
        & scatter_df['similarity'].notna()
    ]
    if len(scatter_df) > 3000:
        scatter_df = scatter_df.sample(3000, random_state=42)

    scatter_rows: List[Dict[str, Any]] = []
    for _, r in scatter_df.iterrows():
        scatter_rows.append(
            {
                'target_name': str(r.get('target_name') or '(unknown)'),
                'final_delta_e': _json_float(r.get('final_delta_e')),
                'similarity': _json_float(r.get('similarity')),
                'ratio_is_perfect': bool(r.get('ratio_is_perfect')),
            }
        )

    return {
        'n_attempts_with_similarity': n,
        'n_perfect_ratio_solutions': n_perfect,
        'n_deviant_ratio_solutions': n_deviant,
        'perfect_ratio_share': _json_float(n_perfect / n if n > 0 else None),
        'mean_similarity': _json_float(base['similarity'].mean()),
        'median_similarity': _json_float(base['similarity'].median()),
        'mean_delta_e': _json_float(base['final_delta_e'].mean()),
        'scatter_rows': scatter_rows,
    }


def plot_deltae_vs_similarity(att: pd.DataFrame, ev: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 5))
    df = build_attempt_recipe_similarity(att, ev)
    if len(df) == 0:
        ax.text(0.5, 0.5, 'No recipe similarity data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    part = df[df['final_delta_e'].notna() & df['similarity'].notna()].copy()
    if len(part) == 0:
        ax.text(0.5, 0.5, 'No final DeltaE rows with similarity', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    hb = ax.hexbin(
        part['similarity'].to_numpy(dtype=float),
        part['final_delta_e'].to_numpy(dtype=float),
        gridsize=44,
        mincnt=1,
        cmap='Blues',
        linewidths=0,
    )
    cbar = fig.colorbar(hb, ax=ax, pad=0.01)
    cbar.set_label('Count', fontsize=8)
    r = _pearson_corr(part['similarity'], part['final_delta_e'])
    rt = 'n/a' if r is None else f'{r:.3f}'
    ax.set_xlabel('Recipe similarity (ratio-based, 0..1)')
    ax.set_ylabel('Final DeltaE')
    ax.set_title(f'Hexbin density: Final DeltaE vs recipe similarity (Pearson r={rt})')
    ax.set_xlim(-0.02, 1.02)
    return _fig_to_png(fig)


def build_attempt_level_strategy_metrics(att: pd.DataFrame, ev: pd.DataFrame) -> pd.DataFrame:
    """One row per attempt with reconstructable action edges (pigment state path)."""
    if len(ev) == 0:
        return pd.DataFrame()
    initial = _state_to_tuple(_zero_state_counts())
    rows: List[Dict[str, Any]] = []
    att_u = att.drop_duplicates(subset=['attempt_uuid'], keep='last')
    att_idx = att_u.set_index(att_u['attempt_uuid'].astype(str), drop=False)

    for attempt_uuid, part in ev.groupby('attempt_uuid', sort=False):
        au = str(attempt_uuid)
        edge_df = _build_edge_table_for_attempt(part)
        if len(edge_df) == 0:
            continue
        ed = edge_df.sort_values('seq').reset_index(drop=True)
        ed['delta_e_before'] = pd.to_numeric(ed['delta_e_before'], errors='coerce')
        ed['delta_e_after'] = pd.to_numeric(ed['delta_e_after'], errors='coerce')

        visited = {initial}
        revisits = 0
        for _, r in ed.iterrows():
            to_s = r['to_state']
            if to_s in visited:
                revisits += 1
            visited.add(to_s)

        pigment_rev = 0
        for i in range(1, len(ed)):
            if (
                ed.loc[i, 'action_color'] == ed.loc[i - 1, 'action_color']
                and ed.loc[i, 'action_type'] != ed.loc[i - 1, 'action_type']
            ):
                pigment_rev += 1

        gains = ed['delta_e_before'] - ed['delta_e_after']
        sign_rev = 0
        for i in range(1, len(gains)):
            pr_g, g = gains.iloc[i - 1], gains.iloc[i]
            if pd.isna(pr_g) or pd.isna(g) or pr_g == 0 or g == 0:
                continue
            if (pr_g > 0 and g < 0) or (pr_g < 0 and g > 0):
                sign_rev += 1

        imp = int((ed['delta_e_after'] < ed['delta_e_before']).sum())
        wors = int((ed['delta_e_after'] > ed['delta_e_before']).sum())
        flat = int((ed['delta_e_after'] == ed['delta_e_before']).sum())
        best_de = float(ed['delta_e_after'].min())

        def first_seq_below(th: float) -> Optional[int]:
            sub = ed[ed['delta_e_after'] < th]
            if len(sub) == 0:
                return None
            return int(sub.iloc[0]['seq'])

        tid: Optional[int] = None
        if au in att_idx.index:
            raw_tid = att_idx.loc[au, 'target_color_id']
            if isinstance(raw_tid, pd.Series):
                raw_tid = raw_tid.iloc[0]
            if pd.notna(raw_tid):
                tid = int(raw_tid)

        final_de: Optional[float] = None
        if au in att_idx.index:
            raw_fd = att_idx.loc[au, 'final_delta_e']
            if isinstance(raw_fd, pd.Series):
                raw_fd = raw_fd.iloc[0]
            if pd.notna(raw_fd):
                final_de = float(raw_fd)

        rows.append(
            {
                'attempt_uuid': au,
                'target_color_id': tid,
                'n_actions': len(ed),
                'n_unique_states': len(visited),
                'n_state_revisits': revisits,
                'n_pigment_reversals': pigment_rev,
                'n_gain_sign_reversals': sign_rev,
                'n_improving': imp,
                'n_worsening': wors,
                'n_flat': flat,
                'best_delta_e_along_path': best_de,
                'first_seq_delta_e_lt_5': first_seq_below(5.0),
                'first_seq_delta_e_lt_2': first_seq_below(2.0),
                'first_seq_delta_e_lt_1': first_seq_below(1.0),
                'final_delta_e': final_de,
            }
        )
    return pd.DataFrame(rows)


def build_strategy_summary_by_target() -> List[Dict[str, Any]]:
    """
    Aggregate path/strategy metrics by target color (attempts with reconstructable actions only).
    """
    att, ev = get_dataframes()
    m = build_attempt_level_strategy_metrics(att, ev)
    if len(m) == 0:
        return []
    m = m[m['target_color_id'].notna()].copy()
    if len(m) == 0:
        return []

    names: Dict[int, str] = {}
    with db.engine.connect() as conn:
        tc = pd.read_sql(text('SELECT id, name FROM target_colors'), conn)
    for _, r in tc.iterrows():
        names[int(r['id'])] = str(r['name'])

    out: List[Dict[str, Any]] = []
    for tid, g in m.groupby('target_color_id', sort=True):
        tid_i = int(tid)
        na = g['n_actions'].replace(0, np.nan)
        out.append(
            {
                'target_color_id': tid_i,
                'target_name': names.get(tid_i, '—'),
                'n_attempts_path': int(len(g)),
                'median_final_delta_e': _json_float(g['final_delta_e'].median()),
                'median_best_path_delta_e': _json_float(g['best_delta_e_along_path'].median()),
                'mean_n_actions': _json_float(g['n_actions'].mean()),
                'median_n_actions': _json_float(g['n_actions'].median()),
                'mean_revisits_per_action': _json_float((g['n_state_revisits'] / na).mean()),
                'mean_pigment_reversals_per_action': _json_float((g['n_pigment_reversals'] / na).mean()),
                'mean_improving_step_rate': _json_float((g['n_improving'] / g['n_actions']).mean()),
                'mean_worsening_step_rate': _json_float((g['n_worsening'] / g['n_actions']).mean()),
                # pandas .median() skips NaN and does not emit numpy's "All-NaN slice" warning
                'median_first_seq_lt_5': _json_float(
                    pd.to_numeric(g['first_seq_delta_e_lt_5'], errors='coerce').median()
                ),
                'median_first_seq_lt_2': _json_float(
                    pd.to_numeric(g['first_seq_delta_e_lt_2'], errors='coerce').median()
                ),
                'median_first_seq_lt_1': _json_float(
                    pd.to_numeric(g['first_seq_delta_e_lt_1'], errors='coerce').median()
                ),
            }
        )
    out.sort(key=lambda r: (-r['n_attempts_path'], r['target_color_id']))
    return out


def build_attempt_archetypes(
    *,
    per_attempt_limit: int = 2000,
) -> Dict[str, Any]:
    """
    Rule-based archetype tagging from action-path metrics.
    Returns:
      - per_attempt_tags: one row per attempt (possibly truncated)
      - distribution_by_color: archetype counts/shares per target color
    """
    att, ev = get_dataframes()
    m = build_attempt_level_strategy_metrics(att, ev)
    if len(m) == 0:
        return {
            'per_attempt_total': 0,
            'per_attempt_truncated': False,
            'per_attempt_tags': [],
            'distribution_by_color': [],
            'archetype_by_attempt_no': [],
            'archetype_transitions': [],
            'archetype_transition_same_rate': None,
            'archetype_sequence_samples': [],
        }

    with db.engine.connect() as conn:
        tc = pd.read_sql(text('SELECT id, name FROM target_colors'), conn)
    tc_names = {int(r['id']): str(r['name']) for _, r in tc.iterrows()}

    df = m.copy()
    df['target_name'] = df['target_color_id'].map(
        lambda x: tc_names.get(int(x), '(unknown)') if pd.notna(x) else '(unknown)'
    )
    df['final_delta_e'] = pd.to_numeric(df['final_delta_e'], errors='coerce')
    df['best_delta_e_along_path'] = pd.to_numeric(df['best_delta_e_along_path'], errors='coerce')
    df['n_actions'] = pd.to_numeric(df['n_actions'], errors='coerce').fillna(0)
    df['n_improving'] = pd.to_numeric(df['n_improving'], errors='coerce').fillna(0)
    df['n_gain_sign_reversals'] = pd.to_numeric(df['n_gain_sign_reversals'], errors='coerce').fillna(0)
    df['n_state_revisits'] = pd.to_numeric(df['n_state_revisits'], errors='coerce').fillna(0)

    def classify(row: pd.Series) -> str:
        n_actions = max(float(row.get('n_actions', 0) or 0), 0.0)
        if n_actions < 3:
            return 'short_run'
        n_improving = float(row.get('n_improving', 0) or 0)
        sign_rev = float(row.get('n_gain_sign_reversals', 0) or 0)
        revisits = float(row.get('n_state_revisits', 0) or 0)
        final_de = row.get('final_delta_e')
        best_de = row.get('best_delta_e_along_path')

        improve_rate = n_improving / max(n_actions, 1.0)
        reversal_rate = sign_rev / max(n_actions - 1.0, 1.0)
        revisit_rate = revisits / max(n_actions, 1.0)
        best_gap = np.nan
        if pd.notna(final_de) and pd.notna(best_de):
            best_gap = float(final_de) - float(best_de)

        if pd.notna(final_de) and float(final_de) <= 1.0 and improve_rate >= 0.75 and reversal_rate < 0.15:
            return 'direct_converger'
        if reversal_rate >= 0.45:
            return 'oscillator'
        if improve_rate < 0.45 and revisit_rate >= 0.20:
            return 'random_searcher'
        if pd.notna(best_gap) and best_gap > 1.0:
            return 'backslider'
        if improve_rate >= 0.55 and reversal_rate < 0.25 and n_actions >= 12:
            return 'slow_and_steady'
        return 'coarse_then_fine'

    # Additional indicators requested for per-attempt profiling.
    df['improve_rate'] = df['n_improving'] / df['n_actions'].replace(0, np.nan)
    df['reversal_rate'] = df['n_gain_sign_reversals'] / (df['n_actions'] - 1).replace(0, np.nan)

    # Compute step-level indicators directly from events for robust per-attempt metrics.
    ev_steps = ev[ev['step_index'].notna()].copy()
    ev_steps['delta_e_before'] = pd.to_numeric(ev_steps['delta_e_before'], errors='coerce')
    ev_steps['delta_e_after'] = pd.to_numeric(ev_steps['delta_e_after'], errors='coerce')
    ev_steps = ev_steps[
        ev_steps['attempt_uuid'].notna()
        & ev_steps['delta_e_before'].notna()
        & ev_steps['delta_e_after'].notna()
    ].copy()
    ev_steps = ev_steps.sort_values(['attempt_uuid', 'seq', 'step_index'], na_position='last')
    ev_steps['gain'] = ev_steps['delta_e_before'] - ev_steps['delta_e_after']

    # Volatility: SD of step gain.
    vol = ev_steps.groupby('attempt_uuid', sort=False)['gain'].std(ddof=0).rename('volatility_sd_gain')

    # Convergence slope: compare early vs late local slopes (first/last 30%).
    conv_rows: List[Dict[str, Any]] = []
    for au, part in ev_steps.groupby('attempt_uuid', sort=False):
        p = part.reset_index(drop=True)
        n = len(p)
        if n < 4:
            conv_rows.append(
                {
                    'attempt_uuid': str(au),
                    'slope_first30': np.nan,
                    'slope_last30': np.nan,
                    'convergence_slope_delta': np.nan,
                }
            )
            continue
        k = max(int(np.ceil(0.30 * n)), 2)
        first = p.iloc[:k]
        last = p.iloc[n - k:]
        x1 = np.arange(1, len(first) + 1, dtype=float)
        y1 = first['delta_e_after'].to_numpy(dtype=float)
        x2 = np.arange(1, len(last) + 1, dtype=float)
        y2 = last['delta_e_after'].to_numpy(dtype=float)
        slope1 = float(np.polyfit(x1, y1, 1)[0]) if len(first) >= 2 else np.nan
        slope2 = float(np.polyfit(x2, y2, 1)[0]) if len(last) >= 2 else np.nan
        conv_rows.append(
            {
                'attempt_uuid': str(au),
                'slope_first30': slope1,
                'slope_last30': slope2,
                'convergence_slope_delta': (slope2 - slope1) if (np.isfinite(slope1) and np.isfinite(slope2)) else np.nan,
            }
        )
    conv_df = pd.DataFrame(conv_rows)

    # Efficiency: DeltaE reduction per action / per second.
    # Ensure initial_delta_e/duration are available by merging from attempts table.
    att_cols = (
        att[['attempt_uuid', 'initial_delta_e', 'duration_sec']].copy()
        if len(att)
        else pd.DataFrame(columns=['attempt_uuid', 'initial_delta_e', 'duration_sec'])
    )
    att_cols = att_cols.rename(columns={'initial_delta_e': 'initial_delta_e_attempt', 'duration_sec': 'duration_sec_attempt'})
    att_cols['attempt_uuid'] = att_cols['attempt_uuid'].astype(str)
    df['attempt_uuid'] = df['attempt_uuid'].astype(str)
    df = df.merge(att_cols, on='attempt_uuid', how='left')
    df['initial_delta_e'] = pd.to_numeric(df['initial_delta_e_attempt'], errors='coerce')
    df['duration_sec'] = pd.to_numeric(df['duration_sec_attempt'], errors='coerce')
    df['deltae_reduction'] = df['initial_delta_e'] - df['final_delta_e']
    df['efficiency_per_action'] = df['deltae_reduction'] / df['n_actions'].replace(0, np.nan)
    df['efficiency_per_sec'] = df['deltae_reduction'] / df['duration_sec'].replace(0, np.nan)

    # Stopping quality: final DeltaE vs expected median for same target and attempt_no.
    attempts_no = _dashboard_attempts_with_attempt_no()
    expected = pd.DataFrame(columns=['target_name', 'attempt_no', 'expected_final_delta_e'])
    attempt_no_map = pd.DataFrame(columns=['attempt_uuid', 'attempt_no'])
    if len(attempts_no):
        attempts_no = attempts_no.copy()
        attempts_no['attempt_uuid'] = attempts_no['attempt_uuid'].astype(str)
        attempts_no['final_delta_e'] = pd.to_numeric(attempts_no['final_delta_e'], errors='coerce')
        expected = (
            attempts_no[attempts_no['final_delta_e'].notna()]
            .groupby(['target_name', 'attempt_no'], dropna=False)['final_delta_e']
            .median()
            .reset_index()
            .rename(columns={'final_delta_e': 'expected_final_delta_e'})
        )
        attempt_no_map = attempts_no[['attempt_uuid', 'attempt_no']].drop_duplicates(subset=['attempt_uuid'], keep='last')

    df = df.merge(attempt_no_map, on='attempt_uuid', how='left')
    df = df.merge(expected, on=['target_name', 'attempt_no'], how='left')
    df['stopping_quality_deltae_vs_expected'] = df['final_delta_e'] - df['expected_final_delta_e']

    df = df.merge(vol.reset_index().rename(columns={'attempt_uuid': 'attempt_uuid'}), on='attempt_uuid', how='left')
    if len(conv_df):
        df = df.merge(conv_df, on='attempt_uuid', how='left')

    # Archetype decision can now leverage richer indicators.
    df['archetype'] = df.apply(classify, axis=1)

    # user_id for successive-attempt analysis (same partition as attempt_no: user × target_name)
    uid = (
        att[['attempt_uuid', 'user_id']].drop_duplicates(subset=['attempt_uuid']).copy()
        if len(att)
        else pd.DataFrame(columns=['attempt_uuid', 'user_id'])
    )
    uid['attempt_uuid'] = uid['attempt_uuid'].astype(str)
    df = df.merge(uid, on='attempt_uuid', how='left')

    archetype_by_attempt_no: List[Dict[str, Any]] = []
    archetype_transitions: List[Dict[str, Any]] = []
    same_rate: Optional[float] = None
    sequence_samples: List[Dict[str, Any]] = []

    seq = df[df['user_id'].notna() & df['attempt_no'].notna()].copy()
    if len(seq):
        seq['attempt_no'] = pd.to_numeric(seq['attempt_no'], errors='coerce')
        seq = seq[seq['attempt_no'].notna() & (seq['attempt_no'] >= 1) & (seq['attempt_no'] <= 15)]
        if len(seq):
            tot_by_no = seq.groupby('attempt_no', sort=True).size().rename('total').reset_index()
            by_no = (
                seq.groupby(['attempt_no', 'archetype'], sort=False)
                .size()
                .reset_index(name='n')
                .merge(tot_by_no, on='attempt_no', how='left')
            )
            by_no['share_within_attempt_no'] = by_no['n'] / by_no['total'].replace(0, np.nan)
            by_no = by_no.sort_values(['attempt_no', 'n'], ascending=[True, False])
            for _, r in by_no.iterrows():
                archetype_by_attempt_no.append(
                    {
                        'attempt_no': int(r['attempt_no']),
                        'archetype': str(r['archetype']),
                        'n': int(r['n']),
                        'total_at_attempt_no': int(r['total']),
                        'share_within_attempt_no': _json_float(r['share_within_attempt_no']),
                    }
                )

        trans_rows: List[Tuple[str, str]] = []
        for (_, _), g in seq.groupby(['user_id', 'target_name'], sort=False):
            g2 = g.sort_values(['attempt_no', 'attempt_uuid']).drop_duplicates(subset=['attempt_no'], keep='last')
            if len(g2) < 2:
                continue
            an = g2['attempt_no'].to_numpy()
            ap = g2['archetype'].astype(str).to_numpy()
            for i in range(1, len(g2)):
                if an[i] == an[i - 1] + 1:
                    trans_rows.append((ap[i - 1], ap[i]))
        if trans_rows:
            tr = pd.DataFrame(trans_rows, columns=['from_archetype', 'to_archetype'])
            tcnts = tr.groupby(['from_archetype', 'to_archetype'], sort=False).size().reset_index(name='n')
            tcnts = tcnts.sort_values('n', ascending=False)
            for _, r in tcnts.iterrows():
                archetype_transitions.append(
                    {
                        'from_archetype': str(r['from_archetype']),
                        'to_archetype': str(r['to_archetype']),
                        'n': int(r['n']),
                    }
                )
            same_rate = float((tr['from_archetype'] == tr['to_archetype']).mean())

        # Example sequences: longest chains per (user, target), cap 12 samples
        samples: List[Dict[str, Any]] = []
        for (u, tn), g in seq.groupby(['user_id', 'target_name'], sort=False):
            g2 = g.sort_values('attempt_no').drop_duplicates(subset=['attempt_no'], keep='last')
            if len(g2) < 2:
                continue
            parts = [f"{int(r['attempt_no'])}:{r['archetype']}" for _, r in g2.iterrows()]
            samples.append(
                {
                    'user_id': str(u),
                    'target_name': str(tn),
                    'n_steps': len(g2),
                    'sequence': ' → '.join(parts),
                }
            )
        samples.sort(key=lambda s: -s['n_steps'])
        sequence_samples = samples[:12]

    per_attempt_total = int(len(df))
    df_sorted = df.sort_values(['target_name', 'attempt_uuid'], ascending=[True, True])
    truncated = len(df_sorted) > int(per_attempt_limit)
    if truncated:
        df_sorted = df_sorted.head(int(per_attempt_limit))

    per_attempt_tags = []
    for _, r in df_sorted.iterrows():
        per_attempt_tags.append(
            {
                'attempt_uuid': str(r.get('attempt_uuid') or ''),
                'target_color_id': int(r['target_color_id']) if pd.notna(r.get('target_color_id')) else None,
                'target_name': str(r.get('target_name') or '(unknown)'),
                'attempt_no': int(r['attempt_no']) if pd.notna(r.get('attempt_no')) else None,
                'n_actions': int(r['n_actions']) if pd.notna(r.get('n_actions')) else None,
                'final_delta_e': _json_float(r.get('final_delta_e')),
                'slope_first30': _json_float(r.get('slope_first30')),
                'slope_last30': _json_float(r.get('slope_last30')),
                'convergence_slope_delta': _json_float(r.get('convergence_slope_delta')),
                'improve_rate': _json_float(r.get('improve_rate')),
                'volatility_sd_gain': _json_float(r.get('volatility_sd_gain')),
                'oscillation_count': int(r['n_gain_sign_reversals']) if pd.notna(r.get('n_gain_sign_reversals')) else None,
                'reversal_rate': _json_float(r.get('reversal_rate')),
                'efficiency_per_action': _json_float(r.get('efficiency_per_action')),
                'efficiency_per_sec': _json_float(r.get('efficiency_per_sec')),
                'expected_final_delta_e': _json_float(r.get('expected_final_delta_e')),
                'stopping_quality_deltae_vs_expected': _json_float(r.get('stopping_quality_deltae_vs_expected')),
                'archetype': str(r.get('archetype') or ''),
            }
        )

    by_color = (
        df.groupby(['target_name', 'archetype'], dropna=False)
        .size()
        .reset_index(name='n_attempts')
    )
    color_totals = (
        by_color.groupby('target_name', dropna=False)['n_attempts']
        .sum()
        .rename('total_attempts_for_color')
        .reset_index()
    )
    by_color = by_color.merge(color_totals, on='target_name', how='left')
    by_color['share_within_color'] = by_color['n_attempts'] / by_color['total_attempts_for_color'].replace(0, np.nan)
    by_color = by_color.sort_values(
        ['total_attempts_for_color', 'target_name', 'n_attempts'],
        ascending=[False, True, False],
    )
    distribution_by_color = []
    for _, r in by_color.iterrows():
        distribution_by_color.append(
            {
                'target_name': str(r.get('target_name') or '(unknown)'),
                'archetype': str(r.get('archetype') or ''),
                'n_attempts': int(r.get('n_attempts') or 0),
                'total_attempts_for_color': int(r.get('total_attempts_for_color') or 0),
                'share_within_color': _json_float(r.get('share_within_color')),
            }
        )

    return {
        'per_attempt_total': per_attempt_total,
        'per_attempt_truncated': bool(truncated),
        'per_attempt_tags': per_attempt_tags,
        'distribution_by_color': distribution_by_color,
        'archetype_by_attempt_no': archetype_by_attempt_no,
        'archetype_transitions': archetype_transitions,
        'archetype_transition_same_rate': _json_float(same_rate),
        'archetype_sequence_samples': sequence_samples,
    }


def plot_mv_scatter_matrix(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Scatter plot matrix (SPLOM) of attempt-level numeric metrics."""
    df = _multivariate_attempt_metrics()
    cols = _select_mv_columns(df)
    if len(cols) < 2:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'Need at least two numeric metrics for SPLOM', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    sub = df[cols].dropna()
    if len(sub) > 4000:
        sub = sub.sample(4000, random_state=42)
    n = len(cols)
    fig, axes = plt.subplots(n, n, figsize=(2.4 * n, 2.4 * n))
    if n == 1:
        axes = np.array([[axes]])
    for i in range(n):
        for j in range(n):
            ax = axes[i][j]
            ci = cols[i]
            cj = cols[j]
            yi = sub[ci].to_numpy(dtype=float)
            xj = sub[cj].to_numpy(dtype=float)
            if i == j:
                ax.hist(yi, bins=30, color='#3949ab', edgecolor='white', linewidth=0.4)
            else:
                ax.scatter(xj, yi, s=6, alpha=0.25, color='#1d4ed8', edgecolors='none')
                if len(xj) >= 3:
                    sx = pd.Series(xj)
                    sy = pd.Series(yi)
                    if sx.nunique() > 1 and sy.nunique() > 1:
                        r = float(sx.corr(sy))
                        ax.text(
                            0.04,
                            0.94,
                            f'r={r:.2f}',
                            transform=ax.transAxes,
                            fontsize=7,
                            color='#111827',
                            va='top',
                            ha='left',
                            bbox=dict(facecolor='white', alpha=0.7, edgecolor='none', pad=1.5),
                        )
            if i < n - 1:
                ax.set_xticklabels([])
            else:
                ax.set_xlabel(_MV_LABEL_MAP.get(cj, cj), fontsize=8)
            if j > 0:
                ax.set_yticklabels([])
            else:
                ax.set_ylabel(_MV_LABEL_MAP.get(ci, ci), fontsize=8)
            ax.tick_params(labelsize=7)
    fig.suptitle(f'Scatter plot matrix (n={len(sub)})', fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    return _fig_to_png(fig)


def plot_mv_parallel_boxplots(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Parallel boxplots of standardized attempt metrics (multidim boxplot)."""
    df = _multivariate_attempt_metrics()
    cols = _select_mv_columns(df)
    fig, ax = plt.subplots(figsize=(9, 4.8))
    if len(cols) == 0:
        ax.text(0.5, 0.5, 'No numeric metrics', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    z = df[cols].copy()
    for c in cols:
        col = z[c]
        sd = float(col.std(ddof=0))
        z[c] = (col - col.mean()) / (sd if sd > 0 else 1.0)
    data = [z[c].dropna().to_numpy(dtype=float) for c in cols]
    bp = ax.boxplot(
        data,
        labels=[_MV_LABEL_MAP.get(c, c) for c in cols],
        showfliers=True,
        patch_artist=True,
        widths=0.55,
        medianprops=dict(color='#dc2626', linewidth=1.4),
        flierprops=dict(marker='o', markerfacecolor='#94a3b8', markeredgecolor='none', markersize=3, alpha=0.5),
    )
    palette = plt.cm.get_cmap('tab10', max(len(cols), 1))
    for idx, patch in enumerate(bp['boxes']):
        patch.set_facecolor(palette(idx))
        patch.set_alpha(0.55)
        patch.set_edgecolor('#1f2937')
    ax.axhline(0, color='#94a3b8', linestyle='--', linewidth=0.9)
    ax.set_ylabel('z-score (per metric)')
    ax.set_title(f'Parallel boxplots of standardized metrics (n={len(df)})')
    ax.tick_params(axis='x', labelrotation=10)
    return _fig_to_png(fig)


def _kde_contour_panel(
    ax,
    x: np.ndarray,
    y: np.ndarray,
    *,
    x_label: str,
    y_label: str,
    title: str,
    cmap: str = 'viridis',
) -> Optional[Any]:
    if len(x) < 20:
        ax.text(0.5, 0.5, 'Not enough data for KDE', ha='center', va='center')
        ax.axis('off')
        return None
    try:
        from scipy.stats import gaussian_kde
    except Exception as e:
        ax.text(0.5, 0.5, f'scipy not available: {e}', ha='center', va='center')
        ax.axis('off')
        return None
    finite = np.isfinite(x) & np.isfinite(y)
    x = x[finite]
    y = y[finite]
    if len(x) < 20:
        ax.text(0.5, 0.5, 'Not enough finite data for KDE', ha='center', va='center')
        ax.axis('off')
        return None
    xl, xh = np.quantile(x, [0.005, 0.995])
    yl, yh = np.quantile(y, [0.005, 0.995])
    mask = (x >= xl) & (x <= xh) & (y >= yl) & (y <= yh)
    x = x[mask]
    y = y[mask]
    if len(x) < 20:
        ax.text(0.5, 0.5, 'Not enough trimmed data for KDE', ha='center', va='center')
        ax.axis('off')
        return None
    if len(x) > 6000:
        rng = np.random.default_rng(42)
        idx = rng.choice(len(x), 6000, replace=False)
        x = x[idx]
        y = y[idx]
    try:
        xy = np.vstack([x, y])
        kde = gaussian_kde(xy)
    except Exception as e:
        ax.text(0.5, 0.5, f'KDE failed: {e}', ha='center', va='center')
        ax.axis('off')
        return None
    xs = np.linspace(float(x.min()), float(x.max()), 90)
    ys = np.linspace(float(y.min()), float(y.max()), 90)
    xx, yy = np.meshgrid(xs, ys)
    zz = kde(np.vstack([xx.ravel(), yy.ravel()])).reshape(xx.shape)
    cf = ax.contourf(xx, yy, zz, levels=12, cmap=cmap)
    ax.contour(xx, yy, zz, levels=6, colors='white', linewidths=0.6, alpha=0.6)
    ax.scatter(x, y, s=4, alpha=0.18, color='white', edgecolors='none')
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    ax.set_title(title)
    return cf


def plot_mv_contour_pairs(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Multidim histogram approximation: 2D KDE contours for two key pairs."""
    df = _multivariate_attempt_metrics()
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    if len(df) == 0:
        for ax in axes:
            ax.text(0.5, 0.5, 'No multivariate data', ha='center', va='center')
            ax.axis('off')
        return _fig_to_png(fig)
    cf1 = _kde_contour_panel(
        axes[0],
        df['duration_sec'].to_numpy(dtype=float),
        df['final_delta_e'].to_numpy(dtype=float),
        x_label='duration_sec',
        y_label='final_delta_e',
        title='Joint density: ΔE × duration',
    )
    if cf1 is not None:
        fig.colorbar(cf1, ax=axes[0], fraction=0.046, pad=0.04, label='density')
    if 'num_steps' in df.columns and df['num_steps'].notna().sum() >= 20:
        cf2 = _kde_contour_panel(
            axes[1],
            df['num_steps'].to_numpy(dtype=float),
            df['final_delta_e'].to_numpy(dtype=float),
            x_label='num_steps',
            y_label='final_delta_e',
            title='Joint density: ΔE × num_steps',
            cmap='magma',
        )
        if cf2 is not None:
            fig.colorbar(cf2, ax=axes[1], fraction=0.046, pad=0.04, label='density')
    else:
        axes[1].text(0.5, 0.5, 'num_steps not available', ha='center', va='center')
        axes[1].axis('off')
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_mv_mahalanobis(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Mahalanobis-distance outlier detection over attempt metrics."""
    df = _multivariate_attempt_metrics()
    cols = _select_mv_columns(df)
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8))
    if len(cols) < 2 or len(df) < 10:
        for ax in axes:
            ax.text(0.5, 0.5, 'Need ≥10 rows and ≥2 metrics for Mahalanobis', ha='center', va='center')
            ax.axis('off')
        return _fig_to_png(fig)
    sub = df[cols].dropna()
    if len(sub) < 10:
        for ax in axes:
            ax.text(0.5, 0.5, 'Not enough complete rows', ha='center', va='center')
            ax.axis('off')
        return _fig_to_png(fig)
    X = sub.to_numpy(dtype=float)
    mu = X.mean(axis=0)
    cov = np.cov(X.T)
    try:
        inv = np.linalg.pinv(cov)
    except Exception as e:
        for ax in axes:
            ax.text(0.5, 0.5, f'cov inversion failed: {e}', ha='center', va='center')
            ax.axis('off')
        return _fig_to_png(fig)
    diff = X - mu
    md2 = np.einsum('ij,jk,ik->i', diff, inv, diff)
    md2 = np.maximum(md2, 0.0)
    md = np.sqrt(md2)
    try:
        from scipy.stats import chi2
        threshold = float(np.sqrt(chi2.ppf(0.975, df=len(cols))))
    except Exception:
        threshold = float(np.sqrt(np.quantile(md2, 0.975)))
    is_out = md > threshold
    n_out = int(is_out.sum())

    ax0 = axes[0]
    ax0.hist(md, bins=40, color='#3949ab', edgecolor='white', alpha=0.85)
    ax0.axvline(
        threshold,
        color='#dc2626',
        linestyle='--',
        linewidth=1.5,
        label=f'97.5% threshold (df={len(cols)}): {threshold:.2f}',
    )
    ax0.set_xlabel('Mahalanobis distance')
    ax0.set_ylabel('Count')
    ax0.set_title(f'Mahalanobis distance distribution ({n_out} flagged of {len(md)})')
    ax0.legend(fontsize=8)

    ax1 = axes[1]
    if 'duration_sec' in cols and 'final_delta_e' in cols:
        ix_x = cols.index('duration_sec')
        ix_y = cols.index('final_delta_e')
        sc = ax1.scatter(
            X[:, ix_x],
            X[:, ix_y],
            c=md,
            s=14,
            alpha=0.55,
            cmap='viridis',
            edgecolors='none',
        )
        if n_out > 0:
            ax1.scatter(
                X[is_out, ix_x],
                X[is_out, ix_y],
                s=80,
                facecolors='none',
                edgecolors='#dc2626',
                linewidths=1.4,
                label=f'outlier (n={n_out})',
                zorder=3,
            )
            ax1.legend(fontsize=8, loc='upper right')
        fig.colorbar(sc, ax=ax1, fraction=0.046, pad=0.04, label='Mahalanobis dist')
        ax1.set_xlabel('duration_sec')
        ax1.set_ylabel('final_delta_e')
        ax1.set_title('Outliers on duration × ΔE projection')
    else:
        ax1.text(0.5, 0.5, 'duration / final_delta_e not in metric set', ha='center', va='center')
        ax1.axis('off')
    fig.tight_layout()
    return _fig_to_png(fig)


def plot_mv_qq_grid(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    """Normal-QQ plots for each attempt metric."""
    df = _multivariate_attempt_metrics()
    cols = _select_mv_columns(df)
    if len(cols) == 0:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, 'No metric data for QQ plots', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    try:
        from scipy import stats as _scipy_stats
    except Exception as e:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, f'scipy not available: {e}', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    n = len(cols)
    rows = int(np.ceil(n / 2.0))
    cols_per_row = 2 if n > 1 else 1
    fig, axes = plt.subplots(rows, cols_per_row, figsize=(4.8 * cols_per_row, 3.6 * rows))
    axes_flat = np.atleast_1d(axes).ravel()
    for i, c in enumerate(cols):
        ax = axes_flat[i]
        s = pd.to_numeric(df[c], errors='coerce').dropna().to_numpy(dtype=float)
        if len(s) < 5:
            ax.text(0.5, 0.5, f'Not enough data for {c}', ha='center', va='center')
            ax.axis('off')
            continue
        if len(s) > 5000:
            rng = np.random.default_rng(42)
            s = rng.choice(s, 5000, replace=False)
        (osm, osr), (slope, intercept, r) = _scipy_stats.probplot(s, dist='norm')
        ax.scatter(osm, osr, s=10, alpha=0.5, color='#1d4ed8', edgecolors='none')
        xs = np.array([float(osm.min()), float(osm.max())])
        ax.plot(xs, slope * xs + intercept, color='#dc2626', linewidth=1.4)
        ax.set_xlabel('Theoretical quantiles', fontsize=8)
        ax.set_ylabel('Sample quantiles', fontsize=8)
        ax.set_title(f'{_MV_LABEL_MAP.get(c, c)}  (R²={r ** 2:.3f})', fontsize=9)
        ax.tick_params(labelsize=7)
    for j in range(n, axes_flat.size):
        axes_flat[j].axis('off')
    fig.suptitle('QQ-plots vs normal', fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return _fig_to_png(fig)


def plot_mixed_models_vif(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    from . import mixed_models_stat

    return mixed_models_stat.plot_mixed_models_vif(_, __)


def plot_mixed_models_coef_logde(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    from . import mixed_models_stat

    return mixed_models_stat.plot_mixed_models_coef_logde(_, __)


def plot_mixed_models_coef_similarity(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    from . import mixed_models_stat

    return mixed_models_stat.plot_mixed_models_coef_similarity(_, __)


def plot_mixed_models_perfect_ratio_or(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    from . import mixed_models_stat

    return mixed_models_stat.plot_mixed_models_perfect_ratio_or(_, __)


PLOT_BUILDERS: Dict[str, Callable[[pd.DataFrame, pd.DataFrame], bytes]] = {
    'fw_hist_final_de': plot_fw_hist_final_de,
    'fw_hist_log_de': plot_fw_hist_log_de,
    'fw_hist_duration': plot_fw_hist_duration,
    'daily_volume': plot_daily_volume,
    'user_bucket': plot_user_bucket,
    'fw_trial_median_de': plot_fw_trial_median_de,
    'fw_trial_success': plot_fw_trial_success,
    'fw_trial_dur': plot_fw_trial_dur,
    'fw_oscillation': plot_fw_oscillation,
    'fw_trajectory': plot_fw_trajectory,
    'h1_improving': plot_h1_improving,
    'h1_steps': plot_h1_steps,
    'h2_improving': plot_h2_improving,
    'h2_gain': plot_h2_gain,
    'h4_improving': plot_h4_improving,
    'h4_gain': plot_h4_gain,
    'h5_stop_success': plot_h5_stop_success,
    'age_pyramid': plot_age_pyramid,
    'plays_per_user': plot_plays_per_user,
    'attempts_per_color': plot_attempts_per_color,
    'deltae_per_color': plot_deltae_per_color,
    'elapsed_per_color': plot_elapsed_per_color,
    'controlled_deltae_by_attempt': plot_controlled_deltae_by_attempt,
    'controlled_elapsed_by_attempt': plot_controlled_elapsed_by_attempt,
    'deltae_elapsed_scatter': plot_deltae_elapsed_scatter,
    'scatter_deltae_vs_steps': plot_scatter_deltae_vs_steps,
    'scatter_duration_vs_steps': plot_scatter_duration_vs_steps,
    'correlation_heatmap': plot_correlation_heatmap,
    'correlation_league': plot_correlation_league,
    'deltae_vs_similarity': plot_deltae_vs_similarity,
    'mv_scatter_matrix': plot_mv_scatter_matrix,
    'mv_parallel_boxplots': plot_mv_parallel_boxplots,
    'mv_contour_pairs': plot_mv_contour_pairs,
    'mv_mahalanobis': plot_mv_mahalanobis,
    'mv_qq_grid': plot_mv_qq_grid,
    'mixed_models_vif': plot_mixed_models_vif,
    'mixed_models_coef_logde': plot_mixed_models_coef_logde,
    'mixed_models_coef_similarity': plot_mixed_models_coef_similarity,
    'mixed_models_perfect_ratio_or': plot_mixed_models_perfect_ratio_or,
    'attempt_deltae_timeline': plot_attempt_deltae_timeline,
    'archetype_deltae_trajectories': lambda a, e: plot_archetype_deltae_trajectories(a, e, archetype=None),
    'archetype_compare_trajectories': lambda a, e: plot_archetype_compare_trajectories(a, e, archetypes=None),
    'archetype_share_by_attempt_no': plot_archetype_share_by_attempt_no,
    'archetype_transition_heatmap': plot_archetype_transition_heatmap,
}

ALLOWED_PLOT_IDS = frozenset(list(PLOT_BUILDERS.keys()) + ['fw_attempt_network'])


def get_plot_png(plot_id: str, plot_options: Optional[Dict[str, Any]] = None) -> bytes:
    if plot_id not in ALLOWED_PLOT_IDS:
        raise ValueError(f'unknown plot: {plot_id}')
    att, ev = get_dataframes()
    if plot_id in (
        'fw_attempt_network',
        'attempt_deltae_timeline',
        'archetype_deltae_trajectories',
        'archetype_compare_trajectories',
    ):
        opts = plot_options or {}
        if plot_id == 'fw_attempt_network':
            return plot_fw_attempt_network(
                att,
                ev,
                attempt_uuid=opts.get('attempt_uuid'),
                target_color_id=opts.get('target_color_id'),
            )
        if plot_id == 'archetype_deltae_trajectories':
            return plot_archetype_deltae_trajectories(
                att,
                ev,
                archetype=opts.get('archetype'),
            )
        if plot_id == 'archetype_compare_trajectories':
            return plot_archetype_compare_trajectories(
                att,
                ev,
                archetypes=opts.get('archetypes'),
            )
        return plot_attempt_deltae_timeline(
            att,
            ev,
            attempt_uuid=opts.get('attempt_uuid'),
            target_color_id=opts.get('target_color_id'),
            view_mode=opts.get('view_mode'),
        )
    return PLOT_BUILDERS[plot_id](att, ev)
