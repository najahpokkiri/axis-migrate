# 05 Run — local and E2B

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Section owner: design subagent 05. Date: 13 Oct 2026. Status: design; no plugin code yet.
Evidence prefixes: `A/` = `~/src/axis-spatial/_archive/`, `BE/` = `A/services/backend/app/`,
`T/` = `A/services/backend/e2b_templates/`, `E/` = `~/src/axis-spatial/e2b/templates/axis-geo-raster/`,
`SDK/` = installed e2b Python SDK 2.20.0 (`/usr/local/lib/python3.12/dist-packages/e2b/`),
`SC/` = `~/src/context/skill-candidates/`, `FX/` = `~/axis-migrate-dev/fixtures/`, `ARENA/` = `~/src/gis-agent-arena/`.
Measurements marked **[measured]** were taken read-only on ts-32gb-1 today (scratch outputs only; no sandbox created).

## 1 Scope and interfaces

The `run` skill executes `migration/pipeline/` step by step, on the user's machine (default) or in one E2B
sandbox (opt-in), and writes one immutable `migration/runs/<run_id>/`. It produces **evidence of execution**
(manifest, logs, hashed outputs). It never produces a parity verdict (that is `verify`, section 06) and never deploys.

```
skills/run/
  SKILL.md
  run_local.py      # host orchestrator, local mode
  run_e2b.py        # host orchestrator, E2B mode (same stage/seal code as local)
  runlib.py         # snapshot, staging, hashing, manifest, exit codes (shared)
  axis_boot.py      # runs INSIDE the step process (local venv or sandbox): env probe, stub check, read ledger, step call
  env/pyproject.toml, env/uv.lock   # the one pinned pipeline environment
```

Reads: `graph.json` (steps; per step `inputs[]`/`outputs[]` dataset ids; datasets with `role`
input|intermediate|final, `legacy_path`, `driver`), `params.yaml`, `pipeline/*.py`, `env/uv.lock`,
`tolerances.yaml` (hash only). Writes only `runs/<run_id>/`.

CLI (both entry points share flags):

```
uv run --project skills/run python run_local.py <project> [--chain chained|isolated] [--steps s03_zonal_population]
                                              [--env pipeline|refenv] [--resume-from <run_id>]
uv run --project skills/run python run_e2b.py   <project> [same flags] --template axis-geo-migrate-<lock8> --confirm-upload
```

Runner exit codes (the agent and the Stop hook read these, never prose):

| Code | Status in manifest | Meaning |
|---|---|---|
| 0 | `succeeded` | every planned step exit 0, contract checks R1–R6 pass |
| 10 | `failed` | a step exited non-zero, was killed, or raised (incl. `SystemExit` inside a step) |
| 11 | `failed` | contract violation: missing/empty/stale/unopenable output, undeclared read, write into a staged input, stub detected |
| 12 | `blocked` | preflight: declared input missing or hash changed; never fabricated (AK-33) |
| 13 | `blocked` | environment refused: lock/freeze mismatch, PROJ network on, wrong template, GDAL major mismatch without `--allow-env-major` |
| 14 | `timed_out` | step or sandbox deadline hit; completed steps kept and resumable |
| 15 | `blocked` | immutability: run dir exists / sealed run touched |
| 70 | `failed` | harness error (runner bug); never maps to success |

There is no `partial` status. A run with 3 of 4 steps done is `failed` with `steps_succeeded: 3` (FH-02).

## 2 Decisions

**D5-1 Local `uv` is the default; E2B is opt-in per run.** Files stay on the user's machine (PLAN "Where things
run"). Finding: `uv` is **not installed** on ts-32gb-1 (`which uv` → none) **[measured]**, and the system Python is
drifted: `/usr/local/lib/python3.12/dist-packages` holds rasterio 1.4.3, geopandas 1.0.1 and a stale
`geopandas-1.1.3.dist-info` that pip reports as invalid metadata **[measured]**. The runner therefore never falls back to
system Python or `pip`; without `uv` it exits 13 with `UV_MISSING` (see Q1).

**D5-2 One lock for local and E2B; no install at run time.** `env/uv.lock` (+ `requires-python ==3.12.*`,
`UV_PYTHON_PREFERENCE=only-managed`, so local and sandbox use the same CPython build). `uv sync --frozen` locally; the
E2B template is built from the same lock. `pip install`, `uv pip`, `subprocess` calls to installers are banned in
step code (scanner) and impossible in the sandbox (no network). Environment changes are edits to the lock → new
`lock_hash` → new run (fixes FH-22: env fixes "returned but never applied"; FH-04; AK-36). Archive precedent: the
`forbidden_patterns` list in `BE/services/managed_template_manifests/axis-geo-validate.json` and the run-time
`pip install` that broke ABI (`BE/sandbox/e2b_sandbox.py:37-44`, `BE/sandbox/sandbox_manager.py:62-69`).

