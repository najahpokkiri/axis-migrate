"""runlib: executor-agnostic run machinery (05 §4, CONTRACTS §6). Used by run_local.py and, later, run_e2b.py.

A `Run` owns one immutable `migration/runs/<run_id>/`:
  allocate → snapshot (+ code_hash) → environment record → plan → preflight (inputs, legacy feeds, scan, smoke
  tests) → per step: ctx.json, executor callback, evaluation (R1–R6, read/write ledger) → finalise (manifest,
  checksums, seal, runs/index.jsonl).

The executor is a callable `execute(run, step) -> ExecResult` supplied by the entry point: it runs
`axis_boot.py` for the step somewhere (local venv under strace, reference env, sandbox) and leaves
`logs/<step>.{stdout,stderr,result.json,io.json,reads}` and `outputs/<step>.tmp/` in the run directory.
Everything else here is shared.
"""
from __future__ import annotations

import dataclasses
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

RUN_DIR = Path(__file__).resolve().parent
ENV_DIR = RUN_DIR / "env"
VENV_PY = ac.env_python()            # one resolver (lib/axis_common.py), same rule as bin/axpy
LOCK = ENV_DIR / "uv.lock"
PYPROJECT = ENV_DIR / "pyproject.toml"
LOCK_MARK = ac.env_lock_marker()
AXIS_BOOT = RUN_DIR / "axis_boot.py"
RUNTIME_SRC = PLUGIN / "skills" / "migrate" / "runtime"
RUNTIME_FILES = ("_axis_runtime.py", "__main__.py")
CODESCAN = PLUGIN / "skills" / "guard" / "codescan.py"
GRAPH_PY = PLUGIN / "skills" / "discover" / "graph.py"
REFENV_PY = Path(os.environ.get("AXIS_REFENV_PYTHON", str(Path.home() / "micromamba/envs/qgis/bin/python")))

MANIFEST_SCHEMA = "axis-migrate.run-manifest.v1"
CTX_SCHEMA = "axis-migrate.ctx.v1"
DEFAULT_TIMEOUT_S = 120
DETERMINISM_ENV = {
    "OGR_CURRENT_DATE": "2000-01-01T00:00:00Z", "GDAL_PAM_ENABLED": "NO", "PYTHONHASHSEED": "0", "TZ": "UTC",
    "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "GDAL_NUM_THREADS": "1", "PROJ_NETWORK": "OFF",
}
# What every step process gets on top of DETERMINISM_ENV (nothing else is inherited; AT-10).
STEP_ENV_FIXED = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTHONUTF8": "1", "MPLBACKEND": "Agg"}

EXIT = ac.EXIT_RUN
CONTRACT_REASONS = ("STUB_DETECTED", "UNDECLARED_READ", "UNDECLARED_WRITE", "STAGED_INPUT_MODIFIED",
                    "OUTPUT_MISSING", "OUTPUT_EMPTY", "OUTPUT_STALE", "OUTPUT_UNOPENABLE", "RUNTIME_NOT_FROM_SNAPSHOT",
                    "MODULE_MISSING", "RUNTIME_MODIFIED", "CTX_STEP_MISMATCH", "OUTPUT_NOT_IN_WRITE_LEDGER",
                    "LEDGER_EMPTY", "DOWNLOAD_HASH_MISMATCH", "LEDGER_BLIND")
ENV_REASONS = ("PROJ_NETWORK_ON",)
SIDECAR_STAGE_SKIP = (".aux.xml", ".lock", "-wal", "-shm", "-journal")


def _load_runtime_module():
    """The plugin's runtime file, imported by path (one owner of path/format rules)."""
    spec = importlib.util.spec_from_file_location("_axis_runtime_plugin", RUNTIME_SRC / "_axis_runtime.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


RT = _load_runtime_module()


# ---------------------------------------------------------------------------------------------------------------
# errors and small records
# ---------------------------------------------------------------------------------------------------------------
class RunAbort(Exception):
    """Stop the run with a runner exit code (DESIGN §3) and reason codes."""

    def __init__(self, exit_code: int, reasons: list[str], message: str = ""):
        super().__init__(message or ", ".join(reasons))
        self.exit_code = exit_code
        self.reasons = reasons


STATUS_FOR_EXIT = {0: "succeeded", 10: "failed", 11: "failed", 12: "blocked", 13: "blocked", 14: "timed_out",
                   15: "blocked", 70: "failed"}


@dataclass
class ExecResult:
    exit_code: int | None
    signal: int | None = None
    timed_out: bool = False
    started_at: str = ""
    ended_at: str = ""
    started_epoch: float = 0.0
    duration_s: float = 0.0
    peak_rss_mb: float | None = None
    ledger_kind: str = "strace"          # strace | audit+cpl_debug | e2b-physical | none
    ledger_path: str | None = None       # run-relative
    note: str | None = None
    extra_reasons: list = field(default_factory=list)   # executor-side findings (e.g. staged input changed remotely)
    ledger_root: str | None = None       # run root as seen by the traced process (remote executors); remapped


@dataclass
class PlannedStep:
    id: str
    kind: str
    action: str                          # execute | reuse | bridge
    module: str | None                   # pipeline-relative module file
    inputs: list[str]
    outputs: list[str]
    params: list[str]
    timeout_s: int
    feeds: dict[str, dict] = field(default_factory=dict)   # ds -> {source, path(abs), layer, kind, format, ...}
    out_specs: dict[str, dict] = field(default_factory=dict)  # ds -> {rel (outputs/…), layer, kind, format, …}


def sha256_file(p: Path) -> str:
    return ac.sha256_file(p)


def utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def rel(run_dir: Path, p: Path | str) -> str:
    return Path(p).resolve().relative_to(run_dir.resolve()).as_posix()


def load_yaml(path: Path) -> Any:
    import yaml
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def hash_or_none(p: Path) -> str | None:
    return sha256_file(p) if p.is_file() else None


def file_record(p: Path) -> dict:
    rec = {"sha256": sha256_file(p), "bytes": p.stat().st_size, "content_sha256": None}
    try:
        rec["content_sha256"] = ac.content_sha256(p)
    except Exception as exc:  # noqa: BLE001 - recorded; content hash is an extra, bytes decide
        rec["content_sha256_error"] = f"{type(exc).__name__}: {exc}"
    return rec


# ---------------------------------------------------------------------------------------------------------------
# lock / freeze
# ---------------------------------------------------------------------------------------------------------------
def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_packages(lock_path: Path) -> dict[str, str]:
    with open(lock_path, "rb") as fh:
        doc = tomllib.load(fh)
    out = {}
    for p in doc.get("package", []):
        src = p.get("source") or {}
        if "virtual" in src or "editable" in src:
            continue
        out[_norm(p["name"])] = p.get("version")
    return out


def freeze_vs_lock(freeze: list[str], lock_path: Path) -> dict:
    lock = lock_packages(lock_path)
    problems = []
    installed = {}
    for line in freeze:
        if "==" not in line:
            continue
        n, v = line.split("==", 1)
        installed[_norm(n)] = v
    for n, v in sorted(installed.items()):
        if n not in lock:
            problems.append(f"{n}=={v} installed but not in the lock")
        elif lock[n] != v:
            problems.append(f"{n}=={v} installed, lock pins {lock[n]}")
    return {"matches": not problems, "problems": problems, "installed": len(installed), "locked": len(lock)}


def lock_versions_for_boot(lock_path: Path) -> dict[str, str]:
    lock = lock_packages(lock_path)
    return {m: lock[m] for m in ("numpy", "pandas", "geopandas", "rasterio", "pyogrio", "shapely", "pyproj",
                                 "openpyxl") if m in lock}


def find_uv() -> str | None:
    p = shutil.which("uv")
    if p:
        return p
    cand = Path.home() / ".local" / "bin" / "uv"
    return str(cand) if cand.is_file() and os.access(cand, os.X_OK) else None


def run_probe(python: Path, crs: list[str], timeout: int = 120) -> dict:
    cmd = [str(python), "-I", str(AXIS_BOOT), "--probe"] + [a for c in crs for a in ("--crs", c)]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), **DETERMINISM_ENV, **STEP_ENV_FIXED}
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if cp.returncode != 0:
        raise RuntimeError(f"probe failed ({cp.returncode}): {cp.stderr[-2000:]}")
    return json.loads(cp.stdout)


_REF_CACHE: dict = {}


def reference_env_record(python: Path, crs: list[str]) -> dict:
    """Probe of the legacy reference environment (QGIS env). Never earns anything; recorded and compared.
    Cached per process (interpreter path + mtime + CRS list)."""
    key = (str(python), python.stat().st_mtime if python.is_file() else None, tuple(sorted(crs)))
    if key not in _REF_CACHE:
        _REF_CACHE[key] = _reference_env_record(python, crs)
    return json.loads(json.dumps(_REF_CACHE[key]))


def _reference_env_record(python: Path, crs: list[str]) -> dict:
    if not python.is_file():
        return {"source": "unavailable", "python_exe": str(python)}
    try:
        pr = run_probe(python, crs)
    except Exception as exc:  # noqa: BLE001 - an unavailable reference env is recorded, never fatal
        return {"source": "probe failed", "python_exe": str(python), "error": str(exc)[:500]}
    prefix = python.resolve().parents[1]
    qgis = None
    for meta in sorted((prefix / "conda-meta").glob("qgis-[0-9]*.json")):
        qgis = meta.name.split("-")[1]
    activation_on = False
    for script in sorted((prefix / "etc" / "conda" / "activate.d").glob("*.sh")):
        try:
            if "PROJ_NETWORK=ON" in script.read_text(errors="replace"):
                activation_on = True
        except OSError:
            pass
    native = pr.get("native", {})
    return {"source": "probe of reference env", "python_exe": str(python), "python": pr.get("python"),
            "qgis": qgis, "gdal": native.get("rasterio.gdal"), "proj": native.get("pyproj.proj"),
            "geos": native.get("shapely.geos"), "native": native, "libs": pr.get("libs"),
            "proj_network": "ON" if activation_on else "OFF",
            "proj_network_note": "conda activation forces PROJ_NETWORK=ON (proj4-activate.sh)" if activation_on
            else None, "site_packages": pr.get("site_packages"), "probe": {k: pr[k] for k in ("proj",) if k in pr}}


def _major(v: str | None) -> str | None:
    return v.split(".")[0] if v else None


def _minor(v: str | None) -> str | None:
    return ".".join(v.split(".")[:2]) if v else None


