# axis-migrate: demo script (AAI4EO, Mon 19 Oct 2026, 18:15, 25 minutes)

Status: rehearsed on 14 Oct 2026 with plugin `main` at 0b758ea, on scratch copies of `demo/lux-exposure`
(`scratch/rehearsal-1` … `rehearsal-7`; nothing under `demo/` or `fixtures/` was touched). I acted as the developer
and wrote the pipeline code that the agent would write. No headless model runs were made. Every number below comes
from a real run of the plugin's own scripts. Screenshots are in `docs/demo-assets/`, and the helper scripts are in
`devtools/demo/`.

The founder's decisions under test (DESIGN §12 Q3) were: (a) a failing first run is kept in the history, (b) one
guard fires, (c) a live graph edit automates the canton rename, (d) ArcPy 03b is migrated as a translation showcase.

## 0 Verdict of the rehearsal (read this first)

| Part | Works as designed? | One-line summary |
|---|---|---|
| (a) failing run, then fixed run | **Yes** | The grid gate catches a half-cell snap at s01 (0.500 px) while the population total is identical. s02–s04 show *inherited* failures. Both runs stay in the history. |
| (b) guard fires | **Yes** | `guard_write.py` **denies** a loosened default and **denies** an edited signed record. It **asks** for a new record. `compare.py` refuses a tampered contract with `EVALUATOR_ERROR/TOLERANCE_CHANGED_AFTER_RUN`. |
| (c) live graph edit (m01 → migrate) | **Yes** | The real page in Chrome was driven by clicks: "Automate this manual step", then q01 answered, then "Send to agent". `graph_server.py wait` returned the diff with `automate_manual_step` and `apply_answer` obligations. |
| (d) ArcPy 03b showcase | **Partly: do not show the headline as is** | Page edits, pairing, translation, run and compare all work: `NODATA_MASK_MISMATCH` (404,349 cells), conservation exact. KD-003 is recorded as **proposed**. But `ladder.py` ignores equivalent references (D1), so the report shows the step as `NO_REFERENCE` and the status line says **0 FAIL**. `ACCEPTED_DIFFERENCE` cannot appear even with a signed KD. The vector pairing fails with `LAYER_AMBIGUOUS` (D2). |

**What Najah must decide or do before Monday:** see §6. In short:
1. Get D1, D2 and D6 fixed and re-rehearse (d), or show (d) only as "the comparator's finding" (wording in §3.4).
2. Sign (or deliberately not sign) the three tolerance proposals yourself.
3. Build the stage workspaces (§2) on Sunday.
4. Check once, interactively, that an `ask` hook really prompts on your machine.

## 1 Honest wording (use these sentences; do not improvise claims)

- Always read out the status line **verbatim** from `ladder.py`. The status line is computed from hashed evidence. Nobody
  types it.
  - After the fixed run (no signatures):
    `axis-migrate status: sandbox-validated | run r…-a1b21207 | 11 pass, 0 FAIL, 0 NOT_COMPARED, 0 needs review | report migration/report/index.html`
  - After the failing run:
    `axis-migrate status: sandbox-validated | run r…-fd4326d5 | 2 pass, 10 FAIL, 0 NOT_COMPARED, 0 needs review | …`
  - After Najah signs, a new run plus the target pack gives (rehearsed with a TEST identity):
    `axis-migrate status: target-ready | run r… | 14 pass, 0 FAIL, 0 NOT_COMPARED, 0 needs review | …`
- "**sandbox-validated** means it ran for real, in the locked environment, with a read ledger. It says nothing about
  parity. Parity is the FAIL/PASS count next to it." (A failing run is still sandbox-validated. Say this explicitly
  or the audience will read the amber box as "ok".)
- "**target-ready, not deployed.**" Then read the "Not done" sentence of the target claim aloud:
  *"Not done: nothing was deployed, no Databricks workspace was contacted, and the code has not run on a Databricks
  cluster. …"* Never say "deployed", "runs on Databricks", "production-ready" or "validated on Databricks".
- The visual review was done by the model, and it "may only flag": a flag can only move a PASS to NEEDS_REVIEW.
- Proposed tolerances and known differences are "proposed by the agent, **waiting for a human**". They are not "accepted".
- Anything signed in the rehearsal copies is a **TEST signature** (`human:rehearsal-test`). Never show those
  screenshots without saying so. They are labelled `…-TEST.png`.
- The lux data is synthetic and built for the demo. Do not call it a customer project.

## 2 Stage set-up (Sunday; about 15 minutes of machine time plus one visual review)

Never work in `demo/`. Build two stage workspaces next to it, for example under `~/axis-migrate-dev/stage/`.

