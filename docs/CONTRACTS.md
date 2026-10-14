# axis-migrate — build contracts (binding for every builder)

Status: 13 Oct 2026, orchestrator. `docs/DESIGN.md` is the design; section files `docs/design/01–10` hold schemas and
acceptance tests. This file fixes the **interfaces between modules** so parallel builders produce parts that fit.
Where this file is more specific than DESIGN, follow this file; if you find a real conflict with DESIGN, note it in
your report (do not silently diverge).

## 0 Repo, plugin, interpreter, tests

- Plugin root (shipped): `~/axis-migrate-dev/axis-migrate/` = `$PLUGIN`. Dev-only material lives **outside** it:
  `~/axis-migrate-dev/devtests/` (lux-specific tests), `~/axis-migrate-dev/evals/` (golden pipeline, cases, harness).
- **No fixture answers inside `$PLUGIN`** (D4-14). The agent under test can read the plugin dir. Plugin tests
  (`$PLUGIN/skills/*/tests/`, `$PLUGIN/hooks/tests/`) use only **synthetic** data built in `conftest.py`. Any test that
  encodes lux facts (expected shifts, counts, canton names, op lines, trap mutations) goes to `devtests/<area>/`.
  Never read `fixtures/lux-exposure-legacy.PROVENANCE.md` or `fixtures/*.build/` from plugin code. Devtests may read
  PROVENANCE as an oracle.
- Never modify `fixtures/lux-exposure-legacy/`. Tests copy it to a tmp dir (`shutil.copytree`) before any mutation.
  `GDAL_PAM_ENABLED=NO` always (a stats read writes `.aux.xml` sidecars into the folder).
- Interpreter: every non-hook script runs as `$PLUGIN/bin/axpy <script.py> …` (the one pinned env,
  `skills/run/env/uv.lock`, Python 3.12.15, numpy 2.5.4, rasterio 1.5.2 (GDAL 3.12.2), pyogrio 0.13.0 (GDAL 3.12.4),
  shapely 2.2.0, pyproj 3.8.0 (PROJ 9.8.1), geopandas 1.2.0, pandas 3.0.6, openpyxl, matplotlib, pyyaml, jinja2,
  pyarrow, pytest, defusedxml, e2b). Run tests with `$PLUGIN/bin/axpy -m pytest <dir> -q`. Adding a dependency =
  edit `skills/run/env/pyproject.toml` + `uv lock` — ask the orchestrator first (one lock, one owner).
- Hooks run as `python3 -I` (system 3.12, **stdlib only**). `skills/report/ladder.py` and `lib/axis_common.py` must stay
  stdlib-only because hooks import them.
- Shared helpers: `$PLUGIN/lib/axis_common.py` (stdlib): vocabularies (verdict precedence `worst()`, rungs, exit
  codes, dispositions, visual categories), `cjson()` (axis-cjson/1), `object_id(kind, obj)`, `dump_json()` (atomic,
  NaN refused), `sha256_file`, `tree_hash`, `gpkg_content_sha256`, `xlsx_content_sha256`, `content_sha256`,
  `find_migration_dir`, `legacy_root`, `slug`, `is_sidecar`. Import it as
  `sys.path.insert(0, str(Path(__file__).resolve().parents[N] / "lib")); import axis_common as ac`.
  Do not fork these; ask the orchestrator to extend them.
- Every CLI: `argparse`, `--help`, JSON on stdout only when `--json`, human text otherwise, deterministic output
  (sorted keys, sorted lists), exit codes as DESIGN §3. British English in user-facing text.

## 1 Workspace

```
<project>/                         cwd when the user runs /migrate ./<legacy>
  <legacy>/                        read-only legacy folder (never written by anything)
  migration/
    inventory.json  discover/{profiles/*.json, scan.log, *.png}
    graph.json  graph-edits.jsonl  .graph/{lock, rev/<n>.json, layout.json}   (only via graph.py)
    params.yaml  decisions.md  known-differences.md  tolerances.yaml  contract-history.jsonl
    plan.md (rendered by graph.py render-plan)
    analysis/                      agent scratch; nothing imports it
    pipeline/                      the package that becomes `axis_pipeline` (see §5)
    tests/                         smoke tests only
    runs/<run_id>/ …  runs/index.jsonl
    report/  databricks/  .axis/protected.json
```
`graph.json.project.legacy_root` is the legacy folder **relative to `migration/`** (lux: `"../lux-exposure-legacy"`).
All stored paths are POSIX; legacy paths are legacy-root-relative; run paths are run-dir-relative. No absolute path in
any hashed object.

