#!/usr/bin/env python3
"""graph.py: the single writer of migration/graph.json (axis-migrate.graph.v1).

Every write goes through `apply(migration_dir, ops, actor, base_revision)`: an exclusive flock on
migration/.graph/lock, a revision check (stale -> ConflictError, CLI exit 3), a per-field ACL (02 §3.5; a field the
user set is locked against the agent -> AclError `user_owned_field`, exit 4), the op handlers, automatic derivation of
steps[].inputs/outputs and datasets[].produced_by/consumed_by/role from the edges, validation (exit 2), an atomic
os.replace, a snapshot .graph/rev/<n>.json and one line per op appended to graph-edits.jsonl.

The core is stdlib only (hooks import `unacked_user_edits` and `hook_summary` with `python3 -I`). PyYAML is optional
and only used to read params.yaml when present; a line parser is the fallback. Op shapes: GRAPH_OPS.md.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import fcntl
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402

SCHEMA = "axis-migrate.graph.v1"
PROTECTED_SCHEMA = "axis-migrate.protected.v1"
DIFF_SCHEMA = "axis-migrate.graph-diff.v1"

ACTORS = ("agent", "user", "verify", "derive")
CLI_ACTORS = ("agent", "verify", "derive")  # the HTTP handler is the only producer of user ops
CHANNELS = ("graph", "interview", "chat", "cli")

STEP_KINDS = list(ac.STEP_KINDS) + ["new"]  # "new" = a step the user added in the editor (02 §3.2)
SCOPES = list(ac.STEP_SCOPES)
DATASET_KINDS = ["raster", "vector", "table", "document"]
ROLES = ["input", "intermediate", "final", "unused"]
PERSISTENCE = ["file", "gdb", "sheet", "overwritten"]
EDGE_KINDS = ["reads", "writes", "reads_grid", "reads_write", "equivalent_to", "displays"]
READ_KINDS = ("reads", "reads_grid")
WRITE_KINDS = ("writes", "reads_write")
FLOW_KINDS = READ_KINDS + WRITE_KINDS
REVIEW_STATES = ["pending", "confirmed", "assumed", "rejected"]
QUESTION_STATUS = ["open", "answered", "assumed", "dismissed"]
QUESTION_KINDS = ["manual_step", "legacy_bug", "branch", "unresolved_value", "fitted_confirm", "confirm_all", "target",
                  "lineage", "scope", "other"]
EVIDENCE_STATES = list(ac.EVIDENCE_STATES)
# Strongest first. `documented` ranks below a fresh answer and above model inference (D2-3).
EVIDENCE_RANK = ["verified", "runtime_probed", "directly_observed", "structurally_inferred", "user_asserted",
                 "documented", "ai_inferred"]
VERDICTS = list(ac.VERDICTS)
STEP_BLAME = ["own", "inherited", "accumulated", "compensating"]
DATASET_BLAME = ["origin", "inherited"]
NO_EXT = {"raster": "tif", "vector": "gpkg", "table": "csv", "document": "txt"}

STEP_ID_RE = re.compile(r"^[smb]\d{2}[a-z]?_[a-z0-9_]+$")
DS_ID_RE = re.compile(r"^ds\.[a-z0-9_]+(?:@[a-z0-9_]+)?$")
Q_ID_RE = re.compile(r"^q\d{2,}$")
EDGE_ID_RE = re.compile(r"^e\.[A-Za-z0-9_.@>#:\-]+$")
PARAM_ID_RE = re.compile(r"^p\.[a-z0-9_.]+$")
FILE_LINE_RE = re.compile(r"^[^:\s][^:]*:\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$")
EXTERNAL_RE = re.compile(r"^(project|qgz|external):.+$")

# Fields a user setting locks against the agent (D2-10).
LOCKABLE = ("title", "review", "scope", "notes", "role")

EXIT = {"ok": 0, "invalid": 2, "conflict": 3, "acl": 4}


# ---------------------------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------------------------
class GraphError(Exception):
    exit_code = 2
    http_status = 400
    code = "invalid"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details

    def to_json(self) -> dict:
        out = {"error": self.code, "message": self.message}
        if self.details is not None:
            out["details"] = self.details
        return out


class ValidationError(GraphError):
    exit_code = 2
    http_status = 400
    code = "invalid"


class ConflictError(GraphError):
    exit_code = 3
    http_status = 409
    code = "conflict"

    def __init__(self, message: str, *, revision: int, conflicting_seqs: list[int]):
        super().__init__(message, details={"revision": revision, "conflicting_seqs": conflicting_seqs})
        self.revision = revision
        self.conflicting_seqs = conflicting_seqs

    def to_json(self) -> dict:
        return {"error": "conflict", "message": self.message, "revision": self.revision,
                "conflicting_seqs": self.conflicting_seqs}


class IntegrityError(GraphError):
    """graph.json or graph-edits.jsonl changed outside graph.py (script half of the H3 graph guard)."""
    exit_code = 2
    http_status = 423  # not 409: the editor must not offer a rebase on top of an out-of-band edit
    code = "GRAPH_EDITED_OUTSIDE_APPLY"


class AclError(GraphError):
    exit_code = 4
    http_status = 403
    code = "acl"


# ---------------------------------------------------------------------------------------------------------------
# Paths and file access
# ---------------------------------------------------------------------------------------------------------------
def paths(mdir: str | os.PathLike) -> dict[str, Path]:
    m = Path(mdir)
    return {"graph": m / "graph.json", "edits": m / "graph-edits.jsonl", "gdir": m / ".graph",
            "head": m / ".graph" / "head.json",
            "lock": m / ".graph" / "lock", "rev": m / ".graph" / "rev", "layout": m / ".graph" / "layout.json",
            "plan": m / "plan.md", "protected": m / ".axis" / "protected.json", "params": m / "params.yaml",
            "inventory": m / "inventory.json"}


@contextlib.contextmanager
def locked(mdir: str | os.PathLike, shared: bool = False):
    p = paths(mdir)
    p["gdir"].mkdir(parents=True, exist_ok=True)
    fd = os.open(p["lock"], os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


HEAD_SCHEMA = "axis-migrate.graph-head.v1"


def _record_head(mdir: str | os.PathLike, revision: int) -> None:
    """Called under the lock after every successful write: the exact bytes graph.py left behind."""
    p = paths(mdir)
    gbytes = p["graph"].read_bytes()
    ebytes = p["edits"].read_bytes() if p["edits"].is_file() else b""
    ac.dump_json(p["head"], {"schema": HEAD_SCHEMA, "revision": revision, "graph_sha256": ac.sha256_bytes(gbytes),
                             "edits_size": len(ebytes), "edits_sha256": ac.sha256_bytes(ebytes),
                             "edits_lines": ebytes.count(b"\n")})


def integrity_problems(mdir: str | os.PathLike) -> list[str]:
    """Differences between the live graph.json / graph-edits.jsonl and the last write recorded by graph.py.

    The edit log must equal what was recorded: only graph.py appends to it, under the same lock as the record,
    so an unrecorded tail is an out-of-band append (or a crash mid-write; either way it needs a human look).
    """
    p = paths(mdir)
    if not p["graph"].is_file():
        return []
    if not p["head"].is_file():
        return ["no write record (.graph/head.json): graph.json was not written by graph.py"]
    try:
        head = ac.load_json(p["head"])
        want_g, want_size, want_e = head["graph_sha256"], int(head["edits_size"]), head["edits_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [f"write record unreadable: {exc}"]
    out = []
    if ac.sha256_bytes(p["graph"].read_bytes()) != want_g:
        out.append(f"graph.json differs from the bytes graph.py wrote at revision {head.get('revision')}")
    ebytes = p["edits"].read_bytes() if p["edits"].is_file() else b""
    if len(ebytes) < want_size or ac.sha256_bytes(ebytes[:want_size]) != want_e:
        out.append("graph-edits.jsonl was rewritten or truncated (the recorded log is not a prefix of it)")
    elif len(ebytes) > want_size:
        out.append(f"graph-edits.jsonl has {len(ebytes) - want_size} byte(s) appended outside graph.py")
    return out


def check_integrity(mdir: str | os.PathLike) -> dict:
    with locked(mdir, shared=True):
        probs = integrity_problems(mdir)
    return {"ok": not probs, "code": None if not probs else IntegrityError.code, "problems": probs}


def require_intact(mdir: str | os.PathLike, *, lock: bool = True) -> None:
    if lock:
        with locked(mdir, shared=True):
            probs = integrity_problems(mdir)
    else:
        probs = integrity_problems(mdir)
    if probs:
        raise IntegrityError("graph edited outside graph.py apply: " + "; ".join(probs) +
                             ". Restore it from .graph/rev/<n>.json (or ask the user); nothing was applied.",
                             details=probs)


def load_graph(mdir: str | os.PathLike) -> dict:
    p = paths(mdir)["graph"]
    if not p.is_file():
        raise ValidationError(f"no graph at {p}; run graph.py init first", code="no_graph")
    return ac.load_json(p)


def read_edits(mdir: str | os.PathLike) -> list[dict]:
    p = paths(mdir)["edits"]
    if not p.is_file():
        return []
    out = []
    with open(p, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValidationError(f"graph-edits.jsonl line {n} is not JSON: {exc}", code="edits_corrupt")
    return out


def _tail_edits(mdir: str | os.PathLike, stop) -> list[dict]:
    """Records from the end of the jsonl backwards while `stop(record)` is False (fast path for hooks)."""
    p = paths(mdir)["edits"]
    if not p.is_file():
        return []
    data = p.read_bytes()
    out = []
    for raw in reversed(data.split(b"\n")):
        raw = raw.strip()
        if not raw:
            continue
        rec = json.loads(raw.decode("utf-8"))
        if stop(rec):
            break
        out.append(rec)
    out.reverse()
    return out


def _last_seq(mdir: str | os.PathLike) -> int:
    p = paths(mdir)["edits"]
    if not p.is_file():
        return 0
    with open(p, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        block = min(size, 1 << 16)
        while True:
            fh.seek(size - block)
            chunk = fh.read(block)
            lines = [ln for ln in chunk.split(b"\n") if ln.strip()]
            if len(lines) >= 2 or block == size:
                break
            block = min(size, block * 4)
    if not lines:
        return 0
    return int(json.loads(lines[-1].decode("utf-8"))["seq"])


def _line(rec: dict) -> str:
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _append_edits(mdir: str | os.PathLike, records: list[dict]) -> None:
    if not records:
        return
    p = paths(mdir)["edits"]
    data = "".join(_line(r) + "\n" for r in records).encode("utf-8")
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_graph(mdir: str | os.PathLike, graph: dict) -> None:
    p = paths(mdir)
    ac.dump_json(p["rev"] / f"{graph['revision']}.json", graph)
    ac.dump_json(p["graph"], graph)


# ---------------------------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------------------------
def _now() -> str:
    return os.environ.get("AXIS_MIGRATE_NOW") or ac.utc_now_iso()


def _index(graph: dict) -> dict[str, tuple[str, dict]]:
    idx: dict[str, tuple[str, dict]] = {}
    for kind, key in (("step", "steps"), ("dataset", "datasets"), ("edge", "edges"), ("question", "questions")):
        for obj in graph.get(key, []):
            idx[obj["id"]] = (kind, obj)
    return idx


def _get(graph: dict, node_id: str, kinds: Iterable[str] | None = None) -> tuple[str, dict]:
    hit = _index(graph).get(node_id)
    if hit is None:
        raise ValidationError(f"unknown id {node_id!r}", code="unknown_id")
    if kinds is not None and hit[0] not in kinds:
        raise ValidationError(f"{node_id!r} is a {hit[0]}, expected {'/'.join(kinds)}", code="wrong_kind")
    return hit


def _need(op: dict, *keys: str) -> None:
    missing = [k for k in keys if op.get(k) in (None, "", [])]
    if missing:
        raise ValidationError(f"op {op.get('type')} needs {', '.join(missing)}", code="missing_field")


def _allowed_keys(op: dict, allowed: Iterable[str]) -> None:
    extra = sorted(set(op) - set(allowed) - {"type", "question", "channel", "verbatim", "comment"})
    if extra:
        raise ValidationError(f"op {op.get('type')}: unknown keys {extra}", code="unknown_key")


def _strongest(evidence: list[dict]) -> str | None:
    states = [e.get("state") for e in evidence if e.get("state") in EVIDENCE_RANK]
    if not states:
        return None
    return min(states, key=EVIDENCE_RANK.index)


def _edge_state(edge: dict) -> None:
    ev = edge.get("evidence") or []
    if edge.get("review") == "rejected" or edge.get("state") == "rejected":
        edge["state"] = "rejected"
        positive = [e for e in ev if e.get("state") in EVIDENCE_RANK and e.get("state") != "user_asserted"]
        edge["conflict"] = bool(positive)
        return
    s = _strongest(ev)
    if s:
        edge["state"] = s
    has_rej = any(e.get("state") == "rejected" for e in ev)
    edge["conflict"] = bool(has_rej and s)


def _user_owned(obj: dict, field: str) -> bool:
    top = field.split(".")[0]
    for e in (obj.get("provenance") or {}).get("edits", []):
        if e.get("by") == "user" and top in e.get("fields", []):
            return True
    return False


def _record_edit(obj: dict, cx: "Ctx", fields: Iterable[str]) -> None:
    prov = obj.setdefault("provenance", {"created": None, "edits": []})
    edits = prov.setdefault("edits", [])
    fields = sorted(set(fields))
    if edits and edits[-1].get("by") == cx.actor and edits[-1].get("rev") == cx.rev:
        edits[-1]["fields"] = sorted(set(edits[-1]["fields"]) | set(fields))
    else:
        edits.append({"by": cx.actor, "rev": cx.rev, "fields": fields})


def _set_path(obj: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if nxt is None:
            nxt = {}
            cur[p] = nxt
        if not isinstance(nxt, dict):
            raise ValidationError(f"cannot set {path}: {p} is not an object", code="bad_path")
        cur = nxt
    cur[parts[-1]] = copy.deepcopy(value)


def _get_path(obj: dict, path: str) -> Any:
    cur: Any = obj
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _question_nodes(q: dict) -> list[str]:
    nodes = [q.get("node")] if q.get("node") else []
    nodes += [n for n in q.get("nodes") or [] if n not in nodes]
    return nodes


def topo_steps(graph: dict) -> list[str]:
    """Step ids in data-dependency order (ties by id). Cycles are reported by validate, not here."""
    steps = sorted(s["id"] for s in graph.get("steps", []))
    deps: dict[str, set[str]] = {s: set() for s in steps}
    prod = {d["id"]: (d.get("produced_by") or {}).get("step") for d in graph.get("datasets", [])}
    for s in graph.get("steps", []):
        for ds in s.get("inputs", []):
            p = prod.get(ds)
            if p and p != s["id"] and p in deps:
                deps[s["id"]].add(p)
    out: list[str] = []
    done: set[str] = set()
    while len(out) < len(steps):
        ready = [s for s in steps if s not in done and deps[s] <= done]
        if not ready:  # cycle: append the rest by id
            ready = [s for s in steps if s not in done][:1]
        for s in ready[:1]:
            out.append(s)
            done.add(s)
    return out


# ---------------------------------------------------------------------------------------------------------------
# Normalisation (defaults) for nodes from a draft or an add_* op
# ---------------------------------------------------------------------------------------------------------------
def _norm_step(s: dict, by: str, rev: int) -> dict:
    out = {
        "id": s["id"], "title": s.get("title") or s["id"], "title_set_by": s.get("title_set_by", by),
        "kind": s.get("kind", "script"), "tool": s.get("tool"), "description": s.get("description"),
        "scope": s.get("scope", "migrate"), "scope_reason": s.get("scope_reason"),
        "scope_set_by": s.get("scope_set_by", by), "review": s.get("review", "pending"),
        "legacy": s.get("legacy") or {"source": None, "lines": [], "executed": None},
        "ops": s.get("ops", []), "params": s.get("params", []), "inputs": [], "outputs": [],
        "new": s.get("new") or {"module": None, "function": "run", "code_sha256": None},
        "plan": s.get("plan"), "timeout_s": s.get("timeout_s", 120), "ladder": s.get("ladder"),
        "verify": s.get("verify") or {"run_id": None, "verdict": None, "reason_codes": [], "blame": None},
        "evidence": s.get("evidence", []), "notes": s.get("notes", []),
        "requires_confirmation": s.get("requires_confirmation"),
        "provenance": s.get("provenance") or {"created": {"by": by, "rev": rev}, "edits": []},
    }
    for k, v in s.items():  # keep extra lineage fields (e.g. executed_evidence, outputs_untrusted)
        if k not in out and k not in ("inputs", "outputs"):
            out[k] = v
    out["new"].setdefault("function", "run")
    out["new"].setdefault("code_sha256", None)
    out["new"].setdefault("module", None)
    return out


def _norm_dataset(d: dict, by: str, rev: int) -> dict:
    legacy = d.get("legacy")
    if legacy is not None:
        legacy = {"path": None, "layer": None, "sheet": None, "exists": False, "sha256": None,
                  "content_sha256": None, "profile": None, "sidecars": [], **legacy}
    out = {
        "id": d["id"], "title": d.get("title") or d["id"][3:], "title_set_by": d.get("title_set_by", by),
        "kind": d.get("kind"), "role": d.get("role", "intermediate"),
        "role_lineage": d.get("role_lineage", d.get("role", "intermediate")),
        "persistence": d.get("persistence", "file" if legacy else None), "legacy": legacy,
        "new": d.get("new"), "produced_by": None, "consumed_by": [],
        "reference": d.get("reference") or {"source": "legacy" if legacy and legacy.get("exists") else "none",
                                            "match_method": "lineage", "via_dataset": None, "confirmed_by": None},
        "legacy_in_memory": bool(d.get("legacy_in_memory", False)),
        "no_reference_reason": d.get("no_reference_reason"),
        "compare": d.get("compare") or {},
        "verify": d.get("verify") or {"run_id": None, "verdict": None, "reason_codes": [], "report": None,
                                      "report_sha256": None, "plots": [], "visual_flag": None, "blame": None,
                                      "waiver": None},
        "review": d.get("review", "pending"), "evidence": d.get("evidence", []), "notes": d.get("notes", []),
        "provenance": d.get("provenance") or {"created": {"by": by, "rev": rev}, "edits": []},
    }
    for k, v in d.items():
        if k not in out and k not in ("produced_by", "consumed_by"):
            out[k] = v
    return out


def edge_id(frm: str, to: str, kind: str) -> str:
    base = f"e.{frm}>{to}"
    return base if kind in ("reads", "writes") else f"{base}#{kind}"


def _norm_edge(e: dict, by: str, rev: int) -> dict:
    kind = e.get("kind")
    out = {"id": e.get("id") or edge_id(e.get("from", ""), e.get("to", ""), kind or ""),
           "from": e.get("from"), "to": e.get("to"), "kind": kind, "op": e.get("op"), "lines": e.get("lines", []),
           "state": e.get("state"), "conflict": bool(e.get("conflict", False)), "review": e.get("review", "pending"),
           "evidence": e.get("evidence", []), "notes": e.get("notes", []),
           "provenance": e.get("provenance") or {"created": {"by": by, "rev": rev}, "edits": []}}
    for k, v in e.items():
        if k not in out:
            out[k] = v
    if not out["state"]:
        out["state"] = _strongest(out["evidence"]) or ("user_asserted" if by == "user" else "ai_inferred")
    _edge_state(out)
    return out


def _norm_question(q: dict, by: str, rev: int) -> dict:
    out = {"id": q["id"], "status": q.get("status", "open"), "node": q.get("node"), "nodes": q.get("nodes", []),
           "param_ids": q.get("param_ids", []), "kind": q.get("kind", "other"), "trigger": q.get("trigger"),
           "evidence": q.get("evidence", []), "header": q.get("header", ""), "text": q.get("text"),
           "why": q.get("why"), "agent_guess": q.get("agent_guess"), "options": q.get("options", []),
           "allow_other": bool(q.get("allow_other", True)), "allow_dont_know": bool(q.get("allow_dont_know", True)),
           "impact": q.get("impact"), "priority": q.get("priority"), "answer": q.get("answer"),
           "answered_by": q.get("answered_by"), "decision": q.get("decision"),
           "provenance": q.get("provenance") or {"created": {"by": by, "rev": rev}, "edits": []}}
    for k, v in q.items():
        if k not in out:
            out[k] = v
    return out


# ---------------------------------------------------------------------------------------------------------------
# Automatic derivation on every write (D2-2): edges are the one copy of the I/O fact
# ---------------------------------------------------------------------------------------------------------------
def _is_container_item(d: dict, shared: dict[str, int]) -> bool:
    lp = (d.get("legacy") or {}).get("path") or ""
    parts = lp.split("/")
    return any(x.lower().endswith(".gdb") for x in parts) or (shared.get(lp, 0) > 1 and bool((d.get("legacy") or
                                                                                              {}).get("layer")))


def _default_new_paths(dsets: dict, steps: dict, in_scope) -> None:
    """Default new outputs (CONTRACTS §4): <producer>/<legacy basename>; container items (FileGDB members, several
    layers of one file) get <producer>/<container stem>__<layer>.<tif|gpkg>; remaining clashes use the dataset id."""
    shared: dict[str, int] = {}
    for d in dsets.values():
        lp = (d.get("legacy") or {}).get("path")
        if lp:
            shared[lp] = shared.get(lp, 0) + 1
    want: dict[str, dict] = {}
    for d in dsets.values():
        pb = d.get("produced_by")
        cur = d.get("new")
        auto_ok = cur is None or cur.get("auto")
        if d.get("role") != "input" and pb and in_scope(pb["step"]):
            if not auto_ok:
                continue
            leg = d.get("legacy") or {}
            lp = leg.get("path")
            ext = "tif" if d.get("kind") == "raster" else NO_EXT.get(d.get("kind"), "bin")
            if lp and _is_container_item(d, shared):
                last = lp.rstrip("/").split("/")
                cont = next((x for x in reversed(last) if x.lower().endswith(".gdb")), last[-1])
                stem = slug(cont.rsplit(".", 1)[0])
                layer = leg.get("layer") or last[-1]
                name = f"{stem}__{slug(layer)}.{'tif' if d.get('kind') == 'raster' else 'gpkg'}"
                layer_out = leg.get("layer") if d.get("kind") != "raster" else None
            elif lp:
                name, layer_out = lp.rsplit("/", 1)[-1], leg.get("layer")
            else:
                name, layer_out = f"{d['id'][3:]}.{ext}", None
            want[d["id"]] = {"path": f"{pb['step']}/{name}", "layer": layer_out, "auto": True}
        elif cur is not None and cur.get("auto"):
            d["new"] = None
    taken: dict[str, int] = {}
    for did, nw in want.items():
        taken[nw["path"]] = taken.get(nw["path"], 0) + 1
    for d in dsets.values():
        if d.get("new") and not d["new"].get("auto") and d["new"].get("path"):
            taken[d["new"]["path"]] = taken.get(d["new"]["path"], 0) + 2
    used = {d["new"]["path"] for d in dsets.values() if d.get("new") and not d["new"].get("auto")
            and d["new"].get("path")}
    for did, nw in sorted(want.items()):
        path = nw["path"]
        step, name = path.split("/", 1)
        ext = name.rsplit(".", 1)[-1] if "." in name else "bin"
        if taken[path] > 1:
            path = f"{step}/{did[3:]}.{ext}"
        k = 2
        base = path
        while path in used:  # never let a default collide (validate refuses duplicate new.path)
            path = f"{base.rsplit('.', 1)[0]}__{k}.{ext}"
            k += 1
        used.add(path)
        dsets[did]["new"] = {**nw, "path": path}


def slug(text: str) -> str:
    return ac.slug(text)


def derive_structure(graph: dict) -> None:
    steps = {s["id"]: s for s in graph["steps"]}
    dsets = {d["id"]: d for d in graph["datasets"]}
    ins: dict[str, set[str]] = {s: set() for s in steps}
    outs: dict[str, set[str]] = {s: set() for s in steps}
    prod: dict[str, list[tuple[int, str, str | None]]] = {d: [] for d in dsets}
    cons: dict[str, set[str]] = {d: set() for d in dsets}
    for e in graph["edges"]:
        if e.get("state") == "rejected" or e.get("kind") not in FLOW_KINDS:
            continue
        if e["kind"] in READ_KINDS and e["from"] in dsets and e["to"] in steps:
            ins[e["to"]].add(e["from"])
            cons[e["from"]].add(e["to"])
        elif e["kind"] in WRITE_KINDS and e["from"] in steps and e["to"] in dsets:
            outs[e["from"]].add(e["to"])
            prod[e["to"]].append((0 if e["kind"] == "writes" else 1, e["from"], e.get("op")))
    for sid, s in steps.items():
        s["inputs"] = sorted(ins[sid] - outs[sid])
        s["outputs"] = sorted(outs[sid])
    for did, d in dsets.items():
        p = sorted(prod[did], key=lambda t: (steps[t[1]].get("scope") == "drop", t[0], t[1], t[2] or ""))
        d["produced_by"] = {"step": p[0][1], "op": p[0][2]} if p else None
        d["consumed_by"] = sorted(cons[did])
    # Role: user override wins; otherwise lineage's role, adjusted for the I/O and scopes (02 §4.5):
    # - an `input`/`unused` dataset written by an in-scope step (an automated manual step, a ported step that now
    #   produces it) becomes intermediate (has in-scope consumers) or final;
    # - an `unused` dataset that an in-scope step reads and no step produces becomes `input` (e.g. a raw file a
    #   newly ported manual step reads; without this the runner cannot stage it). Produced-less `intermediate`/
    #   `final` datasets are NOT turned into inputs: that is a lineage gap (a legacy output), never a raw input;
    # - an intermediate/final of a kept-legacy producer that is consumed in scope becomes `input` (bridge).
    def in_scope(sid: str | None) -> bool:
        st = steps.get(sid) if sid else None
        return bool(st) and st.get("scope") != "drop" and (st.get("scope") == "migrate" or st.get("kind") == "bridge")

    for d in dsets.values():
        if _user_owned(d, "role"):
            continue
        base = d.get("role_lineage") or d.get("role")
        pb = d.get("produced_by")
        pscope = steps[pb["step"]].get("scope") if pb and pb["step"] in steps else None
        in_scope_consumers = [c for c in d["consumed_by"] if in_scope(c)]
        role = base
        if pb and in_scope(pb["step"]) and base in ("input", "unused"):
            role = "intermediate" if in_scope_consumers else "final"
        elif not pb and base == "unused" and in_scope_consumers and (d.get("legacy") or {}).get("exists"):
            role = "input"  # a raw file that exists; a never-written item (exists false) is flagged by validate
        elif pscope == "keep_legacy" and base in ("intermediate", "final") and in_scope_consumers:
            role = "input"
        d["role"] = role
    _default_new_paths(dsets, steps, in_scope)
    graph["steps"].sort(key=lambda s: s["id"])
    graph["datasets"].sort(key=lambda d: d["id"])
    graph["edges"].sort(key=lambda e: e["id"])
    graph["questions"].sort(key=lambda q: q["id"])


# ---------------------------------------------------------------------------------------------------------------
# Validation (JSON-schema-like structure, referential integrity, acyclicity)
# ---------------------------------------------------------------------------------------------------------------
def _enum(errs: list, where: str, value: Any, allowed: list, nullable: bool = False) -> None:
    if value is None and nullable:
        return
    if value not in allowed:
        errs.append(f"{where}: {value!r} not in {allowed}")


def _type(errs: list, where: str, value: Any, typ, nullable: bool = False) -> bool:
    if value is None and nullable:
        return True
    if not isinstance(value, typ):
        errs.append(f"{where}: expected {getattr(typ, '__name__', typ)}, got {type(value).__name__}")
        return False
    return True


def _check_lines(errs: list, where: str, lines: Any) -> None:
    if not isinstance(lines, list):
        errs.append(f"{where}: lines must be a list of [a, b]")
        return
    for ln in lines:
        if isinstance(ln, int):
            continue
        if not (isinstance(ln, list) and len(ln) == 2 and all(isinstance(x, int) for x in ln) and ln[0] <= ln[1]):
            errs.append(f"{where}: bad line range {ln!r}")


def _check_evidence(errs: list, where: str, ev: Any) -> None:
    if not isinstance(ev, list):
        errs.append(f"{where}.evidence: must be a list")
        return
    for i, e in enumerate(ev):
        if isinstance(e, str):
            continue  # bare refs are allowed on steps/datasets (lineage, inventory)
        if not isinstance(e, dict) or not e.get("ref"):
            errs.append(f"{where}.evidence[{i}]: needs ref")
            continue
        if e.get("state") is not None:
            _enum(errs, f"{where}.evidence[{i}].state", e["state"], EVIDENCE_STATES)


def _check_plan(errs: list, where: str, plan: Any, dsets: set) -> None:
    if plan is None:
        return
    if not isinstance(plan, dict):
        errs.append(f"{where}: plan must be an object")
        return
    for i, lo in enumerate(plan.get("legacy_ops") or []):
        ref = lo.get("ref") if isinstance(lo, dict) else None
        if not ref or not FILE_LINE_RE.match(ref):
            errs.append(f"{where}.legacy_ops[{i}].ref must be file:line, got {ref!r}")
    for p in plan.get("params") or []:
        if not isinstance(p, str) or not PARAM_ID_RE.match(p):
            errs.append(f"{where}.params: bad param id {p!r}")
    for i, oc in enumerate(plan.get("outputs_checked") or []):
        if not isinstance(oc, dict) or oc.get("dataset") not in dsets:
            errs.append(f"{where}.outputs_checked[{i}]: unknown dataset {oc.get('dataset') if isinstance(oc, dict) else oc!r}")
    for i, r in enumerate(plan.get("risks") or []):
        if not isinstance(r, dict) or not (r.get("skill_ref") or r.get("risk") == "none"):
            errs.append(f"{where}.risks[{i}]: a risk cites a skill_ref or says risk: none")
    appr = plan.get("approved")
    if appr is not None and not isinstance(appr, dict):
        errs.append(f"{where}.approved must be an object or null")


def validate_graph(graph: dict) -> list[str]:
    errs: list[str] = []
    if graph.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA}")
    _type(errs, "revision", graph.get("revision"), int)
    for key in ("steps", "datasets", "edges", "questions"):
        if not isinstance(graph.get(key), list):
            errs.append(f"{key} must be a list")
            return errs
    proj = graph.get("project")
    if not isinstance(proj, dict) or not isinstance(proj.get("legacy_root"), str):
        errs.append("project.legacy_root (relative to migration/) is required")
    elif os.path.isabs(proj["legacy_root"]):
        errs.append("project.legacy_root must be relative to migration/")
    _type(errs, "legacy_output_dirs", graph.get("legacy_output_dirs", []), list)
    _type(errs, "invariants", graph.get("invariants", []), list)
    seen: set[str] = set()
    for key in ("steps", "datasets", "edges", "questions"):
        for obj in graph[key]:
            oid = obj.get("id")
            if oid in seen:
                errs.append(f"duplicate id {oid!r}")
            seen.add(oid)
    steps = {s["id"]: s for s in graph["steps"]}
    dsets = {d["id"]: d for d in graph["datasets"]}
    for s in graph["steps"]:
        w = f"step {s.get('id')}"
        if not isinstance(s.get("id"), str) or not STEP_ID_RE.match(s["id"]):
            errs.append(f"{w}: id must match {STEP_ID_RE.pattern}")
        _type(errs, f"{w}.title", s.get("title"), str)
        _enum(errs, f"{w}.kind", s.get("kind"), STEP_KINDS)
        _enum(errs, f"{w}.scope", s.get("scope"), SCOPES)
        _type(errs, f"{w}.scope_reason", s.get("scope_reason"), str, nullable=True)
        _enum(errs, f"{w}.review", s.get("review"), REVIEW_STATES)
        if _type(errs, f"{w}.legacy", s.get("legacy"), dict):
            _check_lines(errs, f"{w}.legacy", s["legacy"].get("lines", []))
        if _type(errs, f"{w}.params", s.get("params"), list):
            for p in s["params"]:
                if not isinstance(p, str) or not PARAM_ID_RE.match(p):
                    errs.append(f"{w}.params: bad param id {p!r}")
        _type(errs, f"{w}.ops", s.get("ops"), list)
        _type(errs, f"{w}.timeout_s", s.get("timeout_s"), (int, float))
        if _type(errs, f"{w}.new", s.get("new"), dict):
            mod = s["new"].get("module")
            if mod is not None and (not isinstance(mod, str) or os.path.isabs(mod)):
                errs.append(f"{w}.new.module must be a relative path or null")
        v = s.get("verify") or {}
        _enum(errs, f"{w}.verify.verdict", v.get("verdict"), VERDICTS, nullable=True)
        _enum(errs, f"{w}.verify.blame", v.get("blame"), STEP_BLAME, nullable=True)
        _check_evidence(errs, w, s.get("evidence", []))
        _check_plan(errs, f"{w}.plan", s.get("plan"), set(dsets))
    for d in graph["datasets"]:
        w = f"dataset {d.get('id')}"
        if not isinstance(d.get("id"), str) or not DS_ID_RE.match(d["id"]):
            errs.append(f"{w}: id must match {DS_ID_RE.pattern}")
        _enum(errs, f"{w}.kind", d.get("kind"), DATASET_KINDS)
        _enum(errs, f"{w}.role", d.get("role"), ROLES)
        _enum(errs, f"{w}.persistence", d.get("persistence"), PERSISTENCE, nullable=True)
        _enum(errs, f"{w}.review", d.get("review"), REVIEW_STATES)
        leg = d.get("legacy")
        if leg is not None:
            if _type(errs, f"{w}.legacy", leg, dict):
                lp = leg.get("path")
                if lp is not None and (not isinstance(lp, str) or os.path.isabs(lp) or "\\" in lp):
                    errs.append(f"{w}.legacy.path must be a POSIX path relative to the legacy root")
        new = d.get("new")
        if new is not None and _type(errs, f"{w}.new", new, dict):
            np_ = new.get("path")
            if np_ is not None and (not isinstance(np_, str) or os.path.isabs(np_)):
                errs.append(f"{w}.new.path must be relative to runs/<id>/outputs/")
        ref = d.get("reference") or {}
        _enum(errs, f"{w}.reference.source", ref.get("source"), ["legacy", "equivalent", "none"])
        _enum(errs, f"{w}.reference.match_method", ref.get("match_method"), ["lineage", "human"])
        if ref.get("match_method") == "human" and ref.get("confirmed_by") != "user":
            errs.append(f"{w}.reference: a human match needs confirmed_by: user")
        if ref.get("via_dataset") is not None and ref["via_dataset"] not in dsets:
            errs.append(f"{w}.reference.via_dataset {ref['via_dataset']!r} unknown")
        v = d.get("verify") or {}
        _enum(errs, f"{w}.verify.verdict", v.get("verdict"), VERDICTS, nullable=True)
        _enum(errs, f"{w}.verify.blame", v.get("blame"), DATASET_BLAME, nullable=True)
        wv = v.get("waiver")
        if wv is not None and (not isinstance(wv, dict) or wv.get("by") != "user" or not wv.get("reason")):
            errs.append(f"{w}.verify.waiver needs reason and by: user")
        _type(errs, f"{w}.compare", d.get("compare", {}), dict)
        _check_evidence(errs, w, d.get("evidence", []))
    newpaths: dict[str, list[str]] = {}
    for d in graph["datasets"]:
        np_ = (d.get("new") or {}).get("path")
        if np_:
            newpaths.setdefault(np_, []).append(d["id"])
    for np_, ids in sorted(newpaths.items()):
        if len(ids) > 1:
            errs.append(f"new.path {np_!r} is used by several datasets {sorted(ids)}; each output needs its own file")
    producers: dict[str, set[str]] = {}
    for e in graph["edges"]:
        w = f"edge {e.get('id')}"
        if not isinstance(e.get("id"), str) or not EDGE_ID_RE.match(e["id"]):
            errs.append(f"{w}: id must start with 'e.'")
        _enum(errs, f"{w}.kind", e.get("kind"), EDGE_KINDS)
        _enum(errs, f"{w}.review", e.get("review"), REVIEW_STATES)
        _enum(errs, f"{w}.state", e.get("state"), EVIDENCE_STATES)
        _check_evidence(errs, w, e.get("evidence", []))
        _check_lines(errs, w, e.get("lines", []))
        k, f, t = e.get("kind"), e.get("from"), e.get("to")
        if k in READ_KINDS and not (f in dsets and t in steps):
            errs.append(f"{w}: {k} must go from a dataset to a step ({f!r} -> {t!r})")
        elif k in WRITE_KINDS and not (f in steps and t in dsets):
            errs.append(f"{w}: {k} must go from a step to a dataset ({f!r} -> {t!r})")
        elif k == "equivalent_to" and not (f in dsets and t in dsets and f != t):
            errs.append(f"{w}: equivalent_to joins two different datasets")
        elif k == "displays" and not (isinstance(f, str) and EXTERNAL_RE.match(f) and t in dsets):
            errs.append(f"{w}: displays goes from 'project:<path>' to a dataset")
        # a dropped step never runs: only in-scope writers compete for a dataset (an alternative implementation
        # kept as knowledge writes the same legacy file without conflict)
        if k in WRITE_KINDS and e.get("state") != "rejected" and t in dsets and \
                (steps.get(f) or {}).get("scope") != "drop":
            producers.setdefault(t, set()).add(f)
    for ds, ps in sorted(producers.items()):
        if len(ps) > 1:
            errs.append(f"dataset {ds}: written by several steps {sorted(ps)}; split it into versions ds.x@<step>")
    for q in graph["questions"]:
        w = f"question {q.get('id')}"
        if not isinstance(q.get("id"), str) or not Q_ID_RE.match(q["id"]):
            errs.append(f"{w}: id must match {Q_ID_RE.pattern}")
        _enum(errs, f"{w}.status", q.get("status"), QUESTION_STATUS)
        _enum(errs, f"{w}.kind", q.get("kind"), QUESTION_KINDS)
        if not q.get("text"):
            errs.append(f"{w}: text is required")
        nodes = _question_nodes(q)
        if not nodes:
            errs.append(f"{w}: every question is linked to a node")
        for n in nodes:
            if n not in steps and n not in dsets:
                errs.append(f"{w}: linked node {n!r} unknown")
        if _type(errs, f"{w}.options", q.get("options"), list):
            vals = [o.get("value") for o in q["options"] if isinstance(o, dict)]
            if len(vals) != len(q["options"]) or any(not v for v in vals) or len(set(vals)) != len(vals):
                errs.append(f"{w}: options need unique values")
        if q.get("status") in ("answered", "assumed") and not q.get("answer"):
            errs.append(f"{w}: status {q['status']} without an answer")
        for p in q.get("param_ids") or []:
            if not PARAM_ID_RE.match(str(p)):
                errs.append(f"{w}.param_ids: bad param id {p!r}")
    errs += _cycles(graph, steps, dsets)
    return errs


def _cycles(graph: dict, steps: dict, dsets: dict) -> list[str]:
    adj: dict[str, list[str]] = {n: [] for n in list(steps) + list(dsets)}
    for e in graph["edges"]:
        if e.get("state") == "rejected" or e.get("kind") not in FLOW_KINDS:
            continue
        if e["from"] in adj and e["to"] in adj:
            adj[e["from"]].append(e["to"])
    WHITE, GREY, BLACK = 0, 1, 2
    col = {n: WHITE for n in adj}
    errs: list[str] = []
    for root in sorted(adj):
        if col[root] != WHITE:
            continue
        stack = [(root, iter(sorted(adj[root])))]
        col[root] = GREY
        path = [root]
        while stack:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                col[node] = BLACK
                stack.pop()
                path.pop()
                continue
            if col[nxt] == GREY:
                cyc = path[path.index(nxt):] + [nxt]
                errs.append("cycle: " + " -> ".join(cyc))
                return errs
            if col[nxt] == WHITE:
                col[nxt] = GREY
                stack.append((nxt, iter(sorted(adj[nxt]))))
                path.append(nxt)
    return errs


def _equivalents(graph: dict, ds: str) -> list[str]:
    return sorted({(e["to"] if e["from"] == ds else e["from"]) for e in graph["edges"]
                   if e.get("kind") == "equivalent_to" and e.get("state") != "rejected" and ds in (e["from"], e["to"])})


def unproducible_inputs(graph: dict, step_ids: Iterable[str] | None = None) -> list[dict]:
    """In-scope steps reading a dataset that has no legacy file and no in-scope producer: a run must stop (exit 12),
    so this is an open question (rewire to an existing dataset, e.g. its equivalent_to partner, or ask)."""
    steps = {s["id"]: s for s in graph["steps"]}
    dsets = {d["id"]: d for d in graph["datasets"]}
    out = []
    for sid in sorted(step_ids if step_ids is not None else steps):
        s = steps.get(sid)
        if not s or s.get("scope") != "migrate":
            continue
        for ds in s.get("inputs", []):
            d = dsets[ds]
            pb = (d.get("produced_by") or {}).get("step")
            if (d.get("legacy") or {}).get("exists") or (pb and steps[pb].get("scope") == "migrate"):
                continue
            out.append({"step": sid, "dataset": ds, "equivalents": _equivalents(graph, ds)})
    return out


def params_yaml_ids(mdir: str | os.PathLike) -> set[str] | None:
    p = paths(mdir)["params"]
    if not p.is_file():
        return None
    text = p.read_text(encoding="utf-8")
    try:
        import yaml  # optional
        doc = yaml.safe_load(text) or {}
        return {str(x.get("id")) for x in doc.get("params", []) if isinstance(x, dict) and x.get("id")}
    except ImportError:
        return set(re.findall(r"^\s*-\s*id:\s*[\"']?(p\.[a-z0-9_.]+)", text, flags=re.M))


def validate_full(mdir: str | os.PathLike, graph: dict | None = None) -> dict:
    """validate CLI: structure + params refs in params.yaml + edit log integrity. Returns {errors, warnings}."""
    graph = graph if graph is not None else load_graph(mdir)
    errors = [f"{IntegrityError.code}: {x}" for x in check_integrity(mdir)["problems"]]
    errors += validate_graph(graph)
    warnings: list[str] = []
    open_q = unproducible_inputs(graph)
    for u in open_q:
        hint = (f"rewire it to {', '.join(u['equivalents'])} (equivalent_to: add_edge + reject_edge)"
                if u["equivalents"] else "rewire it to a dataset that exists")
        errors.append(f"UNPRODUCIBLE_INPUT: {u['step']} reads {u['dataset']}, which has no legacy file and no in-scope "
                      f"producer (a run would stop, exit 12); {hint}, or ask the user")
    ids = params_yaml_ids(mdir)
    refs: dict[str, list[str]] = {}
    for s in graph["steps"]:
        for p in s.get("params", []):
            refs.setdefault(p, []).append(s["id"])
        for p in (s.get("plan") or {}).get("params") or []:
            refs.setdefault(p, []).append(f"{s['id']}.plan")
    if ids is None:
        if refs:
            warnings.append("params.yaml absent: param refs not checked")
    else:
        for p, where in sorted(refs.items()):
            if p not in ids:
                errors.append(f"param {p} (referenced by {', '.join(sorted(set(where)))}) is not in params.yaml")
    try:
        recs = read_edits(mdir)
        seqs = [r.get("seq") for r in recs]
        if seqs != list(range(1, len(seqs) + 1)):
            errors.append("graph-edits.jsonl: seq is not 1..n without gaps")
        if recs and max(r.get("rev", 0) for r in recs) > graph["revision"]:
            errors.append("graph-edits.jsonl mentions a revision newer than graph.json")
    except GraphError as exc:
        errors.append(exc.message)
    for d in graph["datasets"]:
        leg = d.get("legacy") or {}
        if leg.get("exists") and leg.get("path"):
            try:
                root = ac.legacy_root(mdir, graph)
                if not (root / leg["path"]).exists():
                    warnings.append(f"{d['id']}: legacy.exists but {leg['path']} is missing under the legacy root")
            except Exception:  # legacy root unreadable: report once
                warnings.append("legacy root not readable; legacy paths not checked")
                break
    return {"errors": errors, "warnings": warnings, "open_questions": open_q}


# ---------------------------------------------------------------------------------------------------------------
# ACL and op handlers
# ---------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, actor: str, channel: str | None, verbatim: str | None, rev: int, mdir: Path | None):
        self.actor = actor
        self.channel = channel
        self.verbatim = verbatim
        self.rev = rev
        self.at = _now()
        self.mdir = mdir


def _deny(msg: str, code: str = "acl") -> None:
    raise AclError(msg, code=code)


def _actor_in(cx: Ctx, allowed: Iterable[str], what: str) -> None:
    if cx.actor not in allowed:
        _deny(f"{what} is not allowed for actor {cx.actor} (allowed: {', '.join(allowed)})", "actor_not_allowed")


def _user_answer_for(graph: dict, qid: str | None, node_id: str) -> dict | None:
    """The question `qid` if it is answered by the user and linked to `node_id`."""
    if not qid:
        return None
    hit = _index(graph).get(qid)
    if not hit or hit[0] != "question":
        raise ValidationError(f"cited question {qid!r} unknown", code="unknown_id")
    q = hit[1]
    if q.get("status") not in ("answered", "assumed") or (q.get("answer") or {}).get("by") != "user":
        return None
    if node_id not in _question_nodes(q):
        return None
    return q


def _guard_user_owned(graph: dict, obj: dict, field: str, cx: Ctx, op: dict) -> None:
    if cx.actor != "agent" or field.split(".")[0] not in LOCKABLE:
        return
    if _user_owned(obj, field) and not _user_answer_for(graph, op.get("question"), obj["id"]):
        _deny(f"{obj['id']}.{field.split('.')[0]} was set by the user; open a question instead "
              f"(or cite a question the user answered for this node)", "user_owned_field")


AGENT_SET = {
    "step": {"title", "kind", "tool", "description", "legacy", "ops", "params", "evidence", "timeout_s", "review",
             "new.module", "new.function", "new.test", "executed_evidence", "outputs_untrusted"},
    "dataset": {"title", "kind", "description", "persistence", "legacy", "new", "compare", "legacy_in_memory",
                "no_reference_reason", "evidence", "review", "reference", "invariants"},
    "edge": {"op", "lines", "evidence", "state", "conflict", "review"},
    "question": {"text", "header", "why", "options", "agent_guess", "impact", "priority", "evidence", "trigger",
                 "param_ids", "kind", "node", "nodes", "allow_other", "allow_dont_know"},
    "graph": {"invariants", "legacy_output_dirs", "project.name", "project.legacy_tree_sha256"},
}
USER_SET = {"step": {"title"}, "dataset": {"title", "role"}, "edge": set(), "question": set(), "graph": set()}
DERIVE_SET = {"step": {"ladder", "new.code_sha256"}, "dataset": set(), "edge": set(), "question": set(),
              "graph": {"ladder"}}
NEVER_SET = {"id", "inputs", "outputs", "produced_by", "consumed_by", "provenance", "title_set_by",
             "scope_set_by", "requires_confirmation", "plan", "scope", "scope_reason", "answer", "status",
             "answered_by", "notes", "role_lineage"}


def _field_allowed(table: dict, kind: str, field: str) -> bool:
    allowed = table.get(kind, set())
    parts = field.split(".")
    return any(".".join(parts[:i]) in allowed for i in range(1, len(parts) + 1))


def op_set_field(g: dict, op: dict, cx: Ctx) -> dict:
    _allowed_keys(op, ("id", "field", "value"))
    _need(op, "id", "field")
    field = op["field"]
    if "value" not in op:
        raise ValidationError("set_field needs value (null allowed)", code="missing_field")
    if op["id"] == "graph":
        kind, obj = "graph", g
    else:
        kind, obj = _get(g, op["id"])
    top = field.split(".")[0]
    if top in NEVER_SET:
        _deny(f"{top} is not settable with set_field (derived, or owned by a dedicated op)", "field_not_settable")
    if cx.actor == "verify":
        if not (kind in ("step", "dataset") and top == "verify" and field != "verify.waiver"):
            _deny("verify writes only verify.* projections", "actor_not_allowed")
    elif cx.actor == "derive":
        if not (_field_allowed(DERIVE_SET, kind, field) or (kind == "dataset" and field == "verify.blame")):
            _deny(f"derive may not set {field}", "actor_not_allowed")
    elif cx.actor == "user":
        if not _field_allowed(USER_SET, kind, field):
            _deny(f"the editor may not set {field} with set_field; use the dedicated op", "actor_not_allowed")
    else:  # agent
        if top == "verify":
            _deny("verify.* is written only by graph.py ingest-verify (actor verify)", "verify_projection")
        if top == "ladder":
            _deny("ladder is a projection written only by graph.py derive", "ladder_projection")
        if kind == "dataset" and top == "role":
            _deny("role is derived from edges and scope, or overridden by the user", "actor_not_allowed")
        if not _field_allowed(AGENT_SET, kind, field):
            _deny(f"agent may not set {kind}.{field}", "actor_not_allowed")
        if top == "review" and op["value"] != "assumed":
            _deny("the agent may set review only to 'assumed'; confirm/reject are user ops", "review_user_only")
        if kind == "question" and obj.get("status") != "open":
            raise ValidationError(f"{obj['id']} is {obj['status']}; ask a new question instead", code="question_closed")
        if kind == "dataset" and top == "reference":
            val = op["value"] if field == "reference" else None
            if field != "reference" or not isinstance(val, dict) or val.get("match_method") != "lineage" \
                    or val.get("confirmed_by") is not None:
                _deny("the agent may set reference only as a whole with match_method lineage and confirmed_by null; "
                      "pairing by a human is the user op set_reference", "reference_user_only")
        if kind == "edge" and top == "state" and op["value"] == "rejected":
            _deny("use reject_edge (with a reason)", "use_reject_edge")
    if kind != "graph":
        _guard_user_owned(g, obj, field, cx, op)
    if top == "review":
        _enum_or_raise(op["value"], REVIEW_STATES, "review")
    _set_path(obj, field, op["value"])
    if top == "title":
        obj["title_set_by"] = cx.actor
    if kind == "edge" and top == "evidence":
        _edge_state(obj)
    if kind != "graph":
        _record_edit(obj, cx, [top])
    return {}


def _enum_or_raise(value: Any, allowed: list, what: str) -> None:
    if value not in allowed:
        raise ValidationError(f"{what}: {value!r} not in {allowed}", code="bad_value")


def _make_edge(g: dict, cx: Ctx, frm: str, to: str, kind: str, evidence: list | None, extra: dict | None = None) -> dict:
    if cx.actor == "user":
        ev = [{"state": "user_asserted", "ref": f"graph:rev{cx.rev}", "summary": "added in the graph editor",
               "by": "user", "rev": cx.rev}]
    else:
        ev = []
        for e in evidence or []:
            if not isinstance(e, dict) or not e.get("ref"):
                raise ValidationError("edge evidence items need ref (file:line, inventory:path, …)", code="bad_evidence")
            ev.append({"state": e.get("state", "structurally_inferred"), "ref": e["ref"],
                       "summary": e.get("summary", ""), "by": e.get("by", cx.actor), "rev": cx.rev})
        if not ev:
            raise ValidationError("the agent adds an edge only with evidence (file:line or inventory:path)",
                                  code="evidence_required")
    e = {"from": frm, "to": to, "kind": kind, "evidence": ev, **(extra or {})}
    edge = _norm_edge(e, cx.actor, cx.rev)
    if edge["id"] in _index(g):
        raise ValidationError(f"edge {edge['id']} exists", code="duplicate_id")
    g["edges"].append(edge)
    return edge


def op_add_step(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "add_step")
    _need(op, "id", "title")
    if op["id"] in _index(g):
        raise ValidationError(f"{op['id']} exists", code="duplicate_id")
    if not STEP_ID_RE.match(op["id"]):
        raise ValidationError(f"step id must match {STEP_ID_RE.pattern}", code="bad_id")
    if cx.actor == "user":
        _allowed_keys(op, ("id", "title", "description", "reads", "writes", "note"))
        raw = {"id": op["id"], "title": op["title"], "kind": "new", "scope": "migrate", "review": "pending",
               "description": op.get("description"), "tool": None}
    else:
        _allowed_keys(op, ("id", "title", "kind", "tool", "description", "scope", "scope_reason", "legacy", "ops",
                           "params", "new", "timeout_s", "evidence", "reads", "writes", "review",
                           "executed_evidence", "outputs_untrusted"))
        raw = {k: v for k, v in op.items() if k not in ("type", "reads", "writes", "question")}
        if raw.get("review") not in (None, "pending", "assumed"):
            _deny("the agent creates nodes as pending or assumed", "review_user_only")
    step = _norm_step(raw, cx.actor, cx.rev)
    if cx.actor == "user" and op.get("note"):
        step["notes"].append({"text": op["note"], "by": "user", "rev": cx.rev, "at": cx.at, "state": "user_asserted"})
    g["steps"].append(step)
    ev = op.get("evidence") if cx.actor == "agent" else None
    for ds in op.get("reads") or []:
        _get(g, ds, ("dataset",))
        _make_edge(g, cx, ds, op["id"], "reads", ev or [{"state": "ai_inferred", "ref": f"graph:rev{cx.rev}",
                                                            "summary": "declared with add_step"}])
    for ds in op.get("writes") or []:
        _get(g, ds, ("dataset",))
        _make_edge(g, cx, op["id"], ds, "writes", ev or [{"state": "ai_inferred", "ref": f"graph:rev{cx.rev}",
                                                             "summary": "declared with add_step"}])
    return {"id": op["id"]}


def op_add_dataset(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "add_dataset")
    _need(op, "id", "kind")
    if op["id"] in _index(g):
        raise ValidationError(f"{op['id']} exists", code="duplicate_id")
    if not DS_ID_RE.match(op["id"]):
        raise ValidationError(f"dataset id must match {DS_ID_RE.pattern}", code="bad_id")
    if cx.actor == "user":
        _allowed_keys(op, ("id", "title", "kind", "role", "description", "note"))
        if op.get("role", "intermediate") == "input":
            _deny("a new input with a legacy file comes from discover; ask the agent in a note", "actor_not_allowed")
        raw = {"id": op["id"], "title": op.get("title"), "kind": op["kind"], "role": op.get("role", "intermediate"),
               "legacy": None, "description": op.get("description")}
    else:
        _allowed_keys(op, ("id", "title", "kind", "role", "description", "persistence", "legacy", "new", "compare",
                           "legacy_in_memory", "no_reference_reason", "reference", "evidence", "review"))
        raw = {k: v for k, v in op.items() if k not in ("type", "question")}
        if raw.get("review") not in (None, "pending", "assumed"):
            _deny("the agent creates nodes as pending or assumed", "review_user_only")
        ref = raw.get("reference")
        if ref and (ref.get("match_method") == "human" or ref.get("confirmed_by")):
            _deny("a human pairing is the user op set_reference", "reference_user_only")
    d = _norm_dataset(raw, cx.actor, cx.rev)
    if cx.actor == "user" and op.get("note"):
        d["notes"].append({"text": op["note"], "by": "user", "rev": cx.rev, "at": cx.at, "state": "user_asserted"})
    g["datasets"].append(d)
    return {"id": op["id"]}


def op_add_edge(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "add_edge")
    _allowed_keys(op, ("id", "from", "to", "kind", "op", "lines", "evidence", "note"))
    _need(op, "from", "to", "kind")
    _enum_or_raise(op["kind"], EDGE_KINDS, "edge kind")
    if cx.actor == "user" and op["kind"] not in FLOW_KINDS + ("equivalent_to",):
        _deny("the editor adds reads/writes/reads_grid/reads_write/equivalent_to edges", "actor_not_allowed")
    extra = {k: op[k] for k in ("id", "op", "lines") if op.get(k) is not None}
    edge = _make_edge(g, cx, op["from"], op["to"], op["kind"], op.get("evidence"), extra)
    if cx.actor == "user" and op.get("note"):
        edge["notes"].append({"text": op["note"], "by": "user", "rev": cx.rev, "at": cx.at, "state": "user_asserted"})
    return {"id": edge["id"]}


def op_add_evidence(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "add_evidence")
    _allowed_keys(op, ("id", "evidence"))
    _need(op, "id", "evidence")
    kind, obj = _get(g, op["id"], ("step", "dataset", "edge"))
    items = op["evidence"] if isinstance(op["evidence"], list) else [op["evidence"]]
    for e in items:
        if not isinstance(e, dict) or not e.get("ref"):
            raise ValidationError("evidence items need ref", code="bad_evidence")
        st = e.get("state", "structurally_inferred")
        _enum_or_raise(st, EVIDENCE_STATES, "evidence state")
        obj.setdefault("evidence", []).append({"state": st, "ref": e["ref"], "summary": e.get("summary", ""),
                                               "by": e.get("by", "agent"), "rev": cx.rev})
    if kind == "edge":
        _edge_state(obj)
    _record_edit(obj, cx, ["evidence"])
    return {}


def op_set_scope(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "set_scope")
    _allowed_keys(op, ("id", "scope", "scope_reason", "reason"))
    _need(op, "id", "scope")
    _, step = _get(g, op["id"], ("step",))
    _enum_or_raise(op["scope"], SCOPES, "scope")
    reason = op.get("scope_reason") or op.get("reason")
    if cx.actor == "agent":
        qid = op.get("question")
        hit = _index(g).get(qid) if qid else None
        if not hit or hit[0] != "question" or hit[1].get("status") not in ("answered", "assumed") \
                or op["id"] not in _question_nodes(hit[1]):
            _deny("the agent changes scope only by citing an answered question linked to the step "
                  "(op field 'question')", "scope_needs_answer")
        _guard_user_owned(g, step, "scope", cx, op)
        step["scope"], step["scope_reason"], step["scope_set_by"] = op["scope"], reason, "agent"
        step["scope_evidence"] = f"answer:{qid}"
        step["requires_confirmation"] = None
        _record_edit(step, cx, ["scope"])
        return {}
    # user
    if op["scope"] == "drop" and step.get("scope") != "drop":
        affected = _deletion_impact(g, step)
        if affected["datasets"] or affected["consumers"]:
            step["requires_confirmation"] = {"op": "set_scope", "proposed": {"scope": "drop", "scope_reason":
                                             reason or "out_of_scope_by_owner"}, "by": "user", "rev": cx.rev,
                                             "at": cx.at, **affected}
            _record_edit(step, cx, ["requires_confirmation"])
            return {"requires_confirmation": True, **affected}
    step["scope"], step["scope_reason"], step["scope_set_by"] = op["scope"], reason, "user"
    step["requires_confirmation"] = None
    _record_edit(step, cx, ["scope"])
    eff: dict = {"from_kind": step.get("kind")}
    if op["scope"] == "migrate":
        miss = unproducible_inputs(g, [step["id"]])
        if miss:
            eff["missing_inputs"] = [{"dataset": m["dataset"], "equivalents": m["equivalents"]} for m in miss]
    return eff


def _deletion_impact(g: dict, step: dict) -> dict:
    dsets = {d["id"]: d for d in g["datasets"]}
    steps = {s["id"]: s for s in g["steps"]}
    legacy_out = sorted(d for d in step.get("outputs", []) if (dsets[d].get("legacy") or {}).get("exists"))
    consumers = sorted({c for d in step.get("outputs", []) for c in dsets[d].get("consumed_by", [])
                        if c != step["id"] and steps[c].get("scope") == "migrate"})
    # downstream finals reachable from this step
    finals, seen, todo = set(), set(), [step["id"]]
    while todo:
        s = todo.pop()
        if s in seen:
            continue
        seen.add(s)
        for d in steps[s].get("outputs", []):
            if dsets[d].get("role") == "final":
                finals.add(d)
            todo += [c for c in dsets[d].get("consumed_by", [])]
    return {"datasets": legacy_out, "consumers": consumers, "finals": sorted(finals)}


def op_delete_step(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("user", "agent"), "delete_step")
    _allowed_keys(op, ("id", "reason"))
    _need(op, "id")
    _, step = _get(g, op["id"], ("step",))
    affected = _deletion_impact(g, step)
    reason = op.get("reason") or "out_of_scope_by_owner"
    if cx.actor == "agent":
        _deny("the agent does not delete steps; it changes scope by citing an answered question", "actor_not_allowed")
    if affected["datasets"] or affected["consumers"]:
        step["requires_confirmation"] = {"op": "delete_step", "proposed": {"scope": "drop", "scope_reason": reason},
                                         "by": "user", "rev": cx.rev, "at": cx.at, **affected}
        _record_edit(step, cx, ["requires_confirmation"])
        return {"requires_confirmation": True, **affected}
    step["scope"], step["scope_reason"], step["scope_set_by"] = "drop", reason, "user"
    _record_edit(step, cx, ["scope"])
    return {"requires_confirmation": False}


def op_resolve_confirmation(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "resolve_confirmation")
    _allowed_keys(op, ("id", "decision"))
    _need(op, "id", "decision", "question")
    _, step = _get(g, op["id"], ("step",))
    rc = step.get("requires_confirmation")
    if not rc:
        raise ValidationError(f"{op['id']} has nothing awaiting confirmation", code="nothing_to_confirm")
    if not _user_answer_for(g, op["question"], op["id"]):
        _deny("resolve_confirmation cites a question the user answered, linked to the step", "scope_needs_answer")
    _enum_or_raise(op["decision"], ["apply", "keep"], "decision")
    if op["decision"] == "apply":
        step["scope"] = rc["proposed"]["scope"]
        step["scope_reason"] = rc["proposed"].get("scope_reason")
        step["scope_set_by"] = "user"
        step["scope_evidence"] = f"answer:{op['question']}"
    step.setdefault("confirmations", []).append({**rc, "decision": op["decision"], "question": op["question"],
                                                 "resolved_rev": cx.rev})
    step["requires_confirmation"] = None
    _record_edit(step, cx, ["requires_confirmation"] + (["scope"] if op["decision"] == "apply" else []))
    return {}


def op_add_question(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "add_question")
    _need(op, "id", "text")
    if op["id"] in _index(g):
        raise ValidationError(f"{op['id']} exists", code="duplicate_id")
    raw = {k: v for k, v in op.items() if k not in ("type", "question", "channel", "verbatim")}
    if raw.get("status", "open") != "open" or raw.get("answer"):
        raise ValidationError("questions are created open; answers are a separate op", code="bad_value")
    q = _norm_question(raw, cx.actor, cx.rev)
    g["questions"].append(q)
    return {"id": q["id"]}


def op_answer_question(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "answer_question")
    _allowed_keys(op, ("id", "value", "text"))
    _need(op, "id", "value")
    _, q = _get(g, op["id"], ("question",))
    channel = op.get("channel") or cx.channel or ("graph" if cx.actor == "user" else None)
    verbatim = op.get("verbatim") or cx.verbatim
    if cx.actor == "agent" and (channel != "interview" or not verbatim):
        _deny("the agent records an answer only with channel interview and the user's verbatim words",
              "answer_needs_interview_verbatim")
    if q.get("status") == "dismissed":
        raise ValidationError(f"{q['id']} is dismissed", code="question_closed")
    prev = q.get("answer")
    if prev and prev.get("by") == "user" and cx.actor == "agent":
        _deny(f"{q['id']} was answered by the user; ask a new question to revisit it", "user_owned_field")
    vals = [o["value"] for o in q.get("options", [])]
    value = op["value"]
    if value == "dont_know":
        if not q.get("allow_dont_know", True):
            raise ValidationError(f"{q['id']} does not allow 'Don't know'", code="bad_value")
    elif value == "other":
        if not q.get("allow_other", True) or not op.get("text"):
            raise ValidationError("'other' needs free text and allow_other", code="bad_value")
    elif value not in vals:
        raise ValidationError(f"{value!r} is not an option of {q['id']} ({vals})", code="bad_value")
    rec_opt = next((o["value"] for o in q.get("options", []) if o.get("recommended")), None)
    answer = {"value": value, "text": op.get("text"), "by": "user", "channel": channel, "verbatim": verbatim,
              "recorded_by": cx.actor, "rev": cx.rev, "at": cx.at}
    if value == "dont_know":
        answer["assumed_value"] = rec_opt
    if prev:
        q.setdefault("answer_history", []).append(prev)
    q["answer"] = answer
    q["status"] = "assumed" if value == "dont_know" else "answered"
    q["answered_by"] = f"user ({channel})"
    _record_edit(q, cx, ["answer"])
    return {"status": q["status"]}


def op_assume_question(g: dict, op: dict, cx: Ctx) -> dict:
    """Agent records a visible assumption without asking (budget exhausted, 03 §4.4)."""
    _actor_in(cx, ("agent",), "assume_question")
    _allowed_keys(op, ("id", "value", "reason"))
    _need(op, "id", "value", "reason")
    _, q = _get(g, op["id"], ("question",))
    if q.get("status") != "open":
        raise ValidationError(f"{q['id']} is {q['status']}", code="question_closed")
    if op["value"] not in [o["value"] for o in q.get("options", [])]:
        raise ValidationError(f"{op['value']!r} is not an option of {q['id']}", code="bad_value")
    q["answer"] = {"value": op["value"], "text": op["reason"], "by": "agent", "channel": None, "verbatim": None,
                   "recorded_by": "agent", "rev": cx.rev, "at": cx.at}
    q["status"] = "assumed"
    q["answered_by"] = "agent (assumption)"
    _record_edit(q, cx, ["answer"])
    return {}


def op_dismiss_question(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "dismiss_question")
    _allowed_keys(op, ("id", "reason"))
    _need(op, "id", "reason")
    _, q = _get(g, op["id"], ("question",))
    if q.get("status") != "open":
        raise ValidationError(f"{q['id']} is {q['status']}", code="question_closed")
    q["status"] = "dismissed"
    q["dismissed"] = {"by": cx.actor, "reason": op["reason"], "rev": cx.rev}
    _record_edit(q, cx, ["status"])
    return {}


PLAN_KEYS = ("summary", "legacy_ops", "oss", "params", "outputs_checked", "risks", "isolation", "notes")


def op_set_plan(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "set_plan")
    _allowed_keys(op, ("id", "plan"))
    _need(op, "id", "plan")
    _, step = _get(g, op["id"], ("step",))
    plan = op["plan"]
    if not isinstance(plan, dict):
        raise ValidationError("plan must be an object", code="bad_value")
    extra = sorted(set(plan) - set(PLAN_KEYS) - {"approved"})
    if extra:
        raise ValidationError(f"plan: unknown keys {extra} (allowed {list(PLAN_KEYS)})", code="unknown_key")
    old = step.get("plan") or {}
    new = {k: copy.deepcopy(plan.get(k)) for k in PLAN_KEYS if k in plan}
    for k in ("legacy_ops", "oss", "params", "outputs_checked", "risks"):
        new.setdefault(k, [])
    new.setdefault("isolation", None)
    was_approved = bool((old.get("approved") or {}).get("by"))
    same = {k: v for k, v in old.items() if k != "approved"} == new
    new["approved"] = old.get("approved") if same else {"by": None, "at": None}
    step["plan"] = new
    _record_edit(step, cx, ["plan"])
    return {"approval_invalidated": was_approved and not same}


STANDING = "agent (standing instruction)"
# Question kinds that may stay open past the plan: the target (workspace, catalog) is chosen after the build.
PLAN_NONBLOCKING_KINDS = ("target",)


def plan_blocking_questions(g: dict) -> list[str]:
    """Open questions on an in-scope node (a migrate step, its inputs or outputs): BUILD.md §0 says each is answered
    or assumed before the plan is approved."""
    scope = set()
    for s in g["steps"]:
        if s.get("scope") == "migrate":
            scope.update([s["id"], *s.get("inputs", []), *s.get("outputs", [])])
    return sorted(q["id"] for q in g["questions"] if q.get("status") == "open"
                  and q.get("kind") not in PLAN_NONBLOCKING_KINDS and scope.intersection(_question_nodes(q)))


def op_approve_plan(g: dict, op: dict, cx: Ctx) -> dict:
    """`by: user` only when the user approved the plan they were shown: from the graph editor (actor user), or by the
    agent with the user's verbatim words AND `plan_sha256` = sha256 of migration/plan.md as rendered from this
    graph (the text shown). Without `plan_sha256` the approval is recorded as `by: agent (standing instruction)`,
    which ladder and the report never present as the user's approval (B2)."""
    _actor_in(cx, ("agent", "user"), "approve_plan")
    _allowed_keys(op, ("steps", "plan_sha256"))
    _need(op, "steps")
    channel = op.get("channel") or cx.channel or ("graph" if cx.actor == "user" else None)
    verbatim = op.get("verbatim") or cx.verbatim
    if cx.actor == "agent" and (channel != "interview" or not verbatim):
        _deny("plan approval by the agent needs channel interview and the user's verbatim words",
              "approval_needs_interview_verbatim")
    blocking = plan_blocking_questions(g)
    if blocking:
        raise ValidationError(f"open question(s) {', '.join(blocking)} on in-scope steps/datasets: answer each "
                              "(answer_question) or record a visible assumption (assume_question) before the plan is "
                              "approved (BUILD.md §0); if only the user can answer, stop and ask",
                              code="open_questions")
    shown = op.get("plan_sha256")
    if shown is not None:
        if not (isinstance(shown, str) and re.fullmatch(r"(sha256:)?[0-9a-f]{64}", shown)):
            raise ValidationError("plan_sha256 must be the 64-hex sha256 of migration/plan.md", code="bad_value")
        shown = shown.split(":")[-1]
        pm = (cx.mdir / "plan.md") if cx.mdir else None
        if pm is None or not pm.is_file():
            raise ValidationError("plan_sha256 given but migration/plan.md does not exist: run render-plan and show "
                                  "it", code="plan_not_shown")
        on_disk = pm.read_bytes()
        if ac.sha256_bytes(on_disk).split(":")[-1] != shown:
            raise ValidationError("plan_sha256 is not the sha256 of migration/plan.md: the user approved another "
                                  "text; re-render, show plan.md again and ask again", code="plan_sha_mismatch")
        if on_disk.decode("utf-8", "replace") != plan_text(g):
            raise ValidationError("migration/plan.md is not the render of the current plan (graph changed since "
                                  "render-plan): re-render, show it again and ask again", code="plan_stale")
    by = "user" if cx.actor == "user" or shown else STANDING
    ids = op["steps"]
    if ids == "*" or ids == ["*"]:
        ids = [s["id"] for s in g["steps"] if s.get("plan") and s.get("scope") == "migrate"]
        if not ids:
            raise ValidationError("no migrate step has a plan", code="no_plan")
    approved = []
    for sid in ids:
        _, step = _get(g, sid, ("step",))
        if not step.get("plan"):
            raise ValidationError(f"{sid} has no plan to approve", code="no_plan")
        step["plan"]["approved"] = {"by": by, "at": cx.at, "channel": channel, "verbatim": verbatim,
                                    "recorded_by": cx.actor, "rev": cx.rev, "shown_sha256": shown}
        _record_edit(step, cx, ["plan.approved"])
        approved.append(sid)
    return {"approved": approved, "by": by}