def env_comparison(native: dict, ref: dict) -> list[dict]:
    rows = []
    pairs = [("gdal", [native.get("rasterio.gdal"), native.get("pyogrio.gdal")], ref.get("gdal")),
             ("proj", [native.get("rasterio.proj"), native.get("pyproj.proj")], ref.get("proj")),
             ("geos", [native.get("shapely.geos")], ref.get("geos"))]
    for key, mine, theirs in pairs:
        mine = [m for m in mine if m]
        if not theirs or not mine:
            rows.append({"key": key, "pipeline": mine, "reference": theirs, "major_equal": None,
                         "minor_equal": None, "allowed_by": None, "note": "reference unavailable"})
            continue
        maj = all(_major(m) == _major(theirs) for m in mine)
        mnr = all(_minor(m) == _minor(theirs) for m in mine)
        rows.append({"key": key, "pipeline": mine, "reference": theirs, "major_equal": maj, "minor_equal": mnr,
                     "allowed_by": "D5-4" if maj else None})
    return rows


# ---------------------------------------------------------------------------------------------------------------
# graph helpers
# ---------------------------------------------------------------------------------------------------------------
def in_scope_steps(graph: dict) -> list[dict]:
    return [s for s in graph.get("steps", []) if s.get("scope") == "migrate" or s.get("kind") == "bridge"]


def producers(graph: dict, steps: list[dict]) -> dict[str, str]:
    out = {}
    for s in steps:
        for ds in s.get("outputs", []):
            out[ds] = s["id"]
    return out


def topo_order(steps: list[dict]) -> list[str]:
    prod = {}
    for s in steps:
        for ds in s.get("outputs", []):
            prod[ds] = s["id"]
    deps = {s["id"]: sorted({prod[d] for d in s.get("inputs", []) if d in prod and prod[d] != s["id"]})
            for s in steps}
    order, done = [], set()
    pending = sorted(deps)
    while pending:
        ready = [s for s in pending if all(d in done for d in deps[s])]
        if not ready:
            raise RunAbort(EXIT["contract"], ["GRAPH_CYCLE:" + ",".join(pending)])
        s = ready[0]
        order.append(s)
        done.add(s)
        pending.remove(s)
    return order


def code_tree_hash(pipeline_dir: Path) -> tuple[str, list[dict]]:
    entries = ac.tree_entries(pipeline_dir)
    return ac.tree_hash(pipeline_dir), entries


# ---------------------------------------------------------------------------------------------------------------
# strace / audit ledger parsing and classification
# ---------------------------------------------------------------------------------------------------------------
TRACE_SET = "openat,?open,?creat,?rename,renameat,renameat2,?link,linkat,?symlink,symlinkat"
STRACE_KINDS = ("strace", "e2b-physical+strace")
AUDIT_KINDS = ("audit+cpl_debug", "e2b-physical+audit")
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
_FD_RE = re.compile(r"^\s*(?:AT_FDCWD|-?\d+)<([^>]*)>")
_CALL_RE = re.compile(r"^(?P<call>\w+)\((?P<args>.*)\)\s+=\s+(?P<ret>-?\d+|\?)(?P<retx>.*)$")
_RET_PATH_RE = re.compile(r"^<(.*)>")
_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "v": "\v", "f": "\f", "a": "\a", "b": "\b"}


def _unescape(s: str) -> str:
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            if n == "x" and i + 3 < len(s) + 1:
                out.append(int(s[i + 2:i + 4], 16))
                i += 4
                continue
            if n in "01234567":
                j = i + 1
                while j < len(s) and j < i + 4 and s[j] in "01234567":
                    j += 1
                out.append(int(s[i + 1:j], 8) & 0xFF)
                i = j
                continue
            out += _ESC.get(n, n).encode()
            i += 2
            continue
        out += c.encode("utf-8")
        i += 1
    return out.decode("utf-8", errors="surrogateescape")


def _split_args(args: str) -> list[str]:
    parts, depth, cur, in_str, esc = [], 0, [], False, False
    for ch in args:
        if in_str:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            cur.append(ch)
        elif ch in "<{[(":
            depth += 1
            cur.append(ch)
        elif ch in ">}])":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _str_arg(a: str) -> str | None:
    m = _STR_RE.search(a)
    return _unescape(m.group(1)) if m else None


def _dirfd(a: str) -> str | None:
    m = _FD_RE.match(a)
    return m.group(1) if m else None


def _join(base: str | None, p: str) -> str:
    if p.startswith("/") or base is None:
        return p
    return os.path.normpath(os.path.join(base, p))


WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND")


def parse_strace(path: Path) -> list[dict]:
    """strace -f -y output → [{pid, call, access: read|write|dir|move_from|link_src, path, ok}]."""
    events: list[dict] = []
    pending: dict[str, str] = {}
    if not path.is_file():
        return events
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = re.match(r"^(\d+)\s+(.*)$", line)
            if not m:
                pid, rest = "0", line
            else:
                pid, rest = m.group(1), m.group(2)
            if rest.endswith("<unfinished ...>"):
                pending[pid] = rest[: -len("<unfinished ...>")].rstrip()
                continue
            rm = re.match(r"^<\.\.\. \w+ resumed>\s?(.*)$", rest)
            if rm:
                rest = pending.pop(pid, "") + rm.group(1)
            cm = _CALL_RE.match(rest)
            if not cm:
                continue
            call, args, ret, retx = cm.group("call"), _split_args(cm.group("args")), cm.group("ret"), cm.group("retx")
            ok = ret != "?" and int(ret) >= 0
            rp = _RET_PATH_RE.match(retx.strip())
            ret_path = rp.group(1) if rp else None
            if call in ("open", "creat"):
                p = _str_arg(args[0]) if args else None
                if p is None:
                    continue
                flags = args[1] if len(args) > 1 else ""
                full = ret_path or _join(None, p)
                acc = "write" if call == "creat" or any(f in flags for f in WRITE_FLAGS) else (
                    "dir" if "O_DIRECTORY" in flags else "read")
                events.append({"pid": pid, "call": call, "access": acc, "path": full, "ok": ok, "flags": flags})
            elif call == "openat":
                if len(args) < 2:
                    continue
                base, p = _dirfd(args[0]), _str_arg(args[1])
                if p is None:
                    continue
                flags = args[2] if len(args) > 2 else ""
                full = ret_path if (ok and ret_path) else _join(base, p)
                acc = "write" if any(f in flags for f in WRITE_FLAGS) else ("dir" if "O_DIRECTORY" in flags else "read")
                events.append({"pid": pid, "call": call, "access": acc, "path": full, "ok": ok, "flags": flags})
            elif call in ("rename", "link", "symlink"):
                if len(args) < 2:
                    continue
                a, b = _str_arg(args[0]), _str_arg(args[1])
                if a is None or b is None:
                    continue
                src_acc = "move_from" if call == "rename" else ("link_src" if call == "link" else "symlink_target")
                events.append({"pid": pid, "call": call, "access": src_acc, "path": a, "ok": ok})
                events.append({"pid": pid, "call": call, "access": "write", "path": b, "ok": ok})
            elif call in ("renameat", "renameat2", "linkat"):
                if len(args) < 4:
                    continue
                a = _join(_dirfd(args[0]), _str_arg(args[1]) or "")
                b = _join(_dirfd(args[2]), _str_arg(args[3]) or "")
                src_acc = "move_from" if call.startswith("rename") else "link_src"
                events.append({"pid": pid, "call": call, "access": src_acc, "path": a, "ok": ok})
                events.append({"pid": pid, "call": call, "access": "write", "path": b, "ok": ok})
            elif call == "symlinkat":
                if len(args) < 3:
                    continue
                target = _str_arg(args[0]) or ""
                b = _join(_dirfd(args[1]), _str_arg(args[2]) or "")
                events.append({"pid": pid, "call": call, "access": "symlink_target", "path": target, "ok": ok})
                events.append({"pid": pid, "call": call, "access": "write", "path": b, "ok": ok})
    return events


_VSI_ARCHIVE = re.compile(r"^/vsi(?:zip|gzip|tar|7z|rar)/+(.*)$")
_DRIVER_PREFIX = re.compile(r'^[A-Za-z0-9_]+:"?(/[^"]+?)"?(?::[^/]*)?$')


def gdal_name_to_path(raw: str, cwd: str | None) -> str | None:
    """A GDAL dataset name as a file path: None for in-memory datasets (/vsimem/, MEM:::); archive and driver
    prefixes unwrapped (`/vsizip//a/b.zip/x` → /a/b.zip, `GPKG:"/a/b.gpkg":layer` → /a/b.gpkg)."""
    raw = raw.strip()
    if raw.startswith("/vsimem/") or raw.startswith("MEM:::"):
        return None
    m = _VSI_ARCHIVE.match(raw)
    if m:
        inner = "/" + m.group(1).lstrip("/")
        parts = inner.split("/")
        for i in range(len(parts), 1, -1):          # the archive file is the longest existing prefix
            cand = "/".join(parts[:i])
            if os.path.isfile(cand):
                return cand
        return inner
    if raw.startswith("/vsi"):
        return None                                 # network/other virtual file systems: no local file
    m = _DRIVER_PREFIX.match(raw)
    if m and not raw.startswith("/"):
        return m.group(1)
    return raw if os.path.isabs(raw) else os.path.normpath(os.path.join(cwd or "/", raw))


def parse_audit(path: Path, stderr_path: Path | None, cwd: str | None = None) -> list[dict]:
    """Fallback ledger (no strace): axis_boot's audit/CPL_DEBUG JSON lines + pyogrio's `GDALOpen(...)` stderr lines."""
    events = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev, p = rec.get("event", "open"), rec.get("path")
            pid = str(rec.get("pid", 0))
            if ev == "gdal.open":
                gp = gdal_name_to_path(rec.get("raw") or "", rec.get("cwd"))
                if gp:
                    events.append({"pid": pid, "call": "cpl_debug.GDALOpen", "access": "read", "path": gp, "ok": True})
                continue
            if not p or rec.get("unresolved_dir_fd"):
                continue
            if ev == "open":
                mode = str(rec.get("mode") or "r")
                flags = rec.get("flags") or 0
                wr = any(c in mode for c in "wax+") or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))
                events.append({"pid": pid, "call": "audit.open", "access": "write" if wr else "read", "path": p,
                               "ok": True})
            elif ev in ("os.rename", "shutil.copyfile", "os.link", "os.symlink", "shutil.copytree"):
                src_acc = {"os.rename": "move_from", "os.link": "link_src", "os.symlink": "symlink_target"}.get(
                    ev, "read")
                events.append({"pid": pid, "call": f"audit.{ev}", "access": src_acc, "path": p, "ok": True})
                events.append({"pid": pid, "call": f"audit.{ev}", "access": "write", "path": rec["dst"], "ok": True})
            elif ev in ("os.listdir", "os.scandir"):
                events.append({"pid": pid, "call": f"audit.{ev}", "access": "dir", "path": p, "ok": True})
    if stderr_path is not None and stderr_path.is_file():
        for m in re.finditer(r"GDALOpen\((.+?), this=", stderr_path.read_text(errors="replace")):
            gp = gdal_name_to_path(m.group(1), cwd)
            if gp:
                events.append({"pid": "0", "call": "cpl_debug.GDALOpen", "access": "read", "path": gp, "ok": True})
    return events


