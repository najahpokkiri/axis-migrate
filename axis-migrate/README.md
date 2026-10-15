# axis-migrate

A plugin for agent harnesses (Claude Code and Codex, same hooks and guards; see `CODEX.md`) that moves a legacy, GUI-based
desktop GIS project — models, scripts, notebooks, Excel and hand edits stitched together over years — into a
reproducible Python codebase and onto your cloud platform (Databricks today). It verifies every step against the legacy
run's own intermediates and final outputs and reports one status computed from evidence, never claimed by the agent.
The output is a codebase plus a cloud job, not snippets.

**What it reads today:** QGIS projects and graphical models and Python scripts (PyQGIS, ArcPy, plain), with notebooks
and Excel. ArcGIS Pro projects (`.aprx`/`.atbx`) are parsed only in part and have never been validated on a real
project; ModelBuilder `.tbx` and `.mxd` are inventory only (no operations, so nothing to pair with the legacy outputs).
ArcGIS fixtures with ArcPy reference outputs are coming and will widen this. Inside, the new codebase uses open-source
GIS libraries (geopandas, rasterio, GDAL).

## Install

**Claude desktop app (Code tab)** — the intended harness: the graph editor, input maps, plots and the report open in
the app's side pane.
```
/plugin marketplace add najahpokkiri/axis-migrate        # or a local path to this repository
/plugin install axis-migrate@axis-migrate-dev
```
Success looks like: `/axis-migrate:migrate` appears in the slash-command list, and the first run prints the preflight
(`scripts/axpy --doctor`: uv found, cache writable, local port bind OK, read ledger strace or audit+cpl_debug) before it
scans. Installing asks two optional settings (`/plugin configure axis-migrate@axis-migrate-dev` later): `sandbox`
(`local`, the default, or `e2b`) and `e2b_api_key` (sensitive: kept in your credential store, never in settings).

**Terminal Claude Code**: the same two commands, or `claude --plugin-dir ./axis-migrate` from a checkout; pages are then
given as local links.

