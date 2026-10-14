#!/usr/bin/env python3
"""ladder.py — the only producer of status rungs (DESIGN §3, §4.7; 07 §3.4, §4.3; CONTRACTS §9).

STDLIB ONLY: the Stop gate and SessionStart hooks import this with `python3 -I`.

    compute(migration_dir, run_id=None) -> {pipeline{rung, suffix, headline, blockers[], counts, ...},
                                            steps{id: {rung, disposition, caps[], annotations[], datasets[]}},
                                            datasets{id: {...}}, integrity[], run_id, evidence_id, status_line}

Rules in one place:
- A rung is computed from the sealed evidence bundle (runs/<id>/verify/evidence*.json) every time; no stored or
  declared status is ever read as a rung (the manifest's `eligible_for` and verdict.json are ignored here).
- Integrity (07 §4.3): bundle schema, evidence id, every object's file/hash/id, compare binding (UNBOUND_COMPARE),
  compare states re-derived from their checks (STATE_RECOMPUTE_MISMATCH), legacy references and inputs re-hashed and
  no new `.aux.xml` in the legacy tree (LEGACY_REF_CHANGED). Any of these → EVIDENCE INCOMPLETE: no rung.
- Drift (live project files ≠ the run's snapshot): *_CHANGED_AFTER_RUN caps every step at sandbox-validated;
  CODE_CHANGED_AFTER_RUN withdraws target-ready.
- The rungs above target-ready (deployed, live-target-verified, customer-accepted) have no code path here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402
import visual_receipt as vrc  # noqa: E402

LADDER_VERSION = "ladder.v1"
EVIDENCE_SCHEMA = "axis-migrate.evidence.v1"
RUNGS = ac.RUNGS  # none → generated → sandbox-validated → reference-verified → target-ready
GREYED = ac.GREYED_RUNGS

# Object kinds (07 §3.2) + `aux` for supporting JSON (manifest, verdict, contract, plots.json, display.json,
# visual-review.json, snapshot inventory) and `inv`-free snapshot copies.
# `view`: a GeoLibre view project (skills/map/views.py) a view receipt cites; display only, so a changed one is
# VIEW_CHANGED (info: its receipts stop counting), never EVIDENCE INCOMPLETE.
FILE_KINDS = {"in", "ref", "out", "plot", "disp", "params", "tol", "graph", "dec", "kd", "lock", "aux", "view"}
TREE_KINDS = {"code", "tgt"}
JSON_KINDS = {"cmp", "scan", "tests"}
INLINE_KINDS = {"envrt"}
ENTRY_KINDS = {"dec-e", "kd-e"}
ELEMENT_KINDS = {"vis"}
ALL_KINDS = FILE_KINDS | TREE_KINDS | JSON_KINDS | INLINE_KINDS | ENTRY_KINDS | ELEMENT_KINDS
AMENDABLE_KINDS = {"vis", "view", "dec-e", "kd-e", "tgt"}

EVIDENCE_KEYS = {"schema", "parent_evidence_id", "amendment", "project", "plugin", "run", "sealed", "objects",
                 "records", "coverage", "secret_scan", "amended_at"}
OBJECT_KEYS = {"id", "kind", "path", "sha256", "size", "dataset", "step", "refs", "meta", "value", "entry",
               "finding_id", "name"}

# Dataset-level reason codes that mean "a person must decide" (orchestrator, 13 Oct): blocks reference-verified.
DECISION_CODES = {"TOLERANCE_UNSIGNED", "KD_UNSIGNED", "LEGACY_DEFECT_SUSPECTED"}
DISPOSITION_ORDER = ["EVIDENCE_INCOMPLETE", "FAILED", "EVALUATOR_ERROR", "BLOCKED", "NEEDS_DECISION", "NEEDS_REVIEW",
                     "NOT_COMPARED", "ACCEPTED_DIFFERENCE", "VERIFIED"]
STATE_TO_DISPOSITION = {"FAIL": "FAILED", "EVALUATOR_ERROR": "EVALUATOR_ERROR", "BLOCKED": "BLOCKED",
                        "NEEDS_DECISION": "NEEDS_DECISION", "NEEDS_REVIEW": "NEEDS_REVIEW",
                        "NOT_COMPARED": "NOT_COMPARED", "PASS_WITHIN_TOLERANCE": "VERIFIED", "PASS_EXACT": "VERIFIED"}
GLOBAL_PARAM_KINDS = {"grid", "cell_size", "scale", "crs"}
INCOMPLETE_CODES = {"EVIDENCE_FILE_MISSING", "EVIDENCE_HASH_MISMATCH", "EVIDENCE_SCHEMA_INVALID",
                    "EVIDENCE_ID_MISMATCH", "STATE_RECOMPUTE_MISMATCH", "UNBOUND_COMPARE", "LEGACY_REF_CHANGED",
                    "SECRET_IN_BUNDLE", "RECORDS_PARSE_MISMATCH", "EVIDENCE_CHAIN_BROKEN"}
CAPPING_DRIFT = {"tolerances": "TOLERANCE_CHANGED_AFTER_RUN", "params": "PARAMS_CHANGED_AFTER_RUN",
                 "graph": "GRAPH_CHANGED_AFTER_RUN", "lock": "LOCK_CHANGED_AFTER_RUN"}
INFO_DRIFT = {"decisions": "DECISIONS_CHANGED_AFTER_RUN", "known_differences": "KNOWN_DIFFERENCES_CHANGED_AFTER_RUN"}
SNAPSHOT_FILES = {"graph": "graph.json", "params": "params.yaml", "tolerances": "tolerances.yaml",
                  "decisions": "decisions.md", "known_differences": "known-differences.md", "lock": "uv.lock",
                  "inventory": "inventory.json"}
TARGET_REQUIRED = ["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9", "T10", "T13"]

SECRET_PATTERNS = [
    ("bearer", re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/\-]{20,}")),
    ("api_key_assignment", re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token)\b\s*[:=]\s*"
                                      r"['\"]?[A-Za-z0-9/+_\-]{12,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("databricks_token", re.compile(r"\bdapi[0-9a-f]{32}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36}\b")),
]
SECRET_PATTERN_NAMES = "bearer, api key/secret/password assignment, private key, dapi token, AWS key, sk-ant, gh token"
DATA_URI_RE = re.compile(r"data:[a-z/+\-]+;base64,[A-Za-z0-9+/=]+")


# ------------------------------------------------------------------------------------------------------------------
# Small helpers (shared with seal.py and report.py)
# ------------------------------------------------------------------------------------------------------------------
def hex_of(x: str | None) -> str | None:
    """'sha256:abc' / 'code:abc' / 'abc' → 'abc'."""
    if not x:
        return None
    return str(x).rsplit(":", 1)[-1].lower()


def tree_hex(root: Path) -> str:
    return hex_of(ac.tree_hash(root))


def entry_text(markdown: str, entry_id: str) -> str | None:
    """Text of the `## <entry_id> · …` block, LF-normalised and stripped (07 §3.2 dec-e / kd-e)."""
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    start = None
    pat = re.compile(r"^##\s+" + re.escape(entry_id) + r"(\s|$|·)")
    for i, ln in enumerate(lines):
        if start is None and pat.match(ln):
            start = i
        elif start is not None and ln.startswith("## "):
            return "\n".join(lines[start:i]).strip()
    if start is None:
        return None
    return "\n".join(lines[start:]).strip()


def entry_ids(markdown: str, prefix: str) -> list[str]:
    return sorted(set(re.findall(r"^##\s+(" + prefix + r"-\d+)", markdown, flags=re.M)))


def entry_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def secret_scan_text(text: str) -> list[str]:
    text = DATA_URI_RE.sub("data:", text)
    return sorted({name for name, rx in SECRET_PATTERNS if rx.search(text)})


def cmp_content_id(obj: dict) -> str:
    """cmp id = id of the compare result minus volatile keys and its own result_id (06 §3.1 result_id rule)."""
    return ac.object_id("cmp", {k: v for k, v in obj.items() if k != "result_id"})


def json_content_id(kind: str, obj) -> str:
    if kind == "cmp":
        return cmp_content_id(obj)
    return ac.object_id(kind, obj)


def load_index(migration: Path) -> list[dict]:
    p = migration / "runs" / "index.jsonl"
    out = []
    if not p.is_file():
        return out
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            out.append({"_unparseable": ln[:200]})
    return out


def newest_sealed_run(migration: Path) -> str | None:
    for rec in reversed(load_index(migration)):
        if rec.get("evidence_id") and rec.get("run_id"):
            return rec["run_id"]
    return None


def evidence_chain(verify: Path) -> list[Path]:
    """evidence.json, evidence.1.json, … in order (amendments, 07 §3.5)."""
    chain = []
    if (verify / "evidence.json").is_file():
        chain.append(verify / "evidence.json")
        n = 1
        while (verify / f"evidence.{n}.json").is_file():
            chain.append(verify / f"evidence.{n}.json")
            n += 1
    return chain


def _rel(project: Path, p: Path) -> str:
    return Path(os.path.relpath(p, project)).as_posix()


def _norm_list(x) -> list:
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return list(x)
    return [x]


def rung_index(r: str | None) -> int:
    return RUNGS.index(r) if r in RUNGS else 0


def rung_cap(r: str, cap: str) -> str:
    return RUNGS[min(rung_index(r), rung_index(cap))]


def worst_disposition(ds: list[str]) -> str | None:
    vals = [DISPOSITION_ORDER.index(d) for d in ds if d in DISPOSITION_ORDER]
    return DISPOSITION_ORDER[min(vals)] if vals else None


# ------------------------------------------------------------------------------------------------------------------
# Compare state re-derivation (07 §4.3 check 5)
# ------------------------------------------------------------------------------------------------------------------
def is_gated(check: dict) -> bool:
    return check.get("state") == "NOT_COMPARED" and ("GATED" in _norm_list(check.get("reason_codes"))
                                                     or bool(check.get("gated_by")))


def is_kd_check(check: dict) -> bool:
    """The KD bound check of an accepted_change (compare.py: id `kd:<KD-id>`, `kd_check: true`)."""
    cid = str(check.get("id") or "")
    return bool(check.get("kd_check") or check.get("kd_id") or check.get("known_difference")) or \
        cid.startswith(("kd:", "kd.", "known_difference."))


def check_matches(listed: str, check_id: str) -> bool:
    """Same rule as compare.py: a KD listing `raster.values` covers `raster.values`, `raster.values:b1`, …"""
    return check_id == listed or check_id.startswith((listed + ":", listed + ".", listed + "@"))


def declared_state(cmp: dict) -> str | None:
    return cmp.get("verdict") or cmp.get("state")


def recompute_state(cmp: dict) -> tuple[str | None, str | None]:
    """Return (state_from_checks, problem). Checks decide; a declared state may be worse (pairing / decision codes)
    but never better than its checks. A pass needs at least one non-gated check."""
    states = []
    for ch in cmp.get("checks") or []:
        if not isinstance(ch, dict):
            return None, "check is not an object"
        if is_gated(ch) or ch.get("diagnostic") is True or is_kd_check(ch):
            continue  # accepted_change bound checks never change the compare state (DESIGN §4.6)
        st = ch.get("state")
        if st not in ac.VERDICTS:
            return None, f"check {ch.get('id')!r} has unknown state {st!r}"
        states.append(st)
    codes = set(_norm_list(cmp.get("reason_codes")))
    if codes & DECISION_CODES:
        states.append("NEEDS_DECISION")
    return ac.worst(states), None


def effective_state(cmp: dict) -> tuple[str, bool, str]:
    """(state, recompute_ok, detail). state = worst(declared, recomputed); mismatch iff declared is better."""
    dec = declared_state(cmp)
    rec, problem = recompute_state(cmp)
    if problem:
        return "EVALUATOR_ERROR", False, problem
    if dec not in ac.VERDICTS:
        return "EVALUATOR_ERROR", False, f"declared state {dec!r} unknown"
    if rec is None:
        # no checks at all: only a non-pass declaration is consistent (empty is never a pass)
        if dec in ac.PASS_STATES:
            return "EVALUATOR_ERROR", False, "declared pass with no checks"
        return dec, True, ""
    eff = ac.worst([dec, rec])
    if ac.VERDICTS.index(dec) > ac.VERDICTS.index(rec):
        return eff, False, f"declared {dec} but checks give {rec}"
    return eff, True, ""


def failing_checks(cmp: dict) -> list[dict]:
    return [c for c in cmp.get("checks") or [] if isinstance(c, dict) and not is_gated(c) and not is_kd_check(c)
            and c.get("state") not in ac.PASS_STATES and c.get("state") not in (None, "NOT_COMPARED")
            and c.get("diagnostic") is not True]


def first_reason(cmp: dict) -> tuple[str | None, dict]:
    """First failing reason code and its check (for phrases); falls back to dataset-level codes."""
    for c in failing_checks(cmp):
        codes = _norm_list(c.get("reason_codes"))
        if codes:
            return codes[0], c
    codes = _norm_list(cmp.get("reason_codes"))
    return (codes[0] if codes else None), {}


# ------------------------------------------------------------------------------------------------------------------
# Loading and verifying the bundle
# ------------------------------------------------------------------------------------------------------------------
class Bundle:
    def __init__(self, migration: Path, run_id: str):
        self.migration = migration
        self.project = migration.parent
        self.run_id = run_id
        self.run_dir = migration / "runs" / run_id
        self.verify = self.run_dir / "verify"
        self.integrity: list[dict] = []
        self.evidence: dict | None = None
        self.chain: list[dict] = []
        self.evidence_id: str | None = None
        self.objects: list[dict] = []
        self.by_kind: dict[str, list[dict]] = {}
        self._json_cache: dict[str, object] = {}

    def problem(self, code: str, detail: str = "", **kw):
        rec = {"code": code, "detail": detail}
        rec.update({k: v for k, v in kw.items() if v is not None})
        rec.setdefault("severity", "incomplete" if code in INCOMPLETE_CODES else "stale")
        self.integrity.append(rec)

    def abspath(self, rel: str) -> Path:
        return (self.project / rel)

    def read_json_rel(self, rel: str):
        if rel in self._json_cache:
            return self._json_cache[rel]
        try:
            val = ac.load_json(self.abspath(rel))
        except (OSError, json.JSONDecodeError):
            val = None
        self._json_cache[rel] = val
        return val


def _validate_schema(b: Bundle, ev: dict, name: str) -> bool:
    ok = True
    if not isinstance(ev, dict) or ev.get("schema") != EVIDENCE_SCHEMA:
        b.problem("EVIDENCE_SCHEMA_INVALID", f"{name}: schema is not {EVIDENCE_SCHEMA}")
        return False
    extra = set(ev) - EVIDENCE_KEYS
    if extra:
        b.problem("EVIDENCE_SCHEMA_INVALID", f"{name}: unknown fields {sorted(extra)}")
        ok = False
    if not isinstance(ev.get("objects"), list) or not isinstance(ev.get("run"), dict):
        b.problem("EVIDENCE_SCHEMA_INVALID", f"{name}: objects/run missing")
        return False
    for o in ev["objects"]:
        if not isinstance(o, dict) or o.get("kind") not in ALL_KINDS or not isinstance(o.get("id"), str):
            b.problem("EVIDENCE_SCHEMA_INVALID", f"{name}: bad object {str(o)[:120]}")
            ok = False
            continue
        if set(o) - OBJECT_KEYS:
            b.problem("EVIDENCE_SCHEMA_INVALID", f"{name}: object {o['id'][:24]} has unknown fields "
                                                 f"{sorted(set(o) - OBJECT_KEYS)}")
            ok = False
    return ok


def load_bundle(migration: Path, run_id: str) -> Bundle:
    b = Bundle(migration, run_id)
    files = evidence_chain(b.verify)
    if not files:
        b.problem("EVIDENCE_FILE_MISSING", "evidence.json missing (run not sealed)",
                  path=_rel(b.project, b.verify / "evidence.json"))
        return b
    prev_id = None
    for f in files:
        try:
            ev = ac.load_json(f)
        except (OSError, json.JSONDecodeError) as e:
            b.problem("EVIDENCE_SCHEMA_INVALID", f"{f.name}: {e}")
            return b
        if not _validate_schema(b, ev, f.name):
            return b
        try:
            eid = ac.object_id("ev", ev)
        except ac.CJSONError as e:
            b.problem("EVIDENCE_SCHEMA_INVALID", f"{f.name}: {e}")
            return b
        if ev.get("parent_evidence_id") != prev_id:
            b.problem("EVIDENCE_CHAIN_BROKEN", f"{f.name}: parent {ev.get('parent_evidence_id')} ≠ {prev_id}")
        if prev_id is not None:
            _check_amendment(b, b.chain[-1], ev, f.name)
        b.chain.append(ev)
        prev_id = eid
    b.evidence = b.chain[-1]
    b.evidence_id = prev_id
    idf = b.verify / "EVIDENCE_ID"
    stored = idf.read_text(encoding="utf-8").strip() if idf.is_file() else None
    if stored != b.evidence_id:
        b.problem("EVIDENCE_ID_MISMATCH", f"EVIDENCE_ID file says {stored}, recomputed {b.evidence_id}")
    idx_ids = {r.get("evidence_id") for r in load_index(migration) if r.get("run_id") == run_id}
    if b.evidence_id not in idx_ids:
        b.problem("EVIDENCE_ID_MISMATCH", "recomputed evidence id not in runs/index.jsonl for this run")
    if b.evidence.get("run", {}).get("run_id") != run_id:
        b.problem("EVIDENCE_SCHEMA_INVALID", "evidence run_id differs from the run folder")
    b.objects = list(b.evidence.get("objects") or [])
    for o in b.objects:
        b.by_kind.setdefault(o["kind"], []).append(o)
    return b


def _check_amendment(b: Bundle, parent: dict, child: dict, name: str):
    pk = {okey(o): o for o in parent.get("objects", [])}
    ck = {okey(o): o for o in child.get("objects", [])}
    removed = set(pk) - set(ck)
    added = [ck[i] for i in set(ck) - set(pk)]
    bad = [o for o in added if o["kind"] not in AMENDABLE_KINDS]
    if removed or bad:
        b.problem("EVIDENCE_CHAIN_BROKEN", f"{name}: amendment removed {len(removed)} object(s) or added "
                                           f"non-amendable kinds {sorted({o['kind'] for o in bad})}")
    for k in ("run", "sealed", "project", "coverage"):
        if json.dumps(ac.strip_volatile(parent.get(k)), sort_keys=True) != \
                json.dumps(ac.strip_volatile(child.get(k)), sort_keys=True):
            b.problem("EVIDENCE_CHAIN_BROKEN", f"{name}: amendment changed '{k}'")
    for k in ("params", "tolerances"):
        if json.dumps((parent.get("records") or {}).get(k), sort_keys=True) != \
                json.dumps((child.get("records") or {}).get(k), sort_keys=True):
            b.problem("EVIDENCE_CHAIN_BROKEN", f"{name}: amendment changed records.{k}")


def okey(o: dict) -> str:
    """Identity of an object entry: the same bytes may legitimately sit at two paths (e.g. two empty plots)."""
    # dataset is part of the identity: one legacy file can be the reference of several datasets (an equivalent
    # pairing shares the QGIS step's file), and each dataset needs its own bound ref object
    return "|".join([o["id"], o.get("path") or "", o.get("finding_id") or o.get("entry") or "",
                     o.get("dataset") or ""])


def verify_objects(b: Bundle) -> dict[str, bool]:
    """07 §4.3 check 2: every object exists, sha256 matches, id recomputes. Returns {id: ok}."""
    ok: dict[str, bool] = {}
    vis_files: dict[str, dict] = {}
    for o in b.objects:
        kind, oid, rel = o["kind"], o["id"], o.get("path")
        good = True
        try:
            if kind in INLINE_KINDS:
                good = ac.object_id(kind, o.get("value")) == oid
                if not good:
                    b.problem("EVIDENCE_HASH_MISMATCH", f"inline {kind} value does not hash to its id", object=oid)
            elif rel is None:
                b.problem("EVIDENCE_SCHEMA_INVALID", f"object {oid[:30]} has no path", object=oid)
                good = False
            elif kind in ELEMENT_KINDS:
                p = b.abspath(rel)
                if not p.is_file():
                    b.problem("EVIDENCE_FILE_MISSING", f"{rel} missing", object=oid, path=rel)
                    good = False
                else:
                    if rel not in vis_files:
                        vis_files[rel] = {f.get("finding_id"): f for f in
                                          ((b.read_json_rel(rel) or {}).get("findings") or [])}
                    f = vis_files[rel].get(o.get("finding_id"))
                    if f is None or ac.object_id(kind, f) != oid:
                        b.problem("EVIDENCE_HASH_MISMATCH", f"visual finding {o.get('finding_id')} changed or "
                                                            f"missing in {rel}", object=oid, path=rel)
                        good = False
            elif kind == "view":
                p = b.abspath(rel)
                h = ac.sha256_file(p) if p.is_file() else None
                if h is None or hex_of(o.get("sha256")) != h or f"view:{h}" != oid:
                    b.problem("VIEW_CHANGED", f"view project {rel} missing or changed since seal: receipts citing it "
                                              "do not count", object=oid, path=rel, dataset=o.get("dataset"),
                              severity="info")
                    good = False
            elif kind in ENTRY_KINDS:
                p = b.abspath(rel)
                txt = p.read_text(encoding="utf-8") if p.is_file() else None
                et = entry_text(txt, o.get("entry", "")) if txt is not None else None
                if et is None:
                    b.problem("EVIDENCE_FILE_MISSING", f"entry {o.get('entry')} missing in {rel}", object=oid,
                              path=rel, expected=o.get("sha256"))
                    good = False
                elif f"{kind}:{entry_sha(et)}" != oid:
                    b.problem("EVIDENCE_HASH_MISMATCH", f"entry {o.get('entry')} in {rel} changed", object=oid,
                              path=rel, expected=o.get("sha256"))
                    good = False
            else:
                p = b.abspath(rel)
                if kind in TREE_KINDS:
                    if not p.is_dir():
                        b.problem("EVIDENCE_FILE_MISSING", f"{rel} missing", object=oid, path=rel,
                                  expected=o.get("sha256"))
                        good = False
                    else:
                        h = tree_hex(p)
                        if f"{kind}:{h}" != oid:
                            b.problem("EVIDENCE_HASH_MISMATCH", f"tree {rel} changed", object=oid, path=rel,
                                      expected=o.get("sha256"), actual="sha256:" + h)
                            good = False
                elif kind in FILE_KINDS and p.is_dir() and (o.get("meta") or {}).get("tree"):
                    h = tree_hex(p)
                    if f"{kind}:{h}" != oid:
                        b.problem("EVIDENCE_HASH_MISMATCH", f"tree {rel} changed", object=oid, path=rel,
                                  expected=o.get("sha256"), actual="sha256:" + h, dataset=o.get("dataset"))
                        good = False
                elif not p.is_file():
                    b.problem("EVIDENCE_FILE_MISSING", f"{rel} missing", object=oid, path=rel,
                              expected=o.get("sha256"), dataset=o.get("dataset"), step=o.get("step"))
                    good = False
                else:
                    h = ac.sha256_file(p)
                    if hex_of(o.get("sha256")) != h:
                        b.problem("EVIDENCE_HASH_MISMATCH", f"{rel} bytes changed", object=oid, path=rel,
                                  expected=o.get("sha256"), actual="sha256:" + h, dataset=o.get("dataset"),
                                  step=o.get("step"))
                        good = False
                    elif kind in JSON_KINDS:
                        obj = b.read_json_rel(rel)
                        if obj is None or json_content_id(kind, obj) != oid:
                            b.problem("EVIDENCE_HASH_MISMATCH", f"{rel} content id differs", object=oid, path=rel)
                            good = False
                    elif kind in FILE_KINDS and kind != "aux" and f"{kind}:{h}" != oid:
                        b.problem("EVIDENCE_HASH_MISMATCH", f"{rel} id differs", object=oid, path=rel)
                        good = False
        except (OSError, ValueError) as e:
            b.problem("EVIDENCE_HASH_MISMATCH", f"{rel}: {e}", object=oid, path=rel)
            good = False
        ok[okey(o)] = good
    return ok


# ------------------------------------------------------------------------------------------------------------------
# Live checks: drift and legacy tree
# ------------------------------------------------------------------------------------------------------------------
def _graph_contract_hex(p: Path) -> str:
    """Graph drift compares contract views: derive's projections (ladder, verify, revision, …) are not a change.
    The sealed graph object itself is still checked by raw bytes (verify_objects)."""
    try:
        return hex_of(ac.graph_contract_sha256(p))
    except (OSError, ValueError):
        return "unreadable:" + ac.sha256_file(p)


def drift_findings(b: Bundle) -> list[dict]:
    out = []
    snap = b.run_dir / "snapshot"
    live_paths = {"graph": b.migration / "graph.json", "params": b.migration / "params.yaml",
                  "tolerances": b.migration / "tolerances.yaml", "decisions": b.migration / "decisions.md",
                  "known_differences": b.migration / "known-differences.md",
                  "lock": ac.plugin_root() / "skills" / "run" / "env" / "uv.lock"}
    for key, live in sorted(live_paths.items()):
        s = snap / SNAPSHOT_FILES[key]
        code = CAPPING_DRIFT.get(key) or INFO_DRIFT.get(key)
        hasher = _graph_contract_hex if key == "graph" else ac.sha256_file
        sh = hasher(s) if s.is_file() else None
        lh = hasher(live) if live.is_file() else None
        if sh != lh:
            prefix = "contract-sha256:" if key == "graph" else "sha256:"
            out.append({"code": code, "file": key, "sealed": sh and prefix + sh, "live": lh and prefix + lh,
                        "caps": key in CAPPING_DRIFT, "severity": "stale" if key in CAPPING_DRIFT else "info"})
    live_code = b.migration / "pipeline"
    snap_code = snap / "axis_pipeline"
    lc = tree_hex(live_code) if live_code.is_dir() else None
    sc = tree_hex(snap_code) if snap_code.is_dir() else None
    if lc != sc:
        out.append({"code": "CODE_CHANGED_AFTER_RUN", "file": "pipeline", "sealed": sc and "code:" + sc,
                    "live": lc and "code:" + lc, "caps": False, "severity": "stale"})
    return out


def legacy_tree_findings(b: Bundle, graph: dict | None, inventory: dict | None) -> list[dict]:
    """R23: a new .aux.xml (PAM sidecar) anywhere in the legacy tree after discover = LEGACY_REF_CHANGED."""
    out = []
    if not graph or not inventory:
        return out
    try:
        root = (b.migration / graph["project"]["legacy_root"]).resolve()
    except (KeyError, TypeError):
        return out
    known = {f.get("path") for f in inventory.get("files") or []}
    for f in inventory.get("files") or []:
        for s in f.get("sidecars") or []:
            known.add(s if "/" in str(s) else str(Path(f.get("path", "")).parent / s).lstrip("./"))
    if not root.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for fn in sorted(filenames):
            if fn.lower().endswith(".aux.xml"):
                rel = (Path(dirpath) / fn).relative_to(root).as_posix()
                if rel not in known:
                    out.append({"code": "LEGACY_REF_CHANGED", "path": rel,
                                "detail": f"new PAM sidecar {rel} appeared after discover"})
    return out


# ------------------------------------------------------------------------------------------------------------------
# Graph helpers
# ------------------------------------------------------------------------------------------------------------------
def ds_kind(ds: dict) -> str | None:
    return ds.get("kind") or ds.get("family")


def ds_legacy_path(ds: dict) -> str | None:
    leg = ds.get("legacy") or {}
    return leg.get("path") or ds.get("legacy_path")


def equivalent_via(ds: dict, datasets: dict | None) -> dict | None:
    """A user-confirmed equivalent pairing (reference.source equivalent, via_dataset, confirmed_by; 06 §4a, DESIGN
    §4.2 set_reference): the dataset is compared against the via dataset's legacy file. None otherwise."""
    ref = ds.get("reference") or {}
    if ref.get("source") != "equivalent" or not ref.get("via_dataset") or not datasets:
        return None
    via = datasets.get(ref["via_dataset"])
    if not via or not ref.get("confirmed_by"):
        return None
    vleg = via.get("legacy") or {}
    if not ds_legacy_path(via) or vleg.get("exists") is False:
        return None
    return via


