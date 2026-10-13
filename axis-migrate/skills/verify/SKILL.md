---
name: verify
description: Prove per-step parity of a pipeline run against the legacy run's own intermediates and finals. Compare every lineage pair (compare.py), render legacy | new | difference plots (plot.py), LOOK at every plot and answer the calibrated rubric (review-add; you may only flag), aggregate verdicts and blame, run the repair loop within its limits, and propose (never sign) tolerance records. Use after every isolated step run while migrating and after the chained run.
---

# Verify: numbers decide, plots show, you look and may only flag

`$P` = plugin root. Run every script as `"$P/bin/axpy" "$P/skills/verify/<script>.py" …` from the project directory.
`<run>` = `migration/runs/<run_id>` (printed by `run_local.py`). One verify per run: a changed tolerance, known
difference or pairing needs a **new run**; never re-verify an old run after editing those files.

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
`per_feature.png`, `worst_features.png`. Table: `residuals.png`. Plots are evidence about verdicts, never inputs.

## 3 Look (mandatory for every compared raster and vector dataset)
1. `plot.py review-status --run <run>` lists what still lacks a receipt and which questions are missing.
2. **Read every PNG** of the dataset (Read tool on `migration/runs/<id>/verify/plots/<ds>/<name>.png`). Take
   `plot_sha256`, `panel_names`, `crs` and `extent` from `plots.json`.
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
6. Before sealing, receipts go to `verify/visual-review.json`. After the run is sealed, `review-add` appends to
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

## 6 Tolerances: propose with two numbers, never sign
Default is exact. A non-zero tolerance exists only as a record whose `correct_spread` (an independent correct
implementation) < threshold < `nearest_wrong` (closest plausible wrong recipe), fixed before the run that uses it.
1. Nearest wrong: `tolcal.py mutate --legacy <legacy file> --kind raster|vector|table --out migration/analysis/tolcal/<ds>
   [--spec spec.json] [--recipe rasterize.json] [--key col]…` (deterministic mutants of the legacy file, compared with
   the frozen comparator; writes `tolcal.json`).
2. Correct spread: measure it with an independent correct implementation in `migration/analysis/` (never imported by
   the pipeline). Without it there is no record and the check stays exact.
3. `tolcal.py propose --dataset ds.x --check raster.values --correct-spread X --nearest-wrong Y [--form rtol|atol|
   value|max_mismatch] [--units …] [--spread-how … --spread-evidence …] [--wrong-variant … --wrong-evidence …]`
   prints one record with `agreed_by: null`; append it to `migration/tolerances.yaml` as a new record id (a guard asks
   the user). Never edit an existing record, never fill `agreed_by`/`agreed_at`.
4. `signoff.py migration` renders `migration/SIGN-OFF.md` (every unsigned record and proposed KD with its numbers and
   the lines a person edits to sign). Tell the user it exists.
5. Unsigned records are ignored: a check that would pass only under one ends NEEDS_DECISION / `TOLERANCE_UNSIGNED`
   (`KD_UNSIGNED` for known differences). Say so; do not claim parity for it. A signed record takes effect from the
   next run.

## Never
Write or edit anything under `migration/runs/` by hand; widen a tolerance or edit a record after seeing a failure;
pair datasets by name or position; treat a plot or your answer as a verdict; claim a status that `ladder.py` does
not print.
