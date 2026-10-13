# 04 — Migrate: generation order, partial migration and bridges, and the skill reference files

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Section owner: design subagent 04. Date: 13 Oct 2026. Status: proposal for the orchestrator.
Candidate-file evidence ids: EP (01), MP/MPV (02), IN (03), AK (04), FH/T (05), HL (07). Other ids: `06:NN` / `08 §` / `09 §` / `10 C` = context file and line or section; `GEN:NN` = archive `services/backend/app/ai/prompts/generation.py`; `scope.py`/`bridges.py` = archive `services/backend/app/ai/migration/`. I re-read every code reference marked "verified" myself on 13 Oct.
NDA: the real client migration is cited only by candidate ids and generalised rules. No client names, constants, file names or code appear here.

## 1 Scope and interfaces

**In scope:** the `migrate` skill (SKILL.md plus `reference/`). It turns an agreed workflow graph into `migration/pipeline/` (one module per step), `tests/` and `README.md`. It generates the code one step at a time, in lineage order, and gets each step checked before moving on. It handles partial migration with bridges, takes failures back from verify, and owns the curated skill reference files.
**Out of scope:** scanning (discover), the questions (interview), the runners (run), `compare.py`/`plot.py`, tolerances and verdicts (verify, section 06), hooks and the scanner (section 08), the report and Databricks files.

| Reads (never writes) | Owner |
|---|---|
| `graph.json` steps, datasets, evidence, open questions | discover / graph editor |
| `params.yaml` (value, source, reason, node), `decisions.md` | interview |
| `tolerances.yaml` (ids are referenced from the plan; migrate cannot edit it) | verify (06) |
| `inventory.json` (legacy files, hashes, layers, sheets) | discover |
| failure packet `runs/<id>/verify/failure.json` | verify (06) |

| Writes | Notes |
|---|---|
| `graph.json`: `steps[].plan`, `steps[].migrated`, `steps[].scope`, bridge steps | append-only edits plus a reason (08 §9.4 supersede rule) |
| `migration/plan.md` | rendered from `graph.json`; a view only, never edited |
| `pipeline/_axis_runtime.py` (vendored from the plugin), `pipeline/sNN_*.py`, `pipeline/run.py` | the model writes only the `sNN_*.py` step modules |
| `tests/`, `README.md` | the tests are labelled smoke tests (FH-26) |

## 2 Decisions