def ds_reference_path(ds: dict, datasets: dict | None = None) -> str | None:
    """The legacy file this dataset is compared against (its own, or the confirmed equivalent's)."""
    via = equivalent_via(ds, datasets)
    return ds_legacy_path(via) if via else ds_legacy_path(ds)


def ds_has_reference(ds: dict, datasets: dict | None = None) -> bool:
    if ds.get("role") == "input":
        return False
    ref = ds.get("reference") or {}
    if ref.get("source") == "equivalent" and ref.get("via_dataset"):
        # a proposed equivalent pairing counts as a reference: unconfirmed, compare reports PAIRING_UNCONFIRMED
        # (NOT_COMPARED, blocks) — never a silent NO_REFERENCE
        return datasets is None or ref["via_dataset"] in datasets
    leg = ds.get("legacy") or {}
    if not ds_legacy_path(ds):
        return False
    if leg.get("exists") is False or ds.get("legacy_in_memory"):
        return False
    if ref.get("source") == "none" or ds.get("no_reference_reason"):
        return False
    return True


def producer_of(ds: dict, steps: dict) -> str | None:
    pb = ds.get("produced_by")
    if isinstance(pb, dict) and pb.get("step"):
        return pb["step"]
    if isinstance(pb, str):
        return pb
    for sid, s in steps.items():
        if ds["id"] in (s.get("outputs") or []):
            return sid
    return None


