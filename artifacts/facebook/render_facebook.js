const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path');

const SCRATCH = __dirname;
const OUT = process.argv[2] || SCRATCH;

// [htmlBaseName, outputName, width, height]
const JOBS = [
  ['fb_profile',  'shadestudy_fb_profile',    500,  500],
  ['fb_cover_en', 'shadestudy_fb_cover_EN',  1640,  624],
  ['fb_cover_hu', 'shadestudy_fb_cover_HU',  1640,  624],
  ['fb_post_en',  'shadestudy_fb_post_EN',   1080, 1080],
  ['fb_post_hu',  'shadestudy_fb_post_HU',   1080, 1080],
  ['fb_challenge_square_en',   'shadestudy_fb_challenge_square_EN',   1080, 1080],
  ['fb_challenge_square_hu',   'shadestudy_fb_challenge_square_HU',   1080, 1080],
  ['fb_challenge_portrait_en', 'shadestudy_fb_challenge_portrait_EN', 1080, 1350],
  ['fb_challenge_portrait_hu', 'shadestudy_fb_challenge_portrait_HU', 1080, 1350],
  ['fb_challenge2_square',   'shadestudy_fb_challenge2_square',   1080, 1080],
  ['fb_challenge2_portrait', 'shadestudy_fb_challenge2_portrait', 1080, 1350],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  for (const [src, out, w, h] of JOBS) {
    await page.setViewportSize({ width: w, height: h });
    const url = 'file://' + path.join(SCRATCH, `${src}.html`);
    await page.goto(url, { waitUntil: 'networkidle', timeout: 20000 })
      .catch(async () => { await page.goto(url, { waitUntil: 'load' }); });
    await page.evaluate(() => document.fonts.ready);
    const dest = path.join(OUT, `${out}.png`);
    await page.screenshot({ path: dest, clip: { x: 0, y: 0, width: w, height: h } });
    console.log('rendered', dest.split('/').pop());
  }
  await browser.close();
})();
