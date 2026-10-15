# Plan, then build the codebase step by step (read from SKILL.md §Flow 3–5)

`$P` = plugin root. Run scripts as `"$P/scripts/axpy" <script> …`: `$P/skills/discover/graph.py`,
`$P/skills/run/run_local.py`, `$P/skills/verify/compare.py`, `$P/skills/guard/codescan.py`,
`$P/skills/interview/params_check.py`. Graph writes only via `graph.py apply migration --actor agent --base-rev N --ops ops.json`
(op shapes: `$P/skills/discover/GRAPH_OPS.md`). Step contract and example: `reference/F10-pipeline-runtime-contract.md`,
`templates/step_template.py`. Reference entries are cited by id (`G-1`, `N-2`, …) in plans and decisions.

## 0 Preconditions (refuse to start otherwise; say which one fails)
`graph.py validate migration` and `params_check.py migration` exit 0; every question on the lineage of a final is
answered or assumed in `decisions.md`; every dataset of a `migrate` step has `kind` (and CRS, NoData, `compare.measure`
where they apply) and its legacy file in `inventory.json`; `migration/tolerances.yaml` exists (verify skill).

## 1 Plan (a hook denies `pipeline/sNN_*.py` until the step's plan is approved)
Per in-scope step (`scope: migrate`), read its legacy lines and the reference files the router names, then `set_plan`:
`legacy_ops[]` (tool, `ref` = legacy `file:line[-line]`, settings as called incl. env in force) · `oss[]` (op, `why`:
the legacy semantics it reproduces, `skill_refs`) · `params` (every `p.*` it reads) · `outputs_checked[]` (dataset,
family, measure, checks, `tolerance_ids`, empty = exact) · `risks[]` (`skill_ref` + risk + evidence, or `"risk": "none"`)
· `isolation` (`possible`, `legacy_inputs`, `missing` = inputs with no legacy file). Then `graph.py render-plan migration`,
show `migration/plan.md` in the file pane (SKILL.md §Showing), summarise it in a few lines, and ask ONE AskUserQuestion: "Approve the plan (Recommended)" /
"Change something". Approve → `{"type": "approve_plan", "steps": "*"}` via `graph.py apply … --channel interview
--verbatim "<the user's words>"`. Change → revise with `set_plan`, re-render, ask again. Never approve without the user.
Codex: the same question with `request_user_input_async` when it exists, else as plain text, numbered, "Approve the
plan (Recommended)" first; stop and wait (an elapsed wait is never an answer); record the user's words as `--verbatim`.
After approval, once: `run_local.py migration --vendor-runtime` (copies the runtime into `pipeline/`; never edit it).

## 2 Per-step loop (topological order; a failure stops the walk at that step)
1. Open the reference files for this step's legacy ops (router, §6) and F10. Write `pipeline/<step id>.py` only, with
   `STEP_ID, INPUTS, OUTPUTS, PARAMS` equal to the step in `graph.py show migration --json` and `def run(ctx) -> None`.
   Record it: `set_new` `{"id": "<step id>", "new": {"module": "pipeline/<step id>.py", "function": "run"}}`.
2. The PostToolUse scanner reports on every write; fix each block finding before running. Recheck with
   `codescan.py --file migration/pipeline/<step id>.py --migration migration`.
3. Isolated run: `run_local.py migration --chain isolated --steps <step id>` (add the producer when `isolation.missing`
   is non-empty). The runner stages the legacy copies of the inputs; the code cannot tell. Exit 12 or a step exit 2
   (input or parameter) → interview, not code. Exit 11 → read the reason code and fix the cause, never work around it.
4. Verify that step (verify skill): `compare.py run --run migration/runs/<run id>`, then plots, **look at every plot**,
   and record findings with `plot.py review-add`. Isolated verdicts are diagnostic. FAIL → repair (§3); PASS → next.

## 3 Repair (at most 3 attempts per step)
Work from the step spec (plan, declared I/O, params) and the current failure only (innermost traceback in `pipeline/`,
or reason codes such as `RASTER_TRANSFORM_MISMATCH` with `shift_px`, and the plots). Edit only the blamed step module;
never `tolerances.yaml`, `compare.py`, legacy files or the vendored runtime. A different parameter value is an interview
question (answer → `params.yaml` → `decisions.md`), never a literal in code. Stop and ask the user when the same
failure signature survives two distinct patches, or when the cause is not code (input, parameter, environment).

## 4 After the last step
First `params_check.py migration --fill-used-by`, then `params_check.py migration --stage wired` (must exit 0; any
edit of `params.yaml` after a run marks that run stale). Then the chained run from raw inputs, `run_local.py migration
--chain chained`, verified in full (verify skill); only chained runs move rungs, and a step that passed isolated but
fails chained is upstream drift to report, never to widen. Smoke tests (`migration/tests/`) never mock geo libs.

## 5 Manual steps, bridges, unported and dropped code
- Manual step (`kind: manual`): the user's answer decides. Port-as-code: rule in `params.yaml`, `set_scope migrate`,
  verified against the hand-made file. Bridge: `set_scope keep_legacy`; the artefact becomes a hash-pinned input.
- No OSS equivalent or kept by the owner: `set_scope keep_legacy` citing the answered question; its outputs become
  hash-pinned inputs, shown `BRIDGED` (never a pass); finals downstream read "partial migration (n bridged)". Turning a
  step that failed verify into a bridge needs the user's decision (the hook asks). A migrated output that a kept legacy
  consumer reads gets a `bNN_*` step of `kind: bridge` that writes the legacy format, compared after the round trip.
- Unported code raises `NotImplementedError`. `drop` steps (dead or alternative code) are not ported. If the user switches one to `migrate`, translate
  it, rewire inputs that have no file to existing datasets via its `equivalent_to` edges (`graph.py validate` lists
  them as UNPRODUCIBLE_INPUT), verify it against the equivalent step's references (the user pairs them on the graph
  page) and record every difference as a proposed KD with `record.py kd`; a difference is never a pass.

## 6 Router: legacy operation → reference file (`$P/skills/migrate/reference/`)
| Legacy operation | Read |
|---|---|
| snap raster, cell size, extent, output CRS env; QGIS extent/resolution options; clip, warp, window; ≥2 rasters | `F1-raster-grid-alignment.md` |
| resample, aggregate, disaggregate, `Int()`, rounding, scale or unit factors | `F2-resampling-and-counts.md` |
| raster algebra, `Con`/`SetNull`/`IsNull`, masks, `CellStatistics`, raster writes | `F3-nodata-masks-dtypes.md` |
| rasterise, zonal statistics, raster clip, extract by mask | `F4-raster-vector-conversion.md` |
| buffer, distance, near, area, any CRS literal or transform | `F5-crs-distance-units.md` |
| spatial join, select by location, near, attribute join, overlay, dissolve | `F6-vector-overlay-joins.md` |
| repair geometry, dissolve, union, simplify | `F7-geometry-validity-simplify.md` |
| any ArcPy or PyQGIS script, QGIS model or project (before planning) | `F8-legacy-tool-semantics.md` |
| xlsx, csv, notebooks, manual notes | `F9-tabular-excel-notebooks.md` |
| always, before writing any step | `F10-pipeline-runtime-contract.md` |
| Databricks target or cloud-mount I/O | `F11-target-runtime-io.md` |
