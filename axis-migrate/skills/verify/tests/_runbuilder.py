"""Minimal synthetic project + run directory builder following CONTRACTS §6 exactly (test helper, generic).

Used by the plugin tests and by devtests/verify (which import it by path). It knows nothing about any fixture.

Layout written:
    <root>/<legacy_name>/...                         legacy folder (files copied in by the caller)
    <root>/migration/{graph.json, inventory.json, params.yaml, tolerances.yaml, known-differences.md}
    <root>/migration/runs/<run_id>/snapshot/{graph.json, inventory.json, params.yaml, tolerances.yaml,
                                             known-differences.md}
    <root>/migration/runs/<run_id>/{manifest.json, outputs/<step>/<file>, verify/}
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

_LIB = Path(__file__).resolve().parents[3] / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import axis_common as ac  # noqa: E402

STARTED_AT = "2026-10-16T10:00:00Z"
ENDED_AT = "2026-10-16T10:00:09Z"


def step(sid, scope="migrate", kind="script"):
    return {"id": sid, "title": sid, "kind": kind, "tool": "python", "scope": scope, "scope_reason": None,
            "legacy": {"source": f"scripts/{sid}.py", "lines": [[1, 10]], "executed": "yes"}, "ops": [],
            "params": [], "inputs": [], "outputs": [], "new": {"module": f"pipeline/{sid}.py", "function": "run"},
            "plan": None}


def dataset(did, kind, role, legacy_path=None, *, layer=None, sheet=None, produced_by=None, new_path=None,
            new_layer="__same__", compare=None, legacy_in_memory=False, reference=None, exists=None):
    if new_path is None and produced_by and legacy_path:
        new_path = f"{produced_by}/{Path(legacy_path).name}"
    return {
        "id": did, "title": did, "kind": kind, "role": role, "persistence": "file",
        "legacy": {"path": legacy_path, "layer": layer, "sheet": sheet,
                   "exists": bool(legacy_path) if exists is None else exists},
        "new": ({"path": new_path, "layer": layer if new_layer == "__same__" else new_layer} if new_path else None),
        "produced_by": {"step": produced_by, "op": f"{produced_by}.op1"} if produced_by else None,
        "consumed_by": [],
        "reference": reference or {"source": "legacy" if legacy_path else "none", "match_method": "lineage",
                                   "via_dataset": None, "confirmed_by": None},
        "legacy_in_memory": legacy_in_memory, "no_reference_reason": None,
        "compare": compare or {},
        "verify": {"waiver": None},
    }


def graph(steps, datasets, *, reads=(), invariants=(), legacy_output_dirs=("out",), legacy_name="legacy",
          edge_evidence=True):
    edges = []
    for d in datasets:
        pb = d.get("produced_by")
        if pb:
            ev = [{"state": "structurally_inferred", "ref": f"scripts/{pb['step']}.py:5", "summary": "write",
                   "by": "scan", "rev": 1}] if edge_evidence else []
            edges.append({"id": f"e.{pb['step']}>{d['id']}", "from": pb["step"], "to": d["id"], "kind": "writes",
                          "state": "structurally_inferred", "conflict": False, "evidence": ev})
    for (sid, did) in reads:
        edges.append({"id": f"e.{did}>{sid}", "from": did, "to": sid, "kind": "reads",
                      "state": "structurally_inferred", "conflict": False,
                      "evidence": [{"state": "structurally_inferred", "ref": f"scripts/{sid}.py:2"}]})
    for s in steps:
        s["outputs"] = sorted(d["id"] for d in datasets if (d.get("produced_by") or {}).get("step") == s["id"])
        s["inputs"] = sorted(did for (sid, did) in reads if sid == s["id"])
    return {"schema": "axis-migrate.graph.v1",
            "project": {"name": "synthetic", "legacy_root": f"../{legacy_name}", "created_at": "2026-10-13T00:00:00Z"},
            "revision": 1, "steps": steps, "datasets": datasets, "edges": edges, "questions": [],
            "invariants": list(invariants), "legacy_output_dirs": list(legacy_output_dirs), "ladder": {}}


def _inventory(legacy_root: Path, g: dict) -> dict:
    files = []
    for p in sorted(legacy_root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(legacy_root).as_posix()
            files.append({"path": rel, "size": p.stat().st_size, "sha256": ac.sha256_file(p),
                          "content_sha256": ac.content_sha256(p), "kind": p.suffix.lstrip("."),
                          "sidecar_of": None})
    dsets = []
    for d in g["datasets"]:
        lp = d["legacy"]["path"]
        dsets.append({"id": d["id"], "legacy_path": lp, "layer": d["legacy"].get("layer"), "kind": d["kind"],
                      "exists": bool(lp and (legacy_root / lp).exists()), "role": d["role"],
                      "reference_candidate": d["role"] in ("intermediate", "final")})
    return {"schema": "axis-migrate.inventory.v1", "legacy_root": "../legacy", "files": files, "datasets": dsets,
            "scan": {"read_only_verified": True}}


def write_yaml_text(path: Path, text: str | None):
    if text is None:
        if path.exists():
            path.unlink()
        return
    path.write_text(text, encoding="utf-8")


def build(root: Path, g: dict, *, outputs: dict, tolerances: str | None = None, kd: str | None = None,
          params: str | None = None, run_id: str = "r20261016T100000Z-deadbeef", chain: str = "chained",
          started_at: str = STARTED_AT, step_status: dict | None = None, ledger_drop: tuple = (),
          written_by: dict | None = None, extra_outputs: dict | None = None, legacy_name: str = "legacy",
          inventory_roles: dict | None = None, proposals: str | None = None) -> Path:
    """outputs: {ds_id: Path to a file (copied to outputs/<new.path>)}; legacy files must already be in
    <root>/<legacy_name>/. Returns the run dir."""
    mig = root / "migration"
    mig.mkdir(parents=True, exist_ok=True)
    legacy_root = root / legacy_name
    inv = _inventory(legacy_root, g)
    if inventory_roles:
        for d in inv["datasets"]:
            if d["legacy_path"] in inventory_roles:
                d["role"] = inventory_roles[d["legacy_path"]]
        for path, role in inventory_roles.items():
            if not any(d["legacy_path"] == path for d in inv["datasets"]):
                inv["datasets"].append({"id": "ds.x_" + ac.slug(path), "legacy_path": path, "role": role,
                                        "exists": True})
    params = params if params is not None else "version: 1\nparams:\n- {id: p.measure_crs, node: s1, kind: crs, value: 'EPSG:32632', source: answer}\n"
    ac.dump_json(mig / "graph.json", g)
    ac.dump_json(mig / "inventory.json", inv)
    write_yaml_text(mig / "params.yaml", params)
    write_yaml_text(mig / "tolerances.yaml", tolerances)
    write_yaml_text(mig / "known-differences.md", kd)
    write_yaml_text(mig / "tolerances.proposed.yaml", proposals)
    run = mig / "runs" / run_id
    if run.exists():
        shutil.rmtree(run)
    snap = run / "snapshot"
    snap.mkdir(parents=True)
    for name in ("graph.json", "inventory.json", "params.yaml", "tolerances.yaml", "known-differences.md",
                 "tolerances.proposed.yaml"):
        if (mig / name).exists():
            shutil.copy2(mig / name, snap / name)
    (run / "verify").mkdir()
    step_status = step_status or {}
    written_by = written_by or {}
    by_id = {d["id"]: d for d in g["datasets"]}
    m_outputs, steps = [], {}
    for s in g["steps"]:
        if s["scope"] != "migrate":
            continue
        st = step_status.get(s["id"], "succeeded")
        if st == "absent":
            continue
        steps[s["id"]] = {"step_id": s["id"], "status": st, "exit_code": 0 if st == "succeeded" else 10,
                          "reason_codes": [], "outputs": [], "writes_ledger": []}
    all_out = dict(outputs)
    all_out.update(extra_outputs or {})
    for did, src in all_out.items():
        d = by_id.get(did)
        if d is not None and d.get("new"):
            rel = "outputs/" + d["new"]["path"]
            wstep = written_by.get(did, d["produced_by"]["step"])
        else:
            wstep = written_by.get(did, g["steps"][0]["id"])
            rel = f"outputs/{wstep}/{Path(src).name}"
        dst = run / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if Path(src).is_dir():
            shutil.copytree(src, dst)
            sha = ac.tree_hash(dst)
        else:
            shutil.copy2(src, dst)
            sha = ac.sha256_file(dst)
        rec = {"dataset_id": did, "path": rel, "sha256": sha, "written_by_step": wstep}
        m_outputs.append(rec)
        if wstep in steps:
            steps[wstep]["outputs"].append({"dataset_id": did, "path": rel, "sha256": sha})
            if did not in ledger_drop:
                steps[wstep]["writes_ledger"].append({"path": rel, "sha256": sha})

    def _sha(p):
        return ac.sha256_file(p) if p.exists() else None
    manifest = {
        "schema": "axis-migrate.run-manifest.v1", "run_id": run_id, "chain": chain, "executor": "local",
        "status": "succeeded" if all(s["status"] == "succeeded" for s in steps.values()) else "failed",
        "exit_code": 0, "reason_codes": [], "steps_planned": len(steps),
        "steps_succeeded": sum(1 for s in steps.values() if s["status"] == "succeeded"), "full": chain == "chained",
        "read_ledger": "strace",
        "code": {"code_hash": "sha256:" + "0" * 64, "files": [], "worktree_changed_during_run": False},
        "params": {"sha256": _sha(snap / "params.yaml")}, "graph": {"sha256": _sha(snap / "graph.json")},
        "tolerances": {"sha256": _sha(snap / "tolerances.yaml")},
        "known_differences": {"sha256": _sha(snap / "known-differences.md")},
        "inputs": [], "legacy_feeds": [], "steps": list(steps.values()), "outputs": m_outputs,
        "started_at": started_at, "ended_at": ENDED_AT, "resumed_from": None,
    }
    ac.dump_json(run / "manifest.json", manifest)
    return run


def edit_live(run_dir: Path, name: str, text: str):
    (Path(run_dir).parents[1] / name).write_text(text, encoding="utf-8")


def live_graph(run_dir: Path) -> dict:
    return json.loads((Path(run_dir).parents[1] / "graph.json").read_text())


def set_live_graph(run_dir: Path, g: dict):
    ac.dump_json(Path(run_dir).parents[1] / "graph.json", g)


def clone(obj):
    return copy.deepcopy(obj)
