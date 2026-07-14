const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path');
const fs = require('fs');

const SCRATCH = __dirname;
const FPS = 30;
const DURATION_MS = 6800;   // 6.5s timeline + short hold

(async () => {
  const browser = await chromium.launch();
  const FORMATS = { '45': [1080, 1350], '916': [1080, 1920] };
  for (const lang of ['hu', 'en']) {
    for (const [fmt, [w, h]] of Object.entries(FORMATS)) {
      const page = await browser.newPage({ viewport: { width: w, height: h } });
      const framesDir = path.join(SCRATCH, `frames_${fmt}_${lang}`);
      fs.mkdirSync(framesDir, { recursive: true });
      await page.goto('file://' + path.join(SCRATCH, `insta_anim_${fmt}_${lang}.html`), { waitUntil: 'networkidle', timeout: 20000 }).catch(async () => {
        await page.goto('file://' + path.join(SCRATCH, `insta_anim_${fmt}_${lang}.html`), { waitUntil: 'load' });
      });
      await page.evaluate(() => document.fonts.ready);
      await page.evaluate(() => document.getAnimations().forEach(a => a.pause()));
      const nFrames = Math.round(DURATION_MS / 1000 * FPS);
      for (let i = 0; i < nFrames; i++) {
        const t = i * 1000 / FPS;
        await page.evaluate(ms => document.getAnimations().forEach(a => { a.currentTime = ms; }), t);
        await page.screenshot({ path: path.join(framesDir, `f${String(i).padStart(4, '0')}.png`) });
      }
      await page.close();
      console.log(`captured ${nFrames} frames for ${fmt}_${lang}`);
    }
  }
  await browser.close();
})();
