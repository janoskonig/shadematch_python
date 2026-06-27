# ShadeMatch — Architecture Review & Refactoring Strategy

*A senior-engineer reverse-engineering of the codebase: how it works, where it
hurts, and how to make it production-grade without changing behaviour.*

---

## 1. What this system is (reverse-engineered)

ShadeMatch is a **Flask PWA + research instrument** for a colour-vision /
colour-matching study run by Semmelweis University. Players mix five paints
(white, black, red, yellow, blue) to match a target colour; every interaction is
logged at high resolution for later statistical analysis. On top of the core
game sit gamification, a daily challenge, a psychophysics calibration game,
spectral/gamut "lab" tools, email + web-push reminders, research-consent
capture, and an admin statistics dashboard.

**Stack:** Flask 2.1 + Flask-SQLAlchemy 1.4 (single blueprint) · PostgreSQL
(SQLite-compatible) · vanilla-JS PWA (no build step, service worker) · a heavy
scientific stack (pandas, numpy, scipy, statsmodels, scikit-learn, matplotlib,
networkx, colormath) used only by the admin dashboard and the colour-science
endpoints · deployed on Render free tier (512 MB) under gunicorn (1 worker,
4 threads, `gthread`).

### 1.1 Component map

```
                              ┌────────────────────────────────────────────┐
   Browser (PWA)              │                 Flask app                   │
 ┌──────────────────┐        │  app/__init__.py  create_app() factory      │
 │ main.js (game)   │        │  app/routes.py    ~85 routes, ONE blueprint  │  ← god-module
 │ spectral_mixer.js│  HTTP  │     │                                        │
 │ lab/gamut/calib  │ ─────► │     ├─ gamification.py  XP/levels/streaks    │
 │ env_capture.js   │  JSON  │     ├─ next_action.py   recommendation       │
 │ cookie-consent.js│        │     ├─ calibration.py   psychophysics fit    │
 │ sw.js (cache)    │        │     ├─ spectral_km.py   Kubelka–Munk solver  │
 └──────────────────┘        │     ├─ gamut_lab.py     gamut hull/volume    │
                             │     ├─ email_utils.py   SMTP (synchronous)   │
                             │     ├─ stat_eda.py      47 matplotlib plots  │  ← lazy import
                             │     └─ mixed_models_stat.py  statsmodels LMM │  ← lazy import
                             │  app/utils.py     colour conversions (#1)    │
                             │  app/models.py    16 SQLAlchemy models       │
                             └──────────────────┬─────────────────────────┘
                                                │ SQLAlchemy ORM + raw SQL
                                                ▼
                                          PostgreSQL
```

### 1.2 Core data flow — one match attempt

1. **Register / login** (`POST /register`, `/login`) → `User` (random 6-char id),
   `ConsentRecord`, optional verification email (sent **synchronously**).
2. **Attempt lifecycle** — client opens an attempt and streams telemetry:
   - `POST /api/mixing-attempt/start-or-update` → upsert `MixingAttempt` header.
   - `POST /api/mixing-attempt/events` (or `/ingest`) → append `MixingAttemptEvent`
     rows (state-before/after, timing, step index).
3. **Terminal action** — `POST /save_session` (matched) or `/save_skip` (gave up):
   - writes a `MixingSession` summary row,
   - calls `process_progression()` → updates `UserProgress`,
     `UserTargetColorStats`, grants `UserAward`s, computes streak/level,
   - calls `grant_daily_mission_awards()`,
   - synthesises a terminal telemetry event,
   - commits and returns the progress/awards/missions payload.
4. **Recommendation** — `next_action.build_next_action()` tells the client what to
   do next (finish daily, save streak, nearest under-quota colour, refine).
5. **Analysis** — `/stat` endpoints pull whole tables into pandas and render PNGs
   / fit mixed-effects models on demand.

### 1.3 Data model (16 tables)

