# 06 Verify — per-step parity proof (design)

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Status: design, 13 Oct 2026. Owner of `skills/verify/` (`SKILL.md`, `compare.py`, `plot.py`), `tolerances.yaml`, `known-differences.md`, `runs/<run_id>/{contract.json,compare/,plots/,visual-review.json,verdict.json}`.
Evidence keys: **08** = skill-candidates/08 (GBrain history), **09** = 09 (code-level lineage/verify, probes), **10** = 10 (July proof + Arena), **05** = 05 (failure history), **EP/MP/IN** = 01/02/03 entries, **PROV** = `fixtures/lux-exposure-legacy.PROVENANCE.md`, **M** = my measurements on the lux fixture (13 Oct; scripts in session scratchpad, qgis env: rasterio 1.5.2, geopandas 1.2.0, shapely 2.2.0, numpy 2.5.4). `AG/`, `BE/` = `~/src/axis-spatial/_archive/services/{legacy-service-archive/agent/axis_spatial/agent,backend/app}/`; `SC/` = `~/src/server-copies/`.

## 1 Scope and interfaces

Verify proves, step by step, that the new pipeline reproduces the legacy outputs **and intermediates**, using deterministic checks, and gives the agent and the human maps that show what numbers hide. It does not generate code, run pipelines (section run), own the graph (section 02) or render the final report (section report).

| In (read-only for verify) | Owner | Out (written by verify) |
|---|---|---|
| `migration/graph.json` steps, datasets (`legacy_path`, `new_path`, `produced_by`, `compare{}`), edges with evidence | 02 | `runs/<id>/contract.json` (frozen contract + `contract_hash`) |
| `migration/params.yaml` (e.g. `measure_crs`, zone rule) | 03/interview | `runs/<id>/compare/<dataset_id>.json` (§3.1) |
| `migration/tolerances.yaml` (§3.2), `known-differences.md` (§3.3) | **06**, values agreed with owner at Plan | `runs/<id>/plots/<dataset_id>/*.png` + `plots.json` |
| `runs/<id>/manifest.json` (outputs, sha256, `written_by_step`, mode, env) | run | `runs/<id>/visual-review.json` (model receipts, §4h) |
| legacy folder (read-only), `migration/references.json` (legacy sha256 inventory) | 02 writes at scan; 06 checks | `runs/<id>/verdict.json` (dataset → step → pipeline, blame, ladder) |

CLI (deterministic; non-zero exit on anything but PASS_*): `compare.py --run <id> [--dataset ds.x]`, `compare.py --aggregate --run <id>` (merges visual receipts, writes `verdict.json`), `plot.py --run <id> [--dataset]`, `plot.py review-add --run <id> --json finding.json` (schema-validated append; the only way a model writes review findings).

## 2 Decisions

- **D6-1 One verdict vocabulary everywhere** (check, dataset, step, pipeline): `PASS_EXACT, PASS_WITHIN_TOLERANCE, FAIL, NEEDS_DECISION, NEEDS_REVIEW, NOT_COMPARED, BLOCKED, EVALUATOR_ERROR` (§3.1). No WARN, PARTIAL, score or percentage band. Evidence: every old WARN band let wrong output through — `AG/comparators/raster.py:188-196` (<1 % pixels = WARN), vector 5 %, E2B PARTIAL ≥60 % "avoids being called FAIL" (09§3.3); `BE/ai/graph/flow_control.py:148-168` treats WARN/SKIPPED as pass and "proceeds to documentation anyway" after 2 retries; 08§8.3; 10 C1.
- **D6-2 Pairing comes from graph lineage only.** A pair is a graph dataset with both paths and an evidenced `produced_by`; there is no stem, extension, fuzzy-prefix or positional fallback, and legacy *inputs* can never be a reference. Unpaired = explicit verdict. Evidence: P2 paired session *inputs* with outputs and zipped leftovers by position (`BE/routes/verify.py:306-331, 514-530`, `match_method:"order"` at 529); UI relabelled position pairs as name (`useVerification.ts:91-93`); P1 fuzzy prefix pairs `roads_2019`↔`roads_2020` (09§2.2); 06 Step 5 "do not inherit".
- **D6-3 Structure gates values; nothing is warped to make a comparison possible.** CRS → shape → transform must pass before any per-cell value is compared; the candidate is never reprojected, resampled or cropped to the reference (no intersection compare). Evidence: Arena `verifier.py:83-90` grid gate (10 B2.1); intersection compare passed a half-extent raster (`raster.py:160-173`, probe 09§3.6); silent `out_shape` resampling (09§3.1); 05 principle "never fix up a structural mismatch"; B10.1 silent candidate reprojection.
- **D6-4 Grid tolerance = 1e-6 pixel on origin and on the far corner.** Evidence: PROV inventory — every lux raster is bit-identical to the WorldPop transform; the `KEEP_RESOLUTION` trap moves it by (+0.5, −0.4997) px, M: max deviation 0.49999 px; EP-05 shows cell sizes 0.00083333334 vs 0.000833333333 (rel 8e-9) that only show at the far edge, hence the corner term; Arena default `1e-6·max(|a|,|e|)` (10 B2.1). 09§7.6 "1 % of a pixel" and 05 "1e-9·res" are superseded (§7).
- **D6-5 Masks before values; values only on cells valid on both sides; NaN never equals a number.** Evidence: all-NaN raster PASSED C2 (`BE/ai/verify/comparator.py:716`, NaN > tol is False); `except Exception: pass` turned read errors into equal NaN windows (`raster.py:360-362`); `fillna(0)` made null≡0 (`comparator.py:503-504, 859-860`); FH-28, T11-T13.
- **D6-6 Default exact. A tolerance exists only as a record with two measured numbers** — the spread of a correct independent implementation (lower bound) and the distance of the nearest wrong recipe (upper bound) — plus owner and date, fixed before the run. Evidence: Arena AUTHORING.md:50-52 and worked examples (10 B3); "never loosen a tolerance to make a run pass" (PLAN); 08§8.4-8.5 (defaults in degrees vs metres, nobody owned the numbers); C1 0.01 absolute too loose/tight (09§3.1). M shows the lux numbers are measurable in minutes (§4 table).
- **D6-7 Conservation is its own check family and is necessary, not sufficient.** Totals overall and per zone run even when the grid gate fails (they are grid-independent). Evidence: the −tap half-cell shift keeps the population total at 588,219.87 (PROV) — only the grid gate catches it; bilinear count smoothing changes canton totals by up to 4.5 % (M) — conservation catches it; a wrong residential mask leaves per-canton exposure totals intact *by construction* (`factor = tot_value / res_sum`, `04_distribute_value.py`), so only the mask/value checks catch it. EP-06, MP-05, EP-25.
- **D6-8 Vectors and tables are matched on a declared key; geometry matching only when declared; never by row order.** Evidence: probes "renamed+wrong attributes PASS", "dropped column PASS", "reversed order FAIL (false)" (09§3.6); FH-29; 06 §"takeaways" 5.
- **D6-9 Spatial distances/areas are measured in a declared local metric CRS** (`params.measure_crs`, lux: EPSG:2169), never degrees or EPSG:3857. Evidence: `SC/gis-agent-arena/worker/api/src/verifiers.ts:60` measured buffers in 3857 and rewarded the wrong answer (10 B2.4); FH-10/11; M: an area filter in 3857 keeps 2,085 polygons instead of 1,512; in degrees keeps 0.
- **D6-10 The reference is the legacy run's own files, hash-pinned.** `references.json` records sha256 (plus content hash for GPKG/XLSX, whose bytes change per run, PROV Check 1) at scan time; a mismatch is `EVALUATOR_ERROR/REFERENCE_HASH_MISMATCH`. Outputs of the new pipeline, model-written fixtures or "from script logic" references are refused (`REFERENCE_NOT_INDEPENDENT`). Evidence: circular fixtures FH-25/T7; July parity compared caller-supplied JSON (10 A5); Arena `verify_inputs` (10 B2.1).
- **D6-11 The contract is frozen at run start and immutable during the run and the repair loop.** `contract_hash` = sha256(canonical {tolerances, known-difference rules, pairing projection of graph, references, comparator id+sha}); recomputed at compare; mismatch → `EVALUATOR_ERROR/TOLERANCE_CHANGED_AFTER_RUN`. Changing a tolerance = new record version + rerun; the old run stays. Evidence: 08§8.5 "silent tolerance inflation"; 10 C2.6-7; Arena "version, don't edit" (`SC/gis-agent-arena/docs/METHODOLOGY.md:32`).
- **D6-12 Blame uses chained and step-isolation runs; only a chained run from raw inputs can make the pipeline `reference-verified`.** Isolation runs feed legacy intermediates, so they localise defects but prove nothing end to end. Evidence: 09§7.4.5; PLAN "the first step that fails is where the defect is"; in the client migration every output-changing defect was found on intermediates by a human (brief; EP-04/05/06/14).
- **D6-13 Repair = spec + current error only, ≤3 attempts per step, no "proceed anyway".** Evidence: 05 §Remediation (0/3 platforms fixed; 544K-token prompts; history did not help); T5; `flow_control.py:164-168`.
- **D6-14 Plots are deterministic derived evidence, generated from the compare result and both files; never a verdict input.** Full-resolution worst tiles are mandatory because a 1-px defect is invisible in an overview (10 C4.5). Evidence: no legacy-vs-new plot ever existed; the |diff| GeoTIFF leaked to temp (`raster.py:239-254`, 09§5).
- **D6-15 The model reviews plots with a calibrated rubric and may only flag** (dataset → `NEEDS_REVIEW`, reason `VISUAL_FLAG`, citing plot path + sha256 + location). It can never upgrade, set tolerances or clear a flag. Evidence: PLAN rule; 08§5.2; 10 B4/C4; dependence pilot — prompting did not change unsafe acceptances, a deterministic rule did (10 B8).
- **D6-16 Known differences are declared rules inside the frozen contract, not post-hoc overrides.** A suspected legacy defect → `NEEDS_DECISION` until the owner chooses preserve/fix in `decisions.md`; then a new contract version. Evidence: 10 C1 NEEDS_DECISION, C6 last bullet; 08§5.6 (human decides "which differences are acceptable").
- **D6-17 Evaluator faults are never candidate failures and vice versa.** Unreadable *legacy* file, hash mismatch, checker exception → `EVALUATOR_ERROR`; unreadable *new* file → `FAIL/OUTPUT_UNREADABLE`. Stale results are deleted before a rerun writes. Evidence: Arena `verifier.py:340-345, 382-384` (10 B2.1); `eo-agent-benchmark-catalogue/analysis/harbor-task13-verifier/verifier.py:37-46` (stale reward removal, `evaluator_error` gives no reward).
- **D6-18 Results are content-addressed and bound to both artefact hashes.** Inherit `axis.deterministic.v1` identity (`SC/axis-spatial-deploy-mcp-v2/packages/runtime-core/src/migration-delivery.ts:291-297, 343-366`: canonical JSON, tolerances in identity, hash excludes timestamp) but bind `new.sha256` (fixes 10 A8.4) and drop its JSON-tree diff, global tolerance pair and path-substring classes (`:359`).
- **D6-19 Model-written tests are smoke only; the parity verdict comes only from `compare.py`.** Evidence: 0/164 generated tests read legacy outputs, 59 mock (FH-26/T8).
- **D6-20 TARGETED_ACCEPT is not a verdict state.** It is kept as a review-budget rule for the pre-demo human check (§7).