## 2 Canonical ids (DESIGN §3; lux values are fixed here so all builders agree)

- `slug(x)` = lowercase, runs of `[^a-z0-9]` → `_`, stripped. Dataset `ds.<slug(stem)>`; shared stem across files →
  `ds.<stem>_<ext>`; layer suffix `__<slug(layer)>` only for a multi-layer container whose layer ≠ stem (gdb items).
- Lux datasets — inputs: `ds.lux_ppp_2020` (03_INPUT/lux_ppp_2020.tif), `ds.cantons_lux_edit`
  (03_INPUT/cantons_lux_edit.gpkg | layer `cantons`), `ds.osm_landuse_lux` (03_INPUT/osm_landuse_lux.gpkg | `landuse`),
  `ds.value_per_capita` (03_INPUT/value_per_capita.xlsx), `ds.geoboundaries_lux_adm1` (input of the manual step),
  `ds.geoboundaries_lux_adm2` (role `unused`, display only). Intermediates (05_TEMP): `ds.01_lux_outline`,
  `ds.01_pop_lux`, `ds.02_res_2169`, `ds.02_res_4326`, `ds.02_res_mask`, `ds.03_pop_res`, `ds.03_canton_pop`,
  `ds.04_canton_factor`, `ds.04_factor`. Finals (04_FINAL): `ds.lux_res_exposure_2020`, `ds.exposure_by_canton_gpkg`,
  `ds.exposure_by_canton_csv`, `ds.exposure_by_canton_xlsx`. 03b gdb items (`exists:false`): `ds.work__res_mask`,
  `ds.work__cantons_edit`, `ds.work__pop_lux`, `ds.work__pop_res`, `ds.work__zs_pop`, `ds.work__zs_res`,
  `ds.work__canton_pop`.
- Lux steps: `m01_cantons_lux_edit` (manual), `s01_prepare_population`, `s02_landuse_mask`, `s03_zonal_population`,
  `s03b_zonal_arcpy_old` (scope `drop`, `scope_reason: alternative_impl`), `s04_distribute_value`. Ops `<step>.opK`.
- Params `p.<slug>` with `node: <step id>`; questions `q01…`; decisions `D-001…`; KDs `KD-001…`; tolerance records
  `tol.<dataset>.<check>.v<k>` (e.g. `tol.lux_res_exposure_2020.raster.values.v1`).
- Run id `r<UTC yyyymmddThhmmssZ>-<code_hash[:8]>` (code_hash hex of the pipeline tree hash).

## 3 inventory.json (owner: discover/scan.py) — schema `axis-migrate.inventory.v1`, as 01 §3

Consumers rely on: `legacy_root`, `scan{read_only_verified, mtime_reliable, legacy_roots[], manifest_sha256}`,
`files[]{path, size, sha256, content_sha256, kind, dataset_ids, sidecars, sidecar_of}`,
`datasets[]{id, legacy_path, layer, format, kind: raster|vector|table|document, exists, role: input|intermediate|final|unused|unknown,
role_evidence[], reference_candidate, produced_by[op ids], consumed_by[op ids], grid_id, profile_ref, compare_hints}`,
`ops[]` (01 §3 op record), `code[]`, `settings[]`, `params[]`, `memory_datasets[]`, `step_candidates[]`,
`manual_step_candidates[]`, `derived_copies[]`, `twins[]`, `projects[]`, `docs[]`, `grids[]`, `extent_relations[]`,
`open_items[]`, `map{pngs, findings}`, `annotations{}`. The file hash baseline (`files[].sha256` incl. pre-existing
`.aux.xml`) is the **reference pin** used by compare (`REFERENCE_HASH_MISMATCH`) and the hooks (`LEGACY_REF_CHANGED`).

## 4 graph.json (owner: discover/graph.py; content first produced by discover/lineage.py) — `axis-migrate.graph.v1`

02 §3 schema with DESIGN ids and these binding fields (consumers may rely on exactly these):

