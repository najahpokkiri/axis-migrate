---
name: map
description: Sub-step of the axis-migrate migrate flow. Use to show the legacy inputs (at discovery) and legacy vs new results of a migration on a local GeoLibre map in the browser pane (display only; numbers still decide). Not for general web maps or GIS visualisation.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Map: you look, the person watches; numbers still decide

`$P` = plugin root; `<run>` = `migration/runs/<run_id>`. Run every script as `"$P/scripts/axpy" "$P/skills/map/<x>.py" …`
from the project folder (they switch to the map env themselves; first use installs GeoLibre 3.4.0, ~60 MB download,
outside the plugin folder). Nothing here feeds a verdict: findings go through `plot.py review-add` as a view
receipt (verify SKILL §3.6: view projects + sha256, engine, cells read) or, without a pane, as PNG receipts.
Not for general web maps or GIS visualisation outside a migration: answer those directly and start no migration.

## 0 Before compare (discover)
`views.py inputs migration` (no run needed; reads `inventory.json`, roles from `graph.json` when it exists) writes
`discover/views/`: per input raster and legacy intermediate/final raster a COG copy (`<ds>/data.tif`) and
`cell-<id>` projects (`max`, `aoi-edge` or `edge`, `interior`); per input vector a GeoJSON (EPSG:4326, attributes
kept) and `feature-<id>` projects (`first`, `largest`, `central`); `inputs-overview` (every input; legacy rasters in
the Layers panel, hidden) and one `grid-<family>` per grid family (members toggleable, each member's cell edges,
the AOI outline; bookmarks = `grid_corners.png`'s five windows). When no dataset has role input, every on-disk
raster/vector with role unknown/unused is shown instead and views.json `shown` says so (say it too); with no AOI in
the inventory the single polygon layer is used (`aoi_note`). Index: `discover/views.json`. `views.py list --inputs
migration --json` prints every URL, cell and feature to identify in one call; `check.py --inputs migration` is the
headless check. §2–3 apply unchanged (for a feature the popup lists its attributes). Findings go through `scan.py
annotate migration map-finding` (discover SKILL §2), never `plot.py`.

## 1 Build and serve
- `views.py run --run <run>` after `compare.py run`: per compared raster `verify/views/<ds>/` gets `legacy.tif`,
  `new.tif` (COG copies; the legacy folder is only read), `diff.tif` (new − legacy) and `validity.tif`
  (1 legacy only, 2 new only, 3 both), and the projects `overview`, `worst` (only when max |d| > 0), `edges`
  (validity over legacy) plus one `cell-<id>` project centred on the cell to identify (worst, else edge, else
  interior); vectors get GeoJSON (EPSG:4326) and `overview`, `worst` (only when a feature differs). Views that
  cannot show anything are not built: a dataset identical to legacy (or byte-equal to one reviewed before with
  nothing found) gets none (`skipped: identical (<sha256>)` in views.json; verify SKILL §3), a PASS_EXACT dataset
  only `overview` and one cell, max |d| = 0 no `worst` (`omitted_views` says why). Index: `verify/views/views.json`
  (after seal it writes `amend/views/` instead).
- `start.py migration` prints one JSON line `{url_base, port, data_root, pid}`. Exit 13 with "map server could not
  bind 127.0.0.1 — …": rerun that one command outside the sandbox (Codex: with escalated permissions).
  `stop.py migration` when you are done.
- `views.py list --run <run> --json`: every view URL, every cell (URL, legacy/new value, row/col) and every skipped
  dataset in one call; use it instead of one `url` call per view. `views.py url --run <run> --dataset <ds> --view
  overview|worst|edges` (or `--cell <id>`) prints one page URL. `check.py --run <run>` is the headless check (every project and COG served, Range 206,
  sha256 equal, projects valid; exit 0/2); `--render` also opens overviews and cells in headless Chrome (ready line,
  GPU engine, cells drawn as squares). It stops a server it
  started, unless `--keep-server` (then open the views right away; `stop.py` ends it).
- `mapenv.py --where` prints where the map env lives (no download): for a disk-space question or to remove it.

## 2 Open it where the person is
- **Claude desktop app** (`mcp__Claude_Browser__*`): `preview_start {url}` for the first view, then at once
  `mcp__Claude_Browser__resize_window {width: 1280, height: 960}` on that tab, so the frame stays fixed whatever
  happens to the app's layout; every further view in that tab with `navigate` (a second tab via `tabs_create` only
  when the graph tab must stay open). When you are done with the map: `resize_window {preset: "desktop"}`.
