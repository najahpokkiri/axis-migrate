---
name: run
description: Sub-step of the axis-migrate migrate flow. Use for every execution of migration/pipeline/ code (isolated steps, the chained run, the control run) as sealed, ledgered runs in the pinned environment. Never run pipeline modules another way.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Run

`run_local.py` is the only way pipeline code executes. Each step runs in its own process under `strace`
(read and write ledger), with real libraries from the lock, a stub canary, PROJ network off and deterministic
GDAL settings. The result is `migration/runs/<run_id>/` — evidence of execution, never a verdict.

```
$PLUGIN/scripts/axpy $PLUGIN/skills/run/run_local.py migration [--chain chained|isolated] [--steps a,b]
                 [--env pipeline|refenv] [--resume-from <run_id>] [--json]
$PLUGIN/scripts/axpy $PLUGIN/skills/run/run_local.py migration --vendor-runtime     # once, before the first run
$PLUGIN/scripts/axpy $PLUGIN/skills/run/run_local.py migration --check-sealed <run_id>
```

## Before the first run
- `$PLUGIN/scripts/axpy --doctor` checks uv, the environment cache (outside the plugin: `${CLAUDE_PLUGIN_DATA}` when
  set, which the Bash tool never has, so normally `~/.cache/axis-migrate/env-<lock8>`; the cache line names the rule),
  the locked env, strace, a local port, E2B (`e2b`), the reference env (`refenv`) and the highest rung this machine can
  reach (`rung`). Without strace (macOS) the read and write ledger is `audit+cpl_debug` (Python audit hook + GDAL
  CPL_DEBUG): runs still refuse undeclared reads, and the rung is capped at sandbox-validated
  (`read_ledger_rung_cap` in the manifest); E2B runs (below) are the way past that cap.
- `--vendor-runtime` copies `_axis_runtime.py`, `__main__.py` (and `__init__.py` if absent) into `pipeline/`.
  Never edit those files: a changed copy stops the run (`RUNTIME_MODIFIED`, exit 11). You write only `sNN_*.py` /
  `mNN_*.py` with `STEP_ID, INPUTS, OUTPUTS, PARAMS` equal to the graph and `def run(ctx)`.
- All I/O goes through `ctx` (`read_raster`, `write_raster(grid=, dtype=, nodata=)`, `read_vector`, `write_vector`,
  `open_workbook`, `save_workbook`, `write_csv`, `param`). No path literals, no `sys.exit`, no broad `except`.

## Isolated vs chained
- **Isolated** (`--chain isolated --steps sNN_x`): while writing a step. Upstream intermediates are the legacy
  files, copied read-only into `stage/legacy_ro/`; the step never sees its own reference or any legacy final
  output. Use it to localise a defect to one step. It never earns a rung.
- **Chained** (default, all in-scope steps from raw inputs): the run verify grades. Only a chained,
  succeeded, lock-matched run with a clean ledger can support `sandbox-validated` (ladder.py decides).
- `--resume-from <run_id>` reuses a parent's succeeded steps only when code, params, lock and input hashes are
  identical (after a timeout, for instance).

## Exit codes — what to do
| Exit | Meaning | Your next action |
|---|---|---|
| 0 | succeeded | run verify (`compare.py run --run <dir>`) |
| 10 | a step failed (exception, `STEP_CALLED_EXIT`, contract mismatch) | repair loop: read `logs/<step>.result.json` (`exception.innermost_pipeline_frame`) and `logs/<step>.stderr`; fix only that step module |
| 11 | contract violation | read `reason_codes` in `manifest.json` (`LEDGER_BLIND`: the ledger missed a read — report it, never work around it): `UNDECLARED_READ` (the step opened a file it did not declare — read it through `ctx`, never legacy outputs), `STUB_DETECTED` (remove the stub/mock), `SCAN_BLOCKED` (fix the finding in `verify/scan.json`), `OUTPUT_MISSING`/`OUTPUT_EMPTY` (the step did not write a declared output), `OUTPUT_NOT_IN_WRITE_LEDGER` (an output appeared without the step writing it — never copy files into place), `UNDECLARED_WRITE` |
| 12 | blocked: input missing/changed, or a step raised `ParamMissing`/`InputMissing` | stop and ask the user (interview); never fabricate, download or substitute data; never add a default |
| 13 | blocked: environment (`UV_MISSING`, `FREEZE_MISMATCH`, `ENV_MAJOR_MISMATCH`, `PROJ_NETWORK_ON`, `REFENV_MISSING`, `TEMPLATE_LOCK_MISMATCH`, `TEMPLATE_NOT_FOUND:<name>`, `UPLOAD_NOT_CONFIRMED`, `UPLOAD_TOO_LARGE`, `E2B_KEY_MISSING`, `E2B_KEY_REJECTED`, `SECRETS_FILE_MODE`, `E2B_CREATE_FAILED`) | report it; do not install anything or edit the lock. `REFENV_MISSING`: no reference env on this machine (see below). `TEMPLATE_NOT_FOUND`: the template name is wrong or not visible to the key's team; `UPLOAD_TOO_LARGE`: ask before raising `--upload-cap-mb`; `E2B_KEY_*`/`SECRETS_FILE_MODE`: the person fixes the key (`scripts/axpy --configure e2b` in their own terminal, `chmod 600`) — never ask for the key in chat |
| 14 | timed out (`STEP_TIMED_OUT:<step>`, `BUDGET_EXHAUSTED_BEFORE:<step>` in E2B) | a step's default limit is 600 s; when a step legitimately needs longer, raise that step's `timeout_s` with `graph.py apply` (`{"type": "set_field", "id": "<step>", "field": "timeout_s", "value": 1800}`), then start a new run (`--resume-from` needs identical hashes, so it applies only when the graph contract did not change) |
| 15 | immutability (run dir exists, sealed run touched) | start a new run; never reuse, edit or delete a run dir |
| 70 | runner bug | report; never treat as success |

