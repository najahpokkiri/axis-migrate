# axis-migrate — integrated design

Status: design, 13 Oct 2026 (orchestrator synthesis of `docs/design/01–10`). Demo: Mon 19 Oct 2026, 18:15 (AAI4EO).
Where this file and a section file disagree, **this file wins**; §11 lists every reconciliation. Section files hold the
evidence, schemas in full and acceptance tests; this file holds the decisions, the shared contracts and the gaps closed.
Evidence ids: `EP/MP/IN/AK/FH/HL-nn` = skill-candidates 01–07; `06/08/09/10 §x` = candidate files 06, 08, 09, 10;
`Dn-m` = decision m in section n; `PROV` = `fixtures/lux-exposure-legacy.PROVENANCE.md` (hidden from the agent).

## 0 Executive summary

axis-migrate is a Claude Code plugin: one agent, skills for GIS knowledge, scripts for every check, hooks for honest
status. It turns a messy legacy desktop GIS project into a reproducible Python codebase on the target cloud (open-source GIS
libraries inside) and proves parity **per step**, against
the legacy run's own intermediates and finals, with deterministic checks and maps the agent must look at.

The earlier Migration Engine failed in specific, recorded ways. Each design choice below answers one of them:

| Old failure (evidence) | Design answer |
|---|---|
| Data lost at every handoff; state in five stores (06 headline) | Every handoff is a file under `migration/`; `graph.json` is the hub, written only through `graph.py apply` (D2-9) |
| Answers never reached the generator (`generation_input.py:54-80`, Bug #94) | Pipeline reads `params.yaml` by id; `params_check.py` fails unwired params; eval NC2b "different answer → different output" (D3-5) |
| Interview "clarity" = 0.80 from four deployment answers (`smart_interview_v2.py:631-665`) | Investigate first; ask only what files cannot answer and parity cannot catch; platform is one last question (D3-1, D3-6) |
| Four comparators, 14 probes passed wrong outputs (09 §3.6); positional pairing (09 §2.2) | `compare.py` rewritten test-first: pairing by lineage dataset id only, structure gates values, default exact, tolerance = record with two measured numbers (D6-2…D6-6) |
| No intermediate ever verified; humans found every real defect in intermediates (09 §0, HL-29) | Every persisted legacy intermediate is compared or explicitly waived; first-failing-step blame from chained + isolated runs (D2-6, D6-12) |
| "Validated" meant stubbed imports (`sandbox_manager.py:1114-1278`, FH-03) | Real libraries from one `uv.lock`, stub canary, strace read ledger, exit-code contract, no `partial` (D5-6…D5-9) |
| Reports hid failures, "All Checks Passed" on zero comparisons (`VerifySummary.tsx:19-22`) | `ladder.py` computes the rung from hashed evidence; failures first, unfoldable; empty ≠ pass (D7-1, D7-9, D7-10) |
| Deploy reported "complete" on job creation (`platforms.py:315`) | Demo stops at `target-ready`: bundle written, wheel dry-run under a Databricks-like layout, claim text fixed, never deployed (D9-1…D9-9) |

What a model may do: write code, read files, propose parameters, ask questions, review plots against a calibrated
rubric and **flag** (downgrade to NEEDS_REVIEW). What a model may never do: produce or upgrade a verdict, set or widen a
tolerance, own a known difference, write `graph.json` directly, or claim a rung that `ladder.py` does not compute.

Demo path on `lux-exposure-legacy`: scan + input map → graph (6 steps, 13 legacy pairs) → 3 questions → plan approval →
step-by-step code with isolated verify → chained run → per-step parity with plots → report → Databricks bundle →
`target-ready (not deployed)`.

## 1 Principles (binding on every section)

1. **Scripts decide, hooks warn early, the model writes and looks.** Every guard has a script half that refuses to
   proceed, because Bash bypasses Write hooks, hook timeouts fail open and Codex hooks run only after the person trusts them in `/hooks` (D8-1).
2. **Investigate before asking; ask before guessing; never fabricate.** A missing input stops the run (exit 12);
   a manual step is always a question about policy, never about what the files show (D3-1, D3-11, AK-33).
3. **One owner per fact** (HL-36): values in `params.yaml`; structure, questions and status projections in
   `graph.json`; reasoning in `decisions.md`; tolerances in `tolerances.yaml`; accepted differences in
   `known-differences.md`; verdicts in `runs/<id>/verify/compare/*.json`; rungs computed by `ladder.py` only.
4. **Fail closed.** No synthetic DAG, no default parameters in code, no WARN band, no "proceed anyway", no `partial`.
5. **Look at the data.** Input map before code; legacy | new | difference plots for every compared raster and vector
   before a rung above sandbox-validated (`VISUAL_REVIEW_MISSING` otherwise).
6. **No claims ahead of proof.** Claim wording comes from phrase tables; the Stop gate blocks any claim above the rung.
7. **NDA.** Client and HERE material enters only as generalised entries with opaque evidence ids. Fixture answers
   (PROV) never enter skills (D4-14), so evals measure generalisation.

## 2 Plugin layout and workspace

```
axis-migrate/                                  (plugin; Codex manifest is the first thing to drop)
  .claude-plugin/plugin.json   commands/migrate.md   hooks/hooks.json   hooks/bin/*.py (stdlib)
  skills/
    discover/   SKILL.md  scan.py  lineage.py  graph.py  graph_server.py  graph.html  graph.bundle.js  map.py(inputs)
                qgis_algs.json  arcpy_tools.json
    interview/  SKILL.md  params_check.py  fit.py  reference/parameter-evidence.md
    migrate/    SKILL.md (router ≤80 lines)  reference/F1…F11  runtime/_axis_runtime.py (vendored into the pipeline)
    run/        SKILL.md  run_local.py  run_e2b.py  runlib.py  axis_boot.py  env/{pyproject.toml,uv.lock}
    verify/     SKILL.md  compare.py  plot.py  tolcal.py  rubric.v1.yaml  tests/ (probes P1–P14, lux L1–L18)
    report/     SKILL.md  seal.py  ladder.py  report.py  map.py(display)  phrases.v1.yaml  assets/vendor/
    target/     SKILL.md  make_databricks.py  target_check.py  dryrun.py  runtimes/*.json  schema/  templates/
    guard/      codescan.py (rule engine for hook H6 and runner preflight)  protected.py  claims.py
```

Workspace (`<project>/` holds the read-only legacy folder and `migration/`):

| Path | Owner (writer) | Readers |
|---|---|---|
| `migration/inventory.json`, `discover/profiles/*.json`, `discover/*.png` | `scan.py` (+ `scan.py annotate` for agent findings) | all |
| `migration/graph.json`, `graph-edits.jsonl`, `.graph/rev/<n>.json`, `.graph/layout.json` | `graph.py apply` (actors: agent, user via server, verify ingest, derive) | all |
| `migration/params.yaml` | interview (values); migrate fills `used_by` | pipeline, verify, report, target |
| `migration/decisions.md` (append-only), `known-differences.md` (append-only) | interview / user | verify, ladder, report |
| `migration/tolerances.yaml`, `contract-history.jsonl` | verify proposes; human agrees | compare, ladder |
| `migration/analysis/` | agent (exploration, `fit.py` runs) | nothing in `pipeline/` may import it |
| `migration/pipeline/` (package `axis_pipeline`: `_axis_runtime.py`, `sNN_*.py`, `__main__`) , `tests/`, `README.md` | migrate (model writes only `sNN_*.py` and smoke tests) | run, target |
| `migration/runs/<run_id>/` (§4.5), `runs/index.jsonl` | runner, verify, seal | report, Stop gate |
| `migration/report/` (`index.html`, `assets/`, `REPORT.json`) | `report.py` | user, evals |
| `migration/databricks/` | `make_databricks.py`, `target_check.py` | user |

## 3 Canonical identifiers and vocabularies (one list; every section uses these)

**Ids** (generated deterministically by `scan.py`/`lineage.py`, D1-17; never `hash()`):
- Dataset `ds.<file-stem>`; stems shared by several files become `ds.<stem>_<ext>`; a layer suffix `__<layer>` only when
  a multi-layer file's layer differs from the stem. Lux: `ds.01_pop_lux`, `ds.02_res_mask`, `ds.lux_ppp_2020`,
  `ds.exposure_by_canton_gpkg|_csv|_xlsx`, `ds.lux_res_exposure_2020`. (Section 02's prettier ids such as `ds.pop_lux`
  and 05's `ds.worldpop_2020` are superseded.)
- Step `sNN[a]_<script-stem-rest>` (`s01_prepare_population`, `s03b_zonal_arcpy_old`); manual step `mNN_<output-stem>`
  (`m01_cantons_lux_edit`); ops `<step>.opK`; params `p.<slug>` with a `node` field (03 §3.1); questions `q<NN>`;
  decisions `D-nnn`; known differences `KD-nnn`; tolerance records `tol.<dataset>.<check>.v<k>`.
- Run `r<UTC yyyymmddThhmmssZ>-<code_hash[:8]>`.

**Step scope** `migrate | keep_legacy | drop` with `scope_reason` (`dead_code | alternative_impl | out_of_scope_by_owner |
display_only | no_oss_equivalent | …`). Step `kind`: `script | notebook | qgis_model | manual | bridge`. Bridges are steps
(`kind: bridge`, `direction: in|out`), not a scope value (04 §4). Lux: 03b = `drop/alternative_impl` with `equivalent_to`
edges kept as knowledge; ADM2 input and the industrial/commercial classes = `drop/display_only|out_of_scope_by_owner`.

**Evidence state** (per node, edge, fact): `directly_observed | structurally_inferred | ai_inferred | documented |
user_asserted | runtime_probed | verified | rejected | superseded` (runtime-core `EvidenceState` + `documented`, D2-3).
Disagreeing evidence sets `conflict: true` and creates a question.

**Parameter resolution** `resolved | inferred | unresolved`; `source` `code | notes | project | input_data |
reference_metadata | fitted | answer | assumption | agent_decision`; `status` `confirmed | assumed | open` (03 §3.1).

**Verdict states** (check, dataset, step, pipeline; precedence worst first): `FAIL > EVALUATOR_ERROR > BLOCKED >
NEEDS_DECISION > NEEDS_REVIEW > NOT_COMPARED > PASS_WITHIN_TOLERANCE > PASS_EXACT` (D6-1). No WARN, PARTIAL, INCONCLUSIVE,
UNTESTED or score. Section 10's INCONCLUSIVE → `NEEDS_REVIEW`; 02's UNTESTED → `NOT_COMPARED/UNPAIRED_LEGACY`.

**Reason codes**: the 06 §3.1 list is canonical, plus these additions, agreed here:
`BRIDGED` (NOT_COMPARED; a bridged dataset is never compared against itself), `OUTPUT_NOT_PRODUCED_BY_RUN` (FAIL; output
absent from the step's write ledger), `CANDIDATE_IDENTICAL_TO_REFERENCE` (diagnostic; see §7.2), `REFERENCE_VALUE_IN_CODE`
(post-demo), `PARAM_FITTED_TO_REFERENCE`, `UPSTREAM_NOT_VERIFIED`, `*_CHANGED_AFTER_RUN`, `EVIDENCE_FILE_MISSING`,
`EVIDENCE_HASH_MISMATCH`, `STATE_RECOMPUTE_MISMATCH`, `UNBOUND_COMPARE`, `LEGACY_REF_CHANGED` (07), run/guard codes
`INPUT_MISSING`, `UNDECLARED_READ`, `STUB_DETECTED`, `STEP_CALLED_EXIT`, `SCAN_BLOCKED`, `UNLEDGERED_RUN`,
`CLAIM_ABOVE_RUNG` (05, 08), and target codes `TC_*` (09 §4.1). Section 10's names map as:
`RASTER_EXTENSIVE_SUM_DRIFT → TOTAL_DRIFT / ZONE_TOTAL_DRIFT`; `GEOMETRY_AREA_RATIO → diagnostic on GEOMETRY_SYMDIFF_EXCEEDED`;
`BRANCH_OUTPUT_MISMATCH → KEY_SET_MISSING / GEOMETRY_SYMDIFF_EXCEEDED on the branch subset + BRANCH_NOT_EXERCISED`;
`REFERENCE_MISSING → NO_REFERENCE`; `LEGACY_OUTPUT_READ → UNDECLARED_READ` (run) or `SCAN_BLOCKED/S11` (static);
`ALIGNMENT_RESAMPLED` is impossible by construction (compare never warps, D6-3) and becomes a scanner-free unit test;
`NULL_JOIN_INCREASE, VERTEX_COUNT_DROP` are diagnostics.

**Step dispositions** (derived by `ladder.py` from verdicts + records, 07 §3.4): `VERIFIED | ACCEPTED_DIFFERENCE |
NEEDS_REVIEW | NEEDS_DECISION | FAILED | BLOCKED | EVALUATOR_ERROR | NO_REFERENCE | NOT_COMPARED | EVIDENCE_INCOMPLETE |
BRIDGED | NOT_PORTED`. ACCEPTED_DIFFERENCE is amber and never green; the parity row stays FAIL.

**Ladder rungs** `none → generated → sandbox-validated → reference-verified → target-ready`; `deployed`,
`live-target-verified`, `customer-accepted` are drawn greyed ("not attempted") and cannot be emitted. TARGETED_ACCEPT is
dropped from the vocabulary (D7-11); post-demo it may return only as an optional review field on the target pack (09 §4.4).

**Run status** `succeeded | failed | timed_out | cancelled | blocked` (July `ExecutionStatus`, D5-6).

**Exit codes.** Step process (`axis_boot`): `0` ok · `1` step exception (→ repair loop) · `2` input/parameter/contract
error (→ interview, not a code fix) · `3` output contract violation · `11` stub detected. Runner (`run_local/e2b`):
`0` succeeded · `10` a step failed (step exit 1/3) · `11` contract/ledger/stub/scan violation · `12` blocked: input missing
or changed · `13` blocked: environment · `14` timed out · `15` immutability · `70` harness error (05 §1). `compare.py`:
`0` only when every requested dataset is PASS_*; `report.py`: 0/2/3 (07 §4.2); `codescan.py`: 0 clean / 2 block / 3 rule
error.

## 4 The flow, stage by stage

### 4.1 Discover (section 01)

- `scan.py <legacy> --out migration` is deterministic, makes no model calls and reads nothing outside the legacy root;
  before/after hashes give `read_only_verified` (D1-1, D1-16). It sets `GDAL_PAM_ENABLED=NO`; QGIS-written `.aux.xml`
  sidecars are listed but their stats are untrusted (`STATISTICS_APPROXIMATE=YES` in the fixture, EP-26).
- Code: Python AST with flow-sensitive abstract values + `tokenize` comments; notebooks per cell in exec and cell order;
  `.qgz/.qgs` XML datasources and subsets; `.model3`; `.aprx/.atbx` CIM JSON best-effort (rewrite; the old TS parser
  reads neither `CIMPATH` nor `dataConnection`, 01 C1); Excel read twice (formulas and cached values), header never
  inferred silently. I/O roles from signature tables: `qgis_algs.json` generated by `qgis_process --json help`,
  `arcpy_tools.json` lifted from `data_flow_tracer.py:55-75` and extended (D1-3, D1-4). The old extractor missed 3 of 4
  lux inputs; the old tracer found 0 flows in the PyQGIS scripts (D1-3, D2-8) — both rewritten.
- `settings[]` unify ArcPy env (assignment, `EnvManager`, default-unset) and QGIS per-call grid parameters (D1-5);
  rasters grouped into grid families with pixel offsets (D1-7); derived-copy detection exposes manual steps
  (ADM1 → `cantons_lux_edit`: 12/12 equal geometry, 11 prefix drops + 1 exception, D1-10).
- Every file gets a role (input / intermediate / final / unused / unknown) from ranked evidence; mtime only corroborates
  (`mtime_reliable=false` on the fixture). `reference_candidate = exists ∧ role ∈ {intermediate, final}`.
- `map.py inputs` draws `inputs_overview.png`, `grid_corners.png` (native resolution, half-cell shifts visible),
  `legacy_rasters.png`; the agent records findings with a computed metric via `scan.py annotate` — a finding without a
  metric can only raise a question (D1-15). Lux must surface: 0.94 km² of canton area outside the WorldPop extent;
  4 residential polygons outside cantons; residential cells hold 46 % of population.

### 4.2 Lineage and the workflow graph (section 02)

- Bipartite graph of steps and persisted datasets; legacy tool calls kept as `ops[]` inside steps; memory layers never
  become datasets (D2-1). Edges stored with per-edge evidence; `produced_by/consumed_by` derived (D2-2).
- Static lineage is **checked against the real files**: writes exist, layer names exist, consumer fields exist in the
  producer schema, `reads_grid` consumers equal their template grid, mtime order (warning only, sidecars and inputs
  excluded). Every mismatch becomes a question linked to a node and a code line, never a silent drop (D2-7).
- Every in-scope persisted legacy intermediate is compared or waived by the user (D2-6). Pairing = join on dataset id; an
  orphan legacy file is paired only by an explicit user op `set_reference`; no stem/prefix/position fallback, not even as a
  suggestion (D2-5, D6-2).
- Single writer `graph.apply(ops, actor, base_revision)`: flock, revision check, per-field ACL, atomic replace, append
  to `graph-edits.jsonl`, snapshot per revision. User-set fields are locked against the agent; verify writes only
  projections; Write/Edit on `graph.json` is denied by hook (D2-9…D2-11).
- Editor: vendored React 18 + `@xyflow/react` 12 + dagre (LR) bundle, served by `graph_server.py` on 127.0.0.1 with a
  token and same-origin check. `graph_server.py wait` (file-based, timeout 540 s, exit 2 on timeout) is the blocking
  "Send to agent" channel and prints the diff plus the agent's obligation per edit (02 §4.5); a UserPromptSubmit hook only
  announces unacked edits; the Stop hook blocks while user edits are unacked (D2-13, D2-14). Deleting a step with legacy
  outputs only flags it for confirmation; nothing is ever removed.
- Colouring after verify: fill = verdict, hatching = inherited blame, border = review state, badge = open questions,
  "!" = visual flag; text glyphs for colour-blind safety (02 §4.7).

### 4.3 Investigation → params.yaml → interview → decisions.md (section 03)

- For every step, every output-changing parameter becomes a `p.*` entry with `resolution`, `source`, evidence
  (file:line + quote), `impact.datasets` (downstream closure from the graph) and `used_by` (filled by migrate).
  `params_check.py` fails a parameter with no reader or a reader using a literal (D3-5).
- Ask only (a) unresolved items and (b) inferred items scoring ≥ 12 on `impact × uncertainty × cost` (each 1–3), where
  cost is highest for what parity cannot detect (manual-step policy, keep-or-fix on legacy bugs, branches without
  reference, non-unique fits, target platform). One question per AskUserQuestion call, recommended option first,
  explicit "Don't know" (→ visible assumption), budget 8 (demo 5); a `confirm_all` question lists every assumption,
  inferred and fitted value and the proposed **parity scope** (which datasets and checks are contractual); target
  platform last (D3-6…D3-15). Codex fallback: one numbered question per turn in chat; the graph page is a second channel.
- Fitted parameters (`fit.py`): candidate set from evidence declared and hashed **before** fitting, isolated step on legacy
  inputs, unique winner with the nearest-wrong margin, hold-out split frozen in advance. **Reconciled rule (03 vs 07):**
  a fitted value with a passing hold-out may reach `reference-verified`, labelled "fitted, held-out pass", unless the
  parameter acts globally (grid offset, scale) where a split proves nothing — then confirmation is required;
  `target-ready` always requires the value to be confirmed by a person (an individual question or `confirm_all`). A
  fit without hold-out caps the step at sandbox-validated (`PARAM_FITTED_TO_REFERENCE`).
- Values copied from legacy output headers (`source: reference_metadata`, e.g. QGIS 4 writing NoData 0 when unset) are
  allowed; verify tags the matching structural check `by_construction` (D3-8).
- Legacy bugs: measure the effect on the reference data in isolation. Zero effect → the agent fixes it without asking
  and records `KD` with bound 0. Non-zero → a question, recommended **keep for parity**; "fix" creates a `KD` of kind
  `accepted_change` with a measured bound (D3-9, §4.6 KD rules).
- `decisions.md`: `## D-nnn · title · timestamp` + one YAML block (`question, kind, answer, by, params, nodes, changes,
  supersedes, evidence`); append-only, enforced by hook (D3-13). Section 03's `d004` style is renamed `D-004`.
- **Lux expected interview (reconciled 02 vs 03): exactly 3 questions.** q01 manual canton names: derive in code with an
  exception table (recommended) / read the edited file as input (bridge) / don't know. q02 `confirm_all`: assumptions
  (`p.mask_nodata` from metadata, `p.zone_rule` inferred, `p.min_area_m2` tuned in sample, "100 m" = WorldPop 3″ grid),
  scope (03b dropped as alternative implementation per NOTES:13; ADM2 display-only; industrial etc. out of scope),
  parity scope (all of `04_FINAL` + every `05_TEMP` file + per-canton conservation) and tolerance records. q03 target.
  Section 02's separate questions on 03b and ADM2 are folded into q02, because the files answer them (D3-1).

### 4.4 Plan and migrate (section 04)

- The plan is a `plan` block per step inside `graph.json` (legacy ops with file:line, chosen OSS op and why, params ids,
  outputs with planned checks and tolerance ids, risks citing skill entry ids, isolation feasibility); `plan.md` is
  rendered; one user approval before any `pipeline/` file exists (D4-1). Plan approval is a hard gate (hook denies
  writes to `pipeline/sNN_*.py` until `approved`), with a single-keystroke approval for demo pacing.
- Step boundary = a dataset the legacy persisted (D4-2). Generation is topological: write `sNN_*.py` → `codescan.py`
  gate → isolated run (legacy copies of the step's inputs, staged by the runner) → verify that step → next step. After the
  last step a **chained** run from raw inputs; only chained runs move rungs (D4-3, D4-4).
- **Step contract (reconciled 04/05/08/09):** the pipeline is an installable package `axis_pipeline`. The plugin vendors
  `_axis_runtime.py` (tested; owns `Ctx`, param access, path resolution, staging I/O helper for seek-less mounts, grid
  and nodata checks on write, atomic writes, I/O recording, logging). The model writes only
  `def run(ctx) -> None` in `sNN_*.py` with module constants `STEP_ID, INPUTS, OUTPUTS, PARAMS` equal to the graph.
  `ctx.param(id)` has no default argument and raises `ParamMissing` (exit 2); `ctx.input/ctx.output` resolve dataset ids
  from the runner's `ctx.json`; reading or writing an undeclared dataset raises. The same entry point serves
  `axis_boot` locally/E2B and the Databricks `python_wheel_task` (`axis-pipeline --step <id> --input-root … --work-root
  … --output-root … --scratch-dir …`).
- Repair: verify sends the step spec + the current error only; migrate may edit only the blamed step module; a parameter
  change is an interview question; ≤ 3 attempts per step; stop when the same failure signature survives two distinct
  patches (D4-9, D6-13).
- Bridges: explicit `kind: bridge` steps; bridged datasets are `NOT_COMPARED/BRIDGED` and never pass; finals downstream of a
  bridge are labelled "partial migration (n bridged)"; converting a failing step to a bridge needs a user decision
  (`ask`); a bridge input is hash-pinned; isolation staging is not a bridge (D4-10, 04 §4).
- Unported means it raises (`NotImplementedError`, scope recorded), never a `pass` body (D4-16).

### 4.5 Run (section 05)

- Local `uv` run is the default; E2B opt-in per run with explicit upload consent. One `uv.lock` for local, E2B template
  and the target lock's geo stack; no installs at run time (D5-1, D5-2, D9-5). **`uv` is not installed on ts-32gb-1 and
  the system Python has drifted (stale dist-info)**: install `uv` on Wed (PLAN).
- Every native library copy is recorded (rasterio's GDAL 3.12.2, pyogrio's 3.12.4, PROJ 9.8.1 vs the reference env's
  GDAL 3.13.3 / PROJ 9.9.0). Minor drift is allowed and shown; a major difference blocks (exit 13). The first control on
  any failure is a `refenv` run of the same snapshot in the QGIS env's Python: identical outputs exclude the environment
  as the cause (D5-3, D5-4). PROJ network forced OFF inside each step (the references were built with it ON; harmless for
  EPSG:4326↔2169, measured, D5-5).
- One process per step under `axis_boot`, which checks real modules (origin, compiled extension, lock version), runs a
  stub canary, catches `BaseException` incl. `SystemExit`, verifies declared outputs and writes `result.json` itself
  (D5-6…D5-8). Read ledger by `strace` (GDAL's C-level opens are invisible to Python audit hooks, measured); any open
  under the protected set → exit 11 `UNDECLARED_READ`; macOS fallback (audit + `CPL_DEBUG`) caps at sandbox-validated;
  E2B uploads only declared inputs (`allow_internet_access=False` set explicitly; SDK default is True) (D5-9, D5-14).
- Fresh immutable run directory; code, params, graph, tolerances, KD, decisions and lock copied into
  `runs/<id>/snapshot/` and hashed at start; steps import from the snapshot; `OGR_CURRENT_DATE` and
  `GDAL_PAM_ENABLED=NO` make outputs byte-stable (D5-10…D5-12).
- Modes: `chain: chained | isolated`, `executor: local | e2b | refenv`. Only `chained` + lock-matched + succeeded +
  no stub + no undeclared read can support `sandbox-validated`; isolated legacy feeds are copied read-only into
  `stage/legacy_ro/`, never `role: final` datasets (D5-13, D5-19). `full` (needed for reference-verified) = chained ∧ every
  in-scope step executed or reused from a hash-identical parent run (`--resume-from`).
- Lux estimate: ≈ 4 s local (≈ 8 s with strace), 20–60 s on E2B; measure Fri.

**Run directory** (reconciles 05 `snapshot/`, 07 `sealed/`, 06 `compare/`, 05 "verify never writes into the sealed run"):

```
runs/<run_id>/
  snapshot/  (code tree, params, graph, tolerances, KD, decisions, uv.lock)   manifest.json  checksums.sha256
  stage/{inputs,legacy_ro}/  outputs/<step>/  logs/<step>.{stdout,stderr,reads,result.json}  env/{probe.json,freeze.txt}
      ── sealed read-only by the runner at run end ──
  verify/  contract.json  compare/<ds>.json (+ .mismatches.parquet)  plots/<ds>/*.png + plots.json
           display/<ds>.{legacy,new,diff}.png + display.json  visual-review.json  verdict.json  scan.json  tests.json
      ── sealed by seal.py into evidence.json + EVIDENCE_ID; appended to runs/index.jsonl ──
  target/  (dry-run evidence, sealed with the target pack)
```
One verify per run: a changed contract (tolerances, KD, pairing projection) requires a new run (D6-11).

### 4.6 Verify — the core (section 06)

**Contract.** `verify/contract.json` = frozen {tolerance records, accepted KD rules, pairing projection of the graph,
reference hashes, comparator id + sha}; `contract_hash` recomputed at compare time; mismatch →
`EVALUATOR_ERROR/TOLERANCE_CHANGED_AFTER_RUN` (D6-11). References are the legacy run's own files, hash-pinned at scan
(content hash for GPKG/XLSX, whose bytes change per write); a modified reference → `EVALUATOR_ERROR/REFERENCE_HASH_MISMATCH`,
never FAIL (D6-10, D6-17).

**Pairing** (06 §4a): for every non-input dataset in scope: legacy without new → `NOT_COMPARED/UNPAIRED_LEGACY` (blocks);
new without legacy → `NO_REFERENCE` or `UNPAIRED_NEW` (listed); producer without evidence → `PAIRING_UNCONFIRMED`;
multi-layer without declared layer → `EVALUATOR_ERROR/LAYER_AMBIGUOUS`; output missing → `FAIL/OUTPUT_MISSING`
(`BLOCKED/UPSTREAM_BLOCKED` if upstream did not run); output written by another step → `FAIL/LINEAGE_MISMATCH`; output
not in the step's write ledger → `FAIL/OUTPUT_NOT_PRODUCED_BY_RUN`. A sweep lists legacy files under output directories
that lineage never mentions.

**compare.py per family** (06 §4b). Raster: band count, CRS (`pyproj.CRS.equals`, never `to_epsg()`), shape, transform
deviation in pixels at origin **and far corner** ≤ 1e-6 → only then dtype, nodata value, nodata-mask XOR, values on cells
valid on both sides (integer exact, float `|n−l| ≤ atol + rtol·|l|`, NaN never equal); conservation runs even when the grid
gate fails; diagnostics on FAIL (`DATA_OFFSET_SUSPECTED` integer shift search, `NUMERIC_SCALE_FACTOR`,
`SMOOTHING_SUSPECTED`). Nothing is reprojected, resampled or cropped to make a comparison possible. Vector: declared layer,
CRS equals, geometry-type family, declared key (or declared geometry matching), key sets both sides, schema with declared
renames, per-key geometry symdiff ratio and Hausdorff measured in `p.measure_crs` (never degrees or EPSG:3857),
null pattern then attributes. Table: one reader config for both sides (XLSX `data_only=True`, declared sheet/header/data/total
rows), schema, key, row conservation, null pattern, values, totals.

**Conservation and invariants** (D6-7): extensive datasets get total and per-zone checks (zones and zone rule declared in
the graph and equal to the legacy rule — lux: cell centre); graph `invariants[]` are evaluated on legacy first (legacy
violates → `LEGACY_DEFECT_SUSPECTED → NEEDS_DECISION`). Conservation is necessary, not sufficient: the half-cell `-tap`
shift keeps the population total (PROV), and a wrong residential mask keeps per-canton exposure totals by construction.

**Tolerances** (D6-6, 06 §3.2): default exact. A non-zero tolerance exists only as a versioned record with
`correct_spread` (independent correct implementation) < threshold < `nearest_wrong` (closest plausible wrong recipe), both
measured with evidence files, `agreed_by` a human, fixed before the first run that uses it; otherwise
`TOLERANCE_UNJUSTIFIED` and the check runs exact. `tolcal.py` produces the nearest-wrong numbers by deterministic
mutation of the legacy file (shift transform, bilinear round trip, toggle all_touched, ×10^k, strip/drop keys). Lux
proposal (measured): exact everywhere except exposure raster values and zone totals `rtol 1e-6` (legit float64
widening 5.9e-8 vs nearest wrong 1.8e-3) and float64 sums `rtol 1e-9` (summation order); 9 of 13 datasets reproduced
exactly by an independent rasterio/geopandas implementation, 4 to confirm Wed. Note: `make_valid` default mode is a
**wrong recipe** here (13 GeometryCollections, 3 extra mask cells); `method="structure"` matches QGIS fixgeometries.

**Known differences (reconciled 03/06/07).** One schema: each `## KD-nnn` block in `known-differences.md` ends with a YAML
block `{id, kind: normalisation | exclusion | accepted_change | legacy_defect_preserved, datasets, checks, rule,
bound: {metric, max} | null, reason, evidence, owner: human:<name>, decided_at, decision_ref, status: proposed | accepted |
withdrawn}`. Normalisations (nodata equivalence, dtype widening, multi-equivalence, string strip, rename, volatile-bytes)
make checks pass as `PASS_WITHIN_TOLERANCE` with the KD listed. `accepted_change` never changes the compare state: the
check stays FAIL, the KD's `rule`/`bound` is evaluated as an extra check, and `ladder.py` turns the step disposition into
`ACCEPTED_DIFFERENCE` (amber) only if every failing reason code is covered by an accepted KD with a human owner whose
bound held (07 §3.4). A KD enters the contract only from the next run; one decided after a failing run is shown as
"decided after seeing the difference". Model owners are ignored. Writing a KD with `owner: human:` triggers a permission
`ask` (D8-10).

**Aggregation and blame** (06 §4d–e): dataset = worst check; step = worst of its datasets; pipeline = worst step of the
chained run, plus `UNPAIRED_LEGACY` notes and `VISUAL_REVIEW_MISSING` for any compared raster/vector without a visual
receipt. Blame per step from chained vs isolated verdicts: `own | inherited | accumulated (NEEDS_REVIEW) |
compensating (NEEDS_REVIEW)`; the repair loop takes the earliest `own` failure. `verdict.json` holds verdicts and blame
only — **no rung** (reconciled: rungs are `ladder.py`'s alone).

**Plots** (06 §4g): deterministic matplotlib; legacy | new | difference with one shared norm (p2/p98 of both sides) and a
symmetric diverging diff scale; max-|d| downsampling for diffs; `mask_diff.png`; histogram with tolerance lines and
per-zone bars; **worst tiles at native resolution with the cell grid drawn** (the only view where a one-cell edge offset
is visible); vector overlay with symmetric difference filled; table residuals. Plots are evidence about verdicts, never
inputs to them.

**The model's visual review** (06 §4h, 10 D10-7, reconciled): rubric `rubric.v1.yaml`, questions Q1–Q7 (offset stripes,
smoothing, angular buffers, missing region, tile seams, boundary ring, saturation under shared scale), each answered
`yes | no | cannot_tell` per plot; each finding is appended only through `plot.py review-add` with schema
`{finding_id, dataset_id, question_id, category, answer, effect: flag|none, plot, plot_sha256, panel, location, note ≤200}`
where `category` comes from 10's closed list (`whole_offset, boundary_stripes, missing_region, uniform_scale,
smoothed_blurred, angular_outline, shrunken_features, class_missing, holes, none`). Limits enforced in the aggregator:
a flag moves only a PASS_* dataset to `NEEDS_REVIEW`; nothing moves a state up; a flag is cleared by a human decision or
by a new run whose plot no longer triggers it; receipts must cite a plot hash of the same run; shifts and sub-cell
defects remain code's job (Q1 complements G4 and `DATA_OFFSET_SUSPECTED`). The rubric is calibrated on lux controls and
mutants before the demo (`calibration/rubric.v1.json`). Section 07's `visual/<ds>.json` per-dataset files are replaced by
the single `verify/visual-review.json`.

**Regression tests** (written first, Wed): the 14 probes of 09 §3.6 as P1–P14 with expected verdict + reason code (06 §6.1)
and the lux cases L1–L18 (identity, independent implementation, `make_valid` linework, half-cell shift, crop extent,
bilinear round trip, cell-centre mask, all-touched factor, unstripped names, ×1000 missing, area in 3857/degrees, 03b
semantics, float64 output, missing pair, tolerance edited, reference modified, isolation blame, visual receipts, repair
loop) (06 §6.2).

### 4.7 Evidence, ladder and report (section 07)

- `seal.py` (after compare, plots and visual review) writes `evidence.json` (`axis-migrate.evidence.v1`) listing every
  object with a content-hashed id `<kind>:<sha256>` (files by bytes or tree hash; plugin JSON by `axis-cjson/1`: sorted by
  code point, NaN refused, volatile keys excluded), `EVIDENCE_ID`, and a line in `runs/index.jsonl` (D7-4, D7-5).
  `compare` objects must reference this run's `out:`, the `ref:` and the `tol:` ids (binds parity to the executed
  artefact, which July did not).
- `ladder.py compute(bundle)` (stdlib only, < 5 s on lux; used by `report.py`, the Stop gate, `graph.py derive`, evals) is
  the only producer of rungs. Per step: generated (module in sealed code tree, no scanner block) → sandbox-validated
  (typed `ok`, outputs hashed, smoke tests pass, real libraries, inputs = inventory hashes, executor recorded) →
  reference-verified (every legacy-paired output has a bound compare with an independent reference, disposition VERIFIED
  or ACCEPTED_DIFFERENCE, `full` chained run, sealed = live contract) → target-ready (target pack bound to the same code
  and lock ids, `target_check.py` passing, fitted values confirmed, `deployment_status: not_deployed`). Pipeline = minimum
  over steps + every final reference-verified + integrity complete + no drift. Caps and annotations per 07 §3.4 (fitted
  parameters as reconciled in §4.3; assumptions annotate; bridges suffix "· partial (n bridged)"; live ≠ sealed caps at
  sandbox-validated; code drift withdraws target-ready; a changed legacy reference = EVIDENCE INCOMPLETE).
- `report.py` renders `report/index.html` from one sealed bundle only, re-verifying every hash and recomputing every
  compare state from its checks on each render; missing or mismatched evidence → headline **EVIDENCE INCOMPLETE**, no rung,
  exit 2; zero comparisons → **NOTHING VERIFIED** (D7-6, D7-10). Page order (first screen = 1280×720): integrity bar →
  headline with ladder, counts and an unfoldable failures list → workflow graph (static SVG from the sealed graph) → swipe
  map → failed steps in full → parity table → passing step cards → assumptions and decisions → known differences → not
  checked → run history (failing runs kept) → "Agent notes (model-written, not evidence)" → reproducibility (07 §4.1).
  Verdict wording only from `phrases.v1.yaml` keyed by reason code.
- Swipe map: MapLibre GL 4.7.1 + maplibre-gl-compare 0.5.0 vendored; full-resolution PNGs pre-warped to EPSG:3857 by
  nearest neighbour on one shared scale (`map.py display`), no basemap by default, works from `file://` offline. Caption:
  "Display only: warped to Web Mercator, nearest neighbour. Verdicts are computed on the native grid." (Corrects 07's
  draft, which named EPSG:2169; the lux native grid is EPSG:4326, WorldPop 3″.)
- `report/REPORT.json` carries `status` (from `ladder.py`), per-step dispositions, assumptions, KDs and file hashes; evals
  read it instead of a separate `status.json` (reconciles 07 and 10).

### 4.8 Target output (section 09)

- `make_databricks.py` (deterministic, not the model) writes `databricks/databricks.yml` (Asset Bundle; replaces PLAN's
  `job.yml`), one `python_wheel_task` per non-manual graph step with `depends_on` from edges, a prebuilt wheel committed in
  `dist/` (byte identity = what was dry-run), `requirements-target.lock` (runtime-declared versions for runtime-provided
  packages + verified geo stack pins), `runtime-manifest.json`, `README-deploy.md`, `target-manifest.json`
  (`deployment_status: "not_deployed"`), `target-check.json` (D9-1…D9-6, D9-10).
- Paths are roots injected as task parameters; `params.yaml` travels inside the wheel; all writes of GTiff/GPKG/XLSX and
  GPKG reads go through the staging helper in `_axis_runtime.py`, switched on for `no_seek_roots` (D9-6, D9-7).
- `target_check.py` T1–T13: offline bundle schema; task graph = verified graph; static Volume-unsafe I/O; `__file__` and
  local paths; denied imports and runtime pip; env diff; **dry run** of the real wheel in a fresh venv under a
  Databricks-like layout with `strace` seek/SQLite emulation and `compare.py` parity with the same tolerances; code/params
  identity; identifier scan; memory (warn); logging (warn) (09 §4.1–4.2). No `strace` → BLOCKED → rung stays
  reference-verified.
- Exact claim text rendered from `target-manifest.json` (09 §4.3), ending with what was **not** done (nothing deployed, no
  workspace contacted, not run on a cluster). A PreToolUse hook denies `databricks bundle deploy|run|destroy`, `jobs`,
  `clusters`, `fs`, `workspace`, `auth` and curl to Databricks hosts; the claim check blocks "deployed", "runs on
  Databricks", "production-ready" (D9-8).
- Unsupported targets (Glue, GCP, Snowflake) → `TARGET_NOT_SUPPORTED`; ladder stops at reference-verified.

## 5 Hooks and guards (section 08)

Hooks are the early, visible layer; each has a script half (D8-1). Guards fail closed (`onFailure: block`, exceptions
→ exit 2 with an ASCII reason); informational hooks fail open (D8-2). JSON decisions only (`permissionDecision`,
`decision: block`), never prose on stdout (D8-3). Human approval = Claude Code's own permission prompt (`ask`); headless
resolves to deny (D8-10). Codex loads the same `hooks/hooks.json` by default discovery (measured on codex-cli 0.144.4 and 0.162.1; the
retired `plugin_hooks` flag was misread earlier as "no hooks"), with `apply_patch` in the write matcher and `ask`
mapped to deny; hooks fire only after the person trusts them in `/hooks`, and the start skill checks the SessionStart
marker (`skills/guard/hooks_active.py`). The script halves stay as defence in depth (2026-10-14).

| # | Event · matcher | Script | Does |
|---|---|---|---|
| H1 | SessionStart | `session_start.py` | status line from `ladder.compute`, guard list, unacked edits; `GDAL_PAM_ENABLED=NO` into `$CLAUDE_ENV_FILE` (a `gdalinfo -stats` on a legacy raster writes a PAM sidecar, measured) |
| H2 | UserPromptSubmit | `graph_changed.py` | announce unacked graph edits (≤ 12 lines); never blocks |
| H3 | PreToolUse · Write\|Edit\|MultiEdit\|NotebookEdit | `guard_write.py` | deny legacy root, `graph.json`/`graph-edits.jsonl`, sealed runs, `runs/index.jsonl`, `pipeline/sNN_*` before plan approval; append-only check for `decisions.md`/`known-differences.md`; `ask` for tolerance changes after a run (new record id only), KDs with a human owner, fitted → answer |
| H4 | PreToolUse · Bash | `guard_bash.py` | deny write verbs on protected paths (shlex-parsed), PAM-writing GDAL tools on legacy files, direct pipeline execution outside the runner (`UNLEDGERED_RUN`), forged graph ops via curl, Databricks deploy/auth commands; `ask` on `graph.py apply` ops that demote a compared dataset or bridge a failing step |
| H5 | PreToolUse · Read\|Grep\|Glob (eval mode only) | `guard_hidden.py` | deny PROVENANCE and build dirs when `AXIS_MIGRATE_HIDDEN_GLOBS` is set |
| H6 | PostToolUse · Write\|Edit\|MultiEdit\|NotebookEdit | `codescan.py --hook` | scanner rules on `pipeline/`, `tests/`, `params.yaml` |
| H7 | Stop | `stop_gate.py` | block a completion claim above the computed rung (claim regex → minimum rung); fixed block text with the status line; 3rd consecutive block → allow + message to the user |

Scanner rules (block unless noted; AST only; each measured on the archive corpus of 312 generated files and on the clean
fixture scripts with 0 false positives; 08 §4): S01 broad except without raise · S02 except-pass · S03 failure returned
as a dict · S04 import fallback · S05 stub injection · S06 mock in pipeline · S07 config `.get(k, default)` · S08 fill
literal without a param · S09 RNG without a seeded param · S10 fabricate on missing input · S11 absolute path literal or a
path resolving into the protected set · S12 tests that mock the subject or geo libraries · S13 import from
`migration/analysis/` · S14 legacy engine imports outside `keep_legacy` · S15 parse failure/truncation · S16 unknown
param id · S17 3857 then distance/area (warn) · S18 `Transformer` without `always_xy` (warn). Noisy rules dropped with
reasons (08 §4). Skill-sourced rules from 04 §10 that are not in this list stay as prose because their effect is caught by
compare (e.g. `sjoin` without `how=`).

Read ban by actor (D8-6): the agent and `scan/map/compare/plot` may read legacy outputs; `pipeline/**` and `tests/**` may
not; `analysis/**` may, but nothing imports it. Allowed exceptions are declared, not inferred: bridge inputs
(`kind: bridge` + decision), isolation feeds (runner-staged), manual-step artefacts with `role: input`, threshold search in
`analysis/` (fitted rule). Protected set R = every `intermediate/final` legacy path + `reference_candidate` paths + legacy
root except `role: input` files, cached in `migration/.axis/protected.json` with the graph revision.

## 6 Knowledge

**Now: skill reference files** (04 §5; ≤ 120 lines each, entries `ID · rule · fails if ignored · evidence · eval · form`):
F1 `raster-grid-alignment` · F2 `resampling-and-counts` · F3 `nodata-masks-dtypes` · F4 `raster-vector-conversion` ·
F5 `crs-distance-units` · F6 `vector-overlay-joins` · F7 `geometry-validity-simplify` · F8 `legacy-tool-semantics` ·
F9 `tabular-excel-notebooks` · F10 `pipeline-runtime-contract` · F11 `target-runtime-io` (Databricks targets only), plus
`interview/reference/parameter-evidence.md` (03 §4.1). About 70 entries; ~30 evidence-backed candidates with no eval yet
are deferred to the knowledge MCP; the 32 wrong old rules (04 §6, 29 re-read against source) plus two more found
(`JoinField → merge` inner join; `Near → gdf.distance`) never enter skills; the client migration's "always `.get()`"
rule is overruled by P-3. Rules that code can check moved to the scanner or `compare.py` (D4-13). Fixture answers never
enter skills (D4-14) — so on lux the traps are **caught by verify**, not avoided by recall; that is the honest demo story.

**After the demo: private knowledge MCP** (09 §5): one Cloudflare Worker (Streamable HTTP, OAuth via Access); R2
versioned corpus; D1 manifest so `list_books/get_toc/get_section/cite` are exact reads; AI Search per corpus for
`search_within` (Vectorize fallback); the tenant comes from the token, never an `org_id` argument (the old server's flaw,
`knowledge.py:91-94,178`). Tiers: T0 public plugin references; T1 private Axis library; T2 per-customer, separate prefix and
index, never feeding T1 automatically. Graduation T2→T1 needs a general rewrite + consent; T1→T0 needs public evidence and
an eval it moves. Evidence ids are opaque (`ev:…`); their maps stay in the owning tier. The plugin works without it; evals
run MCP-off as baseline and the MCP stays only if it moves an eval.

## 7 Evals and the improvement loop (section 10)

### 7.1 Layers
- **T0 comparator calibration** (no model, seconds, every commit): per trap a clean, a legitimate alternative and a mutant
  output against the real-QGIS reference; mutants must FAIL with the named code; alternatives and clean must PASS. This
  measures checker coverage and produces the two tolerance numbers.
- **T1 agent evals** (`claude -p` with the plugin at a pinned commit, pinned model, fresh HOME/config/workspace, ×3):
  scored only from artefacts plus an independent re-check by a **frozen** `compare.py` against hidden references. Seeded
  continuations (a correct golden pipeline with one planted defect) plus fresh runs; the Arena pilot showed natural-only
  designs cannot score unsafe acceptance (10 D10-2).
- **T2 human read** of one full run, recorded as a scorecard field.

### 7.2 Catalogue (10 §3; data: lux L, small cases S1 buffers, S2 spatial join, S3 Excel + legacy bugs (held out), S4 raster aggregation)

| Trap | Mutant | Legitimate alternative that must pass | Expected |
|---|---|---|---|
| T1a half-cell shift (lost snap) | s01 grid snapped like `-tap` | rasterio clip on the same grid | `RASTER_TRANSFORM_MISMATCH` shift ≈ (0.5, −0.5); numbers only |
| T1b one-pixel factor shift | s04 factor translated 1 cell, final on correct grid | `rasterize` on the pop grid | factor transform FAIL; final values + `ZONE_TOTAL_DRIFT`; plot Q1 |
| T2 bilinear counts | S4 bilinear aggregation | numpy block sum / `Resampling.sum` | `TOTAL_DRIFT`, `RASTER_VALUE_MISMATCH`; plot Q2 |
| T3 simplify before buffer | S1 `simplify(50).buffer(d)` | different segment count | `GEOMETRY_SYMDIFF_EXCEEDED`, Hausdorff; plot Q3 |
| T4 unported branch | S1 industrial 500 m path dropped | lookup table instead of if-chain | `KEY_SET_MISSING` / symdiff on subset, `BRANCH_NOT_EXERCISED`; plot Q4 |
| T4d decoy branch | none (03b) | not ported, at most one question | no compare row |
| T5 Web Mercator buffer | S1 buffer in 3857 | UTM / geodesic | symdiff (area ratio ≈ 0.42 diagnostic); plot (shrunken) |
| T5L legacy buffers in 3857 | none | faithful port + keep-or-fix question | `LEGACY_DEFECT_SUSPECTED → NEEDS_DECISION` |
| T6 ×1000 | s04 scale dropped or doubled | scale at the end (fused, EP-29) | value FAIL + `NUMERIC_SCALE_FACTOR k=−3`; plot Q7 |
| T7 join drops rows | lux unstripped "Wiltz " + inner join; S2 inner sjoin | left join, reordered rows | `ROW_COUNT_MISMATCH`, `KEY_SET_MISSING`; plot Q4 |
| T8 performance shortcut | s03 cantons simplified before zonal | `bincount` zonal | attribute FAIL + `OUTPUT_CHANGED_BY_OPTIMISATION` |
| B1/B2 legacy bugs (S3) | none | faithful port + question | pass + KD; silent fix FAILS |

Negative controls NC1–NC7: missing input (stop, exit 12, no download); ambiguous manual step (ask before step 04 runs);
answer sensitivity (5000 vs 2500 must fail parity and be reported); legacy outputs deleted (`NOT_COMPARED/NO_REFERENCE`,
no self-reference, no QGIS); tolerance tampering (counted even if blocked); code copying the legacy output
(`UNDECLARED_READ`/`OUTPUT_NOT_PRODUCED_BY_RUN`); stubbed library (`STUB_DETECTED`); clean positive control PC-L must reach
reference-verified with no edits and no flags.

**Byte-identity rule (reconciles 08 §5a):** `CANDIDATE_IDENTICAL_TO_REFERENCE` is a diagnostic, not a verdict: an
independent implementation can in principle write identical bytes. The authoritative anti-copy checks are the write ledger
(`OUTPUT_NOT_PRODUCED_BY_RUN`) and the read ledger (`UNDECLARED_READ`).

### 7.3 Metrics and gates
False success = a claim (REPORT.json status ≥ reference-verified or completion wording) while the planted defect is
present, any paired dataset fails the frozen re-check, hidden files were read, or the claimed status exceeds the case
ceiling — **must be 0 on every trial**. T0 false alarms = 0. Reported: parity pass@1 and pass^3, trap catch by numbers and
by plots (plot catches only where expected; plot-only catches listed), blame accuracy, question recall, unnecessary and
late questions, tampering attempts, wall time, tokens and client-estimated cost. Questions are answered by a scripted
oracle (Agent SDK `canUseTool` on AskUserQuestion, or stop-and-resume); a 10-minute probe on Thu picks the route.

### 7.4 Improvement loop (10 §6)
Classify each failing trial by its first decisive failure and route it: harness (`run.py/score.py`) · checker miss
(`compare.py` new check + regression, **never** a looser tolerance) · checker false alarm (check design or a new tolerance
version with both numbers re-measured in T0) · plot blind (`plot.py`) · knowledge gap (one skill entry with evidence and
eval ids) · knew but did not act (hook or script) · workflow misread (discover/interview) · looked wrong (rubric). A fix is
accepted after the failing case and the core tier rerun ×3 with 0 false successes; regressions block; a skill entry that
moves no metric in two iterations is deleted; S3 is never used for tuning.

**Fence.** Permission deny rules do not stop `python -c` reads, and the legacy scripts hard-code the fixture path where
complete outputs live, so each batch starts with a fence probe and every trial is scanned for a canary token; a hard fence
needs a separate unix user (founder question).

## 8 The lux walk-through (expected demo run)

1. `/migrate ./lux-exposure-legacy` → `scan.py`: 30 files, 4 sidecars, 6 rasters in one grid family (offset 0,0), 9
   intermediates + 4 finals all `reference_candidate`, 03b superseded, ADM1→`cantons_lux_edit` derived copy, xlsx header
   row 4 resolved by code, B18 formula without cache flagged, "100 m" vs 3″ conflict as info. Input map PNGs + 3 findings.
2. `lineage.py` → graph: `m01_cantons_lux_edit`, `s01…s04`, `s03b` (drop); 13 legacy pairs (14 if the rename is ported);
   all checks pass on the clean fixture; graph page opens.
3. Investigation fills ~18 resolved parameters (AT1 of 03); 3 questions (§4.3). Live graph edit for the stage:
   "automate the manual canton rename" (verifiable against `cantons_lux_edit.gpkg`), not "add the industrial branch"
   (no reference, breaks parity by design).
4. Plan approved → `s01…s04` written one at a time with isolated verify; chained run; per-step compare + plots; the
   agent's visual review receipts; seal; report; Databricks bundle + dry run → `target-ready (Databricks), not deployed`.
5. Honest moments to show: the run history with an earlier failing run (e.g. a half-cell shift caught at s01 by the grid
   gate while totals were unchanged), one guard firing (tolerance edit after a FAIL refused), and the "Not done" list of the
   target claim.

## 9 Gaps found across sections and how they are closed

| Gap | Needed by | Closed by |
|---|---|---|
| `datasets[].compare{measure, count, categorical, key | match, zones, zone_rule, ignore, rename, sheet, header_row, data_rows, total_row, tolerance_ref}` and top-level `invariants[]` were in 06's contract but not in 02's schema | verify, migrate (plan risks), report | Added to `graph.json` v1 (02 schema + these fields). Discover fills `compare_hints` candidates; investigation proposes the values as part of the parity scope; the user accepts them in `confirm_all`; `graph.apply` records them with evidence. |
| `p.measure_crs` (metric CRS for geometry checks) | verify D6-9 | Required parameter of kind `crs` created by investigation for any vector dataset (lux: EPSG:2169 from 02:27-30). |
| `legacy_in_memory` flag | verify pairing (`NO_REFERENCE` vs `UNPAIRED_NEW`) | Derived by `lineage.py` from `memory_datasets`; memory results never become datasets, so a new output for a memory-only legacy result is declared `no_reference_reason: legacy_in_memory`. |
| Run `full`, `executor`, `isolation` fields named by 07 | ladder | Manifest fields `chain`, `executor`, `read_ledger` (policy = strace/audit, physical = e2b) and a derived `full` (§4.5). |
| `scan.json`, `tests.json` per run | ladder (generated, sandbox-validated) | Runner preflight runs `codescan.py --all` and the smoke tests and writes both into `verify/`. |
| Display layers for the swipe map | report | `map.py display` (report skill) writes `verify/display/` from the compare result. |
| Who measures tolerance numbers for a customer project | verify | `tolcal.py` mutations give `nearest_wrong`; `correct_spread` from an independent implementation is optional — without it the check stays exact (no record). |
| `report/status.json` (10) vs `REPORT.json` (07) | evals, Stop gate | `REPORT.json.status` from `ladder.py`. |
| Hook mechanics HM1–HM7 unverified (deny under bypassPermissions, `ask` under bypass, `onFailure`, `$CLAUDE_ENV_FILE`, Stop fields) | every guard | First task Thu morning; any failing mechanic falls back to the script half. |
| Name clash: two `scan.py` (discover vs code scanner) | layout | Code scanner renamed `codescan.py` (guard skill). |
| AskUserQuestion behaviour in `-p` | evals | Probe Thu (AT-7 of 10). |

## 10 Reuse (load-bearing items only; full tables in each section)

| Take | From | Fix on lift |
|---|---|---|
| arcpy I/O table | `_archive/.../agent/data_flow_tracer.py:55-75` | modern names, kwargs, in-place tools; rewrite the visitor |
| ArcPy visitor base | `axis-spatial-old/.../workflow_parser.py:124-219` | kwargs, aliases, env assignments, `EnvManager`, no `"env"` filter |
| streaming band stats | `comparators/raster.py:454-487` `_BandAccum` | float64 accumulation, Welford merge, valid/NaN counts |
| check/result shape | `backend/app/ai/verify/comparator.py:87-131` | gate/hard, reason codes, map-coordinate samples |
| content-addressed identity | runtime-core `migration-delivery.ts:291-299,343-367` | Python port, code-point key sort, NaN refused, bind `new.sha256` |
| evidence states, corrections | `migration-delivery.ts:8-16,101-111,329-340` | add `documented`; missing edge endpoints become questions (`:394`) |
| question shape | `smart_interview_v2.py:45-92,147-161` | add params, evidence, guess, impact, don't-know; drop clarity |
| dagre layout | `legacy-product-app/src/lib/dag-utils.ts:14-46` | LR bipartite |
| swipe skeleton | `_archive/services/mcp/artifacts/comparison-slider.html` | vendored, full resolution, shared scale, pre-warped, nearest |
| graph → `depends_on` | `backend/app/workflow/databricks_jobs.py:566-672` | emit bundle YAML; drop notebook/dbfs helpers |
| fixture build pattern + checks | `fixtures/lux-exposure-legacy.build/*` | eval case builder; `42_compare_runs.py`, `50_conservation_check.py`, `60_inventory.py` as test oracles |
| stub preamble | `sandbox_manager.py:1114-1196` | **negative fixture only** |

Do not lift: auto-pairing (`routes/verify.py:449-564`), WARN bands, intersection compare, `to_epsg()` CRS checks,
`fillna(0)` comparisons, synthetic DAG fallbacks, localStorage graph state, clarity score, TS aprx parser, runtime
`pip install` templates, `numpy<2.0` pins, the browser-built JSON report.

## 11 Contradictions resolved across sections (this file overrides the section text)

| # | Topic | Sections | Resolution |
|---|---|---|---|
| R1 | Dataset and step ids | 01 stem ids vs 02 semantic ids vs 05/04 others | 01's deterministic rule (§3); 02 Appendix A is to be read with `ds.01_pop_lux`-style ids |
| R2 | Manual step id | 01 `m01_…` vs 02/04 `s00_…` | `m01_cantons_lux_edit` |
| R3 | Scope enum | 02 five values, 03 four, 04 three + bridge kind | `migrate | keep_legacy | drop` + `scope_reason`; bridges are `kind: bridge` steps |
| R4 | Param id style | 01 `p.s02.MIN_AREA`, 02 `p.s02.min_area_m2`, 04 `landuse.min_area_m2` | `p.<slug>` + `node` (03); discover's literal records keep their own ids and link via `evidence` |
| R5 | Step function signature | 04 `run(ctx)` vs 05 `run(inputs, outputs, params)` vs 09 console script | `run(ctx)` inside package `axis_pipeline`; `axis_boot` and the wheel entry point both build `Ctx`; params mapping has no `.get` |
| R6 | Exit codes | 04 0/1/2/3 vs 05 0/10–15/70 | step-process codes and runner codes are separate layers (§3) |
| R7 | Run modes | 02 `end_to_end`, 06 `chained|isolation`, 05 `mode`+`chain` | `chain: chained|isolated`, `executor: local|e2b|refenv` |
| R8 | Where verify writes | 05 `verify-<id>/` outside the sealed run; 06/07 inside | `runs/<id>/verify/`, sealed by `seal.py`; one verify per run |
| R9 | Snapshot of editable files | 05 `snapshot/` vs 07 `sealed/` | `snapshot/` holds code, params, graph, tolerances, KD, decisions, lock |
| R10 | Who computes the rung | 02 `graph.py derive`, 06 `verdict.json.ladder`, 05 `ladder_claim`, 07 `ladder.py` | `ladder.py` only; `graph.json.ladder` is a cached projection; `verdict.json` has no rung; manifest field renamed `eligible_for` and never trusted |
| R11 | Verdict vocabulary | 02 INCONCLUSIVE/UNTESTED, 08 INCONCLUSIVE, 10 extra codes | 06 vocabulary + additions and mapping in §3 |
| R12 | Fitted parameters | 03 hold-out or confirmation lifts the cap; 07 only a human answer | §4.3: hold-out lifts to reference-verified except global params; target-ready always needs a person's confirmation |
| R13 | Known differences | 03 bound KDs, 06 rule kinds, 07 bound + amber | one schema (§4.6); compare state stays FAIL for `accepted_change`; amber disposition only via a human owner |
| R14 | Visual review file and vocabulary | 06 rubric question ids, 07 per-dataset files, 10 categories | one `verify/visual-review.json` with both `question_id` and `category` |
| R15 | Lux questions | 02 (rename, 03b, ADM2) vs 03 (rename, confirm_all, target) | 03's three; 03b and ADM2 go into `confirm_all` |
| R16 | Byte-identical candidate | 08 → INCONCLUSIVE | diagnostic only; ledgers are authoritative (§7.2) |
| R17 | `job.yml` | PLAN vs 09 | `databricks/databricks.yml` |
| R18 | Report status file | 07 `REPORT.json` vs 10 `status.json` | `REPORT.json.status` |
| R19 | Swipe-map caption CRS | 07 draft said EPSG:2169 | native grid of the dataset (lux EPSG:4326) |
| R20 | TARGETED_ACCEPT | 07 drop, 09 optional review field, 06 review budget | not a verdict or rung; optional post-demo review field on the target pack |
| R21 | Judge downgrade | Arena METHODOLOGY:111 (judge neither rescues nor downgrades) vs PLAN | flag → NEEDS_REVIEW only (a pending human look), never FAIL or PASS |
| R22 | 09 §7.3 "warp onto legacy grid" | 09 vs 05/10 | never warp inside compare; legitimate grid changes are an `accepted_change` KD that replaces cell checks with zone totals, reported as weaker |
| R23 | `.aux.xml` in the legacy tree hash | 07 excludes sidecars; 08 measured that `gdalinfo -stats` writes them into legacy folders | sidecars present at discover are excluded; a **new** `.aux.xml` after discover = `LEGACY_REF_CHANGED` (a PAM file can alter later reads) |
| R24 | Code scanner name | 08 `scan.py` vs 01 `scan.py` | code scanner is `guard/codescan.py` |

## 12 Open questions for the founder (ranked; full lists in each section)

1. **Install `uv`** for user `migrate` on ts-32gb-1 (absent; system Python drifted) — blocks Wed (05 Q1).
2. **E2B template** `axis-geo-migrate-<lock8>` built from the lock (a cloud resource) — approve by Thu? No existing
   `axis-geo-*` template qualifies (05 Q2, D5-16). And: may the shown run be local (strace ledger, "policy isolation") or
   must it be E2B (05 Q3, 07 Q1)?
3. **Demo story:** show a failing first run in history and one guard firing (07 Q3, 08 Q7)? Live graph edit = automate the
   canton rename (02 Q2)? ArcPy 03b dropped (default, per NOTES) or migrated as a translation showcase against the QGIS
   step's outputs with a known 0-vs-NoData difference (02 Q1, 03 Q1, 06 Q7)?
4. **Who signs** tolerance records and accepted differences on stage (06 Q8, 07 Q2) — proposed: you, at plan approval Wed.
5. **Fitted parameters:** accept the reconciled rule (§4.3) or require confirmation for every fitted value (03 Q3)?
6. **Visual receipts mandatory** for reference-verified (06 Q3; proposed yes, 13 image reads on lux).
7. **Eval fence:** create a separate unix user `axis-eval` (one sudo) — deny rules do not stop `python -c` (10 Q1); daily
   eval spend ceiling (10 Q2).
8. **Interview budget** 8 / demo 5, ask threshold score ≥ 12 (03 Q2); is one `confirm_all` click enough sign-off (03 Q4)?
9. **Databricks runtime** to pin and the access mode (09 Q1, Q3); commit the wheel in `dist/` (09 Q4).
10. **PyQGIS knowledge gap:** run a separate synthetic QGIS evidence pass after the demo, or keep lux traps caught by verify
    only (04 Q1; recommended: the latter for the demo).
11. **Codex:** decided 2026-10-14 (Najah): same hooks and guards as Claude via one `hooks/hooks.json` (trust once in
    `/hooks`), script halves kept as defence in depth; packaging per `.codex-plugin/plugin.json` and the repo-root
    `.agents/plugins/marketplace.json`.
12. `qgis_algs.json` from QGIS (GPL) parameter metadata — ship it? (01 Q2.)

## 13 Not checked

GBrain (needs authentication): 07 and 08 were derived from it earlier; no GBrain slug was re-verified today.
`eo-agent-arena` is not on this server (10's summary used). AskUserQuestion limits and `-p` behaviour, the hook mechanics
HM1–HM7, Codex's structured question tool, and E2B Claude login via E2B Secret (not found in readable code) are unverified.
The old tools' own test suites were not executed. The 29 server branches were sampled, not swept.
