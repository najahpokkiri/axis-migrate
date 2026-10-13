// Dev-only smoke: open the editor in the system Chrome, print console errors, take screenshots.
//   node shot.mjs <url-with-token> <out-prefix> [click-node-id ...]
import { chromium } from 'playwright-core';
const [url, prefix, ...clicks] = process.argv.slice(2);
const exe = process.env.CHROME || '/usr/bin/google-chrome';
const browser = await chromium.launch({ executablePath: exe, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 860 } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`${m.type()}: ${m.text()}`); });
page.on('dialog', (d) => d.accept());
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));
await page.goto(url);
await page.waitForSelector('.react-flow__node', { timeout: 10000 });
await page.waitForTimeout(600);
await page.screenshot({ path: `${prefix}-0.png` });
let i = 1;
for (const c of clicks) {
  if (c.startsWith('btn:')) await page.getByRole('button', { name: c.slice(4) }).first().click();
  else if (c.startsWith('css:')) await page.locator(c.slice(4)).first().click();
  else if (c.startsWith('fill:')) { const [sel, val] = c.slice(5).split('='); await page.locator(sel).first().fill(val); }
  else await page.locator(`.react-flow__node[data-id="${c}"]`).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${prefix}-${i++}.png` });
}
console.log(JSON.stringify({ errors, nodes: await page.locator('.react-flow__node').count(), edges: await page.locator('.react-flow__edge').count() }));
await browser.close();