**Stage A: history, guard and final status** (the main workspace on the projector):
```
P=~/axis-migrate-dev/axis-migrate; AX=$P/scripts/axpy
cp -a ~/axis-migrate-dev/demo/lux-exposure ~/axis-migrate-dev/stage/A && cd ~/axis-migrate-dev/stage/A
python3 ~/axis-migrate-dev/devtools/demo/plant_s01_snap.py . plant          # 'tidy grid' mistake in s01
$AX $P/skills/run/run_local.py migration --chain chained                     # → r…-fd4326d5 (code hash changes)
~/axis-migrate-dev/devtools/demo/after_run.sh . <run_id> verify              # compare + plots
#   visual review: the agent (or you) READS the PNGs and records receipts (plot.py review-add; or
#   devtools/demo/receipts.py with an answers file written by whoever looked)
~/axis-migrate-dev/devtools/demo/after_run.sh . <run_id> seal                # aggregate, display, seal, ladder, report
cp -a migration/report ../A-report-failing-run                               # keep this render (see note)
$AX $P/skills/discover/graph.py ingest-verify migration --run <run_id>; $AX $P/skills/discover/graph.py derive migration
python3 ~/axis-migrate-dev/devtools/demo/plant_s01_snap.py . revert          # byte-identical to the verified module
# chained run → verify → review → seal → ingest-verify → derive, as above   → r…-a1b21207, 11 pass, 0 FAIL
# Najah signs (SIGN-OFF.md), waits ≥ 1 s, then: chained run → verify → review → seal → target (§3.5)
```
Note: rendering the failing run later with `report.py --run <failing id>` gives a red **STALE** banner
(`CODE_CHANGED_AFTER_RUN`), which is correct because the code was reverted. Keep the copy taken right after sealing
(`A-report-failing-run/index.html`; a copied report folder works from `file://`), or show the stale banner and
explain it.

`scratch/rehearsal-2` already holds the first two runs, with receipts from a model that did look at the plots.
It can serve as stage A if there is no time.

**Stage B: live graph edit** (fresh lineage, so m01 is still `keep_legacy`):
```
mkdir -p ~/axis-migrate-dev/stage/B && cp -a ~/axis-migrate-dev/demo/lux-exposure/lux-exposure-legacy ~/axis-migrate-dev/stage/B/
cd ~/axis-migrate-dev/stage/B
$AX $P/skills/discover/scan.py lux-exposure-legacy --out migration           # 11.9 s
$AX $P/skills/discover/lineage.py migration                                  # 0.2 s: 6 steps, 26 datasets, 13 pairs, q01
$AX $P/skills/discover/graph_server.py serve migration &                     # prints http://127.0.0.1:<port>/?t=<token>
```
Make one copy of B per rehearsal attempt (`cp -a stage/B stage/B2`). An edit cannot be undone, by design.

**Stage C: 03b showcase.** Use `scratch/rehearsal-4` (pre-baked: s03b migrated, run r20261014T073445Z-fbbb48ab,
KD-003 proposed) unless D1/D2 are fixed. If they are fixed, rebuild it from stage A (§3.4 has the steps).

Open these before you go on stage: browser tabs for A's report, A's failing-run report copy, the graph page of B,
the graph page of C and C's report. Also a terminal in A, one in B, and Claude Code in A with the plugin loaded
(check `/hooks` lists the axis-migrate hooks).

## 3 Run of show (25 minutes)

| Time | Segment | Live / pre-baked |
|---|---|---|
| 0:00–2:00 | Problem and principle: "scripts decide, the model writes and looks" | slide or terminal |
| 2:00–7:00 | Report of the fixed run: ladder, counts, failures first, swipe map, status line | A, live browser |
| 7:00–11:00 | (a) Run history: the failing run, s01 card, worst tiles, graph inheritance | A, pre-rendered |
| 11:00–13:00 | (b) Guard: a tolerance edit after the FAIL is refused | A, live terminal |
| 13:00–17:30 | (c) Live graph edit: automate the canton rename | B, live clicks |
| 17:30–22:00 | (d) ArcPy 03b translation: pairing by a human, 0-vs-NoData found, KD proposed | C, live clicks + pre-baked |
| 22:00–24:30 | Signed, target-ready, "Not done" | A (after Najah signs), live browser |
| 24:30–25:00 | Close: quote the status line | – |

