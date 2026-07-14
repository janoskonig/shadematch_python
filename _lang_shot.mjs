import { chromium } from 'playwright';
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
await page.goto('https://shadestudy.com/?lang=hu', { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);
// dismiss cookie banner if present
try { await page.click('text=ELFOGADOM', { timeout: 2500 }); } catch {}
await page.waitForTimeout(400);
const trigger = await page.$('#overflowTrigger');
console.log('overflow trigger visible:', trigger ? await trigger.isVisible() : false);
if (trigger && await trigger.isVisible()) {
  await trigger.click();
  await page.waitForTimeout(400);
  const sel = await page.$('#langSelect');
  console.log('lang selector visible after open:', sel ? await sel.isVisible() : false);
}
await page.screenshot({ path: process.env.SCRATCH + '/lang_selector.png' });
await browser.close();
