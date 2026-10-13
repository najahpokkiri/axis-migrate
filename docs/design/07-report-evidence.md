# 07 — Report and evidence bundle

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Section owner: design subagent 07. Date: 13 Oct 2026. Status: proposal for the founder.
Sources are cited as `08§4.5` (skill-candidate file and section) or `path:line`. Short prefixes:
`ARC/` = `~/src/axis-spatial/_archive/`, `MD/` = `~/src/server-copies/axis-spatial-deploy-mcp-v2/`,
`EVJ/` = `~/src/axis-spatial-mcp-v2/docs/evidence/2026-07-18-migration-delivery/` (July bundle copy).

## 1 Scope and interfaces

This section owns three things: (a) the **evidence bundle** in each `runs/<run_id>/` (the `evidence.json`
index, how ids are made, how the bundle is sealed and checked); (b) **`ladder.py`**, the one function
that turns a bundle into step and pipeline rungs, used by `report.py` *and* the Stop hook; (c)
**`report.py`**, which renders `migration/report/` from one sealed bundle and nothing else.

It does not own `compare.py` states or checks (verify section), plot generation (`plot.py`), display
layers (`map.py`), `graph.json`, `params.yaml`, `tolerances.yaml`, or the Databricks files. It consumes
them through the contract in §9 and refuses to guess when one is missing.

Flow: `run` seals inputs at start → pipeline runs → `compare.py` → `plot.py` / `map.py` → agent's visual
review → `seal.py` writes `evidence.json` and freezes the run → `report.py` renders → Stop hook calls
`ladder.py` on the same bundle.

## 2 Decisions

