# 02 — Lineage and workflow graph: graph.json, editor, "Send to agent"

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Owner: design subagent 02. Date: 13 Oct 2026. Status: proposal for the orchestrator. Path prefixes: `AS`=`~/src/axis-spatial`; `AG`=`AS/_archive/services/legacy-service-archive/agent/axis_spatial/agent`; `FE`=`AS/_archive/apps/legacy-product-app/src`; `BE`=`AS/_archive/services/backend/app`; `RC`=`~/src/server-copies/axis-spatial-deploy-mcp-v2/packages/runtime-core/src` (byte-identical to `AS/packages/runtime-core/src`, checked with `diff -q`); `FX`=`~/axis-migrate-dev/fixtures/lux-exposure-legacy`. Skill-candidate ids as in `~/src/context/skill-candidates/`.

## 1 Scope and interfaces

I own `migration/graph.json` (schema, invariants, write path), the legacy lineage builder (`skills/discover/lineage.py`), the graph library and CLI (`skills/discover/graph.py`), the editor (`graph.html` + `graph.bundle.js`), `graph_server.py`, the edit log `migration/graph-edits.jsonl`, the runtime-lineage contract for the new pipeline (`runs/<id>/lineage.jsonl`), and the pairing rule verify uses. Not mine: compare/plot internals (verify), question wording and ordering (interview), params.yaml values (params/discover), hook wiring (hooks), report layout (report).

```
inventory.json ──► lineage.py ──► graph.py apply (actor=agent) ──► graph.json ◄── graph_server.py (actor=user) ◄── graph.html
                                        ▲                │                       │ append                    │ Send to agent
       compare/*.json ─ graph.py ingest-verify (actor=verify)   graph-edits.jsonl ◄┘── graph_server.py wait / UserPromptSubmit hook
       runs/<id>/lineage.jsonl ─ graph.py derive (actor=derive: produced_by, consumed_by, ladder, blame)
```

## 2 Decisions

**D2-1 Bipartite graph of steps and persisted datasets; legacy tool calls kept as `ops` inside steps.** A step is one script, notebook, QGIS model or manual action by default; the agent splits a script only when its op graph has independent chains. Every persisted artefact (file, file+layer, gdb item, sheet) is a dataset node. In-memory results (`'memory:'`, `TEMPORARY_OUTPUT`, unsaved arcpy Raster objects) live only in `step.ops[]` as `mem:` handles. Evidence: SmartDAG collapsed a real exposure project into 6 broad nodes and lost the raster algebra (08§2.4); per-step verification needs the boundary datasets, which are exactly the persisted ones (09§6, 09§7.4); defects in the real migration were found in intermediates (MPV-03, HL-29).

**D2-2 Edges are stored explicitly with per-edge evidence; `produced_by`/`consumed_by` are derived, never edited.** SmartDAG flows were a `data_name` string never checked against files (09§1.2, `BE/workflow/smart_dag.py:111-122`); 09§7.4 keeps both `inputs/outputs` and `produced_by/consumed_by`, which would be two copies of one fact. One copy (edges) plus a derivation.

**D2-3 Evidence vocabulary = the implemented `EvidenceState` plus `documented`.** States: `directly_observed`, `structurally_inferred`, `ai_inferred`, `documented`, `user_asserted`, `runtime_probed`, `verified`, `rejected`, `superseded` (`RC/migration-delivery.ts:8-16`). `documented` is added for analyst notes and code comments: they are historical human claims that drift from code (HL-36; 03 "treat names, docs and tests as claims"; EP-27), so they rank below a fresh answer and above model inference. An item's `state` is the strongest non-rejected evidence; disagreeing evidence sets `conflict: true` and must produce a question (never "strongest wins").

**D2-4 Four separate status fields, never blended.** `review` (pending | confirmed | assumed | rejected; PLAN "Workflow graph editor"), `scope` (migrate | keep_legacy | bridge | reference_only | out_of_scope; from `BE/ai/migration/scope.py:16-21`), `ladder` (not_started → generated → sandbox-validated → reference-verified → target-ready; 08§4.3, PLAN) and `verify.verdict` (the 10§C1 states). 08§8.2 found seven unmapped status vocabularies; mixing them is how "confirmed" got read as "verified".

**D2-5 Pairing = join on dataset id. No positional, stem, prefix or similarity fallback, not even as a suggestion.** A pair exists iff an in-scope dataset has a `legacy` reference and a `new` output in the run. All four old pairers were wrong (09§2.2: P2 paired legacy *inputs* with outputs and zipped leftovers by position, `BE/routes/verify.py:306-331,514-530`; the UI relabelled positional pairs as name matches, `FE/hooks/useVerification.ts:91-93`; FH-32). Orphan legacy files are paired only by an explicit user op `set_reference` (`match_method: human`), checked for kind/CRS/grid plausibility first. An `equivalent_to` edge (an alternative implementation, e.g. ArcPy 03b vs QGIS 03) may supply a reference only once a human confirms it, and the result is labelled "reference from equivalent step" (PLAN "Demo project").

**D2-6 Every legacy intermediate in scope is either compared or explicitly waived.** A dataset with `legacy.exists` and in-scope producer but no new output gives `OUTPUT_MISSING`/`UNTESTED`, which blocks `reference-verified`. A waiver needs `verify.waiver {reason, by: user, rev}`. Evidence: no intermediate was ever verified (09§0.3); P4's "declared output missing = FAIL" is the one sound pairing rule (`BE/pipelines/validation/migrated.py:153-161`); HL-28 (declared but never produced).

**D2-7 Lineage is computed statically, then checked against files. Unmatched items become questions, never silent drops.** `adaptLegacyAnalysis` drops edges whose endpoints are missing (`RC/migration-delivery.ts:394`). The old tracer silently emitted `[unknown]` (`AG/data_flow_tracer.py:331-348`). Here, a write with no file, a file with no writer, an unresolved path template and a field read that the producer's schema lacks each give a question linked to the node and the code line.