def step_class(step: dict) -> str:
    """'drop' | 'bridged' | 'not_ported' | 'migrate'."""
    scope = step.get("scope") or "migrate"
    if scope == "drop":
        return "drop"
    if step.get("kind") == "bridge" or scope == "keep_legacy":
        return "bridged"
    module = (step.get("new") or {}).get("module") or step.get("module")
    if not module:
        return "not_ported"
    return "migrate"


def topo_order(steps: dict, datasets: dict) -> list[str]:
    deps = {sid: set() for sid in steps}
    for sid, s in steps.items():
        for d in s.get("inputs") or []:
            p = producer_of(datasets.get(d, {"id": d}), steps) if d in datasets else None
            if p and p != sid and p in steps:
                deps[sid].add(p)
    order, done = [], set()
    while len(done) < len(steps):
        ready = sorted(s for s in steps if s not in done and deps[s] <= done)
        if not ready:  # cycle: append the rest by id
            ready = sorted(s for s in steps if s not in done)
            order.extend(ready)
            break
        for s in ready:
            order.append(s)
            done.add(s)
    return order


def ancestors(sid: str, steps: dict, datasets: dict) -> set[str]:
    seen, todo = set(), [sid]
    while todo:
        cur = todo.pop()
        for d in steps.get(cur, {}).get("inputs") or []:
            p = producer_of(datasets[d], steps) if d in datasets else None
            if p and p not in seen and p != sid:
                seen.add(p)
                todo.append(p)
    return seen


