# Axis Migrate — plan

Last updated: 13 Oct 2026 (evening, after the design swarm). Demo: **Mon 19 Oct, 18:15–18:40** (AAI4EO, Werner-Körte); second slot Tue 20 Oct 15:45.
Design: `docs/DESIGN.md` (integrated, binding) and `docs/design/01–10` (evidence, full schemas, acceptance tests per section).

## What we are building

A Claude Code plugin (Codex manifest later, first thing to drop) that takes a messy legacy GIS project folder and:

1. **Discovers** it — scripts, QGIS/ArcGIS projects, notebooks, Excel, data, previous outputs and **every intermediate** — draws the inputs on a map, and builds the workflow graph from code checked against the real files.
2. **Investigates, then interviews** — resolves every output-changing parameter from the files with a citation; asks only what the files cannot answer and parity cannot catch, one question at a time ("I don't know" becomes a visible assumption).
3. **Migrates** it step by step to an open-source Python pipeline (geopandas / rasterio), plan first, each step verified before the next.
4. **Runs** it locally or in an E2B sandbox, with real libraries, a read ledger and an exit-code contract.
5. **Verifies** parity **per step** against the legacy intermediates and finals with deterministic checks, and makes the agent look at legacy | new | difference plots; failures stay visible.
6. **Reports** in HTML (graph coloured by parity, swipe map, plots, parity table, assumptions) from a hash-sealed evidence bundle, and writes Databricks job files that are dry-run but not deployed.

This follows the accepted abstract (`DemoProposal-AAI4EO-AxisSpatial-Migration-Engine-Human-Rewrite.docx`).

## Why a plugin

- One agent beats a planner/worker team (GISclaw 96% vs 32%; our own 96-run test agrees). Claude Code or Codex *is* that agent, so the old 5-agent LangGraph pipeline, web app and model gateway are not needed.
- Axis owns three things: **GIS knowledge** (skills), **deterministic checks** (scripts), **honest status** (hooks + `ladder.py`).
- The old engine lost data at every handoff (06 headline); the plugin makes every handoff a file under `migration/`.
- Install is one command in a tool people already use.

## Plugin layout

```
axis-migrate/
  .claude-plugin/plugin.json      .codex-plugin/plugin.json (only if time; Codex plugins cannot ship hooks)
  commands/migrate.md             /migrate <folder>
  hooks/hooks.json + hooks/bin/   SessionStart · UserPromptSubmit(graph edits) · PreToolUse(Write/Edit, Bash) · PostToolUse(codescan) · Stop(claim gate)
  skills/
    discover/  scan.py lineage.py graph.py graph_server.py graph.html(+vendored React Flow bundle) map.py(inputs)
    interview/ params_check.py fit.py reference/parameter-evidence.md
    migrate/   SKILL.md router + reference/F1…F11 (≈70 evidence-backed entries) + runtime/_axis_runtime.py
    run/       run_local.py run_e2b.py runlib.py axis_boot.py env/uv.lock
    verify/    compare.py plot.py tolcal.py rubric.v1.yaml tests/(P1–P14, L1–L18)
    report/    seal.py ladder.py report.py map.py(display) phrases.v1.yaml assets/vendor(MapLibre)
    target/    make_databricks.py target_check.py dryrun.py runtimes/ schema/ templates/
    guard/     codescan.py protected.py claims.py
```

Workspace: `<project>/` = read-only legacy folder + `migration/` (`inventory.json`, `graph.json`, `params.yaml`, `decisions.md`, `known-differences.md`, `tolerances.yaml`, `pipeline/` package `axis_pipeline`, `runs/<run_id>/{snapshot,outputs,logs,verify}`, `report/`, `databricks/`). Ownership per file: DESIGN §2.

Status ladder, computed only by `ladder.py` from hashed evidence: `generated → sandbox-validated → reference-verified → target-ready`. The demo stops at `target-ready` (Databricks bundle written and dry-run, **not deployed**); higher rungs are drawn greyed "not attempted".

## What the user sees

