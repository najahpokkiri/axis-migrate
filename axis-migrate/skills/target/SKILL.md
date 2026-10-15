---
name: target
description: Sub-step of the axis-migrate migrate flow. Use when a reference-verified pipeline needs its Databricks Asset Bundle and the offline target checks and dry run. Never deploys. Not for general Databricks setup or job questions.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Target: Databricks job files and the honest `target-ready`

**When.** Only after the pipeline is `reference-verified` (`ladder.py <migration>` says so) and `params.yaml`
has `target.platform: databricks` (the end-of-interview answer). Any other platform (Glue, GCP, Snowflake, …) →
`TARGET_NOT_SUPPORTED`: nothing is written and the status stays `reference-verified`. Say so; do not improvise files.

**Commands** (`$PLUGIN/scripts/axpy`, from the project folder):

```
axpy $PLUGIN/skills/target/make_databricks.py migration              # writes migration/databricks/
axpy $PLUGIN/skills/target/target_check.py migration --run <run_id>  # T1–T13 + dry run (~10–20 s)
axpy $PLUGIN/skills/report/seal.py migration --run <run_id> --amend --target   # binds the pack (else TARGET_NOT_SEALED)
axpy $PLUGIN/skills/report/ladder.py migration                       # now target-ready, or why not
```

`<run_id>` = the sealed, chained run that reached reference-verified. After **any** change to `pipeline/`,
`params.yaml` or `graph.json`, the old pack is void: make a new verified run, then repeat all four commands.

**What make_databricks writes** (deterministic; you may explain these files, never edit them — T9 re-renders and
fails on any difference): `databricks.yml` (one `python_wheel_task` per non-manual migrated step, `depends_on` from the
graph edges, single-node job cluster, `SINGLE_USER`, runtime from `runtimes/dbr-17.3-lts.json`, variables `catalog`,
`schema`, `volume`, `node_type_id` without defaults, no host), `dist/axis_pipeline-<ver>+<tree8>-py3-none-any.whl`
(the bytes that are dry-run and deployed), `requirements-target.lock`, `runtime-manifest.json`, `pyproject.toml`,
`_datasets.json`, `README-deploy.md`. Manual, bridged and kept-in-legacy steps get no task; their outputs are inputs
to upload (README §3).

**Reading `databricks/target-check.json`.** One record per check `{id, state, reason_codes, evidence}`.
`target-ready` needs T1–T10 and T13 `PASS`, T8 a pass verdict, nothing `NOT_RUN`. T11 (memory) and T12 (`print`)
are `WARN` only: report them, they do not block.

| Check | Fails with | What to do |
|---|---|---|
| T1 schema | `TC_SCHEMA_INVALID` | regenerate; never hand-edit `databricks.yml` |
| T2 task graph | `TC_TASK_GRAPH_MISMATCH` | regenerate from the verified graph |
| T3 Volume-unsafe I/O | `TC_VOLUME_UNSAFE_WRITE/READ` | write via `ctx.write_*`/`ctx.output_path`, read via `ctx.read_*`/`ctx.input_path` |
| T4 paths | `TC_DUNDER_FILE`, `TC_LOCAL_PATH_LITERAL` | no `__file__`, cwd or absolute paths in steps; use params and ctx |
| T5 installs/imports | `TC_RUNTIME_PIP`, `TC_DENIED_IMPORT` | no pip at run time, no Spark/Sedona/Mosaic/dbutils/osgeo |
| T6 env diff | (diagnostics) | read the diff; T8 decides whether it matters |
| T7 dry run | `TC_DRYRUN_TASK_FAILED` | read `databricks/dryrun/logs/<task>.stderr` |
| T8 parity | `TC_DRYRUN_PARITY_FAIL` + compare codes | the packaged code or runtime versions change an output: fix code, not tolerances |
| T9 identity | `TC_CODE_CHANGED_SINCE_VERIFY`, `TC_PARAMS_CHANGED`, `TC_GRAPH_CHANGED`, `TC_GENERATED_FILE_EDITED` | new verified run, then regenerate |
| T10 identifiers | `TC_IDENTIFIER_IN_BUNDLE` | remove hosts, tokens, e-mails, cluster ids from params/code |
| T13 seek trace | `TC_SEEK_ON_VOLUME`, `TC_SQLITE_ON_VOLUME`, `TC_NO_STRACE` (BLOCKED) | stage through ctx; without strace (macOS) offer the E2B dry run |

Dry-run evidence: `databricks/dryrun/` (`layout.json`, `trace.log`, `trace-findings.json`, `compare-report.json`,
`rss.json`, `dryrun.json`) — the run directory itself is sealed read-only, so this is where `seal --amend --target`
finds it.

**The claim.** Quote `claim` from `databricks/target-manifest.json` verbatim (it is rendered from
`templates/claim.v1.md` with real values); never compose your own wording, never shorten away the "Not done" part:

> **Not done:** nothing was deployed, no Databricks workspace was contacted, and the code has not run on a Databricks
> cluster. Unity Catalog permissions, Volume behaviour beyond the seek emulation, cluster availability, real runtime
> package versions and performance at full scale are untested.

`deployment_status` is always `not_deployed`. Never write "deployed", "runs on Databricks", "production-ready",
"validated on Databricks" or "tested on the cluster" — the claim check blocks them.

**Forbidden (a hook denies them; do not try workarounds):** `databricks bundle deploy|run|destroy|validate`,
`databricks jobs|clusters|fs|workspace|auth …`, `curl`/`wget` to `*.databricks.*` / `*.azuredatabricks.*`, reading
or creating `~/.databrickscfg`, setting `DATABRICKS_*`. Deploying is the user's own step with their own CLI
(`README-deploy.md` §4: validate, deploy, run one task first).
