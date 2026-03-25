#!/usr/bin/env node
/**
 * Adds level_required to target_colors and normalizes frequency to 1.
 *
 * level_required controls which colors are unlocked at each player level:
 *   1 → Orange, Purple, Green        (catalog_order 0–2)
 *   2 → Pink, Olive, Custom          (catalog_order 3–5)
 *   3 → Peach, Coral, Turquoise      (catalog_order 6–8)
 *   4 → Chartreuse, Teal             (catalog_order 9–10)
 *   5 → all skin_light               (catalog_order 11–24)
 *   7 → all skin_dark                (catalog_order 25–39)
 *
 * frequency is reset to 1 for all rows — historical play counts
 * are no longer used as selection weights (per-user attempt_count
 * from user_target_color_stats drives quota-priority weighting instead).
 *
 * Safe to re-run.
 */
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const { Client } = require('pg');

function normalizeDatabaseUrl(url) {
  if (!url) return url;
  if (url.startsWith('postgres://')) {
    return url.replace('postgres://', 'postgresql://');
  }
  return url;
}

async function run(client, label, sql, params) {
  try {
    const res = await client.query(sql, params);
    console.log(`  ✓ ${label}${res.rowCount != null ? ` (${res.rowCount} rows)` : ''}`);
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
    console.log('Connected. Running level_required migration…\n');

    // ── Add column ──────────────────────────────────────────────────────────
    await run(
      client,
      'target_colors.level_required column',
      `ALTER TABLE target_colors
       ADD COLUMN IF NOT EXISTS level_required INTEGER NOT NULL DEFAULT 1`
    );

    // ── Assign level tiers by catalog_order ─────────────────────────────────
    // Level 1 — starters (already default 1, explicit for clarity)
    await run(
      client,
      'level_required = 1 for Orange/Purple/Green (catalog_order 0-2)',
      `UPDATE target_colors SET level_required = 1 WHERE catalog_order IN (0, 1, 2)`
    );

    await run(
      client,
      'level_required = 2 for Pink/Olive/Custom (catalog_order 3-5)',
      `UPDATE target_colors SET level_required = 2 WHERE catalog_order IN (3, 4, 5)`
    );

    await run(
      client,
      'level_required = 3 for Peach/Coral/Turquoise (catalog_order 6-8)',
      `UPDATE target_colors SET level_required = 3 WHERE catalog_order IN (6, 7, 8)`
    );

    await run(
      client,
      'level_required = 4 for Chartreuse/Teal (catalog_order 9-10)',
      `UPDATE target_colors SET level_required = 4 WHERE catalog_order IN (9, 10)`
    );

    await run(
      client,
      'level_required = 5 for all skin_light (catalog_order 11-24)',
      `UPDATE target_colors SET level_required = 5 WHERE classification = 'skin_light'`
    );

    await run(
      client,
      'level_required = 7 for all skin_dark (catalog_order 25-39)',
      `UPDATE target_colors SET level_required = 7 WHERE classification = 'skin_dark'`
    );

    // ── Index for fast per-level queries ────────────────────────────────────
    await run(
      client,
      'index on target_colors.level_required',
      `CREATE INDEX IF NOT EXISTS idx_target_colors_level_required
       ON target_colors (level_required)`
    );

    // ── Normalize frequency to 1 ────────────────────────────────────────────
    // Historical play counts are no longer used for selection weighting.
    // Per-user quota weighting uses user_target_color_stats.attempt_count.
    await run(
      client,
      'normalize frequency = 1 for all rows',
      `UPDATE target_colors SET frequency = 1 WHERE frequency != 1`
    );

    console.log('\n✅ level_required migration complete.');
  } catch (err) {
    console.error('\n❌ Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
