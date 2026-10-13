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

PLUGIN_VERSION = "0.1.0"

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
    """Content hash of an XLSX: every zip member except docProps/* (timestamps), by bytes, sorted by name."""
    h = hashlib.sha256()
    with zipfile.ZipFile(path) as z:
        for name in sorted(z.namelist()):
            if name.startswith("docProps/"):
                continue
            h.update(name.encode() + b"\0" + hashlib.sha256(z.read(name)).digest())
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