| # | Decision | Evidence |
|---|---|---|
| D7-1 | The rung is **computed at render time** by `ladder.py` from hashed evidence. No file stores a rung that anyone trusts; a declared status is never read. | Declared states never produced (07 HL-28); success-vocabulary mismatch reported `complete` for a stopped pipeline (05 FH-02); July acceptance rows whose `verify_cmd` only read their own `status` (10 A8.6). |
| D7-2 | One sealed run = one bundle = one report. `reference-verified` needs `run.full = true` (all steps executed in that run from raw inputs). Partial reruns are fine while fixing, but cannot carry a rung above `sandbox-validated`. | Immutable runs, quoted numbers cite a run id (07 HL-27); July "unchanged rerun idempotent" row MD-22 (08§4.4). |
| D7-3 | Editable project files (`graph.json`, `params.yaml`, `tolerances.yaml`, `decisions.md`, `known-differences.md`, `uv.lock`, `pipeline/`) are **copied into `runs/<id>/sealed/` at run start**. The report renders from the sealed copies and compares them with the live files. Any drift is shown as a banner and withdraws rungs (§3.4). | Tolerances fixed before or after was never resolved (08§8.5); C2.6 "change after run start → `TOLERANCE_CHANGED_AFTER_RUN`" (10 C2); hook proposal 08§9.7. |
| D7-4 | Ids are content hashes: `sha256` of file bytes for files, of **axis-cjson/1** for JSON the plugin writes, with volatile keys excluded from the id but kept in the file. | July `parity:<sha256>` over `canonicalJson`, `reportHash` excludes `comparedAt` (`MD/packages/runtime-core/src/migration-delivery.ts:291-299,366-367`); Arena hash form `sha256:<64-hex>` (`server-copies/gis-agent-arena/docs/product-foundry/2026-07-12-arena-realization-plan.md:207`). |
| D7-5 | axis-cjson/1 sorts keys by **Unicode code point**, rejects NaN/±Inf, writes floats with Python `repr`. The July `canonicalJson` sorts with `localeCompare` (`migration-delivery.ts:294`), which depends on the locale, so it is not reused as is. | `migration-delivery.ts:294`. |
| D7-6 | `report.py` **verifies every hash on every render** and re-derives each compare state from its checks. On any mismatch or missing file it still writes a page, but the headline reads **EVIDENCE INCOMPLETE**, no rung is shown, and it exits 2. | The July bundle drifted unnoticed: one commit after the evidence commit `902530dd5`, an ingestion tool added front-matter to 4 of the 8 hashed files (`git diff 902530dd5 1f1b1c43a`), so `EVJ/bundle-checksums.json` no longer matches (I rehashed them: 4 of 8 fail). Nothing re-verified it. |
| D7-7 | The bundle has no unhashed human mirror. `evidence.json` is the machine record and `index.html` is the only human view, regenerated from it. No `report.md`. | July `.md` mirrors of the JSON files were never in `bundle-checksums.json` (`EVJ/artifact-index.json:103,105`). The old backend markdown report was free-form (`ARC/services/backend/app/ai/verify/report.py`). |
| D7-8 | Verdict text comes only from a fixed phrase table keyed by state and reason code, filled with numbers from compare JSON. Model-written prose appears only in the section "Agent notes (model-written, not evidence)", below every evidence section. | The old report printed "Migration is **SAFE** to deploy" (`ARC/.../ai/verify/report.py:110`). A model may annotate and never upgrade (08§5.2; PLAN:59). |
| D7-9 | **Failures cannot be collapsed.** Every non-pass step is listed by name with its reason in the first screen (1280×720) and is shown in full before any passing step. No `<details>`, no toggles, no "show more" on failures. | The old `ComparisonCard` was collapsed by default (`ARC/apps/legacy-product-app/src/components/verify/ComparisonCard.tsx:120,149`). Failures never arrived: reconciliation results were never persisted, and FAIL led to "proceed anyway" (09§4.3). |
| D7-10 | An empty result is never a pass. Zero compared datasets gives **NOTHING VERIFIED**; all BLOCKED or SKIPPED gives no rung. | The old UI showed "All Checks Passed" when every count was 0 (`ARC/.../verify/VerifySummary.tsx:19-22`). The old backend `_overall_status` returns `PASS` when all results are `SKIPPED` (`ARC/.../ai/verify/report.py:185-192`). |
| D7-11 | **Drop `TARGETED_ACCEPT`** from the plugin vocabulary. The one residual need, a named human accepting a specific difference, becomes the step disposition `ACCEPTED_DIFFERENCE` (amber, never green). | TARGETED_ACCEPT meant "one bounded review recheck closed four findings". It is not parity quality and not acceptance (08§4.5, 10 A6), yet GBrain front-matter now reads `truth_status: targeted_accept_passed`, which an audience will read as acceptance. |
| D7-12 | Swipe map: **MapLibre GL 4.7.1 + @maplibre/maplibre-gl-compare 0.5.0**, vendored into `report/assets/vendor/`. Data comes as full-resolution PNGs pre-warped to EPSG:3857 (nearest neighbour) on **one shared colour scale**, inlined as data URIs in `assets/layers.js`, plus GeoJSON in the same file. No basemap by default. | Reuse the archive slider `ARC/services/mcp/artifacts/comparison-slider.html` (same libraries, lines 122-123, compare at :381) but fix four defects: it loads from a CDN and OSM tiles (:122-125, :333-334), so it fails offline; it downsamples to ≤512 px (:296-297), which hides a one-pixel shift; it scales colours per side from each side's own min/max (:301-305), which hides a ×1000 error; and it places a projected raster as a 4-corner quad (:258-262), which is not exact. `layers.js` is loaded via `<script>` because `file://` blocks `fetch`. Axis already standardises on MapLibre (`context/Claude-Migration-Agent-Handoff-2026-10-13.md:79`). |
| D7-13 | The workflow graph in the report is a **static SVG drawn by `report.py`** from the sealed `graph.json`: layered top-down by topological rank, using editor positions when present, with every node an `<a href="#step-…">`. React Flow is not used in the report. | Deterministic, works offline, prints. The old lineage view never showed data (09§4.4). |
| D7-14 | Run history is part of the report: every run of the project with its computed rung and number of failures. Earlier failing runs are kept and linked, never overwritten. | July kept non-accepting deliveries as correction history (08§6.2; 10 A1). HL-27. |
| D7-15 | A local run can reach `sandbox-validated` only with `isolation: policy` shown on the card. E2B runs show `isolation: physical`. | PLAN:157 (local by default, E2B opt-in); fake libraries are not validation (05 FH-03). |

## 3 Schemas

### 3.1 Bundle layout (extends the shared convention)

```
migration/runs/<run_id>/            run_id = r<UTC yyyymmddThhmmssZ>-<first 8 hex of run identity>
  sealed/        graph.json params.yaml tolerances.yaml decisions.md known-differences.md uv.lock
                 code.tree.json            # [{path, sha256, size}] of pipeline/ + tests/ at run start
  manifest.json  # run section: input/code/params/env hashes, library versions, step_results[]
  outputs/       # pipeline outputs (immutable after seal)
  compare/<dataset_id>.json   plots/<dataset_id>/*.png + plots/plots.json
  display/<dataset_id>.{legacy,new,diff}.png + display/display.json   (map.py, EPSG:3857, shared scale)
  visual/<dataset_id>.json    # model visual review receipts (before seal)
  scan.json  tests.json       # code-scanner and pytest results for this code tree
  evidence.json               # the index (below); no self-hash inside
  EVIDENCE_ID                 # "ev:<64-hex>"; also appended to migration/runs/index.jsonl
migration/report/index.html, assets/, REPORT.json   # a view; regenerable; never evidence
```

Legacy references are **not copied** into the run because the legacy folder is read-only and may be
large. They are recorded by hash and re-hashed on render; for the demo this is cheap, see §8 Q4.
The layout maps onto the July-derived per-run proposal (10 C3) as follows: `inputs.sha256` becomes
`objects[kind=input]`, `reference.json` becomes `objects[kind=legacy_ref].meta`, `checksums.json`
becomes `evidence.json`, and `env.lock` becomes `sealed/uv.lock` plus `env_runtime`.