## 3 Schemas

### 3.1 Compare result `runs/<id>/compare/<dataset_id>.json` (`axis-migrate.compare-result.v1`)

```json
{"schema":"axis-migrate.compare-result.v1",
 "comparator":{"id":"axis-migrate.compare","version":"1.0.0","sha256":"<sha of compare.py>"},
 "result_id":"cmp:<sha256(canonical(all fields except created_at, result_id))>",
 "run_id":"r20261016T1012-chained","mode":"chained","isolated_step":null,"contract_hash":"sha256:…",
 "dataset_id":"ds.exposure_raster","step_id":"s04_distribute_value","family":"raster","role":"final","measure":"extensive",
 "legacy":{"path":"04_FINAL/lux_res_exposure_2020.tif","layer":null,"sha256":"b364ee90…","content_sha256":null,"pinned_sha256":"b364ee90…"},
 "new":{"path":"runs/…/outputs/lux_res_exposure_2020.tif","sha256":"…","written_by_step":"s04_distribute_value"},
 "pairing":{"method":"lineage","evidence":["02_SCRIPTS/04_distribute_value.py:17,106"],"state":"confirmed"},
 "verdict":"FAIL","reason_codes":["RASTER_TRANSFORM_MISMATCH"],
 "checks":[{"id":"raster.grid.transform","order":4,"gate":true,"state":"FAIL","reason_codes":["RASTER_TRANSFORM_MISMATCH"],
   "metric":"transform_dev_px","value":0.49999,"tolerance_id":"tol.default.grid.v1","threshold":1e-6,
   "legacy":[0.000833333,0,5.744583325,0,-0.000833333,50.18125],"new":[…],
   "diagnostics":{"shift_px":[0.5,-0.4997]},"samples":[]},
  {"id":"raster.values","state":"NOT_COMPARED","reason_codes":["GATED"],"gated_by":"raster.grid.transform"},
  {"id":"conservation.zone_totals","state":"PASS_EXACT","metric":"max_rel_zone_drift","value":0.0,"zones":"ds.cantons:canton",
   "table":[{"zone":"Wiltz","legacy":2326648851.2,"new":2326648851.2,"rel":0.0}]}],
 "within_tolerance":[],"not_checked":["raster.values (GATED by raster.grid.transform)"],
 "known_differences_applied":[],"env":{"gdal":"3.13.3","proj":"9.9.0","geos":"3.14.1","rasterio":"1.5.2"},
 "plots":"plots/ds.exposure_raster/plots.json","created_at":"2026-10-16T10:14:03Z"}
```

Check `state` uses the dataset vocabulary restricted to `PASS_EXACT | PASS_WITHIN_TOLERANCE | FAIL | NOT_COMPARED (GATED) | EVALUATOR_ERROR`. Every `FAIL` check carries `metric`, `value`, `threshold`, `tolerance_id` and up to 20 `samples` in **map coordinates** (+ key for vectors/tables); full mismatch lists go to `compare/<dataset_id>.mismatches.parquet`.

**States** (precedence for aggregation, worst first):

| State | Meaning | Ladder effect |
|---|---|---|
| `FAIL` | ≥1 mandatory check failed against a valid, pinned reference | blocks |
| `EVALUATOR_ERROR` | reference/contract/checker fault; says nothing about the candidate | blocks; fix evaluator, rerun |
| `BLOCKED` | could not compare for an external reason (step crashed upstream in chained mode, legacy file missing, isolation input unavailable) | blocks |
| `NEEDS_DECISION` | differs where the legacy itself is suspected wrong; owner must choose preserve/fix | blocks |
| `NEEDS_REVIEW` | no mandatory failure, but a human must look (visual flag, branch not exercised, accumulated drift, missing visual receipt) | blocks |
| `NOT_COMPARED` | no reference by design (unpaired, unsupported family, legacy intermediate never written to disk) | blocks unless an accepted exclusion exists (never for `role: final`) |
| `PASS_WITHIN_TOLERANCE` | all mandatory checks pass; ≥1 non-zero difference inside a justified record; all listed | passes |
| `PASS_EXACT` | all mandatory checks equal as declared | passes |