# ------------------------------------------------------------------------------------------------------------------
# compute()
# ------------------------------------------------------------------------------------------------------------------
def _empty_result(run_id, headline, blockers, integrity=None, migration: Path | None = None):
    rung = "none"
    res = {"ladder_version": LADDER_VERSION, "run_id": run_id, "evidence_id": None,
           "pipeline": {"rung": rung, "suffix": "", "headline": headline, "blockers": blockers,
                        "counts": {"pass": 0, "fail": 0, "not_compared": 0, "needs_review": 0,
                                   "histogram": {s: 0 for s in ac.VERDICTS}, "compared": 0},
                        "bridged": 0, "incomplete": bool(integrity), "greyed": list(GREYED), "drift": [],
                        "target": {"state": "not_attempted", "reasons": []},
                        "report": report_path(migration) if migration else None},
           "steps": {}, "datasets": {}, "integrity": integrity or []}
    res["status_line"] = status_line(res)
    return res


def status_line(res: dict) -> str:
    p = res["pipeline"]
    c = p["counts"]
    rung = p["rung"]
    if p.get("incomplete"):
        rung = "none (EVIDENCE INCOMPLETE)"
    elif p.get("headline") == "NOTHING VERIFIED":
        rung = f"{rung} (NOTHING VERIFIED)"
    suffix = p.get("suffix") or ""
    return (f"axis-migrate status: {rung}{suffix} | run {res.get('run_id') or 'none'} | {c['pass']} pass, "
            f"{c['fail']} FAIL, {c['not_compared']} NOT_COMPARED, {c['needs_review']} needs review | "
            + (f"report {p['report']}" if p.get("report") else "no report"))


def report_path(migration: Path) -> str | None:
    """'migration/report/index.html' when it exists, else None (the status line then says 'no report')."""
    return "migration/report/index.html" if (migration / "report" / "index.html").is_file() else None