**D2-8 Rewrite the tracer; lift only its arcpy I/O table.** I ran `AG/data_flow_tracer.py` on `FX/02_SCRIPTS`: **0 flows** for all four PyQGIS scripts (it has no `processing.run` support and `_get_value` ignores `BASE + "/…"` string concatenation, `AG/data_flow_tracer.py:266-284`), and on 03b **2 identical unresolved flows** (`cantons → CopyFeatures → out_fc`, line 41 twice: the double-visit bug at 152-164, 09§1.1). Keep the parameter table at 55-75, with both arcpy naming styles and star-imported `arcpy.sa` added (AK-31).

**D2-9 Single writer library, optimistic concurrency, append-only log, snapshots.** All writes go through `graph.apply(ops, actor, base_revision)`: `fcntl.flock` on `migration/.graph/lock`, check the revision, check the ACL, apply, validate, `revision+1`, atomic `os.replace`, append to `graph-edits.jsonl`, snapshot to `.graph/rev/<n>.json`. A PreToolUse hook denies Write/Edit on `graph.json` and `graph-edits.jsonl`. Evidence: the old Refine page had two writers, two formats and one column, and edits returned 400 that the client swallowed (06 Step 1; `BE/routes/workflow.py:1411`, `FE/pages/refine.tsx:713`). State lived in five stores (06 "take away" 1). The July graph corrected by appending and superseding, never overwriting (`RC/migration-delivery.ts:101-111,329-340`; 08§9.4).

**D2-10 User-set fields are locked against the agent.** Once a user sets `title`, `review`, `scope`, `notes` or an answer, an agent op touching that field fails with `user_owned_field`. To change it, the agent opens a question. The old page auto-confirmed every node on "proceed" (`FE/pages/refine.tsx:1763`), which turned rubber-stamps into confirmations.

**D2-11 Verdict and ladder fields are projections written only by `ingest-verify`/`derive`.** `compare/*.json` is the authority, and the graph stores its path and sha256. "Verdicts come only from compare.py" and "a model never upgrades a failed check" (PLAN; 08§9.3; `DOC/2026-06-27-…:159-161` via 09§4.2).

**D2-12 Runtime lineage for the new pipeline comes from the runner, not from the generated code.** The runner gives each step a `ctx` that resolves dataset ids to paths and a private output directory `runs/<id>/outputs/<step>/`. It diffs that directory before and after, and streams sha256 once after the step closes. The archive relied on clients POSTing lineage, so lineage was empty (08§2.1; `AG/server.py:2770-2782` was its only writer). Re-reading rasters for bookkeeping caused OOM (MP-28).

**D2-13 Editor = static page plus prebuilt bundle (React 18, `@xyflow/react` 12, `@dagrejs/dagre`), vendored for offline.** The old app already depended on both `reactflow@11` and `@xyflow/react@12` (`FE/../package.json:37,66`) and used dagre with `rankdir TB, nodesep 70, ranksep 100` (`FE/lib/dag-utils.ts:14-46`). Here dagre runs with `rankdir: LR` for the bipartite layout. ELK was rejected: about 1.5 MB, and no port routing is needed. The bundle is vendored because the demo hall's network is not a dependency we control. Node positions go to `.graph/layout.json`: never in graph.json, never sent to the agent.

**D2-14 Two return channels, one diff format.** Blocking `graph_server.py wait` (Codex and Claude Code), and a passive UserPromptSubmit hook that only *announces* edits since the agent's ack cursor. The Stop hook refuses "done" while user edits are unacked (PLAN "Workflow graph editor").

## 3 Schemas

### 3.1 graph.json top level (`schema: "axis-migrate.graph.v1"`)

```json
{"schema": "axis-migrate.graph.v1",
 "revision": 17,
 "project": {"name": "lux-exposure-legacy", "legacy_root": "../lux-exposure-legacy",
   "legacy_tree_sha256": "sha256 of sorted (relpath, sha256) over the legacy root",
   "created_at": "2026-10-15T09:12:00Z", "plugin_version": "0.1.0"},
 "steps": [], "datasets": [], "edges": [], "questions": [],
 "runs": [{"run_id": "r20261016-1", "where": "local", "lineage": "runs/r20261016-1/lineage.jsonl",
   "compare_dir": "runs/r20261016-1/compare", "mode": "end_to_end", "status": "ok"}],
 "ladder": {"project": "sandbox-validated", "derived_rev": 17, "blocking": ["ds.res_mask: FAIL RASTER_TRANSFORM_MISMATCH"]},
 "acks": {"agent_seq": 41},
 "updated": {"rev": 17, "by": "user", "at": "2026-10-16T10:02:11Z"}}
```

### 3.2 Step

```json
{"id": "s02_landuse_mask", "title": "Residential mask from OSM land use", "title_set_by": "agent",
 "kind": "script",                    // script | notebook | qgis_model | manual | bridge | new
 "tool": "pyqgis",                    // pyqgis | arcpy | python | excel | qgis_gui | arcgis_gui
 "legacy": {"source": "02_SCRIPTS/02_landuse_mask.py", "lines": [[26, 76]], "executed": "yes",
   "executed_evidence": ["inventory:05_TEMP/02_res_mask.tif", "NOTES.txt:7-11"]},
 "ops": [{"id": "s02.op7", "call": "gdal:rasterize", "lines": [61, 76],
   "reads": ["mem:s02.op5"], "reads_grid": ["ds.pop_lux"], "writes": ["ds.res_mask"],
   "literal_params": {"BURN": 1, "UNITS": 0, "INIT": 0, "NODATA": null, "EXTRA": "-at"}}],
 "params": ["p.s02.min_area_m2", "p.s02.all_touched", "p.s02.mask_nodata"],
 "scope": "migrate", "scope_reason": null, "scope_set_by": "agent",
 "review": "pending", "evidence": [], "notes": [],
 "new": {"module": "pipeline/s02_landuse_mask.py", "function": "run", "code_sha256": null, "test": "tests/test_s02.py"},
 "ladder": {"state": "not_started", "run_id": null, "code_sha256": null, "params_sha256": null, "blocking": []},
 "verify": {"verdict": null, "blame": null},          // derived: worst of its datasets; blame origin|inherited
 "provenance": {"created": {"by": "agent", "rev": 3}, "edits": [{"by": "user", "rev": 12, "fields": ["title"]}]}}
```