def _real(p: str | Path) -> str:
    return os.path.realpath(str(p))


def _is_under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip("/") + "/")


@dataclass
class LedgerPolicy:
    run_dir: str
    step_id: str
    input_files: list[str]            # realpaths of declared, resolved inputs
    allowed_prefixes: list[str]       # run-internal areas the step may touch
    allowed_files: list[str]
    system_allow: list[str]           # interpreter / venv / boot script
    protected: list[str]              # legacy root, migration dir, project dir
    out_tmp: str
    strict: list[str] = field(default_factory=list)   # legacy root + migration dir: never exempt
    name_prefixes: list[str] = field(default_factory=list)  # e.g. logs/<step>. and logs/.<step>. (atomic tmp)


def classify_events(events: list[dict], pol: LedgerPolicy) -> dict:
    """Apply the read-ledger rule (D5-9) and build the step's write ledger."""
    undeclared, attempted, declared_opened, writes, undeclared_writes, elsewhere = [], [], set(), set(), set(), set()
    input_set = set(pol.input_files)
    input_dirs = {os.path.dirname(p) for p in pol.input_files}
    input_stems = [(os.path.dirname(p), os.path.basename(p).split(".")[0]) for p in pol.input_files]

    def allowed(p: str, acc: str) -> bool:
        if p in input_set or p in pol.allowed_files:
            return True
        if acc == "dir":
            if p in input_dirs or p == pol.run_dir:
                return True
            if _is_under(p, pol.run_dir) and any(_is_under(d, p) for d in input_dirs | {pol.out_tmp}):
                return True
        if acc != "write":
            for d, stem in input_stems:  # sidecars of a declared input (aux, ovr, shx/dbf/prj, journal)
                base = os.path.join(d, stem)
                if p.startswith(base + ".") or p.startswith(base + "-"):
                    return True
        if any(p.startswith(n) for n in pol.name_prefixes):
            return True
        return any(_is_under(p, a) for a in pol.allowed_prefixes)

    for ev in events:
        raw = ev["path"]
        p = _real(raw) if ev["ok"] else os.path.normpath(raw)
        acc = ev["access"]
        if acc == "symlink_target":
            p = os.path.normpath(_join(os.path.dirname(ev["path"]), raw))
        if any(_is_under(p, s) for s in pol.system_allow) and not any(_is_under(p, x) for x in pol.strict):
            continue
        if acc == "write":
            if not ev["ok"]:
                continue
            if _is_under(p, pol.out_tmp):
                writes.add(p)
            elif allowed(p, acc):
                continue
            elif any(_is_under(p, x) for x in pol.protected):
                undeclared_writes.add(p)
            else:
                elsewhere.add(p)
            continue
        if p in input_set and ev["ok"]:
            declared_opened.add(p)
        if allowed(p, acc):
            continue
        if any(_is_under(p, x) for x in pol.protected):
            (undeclared if ev["ok"] else attempted).append({"path": p, "access": acc, "call": ev["call"]})
    dedup = sorted({(u["path"], u["access"]) for u in undeclared})
    att = sorted({(u["path"], u["access"]) for u in attempted if not _is_under(u["path"], pol.run_dir)})
    return {"undeclared": [{"path": a, "access": b} for a, b in dedup],
            "attempted_protected": [{"path": a, "access": b} for a, b in att],
            "declared_opened": sorted(declared_opened), "writes": sorted(writes),
            "undeclared_writes": sorted(undeclared_writes), "writes_elsewhere": sorted(elsewhere)[:200]}


