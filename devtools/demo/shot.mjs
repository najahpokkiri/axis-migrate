// Dev-only: screenshot a report (or any page) and selected elements with the system Chrome.
//   node devtools/demo/shot.mjs <url> <out-prefix> [selector ...]
// Writes <out-prefix>-top.png (1280x720 first screen) and <out-prefix>-<n>.png per selector (element screenshot).
// A selector may be "sel@maxHeight" to clip a tall element to its first maxHeight pixels.
import { chromium } from '../graph-ui/node_modules/playwright-core/index.mjs';
const [url, prefix, ...sels] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
const errors = [];
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
await page.goto(url);
await page.waitForTimeout(1500);
await page.screenshot({ path: `${prefix}-top.png` });
let i = 1;
for (const raw of sels) {
  const [sel, maxh] = raw.split('@');
  const el = page.locator(sel).first();
  await el.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  const box = await el.boundingBox();
  if (!box) { errors.push(`no element ${sel}`); continue; }
  const h = maxh ? Math.min(box.height, Number(maxh)) : box.height;
  await page.screenshot({ path: `${prefix}-${i++}.png`, fullPage: true, clip: { x: box.x, y: box.y + (await page.evaluate(() => window.scrollY)), width: box.width, height: h } });
}
console.log(JSON.stringify({ errors }));
await browser.close();