**D5-3 Record every native library copy, not "the GDAL version".** A wheel environment loads several GDAL/PROJ builds in
one process. Today's system env: rasterio → GDAL 3.9.3 / PROJ 9.4.1, pyogrio → GDAL 3.9.1, pyproj → PROJ 9.5.1
**[measured]**. For the versions the reference env uses, the manylinux wheels bundle: rasterio 1.5.2 → GDAL **3.12.2** +
PROJ 9.8.1; pyogrio 0.13.0 → GDAL **3.12.4**; pyproj 3.8.0 → PROJ 9.8.1 (read from wheel `.libs/` names) **[measured]**.
The QGIS reference env (micromamba `qgis`) has one shared GDAL **3.13.3**, PROJ 9.9.0, GEOS 3.14.1, QGIS 4.2.3,
Python 3.14.8, numpy 2.5.4, pandas 3.0.6, geopandas 1.2.0, rasterio 1.5.2, shapely 2.2.0, pyproj 3.8.0, pyogrio 0.13.0
**[measured]**; matches `FX/lux-exposure-legacy.PROVENANCE.md` "Environment". The manifest records each copy
(`rasterio.__gdal_version__`, `pyogrio.__gdal_version_string__`, `pyproj.proj_version_str`, `shapely.geos_version_string`).

**D5-4 GDAL/PROJ difference policy: known and recorded, never hidden.** Minor/patch differences (3.12 vs 3.13) are
allowed and listed in `env_comparison`. A **major** difference (GDAL or PROJ major ≠ reference) blocks (exit 13) unless
the user passes `--allow-env-major`, which is recorded and printed in the report header. Either way, when any
structural or value check fails in verify, the agent's first cheap control is a **reference-env run**
(`--env refenv`): the same snapshot executed with the `qgis` env's Python, which already has geopandas/rasterio/pyogrio
**[measured]**. If pipeline-env and refenv outputs are byte-identical, environment drift is excluded as the cause;
if they differ, the difference is the environment's and is reported as such. A refenv run can never advance the
ladder (its env is not the lock).

**D5-5 PROJ network off, enforced after activation.** The conda activation script forces `PROJ_NETWORK=ON` whenever
`proj-data` is absent (`~/micromamba/envs/qgis/etc/conda/activate.d/proj4-activate.sh:16-21`), overriding the caller,
and `40_run_legacy.sh` uses plain `micromamba run` — so the references were built with **PROJ network ON** (env shows
`PROJ_NETWORK=ON`) **[measured]**. For this fixture it does not matter: EPSG:4326→2169 has only Helmert operations
("LUREF to WGS 84 (3)/(4)", no grids, no unavailable ops) and PROJ 9.5.1 and 9.9.0 give identical coordinates
(77244.695505, 75041.870948) **[measured]**. Rule: the runner sets `PROJ_NETWORK=OFF` *inside* the step process
(`env PROJ_NETWORK=OFF python …` after activation), `axis_boot` calls `pyproj.network.set_network_enabled(False)`
and asserts it; the manifest records the reference env's network state and, per CRS pair used, whether any
candidate operation needs a grid (`TransformerGroup(...).unavailable_operations`).

**D5-6 Exit-code contract.** A step succeeds only if (R1) its process exits 0, (R2) `axis_boot` wrote
`step_result.json` with `ok: true` (boolean, written by the boot wrapper, not by step code — FH-02), (R3) every declared
output exists, is non-empty, has mtime ≥ step start, opens with its declared driver, (R4) no undeclared read, (R5) no
stub detected, (R6) staged inputs unchanged (sha256 before/after). Steps never call `sys.exit`; `axis_boot` treats
`SystemExit` raised inside a step as failure `STEP_CALLED_EXIT` (the old harness caught only `Exception`, so
`sys.exit(0)` escaped: `BE/pipelines/validation/migrated.py:83-87`). Broad `except` without re-raise is a scanner error
(FH-01; AK-32; 240 swallowing handlers in 136/307 archived task files, `SC/05-failure-history.md:14-16`). The status
vocabulary is the July proof's `ExecutionStatus` (`SC/10…md:115`): `succeeded|failed|timed_out|cancelled|blocked`.

**D5-7 One process per step; logs per step.** `python -m axis_boot --step <id> --ctx ctx.json` per step. Stdout/stderr
go to `logs/<step_id>.stdout|.stderr` (and `logging` configured once, MP-34). A crash in s03 cannot leave s04 reading
half-written files, and per-step logs are what the agent gets on failure ("spec plus current error only",
`SC/05…md:263`). Cost: about 0.6 s import per step **[measured]**.