- **Codex desktop**: open the view URL in the in-app browser with browser use (one clickable link in chat); set a
  1280×960 viewport if the browser tool offers it (the page fits its overview to whatever size it gets). The first
  `start.py` needs sandbox approval (map environment sync + 127.0.0.1 bind).
- **Terminal harness**: print the URL once and name `views.json`; do not ask the person to copy anything else.
- Screenshots at scale 1 (never below 0.75: a shrunken map cannot be judged). For detail use `computer {action:
  "zoom", region: [x0, y0, x1, y1]}` (Codex: the browser's region/zoom screenshot) on the map area, not a smaller
  page. Values are read as text (popup and page text via `get_page_text`), never from pixels.

## 3 In the page (every time a project opens)
1. **Wait for the ready line**, never a fixed wait and never a nudge: after `navigate`, read the page text
   (`get_page_text`, or `javascript_tool` `document.documentElement.dataset.axisMap`) until it holds
   `axis-map: ready · engine maplibre-gl-raster · project sha256 <sha> · click centre x,y` (a few seconds; poll at
   most every 2 s, give up after 60 s and say so). Ready means: project loaded and hashed, GPU raster engine set by
   the page itself (no Add Raster Layer dialog), no data request in flight, every tile drawn and the map idle after
   a forced repaint. The sha must be the view's `project_sha256` from `views.json`; otherwise you are on the wrong
   view. Then take **one** screenshot. "loading (tab hidden …)" means the tab is not visible: show it. `start.py`
   printing `engine_preset: false` (another GeoLibre version) is the only case for switching the engine by hand.
2. Overviews (and `inputs-overview`) fit the whole data extent into whatever canvas the pane has; `worst`,
   `edges` and cell/feature projects open on their place. If you pan or zoom, the line says `drawing` until ready.
3. **Compare by toggling layers** in the Layers panel (eye icon). Do not use swipe: its two sides use different
   engines.
4. **Identify a cell**: open its `cell-<id>` project (URL from `views.py list`), wait for ready, click the `click
   centre x,y` the ready line gives (CSS px = screenshot px at scale 1), then `get_page_text`. The popup "Identified
   results" lists each visible layer with Band 1, lon/lat, row and column. Check it against the cell in `views.py
   list` / `views.json`: `legacy_value`/`new_value` (the popup rounds to about 3 significant figures), `row`/`col`
   on legacy.tif, `new_row`/`new_col` on new.tif, `grid_row`/`grid_col` on diff.tif and validity.tif, `validity`. A
   mismatch means you are on the wrong cell or the wrong layer: say so.
5. **Colours and the caption**: `LOOK.md` §2 (blue legacy only, orange new only, green both; the top-left caption
   names dataset · view · visible layers with their scale and follows your toggles). Basemap is off. A
   "Diagnostics: 1" badge with the luma.gl warning "Binding sampler not set" comes from the GPU engine and is
   harmless.
6. A screenshot is for you, not evidence; the receipt cites projects, shas and popup values. What to check and
   what to say in chat: `LOOK.md` §3 and §7.

## 4 What each view is for
- `overview`: legacy and new over the whole extent (toggle new off to see legacy).
- `worst`: camera on the largest |new − legacy|; diff on top; bookmarks for plot.py's worst tiles or compare samples.
- `edges`: validity over legacy where cells valid on one side only concentrate (a one-cell shift shows as a blue
  stripe on one side and an orange one on the other); its bookmark "whole extent (validity everywhere)" shows
  where NoData differs over the whole extent (what the `nodata` view showed in older runs).

## 5 Rules
- Cite a view by dataset, view and `project_sha256`; the project bytes do not depend on the port (layer URLs are
  origin-relative `/data/…`), so the sha is of exactly what the server sent.
- Never claim what the map shows unless you saw it in this session (popup text, page text or screenshot). If no
  pane or browser is available, say the views were checked headlessly only (`check.py`).
- Never edit views, projects or COGs by hand; rerun `views.py run`. Never write into the legacy folder.
- The report (report skill) lists every view with its sha256 and whether the bytes still match, and the receipts; it
  never holds the server's port and never embeds the app.
- The app loads its default basemap style from tiles.openfreemap.org once at start-up; the migration's data is only
  ever served from 127.0.0.1.
