# 08 — Hooks and guards

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Owner: section 08. Status: design, 13 Oct 2026. Evidence ids: `FH-*` (05), `AK-*`/`H-*` (04), `MP-*`/`MPV-*` (02),
`IN-*` (03), `HL-*` (07), `08§*`, `10 B*/C*`, `06:<line>` (06-old-app-flow.md). Code paths: `A/` = `~/src/axis-spatial/_archive/`,
`RW/` = the real client migration's `.claude/` (generalised only), `HR/` = `~/src/server-copies/axis-spatial-harness-release/.claude/`.
**[measured]** = I ran it on this host today (scratch scripts, not kept).

## 1 Scope and interfaces

I own: `hooks/hooks.json`; the hook scripts (`hooks/bin/*.py`, stdlib only); the scanner `scan.py` (one rule engine used
by the PostToolUse hook **and** by `run_local.py`/`run_e2b.py` before any run); the protected-path policy; the claim check in
the Stop gate. I do not own the verdicts (06 `compare.py`), the rungs (07 `ladder.py`), the read ledger (05 D5-9) or graph
writes (02 `graph.apply`). Hooks are the **early, visible** layer; scripts are the **authoritative** layer. Every guard
below names both, so that a missed hook (Bash writes, Codex, a hook timeout) never turns into a pass.

```
Write/Edit ─PreToolUse guard_write──► deny protected paths ──► file lands ─PostToolUse scan ──► block reason to model
Bash ───────PreToolUse guard_bash───► deny writes to legacy/runs, direct pipeline runs, forged graph ops
run_local ──scan.py --all (refuse on block) ─► strace read ledger (05) ─► manifest ─► compare (06) ─► seal/ladder (07)
Stop ───────stop_gate: claim in last message vs ladder.compute(bundle) ─► block "done/verified" when unsupported
UserPromptSubmit graph_changed.py (02 §4.7, informs only) · SessionStart: status line + GDAL_PAM_ENABLED=NO
```

## 2 Decisions

