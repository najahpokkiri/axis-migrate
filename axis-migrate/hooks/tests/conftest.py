"""Shared fixtures for the hook and guard tests: a synthetic legacy project (no fixture facts) and a hook runner.

Payloads in payloads/*.json are real stdin captures from Claude Code 2.1.296 (headless, sanitised paths); tests copy
them and override cwd / tool_input, so field names and shapes stay the real ones (D8-4).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[2]
BIN = PLUGIN / "hooks" / "bin"
GUARD = PLUGIN / "skills" / "guard"
PAYLOADS = Path(__file__).resolve().parent / "payloads"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
import tempfile as _tempfile
MARKER_DIR = _tempfile.mkdtemp(prefix="axis-hooks-data-")
SYS_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else (shutil.which("python3") or sys.executable)

for p in (GUARD, PLUGIN / "lib", BIN):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def payload(name: str, **over) -> dict:
    d = json.loads((PAYLOADS / f"{name}.json").read_text())
    d = copy.deepcopy(d)
    for k, v in over.items():
        d[k] = v
    return d


def tool_payload(event: str, tool: str, cwd, tool_input: dict, **extra) -> dict:
    name = f"{event}_{tool}"
    if not (PAYLOADS / f"{name}.json").exists():
        name = f"{event}_Write" if event == "PreToolUse" and tool in ("MultiEdit", "NotebookEdit", "Grep", "Glob") \
            else f"{event}_Bash"
    d = payload(name, cwd=str(cwd), tool_name=tool, tool_input=tool_input)
    d.update(extra)
    return d


@dataclass
class HookResult:
    rc: int
    out: dict | None
    raw: str
    err: str
    ms: float

    @property
    def decision(self) -> str | None:
        if not self.out:
            return None
        hso = self.out.get("hookSpecificOutput") or {}
        return hso.get("permissionDecision") or self.out.get("decision")

    @property
    def reason(self) -> str:
        if not self.out:
            return self.err
        hso = self.out.get("hookSpecificOutput") or {}
        return hso.get("permissionDecisionReason") or self.out.get("reason") or hso.get("additionalContext") or ""


def run_hook(script: str, data, env: dict | None = None, timeout: float = 30, proc_cwd: str | None = None) -> HookResult:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("AXIS_MIGRATE_HIDDEN", "AXIS_MIGRATE_CODESCAN"))
         and k not in ("PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA")}
    e["PYTHONIOENCODING"] = "utf-8"
    e["CLAUDE_PLUGIN_DATA"] = MARKER_DIR  # never write the hooks-active marker into the real ~/.cache
    e.update(env or {})
    stdin = data if isinstance(data, str) else json.dumps(data)
    cwd = data.get("cwd") if isinstance(data, dict) else None
    cwd = cwd if cwd and os.path.isdir(cwd) else "/"
    cwd = str(proc_cwd) if proc_cwd else cwd  # the hook PROCESS cwd (may differ from stdin cwd under Codex desktop)
    t0 = time.perf_counter()
    cp = subprocess.run([SYS_PY, "-I", str(BIN / script)], input=stdin, capture_output=True, text=True, env=e,
                        timeout=timeout, cwd=cwd)
    ms = (time.perf_counter() - t0) * 1000
    out = json.loads(cp.stdout) if cp.stdout.strip() else None
    return HookResult(cp.returncode, out, cp.stdout, cp.stderr, ms)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------------------------
# Synthetic project
# ---------------------------------------------------------------------------------------------------------------
_GRAPH_MOD = None


def graph_module():
    """skills/discover/graph.py (stdlib core), imported once."""
    global _GRAPH_MOD
    if _GRAPH_MOD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("axis_graph_for_tests", PLUGIN / "skills" / "discover" / "graph.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GRAPH_MOD = mod
    return _GRAPH_MOD


@dataclass
class Project:
    root: Path
    mig: Path
    legacy: Path

    def write(self, rel: str, text: str) -> Path:
        p = self.mig / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def graph(self) -> dict:
        return json.loads((self.mig / "graph.json").read_text())

    def set_graph(self, g: dict) -> None:
        """Write the graph the way graph.py does (rev snapshot + .graph/head.json write record, GRAPH_OPS §8)."""
        G = graph_module()
        G._write_graph(self.mig, g)
        G._record_head(self.mig, g["revision"])

    def set_graph_outside(self, g: dict) -> None:
        """Simulate an edit of graph.json outside graph.py apply (GRAPH_EDITED_OUTSIDE_APPLY)."""
        (self.mig / "graph.json").write_text(json.dumps(g, indent=1, sort_keys=True))

    def append_edit(self, rec: dict) -> None:
        """Append to graph-edits.jsonl as graph.py would (re-recording the write record)."""
        with open(self.mig / "graph-edits.jsonl", "a") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
        graph_module()._record_head(self.mig, self.graph()["revision"])


LEGACY_FILES = {
    "03_INPUT/pop.tif": b"II*\x00synthetic-input-raster",
    "03_INPUT/zones.gpkg": b"SQLite format 3\x00synthetic",
    "05_TEMP/01_mid.tif": b"II*\x00synthetic-intermediate",
    "05_TEMP/01_mid.tif.aux.xml": b"<PAMDataset/>",
    "04_FINAL/final.csv": b"zone,value\na,1\n",
    "02_SCRIPTS/s01_prep.py": b"# legacy script\n",
    "NOTES.txt": b"notes\n",
}

DECISIONS = """# Decisions

