#!/usr/bin/env python3
"""make_databricks.py — deterministic Databricks Asset Bundle for a verified pipeline (09 §3; D9-1…D9-6, D9-10).

    axpy make_databricks.py <migration> [--run <run_id>] [--json]

Writes migration/databricks/ from graph.json + params.yaml (+ the lock of the verified run):
  databricks.yml            bundle: variables (catalog, schema, volume, node_type_id; no defaults), one `dev` target
                            without a host, one job with a single-node job cluster and one python_wheel_task per
                            non-manual, in-scope, migrated graph step (task_key = step id; depends_on from the edges)
  pyproject.toml            the equivalent hatchling build (../pipeline → package axis_pipeline, params.yaml and
                            _datasets.json as package data, console script axis-pipeline)
  requirements-target.lock  exact pins: runtime-provided packages at the runtime's DECLARED versions, the geo stack
                            (and anything else the runtime does not provide) at the verified uv.lock versions
  runtime-manifest.json     copy of skills/target/runtimes/<id>.json
  _datasets.json            the dataset map packaged into the wheel (roots + relative paths per dataset)
  dist/axis_pipeline-<ver>+<tree8>-py3-none-any.whl
                            built here by a small reproducible PEP 427 builder (sorted entries, fixed timestamps from
                            SOURCE_DATE_EPOCH, default 1980-01-01), from migration/pipeline + params.yaml
  README-deploy.md          manual deploy steps for the user's own CLI; no host, token, e-mail or cluster id

Nothing here contacts a workspace or calls the Databricks CLI. Steps marked manual, bridges and steps kept in legacy
get no task: their outputs become inputs to upload (listed in README-deploy.md). The model may explain these files but
never edits them: target_check.py re-renders them and fails T9 on any difference.

Exit codes: 0 written · 2 refused (graph/params/runtime problem; nothing written) · 3 TARGET_NOT_SUPPORTED (the
answered platform is not Databricks; nothing written, the ladder stays at reference-verified).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import string
import sys
import time
import tomllib
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

import yaml  # noqa: E402  (pinned env)

GENERATOR = "axis-migrate make_databricks.py 1.0.0"
RUNTIMES = HERE / "runtimes"
TEMPLATES = HERE / "templates"
SCHEMA_CLI_VERSION = "v1.20.0"
SCHEMA_FILE = HERE / "schema" / f"bundle.subset.cli-{SCHEMA_CLI_VERSION}.json"
RUNTIME_SRC = PLUGIN / "skills" / "migrate" / "runtime" / "_axis_runtime.py"
PLUGIN_LOCK = PLUGIN / "skills" / "run" / "env" / "uv.lock"

PACKAGE = "axis_pipeline"
ENTRY_POINT = "axis-pipeline"
ENTRY_TARGET = "axis_pipeline._axis_runtime:main"
PIPELINE_VERSION = "0.1.0"
DEFAULT_RUNTIME = "dbr-17.3-lts"
DEFAULT_NODE_MEMORY_GB = 32
SOURCE_DATE_EPOCH_DEFAULT = 315532800          # 1980-01-01T00:00:00Z, the earliest zip timestamp
VOLUME = "/Volumes/${var.catalog}/${var.schema}/${var.volume}"
SCRATCH = "/local_disk0/tmp/axis"
JOB_CLUSTER_KEY = "single_node"
TASK_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
PACKAGED = ("params.yaml", "_datasets.json")
# 09 D9-5: pinned exactly to the verified lock even when the runtime declares a version of its own.
GEO_STACK = ("geopandas", "openpyxl", "pyogrio", "pyproj", "rasterio", "shapely")
ROOT_DISTS = GEO_STACK + ("numpy", "pandas", "pyyaml")      # what the vendored runtime itself imports
IMPORT_ALIASES = {"yaml": "pyyaml", "PIL": "pillow", "dateutil": "python-dateutil", "sklearn": "scikit-learn",
                  "osgeo": "gdal", "skimage": "scikit-image", "cv2": "opencv-python"}
GENERATED = ("databricks.yml", "pyproject.toml", "requirements-target.lock", "runtime-manifest.json",
             "_datasets.json")
TARGET_ENV = {"python_version": "3.12", "python_full_version": "3.12.3", "sys_platform": "linux",
              "platform_system": "Linux", "platform_machine": "x86_64", "os_name": "posix",
              "implementation_name": "cpython", "platform_python_implementation": "CPython",
              "platform_release": "", "platform_version": "", "implementation_version": "3.12.3", "extra": ""}


class Refused(Exception):
    def __init__(self, code: str, reasons: list[str], message: str = ""):
        super().__init__(message or f"{code}: " + "; ".join(reasons))
        self.code = code
        self.reasons = reasons


EXIT_OK, EXIT_REFUSED, EXIT_NOT_SUPPORTED = 0, 2, 3


# ---------------------------------------------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------------------------------------------
def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def resolve_migration(p: str | Path) -> Path:
    mig = Path(p).resolve()
    if (mig / "graph.json").is_file():
        return mig
    found = ac.find_migration_dir(mig)
    if found is None:
        raise Refused("MIGRATION_NOT_FOUND", [f"no migration/graph.json at or above {mig}"])
    return found


def load_runtime_module():
    spec = importlib.util.spec_from_file_location("_axis_runtime_target", RUNTIME_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def target_block(params_doc: dict) -> dict:
    """params.yaml `target:` (09 §3.2) with its documented defaults; raises Refused / not-supported."""
    t = (params_doc or {}).get("target")
    if not isinstance(t, dict) or not t.get("platform"):
        raise Refused("TARGET_NOT_CONFIGURED", ["params.yaml has no target.platform (the end-of-interview answer)"])
    platform = str(t["platform"]).strip().lower()
    if "databricks" not in platform:
        raise Refused("TARGET_NOT_SUPPORTED", [f"target.platform {t['platform']!r}: only Databricks is generated in "
                                               f"v1 (Glue, GCP, Snowflake and others are not)"])
    vol = t.get("volume") if isinstance(t.get("volume"), dict) else {}
    io_ = t.get("io") if isinstance(t.get("io"), dict) else {}
    try:
        mem = float(t.get("node_memory_gb") or DEFAULT_NODE_MEMORY_GB)
    except (TypeError, ValueError):
        raise Refused("TARGET_INVALID", [f"target.node_memory_gb {t.get('node_memory_gb')!r} is not a number"])
    return {"platform": "databricks", "runtime": str(t.get("runtime") or DEFAULT_RUNTIME),
            "compute": str(t.get("compute") or "job_cluster_single_node"), "node_memory_gb": mem,
            "node_memory_source": "params" if t.get("node_memory_gb") else "default",
            "volume": {k: vol.get(k) for k in ("catalog", "schema", "volume")},
            "no_seek_roots": list(io_.get("no_seek_roots") or [])}


def load_runtime(rid: str) -> tuple[dict, Path]:
    if not re.match(r"^[A-Za-z0-9._-]+$", rid):
        raise Refused("TC_RUNTIME_MANIFEST_MISSING", [f"runtime id {rid!r} is not a file key"])
    p = RUNTIMES / f"{rid}.json"
    if not p.is_file():
        raise Refused("TC_RUNTIME_MANIFEST_MISSING", [f"no runtime manifest {p.name} in skills/target/runtimes/"])
    doc = ac.load_json(p)
    for k in ("id", "python", "spark_version", "source_url", "retrieved", "kind", "python_packages"):
        if k not in doc:
            raise Refused("TC_RUNTIME_MANIFEST_MISSING", [f"{p.name} lacks {k}"])
    if doc["kind"] != "declared":
        raise Refused("TC_RUNTIME_MANIFEST_MISSING", [f"{p.name}: kind must be 'declared'"])
    return doc, p


def newest_sealed_run(mig: Path) -> str | None:
    idx = mig / "runs" / "index.jsonl"
    if not idx.is_file():
        return None
    last = None
    for line in idx.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("sealed") and row.get("run_id"):
            last = row["run_id"]
    return last


def lock_path_for(mig: Path, run_id: str | None) -> Path:
    if run_id:
        p = mig / "runs" / run_id / "snapshot" / "uv.lock"
        if not p.is_file():
            raise Refused("RUN_NOT_FOUND", [f"run {run_id} has no snapshot/uv.lock"])
        return p
    return PLUGIN_LOCK


# ---------------------------------------------------------------------------------------------------------------
# task plan (09 §3.3 generation rules)
# ---------------------------------------------------------------------------------------------------------------
def task_key(step_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", step_id)[:100]


def is_task_step(s: dict) -> bool:
    return (s.get("scope") == "migrate" and s.get("kind") not in ("manual", "bridge")
            and bool((s.get("new") or {}).get("module")))


def task_plan(graph: dict, pipeline_dir: Path) -> dict:
    """{tasks: [{task_key, step, module, depends_on}] in job order, uploads: [...], skipped: [...]}."""
    reasons = []
    steps = graph.get("steps") or []
    datasets = {d["id"]: d for d in graph.get("datasets") or []}
    tasks = [s for s in steps if is_task_step(s)]
    skipped = []
    for s in steps:
        if s in tasks or s.get("scope") == "drop":
            continue
        if s.get("scope") == "migrate" and s.get("kind") not in ("manual", "bridge"):
            reasons.append(f"STEP_WITHOUT_MODULE:{s['id']}")
            continue
        skipped.append({"step": s["id"], "kind": s.get("kind"), "scope": s.get("scope"),
                        "why": "manual step" if s.get("kind") == "manual" else
                        ("bridge" if s.get("kind") == "bridge" else f"scope {s.get('scope')}")})
    if not tasks:
        reasons.append("NO_TASK_STEPS")
    keys: dict[str, str] = {}
    for s in tasks:
        k = task_key(s["id"])
        if not TASK_KEY_RE.match(k):
            reasons.append(f"TC_TASK_KEY_INVALID:{s['id']}")
        if k in keys:
            reasons.append(f"TC_TASK_KEY_COLLISION:{keys[k]}|{s['id']}")
        keys[k] = s["id"]
        mod = (s.get("new") or {}).get("module") or ""
        rel = mod[len("pipeline/"):] if mod.startswith("pipeline/") else mod
        if rel != f"{s['id']}.py" or not (pipeline_dir / rel).is_file():
            reasons.append(f"MODULE_MISSING:{s['id']}")
    prod = {}
    for s in tasks:
        for d in s.get("outputs") or []:
            prod[d] = s["id"]
    deps = {s["id"]: sorted({prod[d] for d in s.get("inputs") or [] if d in prod and prod[d] != s["id"]})
            for s in tasks}
    uploads: dict[str, dict] = {}
    for s in tasks:
        for d in s.get("inputs") or []:
            if d in prod:
                continue
            ds = datasets.get(d)
            lg = (ds or {}).get("legacy") or {}
            if ds is None or not lg.get("path") or lg.get("exists") is False:
                reasons.append(f"TARGET_INPUT_NOT_UPLOADABLE:{d}")
                continue
            producer = None
            for o in steps:
                if d in (o.get("outputs") or []):
                    producer = o["id"]
            u = uploads.setdefault(d, {"dataset": d, "legacy_path": lg["path"], "layer": lg.get("layer"),
                                       "kind": ds.get("kind"), "produced_by_untasked_step": producer,
                                       "consumers": []})
            u["consumers"].append(s["id"])
    order, done = [], set()
    pending = sorted(deps)
    while pending:
        ready = [x for x in pending if all(p in done for p in deps[x])]
        if not ready:
            reasons.append("GRAPH_CYCLE:" + ",".join(pending))
            break
        order.append(ready[0])
        done.add(ready[0])
        pending.remove(ready[0])
    if reasons:
        raise Refused("GENERATION_REFUSED", sorted(set(reasons)))
    by_id = {s["id"]: s for s in tasks}
    return {"tasks": [{"task_key": task_key(sid), "step": sid, "module": by_id[sid]["new"]["module"],
                       "depends_on": [task_key(p) for p in deps[sid]]} for sid in order],
            "uploads": [dict(u, consumers=sorted(u["consumers"])) for _, u in sorted(uploads.items())],
            "skipped": skipped}


def datasets_manifest(graph: dict, plan: dict) -> dict:
    """The runtime's own `_datasets.json` builder over the task steps only: untasked producers become inputs."""
    rt = load_runtime_module()
    wanted = {t["step"] for t in plan["tasks"]}
    g = dict(graph, steps=[s for s in graph.get("steps") or [] if s["id"] in wanted])
    return rt.datasets_manifest_from_graph(g)


