#!/usr/bin/env python3
"""Migration: add mixing_model/input_mode to mixing_attempt_events, then backfill
all pre-existing rows across every table to the historical condition (mixbox + integer).

Idempotent: only adds missing columns and only fills rows that are still NULL, so it
is safe to re-run. Dialect-agnostic (PostgreSQL or SQLite).
"""
from sqlalchemy import inspect

from app import create_app, db

app = create_app()

with app.app_context():
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())

    # 1) Ensure mixing_attempt_events has the two tag columns.
    if 'mixing_attempt_events' in tables:
        cols = {c['name'] for c in inspector.get_columns('mixing_attempt_events')}
        for col in ('mixing_model', 'input_mode'):
            if col not in cols:
                db.session.execute(db.text(
                    f"ALTER TABLE mixing_attempt_events ADD COLUMN {col} VARCHAR(16)"
                ))
                print(f"✅ added mixing_attempt_events.{col}")
        db.session.commit()

    # 2) Backfill gameplay/telemetry rows: NULL → mixbox / integer.
    for table in ('mixing_sessions', 'mixing_attempts', 'mixing_attempt_events'):
        if table not in tables:
            continue
        res = db.session.execute(db.text(
            f"UPDATE {table} SET mixing_model = 'mixbox' WHERE mixing_model IS NULL"
        ))
        res2 = db.session.execute(db.text(
            f"UPDATE {table} SET input_mode = 'integer' WHERE input_mode IS NULL"
        ))
        print(f"• {table}: model+{res.rowcount}, input+{res2.rowcount}")

    # 3) Catalog colours that carry a drop recipe were authored in mixbox/integer space.
    if 'target_colors' in tables:
        r = db.session.execute(db.text(
            "UPDATE target_colors SET mixing_model = 'mixbox' "
            "WHERE mixing_model IS NULL AND drop_white IS NOT NULL"
        ))
        r2 = db.session.execute(db.text(
            "UPDATE target_colors SET input_mode = 'integer' "
            "WHERE input_mode IS NULL AND drop_white IS NOT NULL"
        ))
        print(f"• target_colors (with recipe): model+{r.rowcount}, input+{r2.rowcount}")

    db.session.commit()
    print("Backfill complete.")
