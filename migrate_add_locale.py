#!/usr/bin/env python3
"""
Migration: add users.locale (UI/notification language, 'en'/'hu'; null =
follow browser/cookie) and target_colors.name_hu (Hungarian gloss shown
alongside the English colour name). Safe to run multiple times.
"""
from app import create_app, db

app = create_app()

with app.app_context():
    db.session.execute(db.text(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS locale VARCHAR(5)"
    ))
    db.session.execute(db.text(
        "ALTER TABLE target_colors ADD COLUMN IF NOT EXISTS name_hu VARCHAR(120)"
    ))
    db.session.commit()
    print("✅ users.locale + target_colors.name_hu columns ensured.")
