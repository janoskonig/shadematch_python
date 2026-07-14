// Frame-driven capture: node capture_duel.js <htmlPath> <outDir> <w> <h> <nFrames>
const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path'), fs = require('fs');

const [, , htmlPath, outDir, wArg, hArg, nArg] = process.argv;
const w = parseInt(wArg,10), h = parseInt(hArg,10), N = parseInt(nArg,10);
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.resolve(htmlPath), { waitUntil: 'load' });
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(300);
  for (let f = 0; f < N; f++) {
    const p = N > 1 ? f / (N - 1) : 0;
    await page.evaluate(pp => window.SM.render(pp), p);
    const name = 'frame_' + String(f).padStart(4, '0') + '.png';
    await page.screenshot({ path: path.join(outDir, name), clip: { x:0, y:0, width:w, height:h } });
  }
  console.log('captured', N, 'frames ->', outDir);
  await browser.close();
})();