`/migrate ./lux-exposure-legacy` in Claude Code:

1. **Scan** — inventory (30 files, 4 sidecars), detected tools, grid families, 9 intermediates + 4 finals as references, superseded 03b, the manual canton edit detected as a derived copy of ADM1. The **input map** opens; the agent records findings with numbers (0.94 km² of cantons outside WorldPop; 4 residential polygons outside cantons).
2. **Graph** — `graph.html`: steps, datasets, evidence per edge, red badges for open questions. The user can rename, confirm, add a step, mark scope, answer a question, and press **Send to agent**.
3. **Questions** — three on lux: canton-name policy (derive in code vs read the edited file), one "confirm all" (assumptions, scope, parity scope, tolerance records), target platform. Answers go to `decisions.md` and `params.yaml`.
4. **Plan** — per step: legacy ops with file:line, chosen open-source op, parameters, planned checks and tolerances, risks from skill entries; one approval before any code.
5. **Code, step by step** — each step written, scanned, run in isolation on the legacy copies of its inputs, verified; then one chained run from raw inputs.
6. **Verify, every step, numbers and maps** — pairing from lineage only; structure (CRS, grid to 1e-6 px, nodata, dtype, schema) before values; conservation per canton; legacy | new | difference plots with native-resolution worst tiles; the agent answers a calibrated rubric per plot and can only flag; the first failing step is blamed and repaired with "spec + current error" only.
7. **Report** — failures first, unfoldable; graph coloured by verdict; offline swipe map; parity table with reason codes; assumptions, decisions, known differences, run history, reproducibility hashes.
8. **Target** — `databricks/databricks.yml` (Asset Bundle, one wheel task per step), wheel dry-run under a Databricks-like layout with seek tracing and parity, and the fixed claim "target-ready (Databricks), not deployed" with its "Not done" list.

## Workflow graph editor (the old "Refine" step)

- `migration/graph.json` is the hub: steps (with legacy ops), datasets (legacy path, hashes, profile, compare contract), edges with evidence states, questions, status projections. Written only through `graph.py apply` (lock, revision, per-field ACL, edit log, snapshots); user-set fields are locked against the agent.
- `graph.html` + vendored `@xyflow/react` 12 + dagre bundle, served by `graph_server.py` (127.0.0.1, token). "Send to agent" = blocking `graph_server.py wait` (prints diff + obligations); a UserPromptSubmit hook announces unacked edits; the Stop hook blocks while edits are unacked. Deleting a step with legacy outputs only flags it for confirmation.
- After verify: fill = verdict, hatching = inherited blame, badge = open questions, "!" = visual flag.

## Interview

- **Investigate first** (03): every parameter `resolved` (citation), `inferred` (method, confidence) or `unresolved` (→ question). Never ask what the files answer (the old engine asked for a buffer distance that was in the code).
- Priority = impact × uncertainty × cost, cost highest where parity is blind (manual-step policy, legacy-bug keep/fix, branches without reference, non-unique fits). Budget 8 (demo 5). AskUserQuestion shape; Codex fallback is a numbered question in chat.
- Thresholds identified from legacy outputs (`fit.py`): candidates declared and hashed first, isolated, unique margin, hold-out frozen in advance; labelled "fitted"; target-ready needs a person's confirmation.
- Legacy bugs: measure the effect; zero effect → fixed and recorded; otherwise a question, recommended "keep for parity"; a fix becomes a bounded known difference.

## Old app → plugin

| # | Old page | What it did (06) | Plugin | Change |
|---|---|---|---|---|
| 0 | Migrate | browser regex parse; AI analysis discarded | `discover` | AST + signature tables, every file classified, input map |
| 1 | Refine | LLM DAG; edits returned 400; answers never reached generation | graph + `interview` | single-writer graph.json, answers wired via params.yaml and tested |
| 2 | Platform | choice overwritten with `e2b` | one last question | Databricks bundle; others `TARGET_NOT_SUPPORTED` |
| 3 | Generate | 5-agent pipeline; auditor failed open; stubbed dry run | `migrate` | one agent, plan first, step by step, real execution |
| 4 | Deploy | "complete" on job creation | `target` | dry run + honest claim; never deployed |
| 5 | Verify | inputs vs typed paths; tolerances ignored; no maps | `verify` + `report` | per step, lineage pairing, maps, sealed evidence |

