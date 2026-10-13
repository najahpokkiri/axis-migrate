# 09 — Target output (Databricks job files, honest `target-ready`) and the later knowledge MCP

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Design section 09 of `axis-migrate`. Status: proposal for the founder, 13 Oct 2026. Evidence is cited as
file:line or as a skill-candidate ID (EP-, MP-, IN-, AK-, FH-; files in `~/src/context/skill-candidates/`).
Abbreviations: `ARC` = `~/src/axis-spatial/_archive/services/backend/app`; `LES` = `~/src/axis-spatial/_archive/docs/lessons/deploy-errors`;
`KN` = `~/src/server-copies/axis-spatial-deploy-mcp-v2/_archive/services/mcp/axis-mcp-consolidated/tools_knowledge/knowledge.py`;
`KP` = `~/src/axis-spatial-mcp-v2/docs/planning/2026-09-21-spatial-mcp-tool-and-knowledge-plane-plan.md`;
`MD` = `~/src/server-copies/axis-spatial-harness-release/packages/runtime-core/src/migration-delivery.ts`.
The real client Databricks job was read for its structure only and is described here in general terms (NDA); no
identifiers, constants or paths from it appear below.

## 1. Scope and interfaces

**In scope.** (a) After a run reaches `reference-verified`, generate the Databricks target files under
`<project>/migration/databricks/` deterministically from `graph.json` + `params.yaml`; (b) run the offline
target checks, including a dry run of the packaged code; (c) compute `target-ready` (or refuse it) and
write the exact claim; (d) the post-demo private knowledge MCP and how knowledge moves between tiers.

**Out of scope, by rule.** No Databricks API calls, CLI authentication, `bundle deploy/run`, workspace/Volume/cluster
creation, or Cloudflare resources in the demo. Glue, GCP and Snowflake are not generated in v1: such an answer is
recorded and the ladder stops at `reference-verified` with `TARGET_NOT_SUPPORTED` (the old Platform page offered five
cards; Snowflake had no route at all, 06 Step 4, `deploy.tsx:814`).

**Inputs.** `graph.json`, `params.yaml` (incl. `target:`, §3.2), `migration/pipeline/` (installable package), the
`reference-verified` run `runs/<run_id>/` (compare report, `env.lock`, tree and tolerances hashes). **Outputs.** `migration/databricks/*` (§3.1), `runs/<run_id>/target/` (dry-run evidence), `target-check.json`,
and the `target` block of `status.json` (consumed by report and hooks).

## 2. Decisions

