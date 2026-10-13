#!/usr/bin/env python3
"""target_check.py — the offline "honest target-ready" checks T1–T13 (09 §4.1–4.3; D9-8, D9-9).

    axpy target_check.py <migration> --run <run_id> [--json] [--keep]

<run_id> is the verified run: chained, succeeded, full and sealed (`seal.py`). Reads migration/databricks/ as
written by make_databricks.py and writes:
  databricks/target-check.json     one record per check {id, title, state: PASS|FAIL|WARN|BLOCKED|NOT_RUN,
                                   reason_codes[], evidence[]}
  databricks/target-manifest.json  deployment_status "not_deployed", review "not_run", hashes binding the shipped code
                                   (wheel tree), params, graph, verified lock, target lock, wheel and bundle; runtime;
                                   status (target-ready only when T1–T10 and T13 PASS, T8 passed, nothing NOT_RUN and
                                   the pipeline itself is reference-verified); the claim rendered from
                                   templates/claim.v1.md (or the refusal text)
  databricks/README-deploy.md      section 1 re-rendered with that text
  dry-run evidence                 runs/<run_id>/target/ when the run directory is writable; the runner seals runs
                                   read-only, so normally databricks/dryrun/ (both are what `seal.py --amend --target`
                                   seals as `tgt`)
Then seal: `axpy $PLUGIN/skills/report/seal.py <migration> --run <run_id> --amend --target` (else the ladder reports
TARGET_NOT_SEALED). Nothing here deploys, authenticates or contacts a workspace.

Exit codes: 0 target-ready · 1 checks ran, not target-ready · 2 refused (run not eligible, bundle missing) ·
3 TARGET_NOT_SUPPORTED (nothing written).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import string
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLUGIN / "skills" / "report"))
import axis_common as ac  # noqa: E402
import make_databricks as md  # noqa: E402
import dryrun as dr  # noqa: E402

import yaml  # noqa: E402

VERSION = "axis-migrate target_check.py 1.0.0"
REQUIRED = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T13"]
WARN_ONLY = ["T11", "T12"]
TITLES = {"T1": "Bundle schema", "T2": "Task graph = verified graph", "T3": "Volume-unsafe I/O (static)",
          "T4": "__file__, cwd and literal paths", "T5": "Runtime installs and denied imports", "T6": "Env diff",
          "T7": "Dry run executes", "T8": "Dry-run parity", "T9": "Code and params identity",
          "T10": "No identifiers or secrets", "T11": "Memory budget (warn)", "T12": "Logging (warn)",
          "T13": "Seek trace"}
VENDORED = ("_axis_runtime.py", "__main__.py", "__init__.py")
EXIT_READY, EXIT_NOT_READY, EXIT_REFUSED, EXIT_NOT_SUPPORTED = 0, 1, 2, 3


class Refusal(Exception):
    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


def chk(cid: str, state: str, codes=(), evidence=(), **extra) -> dict:
    return {"id": cid, "title": TITLES[cid], "state": state, "reason_codes": sorted(set(codes)),
            "evidence": list(evidence), **extra}


# ---------------------------------------------------------------------------------------------------------------
# T1 — a small JSON-Schema validator for the vendored subset ($ref, oneOf/anyOf/allOf, type, properties,
# additionalProperties, required, enum, const, pattern, items)
# ---------------------------------------------------------------------------------------------------------------
def _type_ok(v, t) -> bool:
    if isinstance(t, list):
        return any(_type_ok(v, x) for x in t)
    return {"object": isinstance(v, dict), "array": isinstance(v, list), "string": isinstance(v, str),
            "integer": isinstance(v, int) and not isinstance(v, bool),
            "number": isinstance(v, (int, float)) and not isinstance(v, bool), "boolean": isinstance(v, bool),
            "null": v is None}.get(t, False)


def validate(v, schema: dict, root: dict, path: str = "$") -> list[str]:
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}: not allowed"]
    if "$ref" in schema:
        node = root
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        errs = validate(v, node, root, path)
        rest = {k: x for k, x in schema.items() if k not in ("$ref", "description")}
        return errs + (validate(v, rest, root, path) if rest else [])
    errs: list[str] = []
    if "oneOf" in schema:
        res = [validate(v, s, root, path) for s in schema["oneOf"]]
        ok = [r for r in res if not r]
        if len(ok) != 1:
            best = min(res, key=len) if res else []
            errs.append(f"{path}: matches {len(ok)} of the oneOf alternatives" + (f" ({best[0]})" if best else ""))
    if "anyOf" in schema:
        res = [validate(v, s, root, path) for s in schema["anyOf"]]
        if not any(not r for r in res):
            errs.append(f"{path}: matches none of anyOf ({min(res, key=len)[0] if res else ''})")
    for s in schema.get("allOf") or []:
        errs += validate(v, s, root, path)
    if "type" in schema and not _type_ok(v, schema["type"]):
        return errs + [f"{path}: expected {schema['type']}, got {type(v).__name__}"]
    if "enum" in schema and v not in schema["enum"]:
        errs.append(f"{path}: {v!r} not in {schema['enum']}")
    if "const" in schema and v != schema["const"]:
        errs.append(f"{path}: must be {schema['const']!r}")
    if isinstance(v, str) and "pattern" in schema and not re.search(schema["pattern"], v):
        errs.append(f"{path}: {v!r} does not match {schema['pattern']}")
    if isinstance(v, dict):
        props = schema.get("properties") or {}
        for r in schema.get("required") or []:
            if r not in v:
                errs.append(f"{path}: missing required {r}")
        for k, x in v.items():
            if k in props:
                errs += validate(x, props[k], root, f"{path}.{k}")
            elif "additionalProperties" in schema:
                ap = schema["additionalProperties"]
                if ap is False:
                    errs.append(f"{path}: unknown key {k!r} (not in the schema subset)")
                elif isinstance(ap, dict):
                    errs += validate(x, ap, root, f"{path}.{k}")
    if isinstance(v, list) and isinstance(schema.get("items"), dict):
        for i, x in enumerate(v):
            errs += validate(x, schema["items"], root, f"{path}[{i}]")
    return errs


def check_t1(dbx: Path) -> dict:
    ev = [f"schema: skills/target/schema/{md.SCHEMA_FILE.name} (subset of databricks/cli {md.SCHEMA_CLI_VERSION} "
          f"bundle schema; `databricks bundle validate` not run: it needs authentication)"]
    try:
        doc = yaml.safe_load((dbx / "databricks.yml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        return chk("T1", "FAIL", ["TC_SCHEMA_INVALID"], ev + [f"unreadable databricks.yml: {e}"])
    schema = ac.load_json(md.SCHEMA_FILE)
    errs = validate(doc, schema, schema)
    return chk("T1", "FAIL" if errs else "PASS", ["TC_SCHEMA_INVALID"] if errs else [], ev + errs[:30])


# ---------------------------------------------------------------------------------------------------------------
# T2 — tasks ↔ verified graph
# ---------------------------------------------------------------------------------------------------------------
def check_t2(dbx: Path, run_dir: Path, dres: dict | None, wheel_file: str | None) -> dict:
    codes, ev = [], []
    graph = ac.load_json(run_dir / "snapshot" / "graph.json")
    try:
        plan = md.task_plan(graph, run_dir / "snapshot" / "axis_pipeline")
    except md.Refused as e:
        return chk("T2", "FAIL", ["TC_TASK_GRAPH_MISMATCH"], [f"verified graph: {r}" for r in e.reasons])
    bundle = yaml.safe_load((dbx / "databricks.yml").read_text(encoding="utf-8")) or {}
    try:
        _, tasks = dr.bundle_tasks(bundle)
    except dr.SetupError as e:
        return chk("T2", "FAIL", ["TC_TASK_GRAPH_MISMATCH"], [str(e)])
    want = {t["task_key"]: t for t in plan["tasks"]}
    have = {t["task_key"]: t for t in tasks}
    if set(want) != set(have):
        codes.append("TC_TASK_GRAPH_MISMATCH")
        ev.append(f"tasks {sorted(have)} ≠ non-manual graph steps {sorted(want)}")
    clusters = {c.get("job_cluster_key") for j in ((bundle.get("resources") or {}).get("jobs") or {}).values()
                for c in j.get("job_clusters") or []}
    for k in sorted(set(want) & set(have)):
        w, h = want[k], have[k]
        deps = sorted(d["task_key"] for d in h.get("depends_on") or [])
        if deps != sorted(w["depends_on"]):
            codes.append("TC_TASK_GRAPH_MISMATCH")
            ev.append(f"{k}: depends_on {deps} ≠ graph edges {sorted(w['depends_on'])}")
        pwt = h.get("python_wheel_task") or {}
        if pwt.get("package_name") != md.PACKAGE or pwt.get("entry_point") != md.ENTRY_POINT:
            codes.append("TC_TASK_GRAPH_MISMATCH")
            ev.append(f"{k}: python_wheel_task {pwt.get('package_name')}/{pwt.get('entry_point')}")
        if list(pwt.get("parameters") or []) != md.task_parameters(w["step"]):
            codes.append("TC_TASK_GRAPH_MISMATCH")
            ev.append(f"{k}: parameters differ from the generated ones for step {w['step']}")
        if h.get("job_cluster_key") not in clusters:
            codes.append("TC_TASK_GRAPH_MISMATCH")
            ev.append(f"{k}: job_cluster_key {h.get('job_cluster_key')!r} not declared")
        libs = [lib.get("whl") for lib in h.get("libraries") or []]
        if libs != [f"./dist/{wheel_file}"]:
            codes.append("TC_TASK_GRAPH_MISMATCH")
            ev.append(f"{k}: libraries {libs} ≠ the committed wheel ./dist/{wheel_file}")
    ev.append(f"{len(have)} task(s): " + ", ".join(t["task_key"] for t in tasks))
    if plan["skipped"]:
        ev.append("no task (outputs uploaded as inputs): " + ", ".join(f"{s['step']} ({s['why']})"
                                                                      for s in plan["skipped"]))
    probe = (dres or {}).get("probe")
    if not probe or probe.get("error"):
        ev.append("entry point and step modules not confirmed: no dry-run environment")
        return chk("T2", "FAIL" if codes else "BLOCKED", codes or ["TC_DRYRUN_ENV_UNAVAILABLE"], ev)
    if probe.get("entry_point") != md.ENTRY_TARGET:
        codes.append("TC_TASK_GRAPH_MISMATCH")
        ev.append(f"console script axis-pipeline → {probe.get('entry_point')!r} in the dry-run venv")
    missing = sorted(s for s, ok in (probe.get("modules") or {}).items() if not ok)
    want_steps = {t["step"] for t in plan["tasks"]}
    if missing or not want_steps <= set(probe.get("modules") or {}):
        codes.append("TC_TASK_GRAPH_MISMATCH")
        ev.append(f"step modules missing from the installed wheel: {missing or sorted(want_steps)}")
    else:
        ev.append(f"importlib.metadata in the dry-run venv: entry point {probe.get('entry_point')}, "
                  f"{len(want_steps)} step module(s) importable")
    return chk("T2", "FAIL" if codes else "PASS", codes, ev)


# ---------------------------------------------------------------------------------------------------------------
# T3, T4, T5, T12 — AST over the shipped step modules (the wheel's axis_pipeline/*.py)
# ---------------------------------------------------------------------------------------------------------------
LOCAL_RE = re.compile(r"^(/Users|/home|/tmp|/dbfs|/Workspace|/Volumes|[A-Za-z]:\\|dbfs:)")
WRITE_ATTRS = {"to_file", "to_excel"}
GDAL_WRITES = {"Translate", "Warp", "Rasterize", "BuildVRT", "Grid", "DEMProcessing", "VectorTranslate", "Nearblack",
               "Create", "CreateCopy", "Polygonize", "ContourGenerate"}
READ_FUNCS = {"read_file", "read_dataframe", "read_info", "read_arrow", "load_workbook", "read_excel"}
DENIED_IMPORTS = {"sedona", "pyspark", "mosaic", "databricks", "dbutils", "pip"}


def _dotted(n) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return _dotted(n.value) + "." + n.attr
    if isinstance(n, ast.Call):
        return _dotted(n.func) + "()"
    return "?"


class _Scan(ast.NodeVisitor):
    def __init__(self, fname: str, allow_osgeo: bool):
        self.f = fname
        self.allow_osgeo = allow_osgeo
        self.hits: list[tuple[str, str, int, str]] = []   # (check, code, line, detail)
        self.derived: list[dict[str, str]] = [{}]
        self.docstrings: set[int] = set()
        self.imports_openpyxl = False

    def hit(self, check, code, node, detail):
        self.hits.append((check, code, getattr(node, "lineno", 0), f"{self.f}:{getattr(node, 'lineno', 0)} {detail}"))

    # --- ctx-derived paths: ctx.output_path(...) / ctx.input_path(...), possibly wrapped in str()/Path()
    def kind_of(self, e) -> str | None:
        if isinstance(e, ast.Call):
            if isinstance(e.func, ast.Attribute) and e.func.attr in ("output_path", "input_path"):
                return "out" if e.func.attr == "output_path" else "in"
            if _dotted(e.func) in ("str", "Path", "pathlib.Path", "os.fspath") and len(e.args) == 1:
                return self.kind_of(e.args[0])
        if isinstance(e, ast.Name):
            for scope in reversed(self.derived):
                if e.id in scope:
                    return scope[e.id]
        return None

    def visit_FunctionDef(self, node):
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            self.docstrings.add(id(node.body[0].value))
        self.derived.append({})
        self.generic_visit(node)
        self.derived.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Module(self, node):
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            self.docstrings.add(id(node.body[0].value))
        self.generic_visit(node)

    def visit_Assign(self, node):
        k = self.kind_of(node.value)
        for t in node.targets:
            if isinstance(t, ast.Name):
                if k:
                    self.derived[-1][t.id] = k
                else:
                    self.derived[-1].pop(t.id, None)
        self.generic_visit(node)

    def visit_withitem(self, node):
        self.generic_visit(node)

    def visit_Import(self, node):
        for a in node.names:
            self._imp(node, a.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module and node.level == 0:
            self._imp(node, node.module)
            if node.module == "importlib" and any(a.name == "reload" for a in node.names):
                self.hit("T5", "TC_DENIED_IMPORT", node, "from importlib import reload")
        self.generic_visit(node)

    def _imp(self, node, name):
        root = name.split(".")[0]
        if root == "openpyxl":
            self.imports_openpyxl = True
        if root in DENIED_IMPORTS:
            self.hit("T5", "TC_RUNTIME_PIP" if root == "pip" else "TC_DENIED_IMPORT", node, f"import {name}")
        if root == "osgeo" and not self.allow_osgeo:
            self.hit("T5", "TC_DENIED_IMPORT", node, "import osgeo (the runtime manifest declares no GDAL bindings)")

    def visit_Name(self, node):
        if node.id == "__file__":
            self.hit("T4", "TC_DUNDER_FILE", node, "__file__")
        if node.id == "dbutils":
            self.hit("T5", "TC_DENIED_IMPORT", node, "dbutils")
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str) and id(node) not in self.docstrings:
            s = node.value
            if LOCAL_RE.match(s):
                self.hit("T4", "TC_LOCAL_PATH_LITERAL", node, f"literal {s[:60]!r}")
            if s.lstrip().startswith("%pip"):
                self.hit("T5", "TC_RUNTIME_PIP", node, "%pip")
        self.generic_visit(node)

    def visit_Call(self, node):
        d = _dotted(node.func)
        last = d.rsplit(".", 1)[-1]
        args = node.args
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        first = args[0] if args else (kw.get("fp") or kw.get("path") or kw.get("filename") or kw.get("path_or_buf"))
        if d in ("os.getcwd", "Path.cwd", "pathlib.Path.cwd", "os.getcwdb"):
            self.hit("T4", "TC_LOCAL_PATH_LITERAL", node, f"{d}()")
        if d in ("importlib.reload", "imp.reload"):
            self.hit("T5", "TC_DENIED_IMPORT", node, d)
        if d.startswith(("subprocess.", "os.system", "os.popen", "os.exec", "os.spawn")):
            for c in ast.walk(node):
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and re.search(r"\bpip3?\b|ensurepip",
                                                                                          c.value):
                    self.hit("T5", "TC_RUNTIME_PIP", node, f"{d}(… {c.value[:40]!r} …)")
                    break
        if d in ("print", "builtins.print"):
            self.hit("T12", "TC_PRINT_NOT_LOGGED", node, "print(")
        # --- writes
        unsafe_w = None
        if last in WRITE_ATTRS and isinstance(node.func, ast.Attribute):
            unsafe_w = f".{last}("
        elif last == "ExcelWriter":
            unsafe_w = "ExcelWriter("
        elif d in ("rasterio.open",) or (last == "open" and d.startswith("rasterio")):
            mode = args[1] if len(args) > 1 else kw.get("mode")
            if isinstance(mode, ast.Constant) and mode.value in ("w", "w+", "r+", "a"):
                unsafe_w = f"rasterio.open(…, {mode.value!r})"
        elif d == "sqlite3.connect":
            unsafe_w = "sqlite3.connect("
        elif last == "ZipFile":
            mode = args[1] if len(args) > 1 else kw.get("mode")
            if isinstance(mode, ast.Constant) and mode.value in ("w", "a", "x"):
                unsafe_w = f"ZipFile(…, {mode.value!r})"
        elif d.startswith(("gdal.", "osgeo.gdal.")) and last in GDAL_WRITES:
            unsafe_w = f"{d}("
        elif last in ("Create", "CreateCopy") and isinstance(node.func, ast.Attribute) and "Driver" in d:
            unsafe_w = f"{d}("
        elif last == "save" and isinstance(node.func, ast.Attribute) and self.imports_openpyxl and len(args) == 1:
            unsafe_w = ".save("
        if unsafe_w and self.kind_of(first) != "out":
            self.hit("T3", "TC_VOLUME_UNSAFE_WRITE", node, f"{unsafe_w} on a path not from ctx.output_path "
                                                           f"(bypasses the staging helper)")
        # --- reads of formats that need random access / SQLite locks
        if last in READ_FUNCS and self.kind_of(first) is None:
            self.hit("T3", "TC_VOLUME_UNSAFE_READ", node, f"{d}( on a path not from ctx.input_path (no staging)")
        if d == "fiona.open" and self.kind_of(first) is None:
            self.hit("T3", "TC_VOLUME_UNSAFE_READ", node, "fiona.open( on a path not from ctx.input_path")
        self.generic_visit(node)


def scan_modules(members: dict[str, bytes], allow_osgeo: bool) -> tuple[list[tuple], list[str]]:
    hits, files = [], []
    rt = {n: (md.RUNTIME_SRC.parent / n).read_bytes() for n in VENDORED if (md.RUNTIME_SRC.parent / n).is_file()}
    for name in sorted(members):
        if not name.startswith(md.PACKAGE + "/") or not name.endswith(".py"):
            continue
        rel = name[len(md.PACKAGE) + 1:]
        if rel in VENDORED and rt.get(rel) == members[name]:
            continue                       # the vendored runtime (byte-identical to the plugin's): the staging helper
        files.append(rel)
        try:
            tree = ast.parse(members[name].decode("utf-8"))
        except (SyntaxError, UnicodeDecodeError) as e:
            hits.append(("T3", "TC_PARSE_ERROR", 0, f"{rel}: {e}"))
            continue
        s = _Scan(rel, allow_osgeo)
        s.visit(tree)
        hits += s.hits
    return hits, files


def checks_static(members: dict[str, bytes], runtime: dict) -> list[dict]:
    hits, files = scan_modules(members, bool(runtime.get("gdal_python_bindings")))
    base = [f"AST over the shipped step modules: {', '.join(files) or 'none'} (vendored runtime excluded: it is "
            f"the staging helper)"]
    out = []
    for cid in ("T3", "T4", "T5", "T12"):
        mine = [h for h in hits if h[0] == cid]
        if cid == "T3":
            mine += [h for h in hits if h[1] == "TC_PARSE_ERROR"]
        state = "PASS" if not mine else ("WARN" if cid == "T12" else "FAIL")
        out.append(chk(cid, state, [h[1] for h in mine], base + [h[3] for h in mine][:40]))
    return out


# ---------------------------------------------------------------------------------------------------------------
# T6 — env diff (diagnostics)
# ---------------------------------------------------------------------------------------------------------------
def check_t6(dbx: Path, run_dir: Path, runtime: dict, dres: dict | None) -> dict:
    lock = md.lock_packages(run_dir / "snapshot" / "uv.lock")
    pins = {}
    for line in (dbx / "requirements-target.lock").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9._-]+)==(\S+)", line)
        if m:
            pins[md.norm(m.group(1))] = m.group(2)
    declared = {md.norm(k): v for k, v in (runtime.get("python_packages") or {}).items()}
    observed = {}
    for x in ((dres or {}).get("venv") or {}).get("freeze") or []:
        m = re.match(r"^([A-Za-z0-9._-]+)==(\S+)", x)
        if m:
            observed[md.norm(m.group(1))] = m.group(2)
    rows = []
    for n in sorted(set(pins) | set(observed)):
        r = {"package": n, "verified_run": (lock.get(n) or {}).get("version"), "target_lock": pins.get(n),
             "runtime_declared": declared.get(n), "dryrun_venv": observed.get(n)}
        vals = {v for v in (r["verified_run"], r["target_lock"], r["dryrun_venv"]) if v}
        if len(vals) > 1 or (r["dryrun_venv"] and not r["target_lock"]):
            rows.append(r)
    probe_run = ac.load_json(run_dir / "env" / "probe.json") if (run_dir / "env" / "probe.json").is_file() else {}
    pyrun = str(probe_run.get("python") or "")
    native = {"python": {"verified_run": pyrun.split()[0] if pyrun else None, "runtime_declared": runtime.get("python"),
                         "dryrun_venv": ((dres or {}).get("venv") or {}).get("python_used")},
              "dryrun_native": ((dres or {}).get("probe") or {}).get("native"),
              "verified_run_native": probe_run.get("native")}
    ev = [f"{len(rows)} package difference(s) between the verified run's uv.lock, requirements-target.lock and the "
          f"dry-run venv; a difference is not a failure in itself, T8 decides"]
    ev += [f"{r['package']}: run {r['verified_run']} · target {r['target_lock']} · runtime {r['runtime_declared']} · "
           f"dry run {r['dryrun_venv']}" for r in rows]
    pc = ((dres or {}).get("venv") or {}).get("pip_check")
    if pc:
        ev.append("uv pip check in the dry-run venv: " + ("compatible" if pc.get("ok") else pc.get("output", "")[:400]))
    extra = sorted(n for n in observed if n not in pins and n != "axis-pipeline")
    if extra:
        ev.append("resolved in the dry run from the runtime's declared set (dependencies of runtime-provided "
                  "packages, not in the target lock): " + ", ".join(f"{n}=={observed[n]}" for n in extra))
    dev = ((dres or {}).get("venv") or {}).get("deviation")
    if dev:
        ev.append(f"Python deviation: {dev}")
    return chk("T6", "PASS", [], ev, diff=rows, versions=native)


# ---------------------------------------------------------------------------------------------------------------
# T9 — identity; T10 — identifiers
# ---------------------------------------------------------------------------------------------------------------
def check_t9(mig: Path, run_id: str, members: dict[str, bytes], dbx: Path) -> tuple[dict, dict | None]:
    run_dir = mig / "runs" / run_id
    snap = run_dir / "snapshot"
    codes, ev = [], []
    snap_tree = ac.tree_hash(snap / "axis_pipeline")
    live_tree = ac.tree_hash(mig / "pipeline")
    wheel_tree = md.wheel_code_tree_hash(members)
    if live_tree != snap_tree:
        codes.append("TC_CODE_CHANGED_SINCE_VERIFY")
        ev.append(f"pipeline/ tree {live_tree[7:19]} ≠ verified {snap_tree[7:19]}")
    if wheel_tree != snap_tree:
        codes.append("TC_CODE_CHANGED_SINCE_VERIFY")
        ev.append(f"wheel package tree {wheel_tree[7:19]} ≠ verified {snap_tree[7:19]}")
    sp = ac.sha256_file(snap / "params.yaml")
    if ac.sha256_file(mig / "params.yaml") != sp:
        codes.append("TC_PARAMS_CHANGED")
        ev.append("live params.yaml ≠ the verified run's")
    wp = members.get(f"{md.PACKAGE}/params.yaml")
    if wp is None or ac.sha256_bytes(wp) != sp:
        codes.append("TC_PARAMS_CHANGED")
        ev.append("params.yaml packaged in the wheel ≠ the verified run's")
    sg = ac.sha256_file(snap / "graph.json")
    if ac.sha256_file(mig / "graph.json") != sg:
        codes.append("TC_GRAPH_CHANGED")
        ev.append("live graph.json ≠ the verified run's")
    regen = None
    try:
        regen = md.generate(mig, run_id)
    except md.Refused as e:
        codes.append("TC_GENERATED_FILE_EDITED")
        ev.append(f"cannot regenerate the bundle: {e}")
    if regen is not None:
        for rel, data in sorted(regen["files"].items()):
            if rel == "README-deploy.md":
                continue
            p = dbx / rel
            if not p.is_file() or p.read_bytes() != data:
                codes.append("TC_GENERATED_FILE_EDITED")
                ev.append(f"{rel} differs from make_databricks.py's output (edited, stale or not generated)")
        extra = sorted(p.name for p in (dbx / "dist").glob("*.whl") if f"dist/{p.name}" not in regen["files"])
        if extra:
            codes.append("TC_GENERATED_FILE_EDITED")
            ev.append(f"unexpected wheel(s) in dist/: {extra}")
        try:
            plan = md.task_plan(ac.load_json(snap / "graph.json"), snap / "axis_pipeline")
            want = md.datasets_manifest(ac.load_json(snap / "graph.json"), plan)
            got = json.loads(members.get(f"{md.PACKAGE}/_datasets.json", b"{}"))
            if got != want:
                codes.append("TC_GRAPH_CHANGED")
                ev.append("_datasets.json in the wheel ≠ the verified graph's dataset map")
        except md.Refused as e:
            codes.append("TC_GRAPH_CHANGED")
            ev.append(f"verified graph: {e}")
    if not codes:
        ev.append(f"pipeline tree {snap_tree[7:19]}, params, graph and the wheel's content equal the verified run "
                  f"{run_id}; every generated file equals a fresh make_databricks.py output")
    return chk("T9", "FAIL" if codes else "PASS", codes, ev), regen


ID_PATTERNS = [
    ("workspace host", re.compile(r"\b[a-z0-9][a-z0-9-]*\.(?:cloud\.databricks\.com|azuredatabricks\.net|"
                                  r"gcp\.databricks\.com|cloud\.databricks\.us|databricks\.azure\.cn)\b", re.I)),
    ("token", re.compile(r"\bdapi[0-9a-f]{32}(?:-\d+)?\b|\bdose[0-9a-f]{32}\b", re.I)),
    ("e-mail", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")),
    ("cluster id", re.compile(r"\b\d{4}-\d{6}-[a-z0-9]{8}\b")),
    ("workspace id", re.compile(r"[?&]o=\d{6,}\b")),
    (".env content", re.compile(r"^\s*(?:export\s+)?DATABRICKS_[A-Z_]+\s*=", re.M)),
]
SCAN_SKIP = {"target-check.json", "target-manifest.json"}


def check_t10(dbx: Path) -> dict:
    hits = []

    def scan(label: str, text: str):
        for what, rx in ID_PATTERNS:
            for m in rx.finditer(text):
                hits.append(f"{label}: {what} {m.group(0)[:6]}… (redacted)")
    for p in sorted(dbx.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(dbx).as_posix()
        if rel.startswith("dryrun/") or rel in SCAN_SKIP:
            continue
        if p.name.startswith(".env"):
            hits.append(f"{rel}: .env file in the bundle")
        if p.suffix == ".whl":
            for n, b in sorted(md.wheel_members(p).items()):
                scan(f"{rel}!{n}", b.decode("utf-8", errors="replace"))
        else:
            scan(rel, p.read_bytes().decode("utf-8", errors="replace"))
    ev = ["regex over migration/databricks/ (and every member of the wheel): workspace hosts, dapi/dose tokens, "
          "e-mail addresses, cluster ids, workspace ids, DATABRICKS_* assignments"] + hits[:30]
    return chk("T10", "FAIL" if hits else "PASS", ["TC_IDENTIFIER_IN_BUNDLE"] if hits else [], ev)


# ---------------------------------------------------------------------------------------------------------------
# T7, T8, T11, T13 from the dry run
# ---------------------------------------------------------------------------------------------------------------
def checks_dynamic(dres: dict | None, derr: str | None, ev_rel: str, mem_gb: float) -> list[dict]:
    if dres is None:
        why = [f"dry run could not start: {derr}"]
        return [chk("T7", "BLOCKED", ["TC_DRYRUN_ENV_UNAVAILABLE"], why), chk("T8", "NOT_RUN", [], why),
                chk("T11", "NOT_RUN", [], why), chk("T13", "NOT_RUN", [], why)]
    out = []
    venv = dres.get("venv") or {}
    tasks = dres.get("tasks") or []
    if not venv.get("install_ok"):
        why = [f"no dry-run environment: {venv.get('error')} (see {ev_rel}/logs/install.json)"]
        return [chk("T7", "BLOCKED", ["TC_DRYRUN_ENV_UNAVAILABLE"], why), chk("T8", "NOT_RUN", [], why),
                chk("T11", "NOT_RUN", [], why), chk("T13", "NOT_RUN", [], why)]
    failed = [t["task_key"] for t in tasks if t.get("exit_code") != 0]
    ev7 = [f"{len(tasks) - len(failed)}/{len(tasks)} task(s) exited 0 in a fresh venv (Python "
           f"{venv.get('python_used')}), python -I, empty cwd, no PYTHONPATH, temporary HOME; "
           f"{(dres.get('network') or {}).get('note') or 'network isolated (unshare -n)'}",
           f"{ev_rel}/dryrun.json", f"{ev_rel}/layout.json"]
    ev7 += [f"{t['task_key']}: exit {t.get('exit_code')} (see {ev_rel}/logs/{t['task_key']}.stderr)" for t in tasks
            if t.get("exit_code") != 0]
    out.append(chk("T7", "FAIL" if failed or not tasks else "PASS", ["TC_DRYRUN_TASK_FAILED"] if failed else [], ev7))
    cmp_ = dres.get("compare") or {}
    if failed:
        out.append(chk("T8", "NOT_RUN", [], ["not every task ran, so parity was not checked"]))
    else:
        rep_codes = []
        try:
            rep = ac.load_json(Path(dres["_evidence"]) / "compare-report.json")
        except (OSError, KeyError, ValueError):
            rep = {}
        for e in (rep.get("datasets") or {}).values():
            if e.get("state") == "compared" and e.get("verdict") not in ac.PASS_STATES:
                rep_codes += e.get("reason_codes") or []
        n, npass = cmp_.get("n_compared") or 0, cmp_.get("n_pass") or 0
        n_run = rep.get("n_compared_in_run", n)
        problems = list(cmp_.get("problems") or [])
        ok = (cmp_.get("verdict") in ac.PASS_STATES and n > 0 and n == npass and n == n_run and not problems)
        ev8 = [f"{npass} of {n} step and final outputs match the legacy reference ({cmp_.get('verdict')}); "
               f"comparator and tolerances of the verified run (report {str(cmp_.get('report_id'))[11:23]})",
               f"{ev_rel}/compare-report.json"] + [f"problem: {p}" for p in problems]
        if n != n_run:
            ev8.append(f"the run compared {n_run} dataset(s) the job does not all produce")
        out.append(chk("T8", "PASS" if ok else "FAIL",
                       [] if ok else ["TC_DRYRUN_PARITY_FAIL"] + rep_codes + [p.split(":")[0] for p in problems], ev8,
                       verdict=cmp_.get("verdict"), n_compared=n, n_pass=npass, report_id=cmp_.get("report_id")))
    rss = dres.get("rss") or {}
    budget = 0.6 * mem_gb * 1024
    peaks = {k: v.get("peak_rss_mb") for k, v in rss.items()}
    over = sorted(k for k, v in peaks.items() if v is not None and v > budget)
    ev11 = [f"peak RSS per task (MB): " + ", ".join(f"{k} {v}" for k, v in sorted(peaks.items())),
            f"budget 0.6 × node_memory_gb {mem_gb:g} GB = {budget:.0f} MB", f"{ev_rel}/rss.json"]
    out.append(chk("T11", "WARN" if over else ("PASS" if peaks else "NOT_RUN"),
                   ["TC_MEMORY_BUDGET"] if over else [], ev11, peak_rss_mb=peaks))
    tr = dres.get("trace") or {}
    if not tr.get("strace"):
        out.append(chk("T13", "BLOCKED", ["TC_NO_STRACE"],
                       ["strace is not available on this host, so the seek/SQLite emulation could not run; "
                        "status stays reference-verified. Run the dry run on Linux with strace, or in an E2B "
                        "sandbox (opt-in), to check it"]))
    else:
        codes = tr.get("codes") or []
        traced = len([t for t in tasks if t.get("exit_code") is not None])
        ev13 = [f"strace -f -y on {traced} task(s); rules: no seek/pwrite/ftruncate on a write fd under the Volume, "
                f"no O_RDWR open of *.gpkg|*.sqlite|*.db, no -journal/-wal/-shm, no POSIX record lock there; "
                f"random reads allowed (assumption)", f"{ev_rel}/trace-findings.json", f"{ev_rel}/trace.log"]
        try:
            tf = ac.load_json(Path(dres["_evidence"]) / "trace-findings.json")
            ev13 += [f"{f['task']}: {f['code']} {f['rule']} {f['path']}" for f in tf.get("findings", [])[:20]]
        except (OSError, KeyError, ValueError):
            pass
        out.append(chk("T13", "FAIL" if codes else "PASS", codes, ev13))
    return out


# ---------------------------------------------------------------------------------------------------------------
# claim, manifest
# ---------------------------------------------------------------------------------------------------------------
def render_claim(**v) -> str:
    return string.Template((md.TEMPLATES / "claim.v1.md").read_text(encoding="utf-8").strip()).substitute(v)


def render_refusal(failing: list[str], rung: str) -> str:
    return string.Template((md.TEMPLATES / "refusal.v1.md").read_text(encoding="utf-8").strip()).substitute(
        failing=", ".join(failing) or "none", rung=rung)


def run_eligibility(mig: Path, run_id: str) -> dict:
    run_dir = mig / "runs" / run_id
    if not (run_dir / "manifest.json").is_file():
        raise Refusal("RUN_NOT_FOUND", f"runs/{run_id}/manifest.json missing")
    man = ac.load_json(run_dir / "manifest.json")
    probs = []
    if man.get("chain") != "chained":
        probs.append(f"chain {man.get('chain')} (needs chained)")
    if man.get("status") != "succeeded":
        probs.append(f"status {man.get('status')}")
    if not man.get("full"):
        probs.append("not full")
    if not (run_dir / "verify" / "evidence.json").is_file():
        probs.append("not sealed (run seal.py first)")
    if probs:
        raise Refusal("TARGET_RUN_NOT_ELIGIBLE", f"run {run_id}: " + "; ".join(probs))
    return man


def pre_target_rung(mig: Path, run_id: str) -> tuple[str, list[dict]]:
    import ladder
    res = ladder.compute(mig, run_id)
    rung = res["pipeline"]["rung"]
    tgt = (res["pipeline"].get("target") or {}).get("reasons") or []
    return ("reference-verified" if rung == "target-ready" else rung), tgt


def evidence_dir(mig: Path, run_dir: Path) -> Path:
    """runs/<id>/target/ if the run directory is writable (it normally is not: the runner seals it), else
    databricks/dryrun/. seal.py --amend --target seals both locations."""
    if os.access(run_dir, os.W_OK) and not (run_dir / "verify" / "evidence.json").is_file():
        return run_dir / "target"
    return mig / "databricks" / "dryrun"


def target_check(mig: Path, run_id: str, *, keep: bool = False, use_strace: bool = True) -> tuple[int, dict]:
    t0 = time.monotonic()
    params_doc = yaml.safe_load((mig / "params.yaml").read_text(encoding="utf-8")) or {}
    tgt = md.target_block(params_doc)            # raises Refused (not configured / not supported)
    run_eligibility(mig, run_id)
    run_dir = mig / "runs" / run_id
    dbx = mig / "databricks"
    for f in ("databricks.yml", "requirements-target.lock", "runtime-manifest.json"):
        if not (dbx / f).is_file():
            raise Refusal("TARGET_NOT_GENERATED", f"databricks/{f} missing: run make_databricks.py first")
    whls = sorted((dbx / "dist").glob("*.whl")) if (dbx / "dist").is_dir() else []
    if len(whls) != 1:
        raise Refusal("TARGET_NOT_GENERATED", f"expected exactly one wheel in databricks/dist/, found {len(whls)}")
    wheel = whls[0]
    members = md.wheel_members(wheel)
    rung, _ = pre_target_rung(mig, run_id)
    runtime_ok = True
    try:
        runtime = ac.load_json(dbx / "runtime-manifest.json")
        for k in ("id", "python", "source_url", "retrieved", "kind", "python_packages"):
            runtime[k]  # noqa: B018
    except (OSError, ValueError, KeyError):
        runtime, runtime_ok = {}, False
    checks = {}
    checks["T1"] = check_t1(dbx)
    for c in checks_static(members, runtime):
        checks[c["id"]] = c
    checks["T9"], regen = check_t9(mig, run_id, members, dbx)
    checks["T10"] = check_t10(dbx)
    # ---- dry run
    ev_dir = evidence_dir(mig, run_dir)
    ev_rel = ev_dir.relative_to(mig).as_posix()
    dres, derr = None, None
    try:
        dres = dr.dry_run(mig, run_id, ev_dir, keep=keep, use_strace=use_strace)
        dres["_evidence"] = str(ev_dir)
    except (dr.SetupError, md.Refused) as e:
        derr = str(e)
    checks["T2"] = check_t2(dbx, run_dir, dres, wheel.name)
    if runtime_ok:
        checks["T6"] = check_t6(dbx, run_dir, runtime, dres)
    else:
        checks["T6"] = chk("T6", "BLOCKED", ["TC_RUNTIME_MANIFEST_MISSING"], ["databricks/runtime-manifest.json "
                                                                               "missing or incomplete"])
    for c in checks_dynamic(dres, derr, ev_rel, tgt["node_memory_gb"]):
        checks[c["id"]] = c
    order = [f"T{i}" for i in range(1, 14)]
    items = [checks[i] for i in order]
    failing = [c["id"] for c in items if c["id"] in REQUIRED and c["state"] != "PASS"]
    failing += [c["id"] for c in items if c["id"] in WARN_ONLY and c["state"] not in ("PASS", "WARN")]
    warnings = [c["id"] for c in items if c["state"] == "WARN"]
    t8 = checks["T8"]
    ready = (not failing and rung == "reference-verified" and t8.get("verdict") in ac.PASS_STATES)
    wheel_sha = ac.sha256_file(wheel)
    snap = run_dir / "snapshot"
    hashes = {"code": md.wheel_code_tree_hash(members), "pipeline_tree": ac.tree_hash(mig / "pipeline"),
              "params": "sha256:" + ac.sha256_bytes(members.get(f"{md.PACKAGE}/params.yaml", b"")),
              "graph": "sha256:" + ac.sha256_file(mig / "graph.json"),
              "lock": "sha256:" + ac.sha256_file(snap / "uv.lock"),
              "target_lock": "sha256:" + ac.sha256_file(dbx / "requirements-target.lock"),
              "wheel": "sha256:" + wheel_sha, "bundle": "sha256:" + ac.sha256_file(dbx / "databricks.yml"),
              "pyproject": "sha256:" + ac.sha256_file(dbx / "pyproject.toml") if (dbx / "pyproject.toml").is_file()
              else None,
              "runtime_manifest": "sha256:" + ac.sha256_file(dbx / "runtime-manifest.json"),
              "datasets_json": "sha256:" + ac.sha256_bytes(members.get(f"{md.PACKAGE}/_datasets.json", b""))}
    claim_vals = {"w12": wheel_sha[:12], "t12": hashes["code"][7:19], "run_id": run_id,
                  "runtime": runtime.get("id", "?"), "source_url": runtime.get("source_url", "?"),
                  "date": runtime.get("retrieved", "?"), "n": str(t8.get("n_compared") or 0),
                  "report_id12": str(t8.get("report_id") or "")[len("dryrun-cmp:"):][:12]}
    tc = {"schema": "axis-migrate.target-check.v1", "run_id": run_id, "checks": items,
          "required": REQUIRED, "warn_only": WARN_ONLY, "failing": failing, "warnings": warnings,
          "pre_target_rung": rung, "evidence_dir": ev_rel, "generated_by": VERSION}
    ac.dump_json(dbx / "target-check.json", tc)

    def manifest(status: str, claim: str, ladder_reasons: list) -> dict:
        return {"schema": "axis-migrate.target-manifest.v1", "platform": "databricks", "run_id": run_id,
                "status": status, "deployment_status": "not_deployed", "review": "not_run", "claim": claim,
                "claim_template": {"file": "skills/target/templates/" + ("claim.v1.md" if status == "target-ready"
                                                                         else "refusal.v1.md")},
                "runtime": {k: runtime.get(k) for k in ("id", "name", "variant", "spark_version", "python",
                                                        "source_url", "retrieved", "kind")},
                "hashes": hashes, "wheel": {"file": wheel.name, "sha256": wheel_sha},
                "pre_target_rung": rung, "failing": failing, "warnings": warnings, "ladder_target_reasons": ladder_reasons,
                "checks_ref": "target-check.json",
                "target_check_sha256": "sha256:" + ac.sha256_file(dbx / "target-check.json"),
                "dryrun": {"evidence": ev_rel, "wall_time_s": (dres or {}).get("wall_time_s"),
                           "network": (dres or {}).get("network"),
                           "python": ((dres or {}).get("venv") or {}).get("python_used"),
                           "python_deviation": ((dres or {}).get("venv") or {}).get("deviation")},
                "seal": "run `seal.py <migration> --run " + run_id + " --amend --target` to bind this pack",
                "generated_by": VERSION, "created_at": ac.utc_now_iso()}
    status = "target-ready" if ready else rung
    claim = render_claim(**claim_vals) if ready else render_refusal(failing or ["pipeline not reference-verified"],
                                                                    rung)
    ac.dump_json(dbx / "target-manifest.json", manifest(status, claim, []))
    if ready:
        # the ladder's own view (fitted parameters, drift, binding); only the missing seal is expected here
        import ladder
        res = ladder.compute(mig, run_id)
        reasons = [r for r in (res["pipeline"].get("target") or {}).get("reasons") or []
                   if r.get("code") not in ("TARGET_NOT_SEALED", "TARGET_CHANGED_AFTER_SEAL")]
        if reasons:
            ready = False
            status = rung
            failing = failing + sorted({r["code"] for r in reasons})
            claim = render_refusal(failing, rung)
            ac.dump_json(dbx / "target-manifest.json", manifest(status, claim, reasons))
    if regen is not None:
        readme = md.render_readme(regen["readme_context"], claim).encode()
        ac.atomic_write_bytes(dbx / "README-deploy.md", readme)
        os.chmod(dbx / "README-deploy.md", 0o644)
    summary = {"status": status, "ready": ready, "run_id": run_id, "failing": failing, "warnings": warnings,
               "pre_target_rung": rung, "checks": {c["id"]: c["state"] for c in items}, "claim": claim,
               "evidence": ev_rel, "dryrun_wall_time_s": (dres or {}).get("wall_time_s"),
               "wall_time_s": round(time.monotonic() - t0, 2),
               "next": f"seal.py {mig} --run {run_id} --amend --target"}
    return (EXIT_READY if ready else EXIT_NOT_READY), summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Offline target checks T1–T13 and the honest target-ready claim.")
    ap.add_argument("migration")
    ap.add_argument("--run", required=True, help="the verified run (chained, sealed)")
    ap.add_argument("--keep", action="store_true", help="keep the dry run's temporary root and venv")
    ap.add_argument("--no-strace", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        mig = md.resolve_migration(a.migration)
        code, res = target_check(mig, a.run, keep=a.keep, use_strace=not a.no_strace)
    except md.Refused as e:
        out = {"status": "TARGET_NOT_SUPPORTED" if e.code == "TARGET_NOT_SUPPORTED" else "refused", "code": e.code,
               "reasons": e.reasons}
        print(json.dumps(out, sort_keys=True, indent=1) if a.json else f"target_check: {e.code}: {'; '.join(e.reasons)}",
              file=sys.stdout if a.json else sys.stderr)
        return EXIT_NOT_SUPPORTED if e.code == "TARGET_NOT_SUPPORTED" else EXIT_REFUSED
    except Refusal as e:
        out = {"status": "refused", "code": e.code, "reasons": [e.detail]}
        print(json.dumps(out, sort_keys=True, indent=1) if a.json else f"target_check: {e}",
              file=sys.stdout if a.json else sys.stderr)
        return EXIT_REFUSED
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1, ensure_ascii=False))
    else:
        for k, v in res["checks"].items():
            print(f"  {k:4s} {v}")
        print(f"status: {res['status']} (deployment_status: not_deployed)")
        if res["failing"]:
            print("failing: " + ", ".join(res["failing"]))
        print(res["claim"])
        print(f"next: {res['next']}")
    return code


if __name__ == "__main__":
    sys.exit(main())