### 3.3 Dataset

```json
{"id": "ds.res_mask", "title": "Residential mask, all touched", "kind": "raster",   // raster | vector | table | document
 "role": "intermediate",                      // input | intermediate | final  (derived from edges + location, overridable by user)
 "persistence": "file",                       // file | gdb | sheet | overwritten   (memory never appears here)
 "legacy": {"path": "05_TEMP/02_res_mask.tif", "layer": null, "sidecars": ["05_TEMP/02_res_mask.tif.aux.xml"],
   "exists": true, "size": 32280, "mtime": "2026-10-13T18:15:22.786Z",
   "sha256": "cb16d48a…", "content_sha256": "…", "content_hash_method": "raster-pixels+transform+crs.v1",
   "profile": {"crs": "EPSG:4326", "shape": [880, 940], "dtype": "uint8", "nodata": 0,
     "transform": [0.0008333333297872345, 0, 5.744583325, 0, -0.0008333333295454553, 50.18125]}},
 "reference": {"source": "legacy", "match_method": "lineage", "via_dataset": null, "confirmed_by": null},
 "new": {"path": "outputs/s02_landuse_mask/res_mask.tif", "layer": null},   // relative to runs/<id>/
 "produced_by": {"step": "s02_landuse_mask", "op": "s02.op7"}, "consumed_by": ["s03_zonal_population"],   // derived
 "compare": {"tolerance_ref": "tolerances.yaml#ds.res_mask", "categorical": true, "key": null, "rename": {}},
 "verify": {"run_id": null, "verdict": null, "reason_codes": [], "report": null, "report_sha256": null,
   "plots": [], "visual_flag": null, "blame": null, "waiver": null},
 "review": "pending", "evidence": [], "notes": [], "provenance": {"created": {"by": "agent", "rev": 3}, "edits": []}}
```

Content hashes: GeoPackage bytes change on every write (`gpkg_contents.last_change`), and so do XLSX bytes (`docProps/core.xml`). For these, `content_sha256` is computed over decoded rows: vector = schema + rows sorted by WKB+attributes; table = cell values per sheet. Raster = decoded pixels + transform + CRS + nodata. `sha256` stays as the custody hash (10§C3).

### 3.4 Edge, evidence, question, edit-log record

```json
{"id": "e.s02_landuse_mask>ds.res_mask", "from": "s02_landuse_mask", "to": "ds.res_mask",
 "kind": "writes",        // reads | writes | reads_grid | reads_write (in place) | equivalent_to | displays (qgz)
 "state": "directly_observed", "conflict": false, "review": "pending",
 "evidence": [{"state": "structurally_inferred", "ref": "02_SCRIPTS/02_landuse_mask.py:61-76", "summary": "gdal:rasterize OUTPUT", "by": "scan", "rev": 3},
              {"state": "directly_observed", "ref": "inventory:05_TEMP/02_res_mask.tif", "summary": "exists; transform == ds.lux_ppp_2020", "by": "scan", "rev": 3}],
 "provenance": {"created": {"by": "agent", "rev": 3}, "edits": []}}
```
Evidence `ref` forms: `path:L[-L]`, `path#cell=N`, `inventory:path`, `qgz:01_QGIS/x.qgz#layer=name`, `runs/<id>/lineage.jsonl#L12`, `answer:q.id`, `compare:runs/<id>/compare/ds.x.json`.

```json
{"id": "q.s00_manual_canton_names.automate", "nodes": ["s00_manual_canton_names", "ds.cantons_lux_edit"],
 "text": "NOTES says canton names were fixed by hand in ADM1 and saved as cantons_lux_edit.gpkg. Should the pipeline do this rename?",
 "why": "Manual step; redone by hand whenever boundaries change (NOTES.txt:15-19)",
 "evidence": ["NOTES.txt:15-19", "02_SCRIPTS/03_zonal_population.py:8"], "agent_guess": "automate",
 "options": [{"label": "Automate it (name map in params.yaml, checked against the hand-edited file)", "value": "automate", "recommended": true},
             {"label": "Keep the hand-edited file as an input", "value": "keep_legacy"}],
 "allow_other": true, "allow_dont_know": true, "impact": "high", "blocks": ["s00_manual_canton_names"],
 "state": "open",          // open | answered | assumed | dismissed
 "answer": null,           // {value, text, by: user|agent, channel: graph|interview, verbatim, rev, at}
 "writes_params": ["p.s00.canton_name_map"], "decision_ref": null}
```
Question fields follow `SmartQuestionV2` (`BE/workflow/smart_interview_v2.py:54-69`: `related_task_id`→`nodes`, `trigger_code`→`evidence`, recommended option = `assumption`→`agent_guess`). Every question is linked to a node, which fixes the answers that never reached generation (06 Step 1).

Edit log (`graph-edits.jsonl`, one line per op, append-only, never rewritten):
```json
{"seq": 42, "rev": 17, "base_rev": 16, "actor": "user", "channel": "graph", "at": "2026-10-16T10:02:11Z",
 "op": {"type": "add_step", "id": "s05_industrial_weights", "title": "Industrial weights", "reads": ["ds.osm_landuse"], "writes": []},
 "result": "applied"}
{"seq": 43, "rev": 17, "actor": "user", "op": {"type": "submit", "message": "added the industrial branch"}, "result": "applied"}
```
This is `WorkflowCorrection` (`RC/migration-delivery.ts:101-111`) with a `seq`.

### 3.5 Ownership and ACL (enforced in `graph.apply`)

