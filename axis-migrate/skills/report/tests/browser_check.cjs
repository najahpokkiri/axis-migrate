// Headless check of a rendered report (07 §6 tests 1 and 14). Dev-only: needs playwright-core (resolved from
// AXIS_PLAYWRIGHT_DIR, default ~/axis-migrate-dev/devtools/report-vendor/node_modules) and a Chrome/Chromium binary
// (AXIS_CHROME, default google-chrome). Prints one JSON object on stdout.
// usage: node browser_check.cjs <index.html> [screenshot.png] [--swipe]
"use strict";
const path = require("path");
const os = require("os");
const modDir = process.env.AXIS_PLAYWRIGHT_DIR || path.join(os.homedir(), "axis-migrate-dev/devtools/report-vendor/node_modules");
const { chromium } = require(path.join(modDir, "playwright-core"));

(async () => {
  const [html, shot] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
  const swipe = process.argv.includes("--swipe");
  const exe = process.env.AXIS_CHROME || "/usr/bin/google-chrome";
  const browser = await chromium.launch({ executablePath: exe, headless: true,
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, offline: true });
  const page = await context.newPage();
  const requests = [], errors = [];
  page.on("request", (r) => requests.push(r.url()));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("file://" + path.resolve(html));
  let mapReady = false;
  try { await page.waitForSelector("#swipe-wrap[data-ready='1']", { timeout: 15000 }); mapReady = true; } catch (e) { mapReady = false; }
  await page.waitForTimeout(500);
  const out = await page.evaluate(() => {
    const box = (sel) => { const el = document.querySelector(sel); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.x, y: r.y, w: r.width, h: r.height, bottom: r.bottom }; };
    const fails = document.querySelector("#failures");
    const items = fails ? Array.from(fails.querySelectorAll("li")).map((li) => { const r = li.getBoundingClientRect(); return { text: li.innerText, bottom: r.bottom, visible: !!(li.offsetWidth || li.offsetHeight) }; }) : [];
    let hiddenAncestor = false, detailsAncestor = false;
    for (let el = fails; el; el = el.parentElement) { if (el.tagName === "DETAILS") detailsAncestor = true; const cs = getComputedStyle(el); if (cs.display === "none" || cs.visibility === "hidden") hiddenAncestor = true; }
    const fb = document.getElementById("map-fallback");
    return { failures: box("#failures"), items, detailsAncestor, hiddenAncestor, verdict: (document.getElementById("verdict") || {}).innerText,
      fallbackShown: fb ? !fb.hidden : null, caption: (document.getElementById("map-caption") || {}).innerText,
      compare: !!(window.AXIS_MAPS && window.AXIS_MAPS.compare),
      views: Array.from(document.querySelectorAll("div.views")).map((v) => { const r = v.getBoundingClientRect(); const t = v.querySelector("table.views-table"); return { id: v.id, rows: v.querySelectorAll("tr.view-row").length, changed: v.querySelectorAll("tr.view-row:not([data-state='ok'])").length, receipts: Array.from(v.querySelectorAll("tr.receipt")).map((x) => x.dataset.kind + ":" + x.dataset.status), shaTitles: Array.from(v.querySelectorAll("code.sha")).filter((c) => /^[0-9a-f]{64}$/.test(c.title) && c.innerText === c.title.slice(0, 12)).length, w: r.width, h: r.height, tableW: t ? t.getBoundingClientRect().width : null, text: v.innerText }; }),
      mapViewLinks: Array.from(document.querySelectorAll("#map-views-links a")).map((a) => a.getAttribute("href")),
      sections: Array.from(document.querySelectorAll("body > main > section, body > main > header, body > div#integrity")).map((s) => s.id) };
  });
  out.mapReady = mapReady;
  if (swipe && out.compare) {
    const before = await page.evaluate(() => { const c = document.querySelector(".maplibregl-compare"); return c ? c.style.transform || c.getBoundingClientRect().x : null; });
    const wrap = await page.$("#swipe-wrap"); await wrap.scrollIntoViewIfNeeded(); await page.waitForTimeout(200); const b = await wrap.boundingBox();
    const handle = await page.$(".maplibregl-compare .compare-swiper-vertical");
    if (handle) { const hb = await handle.boundingBox(); await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2); await page.mouse.down(); await page.mouse.move(b.x + b.width * 0.25, hb.y + hb.height / 2, { steps: 5 }); await page.mouse.up(); }
    const after = await page.evaluate(() => { const c = document.querySelector(".maplibregl-compare"); return c ? c.style.transform || c.getBoundingClientRect().x : null; });
    out.swipe = { before, after, moved: before !== after };
    out.rightClip = await page.evaluate(() => { const m = document.getElementById("map-new"); return m ? m.style.clip || m.style.clipPath : null; });
  }
  if (shot) { await page.evaluate(() => window.scrollTo(0, 0)); await page.screenshot({ path: shot, fullPage: false }); }
  const mapShot = process.argv.find((a) => a.startsWith("--mapshot="));
  if (mapShot) { const w = await page.$("#map"); await w.scrollIntoViewIfNeeded(); await w.screenshot({ path: mapShot.slice(10) }); }
  out.requests = requests;
  out.external = requests.filter((u) => !u.startsWith("file://") && !u.startsWith("data:") && !u.startsWith("blob:"));
  out.errors = errors;
  await browser.close();
  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stdout.write(JSON.stringify({ fatal: String(e) })); process.exit(1); });
