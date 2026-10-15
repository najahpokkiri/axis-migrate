---
name: map
description: Look at legacy vs new results on a live map (GeoLibre, local only) and show them to the person in the harness's own browser pane. Build the views of a compared run (legacy, new, diff and validity COGs, GeoJSON, one GeoLibre project per view), start the local map server, open the views, switch the raster engine, toggle layers and identify cells. Display only; use after compare.py (and plot.py) on a run.
---

# Map: you look, the person watches; numbers still decide

`$P` = plugin root; `<run>` = `migration/runs/<run_id>`. Run every script as `"$P/scripts/axpy" "$P/skills/map/<x>.py" …`
from the project folder (they switch to the map env themselves; first use installs GeoLibre 3.4.0, ~60 MB download,
outside the plugin folder). Nothing here feeds a verdict: findings go through `plot.py review-add` as a view
receipt (verify SKILL §3.6: view projects + sha256, engine, cells read) or, without a pane, as PNG receipts.

## 1 Build and serve
- `views.py run --run <run>` after `compare.py run`: per compared raster `verify/views/<ds>/` gets `legacy.tif`,
  `new.tif` (COG copies; the legacy folder is only read), `diff.tif` (new − legacy) and `validity.tif`
  (1 legacy only, 2 new only, 3 both), and the projects `overview`, `worst`, `edges`, `nodata` plus `cell-<id>`
  projects centred on each cell to identify; vectors get GeoJSON (EPSG:4326) and `overview`, `worst`. Index:
  `verify/views/views.json` (after seal it writes `amend/views/` instead).
- `start.py migration` prints one JSON line `{url_base, port, data_root, pid}`. Exit 13 with "map server could not
  bind 127.0.0.1 — …": rerun that one command outside the sandbox (Codex: with escalated permissions).
  `stop.py migration` when you are done.
- `views.py url --run <run> --dataset <ds> --view overview|worst|edges|nodata` (or `--cell worst|interior|edge`)
  prints the page URL. `check.py --run <run>` is the headless check (every project and COG served, Range 206,
  sha256 equal, projects valid; exit 0/2). It says nothing about what a browser shows.

## 2 Open it where the person is
- **Claude desktop app** (`mcp__Claude_Browser__*`): `mcp__Claude_Browser__preview_start {url}` for the first view;
  every further view in a second tab (`tabs_create`, then `navigate` with its `tabId`) so the graph tab stays open.
- **Codex desktop**: open the view URL in the in-app browser with browser use (one clickable link in chat). The
  first `start.py` needs sandbox approval (map environment sync + 127.0.0.1 bind).
- **Terminal harness**: print the URL once and name `views.json`; do not ask the person to copy anything else.

## 3 In the page (every time a project opens)
1. **Switch the raster engine to the GPU one**: Add Data → Raster Layer → render engine `maplibre-gl-raster` (a
   `<select>` whose options are `maplibre-gl-raster`, `cog-tiler-wasm`, `titiler`). The default `cog-tiler-wasm`
   smooths cells: blurred edges, and on the validity layer a false orange fringe between blue and green.
2. **Nudge**: if the map stays blank, drag it a few pixels and back.
3. **Compare by toggling layers** in the Layers panel (eye icon). Do not use swipe: its two sides use different
   engines.
4. **Identify a cell**: open its `cell-<id>` project (`views.py url … --cell <id>`), switch the engine, click the
   exact centre of the map. The popup "Identified results" lists each visible layer with Band 1, lon/lat, row and
   column. Check it against `cells_to_identify` in `views.json`: `legacy_value`/`new_value` (the popup rounds to about
   3 significant figures), `row`/`col` on legacy.tif, `new_row`/`new_col` on new.tif, `grid_row`/`grid_col` on
   diff.tif and validity.tif, `validity`. A mismatch means you are on the wrong cell or the wrong layer: say so.
5. **Colours**: legacy and new share one viridis scale (p2/p98 of both sides); diff is PuOr reversed on ±max|d|
   (orange = new larger, purple = legacy larger, transparent = equal or nodata); validity is blue = legacy only,
   orange = new only, green = both, transparent = neither. Basemap is off.
6. Read the popup and page as text where the pane allows; a screenshot is for you, not evidence.

## 4 What each view is for
- `overview`: legacy and new over the whole extent (toggle new off to see legacy).
- `worst`: camera on the largest |new − legacy|; diff on top; bookmarks for plot.py's worst tiles or compare samples.
- `edges`: where cells valid on one side only concentrate (a one-cell shift shows as a blue stripe on one side and
  an orange one on the other); `nodata`: the validity layer alone over the whole extent.

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
