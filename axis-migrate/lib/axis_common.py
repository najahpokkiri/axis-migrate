"""axis_common: shared, stdlib-only helpers for every axis-migrate script and hook.

One owner per fact: vocabularies, canonical JSON (axis-cjson/1), hashing and path discovery live here and nowhere
else. Hooks import this with `python3 -I`, so nothing here may import a third-party package.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Iterable

PLUGIN_VERSION = "0.4.0"

# ---------------------------------------------------------------------------------------------------------------
# Vocabularies (DESIGN.md §3). Order matters: precedence worst first.
# ---------------------------------------------------------------------------------------------------------------
VERDICTS = [
    "FAIL",
    "EVALUATOR_ERROR",
    "BLOCKED",
    "NEEDS_DECISION",
    "NEEDS_REVIEW",
    "NOT_COMPARED",
    "PASS_WITHIN_TOLERANCE",
    "PASS_EXACT",
]
PASS_STATES = {"PASS_EXACT", "PASS_WITHIN_TOLERANCE"}
CHECK_STATES = {"PASS_EXACT", "PASS_WITHIN_TOLERANCE", "FAIL", "NOT_COMPARED", "EVALUATOR_ERROR"}

RUNGS = ["none", "generated", "sandbox-validated", "reference-verified", "target-ready"]
GREYED_RUNGS = ["deployed", "live-target-verified", "customer-accepted"]  # never emitted

DISPOSITIONS = [
    "VERIFIED", "ACCEPTED_DIFFERENCE", "NEEDS_REVIEW", "NEEDS_DECISION", "FAILED", "BLOCKED", "EVALUATOR_ERROR",
    "NO_REFERENCE", "NOT_COMPARED", "EVIDENCE_INCOMPLETE", "BRIDGED", "NOT_PORTED",
]
RUN_STATUS = ["succeeded", "failed", "timed_out", "cancelled", "blocked"]
EVIDENCE_STATES = [
    "directly_observed", "structurally_inferred", "ai_inferred", "documented", "user_asserted", "runtime_probed",
    "verified", "rejected", "superseded",
]
STEP_SCOPES = ["migrate", "keep_legacy", "drop"]
STEP_KINDS = ["script", "notebook", "qgis_model", "manual", "bridge"]
VISUAL_CATEGORIES = [
    "whole_offset", "boundary_stripes", "missing_region", "uniform_scale", "smoothed_blurred", "angular_outline",
    "shrunken_features", "class_missing", "holes", "none",
]

# Runner exit codes (DESIGN §3)
EXIT_RUN = {"succeeded": 0, "step_failed": 10, "contract": 11, "input_missing": 12, "environment": 13,
            "timed_out": 14, "immutability": 15, "harness": 70}
# Step process exit codes (axis_boot)
EXIT_STEP = {"ok": 0, "exception": 1, "input_param_contract": 2, "output_contract": 3, "stub": 11}

SIDECAR_SUFFIXES = (".aux.xml", ".ovr", ".tfw", ".qix", ".shx", ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".msk",
                    ".lock", "-wal", "-shm", "-journal")


def worst(states: Iterable[str]) -> str | None:
    """Worst verdict under the DESIGN §3 precedence; None for an empty input (empty is never a pass)."""
    best_idx = None
    for s in states:
        if s not in VERDICTS:
            raise ValueError(f"unknown verdict state {s!r}")
        i = VERDICTS.index(s)
        best_idx = i if best_idx is None else min(best_idx, i)
    return None if best_idx is None else VERDICTS[best_idx]


def rung_min(rungs: Iterable[str]) -> str:
    vals = [RUNGS.index(r) for r in rungs]
    return RUNGS[min(vals)] if vals else "none"


# ---------------------------------------------------------------------------------------------------------------
# axis-cjson/1 and content-addressed ids (07 §3.2)
# ---------------------------------------------------------------------------------------------------------------
VOLATILE_KEYS = {"created_at", "started_at", "ended_at", "wall_time_s", "hostname", "pid"}


class CJSONError(ValueError):
    pass


def _check_finite(obj: Any, path: str = "$") -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise CJSONError(f"non-finite number at {path}; write null plus a reason code")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if not isinstance(k, str):
                raise CJSONError(f"non-string key at {path}")
            _check_finite(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_finite(v, f"{path}[{i}]")


def cjson(obj: Any) -> bytes:
    """axis-cjson/1: keys sorted by code point, compact separators, UTF-8, NaN/Inf refused."""
    _check_finite(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def strip_volatile(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: strip_volatile(v) for k, v in obj.items() if k not in VOLATILE_KEYS}
    if isinstance(obj, list):
        return [strip_volatile(v) for v in obj]
    return obj


def object_id(kind: str, obj: Any) -> str:
    return f"{kind}:" + hashlib.sha256(cjson(strip_volatile(obj))).hexdigest()


def dump_json(path: str | os.PathLike, obj: Any, *, pretty: bool = True) -> None:
    """Atomic JSON write; refuses NaN. Pretty output is still deterministic (sorted keys)."""
    _check_finite(obj)
    text = json.dumps(obj, sort_keys=True, indent=1 if pretty else None, ensure_ascii=False, allow_nan=False)
    atomic_write_text(path, text + "\n")


def load_json(path: str | os.PathLike) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------------------------------------------
def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ForeignInputError(ValueError):
    """An input file (--json, --ops, --spec) that another account owns or anyone may write (B7)."""


def read_own_text(path: str | os.PathLike, what: str = "input") -> str:
    """Text of an input file the agent named on a command line, only when this user owns it and it is not
    world-writable. A stale file of another project in a shared folder (/tmp/q05.json, /tmp/axf/mf1.json from an
    eval account) has the same name as the one the agent meant to write; reading it would record foreign content.
    Checked on the open descriptor. Pass '-' and stdin instead."""
    with open(path, "r", encoding="utf-8") as fh:
        st = os.fstat(fh.fileno())
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise ForeignInputError(f"{what} {path} is owned by uid {st.st_uid}, not by you (uid {os.getuid()}): "
                                    "refused (a stale file of another project?). Pass it on stdin with '-' (a quoted "
                                    "heredoc), or write it under migration/analysis/")
        if st.st_mode & 0o002:
            raise ForeignInputError(f"{what} {path} is world-writable (mode {st.st_mode & 0o777:o}): refused. Pass "
                                    "it on stdin with '-', or write it under migration/analysis/")
        return fh.read()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_entries(root: str | os.PathLike, *, exclude_dirs=("__pycache__", ".pytest_cache", ".git", ".venv"),
                 exclude_suffixes=(".pyc", ".aux.xml")) -> list[dict]:
    root = Path(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude_dirs)
        for fn in sorted(filenames):
            if fn.endswith(exclude_suffixes):
                continue
            p = Path(dirpath) / fn
            rel = p.relative_to(root).as_posix()
            out.append({"path": rel, "sha256": sha256_file(p), "size": p.stat().st_size})
    out.sort(key=lambda e: e["path"])
    return out


def tree_hash(root: str | os.PathLike, **kw) -> str:
    """July tree-hash form: sha256(cjson(sorted [{path, sha256, size}]))."""
    return "sha256:" + sha256_bytes(cjson(tree_entries(root, **kw)))


def gpkg_content_sha256(path: str | os.PathLike) -> str:
    """Content hash of a GeoPackage: every table's rows sorted, excluding gpkg_contents.last_change.

    Opened read-only and immutable, so hashing can never bump the SQLite header of a legacy file.
    """
    uri = "file:" + str(Path(path).resolve()) + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        h = hashlib.sha256()
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for t in tables:
            cols = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]
            if t == "gpkg_contents":
                cols = [c for c in cols if c != "last_change"]
            if t.startswith("rtree_"):
                continue  # spatial index shadow tables: derived, order/implementation dependent
            h.update(f"T:{t}:{','.join(cols)}\n".encode())
            q = "SELECT " + ",".join(f'"{c}"' for c in cols) + f' FROM "{t}"'
            rows = [repr(tuple(r)).encode() for r in con.execute(q)]
            for r in sorted(rows):
                h.update(r + b"\n")
        return h.hexdigest()
    finally:
        con.close()


def xlsx_content_sha256(path: str | os.PathLike) -> str:
    """Content hash of an XLSX: per sheet (workbook order, by name) every non-empty cell's reference, value and
    formula, with shared strings resolved and numbers normalised. Styles, docProps (timestamps) and the XML
    serialisation (lxml vs ElementTree writers) do not enter the hash."""
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    h = hashlib.sha256()
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(f"{rel_ns}Relationship"):
                tgt = r.get("Target", "")
                tgt = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
                rels[r.get("Id")] = tgt
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        for sh in wb.findall("m:sheets/m:sheet", ns):
            name = sh.get("name")
            target = rels.get(sh.get(f"{{{ns['r']}}}id"))
            h.update(b"S\0" + name.encode() + b"\n")
            if not target or target not in names:
                continue
            cells = []
            for c in ET.fromstring(z.read(target)).iter(f"{{{ns['m']}}}c"):
                typ = c.get("t") or "n"
                v = c.find("m:v", ns)
                f = c.find("m:f", ns)
                val = v.text if v is not None else None
                if typ == "s" and val is not None:
                    val = shared[int(val)]
                elif typ == "inlineStr":
                    val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t"))
                elif typ == "n" and val is not None:
                    try:
                        val = repr(float(val))
                    except ValueError:
                        pass
                ftxt = f.text if f is not None else None
                if val is None and ftxt is None:
                    continue
                cells.append(f"{c.get('r')}|{typ if typ != 'str' else 's'}|{val}|{ftxt}")
            for line in sorted(cells):
                h.update(line.encode() + b"\n")
    return h.hexdigest()


def content_sha256(path: str | os.PathLike) -> str | None:
    p = str(path).lower()
    if p.endswith(".gpkg"):
        return gpkg_content_sha256(path)
    if p.endswith((".xlsx", ".xlsm")):
        return xlsx_content_sha256(path)
    return None


# ---------------------------------------------------------------------------------------------------------------
# Paths and project discovery
# ---------------------------------------------------------------------------------------------------------------
def find_migration_dir(start: str | os.PathLike) -> Path | None:
    """Walk up from `start` to the first directory holding migration/graph.json; return that migration/ dir."""
    p = Path(start).resolve()
    for cand in [p, *p.parents]:
        if (cand / "migration" / "graph.json").is_file():
            return cand / "migration"
        if cand.name == "migration" and (cand / "graph.json").is_file():
            return cand
    return None


def legacy_root(migration_dir: str | os.PathLike, graph: dict | None = None) -> Path:
    migration_dir = Path(migration_dir)
    if graph is None:
        graph = load_json(migration_dir / "graph.json")
    return (migration_dir / graph["project"]["legacy_root"]).resolve()


def plugin_root() -> Path:
    env = os.environ.get("AXIS_MIGRATE_PLUGIN") or os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str) -> str:
    return SLUG_RE.sub("_", text.lower()).strip("_")


def is_sidecar(path: str) -> bool:
    return path.lower().endswith(SIDECAR_SUFFIXES)


def utc_now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------------
# Graph contract view: drift checks must ignore projections written by graph.py derive / ingest-verify
# ---------------------------------------------------------------------------------------------------------------
GRAPH_VOLATILE_TOP = {"revision", "updated", "acks", "ladder", "runs"}
GRAPH_VOLATILE_STEP = {"ladder", "verify", "provenance"}
GRAPH_VOLATILE_DATASET = {"verify", "provenance"}


def graph_contract_view(graph: dict) -> dict:
    """The graph minus projections (ladder, verify, revision counters, provenance, code_sha256 written by derive).

    Two graphs with equal contract views describe the same workflow, scope, pairing, params refs, plans, questions and
    compare settings; projections (rungs, verdicts, blame) never count as a change of the graph after a run.
    """
    g = {k: v for k, v in graph.items() if k not in GRAPH_VOLATILE_TOP}
    steps = []
    for s in graph.get("steps") or []:
        s2 = {k: v for k, v in s.items() if k not in GRAPH_VOLATILE_STEP}
        if isinstance(s2.get("new"), dict):
            s2["new"] = {k: v for k, v in s2["new"].items() if k != "code_sha256"}
        steps.append(s2)
    g["steps"] = steps
    g["datasets"] = [{k: v for k, v in d.items() if k not in GRAPH_VOLATILE_DATASET}
                     for d in graph.get("datasets") or []]
    return g


def graph_contract_sha256(graph_or_path) -> str:
    graph = graph_or_path if isinstance(graph_or_path, dict) else load_json(graph_or_path)
    return sha256_bytes(cjson(graph_contract_view(graph)))


# ---------------------------------------------------------------------------------------------------------------
# The one pinned environment: location resolver (shared by scripts/axpy, the runner, doctor, dry runs and tests)
# ---------------------------------------------------------------------------------------------------------------
def env_project_dir() -> Path:
    """skills/run/env: pyproject.toml + uv.lock (the lock is the identity of the environment)."""
    return plugin_root() / "skills" / "run" / "env"


def env_lock_sha256() -> str:
    return sha256_file(env_project_dir() / "uv.lock")


def env_venv_dir() -> Path:
    """The synced venv lives OUTSIDE the plugin folder, keyed by the lock:
    ${CLAUDE_PLUGIN_DATA}/env-<lock8> when set, else ${XDG_CACHE_HOME:-~/.cache}/axis-migrate/env-<lock8>.
    Must stay identical to scripts/axpy's shell logic."""
    return env_venv_rule()[0]


