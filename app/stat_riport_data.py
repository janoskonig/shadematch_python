"""Data builders for /stat/riport — the supervisor-facing, gamut-only,
exploratory report (Hungarian UI).

Everything here is restricted to the gamut target set
(``target_colors.color_type='gamut'``, 332 colours) and the clean gamut era
(``GAMUT_ERA_START_UTC``; a handful of earlier rows are dropped). Exploratory
and descriptive only — no fitted models.

Two bundles keep the page responsive:
  * ``build_report()``  – overview + 24h trend, recruitment/sample, catalog +
                          difficulty, performance + learning.
  * ``build_steps()``   – step-level behaviour + rule-based strategy phenotypes
                          (heavier: aggregates ~190k mixing_attempt_events).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List

import numpy as np
from sqlalchemy import text

from . import db

# Clean gamut era start (see PR #24). Shared with routes' guard/constant.
GAMUT_ERA_START_UTC = '2026-07-06 08:00:00'

# Gamut-attempt filter, inlined per statement (CTE scope is per-statement).
# Full set: every gamut attempt (used for engagement/volume counts + the 3.3
# outcome breakdown, where the excluded outcomes are shown).
_GA = (
    "SELECT ma.* FROM mixing_attempts ma "
    "JOIN target_colors tc ON tc.id = ma.target_color_id "
    "WHERE tc.color_type = 'gamut' AND ma.user_id IS NOT NULL "
    "AND ma.attempt_started_server_ts >= :era"
)
# Analysis set: only mixes that carry a meaningful final outcome — completed
# (saved) or a deliberate give-up (skipped). Restarted (reset/restart) and
# abandoned/unknown attempts are process artifacts and are EXCLUDED from every
# outcome / quality / behaviour analysis (ΔE, learning, difficulty, steps).
# They appear once only, in the 3.3 outcome breakdown.
_ANALYSIS_REASONS = "('saved_match','saved_stop','skipped')"
_ANALYSIS_COND = f"end_reason IN {_ANALYSIS_REASONS}"
_GA_ANALYSIS = _GA + f" AND ma.end_reason IN {_ANALYSIS_REASONS}"
# Naive-UTC "now" so comparisons with attempt_started_server_ts (naive UTC) work.
_NOW = "(now() at time zone 'utc')"


def _rows(q: str, **p) -> List[Dict[str, Any]]:
    return [dict(r) for r in db.session.execute(text(q), p).mappings().all()]


def _one(q: str, **p) -> Dict[str, Any]:
    r = db.session.execute(text(q), p).mappings().first()
    return dict(r) if r else {}


def _f(x):
    """Cast Decimal/None safely to float|None for JSON."""
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rgb_to_lab(rows):
    """Batch sRGB (0–255 dicts with r/g/b) → CIELAB (L, a, b) via colormath —
    the app's canonical colour path. Returns list of (L, a, b) tuples."""
    from colormath.color_objects import sRGBColor, LabColor
    from colormath.color_conversions import convert_color
    out = []
    for row in rows:
        c = convert_color(
            sRGBColor(row['r'] / 255.0, row['g'] / 255.0, row['b'] / 255.0),
            LabColor)
        out.append((float(c.lab_l), float(c.lab_a), float(c.lab_b)))
    return out


def _median(xs):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def _pearson(xs: List[float], ys: List[float]):
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None
             and not math.isnan(x) and not math.isnan(y)]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