def compute(migration_dir, run_id: str | None = None) -> dict:
    migration = Path(migration_dir).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    if run_id is None:
        run_id = newest_sealed_run(migration)
    if run_id is None:
        return _empty_result(None, "NO SEALED RUN", [{"code": "NO_SEALED_RUN", "detail": "no sealed run in "
                                                      "runs/index.jsonl: nothing verified"}], migration=migration)
    b = load_bundle(migration, run_id)
    if b.evidence is None:
        return _empty_result(run_id, "EVIDENCE INCOMPLETE", [{"code": "EVIDENCE_INCOMPLETE",
                                                              "detail": "bundle unreadable"}], b.integrity,
                             migration=migration)
    ev = b.evidence
    obj_ok = verify_objects(b)

    # sealed project files
    snap = b.run_dir / "snapshot"
    graph = _load_json_quiet(snap / "graph.json")
    inventory = _load_json_quiet(snap / "inventory.json") or _load_json_quiet(migration / "inventory.json")
    manifest = _load_json_quiet(b.run_dir / "manifest.json") or {}
    if graph is None:
        b.problem("EVIDENCE_FILE_MISSING", "snapshot/graph.json missing", path=_rel(b.project, snap / "graph.json"))
        return _finish_incomplete(b)
    for f in legacy_tree_findings(b, graph, inventory):
        b.problem("LEGACY_REF_CHANGED", f["detail"], path=f["path"])
    sec = ev.get("secret_scan") or {}
    if sec.get("matches"):
        b.problem("SECRET_IN_BUNDLE", f"secret scan matched {sec.get('matches')} file(s): {sec.get('files')}")
    for o in b.by_kind.get("ref", []) + b.by_kind.get("in", []):
        if not obj_ok.get(okey(o), False):
            b.problem("LEGACY_REF_CHANGED", f"{o['kind']} {o.get('path')} differs from its pinned hash",
                      path=o.get("path"), dataset=o.get("dataset"))
    drift = drift_findings(b)
    capping_drift = [d for d in drift if d["caps"]]
    code_drift = any(d["code"] == "CODE_CHANGED_AFTER_RUN" for d in drift)
    for d in drift:
        if d["severity"] == "stale":
            b.problem(d["code"], f"live {d['file']} differs from the run snapshot", sealed=d["sealed"],
                      live=d["live"])

    steps = {s["id"]: s for s in graph.get("steps") or []}
    datasets = {d["id"]: d for d in graph.get("datasets") or []}
    order = topo_order(steps, datasets)
    records = ev.get("records") or {}
    run = ev.get("run") or {}

    # index objects
    out_by_ds = {o.get("dataset"): o for o in b.by_kind.get("out", [])}
    ref_by_ds = {o.get("dataset"): o for o in b.by_kind.get("ref", [])}
    cmp_by_ds = {}
    for o in b.by_kind.get("cmp", []):
        cmp_by_ds.setdefault(o.get("dataset"), o)
    tol_ids = {o["id"] for o in b.by_kind.get("tol", [])}
    tol_ids |= {r.get("record_id") for r in records.get("tolerances") or [] if r.get("record_id")}
    plot_sha_by_ds: dict[str, set] = {}
    for o in b.by_kind.get("plot", []):
        if obj_ok.get(okey(o)):
            plot_sha_by_ds.setdefault(o.get("dataset"), set()).add(hex_of(o.get("sha256")))
    run_rel = _rel(b.project, b.run_dir) + "/"
    views_by_ds: dict[str, dict] = {}       # dataset → {path relative to the run: {name, sha256, current, meta}}
    for o in b.by_kind.get("view", []):
        rel = str(o.get("path") or "")
        if not rel.startswith(run_rel):
            continue
        key = rel[len(run_rel):]
        ok = bool(obj_ok.get(okey(o)))
        cur = views_by_ds.setdefault(o.get("dataset"), {}).get(key)
        if cur is None or (ok and cur["current"] is None):
            views_by_ds[o.get("dataset")][key] = {"name": (o.get("meta") or {}).get("view"),
                                                  "sha256": hex_of(o.get("sha256")),
                                                  "current": hex_of(o.get("sha256")) if ok else None,
                                                  "meta": o.get("meta") or {}}
    findings = []
    for o in b.by_kind.get("vis", []):
        rel = o.get("path")
        doc = b.read_json_rel(rel) or {}
        for f in doc.get("findings") or []:
            if f.get("finding_id") == o.get("finding_id") and obj_ok.get(okey(o)):
                findings.append(f)
    cleared = set()
    for d in records.get("decisions") or []:
        if str(d.get("by") or "").lower().startswith(("user", "human")):
            cleared.update(_norm_list(d.get("clears")))
    kd_records = [k for k in records.get("known_differences") or [] if k.get("in_contract", True)]

    # ---------------------------------------------------------------- datasets
    dres: dict[str, dict] = {}
    hist = {s: 0 for s in ac.VERDICTS}
    n_compared = 0
    for dsid in sorted(datasets):
        ds = datasets[dsid]
        if ds.get("role") == "input":
            continue
        prod = producer_of(ds, steps)
        pstep = steps.get(prod) if prod else None
        cls = step_class(pstep) if pstep else "migrate"
        if cls == "drop":
            continue
        r = {"dataset": dsid, "step": prod, "kind": ds_kind(ds), "role": ds.get("role"),
             "legacy_path": ds_reference_path(ds, datasets), "has_reference": ds_has_reference(ds, datasets),
             "state": None,
             "declared_state": None, "reason_codes": [], "disposition": None, "notes": [], "visual": {},
             "kd": [], "compare": None, "first_reason": None, "first_check": {}}
        dres[dsid] = r
        via = equivalent_via(ds, datasets)
        if via is not None:
            vstep = producer_of(via, steps)
            tool = str((steps.get(vstep) or {}).get("tool") or (steps.get(vstep) or {}).get("kind") or "")
            label = "QGIS step" if "qgis" in tool.lower() else "step"
            r["equivalent"] = {"via_dataset": via["id"], "via_step": vstep,
                               "confirmed_by": (ds.get("reference") or {}).get("confirmed_by")}
            r["notes"].append(f"reference from equivalent {label} {vstep or '?'} ({via['id']}), pairing confirmed "
                              f"by {r['equivalent']['confirmed_by']}")
        if cls == "bridged":
            r["disposition"] = "BRIDGED"
            r["reason_codes"] = ["BRIDGED"]
            continue
        waiver = (ds.get("verify") or {}).get("waiver")
        if not r["has_reference"]:
            r["disposition"] = "NO_REFERENCE"
            r["reason_codes"] = [ds.get("no_reference_reason") or "NO_REFERENCE"]
            continue
        co = cmp_by_ds.get(dsid)
        if co is None:
            if waiver and ds.get("role") != "final" and str((waiver or {}).get("by", "")).startswith(("user",
                                                                                                    "human")):
                r["disposition"] = "NOT_COMPARED"
                r["reason_codes"] = ["WAIVED"]
                r["waived"] = True
                continue
            r["state"] = "NOT_COMPARED"
            r["disposition"] = "NOT_COMPARED"
            r["reason_codes"] = ["NOT_COMPARED"]
            hist["NOT_COMPARED"] += 1
            continue
        r["compare"] = co.get("path")
        if not obj_ok.get(okey(co)):
            r["disposition"] = "EVIDENCE_INCOMPLETE"
            r["reason_codes"] = ["EVIDENCE_HASH_MISMATCH"]
            continue
        cmp = b.read_json_rel(co["path"]) or {}
        # binding (07 §4.3 check 4)
        refs = cmp.get("refs") or {}
        outo, refo = out_by_ds.get(dsid), ref_by_ds.get(dsid)
        bind_errs = []
        if cmp.get("run_id") != b.run_id:
            bind_errs.append(f"run_id {cmp.get('run_id')} ≠ {b.run_id}")
        compared = bool(cmp.get("checks")) or declared_state(cmp) in ac.PASS_STATES
        ref_ok_ids = set()
        if refo:
            ref_ok_ids = {refo["id"]} | {f"ref:{hex_of((refo.get('meta') or {}).get('content_sha256'))}"}
        if not compared:
            # pairing-level result without checks (OUTPUT_MISSING, PAIRING_UNCONFIRMED, …): can never pass, so
            # null refs are consistent; refs that are present must still point at this run's objects
            if refs.get("out") and (outo is None or refs.get("out") != outo["id"]):
                bind_errs.append(f"refs.out {refs.get('out')} is not this run's output")
            if refs.get("ref") and refs.get("ref") not in ref_ok_ids:
                bind_errs.append(f"refs.ref {refs.get('ref')} not the pinned legacy reference")
        else:
            if outo is None or refs.get("out") != outo["id"]:
                bind_errs.append(f"refs.out {refs.get('out')} ≠ this run's {outo and outo['id']}")
            if refs.get("ref") not in ref_ok_ids:
                bind_errs.append(f"refs.ref {refs.get('ref')} not the pinned legacy reference")
        bad_tol = [t for t in _norm_list(refs.get("tol")) if t not in tol_ids]
        if bad_tol:
            bind_errs.append(f"refs.tol {bad_tol} not in the sealed tolerances")
        if cmp.get("result_id") and cmp.get("result_id") != co["id"]:
            bind_errs.append("result_id differs from content id")
        if bind_errs:
            b.problem("UNBOUND_COMPARE", "; ".join(bind_errs), dataset=dsid, object=co["id"])
            r["disposition"] = "EVIDENCE_INCOMPLETE"
            r["reason_codes"] = ["UNBOUND_COMPARE"]
            continue
        state, rec_ok, detail = effective_state(cmp)
        r["declared_state"] = declared_state(cmp)
        if not rec_ok:
            b.problem("STATE_RECOMPUTE_MISMATCH", detail, dataset=dsid, object=co["id"])
            r["disposition"] = "EVIDENCE_INCOMPLETE"
            r["reason_codes"] = ["STATE_RECOMPUTE_MISMATCH"]
            continue
        if state not in ("BLOCKED", "NOT_COMPARED", "EVALUATOR_ERROR"):
            n_compared += 1  # BLOCKED / NOT_COMPARED / EVALUATOR_ERROR say nothing about the candidate
        codes = list(_norm_list(cmp.get("reason_codes")))
        fr, fc = first_reason(cmp)
        r["first_reason"], r["first_check"] = fr, fc

        # visual review (06 §4h): a PNG receipt must cite a sealed plot hash of this run, a view receipt sealed
        # view projects of this run whose bytes still match (lib/visual_receipt.py; both count the same way)
        if r["kind"] in ("raster", "vector"):
            shas = plot_sha_by_ds.get(dsid, set())
            mine = [f for f in findings if f.get("dataset_id") == dsid]
            vmap = views_by_ds.get(dsid, {})
            vcounts = next((x["meta"].get("validity_counts") for x in vmap.values()
                            if isinstance(x["meta"].get("validity_counts"), dict)), None)

            def counts(f, kind=r["kind"], shas=shas, vmap=vmap, vcounts=vcounts) -> bool:
                # same acceptance rule as compare.py aggregate: this run, closed answer/effect vocabulary, then the
                # cited plot or views; `cannot_tell` is listed as "not visually assessed" and is not a receipt
                if not (f.get("run_id") == b.run_id and f.get("answer") in ("yes", "no", "cannot_tell")
                        and f.get("effect") in ("flag", "none") and not (f.get("effect") == "flag"
                                                                         and f.get("answer") != "yes")):
                    return False
                if vrc.is_view(f):
                    return not vrc.check_view_receipt(f, family=kind, lookup=vmap.get,
                                                      validity_counts=vcounts if kind == "raster" else None)
                return hex_of(f.get("plot_sha256")) in shas
            valid = [f for f in mine if counts(f)]
            invalid = [f for f in mine if f not in valid]
            flags = [f for f in valid if f.get("answer") == "yes" and f.get("effect") == "flag"
                     and f.get("finding_id") not in cleared]
            r["visual"] = {"receipts": sum(1 for f in valid if f.get("answer") in ("yes", "no")),
                           "view_receipts": sum(1 for f in valid if vrc.is_view(f)
                                                and f.get("answer") in ("yes", "no")),
                           "invalid_receipts": len(invalid),
                           "flags": [f.get("finding_id") for f in flags],
                           "cleared": sorted(f.get("finding_id") for f in valid if f.get("finding_id") in cleared),
                           "cannot_tell": [f.get("finding_id") for f in valid if f.get("answer") == "cannot_tell"]}
            if not [f for f in valid if f.get("answer") in ("yes", "no")]:
                if state in ac.PASS_STATES:
                    state = "NEEDS_REVIEW"
                codes.append("VISUAL_REVIEW_MISSING")
                r["notes"].append("VISUAL_REVIEW_MISSING")
            if flags:
                if state in ac.PASS_STATES:
                    state = "NEEDS_REVIEW"
                codes.append("VISUAL_FLAG")
                r["notes"].append("VISUAL_FLAG")
        r["state"] = state
        r["reason_codes"] = sorted(set(codes), key=codes.index)
        hist[state] += 1
        disp = STATE_TO_DISPOSITION[state]
        # known differences (DESIGN §4.6, 07 §3.4)
        if state in ("FAIL", "NEEDS_DECISION"):
            acc, notes = _kd_accepts(cmp, dsid, kd_records)
            r["kd"] = notes
            if acc and not (set(codes) & {"TOLERANCE_UNSIGNED", "KD_UNSIGNED"}):
                disp = "ACCEPTED_DIFFERENCE"
        r["disposition"] = disp

    # ---------------------------------------------------------------- steps
    params = records.get("params") or []
    decisions = records.get("decisions") or []
    step_results = {s.get("step"): s for s in run.get("step_results") or []}
    scan_findings = _scan_blocks(b)
    tests_state = _tests_state(b)
    chained = run.get("chain") == "chained"
    sres: dict[str, dict] = {}
    for sid in order:
        s = steps[sid]
        cls = step_class(s)
        if cls == "drop":
            continue
        caps: list[dict] = []
        ann: list[dict] = []
        my_ds = [d for d in dres.values() if d["step"] == sid]
        outs = [d for d in (s.get("outputs") or []) if d in datasets and datasets[d].get("role") != "input"]
        if not outs:
            outs = [d["dataset"] for d in my_ds]
        entry = {"step": sid, "class": cls, "rung": "none", "disposition": None, "caps": caps,
                 "annotations": ann, "datasets": sorted(d["dataset"] for d in my_ds), "kind": s.get("kind"),
                 "scope": s.get("scope") or "migrate", "module": (s.get("new") or {}).get("module")}
        sres[sid] = entry
        if cls == "bridged":
            entry["rung"] = None
            entry["disposition"] = "BRIDGED"
            continue
        if cls == "not_ported":
            entry["disposition"] = "NOT_PORTED"
            caps.append({"code": "NOT_PORTED", "max": "none"})
            continue
        # generated
        module = entry["module"]
        mod_snap = snap / "axis_pipeline" / Path(module).name
        gen = mod_snap.is_file()
        if not gen:
            caps.append({"code": "MODULE_MISSING", "max": "none", "detail": f"{module} not in the sealed code tree"})
        blocks = [f for f in scan_findings if Path(str(f.get("file", ""))).name == Path(module).name]
        if scan_findings is None:
            caps.append({"code": "SCAN_MISSING", "max": "none"})
            gen = False
        elif blocks:
            caps.append({"code": "SCAN_BLOCKED", "max": "none",
                         "detail": ", ".join(sorted({str(f.get("rule")) for f in blocks}))})
            gen = False
        rung = "generated" if gen else "none"
        # sandbox-validated
        sv = gen
        sr = step_results.get(sid)
        if not sr or not sr.get("ok"):
            sv = False
            caps.append({"code": "STEP_NOT_SUCCEEDED" if sr else "STEP_NOT_RUN", "max": "generated",
                         "detail": (sr or {}).get("status")})
        else:
            if sr.get("undeclared_reads"):
                sv = False
                caps.append({"code": "UNDECLARED_READ", "max": "generated", "detail": str(sr["undeclared_reads"])})
        missing_out = [d for d in outs if d not in out_by_ds or not obj_ok.get(okey(out_by_ds[d]))]
        if missing_out:
            sv = False
            caps.append({"code": "OUTPUT_NOT_HASHED", "max": "generated", "detail": ", ".join(missing_out)})
        if tests_state.get(sid) is False or tests_state.get("*") is False:
            sv = False
            caps.append({"code": "TESTS_FAILED", "max": "generated"})
        elif tests_state.get("*") is None:
            sv = False
            caps.append({"code": "TESTS_MISSING", "max": "generated"})
        if run.get("stub_check_passed") is not True:
            sv = False
            caps.append({"code": "STUB_DETECTED" if run.get("stub_check_passed") is False else "STUB_CHECK_MISSING",
                         "max": "generated"})
        if not chained:
            sv = False
            caps.append({"code": "RUN_NOT_CHAINED", "max": "generated", "detail": run.get("chain")})
        if run.get("worktree_changed_during_run"):
            sv = False
            caps.append({"code": "CODE_CHANGED_DURING_RUN", "max": "generated"})
        if any(not obj_ok.get(okey(o), False) for o in b.by_kind.get("in", [])):
            sv = False
            caps.append({"code": "INPUT_HASH_MISMATCH", "max": "generated"})
        if not run.get("executor"):
            sv = False
            caps.append({"code": "EXECUTOR_NOT_RECORDED", "max": "generated"})
        if sv:
            rung = "sandbox-validated"
        if run.get("read_ledger") in ("audit+cpl_debug", "audit"):
            caps.append({"code": "READ_LEDGER_WEAK", "max": "sandbox-validated", "detail": run.get("read_ledger")})
        ann.append({"code": "ISOLATION", "text": f"isolation: {run.get('isolation') or 'unknown'}"})

        # disposition
        disp = worst_disposition([d["disposition"] for d in my_ds if d["disposition"] not in
                                  ("NO_REFERENCE", "BRIDGED", None) and not d.get("waived")])
        if sr and not sr.get("ok"):
            disp = worst_disposition([disp or "FAILED", "FAILED"])
        if disp is None:
            disp = "NO_REFERENCE" if any(d["disposition"] == "NO_REFERENCE" for d in my_ds) else "NOT_COMPARED"
        entry["disposition"] = disp
        # reference-verified
        rv = sv and disp in ("VERIFIED", "ACCEPTED_DIFFERENCE")
        if sv and not rv:
            caps.append({"code": "DISPOSITION_" + disp, "max": "sandbox-validated"})
        if rv and not run.get("full"):
            rv = False
            caps.append({"code": "RUN_NOT_FULL", "max": "sandbox-validated"})
        if any(d["disposition"] == "NOT_COMPARED" and not d.get("waived") for d in my_ds):
            rv = False
        # fitted parameters (DESIGN §4.3 reconciled rule)
        target_block = []
        for p in sorted(params, key=lambda x: x.get("id", "")):
            fitted = (p.get("source") == "fitted" or "fitted_on_reference" in _norm_list(p.get("flags"))
                      or bool(p.get("fitted_against")))
            if p.get("node") != sid:
                if fitted and p.get("node") in ancestors(sid, steps, datasets):
                    ann.append({"code": "UPSTREAM_FITTED_PARAM", "param": p.get("id"),
                                "text": f"rests on fitted {p.get('id')} upstream"})
                continue
            if fitted:
                who = _confirmed_by(p, decisions)
                if who:
                    ann.append({"code": "PARAM_FITTED_CONFIRMED", "param": p.get("id"),
                                "text": f"fitted, confirmed by {who}"})
                elif p.get("holdout_pass") and not p.get("global"):
                    ann.append({"code": "PARAM_FITTED_HELDOUT", "param": p.get("id"), "text": "fitted, held-out pass"})
                    target_block.append(p.get("id"))
                else:
                    why = ("acts globally; a split proves nothing" if p.get("global") and p.get("holdout_pass")
                           else "no passing hold-out")
                    caps.append({"code": "PARAM_FITTED_TO_REFERENCE", "max": "sandbox-validated",
                                 "param": p.get("id"), "detail": why})
                    target_block.append(p.get("id"))
            elif p.get("source") == "assumption" or p.get("status") == "assumed":
                ann.append({"code": "ASSUMPTION", "param": p.get("id"), "text": f"rests on assumption {p.get('id')}"})
            elif p.get("resolution") == "inferred":
                ann.append({"code": "INFERRED", "param": p.get("id"),
                            "text": f"{p.get('id')} inferred ({p.get('confidence') or 'confidence not stated'})"})
        entry["fitted_unconfirmed"] = sorted(target_block)
        for d in my_ds:
            for n in d["notes"]:
                if n.startswith("reference from equivalent"):
                    ann.append({"code": "LEGACY_EQUIVALENT_REFERENCE", "dataset": d["dataset"], "text": n})
        if rv:
            rung = "reference-verified"
        for c in capping_drift:
            caps.append({"code": c["code"], "max": "sandbox-validated"})
        if code_drift:
            caps.append({"code": "CODE_CHANGED_AFTER_RUN", "max": "reference-verified"})
        for c in caps:
            rung = rung_cap(rung, c["max"])
        entry["rung"] = rung
        entry["step_ok"] = bool(sr and sr.get("ok"))
        # first failing reason (for the Stop gate's "First failing step: <step> <code> <diagnostic>")
        for d in sorted(my_ds, key=lambda x: x["dataset"]):
            if d["disposition"] not in ("VERIFIED", "NO_REFERENCE", "BRIDGED", None):
                code = d.get("first_reason") or (d["reason_codes"][0] if d["reason_codes"] else d["disposition"])
                entry["first_reason"] = {"dataset": d["dataset"], "code": code,
                                         "diagnostics": (d.get("first_check") or {}).get("diagnostics") or {}}
                break
    # upstream annotation
    for sid, e in sres.items():
        if e["disposition"] in ("VERIFIED", "ACCEPTED_DIFFERENCE"):
            bad = sorted(a for a in ancestors(sid, steps, datasets)
                         if a in sres and sres[a]["disposition"] not in ("VERIFIED", "ACCEPTED_DIFFERENCE",
                                                                          "BRIDGED", "NO_REFERENCE"))
            if bad:
                e["annotations"].append({"code": "UPSTREAM_NOT_VERIFIED", "text": "upstream not verified: "
                                         + ", ".join(bad)})

    # ---------------------------------------------------------------- pipeline
    blockers: list[dict] = []
    in_scope = [e for e in sres.values() if e["class"] in ("migrate", "not_ported")]
    bridged = [e for e in sres.values() if e["class"] == "bridged"]
    rung = ac.rung_min([e["rung"] for e in in_scope]) if in_scope else "none"
    if not in_scope:
        blockers.append({"code": "NO_STEPS_IN_SCOPE"})
    for e in in_scope:
        if e["rung"] != "target-ready" and e["disposition"] not in ("VERIFIED", "ACCEPTED_DIFFERENCE"):
            blockers.append({"code": e["disposition"], "step": e["step"]})
    headline = None
    if n_compared == 0:
        headline = "NOTHING VERIFIED"
        rung = rung_cap(rung, "sandbox-validated")
        blockers.append({"code": "NOTHING_VERIFIED", "detail": "zero datasets actually compared"})
    finals = [d for d in dres.values() if d["role"] == "final"]
    for d in finals:
        if d["disposition"] not in ("VERIFIED", "ACCEPTED_DIFFERENCE", "BRIDGED"):
            rung = rung_cap(rung, "sandbox-validated")
            blockers.append({"code": "FINAL_NOT_VERIFIED", "dataset": d["dataset"], "detail": d["disposition"]})
    if not run.get("full"):
        rung = rung_cap(rung, "sandbox-validated")
        blockers.append({"code": "RUN_NOT_FULL", "detail": f"chain={run.get('chain')}"})
    executed_all = all(e.get("step_ok") for e in in_scope if e["class"] == "migrate") and in_scope
    if not executed_all:
        rung = rung_cap(rung, "sandbox-validated")
    for c in capping_drift:
        blockers.append({"code": c["code"]})
    incomplete = any(p["severity"] == "incomplete" for p in b.integrity)
    # target-ready
    target = _target_state(b, migration, ev, run, sres, in_scope, code_drift, rung)
    if rung == "reference-verified" and target["state"] == "ready":
        rung = "target-ready"
        for e in in_scope:
            e["rung"] = "target-ready" if e["rung"] == "reference-verified" else e["rung"]
    elif rung == "reference-verified":
        for r in target["reasons"]:
            blockers.append({"code": r.get("code"), "detail": r.get("detail"), "target": True})
    if incomplete:
        rung = "none"
        headline = "EVIDENCE INCOMPLETE"
        blockers.insert(0, {"code": "EVIDENCE_INCOMPLETE",
                            "detail": f"{sum(1 for p in b.integrity if p['severity'] == 'incomplete')} finding(s)"})
        for e in sres.values():
            if e["rung"] is not None:
                e["rung"] = "none"
    counts = {"pass": sum(1 for d in dres.values() if d["state"] in ac.PASS_STATES),
              "fail": sum(1 for d in dres.values() if d["state"] == "FAIL"),
              "not_compared": sum(1 for d in dres.values() if d["state"] == "NOT_COMPARED"),
              "needs_review": sum(1 for d in dres.values() if d["state"] == "NEEDS_REVIEW"),
              "histogram": hist, "compared": n_compared,
              "dispositions": {k: sum(1 for e in sres.values() if e["disposition"] == k)
                               for k in ac.DISPOSITIONS}}
    suffix = f" · partial ({len(bridged)} bridged)" if bridged else ""
    res = {"ladder_version": LADDER_VERSION, "run_id": run_id, "evidence_id": b.evidence_id,
           "pipeline": {"rung": rung, "suffix": suffix, "headline": headline or rung, "blockers": blockers,
                        "counts": counts, "bridged": len(bridged), "incomplete": incomplete,
                        "greyed": list(GREYED), "drift": drift, "target": target,
                        "full": bool(run.get("full")), "chain": run.get("chain"), "executor": run.get("executor"),
                        "isolation": run.get("isolation"), "order": [s for s in order if s in sres],
                        "report": report_path(migration)},
           "steps": sres, "datasets": dres, "integrity": b.integrity}
    assert rung in RUNGS  # never deployed / live-target-verified / customer-accepted
    res["status_line"] = status_line(res)
    return res


