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
2. **Display layers**: `"$P/scripts/axpy" "$P/skills/report/map.py" display --run migration/runs/<id> [--dataset ds.x]…`. This writes
   `verify/display/` (EPSG:3857, nearest neighbour, one shared colour scale). It is for display only and must run before
   sealing.
3. **Seal**: `"$P/scripts/axpy" "$P/skills/report/seal.py" migration --run <id> [--json]`. This writes `verify/evidence.json` and `EVIDENCE_ID`, makes
   `verify/` read-only and appends a line to `runs/index.jsonl`. Exit 2 means refused (already sealed, a secret was found,
   or a NaN was found); report the stderr line verbatim. Seal runs once per run.
4. **Graph projections**: `"$P/scripts/axpy" "$P/skills/discover/graph.py" ingest-verify migration --run <id>`, then
   `"$P/scripts/axpy" "$P/skills/discover/graph.py" derive migration`, then `"$P/scripts/axpy" "$P/skills/report/graphview.py" md migration`
   (rewrites `migration/graph.md` + `graph.png`, coloured by this run). These are projections only: they never count
   as graph drift, and they never change the rung.
5. **Status**: `python3 -I "$P/skills/report/ladder.py" migration [--run <id>] [--json]`. Quote its first line (`status_line`)
   **verbatim**, for example
   `axis-migrate status: sandbox-validated | run r… | 3 pass, 1 FAIL, 0 NOT_COMPARED, 0 needs review | report migration/report/index.html`.
   Never claim a rung above it. The rungs above `target-ready` (deployed, live-target-verified, customer-accepted) are
   never computed, so never claim them.
6. **Report**: `"$P/scripts/axpy" "$P/skills/report/report.py" migration [--run <id>] [--json]`. It writes `migration/report/`: `REPORT.md` (the
   summary: status line first, what it means, what the next rung needs, failures and NOT_COMPARED, decisions for the
   person, parity table, lineage as Mermaid), `index.html` (one self-contained page, opens from disk, no network),
   `parity.xlsx` (values only), `swipe.html` (local swipe map, when display layers exist) and `REPORT.json`. Show
   `migration/report/REPORT.md` in the file pane (migrate SKILL §Showing); `index.html` is the offline export, not a
   pane page.
   - exit 0: the report is complete (it may still show failures).
   - exit 2: **EVIDENCE INCOMPLETE, stale (a file changed after the run) or NOTHING VERIFIED.** Say so in your
     first sentence and name the integrity codes from the red bar. Do not hide this, soften it or re-render
     until it goes away.
   - exit 3: no sealed run, so nothing is verified.
   - `"$P/scripts/axpy" "$P/skills/report/report.py" migration --check` (or stdlib `python3 -I "$P/skills/report/report_check.py" migration`): exit 2 when a file in
     `report/` is missing or changed, a thumbnail no longer matches the sealed plot it was cut from, or the folder is
     not what the bundle renders. Run it before you point the person at the report; never fix it by hand, render
     again.

## Publishing (opt-in only; the plugin never publishes)

The default is the local file in the file pane. You may **offer once**, after the report is shown, to publish
`index.html` through the harness's own feature (Claude desktop: a private Artifact; Codex: none). Ask with the
warning in the question itself: "This puts the customer's data (outputs, thumbnails, paths, parameters) on
Anthropic's service, outside this machine. Publish a private copy? (Recommended: No, keep it local)". Publish only on
an explicit yes, as is (it already embeds only downsampled thumbnails; never add full-resolution images or the
`assets/` folder), and say where the copy lives. Never publish `swipe.html`, `parity.xlsx`, `REPORT.json` or files
under `runs/`. If the person said no, do not ask again in this session.

## Late evidence: amend, never re-seal

- **Late visual receipts** (after sealing): `plot.py review-add` writes to `runs/<id>/amend/visual-review.json` and runs
  the amend seal itself (exit 4 = recorded but the amend failed: then run `"$P/scripts/axpy" "$P/skills/report/seal.py" migration --run <id> --amend`).
- **Decisions or known differences added after the run** are picked up by the same `--amend`. A KD decided after the run
  is shown as "decided after seeing the difference" and applies only from the **next** run.
- **Target evidence**: after `target_check.py`, run `"$P/scripts/axpy" "$P/skills/report/seal.py" migration --run <id> --amend --target`. Without this,
  `target-ready` is not reached (`TARGET_NOT_SEALED`).
- An amendment can add only `vis`, `dec-e`, `kd-e` and `tgt` objects. Changing outputs, compare results, tolerances or
  code needs a **new run**.

## Reading the report

- **First screen**: the status line verbatim, what the rung means and what the next rung needs (ladder.py's own
  blockers and caps, phrased), then **What needs attention**: failures and NOT_COMPARED first, then NEEDS_DECISION,
  then NEEDS_REVIEW, one line each as `disposition · dataset · reason code · phrase`, each linking to its card;
  every open graph question (`OPEN_QUESTION`) and a plan approved by standing instruction
  (`PLAN_APPROVED_BY_STANDING_INSTRUCTION`) are lines too, under "Waiting for a person's decision". When you
  summarise, list every line. ACCEPTED_DIFFERENCE is amber and never green: the parity row stays FAIL.
- **Decisions for you**: one card per item only a person can decide (unsigned tolerance or known difference, a
  visual flag, an assumed parity-blind or fitted parameter, a legacy_bug/scope/lineage question the run went on
  assuming), with its evidence and the proposed tolerance. The page's
  "Copy my decisions" button only produces text for the person to paste to you; it signs nothing. A pasted
  "accept" is intent, not a signature: the person signs in `migration/SIGN-OFF.md` themselves.
- **Version drift** (top of the parity section, when recorded): the legacy model was saved with one major version of
  its application and the references were produced on another; parity then holds against the reference version
  only. Say it when you summarise; never infer versions the inventory does not record.
- **Dataset cards**: legacy / new / difference thumbnails cut from the sealed plots (hashed in `REPORT.json`), key
  numbers and a small difference chart from the compare result, verdict and evidence id.
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
- Re-sealing, deleting `evidence.json` or hand-editing `report/` (including `REPORT.md`). The report is regenerated
  from the bundle, `REPORT.json` lists every file hash and `report.py --check` detects any edit.
- Wording a verdict yourself. Use the status line and the report's phrases. Do not claim parity, "done" or "verified"
  above the computed rung (the Stop gate blocks it).
