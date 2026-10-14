# axis-migrate

A plugin for agent harnesses (Claude Code and Codex, same hooks and guards; see `CODEX.md`) that moves a legacy, GUI-based
desktop GIS project — QGIS projects and models, ArcPy/PyQGIS scripts, notebooks, Excel, hand edits stitched together over
years — into a reproducible Python codebase and onto your cloud platform (Databricks today). It verifies every step
against the legacy run's own intermediates and final outputs and reports one status computed from evidence, never
claimed by the agent. The output is a codebase plus a cloud job, not snippets.

Inside, the new codebase uses open-source GIS libraries (geopandas, rasterio, GDAL).

## Install

**Claude desktop app (Code tab)** — the intended harness: the graph editor, input maps, plots and the report open in
the app's side pane.
```
/plugin marketplace add najahpokkiri/axis-migrate        # or a local path to this repository
/plugin install axis-migrate@axis-migrate-dev
```
Success looks like: `/axis-migrate:migrate` appears in the slash-command list, and the first run prints the preflight
(`bin/axpy --doctor`: uv found, cache writable, local port bind OK, read ledger strace or audit+cpl_debug) before it
scans.

**Terminal Claude Code**: the same two commands, or `claude --plugin-dir ./axis-migrate` from a checkout; pages are then
given as local links.

**Codex (desktop app or CLI)** — from a checkout of this repository:
```
codex plugin marketplace add <path to this repository>
codex plugin add axis-migrate@axis-migrate-dev
```
or open `codex://plugins/axis-migrate?marketplacePath=<url-encoded absolute path of .agents/plugins/marketplace.json>`
(print it from the repository root with `python3 -c 'import os,urllib.parse as u;print("codex://plugins/axis-migrate?marketplacePath="+u.quote(os.path.abspath(".agents/plugins/marketplace.json"),safe=""))'`).
Then once: `/hooks`, trust the axis-migrate hooks, start a new session and type `$axis-migrate:start`. Success looks
like: the start skill prints `axis-migrate hooks: active`, then the preflight. Approvals and differences: `CODEX.md`.

Requirements: Linux or macOS, `uv` on PATH (the plugin builds one pinned environment from `skills/run/env/uv.lock`
on first use, in `${CLAUDE_PLUGIN_DATA}` or `~/.cache/axis-migrate/`; no other installs at run time), `strace` on Linux for the read
ledger (macOS uses the audit-hook + GDAL-debug ledger and caps runs at sandbox-validated). Optional: an E2B API key for
sandboxed runs (`run_e2b.py`, explicit upload consent per run).

## Use

```
/axis-migrate:migrate ./my-legacy-project
```
Run it from the folder that contains the legacy project; everything is written to `./migration/`, the legacy folder is
never written. The agent: scans and maps the inputs → builds the workflow graph (editable in a page that opens in the
Claude desktop app's side pane, or a browser link in Terminal) → resolves every output-changing parameter from the
files with citations → asks only what the files cannot answer (one question at a time) → plans each step and asks for
one approval → writes one step at a time, runs it in isolation and verifies it → runs the whole pipeline from raw
inputs → compares every intermediate and final with the legacy files, draws legacy | new | difference maps and reviews
them → seals the evidence and renders `migration/report/index.html` → optionally writes a Databricks Asset Bundle and
dry-runs it (never deploys).

## Honest status

`skills/report/ladder.py` computes the rung from the sealed, hash-checked evidence:
`generated → sandbox-validated → reference-verified → target-ready`; `deployed` and above are never emitted.
Default comparison is exact. A tolerance exists only as a record with two measured numbers (spread of a correct
implementation < threshold < nearest wrong recipe), **signed by a person**: the agent proposes them in
`migration/tolerances.proposed.yaml` and lists everything awaiting a signature in `migration/SIGN-OFF.md`. Until a
person signs, the affected checks stay NEEDS_DECISION and the rung stays below reference-verified.

## Scope (today)

- **Reads:** Python scripts (PyQGIS, ArcPy, plain), Jupyter notebooks, QGIS projects (`.qgz`/`.qgs`) and QGIS graphical
  models (`.model3`), Excel (formulas and cached values), CSV, rasters, vector files, file geodatabases (listing), notes.
- **Best effort / next:** ArcGIS Pro projects (`.aprx`/`.atbx`: parsed best effort, not yet validated on a real
  project); ModelBuilder toolboxes (`.tbx`) and `.mxd` are listed, not parsed.
- **Targets:** Databricks (Asset Bundle, offline checks and a dry run; never deployed by the plugin). Other platforms
  stop at reference-verified with `TARGET_NOT_SUPPORTED`.

## Layout

`skills/migrate/SKILL.md` (the entry: `/axis-migrate:migrate`) · `skills/{discover,interview,migrate,run,verify,report,target,map}/` ·
`skills/guard/` (code scanner, protected paths, claim rules) · `hooks/` (Claude Code guards; each has a script half) ·
`lib/axis_common.py` (shared stdlib helpers) · `bin/axpy` (the pinned interpreter) · `run_tests.sh`.