## D-001 · Zones from the input file · 2026-10-14T10:00:00Z
```yaml
id: D-001
question: q01
kind: answer
answer: derive
by: user (AskUserQuestion)
params: [p.min_area]
nodes: [s01_prep]
changes: {}
supersedes: null
evidence: ["NOTES.txt:1"]
```
"""

KDS = """# Known differences

## KD-001 · Float widening
```yaml
id: KD-001
kind: normalisation
datasets: [ds.final]
checks: [table.attr:value]
rule: dtype widening
bound: null
reason: float32 vs float64
evidence: none
owner: agent
decided_at: 2026-10-14
decision_ref: null
status: proposed
```
"""

TOLERANCES = """version: 1
defaults:
  raster.values: {metric: abs+rel, atol: 0, rtol: 0, max_mismatch: 0}
records:
- id: tol.final.values.v1
  dataset: ds.final
  check: table.attr:value
  metric: abs+rel
  atol: 0
  rtol: 1.0e-6
  correct_spread: {value: 0.0, how: independent, evidence: x}
  nearest_wrong: {value: 1.0e-3, metric: rel, variant: y, evidence: z}
  agreed_by: null
  agreed_at: null
"""

PARAMS = """version: 1
budget: {max_questions: 8, asked: 1}
params:
  - id: p.min_area
    node: s01_prep
    kind: threshold
    value: 2500
    unit: m2
    resolution: resolved
    source: code
    evidence:
      - {ref: "02_SCRIPTS/s01_prep.py:1", quote: "MIN_AREA = 2500"}
    status: confirmed
  - id: p.threshold
    node: s01_prep
    kind: threshold
    value: 0.5
    resolution: inferred
    source: fitted
    fitted_against: ds.01_mid
    status: assumed
  - id: p.zones_path
    node: s01_prep
    kind: other
    value: "03_INPUT/zones.gpkg"
    resolution: resolved
    source: code
    status: confirmed