def _finish_incomplete(b: Bundle) -> dict:
    return _empty_result(b.run_id, "EVIDENCE INCOMPLETE", [{"code": "EVIDENCE_INCOMPLETE"}], b.integrity,
                         migration=b.migration)


def _load_json_quiet(p: Path):
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def _scan_blocks(b: Bundle):
    objs = b.by_kind.get("scan", [])
    if not objs:
        return None
    doc = b.read_json_rel(objs[0]["path"])
    if doc is None:
        return None
    if isinstance(doc, dict) and doc.get("status") in ("not_run", "error", "rule_error") and not doc.get("findings"):
        return None  # no scanner verdict: generated is not reached
    items = doc if isinstance(doc, list) else (doc.get("findings") or [])
    return [f for f in items if str(f.get("sev") or f.get("severity") or "").lower() in ("block", "error")]


def _tests_state(b: Bundle) -> dict:
    """{'*': True|False|None, '<step>': False} from verify/tests.json (runner preflight, `axis-migrate.tests.v1`:
    status passed|failed|error|absent, cases[{name, outcome}]). No smoke tests (`absent`) fails nothing."""
    objs = b.by_kind.get("tests", [])
    if not objs:
        return {"*": None}
    doc = b.read_json_rel(objs[0]["path"]) or {}
    cases = doc.get("cases") if isinstance(doc.get("cases"), list) else (
        doc.get("tests") if isinstance(doc.get("tests"), list) else [])
    out: dict = {}
    failed = [t for t in cases if str(t.get("outcome")) in ("failed", "failure", "error")]
    for t in failed:
        m = re.search(r"([sm]\d\d[a-z]?_[a-z0-9_]+)", str(t.get("step") or t.get("nodeid") or t.get("name") or ""))
        if t.get("step"):
            out[t["step"]] = False
        elif m:
            out[m.group(1)] = False
    status = doc.get("status")
    passed = doc.get("passed")
    if passed is None:
        if status is not None:
            passed = status in ("passed", "absent")
        else:
            passed = doc.get("exit_code") in (0, 5) and not failed  # pytest 5 = no tests collected
    if passed:
        out["*"] = True
    else:
        # failures attributed to steps cap only those steps; unattributed failures cap every step
        out["*"] = bool(out) and len(out) == len(failed)
    return out