def op_set_new(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "set_new")
    _allowed_keys(op, ("id", "new"))
    _need(op, "id")
    kind, obj = _get(g, op["id"], ("step", "dataset"))
    new = op.get("new")
    if kind == "step":
        if not isinstance(new, dict):
            raise ValidationError("step new must be an object {module, function, test}", code="bad_value")
        cur = obj.get("new") or {}
        obj["new"] = {"module": new.get("module"), "function": new.get("function", "run"),
                      "code_sha256": cur.get("code_sha256") if cur.get("module") == new.get("module") else None,
                      **({"test": new["test"]} if new.get("test") else {})}
    else:
        if new is not None and (not isinstance(new, dict) or not new.get("path")):
            raise ValidationError("dataset new must be {path, layer} or null", code="bad_value")
        obj["new"] = None if new is None else {"path": new["path"], "layer": new.get("layer")}
    _record_edit(obj, cx, ["new"])
    return {}


def op_set_reference(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("user",), "set_reference")
    _allowed_keys(op, ("id", "source", "via_dataset", "legacy_path", "layer", "reason"))
    _need(op, "id", "source")
    _, d = _get(g, op["id"], ("dataset",))
    src = op["source"]
    _enum_or_raise(src, ["legacy", "equivalent", "none"], "reference source")
    if src == "equivalent":
        _need(op, "via_dataset")
        _get(g, op["via_dataset"], ("dataset",))
        eq = [e for e in g["edges"] if e["kind"] == "equivalent_to" and e.get("state") != "rejected"
              and {e["from"], e["to"]} == {op["id"], op["via_dataset"]}]
        if not eq:
            raise ValidationError(f"no equivalent_to edge between {op['id']} and {op['via_dataset']}",
                                  code="no_equivalence")
        for e in eq:
            e["review"] = "confirmed"
            _record_edit(e, cx, ["review"])
        d["reference"] = {"source": "equivalent", "match_method": "human", "via_dataset": op["via_dataset"],
                          "confirmed_by": "user"}
    elif src == "legacy":
        if op.get("legacy_path"):
            lp = op["legacy_path"]
            if os.path.isabs(lp) or ".." in Path(lp).parts or "\\" in lp:
                raise ValidationError("legacy_path is POSIX and relative to the legacy root", code="bad_path")
            exists = None
            if cx.mdir is not None:
                try:
                    exists = (ac.legacy_root(cx.mdir, g) / lp).exists()
                except Exception:
                    exists = None
            leg = d.get("legacy") or {}
            d["legacy"] = {"path": None, "layer": None, "sheet": None, "exists": False, "sha256": None,
                           "content_sha256": None, "profile": None, "sidecars": [], **leg,
                           "path": lp, "layer": op.get("layer"), "exists": bool(exists), "set_by": "user"}
        elif not (d.get("legacy") or {}).get("path"):
            raise ValidationError("set_reference legacy needs legacy_path for a dataset without a legacy file",
                                  code="missing_field")
        d["reference"] = {"source": "legacy", "match_method": "human", "via_dataset": None, "confirmed_by": "user"}
    else:
        d["reference"] = {"source": "none", "match_method": "human", "via_dataset": None, "confirmed_by": "user"}
        d["no_reference_reason"] = op.get("reason") or "declared_by_user"
    _record_edit(d, cx, ["reference"] + (["legacy"] if op.get("legacy_path") else []))
    return {}