| Field / op | agent | user (page) | verify | derive |
|---|---|---|---|---|
| create step/dataset/edge, `legacy.*`, `ops`, `evidence`, `params` refs | yes | add_step/add_edge only (as `user_asserted`) | – | – |
| `title`, `notes` | until the user sets it | yes (then locked) | – | – |
| `review` | `assumed` only, via question state | confirm / reject | – | – |
| `scope` | only by citing an answered question id | yes; may need confirmation (§4.5) | – | – |
| question create / answer | create; answer only with `channel: interview` + verbatim | answer | – | – |
| `new.*` | yes (migrate) | – | – | `code_sha256` |
| `verify.*` (dataset) | **no** | `waiver` only | yes | `blame` |
| `ladder`, `produced_by`, `consumed_by`, `role` (default) | no | `role` override | no | yes |

The CLI rejects `--actor user`. Only the HTTP handler creates user ops (residual risk §8 Q6).

## 4 Algorithms

### 4.1 Static legacy lineage (`lineage.py`, input = `inventory.json` + legacy files)
1. **Environment per script (flow-sensitive, straight-line):** evaluate assignments in order. Handle `Constant`, `Name`, `BinOp(Add)` string concatenation, `%` and `.format` with known operands, f-strings with known parts, `os.path.join`, `Path /`, `.replace`. An `if`/`else` gives a value set. Loop variables give templates `{?var}`. Unknown values stay templates and give a question; they are never `[unknown]`.
2. **I/O recognisers** (table-driven, one `ops[]` entry per call site, visited once): - `processing.run(alg, {…})`: a value that resolves to a path or a layer object is I/O. Keys `OUTPUT*` are writes, everything else reads. `'memory:'`/`TEMPORARY_OUTPUT` gives `mem:`. `x = processing.run(…)['OUTPUT']` binds `x` to that dataset. Alg-specific overrides go in a small table. - `QgsRasterLayer/QgsVectorLayer(path,…)` bind a layer object. `.extent()/.width()/.height()` feeding another op's params gives `reads_grid`. `dataProvider().addAttributes/changeAttributeValues` gives `reads_write` on the bound dataset. - Python I/O: `open(p,'w'|'r')`+`csv`, `openpyxl.load_workbook` (sheet and row range recorded in `literal_params`) and `.save`, pandas/geopandas `read_*`/`to_*`, `rasterio.open(p,'w')`, `gdal.Open/Translate/Warp`, `shutil.copy*`. - ArcPy: both naming styles, aliases, star-imported `arcpy.sa` (AK-31). `Raster(p)` reads and `obj.save(p)` writes. `arcpy.env.workspace` prefixes bare names. `da.SearchCursor` reads, `da.UpdateCursor/InsertCursor`/`AddField`/`CalculateField` are `reads_write`. `env.snapRaster/cellSize/extent` give `reads_grid` plus step params (AK-16, MP-03). - Notebooks: code cells in exec-count order when it is monotonic, else in source order and the step gets `outputs_untrusted` (03 "out-of-order execution"). Refs are `nb.ipynb#cell=N`. - `.qgz`: unzip and parse `<maplayer><datasource>` (port of `FE/lib/gis-parser/index.ts:728-765`). Resolve relative to the project file, split `|layername=` and `|subset=`. Gives `displays` evidence only; a subset that matches a script filter is corroboration.
3. **Path → inventory resolution:** normalise separators and case. Strip the longest prefix so that the remaining path suffix equals an inventory relpath with **≥ 2 components** and exactly one match (`D:\GIS\LUX_exposure\03_INPUT\lux_ppp_2020.tif` → `03_INPUT/lux_ppp_2020.tif`, with evidence "rebased from D:\GIS\LUX_exposure"). A filename-only match is a question. This resolves code references to files; it is not output pairing.
4. **Datasets:** identity = (relpath, layer). Sidecars (`.aux.xml .ovr .qix .tfw .msk`, shapefile parts; `AG/verify.py:33` extended) attach to their parent. A dataset written in step A and modified in place in step B splits into versions `ds.x@A` (`persistence: overwritten`, no legacy file, reported unverifiable) and `ds.x` (the file on disk).
5. **Join across scripts** on dataset id (replacing script-level `AG/workflow_graph.py:354-363`). Order = data dependencies. NOTES run order and numeric prefixes are `documented` evidence; a contradiction gives a question.
6. **Check against files** (each check adds `directly_observed` evidence or a question): (a) every write exists, (b) every `|layername` exists, (c) fields read by a consumer exist in the producer's file schema, (d) every `reads_grid` consumer raster has transform, shape and CRS equal to its template, (e) a declared `TARGET_CRS` equals the file CRS, (f) mtime order: producer outputs ≤ consumer outputs, ignoring sidecars and inputs (PAM `.aux.xml` is rewritten on read: `02_res_mask.tif.aux.xml` is newer than `03_pop_res.tif`; `03_INPUT/osm_landuse_lux.gpkg` is newer than the `.qgz`), (g) no 0-byte or unreadable outputs. A violation of (d) is high impact: the legacy itself lost alignment (EP-05, MP-04). (f) is only a warning.
7. **Unmatched → questions, grouped per step:** writes without files (branch probably never run), files without a writer under output-like folders (orphan: manual step or stale copy), read-only files no script reads (unused input), files read but neither written nor input-like (missing upstream or manual step; 03 "manual steps").