### 3.1 Report walk-through (2:00–7:00)
Open stage A, `migration/report/index.html` (screenshot `demo-assets/a6-fixed-run-report.png`). Point at:
- The **ladder**. `deployed`, `live-target-verified` and `customer-accepted` are drawn greyed as "not attempted"; the ladder can never emit them.
- The **counts**: PASS_EXACT 11 and NEEDS_DECISION 3. The three are `ds.02_res_2169` and `ds.02_res_4326` (`area_m2`
  float noise ≤ 8.64e-13, nearest wrong 1.4e-5) and `ds.lux_res_exposure_2020` (per-canton invariant: the legacy
  itself only holds within 1.57e-9). Say: "the agent measured two numbers for each and proposed a tolerance; only I can sign it. Until then it is
  NEEDS_DECISION, never a pass."
- The **failures list** is the first thing on the page, and every line has a reason code.
- The **swipe map** (scroll down). Read its caption: "Display only … Verdicts are computed on the native grid."
- The **status line**: read it verbatim.

### 3.2 (a) The failing run in the history (7:00–11:00)
Scroll to **Run history** (`a7-run-history.png`). It lists the trial's isolated runs, the trial's chained run, the
failing run **r…-fd4326d5 (10 FAIL)** and the fixed run **r…-a1b21207 (0 FAIL)**. Say: "failing runs stay; nothing
is deleted".

Open the failing-run report copy (`a1-failing-run-report.png`). Expected numbers (rehearsal run
r20261014T052758Z-fd4326d5):
- `s01_prepare_population` FAILED with `RASTER_TRANSFORM_MISMATCH`: transform deviation **0.500019 px** (limit 1e-6),
  `shift_px` (−0.500019, −0.499726).
- `conservation.total` **PASS_EXACT**, legacy 588219.8669 = new 588219.8669 (rel 0). The values were copied 1:1.
- `conservation.zone_totals` **FAIL** `ZONE_TOTAL_DRIFT`, max rel 0.0103 (Echternach). On the shifted grid, 486
  cells (731.8 people) fall outside every canton by the cell-centre rule. Say: "the national total is identical, to
  the person; only the grid gate and the per-canton check notice".
- s02, s03 and s04 are FAILED with blame **inherited** (`INHERITED_FROM:ds.01_pop_lux`). `first_failing_steps` is
  `['s01_prepare_population']`.
- Status line: `sandbox-validated | … | 2 pass, 10 FAIL, 0 NOT_COMPARED, 0 needs review`.

Show the s01 card (`a2-failing-s01-card.png`), then `worst_tiles.png` (`a3-worst-tiles-grid-overlay.png`). This is
the only view where half a cell is visible: blue legacy grid, orange dashed new grid, one half cell north-west. Also
show `hist.png` (`a4-total-same-zones-drift.png`): the totals are identical and the per-canton names are in red.

On the graph page of A after `ingest-verify` + `derive` (`a5-graph-page-inherited.png`), s01 is solid red and
s02–s04 are hatched red ("inherited").

**Wording caution (D6):** the report phrase reads "grid shifted by −0.500019 px east, −0.499726 px north". The second
half has the wrong sign: the new grid moved half a cell **north** (and west). Say "half a cell north-west, see the
tiles" rather than reading the phrase aloud.

What the mistake was (for questions): the step "tidied" the output origin to whole multiples of the cell size
(`gdalwarp -tap` style) right before the write: `devtools/demo/plant_s01_snap.py` (4 lines). The fix is a byte-for-byte
revert: same code hash `a1b21207` as the trial's verified run.

### 3.3 (b) The guard fires (11:00–13:00)
Primary, live in Claude Code in A: ask *"As a test of the guard, use the Edit tool on migration/tolerances.yaml and
change defaults raster.values rtol from 0 to 1e-3."* The model may refuse on its own (rule 3 of `/migrate`); that is
fine to show too. If it tries, the hook answers.

Fallback, or as the second beat: feed the hook the real Claude Code PreToolUse payload. Run it from `~/axis-migrate-dev`;
it takes under 100 ms:
```
python3 devtools/demo/guard_probe.py stage/A migration/tolerances.yaml \
  "atol: 0, rtol: 0, max_mismatch: 0}" "atol: 0, rtol: 1.0e-3, max_mismatch: 0}"
```
Expected output (rehearsed, also with `PERMISSION_MODE=bypassPermissions`):
```
decision: deny
reason:   axis-migrate: tolerances.yaml defaults/top level changed after a run recorded its hash (r…). Tolerances
          are fixed before the run; propose a new versioned record.
```
Other cases (all rehearsed):