**Reason codes** (stable strings; D = diagnostic only, never sets a verdict alone):
- Contract/pairing: `OUTPUT_MISSING, OUTPUT_UNREADABLE, OUTPUT_EMPTY, LINEAGE_MISMATCH` (new file written by a different step than `produced_by`), `UNPAIRED_LEGACY, UNPAIRED_NEW, PAIRING_UNCONFIRMED, NO_REFERENCE, LAYER_AMBIGUOUS, KEY_UNDECLARED, FAMILY_UNSUPPORTED, GATED, UPSTREAM_BLOCKED, ISOLATION_INPUT_UNAVAILABLE`.
- Structure: `CRS_MISSING, CRS_MISMATCH, RASTER_BAND_COUNT_MISMATCH, RASTER_SHAPE_MISMATCH, RASTER_TRANSFORM_MISMATCH, DTYPE_MISMATCH, NODATA_VALUE_MISMATCH, NODATA_MASK_MISMATCH, SCHEMA_MISMATCH, GEOMETRY_TYPE_MISMATCH, GEOMETRY_INVALID, FEATURE_COUNT_MISMATCH, ROW_COUNT_MISMATCH, KEY_DUPLICATE, KEY_SET_MISSING, KEY_SET_EXTRA`.
- Values: `RASTER_VALUE_MISMATCH, RASTER_CATEGORY_MISMATCH, RASTER_NON_INTEGER_COUNTS, NULL_PATTERN_MISMATCH, ATTRIBUTE_VALUE_MISMATCH, GEOMETRY_SYMDIFF_EXCEEDED, GEOMETRY_HAUSDORFF_EXCEEDED, GEOMETRY_MEASURE_CRS_INVALID`.
- Conservation: `TOTAL_DRIFT, ZONE_TOTAL_DRIFT, INVARIANT_VIOLATED`.
- Diagnostics (D): `NUMERIC_SCALE_FACTOR` (median ratio ≈10^k), `NUMERIC_OFFSET, NUMERIC_SIGN_FLIP, DATA_OFFSET_SUSPECTED` (integer shift explains mismatch), `SMOOTHING_SUSPECTED, ACCUMULATED_UPSTREAM_DIFFERENCE, COMPENSATING_DEFECT, INHERITED_FROM:<dataset_id>`.
- Review/decision: `VISUAL_FLAG, VISUAL_REVIEW_MISSING, BRANCH_NOT_EXERCISED, LEGACY_DEFECT_SUSPECTED`.
- Change control / evaluator: `TOLERANCE_CHANGED_AFTER_RUN, TOLERANCE_UNJUSTIFIED` (record ignored, check ran exact), `OUTPUT_CHANGED_BY_OPTIMISATION, REFERENCE_HASH_MISMATCH, INPUT_HASH_MISMATCH, CHECKER_HASH_MISMATCH, CHECKER_EXCEPTION, REFERENCE_NOT_INDEPENDENT`.

### 3.2 `migration/tolerances.yaml` record

```yaml
version: 3                      # file version; every record change bumps it and appends to contract-history.jsonl
defaults:                       # stated in every report; no hidden defaults
  raster.grid.transform: {metric: transform_dev_px, value: 1.0e-6, basis: float_storage}
  raster.mask: {metric: xor_cells, value: 0}
  raster.values: {metric: abs+rel, atol: 0, rtol: 0, max_mismatch: 0}   # exact
  vector.geometry_type: {normalise: multi_equivalence}                  # Polygon ≡ MultiPolygon (M: QGIS writes Multi*, shapely structure repair gives Polygon)
records:
- id: tol.exposure_raster.values.v1
  dataset: ds.exposure_raster          # graph dataset id
  check: raster.values                  # raster.values | raster.mask | conservation.zone_totals | vector.symdiff_ratio | vector.hausdorff_m | table.attr:<col> | …
  metric: abs+rel                        # |n−l| ≤ atol + rtol·|l|  (Arena number rule)
  atol: 0
  rtol: 1.0e-6
  max_mismatch: 0                        # cells allowed over the rule; >0 needs its own two numbers
  units: EUR_per_cell                    # spatial distances only in metres (measure_crs) or pixels
  basis: measured_spread                 # measured_spread | float_storage | data_resolution | domain_rule
  correct_spread: {value: 0.0, how: "rasterio/numpy re-implementation of s04 on legacy 03_* (float32 output)", evidence: "controls/s04_alt.py → controls/s04_alt.json sha256:…"}
  nearest_wrong: {value: 1.79e-3, metric: max_rel_zone_drift, variant: "factor rasterised all_touched (95 cells differ)", evidence: "controls/mutants/s04_factor_all_touched.json"}
  agreed_by: najah                        # a human; a model may propose, never agree
  agreed_at: 2026-10-14
  decision_ref: decisions.md#D-07
  fixed_before: r20261016T1012-chained    # first run under this record; contract_hash in that run's manifest
```

Validation (in `compare.py`, before any check): unknown keys → `EVALUATOR_ERROR` (C1 silently ignored unknown keys, 08§8.7); missing `correct_spread` or `nearest_wrong`, or not `correct_spread < threshold < nearest_wrong` → `TOLERANCE_UNJUSTIFIED`, check runs exact; `units` in degrees or `measure_crs` geographic/3857 → `GEOMETRY_MEASURE_CRS_INVALID`; `agreed_at` later than the run's `started_at` → `TOLERANCE_CHANGED_AFTER_RUN`.

### 3.3 `migration/known-differences.md` entry

Human-readable markdown; each entry ends with one fenced `yaml` block that `compare.py` parses (prose is ignored by code):

```yaml
id: KD-003
kind: normalisation      # normalisation | exclusion | accepted_change | legacy_defect_preserved
datasets: [ds.pop_res]
checks: [raster.nodata_value]
rule: {nodata_equivalent: [-3.4028234663852886e+38, nan]}   # machine rule; allowed vocab per kind (below)
reason: "QGIS rastercalc default sentinel; mask equality is still checked cell by cell"
evidence: ["05_TEMP/03_pop_res.tif nodata (PROV inventory)", "02_SCRIPTS/03_zonal_population.py:31-37"]
owner: najah
decided_at: 2026-10-14
decision_ref: decisions.md#D-09
status: accepted         # proposed | accepted | withdrawn — only accepted entries enter contract.json
```

Rule vocabulary: `normalisation` → `nodata_equivalent`, `dtype_widening {from,to}`, `multi_equivalence`, `string_strip|casefold {columns}`, `rename {legacy: new}`, `content_hash_only {gpkg_contents.last_change}`; `exclusion` → `{reason: NO_REFERENCE|FAMILY_UNSUPPORTED}` (never `role: final`); `accepted_change` → `{expected: "new = legacy * 1000"}` (the check compares against the transformed legacy, so it still fails on any other difference); `legacy_defect_preserved` → no rule, records that parity keeps a known legacy bug (report shows it). Nothing in this file can name a dataset+check that has already FAILED in the current contract version: it applies only from the next contract version.

## 4 Algorithms

### (a) Pairing from lineage only