# ---------------------------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------------------------
class Run:
    def __init__(self, migration: Path, *, chain: str = "chained", executor: str = "local",
                 steps_filter: list[str] | None = None, resume_from: str | None = None,
                 allow_env_major: bool = False, run_stamp: str | None = None, read_ledger: str = "strace",
                 argv: list[str] | None = None):
        self.migration = migration.resolve()
        self.project = self.migration.parent
        self.chain = chain
        self.executor = executor
        self.steps_filter = steps_filter
        self.resume_from = resume_from
        self.allow_env_major = allow_env_major
        self.run_stamp = run_stamp
        self.read_ledger = read_ledger
        self.argv = argv or []
        self.reasons: list[str] = []
        self.steps_records: list[dict] = []
        self.inputs_records: list[dict] = []
        self.feeds_records: list[dict] = []
        self.outputs_records: list[dict] = []
        self.plan: list[PlannedStep] = []
        self.env_record: dict = {}
        self.reference_env: dict = {}
        self.env_cmp: list[dict] = []
        self.started_at = ac.utc_now_iso()
        self.t0 = time.monotonic()
        self.dir: Path | None = None
        self.run_id: str | None = None
        self.parent: dict | None = None
        self.scan_record: dict = {"status": "not_run"}
        self.tests_record: dict = {"status": "not_run"}
        self.boot_python: Path = VENV_PY
        self.expect: dict = {}
        self.graph = ac.load_json(self.migration / "graph.json")
        self.legacy_root = ac.legacy_root(self.migration, self.graph)
        inv_p = self.migration / "inventory.json"
        self.inventory = ac.load_json(inv_p) if inv_p.is_file() else None
        self.ds = {d["id"]: d for d in self.graph.get("datasets", [])}
        self.code_hash, self.code_files = code_tree_hash(self.migration / "pipeline")
        self.snapshot_hashes: dict = {}
        self.exec_root: str | None = None     # where the run dir lives for the step process (None = here)
        self.preflight: dict = {"plan_approval": {"status": "not_run"}, "graph_integrity": {"status": "not_run"}}
        self.e2b_record: dict | None = None

    # ---- allocation, snapshot --------------------------------------------------------------------------------
    def allocate(self) -> None:
        hexh = self.code_hash.split(":", 1)[-1]
        stamp = self.run_stamp or utc_stamp()
        self.run_id = f"r{stamp}-{hexh[:8]}"
        runs = self.migration / "runs"
        runs.mkdir(exist_ok=True)
        d = runs / self.run_id
        try:
            d.mkdir()
        except FileExistsError:
            raise RunAbort(EXIT["immutability"], [f"RUN_DIR_EXISTS:{self.run_id}"]) from None
        self.dir = d
        for sub in ("snapshot", "ctx", "stage/inputs", "stage/legacy_ro", "outputs", "logs", "env", "verify",
                    "scratch"):
            (d / sub).mkdir(parents=True, exist_ok=True)

    LIVE_FILES = {"params.yaml": "params.yaml", "graph.json": "graph.json", "inventory.json": "inventory.json",
                  "tolerances.yaml": "tolerances.yaml", "known-differences.md": "known-differences.md",
                  "decisions.md": "decisions.md", "tolerances.proposed.yaml": "tolerances.proposed.yaml"}

    def snapshot(self) -> None:
        snap = self.dir / "snapshot"
        pipe_src = self.migration / "pipeline"
        if not pipe_src.is_dir():
            raise RunAbort(EXIT["contract"], ["PIPELINE_MISSING"])
        shutil.copytree(pipe_src, snap / "axis_pipeline",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
        tests = self.migration / "tests"
        if tests.is_dir():
            shutil.copytree(tests, snap / "tests", ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                                                   ".pytest_cache"))
        for live, name in self.LIVE_FILES.items():
            src = self.migration / live
            if src.is_file():
                shutil.copy2(src, snap / name)
        shutil.copy2(LOCK, snap / "uv.lock")
        shutil.copy2(PYPROJECT, snap / "pyproject.toml")
        snap_hash, entries = code_tree_hash(snap / "axis_pipeline")
        if snap_hash != self.code_hash:
            # the tree changed between run-id allocation and the copy: use the snapshot's identity
            self.reasons.append("CODE_CHANGED_DURING_SNAPSHOT")
            self.code_hash, self.code_files = snap_hash, entries
        self.snapshot_hashes = {name: hash_or_none(snap / name) for name in
                                list(self.LIVE_FILES.values()) + ["uv.lock", "pyproject.toml"]}
        # the vendored runtime must be the plugin's, byte for byte
        problems = []
        for f in RUNTIME_FILES:
            mine, theirs = snap / "axis_pipeline" / f, RUNTIME_SRC / f
            if not mine.is_file():
                problems.append(f"RUNTIME_MISSING:{f}")
            elif sha256_file(mine) != sha256_file(theirs):
                problems.append(f"RUNTIME_MODIFIED:{f}")
        if not (snap / "axis_pipeline" / "__init__.py").is_file():
            problems.append("RUNTIME_MISSING:__init__.py")
        if not (snap / "params.yaml").is_file():
            problems.append("PARAMS_MISSING")
        if problems:
            raise RunAbort(EXIT["contract"], problems)

    # ---- environment -----------------------------------------------------------------------------------------
    def crs_list(self) -> list[str]:
        found = set()
        for p in (self.migration / "params.yaml",):
            if p.is_file():
                found |= set(re.findall(r"EPSG:\d{4,5}", p.read_text(errors="replace")))
        return sorted(found)

    def record_environment(self, probe: dict, *, lock_path: Path | None = None, lock_mark: Path | None = None,
                           reference: dict | None = None, executor_python: Path | str | None = None,
                           lock_mark_value: str | None = None, mismatch_reason: str = "FREEZE_MISMATCH") -> None:
        """Pipeline env vs lock, reference env, majors (D5-3/D5-4). Raises RunAbort(13) when refused."""
        lock_path = lock_path or LOCK
        lock_mark = lock_mark or LOCK_MARK
        executor_python = executor_python or VENV_PY
        env_dir = self.dir / "env"
        freeze = probe.get("freeze", [])
        (env_dir / "freeze.txt").write_text("\n".join(freeze) + "\n", encoding="utf-8")
        ac.dump_json(env_dir / "probe.json", probe)
        lock_hash = "sha256:" + sha256_file(lock_path)
        fz = freeze_vs_lock(freeze, lock_path)
        if lock_mark_value is not None:
            mark = lock_mark_value.strip()
        else:
            mark = lock_mark.read_text().strip() if lock_mark.is_file() else None
        reasons = []
        self.reference_env = reference or {"source": "unavailable"}
        native = probe.get("native", {})
        if self.executor == "refenv":
            enforce = False
            self.expect = {"site_packages": probe.get("site_packages", []),
                           "versions": lock_versions_for_boot(lock_path), "enforce_versions": False}
        else:
            enforce = True
            self.expect = {"site_packages": probe.get("site_packages", []),
                           "versions": lock_versions_for_boot(lock_path), "enforce_versions": True}
            if mark != sha256_file(lock_path):
                reasons.append(f"{mismatch_reason}:lock_not_synced")
            if not fz["matches"]:
                reasons.append(mismatch_reason)
        ac.dump_json(env_dir / "expect.json", self.expect)
        net = probe.get("proj", {})
        if net.get("network_enabled"):
            reasons.append("PROJ_NETWORK_ON")
        self.env_cmp = env_comparison(native, self.reference_env)
        majors_bad = [r["key"] for r in self.env_cmp if r["key"] in ("gdal", "proj") and r["major_equal"] is False]
        if majors_bad and self.executor != "refenv":
            if self.allow_env_major:
                for r in self.env_cmp:
                    if r["key"] in majors_bad:
                        r["allowed_by"] = "--allow-env-major"
            else:
                reasons.append("ENV_MAJOR_MISMATCH:" + ",".join(majors_bad))
        sc = probe.get("stub_check", {})
        self.env_record = {
            "lock_hash": lock_hash, "lock_marker_matches": mark == sha256_file(lock_path),
            "freeze_hash": "sha256:" + ac.sha256_bytes(("\n".join(freeze) + "\n").encode()),
            "freeze_matches_lock": fz["matches"], "freeze_problems": fz["problems"][:50],
            "python": probe.get("python"), "python_exe": str(executor_python), "platform": probe.get("platform"),
            "cpu": probe.get("cpu"), "libs": probe.get("libs"), "native": native,
            "proj": probe.get("proj"), "runner_env": dict(DETERMINISM_ENV),
            "stub_check": {"passed": bool(sc.get("passed")), "canary": sc.get("canary"),
                           "meta_path": sc.get("meta_path"), "problems": sc.get("problems", [])},
            "read_ledger": self.read_ledger, "e2b": self.e2b_record, "allow_env_major": self.allow_env_major,
            "versions_enforced": enforce, "uv": find_uv()}
        if not sc.get("passed"):
            raise RunAbort(EXIT["contract"], ["STUB_DETECTED:probe"])
        if reasons:
            raise RunAbort(EXIT["environment"], reasons)

    # ---- plan --------------------------------------------------------------------------------------------------
    def _resume_parent(self) -> dict | None:
        if not self.resume_from:
            return None
        pdir = self.migration / "runs" / self.resume_from
        if not (pdir / "manifest.json").is_file():
            raise RunAbort(EXIT["input_missing"], [f"RESUME_PARENT_MISSING:{self.resume_from}"])
        bad = verify_checksums(pdir)
        if bad:
            raise RunAbort(EXIT["immutability"], [f"SEALED_RUN_TOUCHED:{self.resume_from}"] + bad[:5])
        man = ac.load_json(pdir / "manifest.json")
        mism = []
        if man.get("code", {}).get("code_hash") != self.code_hash:
            mism.append("code_hash")
        if man.get("params", {}).get("sha256") != self.snapshot_hashes.get("params.yaml"):
            mism.append("params")
        if man.get("env", {}).get("lock_hash") != "sha256:" + sha256_file(LOCK):
            mism.append("lock_hash")
        if man.get("chain") != self.chain:
            mism.append("chain")
        for rec in man.get("inputs", []) + man.get("legacy_feeds", []):
            src = self.legacy_root / rec["legacy_path"]
            if not src.is_file() or sha256_file(src) != rec.get("sha256"):
                mism.append(f"input:{rec.get('dataset_id')}")
        if mism:
            raise RunAbort(EXIT["input_missing"], [f"RESUME_MISMATCH:{m}" for m in mism])
        man["_dir"] = str(pdir)
        return man

    def _out_spec(self, step_id: str, ds_id: str) -> dict:
        d = self.ds.get(ds_id)
        if d is None:
            raise RunAbort(EXIT["contract"], [f"DATASET_UNKNOWN:{ds_id}"])
        new_rel = RT.new_relpath(d, step_id)
        if not new_rel.startswith(step_id + "/"):
            raise RunAbort(EXIT["contract"], [f"NEW_PATH_OUTSIDE_STEP_DIR:{ds_id}"])
        kind, fmt = RT.infer_kind_format(new_rel)
        layer = (d.get("new") or {}).get("layer") or (d.get("legacy") or {}).get("layer")
        if kind != "vector":
            layer = (d.get("new") or {}).get("layer")
        return {"rel": "outputs/" + new_rel, "name": new_rel[len(step_id) + 1:], "layer": layer,
                "kind": d.get("kind") or kind, "format": fmt,
                "may_be_empty": bool((d.get("compare") or {}).get("may_be_empty", False))}

    def make_plan(self) -> None:
        steps = in_scope_steps(self.graph)
        by_id = {s["id"]: s for s in steps}
        order = topo_order(steps)
        prod = producers(self.graph, steps)
        wanted = set(order)
        if self.steps_filter:
            unknown = [s for s in self.steps_filter if s not in by_id]
            if unknown:
                raise RunAbort(EXIT["contract"], [f"STEP_UNKNOWN:{s}" for s in unknown])
            wanted = set(self.steps_filter)
        self.parent = self._resume_parent()
        parent_ok = {s["step_id"] for s in (self.parent or {}).get("steps", [])
                     if s.get("status") in ("succeeded", "reused")}
        plan: list[PlannedStep] = []
        planned: dict[str, PlannedStep] = {}
        # chained runs pull every upstream producer that is not wanted from the resume parent (or refuse)
        for sid in order:
            s = by_id[sid]
            action = None
            if sid in wanted:
                action = "bridge" if s.get("kind") == "bridge" else "execute"
                if self.parent and sid in parent_ok and self.chain == "chained":
                    action = "reuse"
            elif self.chain == "chained" and self._needed_upstream(sid, wanted, by_id, prod):
                if self.parent and sid in parent_ok:
                    action = "reuse"
                else:
                    continue  # resolved below as UPSTREAM_NOT_IN_PLAN when a consumer needs it
            if action is None:
                continue
            module = (s.get("new") or {}).get("module")
            ps = PlannedStep(id=sid, kind=s.get("kind", "script"), action=action,
                             module=module, inputs=sorted(s.get("inputs", [])), outputs=sorted(s.get("outputs", [])),
                             params=sorted(s.get("params", [])), timeout_s=int(s.get("timeout_s") or DEFAULT_TIMEOUT_S))
            ps.out_specs = {ds: self._out_spec(sid, ds) for ds in ps.outputs}
            plan.append(ps)
            planned[sid] = ps
        for ps in plan:
            if ps.action != "execute":
                continue
            if not ps.module:
                raise RunAbort(EXIT["contract"], [f"MODULE_MISSING:{ps.id}"])
            mod_rel = ps.module[len("pipeline/"):] if ps.module.startswith("pipeline/") else ps.module
            if mod_rel != f"{ps.id}.py" or not (self.dir / "snapshot" / "axis_pipeline" / mod_rel).is_file():
                raise RunAbort(EXIT["contract"], [f"MODULE_MISSING:{ps.id}"])
            for ds in ps.inputs:
                ps.feeds[ds] = self._resolve_feed(ps, ds, prod, planned)
        self.plan = plan

    def _needed_upstream(self, sid: str, wanted: set, by_id: dict, prod: dict) -> bool:
        """Is step `sid` an upstream producer of any wanted step (transitively)?"""
        frontier = list(wanted)
        seen = set()
        while frontier:
            w = frontier.pop()
            if w in seen or w not in by_id:
                continue
            seen.add(w)
            for ds in by_id[w].get("inputs", []):
                p = prod.get(ds)
                if p == sid:
                    return True
                if p and p not in seen:
                    frontier.append(p)
        return False

    def _resolve_feed(self, ps: PlannedStep, ds_id: str, prod: dict, planned: dict) -> dict:
        d = self.ds.get(ds_id)
        if d is None:
            raise RunAbort(EXIT["contract"], [f"DATASET_UNKNOWN:{ds_id}"])
        legacy = d.get("legacy") or {}
        kind_l, fmt_l = RT.infer_kind_format(legacy.get("path") or "")
        p = prod.get(ds_id)
        role = d.get("role")
        if p == ps.id:
            raise RunAbort(EXIT["contract"], [f"STEP_READS_OWN_OUTPUT:{ps.id}:{ds_id}"])
        isolated = self.chain == "isolated"
        if p is None or (isolated and role == "input" and legacy.get("path")):
            if not legacy.get("path"):
                raise RunAbort(EXIT["input_missing"], [f"INPUT_MISSING:{ds_id}"])
            if p is None and role not in ("input", None):
                raise RunAbort(EXIT["input_missing"], [f"INPUT_NOT_PRODUCIBLE:{ds_id}"])
            return {"source": "input", "legacy_path": legacy["path"], "layer": legacy.get("layer"),
                    "kind": d.get("kind") or kind_l, "format": fmt_l,
                    "path": str(self.dir / "stage" / "inputs" / legacy["path"])}
        if isolated:
            if role == "final":
                raise RunAbort(EXIT["input_missing"], [f"ISOLATION_FEED_IS_FINAL:{ds_id}"])
            if legacy.get("path") and legacy.get("exists", True) and (self.legacy_root / legacy["path"]).is_file():
                return {"source": "legacy_isolation", "legacy_path": legacy["path"], "layer": legacy.get("layer"),
                        "kind": d.get("kind") or kind_l, "format": fmt_l, "producer": p,
                        "path": str(self.dir / "stage" / "legacy_ro" / legacy["path"])}
            if p in planned:   # memory-only legacy result: fall back to this run's upstream output
                spec = planned[p].out_specs[ds_id]
                return {"source": "run", "producer": p, "layer": spec["layer"], "kind": spec["kind"],
                        "format": spec["format"], "path": str(self.dir / spec["rel"]), "isolation": "partial"}
            raise RunAbort(EXIT["input_missing"], [f"INPUT_MISSING:{ds_id}"])
        if p not in planned:
            raise RunAbort(EXIT["input_missing"], [f"UPSTREAM_NOT_IN_PLAN:{ds_id}"])
        spec = planned[p].out_specs[ds_id]
        src = "reused" if planned[p].action == "reuse" else ("bridge" if planned[p].action == "bridge" else "run")
        return {"source": src, "producer": p, "layer": spec["layer"], "kind": spec["kind"], "format": spec["format"],
                "path": str(self.dir / spec["rel"])}

    # ---- preflight: graph integrity and plan approval (script halves of the Claude Code hooks; Codex has none) --
    def preflight_graph(self, python: Path | None = None) -> None:
        """graph.py check-integrity (graph.json only ever written by graph.py apply), then: every step this run
        executes has an approved plan. Both refuse with exit 11 and are recorded in manifest.preflight."""
        python = python or VENV_PY
        gi: dict = {"status": "not_available", "tool": "graph.py check-integrity"}
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "AXIS_MIGRATE_PLUGIN": str(PLUGIN),
               **DETERMINISM_ENV, **STEP_ENV_FIXED}
        if GRAPH_PY.is_file():
            hp = subprocess.run([str(python), str(GRAPH_PY), "--help"], capture_output=True, text=True, env=env,
                                timeout=60)
            if "check-integrity" in hp.stdout + hp.stderr:
                cp = subprocess.run([str(python), str(GRAPH_PY), "check-integrity", str(self.migration)],
                                    capture_output=True, text=True, env=env, timeout=300)
                gi = {"status": "ok" if cp.returncode == 0 else "failed", "tool": "graph.py check-integrity",
                      "exit_code": cp.returncode, "output_tail": (cp.stdout + cp.stderr)[-2000:]}
            else:
                gi["note"] = "graph.py has no check-integrity subcommand yet; skipped (runs once present)"
        else:
            gi["note"] = "graph.py not installed; skipped"
        live = hash_or_none(self.migration / "graph.json")
        if self.snapshot_hashes and live != self.snapshot_hashes.get("graph.json"):
            gi["snapshot_matches_live"] = False
        self.preflight["graph_integrity"] = gi
        if gi["status"] == "failed":
            raise RunAbort(EXIT["contract"], ["GRAPH_EDITED_OUTSIDE_APPLY"])
        if gi.get("snapshot_matches_live") is False:
            raise RunAbort(EXIT["contract"], ["GRAPH_CHANGED_DURING_PREFLIGHT"])
        by_id = {st["id"]: st for st in self.graph.get("steps", [])}
        unapproved = []
        for ps in self.plan:
            if ps.action != "execute":
                continue
            appr = ((by_id.get(ps.id) or {}).get("plan") or {}).get("approved") or {}
            if not appr.get("by") or not appr.get("at"):
                unapproved.append(ps.id)
        self.preflight["plan_approval"] = {"status": "refused" if unapproved else "ok",
                                           "checked": [p.id for p in self.plan if p.action == "execute"],
                                           "unapproved": unapproved}
        if unapproved:
            raise RunAbort(EXIT["contract"], [f"PLAN_NOT_APPROVED:{s}" for s in unapproved])

    # ---- preflight: inputs, feeds ---------------------------------------------------------------------------
    def _inventory_file(self, legacy_rel: str) -> dict | None:
        if not self.inventory:
            return None
        for f in self.inventory.get("files", []):
            if f.get("path") == legacy_rel:
                return f
        return None

    def _inventory_paths(self) -> set[str]:
        return {f.get("path") for f in (self.inventory or {}).get("files", [])}

    def _check_pin(self, ds_id: str, legacy_rel: str, rec: dict) -> list[str]:
        """Compare a legacy file against the scan pin (inventory), GPKG/XLSX by content when bytes differ."""
        notes = []
        pin = self._inventory_file(legacy_rel)
        gpin = (self.ds.get(ds_id) or {}).get("legacy") or {}
        pin_sha = (pin or {}).get("sha256") or gpin.get("sha256")
        pin_csha = (pin or {}).get("content_sha256") or gpin.get("content_sha256")
        rec["pinned_sha256"] = pin_sha
        if pin_sha is None:
            rec["pinned"] = False
            return notes
        rec["pinned"] = True
        if rec["sha256"] == pin_sha:
            return notes
        if rec.get("content_sha256") and pin_csha and rec["content_sha256"] == pin_csha:
            rec["note"] = "bytes differ from the scan pin, content identical (SQLite header / format timestamp)"
            return notes
        raise RunAbort(EXIT["input_missing"], [f"INPUT_CHANGED:{ds_id}"])

    def _companions(self, src: Path) -> list[Path]:
        """Files that belong to a dataset (shapefile parts, world files, overviews); never PAM .aux.xml."""
        out = []
        stem = src.name.split(".")[0]
        for p in sorted(src.parent.iterdir()):
            if p == src or not p.is_file():
                continue
            if p.name.startswith(stem + ".") and ac.is_sidecar(p.name) and not p.name.lower().endswith(
                    SIDECAR_STAGE_SKIP):
                out.append(p)
        return out

    def _sidecar_added(self, src: Path, legacy_rel: str) -> bool:
        if not self.inventory:
            return False
        known = self._inventory_paths()
        aux = src.with_name(src.name + ".aux.xml")
        aux_rel = (Path(legacy_rel).parent / aux.name).as_posix()
        return aux.exists() and aux_rel not in known

    def _stage_copy(self, ds_id: str, legacy_rel: str, dst_root: Path, kind: str) -> dict:
        src = self.legacy_root / legacy_rel
        if not src.is_file():
            raise RunAbort(EXIT["input_missing"], [f"INPUT_MISSING:{ds_id}"])
        if self._sidecar_added(src, legacy_rel):
            raise RunAbort(EXIT["input_missing"], [f"LEGACY_REF_CHANGED:{ds_id}:aux.xml"])
        rec = {"dataset_id": ds_id, "legacy_path": legacy_rel, **file_record(src)}
        self._check_pin(ds_id, legacy_rel, rec)
        dst = dst_root / legacy_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(src, dst)
            for comp in self._companions(src):
                shutil.copyfile(comp, dst.parent / comp.name)
                os.chmod(dst.parent / comp.name, 0o444)
            os.chmod(dst, 0o444)
        staged_sha = sha256_file(dst)
        if staged_sha != rec["sha256"]:
            raise RunAbort(EXIT["input_missing"], [f"STAGE_HASH_MISMATCH:{ds_id}"])
        rec["staged"] = rel(self.dir, dst)
        rec["kind"] = kind
        return rec

    def preflight_inputs(self) -> None:
        seen: dict[str, dict] = {}
        feeds: dict[str, dict] = {}
        for ps in self.plan:
            if ps.action == "bridge":
                for ds in ps.outputs:
                    legacy = (self.ds.get(ds) or {}).get("legacy") or {}
                    if not legacy.get("path") or not (self.legacy_root / legacy["path"]).is_file():
                        raise RunAbort(EXIT["input_missing"], [f"INPUT_MISSING:{ds}"])
                continue
            if ps.action != "execute":
                continue
            for ds in ps.inputs:
                f = ps.feeds[ds]
                if f["source"] == "input":
                    key = f["legacy_path"]
                    if key not in seen:
                        seen[key] = self._stage_copy(ds, key, self.dir / "stage" / "inputs", "input")
                        seen[key]["dataset_ids"] = [ds]
                    elif ds not in seen[key]["dataset_ids"]:
                        seen[key]["dataset_ids"].append(ds)
                elif f["source"] == "legacy_isolation":
                    key = f["legacy_path"]
                    if key not in feeds:
                        feeds[key] = self._stage_copy(ds, key, self.dir / "stage" / "legacy_ro", "legacy_feed")
                        feeds[key]["fed_to"] = []
                    feeds[key]["fed_to"].append(ps.id)
        self.inputs_records = [dict(v, dataset_id=v["dataset_ids"][0]) for _, v in sorted(seen.items())]
        self.feeds_records = [v for _, v in sorted(feeds.items())]

    # ---- preflight: scanner and smoke tests -------------------------------------------------------------------
    def run_codescan(self, python: Path = VENV_PY) -> None:
        out = self.dir / "verify" / "scan.json"
        if not CODESCAN.is_file():
            self.scan_record = {"status": "not_run", "reason": "codescan.py is not installed in this plugin"}
            ac.dump_json(out, {"schema": "axis-migrate.scan.v1", **self.scan_record})
            return
        # codescan needs the real migration layout (S11 resolves the legacy root from it), so it scans the live tree;
        # the scan binds to the executed code only if the live tree equals the snapshot before and after the scan
        # and every file the scanner hashed equals its snapshot copy.
        drift = self._live_vs_snapshot()
        if drift:
            raise RunAbort(EXIT["contract"], [f"SCAN_TARGET_CHANGED:{d}" for d in drift])
        cmd = [str(python), str(CODESCAN), "--all", str(self.migration), "--out", str(out), "--json"]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "AXIS_MIGRATE_PLUGIN": str(PLUGIN),
               **DETERMINISM_ENV, **STEP_ENV_FIXED}
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            code, stdout, stderr = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired:
            code, stdout, stderr = None, "", "codescan timed out after 300 s"
        status = {0: "clean", 2: "blocked", 3: "rule_error"}.get(code, "error")
        if not out.is_file():   # older scanner without --out: keep its stdout
            try:
                parsed = json.loads(stdout) if stdout.strip() else None
            except ValueError:
                parsed = None
            doc = parsed if isinstance(parsed, dict) else {"schema": "axis-migrate.scan.v1", "stdout": stdout[-20000:]}
            doc.setdefault("exit_code", code)
            if stderr:
                doc.setdefault("stderr", stderr[-4000:])
            ac.dump_json(out, doc)
        drift = self._live_vs_snapshot() + self._scan_files_vs_snapshot(out)
        self.scan_record = {"status": status, "exit_code": code, "sha256": sha256_file(out),
                            "path": "verify/scan.json", "bound_to_snapshot": not drift,
                            "code_hash": self.code_hash}
        if drift:
            raise RunAbort(EXIT["contract"], [f"SCAN_TARGET_CHANGED:{d}" for d in drift])
        if code != 0:
            raise RunAbort(EXIT["contract"], ["SCAN_BLOCKED" if code == 2 else f"SCAN_ERROR:{code}"])

    def _live_vs_snapshot(self) -> list[str]:
        """What codescan reads (pipeline/, tests/, params.yaml, tolerances.yaml) must equal the snapshot."""
        snap = self.dir / "snapshot"
        bad = []
        if code_tree_hash(self.migration / "pipeline")[0] != code_tree_hash(snap / "axis_pipeline")[0]:
            bad.append("pipeline")
        live_t, snap_t = self.migration / "tests", snap / "tests"
        if live_t.is_dir() != snap_t.is_dir() or (live_t.is_dir() and ac.tree_hash(live_t) != ac.tree_hash(snap_t)):
            bad.append("tests")
        for f in ("params.yaml", "tolerances.yaml"):
            if hash_or_none(self.migration / f) != hash_or_none(snap / f):
                bad.append(f)
        return bad

    def _scan_files_vs_snapshot(self, scan_json: Path) -> list[str]:
        """Every file hash codescan recorded must be the snapshot's copy of that file."""
        try:
            doc = json.loads(scan_json.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        snap = self.dir / "snapshot"
        bad = []
        for f in doc.get("files") or []:
            rel_p, h = f.get("file"), f.get("sha256")
            if not rel_p or not h:
                continue
            if rel_p.startswith("pipeline/"):
                p = snap / "axis_pipeline" / rel_p[len("pipeline/"):]
            else:
                p = snap / rel_p
            if hash_or_none(p) != h:
                bad.append(rel_p)
        return bad

    def run_smoke_tests(self, python: Path = VENV_PY) -> None:
        out = self.dir / "verify" / "tests.json"
        tdir = self.dir / "snapshot" / "tests"
        files = sorted(p for p in tdir.rglob("test_*.py")) if tdir.is_dir() else []
        if not files:
            self.tests_record = {"status": "absent", "tests": 0}
            ac.dump_json(out, {"schema": "axis-migrate.tests.v1", **self.tests_record})
            return
        scratch = self.dir / "scratch" / "_tests"
        scratch.mkdir(parents=True, exist_ok=True)
        junit = scratch / "junit.xml"
        cmd = [str(python), "-m", "pytest", str(tdir), "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
               f"--rootdir={tdir}"]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONPATH": str(self.dir / "snapshot"),
               "TMPDIR": str(scratch), **DETERMINISM_ENV, **STEP_ENV_FIXED}
        t = time.monotonic()
        try:
            cp = subprocess.run(cmd, capture_output=True, text=True, timeout=600, env=env, cwd=str(scratch))
            code, tail = cp.returncode, (cp.stdout + cp.stderr)[-6000:]
        except subprocess.TimeoutExpired:
            code, tail = None, "pytest timed out after 600 s"
        counts, cases = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}, []
        if junit.is_file():
            import xml.etree.ElementTree as ET
            root = ET.parse(junit).getroot()
            suites = [root] if root.tag == "testsuite" else list(root)
            for s in suites:
                for k in counts:
                    counts[k] += int(s.get(k, 0))
                for c in s.iter("testcase"):
                    outcome = "passed"
                    for child in c:
                        if child.tag in ("failure", "error", "skipped"):
                            outcome = child.tag
                    cases.append({"name": f"{c.get('classname')}::{c.get('name')}", "outcome": outcome})
        status = "passed" if code == 0 else ("failed" if code == 1 else "error")
        self.tests_record = {"status": status, "exit_code": code, **counts,
                             "duration_s": round(time.monotonic() - t, 3)}
        ac.dump_json(out, {"schema": "axis-migrate.tests.v1", **self.tests_record,
                           "cases": sorted(cases, key=lambda c: c["name"]), "output_tail": tail,
                           "tests_tree_sha256": ac.tree_hash(tdir)})
        self.tests_record["sha256"] = sha256_file(out)
        self.tests_record["path"] = "verify/tests.json"

    def x(self, p: Path | str) -> str:
        """A local run path as the step process sees it (identity locally; remapped for remote executors)."""
        p = str(p)
        if self.exec_root is None:
            return p
        base = str(self.dir)
        return self.exec_root + p[len(base):] if p == base or p.startswith(base + "/") else p

    def show_path(self, p: str) -> str:
        """Run-relative inside the run, `project:<rel>` inside the project, else as is (no absolute project paths
        in hashed records; CONTRACTS §1)."""
        rp = _real(p)
        if self.dir is not None and _is_under(rp, _real(self.dir)):
            return os.path.relpath(rp, _real(self.dir))
        if _is_under(rp, _real(self.project)):
            return "project:" + os.path.relpath(rp, _real(self.project))
        return p

    # ---- per step -------------------------------------------------------------------------------------------
    def ctx_for(self, ps: PlannedStep) -> Path:
        out_tmp = self.dir / "outputs" / f"{ps.id}.tmp"
        out_tmp.mkdir(parents=True, exist_ok=True)
        scratch = self.dir / "scratch" / ps.id
        scratch.mkdir(parents=True, exist_ok=True)
        inputs = {ds: {**{k: f[k] for k in ("layer", "kind", "format", "source")}, "path": self.x(f["path"])}
                  for ds, f in ps.feeds.items()}
        outputs = {ds: {"path": self.x(out_tmp / s["name"]), "layer": s["layer"], "kind": s["kind"],
                        "format": s["format"], "may_be_empty": s["may_be_empty"]} for ds, s in ps.out_specs.items()}
        doc = {"schema": CTX_SCHEMA, "run_id": self.run_id, "step_id": ps.id, "chain": self.chain,
               "inputs": inputs, "outputs": outputs, "params_file": self.x(self.dir / "snapshot" / "params.yaml"),
               "declared_params": ps.params, "scratch_dir": self.x(scratch), "no_seek_roots": [],
               "io_log": self.x(self.dir / "logs" / f"{ps.id}.io.json"), "run_dir": self.x(self.dir)}
        p = self.dir / "ctx" / f"{ps.id}.json"
        ac.dump_json(p, doc)
        return p

    def step_env(self, ps: PlannedStep, extra: dict | None = None) -> dict:
        scratch = self.dir / "scratch" / ps.id
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "TMPDIR": str(scratch),
               "MPLCONFIGDIR": str(scratch / "mpl"), **DETERMINISM_ENV, **STEP_ENV_FIXED}
        if extra:
            env.update(extra)
        return env

    def boot_argv(self, ps: PlannedStep, python: Path | str, boot: Path | str | None = None) -> list[str]:
        # not -I: it implies -E, which would drop PYTHONHASHSEED; the environment is a full allow-list anyway.
        # -s: no user site, -P: no script dir on sys.path, -B: no bytecode written into the snapshot.
        return [str(python), "-s", "-P", "-B", str(boot or AXIS_BOOT), "--step", ps.id,
                "--ctx", self.x(self.dir / "ctx" / f"{ps.id}.json"), "--snapshot", self.x(self.dir / "snapshot"),
                "--result", self.x(self.dir / "logs" / f"{ps.id}.result.json"),
                "--expect", self.x(self.dir / "env" / "expect.json")]

    def ledger_policy(self, ps: PlannedStep, python: Path) -> LedgerPolicy:
        d = _real(self.dir)
        out_tmp = _real(self.dir / "outputs" / f"{ps.id}.tmp")
        allowed_prefixes = [out_tmp, _real(self.dir / "scratch" / ps.id), _real(self.dir / "snapshot" / "axis_pipeline")]
        allowed_files = [_real(self.dir / "snapshot" / "params.yaml"), _real(self.dir / "ctx" / f"{ps.id}.json"),
                         _real(self.dir / "env" / "expect.json"), _real(self.dir / "snapshot"),
                         _real(self.dir / "boot" / "axis_boot.py")]
        logs_dir = _real(self.dir / "logs")
        allowed_files.append(logs_dir)
        py = Path(python)
        sys_allow = {_real(AXIS_BOOT)}
        try:
            prefix = py.resolve().parents[1]
            sys_allow.add(_real(prefix))
            venv_cfg = py.parent.parent / "pyvenv.cfg"
            if venv_cfg.is_file():
                sys_allow.add(_real(py.parent.parent))
                for line in venv_cfg.read_text().splitlines():
                    if line.startswith("home"):
                        sys_allow.add(_real(Path(line.split("=", 1)[1].strip()).parent))
        except IndexError:
            pass
        inputs = [_real(f["path"]) for f in ps.feeds.values()]
        protected = sorted({_real(self.legacy_root), _real(self.migration), _real(self.project)})
        return LedgerPolicy(run_dir=d, step_id=ps.id, input_files=inputs, allowed_prefixes=allowed_prefixes,
                            allowed_files=allowed_files, system_allow=sorted(sys_allow), protected=protected,
                            out_tmp=out_tmp, strict=sorted({_real(self.legacy_root), _real(self.migration)}),
                            name_prefixes=[os.path.join(logs_dir, f"{ps.id}."), os.path.join(logs_dir, f".{ps.id}.")])

    def _rehash_feeds(self, ps: PlannedStep, before: dict[str, str]) -> list[str]:
        bad = []
        for ds, f in sorted(ps.feeds.items()):
            p = Path(f["path"])
            if not p.is_file() or sha256_file(p) != before.get(ds):
                bad.append(f"STAGED_INPUT_MODIFIED:{ds}")
        return bad

    def execute_step(self, ps: PlannedStep, executor: Callable[["Run", PlannedStep], ExecResult]) -> dict:
        before = {ds: sha256_file(Path(f["path"])) for ds, f in ps.feeds.items() if Path(f["path"]).is_file()}
        missing = [ds for ds, f in ps.feeds.items() if not Path(f["path"]).is_file()]
        if missing:
            raise RunAbort(EXIT["input_missing"], [f"INPUT_MISSING:{m}" for m in missing])
        self.ctx_for(ps)
        er = executor(self, ps)
        return self.evaluate_step(ps, er, before)

    def evaluate_step(self, ps: PlannedStep, er: ExecResult, before: dict[str, str]) -> dict:
        logs = self.dir / "logs"
        res_p = logs / f"{ps.id}.result.json"
        result = None
        if res_p.is_file():
            try:
                result = json.loads(res_p.read_text(encoding="utf-8"))
            except ValueError:
                result = None
        reasons: list[str] = list(er.extra_reasons)
        # ledger
        ledger_rel = er.ledger_path
        if er.ledger_kind in STRACE_KINDS:
            events = parse_strace(self.dir / ledger_rel) if ledger_rel else []
        elif er.ledger_kind in AUDIT_KINDS:
            events = parse_audit(self.dir / ledger_rel, logs / f"{ps.id}.stderr",
                                 cwd=er.ledger_root or str(self.dir)) if ledger_rel else []
        else:
            events = []
        if er.ledger_root:   # remote run root → this run dir, so one policy applies to both
            roots = sorted({er.ledger_root.rstrip("/"), os.path.realpath(er.ledger_root).rstrip("/")}, key=len,
                           reverse=True)
            for e in events:
                for root in roots:
                    if e["path"] == root or e["path"].startswith(root + "/"):
                        e["path"] = str(self.dir) + e["path"][len(root):]
                        e["remote"] = True
                        break
        pol = self.ledger_policy(ps, self.boot_python)
        led = classify_events(events, pol)
        for key in ("undeclared", "attempted_protected"):
            led[key] = [dict(u, path=self.show_path(u["path"])) for u in led[key]]
        led["undeclared_writes"] = [self.show_path(p) for p in led["undeclared_writes"]]
        if er.ledger_kind in STRACE_KINDS + AUDIT_KINDS and not events:
            reasons.append(f"LEDGER_EMPTY:{ps.id}")
        if led["undeclared"]:
            reasons.append(f"UNDECLARED_READ:{ps.id}")
        if led["undeclared_writes"]:
            reasons.append(f"UNDECLARED_WRITE:{ps.id}")
        # R1/R2
        if er.timed_out:
            reasons.insert(0, f"STEP_TIMED_OUT:{ps.id}")
        if result is None:
            reasons.append(f"RESULT_MISSING:{ps.id}")
        else:
            for rc in result.get("reason_codes", []):
                reasons.append(rc if ":" in rc else f"{rc}:{ps.id}")
            if result.get("exit_code") != er.exit_code and not er.timed_out:
                reasons.append(f"EXIT_CODE_DISAGREES:{ps.id}")
            if result.get("ok") is not True and er.exit_code == 0:
                reasons.append(f"RESULT_NOT_OK:{ps.id}")
        if er.exit_code not in (0, None) and not any(r.split(":")[0] in ("STEP_EXCEPTION", "STEP_CALLED_EXIT")
                                                      for r in reasons):
            reasons.append(f"STEP_NONZERO_EXIT:{ps.id}:{er.exit_code}")
        # R3 by the runner too (exists, non-empty, fresh)
        out_tmp = self.dir / "outputs" / f"{ps.id}.tmp"
        outs = []
        if er.exit_code == 0 and not er.timed_out:
            for ds, spec in sorted(ps.out_specs.items()):
                p = out_tmp / spec["name"]
                if not p.is_file():
                    reasons.append(f"OUTPUT_MISSING:{ds}")
                    continue
                st = p.stat()
                if st.st_size == 0:
                    reasons.append(f"OUTPUT_EMPTY:{ds}")
                if st.st_mtime < er.started_epoch - 1.0:
                    reasons.append(f"OUTPUT_STALE:{ds}")
                boot_rec = next((o for o in (result or {}).get("outputs", []) if o.get("dataset_id") == ds), {})
                outs.append({"dataset_id": ds, "path": f"outputs/{ps.id}/{spec['name']}", "layer": spec["layer"],
                             "driver": boot_rec.get("driver"), "opened_ok": boot_rec.get("opened_ok"),
                             "mtime_after_start": st.st_mtime >= er.started_epoch - 1.0,
                             **file_record(p)})
        # R6
        reasons += self._rehash_feeds(ps, before)
        declared_names = {s["name"] for s in ps.out_specs.values()}
        out_tmp.mkdir(parents=True, exist_ok=True)
        unexpected = sorted(p.relative_to(out_tmp).as_posix() for p in out_tmp.rglob("*")
                            if p.is_file() and p.relative_to(out_tmp).as_posix() not in declared_names) \
            if out_tmp.is_dir() else []
        writes_ledger = sorted({f"outputs/{ps.id}/" + Path(w).relative_to(pol.out_tmp).as_posix()
                                for w in led["writes"] if _is_under(w, pol.out_tmp) and w != pol.out_tmp})
        if er.exit_code == 0 and not er.timed_out and er.ledger_kind in STRACE_KINDS + AUDIT_KINDS:
            for o in outs:
                if o["path"] not in writes_ledger:
                    reasons.append(f"OUTPUT_NOT_IN_WRITE_LEDGER:{o['dataset_id']}")
            # the ledger must have SEEN every read the runtime recorded; otherwise it is blind on this platform
            # and cannot vouch for "no undeclared read" (refuse rather than pass silently)
            io_p = logs / f"{ps.id}.io.json"
            if io_p.is_file():
                try:
                    io_reads = json.loads(io_p.read_text(encoding="utf-8")).get("reads", [])
                except ValueError:
                    io_reads = []
                seen = set(led["declared_opened"])
                for r in io_reads:
                    f = ps.feeds.get(r.get("dataset"))
                    if f and not r.get("staged_local") and _real(f["path"]) not in seen:
                        reasons.append(f"LEDGER_BLIND:{r.get('dataset')}")
        code = self._exit_for(reasons, er)
        status = "succeeded" if code == 0 else ("timed_out" if code == 14 else
                                                 ("blocked" if code in (12, 13) else "failed"))
        final_dir = self.dir / "outputs" / (ps.id if code == 0 else f"{ps.id}.failed")
        out_tmp.mkdir(parents=True, exist_ok=True)
        if code != 0:  # keep what the step left in scratch, for diagnosis; never an input of anything
            partial = self.dir / "scratch" / ps.id / "out"
            if partial.is_dir() and any(partial.rglob("*")):
                shutil.copytree(partial, out_tmp / "_scratch_partial", dirs_exist_ok=True)
        os.replace(out_tmp, final_dir)
        for p in final_dir.rglob("*"):
            if p.is_file():
                os.chmod(p, 0o444)
        shutil.rmtree(self.dir / "scratch" / ps.id, ignore_errors=True)
        log_sha = {}
        for suffix in ("stdout", "stderr", "reads", "result.json", "io.json", "audit"):
            p = logs / f"{ps.id}.{suffix}"
            if p.is_file():
                log_sha[f"logs/{ps.id}.{suffix}"] = sha256_file(p)
        snap_mod = self.dir / "snapshot" / "axis_pipeline" / f"{ps.id}.py"
        rec = {"step_id": ps.id, "kind": ps.kind, "action": "executed", "module": f"snapshot/axis_pipeline/{ps.id}.py",
               "module_sha256": hash_or_none(snap_mod),
               "fed_from": {ds: ("legacy" if f["source"] == "legacy_isolation" else
                                 (f"reused:{self.resume_from}" if f["source"] == "reused" else f["source"]))
                            for ds, f in sorted(ps.feeds.items())},
               "isolation": ("partial" if any(f.get("isolation") == "partial" for f in ps.feeds.values())
                             else ("full" if self.chain == "isolated" else None)),
               "started_at": er.started_at, "ended_at": er.ended_at, "duration_s": round(er.duration_s, 3),
               "exit_code": er.exit_code, "signal": er.signal, "timed_out": er.timed_out, "timeout_s": ps.timeout_s,
               "status": status, "runner_exit_code": code, "reason_codes": sorted(set(reasons)),
               "peak_rss_mb": er.peak_rss_mb,
               "logs": {"stdout": f"logs/{ps.id}.stdout", "stderr": f"logs/{ps.id}.stderr",
                        "result": f"logs/{ps.id}.result.json", "io": f"logs/{ps.id}.io.json", "sha256": log_sha},
               "reads": {"ledger": er.ledger_path, "kind": er.ledger_kind, "undeclared": led["undeclared"],
                         "attempted_protected": led["attempted_protected"][:50],
                         "declared_opened": [rel(self.dir, p) if _is_under(p, _real(self.dir)) else p
                                             for p in led["declared_opened"]]},
               "writes_ledger": writes_ledger, "undeclared_writes": led["undeclared_writes"],
               "writes_elsewhere": led["writes_elsewhere"][:50],
               "outputs": outs if code == 0 else [], "unexpected_outputs": unexpected,
               "exception": (result or {}).get("exception"),
               "stub_check_passed": ((result or {}).get("stub_check") or {}).get("passed")}
        return rec

    @staticmethod
    def _exit_for(reasons: list[str], er: ExecResult) -> int:
        heads = [r.split(":", 1)[0] for r in reasons]
        if er.timed_out or "STEP_TIMED_OUT" in heads:
            return EXIT["timed_out"]
        if any(h in CONTRACT_REASONS for h in heads):
            return EXIT["contract"]
        if any(h in ENV_REASONS for h in heads):
            return EXIT["environment"]
        if any(h in ("PARAM_MISSING", "INPUT_MISSING", "DATASET_NOT_DECLARED") for h in heads) and er.exit_code == 2:
            return EXIT["input_missing"]
        if reasons or er.exit_code != 0:
            return EXIT["step_failed"]
        return EXIT["succeeded"]

    def reuse_step(self, ps: PlannedStep) -> dict:
        parent = self.parent or {}
        pdir = Path(parent["_dir"])
        prec = next(s for s in parent.get("steps", []) if s["step_id"] == ps.id)
        dst = self.dir / "outputs" / ps.id
        src = pdir / "outputs" / ps.id
        shutil.copytree(src, dst)
        outs = []
        for o in prec.get("outputs", []):
            p = self.dir / o["path"]
            if not p.is_file() or sha256_file(p) != o["sha256"]:
                raise RunAbort(EXIT["immutability"], [f"REUSED_OUTPUT_HASH_MISMATCH:{o['dataset_id']}"])
            outs.append(dict(o))
        for p in dst.rglob("*"):
            os.chmod(p, 0o444 if p.is_file() else 0o555)
        os.chmod(dst, 0o755)
        return {"step_id": ps.id, "kind": ps.kind, "action": "reused", "status": "reused",
                "reused_from": parent.get("run_id"), "module_sha256": prec.get("module_sha256"),
                "fed_from": prec.get("fed_from", {}), "exit_code": prec.get("exit_code"), "reason_codes": [],
                "outputs": outs, "writes_ledger": prec.get("writes_ledger", []),
                "writes_ledger_source": f"run:{parent.get('run_id')}", "reads": prec.get("reads")}

    def bridge_step(self, ps: PlannedStep) -> dict:
        dst = self.dir / "outputs" / ps.id
        dst.mkdir(parents=True, exist_ok=True)
        outs = []
        for ds, spec in sorted(ps.out_specs.items()):
            legacy = (self.ds.get(ds) or {}).get("legacy") or {}
            src = self.legacy_root / legacy["path"]
            rec = {"dataset_id": ds, "legacy_path": legacy["path"], **file_record(src)}
            self._check_pin(ds, legacy["path"], rec)
            p = dst / spec["name"]
            shutil.copyfile(src, p)
            os.chmod(p, 0o444)
            outs.append({"dataset_id": ds, "path": f"outputs/{ps.id}/{spec['name']}", "bridged_from": legacy["path"],
                         **file_record(p)})
        return {"step_id": ps.id, "kind": ps.kind, "action": "bridged", "status": "succeeded",
                "reason_codes": [], "outputs": outs, "writes_ledger": [o["path"] for o in outs],
                "writes_ledger_source": "runner-bridge-copy", "fed_from": {}}

    # ---- the loop -------------------------------------------------------------------------------------------
    def run_steps(self, executor: Callable[["Run", PlannedStep], ExecResult]) -> int:
        for ps in self.plan:
            if ps.action == "reuse":
                rec = self.reuse_step(ps)
            elif ps.action == "bridge":
                rec = self.bridge_step(ps)
            else:
                rec = self.execute_step(ps, executor)
            self.steps_records.append(rec)
            for o in rec.get("outputs", []):
                self.outputs_records.append({"dataset_id": o["dataset_id"], "path": o["path"], "sha256": o["sha256"],
                                             "content_sha256": o.get("content_sha256"), "bytes": o.get("bytes"),
                                             "written_by_step": ps.id,
                                             **({"reused_from": rec["reused_from"]} if rec.get("reused_from") else {}),
                                             **({"bridged": True} if rec.get("action") == "bridged" else {})})
            code = rec.get("runner_exit_code", 0)
            if code:
                self.reasons += rec["reason_codes"]
                return code
        return 0

    def env_control(self) -> dict:
        """refenv run: compare outputs with the newest succeeded pipeline-env run of the same code and chain (D5-4).
        Identical outputs exclude the environment as the cause of a difference; differing ones are the env's."""
        idx = self.migration / "runs" / "index.jsonl"
        rows = []
        if idx.is_file():
            for line in idx.read_text(encoding="utf-8").splitlines():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
        for row in reversed(rows):
            if row.get("executor") != "local" or row.get("status") != "succeeded" or row.get("chain") != self.chain:
                continue
            mp = self.migration / "runs" / row["run_id"] / "manifest.json"
            if not mp.is_file():
                continue
            other = ac.load_json(mp)
            if other.get("code", {}).get("code_hash") != self.code_hash:
                continue
            theirs = {o["dataset_id"]: o for o in other.get("outputs", [])}
            identical, same_content, differ, missing = [], [], [], []
            for o in self.outputs_records:
                t = theirs.get(o["dataset_id"])
                if t is None:
                    missing.append(o["dataset_id"])
                elif t["sha256"] == o["sha256"]:
                    identical.append(o["dataset_id"])
                elif o.get("content_sha256") and t.get("content_sha256") == o.get("content_sha256"):
                    same_content.append(o["dataset_id"])
                else:
                    differ.append(o["dataset_id"])
            return {"against_run": row["run_id"], "byte_identical": sorted(identical),
                    "content_identical": sorted(same_content), "differ": sorted(differ),
                    "not_in_pipeline_run": sorted(missing),
                    "note": "differences here are the environment's (same snapshot, different native libraries)"}
        return {"against_run": None, "note": "no succeeded pipeline-env run of the same code to compare with"}

    # ---- finalise ---------------------------------------------------------------------------------------------
    def _worktree_changes(self) -> list[str]:
        changed = []
        live_code, _ = code_tree_hash(self.migration / "pipeline") if (self.migration / "pipeline").is_dir() \
            else (None, None)
        if live_code != self.code_hash:
            changed.append("pipeline")
        for live, name in self.LIVE_FILES.items():
            if hash_or_none(self.migration / live) != self.snapshot_hashes.get(name):
                changed.append(live)
        return changed

    def finalise(self, exit_code: int, *, extra_reasons: list[str] | None = None) -> dict:
        assert self.dir is not None
        reasons = sorted(set(self.reasons + (extra_reasons or [])))
        status = STATUS_FOR_EXIT.get(exit_code, "failed")
        # R6 again for the legacy originals and staged inputs
        for rec in self.inputs_records + self.feeds_records:
            src = self.legacy_root / rec["legacy_path"]
            rec["unchanged_after_run"] = src.is_file() and sha256_file(src) == rec["sha256"]
            staged = self.dir / rec["staged"]
            rec["staged_unchanged_after_run"] = staged.is_file() and sha256_file(staged) == rec["sha256"]
            if not rec["unchanged_after_run"]:
                reasons.append(f"LEGACY_INPUT_CHANGED_DURING_RUN:{rec['dataset_id']}")
        changes = self._worktree_changes() if self.snapshot_hashes else []
        steps_planned = len(self.plan)
        succeeded = [s for s in self.steps_records if s.get("status") in ("succeeded", "reused")]
        in_scope = {s["id"] for s in in_scope_steps(self.graph)}
        done = {s["step_id"] for s in succeeded}
        full = bool(self.chain == "chained" and status == "succeeded" and in_scope and in_scope <= done)
        stub_ok = self.env_record.get("stub_check", {}).get("passed", False) and all(
            s.get("stub_check_passed") is not False for s in self.steps_records)
        undeclared = any(s.get("reads", {}) and s["reads"].get("undeclared") for s in self.steps_records)
        eligible = "none"
        if (self.chain == "chained" and self.executor in ("local", "e2b") and status == "succeeded" and stub_ok
                and not undeclared and self.env_record.get("freeze_matches_lock") and not self.allow_env_major
                and self.read_ledger in ("strace", "e2b-physical", "e2b-physical+strace", "audit+cpl_debug",
                                         "e2b-physical+audit")):
            eligible = "sandbox-validated"
        man = {
            "schema": MANIFEST_SCHEMA, "run_id": self.run_id, "project": self.graph.get("project", {}).get("name"),
            "plugin_version": ac.PLUGIN_VERSION, "created_at": self.started_at, "started_at": self.started_at,
            "ended_at": ac.utc_now_iso(), "wall_time_s": round(time.monotonic() - self.t0, 3),
            "chain": self.chain, "executor": self.executor, "mode": self.executor, "status": status,
            "exit_code": exit_code, "reason_codes": sorted(set(reasons)), "steps_planned": steps_planned,
            "steps_succeeded": len(succeeded), "steps_filter": self.steps_filter, "full": full,
            "eligible_for": eligible, "eligible_for_note": "informational; never trusted (ladder.py decides)",
            "read_ledger": self.read_ledger, "resumed_from": self.resume_from, "argv": self.argv,
            # D5-9: without strace (macOS) the ledger is policy-level (audit hook + CPL_DEBUG): a lock-matched
            # chained run may support sandbox-validated, never more
            "read_ledger_rung_cap": "sandbox-validated" if self.read_ledger in AUDIT_KINDS else None,
            "code": {"code_hash": self.code_hash, "files": [dict(e, path="pipeline/" + e["path"])
                                                             for e in self.code_files],
                     "worktree_changed_during_run": bool(changes), "worktree_changes": changes},
            "params": {"path": "snapshot/params.yaml", "sha256": self.snapshot_hashes.get("params.yaml")},
            "graph": {"path": "snapshot/graph.json", "sha256": self.snapshot_hashes.get("graph.json"),
                      "revision": self.graph.get("revision")},
            "inventory": {"sha256": self.snapshot_hashes.get("inventory.json")},
            "tolerances": {"sha256": self.snapshot_hashes.get("tolerances.yaml")},
            # unsigned proposals: not part of the contract; compare uses them only for NEEDS_DECISION vs FAIL
            "tolerances_proposed": ({"sha256": self.snapshot_hashes["tolerances.proposed.yaml"]}
                                    if self.snapshot_hashes.get("tolerances.proposed.yaml") else None),
            "known_differences": {"sha256": self.snapshot_hashes.get("known-differences.md")},
            "decisions": {"sha256": self.snapshot_hashes.get("decisions.md")},
            "env": self.env_record, "reference_env": self.reference_env, "env_comparison": self.env_cmp,
            "inputs": self.inputs_records, "legacy_feeds": self.feeds_records,
            "steps": self.steps_records, "outputs": sorted(self.outputs_records, key=lambda o: o["dataset_id"]),
            "scan": self.scan_record, "tests": self.tests_record, "preflight": self.preflight,
            "plan": [{"step_id": p.id, "action": p.action, "timeout_s": p.timeout_s} for p in self.plan],
            "legacy_root": os.path.relpath(self.legacy_root, self.migration),
            "sealed": True, "checksums": "checksums.sha256",
        }
        if self.executor == "refenv":
            man["env_control"] = self.env_control()
        if self.e2b_record is not None:
            man["env"]["e2b"] = self.e2b_record
        shutil.rmtree(self.dir / "scratch", ignore_errors=True)
        ac.dump_json(self.dir / "manifest.json", man)
        write_checksums(self.dir)
        seal(self.dir)
        append_index(self.migration, {"run_id": self.run_id, "chain": self.chain, "executor": self.executor,
                                      "status": status, "exit_code": exit_code, "started_at": self.started_at})
        return man