- **D4-1 The plan lives in `graph.json`; `plan.md` is rendered from it.** Each step gets a `plan` block (§3). There is one approval: the user approves the rendered plan before any `pipeline/` file exists. Evidence: PLAN "graph.json is the single source of truth" and "Plan … agreed before code"; the old app lost state at every handoff and never gave the DAG or the answers to the generator (`06:212`, `generation_input.py:391-392` sets `data_flow_graph=[]`, verified; `06:345` "make every handoff a file").
- **D4-2 Step boundary = a dataset the legacy run persisted.** A step is one legacy script or stage. Its outputs are every file it wrote. Memory-only layers are sub-operations inside the step. Reason: a step can only be verified at a boundary where a legacy artefact exists (`09 §6` "that boundary dataset is exactly what a per-step comparison needs"; MPV-03; the fixture's own `NOTES.txt` keeps `05_TEMP` "to check steps"). Inside a step, verify blames the first failing output, in write order.
- **D4-3 Code is generated in topological order. Each step goes: write, static gate, isolated run, verify. A full chained run follows.** The real migration converged on the same rule ("run tasks individually first", project instructions line 68, verified; 02 Part B), and `09 §7.4.5` names isolation mode. A failure in step N stops the walk at N.
- **D4-4 Isolated results are diagnostic only.** Only a chained run from raw inputs can move a step up the ladder past `sandbox-validated`. An isolated run proves the step's logic given legacy inputs. The deliverable runs without those inputs. Evidence: FH-25 (circular fixtures), HL-34 (circular evidence), `10 C1` `REFERENCE_NOT_INDEPENDENT`.
- **D4-5 The plugin ships a tested `_axis_runtime.py` and the model writes only step modules.** The runtime owns parameter access, path resolution, I/O recording, atomic writes, logging and exit codes. Evidence: invented helper modules were the #1 remediation blocker (FH-05, FH-09); hard-coded `'input'/'output'` strings between steps (Bug #108, `test_bug_108_hardcoded_paths.py:1-10`, verified); empty stub bodies (Bug #93, `test_bug_93_empty_stubs.py:1-12`, verified).
- **D4-6 No literals in step code.** Every threshold, distance, nodata value, CRS, scale factor, sheet range or path comes from `params.yaml` or `graph.json`. A required key that is missing fails the run; there are no `.get(k, default)` calls. Evidence: FH-17, FH-18 (DFIX-003 turned crashes into defaults), HL-26 (a default is not wiring), HL-36 (one owner per rule). Overrules the real migration's "ALWAYS use .get()" rule (project instructions line 113, verified; see §8 C6).
- **D4-7 Exit codes:** 0 = ok; 1 = step exception (goes to the repair loop); 2 = input, parameter or contract error (goes to the interview, not to a code fix); 3 = output contract violation (a declared output is missing or empty, or a write fell outside the manifest). `run_summary.json` is the only success record. Evidence: FH-01, FH-02, T1, T2; FH-21 (data and infrastructure errors were sent to code fixers).
- **D4-8 Runtime lineage comes from the runtime context, not from the model.** Every `ctx.input` and `ctx.write_*` call records dataset id, path, sha256, size and in-memory statistics. Verify diffs this against the graph. Evidence: lineage was never wired to verification (`09 §0.2`, `09 §1.4`); 08 §9.4 asks for runtime capture of reads and writes; MP-28 (bookkeeping re-reads caused out-of-memory failures).
- **D4-9 Repair takes verify's packet: the spec plus the current error only.** Migrate edits only the module of the blamed step (§3.5). Evidence: 05 "Recommended loop"; FH-20 (the outermost frame was blamed); FH-23 (544K-token prompts).
- **D4-10 Bridges are explicit graph steps.** A bridged dataset gets the status `BRIDGED`, never `PASS`. Converting a failing step into a bridge needs a recorded user decision. Evidence: `09 §6`/`§7.4.6`. The old scope modelled bridges by list adjacency (`scope.py:132-154`, verified), its import bridge crashes (`bridges.py:735`: `target / stem + '.ext'` is `Path + str`, a TypeError; verified), and no bridge checked its own conversion.
- **D4-11 Knowledge ships as 10 core reference files plus 1 target-only file.** Each file is at most 120 lines. Each entry is one rule with evidence and an eval. `SKILL.md` is a router (≤80 lines) keyed on legacy operations. Evidence: the bulk handbook made prompts worse (04 headline; LESSON prompt-growth files); PLAN "no bulk documentation dumps".
- **D4-12 An entry that serves no eval is deferred to the post-demo knowledge MCP, not shipped** (PLAN "entries that move no eval are deleted"). §6 lists them.
- **D4-13 Deterministic rules leave the prose.** If a rule can be checked by AST or by comparing outputs, it goes to the scanner (08) or `compare.py` (06). The reference file keeps one line saying why. Evidence: PLAN improvement table ("knew but didn't act → hook"); `10 B8` (prompting a model to be careful did not change acceptances; a code rule did).
- **D4-14 Skills never contain a fixture's answers.** Facts known only from `lux-exposure-legacy.PROVENANCE.md`, such as which QGIS clip options shift the grid or how QGIS 4 rasterise handles unset NoData, stay out of the skills, so the eval measures generalisation and not recall. Evidence: PLAN keeps the provenance file hidden; FH-25; `10 C5` ("comparator caught" is a different measure from "agent avoided").
- **D4-15 Generated code never opens legacy outputs.** In isolation mode the *runner* stages legacy copies of a step's *inputs* into the run manifest. The step cannot tell where they came from. Evidence: PLAN "generated code may not read legacy outputs"; T9/FH-35 (input manifest).
- **D4-16 Unimplemented means it raises.** A step that cannot be ported raises `NotImplementedError` and is marked `not_generated`. There are no `pass` bodies and no "TODO, log and continue". Evidence: Bug #93 (above); EP-28 (a dropped legacy filter that logged and continued); MP-27.

## 3 Generation order and artefacts

### 3.1 Preconditions (checked by the skill, which refuses to start otherwise)
The graph has no unresolved red node on the lineage of any final output; each one is resolved or explicitly assumed in `decisions.md`. Every `migrate` step has its inputs and outputs typed (`kind`, `measure`, CRS and nodata where known). `tolerances.yaml` exists and its hash is recorded (06). Import smoke test in the run environment (AK-36). `inventory.json` lists the legacy artefact for every persisted dataset.

### 3.2 Plan (written into graph.json, rendered to plan.md, approved once)
Per step:
```json
"plan": {
  "legacy_ops": [{"tool": "native:rasterize", "ref": "02_SCRIPTS/02_landuse_mask.py:71-87",
                  "settings": {"all_touched": true, "grid": "from ds.pop_lux"}}],
  "oss": [{"op": "rasterio.features.rasterize", "why": "cell assignment matches legacy option",
           "skill_refs": ["RV-1", "G-1"]}],
  "params": ["landuse.min_area_m2", "landuse.class", "mask.all_touched"],
  "outputs_checked": [{"dataset": "ds.res_mask", "family": "raster", "measure": "mask",
           "checks": ["grid", "nodata_mask", "values"], "tolerance_ids": ["tol-res-mask-v1"]}],
  "risks": [{"skill_ref": "N-2", "risk": "legacy mask NoData vs 0 changes downstream valid cells",
           "evidence": "params.yaml mask.nodata (source: legacy raster profile)"}],
  "isolation": {"possible": true, "legacy_inputs": ["ds.pop_lux"], "missing": []},
  "approved": {"by": null, "at": null}
}
```
Rules: every `legacy_ops[].ref` is a file:line in the legacy folder. Every `params` id exists in `params.yaml`. A risk cites a skill entry id or says `"none"`. The two raw parts of the plan are `isolation.missing` (inputs with no legacy copy) and `oss[].why`.

### 3.3 Step module contract (outline, not plugin code)
```python
STEP_ID = "s02_landuse_mask"
INPUTS  = ["ds.landuse_osm", "ds.pop_lux"]      # must equal the graph
OUTPUTS = ["ds.res_proj", "ds.res_geo", "ds.res_mask"]
PARAMS  = ["landuse.min_area_m2", "landuse.class", "crs.area", "mask.all_touched"]
def run(ctx):
    lu   = ctx.read_vector("ds.landuse_osm")    # path from the run manifest; read recorded
    minA = ctx.param("landuse.min_area_m2")     # missing -> ParamMissing -> exit 2
    grid = ctx.grid_of("ds.pop_lux")            # (crs, transform, shape) from the input
    ...
    ctx.write_raster("ds.res_mask", arr, grid=grid, nodata=ctx.param("mask.nodata"), dtype=...)
```
`ctx` checks that the write is on the declared grid (G-2) and that the declared nodata is set (N-6). It writes atomically (tmp file, then `os.replace`). It records `{dataset, path, sha256, bytes, stats{sum,count,nodata_count,min,max}}` into `runs/<id>/steps/<step>/io.json`. It refuses empty outputs unless the graph marks the dataset `may_be_empty` (MP-22). It logs through `logging` to stderr and `runs/<id>/logs/<step>.log` (MP-34). It raises on a read or write of a dataset the step did not declare.
`run.py` modes are `--all`, `--step S`, `--from S` and `--isolate S`. They build the manifest (dataset id → path) and never contain paths themselves.
Honest limit: Python audit hooks (FH-35) do not see opens made through GDAL's C library. Locally, enforcement is the ctx-only rule plus the scanner's ban on path literals. In E2B only manifest files are uploaded, which makes the guarantee hard.

### 3.4 Per-step loop (topological order)
1. Open the reference files that the router names for this step's legacy operations. Write `sNN_*.py`, and only that file.
2. Static gate (08 PostToolUse scanner: parse, undefined names, imports resolve to stdlib, the lockfile or the runtime, plus the rule ids in §10). A finding goes back to step 1.
3. **Isolated run** (`run.py --isolate S`). The runner stages the legacy copy of each input whose `produced_by` is upstream, never S's own outputs and never anything downstream. Inputs with no legacy copy (memory layers) come from the new upstream outputs, and the result is labelled `isolation: partial`.
4. Verify (06) compares every output of S against its legacy pair: structure, then values, then conservation. It also draws the plots. **The agent looks at the plots**, which can flag a result but never pass it.
5. FAIL: migrate receives the packet (§3.5) and fixes S only. PASS: S is marked `isolated-pass` and the walk continues.
6. After the last step: **chained run** (`run.py --all`, fresh `run_id`, raw inputs plus declared bridge inputs only). Verify walks every dataset in topological order. The first failing dataset blames its producer; failures downstream of it are reported as inherited (`09 §7.4.5`). If a step passed in isolation but fails chained, the cause is upstream drift inside tolerance that the step amplifies. That goes to verify as a tolerance-propagation question, never to a silent widening.
7. Write the tests (smoke tests on small slices), `README.md` and the step-status table in `graph.json`. The ladder: `generated` → `sandbox-validated` (chained run exit 0 in local or E2B) → `reference-verified` (chained compare PASS, set by verify only).

### 3.5 Receiving a failure (repair is owned by verify, 06)
Packet fields: `step_id`; immutable step spec (`plan` block, declared I/O with CRS, units, nodata and keys, the `params` used, allowed imports); **current failure only**: either the innermost traceback frame inside `pipeline/` plus about 40 lines around it plus the full module, or verify's structural mismatch table and reason codes (`RASTER_TRANSFORM_MISMATCH shift_px`, `NUMERIC_SCALE_FACTOR`, and so on) plus plot paths; and the scanner findings. No earlier attempts are included. Migrate may edit only `pipeline/<step_id>.py`; an edit to `params.yaml` needs an interview answer. Verify's non-LLM ledger stops the loop when the same signature survives two distinct patches, or when the class is not a code problem: exit 2, environment or credentials (05 loop items 4-5; FH-21, FH-22). Migrate never edits `tolerances.yaml`, legacy files or `compare.py`.

### 3.6 Lux example (what the plan should come out as; used in §7)
Steps: `s00_canton_names` (manual step, §4), `s01_prepare_population`, `s02_landuse_mask`, `s03_zonal_population`, `s04_distribute_value`. `03b_zonal_arcpy_OLD.py` gets `scope: drop`, reason "alternative implementation that did not produce the references". Its `Con(IsNull(mask),0,mask)` has different NoData semantics from the QGIS step that did produce them (L-5, N-7). Every persisted `05_TEMP` file is a dataset with a legacy pair, so isolation is possible for s02–s04. The landuse reprojection and fix-geometries layers in s02, and the first zonal pass in s03, exist only in memory; they are sub-operations.

## 4 Partial migration and bridges

**Model.** `steps[].scope ∈ {migrate, keep, drop}`, plus steps of `kind: bridge` with `direction ∈ {in, out}`. This replaces the old four-value enum (`scope.py:16-21`): a bridge is a step, not a status. `datasets[].origin ∈ {raw, produced, bridged}`. Bridge boundaries come from DAG edges where producer and consumer scopes differ, never from list order (`scope.py:132-154` bug).

| Case | Graph | Production behaviour | Verification |
|---|---|---|---|
| Manual step (hand edit, Excel template pasted into config, file renamed by hand) | `kind: manual`. The interview chooses **port-as-code** (a rule in `params.yaml`) or **bridge_in** | port: a generated step. bridge: the pipeline reads the human-made artefact from a declared external input | port: compare against the legacy hand-made artefact (normal PASS/FAIL). bridge: `BRIDGED` |
| Tool with no OSS equivalent (e.g. a MAXIMUM_AREA cell assignment, EP-10), or a licensed extension | `scope: keep` plus `bridge_in` for its outputs | legacy output becomes a pipeline input with its sha256 pinned | `BRIDGED`; downstream checked normally |
| Out of scope by owner decision | `scope: keep` | as above | as above |
| Migrated output feeding a kept legacy consumer (an Excel model) | `bridge_out`, format conversion declared | conversion step writes the legacy format | round trip: compare pre- and post-conversion with the rename map (`09 §7.7`), plus row count, CRS and hash |
| Dead or alternative code (`03b`, a fork, an abandoned helper) | `scope: drop` + reason | none | listed in the report as not migrated |

**Anti-fake rules** (code-enforced; owners in §10):
1. A dataset with `origin: bridged` cannot be compared against its own legacy file. `compare.py` returns `BRIDGED` (not a pass, and not counted as passed). The report front page lists every bridged dataset.
2. A final output whose lineage contains a bridge can reach `reference-verified` only with the qualifier `bridged: [ds…]`. The run headline then reads "partial migration (N bridges)", never "migrated".
3. Each bridge needs a `decisions.md` entry (reason: manual, no-equivalent, out-of-scope, or user choice; owner; date). **Changing a step from `migrate` to `bridge`/`keep` after verify has FAILED that step is blocked** unless the user approves (graph hook). This stops "bridge the failing step".
4. A bridge input is pinned by sha256 in `inputs.sha256`. If the artefact changes, the run is `EVALUATOR_ERROR` (`10 C3`).
5. A bridge never synthesises data, and a missing bridge artefact means exit 2 (P-2).
6. Isolation-mode staging (D4-15) is not a bridge. It never appears in a chained run.
**Lux:** `s00_canton_names` is the canonical case. `NOTES.txt` describes a hand edit (dropped prefix, one renamed canton) whose result is `cantons_lux_edit.gpkg`. Recommended default: port as code, with a rename map in `params.yaml` (source: `NOTES.txt` lines, plus a values diff between the ADM1 and edited files), verified against the legacy edited file. The other option is a bridge. The interview asks; the answer is recorded.

## 5 Skill reference files (`skills/migrate/reference/`)

Eval tags (from PLAN): **lux** (main case); small cases **E-buf** (buffer/clip/area), **E-sj** (spatial join with unmatched rows), **E-xl** (Excel lookup with a messy header), **E-rs** (raster resample/align); traps **T-shift**, **T-bil** (bilinear counts), **T-simp**, **T-branch**, **T-wm** (Web Mercator metres), **T-x1000**, **T-join**, **T-perf**; negative controls **N-miss**, **N-manual**. Form: **P** = prose stays; **S** = scanner rule (08); **C** = compare check (06); **R** = runtime ctx. Entry format in each file: `ID · rule (one line) · fails if ignored · evidence ids · eval`. No library tutorials.

**SKILL.md router** (≤80 lines): the workflow in §3, then a trigger table from legacy operation to file. Examples: `env.snapRaster|cellSize|extent`, clip/warp/rasterize options → F1; any resample, aggregate, Int() or scaling → F2; raster algebra, Con/SetNull/IsNull, masks → F3; rasterize, zonal, raster clip, extract by mask → F4; buffer, distance, area, CRS strings → F5; sjoin, select by location, near, joins, merges → F6; repair, dissolve, union, simplify → F7; any ArcPy or PyQGIS script → F8; xlsx, csv, ipynb, manual notes → F9; always → F10; Databricks or target files → F11.

**F1 `raster-grid-alignment.md`** (when two or more rasters are combined, or a raster is clipped, warped, windowed or rasterised)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| G-1 | Build one target grid (crs, transform, w, h) from the legacy reference raster and pass it to every read, warp and rasterise; never derive a grid from vector bounds | broadcast crash or silent sub-pixel shift | AK-16, EP-11, MP-03, IN-01, IN-16, FH-12, FH-13 | T-shift, lux | P+C+S |
| G-2 | Snapping bounds is not snapping data: a window read from a source whose origin or cell size differs (even in the 9th digit) has fractional offsets. Assert integer offsets, otherwise warp. Never trust "aligned" in a log | file written off the snap grid while the log says aligned | EP-05, IN-02, MP-03 | T-shift | P+R |
| G-3 | Translate the env, not the tool call: crop/mask/reproject without an explicit destination transform mints a new origin; a legacy env block may already have reprojected and snapped the output | values land in neighbouring cells | MP-03, IN-01, IN-16 | T-shift, E-rs | P |
| G-4 | Alignment is mandatory: a missing reference grid is an error, never an unsnapped fallback | green run, misaligned grid | MP-04 | T-shift | S+P |
| G-5 | Finer grids nest in the coarse grid: derive the fine transform from the coarse one; chunk sizes a multiple of the factor | blocks straddle coarse cells | EP-04 | E-rs | P+C |
| G-6 | Output extent belongs to the output family (declared), not the bbox of the data inside it; clip with the raster's own bounds | stacking/mosaic breaks, edge strips | EP-11, MP-18 | lux, T-shift | C+P |
| G-7 | Clamp windows by intersecting with `Window(0,0,w,h)` (offset *and* size) | zero-size reads, crashes far from the cause | AK-24, EP-23, MP-16 | E-rs | P |
| G-8 | Tiles: process with a halo, write the core only; count per cell from a VRT, not per tile | double counts, seams, partial edge cells | MP-17, IN-15, EP-17 | T-perf | P |

**F2 `resampling-and-counts.md`** (any resample, reprojection, aggregation, disaggregation, rounding or scaling of value rasters)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| R-1 | Every raster dataset gets a `measure` in the graph. Extensive counts are never resampled bilinear or cubic; nearest preserves sums only on identical or nested grids; otherwise use sum or area weighting and check totals | totals drift, counts smear | EP-06, MP-05, AK-23 | T-bil | C+S+P |
| R-2 | Resample changes cell size, not CRS; the default is nearest in ArcGIS and rasterio | wrong CRS, wrong resolution | AK-23 (old rule GEN:103) | E-rs | P |
| R-3 | Integer-scaled legacy outputs (×10^k stored as int): units and scale are a recorded decision in `params.yaml`, and every downstream rounding is revisited | factor-of-10^k parity gap; small values round to 0 | EP-01, EP-02, MP-06 (see §8) | T-x1000 | C+P |
| R-4 | ArcPy `Int()` truncates, `np.round` rounds half to even, legacy `Int(x+0.5)` is `floor(x+0.5)`; thresholds on `Int()` differ from thresholds on round | class flips, sum drift between modes | EP-03, IN-11 | T-x1000, E-rs | P |
| R-5 | Block aggregation keeps partial edge blocks; an all-NoData block stays NoData, not 0 | edge loss, new zeros | IN-03 | E-rs | P |
| R-6 | Each step records a value ledger (sum and count in and out, dropped with reason), computed from arrays already in memory; partition invariants fail hard | losses invisible until the end | EP-25, MPV-05, HL-06, MP-28 | lux, T-branch | R+C |
| R-7 | A fused or optimised expression replaces a step chain only after an output-equality run; any optimisation re-runs parity | "faster" changes the answer | EP-29, MP-13, AK-35 | T-perf | C+P |

**F3 `nodata-masks-dtypes.md`** (raster algebra, masks, Con/SetNull/IsNull, raster writes)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| N-1 | Read with masking (or sentinel→NaN) before arithmetic; `nan_to_num` leaves −9999 untouched; a polygon mask with a fill value fills only outside the polygon | sentinels summed as data | AK-13, EP-07 | lux | S(info)+C+P |
| N-2 | NoData propagation is part of parity: `Con` without a false branch gives NoData; SetNull gives NoData, not 0; any NoData input stays NoData | valid-mask mismatch, zonal means and counts shift | AK-14, MP-07 | lux | C+P |
| N-3 | Mask predicates exclude the mask's own nodata (255/65535 pass `> 0`); one nodata convention per mask family | NoData cells kept as "in mask" | EP-08, MP-07, MP-36 | lux | P |
| N-4 | CellStatistics-style sums ignore NoData by default and align inputs; `np.sum` over a stack does neither | value + sentinel | EP-09 | lux, E-rs | P |
| N-5 | Write profiles explicitly (driver, dtype, nodata, compression); `meta.copy()` from a VRT is wrong; match the legacy dtype (ArcGIS FLOAT = float32) | wrong dtype or nodata, huge files | MP-19, IN-11, IN-19 | lux | C+R |
| N-6 | `Con(IsNull(m), a, b)` decides per cell on `m`, not on whether `b` is valid; mask-replace ≠ coalesce | NoData treated as 0, or the reverse | IN-12, EP-08 | lux (03b vs 03) | P |

**F4 `raster-vector-conversion.md`** (rasterise, zonal statistics, raster clip, extract by mask)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| RV-1 | CELL_CENTER is `all_touched=False`; `all_touched=True` reproduces an explicit "all touched" option and is not MAXIMUM_AREA; burn on the target grid; `fill` = nodata unless legacy filled 0; overlaps are last-wins, so order by legacy priority | masks inflate or shrink; "no polygon" becomes 0 | AK-19, EP-10, MP-14 | lux, T-perf | P+C |
| RV-2 | ArcGIS raster Clip defaults to the rectangle; ExtractByMask is the polygon mask | blanked pixels vs legacy | AK-17 (old rule GEN:52) | E-rs | P |
| RV-3 | Zonal parity: cell-centre assignment (rasterstats `all_touched=False`); fractional coverage only if new numbers are accepted; aggregate duplicate zone ids; zones smaller than a cell return None | zone totals differ | AK-21 | lux | P+C |
| RV-4 | Rasterise the polygon on the target grid instead of reprojecting the raster and the polygon independently | pixels fall outside "their" polygon | IN-14 | lux | P |

**F5 `crs-distance-units.md`** (buffer, distance, area, any CRS literal)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| C-1 | ArcGIS linear-unit distances on geographic data are geodesic; geopandas uses CRS units; parse the unit string | buffers in degrees | AK-01, FH-10, EP-12 | T-wm, E-buf | S+R+P |
| C-2 | Distances in local UTM/AEQD or geodesically; never in 3857, Mollweide or any equal-area CRS; equal-area only for areas | ~0.67× or anisotropic buffers | AK-02, EP-12, FH-11, IN-13, MP-12 (§8) | T-wm, E-buf | S+C+P |
| C-3 | No degree↔metre constants (`/111320`, `/111000`) | ellipses, latitude bias | EP-13, MP-12, HL-12 | T-wm | S |
| C-4 | PyQGIS `native:buffer` uses layer units; a degree buffer in legacy is legacy behaviour, so it becomes `NEEDS_DECISION`, not a silent fix | "fixed" output fails parity, or a bug is kept unknowingly | AK-38 (inferred) | T-wm | P+C |

**F6 `vector-overlay-joins.md`** (sjoin, select by location, near, overlay, attribute joins)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| V-1 | ArcGIS SpatialJoin defaults to ONE_TO_ONE + KEEP_ALL + INTERSECT; `sjoin` defaults to inner and one-to-many. Set `how=`, aggregate per left index; `predicate=` (not `op=`) | dropped or duplicated rows | AK-05, IN-27 (old rule GEN:29) | T-join, E-sj | S+C+P |
| V-2 | Attribute joins: `merge(how="left", validate=...)` on normalised key dtypes; pandas `join(on=)` joins the right index; `rename({..})` without `columns=` renames the index | rows dropped, silent no-ops | IN-21, IN-22, IN-04 | T-join, E-xl | S+P |
| V-3 | Coordinate-string keys become integer grid indices from the shared transform; a nearest join that "fixes" non-matches hides misalignment | 0 matches become 0 counts | IN-04 | T-shift | P |
| V-4 | SelectLayerByLocation is a filter (rows ⊆ input, no duplicates); Near → `sjoin_nearest` in a projected CRS with `max_distance`; −1 vs NaN; ties | duplicates, wrong distances | AK-09, AK-10, IN-27 | E-sj | P+C |
| V-5 | `dissolve(dropna=False)`; `clip(keep_geom_type=True)`; overlay is pairwise and suffixes `_1/_2`; FID −1 becomes NaN | NULL groups vanish, mixed types, broken field refs | AK-06, AK-07, AK-08 | E-buf | P |
| V-6 | Replicate source-side filters the legacy relied on (geometry type, tag filter); deduplicate by feature id, never by geometry | stream lines buffered, tile duplicates kept | EP-16, EP-19 | lux, E-buf | P+C |

**F7 `geometry-validity-simplify.md`** (repair, dissolve, union, simplify, buffer chains)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| GV-1 | Repair with `make_valid`, then explode and keep the legacy geometry types; never `buffer(0)`; `method="structure"` only after checking the installed Shapely is ≥2.1 | lost rings, GeometryCollections, TypeError | AK-12, EP-15, MP-08 (§8) | lux, E-buf | S+P |
| GV-2 | Repair changes answers: write a ledger (invalid count, rows dropped, area before and after); dropping invalid rows is a decision, not a fix | silent area or count shifts | FH-33, MPV-07 | lux | R+C |
| GV-3 | Never simplify before buffering; a post-buffer tolerance must be ≪ d; keep the default segment count | triangular buffers, collapsed lakes | EP-14, MP-11 | T-simp | C+P |
| GV-4 | `coverage_union_all` only on valid, polygon-only, non-overlapping input (check `coverage_is_valid`); otherwise `union_all` | TopologyException or a wrong union | MP-09, EP-15 | lux | P |

**F8 `legacy-tool-semantics.md`** (reading any ArcPy or PyQGIS script before planning)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| L-1 | Every `arcpy.env.*`, `EnvManager` and tool-level env (including mid-script mutations that leak into later tools) becomes a `params.yaml` entry and grid field | outputs differ in CRS, extent or cell size with no error | IN-01, FH-13, AK-16, EP-11 | T-shift | P (discover extracts) |
| L-2 | `except ExecuteError: print(...)` wrappers exit 0; never port the swallow | green run, missing outputs | AK-32, FH-01 | N-miss | S |
| L-3 | `Exists`-skip caches are hidden state (they check another path, leave variables unbound, mask missing config); port them as pure steps | works only on the author's disk | 03 "Existence-check caches", AK-34, MP-29 | N-miss | P |
| L-4 | Comments, docstrings, migration docs and file names are claims: re-derive each from legacy file:line (a doc described a scale step the code never did) | invented behaviour reproduced | EP-27, EP-01 vs MP-06, HL-01, 03 "doc and test drift" | T-x1000, lux | P |
| L-5 | An alternative or "old" implementation of a step (other tool, fork, helper) is evidence of intent, not the spec: port the code that produced the references and diff the two semantics | NoData or threshold semantics from the wrong source | 03 "country forks", HL-25 | lux (03b) | P |
| L-6 | Every flag, `if` on config and input layer maps to a step or a `drop` decision | unported branch | EP-28, MP-27, HL-28 | T-branch | P+C (coverage) |
| L-7 | PyQGIS processing defaults are not ArcGIS's or Shapely's (buffer segments, join cardinality, layer units); when legacy is PyQGIS, the parity target is QGIS semantics | default-driven drift | AK-38 (inferred; evidence gap, §9 Q1) | lux, T-wm | P |

**F9 `tabular-excel-notebooks.md`** (xlsx, csv, notebooks, manual notes)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| T-1 | Excel cached values are the oracle; read formulas and values separately; `data_only=True` gives None for library-saved books | wrong numbers, None | AK-30 | E-xl, lux | P |
| T-2 | Declare the header row, data range, units cell, label trimming and check rows below the data (totals, AVERAGE) in `params.yaml` with a file:cell source; never infer them | header row read as data, units lost | AK-30, IN-23, 05 table rule 2 | E-xl, lux | P+C |
| T-3 | Code columns as strings: `keep_default_na=False` ("NA" is a code), strip `.0`, keep leading zeros; NaN dict keys never match | codes mangled, mappings miss | IN-22 | E-xl | P |
| T-4 | Pivots drop absent classes, so reindex to the full class list; nulls in categorical inputs fall into the `else` branch, so map and assert | KeyError or a wrong branch | IN-05, IN-06 | T-branch | P |
| T-5 | One canonical label table plus aliases; class lists come from live distinct values; match whole tokens, not substrings | bins dropped or misassigned | IN-08, MP-23, HL-03 | E-xl | P |
| T-6 | Intensive vs extensive: mean of shares ≠ share of totals; broadcast only ratios; `coerce` + `fillna(0)` changes meaning | totals multiplied, "n/a" becomes 0 | IN-28, IN-26 | lux | P+C |
| T-7 | A described filter is not the applied one, and zero rows is a finding: check predicate literals against the column domain and per-source NULL rates | wrong population, false "missing" | HL-01, HL-02, HL-04, HL-06 | E-sj | P |
| T-8 | A manual step (hand edit, template value pasted into config, file renamed between steps) is a graph step with a declared artefact: port it or bridge it (§4) | hidden loop, unreproducible output | 03 "Manual steps", IN-17 | N-manual, lux | P |
| T-9 | Notebooks: non-monotonic exec counts mean saved outputs are untrusted; port from code that is re-executed; choose the authoritative twin (.ipynb/.py/checkpoint) by content and ask | porting a state that never ran | 03 "Out-of-order execution", "Diverged twins" | proposed E-nb (§9 Q5) | P |

**F10 `pipeline-runtime-contract.md`** (always loaded when writing a step)
| ID | Rule | Fails if ignored | Evidence | Eval | Form |
|---|---|---|---|---|---|
| P-1 | Any failure exits non-zero; a broad `except` re-raises; never return `{"success": False}` | green partial runs | FH-01, FH-02, AK-32 | N-miss | S+R |
| P-2 | A missing input or parameter exits 2 naming the id; never synthesise, sample or fall back to another source or engine | plausible wrong outputs | AK-33, FH-35, IN-29, MP-04 | N-miss | S+R |
| P-3 | Parameters only via `ctx.param`; no `.get(k, default)`; defaults live in `params.yaml` with a source; every entry is read by a step | silent defaults, unwired parameters | FH-17, FH-18, MP-24, HL-26, HL-36 (§8) | lux, all | S+R |
| P-4 | Paths only via `ctx`; no literals, no `__file__`, no `exists()` control flow | runs only on the author's machine; reads legacy outputs | FH-14, HL-19, AK-26 | lux | S |
| P-5 | Imports: stdlib, lockfile or `_axis_runtime` only; never invent helper modules | ModuleNotFound loops | FH-05, FH-09 | all | S |
| P-6 | No skip-if-exists; every run writes a new `runs/<id>/`; atomic writes | stale outputs mask fixes; partial files skipped | MP-29, AK-34, HL-27 | all | R |
| P-7 | No stubs: an unimplemented step raises; no `pass` bodies, no "TODO, log and continue" | silent dropped branch | Bug #93 (`06:216`), EP-28 | T-branch | S |
| P-8 | Model-written tests are smoke tests; never mock geopandas, rasterio or numpy | green tests mistaken for parity | FH-26, FH-03 | all | S+report |

**F11 `target-runtime-io.md`** (loaded only when writing `databricks/` or when a target is a cloud mount; it serves the Databricks file-lint acceptance in the target section, not a parity eval)
X-1 FUSE/object mounts cannot seek: write GTiff/GPKG/XLSX to `tempfile.mkdtemp()` then copy; stage GPKG reads locally (AK-26, EP-20, MP-01, MP-02, FH-15, IN-33; S). X-2 `__file__` is undefined and workspace files become notebooks: take paths from task parameters (EP-21, MP-31, FH-19; S). X-3 The job must run the committed code: Git source pinned, commit logged at task start (MP-30, EP-21). X-4 One lockfile for sandbox and target; never upgrade core packages inside a managed runtime ("numpy<2" is not the rule) (AK-36, FH-04, FH-22). X-5 Persist geometry as WKB/GeoParquet, not WKT (AK-28). X-6 COGs via `driver="COG"`; VRT sources absolute (AK-25, AK-29).

## 6 Dropped candidates and the 32 wrong old rules

**Moved elsewhere (not skill prose):** syntax, truncation and Unicode problems FH-06/07/08/37 → scanner C1–C3; remediation infrastructure FH-20/21/22/23/24 → verify loop (06); comparator design FH-25..32, MPV-01..11, EP-26, MP-20, IN-30, HL-22/23/29/30/31/32/34 → verify (06); AK-31 call styles, EP-18, MP-26, HL-16/18/24/25 and the discover patterns in 03 → discover; AK-39, EP-30, and the question lists in 03 and 07 → interview; HL-08/09/33/35 provenance and manifests → run; MP-37 → plugin CI.
**Deferred to the knowledge MCP (evidence is fine, no eval yet):** memory and scale EP-22, EP-24, MP-10, MP-15, MP-33, MP-36, FH-36, AK-35 (the lux fixture is small); AK-04 datums (inferred); AK-11, AK-15, AK-18/IN-32 (mosaic), AK-20, MP-25, IN-31, AK-22 terrain, AK-27 FGDB domains, IN-07 codeblock weights, IN-09/10 legends and value domains, IN-18, IN-20 field case and truncation, IN-24/25 SQL, AK-37 STAC (EO domain), HL-05, HL-10, HL-13.
**Generic or out of domain:** the mapping-table rows a strong model already knows (AddField, Erase, Merge, Project, MultipartToSinglepart …; 04 says so itself); MP-35 (double `del`); HL-11/14/15/17 (imagery and heading semantics); HL-20/21 (machine moves and warehouse quotas).
**Wrong (never enter skills):** all 32 rows of 04 "Wrong or outdated old guidance". ✓ = I re-read the source line on 13 Oct; (≈) = right content, line drifted.

| # | Wrong rule | Source | Correct rule | Check |
|---|---|---|---|---|
| 1 | Buffer in EPSG:3857 as "metric" | GEN:1732 | C-2 | ✓ |
| 2 | Buffer in ESRI:54009 | GEN:2006 | C-2 | ✓ |
| 3 | `buffer(0)` repair vs "NEVER buffer(0)" | GEN:524 vs 2203 | GV-1 | ✓ |
| 4 | `make_valid(method='structure')` as universal | GEN:2203,2211 | GV-1 | ✓ |
| 5 | richdem `attrib='slope'/'hillshade'` | GEN:54-55 | deferred AK-22 | ✓ |
| 6 | Resample → `rioxarray.reproject('EPSG:4326')` | GEN:103 | R-2 | ✓ |
| 7 | Raster Clip → `rasterio.mask.mask` | GEN:52; arcpy-translation skill table | RV-2 | ✓ |
| 8 | Mosaic → `merge()` with no method | arcpy-translation skill table | deferred AK-18 | ✓ |
| 9 | SpatialJoin → `gpd.sjoin(left, right)` | GEN:29, 2365 (≈30, 2367) | V-1 | ✓ |
| 10 | SelectLayerByLocation → `gpd.sjoin()` | GEN:38 (≈34) | V-4 | ✓ |
| 11 | Near → `apply(distance.min)` / cKDTree | old `arcpy_patterns.py:44-46` | V-4 | ✓ |
| 12 | "ArcPy uses Lat, Lon" | ERR-007:40 | AK-03 → scanner H-12 | ✓ |
| 13 | "ArcPy Buffer implicitly reprojects" | ERR-014:46-47 | C-1 | ✓ |
| 14 | Always R-tree prefilter before clip; `prep()`+apply | RAG/GEOMETRY_PATTERNS.md:58-77 | deferred AK-35 | ✓ |
| 15 | `numpy>=1.24,<2.0` as generic | GEN:535 | X-4 | ✓ |
| 16 | "Use Fiona" for GDB/GPKG | GEN:72,86,87 (≈69,76) | pyogrio default (generic) | ✓ |
| 17 | `gdf.unary_union` | PAT/buffer_spatial.py:20 | `union_all()` → scanner H-05 | ✓ |
| 18 | Hand UTM zone `int((x+180)/6)+1` | PAT/buffer_spatial.py:23 | `estimate_utm_crs()` (C-2) | ✓ |
| 19 | `write_cog_with_stats` = COG | GEN:2296-2333 | X-6 | ✓ |
| 20 | Hand VRT XML from numpy dtypes | GEN:2271 region | X-6 | not re-read |
| 21 | Persist geometry via `to_wkt()` | GEN:1717-1727 | X-5 | ✓ (1721) |
| 22 | "Create synthetic data if input missing" | GEN:540-576 | P-2 | ✓ |
| 23 | "Divide S2 bands by 10000" | git 3a12c5db6 | deferred AK-37 | not re-read |
| 24 | stackstac `epsg=3857` default | git 3a12c5db6 | C-2 | not re-read |
| 25 | SCL cloud mask `[4,5]` vs `[4,5,6]` | GEN:99 | deferred | ✓ |
| 26 | Databricks serverless "CANNOT use PySpark", "dbutils not available" | GEN:327 | target section | ✓ |
| 27 | Databricks Labs Mosaic for H3 | GEN:3247 | target section | ✓ |
| 28 | Glue "--extra-jars includes Sedona" (also GEN:1547 vs 588 contradict) | GEN:3245 | target section | ✓ |
| 29 | `/local_disk0/` vs `/tmp/` staging | GEN:2214 vs 1623 | X-1 (`tempfile`) | ✓ |
| 30 | "NEVER `from osgeo import gdal`" as universal | GEN:2202 | platform-scoped only | ✓ |
| 31 | Fabricated "40 CFR § 123.45" citation | GEN:3407 | evidence rule (D4-11) | ✓ |
| 32 | "99.9% faster" table | GEN:174-180 | none (marketing) | ✓ |

Also wrong and found during this pass, not in 04's list: the archive skill maps `JoinField_management` → `gdf.merge(other, on=key)`, which is an inner join by default (arcpy-translation skill table; V-2), and `Near_analysis` → `gdf.distance(other)` + custom logic (same table; V-4). The real migration's "always `.get()`" config rule is overruled by P-3.

## 7 Acceptance tests (migrate skill on `lux-exposure-legacy`; headless, 3 repeats)

| # | Test | Pass condition |
|---|---|---|
| A1 | Plan before code | every `migrate` step has a `plan` block with file:line `legacy_ops`, `params` ids and `tolerance_ids` before the first `pipeline/s*.py` exists (file mtimes and the hook log); `plan.md` rendered; approval recorded |
| A2 | No literals | scanner: zero path literals, and no threshold, scale, nodata or CRS literals in `pipeline/s*.py`. The area threshold, population minimum, kEUR→EUR factor, sheet range, nodata values and CRS codes all sit in `params.yaml`, each with a legacy file:line or `NOTES.txt` source |
| A3 | Parameters are wired | changing the area threshold in `params.yaml` changes `ds.res_proj` and the final raster (answers change output, `06:346`); every `params.yaml` entry appears in some step's `io.json` param-read log (HL-26) |
| A4 | Isolation staging is honest | `--isolate s03` manifest holds only the legacy copies of s03's inputs (`01_pop_lux`, `02_res_mask`) plus raw inputs; no `03_*`/`04_*` legacy path; in E2B those files are not uploaded |
| A5 | Blame localisation (seeded) | mutant s02 with cell-centre rasterise: isolated s02 FAIL (values or valid mask), isolated s03 PASS (fed the legacy mask), chained s03/s04 FAIL marked "inherited from s02". Mutant s01 with its output shifted one cell: s01 FAIL `RASTER_TRANSFORM_MISMATCH shift_px` |
| A6 | Chained parity | `run.py --all` from raw inputs (plus the declared s00 decision) exits 0; verify reports all `04_FINAL` and `05_TEMP` pairs PASS under `tolerances.yaml`; ladder at `reference-verified` |
| A7 | NoData semantics | s03 and s04 valid masks equal legacy (NoData outside residential cells, not 0); the plan or decisions cite N-2/N-6 and note that `03b` differs (L-5) |
| A8 | Manual step, port | user answers "port": s00 builds the join field from ADM1 using a `params.yaml` rename map; compared against `cantons_lux_edit.gpkg` → PASS; the xlsx join drops no canton (T-join) |
| A9 | Manual step, bridge | user answers "bridge": `ds.cantons_edit` shows `BRIDGED`; the final outputs read `reference-verified (bridged: ds.cantons_edit)`; the headline says partial migration |
| A10 | Bridge cannot fake | (a) with s04 forced to fail, the agent's attempt to set `scope: keep` on s04 is blocked without a user decision; (b) a dataset produced by a bridge never gets PASS from compare, even when byte-identical |
| A11 | Dead code | `03b_zonal_arcpy_OLD.py` is `scope: drop` with a reason; no `arcpy` import in `pipeline/` |
| A12 | Missing input (N-miss) | with the xlsx removed from a copy: exit 2 naming `ds.vpc_xlsx`, no final outputs, status unchanged, no synthetic data |
| A13 | Repair discipline | when a failure packet arrives, the patch touches only `pipeline/<blamed step>.py`; when the same signature survives two patches, the agent stops and asks |
| A14 | Runtime lineage = graph | each step's `io.json` reads and writes equal the graph `INPUTS/OUTPUTS`; any mismatch is reported, never dropped |
| A15 | Skill routing (measured, not gated) | the transcript shows F1, F3, F4 and F9 opened before s01–s04 are written; logged per repeat for the improvement loop |

## 8 Contradictions resolved

| # | Topic | Conflict | Resolution |
|---|---|---|---|
| C1 | Metric CRS for buffers | MP-12 "local metric or equal-area"; IN-13 utility in EPSG:6933; GEN:2006 Mollweide; GEN:1732 3857 vs AK-02, EP-12 (measured 66–152 m for a 100 m buffer), FH-11 | C-2: distances only in UTM/AEQD or geodesic; equal-area only for areas |
| C2 | Geometry repair | EP-15 and MP-08 snippets use `make_valid(method="structure")`; GEN:524 `buffer(0)` vs GEN:2203 "never" vs AK-12 (needs Shapely ≥2.1) | GV-1: `make_valid`, version-gated `method=`, type filter, ledger; `buffer(0)` banned (scanner) |
| C3 | Legacy scale factor | MP-06 "divided at the end" (from migrated-side docs) vs EP-01 (legacy code never divides) and EP-27 (docs cite lines that do not exist) | EP-01 wins: code over docs (L-4); units are an interview decision (R-3) |
| C4 | Count resampling | MP-05 "legacy used nearest" vs EP-06 "nearest is not sum-preserving either" | R-1: nearest only on identical or nested grids, otherwise sum-preserving; totals checked |
| C5 | Mosaic order | AK-18 `method="last"` vs IN-32 `method="first"` with priority | both equivalent if order and method are explicit and reproduce legacy precedence; deferred entry |
| C6 | Config access | real migration "ALWAYS use .get()" (project instructions:113) and 04 "`.get()` on optional config" vs FH-18 (DFIX-003 masked missing keys) | P-3: required keys fail; optional ones have a `params.yaml` default with a source; no code defaults |
| C7 | Simplify for speed | the migration's job file "simplify BEFORE buffering" (EP-14), AK-35 "simplify early" vs EP-14 fix history and MP-11 | GV-3 + R-7: simplification is an output change and needs parity |
| C8 | Two-stage write location | EP-20/MP-01 `/local_disk0`; GEN:2214 vs GEN:1623 `/tmp` | X-1 `tempfile.mkdtemp()`; checkpoints that must survive restarts not on ephemeral disk (EP-20) |
| C9 | Lineage isolation vs "code never reads legacy outputs" | `09 §7.4.5` isolation vs the PLAN rule | D4-15: the runner stages, the code is unaware; isolation is diagnostic (D4-4) |
| C10 | Partial migration model | `scope.py` four-value status vs `09 §7.4.6` "bridges as own steps" | §4: `scope` ∈ {migrate, keep, drop} + bridge steps |
| C11 | Number of reference files | 04's 12-file split vs the brief's list | 10 core + 1 target-only (§5), merged by when the agent needs them |
| C12 | ArcPy axis order | ERR-007:40 "Lat, Lon" vs AK-03 | AK-03 (x = lon); the scanner checks `always_xy` |

## 9 Open questions for the founder

1. **PyQGIS evidence gap.** The demo legacy is PyQGIS, yet our only PyQGIS entry is AK-38 (inferred, "gap" in 04). Should we run a one-off evidence pass on a *separate* synthetic QGIS case (rasterise NoData default, clip resolution options, zonal assignment) to add entries? Using the lux fixture would contaminate the eval (D4-14). Without the pass, lux traps are caught by verify, not avoided by skills. That may be the more honest demo.
2. **Default for the lux manual step:** port as code (recommended) or bridge? Either way the interview asks.
3. **Plan approval:** a hard gate (a hook blocks `pipeline/` writes until `approved`) or a soft one for demo pacing?
4. **Isolation without persisted legacy intermediates** (memory layers): accept chained-only blame, or re-run the legacy with extra persisted intermediates? That needs the legacy tool; possible for lux with QGIS, rarely for customers.
5. **Notebook eval:** add a small E-nb case, so T-9 earns its place, or defer T-9?
6. **`keep` steps in the demo**, or `bridge_in` only?
7. Confirm that the ~30 deferred entries (§6) go to the post-demo knowledge MCP with their evidence.
8. Exit-code numbers and `run_summary.json` fields need agreeing with the run section.

## 10 Cross-section contract

| To / from | Item | Detail |
|---|---|---|
| discover (in) | `graph.json` steps/datasets | needs `datasets[].measure`, `origin`, `legacy.{path,sha256,persisted}`, `may_be_empty`; steps with legacy file:line; env extraction (L-1), call-style detection (AK-31), branch and flag inventory (L-6), manual steps (T-8), dead or alternative code (L-5) |
| interview (in) | `params.yaml`, `decisions.md` | port-or-bridge per manual step; units and scale (R-3); legacy-defect keep-or-fix (C-4); every param has `source` + `reason` + `node` |
| run (both) | `run.py` modes and staging | `--isolate S` stages only legacy *inputs* of S; E2B uploads the manifest only; `run_summary.json` + per-step `io.json`; exit codes per D4-7; fresh `run_id` every time (P-6) |
| verify 06 (in) | failure packet | §3.5 fields; innermost in-bundle frame; structural table + reason codes; ledger stop rules |
| verify 06 (out) | compare checks from skills | grid/transform gate with `shift_px` (G-1/2/6, T-shift); extensive-sum conservation + non-integer counts (R-1, T-bil); `NUMERIC_SCALE_FACTOR` (R-3, T-x1000); valid-mask XOR + nodata value (N-2, N-5); dtype (N-5); Hausdorff/symdiff in metres (GV-3, C-2, T-simp, T-wm); row count, key sets, null joins (V-1/2, T-join); `BRANCH_NOT_EXERCISED` (L-6); `OUTPUT_CHANGED_BY_OPTIMISATION` (R-7); repair-ledger check (GV-2); `BRIDGED` status (§4 rule 1); `NEEDS_DECISION` for legacy defects (C-4) |
| hooks/scanner 08 (out) | rules from skills | H-04 `op=`; H-05 `unary_union`; H-06 `buffer(0)`; H-07 `make_valid(method=)` with Shapely <2.1; H-08 `sjoin` without `how=`; H-11 3857/Mollweide/equal-area before buffer/distance; H-12 Transformer without `always_xy`; H-13 unmasked read + arithmetic (info); C3 degree↔metre constants; C5 imports; C6 broad except / exit; C8 path literals, `__file__`; C9 RNG/synthetic/fallback; C10 `ctx.param` ids ⊆ `params.yaml`, no `.get` on params; C11 multi-raster algebra without a grid from `ctx`; explicit `resampling=` arg; `.rename({` without `columns=`; `.join(on=`; `pass`/TODO step bodies; mocks of geo libs in tests; graph hook blocking migrate→bridge after a FAIL without a decision; plan-approval gate (Q3) |
| report | bridges and scope | bridged datasets, `drop` steps with reasons, "partial migration" headline, isolation vs chained results shown separately |
| knowledge MCP (later) | deferred entries | §6 list with evidence ids |