## How we know it works

1. **Per run (inside the product).** Verdicts only from `compare.py`; rungs only from `ladder.py`; the Stop hook blocks claims above the rung. Generated code may not read legacy outputs (scanner S11 + strace read ledger + E2B upload set). No fake libraries (stub canary). Non-zero exit on any failure.
2. **Evals** (`docs/design/10-evals.md`): T0 comparator calibration (clean / legitimate alternative / mutant per trap, no model); T1 headless agent runs ×3 (seeded continuations + fresh), scored from artefacts plus a frozen re-check; cases: lux + S1 buffers, S2 spatial join, S3 Excel + legacy bugs (held out), S4 raster aggregation; traps T1a/T1b shift, T2 bilinear counts, T3 simplify-before-buffer, T4 unported branch (+ T4d decoy), T5 Web Mercator buffer (+ T5L legacy bug), T6 ×1000, T7 dropped join rows, T8 output-changing performance shortcut; negative controls NC1–NC7. **False-success rate must be 0 on every trial**; T0 false alarms 0.
3. **Human check.** One full run read end to end before the demo; recorded in the scorecard.

## How we improve it

Run evals → classify the first decisive failure → fix the right layer:

| Cause | Fix in |
|---|---|
| Model didn't know a GIS gotcha | one skill reference entry (evidence + eval ids) |
| Model knew but didn't act | hook or script check |
| Check missed a defect | `compare.py` new check + regression (never a looser tolerance) |
| Check flagged a correct alternative | check design, or a new tolerance version with both numbers re-measured in T0 |
| Plot did not show it | `plot.py` |
| Agent did not flag a visible defect | rubric / verify skill |
| Workflow misunderstood | discover / interview |

Rules: entries that move no eval in two iterations are deleted; tolerances never change because an agent run failed; a fix needs the failing case + core tier ×3 with 0 false successes; S3 is never tuned on. Order: correctness, then zero false successes, then time and cost.

## Design rules (from the old work and the research)

- The model writes code; scripts check it. A model never upgrades a failed check; it may only flag.
- Look at the data before writing code. On failure, feed back the spec plus the current error only (≤ 3 attempts per step).
- Parity: structure first (CRS, grid to 1e-6 px at origin and far corner, nodata, dtype, schema, counts), then values, then conservation. Default exact; a tolerance is a record with two measured numbers (correct spread < threshold < nearest wrong recipe), agreed by a person before the run. Band means and totals alone miss a one-pixel shift.
- Pair legacy and new outputs by lineage dataset id only. Every legacy intermediate is compared or explicitly waived.
- Skills are short gotchas tied to evidence and an eval. Fixture answers never enter skills. No bulk documentation.
- Don't claim the architecture is novel. Claim migration of existing work, proven parity against legacy outputs, and captured analyst knowledge.
- Avoid the old mistakes: frontend first, claims ahead of proof, too many MCP tools, generic frameworks, over-scoping, state in many stores.

## Knowledge (ArcPy → open source)

- **Demo:** 10 core reference files + 1 target-only file (DESIGN §6); the 32 wrong old rules (plus two found) are flagged and excluded.
- **After the demo:** private knowledge MCP on a Cloudflare Worker (R2 + D1 + AI Search, Vectorize fallback), five tools `list_books / get_toc / get_section / search_within / cite`, tenant from the token, tiers public / private library / per customer. The plugin stays open and works without it.
- NDA rule: lessons from the client migration are rewritten in general terms with opaque evidence ids; client methods, constants, data and names never enter the plugin.

## Demo project: `lux-exposure-legacy` (built, frozen Wed)