| Edit after a run | Decision | Why |
|---|---|---|
| loosen `defaults` | **deny** | top level changed after a run recorded the hash |
| edit an existing (signed) record, e.g. rtol 1e-8 → 1e-3 | **deny** `TOLERANCE_CHANGED_AFTER_RUN` | "Add a new record id (...v2) with a reason, then a new run is required." |
| add a new record (e.g. `tol.01_pop_lux.raster.grid.transform.v1`) | **ask** | the user decides. Interactively that is Claude Code's permission prompt; click **No**. Headless it resolves to deny (HM2). |
| add a record carrying `agreed_by: human:najah` | **ask** | only a human signs |
| `sed -i` on tolerances.yaml (Bash) | **deny** (guard_bash) | "change it with the Edit tool so the user is asked" |
| widen an unsigned proposal in `tolerances.proposed.yaml` | allowed | proposals never make a check pass (by design) |

Script half, to mention or show as a recording: after tampering the live `tolerances.yaml` behind the hooks' back
(rehearsal-1), `compare.py run` on the existing run gave **EVALUATOR_ERROR / TOLERANCE_CHANGED_AFTER_RUN** on every
dataset (0.8 s). A changed contract needs a new run.

Talking point: the grid gate threshold (1e-6 px) is not a tolerance record at all. No signed record can widen it.

### 3.4 (c) Live graph edit: automate the canton rename (13:00–17:30)
On the graph page of stage B (`graph_server.py serve` URL with the token):
1. Click the node **"Manual edit: cantons_lux_edit.gpkg …"** (`m01_cantons_lux_edit`). The panel shows scope
   `keep_legacy (manual_pending_answer)` and a blue callout (`c1-graph-m01-panel.png`).
2. Click **"Automate this manual step"**. The banner shows "Saved: automate m01_cantons_lux_edit (revision 2)" and
   the scope reads "migrate set by user" (`c2-graph-m01-automated.png`).
3. In the same panel, under **Questions** → q01, choose **"Derive in code (Recommended)"** and click **Answer**.
4. Click **"Send to agent (2)"** and type a message, for example "Automate the canton rename and verify it against
   cantons_lux_edit.gpkg". Then click **Send to agent** (`c3-send-dialog.png`).

In the terminal beside it, `graph_server.py wait migration` (started before step 1; Claude Code runs it in the
background) exits 0 within a second of the click and prints the diff:
```
"obligations": [
  {"code": "automate_manual_step", "op": "set_scope", "target": "m01_cantons_lux_edit",
   "obligation": "automate manual step m01_cantons_lux_edit: derive its rule in params.yaml (an interview answer),
                  plan the step, verify it against the hand-made legacy file"},
  {"code": "apply_answer", "op": "answer_question", "target": "q01",
   "obligation": "write params.yaml (source: answer:q01) and decisions.md for answer 'derive'; ..."}]
```
Say: "the scope is now a user-owned field; the agent cannot change it back". Until the agent runs `graph.py ack`, the
Stop hook blocks the agent from ending its turn ("the user edited the workflow graph (1 unacked edit(s))"), and the next prompt announces the
edit (H2). Both were rehearsed.

Then switch to stage A: there, the automated m01 is **VERIFIED, PASS_EXACT against the hand-made
`03_INPUT/cantons_lux_edit.gpkg`** (12/12 cantons, the Esch/Alzette exception included). Say: "that is what the agent
does with this edit; it takes a few minutes, so here is the run where it did it."

Fallback: the screenshots `c1…c3`, plus the `wait` JSON above. The page API answers 403 without the token and 403
`bad_origin` for a foreign Origin (rehearsed with curl).

### 3.5 (d) ArcPy 03b as a translation showcase (17:30–22:00)
Rehearsed clicks (stage C graph page; `d1…d3`):
1. Node **"OLD - step 03 from when this ran in ArcGIS …"** (`s03b_zonal_arcpy_old`): scope select → **migrate**,
   reason "translation showcase: verify against step 03" → **Set scope** (`d1-graph-s03b-migrate.png`).
2. Pill **pop_res** (`ds.work__pop_res`) → open **Reference pairing** → "equivalent dataset" `ds.03_pop_res` → **Set
   reference**. The panel then shows "reference: equivalent (human via ds.03_pop_res, confirmed by user)" and the
   purple edge reads "≡ equivalent (confirmed)" (`d2-graph-reference-confirmed.png`).