`User`, `Session` (legacy/unused), `TargetColor`, `MixingSession`,
`MixingAttempt`, `MixingAttemptEvent`, `UserProgress`, `UserTargetColorStats`,
`UserAward`, `DailyChallengeRun`, `DailyChallengeWinner`, `PushSubscription`,
`EmailVerificationToken`, `ConsentRecord`, `AnalyticsEvent`,
`CalibrationSession`, `CalibrationTrial`.

---

## 2. Clean architecture breakdown (target shape)

The system is functionally rich but **structurally flat**: HTTP handling, business
rules, persistence, heavy compute, and presentation are interleaved. The target
is a conventional layered architecture that keeps the *behaviour* identical:

```
┌─────────────────────────────────────────────────────────────┐
│ Presentation / Transport                                     │
│   app/api/*.py   thin Flask blueprints (parse → call → jsonify)│
│   static/*.js    PWA, one apiClient() wrapper                  │
├─────────────────────────────────────────────────────────────┤
│ Application / Services  (pure-ish, testable)                  │
│   services/progression.py   (was process_progression)         │
│   services/awards.py        (grant_* logic)                   │
│   services/attempts.py      (save_session/save_skip shared)    │
│   services/recommendation.py(next_action)                     │
│   services/stats/           data-access | metrics | render     │
├─────────────────────────────────────────────────────────────┤
│ Domain libraries (no DB, no Flask)                           │
│   color/constants.py  CMFs, illuminants, sRGB matrix          │
│   color/transforms.py xyz↔lab↔rgb, compand                    │
│   color/distance.py   ciede2000 (ONE implementation)          │
│   color/spectral.py   km/ks, mixing                           │
│   color_drops.py      ✅ paint-channel helpers (done)          │
├─────────────────────────────────────────────────────────────┤
│ Infrastructure                                               │
│   repositories/  query builders (catalog, stats, leaderboard) │
│   tasks/         async email + push (queue)                   │
│   cache/         Redis-backed (replaces in-process dicts)     │
│   models.py      schema only                                  │
└─────────────────────────────────────────────────────────────┘
```

The guiding rule: **routes parse and serialise; services decide; libraries
compute; repositories query.** No layer reaches "up".

---

## 3. Critical problem areas

Ranked by risk × effort. Severity: 🔴 high · 🟠 medium · 🟡 low.

### 🔴 C1 — `routes.py` is a 3,983-line god-module (~85 handlers, one blueprint)
All concerns live in one file with no service boundary. Handlers call
`db.session` directly, embed raw analytics SQL, orchestrate gamification, send
email, and build push payloads inline. Examples:
- `stat_summary()` ≈ 424 lines, 8+ raw SQL queries + lazy imports + caching.
- `push_send_daily()` ≈ 223 lines with nested helpers.
- `_ingest_mixing_events()` ≈ 114 lines (validate + dedup + reorder + insert).
- `_upsert_attempt_header()` ≈ 103 lines of first-write-wins `setattr`.

**Impact:** unreviewable diffs, merge conflicts, untestable units.

### 🔴 C2 — Duplicated colour science across modules ✅ *addressed in this PR*
`utils.py`, `spectral_km.py`, `calibration.py`, `routes.py`, and the orphan
top-level `spectral_mixer.py` each carried their own CIE colour-matching
functions, XYZ→sRGB matrix, Lab transforms, and ΔE. CIEDE2000 existed **twice**
(a colormath wrapper in `utils.py` and a hand-vectorised version in
`spectral_km.py`), and the **CIE 1931 data was copy-pasted three times** (utils,
routes, spectral_mixer). A correction to one would silently not reach the others.

**Resolution (see §6):** introduced the `app/color/` leaf package as the single
home for the CIE data, the sRGB conversion stack, and the vectorised CIEDE2000.
`utils`/`spectral_km`/`routes` now delegate to it. The two CIEDE2000
*representations* are kept on purpose — colormath-exact for authoritative RGB
scoring, fast vectorised for the solver — but a test now pins their agreement
(~1e-4) so they cannot drift apart. The 38-bin Kubelka–Munk engine grid and its
engine-white `xyz_to_lab` stay in `spectral_km` (tuned to the spectral pipeline;
merging would change output).