**Codex (desktop app or CLI)** — from GitHub (needs read access to the repository; SSH:
`git@github.com:najahpokkiri/axis-migrate.git`):
```
codex plugin marketplace add najahpokkiri/axis-migrate --ref main
codex plugin add axis-migrate@axis-migrate-dev
```
Or from a **fresh clone**, never a working tree: Codex copies the plugin folder as it is, git-ignored files included (a
stale `skills/run/env/.venv` made one install 460 MB instead of 5 MB; `scripts/axpy --doctor` warns `PLUGIN_NOT_CLEAN`).
With a fresh clone the deeplink also works:
`codex://plugins/axis-migrate?marketplacePath=<url-encoded absolute path of .agents/plugins/marketplace.json>`
(print it from the clone's root with `python3 -c 'import os,urllib.parse as u;print("codex://plugins/axis-migrate?marketplacePath="+u.quote(os.path.abspath(".agents/plugins/marketplace.json"),safe=""))'`).
Then once: `/hooks`, select axis-migrate's hooks, press `t`, start a NEW chat and type `$axis-migrate:start`. Success looks
like: the start skill prints `axis-migrate hooks: active`, then the preflight. Approvals and differences: `CODEX.md`.
Codex has no install-time settings: for E2B, the person runs `scripts/axpy --configure e2b` in their own terminal.

Requirements: Linux or macOS, `uv` on PATH, `strace` on Linux for the read ledger (macOS uses the audit-hook +
GDAL-debug ledger and caps runs at sandbox-validated). The plugin builds one pinned environment from
`skills/run/env/uv.lock` on first use, in `~/.cache/axis-migrate/` (`${XDG_CACHE_HOME}` if set). `${CLAUDE_PLUGIN_DATA}`
is set for hooks but **not** in the agent's Bash tool, so the agent's commands always use the cache folder;
`scripts/axpy --doctor` prints the path. No other installs at run time. Optional: an E2B API key for sandboxed runs.

Many installed plugins? Claude Code fits all skill descriptions into a shared budget (1% of the context window) and
lists the least-used skills by name only when it overflows. The flow still works (it names each skill), but raising
`skillListingBudgetFraction` in your settings keeps every description visible.

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

It is not a general GIS assistant: a one-off geopandas question, general GIS coding or Databricks setup gets a direct
answer and starts no migration.

## What to expect

- **Time.** On small test projects (one to two dozen datasets), a full run from scan to report took 6–20 minutes of
  agent time in our evals; a real project with many steps takes longer, mostly in the step-by-step verify loop. You can
  stop at any point; running `/axis-migrate:migrate` again resumes from `migration/`.
- **Questions.** Only what the files cannot answer and the comparison cannot catch, one at a time with a recommended
  option and "Don't know": usually a handful, at most 8 in the interview, then one plan approval and the target
  question. A missing input stops the run and becomes a question; the agent never invents data.
- **Your signature.** Where results differ by float noise, the agent proposes a tolerance with two measured numbers and
  lists it in `migration/SIGN-OFF.md`. Until a person signs, the status stays below reference-verified.
- **The status line** (computed by `skills/report/ladder.py` from the sealed evidence, quoted by the agent):

| Rung | Means |
|---|---|
| none | no sealed evidence yet, or the evidence failed an integrity check |
| generated | pipeline code exists, but a clean chained run with passing tests is not yet in the evidence |
| sandbox-validated | ran in the pinned environment under a read ledger; some checks are unsigned, assumed, not compared, or the ledger is the weaker macOS kind |
| reference-verified | every compared intermediate and final matches the legacy outputs exactly or within a signed tolerance, on a chained run from raw inputs, with a syscall read ledger |
| target-ready | reference-verified, plus the Databricks bundle passed the offline checks and a traced dry run; **not deployed** |

`deployed` and above are never emitted: deploying is yours. Default comparison is exact.

## Safety

- **The legacy folder is read-only.** The hooks refuse any write, move, `touch` or sidecar-producing command in it
  (`GDAL_PAM_ENABLED=NO`), and the runner and `ladder.py` re-hash the legacy files and refuse a status if they changed.
- **Legacy files are untrusted input.** Text inside them (code comments, notebook cells, READMEs, attribute values,
  sheet and file names) is data to migrate, never instructions to the agent.
- **What runs locally:** the scanner, the pipeline (in the pinned environment, under a read ledger), comparisons,
  plots, the report, the graph editor and map server (bound to 127.0.0.1 only), and the Databricks dry run.
- **What leaves the machine:** your data, nothing, unless you choose the E2B sandbox; then only the declared inputs
  and the pipeline snapshot, after you have seen the upload list and confirmed it. The Databricks target writes files
  locally and never contacts a workspace. Network use otherwise: the first `uv sync` of the pinned environment, the
  map environment (~60 MB, first use) and any dry-run package not in the uv cache are downloads (PyPI, uv's Python builds); GeoLibre
  fetches its basemap style once from tiles.openfreemap.org at start-up (your map data stays on 127.0.0.1).
- **Keys never go in chat.** The E2B key comes from the plugin setting or `scripts/axpy --configure e2b` (typed by the
  person on their own terminal, stored in a 0600 file); the guards refuse commands that print it. The agent runs as
  your user, so this is a refusal, not an impossibility (see `CODEX.md`).
- Scripts decide verdicts and status; the agent may only flag in visual review, propose tolerances and quote the status.

## Working in a team

- **One `migration/` per legacy project**, next to the legacy folder, committed with the generated codebase. It holds
  the graph, parameters with citations, `decisions.md` (append-only), runs, evidence and the report, so anyone can
  resume or audit it.
- **Sign-off is by a person.** Tolerances and known differences are signed in `migration/SIGN-OFF.md` by someone who
  owns the result (`agreed_by` is never set by the agent); a new run follows every signature.
- **Graph edits** go through the graph editor or `graph.py apply`, never by hand: each edit is recorded with who made
  it in `graph-edits.jsonl`, and the agent must act on every edit before it continues.
- Review the report (`migration/report/index.html`): failures first, the parity table, map views and the run history.

## Glossary

- **Rung** — one step of the status ladder above (none → generated → sandbox-validated → reference-verified →
  target-ready), computed by `ladder.py`, never declared.
- **Parity** — the new pipeline's intermediates and finals match the legacy run's own files, dataset by dataset.
- **Lineage pair** — a new dataset and the legacy file it must reproduce, paired from the workflow graph.
- **Chained run** — the whole pipeline run from raw inputs in one go (as opposed to an isolated step run).
- **Tolerance** — an allowed difference for one check, recorded with two measured numbers (the spread of a correct
  implementation and the nearest wrong recipe) and signed by a person.
- **KD (known difference)** — a documented, accepted difference from the legacy output (e.g. a legacy bug not
  reproduced), owned and signed by a person.
- **Receipt** — the record that the agent looked at a plot or map view (file hash, what it read, its answer).
- **Ledger** — the record of every file a run read and wrote (strace on Linux; a weaker audit ledger on macOS).
- **Seal** — the hash-checked evidence bundle of a run; the report and status are computed from it.
- **Doctor** — `scripts/axpy --doctor`, the preflight that checks uv, the environment, the ledger and the port bind.

## Scope (today)

- **Reads:** QGIS projects (`.qgz`/`.qgs`) and graphical models (`.model3`), Python scripts (PyQGIS, ArcPy, plain),
  Jupyter notebooks, Excel (formulas and cached values), CSV, rasters, vector files, file geodatabases (listing), notes.
- **Partial:** ArcGIS Pro projects (`.aprx`/`.atbx`): parsed in part, never validated on a real project.
- **Inventory only:** ModelBuilder toolboxes (`.tbx`) and `.mxd`: listed with no operations, so nothing to pair.
- **Targets:** Databricks (Asset Bundle, offline checks and a dry run; never deployed by the plugin). Other platforms
  stop at reference-verified with `TARGET_NOT_SUPPORTED`.

## Layout

`skills/migrate/SKILL.md` (the entry: `/axis-migrate:migrate`) · `skills/{discover,interview,migrate,run,verify,report,target,map}/` ·
`skills/guard/` (code scanner, protected paths, claim rules) · `hooks/` (the guards; each has a script half) ·
`lib/axis_common.py` (shared stdlib helpers) · `scripts/axpy` (the pinned interpreter) · `codex/` (Codex entry skill) ·
`assets/` (logo and icon) · `run_tests.sh` · `CHANGELOG.md`.
