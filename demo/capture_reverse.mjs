// Capture the /reverse_engineer solver RESULT view: upload sample_spectrum.csv,
// run the solve, then screenshot the Pareto recipes + metamerism + reachability.
// Usage: node demo/capture_reverse.mjs [baseURL]
import { chromium } from 'playwright';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const repo = dirname(here);
const outDir = join(here, 'evolution');
mkdirSync(outDir, { recursive: true });
const baseURL = (process.argv[2] || 'http://localhost:5001').replace(/\/$/, '');
const csv = join(repo, 'sample_spectrum.csv');

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1200, height: 1300 } });
  await page.goto(baseURL + '/reverse_engineer', { waitUntil: 'networkidle', timeout: 20000 });
  await page.setInputFiles('#spectrumFile', csv);
  await page.waitForTimeout(400);
  await page.click('#analyzeBtn');
  // Wait for the results section to render (solver returns recipe options).
  await page.waitForSelector('#results', { state: 'visible', timeout: 30000 });
  await page.waitForTimeout(1500);
  const file = join(outDir, 'now-reverse.png');
  await page.screenshot({ path: file, fullPage: true });
  console.log('saved ' + file);
  await page.close();
} finally {
  await browser.close();
}