**D5-8 No stubbed libraries: detect and refuse.** The archive's dry run injected a `MetaPathFinder` that returned
fake modules for numpy, pandas, geopandas, rasterio, shapely, pyproj (`BE/sandbox/sandbox_manager.py:84-107,1114-1165`),
whose `__eq__` is always True (`:1144`), faked `subprocess.run` with returncode 0 (`:1180`), turned missing files into
`io.StringIO('')` (`:1189-1196`) and set `success = True` on runtime errors (`:1278`); exit codes defaulted to 0 when
absent (`:948,1041,1237`). Our guard has three layers:
1. Scanner (PostToolUse, section hooks): in non-test code flag `sys.modules[...] =`, `sys.meta_path.insert/append`,
   `importlib.abc.MetaPathFinder|Loader` subclasses, `types.ModuleType` subclasses, `unittest.mock`/`MagicMock`,
   `builtins.open =`, `subprocess.*pip`.
2. Runtime (`axis_boot`, before and after the step): for numpy, pandas, geopandas, rasterio, pyogrio, shapely, pyproj:
   `type(mod) is types.ModuleType`, `mod.__spec__.origin` is a real file under the venv's `site-packages`, the compiled
   extension exists (e.g. `rasterio._base` origin ends in `.so`), version equals the lock; `sys.meta_path` contains
   only stdlib finders plus an allow-list; `unittest.mock` not in `sys.modules`.
3. Canary: `(np.arange(3) == np.arange(3)[::-1]).tolist() == [False, True, False]` (a stub's always-True `__eq__`
   fails), write+read a 2×2 GeoTIFF in `/vsimem/` with rasterio, and a 1-row GPKG round trip with pyogrio.
Any failure → exit 11, `STUB_DETECTED`; such a run can never be `sandbox-validated` (FH-03, T3).