```
pairs, verdicts = [], {}
for ds in graph.datasets:
    if ds.role == "input": continue                        # inputs are never references (fixes P2, verify.py:306-331)
    L, N = ds.legacy_path, ds.new_path
    if L and not N:  verdicts[ds] = NOT_COMPARED/UNPAIRED_LEGACY; continue      # step not ported or output dropped
    if N and not L:  verdicts[ds] = NOT_COMPARED/(NO_REFERENCE if ds.legacy_in_memory else UNPAIRED_NEW); continue
    if not ds.produced_by or not evidence_for(ds.produced_by, L):  # file:line where the legacy step writes L, or user_asserted
        verdicts[ds] = NOT_COMPARED/PAIRING_UNCONFIRMED; continue
    if is_multilayer(L) and not ds.layer:  verdicts[ds] = EVALUATOR_ERROR/LAYER_AMBIGUOUS; continue
    if sha(L) != references[L].sha256 and content_sha(L) != references[L].content_sha256:
        verdicts[ds] = EVALUATOR_ERROR/REFERENCE_HASH_MISMATCH; continue
    m = manifest.outputs.get(ds.id)
    if m is None or not exists(m.path): verdicts[ds] = FAIL/OUTPUT_MISSING (or BLOCKED/UPSTREAM_BLOCKED if an upstream step did not run); continue
    if m.written_by_step != ds.produced_by: verdicts[ds] = FAIL/LINEAGE_MISMATCH; continue
    pairs.append(ds)
# sweep for things lineage does not mention
for f in files_under(legacy_output_dirs) - sidecars - {ds.legacy_path}: pipeline_note(UNPAIRED_LEGACY, f)   # blocks reference-verified unless an exclusion exists
for f in manifest.outputs_undeclared: pipeline_note(UNPAIRED_NEW, f)                                          # listed, not blocking
```
`legacy_output_dirs` = directories that any graph step writes to (lux: `04_FINAL/`, `05_TEMP/`). Sidecars: `.aux.xml .ovr .tfw .qix .shx .dbf .prj .cpg .sbn .sbx .xml .lock` (`AG/verify.py:33` + 09§7.1). In-place edits (lux `04_canton_factor.gpkg`: saved at `04_distribute_value.py:46`, attributes changed after) pair the file's **final** state with the step that last writes it. Name similarity is never read by `compare.py`; discover may *suggest* a path, which stays `PAIRING_UNCONFIRMED` until static+runtime evidence or a user confirmation exists (08§2.5 PRD rule).

### (b) compare.py per family

Common: open both read-only; legacy unreadable → `EVALUATOR_ERROR`; new unreadable → `FAIL/OUTPUT_UNREADABLE`; identical bytes or identical content hash → all checks `PASS_EXACT` (still run structure for the record). No `except: pass`; any exception → `EVALUATOR_ERROR/CHECKER_EXCEPTION` with traceback in the result. Floats accumulate in float64. Number rule `close(l,n) = |n−l| ≤ atol + rtol·|l|`, NaN never close to a number.

**Raster** (rasterio, per band, windowed over legacy block windows or 512²):
1. `G1` band count equal. `G2` CRS: both present (`CRS_MISSING`), `pyproj.CRS(l).equals(n)` (not `to_epsg()`: `comparator.py:378,645` gave None==None passes). `G3` (h,w) equal → else `RASTER_SHAPE_MISMATCH`, diagnostic whole-cell offset `((n.c−l.c)/l.a, (n.f−l.f)/l.e)`.
2. `G4` transform: `dev = max(|Δc|/|a|, |Δf|/|e|, W·|Δa|/|a|, H·|Δe|/|e|, H·|Δb|/|a|, W·|Δd|/|e|)` (origin + far corner, in pixels) ≤ 1e-6 → else `RASTER_TRANSFORM_MISMATCH`, diagnostic `shift_px`. If any of G1–G4 fails: masks and values are `NOT_COMPARED/GATED`; go to step 6.
3. `S1` dtype equal (or accepted `dtype_widening`) → else `DTYPE_MISMATCH`. `S2` nodata value equal (NaN==NaN, or accepted `nodata_equivalent`) → else `NODATA_VALUE_MISMATCH`. Both hard, neither gates.
4. `M` valid = `read_masks()>0 & isfinite(v) & v != nodata`; `xor = vl ^ vn`; FAIL `NODATA_MASK_MISMATCH` if `count(xor) > max_mismatch` (default 0); report counts legacy-only / new-only and 20 samples (`transform*(col+.5,row+.5)`).
5. `V` on `vl & vn`: categorical → equality + class transition table; integer dtype → exact; float → number rule. Stream: `count_over`, `max_abs`, `max_rel` (|l|>0), `mean_bias`, quantiles p50/p90/p99/p99.9/max of |d| from a fixed log-spaced histogram (1e-15…1e15). FAIL `RASTER_VALUE_MISMATCH` if `count_over > max_mismatch`; if differences exist but all within → `PASS_WITHIN_TOLERANCE` with the stats listed. `measure: count` and legacy all-integer → `RASTER_NON_INTEGER_COUNTS` if new has fractional values.
6. `C` conservation (`measure: extensive`, always runs, even when gated): see (c).
7. Diagnostics on FAIL only: `DATA_OFFSET_SUSPECTED` if some integer (dx,dy)∈[−2,2]²∖(0,0) shift of new reduces mask+value mismatches by ≥90 % (catches EP-05 "bounds snapped, data not", where the transform is right but cells are offset); `NUMERIC_SCALE_FACTOR` if median(n/l) over nonzero both-valid cells is within 1 % of 10^k, k≠0; `SMOOTHING_SUSPECTED` if totals agree within 1 % and mean |Laplacian| ratio new/legacy < 0.8 (threshold calibrated on lux test L5).

**Vector** (pyogrio/geopandas, declared `layer`):
1. Layers: `>1` and none declared → `EVALUATOR_ERROR/LAYER_AMBIGUOUS`. CRS present and `equals` (no silent reprojection, B10.1). Geometry type family equal after declared normalisation (default `multi_equivalence`), else `GEOMETRY_TYPE_MISMATCH` (catches GeometryCollection output from `make_valid(method="linework")`, M).
2. Key from `graph.datasets[].compare.key` (composite allowed; nulls as ""; lux `ds.res_2169`: `[osm_id, osm_way_id]`, unique, M). No key → `match: geometry` must be declared (normalize → `set_precision(grid=geom_tol)` → WKB hash multiset, then STRtree nearest within `geom_tol`, 09§7.3 4b-c); neither → `EVALUATOR_ERROR/KEY_UNDECLARED`. No auto-detected key, no order.
3. `KEY_DUPLICATE` either side; `FEATURE_COUNT_MISMATCH`; `KEY_SET_MISSING`/`KEY_SET_EXTRA` with up to 20 keys + bbox each.
4. Schema: `legacy_cols − ignore ⊆ rename(new_cols)` → else `SCHEMA_MISMATCH` (lists columns); dtype family per column (numeric/str/datetime/bool); extra new columns listed only.
5. Geometry per matched key, both reprojected **for measurement only** to `measure_crs` (lux 2169; unset and geographic → `GEOMETRY_MEASURE_CRS_INVALID`): `GEOMETRY_INVALID` if new invalid count > legacy; polygons `symdiff_ratio = area(L△N)/area(L)`; all types `hausdorff_m`; thresholds from records (default exact: `ratio ≤ 0`, `hausdorff ≤ 0`, i.e. normalised-equal). Aggregates (evidence): total area/length, union symmetric difference (kept for the plot).
6. Attributes per key: null pattern first (`NULL_PATTERN_MISMATCH`), then numeric number rule / strings exact after declared normalisation / datetimes equal → `ATTRIBUTE_VALUE_MISMATCH` with key samples; diagnostics as raster.

**Table** (CSV via one reader config for both sides; XLSX via `openpyxl data_only=True` with declared `sheet`, `header_row`, `data_rows`, optional `total_row`; GPKG attribute tables via vector rules):
1. Schema as vector 4. 2. Key (declared; `match: full_row` allowed only if declared → canonical sort of all columns after dtype canonicalisation). 3. Row conservation: `ROW_COUNT_MISMATCH`, key-set diff both sides. 4. Null pattern, then values per column (records per `table.attr:<col>`). 5. Totals: sum of each numeric column new vs legacy (number rule) and, if `total_row` declared, the legacy total row vs new total row → `TOTAL_DRIFT`. Lux XLSX: `sheet: by_canton, header_row: 1, data_rows: 2-13, total_row: 15` (PROV inventory: header + 12 + blank + TOTAL).