def _confirmed_by(p: dict, decisions: list[dict]) -> str | None:
    if p.get("source") == "answer":
        return p.get("confirmed_by") or "user"
    for d in decisions:
        if p.get("id") in _norm_list(d.get("params")) and str(d.get("by") or "").lower().startswith(("user",
                                                                                                      "human")):
            if d.get("kind") in ("answer", "fit_confirm", "confirm_all") or p.get("status") == "confirmed":
                return str(d.get("by"))
    return None


def _kd_accepts(cmp: dict, dsid: str, kds: list[dict]) -> tuple[bool, list[dict]]:
    """accepted_change → ACCEPTED_DIFFERENCE only with human owner + bound held + every failing code covered."""
    notes = []
    fails = failing_checks(cmp)
    checks = cmp.get("checks") or []
    relevant = [k for k in kds if dsid in _norm_list(k.get("datasets"))]
    usable = []
    for k in relevant:
        reasons = []
        if k.get("status") != "accepted":
            reasons.append(f"status {k.get('status')}")
        if not str(k.get("owner") or "").startswith("human:"):
            reasons.append(f"owner {k.get('owner')!r} is not a human")
        if k.get("kind") not in ("accepted_change", "legacy_defect_preserved"):
            reasons.append(f"kind {k.get('kind')} does not accept a difference")
        kid = k.get("id")
        bound_check = [c for c in checks if isinstance(c, dict) and is_kd_check(c) and (
            kid in _norm_list(c.get("kd")) or c.get("kd_id") == kid or c.get("known_difference") == kid
            or c.get("id") in (f"kd:{kid}", f"kd.{kid}", f"known_difference.{kid}"))]
        held = bool(bound_check) and k.get("bound") is not None and all(
            (c.get("held") is True if "held" in c else c.get("state") in ac.PASS_STATES)
            and (c.get("detail") or {}).get("signed", True) is not False
            and "KD_UNSIGNED" not in _norm_list(c.get("reason_codes")) for c in bound_check)
        if not held:
            reasons.append("bound did not hold" if bound_check else "no bound check in the compare result")
        notes.append({"kd": k.get("id"), "accepted": not reasons, "why_not": reasons, "owner": k.get("owner"),
                      "bound": k.get("bound"), "bound_held": held})
        if not reasons:
            usable.append(k)
    if not fails and declared_state(cmp) not in ("FAIL", "NEEDS_DECISION"):
        return False, notes
    uncovered = []
    for c in fails:
        cid, codes = c.get("id"), set(_norm_list(c.get("reason_codes")))
        ok = any(any(check_matches(str(x), str(cid)) for x in _norm_list(k.get("checks")))
                 or (codes and codes <= set(_norm_list(k.get("reason_codes")))) for k in usable)
        if not ok:
            uncovered.append(cid)
    check_codes = {x for c in checks if isinstance(c, dict) for x in _norm_list(c.get("reason_codes"))}
    for code in _norm_list(cmp.get("reason_codes")):
        if code in DECISION_CODES and code not in check_codes:
            if not any(code in _norm_list(k.get("reason_codes")) for k in usable):
                uncovered.append(code)
    if uncovered:
        for n in notes:
            if n["accepted"]:
                n["why_not"] = [f"does not cover {sorted(set(map(str, uncovered)))}"]
    return bool(usable) and not uncovered, notes


