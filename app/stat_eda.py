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
}

ALLOWED_PLOT_IDS = frozenset(list(PLOT_BUILDERS.keys()) + ['fw_attempt_network'])


def get_plot_png(plot_id: str, plot_options: Optional[Dict[str, Any]] = None) -> bytes:
    if plot_id not in ALLOWED_PLOT_IDS:
        raise ValueError(f'unknown plot: {plot_id}')
    att, ev = get_dataframes()
    if plot_id == 'fw_attempt_network':
        opts = plot_options or {}
        return plot_fw_attempt_network(
            att,
            ev,
            attempt_uuid=opts.get('attempt_uuid'),
            target_color_id=opts.get('target_color_id'),
        )
    return PLOT_BUILDERS[plot_id](att, ev)