### 3.2 Id derivation

- **File:** `sha256` = `"sha256:" + hex(sha256(bytes))`. Multi-file datasets such as a shapefile with its
  sidecars, and folders, use a **tree hash**: `sha256(cjson(sorted [{path, sha256, size}]))`. This is the
  July legacy-manifest algorithm (08§6.1), with paths POSIX-relative and `__pycache__` and `.aux.xml` excluded.
- **axis-cjson/1:** `json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=False,
  allow_nan=False).encode("utf-8")`. Python's default `sort_keys` sorts by code point. A metric that is
  NaN must be written as `null` with a reason code; the serialiser raises rather than writing `NaN`.
- **Object id** = `<kind>:` + hex(sha256(cjson(object minus volatile keys))). Volatile keys, kept in the
  file but out of the id: `created_at, started_at, ended_at, wall_time_s, hostname, pid`. Absolute paths
  are forbidden in any hashed object: every path is relative to `<project>`.
- **Run identity** `run:` = id of `{input ids, code tree id, params id, tolerances id, env_lock id,
  comparator id, executor, full}`. Two runs with the same identity must produce byte-identical
  outputs. The report shows "reproduced bit-identically by r…" when they do, and a red reproducibility
  finding when they do not.
- **Evidence id** `ev:` = id of `evidence.json` minus volatile keys. Like the July bundle it stores no
  self-checksum (`EVJ/bundle-checksums.json` `selfChecksumOmitted`). The id lives in `EVIDENCE_ID`, in
  `runs/index.jsonl` (append-only, hook-protected) and in the report footer.

Objects that get ids (`kind:` prefix):

