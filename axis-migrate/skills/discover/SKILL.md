---
name: discover
description: First step of /migrate. Scan a read-only legacy GIS folder into migration/inventory.json, draw and inspect the input maps, record map findings with numbers, build the legacy workflow graph (graph.json) and open the graph editor. Use before any question, plan or pipeline code exists, and again (scan only) whenever the legacy folder changes.
---

# Discover

Evidence first, interpretation second. Scripts write the facts; you read them, look at the maps and record what you
see through `scan.py annotate`. `$AXPY` = `${CLAUDE_PLUGIN_ROOT}/scripts/axpy`, `$D` = `${CLAUDE_PLUGIN_ROOT}/skills/discover`.

## Hard rules
- The legacy folder is read-only. Never write, move or touch anything in it; never run `gdalinfo -stats`, QGIS or
  any tool that writes `.aux.xml` sidecars there. `GDAL_PAM_ENABLED=NO` is set by `axpy`; keep it for any GDAL call.
- Never run the legacy code. Never edit `migration/graph.json` or `graph-edits.jsonl` by hand: only `graph.py`.
- Never pair an orphan legacy file with a new output by its name; only the user pairs orphans (`set_reference`).
- A manual step is a policy question for the user, never a question about what the files show.

## 1 Scan
```
$AXPY $D/scan.py <legacy> --out migration          # exit 3 = the legacy folder changed during the scan
```
Read `migration/inventory.json`: `scan.read_only_verified` (must be true), `scan.mtime_reliable`, `files[]`,
`datasets[]` (role input/intermediate/final/unused/unknown with `role_evidence`, `reference_candidate`),
`code[]` (status live/superseded/twin/helper and `twin_of`), `ops[]` (one per legacy tool call, file:line),
`settings[]` (grid-shaping parameters, incl. `default_unset`), `params[]` (literals with `used_at` and
`doc_mentions`), `derived_copies[]` and `manual_step_candidates[]`, `grids[]` (families and pixel offsets),
`extent_relations[]`, `join_coverage[]`, `unresolved[]` and above all `open_items[]`. Profiles are in
`migration/discover/profiles/<dataset>.json`; statistics there are computed, sidecar statistics are only recorded.

## 2 Look at the inputs
```
$AXPY $D/map.py inputs migration
```
Then **Read every PNG** it lists (`discover/inputs_overview.png`, `grid_corners.png`, `legacy_rasters.png`) and go
through this checklist (Codex has no Read tool: open each PNG with `view_image`):
- every input covers the area of interest; quantify slivers outside (`extent_relations`);
- mixed CRS or units (degrees vs metres; nominal vs real cell size; documented resolution vs the grid);
- grid families and offsets: in `grid_corners.png` aligned rasters share one set of cell edges; a second, shifted
  set of edges is a misalignment (half a cell is enough);
- layers reaching past the area of interest, holes, NoData patches inside it, partial thematic coverage;
- value ranges against units or legends; intermediates and finals that look empty or saturated.

Record each observation with a number computed by a script (inventory, profile, or your own analysis code under
`migration/analysis/`):
```
$AXPY $D/scan.py annotate migration map-finding --json finding.json
# {"id": "MF-01", "png": "discover/inputs_overview.png", "observation": "...", "severity": "info|watch|question",
#  "metric": {"name": "...", "value": 0.0, "computed_by": "..."}, "about": ["ds.x"]}
```
A finding without a computed metric can only have `severity: question`. Visual findings never resolve anything;
they only raise. Free-form agent notes: `scan.py annotate migration note --json note.json`. Findings and notes
survive a rescan.

## 3 Lineage and the graph
```
$AXPY $D/lineage.py migration            # draft in migration/.graph/draft.json, then graph.py init (revision 1)
$AXPY $D/graph.py show migration          # steps, datasets, open questions
```
Lineage checks the static lineage against the real files (writes exist, layer names, fields, template grids, CRS,
mtime order as a warning only). Anything unmatched becomes a question linked to a node and a code line. Scope facts
the files already answer (superseded code, display-only layers, unused classes) are in `graph.json.scope_facts`
with evidence; the interview folds them into its confirm-all question instead of asking each one. Exit 3 means a
graph already exists: change it only with `graph.py apply` (see `GRAPH_OPS.md`).

## 4 The editor and the "Send to agent" channel
```
$AXPY $D/graph_server.py serve migration          # in the background; show the URL in the side pane (§Showing)
$AXPY $D/graph_server.py wait migration           # run_in_background; you are re-invoked when it exits
```
`wait` exits 0 with the user's edits and your obligation per edit (exit 2 on timeout: start it again). Act on every
obligation (`GRAPH_OPS.md` §4), then `$AXPY $D/graph.py ack migration <ack_seq>`. Deleting a step with legacy
outputs never removes it: ask the user with the listed datasets, then `resolve_confirmation`.

## Hand-off
Investigation and interview read `inventory.json` (`open_items`, `params`, `settings`, `derived_copies`,
`map.findings`) and `graph.json` (`questions`, `scope_facts`). Do not ask the user anything during discover except
what an open question in the graph already says; asking is the interview's job.