# ========================================================================== #
# BUNDLE 1: overview + trend, recruitment, catalog + difficulty, performance
# ========================================================================== #
def build_report(era: str = GAMUT_ERA_START_UTC) -> Dict[str, Any]:
    p = {'era': era}

    # ---- 1) Overview headline numbers ------------------------------------ #
    # Volume/engagement counts on the full set; ΔE / quality metrics on the
    # analysis set (saved + skipped only). completion_rate stays full (it is an
    # outcome-composition number, same as the 3.3 breakdown).
    overview = _one(
        f"""
        WITH ga AS ({_GA}), gaa AS ({_GA_ANALYSIS})
        SELECT
          (SELECT COUNT(*)::bigint FROM target_colors WHERE color_type='gamut') AS gamut_targets_total,
          (SELECT COUNT(DISTINCT target_color_id)::bigint FROM ga) AS gamut_targets_played,
          (SELECT COUNT(*)::bigint FROM ga) AS total_plays,
          (SELECT COUNT(*)::bigint FROM gaa) AS analyzed_plays,
          (SELECT COUNT(DISTINCT user_id)::bigint FROM ga) AS distinct_users,
          (SELECT COUNT(*)::bigint FROM users) AS registered_users,
          (SELECT MIN(attempt_started_server_ts)::text FROM ga) AS first_play_ts,
          (SELECT MAX(attempt_started_server_ts)::text FROM ga) AS last_play_ts,
          (SELECT AVG(final_delta_e)::double precision FROM gaa WHERE final_delta_e IS NOT NULL) AS mean_delta_e,
          (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)::double precision FROM gaa WHERE final_delta_e IS NOT NULL) AS median_delta_e,
          (SELECT percentile_cont(0.90) WITHIN GROUP (ORDER BY final_delta_e)::double precision FROM gaa WHERE final_delta_e IS NOT NULL) AS p90_delta_e,
          (SELECT AVG(CASE WHEN final_delta_e<=0.01 THEN 1.0 ELSE 0.0 END)::double precision FROM gaa WHERE final_delta_e IS NOT NULL) AS perfect_rate,
          (SELECT AVG(CASE WHEN final_delta_e<=2.0 THEN 1.0 ELSE 0.0 END)::double precision FROM gaa WHERE final_delta_e IS NOT NULL) AS acceptable_rate,
          (SELECT AVG(CASE WHEN end_reason IN ('saved_match','saved_stop') THEN 1.0 ELSE 0.0 END)::double precision FROM ga) AS completion_rate,
          (SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)::double precision
             FROM gaa WHERE duration_sec IS NOT NULL AND duration_sec>0 AND duration_sec<=300) AS median_duration_sec
        """, **p) or {}
    overview = {k: _f(v) if k not in ('first_play_ts', 'last_play_ts') else v
                for k, v in overview.items()}

    # ---- 1) 24h trend (current vs previous 24h window) ------------------- #
    win = _rows(
        f"""
        WITH ga AS ({_GA}),
        w AS (
          SELECT *,
            CASE WHEN attempt_started_server_ts >= {_NOW} - interval '24 hours' THEN 'cur'
                 WHEN attempt_started_server_ts >= {_NOW} - interval '48 hours' THEN 'prev'
                 END AS win
          FROM ga
        )
        SELECT win,
          COUNT(*)::bigint AS plays,
          COUNT(DISTINCT user_id)::bigint AS users,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)
            FILTER (WHERE {_ANALYSIS_COND} AND final_delta_e IS NOT NULL)::double precision AS median_de,
          AVG(CASE WHEN final_delta_e<=0.01 THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE {_ANALYSIS_COND} AND final_delta_e IS NOT NULL)::double precision AS perfect_rate,
          AVG(CASE WHEN end_reason IN ('saved_match','saved_stop') THEN 1.0 ELSE 0.0 END)::double precision AS completion_rate,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)
            FILTER (WHERE {_ANALYSIS_COND} AND duration_sec>0 AND duration_sec<=300)::double precision AS median_time
        FROM w WHERE win IS NOT NULL GROUP BY win
        """, **p)
    trend = {'cur': {}, 'prev': {}}
    for r in win:
        trend[r['win']] = {k: _f(v) for k, v in r.items() if k != 'win'}
    new_users_24h = _one(
        f"SELECT COUNT(*)::bigint AS n FROM users WHERE created_at >= {_NOW} - interval '24 hours'"
    ).get('n', 0)
    trend['new_users_24h'] = int(new_users_24h or 0)

    # ---- 1) 14-day daily series (sparklines) ----------------------------- #
    spark = _rows(
        f"""
        WITH ga AS ({_GA})
        SELECT to_char(date_trunc('day', attempt_started_server_ts),'YYYY-MM-DD') AS day,
          COUNT(*)::bigint AS plays,
          COUNT(DISTINCT user_id)::bigint AS users,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)
            FILTER (WHERE {_ANALYSIS_COND} AND final_delta_e IS NOT NULL)::double precision AS median_de,
          AVG(CASE WHEN final_delta_e<=0.01 THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE {_ANALYSIS_COND} AND final_delta_e IS NOT NULL)::double precision AS perfect_rate,
          AVG(CASE WHEN end_reason IN ('saved_match','saved_stop') THEN 1.0 ELSE 0.0 END)::double precision AS completion_rate,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_sec)
            FILTER (WHERE {_ANALYSIS_COND} AND duration_sec>0 AND duration_sec<=300)::double precision AS median_time
        FROM ga
        WHERE attempt_started_server_ts >= {_NOW} - interval '14 days'
        GROUP BY 1 ORDER BY 1
        """, **p)

    # ---- 3) Recruitment funnel ------------------------------------------- #
    funnel = _one(
        f"""
        SELECT
          (SELECT COUNT(*)::bigint FROM users) AS registered,
          (SELECT COUNT(DISTINCT user_id)::bigint FROM mixing_attempts WHERE user_id IS NOT NULL) AS any_players,
          (SELECT COUNT(DISTINCT user_id)::bigint FROM ({_GA}) g) AS gamut_players,
          (SELECT COUNT(DISTINCT user_id)::bigint FROM ({_GA}) g
             WHERE end_reason IN ('saved_match','saved_stop')) AS gamut_completers
        """, **p)

    # attempts-per-player buckets + raw counts (for Lorenz / distribution)
    per_user = _rows(
        f"SELECT user_id, COUNT(*)::bigint AS n FROM ({_GA}) g GROUP BY user_id ORDER BY n DESC", **p)
    user_counts = [int(r['n']) for r in per_user]
    total_user_plays = sum(user_counts)
    n_users = len(user_counts)
    top_share = None
    if n_users and total_user_plays:
        k = max(1, round(n_users * 0.1))
        top_share = sum(user_counts[:k]) / total_user_plays

    def _bucket(n):
        if n <= 1:
            return '1'
        if n <= 3:
            return '2–3'
        if n <= 9:
            return '4–9'
        if n <= 24:
            return '10–24'
        if n <= 49:
            return '25–49'
        return '50+'
    apo = {}
    for c in user_counts:
        b = _bucket(c)
        apo[b] = apo.get(b, 0) + 1
    attempts_per_player = [{'bucket': b, 'n_players': apo.get(b, 0)}
                           for b in ['1', '2–3', '4–9', '10–24', '25–49', '50+']]

    # dropoff: how many players reach >= k gamut plays
    dropoff = _rows(
        f"""
        WITH pu AS (SELECT user_id, COUNT(*) AS n FROM ({_GA}) g GROUP BY user_id)
        SELECT k, COUNT(*) FILTER (WHERE n >= k)::bigint AS players
        FROM pu, generate_series(1, 20) AS k
        GROUP BY k ORDER BY k
        """, **p)

    # Outcome breakdown — only two things carry analysable information: a
    # completed (perfect) mix, or a give-up with a subjective rating
    # (identical / acceptable / unacceptable). Everything else (reset, abandon
    # without rating, unknown) is pooled as "egyéb (nem elemzett)".
    outcomes = _rows(
        f"""
        WITH ga AS ({_GA})
        SELECT
          CASE
            WHEN ga.end_reason IN ('saved_match','saved_stop') THEN 'Teljesítve (tökéletes)'
            WHEN ms.skip_perception = 'identical'     THEN 'Feladva – azonos'
            WHEN ms.skip_perception = 'acceptable'    THEN 'Feladva – elfogadható'
            WHEN ms.skip_perception = 'unacceptable'  THEN 'Feladva – nem elfogadható'
            ELSE 'Egyéb (nem elemzett)'
          END AS outcome,
          COUNT(*)::bigint AS n
        FROM ga LEFT JOIN mixing_sessions ms ON ms.attempt_uuid = ga.attempt_uuid
        GROUP BY 1
        """, **p)

    # analyzable attempts (reconstructable step path)
    analyzable = _one(
        f"""
        WITH ga AS ({_GA}),
        ev AS (SELECT attempt_uuid, COUNT(*) FILTER (WHERE delta_e_after IS NOT NULL) AS nde
               FROM mixing_attempt_events GROUP BY attempt_uuid)
        SELECT COUNT(*)::bigint AS total,
          COUNT(*) FILTER (WHERE ga.num_steps IS NOT NULL AND ga.num_steps > 0)::bigint AS with_steps,
          COUNT(*) FILTER (WHERE COALESCE(ev.nde,0) > 0)::bigint AS with_reconstructable
        FROM ga LEFT JOIN ev ON ev.attempt_uuid = ga.attempt_uuid
        """, **p)

    # ---- 3.6) environment / sample biases (gamut-scoped) ----------------- #
    def _env(select_expr, order='n_attempts DESC'):
        return _rows(
            f"""WITH ga AS ({_GA})
                SELECT {select_expr} AS label, COUNT(*)::bigint AS n_attempts
                FROM ga GROUP BY 1 ORDER BY {order}""", **p)

    DEV_EXPR = (
        "CASE WHEN COALESCE(NULLIF(client_env_json->>'device_kind',''),'')<>'' THEN client_env_json->>'device_kind' "
        "WHEN (client_env_json->>'ua') ~* 'iPad' OR ((client_env_json->>'ua') ~* 'Android' AND (client_env_json->>'ua') !~* 'Mobile') THEN 'tablet' "
        "WHEN (client_env_json->>'ua') ~* 'iPhone|iPod|Mobile' THEN 'mobile' "
        "WHEN (client_env_json->>'ua') IS NOT NULL THEN 'desktop' ELSE 'ismeretlen' END")
    BROWSER_EXPR = (
        "CASE "
        "WHEN (client_env_json->>'ua') ~* 'Edg/|EdgiOS' THEN 'Edge' "
        "WHEN (client_env_json->>'ua') ~* 'SamsungBrowser' THEN 'Samsung Internet' "
        "WHEN (client_env_json->>'ua') ~* 'CriOS|Chrome|Chromium' THEN 'Chrome' "
        "WHEN (client_env_json->>'ua') ~* 'FxiOS|Firefox' THEN 'Firefox' "
        "WHEN (client_env_json->>'ua') ~* 'Safari' THEN 'Safari' "
        "ELSE 'egyéb' END")
    bias_device = _env(DEV_EXPR)
    bias_browser = _env(BROWSER_EXPR)
    # device × browser joint counts for the mosaic (marimekko) plot
    device_browser = _rows(
        f"""WITH ga AS ({_GA})
            SELECT {DEV_EXPR} AS device, {BROWSER_EXPR} AS browser, COUNT(*)::bigint AS n
            FROM ga GROUP BY 1, 2""", **p)
    bias_gamut = _env("COALESCE(NULLIF(client_env_json->>'color_gamut',''),'ismeretlen')")
    bias_fullscreen = _env(
        "CASE WHEN (client_env_json->>'fullscreen')='true' THEN 'teljes képernyő' "
        "WHEN (client_env_json->>'fullscreen')='false' THEN 'ablakos' ELSE 'ismeretlen' END")
    bias_hour = _env(
        "CASE WHEN (client_env_json->>'hour_of_day_local') ~ '^[0-9]+$' "
        "THEN (client_env_json->>'hour_of_day_local') ELSE 'ismeretlen' END", order='label')

    demo = _one(
        f"""
        WITH gu AS (SELECT DISTINCT user_id FROM ({_GA}) g)
        SELECT COUNT(*)::bigint AS n_players,
          COUNT(*) FILTER (WHERE lower(coalesce(u.gender,'')) LIKE 'f%')::bigint AS n_female,
          COUNT(*) FILTER (WHERE lower(coalesce(u.gender,'')) LIKE 'm%')::bigint AS n_male,
          percentile_cont(0.50) WITHIN GROUP (ORDER BY EXTRACT(YEAR FROM age(CURRENT_DATE, u.birthdate)))
            FILTER (WHERE u.birthdate IS NOT NULL)::double precision AS median_age
        FROM gu JOIN users u ON u.id = gu.user_id
        """, **p)

    from .tz_country import tz_to_country
    by_tz = _rows(
        f"""WITH ga AS ({_GA})
            SELECT client_env_json->>'tz' AS tz, COUNT(*)::bigint AS n FROM ga
            WHERE client_env_json IS NOT NULL AND client_env_json->>'tz' IS NOT NULL
            GROUP BY 1""", **p)
    cagg: Dict[str, int] = {}
    for r in by_tz:
        _cc, name = tz_to_country(r['tz'])
        lab = name or 'Ismeretlen'
        cagg[lab] = cagg.get(lab, 0) + int(r['n'])
    bias_country = sorted(({'label': k, 'n_attempts': v} for k, v in cagg.items()),
                          key=lambda r: -r['n_attempts'])

    # ---- 4) Catalog + difficulty ----------------------------------------- #
    catalog_classes = _rows(
        "SELECT classification, COUNT(*)::bigint AS n FROM target_colors "
        "WHERE color_type='gamut' GROUP BY 1 ORDER BY n DESC")
    # Catalog map: every gamut target in the a*–b* plane, coloured by its own
    # sRGB, with the Xiao skin zone flagged + hull (reproduces the artifact at
    # scripts/plot_gamut_targets_ab.py, live from the DB).
    cat = _rows(
        "SELECT id, name, name_hu, classification, r, g, b FROM target_colors "
        "WHERE color_type='gamut' ORDER BY catalog_order")
    catalog_points = []
    for row, (L, a, b) in zip(cat, _rgb_to_lab(cat)):
        catalog_points.append({
            'tid': row['id'],
            'a': round(a, 2), 'bb': round(b, 2), 'L': round(L, 1),
            'r': row['r'], 'g': row['g'], 'blue': row['b'],
            'skin': row['classification'] == 'even_gamut_v2_skin',
            'name': row['name_hu'] or row['name'],
        })
    try:
        from .gamut_lab import skin_gamut
        skin_hull = skin_gamut().get('hull', [])
    except Exception:
        skin_hull = []

    # Structural difficulty = total drops in the reference recipe. gamut rows
    # leave sum_drop_count null but carry the five per-pigment drop_* columns.
    drop_dist = _rows(
        "SELECT (drop_white+drop_black+drop_red+drop_yellow+drop_blue) AS drops, "
        "COUNT(*)::bigint AS n_colors FROM target_colors "
        "WHERE color_type='gamut' AND drop_white IS NOT NULL GROUP BY 1 ORDER BY 1")
    # coverage buckets
    coverage = _rows(
        f"""
        WITH per_target AS (
          SELECT tc.id, COUNT(ma.attempt_uuid) FILTER (
            WHERE ma.user_id IS NOT NULL AND ma.attempt_started_server_ts >= :era) AS n
          FROM target_colors tc LEFT JOIN mixing_attempts ma ON ma.target_color_id = tc.id
          WHERE tc.color_type='gamut' GROUP BY tc.id)
        SELECT CASE WHEN n=0 THEN '0' WHEN n<=2 THEN '1–2' WHEN n<=5 THEN '3–5'
                    WHEN n<=10 THEN '6–10' WHEN n<=20 THEN '11–20' ELSE '21+' END AS bucket,
               COUNT(*)::bigint AS n_targets
        FROM per_target GROUP BY 1
        """, **p)
    cover_stats = _one(
        f"""
        WITH per_target AS (
          SELECT tc.id, COUNT(ma.attempt_uuid) FILTER (
            WHERE ma.user_id IS NOT NULL AND ma.attempt_started_server_ts >= :era) AS n
          FROM target_colors tc LEFT JOIN mixing_attempts ma ON ma.target_color_id = tc.id
          WHERE tc.color_type='gamut' GROUP BY tc.id)
        SELECT percentile_cont(0.50) WITHIN GROUP (ORDER BY n) FILTER (WHERE n>0)::double precision AS median_played,
               MAX(n)::bigint AS max_n
        FROM per_target
        """, **p)

    # per-color difficulty: structural (sum_drop_count) vs observed (ΔE / giveup / steps)
    per_color = _rows(
        f"""
        WITH ga AS ({_GA_ANALYSIS}),
        per AS (
          SELECT target_color_id AS id, COUNT(*)::bigint AS n_plays,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY final_delta_e)
              FILTER (WHERE final_delta_e IS NOT NULL)::double precision AS med_de,
            AVG(CASE WHEN end_reason IN ('skipped','abandoned') THEN 1.0 ELSE 0.0 END)::double precision AS giveup_rate,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY num_steps)
              FILTER (WHERE num_steps IS NOT NULL)::double precision AS med_steps
          FROM ga GROUP BY target_color_id)
        SELECT tc.id, tc.name, tc.name_hu, tc.classification,
               (tc.drop_white+tc.drop_black+tc.drop_red+tc.drop_yellow+tc.drop_blue) AS sum_drops,
               ((tc.drop_white>0)::int + (tc.drop_black>0)::int + (tc.drop_red>0)::int
                + (tc.drop_yellow>0)::int + (tc.drop_blue>0)::int) AS n_pigments,
               tc.r, tc.g, tc.b,
               per.n_plays, per.med_de, per.giveup_rate, per.med_steps
        FROM per JOIN target_colors tc ON tc.id = per.id
        ORDER BY per.med_de DESC NULLS LAST
        """, **p)
    for r in per_color:
        for kk in ('med_de', 'giveup_rate', 'med_steps'):
            r[kk] = _f(r[kk])
        r['n_plays'] = int(r['n_plays']) if r['n_plays'] is not None else 0
        r['sum_drops'] = int(r['sum_drops']) if r['sum_drops'] is not None else None
        r['n_pigments'] = int(r['n_pigments']) if r['n_pigments'] is not None else None
    # correlation structural vs observed (colours with >= 3 plays)
    solid = [r for r in per_color if (r['n_plays'] or 0) >= 3
             and r['sum_drops'] is not None]
    corr_drops_de = _pearson([float(r['sum_drops']) for r in solid],
                             [r['med_de'] for r in solid])
    corr_drops_giveup = _pearson([float(r['sum_drops']) for r in solid],
                                 [r['giveup_rate'] for r in solid])

    # ---- 4.2 Difficulty as a multivariate composite (PCA) ---------------- #
    # Per-colour features: structural (drops, #pigments) + observed (median ΔE,
    # give-up rate, median steps). Standardise, then PCA (SVD). PC1 = a single
    # "difficulty" axis; PC1 is oriented so higher = harder (aligned to ΔE).
    PCA_FEATURES = [('sum_drops', 'cseppszám'), ('n_pigments', 'pigmentszám'),
                    ('med_de', 'medián ΔE'), ('giveup_rate', 'feladási arány'),
                    ('med_steps', 'medián lépésszám')]
    pca_rows = [r for r in per_color
                if (r['n_plays'] or 0) >= 3
                and all(r[k] is not None for k, _ in PCA_FEATURES)]
    difficulty_pca: Dict[str, Any] = {'points': [], 'loadings': [], 'explained': [],
                                      'features': [lbl for _, lbl in PCA_FEATURES]}
    if len(pca_rows) >= 6:
        X = np.array([[float(r[k]) for k, _ in PCA_FEATURES] for r in pca_rows])
        mu, sd = X.mean(0), X.std(0)
        sd[sd == 0] = 1.0
        Z = (X - mu) / sd
        U, S, Vt = np.linalg.svd(Z, full_matrices=False)
        evr = (S ** 2) / (S ** 2).sum()
        scores = U * S
        de_idx = [i for i, (k, _) in enumerate(PCA_FEATURES) if k == 'med_de'][0]
        if np.corrcoef(scores[:, 0], X[:, de_idx])[0, 1] < 0:  # PC1: higher = harder
            scores[:, 0] *= -1
            Vt[0] *= -1
        # PC2 sign is arbitrary; fix it so the drop-count loading is positive →
        # PC2 up = structurally long recipe RELATIVE to its observed accuracy.
        drops_idx = [i for i, (k, _) in enumerate(PCA_FEATURES) if k == 'sum_drops'][0]
        if Vt.shape[0] > 1 and scores.shape[1] > 1 and Vt[1][drops_idx] < 0:
            scores[:, 1] *= -1
            Vt[1] *= -1
        difficulty_pca = {
            'features': [lbl for _, lbl in PCA_FEATURES],
            'explained': [float(x) for x in evr[:len(PCA_FEATURES)]],
            'loadings': [[float(v) for v in Vt[i]] for i in range(min(2, Vt.shape[0]))],
            'points': [{'pc1': float(scores[i, 0]),
                        'pc2': float(scores[i, 1]) if scores.shape[1] > 1 else 0.0,
                        'name': r['name_hu'] or r['name'], 'r': r['r'], 'g': r['g'], 'blue': r['b'],
                        'sum_drops': r['sum_drops'], 'med_de': r['med_de']}
                       for i, r in enumerate(pca_rows)],
        }

    # ---- 5) Region-based learning ---------------------------------------- #
    # A single colour is mixed at most ~twice, but neighbouring colours (which
    # share a region yet have different recipes) are mixed more often. So
    # "learning" is measured as generalisation within a region. For the report
    # the space is split into SIXTEEN regions covering the whole gamut (see the
    # partition comment below). Every target belongs to exactly one region;
    # each gets a Hungarian label and the sRGB colour of its centroid for the
    # map / curve plots.
    from .gamut_lab import _lab_to_srgb

    # The 10-macro-cluster partition (6 k-means background + 4 Xiao skin
    # clusters) lives in app/clusters.py — shared with match drawing, so the
    # report's learning regions and the in-game matches use the same families.
    from .clusters import (
        MACRO_ORDER, cluster_assignments, cluster_display_names, cluster_labs,
    )
    region_by_target: Dict[int, str] = cluster_assignments()
    region_labs: Dict[str, List[tuple]] = cluster_labs()
    _macro_names: Dict[str, str] = cluster_display_names()

    def _macro_name(code):
        return _macro_names.get(code, code)
    # tag the catalog map points with their region id
    for cp in catalog_points:
        cp['reg'] = region_by_target.get(cp.get('tid'))

    reg_att = _rows(
        f"""SELECT user_id, target_color_id, final_delta_e
            FROM ({_GA_ANALYSIS}) s
            WHERE final_delta_e IS NOT NULL
            ORDER BY user_id, attempt_started_server_ts""", **p)
    # The exposure axis is STANDARDISED to relative progress: within each
    # (user, region) sequence the k-th of n mixes sits at (k−1)/(n−1) ∈ [0,1],
    # binned into quarters. This makes short and long sequences comparable
    # (a 3-mix and an 8-mix run both span 0→100%). Sequences with a single mix
    # carry no within-region repetition and are excluded from the curves.
    BIN_POS = [12.5, 37.5, 62.5, 87.5]     # bin midpoints in percent
    totals: Dict[Any, int] = defaultdict(int)
    region_mixes: Dict[str, int] = defaultdict(int)
    region_played: Dict[str, set] = defaultdict(set)   # region -> played target ids
    for r in reg_att:
        reg = region_by_target.get(r['target_color_id'])
        if reg is None:
            continue
        region_mixes[reg] += 1
        region_played[reg].add(r['target_color_id'])
        totals[(r['user_id'], reg)] += 1
    _seq: Dict[Any, int] = defaultdict(int)
    _rl: Dict[int, List[float]] = defaultdict(list)      # bin -> ΔE (pooled)
    _rlr: Dict[tuple, List[float]] = defaultdict(list)   # (region, bin) -> ΔE
    for r in reg_att:
        reg = region_by_target.get(r['target_color_id'])
        if reg is None:
            continue
        key = (r['user_id'], reg)
        _seq[key] += 1
        tot = totals[key]
        if tot < 2:
            continue
        pos = (_seq[key] - 1) / (tot - 1)
        b = min(3, int(pos * 4))
        de = float(r['final_delta_e'])
        _rl[b].append(de)
        _rlr[(reg, b)].append(de)
    # Median ΔE is ~0 (most analysed mixes are perfect saves), so the learning
    # signal lives in the mean (pulled by the give-up tail) and the success rate.
    region_learning = [{'pos': BIN_POS[b], 'n': len(_rl[b]),
                        'mean_de': (sum(_rl[b]) / len(_rl[b])),
                        'perfect_rate': sum(1 for v in _rl[b] if v <= 0.01) / len(_rl[b])}
                       for b in sorted(_rl) if _rl[b]]

    # Region catalogue: the five families, centroid Lab → sRGB swatch.
    regions_payload: List[Dict[str, Any]] = []
    for reg in MACRO_ORDER:
        labs = region_labs.get(reg, [])
        if not labs:
            continue
        Lc = sum(x[0] for x in labs) / len(labs)
        ac = sum(x[1] for x in labs) / len(labs)
        bc = sum(x[2] for x in labs) / len(labs)
        rr, gg, bb = _lab_to_srgb(Lc, ac, bc)
        regions_payload.append({
            'id': reg,
            'name': _macro_name(reg),
            'r': rr, 'g': gg, 'blue': bb,
            'L': round(Lc, 1), 'a': round(ac, 1), 'b': round(bc, 1),
            'n_targets': len(labs), 'n_mixes': int(region_mixes.get(reg, 0)),
            'n_played': len(region_played.get(reg, ())),
        })
    regions_payload.sort(key=lambda x: -x['n_mixes'])

    # Learning curves for ALL five families (whole-space coverage).
    region_curves: List[Dict[str, Any]] = []
    for x in regions_payload:
        pts = []
        for b in range(4):
            des = _rlr.get((x['id'], b), [])
            if len(des) >= 3:
                pts.append({'pos': BIN_POS[b], 'n': len(des),
                            'mean_de': sum(des) / len(des),
                            'perfect_rate': sum(1 for v in des if v <= 0.01) / len(des)})
        if len(pts) >= 2:
            region_curves.append({'id': x['id'], 'name': x['name'],
                                  'r': x['r'], 'g': x['g'], 'blue': x['blue'],
                                  'points': pts})

    # ---- Non-uniform thresholds: perceptibility / acceptability by L,a,b -- #
    # Subjective give-up ratings (identical / acceptable / unacceptable) carry
    # the ΔE at give-up (mixing_sessions.delta_e) and the target's Lab. Per
    # tertile band of L*, a*, b* I take the median ΔE of each rating category;
    # the perceptibility threshold is the midpoint of the identical↔acceptable
    # medians, the acceptability threshold the midpoint of acceptable↔
    # unacceptable. Purely descriptive (medians), shows the thresholds are not
    # constant across colour space.
    rated = _rows(
        f"""
        SELECT ms.skip_perception AS rating, ms.delta_e AS de, tc.r, tc.g, tc.b
        FROM mixing_sessions ms
        JOIN mixing_attempts ma ON ma.attempt_uuid = ms.attempt_uuid
        JOIN target_colors tc ON tc.id = ma.target_color_id
        WHERE tc.color_type = 'gamut' AND ma.attempt_started_server_ts >= :era
          AND ms.skip_perception IN ('identical','acceptable','unacceptable')
          AND ms.delta_e IS NOT NULL
        """, **p)
    thresholds = {'L': [], 'a': [], 'b': [], 'n_total': len(rated), 'overall': {}}
    if rated:
        for row, (L, a, b) in zip(rated, _rgb_to_lab(rated)):
            row['de'] = float(row['de'])
            row['_L'], row['_a'], row['_b'] = L, a, b
        # overall (non-factorised) thresholds — the study-wide average used for
        # the attempt-number learning curve.
        _ov = {rt: _median([r['de'] for r in rated if r['rating'] == rt])
               for rt in ('identical', 'acceptable', 'unacceptable')}
        thresholds['overall'] = {
            'med_identical': _ov['identical'], 'med_acceptable': _ov['acceptable'],
            'med_unacceptable': _ov['unacceptable'],
            'perceptibility': (None if _ov['identical'] is None or _ov['acceptable'] is None
                               else (_ov['identical'] + _ov['acceptable']) / 2.0),
            'acceptability': (None if _ov['acceptable'] is None or _ov['unacceptable'] is None
                              else (_ov['acceptable'] + _ov['unacceptable']) / 2.0),
        }
        AXES = [('L', '_L', 'L* (világosság)'), ('a', '_a', 'a* (zöld→piros)'),
                ('b', '_b', 'b* (kék→sárga)')]
        BAND_LABELS = ['alacsony', 'közép', 'magas']
        for key, attr, _title in AXES:
            vals = sorted(r[attr] for r in rated)
            n = len(vals)
            c1 = vals[n // 3]
            c2 = vals[(2 * n) // 3]
            bands = [[], [], []]
            for r in rated:
                bi = 0 if r[attr] < c1 else (1 if r[attr] < c2 else 2)
                bands[bi].append(r)
            for bi, sub in enumerate(bands):
                mid = {rt: _median([r['de'] for r in sub if r['rating'] == rt])
                       for rt in ('identical', 'acceptable', 'unacceptable')}
                perc = (None if mid['identical'] is None or mid['acceptable'] is None
                        else (mid['identical'] + mid['acceptable']) / 2.0)
                acc = (None if mid['acceptable'] is None or mid['unacceptable'] is None
                       else (mid['acceptable'] + mid['unacceptable']) / 2.0)
                thresholds[key].append({
                    'band': BAND_LABELS[bi], 'n': len(sub),
                    'med_identical': mid['identical'], 'med_acceptable': mid['acceptable'],
                    'med_unacceptable': mid['unacceptable'],
                    'perceptibility': perc, 'acceptability': acc,
                })

    # ---- 5) Performance / learning --------------------------------------- #
    de_hist = _rows(
        f"""WITH ga AS ({_GA_ANALYSIS})
            SELECT width_bucket(final_delta_e, 0, 10, 20) AS bucket, COUNT(*)::bigint AS n
            FROM ga WHERE final_delta_e IS NOT NULL GROUP BY 1 ORDER BY 1""", **p)
    daily_volume = _rows(
        f"""WITH ga AS ({_GA})
            SELECT to_char(date_trunc('day', attempt_started_server_ts),'YYYY-MM-DD') AS day,
                   COUNT(*)::bigint AS n FROM ga GROUP BY 1 ORDER BY 1""", **p)

    return {
        'status': 'success',
        'era_start_utc': era,
        'overview': overview,
        'trend': trend,
        'spark': [{k: _f(v) if k != 'day' else v for k, v in r.items()} for r in spark],
        # recruitment
        'funnel': funnel,
        'attempts_per_player': attempts_per_player,
        'per_user_counts': user_counts,
        'user_concentration': {'n_users': n_users, 'total_plays': total_user_plays,
                               'top10pct_share': top_share},
        'dropoff': [{'k': int(r['k']), 'players': int(r['players'])} for r in dropoff],
        'outcomes': outcomes,
        'analyzable': analyzable,
        'demographics': {k: _f(v) if k == 'median_age' else v for k, v in demo.items()},
        'bias_device': bias_device,
        'bias_browser': bias_browser,
        'device_browser': device_browser,
        'thresholds': thresholds,
        'bias_gamut': bias_gamut,
        'bias_fullscreen': bias_fullscreen,
        'bias_hour': bias_hour,
        'bias_country': bias_country,
        # catalog + difficulty
        'catalog_classes': catalog_classes,
        'catalog_points': catalog_points,
        'skin_hull': skin_hull,
        'drop_dist': [{'drops': int(r['drops']), 'n_colors': int(r['n_colors'])} for r in drop_dist],
        'coverage_buckets': coverage,
        'coverage_stats': cover_stats,
        'per_color': per_color,
        'difficulty_corr': {'drops_vs_de': corr_drops_de, 'drops_vs_giveup': corr_drops_giveup},
        'difficulty_pca': difficulty_pca,
        # performance
        'de_hist': de_hist,
        'region_learning': region_learning,
        'regions': regions_payload,
        'region_curves': region_curves,
        'daily_volume': daily_volume,
    }


# ========================================================================== #
# BUNDLE 2: step-level behaviour + rule-based strategy phenotypes
# ========================================================================== #
def build_steps(era: str = GAMUT_ERA_START_UTC) -> Dict[str, Any]:
    p = {'era': era}

    # per-attempt step features from the event log (analysis set only)
    feats = _rows(
        f"""
        WITH ga AS ({_GA_ANALYSIS}),
        steps AS (
          SELECT e.attempt_uuid,
            COUNT(*) FILTER (WHERE e.action_type IN ('add','remove'))::int AS n_actions,
            COUNT(*) FILTER (WHERE e.action_type='remove')::int AS n_remove,
            COUNT(*) FILTER (WHERE e.delta_e_before IS NOT NULL AND e.delta_e_after IS NOT NULL
                             AND e.delta_e_after < e.delta_e_before)::int AS n_improve,
            COUNT(*) FILTER (WHERE e.delta_e_before IS NOT NULL AND e.delta_e_after IS NOT NULL
                             AND e.delta_e_after > e.delta_e_before)::int AS n_worsen,
            COUNT(*) FILTER (WHERE e.delta_e_before IS NOT NULL AND e.delta_e_after IS NOT NULL)::int AS n_measured
          FROM mixing_attempt_events e JOIN ga ON ga.attempt_uuid = e.attempt_uuid
          GROUP BY e.attempt_uuid)
        SELECT s.attempt_uuid, s.n_actions, s.n_remove, s.n_improve, s.n_worsen, s.n_measured,
               ga.final_delta_e, ga.end_reason
        FROM steps s JOIN ga ON ga.attempt_uuid = s.attempt_uuid
        """, **p)

    # aggregate improving rate + remove rate
    tot_measured = sum(r['n_measured'] or 0 for r in feats)
    tot_improve = sum(r['n_improve'] or 0 for r in feats)
    tot_worsen = sum(r['n_worsen'] or 0 for r in feats)
    tot_actions = sum(r['n_actions'] or 0 for r in feats)
    tot_remove = sum(r['n_remove'] or 0 for r in feats)
    step_summary = {
        'n_attempts': len(feats),
        'improving_rate': (tot_improve / tot_measured) if tot_measured else None,
        'worsening_rate': (tot_worsen / tot_measured) if tot_measured else None,
        'remove_rate': (tot_remove / tot_actions) if tot_actions else None,
        'total_actions': tot_actions,
        'total_measured_steps': tot_measured,
    }

    # raw per-attempt points for the steps↔outcome scatter (action count vs
    # final ΔE), flagged by whether the attempt was given up.
    scatter_steps = [
        {'s': int(r['n_actions']), 'de': round(float(r['final_delta_e']), 2),
         'g': 1 if r.get('end_reason') in ('skipped', 'abandoned') else 0}
        for r in feats
        if r['final_delta_e'] is not None and (r['n_actions'] or 0) > 0
    ]

    # pigment choice (add events only), gamut-scoped, analysis set
    pigments = _rows(
        f"""
        WITH ga AS ({_GA_ANALYSIS})
        SELECT e.action_color AS color, COUNT(*)::bigint AS n
        FROM mixing_attempt_events e JOIN ga ON ga.attempt_uuid = e.attempt_uuid
        WHERE e.action_type='add' AND e.action_color IS NOT NULL
        GROUP BY 1 ORDER BY n DESC
        """, **p)

    # worsening steps vs give-up: mean worsen-rate for completed vs gave-up attempts
    def _grp(rr):
        er = rr.get('end_reason')
        if er in ('saved_match', 'saved_stop'):
            return 'teljesített'
        if er in ('skipped', 'abandoned'):
            return 'feladta'
        return None
    grp_stats: Dict[str, Dict[str, float]] = {}
    for lab in ('teljesített', 'feladta'):
        sub = [r for r in feats if _grp(r) == lab and (r['n_measured'] or 0) > 0]
        if not sub:
            continue
        wr = [r['n_worsen'] / r['n_measured'] for r in sub]
        na = [r['n_actions'] or 0 for r in sub]
        grp_stats[lab] = {
            'n': len(sub),
            'mean_worsen_rate': sum(wr) / len(wr),
            'mean_actions': sum(na) / len(na),
        }

    # step-count vs outcome: median final ΔE by action-count bucket
    def _abucket(n):
        if n <= 2:
            return '1–2'
        if n <= 5:
            return '3–5'
        if n <= 10:
            return '6–10'
        if n <= 20:
            return '11–20'
        if n <= 40:
            return '21–40'
        return '41+'
    order_ab = ['1–2', '3–5', '6–10', '11–20', '21–40', '41+']
    ab: Dict[str, List[float]] = {b: [] for b in order_ab}
    for r in feats:
        if r['final_delta_e'] is not None and (r['n_actions'] or 0) > 0:
            ab[_abucket(r['n_actions'])].append(float(r['final_delta_e']))
    def _median(xs):
        if not xs:
            return None
        xs = sorted(xs)
        m = len(xs) // 2
        return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2
    steps_vs_outcome = [{'bucket': b, 'n': len(ab[b]), 'median_de': _median(ab[b])}
                        for b in order_ab]

    # ---- rule-based strategy phenotypes ---------------------------------- #
    # Features per attempt: length, improve-rate, remove-rate. Exploratory,
    # deterministic rules (not a clustering model).
    pheno_counts: Dict[str, int] = {}
    pheno_out: Dict[str, Dict[str, Any]] = {}
    PHENO_ORDER = ['rövid próba', 'egyenletes javító', 'próbálgató',
                   'maratoni keverő', 'kitartó finomító', 'vegyes']

    def classify(r):
        na = r['n_actions'] or 0
        nm = r['n_measured'] or 0
        if na < 3:
            return 'rövid próba'
        imp = (r['n_improve'] / nm) if nm else 0.0
        rem = (r['n_remove'] / na) if na else 0.0
        if imp >= 0.70 and rem <= 0.15:
            return 'egyenletes javító'
        if rem >= 0.30 or imp <= 0.45:
            return 'próbálgató'
        if na >= 40:
            return 'maratoni keverő'
        if na >= 12:
            return 'kitartó finomító'
        return 'vegyes'

    for lab in PHENO_ORDER:
        pheno_out[lab] = {'des': [], 'giveup': 0, 'n': 0}
    for r in feats:
        lab = classify(r)
        pheno_counts[lab] = pheno_counts.get(lab, 0) + 1
        o = pheno_out[lab]
        o['n'] += 1
        if r['final_delta_e'] is not None:
            o['des'].append(float(r['final_delta_e']))
        if r.get('end_reason') in ('skipped', 'abandoned'):
            o['giveup'] += 1
    phenotypes = []
    for lab in PHENO_ORDER:
        o = pheno_out[lab]
        if not o['n']:
            continue
        phenotypes.append({
            'label': lab,
            'n': o['n'],
            'median_de': _median(o['des']),
            'giveup_rate': (o['giveup'] / o['n']) if o['n'] else None,
        })

    # ---- data-driven strategy clusters (k-means) ------------------------- #
    # Alternative to the fixed rules: standardise five per-attempt behaviour
    # features and cluster them. No predefined rules — each cluster is described
    # post-hoc by its centroid (mean features), which is what makes it readable.
    CLUST_FEATURES = [('hossz (cselekvés)', lambda r: math.log1p(r['n_actions'])),
                      ('javító arány', lambda r: r['n_improve'] / r['n_measured']),
                      ('rontó arány', lambda r: r['n_worsen'] / r['n_measured']),
                      ('eltávolítási arány', lambda r: r['n_remove'] / r['n_actions']),
                      ('végső ΔE', lambda r: float(r['final_delta_e']))]
    crows = [r for r in feats
             if (r['n_measured'] or 0) >= 3 and (r['n_actions'] or 0) >= 3
             and r['final_delta_e'] is not None]
    clusters: List[Dict[str, Any]] = []
    cluster_k = 0
    if len(crows) >= 30:
        Xc = np.array([[f(r) for _, f in CLUST_FEATURES] for r in crows])
        mu, sd = Xc.mean(0), Xc.std(0)
        sd[sd == 0] = 1.0
        Zc = (Xc - mu) / sd
        K = 6
        rng = np.random.default_rng(42)
        # k-means++ init
        cen = [Zc[rng.integers(len(Zc))]]
        for _ in range(K - 1):
            d2 = np.min([((Zc - c) ** 2).sum(1) for c in cen], axis=0)
            cen.append(Zc[rng.choice(len(Zc), p=d2 / d2.sum())])
        C = np.array(cen)
        assign = np.zeros(len(Zc), dtype=int)
        for _ in range(100):
            new_assign = np.argmin(((Zc[:, None, :] - C[None, :, :]) ** 2).sum(2), axis=1)
            new_C = np.array([Zc[new_assign == k].mean(0) if (new_assign == k).any() else C[k]
                              for k in range(K)])
            if np.array_equal(new_assign, assign) and np.allclose(new_C, C):
                assign = new_assign
                break
            assign, C = new_assign, new_C
        cluster_k = K
        for k in range(K):
            members = [crows[i] for i in range(len(crows)) if assign[i] == k]
            if not members:
                continue
            feat_means = {lbl: float(np.mean([f(r) for r in members]))
                          for lbl, f in CLUST_FEATURES}
            # report length on the raw (not log) scale
            feat_means['hossz (cselekvés)'] = float(np.mean([r['n_actions'] for r in members]))
            des = [float(r['final_delta_e']) for r in members]
            gv = sum(1 for r in members if r.get('end_reason') in ('skipped', 'abandoned'))
            clusters.append({
                'id': k + 1, 'orig_k': k, 'n': len(members),
                'mean_actions': feat_means['hossz (cselekvés)'],
                'improve_rate': feat_means['javító arány'],
                'worsen_rate': feat_means['rontó arány'],
                'remove_rate': feat_means['eltávolítási arány'],
                'median_de': _median(des),
                'giveup_rate': gv / len(members),
            })
        clusters.sort(key=lambda c: -c['n'])

        # Post-hoc verbal label from the centroid profile (interpretation aid).
        def _cluster_label(c):
            if c['median_de'] is not None and c['median_de'] > 5:
                return 'sikertelen (nagy ΔE)'
            if c['mean_actions'] < 20:
                return 'rövid, hatékony' if c['improve_rate'] >= 0.7 else 'rövid, ingadozó'
            long_word = 'nagyon hosszú' if c['mean_actions'] >= 60 else 'hosszú'
            if c['remove_rate'] >= 0.2:
                return long_word + ', sok visszavonás, feladó'
            if c['giveup_rate'] >= 0.5:
                return long_word + ', gyakran feladó'
            return long_word + ', kitartó'
        for rank, c in enumerate(clusters, start=1):
            c['label'] = _cluster_label(c)
            c['display_id'] = rank

        # 2-D projection of the standardized feature space (PCA via SVD) so the
        # clusters can be SEEN, not just tabulated. Points carry the display id.
        Uc, Sc, Vtc = np.linalg.svd(Zc - Zc.mean(0), full_matrices=False)
        proj = Uc * Sc
        evr_c = (Sc ** 2) / (Sc ** 2).sum()
        rank_of = {c['orig_k']: c['display_id'] for c in clusters}
        cluster_scatter = [
            {'x': round(float(proj[i, 0]), 3), 'y': round(float(proj[i, 1]), 3),
             'k': rank_of.get(int(assign[i]), 0)}
            for i in range(len(crows))
        ]
        cluster_evr = [float(evr_c[0]), float(evr_c[1]) if len(evr_c) > 1 else 0.0]
        # loadings of this display projection, so the page can explain what the
        # two viewing axes are made of (this PCA is separate from the colour-
        # difficulty PCA in section 4.3 — different units of analysis).
        cluster_proj_loadings = [[float(v) for v in Vtc[i]]
                                 for i in range(min(2, Vtc.shape[0]))]
        cluster_features = [lbl for lbl, _ in CLUST_FEATURES]
    else:
        cluster_scatter, cluster_evr = [], []
        cluster_proj_loadings, cluster_features = [], []

    return {
        'status': 'success',
        'era_start_utc': era,
        'step_summary': step_summary,
        'pigments': pigments,
        'giveup_worsen': grp_stats,
        'steps_vs_outcome': steps_vs_outcome,
        'scatter_steps': scatter_steps,
        'phenotypes': phenotypes,
        'clusters': clusters,
        'cluster_k': cluster_k,
        'cluster_scatter': cluster_scatter,
        'cluster_evr': cluster_evr,
        'cluster_proj_loadings': cluster_proj_loadings,
        'cluster_features': cluster_features,
    }
