#!/usr/bin/env node
/**
 * Sum-drop progression schema:
 *   - user_progress.max_sum_drop_unlocked (default 4, first band cap)
 *   - Drops target_colors.frequency, target_colors.level_required
 *   - Drops idx_target_colors_level_required if present
 *
 * Run after migrate-gamification.js. Safe to re-run.
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
    console.log('Connected. Running sum-drop progression migration…\n');

    await run(
      client,
      'user_progress.max_sum_drop_unlocked',
      `ALTER TABLE user_progress
       ADD COLUMN IF NOT EXISTS max_sum_drop_unlocked INTEGER NOT NULL DEFAULT 4`
    );

    await run(
      client,
      'drop index idx_target_colors_level_required',
      'DROP INDEX IF EXISTS idx_target_colors_level_required'
    );

    await run(
      client,
      'drop target_colors.level_required',
      'ALTER TABLE target_colors DROP COLUMN IF EXISTS level_required'
    );

    await run(
      client,
      'drop target_colors.frequency',
      'ALTER TABLE target_colors DROP COLUMN IF EXISTS frequency'
    );

    console.log('\n✅ sum-drop progression migration complete.');
  } finally {
    await client.end();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