### (c) Conservation checks

Declared in graph per dataset: `measure: extensive|intensive|categorical|mask|index` (+ `count` flag), `zones: <dataset_id>:<key>`, `zone_rule: centre|all_touched` (must equal the legacy rule; lux: centre, PROV "Zones"), and pipeline `invariants`.
```
if measure == extensive:
  T_l, T_n = sum64(valid legacy), sum64(valid new)                 # each on its own grid → works when the grid gate failed
  check TOTAL_DRIFT: close(T_l, T_n, record or exact)
  if zones: Z_l = rasterize(zones, legacy grid, rule); Z_n = rasterize(zones, new grid, rule)
            per zone z: close(sum(l[Z_l==z]), sum(n[Z_n==z])) → ZONE_TOTAL_DRIFT with table of all zones
            cells with value>0 and zone==0 (outside every zone) reported on both sides
for inv in graph.invariants:   # e.g. sum(ds.exposure_raster by ds.cantons:canton) == ds.canton_factor.tot_value (NOTES.txt "sums per canton should come back")
  evaluate on legacy first: legacy violates → LEGACY_DEFECT_SUSPECTED → NEEDS_DECISION; new violates → INVARIANT_VIOLATED (FAIL)
```
Lux legacy satisfies its invariant at max rel 1.57e-9 (PROV Check 2; M same). Invariants must match the operation (EP-25: masking only removes; partitions sum exactly). Non-negativity for counts (`EP-07`: −200 sentinel leaks) is an invariant of type `min ≥ 0`.

### (d) Verdict aggregation

```
dataset.verdict = worst(check states)  under precedence §3.1; GATED checks do not count; NOT_COMPARED datasets excluded by an accepted exclusion are 'excluded' (listed)
step.verdict    = worst(dataset verdicts of datasets with produced_by == step), ignoring UNPAIRED_NEW;
                  no compared dataset at all → NOT_COMPARED; step crashed (manifest) → its outputs FAIL/OUTPUT_MISSING
pipeline        = worst(step verdicts) over the CHAINED run, plus pipeline notes (UNPAIRED_LEGACY without exclusion → NOT_COMPARED);
                  every role: final dataset must be compared; any compared raster/vector dataset without a visual receipt → NEEDS_REVIEW/VISUAL_REVIEW_MISSING
visual merge    = for f in visual-review.json where f.answer == "yes" and f.effect == "flag":  if dataset.verdict in PASS_*: dataset.verdict = NEEDS_REVIEW (+VISUAL_FLAG); else attach note only
ladder          = "reference-verified" iff pipeline ∈ {PASS_EXACT, PASS_WITHIN_TOLERANCE} and contract_hash(run) == contract_hash(now) and mode == chained
```
`verdict.json` = `{run_id, mode, contract_hash, pipeline:{verdict, reason_codes}, steps:{<id>:{verdict, reason_codes, datasets[], blame}}, first_failing_steps[], within_tolerance[], excluded[], not_checked[], ladder}`. The aggregator is code; no model writes `verdict.json` (Stop hook checks its hash against `compare/*.json`).

### (e) First-failing-step blame (chained vs step isolation)

Isolation run for step s: harness copies the legacy files of s's input datasets into `runs/<id>/iso_inputs/` and runs only s (inputs with no legacy file → `BLOCKED/ISOLATION_INPUT_UNAVAILABLE`). The step code reads paths from params; it never opens the legacy folder (hook stays intact).
```
C[s], I[s] = step verdicts (chained, isolation)
for s in topo_order(steps):
  anc_fail = any(C[a] == FAIL for a in ancestors(s))
  if I[s] == FAIL:                    blame[s] = "own"                     # defect is in s
  elif C[s] == FAIL and anc_fail:     blame[s] = "inherited"; add INHERITED_FROM:<first failing ancestor dataset>
  elif C[s] == FAIL and I[s] in PASS: blame[s] = "accumulated"; step → NEEDS_REVIEW/ACCUMULATED_UPSTREAM_DIFFERENCE (upstream within-tolerance drift amplified: tighten the upstream record or document)
  elif I[s] == FAIL and C[s] in PASS: blame[s] = "compensating"; NEEDS_REVIEW/COMPENSATING_DEFECT (two wrongs cancel)
first_failing_steps = minimal elements (by topo order) of {s : blame[s] == "own"}; without isolation results: {s : C[s]==FAIL and no ancestor FAIL}
```
The repair loop takes the earliest of `first_failing_steps`; after a fix it reruns isolation for that step, then the chained run from that step on.

### (f) Repair loop

Per attempt the repairing model receives only: (1) the **step spec** — graph step (legacy script + lines, intent), its params entries with source, the dataset contracts of its inputs/outputs (CRS, grid, dtype, nodata, key, measure, tolerance records), relevant `decisions.md` entries, the step's module file, allowed imports; (2) the **current error** — the first failing dataset's compare result reduced to failing checks + diagnostics + samples (no raw arrays), its plot paths, and for crashes the innermost traceback frame inside the bundle + 40 lines + exception (05 §Recommended loop). Nothing from earlier attempts. A code-side ledger stores `(failure_signature = sha(step, dataset, sorted reason codes, rounded metrics), patch_sha)`.
Rules: max **3 attempts per step per session**; stop early if the same signature survives 2 distinct patches or a patch repeats; stop at once on `BLOCKED`, `EVALUATOR_ERROR`, `NEEDS_DECISION` (→ interview); success only when the rerun step is `PASS_*` in isolation **and** chained; exhausted → step stays `FAIL`, report shows it (no "best attempt"). Immutable while the loop is open (PreToolUse hook + `contract_hash` check): `tolerances.yaml`, `known-differences.md`, `references.json`, the pairing projection of `graph.json`, legacy folder, and `params.yaml` values — a parameter change is an interview question, not a repair (PLAN: thresholds inferred from outputs must be flagged, never silently fitted). Trace review flags edits to these files (10 C5.4).

### (g) plot.py

Deterministic (matplotlib Agg, fixed DPI 150, version in `plots.json`), from both files + the compare result:
- **Raster `overview.png`**: legacy | new | difference, same extent, axes in map coordinates with CRS label and a metre scale bar (computed in `measure_crs`). Legacy and new share one norm: `vmin,vmax` = p2/p98 of the union of valid values of both sides, out-of-range shown with extend arrows (a ×1000 output saturates visibly). Difference `n−l` on both-valid cells, diverging colour-blind-safe map (`PuOr`), **symmetric** limits `±max(p99.5|d|, threshold)`; cells over tolerance outlined. Downsampling for >2048 px uses **max |d|** per block for the diff panel (a mean hides isolated cells) and mode for categorical.
- **`mask_diff.png`**: valid both (grey), legacy-only (blue), new-only (orange); counts in title.
- **`hist.png`**: histogram of d (log y) with ±tolerance lines; for extensive data also per-zone total bars legacy vs new.
- **`worst_tiles.png`**: k = 4 non-overlapping 48×48-cell tiles centred on the largest |d| (or XOR) cells, **native resolution, cell grid drawn**, legacy/new/diff per tile with tile bbox coordinates — the only view where a one-cell edge offset is visible. If the grid gate failed: legacy and new drawn over each other in map space (outlines of valid masks) at the worst corner, so the shift itself is shown.
- **Vector `overlay.png`**: legacy outlines (blue, solid), new (orange, dashed), symmetric difference filled red, unmatched keys labelled; `per_feature.png` choropleth of `symdiff_ratio`/`hausdorff_m`; `worst_features.png` zooms on the 4 worst keys (vertices drawn).
- **Table `residuals.png`**: per-key residuals for up to 6 numeric columns + new-vs-legacy scatter with y=x.
- `plots.json`: `{generator, version, dataset_id, compare_result_id, inputs:{legacy_sha, new_sha}, png:[{name, sha256, extent, norm}]}`.