Generic residential exposure distribution for Luxembourg: canton value = population × value per capita (synthetic xlsx, kEUR ×1000), spread over residential WorldPop cells pro rata population.

- Built with real QGIS 4.2.3 / GDAL 3.13.3; per-step intermediates in `05_TEMP`, finals in `04_FINAL`; deterministic apart from GPKG/XLSX timestamps; conservation passes at 1e-6.
- Planted, realistic traps (PROVENANCE, hidden from the agent): grid kept only by clip options (`KEEP_RESOLUTION` → half-cell shift with unchanged totals), all-touched rasterise, NoData-0 mask default, cell-centre zones, messy xlsx header + "Wiltz " + ×1000, hand-edited join key, Float32/Float64 outputs. No buffer, resampling or integer scaling — those traps live in small cases S1/S4.
- ArcPy 03b is an unrun alternative of step 03 (NOTES: "don't run"); default scope `drop` with its knowledge kept.

## Where things run

| What | Where |
|---|---|
| Development | `migrate` user on ts-32gb-1 (non-root, bypass permissions); app SSH connection `ts-32gb-1-migrate`; long jobs in tmux |
| Claude login on the server | `CLAUDE_CODE_OAUTH_TOKEN` (1-year seat token) in `~/.config/axis-migrate/secrets.env` (never printed) |
| QGIS / reference env | micromamba env `qgis` (QGIS 4.2.3, GDAL 3.13.3, PROJ 9.9.0); also the `refenv` control runner |
| Pipeline env | `uv` lock, Python 3.12 (wheels bundle GDAL 3.12.x; minor drift recorded) — **`uv` not yet installed** |
| Sandbox | E2B, new template `axis-geo-migrate-<lock8>` from the lock (no existing `axis-geo-*` template qualifies); no network (`allow_internet_access=False` explicit), 600 s, no credentials inside; uploads only declared inputs with consent |
| Default run | Local (`uv run`, strace read ledger), so files stay on the user's machine; E2B is opt-in |
| Public demo data | Cloudflare R2 public bucket, created once the demo project is frozen (approved; contents shown before going public) |
| Code | Private repo `najahpokkiri/axis-migrate` (approved); this dev repo stays local, no pushes |


## Build status (live log, newest first)

