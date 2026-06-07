#!/usr/bin/env node
/**
 * Adds optional drop-count recipe columns to target_colors (lab-saved mixes).
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
    console.log('Connected. Running target_colors drop-count columns migration...\n');

    await run(client, 'target_colors drop recipe columns', `
      ALTER TABLE target_colors
        ADD COLUMN IF NOT EXISTS drop_white INTEGER,
        ADD COLUMN IF NOT EXISTS drop_black INTEGER,
        ADD COLUMN IF NOT EXISTS drop_red INTEGER,
        ADD COLUMN IF NOT EXISTS drop_yellow INTEGER,
        ADD COLUMN IF NOT EXISTS drop_blue INTEGER
    `);

    console.log('\nOK: target_colors drop_* columns.');
  } finally {
    await client.end();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