### 4.2 Runtime lineage of the new pipeline (runner contract)
`ctx.input(ds_id)`: a new upstream output; a legacy file only for `role: input`, `scope: keep_legacy` producers, or `--isolate <step>` (legacy copies of that step's inputs, 09§7.4.5). `ctx.output(ds_id)` gives `runs/<id>/outputs/<step>/<name>`. `ctx.param(p_id)`. The runner writes `lineage.jsonl`: `{step, event: start, code_sha256, params_sha256}`, `{step, event: read|write, dataset, path, source: new|legacy_input|legacy_isolation, sha256, size}`, `{step, event: undeclared_write, path}`, `{step, event: end, status, seconds}`. Generated code never sees legacy intermediate or final paths, because `ctx` refuses them except in isolation mode, where the runner (not the code) supplies them. Undeclared reads by GDAL are not visible in-process. They are contained by giving the run only `role: input` files (E2B uploads only those). **Reconciliation per step:** declared outputs vs written = `OUTPUT_MISSING` (fail) / `OUTPUT_UNEXPECTED` (reported). Reads ⊆ declared inputs. Matching events add `runtime_probed` evidence on the new side.

### 4.3 Pairing and per-step verification order (consumed by verify)
```
pairs = [(d, legacy_ref(d), run.outputs[d.id]) for d in datasets
         if d.role != "input" and scope_of(d.produced_by.step) in {"migrate","bridge"}
         and (d.legacy.exists or d.reference.via_dataset and d.reference.confirmed_by)]
missing_new   = [d for d,_,new in pairs if new is None]          -> OUTPUT_MISSING
untested      = [d for d in in_scope if d.legacy.exists and d not in pairs and not d.verify.waiver]  -> UNTESTED
order = topological(datasets via edges); blame: first non-pass dataset on each path = origin; its descendants = inherited
```
`legacy_ref(d)` is `d.legacy`, or for `equivalent_to` the confirmed `via_dataset`'s legacy file. There is no other branch.

### 4.4 Ladder derivation (`graph.py derive`, per step; project = minimum over in-scope steps)
- `generated`: `new.module` parses, `code_sha256` recorded, every `params` ref exists in params.yaml.
- `sandbox-validated`: the latest run with the current `code_sha256`, `params_sha256` and input hashes ended `ok`, and reconciliation is clean.
- `reference-verified`: in that **end-to-end** run, every paired dataset of the step is `PASS_EXACT`/`PASS_WITHIN_TOLERANCE`, with no `UNTESTED`, `INCONCLUSIVE` or open `NEEDS_DECISION`, no open high-impact question on the step, and all upstream in-scope steps reference-verified. An isolation-mode pass shows only as the label "verified in isolation".
- `target-ready`: reference-verified, and `databricks/job.yml` has a task whose key = step id.
- Staleness: any change to `code_sha256`, `params_sha256`, the step's edges or an upstream ladder drops the step (and its descendants) back to `generated` (MP-29).

### 4.5 Editor ops and the agent's obligations

| Op (page) | Graph effect | Agent must |
|---|---|---|
| `rename` | title, locked | nothing (ids are stable) |
| `confirm` / `reject_node` | `review` | nothing. Confirming does not answer the node's questions. Reject: re-check, ask if code evidence is strong |
| `add_note` | note `user_asserted` | read it. If it contradicts evidence, open a question |
| `add_edge` | edge `user_asserted` | find file:line evidence. If none, model it as a manual step or ask. Reject a cycle with reason |
| `reject_edge` | edge state `rejected` (kept) | if AST evidence exists, ask "code at F:L reads X; is it not used?" (`conflict`) |
| `add_step` | step `kind:new`, `review: pending` | investigate (grep code and notes). Classify as found / manual / new behaviour. If it feeds an in-scope final, ask: flag off by default (parity kept), or accept a known difference (`known-differences.md`) |
| `delete_step` / `set_scope out_of_scope` | scope only; nothing is ever removed | if the step has a dataset with `legacy.exists` or an in-scope consumer, mark `requires_confirmation` and ask (AskUserQuestion) listing the affected datasets/finals. Otherwise apply |
| `set_scope keep_legacy` / `reference_only` | scope | keep_legacy: its legacy outputs become inputs (bridge). reference_only: no code; knowledge only |
| `answer_question` | question `answered`/`assumed` (don't know) | write params.yaml (`source: answer:q.id`) and decisions.md. Update evidence. Re-derive the ladder |
| `set_reference` | `reference.match_method: human` | check kind/CRS/grid/schema plausibility. If implausible, ask |
| `waive_verify` | `verify.waiver` | add to `known-differences.md`. The report shows it |
| `move` | `.graph/layout.json` only | – |

The old `handleDeleteNode` silently removed a node and its edges (`FE/pages/refine.tsx:888-927`). Here deletion is a scope change that keeps history.

### 4.6 graph_server.py (stdlib, ~100 lines; logic lives in graph.py)
`serve [--port 0]` binds 127.0.0.1 and prints `http://127.0.0.1:<port>/?t=<token>`. Any local web page can POST to localhost, so every request must carry the token and a same-origin `Origin`. `GET /` serves graph.html. `GET /api/graph` returns `{revision, graph}`. `GET /api/events?after=<rev>` long-polls for 25 s. `POST /api/ops {base_rev, ops}` returns 200 `{revision}`, or 409 `{revision, conflicting_seqs}`. `POST /api/submit {message}`. `GET /files/<p>` serves only under `migration/` (plots), resolved and prefix-checked. Rebase on 409: the page re-applies its pending ops if their touched fields are disjoint from the ops since `base_rev`; otherwise it shows the conflict. `wait [--timeout 540]` is file-based, so it survives server restarts. It polls `graph-edits.jsonl` for a `submit` with `seq` > the agent cursor. On a submit it prints a JSON diff (`{from_rev, to_rev, ops[], obligations[], requires_confirmation[]}`) with obligations from §4.5, and exits 0. On timeout it exits 2 with the count of unsent edits. The timeout is below Bash's 10-minute ceiling. In Claude Code the agent may run it with `run_in_background` and is re-invoked when it exits. `graph.py ack <seq>` moves `acks.agent_seq`.

### 4.7 Passive hook and colouring
UserPromptSubmit hook `graph_changed.py`: find `migration/graph.json` upward from `cwd`. If user ops with `seq > acks.agent_seq` exist, print at most 12 lines: `graph changed (rev 14→17, 3 user edits, 1 submitted): add_step s05_industrial_weights; set_scope s03b → out_of_scope (needs confirmation)… run graph.py diff --since 14, act per the edit table, then graph.py ack 43`. It stays under 200 ms. On error it prints `graph-change check failed: <err>` (informs, never blocks). The Stop hook blocks while unacked user edits exist. Colours use separate channels so they cannot be confused:

- fill = verdict: PASS_EXACT dark green; PASS_WITHIN_TOLERANCE green; FAIL red; INCONCLUSIVE/NEEDS_DECISION amber; UNTESTED/none grey; EVALUATOR_ERROR/BLOCKED white with a black border.
- failure blame: origin solid, inherited hatched.
- border = review: dashed for pending/assumed, solid for confirmed.
- badge = open questions, red with a count.
- glyph = visual flag ("!" amber; it can downgrade a pass, never raise a FAIL).
- Text glyphs P/F/?/– make it colour-blind safe. Clicking a node opens its plots, reason codes, tolerance and the legacy code lines.

## 5 Reuse

| What | Source | Fix needed | Test |
|---|---|---|---|
| arcpy I/O param table | `AG/data_flow_tracer.py:55-75` | add `arcpy.analysis.*` names, keyword params, `sa` tools, in-place = `reads_write` | 5-style fixture (AK-31) gives 5 ops; 03b gives 10 ops, 0 duplicates |
| visitor / value resolution | `AG/data_flow_tracer.py:152-164,266-290` | **rewrite**: double visit, no BinOp, file-global variables | lux scripts give the ops in Appendix A |
| file hash | `AG/comparators/base.py:181-194` | none | trivial |
| sidecar list | `AG/verify.py:33` | add `.aux.xml .ovr .qix .tfw .msk` | 4 lux `.aux.xml` attached, 0 orphans |
| qgz parse | `FE/lib/gis-parser/index.ts:728-765` | port to `zipfile`+`ElementTree`; relative paths; split layername/subset | lux: 7 datasources, ADM2 flagged |
| task fields | `BE/workflow/smart_dag.py:76-108` | keep `purpose`, `line_range`, `source_script`, `risk_factors`; drop `cloud_equivalent` text | schema test |
| evidence states, corrections | `RC/migration-delivery.ts:8-16,101-111,329-340` | add `documented`; turn the edge drop at :394 into a question | unit test: missing endpoint gives a question |
| scope enum | `BE/ai/migration/scope.py:16-21` | boundaries from DAG edges, not list adjacency (09§6) | fan-in fixture |
| question shape | `BE/workflow/smart_interview_v2.py:54-69` | `nodes[]`, `evidence[]`, `impact`, `allow_dont_know` | round trip |
| dagre layout, MiniMap colours | `FE/lib/dag-utils.ts:14-46`; `FE/pages/refine.tsx:2104-2111` | LR, bipartite node sizes; semantic channels §4.7 | snapshot |
| undo/redo | `FE/pages/refine.tsx:428-441` (`useUndoRedo`) | client-only; never writes the server | – |
| **Do not reuse** | localStorage/TTL persistence (`FE/pages/refine.tsx:56-118,560-602`), swallowed sync errors (:713), mass confirm (:1763), synthetic fallback DAGs (06 Step 1), `LineageView` (09§4.4) | | |

## 6 Acceptance tests (pytest + one Playwright smoke; the fixture is copied to a temp directory for mutations)

1. **T-lin-1 static graph:** `lineage.py FX` gives exactly the steps, datasets and edges in Appendix A (ids, kinds, roles, `legacy.path`/`layer`). Every edge has evidence whose file:line points to the call, e.g. `s01→ds.pop_lux` at `02_SCRIPTS/01_prepare_population.py:40-54`.
2. **T-lin-2 one op per call:** op counts s01=2, s02=7, s03=5, s03b=10, s04=10. No duplicate `(file, line, call)`.
3. **T-lin-3 checks pass on the clean fixture:** 13 non-sidecar outputs (9 in 05_TEMP, 4 in 04_FINAL) each matched to one writer; 4 `.aux.xml` attached; `ds.res_mask` and `ds.factor` equal their `reads_grid` templates and all 5 rasters share the `ds.lux_ppp_2020` grid; layer `03_canton_pop` exists (`04_distribute_value.py:13`); fields `canton, pop_sum, res_sum` exist in `ds.canton_pop`; 0 orphans. Exactly 3 questions: s00 automate (high), s03b scope (medium, 5 missing gdb writes grouped into one), ADM2 unused (low). No question about MIN_AREA, POP_MIN or ×1000 (all resolved).
4. **T-lin-4 mutations (one each, on a copy):** delete `05_TEMP/02_res_mask.tif` → missing-write question, and verify later gives `UNTESTED`; shift `02_res_mask.tif` transform by +1 px → high-impact grid question; add `05_TEMP/old_mask_v2.tif` → orphan question, never paired; rename the gpkg layer `03_canton_pop` → layer question at `04_distribute_value.py:13`; `touch 01_pop_lux.tif` → mtime warning only.
5. **T-pair-1:** a synthetic run whose s02 writes `mask.tif` for `ds.res_mask` pairs correctly. A run without `ds.res_mask` gives `OUTPUT_MISSING`. An extra `debug.tif` gives `OUTPUT_UNEXPECTED`. A stray legacy `05_TEMP/02_res_mask_copy.tif` produces no pair. The pairing module has no string-similarity import (AST lint).
6. **T-pair-2:** 13 pairs in the default scope; 14 after `q.s00…automate = automate` (`ds.cantons_lux_edit` gets a legacy reference). s03b pairs only after the user confirms `equivalent_to`, labelled "reference from equivalent step".
7. **T-cc-1 concurrency:** an agent apply with a stale `base_rev` → 409. A disjoint user op rebases. An agent op on a user-set title → `user_owned_field`. `--actor user` on the CLI is refused. Edit/Write on graph.json is denied by the hook. 20 concurrent applies → revisions 1..20, no lost op, the jsonl has 20 lines.
8. **T-rt-1 round trip:** server up; the page adds step `s05_industrial_weights` reading `ds.osm_landuse`, then deletes `s01_prepare_population`; Send. `wait` exits 0 within 2 s. The diff lists two ops; obligations include "investigate new step" and `requires_confirmation: [s01_prepare_population]` (it has legacy outputs). The graph still contains s01, with unchanged scope.
9. **T-hook-1:** after a user op, hook stdout contains `graph changed (rev`. After `ack` it is empty. p95 < 200 ms. A malformed jsonl line gives "graph-change check failed", exit 0. Stop hook with unacked edits → block.
10. **T-ver-1 colouring and blame:** ingest compare fixtures with a FAIL `RASTER_TRANSFORM_MISMATCH` on `ds.res_mask`: s02 origin (solid red), `ds.pop_res`… inherited (hatched). Ladder s02 = sandbox-validated, project ≤ sandbox-validated. The agent cannot write `verify.verdict` (ACL).
11. **T-ladder-1 staleness:** edit `pipeline/s02_landuse_mask.py` → s02, s03, s04 drop to `generated`.
12. **T-schema-1:** JSON Schema validation, referential integrity, acyclicity (except `ds.x@A` versions), and every `params` ref exists in params.yaml.

## 7 Contradictions resolved

1. **Name suggestions.** 09§7.2 and 08§9.4 keep stem matching as an "unconfirmed suggestion"; the brief forbids any fallback. **Resolution:** none are generated. Humans pair orphans with `set_reference`; `match_method` ∈ {lineage, human}.
2. **Duplicate I/O fields.** 09§7.4 has both `steps.inputs/outputs` and `datasets.produced_by`; 08§9.4 has `pairs[]`. **Resolution:** edges are stored; `produced_by`/`consumed_by` are derived; pairs are implicit in the dataset (D2-2, D2-5).
3. **Evidence names.** The brief says observed/static/inferred/asserted; 2026-07-15 says "evidence-backed"; the code says `verified` (08§8.1). **Resolution:** the code vocabulary plus `documented` (D2-3).
4. **"Red" twice in PLAN.** PLAN uses red for unknowns and red for parity failures. **Resolution:** a badge for questions and fill for verdicts (§4.7).
5. **Immutable versions vs a mutable file.** The July graph has a new id per correction; PLAN wants one `graph.json`. **Resolution:** a mutable head plus append-only log plus per-revision snapshots: the same audit guarantees, with one file to read.
6. **Legacy runtime confirm.** 09§7.4.2 says "run the legacy workflow". PLAN and the fixture say customers hand over outputs and ArcGIS is not needed. **Resolution:** file observation (§4.1.6) is the default. Instrumented legacy replay is optional and gives `runtime_probed`.
7. **Status enum.** `scope.py` has migrate/keep/bridge_in/bridge_out; the brief has "out-of-scope". **Resolution:** `migrate | keep_legacy | bridge | reference_only | out_of_scope`. Bridge direction lives on the bridge step's edges.
8. **Step granularity.** PLAN shows steps 01–04, but SmartDAG's broad nodes failed. **Resolution:** a step = a script, with its ops kept and every persisted dataset a node (D2-1).

## 8 Open questions for the founder

1. 03b: `reference_only` (my default, per NOTES.txt:13), or migrated as an ArcPy-translation alternative step verified against s03's legacy outputs (the PLAN demo line "one ArcPy script kept only for translation")?
2. Live demo edit. "Automate the manual canton rename" is verifiable against `cantons_lux_edit.gpkg`. "Add the industrial branch" (NOTES.txt:29) has no legacy reference and breaks parity by design. I propose the first for the stage.
3. A vendored bundle (about 300 KB, built once with esbuild) or a CDN import map? I recommend vendored.
4. Should the page allow direct param edits, or only answers to questions? I propose answers only, for the demo.
5. Legacy intermediates that are overwritten in place: accept them as unverifiable, or offer instrumented legacy replay for customers who still have QGIS/ArcGIS?
6. The agent could forge user ops by calling the HTTP endpoint with the token. Is an audit flag acceptable (a hook denying `curl …/api/ops`, and the report listing the channel of every user op), or is a stronger separation wanted?

Not checked: GBrain (no auth); I relied on 07/08. `~/src/eo-agent-benchmark-catalogue` was not read.

## 9 Cross-section contract

- **From discover (inventory.json), needed:** per file `relpath, size, mtime, sha256, kind, sidecar_of`; raster `crs, transform, shape, dtype, nodata, band_count`; vector `layers[{name, crs, geom_type, count, fields{name: type}}]`; table `sheets[{name, used_range}]`/csv header; `content_sha256` for gpkg/xlsx; scripts `{path, parses, imports}`; notebooks `{exec_counts, monotonic}`; qgz `{datasources[]}`; secrets redacted before anything is written.
- **verify reads:** `datasets[].{id, kind, role, legacy, reference, new, compare, produced_by}`, step `scope`, edges for topological order, and `runs[]`. It writes only `compare/*.json`. `graph.py ingest-verify <run>` projects verdict, reason codes, report path+sha256 and plots into `datasets[].verify`, and blame into steps.
- **interview reads:** `questions` with `state=open`, sorted by `impact`, then by node topological order. It writes answers via `graph.py apply --actor agent` with `channel: interview` and `verbatim`. The page writes them with `channel: graph`. decisions.md is appended at answer time and cites `q.id` and `rev`.
- **params:** `steps[].params` hold ids only, and values live only in params.yaml (HL-36). graph_server shows values when PyYAML is importable, otherwise only the ids.
- **migrate/run read:** steps with `scope ∈ {migrate, bridge}`, `new.module/function`, the `ctx` dataset map (`datasets[].new`, `role`). The runner writes `lineage.jsonl` (§4.2). `graph.py derive` updates the ladder.
- **report reads:** everything, including `graph-edits.jsonl` (who changed what), questions answered/assumed/"don't know", waivers, and the ladder with `blocking[]`.
- **hooks read:** PreToolUse denies edits to graph.json/jsonl. UserPromptSubmit uses `acks.agent_seq` vs the jsonl. Stop needs `ladder.project ≥ reference-verified` for "done" claims (target-ready for the demo), no unacked user edits, and no open high-impact question.

## Appendix A — expected lux graph (`FX`, investigate-first; legacy paths relative to the legacy root)

**Steps** (id · tool · source lines · scope · executed): `s00_manual_canton_names` · qgis_gui · NOTES.txt:15-19 · migrate (pending q) · yes (documented) | `s01_prepare_population` · pyqgis · 01_prepare_population.py:30-54 · migrate · yes | `s02_landuse_mask` · pyqgis · 02_landuse_mask.py:26-76 · migrate · yes | `s03_zonal_population` · pyqgis · 03_zonal_population.py:27-55 · migrate · yes | `s03b_zonal_arcpy_old` · arcpy · 03b_zonal_arcpy_OLD.py:10-65 · reference_only (pending q) · no (5 writes missing; NOTES.txt:13) | `s04_distribute_value` · pyqgis+python · 04_distribute_value.py:35-141 · migrate · yes.

**Datasets** (id · kind · role · legacy path[|layer] · produced_by): inputs: `ds.lux_ppp_2020` raster 03_INPUT/lux_ppp_2020.tif · `ds.geoboundaries_adm1` vector 03_INPUT/geoBoundaries-LUX-ADM1.geojson · `ds.geoboundaries_adm2` vector …-ADM2.geojson (qgz only) · `ds.osm_landuse` vector 03_INPUT/osm_landuse_lux.gpkg|landuse · `ds.value_per_capita` table 03_INPUT/value_per_capita.xlsx (sheet vpc, rows 5-16) · `ds.cantons_lux_edit` vector 03_INPUT/cantons_lux_edit.gpkg|cantons (by s00). intermediates: `ds.lux_outline` 05_TEMP/01_lux_outline.gpkg|01_lux_outline (s01.op1) · `ds.pop_lux` 05_TEMP/01_pop_lux.tif (s01.op2) · `ds.res_2169` …/02_res_2169.gpkg|02_res_2169 (s02.op4) · `ds.res_4326` …/02_res_4326.gpkg|02_res_4326 (s02.op5) · `ds.res_mask` …/02_res_mask.tif (s02.op7) · `ds.pop_res` …/03_pop_res.tif (s03.op3) · `ds.canton_pop` …/03_canton_pop.gpkg|03_canton_pop (s03.op5) · `ds.canton_factor` …/04_canton_factor.gpkg|canton_factor (s04.op2+op3, in place within one step) · `ds.factor` …/04_factor.tif (s04.op5). finals: `ds.res_exposure_2020` 04_FINAL/lux_res_exposure_2020.tif (s04.op6) · `ds.exposure_by_canton_gpkg` …gpkg|exposure_by_canton (s04.op7) · `ds.exposure_by_canton_csv` (s04.op9) · `ds.exposure_by_canton_xlsx` sheet by_canton (s04.op10). 03b (persistence gdb, `legacy.exists: false`): `ds.gdb_cantons_edit`, `ds.gdb_res_mask` (inputs, missing) · `ds.gdb_pop_lux`, `ds.gdb_pop_res`, `ds.gdb_zs_pop`, `ds.gdb_zs_res`, `ds.gdb_canton_pop` (writes, missing).

**Edges** (40): s00 reads adm1 · s00 writes cantons_lux_edit | s01 reads cantons_lux_edit (L12,32), lux_ppp_2020 (L11,41) · writes lux_outline (L35), pop_lux (L54) | s02 reads osm_landuse (L8,28), reads_grid pop_lux (L56-59→L67-69) · writes res_2169 (L48), res_4326 (L54), res_mask (L76) | s03 reads pop_lux (L27), res_mask (L28), cantons_lux_edit (L8,41) · writes pop_res (L37), canton_pop (L55) | s03b reads lux_ppp_2020 (L18,28) + reads_grid (L23-26), gdb_cantons_edit (L20), gdb_res_mask (L19,32) · writes gdb_pop_lux (L29), gdb_pop_res (L34), gdb_zs_pop (L36), gdb_zs_res (L37), gdb_canton_pop (L41-63, reads_write) | s04 reads value_per_capita (L36-42), canton_pop (L46), pop_res (L97) + reads_grid pop_res (L76-78) · writes canton_factor (L46, L47-72 in place), factor (L94), res_exposure_2020 (L106), exposure_by_canton_gpkg (L115), _csv (L126-130), _xlsx (L132-141) | equivalent_to (`documented` 03b L2-5 + NOTES.txt:13; unconfirmed): gdb_pop_lux≈pop_lux, gdb_res_mask≈res_mask, gdb_cantons_edit≈cantons_lux_edit, gdb_pop_res≈pop_res, gdb_canton_pop≈canton_pop. `displays` evidence (qgz) on cantons_lux_edit, adm2, exposure_by_canton_gpkg, lux_ppp_2020, res_exposure_2020, osm_landuse (subset `landuse='residential'` corroborates 02 L47) and res_mask.

**Params referenced (values in params.yaml):** p.s01.crop_to_cutline=false, p.s01.keep_resolution=false, p.s01.nodata=-99999; p.s02.work_crs=EPSG:2169, p.s02.min_area_m2=2500, p.s02.landuse_class=residential, p.s02.all_touched=true, p.s02.mask_nodata=0 (QGIS default when unset); p.s03.pop_min=0.5; p.s04.vpc_scale=1000, p.s04.vpc_sheet_rows=vpc!5:16, p.s04.name_strip=true, p.s04.exposure_dtype=float32, p.s04.factor_dtype=float64.
**Expected pairs:** the 13 intermediates and finals above (+ `ds.cantons_lux_edit` if s00 is automated).
