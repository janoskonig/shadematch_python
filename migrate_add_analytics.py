#!/usr/bin/env python3
"""
Migration: create analytics_events table if it does not already exist.
Safe to run multiple times (idempotent via CREATE TABLE IF NOT EXISTS).
"""
from app import create_app, db
from app.models import AnalyticsEvent  # noqa: F401 — ensures table is registered

app = create_app()

with app.app_context():
    # create_all is a no-op for tables that already exist
    db.create_all()
    print("✅ analytics_events table ensured.")
