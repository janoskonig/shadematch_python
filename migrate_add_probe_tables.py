#!/usr/bin/env python3
"""
Migration: create probe_slots and probe_schedule tables (learning-effect study).
Safe to run multiple times (create_all is a no-op for existing tables).
"""
from app import create_app, db
from app.models import ProbeSlot, ProbeSchedule  # noqa: F401 — registers tables

app = create_app()

with app.app_context():
    db.create_all()
    print("✅ probe_slots and probe_schedule tables ensured.")
