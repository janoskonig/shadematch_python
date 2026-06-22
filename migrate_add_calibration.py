#!/usr/bin/env python3
"""
Migration: create the calibration_sessions and calibration_trials tables if they do not
already exist (the /calibration perceptibility/acceptability threshold game).

Additive only — touches no existing tables. Safe to run multiple times: db.create_all()
creates just the missing tables (and their indexes) and is a no-op for everything else.

Run on deploy the same way as the other migrate_add_*.py scripts:
    python migrate_add_calibration.py
"""
from app import create_app, db
from app.models import CalibrationSession, CalibrationTrial  # noqa: F401 — register tables

app = create_app()

with app.app_context():
    # create_all is a no-op for tables that already exist.
    db.create_all()
    print("✅ calibration_sessions + calibration_trials tables ensured.")