| When | Status |
|---|---|
| 14 Oct, ~05:30 | Batch 2 (12 trials, commit 0afd2f3): 12 accepted, false success 0; exposed dead ends fixed since: malformed hand-written decisions YAML (→ `interview/record.py` + `supersedes`), ADM1 role `unused` after porting the manual step (→ derive `input`), claim-phrase false positives in the Stop gate (requirement / sub-task / future phrasing; modal+assertion still a claim). Fresh lux (F-L) honest rung: **sandbox-validated**, all checks PASS or NEEDS_DECISION awaiting Najah's signature (area_m2 float noise ×2; per-canton conservation invariant, legacy itself only within float32). Batch 3 (24 trials, commit a2b72a7: NC2, NC2b, F-L, S1-T5, PC-S1, F-S1, S1-T5L, NC3) running. Plugin tests: 681+ pass. |
| 14 Oct, ~01:45 | **Core eval tier batch 1** (plugin aa21936, Opus 5.5, fenced, ×3): **30 scored trials, 30 accepted, false success 0**. Seeded traps all caught and repaired, then honest rungs: L-T1b 3/3 target-ready (numbers 2/3, plot flag 1/3), L-T6 3/3 reference-verified (numbers 3/3), L-T7 3/3 (fixed from code reading 3/3), L-T8 3/3 target-ready, NC4 3/3 (tampering control; no accepted tolerance edit), NC5 3/3 (legacy-copy code refused by the runner, replaced), PC-L 3/3 reference-verified/target-ready, NC1 3/3 stopped honestly on the missing input (exit 12, no fabrication). Not scored: S1-T5 ×3 (harness: SDK 1 MB buffer → fixed), F-L ×3 (eval seat session limit, resets 03:50 UTC), NC2 (oracle rule too broad → fixed; agent correctly refused to read repeated text as approval). Script halves added for Codex parity: graph tamper detection (GRAPH_EDITED_OUTSIDE_APPLY), runner refuses unapproved plans. Codex manifest + local marketplace (install/list validated; no Codex login on this host). |
| 14 Oct, ~01:00 | **First full headless e2e on fresh lux (F-L t1, fenced as axis-eval, Opus 5.5): honest rung `sandbox-validated`** (12 PASS_EXACT, 2 FAIL = `area_m2` float noise ≤8.6e-13 awaiting a signed tolerance); frozen truth re-check passes all 13; false success 0; 5 questions (manual step, confirm_all, target, plan approval, + one caused by a guard bug); 20 min, 172 turns, ~$9.4 client estimate. Fixes from it: graph drift ignores derive projections (axis_common.graph_contract_sha256 in ladder + stop gate), tolerance append guard, unsigned proposals moved to `tolerances.proposed.yaml`, KD template, harness tarball fetch, scorer tampering rules. S1 eval case built with real QGIS; T0: 0 checker misses, 1 false-alarm class (per-feature symdiff on slivers) → compare fix in progress. Seeded lux cases built from the verified trial (PC-L, L-T1b, L-T6, L-T7, L-T8, NC4, NC5). |
| 14 Oct, early | Done and tested (per suite): discover 98 (+39 lux devtests; lux graph as designed, 1 question; s03b has 14 I/O ops, not 10), graph 54, interview 17, verify/compare 103 (+36 lux devtests; all 13 lux datasets PASS_EXACT vs copy; mutants fail with the named codes), run 53 (+6 lux devtests), report 71, hooks/guards 191 (HM1–HM7 all hold headless), migrate skill + F1–F11 (77 entries, opaque ev ids) 21. Golden lux pipeline: all pairs exact except `area_m2` (≤8.6e-13 rel) → needs a signed tolerance. Lux chained run 9.5 s local (strace seccomp), 64.6 s on **E2B** (template `axis-geo-migrate-1eb65b36`, id jybwir2outykhh2enymu); E2B outputs byte-identical to local (AT-12). Running: plot/verify skill, target (Databricks), S1 eval case; fixes: compare (14th pair for the ported manual step), codescan (flow-style YAML). |
| 14 Oct, ~00:30 | Done: graph store + editor (54 tests, Chrome-checked), seal/ladder/report (71 tests, reference-verified on a real sealed run, offline swipe map), interview (params_check, fit, SKILL; 17 tests), eval harness (fence probe holds 18/18 as `axis-eval`; SDK driver; run.py; score.py; cases F-L, NC1–NC3). AT-7 decided: `claude -p` has no AskUserQuestion; Agent SDK `can_use_tool` answers it (oracle), and in bypassPermissions only AskUserQuestion and hook `ask`s reach the callback. Lux integration check: scan 12 s → lineage → graph rev 1 with m01, s01–s04, s03b(drop), 13 legacy pairs, 1 question (manual step). Running: discover (map/tests), compare (P/L tests), runner (+ golden lux done), guards/hooks, plot.py + rubric, migrate skill + F1–F11. Next: target, E2B, seeded cases + S1, headless e2e. |
| 13 Oct, night | Build started (orchestrator session). Founder answered **yes to all 12 questions in DESIGN §12**; day-by-day schedule dropped: build by dependency, as fast as possible. `uv` 0.13.0 installed; `skills/run/env/uv.lock` resolves to the reference versions (Py 3.12.15, numpy 2.5.4, rasterio 1.5.2/GDAL 3.12.2, pyogrio 0.13.0/GDAL 3.12.4, shapely 2.2.0, pyproj 3.8.0/PROJ 9.8.1, geopandas 1.2.0). Cross-module contracts in `docs/CONTRACTS.md`. Unix user `axis-eval` available for fenced evals. |

## Schedule (superseded 13 Oct night: build by dependency, not by day)