### (h) The model's visual review

Rubric `skills/verify/rubric.v1.yaml` (sha256 recorded in every receipt; a change requires recalibration). One question per row; answer `yes | no | cannot_tell`; **yes means "defect present"**.

| id | question (asked per plot) | plot | maps to | calibration: must flag | must not flag |
|---|---|---|---|---|---|
| Q1 | Is there a uniform offset of about one cell (or half a cell) along edges, visible as paired positive/negative stripes on opposite sides of features? | diff, worst_tiles | DATA_OFFSET / shift | L3 −tap shift, 1-px data offset | L1, L2 |
| Q2 | Do the new counts look smoothed or blurred (peaks lower, halos around populated cells) compared with legacy? | overview, worst_tiles | SMOOTHING | L5 bilinear | L1, L12 float64 |
| Q3 | Are buffers/polygons angular, triangular or collapsed where legacy is smooth? | overlay, worst_features | simplify-before-buffer | simplify(10 m) mutant | L2 |
| Q4 | Is a region (a canton, tile, strip or class) missing or constant in new but present in legacy? | overview, mask_diff, per-zone bars | missing branch / join | L8 Wiltz unmatched | L1 |
| Q5 | Are there straight stripes or block patterns aligned with tile/chunk boundaries? | diff | chunking (EP-04) | 512-px chunk mutant | L1 |
| Q6 | Is there a thin ring of differences along the outer boundary or nodata edge? | mask_diff | clip rule / all_touched | L-clip all_touched mutant (1,074 cells, M) | L2 |
| Q7 | Does the new panel saturate or look empty under the shared scale while legacy does not? | overview | scale/units | L9 ×1/1000 | L12 |

Finding schema (validated by `plot.py review-add`; free text ≤ 200 chars; no reasoning stored, 08§8.13):
```json
{"finding_id":"vf-…","run_id":"…","dataset_id":"ds.exposure_raster","rubric_id":"rubric.v1","rubric_sha256":"…","question_id":"Q1",
 "answer":"yes","effect":"flag","plot":"plots/ds.exposure_raster/worst_tiles.png","plot_sha256":"…","panel":"tile 2 diff",
 "location":{"crs":"EPSG:4326","bbox":[6.10,49.60,6.14,49.63]},"note":"paired ± stripes on west/east edges of all residential blocks",
 "model":"claude-opus-…","created_at":"…"}
```
Limits, enforced in the aggregator: (1) `effect` ∈ {`flag`, `none`}; `flag` requires `answer: yes` and a `plot_sha256` that matches `plots.json` of the same run; (2) flag moves a `PASS_*` dataset to `NEEDS_REVIEW`; on any other state it is attached as a note; nothing moves a state upwards, sets tolerances or edits compare results; (3) `cannot_tell` has no effect but is listed under "not visually assessed"; (4) receipts must exist for every compared raster/vector dataset (else `VISUAL_REVIEW_MISSING`) — this makes looking mandatory but a receipt only proves the file was cited, not seen (known limit); (5) a flag is cleared only by a human in `decisions.md`, or by a new run whose plot no longer triggers it; (6) one sample per question; two reviews disagreeing are both shown, not averaged (08§5.5); (7) shifts and sub-cell defects are code's job: Q1 complements, never replaces, G4 and `DATA_OFFSET_SUSPECTED`.

### Lux tolerance records (proposed; justified by the Arena two-number procedure, measured 13 Oct, M)

Procedure per dataset: correct spread = independent rasterio/geopandas/numpy implementation of the step run in isolation on the legacy inputs (QGIS reference vs GDAL-via-rasterio implementation; for the ArcPy step the reference is the equivalent QGIS step 03, labelled so). Nearest wrong = the closest plausible wrong recipe from the trap list, applied as a code variant or as a deterministic mutation of the legacy file (shift transform, bilinear round trip, toggle all_touched, ×10^k, drop/strip). Result: an independent implementation reproduced every dataset measured (9 of 13) exactly in storage precision, so almost all records are exact; the one correct-looking variant that diverged (`make_valid` linework) is a wrong recipe, not spread.

| dataset (step) | check | value | correct spread (independent) | nearest wrong recipe |
|---|---|---|---|---|
| ds.pop_lux (s01) | grid | 1e-6 px | 0 (bit-identical, PROV) | −tap `KEEP_RESOLUTION`: 0.49999 px |
| ds.pop_lux | mask / values | 0 cells / exact | 0 / 0 (`geometry_mask` centre rule) | all_touched clip: 1,074 cells, total −0.31 %; bilinear round trip: 403,504 cells >1e-3, max 19.4 |
| ds.lux_outline (s01) | symdiff ratio / Hausdorff | exact | 0 m² / 0 m (geopandas dissolve) | — (no plausible wrong recipe found; stays exact) |
| ds.res_2169 (s02) | key set, count | exact | 1,512 = 1,512, keys equal | area in 3857: 2,085; in degrees: 0 |
| ds.res_2169 | symdiff ratio / hausdorff_m / `area_m2` rtol | 1e-9 / 0.001 m / 1e-9 | 0 / 0 m / 8.6e-13 (shapely `make_valid(method="structure")` ≙ QGIS fixgeometries METHOD 1) | simplify(10 m): median ratio 5.8 %, max 55 %, Hausdorff 16 m; `make_valid` linework: 13 GeometryCollections, Hausdorff 72 m (type gate) |
| ds.res_mask (s02) | mask xor | 0 cells | 0 (rasterio `all_touched=True`) | linework repair: 3 cells; cell-centre rasterize: 24,377 cells |
| ds.pop_res (s03) | values / mask | exact / 0 | 0 / 0 | ArcPy-03b semantics (0 instead of NoData outside residential): mask differs on ~404k cells |
| ds.canton_pop (s03) | `pop_sum`,`res_sum` rtol; counts exact | 1e-9 | 0 (float64 sum, centre zones) | zones all_touched: 2.2e-2 rel |
| ds.factor_raster (s04) | values / mask | exact / 0 | 0 / 0 | factor all_touched: 95 cells |
| ds.exposure_raster (s04) | values rtol / zone totals rtol | 1e-6 / 1e-6 | 0 (float32); legit float64 widening 5.9e-8 (only with KD `dtype_widening`) | factor all_touched: zone drift 1.79e-3; bilinear pop: 4.5e-2; kEUR not ×1000: ratio 1e-3 |
| ds.exposure_by_canton_csv/xlsx/gpkg (s04) | `exposure_raster_sum_eur`, `total_value_eur` rtol; `diff_eur` atol | 1e-9; 0.01 EUR | 2e-16; 7.6e-6 EUR | all_touched factor: ≥1.8e-3 rel (= millions of EUR in `diff_eur`) |

Why 1e-9 and not exact for float64 sums: summation order differs between QGIS and numpy (bound ≈ n·ε ≈ 1e-11 for 5e4 cells), so an exact record would fail a correct alternative; 1e-9 sits 6 orders below the nearest wrong. Why 1e-6 for the float32 exposure: the only legitimate variant is dtype widening (5.9e-8); the nearest wrong is 1.8e-3. Record `agreed_by` = owner at Plan; all are fixed before the first chained run (D6-11).

## 5 Reuse (lift with fixes; never lift the rest)

