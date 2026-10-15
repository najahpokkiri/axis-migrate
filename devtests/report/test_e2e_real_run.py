"""End-to-end with the real runner and comparator (dev-only): run/tests/synth project → run_local.py → compare.py
run → map.py display → stand-in plots + receipts (plot.py not built yet) → seal.py → ladder.py → report.py.

Run: axis-migrate/scripts/axpy -m pytest devtests/report -q
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

DEV = Path(__file__).resolve().parents[2]
PLUGIN = DEV / "axis-migrate"
AXPY = str(PLUGIN / "scripts" / "axpy")
sys.path.insert(0, str(PLUGIN / "skills" / "report"))
sys.path.insert(0, str(PLUGIN / "lib"))

pytestmark = pytest.mark.skipif(not (PLUGIN / "skills" / "verify" / "compare.py").is_file()
                                or not (PLUGIN / "skills" / "run" / "run_local.py").is_file(),
                                reason="needs run_local.py and compare.py")


def sh(*args, ok=(0,)):
    cp = subprocess.run([AXPY, *map(str, args)], capture_output=True, text=True)
    assert cp.returncode in ok, (args, cp.stdout[-2000:], cp.stderr[-2000:])
    return cp


def stand_in_plots_and_review(run_dir: Path):
    v = run_dir / "verify"
    findings = []
    for f in sorted((v / "display").glob("*.triptych.png")):
        ds = f.name.split(".triptych")[0]
        d = v / "plots" / ds
        d.mkdir(parents=True, exist_ok=True)
        (d / "overview.png").write_bytes(f.read_bytes())
        sha = hashlib.sha256((d / "overview.png").read_bytes()).hexdigest()
        (d / "plots.json").write_text(json.dumps({"dataset_id": ds, "png": [{"name": "overview.png", "sha256": sha}]}))
        findings.append({"finding_id": f"vf-{ds}", "run_id": run_dir.name, "dataset_id": ds, "question_id": "Q1", "answer": "no",
                         "effect": "none", "category": "none", "plot": f"plots/{ds}/overview.png",
                         "plot_sha256": sha, "note": "nothing visible"})
    for f in sorted((v / "display").glob("*.legacy.geojson")):
        ds = f.name.split(".legacy")[0]
        d = v / "plots" / ds
        d.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        Image.new("RGB", (40, 30), (200, 200, 200)).save(d / "overlay.png")
        sha = hashlib.sha256((d / "overlay.png").read_bytes()).hexdigest()
        (d / "plots.json").write_text(json.dumps({"dataset_id": ds, "png": [{"name": "overlay.png", "sha256": sha}]}))
        findings.append({"finding_id": f"vf-{ds}", "run_id": run_dir.name, "dataset_id": ds, "question_id": "Q3", "answer": "no",
                         "effect": "none", "category": "none", "plot": f"plots/{ds}/overlay.png",
                         "plot_sha256": sha, "note": "nothing visible"})
    (v / "visual-review.json").write_text(json.dumps({"schema": "axis-migrate.visual-review.v1",
                                                      "rubric_id": "rubric.v1", "findings": findings}))


def test_real_run_to_report(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("run_synth", PLUGIN / "skills" / "run" / "tests" / "synth.py")
    runsynth = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runsynth)
    # graph.json is only ever written by graph.py (GRAPH_OPS.md §8): adjust the draft, then let build_project run
    # graph.init() on it (the run synth's draft already carries approved plans)
    base_doc = runsynth.graph_doc

    def graph_doc():
        g = base_doc()
        for e in g["edges"]:
            if e["kind"] == "writes":
                e["evidence"] = [{"state": "directly_observed", "ref": "scripts/legacy.py:10", "summary": "writes"}]
        for d in g["datasets"]:
            if d["id"] in ("ds.zone_sum", "ds.zone_stats"):
                d.setdefault("compare", {}).update(key=["name"], match="key")
        return g
    runsynth.graph_doc = graph_doc
    mig = Path(runsynth.build_project(tmp_path / "proj"))
    head = mig / ".graph" / "head.json"
    assert head.is_file(), "graph.json must be written by graph.py init"
    sh(PLUGIN / "skills/discover/graph.py", "check-integrity", mig)
    out = json.loads(sh(PLUGIN / "skills/run/run_local.py", mig, "--json").stdout)
    run_dir = Path(out["run_dir"])
    sh(PLUGIN / "skills/verify/compare.py", "run", "--run", run_dir)
    sh(PLUGIN / "skills/report/map.py", "display", "--run", run_dir)
    import ladder
    stand_in_plots_and_review(run_dir)
    sh(PLUGIN / "skills/verify/compare.py", "aggregate", "--run", run_dir)
    sh(PLUGIN / "skills/report/seal.py", mig, "--run", run_dir.name)
    res = ladder.compute(mig)
    assert res["integrity"] == [], res["integrity"]
    assert res["pipeline"]["rung"] == "reference-verified", res["status_line"]
    cp = sh(PLUGIN / "skills/report/report.py", mig, "--json")
    rep = json.loads(cp.stdout)
    assert rep["status"] == "reference-verified"
    # a code edit after the run withdraws nothing below target-ready but marks the report stale
    p = mig / "pipeline" / "s02_zonal_sum.py"
    p.chmod(0o644)
    p.write_text(p.read_text() + "\n# edited after the run\n")
    sh(PLUGIN / "skills/report/report.py", mig, ok=(2,))
    assert "CODE_CHANGED_AFTER_RUN" in {x["code"] for x in ladder.compute(mig)["integrity"]}