"""


def make_graph() -> dict:
    def ds(i, role, path, step=None, verdict=None, kind="raster", side=None):
        return {"id": i, "title": i, "kind": kind, "role": role,
                "legacy": {"path": path, "layer": None, "exists": True, "sidecars": side or []},
                "produced_by": {"step": step, "op": None} if step else None, "consumed_by": [],
                "reference": {"source": "legacy" if role != "input" else "none", "match_method": "lineage"},
                "verify": {"verdict": verdict}, "new": {"path": f"{step}/{Path(path).name}"} if step else None}

    approved = {"by": "user", "at": "2026-10-14T10:00:00Z", "channel": "interview", "verbatim": "go"}
    return {
        "schema": "axis-migrate.graph.v1", "revision": 7,
        "project": {"name": "legacy-demo", "legacy_root": "../legacy-demo", "legacy_tree_sha256": "x",
                    "plugin_version": "0.1.0", "created_at": "2026-10-14T09:00:00Z"},
        "acks": {"agent_seq": 3}, "updated": {"rev": 7, "by": "agent", "at": "2026-10-14T10:00:00Z"},
        "steps": [
            {"id": "s01_prep", "kind": "script", "scope": "migrate", "plan": {"approved": approved},
             "new": {"module": "pipeline/s01_prep.py"}, "verify": {"verdict": "PASS_EXACT"}},
            {"id": "s02_mask", "kind": "script", "scope": "migrate", "plan": {"approved": {"by": None, "at": None}},
             "new": {"module": "pipeline/s02_mask.py"}, "verify": {"verdict": None}},
            {"id": "s03_final", "kind": "script", "scope": "migrate", "plan": {"approved": approved},
             "new": {"module": "pipeline/s03_final.py"}, "verify": {"verdict": "FAIL"}},
            {"id": "s04_bridge", "kind": "bridge", "scope": "keep_legacy", "plan": {"approved": approved},
             "new": {"module": "pipeline/s04_bridge.py"}, "verify": {"verdict": None}},
        ],
        "datasets": [
            ds("ds.pop", "input", "03_INPUT/pop.tif"),
            ds("ds.zones", "input", "03_INPUT/zones.gpkg", kind="vector"),
            ds("ds.01_mid", "intermediate", "05_TEMP/01_mid.tif", "s01_prep", "PASS_EXACT",
               side=["05_TEMP/01_mid.tif.aux.xml"]),
            ds("ds.final", "final", "04_FINAL/final.csv", "s03_final", "FAIL", kind="table"),
        ],
        "edges": [
            {"id": "e.s01_prep>ds.01_mid", "from": "s01_prep", "to": "ds.01_mid", "kind": "writes",
             "evidence": [{"state": "directly_observed", "ref": "02_SCRIPTS/s01_prep.py:1", "summary": "write"}]},
            {"id": "e.s03_final>ds.final", "from": "s03_final", "to": "ds.final", "kind": "writes",
             "evidence": [{"state": "directly_observed", "ref": "02_SCRIPTS/s01_prep.py:1", "summary": "write"}]},
        ],
        "questions": [
            {"id": "q01", "status": "answered", "node": "s01_prep", "nodes": ["s01_prep"], "text": "Which zones?",
             "impact": {"level": "high"}, "answer": {"value": "derive", "by": "user"}},
        ],
        "invariants": [], "legacy_output_dirs": ["04_FINAL", "05_TEMP"],
    }


# session ids of the real payloads (Stop.json, codex_Stop.json) and the tests' default: the synthetic project counts as
# worked on by these sessions (_hooklib.touch_session), so the Stop gate applies to them. Tests of the session scope
# use other ids or make_project(..., sessions=()).
WORKING_SESSIONS = ("7e6b66d4-6be1-4451-8c26-66590462d97b", "019a-codex-session", "s-1")


def make_project(base: Path, *, runs: bool = False, sessions=WORKING_SESSIONS) -> Project:
    root = base / "proj"
    legacy = root / "legacy-demo"
    mig = root / "migration"
    for rel, data in LEGACY_FILES.items():
        p = legacy / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / "legacy-demo.PROVENANCE.md").write_text("grader answers\n")
    (root / "legacy-demo.build").mkdir()
    (root / "legacy-demo.build" / "build.py").write_text("print('build')\n")
    mig.mkdir(parents=True)
    pr = Project(root, mig, legacy)
    graph_module().init(mig, make_graph())   # rev 1 + write record, as lineage.py does
    pr.set_graph(make_graph())               # the synthetic state under test, recorded as a graph.py write
    inv = {
        "schema": "axis-migrate.inventory.v1", "legacy_root": "../legacy-demo",
        "scan": {"read_only_verified": True, "legacy_roots": ["."]},
        "files": [{"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                   "sidecar_of": "05_TEMP/01_mid.tif" if rel.endswith(".aux.xml") else None}
                  for rel, data in sorted(LEGACY_FILES.items())],
        "datasets": [
            {"id": "ds.pop", "legacy_path": "03_INPUT/pop.tif", "role": "input", "reference_candidate": False},
            {"id": "ds.zones", "legacy_path": "03_INPUT/zones.gpkg", "role": "input", "reference_candidate": False},
            {"id": "ds.01_mid", "legacy_path": "05_TEMP/01_mid.tif", "role": "intermediate",
             "reference_candidate": True},
            {"id": "ds.final", "legacy_path": "04_FINAL/final.csv", "role": "final", "reference_candidate": True},
        ],
    }
    (mig / "inventory.json").write_text(json.dumps(inv, indent=1, sort_keys=True))
    pr.write("params.yaml", PARAMS)
    pr.write("decisions.md", DECISIONS)
    pr.write("known-differences.md", KDS)
    pr.write("tolerances.yaml", TOLERANCES)
    pr.write("pipeline/__init__.py", "")
    (mig / "tests").mkdir()
    (mig / "analysis").mkdir()
    if runs:
        add_run(pr, "r20261014T100000Z-abcdef12")
    import _hooklib
    for sid in sessions:
        assert _hooklib.touch_session(mig, sid)
    return pr


def add_run(pr: Project, run_id: str, *, sealed: bool = True) -> Path:
    rd = pr.mig / "runs" / run_id
    snap = rd / "snapshot"
    (snap / "axis_pipeline").mkdir(parents=True)
    for name in ("params.yaml", "tolerances.yaml", "known-differences.md", "decisions.md", "graph.json"):
        shutil.copy(pr.mig / name, snap / name)
    for p in sorted((pr.mig / "pipeline").glob("*.py")):
        shutil.copy(p, snap / "axis_pipeline" / p.name)
    man = {"run_id": run_id, "chain": "chained", "executor": "local", "status": "succeeded",
           "tolerances": {"sha256": sha(pr.mig / "tolerances.yaml")}, "params": {"sha256": sha(pr.mig / "params.yaml")},
           "outputs": []}
    (rd / "manifest.json").write_text(json.dumps(man))
    (rd / "verify" / "compare").mkdir(parents=True)
    if sealed:
        (rd / "verify" / "EVIDENCE_ID").write_text("evidence:abc\n")
    with open(pr.mig / "runs" / "index.jsonl", "a") as fh:
        fh.write(json.dumps({"run_id": run_id, "chain": "chained", "status": "succeeded",
                             "evidence_id": "evidence:abc" if sealed else None}) + "\n")
    return rd


def run_ids(pr: Project) -> list[str]:
    """Run ids of the project, by name (never the listing order; runs/index.jsonl is not a run)."""
    import _hooklib
    return [d.name for d in _hooklib.run_dirs(pr.mig)]


@pytest.fixture
def apfs_order(monkeypatch):
    """Listing order with files first and names reversed (what APFS can return), for Path.iterdir and os.listdir."""
    import pathlib
    real_iterdir = pathlib.Path.iterdir
    real_listdir = os.listdir

    def key(p):
        return (not os.path.isfile(p), [-ord(c) for c in os.path.basename(str(p))])

    monkeypatch.setattr(pathlib.Path, "iterdir", lambda self: iter(sorted(real_iterdir(self), key=key)))
    monkeypatch.setattr(os, "listdir", lambda path=".": sorted(real_listdir(path),
                                                              key=lambda n: key(os.path.join(path, n))))


@pytest.fixture
def project(tmp_path) -> Project:
    return make_project(tmp_path / "a")


@pytest.fixture
def project_run(tmp_path) -> Project:
    return make_project(tmp_path / "b", runs=True)


# ---------------------------------------------------------------------------------------------------------------
# A run with compared raster datasets (Stop gate: visual review from the current run)
# ---------------------------------------------------------------------------------------------------------------
sys.path.append(str(PLUGIN / "skills" / "verify" / "tests"))   # _viewsbuilder (after this dir: never its conftest)


def add_compared_run(pr: Project, run_id: str, datasets=("ds.01_mid",), *, views: bool = True) -> Path:
    """add_run plus a PASS_EXACT raster compare result, one plot (plots.json) and stand-in GeoLibre views for each
    dataset. No receipt yet."""
    import _viewsbuilder as vb
    rd = add_run(pr, run_id)
    for ds in datasets:
        (rd / "verify" / "compare" / f"{ds}.json").write_text(json.dumps({
            "schema": "axis-migrate.compare-result.v1", "run_id": run_id, "dataset_id": ds, "family": "raster",
            "verdict": "PASS_EXACT", "reason_codes": [], "checks": [{"id": "raster.values", "state": "PASS_EXACT"}],
            "result_id": "cmp:" + "1" * 64}))
        pdir = rd / "verify" / "plots" / ds
        pdir.mkdir(parents=True)
        (pdir / "overview.png").write_bytes(b"\x89PNG synthetic " + ds.encode())
        (pdir / "plots.json").write_text(json.dumps({"dataset_id": ds, "png": [
            {"name": "overview.png", "kind": "overview", "sha256": sha(pdir / "overview.png")}]}))
    if views:
        vb.make_views(rd, {ds: "raster" for ds in datasets})
    return rd


def add_receipt(rd: Path, ds: str, kind: str = "png", **over) -> dict:
    """Append a receipt as plot.py review-add stores it (the Stop gate re-checks it like compare.py aggregate)."""
    import _viewsbuilder as vb
    run_id = json.loads((rd / "manifest.json").read_text())["run_id"]
    if kind == "png":
        p = rd / "verify" / "plots" / ds / "overview.png"
        f = {"dataset_id": ds, "question_id": "Q-all", "answer": "no", "effect": "none", "category": "none",
             "plot": f"plots/{ds}/overview.png", "plot_sha256": sha(p), "panel": "all", "note": "", "model": "m"}
    else:
        doc = json.loads((rd / "verify" / "views" / "views.json").read_text())
        f = dict(vb.view_receipt(doc, ds), receipt="view")
    f.update(run_id=run_id, finding_id=f"vf-{ds}-{kind}-{len(over)}", created_at="2026-10-16T12:00:00Z")
    f.update(over)
    vr = rd / "verify" / "visual-review.json"
    doc = json.loads(vr.read_text()) if vr.is_file() else {"schema": "axis-migrate.visual-review.v1", "findings": []}
    doc["findings"].append(f)
    vr.write_text(json.dumps(doc))
    return f