```
project{name, legacy_root, legacy_tree_sha256, plugin_version, created_at}
revision, updated{rev, by, at}, acks{agent_seq}
steps[]{id, title, kind, tool, scope: migrate|keep_legacy|drop, scope_reason, review,
        legacy{source, lines:[[a,b]], executed}, ops[], params[p ids],
        inputs[ds ids], outputs[ds ids],                 # DERIVED from edges by graph.py on every write
        new{module: "pipeline/<step>.py"|null, function: "run", code_sha256},
        plan{…04 §3.2…, approved{by, at, channel, verbatim}} | null,
        timeout_s (default 120), ladder{…projection}, verify{verdict, blame}, provenance}
datasets[]{id, title, kind: raster|vector|table|document, role: input|intermediate|final|unused,
        persistence, legacy{path, layer, sheet, exists, sha256, content_sha256, profile, sidecars},
        new{path: "<producer step>/<file name>", layer} | null,        # relative to runs/<id>/outputs/
        produced_by{step, op} | null, consumed_by[step ids],          # DERIVED
        reference{source: legacy|equivalent|none, match_method: lineage|human, via_dataset, confirmed_by},
        legacy_in_memory: bool, no_reference_reason: null|legacy_in_memory|…,
        compare{measure: extensive|intensive|categorical|mask|index|null, count: bool, categorical: bool,
                key: [cols]|null, match: key|geometry|full_row|null, layer, zones: "<ds>:<col>"|null,
                zone_rule: centre|all_touched|null, ignore: [], rename: {}, sheet, header_row, data_rows: [a,b],
                total_row, measure_crs_param: "p.measure_crs"|null, may_be_empty: false},
        verify{run_id, verdict, reason_codes, report, report_sha256, plots, visual_flag, blame, waiver},
        review, evidence[], provenance}
edges[]{id, from, to, kind: reads|writes|reads_grid|reads_write|equivalent_to|displays, op, lines,
        state, conflict, review, evidence[{state, ref, summary, by, rev}]}
questions[]  (03 §3.2 shape; id q01…; status open|answered|assumed|dismissed)
invariants[]{id, kind: zone_sum_equals|min_ge|total_equals, lhs, rhs, rule, tolerance_ref}
legacy_output_dirs[]   (lux: ["04_FINAL", "05_TEMP"])
ladder{project, derived_rev, blocking[]}   (projection written only by derive from ladder.py)
```
Default new output name: `new.path = "<producer step id>/<legacy basename>"` (e.g.
`s01_prepare_population/01_pop_lux.tif`), `new.layer = legacy.layer` (GPKG layer names are part of the contract).
Datasets without a legacy file: `<step>/<id without "ds.">.<ext>`.

`graph.py` CLI (all writes go through `apply`; `--actor user` is refused on the CLI):
`graph.py init <migration> --from <draft.json>` (rev 1; lineage uses this) ·
`graph.py apply <migration> --actor agent|verify|derive --base-rev N --ops ops.json [--channel interview --verbatim "…"]`
(exit 0 / 3 conflict 409 / 4 ACL `user_owned_field` / 2 invalid) · `graph.py show <migration> [--json]` ·
`graph.py diff --since <rev>` · `graph.py ack <seq>` · `graph.py derive <migration>` ·
`graph.py ingest-verify <migration> --run <run_id>` · `graph.py validate` · `graph.py render-plan` (writes plan.md) ·
`graph.py protected` (writes `.axis/protected.json`).
Op types (minimum): `add_step, add_dataset, add_edge, set_field, set_scope, add_question, answer_question,
set_plan, approve_plan, set_new, set_reference, waive_verify, reject_edge, add_note, confirm, rename, submit` — exact
JSON shapes are defined and documented by the graph builder in `skills/discover/GRAPH_OPS.md`.

## 5 Pipeline package and the step contract (owner: migrate/runtime/_axis_runtime.py)

`migration/pipeline/` = package directory: `__init__.py`, `_axis_runtime.py` (vendored copy of
`$PLUGIN/skills/migrate/runtime/_axis_runtime.py`, byte-identical), `__main__.py` (vendored), `sNN_*.py` / `mNN_*.py`
(model-written, one per step). It is imported as **`axis_pipeline`** (runner snapshot copies `pipeline/` to
`snapshot/axis_pipeline/`; the wheel maps it the same way). Step modules import only stdlib, the lock's libraries and
nothing from the package (they receive `ctx`).

