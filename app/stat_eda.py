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
    ax.bar(np.arange(len(top)), top.values, color='#4b5563')
    ax.set_xticks(np.arange(len(top)))
    ax.set_xticklabels(top.index.astype(str), rotation=70, fontsize=7)
    ax.set_ylabel('Plays')
    ax.set_title('Top users by number of plays')
    return _fig_to_png(fig)


def plot_attempts_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_df()
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No attempts data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    g = att.groupby('target_name', dropna=False).size().sort_values(ascending=False).head(20)
    ax.bar(np.arange(len(g)), g.values, color='#0f766e')
    ax.set_xticks(np.arange(len(g)))
    ax.set_xticklabels(g.index.astype(str), rotation=55, ha='right', fontsize=8)
    ax.set_ylabel('Attempts')
    ax.set_title('Attempts per color (top 20)')
    return _fig_to_png(fig)


def plot_deltae_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_df()
    att = att[att['final_delta_e'].notna()]
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No ΔE data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    g = (
        att.groupby('target_name', dropna=False)['final_delta_e']
        .mean()
        .sort_values(ascending=False)
        .head(20)
    )
    ax.bar(np.arange(len(g)), g.values, color='#7c3aed')
    ax.set_xticks(np.arange(len(g)))
    ax.set_xticklabels(g.index.astype(str), rotation=55, ha='right', fontsize=8)
    ax.set_ylabel('Mean final ΔE')
    ax.set_title('Mean final ΔE per color (top 20 by volume)')
    return _fig_to_png(fig)


def plot_elapsed_per_color(_: pd.DataFrame, __: pd.DataFrame) -> bytes:
    fig, ax = plt.subplots(figsize=(9, 4.5))
    att = _dashboard_attempts_df()
    att = att[att['duration_sec'].notna() & (att['duration_sec'] <= 300)]
    if len(att) == 0:
        ax.text(0.5, 0.5, 'No elapsed-time data', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)
    g = (
        att.groupby('target_name', dropna=False)['duration_sec']
        .mean()
        .sort_values(ascending=False)
        .head(20)
    )
    ax.bar(np.arange(len(g)), g.values, color='#b45309')
    ax.set_xticks(np.arange(len(g)))
    ax.set_xticklabels(g.index.astype(str), rotation=55, ha='right', fontsize=8)
    ax.set_ylabel('Mean elapsed time (s)')
    ax.set_title('Mean elapsed time per color (<=300s, top 20 by volume)')
    return _fig_to_png(fig)


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

    # Reduce overplotting for dense datasets.
    if len(att) > 8000:
        att = att.sample(8000, random_state=42)

    ax.scatter(
        att['duration_sec'].values,
        att['final_delta_e'].values,
        s=10,
        alpha=0.35,
        color='#1d4ed8',
        edgecolors='none',
    )
    ax.set_xlabel('Elapsed time (s, <=300)')
    ax.set_ylabel('Final DeltaE')
    ax.set_title('Scatter: Final DeltaE vs elapsed time')
    return _fig_to_png(fig)


