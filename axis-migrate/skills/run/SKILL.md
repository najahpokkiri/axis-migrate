---
name: run
description: Execute the migrated pipeline (migration/pipeline/) step by step in the one locked environment and write a sealed, immutable run directory with a read/write ledger. Use for every execution of pipeline code — isolated per-step runs while migrating, the chained run that verify grades, and the reference-env control run. Never run pipeline modules any other way.
---

# Run

`run_local.py` is the only way pipeline code executes. Each step runs in its own process under `strace`
(read and write ledger), with real libraries from the lock, a stub canary, PROJ network off and deterministic
GDAL settings. The result is `migration/runs/<run_id>/` — evidence of execution, never a verdict.

```
$PLUGIN/bin/axpy $PLUGIN/skills/run/run_local.py migration [--chain chained|isolated] [--steps a,b]
                 [--env pipeline|refenv] [--resume-from <run_id>] [--json]
$PLUGIN/bin/axpy $PLUGIN/skills/run/run_local.py migration --vendor-runtime     # once, before the first run
$PLUGIN/bin/axpy $PLUGIN/skills/run/run_local.py migration --check-sealed <run_id>
```

## Before the first run
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
| 11 | contract violation | read `reason_codes` in `manifest.json`: `UNDECLARED_READ` (the step opened a file it did not declare — read it through `ctx`, never legacy outputs), `STUB_DETECTED` (remove the stub/mock), `SCAN_BLOCKED` (fix the finding in `verify/scan.json`), `OUTPUT_MISSING`/`OUTPUT_EMPTY` (the step did not write a declared output), `OUTPUT_NOT_IN_WRITE_LEDGER` (an output appeared without the step writing it — never copy files into place), `UNDECLARED_WRITE` |
| 12 | blocked: input missing/changed, or a step raised `ParamMissing`/`InputMissing` | stop and ask the user (interview); never fabricate, download or substitute data; never add a default |
| 13 | blocked: environment (`UV_MISSING`, `FREEZE_MISMATCH`, `ENV_MAJOR_MISMATCH`, `PROJ_NETWORK_ON`, `TEMPLATE_LOCK_MISMATCH`, `UPLOAD_NOT_CONFIRMED`, `E2B_KEY_MISSING`) | report it; do not install anything or edit the lock |
| 14 | timed out | report; resume with `--resume-from` once the cause is fixed |
| 15 | immutability (run dir exists, sealed run touched) | start a new run; never reuse, edit or delete a run dir |
| 70 | runner bug | report; never treat as success |

There is no partial status: 3 of 4 steps done is `failed`.

## When a parity check fails
First control: `--env refenv` — the same snapshot in the legacy reference environment (QGIS env). The manifest's
`env_control` says which outputs are byte-identical to the pipeline-env run. Identical → the environment is not
the cause; fix the code. A refenv run never earns a rung.

## E2B (opt-in, explicit consent)
Local is the default. To run in an E2B sandbox, first ask the user with AskUserQuestion, showing the upload list
(run once without `--confirm-upload`: it prints every file, size and hash and stops with exit 13
`UPLOAD_NOT_CONFIRMED`). Only on a yes:
```
set -a; . ~/.config/axis-migrate/secrets.env; set +a      # puts E2B_API_KEY in the environment; never cat the file
$PLUGIN/bin/axpy $PLUGIN/skills/run/run_e2b.py migration --confirm-upload [same flags as run_local]
```
Uploads only staged declared inputs (+ legacy feeds when isolated) and the snapshot; no internet in the sandbox;
the template must match the local lock (`TEMPLATE_LOCK_MISMATCH` otherwise); the sandbox is always destroyed.
Exit codes and the run directory are the same as local.

## Rules
- Runs are immutable: everything except `verify/` is read-only after the run; checksums in `checksums.sha256`.
- Never run the pipeline directly (`python -m axis_pipeline`, `python pipeline/sNN.py`, `uv run …`): a hook denies
  it (`UNLEDGERED_RUN`) and such a run is not evidence.
- Quote run results only from `manifest.json` (`status`, `exit_code`, `reason_codes`), never from prose.
- Where to look: `manifest.json` (steps, outputs, `writes_ledger`, `reads.undeclared`, env), `logs/<step>.*`,
  `verify/scan.json`, `verify/tests.json`.
