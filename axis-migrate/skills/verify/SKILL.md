---
name: verify
description: Sub-step of the axis-migrate migrate flow. Use after each step run and the chained run to compare against the legacy outputs, look at every dataset and propose (never sign) tolerances. Not for comparing rasters or vectors outside a migration.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Verify: numbers decide, map views and plots show, you look and may only flag

`$P` = plugin root. Run every script as `"$P/scripts/axpy" "$P/skills/verify/<script>.py" …` from the project directory.
`<run>` = `migration/runs/<run_id>` (printed by `run_local.py`). One verify per run: a changed tolerance, known
difference or pairing needs a **new run**; never re-verify an old run after editing those files.
Not for comparing rasters, vectors or tables outside a migration (no `migration/runs/`): answer such a request
directly and start no migration.

## 1 Compare (the only producer of verdicts)
`compare.py run --run <run> [--dataset ds.x]… [--json]` pairs datasets from graph lineage only, freezes the contract
(`verify/contract.json`) and writes `verify/compare/<ds>.json`. Exit 0 = every requested dataset PASS_*; 1 = not;
2 = could not set up. Read `verdict`, `reason_codes`, the failing checks' `metric`/`value`/`threshold`, `diagnostics`
(e.g. `shift_px`, `scale_factor`) and `samples` (map coordinates). Vocabulary, worst first: FAIL > EVALUATOR_ERROR >
BLOCKED > NEEDS_DECISION > NEEDS_REVIEW > NOT_COMPARED > PASS_WITHIN_TOLERANCE > PASS_EXACT. EVALUATOR_ERROR (reference
changed, contract changed, checker fault) is never fixed by changing pipeline code: report it. Never edit
`verify/compare/*.json` or `verdict.json`.

## 2 Plots
`plot.py run --run <run> [--dataset ds.x]…` writes `verify/plots/<ds>/*.png` + `plots.json` for every compare result
(also when the new output is missing or the grid gate failed: it draws what exists and says what is missing).
Raster: `overview.png` (legacy | new | diff; ONE shared colour scale; diff orange = new larger, purple = legacy larger,
black/dark grey = valid on one side only; zone boundaries green), `mask_diff.png`, `hist.png`, `worst_tiles.png`
(native resolution, cell grid; `grid_overlay` mode draws both grids when the grid gate failed). Vector: `overlay.png`,
`per_feature.png`, `worst_features.png`. Table: `residuals.png`. A plot that would show nothing (no differing cell or
key) is not drawn; `plots.json` lists it under `omitted`. Plots are evidence about verdicts, never inputs.
Always run it: the plots are the look's fallback and the report and evals use them, also when you look on the map.

