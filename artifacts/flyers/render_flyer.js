const { chromium } = require('/Users/janoskonig/shadematch_python/node_modules/playwright');
const path = require('path');

const SCRATCH = __dirname;
const OUT = process.argv[2] || SCRATCH;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const jobs = [];
  for (const lang of ['hu', 'en']) {
    const L = lang.toUpperCase();
    jobs.push({ html: `flyer_a5_${lang}.html`, pdf: `shadestudy_flyer_A5_${L}.pdf`, w: '148mm', h: '210mm' });
    jobs.push({ html: `flyer_a4_${lang}.html`, pdf: `shadestudy_flyer_A4_${L}.pdf`, w: '210mm', h: '297mm' });
    jobs.push({ html: `flyer_a5_${lang}_print.html`, pdf: `shadestudy_flyer_A5_${L}_print.pdf`, w: '154mm', h: '216mm' });
    jobs.push({ html: `flyer_a4_${lang}_print.html`, pdf: `shadestudy_flyer_A4_${L}_print.pdf`, w: '216mm', h: '303mm' });
    jobs.push({ html: `flyer_a5_${lang}_press.html`, pdf: `shadestudy_flyer_A5_${L}_press_rgb.pdf`, w: '164mm', h: '226mm' });
    jobs.push({ html: `flyer_a4_${lang}_press.html`, pdf: `shadestudy_flyer_A4_${L}_press_rgb.pdf`, w: '226mm', h: '313mm' });
  }
  for (const j of jobs) {
    await page.goto('file://' + path.join(SCRATCH, j.html), { waitUntil: 'networkidle', timeout: 20000 }).catch(async () => {
      await page.goto('file://' + path.join(SCRATCH, j.html), { waitUntil: 'load' });
    });
    await page.evaluate(() => document.fonts.ready);
    await page.pdf({ path: path.join(OUT, j.pdf), width: j.w, height: j.h, printBackground: true, pageRanges: '1' });
    console.log('rendered', j.pdf);
  }
  await browser.close();
})();
