# Look: what to check on a map, and what to say

Scripts decide verdicts. You look for what a comparator cannot name: data in the wrong place, a part missing, an
edge that moved, a value range that does not fit its unit, a class gone. You may only raise; nothing you see lowers
or lifts a verdict. Discover, verify and migrate all follow this file.

## 1 Where you look (say which in one sentence when you start)
- **Claude desktop, Codex desktop with a browser**: the GeoLibre views (map SKILL §1–3). Do not also read the PNGs
  (they are rendered for the report and evals) unless a view cannot be built; say so if you do.
- **Terminal, headless, evals, no browser tool**: the PNGs (Read; Codex `view_image`).
- **Place against the real world** (any desktop): the MapLibre page (map SKILL §6). Set a basemap (`imagery` or
  `osm`), wait until `basemapStatus` is `loaded`, lower the data opacity, and check that rivers, roads, towns or
  coastline line up with the data. Read the values with `identify(lon, lat)`, not from the colours.
One path per dataset, never both (the MapLibre page is an addition to the views, not a second path).

## 2 Colours (one vocabulary)
- Legacy blue, new orange. Validity: **blue = legacy only, orange = new only, green = both**, transparent = neither
  (NoData on both sides).
- Values: legacy and new share one viridis scale (p2/p98 of both sides). Difference new − legacy: orange = new
  larger, purple = legacy larger, white/transparent = equal.
- Every view names itself in its top-left caption: dataset · view · each visible layer with its scale · display
  only. Quote the caption; do not describe colours from memory.
- PNGs differ in two places until the rubric is recalibrated: `mask_diff.png` draws "both" in grey (not green), and
  difference panels draw legacy-only cells black and new-only dark grey. Light grey = NoData.

## 3 The five checks (every dataset; views and PNGs alike)
| check | the question | view (desktop) | PNG | number from |
|---|---|---|---|---|
| place | Is the data where the AOI or outline is, in the right CRS and units, not shifted? | `overview`, `grid-<family>` | `overview.png`, `worst_tiles.png`, `grid_corners.png`, `inputs_overview.png` | compare grid gate, `extent_relations` |
| coverage | Is anything missing, empty or outside the AOI? | `overview` (toggle new), `edges` bookmark "whole extent" | `mask_diff.png`, `inputs_overview.png` | `map.py metrics`, compare `raster.mask`, feature counts |
| edges | Cells valid on one side only along a border; paired stripes (a one-cell shift)? | `edges` (validity over the data) | `mask_diff.png`, `worst_tiles.png` | views.json `validity_counts`, compare `raster.mask` |
| values | Same picture under the shared scale; does the range fit the unit? | `overview` + one cell read as text | `overview.png`, `hist.png` | compare `raster.values`, the cell read |
| classes | Is a class or feature type absent on one side? (`n/a` for continuous rasters) | `overview`, layers toggled | `overlay.png`, `per_feature.png` | compare key set, class counts |

A check you could not do is `cannot_tell` with why. A finding with no number is `severity: question`, never a flag.

## 4 How (desktop)
- `views.py list … --json` once; `resize_window` 1280×960 once. Per view: `navigate`, wait for `axis-map: ready`
  with the view's sha, **one** screenshot at scale 1, toggle the one layer the check needs; a second screenshot
  only if the picture changed what you think. `zoom` on a region for detail. Values from the popup via
  `get_page_text`, never from pixels. No fixed waits, no nudges, no dialogs.
- Open only the views `views.json` built. A dataset under `skipped` gets the template receipt and no views.
- A blank canvas after `ready` is a defect to report (`check.py --render`), not "no difference".

## 5 Per stage
- **Discover**: `inputs-overview` + one `grid-<family>` per family (or `inputs_overview.png`, `grid_corners.png`,
  `legacy_rasters.png` / `legacy_vectors.png`); read **one cell per raster and one feature per vector** (that is
  what the finding rule checks). Numbers from `map.py metrics` (`discover/metrics.json`). Record with
  `scan.py annotate migration map-finding` (discover SKILL §2). Budget: ≤ 10 min, ≤ 15 browser calls.
- **Verify, each isolated run**: the views `views.json` lists for the dataset (raster `overview` + `edges` + one
  cell, `worst` only when max |d| > 0; vector `overview`, `worst` only when a feature differs), or every PNG in
  `plots.json`. Receipt:
  `plot.py review-template` → fill → `plot.py review-add` (verify SKILL §3).
- **Chained verify**: only datasets not listed under `skipped` (byte-equal to a reviewed output carries over).
- **Report**: quote each dataset's `looked` line (the report prints it); never "looked at on the map" without a
  counted view receipt.

## 6 Done, per dataset
Every listed view or PNG opened once; the five checks answered; at least one cell or feature read as text
(desktop); one receipt accepted (`review-add` in verify, `scan.py annotate` in discover). Then stop. Do not reopen
views of an output byte-equal to one you reviewed.

## 7 What you say in chat (one block per dataset, no more)
```
Looked at ds.x (3 views): place ✓ coverage ✓ edges ✓ values ✓ classes n/a — cell r488,c418 legacy 0.506452 =
new 0.506452; 9179 NoData cells (51 km²) inside the AOI, same on both sides. Nothing to flag.
```
With a flag: `… edges ✗ — 812 legacy-only cells along the west edge (validity 1), flagged Q6 boundary_stripes.`
Always one thing worth knowing with its number. Never "all green", "looks right" or "uniform" alone; never a status
word (`ladder.py` prints those).

## 8 The receipt says the same (verify; `review-template` prefills where and the numbers it has)
A `Q-all` ("looked, nothing found") receipt carries `looked`, one entry per check:
```json
"looked": [{"check": "edges", "answer": "ok", "where": "edges",
            "saw": "no blue or orange cell along the outline; green fills the AOI",
            "number": {"name": "cells valid on one side only", "value": 0, "unit": "cells",
                       "computed_by": "compare.py raster.mask.value"}},
           {"check": "classes", "answer": "n/a", "why": "continuous values: no classes"}, …]
```
`review-add` refuses a `Q-all` receipt unless all five checks are there; `ok` needs `where` (views or PNGs this
receipt cites, joined with `+`) and `saw` (≤ 120 characters, more than "all green"); `cannot_tell` and `n/a`
(classes only) need `why`; at least one entry carries a `number`. A defect is never `Q-all`: record it as a flag
(verify SKILL §3.4/§3.6).
