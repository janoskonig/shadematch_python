// Capture the Gamut Lab with a populated result: run "Find widest gamut",
// wait for the CIELAB a*-b* plot + skin-gamut overlay + coverage metrics.
// Usage: node demo/capture_gamut.mjs [baseURL]
import { chromium } from 'playwright';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, 'evolution');
mkdirSync(outDir, { recursive: true });
const baseURL = (process.argv[2] || 'http://localhost:5001').replace(/\/$/, '');

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 1480 } });
  await page.goto(baseURL + '/gamut', { waitUntil: 'networkidle', timeout: 20000 });
  await page.waitForTimeout(1200);              // catalog + skin gamut load
  await page.click('#runBtn');                  // greedy "Find widest gamut"
  // Wait for the gamut volume readout to leave its placeholder.
  await page.waitForFunction(() => {
    const el = document.getElementById('resVol');
    return el && el.textContent.trim() && el.textContent.trim() !== '—';
  }, { timeout: 40000 });
  await page.waitForTimeout(2200);              // let Plotly draw the hull + skin overlay
  // Hide the cookie banner so it doesn't cover the plot.
  await page.evaluate(() => {
    document.querySelectorAll('.cookie-consent-banner, .modal-overlay').forEach((el) => { el.style.display = 'none'; });
  });
  await page.evaluate(() => { const p = document.getElementById('gamutPlot'); if (p) p.scrollIntoView({ block: 'center' }); });
  await page.waitForTimeout(500);
  const file = join(outDir, 'now-gamut.png');
  await page.screenshot({ path: file, fullPage: false });
  console.log('saved ' + file);
  await page.close();
} finally {
  await browser.close();
}