def op_waive_verify(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("user",), "waive_verify")
    _allowed_keys(op, ("id", "reason"))
    _need(op, "id", "reason")
    _, d = _get(g, op["id"], ("dataset",))
    d.setdefault("verify", {})["waiver"] = {"reason": op["reason"], "by": "user", "rev": cx.rev, "at": cx.at}
    _record_edit(d, cx, ["verify.waiver"])
    return {}


def op_reject_edge(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "reject_edge")
    _allowed_keys(op, ("id", "reason"))
    _need(op, "id")
    _, e = _get(g, op["id"], ("edge",))
    if cx.actor == "agent":
        _need(op, "reason")
        _guard_user_owned(g, e, "review", cx, op)
    e["review"] = "rejected"
    e.setdefault("evidence", []).append({"state": "rejected", "ref": f"graph:rev{cx.rev}",
                                         "summary": op.get("reason") or "rejected in the graph editor",
                                         "by": cx.actor, "rev": cx.rev})
    _edge_state(e)
    _record_edit(e, cx, ["review"])
    return {"conflict": e["conflict"],
            "code_evidence": [x["ref"] for x in e["evidence"] if x.get("state") not in ("rejected", "user_asserted")]}


def op_add_note(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "add_note")
    _allowed_keys(op, ("id", "text"))
    _need(op, "id", "text")
    kind, obj = _get(g, op["id"], ("step", "dataset", "edge", "question"))
    obj.setdefault("notes", []).append({"text": op["text"], "by": cx.actor, "rev": cx.rev, "at": cx.at,
                                        "state": "user_asserted" if cx.actor == "user" else "ai_inferred"})
    _record_edit(obj, cx, ["notes"])
    return {}


