#!/usr/bin/env python3
"""Migration: add research informed-consent audit table."""
from app import create_app, db

app = create_app()

with app.app_context():
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS consent_records (
              id SERIAL PRIMARY KEY,
              user_id VARCHAR(6) NOT NULL REFERENCES users(id),
              purpose VARCHAR(64) NOT NULL DEFAULT 'research_informed_consent',
              consent_version VARCHAR(32) NOT NULL,
              consent_text_hash VARCHAR(64) NULL,
              locale VARCHAR(8) NULL,
              user_agent VARCHAR(255) NULL,
              consented_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            "CREATE INDEX IF NOT EXISTS idx_consent_records_user_id "
            "ON consent_records(user_id)"
        )
    )
    db.session.execute(
        db.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_consent_user_version "
            "ON consent_records(user_id, consent_version)"
        )
    )
    db.session.commit()
    print("✅ Consent records migration completed.")