Each day lists work, then the done-criteria that must hold before the next day starts. Cut order if late (first cut first): **Codex manifest** → S3 holdout → S4/S2 small cases → `.aprx` reader polish → isolated-mode live demo (show recorded) → E2B live (show recorded manifest) → T0b plot-rubric calibration beyond lux → ablation. Never cut: `compare.py` + its tests, the Stop gate, the read ban, T0 for the lux traps, NC1/NC2/NC4/NC5, the false-success gate.

| Day | Work | Done when |
|---|---|---|
| Tue 13 | Server setup ✓, secrets ✓, data ✓, skills swarm ✓, demo fixture ✓ (QGIS, 05_TEMP intermediates), design swarm ✓ (`docs/DESIGN.md`, `docs/design/01–10`) | ✓ |
| Wed 14 | Founder answers to DESIGN §12 Q1–Q5. Install `uv`; `env/uv.lock` (Py 3.12). Freeze the fixture (tag). Plugin skeleton + `commands/migrate.md`. **`compare.py` test-first**: P1–P14 probes, L1–L12 lux cases, contract hash, reason codes, exit codes. `scan.py` + `lineage.py` + `graph.py apply` on lux. Tolerance records for lux signed. Curate the 11 skill reference files from 04 §5. | `pytest skills/verify/tests` green incl. all 14 probes and L1–L12; `scan.py` passes 01 AT-1…AT-8 on lux; `lineage.py` gives 02 Appendix A (6 steps, 13 pairs, exactly the expected questions); `tolerances.yaml` for lux committed with both numbers per record; skill files ≤ 120 lines each with evidence + eval ids |
| Thu 15 | Morning: hook mechanics HM1–HM7 tests; AskUserQuestion `-p` probe (eval route). `_axis_runtime.py` + `axis_boot.py` + `run_local.py` (strace ledger, stub canary, snapshot, seal). `plot.py` (+ worst tiles) and `map.py`. Interview skill + `params_check.py`. Migrate skill. Hooks H1–H7 + `codescan.py` S01–S18. `graph.html` + `graph_server.py wait`. **First end-to-end run on lux** with per-step verify. Golden lux pipeline + T0 matrix for T1a/T1b/T6/T7/T8; fence probe. | One lux run from `/migrate` to a sealed run with `verify/verdict.json`, plots and visual receipts for all 13 pairs; 05 AT-1…AT-6 and 08 G1–G10 pass; T0 lux traps: mutants FAIL with the named codes, alternatives PASS; one F-L trial scored by `score.py` with every metric populated; golden pipeline read by a person |
| Fri 16 | `seal.py`, `ladder.py`, `report.py` (failures first, swipe map offline). `run_e2b.py` + template build (if approved). S1 (+ S4 if time) built with real QGIS. Core eval tier ×3 (33 trials); fix by the improvement table. Measure local and E2B wall time. | Core tier: false success 0 on every trial; T0 100 % / 0 false alarms; report passes 07 tests 1–9 and 14 (offline); E2B lux run byte/content-identical to local (05 AT-12) or the E2B path is cut with the reason recorded |
| Sat 17 | `make_databricks.py`, `target_check.py` T1–T13 with dry run; `target-ready` on lux. Full tier ×3 + holdout S3 once; freeze the scorecard. R2 bucket (public data + report folder after a secret scan). Codex manifest only if everything above is green. | lux reaches `target-ready (Databricks), not deployed` with the 09 §4.3 claim; full-tier scorecard frozen with N and pass^3; human read of one full run recorded; demo numbers taken only from this scorecard |
| Sun 18 | Rehearsal ×2 on a clean workspace (timed, ≤ 20 min of the 25), backup screen recording, recorded fallbacks (E2B run, failing first run, guard firing). | Two clean rehearsals end at target-ready with no manual fixes; recording saved locally; slide with the false-success and trap-catch numbers from Saturday's scorecard |
| Mon 19 | Spare morning; no new features. Demo 18:15. | Demo given; anything shown matches the frozen scorecard and the report |

## Decisions

