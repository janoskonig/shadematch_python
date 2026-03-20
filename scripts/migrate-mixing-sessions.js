#!/usr/bin/env node
/**
 * mixing_sessions: skip_perception, match_category, align with app taxonomy.
 * Loads DATABASE_URL from repo-root `.env` or env.
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

async function main() {
  const rawUrl = process.env.DATABASE_URL;
  if (!rawUrl) {
    console.error('DATABASE_URL is not set.');
    process.exit(1);
  }
  const connectionString = normalizeDatabaseUrl(rawUrl);
  const client = new Client({ connectionString });
  try {
    await client.connect();
    await client.query(
      'ALTER TABLE mixing_sessions ADD COLUMN IF NOT EXISTS skip_perception VARCHAR(32);'
    );
    await client.query(
      'ALTER TABLE mixing_sessions ADD COLUMN IF NOT EXISTS match_category VARCHAR(40);'
    );
    await client.query(
      `ALTER TABLE mixing_sessions ALTER COLUMN match_category TYPE VARCHAR(40)`
    ).catch(() => {});

    await client.query(`
      UPDATE mixing_sessions SET match_category = CASE match_category
        WHEN 'perfect_match' THEN 'perfect'
        WHEN 'no_perceptible_diff' THEN 'no_perceivable_difference'
        WHEN 'acceptable_diff' THEN 'acceptable_difference'
        WHEN 'big_diff' THEN 'big_difference'
        WHEN 'non_perfect' THEN 'stopped'
        WHEN 'skipped_no_rating' THEN 'stopped'
        ELSE match_category
      END
      WHERE match_category IS NOT NULL
    `);

    await client.query(`
      UPDATE mixing_sessions SET match_category = CASE
        WHEN delta_e IS NOT NULL AND delta_e <= 0.01 THEN 'perfect'
        WHEN skipped IS TRUE AND skip_perception = 'identical' THEN 'no_perceivable_difference'
        WHEN skipped IS TRUE AND skip_perception = 'acceptable' THEN 'acceptable_difference'
        WHEN skipped IS TRUE AND skip_perception = 'unacceptable' THEN 'big_difference'
        WHEN skipped IS TRUE THEN 'stopped'
        ELSE 'stopped'
      END
      WHERE match_category IS NULL
    `);

    await client.query(
      `UPDATE mixing_sessions SET match_category = 'stopped' WHERE match_category IS NULL`
    );
    await client.query(
      `ALTER TABLE mixing_sessions ALTER COLUMN match_category SET DEFAULT 'stopped'`
    );
    await client.query(
      `ALTER TABLE mixing_sessions ALTER COLUMN match_category SET NOT NULL`
    );
    console.log('OK: mixing_sessions taxonomy (perfect / perceivable / acceptable / big / stopped).');
  } catch (err) {
    console.error('Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
