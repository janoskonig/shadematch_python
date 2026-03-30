#!/usr/bin/env node
/**
 * Step-level telemetry extension migration.
 *
 * Extends:
 *   - mixing_attempts
 *   - mixing_attempt_events
 *
 * Creates:
 *   - mixing_steps view
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
    console.log('Connected. Running mixing step telemetry migration...\n');

    await run(client, 'mixing_attempts summary columns', `
      ALTER TABLE mixing_attempts
        ADD COLUMN IF NOT EXISTS final_delta_e DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS duration_sec DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS num_steps INTEGER
    `);

    await run(client, 'mixing_attempt_events step analytics columns', `
      ALTER TABLE mixing_attempt_events
        ADD COLUMN IF NOT EXISTS step_index INTEGER,
        ADD COLUMN IF NOT EXISTS time_since_prev_step_ms BIGINT,
        ADD COLUMN IF NOT EXISTS action_type VARCHAR(16),
        ADD COLUMN IF NOT EXISTS amount INTEGER,
        ADD COLUMN IF NOT EXISTS delta_e_before DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS delta_e_after DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS mix_before_r SMALLINT,
        ADD COLUMN IF NOT EXISTS mix_before_g SMALLINT,
        ADD COLUMN IF NOT EXISTS mix_before_b SMALLINT,
        ADD COLUMN IF NOT EXISTS mix_after_r SMALLINT,
        ADD COLUMN IF NOT EXISTS mix_after_g SMALLINT,
        ADD COLUMN IF NOT EXISTS mix_after_b SMALLINT
    `);

    await run(client, 'mixing_steps analytics view', `
      CREATE OR REPLACE VIEW mixing_steps AS
      SELECT
        e.id AS step_id,
        e.attempt_uuid AS attempt_id,
        e.attempt_uuid,
        e.seq,
        e.step_index,
        e.client_ts_ms,
        e.server_ts,
        e.event_type,
        e.action_type,
        e.action_color,
        e.amount,
        e.time_since_prev_step_ms,
        e.delta_e_before,
        e.delta_e_after,
        (e.delta_e_after - e.delta_e_before) AS delta_e_change,
        (
          e.delta_e_before IS NOT NULL
          AND e.delta_e_after IS NOT NULL
          AND e.delta_e_after < e.delta_e_before
        ) AS is_improving,
        e.mix_before_r,
        e.mix_before_g,
        e.mix_before_b,
        e.mix_after_r,
        e.mix_after_g,
        e.mix_after_b,
        e.metadata_json
      FROM mixing_attempt_events e
      WHERE e.step_index IS NOT NULL
    `);

    console.log('\n✅ Mixing step telemetry migration complete.');
  } catch (err) {
    console.error('\n❌ Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