def _target_state(b, migration, ev, run, sres, in_scope, code_drift, rung) -> dict:
    """09 §4.1: T1–T10, T13 PASS; T8 pass; no NOT_RUN; bound to the same code/lock/params ids; not_deployed."""
    reasons = []
    dbx = migration / "databricks"
    tm = _load_json_quiet(dbx / "target-manifest.json")
    tc = _load_json_quiet(dbx / "target-check.json")
    if tm is None or tc is None:
        return {"state": "not_attempted", "reasons": [{"code": "TARGET_NOT_PREPARED"}], "manifest": None,
                "checks": []}
    if tm.get("deployment_status") != "not_deployed":
        reasons.append({"code": "TC_DEPLOYMENT_STATUS_INVALID", "detail": str(tm.get("deployment_status"))})
    hashes = dict(tm.get("hashes") or {})
    for k in ("code_id", "lock_id", "params_id", "graph_id"):
        if tm.get(k):
            hashes.setdefault(k.replace("_id", ""), tm[k])
    snap = b.run_dir / "snapshot"
    expect = {"code": tree_hex(snap / "axis_pipeline") if (snap / "axis_pipeline").is_dir() else None,
              "params": ac.sha256_file(snap / "params.yaml") if (snap / "params.yaml").is_file() else None,
              "lock": ac.sha256_file(snap / "uv.lock") if (snap / "uv.lock").is_file() else None,
              "graph": ac.sha256_file(snap / "graph.json") if (snap / "graph.json").is_file() else None}
    alias = {"code": ("code", "pipeline_tree", "pipeline", "tree"), "params": ("params",), "lock": ("lock", "env_lock"),
             "graph": ("graph",)}
    for key in ("code", "params", "lock"):
        got = next((hashes[a] for a in alias[key] if hashes.get(a)), None)
        if hex_of(got) != expect[key]:
            reasons.append({"code": "TARGET_NOT_BOUND", "detail": f"{key}: target {got} ≠ run {expect[key]}"})
    if tm.get("run_id") and tm.get("run_id") != b.run_id:
        reasons.append({"code": "TARGET_NOT_BOUND", "detail": f"target run {tm.get('run_id')} ≠ {b.run_id}"})
    items = tc.get("checks") if isinstance(tc, dict) else tc
    if isinstance(items, dict):
        items = [{"id": k, **(v if isinstance(v, dict) else {"state": v})} for k, v in items.items()]
    items = items or []
    st = {str(c.get("id")): str(c.get("state")) for c in items}
    for t in TARGET_REQUIRED:
        if st.get(t) != "PASS":
            reasons.append({"code": "TARGET_CHECK_NOT_PASS", "detail": f"{t}: {st.get(t, 'missing')}"})
    if any(v == "NOT_RUN" for v in st.values()):
        reasons.append({"code": "TARGET_CHECK_NOT_RUN"})
    if code_drift:
        reasons.append({"code": "CODE_CHANGED_AFTER_RUN"})
    fitted = sorted({p for e in in_scope for p in e.get("fitted_unconfirmed", [])})
    if fitted:
        reasons.append({"code": "PARAM_FITTED_NOT_CONFIRMED", "detail": ", ".join(fitted)})
    tgt = b.by_kind.get("tgt", [])
    if not tgt:
        reasons.append({"code": "TARGET_NOT_SEALED", "detail": "run seal.py --amend --target"})
    elif not all(tree_hex(b.abspath(o["path"])) == hex_of(o["id"]) if b.abspath(o["path"]).is_dir() else False
                 for o in tgt):
        reasons.append({"code": "TARGET_CHANGED_AFTER_SEAL"})
    if rung != "reference-verified":
        reasons.append({"code": "PIPELINE_NOT_REFERENCE_VERIFIED", "detail": rung})
    return {"state": "ready" if not reasons else "not_ready", "reasons": reasons, "manifest": tm, "checks": items}


# ------------------------------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compute axis-migrate status rungs from the sealed evidence bundle.")
    ap.add_argument("migration", help="path to <project>/migration (or the project folder)")
    ap.add_argument("--run", default=None, help="run id (default: newest sealed run in runs/index.jsonl)")
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    a = ap.parse_args(argv)
    res = compute(a.migration, a.run)
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1, ensure_ascii=False, default=str))
    else:
        print(res["status_line"])
        for sid in res["pipeline"].get("order", []) or sorted(res["steps"]):
            e = res["steps"][sid]
            caps = ", ".join(c["code"] for c in e["caps"])
            print(f"  {sid}: {e['rung'] or 'n/a'} · {e['disposition']}" + (f" · caps: {caps}" if caps else ""))
        for p in res["integrity"]:
            print(f"  ! {p['code']}: {p.get('detail', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
