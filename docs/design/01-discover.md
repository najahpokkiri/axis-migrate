# 01 Discover / scan — design

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Status: design draft, 13 Oct 2026. Owner: section 01. Covers `skills/discover/scan.py`, the scan half of `map.py`, and `migration/inventory.json`.
Path prefixes used for evidence: `LA/` = `~/src/axis-spatial/_archive/services/legacy-service-archive/agent/axis_spatial/`; `OT/` = `~/src/axis-spatial-old/tool/src/axis_spatial/`; `FE/` = `~/src/axis-spatial/_archive/apps/legacy-product-app/src/`; `RC/` = `git show server/codex/mcp-v2-recovery:packages/runtime-core/src/` in `~/src/axis-spatial`; `FX/` = the lux fixture (what the agent sees); `SC/NN` = `~/src/context/skill-candidates/NN-*.md`.

## 1 Scope and interfaces

Discover turns a read-only legacy folder into **evidence, not interpretation**. It runs before any code or question exists. It has two parts:
- `scan.py <legacy_dir> --out <project>/migration` is deterministic and makes no model calls. It writes `inventory.json`, `discover/profiles/*.json` and `discover/scan.log`.
- `map.py inputs` draws PNGs from the inventory. The agent looks at them and writes findings back through `scan.py annotate`.

The agent then reads the inventory, NOTES-style docs and the PNGs, and passes its conclusions to investigation and graph.

**Consumes:** the legacy folder only (never anything outside it); the Python env (rasterio, pyogrio, shapely, openpyxl, optional xlrd/python-calamine); a shipped `qgis_algs.json` and `arcpy_tools.json` (§4.3).

**Produces:**

| For | Artefact / fields |
|---|---|
| lineage + graph (02/03) | `ops[]` (one per tool call, with resolved `reads`/`writes` and file:line); `step_candidates[]`; `manual_step_candidates[]`; `memory_datasets`; `unresolved[]` path expressions |
| investigation + interview | `settings[]` (env/grid); `params[]` (literals with use sites and comments); `docs[].mentions`; `comments`; `twins`; `derived_copies`; `excel` header candidates; `open_items[]` (all `unresolved` or `inferred`, never questions yet) |
| verify (compare/plot) | `datasets[]` with `legacy_path`, `role`, `reference_candidate`, full structural profile, `content_sha256`, `grid_id`, `compare_hints` |
| hooks | the `reference_candidate` path list (deny-list for generated code); `scan.read_only_verified` |
| report | inventory tables, `map.findings[]`, PNG paths |

**Out of scope:** naming final steps, choosing open-source equivalents, asking questions, running the legacy code. Runtime probing is deferred (D1-18).

## 2 Decisions