There is no partial status: 3 of 4 steps done is `failed`.

## When a parity check fails
If `axpy --doctor` reports a reference env (`refenv OK`), the first control is `--env refenv`: the same snapshot in
the legacy reference environment (QGIS env). The manifest's `env_control` says which outputs are byte-identical to
the pipeline-env run. Identical → the environment is not the cause; fix the code. A refenv run never earns a rung.
Otherwise (`refenv WARN`) the control is unavailable — go straight to code repair; `run_local.py --env refenv` would
stop with exit 13 `REFENV_MISSING`. The reference env is the legacy QGIS environment's python, found at
`AXIS_REFENV_PYTHON` (default `~/micromamba/envs/qgis/bin/python`); `run_local.py --refenv-python <path>` overrides it
for one run.

## E2B (opt-in, explicit consent)
Local is the default. E2B runs each step under strace inside a Linux sandbox (ledger `e2b-physical+strace`), which is
how a machine without strace (macOS) gets past the sandbox-validated cap. Use it when the person chose `e2b` at the
first-run question (`axpy --doctor --json`: `sandbox_choice`) and doctor's `e2b` line is OK.
- **The key.** `run_e2b.py` loads it itself: `E2B_API_KEY` in the environment, else the owner-only (`chmod 600`)
  `secrets.env` in the plugin data folder (Claude Code: `/plugin configure axis-migrate`, field `e2b_api_key`) or
  `~/.config/axis-migrate/secrets.env` (written by the person with `scripts/axpy --configure e2b` in their own
  terminal). Never source, read, print or ask for it; never pass it on a command line.
- **Per-run consent.** Before every E2B run, ask with AskUserQuestion, showing the upload list (run once without
  `--confirm-upload`: it prints every file, size and hash, the caps, and stops with exit 13 `UPLOAD_NOT_CONFIRMED`).
  Codex: `request_user_input_async` when it exists, else plain-text numbered options, recommended first, plus "Don't
  know"; stop and wait (an elapsed wait is never an answer). Only on a yes:
```
$PLUGIN/scripts/axpy $PLUGIN/skills/run/run_e2b.py migration --confirm-upload [same flags as run_local]
                 [--upload-cap-mb 200] [--sandbox-timeout-s 3600] [--template <slug>/axis-geo-migrate-<lock8>]
```
- Uploads only staged declared inputs (+ legacy feeds when isolated) and the snapshot; no internet in the sandbox; the
  key stays on this machine; the sandbox is always destroyed. Exit codes and the run directory are the same as local.
- **Template.** Default: the public `<slug>/axis-geo-migrate-<lock8>` (4 vCPU / 8 GB) for this plugin's lock, or the
  person's own build for that lock. It must match the local lock exactly (lock sha, installed packages, native
  libraries, Python), else `TEMPLATE_LOCK_MISMATCH`; a package outside the lock is refused by design, never worked
  around. A changed lock (a fork) needs `e2b/build_template.py --from <public base>` in the person's own E2B team.
- **Limits.** Upload cap 200 MB and sandbox lifetime 3600 s by default (flags above; E2B Hobby allows at most 1 h per
  sandbox, 8 vCPU / 8 GiB, and has a one-time $100 credit). Step timeouts are deducted from the lifetime
  (`BUDGET_EXHAUSTED_BEFORE:<step>`, exit 14).

## Rules
- Runs are immutable: everything except `verify/` is read-only after the run; checksums in `checksums.sha256`.
- Never run the pipeline directly (`python -m axis_pipeline`, `python pipeline/sNN.py`, `uv run …`): a hook denies
  it (`UNLEDGERED_RUN`) and such a run is not evidence.
- Quote run results only from `manifest.json` (`status`, `exit_code`, `reason_codes`), never from prose.
- Where to look: `manifest.json` (steps, outputs, `writes_ledger`, `reads.undeclared`, env), `logs/<step>.*`,
  `verify/scan.json`, `verify/tests.json`.
