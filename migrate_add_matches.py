#!/usr/bin/env python3
"""
Migration: create matches and match_rounds tables (match-based gameplay).
Also adds a partial unique index enforcing at most one active match per user.
Safe to run multiple times (create_all is a no-op for existing tables).
"""
from sqlalchemy import text

from app import create_app, db
from app.models import Match, MatchRound  # noqa: F401 — registers tables

app = create_app()

with app.app_context():
    db.create_all()
    if db.engine.dialect.name == 'postgresql':
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_matches_one_active "
            "ON matches (user_id) WHERE status = 'active'"
        ))
        db.session.commit()
    print("✅ matches and match_rounds tables ensured.")
