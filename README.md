# ShadeMatch Python

A Flask-based color matching and spectral analysis application for pigment mixing and color science.

## Features

- Color spectrum analysis and visualization
- Pigment mixing calculations
- Database-driven color management
- Web-based interface for color operations

## Local Development

1. Clone the repository
2. Create a virtual environment: `python -m venv .venv`
3. Activate the virtual environment: `source .venv/bin/activate` (Linux/Mac) or `.venv\Scripts\activate` (Windows)
4. Install dependencies: `pip install -r requirements.txt`
5. Set up environment variables (see Environment Variables section)
6. Run the application: `python run.py`

## Environment Variables

Create a `.env` file in the root directory with:

```env
DATABASE_URL=your_database_connection_string
SECRET_KEY=your_secret_key
FLASK_ENV=development
```

## Database Setup

1. Ensure your database is running and accessible
2. **New environments:** Run the database initialization script: `python init_db.py` (drops and recreates all tables — use only on empty/dev DBs).
3. **Existing PostgreSQL (e.g. production):** After pulling model changes, run `npm install` then `npm run db:migrate`. The script loads **`DATABASE_URL` from the repo-root `.env`** (same as Flask’s `load_dotenv`) or from your shell if already exported. It adds `skip_perception` and **`match_category`** when missing.
4. **Gamification level recompute (Option C, 30-level mapping):** After deploying the tier-driven leveling rewrite, run `python scripts/recompute_levels.py` once to recompute every user’s `level` and `max_sum_drop_unlocked` under the new mapping. Use `--dry-run` first to inspect the planned changes. Idempotent and safe to re-run.

## Live dashboards (`/live`, `/hetfo`, `/szerda`)

`/live` is the general live dashboard: everyone the server heard from in the last `?hours=` (default 24, 1–168), whatever day they registered, with a rounds-per-hour history chart. `/hetfo` ("hétfő" = Monday) and `/szerda` ("szerda" = Wednesday) are the same dashboard for the players who registered on one calendar day (Europe/Budapest). Every page leads with the live play: who is online, a card per active player (the colour being mixed, its current ΔE and step-by-step ΔE path, calibration progress, last round), a newest-first event feed (rounds finished with their result, app opens, calibration blocks, matches, registrations), rolling "rounds in the last 5 / 15 / 60 min" tiles and a rounds-per-minute chart. Per-player totals sit in the table below; cohort totals, profile and history charts are collapsed at the bottom. The pages poll `/api/live` (general) or `/api/hetfo/live` (cohort) every 5 s.

- The game client flushes mixing steps every ~8 s while a round is open (`flushTelemetryLive` in `static/main.js`), which is what makes the in-round ΔE visible; before, steps only left the device when the round ended.
- Default cohort days: the two recruitment sessions, **2026-08-31** (`/hetfo`) and **2026-09-02** (`/szerda`). Override per deployment with `HETFO_COHORT_DATE` / `SZERDA_COHORT_DATE` (`YYYY-MM-DD`), or per visit with `?date=YYYY-MM-DD` (each page also steps a week at a time; the heading follows the weekday shown).
- `?refresh=<seconds>` (3–120) changes the polling interval; `HETFO_CACHE_SECONDS` (default 3) is the server-side cache shared by everyone watching.
- Like `/stat`, the pages have no login: they show player IDs, nicknames and gameplay figures, never emails or birthdates.

## Deployment on Render

### Prerequisites
- A Render account
- A PostgreSQL database (Render provides this)

### Deployment Steps

1. **Connect your GitHub repository to Render**
   - Go to Render Dashboard
   - Click "New +" and select "Web Service"
   - Connect your GitHub repository

2. **Configure the Web Service**
   - **Name**: `shadematch-python` (or your preferred name)
   - **Environment**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn run:app`
   - **Plan**: Choose your preferred plan (Free tier available)

3. **Environment Variables**
   - `DATABASE_URL`: Your PostgreSQL connection string from Render
   - `SECRET_KEY`: A secure random string for Flask sessions
   - `FLASK_ENV`: Set to `production`

4. **Database Setup**
   - Create a PostgreSQL database in Render
   - Copy the connection string to your `DATABASE_URL` environment variable
   - The app will automatically create tables on first run

### Troubleshooting Render Deployment

If you encounter the "Cannot import 'setuptools.build_meta'" error:

1. **Check requirements.txt**: Ensure all packages have specific versions
2. **Verify Python version**: The app is configured for Python 3.11.7
3. **Clear build cache**: Sometimes Render needs a fresh build
4. **Check logs**: Review the build logs for specific dependency conflicts

### Alternative Deployment Files

The repository includes:
- `render.yaml`: Render-specific configuration
- `Procfile`: Alternative deployment configuration
- `runtime.txt`: Python version specification

## Project Structure

```
shadematch_python/
├── app/                    # Flask application package
│   ├── __init__.py        # App factory and configuration
│   ├── models.py          # Database models
│   ├── routes.py          # Route definitions
│   └── utils.py           # Utility functions
├── static/                 # Static assets (CSS, JS, images)
├── templates/              # HTML templates
├── pigments/               # Pigment data files
├── requirements.txt        # Python dependencies
├── config.py              # Configuration settings
├── run.py                 # Application entry point
└── init_db.py             # Database initialization script
```

## Dependencies

- **Web Framework**: Flask 2.1.2
- **Database**: SQLAlchemy, PostgreSQL support
- **Color Science**: colormath, numpy
- **Data Processing**: pandas, matplotlib, plotly
- **Production Server**: gunicorn

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

[Add your license information here] 