# ---------------------------------------------------------------------------------------------------------------
# requirements-target.lock (D9-5)
# ---------------------------------------------------------------------------------------------------------------
def lock_packages(lock_path: Path) -> dict[str, dict]:
    doc = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {norm(p["name"]): p for p in doc.get("package", [])}


def _marker_ok(marker: str | None) -> bool:
    if not marker:
        return True
    from packaging.markers import Marker
    try:
        return bool(Marker(marker).evaluate(TARGET_ENV))
    except Exception:  # noqa: BLE001 - an unparsable marker keeps the dependency (fail closed: more pins)
        return True


def pipeline_imports(pipeline_dir: Path) -> list[str]:
    import ast
    names = set()
    for p in sorted(pipeline_dir.glob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                names.add(n.module.split(".")[0])
    return sorted(names)


def target_pins(pipeline_dir: Path, lock_path: Path, runtime: dict) -> dict:
    """Closure of the pipeline's third-party imports in the verified lock, versions per D9-5."""
    pk = lock_packages(lock_path)
    declared = {norm(k): v for k, v in (runtime.get("python_packages") or {}).items()}
    roots = set(ROOT_DISTS)
    unknown = []
    for mod in pipeline_imports(pipeline_dir):
        if mod in sys.stdlib_module_names or mod == PACKAGE or mod.startswith("_"):
            continue
        dist = norm(IMPORT_ALIASES.get(mod, mod))
        if dist in pk:
            roots.add(dist)
        else:
            unknown.append(mod)
    closure, todo = set(), sorted(norm(r) for r in roots)
    while todo:
        n = todo.pop()
        if n in closure or n not in pk:
            continue
        closure.add(n)
        for d in pk[n].get("dependencies") or []:
            if d.get("extra"):
                continue
            if _marker_ok(d.get("marker")):
                todo.append(norm(d["name"]))
    pins = []
    for n in sorted(closure):
        lockv = pk[n]["version"]
        if n in GEO_STACK or n not in declared:
            src = "geo_stack" if n in GEO_STACK else "lock"
            ver = lockv
        else:
            src, ver = "runtime", declared[n]
        pins.append({"name": pk[n]["name"], "norm": n, "version": ver, "source": src, "lock_version": lockv,
                     "runtime_version": declared.get(n)})
    return {"pins": pins, "unknown_imports": sorted(set(unknown))}


def render_lock(pins: dict, runtime: dict, lock_sha: str) -> str:
    lines = ["# requirements-target.lock — generated by axis-migrate make_databricks.py; do not edit (09 D9-5).",
             f"# runtime-provided: {runtime['id']} DECLARED versions ({runtime['source_url']}, retrieved "
             f"{runtime['retrieved']}); nothing was observed on a cluster.",
             f"# geo stack and everything the runtime does not declare: the verified uv.lock (sha256:{lock_sha}).",
             "# Only the non-runtime pins are wheel dependencies: pip has nothing to do for runtime-provided packages.",
             f"# Python {runtime['python']} (runtime declaration)."]
    w = max(len(f"{p['name']}=={p['version']}") for p in pins["pins"]) + 2
    for p in pins["pins"]:
        req = f"{p['name']}=={p['version']}"
        note = {"runtime": "provided by the runtime (declared)", "geo_stack": "geo stack (verified uv.lock)",
                "lock": "not provided by the runtime (verified uv.lock)"}[p["source"]]
        if p["source"] == "runtime" and p["lock_version"] != p["version"]:
            note += f"; verified run used {p['lock_version']}"
        if p["source"] == "geo_stack" and p["runtime_version"] and p["runtime_version"] != p["version"]:
            note += f"; runtime declares {p['runtime_version']}"
        lines.append(f"{req:<{w}}# {note}")
    return "\n".join(lines) + "\n"


def wheel_requires(pins: dict) -> list[str]:
    return [f"{p['name']}=={p['version']}" for p in pins["pins"] if p["source"] != "runtime"]


# ---------------------------------------------------------------------------------------------------------------
# reproducible wheel (PEP 427)
# ---------------------------------------------------------------------------------------------------------------
def source_date_epoch() -> int:
    v = os.environ.get("SOURCE_DATE_EPOCH")
    try:
        return max(int(v), SOURCE_DATE_EPOCH_DEFAULT) if v else SOURCE_DATE_EPOCH_DEFAULT
    except ValueError:
        return SOURCE_DATE_EPOCH_DEFAULT


def package_files(pipeline_dir: Path, params_bytes: bytes, datasets_bytes: bytes) -> list[tuple[str, bytes]]:
    files = []
    for e in ac.tree_entries(pipeline_dir):
        if e["path"] in PACKAGED:
            raise Refused("GENERATION_REFUSED", [f"pipeline/{e['path']} clashes with packaged data"])
        files.append((f"{PACKAGE}/{e['path']}", (pipeline_dir / e["path"]).read_bytes()))
    files.append((f"{PACKAGE}/params.yaml", params_bytes))
    files.append((f"{PACKAGE}/_datasets.json", datasets_bytes))
    return sorted(files)


def _b64(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build_wheel(files: list[tuple[str, bytes]], version: str, requires: list[str], python: str) -> tuple[str, bytes]:
    name = f"{PACKAGE}-{version}-py3-none-any.whl"
    di = f"{PACKAGE}-{version}.dist-info"
    major_minor = ".".join(python.split(".")[:2])
    meta = [f"Metadata-Version: 2.1", f"Name: {PACKAGE}", f"Version: {version}",
            "Summary: Migrated GIS pipeline generated by axis-migrate (one module per verified graph step)",
            f"Requires-Python: =={major_minor}.*"] + [f"Requires-Dist: {r}" for r in requires]
    dist = [(f"{di}/METADATA", ("\n".join(meta) + "\n").encode()),
            (f"{di}/WHEEL", f"Wheel-Version: 1.0\nGenerator: {GENERATOR}\nRoot-Is-Purelib: true\n"
                            f"Tag: py3-none-any\n".encode()),
            (f"{di}/entry_points.txt", f"[console_scripts]\n{ENTRY_POINT} = {ENTRY_TARGET}\n".encode())]
    allf = list(files) + dist
    record = "".join(f"{p},sha256={_b64(hashlib.sha256(b).digest())},{len(b)}\n" for p, b in allf)
    record += f"{di}/RECORD,,\n"
    allf.append((f"{di}/RECORD", record.encode()))
    dt = time.gmtime(source_date_epoch())[:6]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in allf:
            zi = zipfile.ZipInfo(p, date_time=dt)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 3
            zi.external_attr = (0o100644 & 0xFFFF) << 16
            z.writestr(zi, b, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return name, buf.getvalue()


def wheel_members(whl: Path | bytes) -> dict[str, bytes]:
    src = io.BytesIO(whl) if isinstance(whl, (bytes, bytearray)) else whl
    with zipfile.ZipFile(src) as z:
        return {n: z.read(n) for n in z.namelist()}


def wheel_code_tree_hash(members: dict[str, bytes]) -> str:
    """ac.tree_hash form over axis_pipeline/* minus the packaged data files: equals the pipeline tree hash."""
    entries = []
    for n, b in members.items():
        if not n.startswith(PACKAGE + "/"):
            continue
        rel = n[len(PACKAGE) + 1:]
        if rel in PACKAGED:
            continue
        entries.append({"path": rel, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)})
    entries.sort(key=lambda e: e["path"])
    return "sha256:" + ac.sha256_bytes(ac.cjson(entries))


# ---------------------------------------------------------------------------------------------------------------
# databricks.yml, pyproject.toml, README
# ---------------------------------------------------------------------------------------------------------------
def names_for(graph: dict) -> dict:
    base = (graph.get("project") or {}).get("name") or "pipeline"
    s = ac.slug(base) or "pipeline"
    return {"bundle": s.replace("_", "-") + "-migration", "job_key": s, "job_name": s.replace("_", "-") + "-migration"}


def task_parameters(step_id: str) -> list[str]:
    return ["--step", step_id, "--scratch-dir", SCRATCH, "--no-seek-root", "/Volumes",
            "--input-root", f"{VOLUME}/inputs", "--work-root", f"{VOLUME}/work", "--output-root", f"{VOLUME}/outputs"]


def bundle_doc(graph: dict, plan: dict, runtime: dict, tgt: dict, wheel_file: str, tree_hex: str) -> dict:
    n = names_for(graph)
    mem = tgt["node_memory_gb"]
    mem_s = str(int(mem)) if float(mem).is_integer() else str(mem)
    tasks = []
    for t in plan["tasks"]:
        task = {"task_key": t["task_key"]}
        if t["depends_on"]:
            task["depends_on"] = [{"task_key": d} for d in t["depends_on"]]
        task["job_cluster_key"] = JOB_CLUSTER_KEY
        task["python_wheel_task"] = {"package_name": PACKAGE, "entry_point": ENTRY_POINT,
                                     "parameters": task_parameters(t["step"])}
        task["libraries"] = [{"whl": f"./dist/{wheel_file}"}]
        tasks.append(task)
    return {
        "bundle": {"name": n["bundle"]},
        "variables": {
            "catalog": {"description": "Unity Catalog catalog holding the Volume"},
            "schema": {"description": "Schema holding the Volume"},
            "volume": {"description": "Volume name (inputs/, work/ and outputs/ live under it)"},
            "node_type_id": {"description": f"Single-node VM type with at least {mem_s} GB RAM"},
        },
        "targets": {"dev": {"mode": "development", "default": True}},
        "sync": {"exclude": ["dryrun/**"]},
        "resources": {"jobs": {n["job_key"]: {
            "name": n["job_name"],
            "max_concurrent_runs": 1,
            "tags": {"generated-by": "axis-migrate", "pipeline-tree": tree_hex[:12]},
            "job_clusters": [{"job_cluster_key": JOB_CLUSTER_KEY, "new_cluster": {
                "spark_version": runtime["spark_version"],
                "node_type_id": "${var.node_type_id}",
                "num_workers": 0,
                "data_security_mode": "SINGLE_USER",
                "spark_conf": {"spark.databricks.cluster.profile": "singleNode", "spark.master": "local[*]"},
                "custom_tags": {"ResourceClass": "SingleNode"}}}],
            "tasks": tasks}}},
    }


def render_bundle(doc: dict) -> str:
    head = ("# Generated by axis-migrate make_databricks.py from graph.json + params.yaml (09 §3.3). Do not edit:\n"
            "# target_check.py re-renders this file and fails T9 on any difference. No host, token or user here:\n"
            "# deploy with your own Databricks CLI profile (README-deploy.md). Nothing was deployed.\n")
    body = yaml.safe_dump(doc, sort_keys=False, default_flow_style=False, width=1_000_000, allow_unicode=True)
    return head + body


def _toml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def render_pyproject(version: str, requires: list[str], python: str, wheel_file: str) -> str:
    mm = ".".join(python.split(".")[:2])
    deps = "".join(f"  {_toml_str(r)},\n" for r in requires)
    return (
        "# Generated by axis-migrate make_databricks.py (09 §3.1). The equivalent hatchling build of\n"
        f"# dist/{wheel_file}. That wheel was built by make_databricks.py's own reproducible builder\n"
        "# (same layout, sorted entries, fixed timestamps) and is the one target_check.py dry-ran; do not rebuild it\n"
        "# for deployment (D9-3). Run make_databricks.py again after any pipeline change.\n"
        "[build-system]\nrequires = [\"hatchling\"]\nbuild-backend = \"hatchling.build\"\n\n"
        f"[project]\nname = \"{PACKAGE}\"\nversion = {_toml_str(version)}\nrequires-python = \"=={mm}.*\"\n"
        f"dependencies = [\n{deps}]\n\n"
        f"[project.scripts]\n{ENTRY_POINT} = {_toml_str(ENTRY_TARGET)}\n\n"
        "[tool.hatch.build.targets.wheel]\nbypass-selection = true\nexclude = [\"__pycache__\", \"*.pyc\"]\n\n"
        "[tool.hatch.build.targets.wheel.force-include]\n"
        f"\"../pipeline\" = \"{PACKAGE}\"\n\"../params.yaml\" = \"{PACKAGE}/params.yaml\"\n"
        f"\"_datasets.json\" = \"{PACKAGE}/_datasets.json\"\n")


def _tmpl(name: str) -> string.Template:
    return string.Template((TEMPLATES / name).read_text(encoding="utf-8"))


def pending_text() -> str:
    return (TEMPLATES / "pending.v1.md").read_text(encoding="utf-8").strip()


def render_readme(ctx: dict, claim: str | None = None) -> str:
    return _tmpl("README-deploy.md.tmpl").substitute(dict(ctx, claim=claim or pending_text()))


def readme_context(mig: Path, graph: dict, plan: dict, dsmap: dict, runtime: dict, tgt: dict, pins: dict,
                   wheel_file: str, wheel_sha: str) -> dict:
    inv_p = mig / "inventory.json"
    inv = ac.load_json(inv_p) if inv_p.is_file() else {}
    pins_by_path = {f.get("path"): f.get("sha256") for f in inv.get("files") or []}
    legacy = ac.legacy_root(mig, graph)
    manual_ds = {u["dataset"]: u for u in plan["uploads"]}
    rows, sums = [], []
    for ds, d in sorted(dsmap["datasets"].items()):
        if d["root"] != "input":
            continue
        sha = pins_by_path.get(d["relpath"])
        if sha is None and (legacy / d["relpath"]).is_file():
            sha = ac.sha256_file(legacy / d["relpath"])
        note = ""
        u = manual_ds.get(ds)
        if u and u.get("produced_by_untasked_step"):
            note = f" (output of `{u['produced_by_untasked_step']}`, not a task)"
        rows.append(f"| `{ds}`{note} | `{d['relpath']}` | `{sha or 'unknown'}` |")
        if sha:
            sums.append(f"{sha}  {d['relpath']}")
    if plan["skipped"]:
        manual = ("Steps with no task (their outputs above are uploaded as inputs): " + ", ".join(
            f"`{s['step']}` ({s['why']})" for s in plan["skipped"]) + ". Upload the file the legacy project used; "
            "the job never recomputes it.")
    else:
        manual = "Every in-scope step is a task; nothing else needs uploading."
    tasks = "\n".join(f"| `{t['task_key']}` | " + (", ".join(f"`{d}`" for d in t["depends_on"]) or "—") + " |"
                      for t in plan["tasks"])
    n = names_for(graph)
    mem = tgt["node_memory_gb"]
    pypi = ", ".join(f"`{p['name']}=={p['version']}`" for p in pins["pins"] if p["source"] != "runtime")
    return {"bundle_name": n["bundle"], "cli_version": SCHEMA_CLI_VERSION, "runtime_name": runtime.get("name"),
            "spark_version": runtime["spark_version"],
            "node_memory_gb": str(int(mem)) if float(mem).is_integer() else str(mem), "pypi_pins": pypi or "none",
            "source_url": runtime["source_url"], "retrieved": runtime["retrieved"],
            "inputs_table": "\n".join(rows), "inputs_sha256": "\n".join(sums), "manual_note": manual,
            "job_key": n["job_key"], "first_task": plan["tasks"][0]["task_key"], "tasks_table": tasks,
            "wheel_file": wheel_file, "wheel_sha256": wheel_sha}


# ---------------------------------------------------------------------------------------------------------------
# generation
# ---------------------------------------------------------------------------------------------------------------
def generate(mig: Path, run_id: str | None = None) -> dict:
    """Everything make_databricks writes, as {relpath: bytes}, plus metadata. Pure: no file is written here."""
    graph = ac.load_json(mig / "graph.json")
    params_bytes = (mig / "params.yaml").read_bytes()
    params_doc = yaml.safe_load(params_bytes) or {}
    tgt = target_block(params_doc)
    runtime, runtime_path = load_runtime(tgt["runtime"])
    pipeline = mig / "pipeline"
    if not pipeline.is_dir():
        raise Refused("GENERATION_REFUSED", ["migration/pipeline/ is missing"])
    plan = task_plan(graph, pipeline)
    dsmap = datasets_manifest(graph, plan)
    ds_bytes = (json.dumps(dsmap, indent=1, sort_keys=True) + "\n").encode()
    tree = ac.tree_hash(pipeline)
    tree_hex = tree.split(":", 1)[1]
    version = f"{PIPELINE_VERSION}+{tree_hex[:8]}"
    lock_p = lock_path_for(mig, run_id)
    pins = target_pins(pipeline, lock_p, runtime)
    requires = wheel_requires(pins)
    wheel_file, wheel_bytes = build_wheel(package_files(pipeline, params_bytes, ds_bytes), version, requires,
                                          runtime["python"])
    wheel_sha = hashlib.sha256(wheel_bytes).hexdigest()
    bundle = render_bundle(bundle_doc(graph, plan, runtime, tgt, wheel_file, tree_hex))
    files = {
        "databricks.yml": bundle.encode(),
        "pyproject.toml": render_pyproject(version, requires, runtime["python"], wheel_file).encode(),
        "requirements-target.lock": render_lock(pins, runtime, ac.sha256_file(lock_p)).encode(),
        "runtime-manifest.json": runtime_path.read_bytes(),
        "_datasets.json": ds_bytes,
        f"dist/{wheel_file}": wheel_bytes,
    }
    rctx = readme_context(mig, graph, plan, dsmap, runtime, tgt, pins, wheel_file, wheel_sha)
    files["README-deploy.md"] = render_readme(rctx).encode()
    return {"files": files, "plan": plan, "datasets": dsmap, "target": tgt, "runtime": runtime, "pins": pins,
            "wheel_file": wheel_file, "wheel_sha256": wheel_sha, "tree_hash": tree, "version": version,
            "lock_path": str(lock_p), "readme_context": rctx}


def write(mig: Path, out: dict) -> list[str]:
    dbx = mig / "databricks"
    (dbx / "dist").mkdir(parents=True, exist_ok=True)
    for old in sorted((dbx / "dist").glob("*.whl")):
        if f"dist/{old.name}" not in out["files"]:
            old.unlink()
    for stale in ("target-check.json", "target-manifest.json"):
        if (dbx / stale).exists():          # a new bundle is unchecked until target_check.py runs again
            (dbx / stale).unlink()
    written = []
    for rel, data in sorted(out["files"].items()):
        ac.atomic_write_bytes(dbx / rel, data)
        os.chmod(dbx / rel, 0o644)
        written.append(rel)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write migration/databricks/ (Asset Bundle + wheel) deterministically.")
    ap.add_argument("migration", help="the project's migration/ directory (or the project dir holding it)")
    ap.add_argument("--run", help="verified run whose snapshot uv.lock pins the geo stack (default: newest sealed)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        mig = resolve_migration(a.migration)
        run_id = a.run or newest_sealed_run(mig)
        out = generate(mig, run_id)
        written = write(mig, out)
    except Refused as e:
        res = {"status": "refused" if e.code != "TARGET_NOT_SUPPORTED" else "TARGET_NOT_SUPPORTED",
               "code": e.code, "reasons": e.reasons, "written": []}
        if a.json:
            print(json.dumps(res, sort_keys=True, indent=1))
        else:
            print(f"make_databricks: {e.code}: " + "; ".join(e.reasons), file=sys.stderr)
            if e.code == "TARGET_NOT_SUPPORTED":
                print("nothing written; the ladder stays at reference-verified", file=sys.stderr)
        return EXIT_NOT_SUPPORTED if e.code == "TARGET_NOT_SUPPORTED" else EXIT_REFUSED
    res = {"status": "written", "databricks": str(mig / "databricks"), "written": written,
           "tasks": [t["task_key"] for t in out["plan"]["tasks"]], "uploads": [u["dataset"] for u in out["plan"]["uploads"]],
           "skipped": out["plan"]["skipped"], "wheel": out["wheel_file"], "wheel_sha256": out["wheel_sha256"],
           "runtime": out["runtime"]["id"], "lock_from": "run " + run_id if run_id else "plugin env lock",
           "unknown_imports": out["pins"]["unknown_imports"]}
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1))
    else:
        print(f"wrote migration/databricks/: {len(res['tasks'])} task(s) {', '.join(res['tasks'])}; wheel "
              f"{out['wheel_file']} (sha256 {out['wheel_sha256'][:12]}); runtime {out['runtime']['id']}")
        print("not checked yet: run target_check.py --run <run_id>; nothing was deployed")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
