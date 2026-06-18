// Capture screenshots of the running ShadeMatch app at a given base URL.
// Usage:  node demo/capture.mjs <baseURL> <label> [route1 route2 ...]
// Example: node demo/capture.mjs http://localhost:5001 now / /spectral /lab
//
// Saves to demo/evolution/<label>-<routeSlug>.png

import { chromium } from 'playwright';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, 'evolution');
mkdirSync(outDir, { recursive: true });

const [baseURL, label, ...routesArg] = process.argv.slice(2);
if (!baseURL || !label) {
  console.error('usage: node demo/capture.mjs <baseURL> <label> [routes...]');
  process.exit(1);
}
const routes = routesArg.length ? routesArg : ['/'];
const slug = (r) => (r === '/' ? 'home' : r.replace(/^\//, '').replace(/\//g, '_'));

const browser = await chromium.launch();
try {
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  // Pre-set cookie-consent so the banner doesn't cover the UI, if the app reads it.
  await ctx.addInitScript(() => {
    try { localStorage.setItem('cookieConsent', JSON.stringify({ necessary: true, analytics: false, preferences: true })); } catch (e) {}
  });
  for (const route of routes) {
    const page = await ctx.newPage();
    const url = baseURL.replace(/\/$/, '') + route;
    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 });
    } catch (e) {
      console.log(`  [warn] ${route}: ${e.message.split('\n')[0]}`);
    }
    await page.waitForTimeout(1500);
    // Hide consent/cookie/login overlays so we photograph the actual UI behind them.
    await page.evaluate(() => {
      document.querySelectorAll('.modal-overlay, .skip-modal-overlay, .cookie-consent-banner, #researchConsentModal, #userModal, #instructionsModal').forEach((el) => { el.style.display = 'none'; });
      document.body.classList.remove('no-scroll', 'is-locked', 'modal-open');
      document.body.style.overflow = '';
    });
    await page.waitForTimeout(700);
    const file = join(outDir, `${label}-${slug(route)}.png`);
    await page.screenshot({ path: file, fullPage: false });
    console.log(`  saved ${file}`);
    await page.close();
  }
} finally {
  await browser.close();
}