```python
STEP_ID = "s02_landuse_mask"
INPUTS  = ["ds.osm_landuse_lux", "ds.01_pop_lux"]                # == graph steps[].inputs
OUTPUTS = ["ds.02_res_2169", "ds.02_res_4326", "ds.02_res_mask"] # == graph steps[].outputs
PARAMS  = ["p.min_area_m2", "p.area_crs", "p.rasterize_all_touched", "p.mask_nodata"]
def run(ctx) -> None: ...
```
`Ctx` API (exact names):
- `ctx.param(pid)` → value; no default argument; undeclared or absent → `ParamMissing` (exit 2). Every read is logged.
- `ctx.input_path(ds)` / `ctx.output_path(ds)` → `pathlib.Path` (output path is a local scratch path; the runtime
  commits it atomically after `run()` returns; for seek-less roots inputs of GPKG/SQLite/XLSX are staged locally).
- `ctx.input_layer(ds)` / `ctx.output_layer(ds)` → declared layer name or None.
- `ctx.grid_of(ds)` → `Grid(crs, transform, width, height)` (from an input raster).
- `ctx.read_raster(ds, band=1, masked=True)` → `(numpy.ma.MaskedArray, profile: dict)`.
- `ctx.write_raster(ds, array, *, grid, dtype, nodata, **creation_options)` — refuses a write without explicit grid
  and nodata keyword (nodata may be `None` explicitly), checks dtype and shape vs grid.
- `ctx.read_vector(ds, layer=None)` → GeoDataFrame (declared layer by default); `ctx.write_vector(ds, gdf, layer=None)`.
- `ctx.open_workbook(ds, data_only=True)` (openpyxl); `ctx.save_workbook(ds, wb)`; `ctx.write_csv(ds, header, rows)`.
- `ctx.log` (logging.Logger). Undeclared dataset ids → `DatasetNotDeclared` (exit 3 for outputs, 2 for inputs).
Exceptions: `ParamMissing`, `InputMissing` (exit 2), `DatasetNotDeclared`, `OutputContractError` (exit 3).
Runtime writes `logs/<step>.io.json`: `{step_id, params_read{pid: value}, reads[{dataset, path, sha256, size}],
writes[{dataset, path, sha256, size, stats{sum,count,nodata_count,min,max}}]}`.

`ctx.json` (written by the runner per step, `runs/<id>/ctx/<step>.json`, schema `axis-migrate.ctx.v1`):
`{run_id, step_id, chain, inputs{ds: {path(abs), layer, kind, format, source: input|run|legacy_isolation|bridge}},
outputs{ds: {path(abs final), layer, kind, format}}, params_file(abs), declared_params[], scratch_dir,
no_seek_roots[], io_log(abs)}`.
Target/wheel entry point: `python -m axis_pipeline --step <id> --input-root R --work-root W --output-root O
--scratch-dir S [--no-seek-root P]…` builds the same Ctx from roots + packaged `_datasets.json` + packaged
`params.yaml` (layout: inputs at `R/<legacy relpath>`, intermediates at `W/<new.path>`, finals at `O/<new.path>`).

## 6 Run directory (owner: run/run_local.py + runlib.py) — DESIGN §4.5

```
runs/<run_id>/
  snapshot/{axis_pipeline/, params.yaml, graph.json, inventory.json, tolerances.yaml, known-differences.md,
            decisions.md, uv.lock, pyproject.toml}
  ctx/<step>.json  stage/{inputs/<legacy relpath>, legacy_ro/<legacy relpath>}  outputs/<step>/  outputs/<step>.failed/
  logs/<step>.{stdout,stderr,reads,result.json,io.json}  env/{probe.json, freeze.txt}
  manifest.json  checksums.sha256          ← all of the above made read-only (chmod a-w) at run end
  verify/                                  ← created empty and writable; compare/plot/review write here; seal.py
                                             later makes it read-only and writes evidence.json + EVIDENCE_ID
```
`manifest.json` = 05 §3 schema (`axis-migrate.run-manifest.v1`) with: `run_id, chain: chained|isolated, executor:
local|e2b|refenv, status, exit_code, reason_codes, steps_planned, steps_succeeded, full (bool), eligible_for
(never trusted), read_ledger: strace|audit+cpl_debug|e2b-physical, code{code_hash, files[], worktree_changed_during_run},
params{sha256}, graph{sha256}, tolerances{sha256}, known_differences{sha256}, env{lock_hash, libs, native, proj,
stub_check}, reference_env, env_comparison, inputs[{dataset_id, legacy_path, staged, sha256, content_sha256,
unchanged_after_run}], legacy_feeds[], steps[{step_id, status, exit_code, reason_codes, fed_from, outputs[{dataset_id,
path, sha256, bytes}], reads{ledger, undeclared[]}, writes_ledger[]}], outputs[{dataset_id, path, sha256,
written_by_step}], started_at, ended_at, resumed_from`.
CLI: `axpy run_local.py <migration> [--chain chained|isolated] [--steps a,b] [--env pipeline|refenv]
[--resume-from <run_id>] [--json]` → prints run_id; exit codes 0/10/11/12/13/14/15/70. Preflight runs
`guard/codescan.py --all` (block → exit 11 `SCAN_BLOCKED`) and smoke tests (`verify/tests.json`, `verify/scan.json`).
Appends `{run_id, chain, executor, status, exit_code, started_at}` to `runs/index.jsonl` (seal adds the evidence id).