def env_venv_rule() -> tuple[Path, str]:
    """(venv dir, which rule chose it). Claude Code exports CLAUDE_PLUGIN_DATA to hooks only, never to the Bash
    tool, so from the agent's shell the second or third rule applies."""
    lock8 = env_lock_sha256()[:8]
    data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if data:
        return Path(data) / f"env-{lock8}", "CLAUDE_PLUGIN_DATA set in this shell"
    if os.environ.get("XDG_CACHE_HOME"):
        return (Path(os.environ["XDG_CACHE_HOME"]) / "axis-migrate" / f"env-{lock8}",
                "CLAUDE_PLUGIN_DATA unset in this shell, XDG_CACHE_HOME set → $XDG_CACHE_HOME/axis-migrate")
    return (Path.home() / ".cache" / "axis-migrate" / f"env-{lock8}",
            "CLAUDE_PLUGIN_DATA unset in this shell → ~/.cache/axis-migrate")


def env_python() -> Path:
    return env_venv_dir() / "bin" / "python"


def env_lock_marker() -> Path:
    """Written by scripts/axpy after a successful `uv sync --frozen`: holds the lock sha the venv was synced from."""
    return env_venv_dir() / ".axis-lock-sha256"


# ---------------------------------------------------------------------------------------------------------------
# Per-install settings outside the plugin: secrets.env (E2B key) and config.json (sandbox choice)
# ---------------------------------------------------------------------------------------------------------------
E2B_TEAM_SLUG = "abdulnajahp"          # E2B team slug that publishes the public template (Najah's team, 15 Oct)
E2B_TEMPLATE_PREFIX = "axis-geo-migrate"