**D5-9 Read ledger: only declared inputs.** A Python audit hook does **not** see GDAL's C-level opens; `strace -f -e
trace=openat` does (it logged `openat(...lux_ppp_2020.tif, O_RDONLY)` for a rasterio read) **[measured]**, at about 2×
wall time (1.7 s → 3.7 s on the lux approximation) **[measured]**. Linux: strace ledger per step. Fallback where strace
is missing (macOS): Python audit hook (`open` events) plus GDAL's `CPL_DEBUG=ON` `GDALOpen(<path>…) succeeds as` lines,
which pyogrio prints to stderr and rasterio routes to the `rasterio._env` logger **[measured]**. Ledger rule: every opened
data path must resolve to the step's staged inputs, its own output dir, the venv or system libraries; anything else
(notably anything under the legacy folder) → exit 11 `UNDECLARED_READ` (FH-35, T9). In E2B the guard is physical:
undeclared files are not uploaded. `bwrap`/user namespaces are blocked here by AppArmor
(`kernel.apparmor_restrict_unprivileged_userns = 1`; `bwrap: setting up uid map: Permission denied`) **[measured]**,
so local read isolation is detection, not prevention.

**D5-10 Fresh, immutable run directories.** `run_id = <UTC yyyymmddThhmmssZ>-<code_hash[:8]>`. The runner refuses an
existing dir (exit 15), never skips a step because an output exists, writes each step into `outputs/<step_id>.tmp/`
and renames to `outputs/<step_id>/` only after R1–R6 pass (atomic, AK-34); a failed step's dir becomes
`outputs/<step_id>.failed/` and is never an input. At the end the runner writes `manifest.json` and
`checksums.sha256`, then `chmod -R a-w` (sealed). A re-run is always a new `run_id` (MP-29: skip-if-exists masked fixes;
HL-27: overwritten runs made quoted numbers drift). The PreToolUse hook refuses Write/Edit under a sealed run.

**D5-11 Run from a content-hashed snapshot.** At start, copy `pipeline/`, `params.yaml`, `graph.json`, `env/pyproject.toml`,
`env/uv.lock` into `runs/<id>/snapshot/`; `code_hash = sha256(json.dumps(sorted([{path, sha256, size}])))` (the July
proof's tree-hash form, `SC/10…md:166`). Steps import from the snapshot only (`PYTHONPATH=snapshot`, cwd = run dir).
At the end the working tree is re-hashed; `worktree_changed_during_run: true` is recorded and shown (MP-30: the job ran
different code than the agent edited). E2B receives the same snapshot tarball and re-verifies the hash inside.

**D5-12 Determinism settings, set by the runner.** `OGR_CURRENT_DATE=2000-01-01T00:00:00Z` makes GPKG output byte-stable:
two writes 1.5 s apart differ without it and are identical with it **[measured]** (the legacy GPKGs differ only in
`gpkg_contents.last_change`, PROVENANCE Check 1). Also `GDAL_PAM_ENABLED=NO` (no `.aux.xml` sidecars; QGIS wrote them),
`PYTHONHASHSEED=0`, `TZ=UTC`, `LC_ALL=C.UTF-8`, `OMP_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1`, `GDAL_NUM_THREADS=1`.
XLSX keeps openpyxl timestamps in `docProps/core.xml`; verify compares XLSX by content. CPU SIMD is recorded
(`numpy.show_runtime()`): this host is AMD EPYC-Rome, AVX2, no AVX-512 **[measured]**; float reductions may differ in the
last bits across hosts, so cross-host equality for reductions is "within declared tolerance", not bytes.

**D5-13 Two chain modes, recorded per run and per step.** `chained` (default): every step reads raw inputs or outputs of
earlier steps **of the same run**; only chained runs can reach `sandbox-validated` and feed the final verdict.
`isolated`: step N reads the **legacy** equivalent of each intermediate it consumes (graph: dataset `legacy_path`,
producer step < N), so a defect is localised to one step (the "first failing step" rule in PLAN §Verify). Legacy
intermediates are **copied** into `runs/<id>/stage/legacy_ro/`, hash-checked against the source, `chmod a-w`; the
legacy folder itself is never opened by a step. Legacy datasets with `role: final` (e.g. `04_FINAL/*`) are never
staged in any mode, and a legacy file is never staged as an output path (the step under test cannot see its answer;
PLAN "Generated code may not read legacy outputs"). Per-step `fed_from` records `input|run|legacy|reused:<run_id>`.

**D5-14 E2B data placement and privacy.** Upload set = staged declared inputs (+ `legacy_ro/` only in isolated mode) +
snapshot tarball + `ctx.json`. Never `04_FINAL`, never undeclared intermediates, never `params` secrets (there are none
by design). Before upload the runner prints the list with sizes and hashes and requires `--confirm-upload` (interactive
via AskUserQuestion). Lux: 4 files, 7.75 MB (`lux_ppp_2020.tif` 2.2 MB, `osm_landuse_lux.gpkg` 4.3 MB,
`cantons_lux_edit.gpkg` 1.25 MB, `value_per_capita.xlsx` 6 KB); the ADM1/ADM2 GeoJSONs (6.6 MB) are not read by any
script (`02_SCRIPTS/0[1-4]*.py` path constants) and are not uploaded unless the migrated graph declares them.
Upload cap 200 MB (archive cap was 50 MB, `BE/pipelines/validation/migrated.py:224`). `Sandbox.create(...,
allow_internet_access=False)` is passed **explicitly**: the SDK default is `True` (`SDK/sandbox_sync/main.py:174,191`).
`envs` is an allow-list (the D5-12 variables plus `PROJ_NETWORK=OFF`); no API key, cloud or storage credential enters
the sandbox. The E2B key is read from `~/.config/axis-migrate/secrets.env` by key name only, passed as `api_key=`, never
logged (existence checked with `test -f` only, today).

**D5-15 Timeouts and resumption.** Sandbox lifetime 600 s set explicitly (SDK default 300 s, `SDK/sandbox/main.py:25`;
per-command default 60 s, `SDK/sandbox_sync/commands/command.py:146`). Per-step timeout from `graph.json`
(`timeout_s`, default 120 s); a deadline guard keeps 60 s for download. Non-zero exits arrive as
`CommandExitException` (`SDK/sandbox/commands/command_handle.py:63`), timeouts as `TimeoutException` — typed, not the
Arena regex on stderr (`ARENA/worker/api/src/e2b-executor.ts:70`). After **each** step the host downloads that step's
logs and outputs and verifies their hashes, so a sandbox death loses at most the running step. Resume =
`--resume-from <run_id>`: new run_id, new sandbox, allowed only if `code_hash`, `params_hash`, `lock_hash` and all input
hashes equal the parent's (the MP-29 sidecar rule); completed steps are copied from the parent with hash check and
marked `reused`, not re-executed. Legacy outputs are never a resume source. Lux never needs this (seconds, D5-18).

**D5-16 Template: a new `axis-geo-migrate-<lock8>`, built from the lock.** None of the existing templates qualifies:
`E/requirements.txt:1-4` (current axis-geo-raster) has no geopandas, pyogrio or openpyxl, pins ranges, apt-installs
Debian `gdal-bin` beside the wheel GDALs (`E/Dockerfile:15-17`, a fourth GDAL copy) and its entrypoint is a COG probe job
(`E/Dockerfile:25`; `E/src/manifest.py:9-14` silently invents a default manifest when none is given); the archived
`axis-geo-lite/-validate/-raster` pin `numpy<2.0`, `pandas<2.3` (`T/axis-geo-validate/requirements.txt:1-2`) and
`--force-reinstall` ranges (`T/*/e2b.Dockerfile:7`); `axis-geo-validate`'s manifest claims `gdalinfo` verified though its
Dockerfile installs no GDAL CLI (`T/axis-geo-validate/e2b.Dockerfile:1-9`; the probe only runs `command -v`,
`T/probe_templates.py:112-121`). New template: uv-managed CPython 3.12, `uv sync --frozen --no-dev`, no apt GDAL,
`axis_boot.py` baked in, `/opt/axis/env.lock.sha256` and `/opt/axis/probe.json` written at build. Build with the archive's
`Template.build` call (`T/build_templates.py:35-44`), 2 vCPU / 2 GB (lux peak RSS 270 MB **[measured]**). The run
re-probes every sandbox and refuses (exit 13) if the probe's freeze hash ≠ the local lock's expected freeze hash.
Building it creates a cloud resource → founder approval (Q2).

**D5-17 Missing input → stop.** Preflight resolves every declared input of every planned step (and, in isolated mode,
every legacy feed) before step 1, hashes it, and exits 12 `INPUT_MISSING:<dataset_id>` on the first gap. No sample data,
no synthetic fallback, no "best attempt" (AK-33; FH-35; contrast `sandbox_manager.py:1189-1196`,
`E/src/manifest.py:9-14`). Inputs are re-hashed after the run (R6); GPKG inputs are hashed by bytes **and** by content,
because opening a GPKG can bump its SQLite header (PROVENANCE Check 1).

**D5-18 Demo-time practicalities.** A single-process approximation of the lux pipeline with real libraries on this host
(clip pop to dissolved cantons, residential filter + area in EPSG:2169 ≥ 2500 m² → 1512 polygons — the legacy count —,
all-touched rasterise, zonal sums, distribute, write) ran in **1.7 s wall, 270 MB peak RSS** (imports 0.58 s; steps
0.25/0.58/0.08/0.06 s) **[measured]**. Estimates: local chained run with four step processes ≈ 4 s, with strace ≈ 8 s;
first `uv sync` on a cold cache 30–90 s (wheel downloads; do it before the demo), warm < 5 s. E2B: create + probe +
7.75 MB up + ≈ 17 MB down (legacy-equivalent outputs: 1.6 MB final + 15 MB intermediates) + ≈ 5 s compute → estimate
20–60 s, an order of magnitude inside 600 s. To be measured on Fri 16 (PLAN schedule). Demo plan: show the local run
live; show E2B live only if Q2 is approved and a rehearsal run is < 60 s; otherwise show the recorded E2B manifest.

**D5-19 Ladder claim.** The run writes `ladder_claim: sandbox-validated` only when: chain = chained, env = pipeline
(lock-matched, freeze hash equal), status = succeeded, no stub, no undeclared read, raw inputs only. Local and E2B both
qualify (the FH-03 lesson is "executed with real geo libraries", `SC/06-old-app-flow.md:341`; location is recorded in
`mode`). Isolated, refenv and `--allow-env-major` runs claim `none`. Confirm with founder (Q3).

## 3 Schemas

`runs/<run_id>/`: `manifest.json`, `checksums.sha256`, `ctx.json`, `snapshot/`, `stage/{inputs,legacy_ro}/`, `outputs/<step_id>/`,
`logs/<step_id>.{stdout,stderr,reads,result.json}`, `env/{probe.json,freeze.txt}`. Verify writes `compare/`, `plots/`
under `runs/<run_id>/verify-<verify_id>/`; the sealed run itself is never modified (section 06 to confirm).

`manifest.json` (`schema: axis-migrate.run-manifest.v1`):

```json
{
  "schema": "axis-migrate.run-manifest.v1",
  "run_id": "20261016T091512Z-3f9c1a7e",
  "project": "lux-exposure-legacy", "plugin_version": "0.1.0", "created_at": "2026-10-16T09:15:12Z", "ended_at": "…",
  "mode": "local",                       // local | e2b | local-refenv
  "chain": "chained",                    // chained | isolated
  "status": "succeeded",                 // succeeded | failed | timed_out | cancelled | blocked
  "exit_code": 0, "reason_codes": [], "steps_planned": 4, "steps_succeeded": 4,   // codes e.g. "STEP_NONZERO_EXIT:s03_…"
  "ladder_claim": "sandbox-validated",   // sandbox-validated | none
  "resumed_from": null,
  "code":   {"code_hash": "sha256:…", "files": [{"path": "pipeline/s01_prepare_population.py", "sha256": "…", "size": 2140}],
             "worktree_hash_at_end": "sha256:…", "worktree_changed_during_run": false},
  "params": {"path": "params.yaml", "sha256": "…"}, "graph": {"sha256": "…"}, "tolerances": {"sha256": "…"},
  "env": {
    "lock_hash": "sha256:…", "freeze_hash": "sha256:…", "freeze_matches_lock": true,
    "python": "3.12.x (uv-managed)", "platform": "linux-x86_64", "cpu": {"model": "AMD EPYC-Rome", "simd": ["AVX2"]},
    "libs":   {"numpy": "…", "pandas": "…", "geopandas": "…", "rasterio": "1.5.2", "pyogrio": "0.13.0",
               "shapely": "…", "pyproj": "3.8.0", "openpyxl": "…"},
    "native": {"rasterio.gdal": "3.12.2", "rasterio.proj": "9.8.1", "pyogrio.gdal": "3.12.4",
               "pyproj.proj": "9.8.1", "shapely.geos": "…"},
    "proj":   {"PROJ_NETWORK": "OFF", "network_enabled": false, "data_dir": "…", "proj_db_sha256": "…",
               "crs_pairs": [{"src": "EPSG:4326", "dst": "EPSG:2169", "grid_ops_unavailable": 0, "op": "Inverse of LUREF to WGS 84 (4) …"}]},
    "runner_env": {"OGR_CURRENT_DATE": "2000-01-01T00:00:00Z", "GDAL_PAM_ENABLED": "NO", "PYTHONHASHSEED": "0", "TZ": "UTC"},
    "stub_check": {"passed": true, "canary": "ok", "meta_path": ["…"]}, "read_ledger": "strace",  // | audit+cpl_debug | e2b-physical
    "e2b": null   // or {template: "axis-geo-migrate-1a2b3c4d", template_id, build_id, sandbox_id, sdk: "2.20.0", timeout_s: 600,
                  //     allow_internet_access: false, sandbox_destroyed: true, uploaded_bytes, upload_confirmed_by}
  },
  "reference_env": {"source": "probe of micromamba env qgis", "qgis": "4.2.3", "gdal": "3.13.3", "proj": "9.9.0", "geos": "3.14.1", "python": "3.14.8", "proj_network": "ON"},
  "env_comparison": [{"key": "gdal", "pipeline": ["3.12.2", "3.12.4"], "reference": "3.13.3", "major_equal": true, "minor_equal": false, "allowed_by": "D5-4"}],
  "inputs": [{"dataset_id": "ds.worldpop_2020", "legacy_path": "03_INPUT/lux_ppp_2020.tif",
              "staged": "stage/inputs/lux_ppp_2020.tif", "bytes": 2215890, "sha256": "…",
              "content_sha256": null, "unchanged_after_run": true, "uploaded": false}],
  "legacy_feeds": [],                    // isolated only: {"dataset_id", "legacy_path", "sha256", "staged", "fed_to": ["s03_…"]}
  "steps": [{
    "step_id": "s01_prepare_population", "module": "snapshot/pipeline/s01_prepare_population.py", "module_sha256": "…",
    "fed_from": {"ds.worldpop_2020": "input", "ds.cantons_edit": "input"},
    "started_at": "…", "ended_at": "…", "duration_s": 0.91, "exit_code": 0, "signal": null, "timed_out": false,
    "status": "succeeded", "reason_codes": [], "peak_rss_mb": 180,
    "logs": {"stdout": "logs/s01_prepare_population.stdout", "stderr": "…", "result": "…", "sha256": {"…": "…"}},
    "reads": {"ledger": "logs/s01_prepare_population.reads", "undeclared": []},
    "outputs": [{"dataset_id": "ds.pop_lux", "path": "outputs/s01_prepare_population/pop_lux.tif", "driver": "GTiff",
                 "bytes": 2199027, "sha256": "…", "opened_ok": true, "mtime_after_start": true}]
  }],
  "violations": [], "sealed": true, "checksums": "checksums.sha256"
}
```

`logs/<step>.result.json` (by `axis_boot`, never step code): `{step_id, ok: bool, exit_code, exception: {type, message,
innermost_frame: "file:line"} | null, outputs: [{dataset_id, path}], stub_check}` — innermost frame, not outermost (FH-20).

## 4 Algorithms

**Step module contract** (section 04 generates it): `def run(inputs: dict[str, Path], outputs: dict[str, Path],
params: Mapping) -> None`; no path literals, no `__file__` for data, no `sys.exit`, raise on any failure.

**`axis_boot.py --step <id> --ctx ctx.json`** (inside the step process):
1. `pyproj.network.set_network_enabled(False)` and assert; configure `logging`; import the libraries; D5-8 runtime
   check + canary; on failure write result `ok:false STUB_DETECTED`, exit 11.
2. Import `snapshot/pipeline/<step_id>.py`; call `run(...)` with staged input paths and `outputs/<id>.tmp/` paths.
3. `except BaseException` (incl. `SystemExit`, `KeyboardInterrupt`): write traceback to stderr, result `ok:false`,
   exit 10. Never swallow.
4. After return: re-run D5-8 check (a step could install a stub mid-run), verify every declared output (exists, size > 0,
   opens with driver), write result `ok:true`, exit 0.

**`run_local.py`**:
1. Preconditions: `uv` present; `uv sync --frozen --project env/`; probe → `env/probe.json`; freeze hash vs lock;
   PROJ network off; GDAL/PROJ major vs reference (D5-4). Fail → exit 13.
2. Allocate `run_id`; `mkdir` without `exist_ok` (exit 15 if exists); snapshot + `code_hash` (D5-11).
3. Plan: topological order from `graph.json`; `--steps` narrows; chained needs all producers in the plan or a
   `--resume-from` parent; isolated maps each consumed intermediate to its legacy file (D5-13).
4. Preflight: resolve and hash inputs and legacy feeds; copy into `stage/`, `chmod a-w`; missing → exit 12.
5. For each step: build `ctx.json`; spawn `strace -f -qq -e trace=openat,creat,rename -o logs/<id>.reads
   uv run --frozen python -m axis_boot …` with the D5-12 env and `timeout_s`; capture stdout/stderr to files; on timeout
   kill the process group → `timed_out`, exit 14. Check R1–R6; pass → rename `.tmp`→final, hash outputs; fail → rename
   `.tmp`→`.failed`, stop (no later step runs on a failed producer), record reason codes.
6. Re-hash staged inputs and the working tree; write manifest, checksums; seal; exit with the table code.

**`run_e2b.py`** (same steps 2–4 and 6 on the host; execution replaced by):
1. Upload consent (D5-14); size cap; read the E2B key by name.
2. `Sandbox.create(template, timeout=600, allow_internet_access=False, envs=ALLOW, metadata={"run_id": …})`.
3. `python -m axis_boot --probe` in the sandbox → compare freeze hash, `/opt/axis/env.lock.sha256` and native versions
   to the local lock's expected values; mismatch → kill, exit 13 `TEMPLATE_LOCK_MISMATCH`.
4. Write one tarball (snapshot + stage + ctx) with `files.write`; extract; verify every sha256 inside; `chmod -R a-w stage`.
5. Per step: remaining = 600 − elapsed − 60; if `timeout_s` > remaining → stop, `timed_out`, reason
   `BUDGET_EXHAUSTED_BEFORE:<step>` (resumable). Else `commands.run(..., timeout=timeout_s)`; catch
   `CommandExitException` → exit code; `TimeoutException` → `timed_out`. Then download logs, result and outputs with
   `files.read(format="bytes")`, verify hashes against `result.json`, place under the local run dir.
6. `finally: sandbox.kill()`; record `sandbox_destroyed`. Read ledger = `e2b-physical` (+ in-sandbox strace if baked in).

## 5 Reuse

| Source | Take | Fix / drop |
|---|---|---|
| `T/build_templates.py:29-57` | `Template(...).from_dockerfile` + `Template.build(cpu, memory)` | Template name carries the lock hash; drop `--force-reinstall` ranges |
| `T/probe_templates.py:94-160` | probe structure (python command, writable paths) | probe versions and native libs, not `command -v` names |
| `BE/pipelines/validation/migrated.py:76-87,224` | env-var path injection idea, JSON-lines events, upload cap, egress off | do not upload fixtures/legacy outputs (`:80`); catch `BaseException`; no PARTIAL (`:103,131,182`) |
| `ARENA/worker/api/src/e2b-executor.ts:23-46,105-107` | recover exit code from the thrown command error; `finally` kill | typed `TimeoutException` instead of stderr regex (`:70`) |
| `BE/sandbox/sandbox_manager.py:1114-1196` | **negative test fixture**: the stub preamble becomes AT-6's input | never reuse as a gate |
| `FX/lux-exposure-legacy.build/41_hash_outputs.sh`, `42_compare_runs.py` | hash listing and content diff for GPKG/XLSX | — |
| July proof tree hash (`SC/10…md:166`) | `sha256(json(sorted [{path, sha256, size}]))` for `code_hash` | — |

## 6 Acceptance tests

| ID | Setup | Expected |
|---|---|---|
| AT-1 | step s03 raises `ValueError` | runner exit 10; `status: failed`; s04 not started; `outputs/s03_….failed/` exists; `steps_succeeded: 2` |
| AT-2 | step wraps body in `try/except Exception: log` and returns without writing its output | exit 11 `OUTPUT_MISSING`; scanner also flags the handler |
| AT-3 | step calls `sys.exit(0)` before writing outputs | exit 10 `STEP_CALLED_EXIT` |
| AT-4 | rename `stage` source `osm_landuse_lux.gpkg` away | exit 12 `INPUT_MISSING:ds.osm_landuse` before any step; no output files; no synthetic data |
| AT-5 | step opens `04_FINAL/lux_res_exposure_2020.tif` by absolute path | exit 11 `UNDECLARED_READ` (strace ledger); in E2B the file is absent → step fails |
| AT-6 | prepend the archived stub preamble (`sandbox_manager.py:1115-1165`) to a step | exit 11 `STUB_DETECTED` via origin check and canary; scanner flags `sys.meta_path.insert` |
| AT-7 | run twice with no change | two run_ids; both sealed; second run never reuses the first's outputs; rasters, CSV and GPKG byte-identical |
| AT-8 | edit a step file during a run | `worktree_changed_during_run: true`; executed `module_sha256` = snapshot's |
| AT-9 | write to a sealed run, or start with an existing run_id | refused, exit 15 |
| AT-10 | `PROJ_NETWORK=ON` exported before the run | step env still OFF; `network_enabled: false` asserted |
| AT-11 | lock edited without `uv sync`, or E2B template from another lock | exit 13 `FREEZE_MISMATCH` / `TEMPLATE_LOCK_MISMATCH` |
| AT-12 | lux chained run local vs E2B | all GeoTIFF and CSV outputs byte-identical; GPKG byte-identical (D5-12); XLSX content-identical; reductions within tolerance if CPUs differ |
| AT-13 | isolated run of s03 | `legacy_feeds` lists `05_TEMP/01_pop_lux.tif` and `02_res_mask.tif` with hashes; `ladder_claim: none`; legacy files unchanged |
| AT-14 | E2B step sleeps past its `timeout_s` | `timed_out`, exit 14; earlier steps' outputs on host with hashes; `--resume-from` reruns only the timed-out step in a new sandbox |
| AT-15 | `--env refenv` on lux; E2B create call (unit test) | GDAL 3.13.3, `ladder_claim: none`, diff vs pipeline env reported; `allow_internet_access=False`, no key in `envs` |

## 7 Contradictions resolved

1. "Pin via uv lock" vs references built with conda GDAL 3.13.3: wheels give GDAL 3.12.x (D5-3). Accept minor drift,
   record it, block on major, use the refenv run as the attribution control (D5-4).
2. Archived templates pin `numpy<2.0` (`T/*/requirements.txt:1`) vs reference numpy 2.5.4: that pin was for a managed
   Databricks runtime (AK-36 calls the generic pin outdated). The pipeline lock uses numpy 2.x; Databricks
   compatibility is a `target-ready` concern (section 10), not a run-time pin.
3. "600 s limit" vs archive manifests (10/20/45 min) and SDK defaults (300 s sandbox, 60 s command): always set both explicitly.
4. "Files stay on the user's machine" vs E2B: opt-in, per-run upload list with consent, inputs only (D5-14).
5. Old E2B harness staged legacy references beside the migrated code (`migrated.py:1-12,80`) vs "generated code may not
   read legacy outputs": comparison runs on the host in verify; only isolation feeds of intermediates are uploaded.
6. Template manifests claimed capabilities their images lacked (`gdalinfo`, D5-16): trust per-sandbox probes. 7. FH T3
   allows stubs for cloud SDK clients: the run component allows none (pipeline steps use no cloud SDKs).
8. "Legacy folder read-only" vs isolation needing legacy intermediates: staged read-only copies (D5-13).
9. Local vs E2B for `sandbox-validated`: either, if real libraries and lock-matched (D5-19), pending Q3.

## 8 Open questions for the founder

- Q1 Install `uv` for user `migrate` on ts-32gb-1 (absent today), and confirm it on the demo laptop?
- Q2 Approve building one E2B template `axis-geo-migrate-<lock8>` (a cloud resource) by Fri 16? Which E2B team/key?
- Q3 Does a local, lock-matched chained run earn `sandbox-validated`, or only E2B?
- Q4 Python 3.12 for the lock (broad wheels) or 3.14 to match the QGIS env (3.14.8)?
- Q5 Upload consent per run, or once per project and dataset hash?
- Q6 "Claude login inside E2B via E2B Secret": not found in readable code (`ARENA/README.md:77-81` uses only
  `E2B_API_KEY`; `eo-agent-arena` is not on this server). Out of scope for `run`; needed only for a "try it" sandbox.
- Q7 Make strace mandatory on Linux (≈2× step time) or keep it on by default with an opt-out?
- Q8 Staging copies inputs into each run (lux 8 MB); above what size do we reference in place with pre/post hashes (proposal: 2 GB)?

## 9 Cross-section contract

What **verify** (06) needs from the run manifest and must enforce:
- Only `status: succeeded`, `chain: chained`, `ladder_claim: sandbox-validated` runs may yield `reference-verified`.
  Per-step verify on a failed or isolated run is allowed for diagnosis and labelled so.
- Pairs come from `steps[].outputs[].dataset_id` ↔ graph `legacy_path`; verify re-hashes each output and refuses if it
  differs from `sha256` in the manifest (binds the parity report to the executed artefact; fixes July A8.4).
- The compare report records `run_id`, `code_hash`, `lock_hash`, `params.sha256`, `tolerances.sha256`.
- `env_comparison` and `reference_env.proj_network` are shown in the report header; a refenv control run is linked by run_id.
- Verify writes under `runs/<run_id>/verify-<id>/`, never into the sealed run.

What **hooks** enforce:
- PostToolUse scanner on `pipeline/**`: D5-8 stub patterns, broad `except` without re-raise, `sys.exit`, installers,
  path literals / `__file__` for data, any reference to the legacy folder or `04_FINAL`, RNG in non-test code.
- PreToolUse: block Write/Edit/Bash writes under a sealed `runs/<id>/`; block reading `secrets.env`.
- Stop: no "done"/"verified" claim unless the latest chained run's manifest is `succeeded` and a passing verify report
  cites that `run_id` and its output hashes; a `failed`, `blocked` or `timed_out` run must be named as such.

What **migrate** (04) and **discover** (02/03) must supply: steps as `run(inputs, outputs, params)` modules; in
`graph.json` per dataset `role`, `legacy_path`, `driver`, and per step `timeout_s`; every parameter in `params.yaml`.