def _review_op(g: dict, op: dict, cx: Ctx, value: str) -> dict:
    _actor_in(cx, ("user",), op["type"])
    _allowed_keys(op, ("id", "reason"))
    _need(op, "id")
    kind, obj = _get(g, op["id"], ("step", "dataset", "edge"))
    if kind == "edge" and value == "rejected":
        return op_reject_edge(g, op, cx)
    obj["review"] = value
    if value == "rejected" and op.get("reason"):
        obj.setdefault("notes", []).append({"text": f"rejected: {op['reason']}", "by": "user", "rev": cx.rev,
                                            "at": cx.at, "state": "user_asserted"})
    if kind == "edge":
        _edge_state(obj)
    _record_edit(obj, cx, ["review"])
    return {}


def op_confirm(g: dict, op: dict, cx: Ctx) -> dict:
    return _review_op(g, op, cx, "confirmed")


def op_reject_node(g: dict, op: dict, cx: Ctx) -> dict:
    return _review_op(g, op, cx, "rejected")


def op_rename(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent", "user"), "rename")
    _allowed_keys(op, ("id", "title"))
    _need(op, "id", "title")
    _, obj = _get(g, op["id"], ("step", "dataset"))
    _guard_user_owned(g, obj, "title", cx, op)
    obj["title"] = op["title"]
    obj["title_set_by"] = cx.actor
    _record_edit(obj, cx, ["title"])
    return {}


def op_submit(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("user",), "submit")
    _allowed_keys(op, ("message",))
    return {}


def op_ack(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("agent",), "ack")
    _allowed_keys(op, ("seq",))
    seq = op.get("seq")
    if not isinstance(seq, int) or seq < 0:
        raise ValidationError("ack needs an integer seq", code="bad_value")
    cur = (g.get("acks") or {}).get("agent_seq", 0)
    last = _last_seq(cx.mdir) if cx.mdir else seq
    if seq > last:
        raise ValidationError(f"seq {seq} is beyond the last edit {last}", code="bad_value")
    g["acks"] = {"agent_seq": max(cur, seq)}
    return {"agent_seq": g["acks"]["agent_seq"]}


def op_set_verify(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("verify",), "set_verify")
    _allowed_keys(op, ("id", "verify"))
    _need(op, "id", "verify")
    kind, obj = _get(g, op["id"], ("step", "dataset"))
    v = op["verify"]
    if not isinstance(v, dict) or "waiver" in v:
        raise ValidationError("verify must be an object without waiver (the waiver is the user's)", code="bad_value")
    cur = obj.get("verify") or {}
    obj["verify"] = {**cur, **copy.deepcopy(v)}
    _record_edit(obj, cx, ["verify"])
    return {}


def op_set_ladder(g: dict, op: dict, cx: Ctx) -> dict:
    _actor_in(cx, ("derive",), "set_ladder")
    _allowed_keys(op, ("id", "ladder"))
    _need(op, "id")
    if op["id"] == "graph":
        g["ladder"] = {**(op.get("ladder") or {}), "derived_rev": cx.rev}
        return {}
    _, step = _get(g, op["id"], ("step",))
    step["ladder"] = op.get("ladder")
    return {}


OPS = {
    "add_step": op_add_step, "add_dataset": op_add_dataset, "add_edge": op_add_edge, "add_evidence": op_add_evidence,
    "set_field": op_set_field, "set_scope": op_set_scope, "delete_step": op_delete_step,
    "resolve_confirmation": op_resolve_confirmation, "add_question": op_add_question,
    "answer_question": op_answer_question, "assume_question": op_assume_question,
    "dismiss_question": op_dismiss_question, "set_plan": op_set_plan, "approve_plan": op_approve_plan,
    "set_new": op_set_new, "set_reference": op_set_reference, "waive_verify": op_waive_verify,
    "reject_edge": op_reject_edge, "add_note": op_add_note, "confirm": op_confirm, "reject_node": op_reject_node,
    "rename": op_rename, "submit": op_submit, "ack": op_ack, "set_verify": op_set_verify, "set_ladder": op_set_ladder,
}
NON_MUTATING = {"submit"}


# ---------------------------------------------------------------------------------------------------------------
# Rebase support: which (node, field) keys an op touches
# ---------------------------------------------------------------------------------------------------------------
def touched(op: dict) -> set[str]:
    t = op.get("type")
    i = op.get("id")
    if t == "submit":
        return set()
    if t == "ack":
        return {"graph:acks"}
    if t in ("add_step", "add_dataset", "add_question"):
        keys = {f"{i}:*"}
        for ds in (op.get("reads") or []) + (op.get("writes") or []):
            keys.add(f"{ds}:edges")
        return keys
    if t == "add_edge":
        eid = op.get("id") or edge_id(op.get("from", ""), op.get("to", ""), op.get("kind", ""))
        return {f"{eid}:*", f"{op.get('from')}:edges", f"{op.get('to')}:edges"}
    if t == "set_field":
        return {f"{i}:{str(op.get('field', '')).split('.')[0]}"}
    if t in ("set_scope", "delete_step", "resolve_confirmation"):
        return {f"{i}:scope"}
    if t in ("answer_question", "assume_question", "dismiss_question"):
        return {f"{i}:answer"}
    if t in ("set_plan", "approve_plan"):
        ids = op.get("steps") if t == "approve_plan" else [i]
        if ids in ("*", ["*"]):
            return {"graph:plan"}
        return {f"{s}:plan" for s in ids or []}
    if t == "set_new":
        return {f"{i}:new"}
    if t == "set_reference":
        return {f"{i}:reference", f"{op.get('via_dataset')}:reference"}
    if t == "waive_verify":
        return {f"{i}:verify"}
    if t in ("reject_edge", "confirm", "reject_node"):
        return {f"{i}:review"}
    if t == "add_note":
        return {f"{i}:notes"}
    if t == "rename":
        return {f"{i}:title"}
    if t == "add_evidence":
        return {f"{i}:evidence"}
    if t == "set_verify":
        return {f"{i}:verify"}
    if t == "set_ladder":
        return {f"{i}:ladder"}
    return {"graph:*"}


def _overlap(a: set[str], b: set[str]) -> bool:
    if "graph:*" in a or "graph:*" in b:
        return bool(a and b)
    if a & b:
        return True
    if ("graph:plan" in a and any(k.endswith(":plan") for k in b)) or \
            ("graph:plan" in b and any(k.endswith(":plan") for k in a)):
        return True
    na = {k.split(":")[0] for k in a if k.endswith(":*")}
    nb = {k.split(":")[0] for k in b if k.endswith(":*")}
    return any(k.split(":")[0] in nb for k in a) or any(k.split(":")[0] in na for k in b)


# ---------------------------------------------------------------------------------------------------------------
# apply / init
# ---------------------------------------------------------------------------------------------------------------
def apply(migration_dir: str | os.PathLike, ops: list[dict], actor: str, base_revision: int, *,
          channel: str | None = None, verbatim: str | None = None, rebase: bool = False) -> dict:
    """Apply `ops` atomically as one revision. Returns {revision, seqs, results}.

    Stale `base_revision` -> ConflictError, except for actor user with rebase=True when every op since
    base_revision touches different (node, field) keys (the editor's 409 rebase, 02 §4.6).
    """
    mdir = Path(migration_dir)
    if actor not in ACTORS:
        raise ValidationError(f"unknown actor {actor!r}", code="bad_actor")
    if channel is not None and channel not in CHANNELS:
        raise ValidationError(f"channel must be one of {CHANNELS}", code="bad_value")
    if not isinstance(ops, list) or not ops or not all(isinstance(o, dict) for o in ops):
        raise ValidationError("ops must be a non-empty list of objects", code="bad_ops")
    for o in ops:
        if o.get("type") not in OPS:
            raise ValidationError(f"unknown op type {o.get('type')!r}; see GRAPH_OPS.md", code="unknown_op")
    if not isinstance(base_revision, int):
        raise ValidationError("base_revision must be an integer", code="bad_value")
    with locked(mdir):
        graph = load_graph(mdir)
        require_intact(mdir, lock=False)
        rev = graph["revision"]
        if base_revision != rev:
            since = [r for r in read_edits(mdir) if r.get("rev", 0) > base_revision]
            if base_revision > rev or not rebase or actor != "user":
                raise ConflictError(f"base revision {base_revision} is stale (graph is at {rev}); re-read and retry",
                                    revision=rev, conflicting_seqs=[r["seq"] for r in since])
            mine: set[str] = set()
            for o in ops:
                mine |= touched(o)
            clash = [r["seq"] for r in since if _overlap(touched(r.get("op") or {}), mine)]
            if clash:
                raise ConflictError("your edit touches what changed since you loaded the graph",
                                    revision=rev, conflicting_seqs=clash)
        mutating = any(o["type"] not in NON_MUTATING for o in ops)
        new_rev = rev + 1 if mutating else rev
        cx = Ctx(actor, channel, verbatim, new_rev, mdir)
        new = copy.deepcopy(graph)
        results = []
        for o in ops:
            res = OPS[o["type"]](new, o, cx) or {}
            results.append(res)
            derive_structure(new)  # later ops in a batch see the derived I/O of earlier ones
        if mutating:
            errs = validate_graph(new)
            if errs:
                raise ValidationError("graph would be invalid: " + "; ".join(errs[:8]), code="invalid_graph",
                                      details=errs)
            new["revision"] = new_rev
            new["updated"] = {"rev": new_rev, "by": actor, "at": cx.at}
            _write_graph(mdir, new)
        seq0 = _last_seq(mdir)
        recs = []
        for k, (o, res) in enumerate(zip(ops, results), 1):
            rec = {"seq": seq0 + k, "rev": new_rev, "base_rev": base_revision, "actor": actor,
                   "channel": o.get("channel") or channel or ("graph" if actor == "user" else "cli"),
                   "at": cx.at, "op": o, "result": "applied"}
            if res:
                rec["effect"] = res
            recs.append(rec)
        _append_edits(mdir, recs)
        _record_head(mdir, new_rev)
    return {"revision": new_rev, "seqs": [r["seq"] for r in recs], "results": results}


def init(migration_dir: str | os.PathLike, draft: dict) -> dict:
    """Create revision 1 from a lineage draft (steps, datasets, edges, questions, project, …)."""
    mdir = Path(migration_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    with locked(mdir):
        p = paths(mdir)
        if p["graph"].exists():
            raise ValidationError(f"{p['graph']} exists; later changes go through apply", code="exists")
        proj = dict(draft.get("project") or {})
        proj.setdefault("name", None)
        proj.setdefault("legacy_tree_sha256", None)
        proj.setdefault("plugin_version", ac.PLUGIN_VERSION)
        proj.setdefault("created_at", _now())
        g = {"schema": SCHEMA, "revision": 1, "project": proj,
             "steps": [_norm_step(s, "agent", 1) for s in draft.get("steps", [])],
             "datasets": [_norm_dataset(d, "agent", 1) for d in draft.get("datasets", [])],
             "edges": [_norm_edge(e, "agent", 1) for e in draft.get("edges", [])],
             "questions": [_norm_question(q, "agent", 1) for q in draft.get("questions", [])],
             "invariants": draft.get("invariants", []), "legacy_output_dirs": draft.get("legacy_output_dirs", []),
             "ladder": None, "acks": {"agent_seq": 0}, "updated": {"rev": 1, "by": "agent", "at": _now()}}
        for k, v in draft.items():
            if k not in g and k not in ("schema", "revision", "acks", "updated"):
                g[k] = v
        derive_structure(g)
        errs = validate_graph(g)
        if errs:
            raise ValidationError("draft is invalid: " + "; ".join(errs[:8]), code="invalid_graph", details=errs)
        _write_graph(mdir, g)
        if not p["edits"].exists() or p["edits"].stat().st_size == 0:
            _append_edits(mdir, [{"seq": 1, "rev": 1, "base_rev": 0, "actor": "agent", "channel": "cli",
                                  "at": g["updated"]["at"], "result": "applied",
                                  "op": {"type": "init", "draft_sha256": ac.sha256_bytes(ac.cjson(draft)),
                                         "counts": {k: len(g[k]) for k in ("steps", "datasets", "edges",
                                                                           "questions")}}}])
        _record_head(mdir, 1)
    return {"revision": 1}


def apply_retry(mdir: str | os.PathLike, ops_fn, actor: str, attempts: int = 20) -> dict:
    """For non-interactive writers (ack, ingest-verify, derive): re-read and retry on conflict."""
    last = None
    for _ in range(attempts):
        g = load_graph(mdir)
        try:
            return apply(mdir, ops_fn(g), actor, g["revision"])
        except ConflictError as exc:
            last = exc
            time.sleep(0.02)
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------------------------------------------
# Unacked user edits, obligations, diff (02 §4.5, §4.7)
# ---------------------------------------------------------------------------------------------------------------
def unacked_user_edits(migration_dir: str | os.PathLike) -> list[dict]:
    """User edit records with seq > acks.agent_seq (stdlib; imported by the Stop and UserPromptSubmit hooks)."""
    mdir = Path(migration_dir)
    g = ac.load_json(paths(mdir)["graph"])
    cursor = int((g.get("acks") or {}).get("agent_seq", 0))
    recs = _tail_edits(mdir, lambda r: int(r.get("seq", 0)) <= cursor)
    return [r for r in recs if r.get("actor") == "user"]


def plan_approved(graph: dict, step_id: str) -> bool:
    """True when the step's plan carries a user approval (the hook gate for pipeline/<step>.py writes)."""
    for s in graph.get("steps", []):
        if s.get("id") == step_id:
            return bool(((s.get("plan") or {}).get("approved") or {}).get("by"))
    return False


def _target(op: dict) -> str:
    return op.get("id") or (",".join(op["steps"]) if isinstance(op.get("steps"), list) else op.get("steps")) or ""


def obligation(rec: dict, graph: dict | None = None) -> dict | None:
    op = rec.get("op") or {}
    t = op.get("type")
    eff = rec.get("effect") or {}
    tgt = _target(op)
    base = {"seq": rec.get("seq"), "op": t, "target": tgt}

    def ob(code: str, text: str) -> dict:
        return {**base, "code": code, "obligation": text}

    if t in ("rename", "confirm", "submit", "ack"):
        return None
    if t == "reject_node":
        return ob("recheck_rejected", f"re-check {tgt}; if the code evidence is strong, ask the user why it is wrong")
    if t == "add_note":
        return ob("read_note", f"read the note on {tgt}: \"{op.get('text', '')[:160]}\"; if it contradicts the "
                               f"evidence, open a question")
    if t == "add_edge":
        return ob("find_edge_evidence", f"find file:line evidence for {op.get('from')} -{op.get('kind')}-> "
                                        f"{op.get('to')}; if none, model it as a manual step or ask")
    if t == "reject_edge":
        refs = eff.get("code_evidence") or []
        if refs:
            return ob("ask_rejected_edge", f"the code at {', '.join(refs)} supports {tgt}; ask the user whether it is "
                                           f"really not used (conflict)")
        return ob("drop_edge", f"{tgt} rejected and has no code evidence; re-derive and continue")
    if t == "add_step":
        return ob("investigate_new_step", f"investigate new step {tgt} (\"{op.get('title', '')}\"): grep code and "
                                          f"notes; classify as found / manual / new behaviour; if it feeds an in-scope "
                                          f"final, ask: flag off by default (parity kept) or accept a known difference")
    if t == "add_dataset":
        return ob("investigate_new_dataset", f"investigate new dataset {tgt}: find its producer and evidence")
    if t in ("delete_step", "set_scope"):
        if eff.get("requires_confirmation"):
            aff = ", ".join(eff.get("datasets", []) + [f"final {f}" for f in eff.get("finals", [])]) or "none"
            return ob("confirm_scope_change", f"ask the user (AskUserQuestion) to confirm dropping {tgt}; it affects: "
                                              f"{aff}; then resolve_confirmation citing the answer")
        scope = op.get("scope", "drop")
        if scope == "keep_legacy":
            return ob("bridge_step", f"{tgt} is kept legacy: its legacy outputs become hash-pinned inputs (bridge); "
                                     f"record the decision in decisions.md")
        if scope == "migrate":
            kind = eff.get("from_kind")
            miss = eff.get("missing_inputs") or []
            rewire = ""
            if miss:
                rewire = ("; rewire inputs that have no file to datasets that exist (e.g. via equivalent_to: add_edge "
                          "to the equivalent + reject_edge on the old read) or ask the user: " +
                          ", ".join(f"{m['dataset']}" + (f" -> {'/'.join(m['equivalents'])}" if m["equivalents"]
                                                         else " (no equivalent)") for m in miss))
            if kind == "manual":
                qs = []
                if graph is not None:
                    qs = sorted(q["id"] for q in graph.get("questions", []) if q.get("status") == "open"
                                and q.get("kind") == "manual_step" and tgt in _question_nodes(q))
                rec = (f"; the user chose Automate on the page but {', '.join(qs)} is still open: record that choice "
                       f"(confirm it with one AskUserQuestion, recommended option = automate, and answer {qs[0]} "
                       f"with channel interview + verbatim), do not re-ask the policy from scratch") if qs else ""
                return ob("automate_manual_step", f"automate manual step {tgt}: derive its rule in params.yaml (an "
                                                  f"interview answer), plan the step, verify it against the hand-made "
                                                  f"legacy file{rec}{rewire}")
            return ob("migrate_step", f"{tgt} is back in scope: plan and migrate it{rewire}")
        return ob("scope_dropped", f"{tgt} dropped by the user; record it in decisions.md")
    if t == "answer_question":
        return ob("apply_answer", f"write params.yaml (source: answer:{tgt}) and decisions.md for answer "
                                  f"{op.get('value')!r}; update evidence; re-derive the ladder")
    if t == "dismiss_question":
        return ob("question_dismissed", f"{tgt} dismissed by the user: record why in decisions.md")
    if t == "set_reference":
        return ob("check_reference", f"check kind/CRS/grid/schema plausibility of the reference for {tgt}; if "
                                     f"implausible, ask")
    if t == "waive_verify":
        return ob("record_waiver", f"add the waiver of {tgt} to known-differences.md; the report shows it")
    if t == "approve_plan":
        return ob("plan_approved", f"plan approved for {tgt}: generate step modules in topological order")
    if t == "set_field":
        return ob("field_changed", f"{tgt}.{op.get('field')} set by the user; take it into account")
    return ob("review_edit", f"review {t} on {tgt}")


def diff(migration_dir: str | os.PathLike, *, since_rev: int | None = None, since_seq: int | None = None,
         user_only: bool = False, upto_seq: int | None = None) -> dict:
    mdir = Path(migration_dir)
    g = load_graph(mdir)
    recs = read_edits(mdir)
    if since_rev is not None:
        recs = [r for r in recs if r.get("rev", 0) > since_rev or (r.get("rev") == since_rev and
                                                                   r.get("op", {}).get("type") == "submit")]
    if since_seq is not None:
        recs = [r for r in recs if r["seq"] > since_seq]
    if upto_seq is not None:
        recs = [r for r in recs if r["seq"] <= upto_seq]
    if user_only:
        recs = [r for r in recs if r.get("actor") == "user"]
    obligations, confirm = [], []
    for r in recs:
        if r.get("actor") != "user":
            continue
        o = obligation(r, g)
        if o:
            obligations.append(o)
        if (r.get("effect") or {}).get("requires_confirmation"):
            confirm.append(_target(r["op"]))
    still = sorted(s["id"] for s in g["steps"] if s.get("requires_confirmation"))
    edits = [r for r in recs if (r.get("op") or {}).get("type") != "submit"]
    submitted = [{"seq": r["seq"], "rev": r.get("rev"), "message": r["op"].get("message")} for r in recs
                 if (r.get("op") or {}).get("type") == "submit"]
    revs = [r.get("rev", 0) for r in recs]
    return {"schema": DIFF_SCHEMA, "from_rev": since_rev if since_rev is not None else (min(revs) - 1 if revs else
                                                                                       g["revision"]),
            "to_rev": g["revision"], "ops": edits, "submitted": submitted, "obligations": obligations,
            "requires_confirmation": sorted(set(confirm) & set(still)) if still else sorted(set(confirm)),
            "pending_confirmation": still, "ack_seq": recs[-1]["seq"] if recs else None,
            "agent_seq": (g.get("acks") or {}).get("agent_seq", 0)}


def hook_summary(migration_dir: str | os.PathLike, max_lines: int = 12) -> str:
    """Text for the UserPromptSubmit hook (02 §4.7); '' when there is nothing unacked."""
    mdir = Path(migration_dir)
    recs = unacked_user_edits(mdir)
    if not recs:
        return ""
    g = ac.load_json(paths(mdir)["graph"])
    cursor = (g.get("acks") or {}).get("agent_seq", 0)
    revs = [r.get("rev", 0) for r in recs]
    edits = [r for r in recs if (r.get("op") or {}).get("type") != "submit"]
    subs = len(recs) - len(edits)
    head = (f"graph changed (rev {min(revs) - 1}→{g.get('revision')}, {len(edits)} user edit"
            f"{'s' if len(edits) != 1 else ''}, {subs} submitted):")
    lines = [head]
    for r in edits[: max_lines - 3]:
        op = r.get("op") or {}
        t = op.get("type")
        s = f"  {t} {_target(op)}"
        if t == "set_scope":
            s += f" → {op.get('scope')}"
        if t in ("rename",):
            s += f" → \"{op.get('title', '')[:40]}\""
        if (r.get("effect") or {}).get("requires_confirmation"):
            s += " (needs confirmation)"
        lines.append(s)
    if len(edits) > max_lines - 3:
        lines.append(f"  … and {len(edits) - (max_lines - 3)} more")
    lines.append(f"Run graph.py diff --since-seq {cursor}, act per the edit table (GRAPH_OPS.md), then "
                 f"graph.py ack {recs[-1]['seq']}.")
    return "\n".join(lines[:max_lines])


# ---------------------------------------------------------------------------------------------------------------
# derive (ladder projection via report/ladder.py), ingest-verify, render-plan, protected
# ---------------------------------------------------------------------------------------------------------------
def _load_ladder():
    p = Path(__file__).resolve().parents[1] / "report" / "ladder.py"
    if not p.is_file():
        return None, f"{p} not found"
    try:
        spec = importlib.util.spec_from_file_location("axis_ladder", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        if not hasattr(mod, "compute"):
            return None, "ladder.py has no compute()"
        return mod, None
    except Exception as exc:  # degrade: leave the projection untouched
        return None, f"ladder.py import failed: {exc.__class__.__name__}: {exc}"


def derive(migration_dir: str | os.PathLike) -> dict:
    mdir = Path(migration_dir)
    require_intact(mdir)
    note = None
    ladder, why = _load_ladder()
    res = None
    if ladder is not None:
        try:
            res = ladder.compute(mdir)
        except Exception as exc:
            why = f"ladder.compute failed: {exc.__class__.__name__}: {exc}"
            res = None
    if res is None:
        note = f"ladder projection left untouched ({why})"

    def build(g: dict) -> list[dict]:
        ops: list[dict] = []
        for s in g["steps"]:
            mod = (s.get("new") or {}).get("module")
            sha = None
            if mod:
                f = mdir / mod
                sha = ac.sha256_file(f) if f.is_file() else None
            if (s.get("new") or {}).get("code_sha256") != sha:
                ops.append({"type": "set_field", "id": s["id"], "field": "new.code_sha256", "value": sha})
        if res is not None:
            pipe = res.get("pipeline") or {}
            lad = json.loads(json.dumps({
                "project": pipe.get("rung"), "suffix": pipe.get("suffix"), "blocking": pipe.get("blockers", []),
                "counts": pipe.get("counts"), "run_id": res.get("run_id"), "status_line": res.get("status_line")},
                default=str))
            cur = {k: v for k, v in (g.get("ladder") or {}).items() if k != "derived_rev"}
            if cur != lad:
                ops.append({"type": "set_ladder", "id": "graph", "ladder": lad})
            steps = res.get("steps") or {}
            for s in g["steps"]:
                st = steps.get(s["id"])
                if st is not None:
                    sl = json.loads(json.dumps({
                        "rung": st.get("rung"), "disposition": st.get("disposition"), "caps": st.get("caps", []),
                        "annotations": st.get("annotations", []), "run_id": res.get("run_id")}, default=str))
                    if s.get("ladder") != sl:
                        ops.append({"type": "set_ladder", "id": s["id"], "ladder": sl})
        return ops

    g = load_graph(mdir)
    if not build(g):
        return {"revision": g["revision"], "changed": False, "note": note}
    out = apply_retry(mdir, build, "derive")
    return {"revision": out["revision"], "changed": True, "note": note}


def _rel(mdir: Path, p: Path) -> str:
    return os.path.relpath(p.resolve(), mdir.resolve()).replace(os.sep, "/")


def ingest_verify(migration_dir: str | os.PathLike, run_id: str) -> dict:
    """Project runs/<id>/verify/compare/*.json + verdict.json into datasets[].verify and steps[].verify."""
    mdir = Path(migration_dir)
    require_intact(mdir)
    vdir = mdir / "runs" / run_id / "verify"
    cdir = vdir / "compare"
    if not cdir.is_dir():
        raise ValidationError(f"{cdir} not found", code="no_compare")
    compares: dict[str, dict] = {}
    for f in sorted(cdir.glob("*.json")):
        c = ac.load_json(f)
        ds = c.get("dataset_id")
        if not ds:
            raise ValidationError(f"{f.name}: no dataset_id", code="bad_compare")
        if c.get("run_id") not in (None, run_id):
            raise ValidationError(f"{f.name}: run_id {c.get('run_id')} != {run_id}", code="bad_compare")
        if c.get("verdict") not in VERDICTS:
            raise ValidationError(f"{f.name}: verdict {c.get('verdict')!r} unknown", code="bad_compare")
        compares[ds] = {"doc": c, "path": f}
    verdict = ac.load_json(vdir / "verdict.json") if (vdir / "verdict.json").is_file() else {}
    visual = ac.load_json(vdir / "visual-review.json") if (vdir / "visual-review.json").is_file() else {}
    flags: dict[str, list[dict]] = {}
    for fnd in visual.get("findings", []) or []:
        if fnd.get("answer") == "yes" and fnd.get("effect") == "flag":
            flags.setdefault(fnd.get("dataset_id"), []).append(fnd)

    def plots_for(ds: str, c: dict) -> list[str]:
        out: list[str] = []
        pj = c.get("plots")
        if isinstance(pj, str) and pj:
            pjp = (vdir / pj) if not pj.startswith("runs/") else (mdir / pj)
            if pjp.is_file():
                try:
                    doc = ac.load_json(pjp)
                except Exception:
                    doc = {}
                items = (doc.get("png") or doc.get("plots") or []) if isinstance(doc, dict) else doc
                for it in items if isinstance(items, list) else []:
                    name = it if isinstance(it, str) else ((it.get("name") or it.get("path") or it.get("file"))
                                                           if isinstance(it, dict) else None)
                    if isinstance(name, str) and name.endswith(".png"):
                        cand = [pjp.parent / name, vdir / name, mdir / name]
                        hit = next((x for x in cand if x.is_file()), None)
                        if hit:
                            out.append(_rel(mdir, hit))
        if not out:
            out = [_rel(mdir, p) for p in sorted((vdir / "plots" / ds).glob("*.png"))]
        return sorted(set(out))

    def build(g: dict) -> list[dict]:
        dsets = {d["id"]: d for d in g["datasets"]}
        steps = {s["id"]: s for s in g["steps"]}
        unknown = sorted(set(compares) - set(dsets))
        if unknown:
            raise ValidationError(f"compare results for datasets not in the graph: {unknown}", code="bad_compare")
        vmap = {ds: c["doc"]["verdict"] for ds, c in compares.items()}
        nonpass = {ds for ds, v in vmap.items() if v not in ac.PASS_STATES}

        def upstream(ds: str) -> set[str]:  # datasets strictly upstream through producers
            seen: set[str] = set()
            todo = [ds]
            while todo:
                x = todo.pop()
                pb = dsets[x].get("produced_by")
                if not pb:
                    continue
                for i in steps[pb["step"]].get("inputs", []):
                    if i not in seen:
                        seen.add(i)
                        todo.append(i)
            return seen

        ops = []
        dblame: dict[str, str | None] = {}
        for ds, c in sorted(compares.items()):
            doc = c["doc"]
            rc = list(doc.get("reason_codes") or [])
            blame = None
            if ds in nonpass:
                inherited = any(r.startswith("INHERITED_FROM:") for r in rc) or bool(upstream(ds) & nonpass)
                blame = "inherited" if inherited else "origin"
            dblame[ds] = blame
            fl = flags.get(ds)
            ops.append({"type": "set_verify", "id": ds, "verify": {
                "run_id": run_id, "verdict": doc["verdict"], "reason_codes": rc,
                "report": _rel(mdir, c["path"]), "report_sha256": ac.sha256_file(c["path"]),
                "plots": plots_for(ds, doc),
                "visual_flag": None if not fl else {
                    "finding_ids": sorted({f.get("finding_id") for f in fl if f.get("finding_id")}),
                    "categories": sorted({f.get("category") for f in fl if f.get("category")}),
                    "question_ids": sorted({f.get("question_id") for f in fl if f.get("question_id")})},
                "blame": blame}})
        vsteps = verdict.get("steps") or {}
        touched_steps = sorted({dsets[ds]["produced_by"]["step"] for ds in compares if dsets[ds].get("produced_by")}
                               | {s for s in vsteps if s in steps})
        for sid in touched_steps:
            mine = [ds for ds in compares if (dsets[ds].get("produced_by") or {}).get("step") == sid]
            vs = vsteps.get(sid) or {}
            sv = vs.get("verdict") or ac.worst(vmap[d] for d in mine)
            blame = vs.get("blame")
            if blame not in STEP_BLAME:
                bad = [d for d in mine if dblame.get(d)]
                blame = None if not bad else ("own" if any(dblame[d] == "origin" for d in bad) else "inherited")
            rcs = vs.get("reason_codes")
            if rcs is None:
                rcs = sorted({r for d in mine for r in compares[d]["doc"].get("reason_codes") or []})
            ops.append({"type": "set_verify", "id": sid, "verify": {
                "run_id": run_id, "verdict": sv, "reason_codes": rcs, "blame": blame, "datasets": sorted(mine),
                "mode": verdict.get("mode")}})
        if verdict.get("pipeline"):
            pass  # pipeline verdict is read by ladder.py from verdict.json; the graph keeps per-node projections
        return ops

    out = apply_retry(mdir, build, "verify")
    return {"revision": out["revision"], "datasets": sorted(compares), "run_id": run_id}


def render_plan(migration_dir: str | os.PathLike) -> Path:
    """Write migration/plan.md; its sha256 is what `approve_plan` cites as `plan_sha256` once the user saw it."""
    mdir = Path(migration_dir)
    require_intact(mdir)
    out = paths(mdir)["plan"]
    ac.atomic_write_text(out, plan_text(load_graph(mdir)))
    return out


def plan_text(g: dict) -> str:
    steps = {s["id"]: s for s in g["steps"]}
    order = topo_steps(g)
    L = [f"# Migration plan — {g['project'].get('name') or 'project'}", "",
         f"Rendered by `graph.py render-plan` from `graph.json` revision {g['revision']}. The plan lives in "
         f"`graph.json` (`steps[].plan`); edit it with `graph.py apply set_plan`, never here.", ""]
    migr = [s for s in order if steps[s].get("scope") == "migrate"]
    n_plan = sum(1 for s in migr if steps[s].get("plan"))
    n_ok = sum(1 for s in migr if ((steps[s].get("plan") or {}).get("approved") or {}).get("by"))
    L += [f"**Approval:** {n_ok} of {len(migr)} migrate steps approved ({n_plan} have a plan).", ""]
    L += ["| Step | Kind | Scope | Plan | Approved |", "|---|---|---|---|---|"]
    for sid in order:
        s = steps[sid]
        appr = (s.get("plan") or {}).get("approved") or {}
        L.append(f"| `{sid}` | {s.get('kind')} | {s.get('scope')}"
                 f"{' (' + s['scope_reason'] + ')' if s.get('scope_reason') else ''} | "
                 f"{'yes' if s.get('plan') else 'no'} | "
                 f"{('yes, ' + appr.get('by', '') + ' via ' + str(appr.get('channel'))) if appr.get('by') else 'no'} |")
    L.append("")
    for sid in migr:
        s = steps[sid]
        p = s.get("plan")
        L += [f"## {sid} · {s.get('title')}", ""]
        leg = s.get("legacy") or {}
        if leg.get("source"):
            lines = ", ".join(f"{a}-{b}" if isinstance(a, int) else str(a) for a, b in
                              [(x[0], x[1]) if isinstance(x, list) else (x, x) for x in leg.get("lines", [])])
            L += [f"Legacy: `{leg['source']}`{(' lines ' + lines) if lines else ''}", ""]
        L += [f"Inputs: {', '.join('`' + i + '`' for i in s.get('inputs', [])) or 'none'}  ",
              f"Outputs: {', '.join('`' + o + '`' for o in s.get('outputs', [])) or 'none'}", ""]
        if not p:
            L += ["_No plan yet._", ""]
            continue
        if p.get("summary"):
            L += [str(p["summary"]), ""]
        if p.get("legacy_ops"):
            L += ["**Legacy operations**", ""]
            for lo in p["legacy_ops"]:
                st = lo.get("settings")
                L.append(f"- `{lo.get('tool')}` at `{lo.get('ref')}`"
                         f"{' — ' + json.dumps(st, sort_keys=True, ensure_ascii=False) if st else ''}")
            L.append("")
        if p.get("oss"):
            L += ["**Open-source operations**", ""]
            for o in p["oss"]:
                refs = ", ".join(o.get("skill_refs") or [])
                L.append(f"- `{o.get('op')}`: {o.get('why', '')}{' (' + refs + ')' if refs else ''}")
            L.append("")
        if p.get("params"):
            L += ["**Parameters** (values in params.yaml): " + ", ".join(f"`{x}`" for x in p["params"]), ""]
        if p.get("outputs_checked"):
            L += ["**Outputs checked**", "", "| Dataset | Family | Measure | Checks | Tolerance ids |",
                  "|---|---|---|---|---|"]
            for oc in p["outputs_checked"]:
                L.append(f"| `{oc.get('dataset')}` | {oc.get('family', '')} | {oc.get('measure', '')} | "
                         f"{', '.join(oc.get('checks') or [])} | {', '.join(oc.get('tolerance_ids') or []) or 'exact'} |")
            L.append("")
        if p.get("risks"):
            L += ["**Risks**", ""]
            for r in p["risks"]:
                L.append(f"- {r.get('skill_ref', '')}: {r.get('risk', '')}"
                         f"{' (evidence: ' + str(r['evidence']) + ')' if r.get('evidence') else ''}")
            L.append("")
        iso = p.get("isolation")
        if iso:
            L += [f"**Isolation:** {'possible' if iso.get('possible') else 'not possible'}; legacy inputs: "
                  f"{', '.join(iso.get('legacy_inputs') or []) or 'none'}; missing: "
                  f"{', '.join(iso.get('missing') or []) or 'none'}", ""]
        appr = p.get("approved") or {}
        L += [f"**Approved:** {('by ' + appr['by'] + ' at ' + str(appr.get('at')) + ' via ' + str(appr.get('channel'))) if appr.get('by') else 'not yet'}", ""]
    other = [s for s in order if steps[s].get("scope") != "migrate"]
    if other:
        L += ["## Not migrated", ""]
        for sid in other:
            s = steps[sid]
            L.append(f"- `{sid}` · {s.get('title')}: {s.get('scope')}"
                     f"{' (' + s['scope_reason'] + ')' if s.get('scope_reason') else ''}")
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def protected(migration_dir: str | os.PathLike) -> dict:
    """Write migration/.axis/protected.json: the protected path set R (DESIGN §5)."""
    mdir = Path(migration_dir)
    require_intact(mdir)
    g = load_graph(mdir)
    prot: set[str] = set()
    inputs: set[str] = set()
    for d in g["datasets"]:
        leg = d.get("legacy") or {}
        lp = leg.get("path")
        if not lp:
            continue
        side = [s for s in leg.get("sidecars") or [] if isinstance(s, str)]
        if d.get("role") == "input" and (d.get("role_lineage") or d.get("role")) in ("input", "unused"):
            inputs.add(lp)
            inputs.update(side)
        else:
            prot.add(lp)
            prot.update(side)
    inv = paths(mdir)["inventory"]
    if inv.is_file():
        try:
            doc = ac.load_json(inv)
            for d in doc.get("datasets", []):
                lp = d.get("legacy_path")
                if lp and (d.get("reference_candidate") or d.get("role") in ("intermediate", "final")):
                    prot.add(lp)
            for f in doc.get("files", []):
                if f.get("sidecar_of") in prot:
                    prot.add(f["path"])
        except Exception:
            pass
    inputs -= prot  # a path both protected and input stays protected (fail closed)
    out = {"schema": PROTECTED_SCHEMA, "graph_revision": g["revision"], "legacy_root": g["project"]["legacy_root"],
           "rule": "every path under legacy_root is protected except input_exceptions; paths lists the "
                   "intermediate/final/reference_candidate files explicitly",
           "root_protected": True, "paths": sorted(prot), "input_exceptions": sorted(inputs),
           "legacy_output_dirs": sorted(g.get("legacy_output_dirs") or [])}
    ac.dump_json(paths(mdir)["protected"], out)
    return out


# ---------------------------------------------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------------------------------------------
def show_text(g: dict, mdir: Path) -> str:
    qopen: dict[str, int] = {}
    for q in g["questions"]:
        if q.get("status") == "open":
            for n in _question_nodes(q):
                qopen[n] = qopen.get(n, 0) + 1
    L = [f"graph {g['project'].get('name') or ''} revision {g['revision']} (updated by {g['updated']['by']} at "
         f"{g['updated']['at']}); {len(g['steps'])} steps, {len(g['datasets'])} datasets, {len(g['edges'])} edges, "
         f"{sum(1 for q in g['questions'] if q.get('status') == 'open')} open questions"]
    lad = g.get("ladder") or {}
    if lad.get("project"):
        L.append(f"ladder (projection, rev {lad.get('derived_rev')}): {lad.get('project')}")
    L.append("")
    L.append("steps:")
    for sid in topo_steps(g):
        s = next(x for x in g["steps"] if x["id"] == sid)
        v = (s.get("verify") or {}).get("verdict") or "-"
        rc = " NEEDS CONFIRMATION" if s.get("requires_confirmation") else ""
        L.append(f"  {sid:<28} {s['kind']:<8} {s['scope']:<11} review={s['review']:<9} verdict={v}"
                 f"{' q=' + str(qopen[sid]) if qopen.get(sid) else ''}{rc}")
        L.append(f"      in:  {', '.join(s['inputs']) or '-'}")
        L.append(f"      out: {', '.join(s['outputs']) or '-'}")
    L.append("datasets:")
    for d in g["datasets"]:
        v = (d.get("verify") or {}).get("verdict") or "-"
        lp = (d.get("legacy") or {}).get("path") or "-"
        L.append(f"  {d['id']:<30} {d['kind']:<7} {d['role']:<12} {lp:<40} verdict={v}"
                 f"{' q=' + str(qopen[d['id']]) if qopen.get(d['id']) else ''}")
    oq = [q for q in g["questions"] if q.get("status") == "open"]
    if oq:
        L.append("open questions:")
        for q in oq:
            L.append(f"  {q['id']} [{q.get('kind')}] on {', '.join(_question_nodes(q))}: {q.get('text')}")
    try:
        un = unacked_user_edits(mdir)
    except Exception as exc:
        un = []
        L.append(f"(unacked check failed: {exc})")
    if un:
        L.append(f"unacked user edits: {len(un)} (last seq {un[-1]['seq']}); run graph.py diff and ack")
    return "\n".join(L)


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------
def _mdir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    found = ac.find_migration_dir(Path.cwd())
    if found is None:
        raise ValidationError("no migration/graph.json above the current directory; pass the migration dir",
                              code="no_graph")
    return found


def _print(obj: Any, as_json: bool, text: str | None = None) -> None:
    if as_json:
        sys.stdout.write(json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write((text if text is not None else json.dumps(obj, sort_keys=True, indent=1,
                                                                   ensure_ascii=False)) + "\n")


def read_ops_file(path: str) -> str:
    """An --ops file this user owns and nobody else may write (B7: a stale /tmp/q05.json of another project, owned
    by another account, was read and applied). Prefer `--ops -` (stdin)."""
    try:
        return ac.read_own_text(path, "--ops")
    except ac.ForeignInputError as exc:
        raise ValidationError(f"{exc} (for graph.py: --ops -)", code="ops_not_owned")
    except OSError as exc:
        raise ValidationError(f"--ops {path}: {exc.strerror or exc}", code="bad_ops")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="graph.py", description="Single writer of migration/graph.json (GRAPH_OPS.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create revision 1 from a lineage draft")
    p.add_argument("migration")
    p.add_argument("--from", dest="draft", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("apply", help="apply ops as one revision (actors agent|verify|derive; never user)")
    p.add_argument("migration", nargs="?")
    p.add_argument("--actor", required=True)
    p.add_argument("--base-rev", type=int, required=True)
    p.add_argument("--ops", required=True, help="JSON file with a list of ops, or - for stdin")
    p.add_argument("--channel", choices=CHANNELS)
    p.add_argument("--verbatim")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("migration", nargs="?")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("diff", help="edits since a revision (or seq) with the agent's obligations")
    p.add_argument("migration", nargs="?")
    g_ = p.add_mutually_exclusive_group(required=True)
    g_.add_argument("--since", type=int, help="revision")
    g_.add_argument("--since-seq", type=int)
    p.add_argument("--user-only", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("ack", help="move acks.agent_seq: graph.py ack [migration] <seq>")
    p.add_argument("args", nargs="+")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("unacked", help="user edits the agent has not acked")
    p.add_argument("migration", nargs="?")
    p.add_argument("--json", action="store_true")
    for name, hlp in (("derive", "ladder projection from report/ladder.py + new.code_sha256"),
                      ("validate", "schema, references, acyclicity, params.yaml refs, edit log"),
                      ("render-plan", "write migration/plan.md from steps[].plan"),
                      ("protected", "write migration/.axis/protected.json")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("migration", nargs="?")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("check-integrity", help="exit 2 (GRAPH_EDITED_OUTSIDE_APPLY) if graph.json or the edit log "
                                               "changed outside graph.py")
    p.add_argument("migration", nargs="?")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("ingest-verify", help="project runs/<id>/verify into datasets[].verify and steps[].verify")
    p.add_argument("migration", nargs="?")
    p.add_argument("--run", required=True)
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "init":
            draft = ac.load_json(a.draft)
            res = init(a.migration, draft)
            _print(res, a.json, f"graph initialised at revision {res['revision']}")
        elif a.cmd == "apply":
            if a.actor == "user":
                raise AclError("--actor user is refused on the CLI: user edits come only from the graph page",
                               code="user_actor_refused")
            if a.actor not in CLI_ACTORS:
                raise ValidationError(f"--actor must be one of {CLI_ACTORS}", code="bad_actor")
            raw = sys.stdin.read() if a.ops == "-" else read_ops_file(a.ops)
            try:
                ops = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValidationError(f"--ops is not JSON: {exc}", code="bad_ops")
            if isinstance(ops, dict):
                ops = ops.get("ops", [ops])
            res = apply(_mdir(a.migration), ops, a.actor, a.base_rev, channel=a.channel, verbatim=a.verbatim)
            _print(res, a.json, f"applied {len(ops)} op(s): revision {res['revision']}, seq "
                                f"{res['seqs'][0]}..{res['seqs'][-1]}")
        elif a.cmd == "check-integrity":
            res = check_integrity(_mdir(a.migration))
            _print(res, a.json, "graph integrity ok" if res["ok"] else
                   "\n".join(f"{IntegrityError.code}: {x}" for x in res["problems"]))
            return 0 if res["ok"] else EXIT["invalid"]
        elif a.cmd == "show":
            m = _mdir(a.migration)
            require_intact(m)
            g = load_graph(m)
            _print(g, a.json, None if a.json else show_text(g, m))
        elif a.cmd == "diff":
            require_intact(_mdir(a.migration))
            d = diff(_mdir(a.migration), since_rev=a.since, since_seq=a.since_seq, user_only=a.user_only)
            if a.json:
                _print(d, True)
            else:
                L = [f"edits rev {d['from_rev']}→{d['to_rev']}: {len(d['ops'])} op(s)"]
                for r in d["ops"]:
                    L.append(f"  seq {r['seq']} rev {r['rev']} {r['actor']}: {r['op'].get('type')} "
                             f"{_target(r['op'])}")
                if d["obligations"]:
                    L.append("obligations:")
                    L += [f"  seq {o['seq']} [{o['code']}] {o['obligation']}" for o in d["obligations"]]
                if d["requires_confirmation"]:
                    L.append("requires confirmation: " + ", ".join(d["requires_confirmation"]))
                if d["ack_seq"]:
                    L.append(f"when done: graph.py ack {d['ack_seq']}")
                _print(d, False, "\n".join(L))
        elif a.cmd == "ack":
            if len(a.args) > 2:
                raise ValidationError("usage: graph.py ack [migration] <seq>", code="bad_args")
            m = _mdir(a.args[0] if len(a.args) == 2 else None)
            try:
                seq = int(a.args[-1])
            except ValueError:
                raise ValidationError("seq must be an integer", code="bad_args")
            res = apply_retry(m, lambda g: [{"type": "ack", "seq": seq}], "agent")
            _print(res, a.json, f"acked up to seq {seq} (revision {res['revision']})")
        elif a.cmd == "unacked":
            require_intact(_mdir(a.migration))
            recs = unacked_user_edits(_mdir(a.migration))
            _print(recs, a.json, "\n".join(f"seq {r['seq']}: {r['op'].get('type')} {_target(r['op'])}"
                                           for r in recs) or "no unacked user edits")
        elif a.cmd == "derive":
            require_intact(_mdir(a.migration))
            res = derive(_mdir(a.migration))
            _print(res, a.json, f"derive: revision {res['revision']}{'' if res['changed'] else ' (no change)'}"
                                f"{'; ' + res['note'] if res.get('note') else ''}")
        elif a.cmd == "ingest-verify":
            require_intact(_mdir(a.migration))
            res = ingest_verify(_mdir(a.migration), a.run)
            _print(res, a.json, f"ingested {len(res['datasets'])} compare result(s) from {a.run}: revision "
                                f"{res['revision']}")
        elif a.cmd == "validate":
            res = validate_full(_mdir(a.migration))
            _print(res, a.json, "\n".join([f"ERROR {e}" for e in res["errors"]] +
                                          [f"warning {w}" for w in res["warnings"]]) or "graph valid")
            return EXIT["invalid"] if res["errors"] else 0
        elif a.cmd == "render-plan":
            out = render_plan(_mdir(a.migration))
            sha = ac.sha256_file(out).split(":")[-1]
            _print({"path": str(out), "sha256": sha}, a.json,
                   f"wrote {out}\nplan.md sha256 {sha} (show plan.md to the user; when they approve it, apply "
                   f"approve_plan with \"plan_sha256\": \"{sha}\")")
        elif a.cmd == "protected":
            res = protected(_mdir(a.migration))
            _print(res, a.json, f"wrote .axis/protected.json: {len(res['paths'])} protected paths, "
                                f"{len(res['input_exceptions'])} input exceptions (graph rev {res['graph_revision']})")
        return 0
    except GraphError as exc:
        if getattr(a, "json", False):
            sys.stdout.write(json.dumps(exc.to_json(), sort_keys=True) + "\n")
        sys.stderr.write(f"graph.py: {exc.code}: {exc.message}\n")
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
