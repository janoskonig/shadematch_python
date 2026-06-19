#!/usr/bin/env python3
"""Migration: add user email identity fields + verification tokens table."""
from app import create_app, db

app = create_app()

with app.app_context():
    db.session.execute(db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email VARCHAR(255)"))
    db.session.execute(
        db.text("ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP")
    )
    db.session.execute(
        db.text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
            "email_opt_in_reminders BOOLEAN NOT NULL DEFAULT FALSE"
        )
    )
    db.session.execute(
        db.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_unique "
            "ON users (email) WHERE email IS NOT NULL"
        )
    )
    db.session.execute(
        db.text(
            """
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
              id SERIAL PRIMARY KEY,
              user_id VARCHAR(6) NOT NULL REFERENCES users(id),
              purpose VARCHAR(32) NOT NULL DEFAULT 'verify_email',
              token_hash VARCHAR(64) NOT NULL UNIQUE,
              expires_at TIMESTAMP NOT NULL,
              used_at TIMESTAMP NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    db.session.execute(
        db.text(
            "CREATE INDEX IF NOT EXISTS idx_email_tokens_user_id "
            "ON email_verification_tokens(user_id)"
        )
    )
    db.session.commit()
    print("✅ Email identity migration completed.")
