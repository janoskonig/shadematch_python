// Generic one-shot renderer: node render_one.js <htmlPath> <outPng> <w> <h>
const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path');

const [, , htmlPath, outPng, wArg, hArg] = process.argv;
const w = parseInt(wArg, 10), h = parseInt(hArg, 10);

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: w, height: h });
  const url = 'file://' + path.resolve(htmlPath);
  await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 })
    .catch(async () => { await page.goto(url, { waitUntil: 'load' }); });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: outPng, clip: { x: 0, y: 0, width: w, height: h } });
  console.log('rendered', path.basename(outPng));
  await browser.close();
})();