3. Do the same for **canton_pop** → `ds.03_canton_pop` and **pop_lux** → `ds.01_pop_lux`. **Reload the page between
   datasets** (D5: the page keeps the previous dataset's choice; the rehearsal got "Not applied: … no equivalent_to
   edge between ds.work__pop_lux and ds.03_canton_pop", `d3-graph-reference-ui-bug.png`).
4. **Send to agent**. `wait` prints `migrate_step` ("s03b_zonal_arcpy_old is back in scope: plan and migrate it") and
   one `check_reference` per pairing.
5. Plan approval for s03b on the page ("Approve this step's plan") prints `plan_approved`. Before the approval, the
   write hook denied `pipeline/s03b_zonal_arcpy_old.py` ("the plan … is not approved").

What the agent then did (in the rehearsal, written by me as stand-in; ≈ 6 minutes of work, so pre-bake it):
- Rewired the inputs. 03b reads `work.gdb/res_mask` and `work.gdb/cantons_edit`, which never existed. Without
  rewiring, every chained run is **BLOCKED, exit 12 `INPUT_MISSING:ds.work__cantons_edit`** (D4). The agent fix:
  `add_edge` reads from `ds.02_res_mask` and `ds.cantons_lux_edit`, citing 03b:19–20 and the documented
  `equivalent_to` edges. Then `reject_edge` on the two gdb reads.
- `set_new` per output, because the default would give five datasets the same `s03b_zonal_arcpy_old/work.gdb` (D3).
- Nine `p.s03b_*` params. Two NoData values are honestly `assumed`, because the gdb never existed.
- A plan block, then `pipeline/s03b_zonal_arcpy_old.py`. The translation is faithful: ExtractByMask on the WorldPop
  grid, `Con(IsNull(mask),0,mask)`, `Con(pop>=0.5,pop,0)*mask0`, zonal SUM by cell centre, and the canton copy with
  `pop_sum`/`res_sum`. codescan: clean.

Expected numbers (chained run r20261014T073445Z-fbbb48ab, 6 steps, 11.8 s):
- `ds.work__pop_res` vs `05_TEMP/03_pop_res.tif` (via the equivalent pairing): **FAIL `NODATA_MASK_MISMATCH`, 404,349
  cells valid in new only, 0 in legacy only**. On the 52,415 cells valid on both sides the values are identical
  (`raster.values` PASS_EXACT). `conservation.total` PASS_EXACT (273489.3342 both) and `conservation.zone_totals`
  PASS_EXACT. Show `mask_diff.png` (`d4-mask-diff-0-vs-nodata.png`): orange = 0 in new where legacy has NoData.
- `ds.work__pop_lux` vs `01_pop_lux.tif`: **PASS_EXACT** (456,764 cells). ExtractByMask with the cantons equals the
  QGIS clip with the dissolved outline.
- `ds.work__canton_pop` vs `03_canton_pop.gpkg`: **EVALUATOR_ERROR `LAYER_AMBIGUOUS`** (D2). With the right layer
  (standalone `compare.py pair`) it is `SCHEMA_MISMATCH`: `pop_count` and `res_count` are missing, as 03b never wrote
  counts. `pop_sum` and `res_sum` are **PASS_EXACT** for all 12 cantons. Do not show this card unless D2 is fixed. In the
  rehearsal copy used for the TEST signature, its pairing was set to "no reference" on the page.
- The graph page shows `pop_res` **F FAIL · origin · ! visual flag: missing_region** (`d5-graph-pop-res-fail.png`).
- KD-003 was recorded with `record.py kd` (owner `agent`, status **proposed**, kind `accepted_change`, rule
  `new = legacy`, bound `count_over` max 0, measured 0). The report lists it as "owner: agent (not a named human:
  ignored) … decided after seeing the difference; applies from the next run" (`d8-kd003-proposed.png`). SIGN-OFF.md
  lists it with the exact lines to edit.

**What the report shows instead of the design (D1):** the s03b step card reads **NO_REFERENCE · superseded_only →
at most sandbox-validated**, and the status line stays `11 pass, 0 FAIL` (`d6-s03b-report-top.png`,
`d7-s03b-card-no-reference.png`). The FAIL is visible only inside the dataset card's check table and on the graph page.
The label "reference from equivalent step" appears nowhere in the report.