### 🔴 C3 — Synchronous email & web-push in the request path
`email_utils.send_email()` opens a blocking SMTP connection (15 s timeout) inside
`/register`, `/email/verification/request`, `/email/recover-id`. On `/register`,
an SMTP failure rolls back the whole transaction — *the user registers but loses
their verification email*. `push_send_daily()` sends to every subscriber in a
serial loop, rebuilding each user's personalised context (N+1 queries) inside it.

### 🔴 C4 — Duplicated paint-channel logic (✅ partially fixed in this PR)
The five drop columns were re-listed and re-summed inline in ≥6 places with two
inconsistent null conventions. The catalog recipe, session summary, and attempt
initial-state all denormalise the same five numbers across three tables.

### 🟠 C5 — Heavy synchronous compute in request threads
`/api/stat/summary` fits mixed-effects models (statsmodels, multi-start) and
`/api/stat/plot/<id>` renders matplotlib **in the gunicorn worker thread**, which
can take 5–30 s. With 1 worker × 4 threads on 512 MB, two concurrent `/stat`
requests can starve the pool or OOM. `get_dataframes()` loads whole tables
(capped at 50k events) into pandas on a 120 s TTL with no query-level filtering.

### 🟠 C6 — Repeated full-catalog scans + N+1 per game save
`compute_quota_progress()`, `_eligible_target_colors()`,
`recompute_max_sum_drop_unlocked()` and `next_action` each independently run
`TargetColor.query.order_by(catalog_order).all()` + a full `UserTargetColorStats`
scan. A single `save_session` triggers `compute_quota_progress()` several times
plus a per-level award loop. `TargetColor.catalog_order` is `unique` but has no
explicit sort index.

### 🟠 C7 — Schema evolution cruft & denormalisation
Three overlapping attempt tables: `Session` (legacy, `user_id String(64)`, no
FKs, appears unused), `MixingSession` (summary), `MixingAttempt` (telemetry).
`MixingSession` ↔ `MixingAttempt` are joined only by `attempt_uuid` with no FK or
cascade. JSON columns (`client_env_json`, `state_*_json`, `metadata_json`) have no
schema. `datetime.utcnow` (naive) is used everywhere while `next_action` emits
tz-aware UTC — mixed tz handling.

### 🟠 C8 — In-process global state defeats horizontal scaling
Rate-limit buckets (`_RATE_LIMIT_BUCKETS`), the stat-summary cache, spectral
palette/plot caches, and `gamut_lab` module globals all live in worker memory.
They are per-worker (incorrect once `WEB_CONCURRENCY > 1`), non-atomic, and lost
on the frequent worker recycle (`max_requests=200`). The rate limiter is thus
trivially bypassable and the cache hit-rate collapses under recycling.

### 🟠 C9 — No real migration framework
Schema changes are ad-hoc scripts: `init_db.py` does a destructive
`drop_all()/create_all()`; `migrate_add_*.py` run idempotent raw DDL;
`scripts/migrate_to_shadematch_v2.py` is a 500-line bespoke copier. No Alembic,
no version table, no rollback, no enforced ordering, and no auto-migrate on
deploy → high risk of model/DB drift.

### 🟠 C9b — Name shadowing of the colour helpers in `routes.py` ✅ *resolved*
`routes.py` imported `spectrum_to_xyz`/`xyz_to_rgb` from `utils` and then
**redefined functions of the same name** at the bottom of the file — so the
imports were dead and several routes (`build_spectrum_plots`, which feeds the
base swatches on `/spectral` + `/lab` + the game, plus the `/color_inspector`
and `/mix_colors` demos) silently used a *different* legacy pipeline
(chromaticity-normalised XYZ, 0–255 int RGB) than the rest of the app — on top of
a third byte-identical copy of the CIE data.