# ---------------------------------------------------------------------------------------------------------------
# sealing helpers (also used by seal.py / report via import)
# ---------------------------------------------------------------------------------------------------------------
def _walk_files(run_dir: Path) -> list[Path]:
    out = []
    for dirpath, dirnames, filenames in os.walk(run_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            out.append(Path(dirpath) / fn)
    return out


def write_checksums(run_dir: Path) -> None:
    lines = []
    for p in _walk_files(run_dir):
        r = p.relative_to(run_dir).as_posix()
        if r == "checksums.sha256":
            continue
        lines.append(f"{sha256_file(p)}  {r}")
    (run_dir / "checksums.sha256").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_checksums(run_dir: Path) -> list[str]:
    """Mismatches between checksums.sha256 and the sealed files (verify/, amend/ and target/ added after sealing are ignored; they are sealed by seal.py)."""
    ck = run_dir / "checksums.sha256"
    if not ck.is_file():
        return ["CHECKSUMS_MISSING"]
    bad = []
    listed = set()
    for line in ck.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        h, r = line.split("  ", 1)
        listed.add(r)
        p = run_dir / r
        if not p.is_file():
            bad.append(f"MISSING:{r}")
        elif sha256_file(p) != h:
            bad.append(f"CHANGED:{r}")
    for p in _walk_files(run_dir):
        r = p.relative_to(run_dir).as_posix()
        if r != "checksums.sha256" and r not in listed and not r.startswith(("verify/", "amend/", "target/")):
            bad.append(f"ADDED:{r}")
    return bad


def seal(run_dir: Path) -> None:
    """chmod a-w everything except the verify/ directory itself (compare/plot/review write there later)."""
    verify = run_dir / "verify"
    for dirpath, dirnames, filenames in os.walk(run_dir, topdown=False):
        d = Path(dirpath)
        for fn in filenames:
            p = d / fn
            mode = stat.S_IMODE(p.stat().st_mode)
            os.chmod(p, (0o555 if mode & 0o111 else 0o444))
        if d != verify:
            os.chmod(d, stat.S_IMODE(d.stat().st_mode) & ~0o222)
    os.chmod(verify, 0o755)


def append_index(migration: Path, row: dict) -> None:
    idx = migration / "runs" / "index.jsonl"
    line = json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    with open(idx, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def vendor_runtime(pipeline_dir: Path) -> list[str]:
    """Copy the plugin runtime (byte-identical) into a pipeline package; create __init__.py if absent."""
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    done = []
    for f in RUNTIME_FILES:
        shutil.copyfile(RUNTIME_SRC / f, pipeline_dir / f)
        done.append(f)
    init = pipeline_dir / "__init__.py"
    if not init.exists():
        shutil.copyfile(RUNTIME_SRC / "__init__.py", init)
        done.append("__init__.py")
    return done