| Id | Decision | Evidence |
|---|---|---|
| D8-1 | **Scripts are the authority, hooks the early warning.** Each guard is re-checked in a script that refuses to proceed (run, compare, seal, ladder). | Bash can write files that PostToolUse(Write) never sees; hook timeouts are non-blocking (docs, hooks#timeouts); `codex features list` on codex-cli 0.144.4 shows `hooks stable true` but `plugin_hooks removed false` **[measured]**: a Codex plugin cannot ship hooks. |
| D8-2 | **Guards fail closed; informational hooks fail open.** Guard entries set `"onFailure": "block"` (Claude Code ≥ 2.1.295; host has 2.1.296 **[measured]**) and wrap `main()` so any exception exits 2 with an ASCII reason. `graph_changed.py` and `session_start.py` exit 0 on error with a one-line note. | Every old guard failed open: `HR/hooks/protect-files.py:40-41` (`except Exception: sys.exit(0)`), `RW/hooks/post-edit-validate.py:14-16`, rule exceptions swallowed in `A/services/backend/app/sandbox/pattern_registry.py:1063`, the LLM auditor "assuming passed" 13× (06:199), scanner `standalone` platform has no rules and always passes (`A/.../guardrails/post_generation_scan.py:24-25`). |
| D8-3 | **Use JSON decisions, never stdout prose or `systemMessage`, to talk to the model.** PreToolUse: `hookSpecificOutput.permissionDecision: deny` + reason; PostToolUse/Stop: `decision: block` + reason. | `RW/hooks/post-edit-validate.py:46-57` reported syntax errors as `systemMessage` with exit 0 (never blocked); `HR/hooks/protect-files.py:35` printed the reason to stdout on exit 2 (the model sees stderr only). |
| D8-4 | **Hooks are tested with real stdin payloads and are ASCII-only**; CI checks every `hooks.json` command path exists. | `RW/hooks/session-end-reminder.py:40` read `input["tool"]` instead of `tool_name` so it never fired; the client repo's settings referenced a missing `pre-edit-check.py`; matcher `Edit|Write` missed MultiEdit (`RW/.../post-edit-validate.py:21`); MP-37 hooks crashed on non-ASCII consoles (`HR/.../protect-files.py:35` prints an emoji). |
| D8-5 | **AST rules only, measured on two corpora before shipping.** A rule ships as `block` only if it fires on the archive's generated code and gives 0 hits on our clean scripts; otherwise `warn` or dropped. | Old line-regex scanner flagged every file (`A/.../code_scanner.py:70-84` `from pathlib import Path` FATAL; `:121-126` any `__file__` FATAL); FH-38: 34/61 harness errors were false positives. Corpora **[measured]**: 312 generated non-test files, 164 generated tests (`A/tasks/legacy-tasks/migration-runs/*/generated-code`), 5 fixture build scripts + 5 legacy PyQGIS scripts (0 hits on every shipped rule). |
| D8-6 | **The read ban is by actor.** The agent (Read, Bash, `scan.py`, `map.py`, `compare.py`, `plot.py`) may read legacy outputs; `migration/pipeline/**` and `migration/tests/**` may not; `migration/analysis/**` may (exploration and threshold search) but nothing imports from it. | PLAN "investigate first" (agent reads intermediates; thresholds tested against an intermediate); HL-32; 01 Q6. |
| D8-7 | **Static + dynamic read ban; no PreToolUse-on-Bash read block.** Static: scanner S11 on code and `params.yaml`. Dynamic: 05's strace ledger. A Bash read hook cannot see what a Python process opens and would block the agent's legitimate inspection. | Probe **[measured]**: a `sys.addaudithook` saw **no** event for `gdal.Open(04_FINAL/lux_res_exposure_2020.tif)`; `strace -f -e trace=openat` logged it. Matches 05 D5-9. |
| D8-8 | **Stop blocks the claim, not the stop.** No claim in the last message → allow, whatever the rung (Stop fires after every turn, including interview questions). A completion/parity claim above the computed rung → block with the status line to use. | PLAN:38 "no done without a passing parity report"; 07 §6 "the hook blocks the claim; the report always renders"; MPV-09 "validated meant compiled". |
| D8-9 | **Immutability by hash, with hooks as tripwires.** Legacy tree hash (01 `scan.read_only_verified`), sealed run copies (07 D7-3) and the manifest's tolerance hash are authoritative; PreToolUse denies the obvious edits. | 10 B3.5 (hash binding), 10 C2.6 (`TOLERANCE_CHANGED_AFTER_RUN`), 05 D5-10 (sealed runs). |
| D8-10 | **Human approval = Claude Code's own permission prompt** (`permissionDecision: "ask"`), never a file the agent can write. Headless (`claude -p`, evals): `ask` resolves to deny (docs, headless). | 08§8.5 tolerance after results; 10 C1 "nothing can move a FAIL up"; 07 KD needs `owner: human:<name>`; 04-migrate-skills.md:117 migrate→bridge after FAIL needs user approval. |
| D8-11 | **SessionStart sets `GDAL_PAM_ENABLED=NO`** (via `$CLAUDE_ENV_FILE`) for all agent Bash; `guard_bash` denies `gdalinfo -stats`/`gdal_edit.py`/`gdaladdo` on legacy paths. | **[measured]** `gdalinfo -stats` on a copy of `05_TEMP/03_pop_res.tif` created `03_pop_res.tif.aux.xml`; with `GDAL_PAM_ENABLED=NO` it did not. 01 D1-16: a QGIS subset filter bumped a GPKG header in this fixture's build. |
| D8-12 | **No SubagentStop gate.** Subagent output goes to the main agent, not the user; the main Stop gate checks the user-facing claim. | Cost without a failure in evidence; `HR/hooks/task-completion-gate.py` gated subagents on a TypeScript build only. |

## 3 hooks.json outline

All commands: `PYTHONIOENCODING=utf-8 python3 -I "${CLAUDE_PLUGIN_ROOT}/hooks/bin/<x>.py"`. Each script first walks up
from stdin `cwd` to `migration/graph.json`; none found → exit 0 (plugin idle). Matchers are exact-name lists (docs: letters
and `|` only are not regex).

| # | Event · matcher | Script | Timeout | onFailure | Mode | Does |
|---|---|---|---|---|---|---|
| H1 | SessionStart · `startup\|resume\|clear\|compact\|fork` | `session_start.py` | 5 s | (default) | inform | additionalContext: status line from `ladder.compute`, the guard list (6 lines), unacked graph edits count; append `export GDAL_PAM_ENABLED=NO` to `$CLAUDE_ENV_FILE` |
| H2 | UserPromptSubmit · `*` | `graph_changed.py` (02 §4.7) | 3 s | (default) | inform | ≤ 12 lines "graph changed (rev a→b …)"; never blocks |
| H3 | PreToolUse · `Write\|Edit\|MultiEdit\|NotebookEdit` | `guard_write.py` | 5 s | block | deny/ask | §5c, §5g, graph.json/graph-edits.jsonl (02 D2-9), `runs/index.jsonl`, sealed runs, append-only files |
| H4 | PreToolUse · `Bash` | `guard_bash.py` | 5 s | block | deny | write verbs on protected paths; direct pipeline execution; `curl …/api/ops` (02 Q6); PAM-writing GDAL tools on legacy |
| H5 | PreToolUse · `Read\|Grep\|Glob` | `guard_hidden.py` | 3 s | block | deny | **eval mode only** (`AXIS_MIGRATE_HIDDEN_GLOBS` set by the harness): deny `*.PROVENANCE.md`, `*.build/**` (10 B5 category a "grader access") |
| H6 | PostToolUse · `Write\|Edit\|MultiEdit\|NotebookEdit` | `scan.py --hook` | 10 s | block | block/warn | rules §4 on the touched file if under `pipeline/`, `tests/`, or is `params.yaml`; block → `decision: block`; warn → `additionalContext` |
| H7 | Stop | `stop_gate.py` | 15 s | block | block | §5b; reads files only, never runs compare |

Not registered: SubagentStop (D8-12), PermissionRequest, PreCompact. The Stop gate relies on the built-in cap (8 consecutive
continuations, `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`) plus its own: on the 3rd consecutive block with `stop_hook_active` it
allows the stop and emits `systemMessage` to the **user**: "axis-migrate: the agent's last message claims '<quote>'; computed
status is <rung>." The `HR/` stop gates had no `stop_hook_active` handling at all (`grep` finds none) **[measured]**.

## 4 Scanner rules

Scope: `migration/pipeline/**/*.py`, `migration/tests/**/*.py`, `migration/params.yaml` (S11 only). Never the legacy folder
or `migration/analysis/` (except S13). Block rules have **no inline suppression**; exceptions come only from declared
params/graph entries named per rule. Counts are files/handlers in the archive corpus (312 non-test / 164 test files) **[measured]**.

| Id | Pattern (AST) | Sev | Evidence | False-positive notes | Test (fires / must not) |
|---|---|---|---|---|---|
| S01 swallow-broad | `ExceptHandler` with `type` None, or Name/Tuple containing `Exception`/`BaseException`, whose body walk has no `Raise` and no `Call` to `sys.exit`/`os._exit`/`exit` | block | FH-01, AK-32 (`GEN:2705-2711`), T1; 214 handlers in 130/312 files | 0 in clean corpora. A log-and-reraise handler passes | `except Exception as e: log.error(e)` / `…: log.exception(e); raise` |
| S02 except-pass | any `ExceptHandler` whose body is only `Pass`/`Continue`/`Expr(Constant)` | block | T11 (`A/.../comparators/raster.py:360-362` all-NaN = equal); 43 handlers / 36 files | Allowed: `FileNotFoundError` handler whose `try` body is one call to `os.remove`/`unlink`/`rmtree` (temp cleanup) | `except ValueError: pass` / cleanup idiom |
| S03 failure-as-value | handler without raise that `Return`s a `Dict` with key `success`/`status`/`ok`/`error` | block | FH-02, T2; 111 returns / 79 files (e.g. `except StepError: return {"status": "failed"}`) | None seen | dict return in handler / dict return after successful work |
| S04 import-fallback | `Try` whose body has `Import`/`ImportFrom`; handler catching `ImportError`/`ModuleNotFoundError`/`Exception`/bare without raise | block | FH-03, T3, IN-29; 10/312 files incl. `exactextract`→`rasterstats` swap (changes zonal semantics, AK-21) and `cloud_logging = None` | Optional deps belong in the lock, not in code | that swap / plain import |
| S05 stub-injection | `Assign` to `Subscript(sys.modules)`; calls `sys.modules.setdefault/update`, `sys.meta_path.insert/append`; `ClassDef` subclassing `MetaPathFinder`/`Loader`/`ModuleType`; `builtins.open =` | block | `A/.../sandbox/sandbox_manager.py:1110-1166` stub preamble; 2/164 tests | Same rule in tests (a stubbed library tests nothing) | `sys.modules["rasterio"] = X` / `import sys; sys.exit` |
| S06 mock-in-pipeline | `Import`/`ImportFrom` of `unittest.mock`, `mock`, `pytest_mock`, or names `MagicMock`/`Mock`/`patch`/`create_autospec` under `pipeline/` | block | FH-03, T3; 05 D5-8 layer 1 | Tests: see S12 | `from unittest.mock import MagicMock` in `pipeline/s01.py` / same in `tests/` |
| S07 config-default | `Call` `.get(Constant str, default)` (2nd arg or `default=`) whose receiver's last name is `params`/`config`/`cfg`/`settings`/`conf`/`spec`; `getattr(params, k, d)`; `ctx.param(k, d)` | block | FH-18 (DFIX-003 `A/.../remediation/deterministic_fixes.py:145-163`, `d['k']`→`d.get('k','')`), FH-17; 347 calls / 166 files incl. `tolerance 1e-3`, `scale_factor 1000`, `output_path ""` | Most hits were benign knobs (`chunk_size`); still block: every knob lives in `params.yaml`. `os.getenv(k, d)`/`os.environ.get(k, d)` → **warn** | `params.get("min_area", 2500)` / `params["min_area"]` |
| S08 fill-literal | `.fillna(Constant)` or `value=Constant`; `np.nan_to_num(x)` without `nan=`; `.replace(np.nan, Constant)`; `np.where(np.isnan(a), Constant, a)` — unless the value is `params[...]`/`ctx.param(...)` | block | T13 (`A/.../verify/comparator.py:503-504`), MP-07 (SetNull → NoData, not 0), MP-22; 1/312 files | Rare in generated code; kept because silent and cheap to comply (declare the fill in `params.yaml`) | `df.fillna(0)` / `df.fillna(params["fill_pop"])` |
| S09 rng | `Call` chain starting `np.random.`/`numpy.random.`/`random.`; `default_rng(...)` unless the seed arg is `params[...]` | block | AK-33 (`A/.../prompts/generation.py:540-576` mandated random points for missing input), FH-35; 1/312 files | Legit sampling declares a seed param | `np.random.uniform(…)` / `default_rng(params["seed"])` |
| S10 fabricate-missing | `If` whose test calls `exists`/`isfile`/`is_file`; the missing branch has no `Raise`/`sys.exit` and calls a data constructor (`GeoDataFrame`, `DataFrame`, `GeoSeries`, `Point`, `Polygon`, `np.zeros/ones/full/empty`, RNG, `rasterio.open(…, "w")`) | block | AK-33, FH-35, ERR-008 | 0/312 hits: the generic "exists without raise" variant hit 41 branches / 36 files, sampled mostly download caches → that variant dropped; this narrow one needs the eval trap "missing input" (PLAN negative control) | `if not p.exists(): gdf = gpd.GeoDataFrame(...)` / `if not p.exists(): raise FileNotFoundError(p)` |
| S11 reference-path | (a) any absolute path `Constant`/`JoinedStr` part in code; (b) a constant or `params.yaml` value that, joined to the legacy root and `realpath`-resolved, lies under a `reference_candidate` path (01) or equals a graph dataset's `legacy_path` whose role is `intermediate`/`final`; (c) `os.walk`/`glob`/`rglob`/`listdir`/`scandir` on a params path → warn | block | HL-32, PLAN:59; FH-14 (absolute literals in 36 files); 04-migrate-skills.md:85 | `params.yaml` input entries with `role: input` pass; `role: bridge` passes only with `decision: D-n` present in `decisions.md` and step `scope: bridge`/`keep_legacy` in the graph (§5a) | `"…/04_FINAL/x.tif"` / `ctx.input("ds.lux_ppp_2020")` |
| S12 test-mocks-subject | in `tests/`: `patch("<t>")`, `patch.object(<m>, …)`, `mocker.patch`, `monkeypatch.setattr(<m>, …)` where `<t>`/`<m>` resolves to `pipeline.*` or to `geopandas`, `rasterio`, `numpy`, `pandas`, `shapely`, `pyproj`, `pyogrio`, `fiona`, `scipy`, `xarray`, `rioxarray`, `exactextract` | block | FH-26/T8: 59/164 tests mock, 0/164 read legacy; 34/164 import mock **[measured]**; the auditor prompt said "REJECT tests that use mocks" (`A/.../prompts/generation.py:3271`) and the tests kept them, so the rule must be code | Patching time, network, or a logger is fine | `patch("rasterio.open")` / `patch("time.time")` |
| S13 analysis-import | `Import`/`ImportFrom` of `analysis` or a relative path into `migration/analysis/` from `pipeline/` or `tests/` | block | HL-32; D8-6 | — | `from analysis.fit import best_min_area` / none |
| S14 legacy-engine | `import arcpy`/`qgis`/`processing`; `subprocess.*` with an arg containing `qgis_process`/`arcpy` — unless the step's `scope` is `keep_legacy` | block | H-01/GUARD-001; 237/309 arcpy rows (FH-24) | — | `import arcpy` in `pipeline/` / in a `keep_legacy` bridge |
| S15 parse | `ast.parse` fails; first line `#` heading or `\|` table in a `.py` | block | FH-06 (17 files / 9 runs); re-scan 5 non-test + 6 test files **[measured]** | — | truncated file / valid file |
| S16 param-id | constant key of `params["k"]`/`ctx.param("k")` not in `params.yaml` | block | FH-17 (6 alias lessons), 04 C10 | — | `params["min_aera"]` / `params["min_area"]` |
| S17 3857-distance | `to_crs(3857\|"EPSG:3857")` then `.buffer/.distance/.area/.length` in the same `FunctionDef` | warn | FH-10/11, AK-02; ≥10 files / 8 runs (05) | Function-level, not dataflow: warn only; compare (06) catches the effect | that sequence / `to_crs(2169)` then buffer |
| S18 axis-order | `Transformer.from_crs(...)` without `always_xy=True` | warn | AK-03, FH-34 (ERR-007) | Low FP (04 H-12) | without / with |

**Dropped** (reason): `__main__` without non-zero exit (FH-01, 148/203) — redundant: uncaught exceptions exit 1, S01–S03 catch
swallowing, the runner owns success (05 exit codes). Narrow-handler fallback (`except NotFound: return 0`) — 36 handlers / 31
files, sampled mostly existence probes; IN-29's "list every fallback taken" needs runtime, not AST. Generic exists-without-raise
(41 hits, download caches; MP-29 is neutralised by fresh run dirs, 05 D5-10). Old `AWS-001/002`, `DB-004` (every file; DB-004's
own fix is an `os.getcwd()` fallback, `code_scanner.py:141`), `missing-dry-run` (`pattern_registry.py:143-194`: it enforces
the stubbed dry-run culture FH-03 rejects), RAG auto-learned string rules (`pattern_registry.py:997-1031`, unreviewed).
Magic-number literals (noise). Non-ASCII NAME tokens (FH-08: real cases are SyntaxErrors → S15). GIS semantics with medium FP
(04 H-08 `sjoin` without `how=`, H-09 `merge` without `method=`, H-13 unmasked read) stay in skill references; their effect is
caught by compare. Geographic-CRS buffers move to the runner as `-W error::UserWarning` (04 H-21), owned by 05.
Undefined names/imports (FH-05, FH-09): `ruff F821/E999` in the runner preflight (needs a non-stdlib tool; hooks stay stdlib).

## 5 Guards

### (a) Generated code may not read legacy outputs or references

**Protected set R** (computed by `scan.py` from `graph.json` + `inventory.json`, cached in `migration/.axis/protected.json`
with the graph revision): every dataset whose `legacy_path` has role `intermediate` or `final`; every 01 `reference_candidate`
path; `<legacy_root>/**` except `role: input` files; sibling `*.PROVENANCE.md`/`*.build/` (eval only).

**Static** (S11, S13, S14): no path literals at all in pipeline code; inputs only through `ctx.input(ds_id)`/`run(inputs=…)`;
`params.yaml` values resolving into R block unless declared. **Dynamic** (05 D5-9): strace ledger per step; any opened path in
R → exit 11 `UNDECLARED_READ`; macOS fallback (audit hook + `CPL_DEBUG`) is recorded as `read_ledger: audit+cpl_debug` and
capped at sandbox-validated (Q2); E2B uploads only staged inputs (`e2b-physical`). **Anti-copy** (asked of 06/05): every
candidate output must appear as a write in the step's ledger (`OUTPUT_NOT_PRODUCED_BY_RUN`), and a candidate byte-identical
to any legacy file → `CANDIDATE_IDENTICAL_TO_REFERENCE` → INCONCLUSIVE (QGIS and GDAL-Python never emit byte-identical GeoTIFFs
or GPKGs for the same data; an identical file means it was copied). **Hard-coded answers** (proposed to 06, Q6): numeric
literals with ≥ 6 significant digits in `pipeline/` or `params.yaml` equal (rel 1e-9) to a value in a reference table →
`REFERENCE_VALUE_IN_CODE` → INCONCLUSIVE (10 B5 category b "values not computed from the inputs").

**Allowed exceptions, and how each is declared** (nothing else):

| Exception | Declaration | Effect |
|---|---|---|
| Bridge input (legacy artefact replaces a step, e.g. a manual edit) | graph step `scope: bridge`/`keep_legacy` (02); `decisions.md` `D-n` with reason; dataset reached via `ctx.input` | disposition `BRIDGED`; pipeline rung suffix "partial (n bridged)" (07 §3.4) |
| Step isolation (step N fed legacy N−1) | runner flag `--chain isolated --steps sN`; the runner, not the code, stages `legacy_ro/` (05 D5-9/D5-14); manifest `legacy_feeds[]` | reads of N−1 allowed; reads of N's own reference or anything downstream → `UNDECLARED_READ`; isolated runs never carry a rung above sandbox-validated (07 D7-2) |
| Manual-step artefact in the input folder (`03_INPUT/cantons_lux_edit.gpkg`) | `role: input`, `produced_by: manual` (02) | ordinary input |
| Threshold search against an intermediate | code in `migration/analysis/`; param `source: inferred, fitted_against: <ds>` | step capped at sandbox-validated, `PARAM_FITTED_TO_REFERENCE`, until the user confirms (07 §3.4) |

### (b) Stop gate

Input: `last_assistant_message`, `stop_hook_active`, `cwd` (docs, hooks: Stop fields; the codex plugin's gate reads the same
field, `~/.claude/remote/plugins/…/scripts/stop-review-gate-hook.mjs:50`). Algorithm (≤ 15 s, files only):

1. No `migration/` → allow. Call `ladder.compute(bundle)` (07) on the newest sealed run in `runs/index.jsonl`; no run → rung
   `generated` or `none`.
2. Re-check what 07 needs live: sealed vs live sha256 of `tolerances.yaml`, `params.yaml`, `graph.json`, `pipeline/` tree;
   legacy tree hash vs 01 baseline (including `.aux.xml` files that did not exist at discover, §7 C9); `compare/*.json` present
   for **every** dataset with a `legacy_path` that is in scope (02 D2-5), bound to this run's output hashes; verdicts aggregated
   by the 10 C1 order (FAIL > EVALUATOR_ERROR > BLOCKED > NEEDS_DECISION > INCONCLUSIVE > PASS_*); `report/index.html` and
   `evidence.json` exist with this `run_id` and are newer than the last compare; no unacked user graph edits (02 D2-14); no open
   high-impact question; for target-ready: `target.json` bound to the same `code:`/`lock:` ids.
3. Claim detection on the last message, case-insensitive, ignoring matches with `not|n't|un-|without|before|until` in the
   preceding 3 tokens and text inside the quoted status line:
   `\b(done|complete[ds]?|finished|works|verified|validated|parity (pass\w*|achieved|confirmed)|(all|every) (checks?|steps?|outputs?|pairs?|tests?) (pass\w*|match\w*)|match(es)? (the )?legacy|identical to (the )?legacy|reference-verified|target-ready|production[- ]ready|ready to (deploy|ship))\b`.
   Each phrase maps to a minimum rung: "done/complete/finished/works/verified/parity/match legacy" → reference-verified;
   "target-ready/ready to deploy" → target-ready; "validated" or "runs/ran successfully" → sandbox-validated.
4. Block iff a claim's rung > computed rung, or step 2 found drift/missing evidence and any claim is present. Otherwise allow.

Block reason (ASCII, fixed phrase table so the model cannot negotiate):

```
axis-migrate Stop gate: your message says "<quote>" but the computed status is <rung>
(run <run_id>: <p> pass, <f> FAIL, <n> NOT_COMPARED, <i> needs review; drift: <none|TOLERANCE_CHANGED_AFTER_RUN ...>).
First failing step: <step> <reason_code> <diagnostic, e.g. shift_px [1,0]>.
Rewrite the summary: start with the status line below, list every failing or uncompared step, and do not claim more.
Do not edit tolerances, references, known differences or the graph to change this result.
Status line: axis-migrate status: <rung>[ · partial (n bridged)] | run <run_id> | <counts> | report <path>
```

Allowed wording below reference-verified: the status line verbatim; "migrated, not verified"; "code generated"; "ran in the
sandbox, exit 0 (sandbox-validated); parity: N of M datasets pass"; "step X fails <reason>"; any claim negated as in step 3.

### (c) Tolerances, references, runs, decisions: immutable after a run

| Path | Before the first run that recorded its hash | After |
|---|---|---|
| `migration/tolerances.yaml` | Write/Edit allowed; scanner checks each record has `correct_spread` and `nearest_wrong` (10 C2.3) → warn | `ask` (user prompt shows the diff and the run ids it invalidates); deny in headless/eval. Allowed only as a **new record id** (`tol-…-v2`, 10 C2.7) with `reason`; the edited old id → deny. Next run gets a new hash; 07 shows "widened after seeing results" with both values |
| `migration/known-differences.md` | append-only | append-only; a new or edited block containing `owner: human:` → `ask` (07: KD with a human owner turns FAIL into amber ACCEPTED_DIFFERENCE); a model owner is ignored by 07 anyway |
| `migration/decisions.md` | append-only (03 D3-13) | same; corrections via `supersedes:` |
| `migration/runs/**`, `runs/index.jsonl` | only runner/seal scripts write | Write/Edit deny; Bash write verbs deny; seal checksums are authoritative (05 D5-10, 07 §3.5) |
| `graph.json`, `graph-edits.jsonl` | deny Write/Edit always; changes via `graph.py apply` (02 D2-9) | `guard_bash` asks on `graph.py apply` ops that demote a dataset with `legacy_path` from compared to `no_reference`, delete a pair, or set `scope: bridge` on a step whose last verdict is FAIL (04-migrate-skills.md:117) |
| `params.yaml` value with `fitted_against` → `source: answer` | — | `ask` (lifts the 07 cap; must be the user) |

Append-only check (pure function): parse `## (D|KD)-\d+` blocks of the current file; the proposed content (Write: `content`;
Edit/MultiEdit: apply `old_string→new_string` in order to the current text) must contain every existing block byte-identical
and in order. The scripts are the authority: 07 seals copies at run start and compares (`*_CHANGED_AFTER_RUN`), 06 refuses to
compare when the live tolerance hash ≠ sealed (`EVALUATOR_ERROR / TOLERANCE_CHANGED_AFTER_RUN`, 10 C1).

### (d) Silent fallbacks and swallowed exceptions

Scanner S01–S09 (static); runner (05) for the runtime half: no `partial` status (FH-02), stub canary (05 D5-8, FH-03), every
declared output present/non-empty/after start, typed `result.json` written by `axis_boot` not by step code. The step contract
(`run(inputs, outputs, params: Mapping)`, 05 §4) passes `params` as a strict read-only mapping **without** a `.get` method and
raising `KeyError` naming `params.yaml`, so S07 also holds at run time and in Codex.

### (e) Tests that mock the subject

S12 blocks mocks of the subject and of GIS/science libraries; S05 blocks stubs in tests; S11 blocks reference paths in tests
(parity lives only in `compare.py` with hashed tolerances, 10 C2; a test asserting equality with legacy under its own tolerance
would be a second, unhashed tolerance file). All model-written tests are labelled **smoke** in `tests.json` and the report;
a green test never raises a rung above sandbox-validated (FH-26, T8; 07 table "tests.json passes").

### (f) No fabrication of missing inputs

S09 + S10 statically; runner preflight exit 12 `blocked` on a missing or changed input, "never fabricated" (05 D5-17); the
interview turns a missing input into a question (03). `guard_write` denies creating files under the legacy root (§5g), so the
agent cannot "restore" a missing input there either. Eval negative control (PLAN): delete an input → expect exit 12 and a
question, 0 files written under `03_INPUT/`.

### (g) Legacy folder read-only

`guard_write` denies any Write/Edit/MultiEdit/NotebookEdit whose `realpath` is under `legacy_root` (symlinks resolved).
`guard_bash` (shlex parse; `&&`, `;`, `|`, subshells split) denies when a resolved argument is under `legacy_root` and the verb
is one of: `rm rmdir mv cp(dest) ln touch chmod chown truncate tee dd(of=) install rsync(dest) sed -i perl -i`, redirections
`> >>`, `git checkout/restore/clean/reset/stash/rm/mv`, `ogr2ogr`/`gdal_translate`/`gdalwarp` (dest), `gdal_edit.py`,
`gdaladdo`, `gdalinfo -stats`, `qgis_process` with an output under the root; also denies `python …migration/pipeline/…`,
`python -m pipeline…` and `uv run … pipeline` not launched through `run_local.py`/`run_e2b.py` (unledgered runs). Unparseable
commands that mention `legacy_root` and a write verb → deny with "use a script under migration/analysis". `python -c` writes
are not decidable: the 01 tree hash (re-checked by the runner preflight, compare and the Stop gate) is the authority →
`LEGACY_REF_CHANGED` (07).

## 6 Acceptance tests (`tests/hooks/`; real stdin payloads captured from Claude Code 2.1.296)

Scanner: one fixture pair per rule in `tests/hooks/fixtures/S<nn>_{fires,clean}.py` (the cases in the §4 test column), plus
corpus tests: (1) 0 block findings on the 10 clean scripts; (2) on the archive corpus S01 ≥ 120 files, S03 ≥ 70, S07 ≥ 150,
S12 ≥ 30 (regression floor from today's counts); (3) a rule that raises → hook exit 2 and `scan.json` `rule_error` (fail closed).

| Id | Must fire (positive) | Must not fire (negative) |
|---|---|---|
| G1 legacy write | Write `fixtures/lux-exposure-legacy/04_FINAL/x.tif` → deny; Bash `sed -i s/a/b/ …/NOTES.txt` → deny; Bash `gdalinfo -stats …/05_TEMP/03_pop_res.tif` → deny | Read of the same file; `gdalinfo` without `-stats` |
| G2 PAM | after H1, agent Bash `gdalinfo -stats` on a scratch copy creates no `.aux.xml` | — |
| G3 runs sealed | Edit `runs/<id>/manifest.json` → deny; Bash `rm -rf migration/runs/<id>` → deny | `run_local.py` writing a new run |
| G4 tolerance | after a sealed run: Edit value of `tol-x-v1` → ask (interactive) / deny (`claude -p`); compare refuses with `TOLERANCE_CHANGED_AFTER_RUN` | before any run: edit allowed; after run: append `tol-x-v2` with reason → ask, then a new run is required |
| G5 append-only | Edit that changes text inside `## D-3` → deny | append `## D-9` with `supersedes: D-3` |
| G6 KD owner | append KD with `owner: human:najah` → ask | KD with `owner: agent` (allowed, ignored by 07) |
| G7 graph | Write `graph.json` → deny; `graph.py apply set_scope s02 bridge` after s02 FAIL → ask | `graph.py apply` adding a question |
| G8 read ledger | trap pipeline step opening `05_TEMP/03_pop_res.tif` via rasterio → exit 11 `UNDECLARED_READ`; same via `open()` → same | isolated run of s03 reading `05_TEMP/02_res_mask.tif` (declared feed) |
| G9 copy answer | `cp 04_FINAL/lux_res_exposure_2020.tif runs/…/outputs/…` (bypassing hook in test) → `OUTPUT_NOT_PRODUCED_BY_RUN` and `CANDIDATE_IDENTICAL_TO_REFERENCE` | normal run output |
| G10 Stop | sandbox-validated bundle + message "Migration complete, parity achieved" → block with status line; 3rd consecutive block → allow + user `systemMessage` | same bundle + "Migrated, not verified: s02 fails RASTER_TRANSFORM_MISMATCH"; interview turn asking a question; reference-verified bundle + "all checks pass" |
| G11 Stop drift | reference-verified bundle, live `tolerances.yaml` edited → "verified" blocked | untouched bundle |
| G12 fail closed | `guard_write.py` given malformed stdin → exit 2; H6 with an exception in a rule → blocks | `graph_changed.py` with malformed jsonl → exit 0, note printed (02 T-hook-1) |
| G13 idle | any hook in a repo without `migration/` → exit 0, no output, < 100 ms | — |
| G14 hidden (eval) | with `AXIS_MIGRATE_HIDDEN_GLOBS`: Read `lux-exposure-legacy.PROVENANCE.md` → deny | same Read without the variable |

Hook mechanics (assumptions from the docs, each a test before Thursday): HM1 PreToolUse deny holds under bypassPermissions
(docs: deny rules apply in every mode); HM2 `ask` from a hook prompts under bypassPermissions (not stated in docs); HM3 PostToolUse
`decision: block` reason reaches the model; HM4 `onFailure: block` turns a crash and a timeout into a block; HM5 `$CLAUDE_ENV_FILE`
from SessionStart reaches later Bash calls; HM6 plugin hooks fire alongside user hooks; HM7 Stop input carries
`last_assistant_message` and `stop_hook_active`.

## 7 Contradictions resolved

| # | Contradiction | Resolution |
|---|---|---|
| C1 | PLAN: the agent reads legacy outputs and tests thresholds against intermediates vs "generated code may not read legacy outputs" | By actor (D8-6); fitted params cap the step (07) |
| C2 | 05 FH-01 "`__main__` must exit non-zero" vs Python already exits 1 on uncaught errors | Dropped; S01–S03 + runner |
| C3 | 04 H-10 broad except = warn (medium FP) vs 05 C6 = block | Block, with S01 limited to handlers lacking raise/exit: 0 hits on clean corpora |
| C4 | 05 T9/FH-35 "audit hook on `open`" as the input manifest vs GDAL bypass | strace (05 D5-9); audit hook only as macOS fallback, capped |
| C5 | 08§9.7 "block writing 'verified' into docs" vs docs that must say "not verified" | Gate the final message's claim only; the report's wording comes from 07's phrase table |
| C6 | Tolerances fixed before the run (PLAN, 10 C2.6) vs presets chosen after results (08§8.5) | After a run: new versioned record + user `ask` + new run; shown as widened |
| C7 | 10 C1 "nothing moves a FAIL up" vs 07 ACCEPTED_DIFFERENCE from a KD | The compare state stays FAIL; amber disposition only with a human owner obtained through `ask` |
| C8 | "Fail closed" (08§9.7) vs a crashing guard wedging the session | Guards block on failure; informational hooks fail open; Stop has a 3-block cap that reports to the user |
| C9 | 07 line 72 excludes `.aux.xml` from the legacy tree hash vs GDAL writing PAM sidecars into legacy folders (measured) | Exclude only sidecars present at discover; a **new** `.aux.xml` = `LEGACY_REF_CHANGED` (a PAM file can carry statistics and metadata that alter later reads; nodata override inferred, to verify) |
| C10 | 02 §B "keep `workflow-orchestrator` reminders" vs no check behind keyword reminders | Not kept; UserPromptSubmit only announces graph edits |
| C11 | Step isolation feeds legacy data vs "never read legacy" | Runner-staged, manifest-recorded, never above sandbox-validated |

## 8 Open questions for the founder

1. **Tolerance/KD approvals:** `ask` interactively and deny in headless evals — acceptable? (HM2 may show `ask` is skipped
   under bypassPermissions on the dev server; then the fallback is deny + "ask the user in chat, then the user edits the file").
2. **macOS local runs:** no strace. Cap at sandbox-validated, or require E2B for reference-verified on macOS?
3. **Codex:** `plugin_hooks` is removed in codex-cli 0.144.4. Ship an opt-in `install-codex-hooks` that writes project-level
   Codex hooks, or rely on scripts only (every guard here has a script half)?
4. Emergency switch `AXIS_MIGRATE_GUARDS=off` (set by the user at launch, not reachable from Bash): allowed if every report
   and status line then says "guards disabled" and the rung is capped at sandbox-validated?
5. `REFERENCE_VALUE_IN_CODE` (≥ 6 significant digits): ship for the demo or after, once the evals show its noise?
6. Should S12 block, or only label tests as smoke? (Blocking costs turns; the verdict never uses tests.)
7. Demo: show one guard firing live (e.g. the tolerance edit after a FAIL being refused) as the "honest status" moment?

## 9 Cross-section contract

**I need:** 01 — `inventory.json` with `legacy_root`, `reference_candidate[]`, the file-hash baseline (incl. existing
`.aux.xml`). 02 — `graph.json` datasets `{id, role input|intermediate|final, legacy_path, produced_by: manual?}`, step
`scope migrate|keep_legacy|bridge|out_of_scope`, `graph.py apply` op names for the G7 ask list, `acks.agent_seq`. 03 —
`decisions.md`/`known-differences.md` blocks headed `## D-n`/`## KD-n`; params `{value, source evidence|answer|assumption|inferred, fitted_against?}`.
04 — step modules use only `ctx.input/output/param` (or `run(inputs, outputs, params)`), no path literals; analysis code in
`migration/analysis/`. 05 — runner calls `scan.py --all` and refuses on any block finding (exit 11 `SCAN_BLOCKED`), strace
ledger, `GDAL_PAM_ENABLED=NO`, strict params mapping, write events per output. 06 — reason codes `OUTPUT_NOT_PRODUCED_BY_RUN`,
`CANDIDATE_IDENTICAL_TO_REFERENCE`, `REFERENCE_VALUE_IN_CODE` (Q5), refusal on tolerance drift. 07 — `ladder.compute(bundle)`
importable with stdlib only, < 5 s on the Lux bundle, and the status-line phrase. Eval harness — `AXIS_MIGRATE_HIDDEN_GLOBS`, headless mode flag.

**I provide:** `hooks/hooks.json`; `scan.py` (`--hook`, `--all`, JSON `scan.json {rule, sev, file, line, msg, evidence}`, exit
0 clean / 2 block / 3 rule error); `protected.json`; the claim regex and phrase→rung map; append-only checker; fixtures G1–G14
and S01–S18; reason codes `SCAN_BLOCKED`, `UNLEDGERED_RUN`, `CLAIM_ABOVE_RUNG`.
