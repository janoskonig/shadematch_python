#!/usr/bin/env node
/**
 * Gamification schema migration.
 * Creates:
 *   - mixing_sessions.attempt_uuid (nullable, unique)
 *   - user_progress
 *   - user_target_color_stats
 *   - user_awards
 *   - daily_challenge_runs
 *   - daily_challenge_winners
 *   - push_subscriptions
 *
 * Loads DATABASE_URL from repo-root `.env` or environment.
 * Safe to re-run (all operations are IF NOT EXISTS / DO-NOTHING).
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const { Client } = require('pg');

function normalizeDatabaseUrl(url) {
  if (!url) return url;
  if (url.startsWith('postgres://')) {
    return url.replace('postgres://', 'postgresql://', 1);
  }
  return url;
}

async function run(client, label, sql) {
  try {
    await client.query(sql);
    console.log(`  ✓ ${label}`);
  } catch (err) {
    console.error(`  ✗ ${label}: ${err.message}`);
    throw err;
  }
}

async function main() {
  const rawUrl = process.env.DATABASE_URL;
  if (!rawUrl) {
    console.error('DATABASE_URL is not set.');
    process.exit(1);
  }
  const client = new Client({ connectionString: normalizeDatabaseUrl(rawUrl) });
  try {
    await client.connect();
    console.log('Connected. Running gamification migrations…\n');

    // ── mixing_sessions: attempt_uuid ──────────────────────────────────────
    await run(client, 'mixing_sessions.attempt_uuid column',
      `ALTER TABLE mixing_sessions ADD COLUMN IF NOT EXISTS attempt_uuid VARCHAR(36)`);

    await run(client, 'mixing_sessions.attempt_uuid unique index',
      `CREATE UNIQUE INDEX IF NOT EXISTS idx_mixing_sessions_attempt_uuid
       ON mixing_sessions (attempt_uuid)
       WHERE attempt_uuid IS NOT NULL`);

    // ── user_progress ──────────────────────────────────────────────────────
    await run(client, 'user_progress table', `
      CREATE TABLE IF NOT EXISTS user_progress (
        id                     SERIAL PRIMARY KEY,
        user_id                VARCHAR(6) NOT NULL UNIQUE REFERENCES users(id),
        xp                     INTEGER NOT NULL DEFAULT 0,
        level                  INTEGER NOT NULL DEFAULT 1,
        current_streak         INTEGER NOT NULL DEFAULT 0,
        longest_streak         INTEGER NOT NULL DEFAULT 0,
        last_activity_date     DATE,
        streak_freeze_available INTEGER NOT NULL DEFAULT 0,
        updated_at             TIMESTAMP DEFAULT NOW()
      )
    `);

    await run(client, 'user_progress.user_id index',
      `CREATE INDEX IF NOT EXISTS idx_user_progress_user_id ON user_progress (user_id)`);

    // ── user_target_color_stats ────────────────────────────────────────────
    await run(client, 'user_target_color_stats table', `
      CREATE TABLE IF NOT EXISTS user_target_color_stats (
        id                SERIAL PRIMARY KEY,
        user_id           VARCHAR(6) NOT NULL REFERENCES users(id),
        target_color_id   INTEGER NOT NULL REFERENCES target_colors(id),
        attempt_count     INTEGER NOT NULL DEFAULT 0,
        completed_count   INTEGER NOT NULL DEFAULT 0,
        best_delta_e      FLOAT,
        last_attempt_at   TIMESTAMP
      )
    `);

    await run(client, 'user_target_color_stats unique constraint', `
      DO $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_target_color_stats'
        ) THEN
          ALTER TABLE user_target_color_stats
            ADD CONSTRAINT uq_user_target_color_stats
            UNIQUE (user_id, target_color_id);
        END IF;
      END $$
    `);

    await run(client, 'user_target_color_stats.user_id index',
      `CREATE INDEX IF NOT EXISTS idx_utcs_user_id ON user_target_color_stats (user_id)`);

    await run(client, 'user_target_color_stats.target_color_id index',
      `CREATE INDEX IF NOT EXISTS idx_utcs_target_color_id
       ON user_target_color_stats (target_color_id)`);

    // ── user_awards ────────────────────────────────────────────────────────
    await run(client, 'user_awards table', `
      CREATE TABLE IF NOT EXISTS user_awards (
        id              SERIAL PRIMARY KEY,
        user_id         VARCHAR(6) NOT NULL REFERENCES users(id),
        award_key       VARCHAR(128) NOT NULL,
        award_scope     VARCHAR(16) NOT NULL DEFAULT 'lifetime',
        award_scope_key VARCHAR(32) NOT NULL DEFAULT 'lifetime',
        metadata_json   JSONB,
        unlocked_at     TIMESTAMP DEFAULT NOW()
      )
    `);

    await run(client, 'user_awards unique constraint', `
      DO $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname = 'uq_user_award'
        ) THEN
          ALTER TABLE user_awards
            ADD CONSTRAINT uq_user_award
            UNIQUE (user_id, award_key, award_scope, award_scope_key);
        END IF;
      END $$
    `);

    await run(client, 'user_awards.user_id index',
      `CREATE INDEX IF NOT EXISTS idx_user_awards_user_id ON user_awards (user_id)`);

    // ── daily_challenge_runs ───────────────────────────────────────────────
    await run(client, 'daily_challenge_runs table', `
      CREATE TABLE IF NOT EXISTS daily_challenge_runs (
        id              SERIAL PRIMARY KEY,
        user_id         VARCHAR(6) NOT NULL REFERENCES users(id),
        challenge_date  DATE NOT NULL,
        attempt_uuid    VARCHAR(36) NOT NULL UNIQUE,
        score_primary   FLOAT,
        score_secondary INTEGER,
        is_final        BOOLEAN NOT NULL DEFAULT FALSE,
        created_at      TIMESTAMP DEFAULT NOW()
      )
    `);

    await run(client, 'daily_challenge_runs composite unique', `
      DO $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname = 'uq_daily_run_uuid'
        ) THEN
          ALTER TABLE daily_challenge_runs
            ADD CONSTRAINT uq_daily_run_uuid
            UNIQUE (user_id, challenge_date, attempt_uuid);
        END IF;
      END $$
    `);

    await run(client, 'daily_challenge_runs.user_id+date index',
      `CREATE INDEX IF NOT EXISTS idx_dcr_user_date
       ON daily_challenge_runs (user_id, challenge_date)`);

    // ── daily_challenge_winners ────────────────────────────────────────────
    await run(client, 'daily_challenge_winners table', `
      CREATE TABLE IF NOT EXISTS daily_challenge_winners (
        id              SERIAL PRIMARY KEY,
        challenge_date  DATE NOT NULL UNIQUE,
        user_id         VARCHAR(6) NOT NULL REFERENCES users(id),
        score_primary   FLOAT,
        score_secondary INTEGER,
        resolved_at     TIMESTAMP DEFAULT NOW()
      )
    `);

    // ── push_subscriptions ─────────────────────────────────────────────────
    await run(client, 'push_subscriptions table', `
      CREATE TABLE IF NOT EXISTS push_subscriptions (
        id          SERIAL PRIMARY KEY,
        user_id     VARCHAR(6) NOT NULL REFERENCES users(id),
        endpoint    TEXT NOT NULL UNIQUE,
        p256dh      TEXT NOT NULL,
        auth        TEXT NOT NULL,
        created_at  TIMESTAMP DEFAULT NOW()
      )
    `);

    await run(client, 'push_subscriptions.user_id index',
      `CREATE INDEX IF NOT EXISTS idx_push_subs_user_id ON push_subscriptions (user_id)`);

    console.log('\n✅ All gamification migrations complete.');
  } catch (err) {
    console.error('\n❌ Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