| Lift | Source | Fix on lift |
|---|---|---|
| streaming band stats | `AG/comparators/raster.py:454-487` `_BandAccum` | `np.sum(valid)` on float32 accumulates in float32 (`:469`) → `dtype=float64`; variance via `sum_sq` (`:470`) → Welford/Chan merge; add valid/NaN counts |
| windowed loop, nodata→NaN | `raster.py:257-320`, `:409-416` | iterate the common grid only (after G4); mask with `read_masks` + `isfinite`; delete `except: pass` (`:360-362`) |
| file hash | `AG/comparators/base.py:181-194` | none |
| check/result shape | `BE/ai/verify/comparator.py:87-131` (`CheckResult`, `compute_status`) | severity → `gate`/hard; states per §3.1; add `reason_codes`, `tolerance_id`, map-coordinate samples |
| declared outputs, missing = FAIL, JSON lines | `BE/pipelines/validation/migrated.py:150-161` | aggregate = precedence, never mean score (`:180-186`) |
| content-addressed identity | `SC/axis-spatial-deploy-mcp-v2/packages/runtime-core/src/migration-delivery.ts:291-297, 343-366` | bind `new.sha256`; per-check records instead of one global pair; reason codes from the checker, not path substrings (`:359`) |
| GPKG/XLSX content hash | `fixtures/lux-exposure-legacy.build/42_compare_runs.py` (rows per table, sorted; cell values) | exclude only `gpkg_contents.last_change` and `docProps/core.xml` explicitly |
| zone conservation | `…build/50_conservation_check.py` (centre-rule rasterize, float64) | zones + rule from graph/params, both grids |
| grid inventory | `…build/60_inventory.py` (exact transform/shape vs reference) | becomes G3/G4 |
| metric symdiff with floor | `eo-agent-benchmark-catalogue/analysis/checks/check_dsh_task8.py:63-95` | per feature by key, not only dissolved union (B10.4) |
| checker integrity, stale-result removal | `…/harbor-task13-verifier/verifier.py:12-21, 37-46` | comparator sha recorded in result; delete `compare/<ds>.json` before writing |

Do not lift: auto-pairing (`BE/routes/verify.py:449-564`, `AG/verify.py:199-269`), WARN bands (`raster.py:188-196`), intersection compare (`raster.py:160-173`), `to_epsg()` CRS (`comparator.py:378,645`), `fillna(0/"")` (`:503-504,519-523,859-860,874-875`), positional/sorted-float alignment (`AG/comparators/vector.py:183-199`), E2B mean/std raster parity, 3857 area checks (`SC/gis-agent-arena/worker/api/src/verifiers.ts:60`), the eyeball verifier as a verdict (`BE/ai/verify/eyeball_verifier.py`).

## 6 Acceptance tests (`skills/verify/tests/`; `compare.py` is written test-first, PLAN Wed 14)

**6.1 The 14 probes (09§3.6) as regressions** — synthetic 50×50 rasters / 50-feature layers / 25-row tables built in `conftest.py`:

| # | legacy vs wrong new | expected verdict / reason codes |
|---|---|---|
| P1 | raster vs all-NaN | FAIL / NODATA_MASK_MISMATCH (+OUTPUT_EMPTY) |
| P2 | raster vs top half only, same res (shape 25×50) | FAIL / RASTER_SHAPE_MISMATCH; values GATED. Variant same shape, bottom half nodata → FAIL / NODATA_MASK_MISMATCH |
| P3 | 0.9 % pixels +50 | FAIL / RASTER_VALUE_MISMATCH (count_over ≈ 0.9 % of cells, max_abs 50; no WARN) |
| P4 | data flipped 180°, same transform | FAIL / RASTER_VALUE_MISMATCH (and NODATA_MASK_MISMATCH if mask asymmetric) |
| P5 | table, column dropped | FAIL / SCHEMA_MISMATCH |
| P6 | table, 0.0 vs NaN in one cell | FAIL / NULL_PATTERN_MISMATCH |
| P7 | table, identical rows shuffled (key declared) | PASS_EXACT |
| P8 | table, 4 % rows +1000 | FAIL / ATTRIBUTE_VALUE_MISMATCH (+NUMERIC_OFFSET) |
| P9 | vector, all attributes renamed and wrong | FAIL / SCHEMA_MISMATCH |
| P10 | vector, column dropped | FAIL / SCHEMA_MISMATCH |
| P11 | vector, identical, order reversed | PASS_EXACT |
| P12 | vector, 1 of 50 attributes wrong | FAIL / ATTRIBUTE_VALUE_MISMATCH (sample key) |
| P13 | identical points | PASS_EXACT (Hausdorff path; no IoU) |
| P14 | all strings wrong + 1/20 geometry moved | FAIL / ATTRIBUTE_VALUE_MISMATCH, GEOMETRY_HAUSDORFF_EXCEEDED |

Plus 09§7.7: multi-layer GPKG without layer → EVALUATOR_ERROR/LAYER_AMBIGUOUS; missing new output → FAIL/OUTPUT_MISSING; legacy inputs offered as reference → refused at pairing; shapefile 10-char truncation + `rename` KD → PASS_EXACT.

**6.2 Lux fixture** (mutants generated deterministically from legacy files or by one-line variants of an independent implementation; each must also be checked against the passing controls L1/L2):

| id | case | expected |
|---|---|---|
| L1 | identity: legacy vs copy; legacy run A vs run B (GPKG/XLSX bytes differ, PROV Check 1) | all PASS_EXACT; pipeline ladder `reference-verified` |
| L2 | independent rasterio/geopandas implementation, chained, structure repair | every dataset PASS_EXACT, float64 sums PASS_WITHIN_TOLERANCE (≤2e-16 rel); measured for 9 of 13 datasets (M), `res_4326`, `canton_factor`, GPKG/XLSX finals to confirm Wed |
| L2b | same with `make_valid` linework | s02: FAIL GEOMETRY_TYPE_MISMATCH (ds.res_*), NODATA_MASK_MISMATCH 3 cells (ds.res_mask); blame s02 own |
| L3 | −tap half-cell shift on ds.pop_lux | FAIL RASTER_TRANSFORM_MISMATCH `shift_px≈(0.5,−0.4997)`; `conservation.total` PASS_EXACT (shows why the grid gate exists); chained s02–s04 inherited; Q1 flags |
| L4 | `CROP_TO_CUTLINE` (953×881) | FAIL RASTER_SHAPE_MISMATCH, offset (−10,−1) cells |
| L5 | bilinear round trip of pop on the same grid | s01 FAIL RASTER_VALUE_MISMATCH + NODATA_MASK_MISMATCH (4,675) + TOTAL_DRIFT (−0.27 %); s03 ZONE_TOTAL_DRIFT ≤4.5 %; SMOOTHING_SUSPECTED; Q2 flags |
| L6 | mask rasterised cell-centre (no `-at`) | s02 FAIL NODATA_MASK_MISMATCH 24,377; s04 exposure per-canton totals PASS (by construction) but values/mask FAIL |
| L7 | factor rasterised all_touched | s04 FAIL RASTER_VALUE_MISMATCH (95 cells) + ZONE_TOTAL_DRIFT 1.79e-3 |
| L8 | names not stripped (Wiltz unmatched) | s04 FAIL NULL_PATTERN_MISMATCH (`vpc_eur`), ZONE_TOTAL_DRIFT (Wiltz 100 %); Q4 flags |
| L9 | kEUR not ×1000 | FAIL RASTER_VALUE_MISMATCH + NUMERIC_SCALE_FACTOR k=−3; Q7 flags |
| L10 | residential area in EPSG:3857 / in degrees | FAIL FEATURE_COUNT_MISMATCH + KEY_SET_EXTRA 573 / OUTPUT_EMPTY |
| L11 | ArcPy-03b semantics ported (0 not NoData; ExtractByMask with cantons) | s03 FAIL NODATA_MASK_MISMATCH; conservation PASS |
| L12 | exposure written float64 | FAIL DTYPE_MISMATCH; with accepted KD `dtype_widening` (new contract) → PASS_WITHIN_TOLERANCE, max rel 5.9e-8 listed |
| L13 | ds.res_4326 not produced and not in graph's new_path | NOT_COMPARED/UNPAIRED_LEGACY → pipeline not reference-verified |
| L14 | `tolerances.yaml` edited after run start | EVALUATOR_ERROR/TOLERANCE_CHANGED_AFTER_RUN |
| L15 | a `05_TEMP` file modified | EVALUATOR_ERROR/REFERENCE_HASH_MISMATCH (not FAIL) |
| L16 | isolation: s04 bug planted, s01–s03 correct | first_failing_steps = [s04], blame own; chained s04 FAIL, upstream PASS |
| L17 | visual receipt "looks fine" on L3; flag on L1 | L3 stays FAIL; L1 → NEEDS_REVIEW; no path upgrades; receipt with wrong plot sha rejected |
| L18 | repair loop given L7 | ≤3 attempts; no write to protected files (hook); success only after isolation + chained PASS |

