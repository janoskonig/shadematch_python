#!/usr/bin/env node
/**
 * Mixing-attempt telemetry schema migration.
 *
 * Creates:
 *   - mixing_attempts
 *   - mixing_attempt_events
 *
 * Safe to re-run.
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
    console.log('Connected. Running mixing telemetry migration...\n');

    await run(client, 'mixing_attempts table', `
      CREATE TABLE IF NOT EXISTS mixing_attempts (
        attempt_uuid VARCHAR(36) PRIMARY KEY,
        user_id VARCHAR(6) REFERENCES users(id),
        target_color_id INTEGER REFERENCES target_colors(id),
        target_r SMALLINT,
        target_g SMALLINT,
        target_b SMALLINT,
        initial_drop_white INTEGER NOT NULL DEFAULT 0,
        initial_drop_black INTEGER NOT NULL DEFAULT 0,
        initial_drop_red INTEGER NOT NULL DEFAULT 0,
        initial_drop_yellow INTEGER NOT NULL DEFAULT 0,
        initial_drop_blue INTEGER NOT NULL DEFAULT 0,
        initial_mixed_r SMALLINT NOT NULL DEFAULT 255,
        initial_mixed_g SMALLINT NOT NULL DEFAULT 255,
        initial_mixed_b SMALLINT NOT NULL DEFAULT 255,
        initial_delta_e DOUBLE PRECISION,
        attempt_started_client_ts_ms BIGINT,
        attempt_started_server_ts TIMESTAMP NOT NULL DEFAULT NOW(),
        first_action_client_ts_ms BIGINT,
        first_action_server_ts TIMESTAMP,
        attempt_ended_client_ts_ms BIGINT,
        attempt_ended_server_ts TIMESTAMP,
        end_reason VARCHAR(32),
        app_version VARCHAR(64)
      )
    `);

    await run(client, 'mixing_attempts.user_id index', `
      CREATE INDEX IF NOT EXISTS idx_mixing_attempts_user_id
      ON mixing_attempts (user_id)
    `);

    await run(client, 'mixing_attempts.started_server_ts index', `
      CREATE INDEX IF NOT EXISTS idx_mixing_attempts_started_server_ts
      ON mixing_attempts (attempt_started_server_ts)
    `);

    await run(client, 'mixing_attempt_events table', `
      CREATE TABLE IF NOT EXISTS mixing_attempt_events (
        id SERIAL PRIMARY KEY,
        attempt_uuid VARCHAR(36) NOT NULL REFERENCES mixing_attempts(attempt_uuid) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        action_color VARCHAR(32),
        client_ts_ms BIGINT NOT NULL,
        server_ts TIMESTAMP NOT NULL DEFAULT NOW(),
        state_before_json JSONB NOT NULL,
        state_after_json JSONB NOT NULL,
        metadata_json JSONB
      )
    `);

    await run(client, 'mixing_attempt_events attempt_uuid+seq unique', `
      CREATE UNIQUE INDEX IF NOT EXISTS uq_mixing_attempt_events_attempt_seq
      ON mixing_attempt_events (attempt_uuid, seq)
    `);

    await run(client, 'mixing_attempt_events attempt_uuid+client_ts index', `
      CREATE INDEX IF NOT EXISTS idx_mixing_attempt_events_attempt_client_ts
      ON mixing_attempt_events (attempt_uuid, client_ts_ms)
    `);

    await run(client, 'mixing_attempt_events event_type+server_ts index', `
      CREATE INDEX IF NOT EXISTS idx_mixing_attempt_events_type_server_ts
      ON mixing_attempt_events (event_type, server_ts)
    `);

    console.log('\n✅ Mixing telemetry migration complete.');
  } catch (err) {
    console.error('\n❌ Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
