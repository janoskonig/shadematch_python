// Playwright showcase for demo/pitch.html
// Opens the standalone deck, steps through every slide, and screenshots it at
// desktop (1280x720, 16:9) and mobile (390x844) widths to prove it's responsive.
//
// Run:  node demo/showcase.mjs
// (auto-installs the chromium browser the first time if it's missing)

import { chromium } from 'playwright';
import { fileURLToPath, pathToFileURL } from 'url';
import { dirname, join } from 'path';
import { mkdirSync } from 'fs';

const here = dirname(fileURLToPath(import.meta.url));
const deckUrl = pathToFileURL(join(here, 'pitch.html')).href;
const outDir = join(here, 'screenshots');
mkdirSync(outDir, { recursive: true });

const viewports = [
  { name: 'desktop', width: 1280, height: 720 },
  { name: 'mobile', width: 390, height: 844 },
];

const browser = await chromium.launch();
try {
  for (const vp of viewports) {
    const page = await browser.newPage({ viewport: { width: vp.width, height: vp.height } });
    await page.goto(deckUrl, { waitUntil: 'networkidle' });

    const count = await page.evaluate(() => window.__deck.count());
    console.log(`\n[${vp.name}] ${vp.width}x${vp.height} — ${count} slides`);

    for (let i = 0; i < count; i++) {
      await page.evaluate((idx) => window.__deck.go(idx), i);
      await page.waitForTimeout(350); // let the progress bar / transition settle
      const idx = String(i + 1).padStart(2, '0');
      const file = join(outDir, `${vp.name}-slide-${idx}.png`);
      await page.screenshot({ path: file });
      console.log(`  saved ${file}`);
    }
    await page.close();
  }
  console.log(`\nDone. Screenshots in ${outDir}`);
} finally {
  await browser.close();
}