## 3 Look (mandatory for every compared raster and vector dataset)
**Primary where a browser pane exists: the map views → view receipts. Fallback (terminal, headless, evals): the PNG
plots → PNG receipts.** Both count the same, a flag has the same effect, the report lists both, and the Stop gate
refuses to end the turn while a compared dataset of the latest run has neither. Viewport, screenshot and readiness
rules: map SKILL §2–3.
**Nothing to show**: `views.py run` builds no views for a dataset whose new output is identical to legacy (compare
`CANDIDATE_IDENTICAL_TO_REFERENCE`) or byte-equal to one already reviewed with nothing found in an earlier run;
views.json lists it under `skipped` as `skipped: identical (<sha256 of the new output>)`. Do not open anything for
it: review-template prints `{"dataset_id", "skipped": "skipped: identical (<sha256>)", "model"}`; fill `model` and
review-add it (accepted only while the new output's bytes, hashed again, are that sha). A PASS_EXACT dataset that is
not identical gets one overview and one cell; a dataset with max |new − legacy| = 0 gets no `worst` view.
- **Claude desktop** (primary): `map` skill (`views.py run`, `start.py migration`), `views.py list --run <run>
  --json` once (every URL, cell and value), `resize_window` 1280×960, then per view: navigate, wait for "axis-map:
  ready", one screenshot at scale 1; for a `cell-<id>` project click the "click centre" the ready line gives and read
  legacy/new from the popup with `get_page_text`. The GPU engine is already set; no dialog. Record a **view receipt**.
- **Codex desktop** (primary): the same in the in-app browser through browser use. Record a **view receipt**.
- **Fallback — terminal, no pane, headless, evals**: **PNG receipts** (steps 1–5); you still Read the PNGs yourself.

1. `plot.py review-status --run <run>` lists what still lacks a receipt and which questions are missing;
   `plot.py review-template --run <run> [--dataset <ds>] [--kind view|png]` prints the receipts prefilled (shas,
   views or plots seen, engine, cells to read, allowed values): fill in only what you saw, never copy it unseen.
2. (PNG fallback) **Read every PNG** of the dataset (Read tool on `migration/runs/<id>/verify/plots/<ds>/<name>.png`;
   Codex: `view_image`). Take `plot_sha256`, `panel_names`, `crs` and `extent` from `plots.json`.
3. Answer every rubric question of `$P/skills/verify/rubric.v1.yaml` that applies to the family (raster Q1, Q2, Q4,
   Q5, Q6, Q7; vector Q1, Q3, Q4), from the plots the rubric names, following its `look` guidance. Yes means
   **defect present**. Answer from what you see, not from the verdict printed in the title; a failing check does not
   make every question yes, a passing one does not make them no. `cannot_tell` is allowed and is listed as "not
   visually assessed" (it is not a receipt).
4. Record each answer with `plot.py review-add --run <run> --json finding.json` (a JSON object or a list):
   ```json
   {"dataset_id": "ds.x", "question_id": "Q6", "answer": "yes", "effect": "flag", "category": "boundary_stripes",
    "plot": "plots/ds.x/mask_diff.png", "plot_sha256": "<sha from plots.json>", "panel": "mask",
    "location": {"crs": "<plot crs>", "bbox": [xmin, ymin, xmax, ymax]},
    "note": "one-cell orange ring along the whole outer edge", "model": "<your model id>"}
   ```
   Rules the script enforces: `answer` yes|no|cannot_tell; yes ⇔ `effect: flag`; `category` from the question's list
   when yes, `none` otherwise (closed list: whole_offset, boundary_stripes, missing_region, uniform_scale,
   smoothed_blurred, angular_outline, shrunken_features, class_missing, holes, none); `plot_sha256` must match
   `plots.json` of this run; `panel` must be one of the plot's `panel_names`; a yes needs `location` (bbox in the
   plot's map coordinates — tile and feature titles print theirs) and a note ≤ 200 characters saying what is where.
5. **Looked, nothing found**: when every applicable question is no, one receipt per dataset is enough:
   `{"dataset_id": "ds.x", "question_id": "Q-all", "answer": "no", "effect": "none", "category": "none",
   "panel": "all", "plot": "plots/ds.x/overview.png", "plot_sha256": "…", "plots_seen": [{"plot": "…", "plot_sha256":
   "…"}, … every png of the dataset], "note": "", "model": "…"}`. Use it only after reading every listed PNG.
6. **View receipt** (same command; it has `view` instead of `plot`):
   ```json
   {"dataset_id": "ds.x", "question_id": "Q6", "answer": "yes", "effect": "flag", "category": "boundary_stripes",
    "view": {"project": "verify/views/ds.x/edges.geolibre.json", "project_sha256": "<views.json>"},
    "views_seen": [{"project": "verify/views/ds.x/overview.geolibre.json", "project_sha256": "…"}, … worst, edges,
                   nodata, cell-edge],
    "engine": "maplibre-gl-raster", "cells_read": [{"cell": "edge", "legacy": 1, "new": null}], "layer": "validity",
    "location": {"crs": "EPSG:4326", "bbox": [west, south, east, north]},
    "observations": [{"what": "cells valid on legacy only, west edge", "value": 812, "unit": "cells"}],
    "note": "one-cell blue stripe west, orange stripe east", "model": "<your model id>"}
   ```
   Enforced: `views_seen` holds every view views.json built of overview, worst, edges, nodata (vector: overview,
   worst; `omitted_views` are not required) and the `cell-<id>` project of every cell read, each sha equal to views.json and to the file; rasters need `engine: maplibre-gl-raster` and at
   least one cell, values as the popup shows them (checked to ~3 significant figures; null = nodata); a yes needs
   `location` (lon/lat), a note and `observations` with numbers. Nothing found = one `Q-all` view receipt with answer
   no, refused while views.json counts cells valid on one side only (validity 1/2): flag those instead. A view
   project whose bytes change after the review stops counting.
7. Before sealing, receipts go to `verify/visual-review.json`. After the run is sealed, `review-add` appends to
   `runs/<id>/amend/visual-review.json` and calls `report/seal.py <migration> --run <id> --amend` itself (exit 4 =
   recorded but the amend seal failed: rerun that command).

What a flag does: a PASS_* dataset becomes NEEDS_REVIEW (`VISUAL_FLAG`); on any other state it is a note. Nothing you
record moves a verdict up, sets a tolerance or clears a flag (only a person in `decisions.md`, or a new run whose plot
no longer shows it). Shifts and sub-cell offsets are the checks' job; Q1 only complements them. Missing receipts make
the dataset NEEDS_REVIEW (`VISUAL_REVIEW_MISSING`) and block reference-verified.

## 4 Aggregate
`compare.py aggregate --run <run> [--isolation-run <isolated run>]…` writes `verify/verdict.json`: dataset → step →
pipeline verdicts with visual receipts merged, blame per step (`own | inherited | accumulated | compensating`) and
`first_failing_steps`. It holds no rung: the status comes from `report/ladder.py` only. Then the report skill.

## 5 Repair loop (06 §4f)
- Repair the **earliest** step in `first_failing_steps` (blame `own`). Inherited failures disappear when it is fixed.
- Per attempt use only the step spec (plan block, declared inputs/outputs, its params entries, relevant decisions) and
  the **current** error (failing checks + diagnostics + samples + plots, or the innermost traceback). Nothing from
  earlier attempts.
- At most **3 attempts per step**. Stop at once when the same failure signature (step, dataset, reason codes, rounded
  metrics) survives two distinct patches, or a patch repeats; stop on BLOCKED, EVALUATOR_ERROR or NEEDS_DECISION.
- Success only when the step is PASS_* isolated **and** in the next chained run. Exhausted → the step stays FAIL and
  the report shows it; no "best attempt".
- Never edit `tolerances.yaml`, `known-differences.md`, `references`/legacy files, `compare.py`/`plot.py`, the
  graph's pairing (reference, produced_by, new paths) or `params.yaml` values to make a check pass. A different
  parameter value is an **interview question** (answer → params.yaml → decisions.md), never a literal in code.
- A difference that looks like a legacy bug (`LEGACY_DEFECT_SUSPECTED`, NEEDS_DECISION) is a keep-or-fix question for
  the user (recommended: keep for parity), not a repair.

## 6 Tolerances and known differences: propose with two numbers, never sign
Default is exact. A non-zero tolerance exists only as a **signed** record in `migration/tolerances.yaml` whose
`correct_spread` (an independent correct implementation) < threshold < `nearest_wrong` (closest plausible wrong recipe),
signed before the run that uses it. You never write `tolerances.yaml` and never sign anything.
1. Nearest wrong: `tolcal.py mutate --legacy <legacy file> --kind raster|vector|table --out migration/analysis/tolcal/<ds>
   [--spec spec.json] [--recipe rasterize.json] [--key col]…` (deterministic mutants of the legacy file, compared with
   the frozen comparator; writes `tolcal.json`).
2. Correct spread: measure it with an independent correct implementation in `migration/analysis/` (never imported by
   the pipeline). Without it there is no record and the check stays exact.
3. Propose: `tolcal.py propose --dataset ds.x --check raster.values --correct-spread X --nearest-wrong Y [--form rtol|
   atol|value|max_mismatch] [--units …] [--spread-how … --spread-evidence …] [--wrong-variant … --wrong-evidence …]`
   prints one record with `agreed_by: null`. Put it under `records:` in **`migration/tolerances.proposed.yaml`**
   (create it as `version: 1` + `records: []`; Write the file or indent the printed record as a list item). You may
   write that file at any time; it is not part of the run contract. Never fill `agreed_by`/`agreed_at`.
4. What proposals do: compare.py reads them (from the run's `snapshot/tolerances.proposed.yaml`) only to classify — a
   check that would pass only under a proposal ends NEEDS_DECISION / `TOLERANCE_UNSIGNED`; it never passes. A record
   with a signature inside the proposals file is ignored (listed in `tolerance_proposals_ignored`).
5. Known differences: record each with `"$P/scripts/axpy" "$P/skills/interview/record.py" kd migration --json kd.json`
   (it assigns `KD-nnn` and writes a serialiser-made block; never hand-write one). To change or fix an entry, even a
   malformed one, record a new KD with `supersedes: KD-nnn`; never edit the old one. The fields, for `kd.json`
   (plus `title`; `id` is assigned):

   ````markdown
   ## KD-004 · exposure written as float64

   One or two sentences of prose (ignored by code).

   ```yaml
   id: KD-004                      # equal to the heading id; next free number
   kind: normalisation             # normalisation | exclusion | accepted_change | legacy_defect_preserved
   datasets: [ds.lux_res_exposure_2020]   # graph dataset ids
   checks: [raster.dtype]          # check ids as in compare/<ds>.json (a prefix covers raster.values:b2 etc.)
   rule: {dtype_widening: {from: float32, to: float64}}
   #   normalisation:   nodata_equivalent: [a, b] | dtype_widening: {from, to} | multi_equivalence: true |
   #                    string_strip: {columns: [c]} | casefold: {columns: [c]} | rename: {legacy: new} |
   #                    content_hash_only: {gpkg_contents.last_change: true}
   #   exclusion:       {reason: NO_REFERENCE | FAMILY_UNSUPPORTED}   (never for a role: final dataset)
   #   accepted_change: {expected: "new = legacy * 1000"}   (needs a bound; the check stays FAIL)
   #   legacy_defect_preserved: null
   bound: null                     # or {metric: max_abs | max_rel | count_over, max: <measured number>}
   reason: "why this difference is acceptable, with numbers"
   evidence: ["migration/analysis/…json", "02_SCRIPTS/x.py:12"]
   owner: agent                    # you always write agent; a human changes it to human:<name>
   decided_at: null                # set by the human who accepts it
   decision_ref: null              # decisions.md#D-nnn, set with the decision
   status: proposed                # proposed | accepted | withdrawn — only a human sets accepted
   ```
   ````
   A proposed KD that would cover a failure gives NEEDS_DECISION / `KD_UNSIGNED`, never a pass; an accepted_change
   never turns a check green (the step can only become ACCEPTED_DIFFERENCE, amber).
6. Validate before you rely on either file: `signoff.py migration --check` (exit 0 clean, 2 with every problem and
   line). Fix a malformed KD by appending a superseding entry, never by editing the old one.
7. `signoff.py migration` renders `migration/SIGN-OFF.md`: every unsigned record (from both files) and proposed KD with
   its two numbers or bound, the runs it affects and exactly what the human edits (copy into `tolerances.yaml`, set
   `agreed_by: human:<name>`, `agreed_at`, `decision_ref`; for a KD `owner`/`status`/`decided_at`), then a **new run**.
   Tell the user it exists. Do not claim parity for anything waiting there.

## Never
Write or edit anything under `migration/runs/` by hand; widen a tolerance or edit a record after seeing a failure;
pair datasets by name or position; treat a plot or your answer as a verdict; claim a status that `ladder.py` does
not print.
