#!/usr/bin/env python3
"""Migration: tag mixes with the mixing model + input mode.

Adds two nullable columns to three tables:
  - mixing_model : 'rgb' | 'spectral'
  - input_mode   : 'integer' | 'dialer'

Tables: mixing_sessions (legacy game saves), mixing_attempts (modern telemetry
header), target_colors (lab saves). Idempotent and dialect-agnostic: it inspects
existing columns first, so re-running is a no-op and it works on PostgreSQL or SQLite.
"""
from sqlalchemy import inspect

from app import create_app, db

TABLES = ('mixing_sessions', 'mixing_attempts', 'target_colors')
NEW_COLUMNS = ('mixing_model', 'input_mode')

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    added = 0

    for table in TABLES:
        if table not in existing_tables:
            print(f"⚠️  table {table!r} does not exist yet — skipping")
            continue
        existing_cols = {c['name'] for c in inspector.get_columns(table)}
        for col in NEW_COLUMNS:
            if col in existing_cols:
                print(f"• {table}.{col} already present")
                continue
            db.session.execute(
                db.text(f"ALTER TABLE {table} ADD COLUMN {col} VARCHAR(16)")
            )
            added += 1
            print(f"✅ added {table}.{col}")

    db.session.commit()
    print(f"Done. {added} column(s) added.")
