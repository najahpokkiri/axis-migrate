# axis-migrate

A Claude Code plugin that migrates a messy legacy GIS project (PyQGIS, ArcPy, notebooks, Excel, hand edits) to an
open-source geopandas/rasterio pipeline and **proves parity per step** against the legacy run's own intermediates and
finals — with deterministic checks, plots the agent must look at, and a status that only a script computes.

## Install

```
/plugin install axis-migrate --marketplace najahpokkiri/axis-migrate
```
(private repository; or locally: `claude --plugin-dir ./axis-migrate`).
Requirements: Linux or macOS, `uv` on PATH (the plugin builds one pinned environment from `skills/run/env/uv.lock`
on first use; no other installs at run time), `strace` on Linux for the read ledger (without it a run is capped at
sandbox-validated). Optional: an E2B API key for sandboxed runs (`run_e2b.py`, explicit upload consent per run).
Codex: script halves only, see `CODEX.md`.

## Use

```
/axis-migrate:migrate ./my-legacy-project
```
Run it from the folder that contains the legacy project; everything is written to `./migration/`, the legacy folder
is never written. The agent: scans and maps the inputs → builds the workflow graph (editable in the browser page it
starts) → resolves every output-changing parameter from the files with citations → asks only what the files cannot
answer (one question at a time) → plans each step and asks for one approval → writes one step at a time, runs it in
isolation and verifies it → runs the whole pipeline from raw inputs → compares every intermediate and final with the
legacy files, draws legacy | new | difference maps and reviews them → seals the evidence and renders
`migration/report/index.html` → optionally writes a Databricks Asset Bundle and dry-runs it (never deploys).

## Honest status

`skills/report/ladder.py` computes the rung from the sealed, hash-checked evidence:
`generated → sandbox-validated → reference-verified → target-ready`; `deployed` and above are never emitted.
Default comparison is exact. A tolerance exists only as a record with two measured numbers (spread of a correct
implementation < threshold < nearest wrong recipe), **signed by a person**: the agent proposes them in
`migration/tolerances.proposed.yaml` and lists everything awaiting a signature in `migration/SIGN-OFF.md`. Until a
person signs, the affected checks stay NEEDS_DECISION and the rung stays below reference-verified.

## Layout

`commands/migrate.md` (entry) · `skills/{discover,interview,migrate,run,verify,report,target}/` (SKILL.md + scripts) ·
`skills/guard/` (code scanner, protected paths, claim rules) · `hooks/` (Claude Code guards; each has a script half) ·
`lib/axis_common.py` (shared stdlib helpers) · `bin/axpy` (the pinned interpreter) · `run_tests.sh`.