**Resolution:** the legacy pipeline moved to `app/color/legacy.py` under honest
names (`spectrum_to_chromaticity_xyz`, `xyz_to_srgb8`); the duplicate
`load_cie_data` and the dead shadowing import are gone, and `routes.py` now
defines no colour maths inline. Pinned by goldens in `tests/test_color_legacy.py`
and verified end-to-end: the five base-swatch RGBs are byte-identical to the old
inline math over the real pigment files (0 mismatches).

### 🟡 C10 — Security & correctness papercuts
- `SECRET_KEY` silently defaults to `'dev'` (`config.py:33`) — insecure sessions
  if the env var is ever unset in prod.
- No CSRF protection on browser-form POSTs (mitigated: most endpoints are JSON +
  id-gated; unsubscribe is token-gated).
- `print()` used for error logging instead of `current_app.logger`.
- Missing input validation: `save_session` reads `data['target_r']` etc. without
  key/range checks (KeyError → 500).
- Random 6-char user ids: collision probability grows with user count (birthday
  problem); registration must retry on `IntegrityError` (verify it does).

### 🟡 C11 — Frontend has no module system
`main.js` (1,729 lines) and `spectral_mixer.js` (1,358) use `window.*` globals,
500-line `DOMContentLoaded` closures, and ~15 ad-hoc `fetch()` sites with
inconsistent error handling (`alert` vs toast vs silent `.catch(()=>{})`). The
service worker precaches only ~8 of the many JS files (missing
`spectral_mixer.js`, `lab.js`, `calibration.js`, …) and has no telemetry
offline-queue, so offline events are lost.

### 🟡 C12 — Dependency & ops hygiene
Old pins (Flask 2.1.2 / Werkzeug 2.1.2 / SQLAlchemy 1.4); **deprecated
`colormath` (2015, unmaintained)** on the ΔE critical path; both
`psycopg2-binary` *and* `mysql-connector-python` shipped though only Postgres is
used; the full scientific stack imported by the web process (mitigated by lazy
imports for `/stat`). `pytest` is a dependency but there is **no test suite**.

---

## 4. Issue inventory by requested category

### Bad architecture decisions
- One 4k-line route module, zero service layer (C1).
- Business logic (gamification, telemetry reconciliation, email) inside handlers.
- Four parallel colour-science implementations (C2).
- Heavy analytics compute and matplotlib rendering inside request threads (C5).
- In-process mutable caches as the scaling boundary (C8).
- Destructive `init_db.py` + ad-hoc migrations instead of Alembic (C9).

### Duplicate logic
- Paint-channel summation in ≥6 sites, two null conventions (C4 — **fixed**).
- CIEDE2000 implemented twice; CMF/Lab/sRGB-matrix duplicated 4× (C2).
- `save_session` and `save_skip` are ~80-line near-clones (only end-reason /
  skip-perception differ).
- `stat_plot` and `stat_attempt_timeline_data` repeat the same param extraction.
- `_resolve_authenticated_user` pattern reinlined across ~10 routes.
- Email issue+send sequence repeated in 3 endpoints.

### Performance bottlenecks
- N+1 in `push_send_daily` (context rebuilt + catalog re-queried per subscriber).
- Multiple full-catalog scans + repeated `compute_quota_progress` per save (C6).
- Whole-table pandas loads on every cold `/stat` (C5).
- `copy.deepcopy()` on every stat-cache hit.
- Leaderboard fetches all users then sorts/ranks in Python.
- `iterrows()` in `build_attempt_recipe_similarity`.
- Per-level award-grant queries in a loop.

### Scalability risks
- Synchronous SMTP / push in request path, no retry/queue (C3).
- Per-worker caches & rate limiter — wrong under multi-worker (C8).
- Single worker on 512 MB with heavy compute; thread-pool starvation (C5).
- Unbounded in-memory dataframes as the events table grows (C5).
- No pagination on several admin queries.