def plot_attempt_deltae_timeline(
    att: pd.DataFrame,
    ev: pd.DataFrame,
    *,
    attempt_uuid: Optional[str] = None,
    target_color_id: Optional[int] = None,
) -> bytes:
    fig, ax = plt.subplots(figsize=(11, 5.5))
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
        mode_title = f'Attempt DeltaE trajectory (…{resolved[-8:]})'
        line_alpha = 0.9
        point_alpha = 0.95
    elif target_color_id is not None:
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
        mode_title = f'Attempt DeltaE trajectories (all attempts, target_id={target_color_id})'
        line_alpha = 0.12
        point_alpha = 0.18
    else:
        # Fall back to all attempts in-sample when no explicit filter is provided.
        attempt_ids = ev['attempt_uuid'].dropna().astype(str).unique().tolist()
        if len(attempt_ids) == 0:
            ax.text(0.5, 0.5, 'No attempts to show', ha='center', va='center', fontsize=11)
            ax.axis('off')
            return _fig_to_png(fig)
        mode_title = 'Attempt DeltaE trajectories (all attempts in sample)'
        line_alpha = 0.06
        point_alpha = 0.10

    rows = ev[ev['attempt_uuid'].astype(str).isin(set(attempt_ids))].copy()
    rows = rows[rows['step_index'].notna()].copy()
    rows = rows.sort_values(['attempt_uuid', 'seq', 'step_index'])
    rows['delta_e_after'] = pd.to_numeric(rows['delta_e_after'], errors='coerce')
    rows = rows[rows['delta_e_after'].notna()]
    if len(rows) == 0:
        ax.text(0.5, 0.5, 'No DeltaE step rows for selected attempts', ha='center', va='center')
        ax.axis('off')
        return _fig_to_png(fig)

    y_min = float(rows['delta_e_after'].min())
    y_max = float(rows['delta_e_after'].max())
    x_max = 0
    is_single = len(attempt_ids) == 1
    for aid, part in rows.groupby('attempt_uuid', sort=False):
        p = part.sort_values(['seq', 'step_index'])
        x = np.arange(1, len(p) + 1)
        y = p['delta_e_after'].to_numpy(dtype=float)
        x_max = max(x_max, len(x))
        if is_single:
            action_type = p['action_type'].fillna('').astype(str).str.lower()
            c = np.where(action_type.eq('remove'), '#ef4444', '#2563eb')
            ax.plot(x, y, color='#334155', linewidth=1.8, alpha=line_alpha, zorder=1)
            ax.scatter(x, y, c=c, s=28, alpha=point_alpha, zorder=2)
        else:
            ax.plot(x, y, color='#334155', linewidth=1.1, alpha=line_alpha, zorder=1)
            ax.scatter(x, y, color='#1d4ed8', s=8, alpha=point_alpha, zorder=2)

    ax.axhline(0.0, color='#16a34a', linestyle='--', linewidth=1.2, alpha=0.8)
    pad = max(0.08 * (y_max - y_min), 0.25)
    ax.set_ylim(bottom=max(-0.05, y_min - pad), top=y_max + pad)
    ax.set_xlim(0.5, max(1.5, x_max + 0.5))
    ax.set_xlabel('Action timeline (ordered step rows per attempt)')
    ax.set_ylabel('DeltaE after action')
    ax.set_title(mode_title)

    if is_single:
        from matplotlib.lines import Line2D
        legend_items = [
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#2563eb', markersize=7, label='add / other'),
            Line2D([0], [0], marker='o', color='w', markerfacecolor='#ef4444', markersize=7, label='remove'),
            Line2D([0], [0], color='#16a34a', linestyle='--', linewidth=1.2, label='DeltaE = 0'),
        ]
        ax.legend(handles=legend_items, fontsize=8, loc='upper right')
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
    'attempt_deltae_timeline': plot_attempt_deltae_timeline,
    'archetype_deltae_trajectories': lambda a, e: plot_archetype_deltae_trajectories(a, e, archetype=None),
    'archetype_share_by_attempt_no': plot_archetype_share_by_attempt_no,
    'archetype_transition_heatmap': plot_archetype_transition_heatmap,
}

ALLOWED_PLOT_IDS = frozenset(list(PLOT_BUILDERS.keys()) + ['fw_attempt_network'])


def get_plot_png(plot_id: str, plot_options: Optional[Dict[str, Any]] = None) -> bytes:
    if plot_id not in ALLOWED_PLOT_IDS:
        raise ValueError(f'unknown plot: {plot_id}')
    att, ev = get_dataframes()
    if plot_id in ('fw_attempt_network', 'attempt_deltae_timeline', 'archetype_deltae_trajectories'):
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
        return plot_attempt_deltae_timeline(
            att,
            ev,
            attempt_uuid=opts.get('attempt_uuid'),
            target_color_id=opts.get('target_color_id'),
        )
    return PLOT_BUILDERS[plot_id](att, ev)