| kind | What | Id from | Notes |
|---|---|---|---|
| `in` | each raw input (03_INPUT) | file/tree bytes | must equal the scan inventory hash; checked at run start and at seal (07 HL-09) |
| `ref` | each legacy output or intermediate used as reference | file/tree bytes | `meta.reference_kind`: `legacy_recorded` or `legacy_equivalent` (the ArcPy step's QGIS stand-in, PLAN:145); `meta.independence` R0–R4 (10 C3); R0 is refused |
| `code` | `pipeline/` + `tests/` | tree hash | from `sealed/code.tree.json` |
| `params` `tol` `graph` `dec` `kd` `lock` | sealed project files | file bytes | `dec:`/`kd:` also get per-entry ids (below) |
| `envrt` | runtime versions: python, GDAL, PROJ, GEOS, numpy, rasterio, geopandas, pyogrio, shapely, pyproj | cjson | GDAL/NoData drift is a named cause (08§9.5) |
| `run` | run identity | cjson | as above |
| `out` | each output file of the run | file/tree bytes | — |
| `cmp` | each `compare/<ds>.json` | cjson minus volatile | must embed the `out`, `ref`, `tol` record ids and the comparator id, version and code hash (fixes unbound parity, 10 A8.4) |
| `plot` `disp` | each PNG | file bytes | integrity only; byte-identical across machines is not required |
| `vis` | each visual review receipt | cjson | `{plot ids, rubric_sha256, model, answers, flag}` (Arena receipt shape, 10 C4) |
| `dec-e` `kd-e` | each `## D-nnn` / `## KD-nnn` entry | sha256 of the entry text, normalised to LF and stripped | lets the report show which decision a step rests on |
| `scan` `tests` | scanner and pytest results | cjson | — |
| `tgt` | `databricks/` + `target.json` | tree hash | sealed at `target` time, see §3.4 |
| `ev` | the bundle | cjson | — |

### 3.3 `evidence.json` (schema `axis-migrate.evidence.v1`)

```json
{
  "schema": "axis-migrate.evidence.v1",
  "parent_evidence_id": null,               // set only by `seal --amend` (additive kinds only, §3.5)
  "project": {"name": "lux-exposure-legacy", "legacy_tree": "tree:…"},
  "plugin": {"version": "0.1.0", "ladder_version": "ladder.v1", "report_version": "report.v1"},
  "run": {"run_id": "r20261016T141502Z-3fa91c0e", "identity": "run:…", "executor": "local|e2b",
          "isolation": "policy|physical", "e2b_template": null, "full": true, "exit_code": 0,
          "started_at": "…", "ended_at": "…",
          "step_results": [{"step": "s02_landuse_mask", "ok": true, "outputs": ["ds.res_mask"],
                            "wall_time_s": 4.2}]},
  "sealed": {"graph": "graph:…", "params": "params:…", "tolerances": "tol:…", "decisions": "dec:…",
             "known_differences": "kd:…", "lock": "lock:…", "code": "code:…"},
  "objects": [
    {"id": "ref:…", "kind": "ref", "path": "05_TEMP/02_res_mask.tif", "sha256": "sha256:…",
     "size": 48211, "dataset": "ds.res_mask", "meta": {"reference_kind": "legacy_recorded",
     "independence": "R1", "produced_by": "02_SCRIPTS/02_landuse_mask.py (QGIS 3.x)"}},
    {"id": "cmp:…", "kind": "cmp", "path": "migration/runs/<id>/compare/ds.res_mask.json",
     "sha256": "sha256:…", "dataset": "ds.res_mask", "step": "s02_landuse_mask",
     "refs": ["out:…", "ref:…", "tol:…"],
     "meta": {"state": "FAIL", "reason_codes": ["RASTER_TRANSFORM_MISMATCH"],
              "comparator": "axis-migrate.compare.v1", "comparator_sha256": "sha256:…"}},
    {"id": "plot:…", "kind": "plot", "path": "…/plots/ds.res_mask/triptych.png",
     "sha256": "sha256:…", "dataset": "ds.res_mask", "refs": ["cmp:…"]},
    {"id": "vis:…", "kind": "vis", "path": "…/visual/ds.res_mask.json", "sha256": "sha256:…",
     "refs": ["plot:…"], "meta": {"flag": true, "category": "edge_shift", "by": "model:<id>"}}
  ],
  "coverage": {"datasets_with_legacy": 11, "compared": 11, "not_compared": [],
               "no_reference": [], "state_histogram": {"PASS_EXACT": 7, "FAIL": 1, "…": 0}},
  "secret_scan": {"patterns": "bearer, api key, secret/password assignment, private key", "matches": 0}
}
```

`meta.state` is a **copy, for convenience only**. `report.py` reads the compare file, checks its hash,
re-derives the state from the per-check results, and treats any disagreement as
`STATE_RECOMPUTE_MISMATCH`. `coverage.state_histogram` lists every state, including those with zero
(HL-28). Unknown fields are rejected when the bundle is written (Arena plan:207).

### 3.4 Status ladder: evidence per rung

Step dispositions are derived from compare states (10 C1) plus records:
`VERIFIED` (all outputs PASS_EXACT or PASS_WITHIN_TOLERANCE, no open visual flag) · `ACCEPTED_DIFFERENCE` ·
`NEEDS_REVIEW` (INCONCLUSIVE, including a visual flag) · `NEEDS_DECISION` · `FAILED` · `BLOCKED` ·
`EVALUATOR_ERROR` · `NO_REFERENCE` (declared in the graph) · `NOT_COMPARED` (a reference exists but there is
no compare file) · `EVIDENCE_INCOMPLETE` · `BRIDGED` · `NOT_PORTED`.

| Rung | A step needs (all of these, each object present and hash-valid) | Blocked by |
|---|---|---|
| generated | The step's `graph.json` node has `module`; the module is in `code:`; `scan.json` has no finding for it (no legacy-output reads, no broad except that exits 0, 05 FH-01) | `NOT_PORTED`; scanner finding; module missing |
| sandbox-validated | + `step_results[step].ok == true` (typed, FH-02); every declared output has an `out:`; `tests.json` passes the step's tests; real libraries, no stubs (FH-03); inputs = `in:` ids recorded at run start; `executor` and `isolation` recorded | exit ≠ 0; missing output; stubbed libraries; input hash differs from the inventory |
| reference-verified | + every output dataset with `legacy_path` has a `cmp:` bound to this run's `out:` and a `ref:` with independence ≥ R1; disposition `VERIFIED` or `ACCEPTED_DIFFERENCE`; `run.full`; sealed tolerances equal to live | FAIL, NEEDS_DECISION, open visual flag, NOT_COMPARED, EVIDENCE_INCOMPLETE, R0 reference, tolerance drift, fitted parameter (below) |
| target-ready | + the step's module is in the Databricks job with the **same `code:` id**; target installs from the same `lock:` id | code changed since run; target lock ≠ run lock (`TARGET_ENV_DIVERGES`, 05 FH-04) |

The **pipeline rung** is the minimum over all steps, plus four conditions: every final output (04_FINAL
datasets) is reference-verified; integrity is complete; nothing has drifted; and for target-ready,
`target.json {code_id, lock_id, params_id, runtime, deployment_status: "not_deployed"}` is sealed and
bound. The ladder graphic always shows `deployed → live-target-verified → customer-accepted` greyed as
"not attempted", mirroring the July honest terminal state `not_deployed` (08§7). `ladder.py` has no code
path that emits those rungs (`deployed` needs an external authority, `MD/backend/src/migration-delivery/service.ts:108`).

Caps and annotations (applied after the table above; all shown on the step card and counted in the headline):

| Condition (source) | Effect |
|---|---|
| A param with `source: assumption` (a "don't know" answer) | annotate: "rests on assumption P-…"; no cap |
| A param with `source: inferred` and `fitted_against: <ds>` where that ds is compared in this step or downstream (PLAN:75) | **cap at sandbox-validated**, reason `PARAM_FITTED_TO_REFERENCE`; lifted only when the param's source becomes `answer` (the card then says "fitted, confirmed by <name>") |
| A param inferred from code or notes, not fitted | annotate with confidence |
| Bridge step: uses a legacy artefact instead of computing it (e.g. reading `cantons_lux_edit.gpkg` instead of encoding the manual rename, NOTES.txt) | step disposition `BRIDGED`, rung n/a; the pipeline rung is labelled "**· partial (n bridged)**"; target-ready also needs the bridged artefact declared as a target input in `target.json` |
| A known difference (`KD-nnn`) with `owner: human:<name>`, `datasets`, `reason_codes`, `bound` (e.g. `max_abs ≤ 0.5`), whose bound check in the compare passes | FAIL or NEEDS_DECISION on those codes becomes `ACCEPTED_DIFFERENCE` (amber). The compare state in the parity table stays FAIL. If decided after the run started, it is shown as "decided after seeing the difference" |
| A KD with no human owner, a model owner, a bound that does not hold, or other reason codes present | ignored; the FAIL stands; KD listed as "not accepted" |
| `legacy_equivalent` reference (the ArcPy step via QGIS) | annotate "reference from equivalent QGIS step" |
| Upstream step not verified while this step passes | annotate `UPSTREAM_NOT_VERIFIED` (the step keeps its own result) |
| Live `tolerances.yaml`, `params.yaml` or `graph.json` ≠ sealed | banner `*_CHANGED_AFTER_RUN`; every step capped at sandbox-validated until a new run |
| Live `pipeline/` ≠ sealed `code:` | banner `CODE_CHANGED_AFTER_RUN`; target-ready withdrawn; the report says it describes code `code:…`, not the working tree |
| Legacy reference re-hash ≠ `ref:` | `LEGACY_REF_CHANGED` → EVIDENCE INCOMPLETE |

### 3.5 Sealing and amendment

`seal.py` runs once, after compare, plots and visual review. It re-hashes everything, writes
`evidence.json`, `EVIDENCE_ID` and the index line, then the PostToolUse hook refuses any write under that
run folder. `seal --amend` writes `evidence.<n>.json` with `parent_evidence_id`, and may add only `vis`,
`dec-e` and `kd-e` objects. Outputs, compares, tolerances and code cannot be amended: that needs a new run.
The report uses the newest file in the chain and lists every amendment with its time.

## 4 Report design

### 4.1 Page order (demo; first screen = 1280×720 projector)

0. **Integrity bar** (only when there is a problem; red, full width, first element): "EVIDENCE INCOMPLETE —
   1 file missing, 1 hash mismatch" or "STALE — tolerances.yaml changed after run r…". Each item links to
   its card.
1. **Headline** (first screen): project, run id, executor and isolation; the ladder graphic with the
   computed rung (or "no rung: evidence incomplete" / "NOTHING VERIFIED"); counts by state; the
   **Failures and open items list**: every step that is not VERIFIED, one line each: step id,
   disposition, the first reason code as a phrase ("grid shifted by 1 px east"), link to the card.
   No limit and no fold. Below it, the **caveats line**: n assumptions · n inferred params · n bridged
   steps · n accepted differences · not checked: k · "not deployed".
2. **Workflow graph** (SVG). Nodes are filled by disposition and carry a text label as well as a colour
   and icon shape (colour-blind and projector safe). Edges show dataset ids. Each node links to its card.
3. **Swipe map.** The default dataset is the first failing raster or vector, otherwise the final raster
   `lux_res_exposure_2020`. A dropdown lists every dataset that has a display layer. Left is legacy,
   right is new, with a "difference" toggle (new − legacy, diverging, symmetric limits from
   `display.json`), the canton outlines, and a legend with units. Caption, rendered from fixed text:
   "Display only: warped to Web Mercator, nearest neighbour. Verdicts are computed on the native grid
   (<dataset CRS>; lux: EPSG:4326)." If WebGL fails, the map area shows the static triptych PNG.
4. **Failed and open steps, in full.** One card per non-VERIFIED step, in topological order, before any
   passing step.
5. **Parity table**: step · dataset · family · legacy path · new path · state · reason codes · key metric
   (max |Δ|, sum drift %, `shift_px`, rows Δ, symdiff m²) · tolerance id · links (compare JSON, plot). It
   is sorted by state severity, then step. Within-tolerance rows show the count of differences.
6. **Passing step cards.** Each card has the plot triptych and histogram, the check list with numbers and
   units, the full list of within-tolerance differences (10 C2.9), the params used with their source
   badge, visual review receipts, and rung with annotations.
7. **Assumptions and decisions**: params grouped by source (assumption, inferred, answer, evidence) with
   reason and node; the `D-nnn` entries; unanswered "don't know" items.
8. **Known differences**: every `KD-nnn` with owner, bound, whether the bound held, and whether it was
   decided after the run.
9. **Not checked**: legacy outputs with no new counterpart (for example a duplicate `.xlsx` if it is not
   migrated), unsupported families, branches not exercised, the state histogram with zero rows shown
   (HL-28).
10. **Run history**: all runs from `runs/index.jsonl` with identity, computed rung and failures. Earlier
    failing runs are linked (D7-14).
11. **Agent notes (model-written, not evidence)**: optional `migration/narrative.md`, rendered as escaped
    text inside a grey, labelled box. It never appears above section 10.
12. **Reproducibility**: run identity; evidence id and amendment chain; code, params, tolerances, lock and
    graph ids; `envrt` versions; inputs table (path, size, sha256); references table (path, sha256,
    kind, independence); comparator id and hash; report and ladder versions; the commands
    `uv run python -m pipeline --params runs/<id>/sealed/params.yaml` and
    `axis-migrate verify-bundle runs/<id>`.

### 4.2 Deterministic rendering

- `report.py <project>/migration [--run <run_id>]` reads only: the run folder, the files it names (legacy
  references, re-hashed), the live project files (for the drift check only), `runs/index.jsonl`, and
  vendored assets. It makes no network calls and no model calls.
- Jinja2 with autoescape; every collection is iterated in sorted order (steps topologically, ties broken
  by id); numbers go through one formatter (6 significant figures, explicit units); there is no
  `datetime.now`. The only times shown are the run's own `started_at` and `ended_at`. Outputs are written
  atomically (temp folder, then rename).
- Phrase table `phrases.v1.yaml`: reason code → template, e.g. `RASTER_TRANSFORM_MISMATCH: "grid shifted
  by {shift_px[0]} px east, {shift_px[1]} px north"`. An unknown reason code is rendered literally and
  flagged as `PHRASE_MISSING`. It is never dropped.
- Plots and display PNGs are copied into `report/assets/` after their hash is checked, so the folder can
  be copied on its own. `REPORT.json` lists every report file with its sha256, the `ev:` id and the
  versions. A secret scan runs on `report/` before writing (July `secretScan`, `EVJ/bundle-checksums.json`),
  because the folder may be published to R2.
- Exit codes: 0 complete; 2 rendered with evidence incomplete or stale; 3 no sealed run (writes a page
  that says "No sealed run: nothing verified").

### 4.3 Integrity checks run by `report.py` (and `verify-bundle`)

1. `evidence.json` parses and validates against the schema; recomputed `ev:` equals `EVIDENCE_ID` and the
   index line.
2. Every object: the file exists, its sha256 matches, and the recomputed id matches. Missing gives
   `EVIDENCE_FILE_MISSING`; changed gives `EVIDENCE_HASH_MISMATCH`.
3. Coverage: every graph dataset with `legacy_path` has a `cmp:` object; otherwise `NOT_COMPARED`.
4. Every `cmp` refs the run's own `out:` for that dataset and the sealed `tol:`; otherwise `UNBOUND_COMPARE`.
5. Re-derive each compare state from its checks with C1 precedence (10 C1); a mismatch gives
   `STATE_RECOMPUTE_MISMATCH`.
6. Sealed versus live drift (§3.4).
7. Legacy references and inputs are re-hashed.

Any finding from 1–5 or 7 sets EVIDENCE INCOMPLETE: the affected cards show what is missing (with the
expected hash), and the headline shows no rung. Limit, stated in the footer: hashes prove the bundle is
consistent, not who wrote it. A bundle re-sealed after tampering is caught only by checks 4, 5 and 7 and
by the append-only index. Signing is §8 Q7.

## 5 Reuse

| Take | From | Change |
|---|---|---|
| `canonicalJson` + `sha256Hex`, timestamp-free report hash | `MD/packages/runtime-core/src/migration-delivery.ts:291-299,366-367` | Python port; code-point sort; reject NaN |
| Tree-hash algorithm | legacy example manifest (08§6.1) | exclude caches and `.aux.xml` |
| Per-file checksums + secret scan + no self-checksum | `EVJ/bundle-checksums.json` | verified at **every render**, not once |
| Integrity rules: unique, existing, passing parity bound to delivery; mandatory checksums; failing history kept | 08§6.2; `MD/backend/src/migration-delivery/service.ts:162-167,177` | bind `cmp` to the `out:` hashes, which July did not do (10 A8.4) |
| `not_deployed` as an honest end state; no self-asserted deploy | `migration-delivery.ts:220`; `service.ts:108` | ladder greys out the rungs above target-ready |
| Compare states and reason codes | 10 C1 | consumed, not redefined |
| Visual receipt shape; `cannot_tell` ≠ yes; review error = pending | 10 B4/C4 | rendered as annotation that can only move a pass down |
| Swipe skeleton (MapLibre + compare) | `ARC/services/mcp/artifacts/comparison-slider.html:122-123,381` | vendored, full resolution, shared scale, pre-warped, `raster-resampling: nearest`, diff layer |
| Legacy/new property table with differing rows highlighted; up to 5 diff locations per failed check | `ComparisonCard.tsx:70-110`; `ARC/.../ai/verify/report.py:144-182` | typed comparison, not `String(a) !== String(b)`; full list in a sidecar |
| Do **not** take | `report.py:55` (wall-clock time), `:110` (SAFE verdict), `:129-132` (tolerances of first result only), `:185-192` (all-SKIPPED = PASS); `VerifySummary.tsx:19-22`; the collapsed cards; `LineageView.tsx` (dead route, 09§4.4); the browser-built JSON report and `AutoVerifyPanels` (06 Step 5) | — |

## 6 Acceptance tests (`tests/report/`, synthetic bundles + one Lux run)

1. **Failure in the first screen.** In a bundle with `s02_landuse_mask` FAIL (`RASTER_TRANSFORM_MISMATCH`,
   `shift_px [1,0]`), headless Chromium at 1280×720 finds the text "s02_landuse_mask" and "shifted by 1 px"
   inside `#failures` with a bounding box above y = 720. No ancestor is `<details>` or hidden, and the
   ladder shows no rung above sandbox-validated.
2. **Plot deleted.** Remove one PNG: exit 2; the headline reads EVIDENCE INCOMPLETE; the card shows
   `EVIDENCE_FILE_MISSING` with the expected sha256; no rung is shown.
3. **Tolerance edited after run.** Change one value in live `tolerances.yaml`: banner
   `TOLERANCE_CHANGED_AFTER_RUN`; every step is capped at sandbox-validated; `ladder.py` (the Stop hook
   path) returns the same result.
4. **Compare tampered.** Change `"state": "FAIL"` to `"PASS_EXACT"`: `EVIDENCE_HASH_MISMATCH`. Re-seal the
   tampered file consistently: `STATE_RECOMPUTE_MISMATCH` (the check values still fail).
5. **Narrative cannot lift.** Add `narrative.md` saying "all steps pass": the DOM text of sections 0–10 is
   byte-identical with and without it.
6. **Deterministic.** Render twice, and render from a copy of the project in another path: the
   `report/` trees are byte-identical.
7. **Empty is not pass.** No compare files gives "NOTHING VERIFIED", exit 2. All BLOCKED gives no rung.
8. **Coverage.** A dataset with `legacy_path` but no compare gives `NOT_COMPARED` in the failures list.
9. **Visual flag.** A flag on a PASS step gives NEEDS_REVIEW (amber), pipeline below reference-verified,
   with the picture shown. A "looks fine" receipt on a FAIL leaves it FAIL.
10. **Fitted param.** `MIN_AREA` with `source: inferred, fitted_against: ds.res_mask` caps s02 with
    `PARAM_FITTED_TO_REFERENCE`. Changing the source to `answer` and re-running lifts the cap.
11. **Known difference.** A KD with a human owner and a bound that holds gives ACCEPTED_DIFFERENCE (amber)
    while the parity row stays FAIL. A model owner, or a broken bound, leaves FAIL.
12. **Bridge.** s00 reading `cantons_lux_edit.gpkg` gives the pipeline label "· partial (1 bridged)".
13. **Code drift.** Edit `pipeline/s04_distribute_value.py` after the run: `CODE_CHANGED_AFTER_RUN` and
    target-ready withdrawn.
14. **Offline.** Open `file://…/index.html` with the network disabled: map overlays render, the swipe moves,
    the console shows no errors, and there are zero external requests (Playwright request log).
15. **Visible shift.** On the one-pixel-shift trap, the display diff PNG has a 1-cell non-zero stripe
    (width = native cell) and is not downsampled.
16. **NaN.** A compare containing NaN makes the serialiser raise. The compare writer must emit `null` plus
    a reason code.
17. **Run history.** After a failing run and then a passing run, both appear in section 10 and the failing
    one still renders.

## 7 Contradictions resolved

| Contradiction | Resolution |
|---|---|
| Five status vocabularies with no mapping (08§8.2) | Three layers only: compare state (10 C1) → step disposition (§3.4) → rung. The report never shows PASS/WARN or SKIPPED. |
| Is WARN/PARTIAL a pass? (08§8.3) | No. Only PASS_EXACT and PASS_WITHIN_TOLERANCE are green. ACCEPTED_DIFFERENCE is amber and needs a named human. |
| TARGETED_ACCEPT keep or drop (08§4.5, 08§9.6) | Dropped (D7-11). The bounded-review idea is not needed for parity; reruns are cheap and immutable. |
| report.json + report.md (08§9.5) vs HTML (PLAN:60) | `evidence.json` + `compare/*.json` are the machine record, `index.html` the only human view (D7-7). |
| `.axis-migrate/runs/` with copied `reference/` (10 C3) vs `migration/runs/` and a read-only legacy folder | Shared convention; references hashed, not copied (§3.1). |
| Stop hook "no done without passing report" vs "failures visible" (PLAN:38,60) | The hook blocks the *claim*; the report always renders, and failures go first. |
| "needs review" (PLAN:59) vs INCONCLUSIVE (10 C1) | Same state; the UI label is "needs review", the reason is `VISUAL_FLAG`. |
| Rungs after target-ready (08§4.3) vs demo scope | Shown greyed "not attempted"; `ladder.py` cannot emit them. |

## 8 Open questions for the founder

1. Can a **local** run (policy isolation) reach `sandbox-validated` in the demo, or must the shown run be
   E2B? Recommendation: run E2B for the shown run; keep local allowed with the label.
2. Who is the named owner for known differences and answers in the demo (you as "analyst")? Is
   ACCEPTED_DIFFERENCE amber acceptable on stage?
3. Should the demo show a **failing first run** in the run history (for example the one-pixel-shift trap
   caught at s02, then fixed)? Recommendation: yes; it is the strongest honest moment.
4. Re-hashing legacy references on every render is cheap for Lux. For multi-GB projects, should we allow
   an mtime+size fast path, labelled in the footer, after the demo?
5. Basemap: none (offline, default), or an optional online OSM layer with attribution when reachable?
6. May the rendered `report/` folder (public data only) go into the public R2 bucket for the audience?
7. After the demo: sign `EVIDENCE_ID` (`ssh-keygen -Y sign`), so that re-sealing after tampering is
   detectable?
8. Is a `legacy_equivalent` reference (QGIS stand-in for the ArcPy step) enough for reference-verified
   with a label, or should it be capped?

## 9 Cross-section contract (what 07 needs and provides)

**Needs** (missing or invalid input gives EVIDENCE INCOMPLETE, never a guess):
- *graph (discover)*: `steps[] {id, name, kind: migrated|bridge|manual|not_ported, module, inputs[], outputs[],
  position?}`, `datasets[] {id, family: raster|vector|table, role: input|intermediate|final, legacy_path?,
  new_path, no_reference_reason?, measure?}`.
- *interview*: `params.yaml` entries `{value, source: evidence|answer|assumption|inferred, reason, node,
  fitted_against?, confidence?}`; `decisions.md` as `## D-nnn · title` with `node:`, `decided_by:
  human:<name>|model`, `decided_at:`; `known-differences.md` as `## KD-nnn` with `datasets:`, `reason_codes:`,
  `bound:`, `decision: preserve-legacy|fix-legacy`, `owner:`, `decided_at:`.
- *run*: `sealed/` copies at start, `manifest.json` with `step_results[] {step, ok, outputs[]}`, `executor`,
  `isolation`, `full`, `envrt`; inputs hashed against the scan inventory.
- *verify*: `compare/<ds>.json {schema, dataset, step, state, reason_codes[], checks[{name, metric, value,
  unit, tolerance_id|exact, op, passed, diagnostics}], within_tolerance[], refs{out, ref, tol},
  comparator{id, version, sha256}}`; tolerance records with ids (10 C2); `visual/<ds>.json` receipts.
- *plot / map*: `plots/plots.json [{plot_id?, dataset, kind, path, cmp_ref}]`; `display/display.json
  [{dataset, legacy|new|diff paths, bounds_3857, scale{min, max, palette, units}, resampling: nearest,
  native_cell_px: 1}]`; vectors as GeoJSON (EPSG:4326) with legacy and new attributes.
- *target*: `databricks/` + `target.json {code_id, lock_id, params_id, runtime, deployment_status}`.
- *hooks*: refuse writes into sealed runs and to `runs/index.jsonl`; the Stop hook calls
  `ladder.compute(bundle)` and blocks "done", "verified" or "parity pass" unless the pipeline rung is
  ≥ reference-verified with integrity complete.

**Provides**: `seal.py`, `evidence.json` v1, `ladder.py` (`compute(bundle) → {steps{id: rung, disposition,
caps[], annotations[]}, pipeline{rung, suffix, blockers[]}, integrity[]}`), `report.py` (exit 0/2/3),
`verify-bundle`, `phrases.v1.yaml`, and the reason codes `EVIDENCE_FILE_MISSING, EVIDENCE_HASH_MISMATCH,
STATE_RECOMPUTE_MISMATCH, UNBOUND_COMPARE, NOT_COMPARED, LEGACY_REF_CHANGED, PARAM_FITTED_TO_REFERENCE,
UPSTREAM_NOT_VERIFIED, TOLERANCE_|PARAMS_|GRAPH_|CODE_CHANGED_AFTER_RUN, TARGET_ENV_DIVERGES, PHRASE_MISSING`.