## 7 Verify outputs (owner: verify/compare.py, verify/plot.py)

- `verify/contract.json`: frozen from the run's **snapshot** (tolerance records, accepted KD rules, pairing projection,
  reference pins from snapshot inventory, comparator id+sha); `contract_hash`. Live tolerances/KD/graph-pairing ≠
  snapshot → `EVALUATOR_ERROR/TOLERANCE_CHANGED_AFTER_RUN` (graph: `GRAPH_CHANGED_AFTER_RUN`).
- `verify/compare/<ds>.json`: 06 §3.1 `axis-migrate.compare-result.v1` exactly, plus `refs{out: "out:<sha>",
  ref: "ref:<sha>", tol: ["tol:…"]}` and `by_construction` flags per check. Paths in it relative to the project.
- `verify/plots/<ds>/*.png` + `plots.json` (06 §4g); `verify/display/<ds>.{legacy,new,diff}.png` + `display.json`
  (07 §9, by `report/map.py display`); `verify/visual-review.json` = `{schema, rubric_id, rubric_sha256, findings[]}`
  appended **only** by `plot.py review-add` (after seal: `runs/<id>/amend/visual-review.json` + `seal.py --amend`);
  `verify/views/` = GeoLibre view projects + COGs + `views.json` (`skills/map/views.py`, display only; after seal
  `amend/views/`); `verify/verdict.json` (06 §4d, **no rung**); `verify/scan.json`,
  `verify/tests.json` (runner preflight).
- CLIs: `compare.py run --run <dir> [--dataset ds]…` · `compare.py aggregate --run <dir> [--isolation-run <dir>]…`
  · `compare.py pair --legacy L --new N --spec spec.json [--out x.json]` (standalone, for T0/score/target dry run)
  · `plot.py run --run <dir> [--dataset ds]` · `plot.py review-add --run <dir> --json finding.json`.
  Exit 0 only when every requested dataset is PASS_*.
- **Visual receipts** (one acceptance rule, `lib/visual_receipt.py`, used by review-add/review-status, aggregate,
  seal, ladder and the Stop gate). Common: `dataset_id, question_id (Q1…Q7 | Q-all), answer yes|no|cannot_tell,
  effect flag|none (yes ⇔ flag), category (closed list), note ≤ 200, model`; set by review-add only: `run_id,
  finding_id, rubric_id, rubric_sha256, created_at` (+ `receipt: "view"`). **PNG receipt**: `plot, plot_sha256, panel,
  location{crs, bbox}` in the plot's map coordinates, `plots_seen` for Q-all; counts when the sha is a plot of this
  run. **View receipt**: `view{project, project_sha256}`, `views_seen[{project, project_sha256}]` (raster: overview,
  worst, edges, nodata + the `cell-<id>` project of each cell read; vector: overview, worst), `engine` (raster:
  `maplibre-gl-raster`, else refused), `cells_read[{cell, legacy, new}]` (raster: ≥ 1, equal to views.json to ~3
  significant figures, null = nodata), `layer?`, `location{crs: EPSG:4326, bbox}` and `observations[{what, value,
  unit?}]` (both required for a yes); paths relative to the run under `verify/views` or `amend/views`; counts while
  every cited project's bytes equal the receipt's sha and views.json; a Q-all "no" is refused (and does not count)
  while views.json `validity_counts` has cells valid on one side only. Either kind is a receipt; a flag moves only
  PASS_* to NEEDS_REVIEW (`VISUAL_FLAG`); nothing moves a verdict or rung up; `cannot_tell` is not a receipt.