### Maintainability issues
- God-functions (>100–400 lines) throughout `routes.py` and `stat_eda.py`.
- Magic numbers scattered (XP table, level thresholds, ΔE bands, layout coeffs).
- Inconsistent error schema (`status` vs `error` vs `message`; mixed codes).
- `print()` logging; sparse type hints/docstrings on data-heavy functions.
- No tests; no migration history; mixed tz handling.

---

## 5. Refactoring strategy (phased, behaviour-preserving)

The codebase is in production with **no test net**, so the strategy is
*characterize-then-extract* in small, verifiable steps — never a big-bang rewrite.

### Phase 0 — Safety net (do first)
1. Add a **pytest harness** with characterization tests: golden ΔE values, a
   `process_progression` scenario, a `solve_recipe` snapshot, and round-trips for
   the `/save_session` payload. These lock current behaviour before any change.
2. Add CI (GitHub Actions) running `pytest` + `ruff`/`flake8` + `pip-audit`.

### Phase 1 — De-duplicate (low risk, high clarity)
3. **`color_drops.py`** — single source of truth for the five channels.
   ✅ *Done in this PR* (see §6); `gamification.py` and `routes.py` now use it.
4. **`app/color/`** package — move CMFs, sRGB matrix, illuminants, Lab/XYZ/RGB
   transforms, and **one** `ciede2000` into a DB/Flask-free library; have
   `utils.py`, `spectral_km.py`, `calibration.py` import it. Pin numeric outputs
   with the Phase-0 golden tests, then delete the orphan `spectral_mixer.py`.
5. Extract `finalize_attempt(...)` shared by `save_session`/`save_skip`, and an
   `@require_user` helper replacing the reinlined auth block.

### Phase 2 — Carve out services (medium risk)
6. Move gamification orchestration into `services/progression.py`,
   `services/awards.py` (batch the award-existence check; compute
   `compute_quota_progress` once per save and thread it through). Routes become
   thin.
7. Split `stat_eda` into `data_access` / `metrics` / `rendering` so aggregation is
   testable without matplotlib and reusable for CSV export.

### Phase 3 — Infrastructure (de-risks scale)
8. **Async email/push**: push sends onto a queue (RQ/Celery, or a DB-backed
   outbox + cron). Registration no longer blocks on SMTP. Batch
   `push_send_daily` context before the loop.
9. **Shared cache + rate limit**: move the stat cache and rate-limit buckets to
   Redis (atomic, cross-worker, survives recycling). Then `WEB_CONCURRENCY > 1`
   becomes safe.
10. **Offload heavy `/stat`** to a worker/endpoint that computes async and caches
    results, returning cached JSON/PNG to the request thread.

### Phase 4 — Data & schema (highest coordination)
11. Adopt **Alembic**; baseline the current schema; replace `init_db.py`’s
    destructive path with `upgrade head`; run migrations on deploy.
12. Add indexes (`TargetColor.catalog_order`, `MixingSession(user_id,timestamp)`,
    `UserTargetColorStats(user_id,last_attempt_at)`); add the missing FK/cascade
    from `MixingSession` → `MixingAttempt`; deprecate the unused `Session` table.
13. Standardise timestamps on tz-aware UTC (`default=lambda: datetime.now(tz=utc)`);
    validate JSON columns with Pydantic at the edge.

### Phase 5 — Frontend & dependencies
14. Introduce a bundler (or ES modules) + a single `apiClient()` with uniform
    error/retry; widen the SW precache; add a localStorage telemetry queue with
    background sync.
15. Upgrade Flask/Werkzeug/SQLAlchemy; replace `colormath` with the in-repo
    `color/` library; drop `mysql-connector-python`.

---

## 6. Improved production-grade code (delivered in this PR)

Scope was deliberately limited to a **provably behaviour-preserving** slice so it
can land safely without a test net — it demonstrates the Phase-1 de-duplication
pattern end-to-end.