Rubric calibration (10 B4): Q1–Q7 run on L1/L2 (must not flag) and their mutants (must flag) before the demo; results stored as `calibration/rubric.v1.json`.

## 7 Contradictions resolved

1. **08 vs 10 vs 09 vs 05 vocabularies.** 08 contract `verified|failed|partial|indeterminate|unsupported`; 10 C1 seven states; 09§7.3 `PASS|FAIL|WARN|ERROR|MISSING|UNTESTED`; 05 `PASS|FAIL|NOT_RUN`; PLAN "needs review". Chosen: §3.1. Mapping: verified→PASS_*; failed→FAIL; partial→abolished (aggregation shows mixed results explicitly); indeterminate→NEEDS_REVIEW/BLOCKED/EVALUATOR_ERROR (split by cause, as Arena splits evaluator from candidate, 10 B6); unsupported→NOT_COMPARED/FAMILY_UNSUPPORTED; WARN→abolished; MISSING→FAIL/OUTPUT_MISSING; ERROR→EVALUATOR_ERROR (checker) or FAIL/OUTPUT_UNREADABLE (candidate); UNTESTED/NOT_RUN→NOT_COMPARED or BLOCKED. 10's INCONCLUSIVE renamed NEEDS_REVIEW to match PLAN. July `DifferenceClass` (12) replaced by checker-assigned reason codes (§3.1). "Every non-verified state has a reason code" (08§3.1) kept; "next safe action" is derived per code in the report.
2. **TARGETED_ACCEPT** (08§4.5, 10 A6) is a review-process outcome (one independent review, P0/P1 repairs, ≤1 recheck), not parity. It never appears in `verdict.json`. Reused: the pre-demo human check (PLAN "How we know", 3) follows the same budget; the repair loop has its own cap (§4f).
3. **Default tolerances 09§7.6 vs Arena procedure.** 09§7.6 (`rtol 1e-6` float, grid "1 % of a pixel", `geom_tol 0.01 m`, attributes `rtol 1e-6`) are *candidate values*, not defaults: a value only takes effect as a record with both measured numbers; otherwise the check is exact. Grid: Arena 1e-6 px beats 1 % (a 1 % tolerance would still catch the 0.5-px lux trap, but not EP-05's sub-ppm cell-size drift over a large grid) and 05's 1e-9·res (below float noise in degrees on some writers; untested). PRD presets Strict/Standard/Lenient (08§3.2) are dropped: a preset is a global pair chosen after seeing results.
4. **09§7.3 "warp onto the legacy grid if `spec.align == reproject`"** vs 10 C5 / 05 "never resample to compare": no warping inside compare. Legitimate grid changes (IN-30) are an `accepted_change` KD that replaces cell checks by zone-total checks, reported as weaker evidence.
5. **09§7.3 auto-detected vector key; Arena `order` matching** vs D6-8: key must be declared in graph; auto-detection only suggests during discover.
6. **09§7.3 dtype/nodata "advisory"** vs 05 "equal or declared": hard (lux trap 7: Float32 exposure, Float64 factor is part of the contract).
7. **Judge may downgrade?** `SC/gis-agent-arena/docs/METHODOLOGY.md:111` says a judge can neither rescue nor downgrade; eo-agent-arena v0.3 and PLAN allow flagging. Resolved for migration: flag → NEEDS_REVIEW (a pending human look, like Arena's `pending`), never FAIL and never PASS; the deterministic verdict stays visible beside it.
8. **08§9.1 `verification-contract.json` vs shared convention `tolerances.yaml`**: `tolerances.yaml` + `known-differences.md` are the editable sources; `runs/<id>/contract.json` is the frozen, hashed snapshot.

## 8 Open questions for the founder

1. Accept dtype widening (float32→float64) only via a KD per dataset (proposed), or as a global policy? PROV trap 7 says the Float32 output is part of the legacy contract.
2. Grid gate 1e-6 px including the far-corner term fails grids whose cell sizes differ by ~1e-8 relative (EP-05). Intended strictness for customers whose legacy used a snap raster with rounded cell size?
3. Make visual receipts mandatory for `reference-verified` (proposed: yes, `VISUAL_REVIEW_MISSING`)? Costs ~1 image read per dataset (13 for lux).
4. Legacy memory-only intermediates (lux s02 reproject/fix/area, s03 `z1`) have no reference; steps are still covered by their written outputs. OK to never re-run legacy with extra taps (QGIS needed; legacy folder read-only)?
5. Repair cap 3 attempts per step: enough for the Thursday end-to-end run and demo time?
6. Show isolation-mode blame live in the demo (one extra run per failing step), or only chained + the first-failing-step table?
7. Should the ArcPy `03b` semantic difference (0 vs NoData, cantons vs dissolved outline) be raised as an interview question when the agent reads it, or left as trap L11 only?
8. Who signs tolerance records for the demo (proposed: you, at Plan, Wed 14) and may the agent pre-fill measured spreads from `controls/`?

## 9 Cross-section contract

**From graph.json (02)** — required, else `EVALUATOR_ERROR`: `steps[]{id, legacy{script, lines}, new{module, function}, inputs[], outputs[], status}`; `datasets[]{id, family: raster|vector|table, role: input|intermediate|final, legacy_path, layer|sheet, new_path, produced_by, consumed_by[], legacy_in_memory: bool, compare{key[] | match: geometry|full_row, measure, count, categorical, zones, zone_rule, ignore[], rename{}, header_row, data_rows, total_row}}`; `edges[]{from, to, dataset, evidence[{kind: static|runtime|user_asserted, ref: "file:line"}]}`; `invariants[]`; `legacy_output_dirs[]`. Step colour in `graph.html` reads `verdict.json.steps`.
**From run manifest (run section)**: `run_id, mode: chained|isolation, isolated_step, started_at, contract_hash, inputs[{path, sha256}], outputs[{dataset_id, path, sha256, written_by_step}], steps[{id, exit_code, started, ended}], env{python, gdal, proj, geos, rasterio, geopandas, shapely, numpy, pyogrio}`. Isolation harness copies legacy intermediates; generated code never opens legacy paths.
**From params.yaml**: `measure_crs` (lux EPSG:2169), zone rule, xlsx layout values; each with `source`.
**Owned here**: `tolerances.yaml` (§3.2), `known-differences.md` (§3.3), `references.json` check, `contract-history.jsonl` (append-only: contract_hash, version, changed records, reason, owner, time).
**To report**: `verdict.json`, `compare/*.json`, `plots/*/plots.json`, `visual-review.json`, `contract.json`; report must show FAILs first, `within_tolerance`, `excluded`, `not_checked`, tolerance two-number justifications, and any contract version created after a failing run ("tolerance changed after failure").
**To hooks**: Stop — "done"/"verified" requires latest chained `verdict.json` with `ladder == reference-verified`, its `contract_hash` == current, and `result_id`s matching files; PreToolUse — block writes to `tolerances.yaml`, `known-differences.md`, `references.json`, legacy folder and `runs/*/compare|verdict.json` while a run or repair loop is open, and any edit of `runs/*` by the model except `plot.py review-add`; scanner — block legacy-path reads in `pipeline/`; hook errors block (fail closed, 08§9.7).
