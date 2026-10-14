// Dev-only (demo rehearsal): drive the REAL graph page (graph_server.py serve) in the system Chrome, as the
// presenter would click it, and screenshot each moment.
//   node devtools/demo/graph_drive.mjs <url-with-token> <out-dir> <action> [<action> ...]
// actions: node:<id> (click a graph node) · btn:<accessible name> (first matching button) · dbtn:<name> (button
// inside the open dialog) · css:<selector> · fill:<selector>=><text> · select:<selector>=><value> · wait:<ms> ·
// shot:<name> (screenshot to <out-dir>/<name>.png) · fit (press the React Flow fit-view control)
import { chromium } from '../graph-ui/node_modules/playwright-core/index.mjs';
const [url, out, ...acts] = process.argv.slice(2);
const browser = await chromium.launch({ executablePath: process.env.CHROME || '/usr/bin/google-chrome', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: Number(process.env.W || 1440), height: Number(process.env.H || 860) } });
const log = [];
page.on('console', (m) => { if (m.type() === 'error') log.push(`console: ${m.text()}`); });
page.on('pageerror', (e) => log.push(`pageerror: ${e.message}`));
page.on('dialog', (d) => d.accept());
page.on('response', (r) => { if (r.url().includes('/api/') && r.request().method() === 'POST') log.push(`POST ${new URL(r.url()).pathname} -> ${r.status()}`); });
const t0 = Date.now();
await page.goto(url);
await page.waitForSelector('.react-flow__node', { timeout: 15000 });
await page.waitForTimeout(800);
log.push(`loaded in ${Date.now() - t0} ms`);
for (const a of acts) {
  const i = a.indexOf(':'); const k = i < 0 ? a : a.slice(0, i); const v = i < 0 ? '' : a.slice(i + 1);
  if (k === 'node') await page.locator(`.react-flow__node[data-id="${v}"]`).click();
  else if (k === 'btn') await page.getByRole('button', { name: v }).first().click();
  else if (k === 'dbtn') await page.locator('form.dialog').getByRole('button', { name: v }).first().click();
  else if (k === 'css') await page.locator(v).first().click();
  else if (k === 'fill') { const j = v.indexOf('=>'); await page.locator(v.slice(0, j)).first().fill(v.slice(j + 2)); }
  else if (k === 'select') { const j = v.indexOf('=>'); await page.locator(v.slice(0, j)).first().selectOption(v.slice(j + 2)); }
  else if (k === 'wait') await page.waitForTimeout(Number(v));
  else if (k === 'fit') await page.locator('.react-flow__controls-fitview').first().click();
  else if (k === 'shot') { await page.waitForTimeout(400); await page.screenshot({ path: `${out}/${v}.png` }); }
  else throw new Error(`unknown action ${a}`);
  if (k !== 'shot' && k !== 'wait') { log.push(`${a} @ ${Date.now() - t0} ms`); await page.waitForTimeout(500); }
}
console.log(log.join('\n'));
await browser.close();