- **D1-1 Scan is a script and the model annotates.** `scan.py` alone writes facts, with an `EvidenceState` taken from `RC/migration-delivery.ts:8-16` (`directly_observed`, `structurally_inferred`, `ai_inferred`, `user_asserted`, `runtime_probed`, …). The agent's judgements go under `annotations` and carry `state: ai_inferred`. A rescan never overwrites `annotations`; they are merged by id. Evidence: The old SmartDAG compressed a real project into 6 nodes (SC/08 §2.4). The old app threw its AI analysis away (`FE/lib/migration-discovery.ts:220-222`, SC/06 Step 0). PLAN rule: "the model writes code; scripts check it".
- **D1-2 List every file and skip nothing silently.** Old skips: `LA/agent/project_detector.py:154-158` drops dirs named `Scripts`, `lib`, `bin`, `env`; `:394` drops all hidden dirs, including `.ipynb_checkpoints`. `OT/agent/workflow_parser.py:289` (`LA/…:415`) drops any path containing the substring `"env"`. These skips hide the twins that HL-25 and SC/03 ("Diverged twins") say must be reconciled. New rule: unknown files get `role: unknown`. Only VCS internals (`.git/`) are excluded, and they are listed with a reason.
- **D1-3 Code analysis uses AST with flow-sensitive abstract values, plus `tokenize` for comments. Regex is never used for I/O.** Evidence: AK-31; SC/09 §1.1 (double visits, no kwargs, no modern names, flow-insensitive). I ran the old extractor (`LA/agent/workflow_parser.py:611-628`) on `FX/02_SCRIPTS`: it returns fragments such as `/01_pop_lux.tif` with no root; it **misses** `cantons_lux_edit.gpkg|layername=cantons`, `osm_landuse_lux.gpkg|layername=landuse` and `value_per_capita.xlsx`, i.e. 3 of the 4 code-read inputs (verified 13 Oct); it cannot tell reads from writes. Comments are first-class evidence. `FX/01_prepare_population.py:37-39` is the only statement of the grid intent.
- **D1-4 Read/write roles come from signature tables, not name guessing.** The QGIS table is generated from `qgis_process --json help <alg>`. Verified for `gdal:rasterize`: `OUTPUT is_destination=True`, all other parameters False. The ArcPy table lifts `LA/agent/data_flow_tracer.py:55-75` and adds: modern `arcpy.<toolbox>.<Tool>` names; keyword arguments by parameter name; Spatial Analyst functions returning `Raster`, written with `.save()`; in-place tools as read+write (SC/09 §7.1). Calls not in a table get `io_source: "name_heuristic"` and a lower `state`.
- **D1-5 One `settings[]` model for ArcPy env and QGIS per-call grid parameters.** Every raster-shaping setting is recorded with `set_by` = `assignment` | `env_manager` | `tool_param` | `derived_from_dataset` | `default_unset`, its scope and its use sites. Evidence: IN-01, IN-16, EP-11, MP-03 (snapRaster set ~19 times in the real legacy), FH-13 ("the planner never extracted env settings"), AK-16. In QGIS the grid lives in call parameters: `FX/02_landuse_mask.py:56-75` (EXTENT/WIDTH/HEIGHT taken from the pop raster) and `FX/01_prepare_population.py:47-48` (CROP_TO_CUTLINE/KEEP_RESOLUTION False).
- **D1-6 Statistics are always computed, never read from file metadata.** Embedded GDAL tags and `.aux.xml` PAM statistics are recorded as `embedded_stats` with their `approximate` flag and diffed against computed values (EP-26). `FX/05_TEMP/01_pop_lux.tif.aux.xml` carries `STATISTICS_APPROXIMATE=YES`. `FX/03_INPUT/lux_ppp_2020.tif` carries tags too. The scan sets `GDAL_PAM_ENABLED=NO` so it cannot write new sidecars.
- **D1-7 Scan computes grid families and pixel offsets.** Rasters are grouped by (CRS, resolution, origin modulo resolution). Each member records its origin offset from the family reference in pixels; a non-integer offset means misaligned (MP-03 check, IN-16 check).
- **D1-8 Classification uses ranked evidence and an explicit `unknown` (§4.5).** mtime is corroborating evidence only. In FX all 30 files have mtimes within 10 minutes of each other (copy/checkout artefact), so `scan.mtime_reliable=false`.
- **D1-9 Two hashes per file.** `sha256` covers the bytes. `content_sha256` is canonical for GPKG (table rows and geometry blobs, excluding `gpkg_contents.last_change`) and XLSX (cell values and formulas, excluding `docProps`). Reason: GPKG and XLSX bytes change on every write even when content is identical (generic GDAL/openpyxl behaviour; observed on this fixture's reruns). Without this, verify and twin detection would fire falsely.
- **D1-10 Derived-copy detection exposes manual steps.** Two vector datasets with equal feature count and equal per-feature WKB hashes on a shared id column are a `derived_copy`. The record also captures the schema diff and the value mapping between changed columns. Evidence: SC/03 "Manual steps inside automated scripts"; MP-27. In FX: ADM1 → `cantons_lux_edit` has 12/12 WKB equal. It adds `canton`, made of 11 prefix drops and one exception (`Canton Esch-sur-Alzette`→`Esch/Alzette`). Verified 13 Oct.
- **D1-11 Superseded and twin code is evidence, not a step.** Status `live | superseded | helper | dead | twin | unknown`, decided from docs, file names (`_OLD`, `old`, `copy`, `backup`, `vN`), header comments, call reachability and hash-normalised code diff (HL-25, SC/03 twins). Superseded code still contributes `settings` and `params`, flagged `from_superseded: true`. 03b is the only file that states the snap/cell/extent intent explicitly (`FX/03b:23-26`).
- **D1-12 Notebooks keep cell addresses** (§4.4). Code cells are not concatenated (`LA/agent/workflow_parser.py:536-597` concatenated them and fell back to regex on any magic). Execution order is recorded, magics become ops, and outputs are weak evidence. A non-monotonic `execution_count` marks the saved outputs as untrusted (SC/03 "Out-of-order execution").
- **D1-13 Excel is read twice and the header row is never inferred silently** (AK-30; SC/05 Table structural #2). openpyxl reads with `data_only=False` and again with `data_only=True`; `.xls` and `.xlsb` go through calamine/xlrd. Header candidates are ranked. A candidate is `resolved` only when code corroborates it (`FX/04_distribute_value.py:39` reads rows 5–16, so the header is row 4). Formula cells with no cached value are flagged. In FX, `vpc!B18 =AVERAGE(B5:B16)` has a `None` cached value because the file was saved by openpyxl (verified).
- **D1-14 Docs are mined deterministically and contradictions become open items.** Sources: `.txt`/`.md`/`.rst`, and `.docx` text through python-docx if installed. Extracted: path mentions, run order, "don't run", NAME VALUE pairs, units. Each documented constant is checked against code use sites. HL-26 says a parameter default is not wiring, so `params[].used_at` must be non-empty for a parameter to count as live; otherwise it is flagged `dead_knob` (SC/03 "Dead knobs").
- **D1-15 Map before code, with numbers behind every visual claim** (§4.7). PLAN "Maps are used from the start"; MPV-03 (human review of intermediates found the real defects). A finding without a computed metric stays `ai_inferred` and can only raise a question.
- **D1-16 Read-only operation and privacy.** Before and after hashes of every file give `scan.read_only_verified` (HL-21 "never mutate legacy inputs"). SQLite-based files (GPKG) are opened read-only/immutable. Opening an input GPKG with a QGIS subset filter bumped its SQLite header in this very fixture build. Stored paths are legacy-root-relative POSIX only, following `RC/project-understanding.ts:27-30`. Secret-like names (regex at `RC/project-understanding.ts:329`) are **excluded and recorded by name and size**, not used to refuse the whole scan (contradiction C7). Connection strings in code, qgs or aprx are redacted to `<redacted:password>` (SC/03 "Secrets").
- **D1-17 Ids are stable slugs.** `ds.<stem>`. If two files share a stem, every one of them becomes `ds.<stem>_<ext>`. A layer is suffixed `__<layer>` only for multi-layer files whose layer name differs from the stem. Steps are `sNN[a]_<rest>` from the script stem; ops are `<step>.opK`; manual steps are `mNN_<output_stem>`. Never `hash()`: `LA/discovery/scanner.py:77,144,232` used `hash(path) % 10000`, which changes per process (PYTHONHASHSEED) and collides. In FX `exposure_by_canton.{gpkg,csv,xlsx}` share a stem (cf. SC/05:223 stem-map overwrite).
- **D1-18 Discovery is static plus existing artefacts for the demo.** It does not re-run the legacy code. SC/08 §2.4's runtime channel (files actually read and written) is open question Q1. FX ships every per-step intermediate, so static evidence plus intermediates is enough to pair every step.
- **D1-19 Format coverage tiers.** Full parse: `.py`, `.ipynb`, `.qgs`/`.qgz`, `.model3`, `.xlsx`/`.xlsm`/`.xls`/`.xlsb`, `.csv`, rasters, vectors, `.gdb` (pyogrio; AK-27 domains listed). Parsed best-effort: `.aprx` and `.atbx` (zip with CIM JSON). Listed only, each producing an open item: `.mxd`, `.tbx`, `.lyr`, `.fmw`, `.sql`. Exception: `.sql` gets table names extracted from `FROM`/`INTO`/`UPDATE` only (SC/06 gap ".model, .tbx, .sql").

## 3 Schemas

`migration/inventory.json`: keys sorted, lists sorted by id; `scan.started_at` is the only volatile field.
```json
{"schema":"axis-migrate.inventory.v1",
 "legacy_root":"../lux-exposure-legacy",
 "scan":{"version":"0.1.0","started_at":"…","libs":{"gdal":"3.13.3","rasterio":"1.5.2"},
         "manifest_sha256":"sha256:…","read_only_verified":true,
         "mtime_reliable":false,"mtime_reason":"30/30 files within 600 s",
         "legacy_roots":[{"value":"C:\\GIS\\LUX_exposure","evidence":[{"kind":"commented_code","file":"02_SCRIPTS/01_prepare_population.py","line":7}]},
                         {"value":"D:\\GIS\\LUX_exposure","evidence":[{"kind":"code_literal","file":"02_SCRIPTS/03b_zonal_arcpy_OLD.py","line":13}]}]},
 "files":[{"path":"05_TEMP/01_pop_lux.tif","size":2199027,"sha256":"…","content_sha256":null,"mtime":"…",
           "kind":"raster","dataset_ids":["ds.01_pop_lux"],"sidecars":["05_TEMP/01_pop_lux.tif.aux.xml"]},
          {"path":"05_TEMP/01_pop_lux.tif.aux.xml","kind":"sidecar","sidecar_of":"05_TEMP/01_pop_lux.tif"},
          {"path":".env","kind":"excluded","excluded_reason":"secret_like_name","size":120}],
 "datasets":[{"id":"ds.01_pop_lux","legacy_path":"05_TEMP/01_pop_lux.tif","layer":null,"format":"GTiff","exists":true,
   "role":"intermediate","role_state":"structurally_inferred","role_evidence":[
     {"kind":"code_write","op":"s01_prepare_population.op2","file":"02_SCRIPTS/01_prepare_population.py","line":40,"arg_line":54},
     {"kind":"code_read","op":"s02_landuse_mask.op6","file":"02_SCRIPTS/02_landuse_mask.py","line":56},
     {"kind":"folder","value":"05_TEMP"},{"kind":"doc_mention","file":"NOTES.txt","line":33}],
   "reference_candidate":true,"produced_by":["s01_prepare_population.op2"],
   "consumed_by":["s02_landuse_mask.op6","s03_zonal_population.op1","s03_zonal_population.op2"],
   "grid_id":"grid.lux_ppp_2020","derived_from":null,
   "profile_ref":"discover/profiles/ds.01_pop_lux.json",
   "compare_hints":{"kind":"raster","categorical":false,"volatile_bytes":[]}}],
 "ops":[ /* §3 op record */ ], "code":[ /* §3 code record */ ],
 "settings":[], "params":[], "unresolved":[], "memory_datasets":[],
 "step_candidates":[], "manual_step_candidates":[], "derived_copies":[], "twins":[],
 "projects":[], "docs":[], "grids":[], "extent_relations":[],
 "open_items":[], "map":{"pngs":[],"findings":[]}, "annotations":{}}
```
**Op record** (one per tool/IO call; FX example). Every `value` carries its resolution chain, so the agent can audit it.
```json
{"id":"s02_landuse_mask.op6","code":"code.s02_landuse_mask","file":"02_SCRIPTS/02_landuse_mask.py","line":61,"end_line":76,
 "api":"processing.run","tool":"gdal:rasterize","family":"pyqgis","io_source":"qgis_algs.json",
 "params":{"INPUT":{"expr":"res4326","value":{"dataset":"ds.02_res_4326"},"chain":["02:51-54"]},
           "BURN":{"value":1},"NODATA":{"value":null},"UNITS":{"value":0},
           "WIDTH":{"expr":"pop.width()","value":{"derived":"width_of","dataset":"ds.01_pop_lux"}},
           "EXTENT":{"expr":"extent","value":{"derived":"extent_of","dataset":"ds.01_pop_lux","format":"%.12f"},"chain":["02:58","02:57","02:56","02:10"]},
           "EXTRA":{"value":"-at","gdal_flags":{"-at":true}},"OUTPUT":{"value":{"dataset":"ds.02_res_mask"},"arg_line":76}},
 "reads":[{"dataset":"ds.02_res_4326","param":"INPUT","line":62}],
 "writes":[{"dataset":"ds.02_res_mask","param":"OUTPUT","line":76,"mode":"create"}],
 "comments":[{"line":75,"text":"all touched, otherwise lose the thin village strips"}],
 "flags":[], "state":"directly_observed"}
```
- `writes[].mode` is `create`, `in_place` or `append`. Examples of `in_place`: `pr.changeAttributeValues` at `FX/04:72`, ArcPy `AddField`, `UpdateCursor`.
- A `memory:` output becomes `{"memory":"ds.mem.s02_l30"}`.

**Code record:**
```json
{"id":"code.s03b_zonal_arcpy_old","path":"02_SCRIPTS/03b_zonal_arcpy_OLD.py","language":"python","runtime":"arcpy",
 "status":"superseded","status_evidence":[{"kind":"doc_mention","file":"NOTES.txt","line":13,"text":"…old arcgis version of 03, don't run."},
   {"kind":"name","value":"_OLD"},{"kind":"comment","line":3,"text":"replaced by 03_zonal_population.py (QGIS)…NOT USED."}],
 "twin_of":"code.s03_zonal_population","imports":[{"module":"arcpy","line":7},{"module":"arcpy.sa","names":"*","line":8}],
 "entry":"module","functions":[],"flags":[{"kind":"swallow_print","line":58},{"kind":"abs_path","line":13}],
 "commented_code":[], "parse_error":null}
```
**Setting record:**
```json
{"id":"set.s03b.snapRaster","code":"code.s03b_zonal_arcpy_old","name":"snapRaster","family":"arcpy_env",
 "set_by":"assignment","file":"02_SCRIPTS/03b_zonal_arcpy_OLD.py","line":23,
 "value":{"dataset":"ds.lux_ppp_2020","chain":["03b:18","03b:13"]},"scope":{"from_line":23,"to_line":null},
 "applies_to":["s03b_zonal_arcpy_old.op1","…"],"from_superseded":true}
```
- Canonical names: `snapRaster`, `cellSize`, `extent`, `outputCoordinateSystem`, `mask`, `resampling`, `workspace`, `nodata`, `all_touched`, `target_aligned_pixels`, `crop_to_cutline`, `keep_resolution`, `units`, `width`, `height`, `dtype`.
- QGIS parameters map to them: `TARGET_CRS`→outputCoordinateSystem; `-tap`/KEEP_RESOLUTION→target_aligned_pixels; `-at`→all_touched; `EXTENT`; `CELL_SIZE`/`TARGET_RESOLUTION`→cellSize; `RESAMPLING`; `NODATA`/`NO_DATA`; `DATA_TYPE`/`RTYPE`→dtype.
- For every raster-producing op whose family has an env model, the env names that are not set are emitted as `default_unset` (FH-13).

**Param record** (literal constants, feeding params.yaml):
```json
{"id":"p.s02.MIN_AREA","name":"MIN_AREA","value":2500,"file":"02_SCRIPTS/02_landuse_mask.py","line":12,
 "comment":"m2","used_at":[{"op":"s02_landuse_mask.op4","param":"EXPRESSION","line":47}],
 "doc_mentions":[{"file":"NOTES.txt","line":22,"value":2500,"agrees":true}],"shadowed_by":null,"flags":[]}
```
Anonymous literals inside tool parameters get ids such as `p.s01.op2.NODATA`.

**Unresolved record:** `{"file","line","expr":"os.path.join(d, f)","reason":"loop_over_listdir","candidates":["…"],"op":"…"}`. Reason codes: `argv`, `env_var`, `input_call`, `loop_over_listdir`, `glob`, `function_param`, `dynamic_attr`, `fstring_unknown`, `cross_file_unresolved`.

**Profiles** (`discover/profiles/<id>.json`):
- Raster: `driver, crs{wkt,epsg,is_geographic,units}, transform[6] (repr floats), shape, res, res_m_at_centre, bounds, origin, dtype, nodata, nodata_in_domain, bands, block, compress, overviews, scale_offset, colortable, embedded_stats{…,approximate}, computed{valid,nodata,nan,zero,min,max,mean,std,sum (float64)}, unique (int ≤256 values), hist64, stats_mode:"full|strided"`.
- Vector, per layer, with every layer listed: `driver, layers[], crs, geom_types{}, count, schema[{name,dtype}], null_geom, empty_geom, invalid{count,top_reasons}, bounds, area_km2 (local LAEA), key_candidates[{col,unique,nonnull,unique_after_strip_casefold}], domains{col:[≤50 values]}, wkb_sha_by_key`.
- Table: `sheets[{name,hidden,dims,merged[],header_candidates[{row,score,reason}],header_row,header_state,data_rows[a,b],formula_cells,formula_no_cache[],units_cells[],strip_issues[],named_ranges,external_links}]`. CSV also gets `encoding,bom,sep,decimal,code_like_cols` (IN-22, IN-23).

**Open item** (a candidate only; investigation decides whether it becomes a question):
```json
{"id":"OI-03","kind":"manual_step","severity":"high","about":["m01_cantons_lux_edit","ds.cantons_lux_edit"],
 "state":"inferred","agent_guess":null,"evidence":[{"kind":"doc_mention","file":"NOTES.txt","line":15},{"kind":"data_diff","ref":"dc.01"}]}
```
Kinds: `missing_input`, `manual_step`, `unknown_role`, `orphan_output`, `unused_branch`, `doc_code_conflict`, `header_unresolved`, `twin_conflict`, `unparsed_format`, `untrusted_notebook_outputs`, `misaligned_grid`, `crs_mismatch`, `dead_knob`.

## 4 Algorithms

**4.1 Walk.**
1. `os.walk(followlinks=False)`; symlinks are recorded, not followed.
2. Hash every file before the scan.
3. Group sidecars (`.aux.xml`, `.ovr`, `.tfw`, `.prj`, `.cpg`, `.dbf`/`.shx`/`.qix`/`.sbn` of a `.shp`, `.qmd`, `-wal`/`-shm`).
4. Treat a `.gdb` directory as one file.
5. Dispatch by extension, then by sniffing magic bytes for files without an extension.
6. Re-hash afterwards and set `read_only_verified`. Every check must pass or the scan exits non-zero.

**4.2 Python resolver.** Each module and each notebook cell sequence is processed in order.
- **Environment.** Maps name → `Value`. A value is one of `Const`, `Concat`, `Dataset(path, layer)`, `Memory`, `ToolOut(op, key)`, `Derived(fn, dataset)`, `Phi[...]` or `Unknown(reason)`. Assignments rebind, which makes the resolution flow-sensitive and so fixes the "last wins" global dict (SC/09 §1.1).
- **Expressions evaluated:** `+` and `%` on strings; f-strings and `.format` with resolvable parts; `os.path.join`. `pathlib` `/`, `Path(...)` and `str(...)`; `__file__` and `os.path.dirname`. `os.environ.get(k, d)` resolves to `d`, flagged `env_var`. Dict and list literals. Subscripts of `processing.run(...)` resolve to `ToolOut`. `QgsRasterLayer(p).extent()`, `.width()` and `.height()`, and `arcpy.Describe(p).spatialReference`, become `Derived`. `if`/`else` produces `Phi`. A `for` over a literal list is unrolled up to 50 iterations. `glob`/`listdir` patterns are expanded against the disk (listing order recorded, IN-32). Local functions with resolvable arguments are inlined to depth 2. `from X import *` of local modules is resolved cross-file. A later local rebinding is recorded as `shadowed_by` (IN-08).
- **Path normalisation:** Raw Windows strings are split on `\` or `/`. An absolute path is rebased by its longest suffix that exists under the legacy root, case-insensitively. The stripped prefix is recorded in `legacy_roots`. OGR/GDAL URIs are parsed: `p|layername=x|subset=…|layerid=n`, `GPKG:p:layer`, `/vsizip/`, and gdb `x.gdb\fc`. The layer default is the file stem (QGIS/GDAL output naming). This is checked against the profile's real layer list; for example `FX/04:13` reads `03_canton_pop.gpkg|layername=03_canton_pop`.
- **Call classification:** `processing.run(alg, {…})` / `processing.runAndLoadResults`: I/O comes from `qgis_algs.json` `is_destination`. ArcPy: aliases are resolved (`import arcpy as ap`, `from arcpy import sa`, star imports bind `arcpy.sa` names). Names are normalised `Tool_toolbox` ↔ `toolbox.Tool`, then looked up in `arcpy_tools.json`. `Raster(p)` is a read; `r.save(p)` is a write. `da.SearchCursor` is a read; `UpdateCursor` and `InsertCursor` are in-place. Python I/O table: geopandas/pyogrio `read_file`/`to_file`/`read_parquet`/`to_parquet`; `rasterio.open(p, mode)`; pandas `read_*`/`to_*`; openpyxl `load_workbook` (sheet, `min_row`/`max_row`, `data_only` captured) and `.save`; `open(p, mode)`; `shutil.copy*` (read src, write dst); `os.remove`/`rename` (flag `mutates_files`); `QgsVectorLayer`/`QgsRasterLayer` (read); `dataProvider().addAttributes`/`changeAttributeValues` (in place); `QgsVectorFileWriter.*` (write); `subprocess`/`os.system`/`!cmd` with gdal or ogr tools: the argv is parsed so the last positional is the destination, the rest are sources, flagged `io_inferred_by_cli`.
- **Flags:** `exists_guard` (`if arcpy.Exists(x)` / `os.path.exists(x)` that skips work; records `checked_path` vs `written_path`, SC/03 caches); `input_call` (manual pause), `swallow_except` (AK-32), `abs_path` (HL-19/FH-14); `uncalled_function`, `disabled_by_docstring` (a triple-quoted block that parses as code), `pip_install`.
- **Comments:** `tokenize` comments attach to the statement on the same line or the next one. A comment whose text parses as an `Assign` or `Call` is `commented_code`.
- **Params:** every `Assign` of a literal at module or function top level, plus every literal in a tool parameter. `used_at` comes from def-use chains.

**4.3 Signature tables (shipped data, versioned).**
- `qgis_algs.json` is built once in the server `qgis` env: `qgis_process --json list`, then `qgis_process --json help <id>` for the `native:`, `gdal:` and `qgis:` providers. It is stamped with the QGIS version. Unknown algorithm ids fall back to the naming rule (`OUTPUT*` = write), labelled as such.
- `arcpy_tools.json` is lifted from `LA/agent/data_flow_tracer.py:55-75`, extended by hand to ~80 tools, with parameter names and in/out positions. The tests assert the five-style detection of AK-31.

**4.4 Notebooks, QGIS projects and ArcGIS Pro projects.**
- **`.ipynb`** (`json`, not nbformat, to stay dependency-light): Each code cell is addressed `nb.ipynb#c12:L3` and records `execution_count`. Magic and shell lines are blanked in place, so line numbers are preserved. They are recorded as `magics[]`: `%run x.py` is a code dependency; `!ogr2ogr …` is an op parsed like `subprocess`; `%cd` changes the base directory of path resolution. The resolver runs twice: in cell order (`order:"cell"`) and in execution order (`order:"exec"`). Paths that differ between the two are flagged. Flags: non-monotonic, duplicate or gap exec counts (kernel restart); unexecuted cells with code; `error` outputs. Saved outputs are untrusted unless the order is monotonic. Output text (`stream`, `text/plain`) is scanned for paths and simple `name = value` reprs, as `notebook_output` evidence (weak). Twins: `.ipynb_checkpoints/*`, a `.py` export and a `-Copy` notebook are compared by normalised code hash and by the set of cells shared (SC/03: an export held 55 of 109 cells).
- **`.qgz`/`.qgs`:** Unzip `.qgz` in memory and parse the XML with `defusedxml`. Recorded: project CRS (`<projectCrs>`), the `Paths/Absolute` flag, and per `<maplayer>` the `provider`, `datasource`, `layername`, layer CRS, layer-tree group and visibility. Relative datasources are resolved against the `.qgs` location. Subset strings are parsed into `{field, op, value}`; PostGIS `dbname=… host=…` strings are redacted. Embedded processing models (`*.model3` files and models stored in the project) are parsed as XML: algorithm ids, child inputs/outputs and parameter values. They become ops with `family:"qgis_model"`. Group names (`input`, `working`, `results`, `output`, `tmp`) are weak role evidence.
- **`.aprx`/`.atbx`:** Unzip and read `GISProject.json`. Follow `CIMPATH=` references to map and layer JSON. Per layer: `dataConnection.workspaceConnectionString`, `workspaceFactory`, `dataset`, `datasetType`, `spatialReference.wkid`/`latestWkid`; per map, the spatial reference. Anything unparsed becomes an `unparsed_format` item. This must be validated on a real `.aprx` (Q4): no `.aprx` fixture exists, and `FE/lib/aprx-parser/index.ts:132-160,244-260` reads neither `dataConnection` nor `CIMPATH`.

**4.5 Classification.**
- **Live steps.** Code with status `live` is ordered by:
  1. a doc run-order list;
  2. data dependency, i.e. a topological sort of op writes → reads;
  3. the numeric prefix of the filename.
  Any disagreement between these sources becomes an open item.
- **Per dataset, with L = live ops** (first matching rule wins):

| Condition | Role |
|---|---|
| written by L and read by a later L | `intermediate` |
| written by L, never read later by L | `final` (confidence high if it is in an output-like folder or a doc lists it) |
| read by L, never written by L | `input`. If it is a `derived_copy` of another file, add `derived_from` and a manual step candidate. That source file becomes `input` of the manual step. |
| referenced only by superseded code, a project file or docs | `unused`, with `unused_kind`: `superseded_only`, `display_only` or `doc_only` |
| unreferenced data file in an output-like folder | `unknown` + `orphan_output` (possible missing branch, MP-27) |
| otherwise | `unknown` |
| referenced by L but not on disk | `exists:false` + `missing_input` (blocking) |

- `reference_candidate = exists ∧ role ∈ {intermediate, final}`.
- `role_state` is `structurally_inferred` when there is code evidence, otherwise `ai_inferred`. Conflicting evidence (for example a file in `05_TEMP` that no op writes) sets `role: unknown`.

**4.6 Profiling.**
- Rasters: Read windowed over `block_windows` with float64 accumulators, as in `LA/agent/comparators/raster.py:454-487` `_BandAccum` (SC/09 §7.1). Files over 2 GB are profiled with a stride over *all* blocks, labelled `strided`. Top-left sampling is what gave false empties (EP-26). Grid families: the reference is the input raster that most others align to; members record `((c-c0)/a, (f-f0)/e)`.
- Vectors: `pyogrio.list_layers`, then read each layer. Validity and reasons come from `shapely.is_valid_reason`. Area is measured in a LAEA centred on the bbox. Key candidates are columns that are unique and non-null.
- **Join coverage.** For every pair (code-read table column, vector column) used together by one step, or with ≥80% overlap after strip/casefold, scan records the match rate on both sides.
- `derived_copies`: vector pairs with equal count and an equal WKB-hash multiset. The record holds the schema diff and, for changed or added string columns, a value-mapping table that separates a common rule (prefix or suffix strip, case) from exceptions.

**4.7 Input map (`map.py inputs`).**
- **PNGs:** `inputs_overview.png`: every input dataset in one display CRS. That is EPSG:4326 when all inputs are geographic, otherwise the project CRS. Raster thumbnails use a 2–98 % stretch of computed stats; each vector layer is drawn as outlines in its own colour; bboxes are labelled with dataset ids; a km scale bar is added. `grid_corners.png`: the four corners plus the centre of each grid family at native resolution, with pixel lines and vector edges. Half-pixel shifts are visible here. `legacy_rasters.png`: small multiples of every legacy intermediate and final raster.
- **`extent_relations[]`** (computed in the scan): per pair of inputs, `within`, `area_outside_km2`, `features_outside` and overlap %. Also, per raster, `nodata_inside_aoi_km2` against the AOI layer (the dissolved boundary input).
- **The agent's checklist:** every input covers the AOI, with any slivers quantified; mixed CRS and units (degrees vs metres; nominal vs real cell size); grid families and offsets; layers reaching past the AOI; holes and NoData patches; partial thematic coverage; value ranges against units or legend.
- **Recording.** Each observation goes in through `scan.py annotate map-finding` as `{id, png, observation, metric{name,value,computed_by}, severity: info|watch|question, about[], open_item}`. A finding with no metric cannot be `info`. Visual findings never resolve anything; they only raise.

**4.8 What discover must surface in FX** (checked against the hidden PROVENANCE traps so the evidence exists; the agent never sees PROVENANCE):

| Finding (evidence) | Supports |
|---|---|
| All 6 rasters share one grid family (offset 0,0). 01 clip has `CROP_TO_CUTLINE`/`KEEP_RESOLUTION` False (01:47-48) and comment 01:37-39. Rasterize grids come from `extent_of`/`width_of(ds.01_pop_lux)` (02:56-68, 04:76-88) | grid trap 1 |
| `fixgeometries METHOD 1` (02:33); osm landuse has 13 invalid geometries; area computed in EPSG:2169 (02:27-41); `MIN_AREA` 2500 matches NOTES:22; `-at` (02:75); `NODATA` unset (02:70) and profile `02_res_mask` uint8, nodata=0, unique {1} | trap 2 |
| `03_pop_res` nodata −3.4e38. Expression `(pop>=0.5)*pop*mask` (03:33) with POP_MIN 0.5 (03:13). 03b twin diff: `Con(IsNull(mask),0,mask)` (03b:32) yields 0 where QGIS yields NoData | trap 3 |
| Both zonal ops (03:40, 03:49) use `ds.cantons_lux_edit` and `ds.03_pop_res`. 03b `ZonalStatisticsAsTable(cantons,"canton",…)` (03b:36) names the zone field | trap 4 |
| xlsx header row 4, data 5–16 (code 04:39), units cell B2, `"Wiltz "` A16 with `.strip()` at 04:42, ×1000 at 04:42 matching NOTES:28, B18 formula with no cache | trap 5 |
| `derived_copy` ADM1 → cantons_lux_edit (12/12 WKB). Value map: 11 prefix drops + 1 exception. Join coverage xlsx↔`canton` 12/12, xlsx↔`shapeName` 0/12. Evidence NOTES:15-19 | trap 6, manual step |
| Dtypes: final Float32 (RTYPE 5, 04:103), factor Float64 (DATA_TYPE 6, 04:90) | trap 7 |
| 03b superseded (NOTES:13, `_OLD`, 03b:2-3). Its env snap/cell/extent/outputCoordinateSystem (03b:23-26) and `workspace` (03b:15) are recorded. `work.gdb` items are missing but non-blocking | grid intent |
| ADM2 is display-only (`.qgz`). Industrial/commercial/retail are present in the landuse domain but filtered out (02:47; NOTES:29) → `unused_branch` | MP-27 |
| NOTES:4 says "100m"; grid is 3″ ≈ 60 × 93 m at the centre latitude → `doc_code_conflict` (info) | units |
| Map: 0.94 km² of canton area lies outside the WorldPop extent (west edge); 4 residential polygons fall outside the cantons; residential cells hold 46 % of population (273,489/588,220, metric from legacy `03_canton_pop` sums) | look first |

## 5 Reuse

| Source (lines) | Use | Fixes / notes | Tests |
|---|---|---|---|
| `LA/agent/project_detector.py:39-160` (GISTool, extension tables) | lift as data | add `.ipynb .model3 .qlr .xlsb .parquet .vrt .nc .sql .atbx`; drop `SKIP_DIRECTORIES` (154-158) and the hidden-dir skip (394) | extension table test |
| `LA/agent/project_detector.py:889-906` `_is_hardcoded_path` | lift | add UNC `\\host`, `/mnt`, `/Volumes`, `/dbfs`, `~` | path cases |
| `LA/agent/project_detector.py:761-834` `_infer_connections` | drop | "Excel in data folder → input" at confidence 0.5 is guessing | – |
| `OT/agent/workflow_parser.py:124-219` `ArcPyCallVisitor` | base for the ArcPy visitor | keyword arguments (219 reads only 5 positional); alias map; env assignments; `EnvManager`; drop the `"env"` substring filter (289) | `OT/…/test_workflow_parser.py` (209 lines) plus AK-31 five-style fixture |
| `LA/agent/data_flow_tracer.py:55-75,266-290` | lift table and `os.path.join` resolution | fixes per SC/09 §7.1: modern names, kwargs, dedupe visits (152-164), in-place tools | "6-line probe gives 4 unique flows" |
| `LA/parsers/python_parser.py` + `arcpy_patterns.py` (identical in `OT/`) | reference only | the regex fallback at :204 always captures `arcpy` (dead); `seen_patterns` (:188) keeps the first call per tool. SC/06 rates it "AST, 21 tests", but only function/import extraction is AST | port the 21 tests that concern imports and functions |
| `LA/parsers/factory.py:94-111` | pattern only | registers `.xls`/`.xlsb` to openpyxl, which cannot read them | – |
| `OT/parsers/excel_parser.py:145-215` (formula classifier, named ranges) | lift | add the `data_only=True` pass, merged cells, header candidates, calamine for xls/xlsb; external-ref regex (:60) returns index "1" | `test_python_parser.py` Excel cases |
| `OT/parsers/raster_parser.py` metadata block | lift fields | `_is_cog` (:134) uses `or`; add computed stats and grid family | – |
| `LA/agent/comparators/raster.py:454-487` `_BandAccum` | lift | float64, `valid_count`, NaN count (SC/09 §7.1) | 3-window equals whole-array |
| `RC/project-understanding.ts:27-30,329,356-377` | port to Python | relative-path rule, secret-name regex and manifest hash; exclude instead of refuse | – |
| `RC/migration-delivery.ts:8-16` `EvidenceState` | reuse enum | – | – |
| browser-scanner `parsers/qgis.ts`, `FE/lib/gis-parser/gis-operations.ts:106,126,146` (env property and QGIS prefix lists) | reference lists | TS regex parsers are not lifted | – |
| `FE/lib/aprx-parser/index.ts` 1-692 | **do not lift** (C1) | wrong CIM layout; circular tests via `createMockAprxFile` (:622) | – |
| fixture builder `lux-exposure-legacy.build/60_inventory.py` | test oracle pattern | used only by tests, never by the agent | AT-10 |

## 6 Acceptance tests

`tests/discover/test_lux.py` runs `scan.py FX --out tmp/migration`. The tests may read PROVENANCE as an oracle; the scan may not.

- **AT-1 read-only.** All 30 FX file hashes are identical before and after, with no new files (no `.aux.xml`). Under `sys.addaudithook`, every `open` event is under FX, the output dir, or the interpreter/site-packages. Nothing matches `PROVENANCE` or `.build`.
- **AT-2 completeness.** `files[]` has all 30 paths exactly once. The 4 `.aux.xml` files are `sidecar_of` their tif. `scan.mtime_reliable == false`.
- **AT-3 05_TEMP producers.** Each `produced_by` op has the exact `file:line` (call start / OUTPUT arg line):

| file | producing op |
|---|---|
| `01_lux_outline.gpkg` | 01:31/35 `native:dissolve` |
| `01_pop_lux.tif` | 01:40/54 `gdal:cliprasterbymasklayer` |
| `02_res_2169.gpkg` | 02:45/48 `native:extractbyexpression` |
| `02_res_4326.gpkg` | 02:51/54 `native:reprojectlayer` |
| `02_res_mask.tif` | 02:61/76 `gdal:rasterize` |
| `03_pop_res.tif` | 03:31/37 `native:rastercalc` |
| `03_canton_pop.gpkg` | 03:49/55 `native:zonalstatisticsfb` |
| `04_canton_factor.gpkg` | 04:46 `native:savefeatures` (create), plus in-place writes at 04:51 (`addAttributes`) and 04:72 (`changeAttributeValues`) |
| `04_factor.tif` | 04:79/94 `gdal:rasterize` |

- **AT-4 finals.** `lux_res_exposure_2020.tif` 04:97/106; `exposure_by_canton.gpkg` 04:109/115; `.csv` 04:126 (`open(...,"w")`); `.xlsx` 04:141 (`wb2.save`). Ids are `ds.exposure_by_canton_{gpkg,csv,xlsx}`. Each `OUTPUT` chain reaches `BASE` (e.g. 04:17→12→7).
- **AT-5 consumers.** `ds.01_pop_lux.consumed_by` ⊇ {02:56, 03:27, 03:40}. `ds.01_lux_outline` is consumed through the variable `outline` at 01:42 (`ToolOut`). `ds.02_res_2169` is consumed in the same step at 02:52. `memory_datasets` has 3 in s02 (02:30, 33, 42) and 1 in s03 (03:46).
- **AT-6 roles.** `input` = {lux_ppp_2020, cantons_lux_edit, osm_landuse_lux, value_per_capita, geoBoundaries-LUX-ADM1 (input of `m01_cantons_lux_edit`)}. ADM2 is `unused/display_only`. 9 `intermediate` and 4 `final`, all `reference_candidate`. Code: live {s01, s02, s03, s04} in that order (NOTES:8-11 agrees with the topological sort); s03b `superseded`, `twin_of` s03.
- **AT-7 manual step.** `derived_copies[0]`: ADM1→cantons_lux_edit, 12/12 WKB equal, `added:["canton"]`, `removed:["shapeGroup","shapeType"]`, rule `strip_prefix("Canton ")` ×11, exception `{"Canton Esch-sur-Alzette":"Esch/Alzette"}`, evidence NOTES:15-19. An `OI` of kind `manual_step` exists.
- **AT-8 settings.** 01 op2: `crop_to_cutline=false` (01:47), `keep_resolution=false` (01:48). 02 op6 and 04 rasterize: `extent`/`width`/`height` are `derived_from_dataset` `ds.01_pop_lux` / `ds.03_pop_res`; `all_touched=true` only for 02. `nodata` is `default_unset` at 02:70 and 04:88. 03b: `snapRaster`, `cellSize` and `extent` = `ds.lux_ppp_2020` (03b:23-25), `outputCoordinateSystem` = `Derived(spatialReference_of)` (03b:26), `workspace` (03b:15). All `from_superseded`.
- **AT-9 ArcPy detection on 03b.** Ops for: `ExtractByMask` (28, bare via star import) and `.save` (29, 34); `Con`/`IsNull`/`Raster` (32-33); `ZonalStatisticsAsTable` ×2 (36-37); `CopyFeatures_management` (41), normalised `management.CopyFeatures`; `AddField` ×2 in-place (42-43); `da.SearchCursor` ×2 read (46, 50) and `da.UpdateCursor` in-place (54). `work.gdb\*` datasets are `exists:false`, non-blocking. `legacy_roots` holds `C:\GIS\LUX_exposure` (commented, 01:7) and `D:\GIS\LUX_exposure` (03b:13). Separate synthetic file with the 5 AK-31 styles plus `import arcpy as ap` and `with arcpy.EnvManager(snapRaster=r, cellSize=10): …`: all are detected, and the EnvManager scope equals the `with` body lines.
- **AT-10 raster profiles.** The 6 rasters have one `grid_id` with offsets (0,0). `01_pop_lux`: valid 456,764, computed sum 588,219.87 ±0.01, `embedded_stats.approximate=true`. `02_res_mask`: uint8, nodata 0, `unique=[1]`. `res_m_at_centre` ≈ (59.9, 92.7) ±0.5. Values match `60_inventory.py` output for dtype, nodata and transform.
- **AT-11 vector profiles.** osm_landuse: 3,879 features, 13 invalid, `landuse` domain of 4. `02_res_2169`: 1,512 features in EPSG:2169. ADM2: 105 features (103 Polygon / 2 MultiPolygon). Every GPKG has `content_sha256`; a second legacy run (PROVENANCE Check 1) gives a different `sha256` and the same `content_sha256`.
- **AT-12 Excel.** As in §4.8 row 5. `header_state="resolved"` with corroboration 04:39. Join coverage xlsx→`canton` 12/12 after strip, →`shapeName` 0/12.
- **AT-13 docs.** NOTES:22 and :25 agree with 02:12 and 03:13. NOTES:28 agrees with 04:42. The NOTES:4 "100m" conflict is emitted as info.
- **AT-14 qgz.** Project CRS EPSG:2169. 7 data layers (Annotations ignored). Groups `results`/`working`/`input`. The `osm_landuse` subset equals the 02:47 `landuse` filter. Relative paths are resolved.
- **AT-15 map.** Three PNGs exist. `extent_relations`: cantons vs lux_ppp extent `area_outside_km2` = 0.94 ±0.05; residential polygons outside cantons = 4.
- **AT-16 determinism.** Two scans give byte-identical `inventory.json` except `scan.started_at`. `PYTHONHASHSEED=0` and `=1` give identical output.
- **AT-17 negative controls** (on a temp copy): deleting `03_INPUT/lux_ppp_2020.tif` gives a blocking `missing_input` with consumers `s01.op2` and `s03b`; adding `.env` gives `excluded`, never opened (audit hook); adding an unreferenced `04_FINAL/extra.tif` gives `orphan_output`.
- **AT-18 notebook fixture** (`tests/fixtures/messy_nb/`): exec counts [1,2,5,3,None] lead to `untrusted_notebook_outputs`; `%matplotlib` is blanked with line numbers kept; `!ogr2ogr out.gpkg in.shp` becomes an op with read/write; a checkpoint twin with one changed cell gives `twin_conflict` with the cell index.

## 7 Contradictions resolved

- **C1 aprx parser.** SC/06 ranks `FE/lib/aprx-parser` as reusable (#7). Code reading shows it reads `layers[].dataSource|path` and `map.xml` (`index.ts:132-160,244-260`), not CIM `CIMPATH=` / `dataConnection`, and its tests are self-fulfilling. **Rewrite in Python.**
- **C2 AST parsers.** SC/06 #8 says LA `python_parser.py` is "AST". Only structure extraction is AST; ArcPy detection is substring/regex with a dead fallback (:204). **Base on `OT/agent/workflow_parser.py` instead.**
- **C3 Granularity.** SC/09 §7.4 makes each tool call a step; PLAN wants one module per step. **Discover emits call-level `ops` grouped into script-level `step_candidates`.** Graph (02/03) owns the final granularity.
- **C4 Skipped folders.** Old scanners skip hidden and `env` folders; HL-25 and SC/03 need twins. **Include them, classified as twins.**
- **C5 mtime.** The brief asks for timestamps; FX mtimes are copy artefacts. **Corroboration only, with an explicit reliability flag.**
- **C6 Sidecars.** PROVENANCE leaves `.aux.xml` unlisted; the inventory must list everything. **Listed as sidecars carrying untrusted stats.**
- **C7 Secrets.** RC refuses a manifest containing secret-like names; messy folders contain them. **Exclude and record.**
- **C8 Nominal vs real cell size.** "100 m" in docs and names vs a 3″ grid. Report both and never rename.
- **C9 Lineage records.** SC/09 §1.3 `LineageRecord` is pipeline-stage level; ours is a dataset-level `ops` model (SC/09 §7.4).

## 8 Open questions for the founder

1. **Q1** Should discover optionally *run* the legacy code in a sandbox to get the runtime channel (files actually read and written, SC/08 §2.4)? Not needed for FX; useful when intermediates are missing.
2. **Q2** May we ship `qgis_algs.json` generated from QGIS 4.2 parameter metadata (GPL project; metadata only)?
3. **Q3** Profile time budget: is full windowed stats on every raster acceptable for the demo? It is small for FX. Strided above 2 GB?
4. **Q4** Is there any real (sanitised) `.aprx`/`.atbx` to validate the CIM reader? Without one, `.aprx` stays "best-effort" in the demo narrative.
5. **Q5** `.mxd` and `.tbx` (binary): list only, or attempt `arcpy`-free extraction later?
6. **Q6** Confirm that the legacy-output read ban (PLAN hook) exempts `scan.py`, `map.py` and `compare.py`, and applies only to `migration/pipeline/**`.

Not checked: GBrain (no auth). Server copies in `~/src/server-copies/*/_archive` duplicate LA (not diffed line by line). The old-tool tests were not executed.

## 9 Cross-section contract

**To lineage/graph (02/03).**
- `ops[]`: `id, code, file, line, end_line, api, tool, family, io_source, params{expr,value,chain,arg_line}, reads[{dataset,param,line}], writes[{dataset,param,line,mode}], comments, flags, state`.
- `step_candidates[]`: `{id:"s01_prepare_population", code, ops[], order, order_evidence[]}`.
- `manual_step_candidates[]`: `{id, inputs[], outputs[], evidence[], derived_copy}`.
- `memory_datasets[]`, `unresolved[]`, `twins[]`.
- Dataset ids `ds.<slug>`, stable across rescans.

**To investigation and interview.**
- `settings[]`, `params[]` (`used_at`, `doc_mentions`, `shadowed_by`, `dead_knob`).
- `docs[].mentions`, `code[].comments` / `commented_code`, `derived_copies[]`, Excel `header_candidates`, `extent_relations[]`, `map.findings[]`.
- `open_items[]` with `kind, severity, about, evidence, state`.

**To verify.**
- `datasets[]`: `id, legacy_path, layer, exists, role, reference_candidate, produced_by, grid_id, profile_ref, sha256, content_sha256, compare_hints{kind, categorical, key_candidates, sheet, header_row, volatile_bytes}`.
- Profiles hold the legacy structural baseline (CRS, transform repr, shape, dtype, nodata, schema, counts, layers, sheets) that `compare.py` checks first.

**To hooks.** `reference_candidate` paths (deny-list for generated code); `scan.read_only_verified`.

**Guarantees.**
- Every path is legacy-root-relative.
- Every fact carries file:line or a data-diff reference and an `EvidenceState`.
- Scan output is deterministic.
- `annotations` survive a rescan.
- Nothing outside the legacy root is read.