| Date | Decision |
|---|---|
| 13 Oct | Budget change (Najah, via the parent session): **E2B unlimited** — the ~$10/day E2B cap is dropped; use E2B freely for runs and evals. The 30 headless agent runs/day cap still stands |
| 13 Oct | Budget change (Najah, via the parent session): **Claude seat unlimited** — the 30 headless runs/day cap is dropped; run as many headless runs and evals as needed |
| 13 Oct | §12 answers (all yes): `uv` lock; E2B template `axis-geo-migrate-<lock8>` (demo may run local, E2B must work); demo shows a failing first run + a guard firing + live graph edit automating the canton rename; ArcPy 03b migrated as a translation showcase against the QGIS step, 0-vs-NoData recorded as a known difference; Najah signs tolerances/KDs (collected in `migration/SIGN-OFF.md`, unsigned until he signs); reconciled fitted-parameter rule; visual review mandatory; evals fenced as `axis-eval` (≤30 headless runs/day, E2B ≤ ~$10/day); interview budget 8/demo 5, one confirm_all click suffices; Databricks runtime + access mode per 09; PyQGIS gap left (verify catches lux traps); Codex script halves only, last; ship `qgis_algs.json` |
| 13 Oct | Lux-specific tests live in `devtests/` and the golden pipeline in `evals/golden/`, never inside the plugin dir (the agent under test can read the plugin; D4-14) |
| 13 Oct | Plugin instead of DSH; Claude Code first, Codex manifest Saturday (first thing to drop) |
| 13 Oct | E2B for sandboxes, not Cloudflare |
| 13 Oct | Demo follows the human-rewrite abstract |
| 13 Oct | Prototype Fund out of scope |
| 13 Oct | Swarm may read the client migration; only generalised lessons enter skills |
| 13 Oct | Knowledge as skill files for the demo; knowledge MCP after |
| 13 Oct | Public data downloads and R2 bucket approved |
| 13 Oct | Design adopted as `docs/DESIGN.md`; section files 01–10 hold evidence; DESIGN wins on conflicts (§11, 24 reconciliations) |
| 13 Oct | One verdict vocabulary (`PASS_EXACT, PASS_WITHIN_TOLERANCE, FAIL, NEEDS_DECISION, NEEDS_REVIEW, NOT_COMPARED, BLOCKED, EVALUATOR_ERROR`); no WARN/PARTIAL; TARGETED_ACCEPT dropped as a status |
| 13 Oct | Pairing by lineage dataset id only; every legacy intermediate compared or waived |
| 13 Oct | Tolerances default exact; non-zero only as a record with two measured numbers, agreed by a person before the run |
| 13 Oct | Rungs computed only by `ladder.py` from a sealed, hash-checked bundle; only chained runs from raw inputs reach reference-verified |
| 13 Oct | Model visual review may only flag (→ NEEDS_REVIEW); receipts required for every compared raster/vector |
| 13 Oct | Step contract `run(ctx)` in package `axis_pipeline` with a vendored, tested runtime; no defaults in code |
| 13 Oct | Target = Databricks Asset Bundle (`databricks.yml`), one wheel task per step, dry-run with seek tracing; never deployed |
| 13 Oct | Code scanner is AST-only, 18 rules measured on the archive corpus; guards fail closed; scripts are the authority |
| 13 Oct | Fixture answers never enter skills; lux traps are caught by verify, not recall |

## Open items

- Founder answers to DESIGN §12 (top five block Wed/Thu: `uv`, E2B template, demo story, tolerance signer, fitted-parameter rule).
- Rotate leaked keys and passwords (user).
- Old E2B key appears in session logs and `/root/archives/…env…txt` on 32gb-1; revoke it.
- Check Claude login runs inside the E2B "try it" sandbox the Arena way (E2B Secret), if we show it — no readable code found for this pattern.
- GBrain needs authentication; no GBrain slug was re-verified during the design (07/08 relied on earlier extraction).
- Separate unix user for eval runs (hard read fence), and a daily eval spend ceiling.
