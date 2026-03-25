#!/usr/bin/env node
/**
 * target_colors catalog + mixing_sessions.target_color_id FK and backfill.
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

const COLOR_FREQUENCY_DATA = {
  '#FFB3BC': 145,
  '#FFE4AF': 108,
  '#6F7A66': 102,
  '#71703E': 101,
  '#547A7A': 96,
  '#FF8352': 90,
  '#679DAE': 68,
  '#9DD267': 66,
  '#D1AE90': 48,
  '#BE8870': 39,
  '#AE967E': 22,
  '#C3A28F': 17,
  '#A97367': 16,
  '#CB9781': 11,
  '#E8B7BA': 10,
  '#A58F5E': 18,
  '#B5866A': 20,
  '#DE958F': 1,
  '#99856A': 5,
  '#A8856F': 23,
  '#A07E63': 4,
  '#80685C': 3,
  '#584B42': 13,
  '#7B5749': 14,
  '#543B34': 9,
  '#583E2D': 2,
  '#A76662': 21,
  '#A28074': 7,
  '#8F7868': 8,
  '#9F7954': 1,
  '#392D1D': 19,
  '#9D7248': 12,
  '#58482F': 24,
};

function rgbToHex(r, g, b) {
  return (
    '#' +
    [r, g, b]
      .map((x) => {
        const h = x.toString(16);
        return h.length === 1 ? '0' + h : h;
      })
      .join('')
      .toUpperCase()
  );
}

function frequencyForRgb(r, g, b) {
  return COLOR_FREQUENCY_DATA[rgbToHex(r, g, b)] || 1;
}

/** Same palette order as legacy static/main.js allTargetColors */
const SEED_DEFINITIONS = [
  { catalog_order: 0, name: 'Orange', color_type: 'basic', classification: null, r: 255, g: 102, b: 30 },
  { catalog_order: 1, name: 'Purple', color_type: 'basic', classification: null, r: 113, g: 1, b: 105 },
  { catalog_order: 2, name: 'Green', color_type: 'basic', classification: null, r: 78, g: 150, b: 100 },
  { catalog_order: 3, name: 'Pink', color_type: 'basic', classification: null, r: 255, g: 179, b: 188 },
  { catalog_order: 4, name: 'Olive', color_type: 'basic', classification: null, r: 113, g: 112, b: 62 },
  { catalog_order: 5, name: 'Custom', color_type: 'basic', classification: null, r: 111, g: 122, b: 102 },
  { catalog_order: 6, name: 'Peach', color_type: 'basic', classification: null, r: 255, g: 228, b: 175 },
  { catalog_order: 7, name: 'Coral', color_type: 'basic', classification: null, r: 255, g: 131, b: 82 },
  { catalog_order: 8, name: 'Turquoise', color_type: 'basic', classification: null, r: 103, g: 157, b: 174 },
  { catalog_order: 9, name: 'Chartreuse', color_type: 'basic', classification: null, r: 157, g: 210, b: 103 },
  { catalog_order: 10, name: 'Teal', color_type: 'basic', classification: null, r: 84, g: 122, b: 122 },
  { catalog_order: 11, name: '#D1AE90', color_type: 'skin', classification: 'skin_light', r: 208, g: 176, b: 148 },
  { catalog_order: 12, name: '#AE967E', color_type: 'skin', classification: 'skin_light', r: 175, g: 149, b: 126 },
  { catalog_order: 13, name: '#C3A28F', color_type: 'skin', classification: 'skin_light', r: 242, g: 166, b: 129 },
  { catalog_order: 14, name: '#BE8870', color_type: 'skin', classification: 'skin_light', r: 193, g: 135, b: 115 },
  { catalog_order: 15, name: '#6D544D', color_type: 'skin', classification: 'skin_light', r: 178, g: 125, b: 107 },
  { catalog_order: 16, name: '#34261B', color_type: 'skin', classification: 'skin_light', r: 205, g: 87, b: 91 },
  { catalog_order: 17, name: '#C8AF91', color_type: 'skin', classification: 'skin_light', r: 208, g: 176, b: 148 },
  { catalog_order: 18, name: '#A97367', color_type: 'skin', classification: 'skin_light', r: 172, g: 115, b: 104 },
  { catalog_order: 19, name: '#CB9781', color_type: 'skin', classification: 'skin_light', r: 212, g: 147, b: 125 },
  { catalog_order: 20, name: '#B68678', color_type: 'skin', classification: 'skin_light', r: 193, g: 135, b: 115 },
  { catalog_order: 21, name: '#E8B7BA', color_type: 'skin', classification: 'skin_light', r: 228, g: 183, b: 190 },
  { catalog_order: 22, name: '#A58F5E', color_type: 'skin', classification: 'skin_light', r: 167, g: 145, b: 92 },
  { catalog_order: 23, name: '#B5866A', color_type: 'skin', classification: 'skin_light', r: 180, g: 134, b: 106 },
  { catalog_order: 24, name: '#DE958F', color_type: 'skin', classification: 'skin_light', r: 225, g: 155, b: 151 },
  { catalog_order: 25, name: '#99856A', color_type: 'skin', classification: 'skin_dark', r: 155, g: 131, b: 108 },
  { catalog_order: 26, name: '#A8856F', color_type: 'skin', classification: 'skin_dark', r: 182, g: 137, b: 96 },
  { catalog_order: 27, name: '#A07E63', color_type: 'skin', classification: 'skin_dark', r: 169, g: 120, b: 74 },
  { catalog_order: 28, name: '#80685C', color_type: 'skin', classification: 'skin_dark', r: 143, g: 103, b: 88 },
  { catalog_order: 29, name: '#584B42', color_type: 'skin', classification: 'skin_dark', r: 88, g: 71, b: 52 },
  { catalog_order: 30, name: '#7B5749', color_type: 'skin', classification: 'skin_dark', r: 127, g: 84, b: 67 },
  { catalog_order: 31, name: '#543B34', color_type: 'skin', classification: 'skin_dark', r: 174, g: 121, b: 123 },
  { catalog_order: 32, name: '#583E2D', color_type: 'skin', classification: 'skin_dark', r: 80, g: 62, b: 41 },
  { catalog_order: 33, name: '#A76662', color_type: 'skin', classification: 'skin_dark', r: 161, g: 104, b: 98 },
  { catalog_order: 34, name: '#A28074', color_type: 'skin', classification: 'skin_dark', r: 165, g: 130, b: 118 },
  { catalog_order: 35, name: '#8F7868', color_type: 'skin', classification: 'skin_dark', r: 144, g: 121, b: 101 },
  { catalog_order: 36, name: '#9F7954', color_type: 'skin', classification: 'skin_dark', r: 189, g: 131, b: 76 },
  { catalog_order: 37, name: '#392D1D', color_type: 'skin', classification: 'skin_dark', r: 57, g: 42, b: 22 },
  { catalog_order: 38, name: '#9D7248', color_type: 'skin', classification: 'skin_dark', r: 150, g: 114, b: 71 },
  { catalog_order: 39, name: '#58482F', color_type: 'skin', classification: 'skin_dark', r: 88, g: 68, b: 44 },
];

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

    await client.query(`
      CREATE TABLE IF NOT EXISTS target_colors (
        id SERIAL PRIMARY KEY,
        name VARCHAR(128) NOT NULL,
        color_type VARCHAR(32) NOT NULL,
        classification VARCHAR(64),
        r SMALLINT NOT NULL,
        g SMALLINT NOT NULL,
        b SMALLINT NOT NULL,
        frequency INTEGER NOT NULL DEFAULT 1,
        catalog_order INTEGER NOT NULL
      )
    `);

    await client.query(`
      CREATE UNIQUE INDEX IF NOT EXISTS target_colors_catalog_order_key
      ON target_colors (catalog_order)
    `);

    for (const row of SEED_DEFINITIONS) {
      const frequency = frequencyForRgb(row.r, row.g, row.b);
      await client.query(
        `
        INSERT INTO target_colors (name, color_type, classification, r, g, b, frequency, catalog_order)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (catalog_order) DO UPDATE SET
          name = EXCLUDED.name,
          color_type = EXCLUDED.color_type,
          classification = EXCLUDED.classification,
          r = EXCLUDED.r,
          g = EXCLUDED.g,
          b = EXCLUDED.b,
          frequency = EXCLUDED.frequency
        `,
        [
          row.name,
          row.color_type,
          row.classification,
          row.r,
          row.g,
          row.b,
          frequency,
          row.catalog_order,
        ]
      );
    }

    await client.query(
      'ALTER TABLE mixing_sessions ADD COLUMN IF NOT EXISTS target_color_id INTEGER'
    );

    await client.query(`
      DO $$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM pg_constraint WHERE conname = 'mixing_sessions_target_color_id_fkey'
        ) THEN
          ALTER TABLE mixing_sessions
            ADD CONSTRAINT mixing_sessions_target_color_id_fkey
            FOREIGN KEY (target_color_id) REFERENCES target_colors (id);
        END IF;
      END $$
    `).catch(() => {});

    const backfill = await client.query(`
      UPDATE mixing_sessions ms
      SET target_color_id = pick.id
      FROM (
        SELECT DISTINCT ON (r, g, b) r, g, b, id
        FROM target_colors
        ORDER BY r, g, b, id
      ) AS pick
      WHERE ms.target_color_id IS NULL
        AND ms.target_r = pick.r
        AND ms.target_g = pick.g
        AND ms.target_b = pick.b
    `);
    console.log(
      `OK: target_colors (${SEED_DEFINITIONS.length} rows); mixing_sessions.target_color_id; backfill rows: ${backfill.rowCount ?? 0}.`
    );
  } catch (err) {
    console.error('Migration failed:', err.message);
    process.exit(1);
  } finally {
    await client.end().catch(() => {});
  }
}

main();
