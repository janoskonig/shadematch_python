# Tests — Phase-0 safety net

These are **characterization tests**: they pin the behaviour the app has *today*
so the refactors in [`../ARCHITECTURE_REVIEW.md`](../ARCHITECTURE_REVIEW.md) can
be made without silently changing functionality. Assertions encode observed
current values, not idealised "should-be" values.

## What's covered

| File | Locks |
|------|-------|
| `test_color_drops.py` | The de-dup module: channel ordering + lenient/strict drop sums. |
| `test_color_science.py` | `app/utils.py` ΔE + spectrum→XYZ→RGB golden values. |
| `test_spectral_km.py` | Kubelka–Munk transforms, Lab conversion, and that the two CIEDE2000 implementations agree (the duplication C2 documents). |
| `test_gamification.py` | `process_progression` / `compute_quota_progress` (XP, streak, level, awards) against a small deterministic catalog. |

The recipe **solver** (`solve_recipe`/`solve_mix`) and the **stats dashboard**
(`stat_eda`, `mixed_models_stat`) are intentionally not covered yet — the solver
needs a seed threaded through for determinism, and the dashboard pulls in the
heavy scientific stack. Both are follow-ups.

## Running locally

The suite needs only a light subset of the project deps (no
pandas/statsmodels/matplotlib). The app's Debian-patched system setuptools can't
build the `colormath` sdist, so use a clean virtualenv:

```bash
python -m venv .venv && . .venv/bin/activate
pip install --upgrade pip setuptools wheel
pip install Flask==2.1.2 Werkzeug==2.1.2 Flask-SQLAlchemy==2.5.1 "SQLAlchemy>=1.4.41,<1.5" \
            numpy==1.24.3 scipy==1.10.1 colormath==3.0.0 python-dotenv==1.0.0 pytest==7.4.3
python -m pytest
```

Tests run against a throwaway SQLite database (created/dropped per session) via a
minimal Flask app wired only to the ORM, so no Postgres/SMTP/push is required.

## Note on colormath + NumPy

`colormath.color_diff.delta_e_cie2000` calls the removed `numpy.asscalar` and
raises on NumPy ≥ 1.20. The app already works around this with
`app/utils.delta_e_cie2000` (built on `color_diff_matrix`), which is what the
tests exercise. This is a concrete instance of the deprecated-dependency risk
(C12) and a reason to migrate off `colormath`.