**New `app/color_drops.py`** — canonical paint-channel module:
- `PAINT_CHANNELS` — the authoritative `('white','black','red','yellow','blue')`.
- `sum_drops(obj, prefix='drop_')` — lenient sum (`None`→0); replaces the inline
  `(x.drop_white or 0) + …` expressions and works for `initial_drop_*` too.
- `sum_drops_strict(obj, prefix='drop_')` — returns `None` if any channel is
  unset; replaces the `target_color_sum_drop` body.

**Refactored call sites (identical output):**
- `gamification.py::_effective_steps_for_session` → `sum_drops(session)`.
- `gamification.py::target_color_sum_drop` → `sum_drops_strict(tc)`.
- `routes.py::PALETTE_COLORS` → aliased to `PAINT_CHANNELS`.

Equivalence was verified directly: lenient sum with a `None` channel = 12, strict
sum with a `None` channel = `None`, strict full recipe = 15, `initial_drop_`
prefix works, and all touched files byte-compile.

### 6.1 Phase-0 safety net (delivered)
A pytest characterization suite (`tests/`) pins current behaviour before deeper
change: colour-science goldens (`utils`, `spectral_km`), the drops module, and a
gamification scenario (`process_progression`/`compute_quota_progress`). Runs in
~1s against throwaway SQLite via a minimal ORM-only app (no pandas/Postgres/SMTP)
plus a light-deps CI workflow. **30 tests passing.**

### 6.2 Phase-1 colour-science consolidation (delivered)
New leaf package **`app/color/`** — Flask/DB-free, the single source of truth:
- `constants.py` — the CIE 1931 2° data (one copy; was three, verified
  byte-equal) + sRGB matrix/gamma constants.
- `convert.py` — `load_cie_data`, `spectrum_to_xyz`, `xyz_to_rgb`, `srgb_compand`
  (moved verbatim from `utils`).
- `distance.py` — the single vectorised `ciede2000` (moved from `spectral_km`,
  with optional kL/kC/kH).

Wiring (all behaviour-preserving, verified by goldens + identity tests):
- `utils.py` re-exports the conversion stack from `app/color`; its authoritative
  `delta_e_cie2000` stays on colormath so stored ΔE values are byte-for-byte
  unchanged.
- `spectral_km.ciede2000` is now the shared function (same object) — so
  `calibration` and `gamut_lab`, which call `spectral_km.ciede2000`, are
  unaffected.
- `routes.py` drops its 3rd CIE-data copy and the dead shadowed import; its
  divergent legacy `spectrum_to_xyz`/`xyz_to_rgb` are extracted to
  `app/color/legacy.py` (honest names), so the module defines no colour maths
  inline (see C9b).

A randomized test now asserts the two CIEDE2000 representations agree to <2e-4,
so the duplication that C2 warned about can no longer silently reappear.

> Everything in §5 beyond Phase 1 is intentionally **not** applied here — those
> changes alter structure broadly and must ride further on the test net. This
> document is the roadmap for that work.

---

## 7. Quick-win checklist (safe, isolated)

- [x] Centralise paint-channel logic (`color_drops.py`).
- [x] Add `pytest` characterization tests + CI before deeper refactors.
- [x] Consolidate colour science into the `app/color/` library (C2).
- [x] Extract the legacy `routes.py` colour pipeline to `app/color/legacy.py` (C9b).
- [ ] Add indexes: `TargetColor.catalog_order`, `MixingSession(user_id,timestamp)`.
- [ ] Fail fast if `SECRET_KEY` is unset in production.
- [ ] Replace `print()` with `current_app.logger`.
- [ ] Extract `finalize_attempt()` shared by save_session/save_skip.
- [ ] Batch the per-level award-existence check into one query.
- [ ] Delete the orphan top-level `spectral_mixer.py` (4th colour-science copy).
- [ ] Drop the unused `Session` model and `mysql-connector-python` dep.
```
