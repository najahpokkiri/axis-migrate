---
name: report
description: Sub-step of the axis-migrate migrate flow. Use after verify on a chained run, after the target checks, or whenever the current status line is needed. Seals the evidence, computes the rung with ladder.py and renders the report.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Seal, compute the status, render the report

`$P` = plugin root. Every script runs as `"$P/scripts/axpy" "$P/skills/report/<script>.py" …`, except `ladder.py`, which is
stdlib only and also runs as `python3 -I "$P/skills/report/ladder.py" …`. Run from the project folder (the parent of
`migration/`).

## Order (one verify per run; the run must be `chained`)

1. **Preconditions**: `compare.py run --run migration/runs/<id>` has run, `plot.py run` has run, where a browser
   pane exists `views.py run` (map skill) has run, your visual review receipts have been added with `plot.py
   review-add` (view receipts from the map views where a pane exists; PNG receipts are the terminal/headless/eval
   fallback), and `compare.py aggregate --run migration/runs/<id>` has run.
   An isolated run is for diagnosis: it can never reach a rung above `generated`.
2. **Display layers**: `axpy map.py display --run migration/runs/<id> [--dataset ds.x]…`. This writes
   `verify/display/` (EPSG:3857, nearest neighbour, one shared colour scale). It is for display only and must run before
   sealing.
3. **Seal**: `axpy seal.py migration --run <id> [--json]`. This writes `verify/evidence.json` and `EVIDENCE_ID`, makes
   `verify/` read-only and appends a line to `runs/index.jsonl`. Exit 2 means refused (already sealed, a secret was found,
   or a NaN was found); report the stderr line verbatim. Seal runs once per run.
4. **Graph projections**: `axpy "$P/skills/discover/graph.py" ingest-verify migration --run <id>`, then
   `axpy "$P/skills/discover/graph.py" derive migration`. The graph page then colours the steps from this run. These are
   projections only: they never count as graph drift, and they never change the rung.
5. **Status**: `python3 -I ladder.py migration [--run <id>] [--json]`. Quote its first line (`status_line`)
   **verbatim**, for example
   `axis-migrate status: sandbox-validated | run r… | 3 pass, 1 FAIL, 0 NOT_COMPARED, 0 needs review | report migration/report/index.html`.
   Never claim a rung above it. The rungs above `target-ready` (deployed, live-target-verified, customer-accepted) are
   never computed, so never claim them.
6. **Report**: `axpy report.py migration [--run <id>] [--json]`. Show the user `migration/report/index.html` in the
   side pane (/migrate §Showing).
   - exit 0: the report is complete (it may still show failures).
   - exit 2: **EVIDENCE INCOMPLETE, stale (a file changed after the run) or NOTHING VERIFIED.** Say so in your
     first sentence and name the integrity codes from the red bar. Do not hide this, soften it or re-render
     until it goes away.
   - exit 3: no sealed run, so nothing is verified.

## Late evidence: amend, never re-seal

- **Late visual receipts** (after sealing): `plot.py review-add` writes to `runs/<id>/amend/visual-review.json` and runs
  the amend seal itself (exit 4 = recorded but the amend failed: then run `axpy seal.py migration --run <id> --amend`).
- **Decisions or known differences added after the run** are picked up by the same `--amend`. A KD decided after the run
  is shown as "decided after seeing the difference" and applies only from the **next** run.
- **Target evidence**: after `target_check.py`, run `axpy seal.py migration --run <id> --amend --target`. Without this,
  `target-ready` is not reached (`TARGET_NOT_SEALED`).
- An amendment can add only `vis`, `dec-e`, `kd-e` and `tgt` objects. Changing outputs, compare results, tolerances or
  code needs a **new run**.

## Reading the report

- **Failures and open items** (first screen): every step that is not VERIFIED, one line each, as
  `disposition · step · reason code · phrase`. Each one links to the step's full card. When you summarise, list every
  line. ACCEPTED_DIFFERENCE is amber and never green: the parity row stays FAIL.
- **Red integrity bar**: EVIDENCE_FILE_MISSING, EVIDENCE_HASH_MISMATCH, STATE_RECOMPUTE_MISMATCH, UNBOUND_COMPARE and
  LEGACY_REF_CHANGED mean no rung is shown. `*_CHANGED_AFTER_RUN` means stale, and the steps are capped at
  sandbox-validated until a new run.
- **Map views** (in each compared raster/vector's card; the swipe map links to them): the views of `views.json` with
  path and sha256, sealed or not, `matches` or `changed after the review` (its receipts then do not count), the
  dataset's receipts (PNG or view: engine, views seen, cells read, flag observations, counts or why not) and the two
  commands to open the views. The page holds no server address and embeds no GeoLibre. No `views.json` → "No map
  views for this run" and the PNG plots are the visual record. Never say a dataset was looked at on the map unless
  its block says a view receipt counts.
- **Step cards**: caps (`→ at most <rung>`) and annotations (assumptions, "fitted, held-out pass",
  UPSTREAM_NOT_VERIFIED) explain why a step stopped where it did.
- **Run history**: every run, including failing ones, with its computed rung and FAIL count. When you cite numbers,
  cite the run id they come from. Earlier failing runs stay in the history; that is intended.
- **Target panel**: the claim text comes verbatim from `target-manifest.json`. Quote it as is, including the
  "Not done" part.

## What you may write

Only `migration/narrative.md`. It is rendered as escaped text under "Agent notes (model-written, not evidence)", below
every evidence section, and it cannot change any status. Keep it factual and point to the run id.

## Forbidden (each of these is detected, and each makes the result worse)

- Editing anything under `migration/runs/` (outputs, `verify/`, `compare/*.json`, `evidence*.json`, `EVIDENCE_ID`,
  `visual-review.json`) or `runs/index.jsonl`.
- Editing `tolerances.yaml`, `known-differences.md`, `params.yaml`, `graph.json` or `pipeline/` to change a result
  after a run. Drift is detected and caps the rung.
- Writing a KD or tolerance record with a human owner or signer for the user. Only a person signs (see
  `migration/SIGN-OFF.md`).
- Re-sealing, deleting `evidence.json` or hand-editing `report/`. The report is regenerated from the bundle, and
  `REPORT.json` lists every file hash.
- Wording a verdict yourself. Use the status line and the report's phrases. Do not claim parity, "done" or "verified"
  above the computed rung (the Stop gate blocks it).