def e2b_template_name(lock_sha256: str | None = None, slug: str | None = E2B_TEAM_SLUG) -> str:
    """`<slug>/axis-geo-migrate-<lock8>` (public, usable from any E2B team); slug=None gives the bare name."""
    lock8 = (lock_sha256 or env_lock_sha256())[:8]
    return (f"{slug}/" if slug else "") + f"{E2B_TEMPLATE_PREFIX}-{lock8}"


def plugin_data_dirs(environ=None) -> list[Path]:
    """Every folder that may hold this install's secrets.env / config.json, in lookup order:
    $PLUGIN_DATA (Codex hooks), $CLAUDE_PLUGIN_DATA (Claude hooks), the harness data folders found on disk
    (${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/data/axis-migrate-*, ${CODEX_HOME:-~/.codex}/plugins/data/axis-migrate-*:
    the agent's shell has neither variable), then ~/.config/axis-migrate (person-run `axpy --configure`, dev)."""
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home())
    dirs = [env.get("PLUGIN_DATA"), env.get("CLAUDE_PLUGIN_DATA")]
    for root in (Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude"), Path(env.get("CODEX_HOME") or home / ".codex")):
        data = root / "plugins" / "data"
        dirs += sorted(str(d) for d in data.glob("axis-migrate-*")) if data.is_dir() else []
    dirs.append(str(home / ".config" / "axis-migrate"))
    out: list[Path] = []
    for d in dirs:
        if d and Path(d) not in out:
            out.append(Path(d))
    return out


def user_config_dir(environ=None) -> Path:
    """Where person-run commands write: ${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.config/axis-migrate}}."""
    env = os.environ if environ is None else environ
    home = Path(env.get("HOME") or Path.home())
    return Path(env.get("PLUGIN_DATA") or env.get("CLAUDE_PLUGIN_DATA") or home / ".config" / "axis-migrate")
