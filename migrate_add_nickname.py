#!/usr/bin/env python3
"""
Migration: add users.nickname (optional public display name) with a
case-insensitive partial unique index. Safe to run multiple times.
"""
from app import create_app, db

app = create_app()

with app.app_context():
    db.session.execute(db.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname VARCHAR(20)"
    ))
    db.session.execute(db.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_nickname_lower_unique "
        "ON users (LOWER(nickname)) WHERE nickname IS NOT NULL"
    ))
    db.session.commit()
    print("✅ users.nickname column + case-insensitive unique index ensured.")
