"""Helpers for the lux target devtest (dev-only; encodes lux facts, so it lives outside the plugin).

`reference_verified_project(dest)` assembles a lux project (fixture copy + golden pipeline, via the run builder's
make_lux_project), writes devtest-only tolerance records signed by a TEST identity (inside the tmp project only),
then: run_local (chained) → compare run → plot run → map display → Q-all receipts through plot.py review-add →
compare aggregate → seal. Returns (migration dir, run id).
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "axis-migrate"
AXPY = str(PLUGIN / "scripts" / "axpy")
sys.path.insert(0, str(REPO / "devtests" / "run" / "lux_project"))
import make_lux_project as mlp  # noqa: E402

TARGET_BLOCK = """target:
  platform: databricks
  runtime: dbr-17.3-lts
  compute: job_cluster_single_node
  node_memory_gb: 32
  volume: {catalog: null, schema: null, volume: null}
  io: {no_seek_roots: []}
"""
TEST_SIGNER = "TEST-devtest-identity (not a real signature)"

# The golden pipeline's only non-exact checks (devtests/run golden parity: area_m2 differs at < 1e-12 relative).
# Numbers copied from devtests/verify/lux_tolerance_proposals.yaml (measured there); signed here by a TEST identity.
_SPREAD = {"value": 8.632161145854927e-13, "how": "independent rasterio/geopandas implementation",
           "evidence": "devtests/verify/lux_measurements.json#legit.A1_clean"}
_WRONG = {"value": 3.775732215106478e-05, "variant": "ellipsoidal (WGS84) area instead of planar EPSG:2169",
          "evidence": "devtests/verify/lux_measurements.json#W_s02_area_ellipsoidal"}


def tolerances_doc() -> dict:
    recs = []
    for ds in ("02_res_2169", "02_res_4326"):
        recs.append({"id": f"tol.{ds}.vector.attr:area_m2.v1", "dataset": f"ds.{ds}", "check": "vector.attr:area_m2",
                     "metric": "abs+rel", "atol": 0.0, "rtol": 1.0e-08, "max_mismatch": 0, "units": "1",
                     "basis": "measured_spread", "correct_spread": dict(_SPREAD), "nearest_wrong": dict(_WRONG),
                     "agreed_by": TEST_SIGNER, "agreed_at": "2026-10-01T00:00:00Z", "decision_ref": None,
                     "fixed_before": None})
    return {"version": 1, "defaults": {}, "records": recs}


def sh(*args, ok=(0,), timeout=900) -> subprocess.CompletedProcess:
    cp = subprocess.run([AXPY, *map(str, args)], capture_output=True, text=True, timeout=timeout)
    if cp.returncode not in ok:
        raise AssertionError(f"{args[:2]} exit {cp.returncode}\n{cp.stdout[-3000:]}\n{cp.stderr[-3000:]}")
    return cp


def receipts(run_dir: Path) -> int:
    """One Q-all receipt per plotted dataset (devtest stand-in for the model's visual review)."""
    n = 0
    for pj in sorted((run_dir / "verify" / "plots").glob("*/plots.json")):
        doc = json.loads(pj.read_text())
        ds = pj.parent.name
        pngs = doc.get("png") or []
        if not pngs:
            continue
        seen = [{"plot": f"plots/{ds}/{e['name']}", "plot_sha256": e["sha256"]} for e in pngs]
        f = {"dataset_id": ds, "question_id": "Q-all", "category": "none", "answer": "no", "effect": "none",
             "plot": seen[0]["plot"], "plot_sha256": seen[0]["plot_sha256"], "panel": "all",
             "looked": [{"check": c, "answer": "ok", "where": Path(seen[0]["plot"]).name, "saw": "stand-in: " + c + " checked on " + Path(seen[0]["plot"]).name, **({"number": {"name": "cells valid on one side only", "value": 0, "computed_by": "test stand-in"}} if c == "edges" else {})} for c in ("place", "coverage", "edges", "values", "classes")],
             "note": "devtest stand-in receipt", "model": "devtest-stand-in", "plots_seen": seen,
             "finding_id": f"vf-{ds[3:]}-all", "run_id": run_dir.name}
        fp = run_dir.parent.parent / "analysis" / f"receipt-{ds}.json"
        fp.parent.mkdir(exist_ok=True)
        fp.write_text(json.dumps(f))
        sh(PLUGIN / "skills/verify/plot.py", "review-add", "--run", run_dir, "--json", fp)
        n += 1
    return n


def _bridge_m01(mig: Path) -> None:
    """The fixture marks m01 (manual canton edit) as a migrated step whose output has no legacy reference
    (reference.source none), so the ladder caps it at sandbox-validated (NO_REFERENCE) and the pipeline never reaches
    reference-verified. The demo path (DESIGN §8: 13 pairs, rename not ported) keeps the human's edited file: here m01
    becomes a bridge (the runner copies the legacy file; the ladder shows "partial (1 bridged)"). Written the way
    graph.py writes (lock, write, head record), so graph integrity holds."""
    sys.path.insert(0, str(PLUGIN / "skills" / "discover"))
    import graph as graph_py
    with graph_py.locked(mig):
        g = json.loads((mig / "graph.json").read_text())
        for s in g["steps"]:
            if s["id"] == "m01_cantons_lux_edit":
                s["kind"], s["direction"] = "bridge", "in"
                s["new"]["module"] = None
        g["revision"] = int(g.get("revision", 1)) + 1
        graph_py._write_graph(mig, g)
        graph_py._record_head(mig, g["revision"])
    (mig / "pipeline" / "m01_cantons_lux_edit.py").unlink()


def reference_verified_project(dest: Path) -> tuple[Path, str]:
    mig = mlp.assemble(dest)
    (mig / "tolerances.yaml").write_text(yaml.safe_dump(tolerances_doc(), sort_keys=False))
    with open(mig / "params.yaml", "a", encoding="utf-8") as fh:       # the end-of-interview platform answer
        fh.write(TARGET_BLOCK)
    _bridge_m01(mig)
    out = json.loads(sh(PLUGIN / "skills/run/run_local.py", mig, "--json").stdout.strip().splitlines()[-1])
    run_dir = Path(out["run_dir"])
    sh(PLUGIN / "skills/verify/compare.py", "run", "--run", run_dir, ok=(0, 1))   # UNPAIRED_NEW is listed, not failed
    sh(PLUGIN / "skills/verify/plot.py", "run", "--run", run_dir)
    if (PLUGIN / "skills/report/map.py").is_file():
        sh(PLUGIN / "skills/report/map.py", "display", "--run", run_dir, ok=(0, 1))
    receipts(run_dir)
    sh(PLUGIN / "skills/verify/compare.py", "aggregate", "--run", run_dir, ok=(0, 1))
    sh(PLUGIN / "skills/report/seal.py", mig, "--run", run_dir.name)
    return mig, run_dir.name


def ladder(mig: Path, run_id: str | None = None) -> dict:
    return json.loads(sh(PLUGIN / "skills/report/ladder.py", mig, *(["--run", run_id] if run_id else []),
                         "--json").stdout)


if __name__ == "__main__":
    t0 = time.monotonic()
    m, r = reference_verified_project(Path(sys.argv[1]).resolve())
    lad = ladder(m, r)
    print(json.dumps({"migration": str(m), "run_id": r, "status": lad["status_line"],
                      "blockers": lad["pipeline"]["blockers"], "integrity": lad["integrity"],
                      "s": round(time.monotonic() - t0, 1)}, indent=1, default=str))