## 8 Records (formats)

- `params.yaml`: 03 §3.1 (`version, budget, params[]{id, node, kind, value, unit, resolution, source, evidence[],
  reason, confidence, method, flags, impact, status, question, decisions, used_by, fit}`) + `target:` block (09 §3.2).
- `tolerances.yaml`: 06 §3.2 (`version, defaults, records[]` with `correct_spread`, `nearest_wrong`, `agreed_by`,
  `agreed_at`, `decision_ref`, `fixed_before`). A record with `agreed_by: null` is **unsigned** → ignored
  (`TOLERANCE_UNJUSTIFIED`, check runs exact). Only a human signs (Najah); never fill `agreed_by` for him.
- `known-differences.md`: `## KD-nnn · title` + one fenced ```yaml block (DESIGN §4.6 schema: `id, kind, datasets,
  checks, rule, bound{metric,max}|null, reason, evidence, owner: human:<name>|agent, decided_at, decision_ref,
  status: proposed|accepted|withdrawn`). Only `accepted` + `owner: human:*` count.
- `decisions.md`: `## D-nnn · title · timestamp` + one fenced ```yaml block (`id, question, kind, answer, by, params,
  nodes, changes, supersedes, evidence`). Append-only.
- `migration/SIGN-OFF.md` (dev convenience for the founder): every unsigned tolerance record and proposed KD with its
  two measured numbers, to sign by editing `agreed_by`/`status` himself.

## 9 Evidence, ladder, report (owner: report/)

- `seal.py <migration> --run <run_id>` → `verify/` read-only, `evidence.json` (07 §3.3), `EVIDENCE_ID`, line in
  `runs/index.jsonl`. `seal.py --amend` adds only `vis`, `view`, `dec-e`, `kd-e`, `tgt`. `view` objects bind the view
  projects listed in views.json (`verify/views` at seal, `amend/views` by --amend); a view object whose file changed
  is `VIEW_CHANGED` (information: its receipts stop counting), never EVIDENCE INCOMPLETE.
- `ladder.py` (stdlib): `compute(migration_dir, run_id=None) -> {pipeline{rung, suffix, blockers[], counts},
  steps{id: {rung, disposition, caps[], annotations[]}}, integrity[], run_id, status_line}`; CLI
  `ladder.py <migration> [--run id] [--json]`. Uses the newest sealed run in `runs/index.jsonl` by default.
  `status_line` = `axis-migrate status: <rung>[ · partial (n bridged)] | run <id> | <p> pass, <f> FAIL, <n>
  NOT_COMPARED, <r> needs review | report migration/report/index.html`.
- `report.py <migration> [--run id]` → `report/index.html`, `report/assets/…`, `report/REPORT.json{status, rung,
  steps{id: disposition}, assumptions[], known_differences[], files{path: sha256}, evidence_id}`; exit 0/2/3.
  Per compared raster/vector the page has a "Map views" block (`id="views-<ds>"`, linked from the swipe map): the
  views.json projects (`verify/views`, `amend/views`) with path, sha256, sealed yes/no and `matches` / `changed after
  the review` / `changed since views.json` / `missing`, the validity layer with its counts, the dataset's sealed
  receipts (PNG or view) with counts-or-why-not (ladder `visual.counted`), and the open commands; no port, server
  address or network URL; no views.json → "No map views for this run". Static, offline, deterministic.

## 10 Target (owner: target/)

`make_databricks.py <migration>` → `databricks/{databricks.yml, pyproject.toml, requirements-target.lock,
runtime-manifest.json, dist/*.whl, README-deploy.md}`; `target_check.py <migration> --run <run_id>` → T1–T13,
`databricks/{target-check.json, target-manifest.json}` (`deployment_status: "not_deployed"`), dry-run evidence in
`runs/<id>/target/` (sealed via `seal.py --amend --target`). Ladder reads `target-manifest.json` + `target-check.json`.

## 11 Hooks (owner: hooks/ + guard/)

`hooks/hooks.json` H1–H7 (DESIGN §5), scripts in `hooks/bin/` (stdlib). Script halves: `guard/codescan.py`
(`--hook`, `--all <migration>`, `--file f`; `scan.json`; exit 0/2/3), `guard/protected.py`, `guard/claims.py`
(claim regex → minimum rung, used by the Stop gate). Every guard fails closed.
