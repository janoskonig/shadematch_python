#!/usr/bin/env python3
"""
Migration: add calibration_trials.center_group — the colour family of the trial's
centre, snapshotted at generation time ('c0'..'c9' frozen match clusters, or 'skin').
Null on rows from the skin-only pilot protocol. Safe to run multiple times.
"""
from app import create_app, db

app = create_app()

with app.app_context():
    db.session.execute(db.text(
        "ALTER TABLE calibration_trials ADD COLUMN IF NOT EXISTS center_group VARCHAR(16)"
    ))
    db.session.commit()
    print("✅ calibration_trials.center_group column ensured.")
