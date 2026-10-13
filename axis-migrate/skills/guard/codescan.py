#!/usr/bin/env python3
"""codescan.py: the axis-migrate code scanner (08 §4, DESIGN §5). AST rules S01-S18, stdlib only.

One rule engine for the PostToolUse hook (H6) and the runner preflight (05):

    codescan.py --hook                      PostToolUse JSON on stdin; blocks with {"decision": "block"}
    codescan.py --all <migration> [--out verify/scan.json] [--json]
    codescan.py --file F [F ...] [--migration M] [--as pipeline|tests] [--json]
    codescan.py --corpus DIR [DIR ...] [--json]      per-rule file counts (corpus measurement, D8-5)

Exit codes: 0 clean (warnings allowed) · 2 at least one block finding · 3 a rule raised (fail closed).
Block rules have no inline suppression; exceptions come only from declared params/graph entries named per rule.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import axis_common as ac  # noqa: E402
import recordparse as rp  # noqa: E402

SCHEMA = "axis-migrate.scan.v1"

RULES = {
    "S01": ("swallow-broad", "block", "re-raise (or let it propagate); never continue after a broad except"),
    "S02": ("except-pass", "block", "handle the error or let it propagate; an empty handler hides failures"),
    "S03": ("failure-as-value", "block", "raise instead of returning a status dict; the runner owns success"),
    "S04": ("import-fallback", "block", "import unconditionally; optional deps belong in the lock, not in code"),
    "S05": ("stub-injection", "block", "never stub modules; real libraries only (stub canary, 05 D5-8)"),
    "S06": ("mock-in-pipeline", "block", "pipeline code may not import mock libraries"),
    "S07": ("config-default", "block", "read the value by id with ctx.param(id) / params[id]; declare it in params.yaml"),
    "S08": ("fill-literal", "block", "declare the fill value in params.yaml and read it with ctx.param"),
    "S09": ("rng", "block", "no randomness in a migration step; if sampling is real, seed from a declared param"),
    "S10": ("fabricate-missing", "block", "a missing input must raise (InputMissing, exit 2), never be synthesised"),
    "S11": ("reference-path", "block", "no path literals; reach data only via ctx.input_path(ds_id)/ctx.read_*"),
    "S12": ("test-mocks-subject", "block", "do not patch the pipeline or GIS/science libraries in tests"),
    "S13": ("analysis-import", "block", "nothing may import from migration/analysis/"),
    "S14": ("legacy-engine", "block", "no arcpy/qgis/processing outside a keep_legacy step"),
    "S15": ("parse", "block", "the file must be complete, valid Python"),
    "S16": ("param-id", "block", "use a param id that exists in params.yaml"),
    "S17": ("3857-distance", "warn", "measure distances/areas in a metric CRS (p.measure_crs), not EPSG:3857"),
    "S18": ("axis-order", "warn", "pass always_xy=True to Transformer.from_crs"),
    "T01": ("tolerance-record", "warn", "a tolerance record needs correct_spread and nearest_wrong (10 C2.3)"),
}

CONFIG_NAMES = {"params", "config", "cfg", "settings", "conf", "spec"}
BROAD = {"Exception", "BaseException"}
EXIT_CALLS = {"sys.exit", "os._exit", "exit", "quit", "_exit"}
GEO_LIBS = {"geopandas", "rasterio", "numpy", "pandas", "shapely", "pyproj", "pyogrio", "fiona", "scipy", "xarray",
            "rioxarray", "exactextract"}
LEGACY_ENGINES = {"arcpy", "qgis", "processing"}
DATA_CTORS = {"GeoDataFrame", "DataFrame", "GeoSeries", "Point", "Polygon"}
NP_CTORS = {"zeros", "ones", "full", "empty"}
PATH_EXT_RE = re.compile(r"\.(tif|tiff|gpkg|shp|dbf|csv|xlsx|xls|xlsm|json|geojson|gdb|txt|vrt|nc|parquet|zip|qgz|"
                         r"qgs|md|img|asc|jp2|sqlite|feather|pkl|npy|ovr|xml|yaml|yml)$", re.I)
ABS_ROOTS = r"(?:tmp|home|mnt|Volumes|data|opt|var|Users|srv|media|dbfs|local_disk0|workspace|Workspace|root|scratch)"
ABS_PATH_RE = re.compile(r"^(?:/(?!vsi[a-z0-9_]*/|dev/|proc/)(?:[\w.\-~@+]+/[\w.\-~@+{}*?=]+|" + ABS_ROOTS + r"/?)"
                         r"(?:/[\w.\-~@+{}*?=]*)*"
                         r"|[A-Za-z]:\\[^\s]*|[A-Za-z]:/(?!/)[^\s]*|~[/\\][^\s]*|\\\\[^\s]+)$")
MD_FIRST_RE = re.compile(r"^(?:```|\||#{1,6} \S)")
STEP_MODULE_RE = re.compile(r"^(?:[sm]\d\d[a-z]?_\w+|step_?\d+\w*)$")


@dataclass
class Finding:
    rule: str
    sev: str
    file: str
    line: int
    msg: str
    evidence: str = ""


class RuleError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------
# Migration context (graph, params, protected set)
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class Context:
    migration: Path | None = None
    graph: dict | None = None
    params_ids: set | None = None
    params_text: str | None = None
    protected: object | None = None  # protected.ProtectedSet
    decisions_ids: set = field(default_factory=set)

    @classmethod
    def load(cls, migration: str | os.PathLike | None) -> "Context":
        if migration is None:
            return cls()
        mig = Path(migration).resolve()
        c = cls(migration=mig)
        gpath = mig / "graph.json"
        if gpath.is_file():
            c.graph = ac.load_json(gpath)
            import protected as prot  # local module, stdlib only
            # always computed fresh from graph + inventory: the cache file is never trusted for a verdict
            c.protected = prot.ProtectedSet(mig, prot.compute(mig, graph=c.graph))
        ppath = mig / "params.yaml"
        if ppath.is_file():
            c.params_text = ppath.read_text(encoding="utf-8")
            items = rp.yaml_list_items(c.params_text, "params") or []
            c.params_ids = {i.id for i in items if i.id}
        dpath = mig / "decisions.md"
        if dpath.is_file():
            c.decisions_ids = {b.id for b in rp.md_blocks(dpath.read_text(encoding="utf-8"))}
        return c

    def step(self, step_id: str | None) -> dict | None:
        if not self.graph or not step_id:
            return None
        for s in self.graph.get("steps") or []:
            if s.get("id") == step_id:
                return s
        return None

    def step_for_module(self, path: Path, tree: ast.AST | None) -> dict | None:
        sid = None
        if tree is not None:
            for node in getattr(tree, "body", []):
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "STEP_ID"
                                                        for t in node.targets):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        sid = node.value.value
        st = self.step(sid) or self.step(path.stem)
        if st is None and self.graph and self.migration:
            try:
                rel = path.resolve().relative_to(self.migration).as_posix()
            except ValueError:
                rel = None
            for s in self.graph.get("steps") or []:
                if rel and ((s.get("new") or {}).get("module") == rel):
                    return s
        return st

    def local_modules(self) -> set:
        """Module names of the code under test: pipeline/*.py stems (S12)."""
        if self.migration is None or not (self.migration / "pipeline").is_dir():
            return set()
        return {p.stem for p in (self.migration / "pipeline").glob("*.py")}

    def kind_of(self, path: Path) -> str | None:
        if self.migration is None:
            return None
        try:
            rel = path.resolve().relative_to(self.migration).as_posix()
        except ValueError:
            return None
        if "__pycache__" in rel:
            return None
        if rel.startswith("pipeline/") and rel.endswith(".py"):
            return "pipeline"
        if rel.startswith("tests/") and rel.endswith(".py"):
            return "tests"
        if rel == "params.yaml":
            return "params"
        if rel == "tolerances.yaml":
            return "tolerances"
        return None

    def rel(self, path: Path) -> str:
        if self.migration is not None:
            try:
                return path.resolve().relative_to(self.migration).as_posix()
            except ValueError:
                pass
        return str(path)


# ---------------------------------------------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------------------------------------------
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def walk_local(nodes):
    """Walk statements/expressions without descending into nested functions, lambdas or classes."""
    stack = list(nodes) if isinstance(nodes, list) else [nodes]
    while stack:
        n = stack.pop()
        yield n
        for ch in ast.iter_child_nodes(n):
            if not isinstance(ch, NESTED):
                stack.append(ch)


def dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def last_name(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return last_name(node.func)
    if isinstance(node, ast.Subscript):
        return last_name(node.value)
    return None


def call_name(call: ast.Call) -> str | None:
    return dotted(call.func)


def is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def is_literal(node) -> bool:
    if isinstance(node, ast.Constant) and node.value is not None and not isinstance(node.value, bytes):
        return True
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) and isinstance(node.operand,
                                                                                                 ast.Constant):
        return True
    return False


def is_param_access(node) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Subscript) and last_name(n.value) in ("params", "PARAMS"):
            return True
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "param":
            return True
    return False


def has_raise_or_exit(stmts) -> bool:
    for n in walk_local(list(stmts)):
        if isinstance(n, ast.Raise):
            return True
        if isinstance(n, ast.Call):
            nm = call_name(n)
            if nm in EXIT_CALLS:
                return True
    return False


def handler_types(h: ast.ExceptHandler) -> list[str]:
    if h.type is None:
        return ["<bare>"]
    elts = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
    return [last_name(e) or "?" for e in elts]


def src_line(lines: list[str], lineno: int) -> str:
    if 1 <= lineno <= len(lines):
        return lines[lineno - 1].strip()[:160]
    return ""


def docstring_nodes(tree) -> set:
    ids = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if isinstance(body, list):
            for st in body:
                if isinstance(st, ast.Expr) and is_str(st.value):
                    ids.add(id(st.value))
    return ids


IMPORTLIB_FUNCS = {"module_from_spec", "import_module", "load_module", "load_source", "SourceFileLoader",
                   "spec_from_file_location", "import_from_path", "load_step_module"}


def build_aliases(tree) -> dict:
    """local name -> canonical dotted module/object (import aliases only)."""
    al = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    al[a.asname] = a.name
                else:
                    al[a.name.split(".")[0]] = a.name.split(".")[0]
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                al[a.asname or a.name] = f"{n.module}.{a.name}"
        elif isinstance(n, ast.ImportFrom) and n.level > 0:
            for a in n.names:  # relative import: always the local package
                al[a.asname or a.name] = f"<local>.{a.name}"
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) and \
                last_name(n.value.func) in IMPORTLIB_FUNCS:
            for t in n.targets:
                if isinstance(t, ast.Name):
                    al[t.id] = f"<local>.{t.id}"  # module loaded from a file path: the code under test
    return al


def canonical(name: str | None, aliases: dict) -> str | None:
    if not name:
        return None
    root, _, rest = name.partition(".")
    base = aliases.get(root, root)
    return f"{base}.{rest}" if rest else base


# ---------------------------------------------------------------------------------------------------------------
# File scan
# ---------------------------------------------------------------------------------------------------------------
class FileScan:
    def __init__(self, path: Path, kind: str, ctx: Context, display: str, local_modules: set | None = None):
        self.path = path
        self.local_modules = local_modules if local_modules is not None else ctx.local_modules()
        self.kind = kind  # pipeline | tests
        self.ctx = ctx
        self.display = display
        self.src = path.read_text(encoding="utf-8", errors="replace")
        self.lines = self.src.splitlines()
        self.tree = None
        self.parse_error = None
        try:
            self.tree = ast.parse(self.src, filename=str(path))
        except (SyntaxError, ValueError) as e:
            self.parse_error = e
        self.aliases = build_aliases(self.tree) if self.tree is not None else {}
        self.docstrings = docstring_nodes(self.tree) if self.tree is not None else set()
        self._calls = None

    @property
    def calls(self) -> list[ast.Call]:
        if self._calls is None:
            self._calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)]
        return self._calls

    def canon_call(self, call: ast.Call) -> str | None:
        return canonical(call_name(call), self.aliases)


def r_s01(fs: FileScan):
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.ExceptHandler):
            types = handler_types(n)
            if ("<bare>" in types or any(t in BROAD for t in types)) and not has_raise_or_exit(n.body):
                yield n.lineno, f"except {'/'.join(types)} without raise or exit", src_line(fs.lines, n.lineno)


def _only_pass(body) -> bool:
    for st in body:
        if isinstance(st, (ast.Pass, ast.Continue)):
            continue
        if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
            continue
        return False
    return True


def _cleanup_idiom(tr, h) -> bool:
    if handler_types(h) != ["FileNotFoundError"] or len(tr.body) != 1:
        return False
    st = tr.body[0]
    return (isinstance(st, ast.Expr) and isinstance(st.value, ast.Call)
            and last_name(st.value.func) in ("remove", "unlink", "rmtree"))


def r_s02(fs: FileScan):
    for n in ast.walk(fs.tree):
        if isinstance(n, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            for h in n.handlers:
                if _only_pass(h.body) and not _cleanup_idiom(n, h):
                    yield h.lineno, f"except {'/'.join(handler_types(h))}: pass", src_line(fs.lines, h.lineno)


STATUS_KEYS = {"success", "status", "ok", "error"}


def _status_dict(v) -> bool:
    if isinstance(v, ast.Dict):
        return any(is_str(k) and k.value in STATUS_KEYS for k in v.keys if k is not None)
    if isinstance(v, ast.Call) and last_name(v.func) == "dict":
        return any(k.arg in STATUS_KEYS for k in v.keywords)
    return False


def r_s03(fs: FileScan):
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.ExceptHandler) and not has_raise_or_exit(n.body):
            for m in walk_local(list(n.body)):
                if isinstance(m, ast.Return) and m.value is not None and _status_dict(m.value):
                    yield m.lineno, "failure returned as a status dict", src_line(fs.lines, m.lineno)


def r_s04(fs: FileScan):
    imp_exc = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException", "<bare>"}
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.Try) and any(isinstance(m, (ast.Import, ast.ImportFrom)) for m in walk_local(n.body)):
            for h in n.handlers:
                if any(t in imp_exc for t in handler_types(h)) and not has_raise_or_exit(h.body):
                    yield h.lineno, "import inside try with a fallback handler", src_line(fs.lines, h.lineno)


STUB_CALLS = {"sys.modules.setdefault", "sys.modules.update", "sys.modules.__setitem__", "sys.meta_path.insert",
              "sys.meta_path.append", "sys.path_hooks.insert", "sys.path_hooks.append"}


def r_s05(fs: FileScan):
    for n in ast.walk(fs.tree):
        if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            flat = []
            for t in targets:
                flat.extend(t.elts if isinstance(t, (ast.Tuple, ast.List)) else [t])
            for t in flat:
                if isinstance(t, ast.Subscript) and dotted(t.value) == "sys.modules":
                    yield n.lineno, "assignment into sys.modules", src_line(fs.lines, n.lineno)
                elif isinstance(t, ast.Attribute) and dotted(t) in ("builtins.open", "__builtins__.open"):
                    yield n.lineno, "builtins.open replaced", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.Call):
            nm = call_name(n) or ""
            if nm in STUB_CALLS:
                yield n.lineno, f"{nm}()", src_line(fs.lines, n.lineno)
            elif nm.endswith("patch.dict") or nm.endswith("monkeypatch.setitem") or nm == "setitem":
                if n.args and (dotted(n.args[0]) == "sys.modules" or (is_str(n.args[0]) and n.args[0].value == "sys.modules")):
                    yield n.lineno, "sys.modules patched", src_line(fs.lines, n.lineno)
            elif nm == "setattr" and n.args and dotted(n.args[0]) in ("builtins", "__builtins__"):
                yield n.lineno, "setattr on builtins", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.ClassDef):
            for b in n.bases:
                if last_name(b) in ("MetaPathFinder", "Loader", "ModuleType"):
                    yield n.lineno, f"class {n.name}({last_name(b)})", src_line(fs.lines, n.lineno)


MOCK_MODULES = {"unittest.mock", "mock", "pytest_mock"}
MOCK_NAMES = {"MagicMock", "Mock", "create_autospec", "AsyncMock", "NonCallableMock"}


def r_s06(fs: FileScan):
    if fs.kind != "pipeline":
        return
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name in MOCK_MODULES or a.name.split(".")[0] in ("mock", "pytest_mock"):
                    yield n.lineno, f"import {a.name}", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.ImportFrom) and n.module:
            mod = n.module
            names = {a.name for a in n.names}
            if (mod in MOCK_MODULES or mod.split(".")[0] in ("mock", "pytest_mock")
                    or (mod == "unittest" and "mock" in names) or names & MOCK_NAMES
                    or ("patch" in names and "mock" in mod)):
                yield n.lineno, f"from {mod} import {', '.join(sorted(names))}", src_line(fs.lines, n.lineno)


def r_s07(fs: FileScan):
    for c in fs.calls:
        f = c.func
        has_default = len(c.args) >= 2 or any(k.arg == "default" for k in c.keywords)
        if isinstance(f, ast.Attribute) and f.attr == "get" and c.args and is_str(c.args[0]) and has_default:
            recv = (last_name(f.value) or "").lower()
            dn = dotted(f.value) or ""
            if dn in ("os.environ", "environ"):
                yield c.lineno, f"os.environ.get({c.args[0].value!r}, default)", src_line(fs.lines, c.lineno), "warn"
            elif recv in CONFIG_NAMES:
                yield c.lineno, f"{last_name(f.value)}.get({c.args[0].value!r}, default)", src_line(fs.lines, c.lineno)
        elif isinstance(f, ast.Name) and f.id == "getattr" and len(c.args) >= 3:
            if (last_name(c.args[0]) or "").lower() in CONFIG_NAMES:
                yield c.lineno, "getattr(config, key, default)", src_line(fs.lines, c.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "param" and has_default:
            yield c.lineno, "ctx.param(id, default)", src_line(fs.lines, c.lineno)
        elif (call_name(c) or "") in ("os.getenv", "getenv") and has_default:
            yield c.lineno, "os.getenv(key, default)", src_line(fs.lines, c.lineno), "warn"


def _is_nan(node) -> bool:
    d = dotted(node) or ""
    if d.split(".")[-1] in ("nan", "NaN", "NAN") and (d.count(".") == 1 or d == "nan"):
        return True
    return (isinstance(node, ast.Call) and last_name(node.func) == "float" and node.args and is_str(node.args[0])
            and node.args[0].value.lower() == "nan")


def r_s08(fs: FileScan):
    for c in fs.calls:
        f = c.func
        kw = {k.arg: k.value for k in c.keywords if k.arg}
        nm = fs.canon_call(c) or ""
        if isinstance(f, ast.Attribute) and f.attr == "fillna":
            v = c.args[0] if c.args else kw.get("value")
            if v is not None and is_literal(v):
                yield c.lineno, "fillna(<literal>)", src_line(fs.lines, c.lineno)
        elif nm.endswith("nan_to_num"):
            v = kw.get("nan")
            if v is None or is_literal(v):
                yield c.lineno, "nan_to_num without a declared nan= value", src_line(fs.lines, c.lineno)
        elif isinstance(f, ast.Attribute) and f.attr == "replace" and len(c.args) >= 2:
            if _is_nan(c.args[0]) and is_literal(c.args[1]):
                yield c.lineno, "replace(nan, <literal>)", src_line(fs.lines, c.lineno)
        elif nm in ("numpy.where", "np.where") and len(c.args) >= 2:
            t = c.args[0]
            if isinstance(t, ast.Call) and last_name(t.func) in ("isnan", "isnull", "isna") and is_literal(c.args[1]):
                yield c.lineno, "where(isnan(a), <literal>, a)", src_line(fs.lines, c.lineno)


SEEDERS = {"default_rng", "RandomState", "seed", "SeedSequence", "Generator", "PCG64"}


def _rng_call(fs: FileScan, c: ast.Call) -> bool:
    nm = fs.canon_call(c)
    if not nm:
        return False
    is_rng = nm.startswith(("numpy.random.", "np.random.")) or (nm.startswith("random.") and nm.count(".") == 1)
    is_rng = is_rng or nm == "default_rng"
    if not is_rng:
        return False
    if nm.split(".")[-1] in SEEDERS:
        seed = c.args[0] if c.args else next((k.value for k in c.keywords if k.arg in ("seed", "x")), None)
        if seed is not None and is_param_access(seed):
            return False
    return True


def r_s09(fs: FileScan):
    for c in fs.calls:
        if _rng_call(fs, c):
            yield c.lineno, f"{call_name(c)}()", src_line(fs.lines, c.lineno)


def _exists_call(node) -> bool:
    return isinstance(node, ast.Call) and last_name(node.func) in ("exists", "isfile", "is_file")


def r_s10(fs: FileScan):
    for n in ast.walk(fs.tree):
        if not isinstance(n, ast.If):
            continue
        negated = False
        found = False
        for m in ast.walk(n.test):
            if isinstance(m, ast.UnaryOp) and isinstance(m.op, ast.Not) and _exists_call(m.operand):
                negated = found = True
            elif _exists_call(m):
                found = True
        if not found:
            continue
        branch = n.body if negated else n.orelse
        if not branch or has_raise_or_exit(branch):
            continue
        for m in walk_local(list(branch)):
            if not isinstance(m, ast.Call):
                continue
            nm = fs.canon_call(m) or ""
            ln = last_name(m.func)
            if (ln in DATA_CTORS or (nm.startswith(("numpy.", "np.")) and ln in NP_CTORS) or _rng_call(fs, m)
                    or (nm.endswith("rasterio.open") and _write_mode(m))):
                yield n.lineno, f"missing-input branch builds data with {ln}()", src_line(fs.lines, n.lineno)
                break


def _write_mode(c: ast.Call) -> bool:
    mode = c.args[1] if len(c.args) >= 2 else next((k.value for k in c.keywords if k.arg == "mode"), None)
    return is_str(mode) and mode.value.startswith("w")


# --- S11 ---------------------------------------------------------------------------------------------------------
def _path_candidates(fs: FileScan):
    """(lineno, string, is_first_part) for every string constant that could be a path, incl. folded joins."""
    out = []
    suffixes = set()  # right operands of string concatenation are suffixes, never absolute paths
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Mod)):
            suffixes.add(id(n.right))
        elif isinstance(n, ast.JoinedStr):
            suffixes.update(id(v) for v in n.values[1:])
        elif isinstance(n, ast.FormattedValue) and n.format_spec is not None:
            suffixes.update(id(v) for v in ast.walk(n.format_spec))
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in fs.docstrings:
            if id(n) in suffixes:
                out.append((n.lineno, n.value.lstrip("/\\"), False))
            else:
                out.append((n.lineno, n.value, True))
        elif isinstance(n, ast.Call) and last_name(n.func) in ("join", "Path", "PurePath", "PosixPath",
                                                                 "PureWindowsPath"):
            tail = []
            for a in reversed(n.args):
                if is_str(a):
                    tail.insert(0, a.value)
                else:
                    break
            if len(tail) >= 2:
                out.append((n.lineno, "/".join(tail), False))
        elif isinstance(n, ast.BinOp) and isinstance(n.op, ast.Div):
            parts = []
            cur = n
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                parts.insert(0, cur.right)
                cur = cur.left
            parts.insert(0, cur)
            tail = []
            for p in reversed(parts):
                if is_str(p):
                    tail.insert(0, p.value)
                else:
                    break
            if len(tail) >= 2:
                out.append((n.lineno, "/".join(tail), False))
    return out


def _looks_pathy(s: str) -> bool:
    if not s or len(s) > 300 or any(ch.isspace() for ch in s) or "://" in s:
        return False
    return "/" in s or "\\" in s or bool(PATH_EXT_RE.search(s)) or bool(re.match(r"^\d\d[A-Za-z_]", s))


def resolve_candidates(ctx: Context, s: str, file_dir: Path | None) -> list[Path]:
    s = os.path.expanduser(s.replace("\\", "/"))
    if os.path.isabs(s):
        return [Path(os.path.normpath(s))]
    bases = []
    ps = ctx.protected
    if ps is not None:
        bases.append(ps.root)
    if ctx.migration is not None:
        bases += [ctx.migration, ctx.migration.parent]
    if file_dir is not None:
        bases.append(file_dir)
    return [Path(os.path.normpath(b / s)) for b in bases]


def protected_hit(ctx: Context, s: str, file_dir: Path | None) -> tuple[str, str] | None:
    """(class, resolved legacy-relative path) when `s` resolves into R; listed or existing paths only."""
    ps = ctx.protected
    if ps is None:
        return None
    for cand in resolve_candidates(ctx, s, file_dir):
        try:
            cls = ps.classify(cand)
        except OSError:
            continue
        if cls not in ("protected", "hidden"):
            continue
        rel = ps.legacy_rel(cand)
        listed = rel is not None and (rel in ps.protected or any(rel.startswith(p + "/") for p in ps.protected))
        if listed or cand.exists() or cls == "hidden":
            return cls, (rel if rel is not None else str(cand.name))
    return None


def r_s11(fs: FileScan):
    ctx = fs.ctx
    seen = set()
    file_dir = fs.path.resolve().parent
    basenames = set()
    if ctx.protected is not None:
        basenames = {p.rsplit("/", 1)[-1] for p in ctx.protected.protected if PATH_EXT_RE.search(p)}
    for lineno, s, first in _path_candidates(fs):
        if (lineno, s) in seen:
            continue
        seen.add((lineno, s))
        st = s.strip()
        if first and ABS_PATH_RE.match(st) and len(st) > 1:
            yield lineno, f"absolute path literal {st[:80]!r}", src_line(fs.lines, lineno)
            continue
        if ctx.protected is not None and _looks_pathy(st):
            hit = protected_hit(ctx, st, file_dir)
            if hit:
                yield lineno, f"path resolves into the protected set ({hit[0]}: {hit[1]})", src_line(fs.lines, lineno)
            elif "/" not in st and st in basenames:
                yield (lineno, f"basename of a protected legacy file {st!r}", src_line(fs.lines, lineno), "warn")
    for c in fs.calls:
        if last_name(c.func) in ("walk", "glob", "iglob", "rglob", "listdir", "scandir"):
            subj = list(c.args) + [k.value for k in c.keywords]
            if isinstance(c.func, ast.Attribute):
                subj.append(c.func.value)
            if any(is_param_access(a) for a in subj):
                yield c.lineno, f"{last_name(c.func)}() on a params path", src_line(fs.lines, c.lineno), "warn"


def scan_params_yaml(ctx: Context, path: Path, display: str) -> list[Finding]:
    """S11 on params.yaml: a value resolving into R blocks unless declared as a bridge (08 §4, §5a)."""
    out = []
    text = path.read_text(encoding="utf-8")
    items = rp.yaml_list_items(text, "params") or []
    for it in items:
        for s in rp.value_strings(it, "value"):
            if not _looks_pathy(s):
                continue
            hit = protected_hit(ctx, s, path.parent)
            if not hit:
                continue
            role = rp.item_scalar(it, "role")
            dec = rp.item_scalar(it, "decision")
            st = ctx.step(rp.item_scalar(it, "node"))
            bridge_ok = (role == "bridge" and dec and dec in ctx.decisions_ids and st is not None
                         and (st.get("scope") == "keep_legacy" or st.get("kind") == "bridge"))
            if bridge_ok:
                continue
            out.append(Finding("S11", "block", display, it.start_line + 1,
                               f"param {it.id} value resolves into the protected set ({hit[0]}: {hit[1]}); "
                               "declare a bridge (role: bridge, decision: D-n, keep_legacy/bridge step) or use an input",
                               s[:160]))
    return out


def scan_tolerances_yaml(path: Path, display: str) -> list[Finding]:
    out = []
    items = rp.yaml_list_items(path.read_text(encoding="utf-8"), "records") or []
    for it in items:
        missing = [k for k in ("correct_spread", "nearest_wrong") if k not in it.fields
                   or rp.item_scalar(it, k) is None and not rp.item_subtree(it, k).strip()]
        if missing:
            out.append(Finding("T01", "warn", display, it.start_line + 1,
                               f"tolerance record {it.id} lacks {', '.join(missing)}: it will be ignored "
                               "(TOLERANCE_UNJUSTIFIED, check runs exact)", ""))
    return out


# --- S12-S18 -----------------------------------------------------------------------------------------------------
def _subject(target: str | None, fs: "FileScan") -> bool:
    """The code under test (local pipeline modules) or a GIS/science library (08 §4 S12)."""
    if not target:
        return False
    target = canonical(target, fs.aliases) or target
    root = target.split(".")[0]
    if root in GEO_LIBS or root in ("pipeline", "axis_pipeline", "<local>") or target.startswith("migration.pipeline"):
        return True
    return bool(STEP_MODULE_RE.match(root)) or root in fs.local_modules


def r_s12(fs: FileScan):
    if fs.kind != "tests":
        return
    for c in fs.calls:
        nm = call_name(c) or ""
        ln = nm.split(".")[-1]
        tgt = None
        if ln in ("patch", "multiple") and (ln == "patch" or nm.endswith("patch.multiple")) and c.args:
            a = c.args[0]
            tgt = a.value if is_str(a) else dotted(a)
        elif nm.endswith("patch.object") and c.args:
            tgt = dotted(c.args[0])
        elif nm.endswith("monkeypatch.setattr") or (ln == "setattr" and "monkeypatch" in nm):
            if c.args:
                a = c.args[0]
                tgt = a.value if is_str(a) else dotted(a)
        if tgt and _subject(tgt, fs):
            yield c.lineno, f"{nm}({tgt}) mocks the subject or a GIS/science library", src_line(fs.lines, c.lineno)


def r_s13(fs: FileScan):
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                parts = a.name.split(".")
                if parts[0] == "analysis" or (parts[0] == "migration" and "analysis" in parts[1:2]):
                    yield n.lineno, f"import {a.name}", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            parts = mod.split(".")
            names = [a.name for a in n.names]
            if parts[0] == "analysis" or (parts[:2] == ["migration", "analysis"]) or (
                    n.level > 0 and (parts[0] == "analysis" or (not mod and "analysis" in names))):
                yield n.lineno, f"from {'.' * n.level}{mod} import ...", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.Call):
            nm = call_name(n) or ""
            if nm.endswith("import_module") and n.args and is_str(n.args[0]) and \
                    n.args[0].value.lstrip(".").split(".")[0] == "analysis":
                yield n.lineno, "import_module('analysis...')", src_line(fs.lines, n.lineno)
            elif nm in ("sys.path.insert", "sys.path.append", "runpy.run_path"):
                for a in n.args:
                    for m in ast.walk(a):
                        if is_str(m) and re.search(r"(^|[/\\])analysis([/\\]|$)", m.value):
                            yield n.lineno, f"{nm}(... analysis ...)", src_line(fs.lines, n.lineno)


def r_s14(fs: FileScan):
    if fs.kind == "pipeline":
        st = fs.ctx.step_for_module(fs.path, fs.tree)
        if st is not None and st.get("scope") == "keep_legacy":
            return
    for n in ast.walk(fs.tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[0] in LEGACY_ENGINES:
                    yield n.lineno, f"import {a.name}", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0 and n.module.split(".")[0] in LEGACY_ENGINES:
            yield n.lineno, f"from {n.module} import ...", src_line(fs.lines, n.lineno)
        elif isinstance(n, ast.Call):
            nm = call_name(n) or ""
            if nm.startswith("subprocess.") or nm in ("os.system", "os.popen", "os.execv", "os.execvp", "run", "Popen",
                                                      "check_call", "check_output", "call"):
                for a in list(n.args) + [k.value for k in n.keywords]:
                    for m in ast.walk(a):
                        if is_str(m) and ("qgis_process" in m.value or "arcpy" in m.value):
                            yield n.lineno, f"{nm or 'call'}() runs a legacy engine", src_line(fs.lines, n.lineno)
                            break
                    else:
                        continue
                    break


def r_s16(fs: FileScan):
    ids = fs.ctx.params_ids
    if ids is None:
        return
    for n in ast.walk(fs.tree):
        key = None
        if isinstance(n, ast.Subscript) and last_name(n.value) == "params":
            sl = n.slice
            if is_str(sl):
                key = sl.value
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "param" and n.args \
                and is_str(n.args[0]):
            key = n.args[0].value
        if key is not None and key not in ids:
            yield n.lineno, f"param id {key!r} not in params.yaml", src_line(fs.lines, n.lineno)


def _is_3857(node) -> bool:
    if isinstance(node, ast.Constant):
        v = node.value
        return v in (3857, 900913) or (isinstance(v, str) and v.strip().upper() in ("EPSG:3857", "EPSG:900913",
                                                                                  "3857"))
    return False


def r_s17(fs: FileScan):
    scopes = [fs.tree] + [n for n in ast.walk(fs.tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for sc in scopes:
        body = sc.body
        to_lines = []
        measure = []
        for m in walk_local(list(body)):
            if isinstance(m, ast.Call) and isinstance(m.func, ast.Attribute) and m.func.attr == "to_crs":
                vals = list(m.args) + [k.value for k in m.keywords if k.arg in ("crs", "epsg")]
                if any(_is_3857(v) for v in vals):
                    to_lines.append(m.lineno)
            elif isinstance(m, ast.Attribute) and m.attr in ("buffer", "distance", "area", "length"):
                measure.append(m.lineno)
        if to_lines:
            first = min(to_lines)
            after = sorted(x for x in measure if x >= first)
            if after:
                yield after[0], "to_crs(EPSG:3857) then a distance/area measure in the same function", \
                    src_line(fs.lines, after[0])


def r_s18(fs: FileScan):
    for c in fs.calls:
        f = c.func
        if isinstance(f, ast.Attribute) and f.attr in ("from_crs", "from_proj") and last_name(f.value) == "Transformer":
            ok = any(k.arg == "always_xy" and isinstance(k.value, ast.Constant) and k.value.value is True
                     for k in c.keywords)
            if not ok:
                yield c.lineno, "Transformer.from_crs without always_xy=True", src_line(fs.lines, c.lineno)


AST_RULES = [("S01", r_s01), ("S02", r_s02), ("S03", r_s03), ("S04", r_s04), ("S05", r_s05), ("S06", r_s06),
             ("S07", r_s07), ("S08", r_s08), ("S09", r_s09), ("S10", r_s10), ("S11", r_s11), ("S12", r_s12),
             ("S13", r_s13), ("S14", r_s14), ("S16", r_s16), ("S17", r_s17), ("S18", r_s18)]


def _fault_injection(rule: str):
    # Test hook for G12 (fail closed): AXIS_MIGRATE_CODESCAN_FAULT=S07 makes rule S07 raise.
    if os.environ.get("AXIS_MIGRATE_CODESCAN_FAULT") == rule:
        raise RuntimeError(f"injected fault in {rule}")


def scan_python(path: Path, kind: str, ctx: Context, display: str | None = None, local_modules: set | None = None):
    """Returns (findings, rule_errors)."""
    display = display or ctx.rel(path)
    findings: list[Finding] = []
    errors: list[dict] = []
    fs = FileScan(path, kind, ctx, display, local_modules)
    first = next((l for l in fs.lines if l.strip()), "")
    if fs.parse_error is not None:
        ln = getattr(fs.parse_error, "lineno", None) or 1
        findings.append(Finding("S15", "block", display, ln, f"does not parse: {fs.parse_error.__class__.__name__}: "
                                f"{getattr(fs.parse_error, 'msg', str(fs.parse_error))}", src_line(fs.lines, ln)))
        return findings, errors
    if MD_FIRST_RE.match(first) and (first.startswith(("```", "|")) or any(l.startswith("```") for l in fs.lines)):
        findings.append(Finding("S15", "block", display, 1, "file starts like Markdown (heading/table/fence)", first[:120]))
    for rid, fn in AST_RULES:
        try:
            _fault_injection(rid)
            for res in fn(fs) or ():
                line, msg, ev = res[0], res[1], res[2]
                sev = res[3] if len(res) > 3 else RULES[rid][1]
                findings.append(Finding(rid, sev, display, line, msg, ev))
        except Exception as e:  # noqa: BLE001 - fail closed: recorded as rule_error, exit 3 / hook block
            errors.append({"rule": rid, "file": display, "error": f"{e.__class__.__name__}: {e}"})
    return findings, errors


def dedupe(findings: list[Finding]) -> list[Finding]:
    seen = set()
    out = []
    for f in sorted(findings, key=lambda f: (f.file, f.line, f.rule, f.msg)):
        k = (f.file, f.line, f.rule, f.msg)
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def scan_file(path: Path, ctx: Context, kind: str | None = None):
    kind = kind or ctx.kind_of(path) or infer_kind(path)
    display = ctx.rel(path)
    if kind == "params":
        try:
            _fault_injection("S11")
            return scan_params_yaml(ctx, path, display), []
        except Exception as e:  # noqa: BLE001
            return [], [{"rule": "S11", "file": display, "error": f"{e.__class__.__name__}: {e}"}]
    if kind == "tolerances":
        try:
            return scan_tolerances_yaml(path, display), []
        except Exception as e:  # noqa: BLE001
            return [], [{"rule": "T01", "file": display, "error": f"{e.__class__.__name__}: {e}"}]
    return scan_python(path, kind, ctx, display)


def infer_kind(path: Path) -> str:
    if path.name == "params.yaml":
        return "params"
    if path.name == "tolerances.yaml":
        return "tolerances"
    parts = path.parts
    if path.name.startswith("test_") or path.name.endswith("_test.py") or "tests" in parts or path.name == "conftest.py":
        return "tests"
    return "pipeline"


def _vendored_identical(path: Path) -> bool:
    if path.name not in ("_axis_runtime.py", "__main__.py"):
        return False
    src = HERE.parents[0] / "migrate" / "runtime" / path.name
    try:
        return src.is_file() and ac.sha256_file(src) == ac.sha256_file(path)
    except OSError:
        return False


def migration_files(mig: Path) -> list[Path]:
    out = []
    for sub in ("pipeline", "tests"):
        d = mig / sub
        if d.is_dir():
            for p in sorted(d.rglob("*.py")):
                if "__pycache__" in p.parts or _vendored_identical(p):
                    continue
                out.append(p)
    for name in ("params.yaml", "tolerances.yaml"):
        if (mig / name).is_file():
            out.append(mig / name)
    return out


def report(findings, errors, files, ctx: Context) -> dict:
    findings = dedupe(findings)
    nb = sum(1 for f in findings if f.sev == "block")
    nw = sum(1 for f in findings if f.sev == "warn")
    status = "rule_error" if errors else ("block" if nb else "clean")
    files_out = []
    for p in files:
        try:
            files_out.append({"file": ctx.rel(p), "sha256": ac.sha256_file(p)})
        except OSError:
            files_out.append({"file": ctx.rel(p), "sha256": None})
    return {
        "schema": SCHEMA,
        "status": status,
        "counts": {"block": nb, "warn": nw, "rule_error": len(errors), "files": len(files)},
        "files": sorted(files_out, key=lambda x: x["file"]),
        "findings": [asdict(f) for f in findings],
        "rule_errors": sorted(errors, key=lambda e: (e["file"], e["rule"])),
        "rules": {k: v[1] for k, v in sorted(RULES.items())},
        "scanner_sha256": ac.sha256_file(__file__),
    }


def exit_code(rep: dict) -> int:
    return 3 if rep["status"] == "rule_error" else (2 if rep["status"] == "block" else 0)


def ascii(s: str) -> str:
    return s.encode("ascii", "replace").decode("ascii")


def human(rep: dict) -> str:
    lines = [f"codescan: {rep['status']} ({rep['counts']['block']} block, {rep['counts']['warn']} warn, "
             f"{rep['counts']['rule_error']} rule errors, {rep['counts']['files']} files)"]
    for f in rep["findings"]:
        lines.append(f"  {f['sev']:5} {f['rule']} {f['file']}:{f['line']}  {f['msg']}")
    for e in rep["rule_errors"]:
        lines.append(f"  ERROR {e['rule']} {e['file']}: {e['error']}")
    return ascii("\n".join(lines))


# ---------------------------------------------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------------------------------------------
def run_hook(stdin_text: str) -> int:
    """PostToolUse (H6). JSON on stdout only. Fail closed: any exception -> exit 2 with an ASCII reason."""
    data = json.loads(stdin_text)
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path")
    cwd = data.get("cwd") or os.getcwd()
    if not fp:
        return 0
    path = Path(fp) if os.path.isabs(fp) else Path(cwd) / fp
    mig = ac.find_migration_dir(path.parent) if path.parent.exists() else None
    mig = mig or ac.find_migration_dir(cwd)
    if mig is None:
        return 0
    ctx = Context.load(mig)
    kind = ctx.kind_of(path)
    if kind is None or not path.is_file():
        return 0
    findings, errors = scan_file(path, ctx, kind)
    findings = dedupe(findings)
    blocks = [f for f in findings if f.sev == "block"]
    warns = [f for f in findings if f.sev == "warn"]
    rel = ctx.rel(path)
    out: dict = {}
    if errors or blocks:
        msg = [f"axis-migrate codescan blocked {rel} (fix the file; there is no inline suppression):"]
        for f in blocks[:20]:
            msg.append(f"- {f.rule} {RULES[f.rule][0]} line {f.line}: {f.msg}. Fix: {RULES[f.rule][2]}.")
        for e in errors:
            msg.append(f"- rule {e['rule']} raised ({e['error']}); scanner fails closed, report this to the user.")
        if len(blocks) > 20:
            msg.append(f"- ... {len(blocks) - 20} more block findings")
        out["decision"] = "block"
        out["reason"] = ascii("\n".join(msg))
    if warns:
        w = [f"axis-migrate codescan warnings for {rel}:"]
        w += [f"- {f.rule} {RULES[f.rule][0]} line {f.line}: {f.msg}. {RULES[f.rule][2]}." for f in warns[:20]]
        out["hookSpecificOutput"] = {"hookEventName": "PostToolUse", "additionalContext": ascii("\n".join(w))}
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=True) + "\n")
    return 0


def run_corpus(dirs: list[str]) -> dict:
    ctx = Context()
    per_rule: dict = {r: {"files": set(), "findings": 0} for r in RULES}
    errors = []
    nfiles = {"pipeline": 0, "tests": 0}
    for d in dirs:
        root = Path(d)
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        local = {p.stem for p in files if infer_kind(p) == "pipeline"}
        local |= {q.name for p in files for q in p.parents if root in q.parents}
        for p in files:
            kind = infer_kind(p)
            nfiles[kind] = nfiles.get(kind, 0) + 1
            fnd, err = scan_python(p, kind, ctx, str(p), local_modules=local)
            errors += err
            for f in dedupe(fnd):
                key = f.rule if f.sev == RULES[f.rule][1] else f"{f.rule}:{f.sev}"
                per_rule.setdefault(key, {"files": set(), "findings": 0})
                per_rule[key]["files"].add((kind, str(p)))
                per_rule[key]["findings"] += 1
    out = {"files": nfiles, "rule_errors": errors, "rules": {}}
    for r, v in sorted(per_rule.items()):
        out["rules"][r] = {
            "files_pipeline": sum(1 for k, _ in v["files"] if k == "pipeline"),
            "files_tests": sum(1 for k, _ in v["files"] if k == "tests"),
            "findings": v["findings"],
        }
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="axis-migrate code scanner (rules S01-S18, 08 §4).")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--hook", action="store_true", help="PostToolUse hook mode (JSON on stdin)")
    mode.add_argument("--all", metavar="MIGRATION", help="scan pipeline/**, tests/**, params.yaml, tolerances.yaml")
    mode.add_argument("--file", nargs="+", metavar="F", help="scan these files")
    mode.add_argument("--corpus", nargs="+", metavar="DIR", help="per-rule file counts over directories")
    ap.add_argument("--migration", help="migration dir for --file (enables S11 protected set, S14 scope, S16 ids)")
    ap.add_argument("--as", dest="kind", choices=["pipeline", "tests", "params", "tolerances"])
    ap.add_argument("--out", help="write the JSON report here (e.g. runs/<id>/verify/scan.json)")
    ap.add_argument("--json", action="store_true", help="print JSON on stdout")
    a = ap.parse_args(argv)

    if a.hook:
        try:
            return run_hook(sys.stdin.read())
        except Exception as e:  # noqa: BLE001 - fail closed
            sys.stderr.write(ascii(f"axis-migrate codescan hook failed closed: {e.__class__.__name__}: {e}\n"))
            return 2
    if a.corpus:
        res = run_corpus(a.corpus)
        print(json.dumps(res, sort_keys=True, indent=1))
        return 3 if res["rule_errors"] else 0
    if a.all:
        mig = Path(a.all).resolve()
        if not (mig / "graph.json").is_file():
            print(f"codescan: no graph.json in {mig}", file=sys.stderr)
            return 3
        ctx = Context.load(mig)
        files = migration_files(mig)
    else:
        mig = ac.find_migration_dir(Path(a.file[0]).resolve().parent) if not a.migration else Path(a.migration)
        ctx = Context.load(mig) if mig else Context()
        files = [Path(f) for f in a.file]
    findings, errors = [], []
    for p in files:
        try:
            fnd, err = scan_file(p, ctx, a.kind if a.file else None)
        except Exception as e:  # noqa: BLE001 - unreadable file etc.
            fnd, err = [], [{"rule": "*", "file": ctx.rel(p), "error": f"{e.__class__.__name__}: {e}"}]
        findings += fnd
        errors += err
    rep = report(findings, errors, files, ctx)
    if a.out:
        ac.dump_json(a.out, rep)
    if a.json:
        print(json.dumps(rep, sort_keys=True, indent=1))
    else:
        print(human(rep))
    return exit_code(rep)


if __name__ == "__main__":
    sys.exit(main())