**TEST-signature check (scratch copy `rehearsal-5`, identity `human:rehearsal-test`, decision D-005 titled "TEST
SIGNATURE"; this is NOT a real sign-off):** with KD-003 accepted, the next run computes `kd:KD-003`
**PASS_WITHIN_TOLERANCE** (held, count_over 0), while `raster.mask` **stays FAIL**. That part is as designed
(`d9-kd003-TEST-accepted.png`, `d10-pop-res-card-TEST.png`). The ladder still says NO_REFERENCE (D1), so
**ACCEPTED_DIFFERENCE never appears**. A what-if render with the reference check patched in-process
(`devtools/demo/ladder_whatif.py`) did not reach amber either. seal.py drops the second ref object for the shared
file (`ref:<sha>` is already owned by `ds.03_pop_res`), so the run became **EVIDENCE INCOMPLETE / UNBOUND_COMPARE**.
A fix needs both parts (§5, D1).

Presenter wording if D1 is not fixed by Monday: *"The comparator paired the ArcPy translation with the QGIS step's
files because I confirmed the pairing, and it found the one real difference: zeros where QGIS writes NoData, 404,349
cells, with every total identical. The agent proposed this as a known difference; only I can accept it. One honest
gap: the status ladder does not yet count pairings across equivalent steps, so this step shows as 'no reference'
rather than amber. That is a defect we found in rehearsal."* Do not show the headline counts as evidence for 03b.

### 3.6 Signed → target-ready, "Not done" (22:00–24:30)
Only after Najah himself signs the three proposals (SIGN-OFF.md: copy each record into `tolerances.yaml`, set
`agreed_by: human:najah`, `agreed_at`, `decision_ref`). Then make a **new** run at least one second later: a record
signed in the same second as the run start is refused as "agreed_at … is not before the run start", which is correct.
Rehearsed with a TEST identity in `rehearsal-6`:
- The new chained run gives the three datasets PASS_WITHIN_TOLERANCE. `ladder.py`: **reference-verified**, 14 pass.
- `make_databricks.py` (0.3 s) writes 4 tasks (s01–s04; no task for m01, see the note below) and the wheel
  `axis_pipeline-0.1.0+a1b21207`. `target_check.py --run <id>` (14.1 s) gives T1–T13 PASS. `seal.py --amend --target`
  (0.1 s) seals it. Status line: **`target-ready | … | 14 pass, 0 FAIL, 0 NOT_COMPARED, 0 needs review`**.
- Report: "target-ready (not deployed)", and the claim box (`t1-target-ready-TEST.png`, `t2-target-claim-TEST.png`)
  ends with the **Not done** sentence. Read it aloud.

Note for questions (D13): the bundle has 4 tasks. The ported canton rename `m01` gets **no** task, because
`make_databricks.py` skips every `kind: manual` step, even a migrated one (DESIGN §4.8 "per non-manual graph step";
target SKILL). On Databricks, `cantons_lux_edit.gpkg` would therefore be an uploaded input (README-deploy §3), not
computed. If asked, say exactly that. Do not claim the automated rename is in the job.

Fallback: the TEST screenshots, labelled as such.

## 4 Measured timings (this host, 14 Oct; seconds)

| Command | Time |
|---|---|
| `cp -a demo/lux-exposure …` | 0.24 |
| `scan.py` (fresh) / `lineage.py` | 11.9 / 0.22 |
| `codescan.py --file` / `params_check.py --stage wired` | 0.18 / 0.18 |
| `run_local.py --chain chained` (5 steps / 6 steps with s03b) | 10.4–10.9 / 11.8–12.4 |
| `run_local.py --chain isolated --steps s03b…` | 3.9 |
| `compare.py run` (14 / 19 datasets) | 3.5–3.7 / 6.0–6.6 |
| `compare.py run` refusing a changed contract | 0.8 |
| `plot.py run` | 37.5–48.7 |
| `plot.py review-add` (a batch of 12–37 findings) | 0.7–0.9 |
| `compare.py aggregate` | 0.3 |
| `map.py display` | 14.9–21.2 |
| `seal.py` / `seal.py --amend` | 3.3–3.4 / 0.1 |
| `ladder.py` | 0.2 |
| `report.py` | 8.1–8.5 |
| `graph.py ingest-verify` / `derive` | 0.15 / 0.29 |
| `guard_write.py` decision (real payload) | 0.07–0.09 |
| graph page load / click → POST 200 / `wait` returns after Send | 1.1–1.2 / < 1 / < 1 |
| `make_databricks.py` / `target_check.py` / `seal --amend --target` | 0.3 / 14.1 / 0.1 |

One verified chained run after a code change costs about 80 s of scripts plus the model's visual review. In the
rehearsal that review meant reading ~50 PNGs for the first run and the 13–30 changed ones afterwards; live, it takes several minutes. So
nothing that produces a new run is done live on stage, apart from the graph edit (no run) and the guard (no run).

## 5 Defects found (not patched; plugin code untouched)

| # | Severity | Where | Behaviour | Expected |
|---|---|---|---|---|
| D1 | **blocker for (d)** | `skills/report/ladder.py` `ds_has_reference()` (l. 572); `skills/report/seal.py` reference objects (l. 350–376); ladder `ref_by_ds` (l. 719) | A dataset whose own `legacy.exists` is false (gdb item) or that has `no_reference_reason: superseded_only` is `NO_REFERENCE`, even with `reference = {source: equivalent, via_dataset, confirmed_by: user}`. Its compare FAIL is excluded from the counts (status line "0 FAIL"). The step disposition is NO_REFERENCE, and ACCEPTED_DIFFERENCE is unreachable. Patching only `ds_has_reference`/`ds_legacy_path` (what-if harness) makes seal emit `ref:<sha>` for the via file. That id is already owned by the via dataset, so it is dropped, ladder finds no ref for the dataset, and the result is `UNBOUND_COMPARE`/EVIDENCE INCOMPLETE. | A confirmed equivalent reference counts as a reference (the via dataset's file and pin), so the FAIL is counted and amber is reachable with an accepted KD. One ref object may serve several datasets (or the binding is by path/sha), and the report labels it "reference from equivalent step". |
| D2 | high for (d) | `skills/verify/compare.py` `_pair_one()` (l. 2984–3026) | For `source: equivalent`, `L_path` is switched to the via dataset's file, but `spec["layer"]`/`sheet` keep the dataset's own gdb layer (`canton_pop`). Opening `03_canton_pop.gpkg` therefore fails: `EVALUATOR_ERROR/LAYER_AMBIGUOUS`, and the plots show "legacy unreadable". | Use the via dataset's `legacy.layer`/`sheet` (and its compare key/zones when unset). |
| D3 | medium | `skills/discover/graph.py`: default `new` derivation for container formats | All five `ds.work__*` outputs of s03b get `new.path = s03b_zonal_arcpy_old/work.gdb` (a collision, and a directory format). `graph.py validate` stays silent. | Default `<step>/<layer>.<ext by kind>` for multi-layer containers, or `validate` flags duplicate `new.path`. |
| D4 | medium | `GRAPH_OPS.md` §4 obligation `migrate_step`; `graph.py` role derivation | Switching a `drop/alternative_impl` step to migrate auto-roles its never-existing gdb inputs (`ds.work__res_mask`, `ds.work__cantons_edit`) to `input`. Every chained run is then **BLOCKED, exit 12 `INPUT_MISSING`**. The obligation says only "plan and migrate it". | The obligation (and migrate SKILL §5) tells the agent to map missing inputs to their `equivalent_to` datasets (`add_edge` + `reject_edge`) or to ask. Or lineage records those reads as equivalents. |
| D5 | medium (stage risk) | `devtools/graph-ui/src/Panel.jsx` `ReferenceControl` (bundled as `skills/discover/graph.bundle.js`) | `useState(eq[0])` for `via` is not reset when another dataset is selected (unlike `ScopeControl`/role, which use `useEffect`). Pairing a second dataset posts the previous dataset's via → 400 "no equivalent_to edge between ds.work__pop_lux and ds.03_canton_pop". | Reset `mode`/`via` on `d.id` change. Stage workaround: reload the page between pairings. |
| D6 | medium (wording) | `skills/report/phrases.v1.yaml:31` vs `compare.py _transform_dev()` l. 873 | `shift_px[1] = (new.f − legacy.f)/legacy.e` is in rows (positive = south), but the phrase prints "{shift_px[1]} px north". A grid moved 0.5 px north reads "−0.4997 px north". | Negate it for "north", or say "rows (down)". |
| D7 | medium | `skills/verify/SKILL.md`, `skills/report/SKILL.md` | No skill tells the agent to run `graph.py ingest-verify` and `graph.py derive` after sealing (only GRAPH_OPS §6 documents them). The graph page keeps the previous run's colours. After ingest without derive, s01 showed "FAIL" next to "ladder reference-verified · VERIFIED" from the older run. | Add both commands to the report skill's order, after seal. On stage, run them by hand (0.4 s). |
| D8 | low | `skills/report/phrases.v1.yaml` | No phrase for `superseded_only`; the report prints "superseded_only superseded_only". | Add a phrase. |
| D9 | low | `skills/verify/compare.py` `run_compare` | With a global contract error, out-of-scope datasets (`ds.geoboundaries_lux_adm2`, `ds.work__*` gdb items) also get `EVALUATOR_ERROR` result files (17 instead of 14). | Skip out-of-scope datasets before the global-error early return. |
| D10 | low | `skills/interview/record.py decision` | There is no decision kind for accepting a KD or signing a tolerance (`kind` must be one of agent_decision … target). The rehearsal used `answer`. | Add `kd_acceptance` / `tolerance_signature`. |
| D11 | info | demo workspace `known-differences.md` | KD-002 corrects KD-001 only in its prose (no `supersedes:` field), so `signoff.py --check` exits 2 forever ("KD-001: accepted_change rule must be …"). The agent may try to append a superseding KD live. | Before the demo, decide: leave both proposed (recommended; KD-002 has `checks: []`, so its bound can never be evaluated, "bound held: no"), or let the agent append a KD with `supersedes: KD-001`. |
| D13 | design gap | `skills/target/make_databricks.py` (task per *non-manual* step) | A manual step ported to code (`m01`, scope migrate, VERIFIED) gets no `python_wheel_task`, so the job consumes the hand-made file. | Emit a task for `kind: manual` steps whose scope is `migrate` (and include them in T2's verified task graph). Or state it in the claim. |
| D12 | info (UX) | graph page | "Automate this manual step" changes the scope but leaves q01 (the policy question) open. If the presenter does not also answer q01 on the page, the agent will ask it again. | Answer q01 on the page in the same visit (the rehearsed clicks do). |

Behaviour that is correct but needs a sentence on stage:
- A failing run still shows the rung **sandbox-validated** (execution rung).
- A re-render of an older run is **STALE**.
- A signature made in the run's start second is refused.
- The zone-total check fails on the half-cell shift (1.03 %), although the national total is exact.

## 6 Before the demo: Najah's list

1. **D1 + D2 (+ D6, D7, D5)**: have them fixed with regression tests, then rebuild stage C and re-rehearse §3.5. That
   takes about 30 minutes of machine and review time. Otherwise use the §3.5 fallback wording and keep the headline
   off screen for 03b.
2. **Sign or not sign** the three tolerance proposals yourself (SIGN-OFF.md). Nobody else may. Without a signature,
   the demo ends at sandbox-validated with 3 NEEDS_DECISION, which is also an honest ending. KD-001/KD-002: see D11.
3. **Interactive `ask` check:** on the presenting machine, start Claude Code in a scratch copy and let the agent add a
   new tolerance record. Confirm that a permission prompt appears, then click No. This was never observed interactively
   (HOOK_MECHANICS HM2). If no prompt appears, use the deny cases only.
4. **Build stages A, B and C on Sunday** (§2), with a fresh `graph_server.py serve` for B and C on Monday afternoon.
   The token changes per start, so write the URLs down.
5. **Recordings:** record §3.4 (the clicks plus the `wait` output) and §3.5 steps 1–4 as screen videos. The playwright
   driver `devtools/demo/graph_drive.mjs` replays exactly the rehearsed clicks. Also keep the PNGs in
   `docs/demo-assets/` as slides.
6. Check `/hooks` in the demo session shows the axis-migrate hooks. Run `GDAL_PAM_ENABLED=NO`-safe commands only. Never open
   a legacy raster in QGIS on stage, because that writes `.aux.xml` and gives `LEGACY_REF_CHANGED`.
7. Decide how to answer the question about m01 on Databricks (§3.6 note, D13).

## 7 Helper scripts (dev-only, `devtools/demo/`)

| Script | Use |
|---|---|
| `plant_s01_snap.py <proj> plant\|revert\|status` | plants or reverts the half-cell "tidy grid" mistake in a scratch copy (refuses `demo/` and `fixtures/`) |
| `after_run.sh <proj> <run_id> verify\|seal` | the plugin's own post-run commands, timed |
| `receipts.py <proj> <run_id> answers.json` | turns a reviewer's answers into `plot.py review-add` findings (fills only hashes, panels, crs). It never decides an answer. |
| `guard_probe.py <proj> <file> <old> <new>` | feeds `guard_write.py` a real PreToolUse Edit payload and prints the decision |
| `graph_drive.mjs <url> <outdir> actions…` | drives the real graph page in Chrome (node/btn/css/fill/select/shot) |
| `shot.mjs <url> <prefix> [selector[@maxh]]…` | first-screen and element screenshots of a report |
| `ladder_whatif.py <scratch>/migration [--seal RUN] [--report]` | D1 what-if (in-process patch, scratch copies only, red banner). Documents why the simple patch is not enough. |

## 8 Screenshot index (`docs/demo-assets/`, 4.1 MB)

`a1-failing-run-report` · `a2-failing-s01-card` · `a3-worst-tiles-grid-overlay` · `a4-total-same-zones-drift` ·
`a5-graph-page-inherited` · `a6-fixed-run-report` · `a7-run-history` · `c1-graph-m01-panel` ·
`c2-graph-m01-automated` · `c3-send-dialog` · `d1-graph-s03b-migrate` · `d2-graph-reference-confirmed` ·
`d3-graph-reference-ui-bug` · `d4-mask-diff-0-vs-nodata` · `d5-graph-pop-res-fail` · `d6-s03b-report-top` ·
`d7-s03b-card-no-reference` · `d8-kd003-proposed` · `d9-kd003-TEST-accepted` · `d10-pop-res-card-TEST` ·
`t1-target-ready-TEST` · `t2-target-claim-TEST`. Files ending in `-TEST` show a test signature by `human:rehearsal-test`
in a scratch copy, never a real one.