**D9-1 Format: a Databricks Asset Bundle (`databricks.yml`), not Jobs-API JSON or an SDK script.**
The archive emitted Jobs API 2.1 CreateJob JSON plus its own REST client (`ARC/workflow/databricks_jobs.py:566-672`, client
from :42). Its deploy route reported `"status": "complete"` once the job was *created*, never run
(`ARC/routes/deploy/platforms.py:315`; `create_job` has no `run_now`, `ARC/workflow/deploy.py:575-632`). The
real job was built by an imperative SDK script holding workspace identifiers (MP §B "databricks-job-creator: hard-coded
identifiers"). A bundle is a declarative file that the user deploys with their *own* CLI credentials. That is the
06 recommendation ("explicit approved action using the user's own CLI credentials", 06 summary row 4). The plugin
writes the file and never needs a token. The file must be called `databricks.yml` for CLI discovery, which
supersedes `databricks/job.yml` in PLAN.md (§8 C7).

**D9-2 One job task per graph step. Every task is a `python_wheel_task` of the same wheel.**
- *Per step, not a single task:* the graph is the unit of verification. Per-step intermediates on the Volume are
  what a later `live-target-verified` would compare (the defects were found in intermediates, MPV-03). The archive
  and the real job both used one task per step with `depends_on` (`ARC/workflow/databricks_jobs.py:637-667`; real job: one
  file task per stage, generalised).
- *Wheel, not notebook or file tasks:* the archive switched to `notebook_task` because workspace-imported `.py` files
  became NOTEBOOK objects that serverless could not POSIX-open (`ARC/workflow/databricks_jobs.py:725-746`; MP-31). The
  real job used Git-source file tasks and then ran code other than what had been edited (MP-30, EP-21). With a wheel,
  the code identity is a file hash. Imports resolve from site-packages, so no `__file__`, no `sys.path` hacks and no
  module-cache purge are needed (EP-21's snippet and the archive's `DependencyManager`,
  `ARC/workflow/databricks_templates.py:14-35`, both exist only to work around this).

**D9-3 The wheel is prebuilt and committed in `databricks/dist/`; the bundle does not rebuild it.** The deployed bytes
are the bytes that passed the dry run (sha256 in `target-manifest.json`), closing MP-30 by construction; a pipeline
change means re-running the plugin (T9).

**D9-4 Compute: a new single-node job cluster per run, with a pinned runtime. No existing cluster and no serverless in v1.**
A fresh cluster per run removes stale module caches (EP-21). Single-node geopandas/rasterio matches the workload,
and FH-16 records hallucinated Spark/Sedona stacks (`SedonaRegistrator` in 8 files / 5 runs) plus "Spark chosen for a 50 MB job".
The archive's serverless path needed an auto-added empty `environments` block (`ARC/workflow/databricks_jobs.py:144-158`); serverless is Q2.

**D9-5 Dependencies: one target lock derived from the runtime manifest. No runtime `pip install`. No hand-written requirements.**
About 200 archive deploy errors are `ModuleNotFoundError` (AK-36; 237 `DEPLOY-ERR-*` files in `LES/`). The numpy
ABI crash came from pip upgrading numpy inside the runtime (`LES/LESSON-databricks-numpy-abi-conflict.md`). The
archive still shipped a runtime `pip install` (`ARC/workflow/databricks_templates.py:51-91`). Rule: packages that the
runtime provides (numpy, pandas, pyarrow, …) are pinned to the runtime's *declared* versions, so pip has nothing
to do on the cluster. The geo stack (shapely, pyproj, pyogrio, rasterio, geopandas, openpyxl) is pinned exactly to
the verified `env.lock`. rasterio/pyogrio wheels bundle their own GDAL, so the native GDAL/PROJ/GEOS build is the
same on a Linux x86_64 dev box and on the cluster. Contradictions C2 and C3 are resolved in §8.

**D9-6 Paths: roots are injected as task parameters, and logical keys come from `params.yaml`.**
The pipeline sees only `--input-root --work-root --output-root --scratch-dir`. On Databricks the first three are
`/Volumes/${var.catalog}/${var.schema}/${var.volume}/{inputs,work,outputs}` and scratch is `/local_disk0/tmp/axis`.
No `/dbfs` (`LES/LESSON-databricks-dbfs-deprecated.md`), no `/Workspace` data paths, no absolute literals
(FH-14: 35/36/15/9 generated files with `Path.exists` on URIs, local literals, `/dbfs`, `__file__`).
`params.yaml` travels *inside* the wheel as package data, and its hash must equal the verified run's. The job can
change roots, never parameters.

**D9-7 All I/O goes through one staging helper, and the target profile switches it on.**
`axis_pipeline/_io.py` writes GTiff/COG/GPKG/XLSX/ZIP to scratch, then copies once (`shutil.copy2`), and stages
GPKG/SQLite/FileGDB reads to scratch first (EP-20, MP-01, MP-02, IN-33, AK-26, FH-15); scratch never holds checkpoints
(lost on restart, EP-20). It stages for paths under `target.io.no_seek_roots`: empty locally, the Volume root in the
dry run and on Databricks, so the dry run exercises the staging code. The demo needs it: it reads two GPKG inputs and
writes GTiff + GPKG + XLSX (`fixtures/lux-exposure-legacy/03_INPUT`, `04_FINAL`), exactly the formats that fail on Volumes.

**D9-8 The plugin never deploys or authenticates, and the status vocabulary has no `deployed`.**
`target-manifest.json` always carries `deployment_status: "not_deployed"`, the honest terminal state kept from the July
proof (`MD:220`; any `deployed` without full authority throws, `MD:382`; a self-asserted deployment returned 409
`deployment_authority_verifier_required`, 08 §4.3). A PreToolUse hook denies `databricks (bundle (deploy|run|destroy)|
jobs|clusters|fs|workspace|auth)` and `curl` to `*.databricks.*`/`*.azuredatabricks.*`. `bundle validate` is not run (it needs auth).

**D9-9 `target-ready` is computed by `target_check.py`, never typed in, and requires a passing dry run.**
"Validated meant compiled/imported" was the real migration's failure (MPV-09). The archive's pre-deploy gate stubbed
geo libraries and forced runtime errors to pass (06 Step 3, `sandbox_manager.py:1248-1278`). So static checks alone
cannot earn the status. The packaged wheel must re-produce parity under a Databricks-like layout (§4.2). On a host
without Linux `strace` (macOS), the dry run goes to E2B, or the check is `BLOCKED` and the status stays `reference-verified`.

**D9-10 The generator is deterministic code, not the model.**
`skills/target/make_databricks.py` renders templates from `graph.json` + `params.yaml`. In the old app the
orchestration files were the deterministic part and the most complete platform (06 Step 2, `orchestration.py:38-80`).
LLM-written requirements and READMEs overrode the backend's (06 Step 3, `generate.tsx:191,1289-1312`). The model may
explain the files. It may not edit them, and the PostToolUse hook re-hashes them against the manifest.

**D9-11 Knowledge: skill reference files for the demo; a private five-tool MCP after it; the plugin never depends on it** (§5; PLAN.md "Knowledge", 13 Oct decisions).

## 3. Target files

### 3.1 Files written to `migration/databricks/`

| File | Content | Generated from |
|---|---|---|
| `databricks.yml` | bundle, variables, one `dev` target with no host, job with one task per step | graph steps/edges, `params.target` |
| `pyproject.toml` | target build: hatchling, package source `../pipeline`, `dependencies` = `requirements-target.lock`, console script `axis-pipeline`, `params.yaml` as package data | pipeline package, lock |
| `requirements-target.lock` | exact pins: runtime-provided pkgs at runtime versions + geo stack at `env.lock` versions | runtime manifest ⊕ `env.lock` |
| `runtime-manifest.json` | declared Python + package versions of the chosen runtime, with `source_url`, `retrieved`, `kind: "declared"` | copied from plugin `skills/target/runtimes/<id>.json` |
| `dist/axis_pipeline-<ver>+<tree8>-py3-none-any.whl` | the wheel that passed the dry run | built from the verified tree |
| `README-deploy.md` | manual steps (below); what is not tested | template |
| `target-manifest.json` | hashes: wheel, pipeline tree, params, graph, lock, bundle; runtime id; `status`; `deployment_status: "not_deployed"`; claim text; pointer to `target-check.json` | `target_check.py` |
| `target-check.json` | one record per check `{id, state: PASS|FAIL|WARN|BLOCKED|NOT_RUN, reason_codes[], evidence[]}` | `target_check.py` |

The dry-run evidence goes to `runs/<run_id>/target/`: `trace.log`, `trace-findings.json`, `compare-report.json`, `rss.json`, `layout.json`.

### 3.2 `params.yaml` target block (contract with the interview section)

```yaml
target:
  platform: databricks            # the single end-of-interview answer; source: answer
  runtime: dbr-<lts-id>           # key into skills/target/runtimes/; source: default|answer
  compute: job_cluster_single_node
  node_memory_gb: 32              # assumption unless answered; used by the T11 memory budget
  volume: {catalog: null, schema: null, volume: null}   # never asked; bundle vars without defaults
  io: {no_seek_roots: []}         # filled by the dry run / the job, never by hand
```

Volume names are not interview questions: the old "clarity" score was mostly deployment answers (PLAN "Interview"; 06 `smart_interview.py:64-129`).

### 3.3 `databricks.yml` shape (generated; the demo's steps shown)

```yaml
bundle: {name: lux-exposure-migration}
variables:
  catalog: {description: "Unity Catalog catalog holding the Volume"}     # no default on purpose
  schema:  {description: "Schema holding the Volume"}
  volume:  {description: "Volume name"}
  node_type_id: {description: "Single-node VM type, >= params.target.node_memory_gb RAM"}
targets:
  dev: {mode: development, default: true}            # workspace.host comes from the user's CLI profile
resources:
  jobs:
    lux_exposure:
      name: lux-exposure-migration
      max_concurrent_runs: 1
      tags: {generated-by: axis-migrate, pipeline-tree: "<sha256[:12]>", status: target-ready}
      job_clusters:
        - job_cluster_key: single_node
          new_cluster:
            spark_version: "<runtime.spark_version from runtime-manifest.json>"
            node_type_id: ${var.node_type_id}
            num_workers: 0
            data_security_mode: SINGLE_USER         # Unity Catalog access needed for /Volumes (Q3)
            spark_conf: {spark.databricks.cluster.profile: singleNode, spark.master: "local[*]"}
            custom_tags: {ResourceClass: SingleNode}
      tasks:
        - task_key: s01_prepare_population
          job_cluster_key: single_node
          python_wheel_task:
            package_name: axis_pipeline
            entry_point: axis-pipeline
            parameters: [--step, s01_prepare_population, --scratch-dir, /local_disk0/tmp/axis, --no-seek-root, /Volumes,
              --input-root, "/Volumes/${var.catalog}/${var.schema}/${var.volume}/inputs",
              --work-root, "/Volumes/${var.catalog}/${var.schema}/${var.volume}/work",
              --output-root, "/Volumes/${var.catalog}/${var.schema}/${var.volume}/outputs"]
          libraries: [{whl: ./dist/axis_pipeline-0.1.0+<tree8>-py3-none-any.whl}]
        - task_key: s02_landuse_mask
          depends_on: [{task_key: s01_prepare_population}]
          # … same shape; s03 depends on s01+s02, s04 on s03 (from graph edges)
```

**Generation rules.** `task_key` = graph step id, sanitised to `^[A-Za-z0-9_-]{1,100}$`, with collisions
failing (no renaming). `depends_on` = the distinct producers of the step's input keys (edges in `graph.json`). A cycle
or a step without a module fails generation. Steps marked `manual` in the graph (e.g. the demo's hand-fixed canton
names, `NOTES.txt`) produce **no task**. Their output must already be a pipeline step, or be listed as an input to upload,
and README-deploy says so. Every task gets the same parameter list apart from `--step`.

**README-deploy.md (template sections).** 1. What this is (copied claim, §4.3). 2. Prerequisites: the Databricks CLI
version the schema was taken from, *your* authenticated profile, a Unity Catalog Volume, permission to create job
clusters, PyPI reachable from the cluster (Q6). 3. Upload inputs: a table of logical key → relative path → sha256,
plus a one-line check. 4. `databricks bundle validate --var=…`, then `deploy`, then run **one task first**
(single-task test, the real project's rule in MP §B and `ARC/workflow/databricks_jobs.py:799`). 5. After the run:
compare the wheel hash in the run's library list with `target-manifest.json` (MP-30 check), download `work/` and
`outputs/`, then run `/migrate verify --against-target <dir>` (post-demo; that is what `live-target-verified` would need).
6. What has not been tested (§4.3). No host, token, e-mail or cluster ID anywhere (check T10).

## 4. "Honest target-ready"

### 4.1 Checks run without deploying (`target_check.py`)

| ID | Check | How | Fails with |
|---|---|---|---|
| T1 | Bundle schema | validate `databricks.yml` against a JSON schema vendored in the plugin, produced once by `databricks bundle schema` at a pinned CLI version (offline; Q5) | `TC_SCHEMA_INVALID` |
| T2 | Task graph = verified graph | tasks ↔ graph steps 1:1 (minus `manual`), `depends_on` = edges, entry point and `--step` ids exist in the wheel (`importlib.metadata` in the dry-run venv) | `TC_TASK_GRAPH_MISMATCH` |
| T3 | Volume-unsafe I/O (static) | AST over `pipeline/`: `rasterio.open(..,'w'/'r+')`, `.to_file(`, `ExcelWriter`, `to_excel`, `sqlite3.connect`, `zipfile.ZipFile(..,'w')`, `gdal.*` writes, `gpd.read_file` on `.gpkg` / `.gdb` anywhere except `_io.py` | `TC_VOLUME_UNSAFE_WRITE/READ` |
| T4 | `__file__`, cwd and literal paths | any `__file__`. `os.getcwd()` / `Path.cwd()` used in a path. Literals matching `^(/Users|/home|/tmp|/dbfs|/Workspace|/Volumes|[A-Z]:\\)` in pipeline code | `TC_DUNDER_FILE`, `TC_LOCAL_PATH_LITERAL` |
| T5 | Runtime installs and denied imports | `subprocess … pip`, `%pip`, `dbutils`, `importlib.reload`. Imports of `sedona`, `pyspark`, `mosaic`, plus `osgeo` unless the runtime manifest declares GDAL bindings (FH-16; `LES/LESSON-databricks-osgeo-import.md`) | `TC_RUNTIME_PIP`, `TC_DENIED_IMPORT` |
| T6 | Env diff | `env.lock` of the verified run vs `requirements-target.lock` vs `runtime-manifest.json`: list every package/Python/GDAL difference. A difference is not a failure in itself; T8 decides | diagnostics only; `TC_RUNTIME_MANIFEST_MISSING` → BLOCKED |
| T7 | Dry run executes | §4.2, every task exits 0 | `TC_DRYRUN_TASK_FAILED` |
| T8 | Dry-run parity | `compare.py` with the *same* tolerances file (hash-checked) on every step output and final output vs the legacy reference | `TC_DRYRUN_PARITY_FAIL` (keeps compare reason codes) |
| T9 | Code and params identity | pipeline tree hash, `params.yaml` hash and `graph.json` hash equal the `reference-verified` run's; wheel content matches the tree | `TC_CODE_CHANGED_SINCE_VERIFY`, `TC_PARAMS_CHANGED` |
| T10 | No identifiers or secrets | regex over `databricks/`: workspace hosts, `dapi…` tokens, e-mails, cluster-id shapes, `.env` content (MP "client-specific": identifiers lived in job scripts) | `TC_IDENTIFIER_IN_BUNDLE` |
| T11 | Memory budget (warn) | peak RSS per task from the dry run vs 0.6 × `node_memory_gb` (EP-22, MP-33) | `TC_MEMORY_BUDGET` (WARN) |
| T12 | Logging (warn) | bare `print(` in pipeline modules (MP-34) | `TC_PRINT_NOT_LOGGED` (WARN) |
| T13 | Seek trace | §4.2 trace rules | `TC_SEEK_ON_VOLUME`, `TC_SQLITE_ON_VOLUME` |

`target-ready` requires all of the following: run verdict `PASS_EXACT`/`PASS_WITHIN_TOLERANCE` (10 C1); T1–T10 and T13 `PASS`;
T8 with a pass verdict; no check `NOT_RUN`. WARNs are shown and do not block. Anything else leaves the status at
`reference-verified`, with the failing IDs in the report.

### 4.2 The dry run: feasible, and it runs the real code

What it is: the committed wheel, installed into a **fresh venv** (Python version from the runtime manifest, via
`uv python install`) from `requirements-target.lock`, run task by task in job order.
- **Layout.** `runs/<id>/target/root/Volumes/<c>/<s>/<v>/{inputs,work,outputs}` and `root/local_disk0/tmp/axis`.
  Inputs are copied in and hash-checked.
- **Task parameters.** Read from the generated `databricks.yml` itself, with `${var.*}` substituted by placeholders.
  The only rewrite is prefix remapping of `/Volumes` and `/local_disk0` to the fake root, recorded in `layout.json`.
  The job file under test is the one that ships.
- **Isolation.** Each task is a new process, `python -I`, cwd an empty directory, `PYTHONPATH` unset, `HOME` temporary.
  The process has no network (`unshare -n` where allowed; E2B has none) and no repo on `sys.path`. A forgotten
  `__file__`, a repo-relative import, missing package data or a wrong entry point fails here even when T4 missed it.
- **Seek emulation (T13).** `strace -f -y -e trace=openat,lseek,pwrite64,pwritev,ftruncate` on each task, filtered
  to fds under the fake Volume root. Rules: a write fd there is written sequentially only (no `lseek`/`pwrite*`/`ftruncate`);
  no `O_RDWR` open of `*.gpkg|*.sqlite|*.db`; no `-journal`/`-wal`/`-shm` created. Random *reads* are allowed
  (windowed raster reads from Volumes worked in the real migration, EP-22), recorded as an assumption. Feasibility
  checked on this server: tracing a SQLite write under a test directory shows the `-journal` creation and `pwrite64`
  calls with fd paths. T3 cannot see dynamically built paths; this can.
- **Parity.** `compare.py` on `work/` and `outputs/` (T8), plus peak RSS per task (T11).

Deliberately **not** emulated, and so not claimed: Spark and the runtime's notebook/IPython hooks (the numpy ABI crash
happened in one, `LES/LESSON-databricks-numpy-abi-conflict.md`), FUSE latency and consistency, Unity Catalog
permissions, cluster policies, node availability, PyPI reachability, and runtime package versions as *observed* (only declared).

### 4.3 The claim (exact wording, rendered from `target-manifest.json`)

> **target-ready (Databricks), not deployed.** Job files are in `migration/databricks/` and were checked offline.
> The packaged pipeline (wheel `sha256:<w12>`, built from the pipeline tree `<t12>` that reached reference-verified
> in run `<run_id>`) was re-run locally in a Databricks-like folder layout, with the package versions declared for
> runtime `<runtime>` (from `<source_url>`, retrieved `<date>`). It matched the legacy reference on `<n>` of `<n>`
> step and final outputs, using the same comparator and tolerances (`<report_id12>`). Static and traced checks found
> no Volume-unsafe writes, no SQLite on the Volume, no `__file__` and no local paths.
> **Not done:** nothing was deployed, no Databricks workspace was contacted, and the code has not run on a Databricks
> cluster. Unity Catalog permissions, Volume behaviour beyond the seek emulation, cluster availability, real runtime
> package versions and performance at full scale are untested.

The claim-lint hook (hooks section) blocks "deployed", "runs on Databricks", "production-ready", "validated on
Databricks", "tested on the cluster" unless `status.json` supports them (the handoff: "Historical phrases such as
'production-ready' … must be revalidated").

### 4.4 Relation to TARGETED_ACCEPT

`target-ready` is a computed rung of the ladder, not an acceptance. TARGETED_ACCEPT (08 §4.5; 10 A6) is the outcome of
a bounded review protocol: one independent adversarial review, P0/P1 repairs only, at most one targeted recheck. It
confirms named findings are closed. It neither measures parity quality nor stands in for customer acceptance.
After the demo, an optional `review` field in `target-manifest.json` (`not_run | REVISE | TARGETED_ACCEPT | REOPENED`)
records a review of the target pack under exactly that protocol; a second failure is `REOPENED`, never a third pass.
The field never changes the ladder or substitutes for a failing T-check; a TARGETED_ACCEPT on a pack whose T9 later
fails is void. Demo: `review: not_run`.

The rungs above `target-ready` (`deployed`, `live-target-verified`, `customer-accepted`, 08 §4.3) need authority and
target-side evidence. The plugin does not model them in v1.

## 5. Knowledge MCP (after the demo)

**What exists to recover.** The books server is Python FastMCP over Vertex AI Search (`KN:103,163,242,338,413`). It has
five read-only tools. Ids are `"{book_id}/{section_path}"`, with the citation rendered as `author year. title. section. §7.3.2, p.N.`
(`KN:464-472`). Two flaws must not come back: the per-org datastore came from a caller-supplied `org_id` (`KN:91-94`)
while four of five tools were hard-wired to the default datastore (`KN:178` and siblings); and the ingest script was
specified but never committed. Also available: `org-docs-mcp`, a reusable Worker skeleton (bearer gate, timing-safe
compare, frozen corpus hashes, tamper refusal; `~/src/axis-spatial/mcp/BUILD-REPORT.md:140-149`); and KP, which proposes
one knowledge tool plus resources, AI Search over a versioned R2 corpus (`KP:453-456`), and three corpora that "must
not share one unscoped index or authorization boundary" (`KP:630-642`).

**Architecture.** One private Worker (Streamable HTTP MCP, OAuth via Cloudflare Access). **R2**: versioned corpus, one
Markdown per section with front-matter. **D1**: manifest of books, sections, versions, evidence ids; `list_books`,
`get_toc`, `get_section`, `cite` are exact D1/R2 reads, never a vector query (the old TOC was a capped search,
`_MAX_RESULTS = 50`, `KN:44`). **AI Search**: answers `search_within` only, one instance per corpus. **Vectorize +
Workers AI embeddings**: fallback if AI Search cannot filter by book and version (R2 + Vectorize + D1 as proposed in
`~/src/axis-spatial-mcp-v2/docs/handoffs/subagents/2026-09-06-org-knowledge-scoping.md:22-33`).

**Tools** (five names kept, PLAN.md): `list_books(domain?)`, `get_toc(book_id)`, `get_section(book_id, section_path)`,
`search_within(book_id|"*", query, limit≤10)` (every hit carries its citation and evidence ids), `cite(book_id,
section_path)`. **No `org_id` argument**: the corpus set comes from the token's tenant claim.

**Content tiers.**

| Tier | Where | Who sees it | Example |
|---|---|---|---|
| T0 public | plugin `skills/*/reference/*.md` (open repo) | everyone | "write locally, then copy to seek-less mounts" |
| T1 private library | Axis corpus in the MCP | Axis + licensed customers | curated entries with their eval links and private evidence; licensed books |
| T2 customer | a separate R2 prefix + separate AI Search instance per customer | that customer's tokens only | their legacy conventions, decisions, failure cases |

There is no cross-customer index, and T2 never feeds T1 automatically.

**Graduation** (hand-curated PR, recorded reviewer). **T2 → T1**: rewritten in general terms (PLAN NDA rule),
client-noun scan passes, customer consent recorded, evidence re-pointed to an opaque id. **T1 → T0**: needs public
evidence (a public fixture or eval trap reproducing it) and must move at least one eval (PLAN: "entries that move
no eval are deleted"). **Demotion**: an entry that stops moving an eval leaves T0 and stays in T1 history.

**Evidence ids survive tiers.** Every entry has a stable `entry_id` (e.g. `io-formats/seekless-mount-write`), a version
and `evidence_ids[]` (opaque, e.g. `ev:7f3c…`). The id → real path/file:line map lives only in the tier that owns the
evidence: T2 maps stay with the customer, T1 maps stay private. `cite` returns
`{entry_id, version, evidence_ids, tier, citation}`, so a public skill entry can cite `ev:7f3c…` without revealing
the source, and Axis can still resolve it internally.

**The plugin without it.** The MCP is not in the public plugin's `.mcp.json`; private users add it at user level. The
`migrate` skill says: "If the knowledge MCP is present, search it for the step's operation. Otherwise use the
references." Absence is logged once, never fatal. Evals run MCP-off as the baseline, and the MCP stays only if it
moves an eval (PLAN improvement table).

**What stays private.** All T1/T2 content, the evidence maps, the private eval cases, real-user failure traces
(consent-gated, PLAN), and the Worker's tenancy map. The tool names and schemas are public so the skill can refer to them.

## 6. Reuse

| Take | From | How |
|---|---|---|
| Graph → `depends_on` mapping, topological order | `ARC/workflow/databricks_jobs.py:566-672` | Re-implement (~60 lines) emitting bundle YAML; drop its `notebook_task` / dbfs path helpers (`:700-746`) |
| Single-task test idea | `ARC/workflow/databricks_jobs.py:799` | README-deploy step 4; not code |
| Serverless `environments` requirement | `ARC/workflow/databricks_jobs.py:144-158` | Knowledge for Q2 only |
| Databricks lessons | `LES/LESSON-databricks-{file-dunder,dbfs-deprecated,numpy-abi-conflict,osgeo-import,serverless-deploy-errors}.md` | Curate into `migrate/reference/platform-notes.md` (AK proposal) with a "verify current runtime docs" banner |
| Volumes write rule | AK-26 (archive `code_scanner.py:114-128`), MP-01 pre-push grep list | T3 rules; drop the overly broad `__file__`/`Path` FATAL rules (AK "Drop" list) |
| `DeploymentStatus` vocabulary, fail-closed authority | `MD:220`, `MD:382` | `deployment_status: not_deployed` in the manifest |
| `env.lock`, evidence bundle, verdict states | 10 C1, C3 | T6/T8 inputs; same comparator id |
| Remediation classifier, log fetcher | 06 "Ten most reusable modules" #4 | After the demo, only if a deploy command is ever added |
| Books tool shapes and citation format | `KN:103-476` | Keep the shapes; replace Vertex; fix tenancy |
| Worker skeleton (auth gate, corpus hashes) | `~/src/axis-spatial/mcp/org-docs-mcp` | Template for the knowledge Worker |

Not reused: `DependencyManager` runtime pip and module purge (`databricks_templates.py:14-122`), `REQUIREMENTS_TEMPLATE`
with `numpy<2.0` (`:248-264`), token-polling `job_monitor.py`, server-side credential relay (06 Step 4).

## 7. Acceptance tests

| ID | Test | Pass condition |
|---|---|---|
| AT9-01 | Demo generation | `lux-exposure-legacy`: tasks = non-manual graph steps; `depends_on` = edges; T1–T13 run; status `target-ready`; claim text matches §4.3 template |
| AT9-02 | Determinism | Generating twice from the same graph/params gives byte-identical `databricks.yml`, lock and manifest (wheel built reproducibly, `SOURCE_DATE_EPOCH`) |
| AT9-03 | Static Volume write | Mutant: `gdf.to_file(out_root/…gpkg)` bypassing `_io` → T3 FAIL; status stays `reference-verified` |
| AT9-04 | Dynamic Volume write | Mutant with the path built from a string concatenation that T3 misses → T13 `TC_SEEK_ON_VOLUME` from the trace |
| AT9-05 | GPKG read in place | Mutant reading the Volume GPKG directly → `TC_SQLITE_ON_VOLUME` (O_RDWR or `-journal`) |
| AT9-06 | `__file__` | Mutant `Path(__file__).parent/"data"` → T4 FAIL, and T7 fails too because the repo is absent |
| AT9-07 | Code drift | Edit one pipeline line after verify → T9 `TC_CODE_CHANGED_SINCE_VERIFY`, no target-ready |
| AT9-08 | Env drift that matters | Runtime manifest with a different core version that changes an output → T8 FAIL with the T6 diff shown; a harmless drift → PASS with the diff listed |
| AT9-09 | No cloud contact | Run with no `~/.databrickscfg`, no `DATABRICKS_*`, network denied: every check completes; hook test shows `databricks bundle deploy` and `curl https://x.cloud.databricks.com` denied |
| AT9-10 | Claim lint | Model writes "deployed to Databricks" into the report → blocked; report shows the §4.3 text only |
| AT9-11 | Identifier scan | Insert a host URL, an e-mail address or a token-shaped string in a param → T10 FAIL |
| AT9-12 | Unsupported target | Answer "Snowflake" → no `databricks/`, `TARGET_NOT_SUPPORTED`, ladder at `reference-verified` |
| AT9-13 | No strace (macOS) | T13 `BLOCKED`, status not `target-ready`, report says why and offers E2B |
| AK9-01 | Knowledge off | Full eval suite with the MCP absent: same pass and false-success rates as the baseline |
| AK9-02 | Tenant isolation | A tenant-A token, any arguments, all five tools: zero tenant-B section ids returned (tested with a canary string) |
| AK9-03 | Evidence ids | `cite` of a T0 entry returns opaque `evidence_ids` that resolve only through the T1 map |

## 8. Contradictions resolved

| # | Contradiction | Resolution |
|---|---|---|
| C1 | Archive: `notebook_task` for workspace files (`ARC/workflow/databricks_jobs.py:725-746`). Real job: Git-source file tasks. MP-31: "run as Python file tasks from Git source" | Neither: `python_wheel_task` (D9-2). It avoids notebook conversion *and* Git branch drift (MP-30) |
| C2 | Archive pins `numpy<2.0` (`databricks_templates.py:248-253`). AK-36 says that pin is outdated. The real job's library list targeted a numpy-2 runtime while its own reference requirements file pinned numpy 1.x (two files in one repo disagreeing) | One generated lock derived from the runtime manifest (D9-5); no hand-written requirements file; T6 shows the diff, T8 decides |
| C3 | Lesson "Databricks has no `osgeo`, use rasterio" vs plain GDAL usage in legacy code | Deny `osgeo` unless the manifest declares it (T5); rasterio/pyogrio wheels carry GDAL |
| C4 | `__file__`: the archive auto-fix falls back to `cwd` (`LES/LESSON-databricks-file-dunder.md`; EP-21) vs AK "drop CS DB-004 (any `__file__` FATAL)" | No `__file__` in *generated pipeline* code (we control it; roots are injected); the broad rule stays dropped for scanning legacy code |
| C5 | Real job: "Git source: fresh code on every run" as a virtue vs MP-30: the job ran other code | A committed, hashed wheel (D9-3) |
| C6 | EP-20: Workspace files ~20× faster than Volumes for checkpoint I/O vs D9-6 Volumes for data | v1 has no checkpoints; no performance claim. Revisit with real sizes |
| C7 | PLAN.md: `databricks/job.yml` | `databricks/databricks.yml` (CLI discovery); PLAN to be updated |
| C8 | Old interview asked platform and storage first vs PLAN: one platform question at the end | At the end; Volume names are not asked (bundle variables) |
| C9 | 06: "make deploy an explicit approved action" vs demo "not deployed" | Not in v1. A future `/migrate deploy` would need the user's CLI, an explicit approval, the `DeliveryAuthority` fields (`MD:222`) and the post-run hash check |
| C10 | KP: one `search_spatial_knowledge` tool + resources vs PLAN: five book tools | Five read-only tools (PLAN is newer and names them); citation on every search hit, as KP wants |

## 9. Open questions for the founder

1. **Runtime to pin.** Which LTS runtime do we vendor a manifest for; is a manifest transcribed from release notes ("declared") acceptable?
2. **Serverless.** Is it in scope after the demo? The demo uses a single-node job cluster (D9-4).
3. **Access mode.** Dedicated (`SINGLE_USER`) or standard in the template? Volumes need a Unity Catalog mode either way.
4. **Committed wheel.** Commit it in `dist/` (byte identity, D9-3) rather than build at deploy (simpler for users)?
5. **Bundle schema.** Vendor the JSON schema from `databricks bundle schema` (pinned CLI) in the plugin? No `bundle validate` on stage (auth).
6. **Air-gapped workspaces.** Is a wheelhouse of all pins (no PyPI from the cluster) needed for the first real customer?
7. **Dry run on E2B.** On macOS, E2B by default or opt-in only (PLAN: local by default)?
8. **Knowledge backend.** AI Search per tenant or Vectorize (cost per customer instance; verify against current Cloudflare docs)?
9. **Graduation owner.** Who signs off a T2 → T1 rewrite, and what form does customer consent take?
10. **On stage.** Show the claim *and* a dry-run trace catching a seeded Volume write (AT9-04), or the claim only?

## 10. Cross-section contract

| Needed from | Field / behaviour |
|---|---|
| Discover / graph | `steps[].{id, module, kind: auto|manual, inputs[], outputs[]}` with logical keys; `edges[]` producer→consumer; ids stable across edits |
| Interview / params | `params.yaml` `target:` block (§3.2); every path is a logical key relative to a root, never absolute |
| Migrate / pipeline | Installable package `axis_pipeline`; console script `axis-pipeline --step <id> --input-root --work-root --output-root --scratch-dir [--no-seek-root]`; all file I/O via `axis_pipeline/_io.py`; `logging`, not `print`; no `__file__`; params loaded from package data |
| Run | `runs/<id>/{env.lock, run.json(tree_hash, params_hash, graph_hash)}`; `run_local.py --layout databricks-dryrun --venv <path>` (or the dry run calls the entry point directly); E2B path for T13 |
| Verify | `compare.py --reference … --candidate … --tolerances … --out …` usable on the dry-run tree; same `comparator_id`; verdict states per 10 C1 |
| Hooks / status | Ladder function reads `target-check.json`; PreToolUse deny list (D9-8); claim-lint words (§4.3); PostToolUse re-hash of `databricks/` against the manifest |
| Report | "Target" panel renders the §4.3 text verbatim from `target-manifest.json`, the T-check table and the env diff; never composes its own wording |
| Evals | AT9-03..06 mutants added as traps; AK9-01 baseline rule |
| This section provides | `skills/target/{SKILL.md, make_databricks.py, target_check.py, dryrun.py, runtimes/*.json, schema/bundle.<cli>.json, templates/*}`; `migration/databricks/*`; `status.target = {state, reasons[], manifest_ref}` |
