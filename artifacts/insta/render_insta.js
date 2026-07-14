const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path');

const SCRATCH = __dirname;
const OUT = process.argv[2] || SCRATCH;
const SIZES = { square: [1080, 1080], portrait: [1080, 1350], story: [1080, 1920] };

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const lang of ['hu', 'en']) {
    for (const [fmt, [w, h]] of Object.entries(SIZES)) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto('file://' + path.join(SCRATCH, `insta_${fmt}_${lang}.html`), { waitUntil: 'networkidle', timeout: 20000 }).catch(async () => {
        await page.goto('file://' + path.join(SCRATCH, `insta_${fmt}_${lang}.html`), { waitUntil: 'load' });
      });
      await page.evaluate(() => document.fonts.ready);
      const out = path.join(OUT, `shadestudy_insta_${fmt}_${lang.toUpperCase()}.png`);
      await page.screenshot({ path: out, clip: { x: 0, y: 0, width: w, height: h } });
      console.log('rendered', out.split('/').pop());
    }
  }
  await browser.close();
})();
