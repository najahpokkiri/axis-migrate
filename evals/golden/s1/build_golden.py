"""Build and verify the golden S1 pipeline with the real runner and the real comparator (dev/eval only).

    axpy evals/golden/s1/build_golden.py [--work evals/work/s1-golden]

1. make_project.py assembles the project (real scan.py + lineage.py + graph ops, golden pipeline);
2. migration/tolerances.yaml gets the PROPOSED, UNSIGNED records measured in T0 (evals/calib/s1/tolerances.proposed.yaml);
3. run_local.py runs it chained (strace ledger);
4. compare.py run grades every paired dataset against the real-QGIS legacy files; unsigned records are ignored, so the
   float-noise area attributes come out NEEDS_DECISION (TOLERANCE_UNSIGNED) until Najah signs them;
5. compare.py aggregate; writes evals/golden/s1/out/golden.json (run id, code hash, timings, per-dataset sha256,
   verdicts and the measured spreads). The outputs themselves stay in the work dir (re-buildable, ~4 MB).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLUGIN = REPO / "axis-migrate"
AXPY = PLUGIN / "scripts" / "axpy"
RUN_LOCAL = PLUGIN / "skills" / "run" / "run_local.py"
COMPARE = PLUGIN / "skills" / "verify" / "compare.py"
PROPOSED = REPO / "evals" / "calib" / "s1" / "tolerances.proposed.yaml"
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402


def unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", default=str(REPO / "evals" / "work" / "s1-golden"))
    a = ap.parse_args()
    work = Path(a.work)
    if work.exists():
        unlock(work)
        shutil.rmtree(work)
    subprocess.run([str(AXPY), str(HERE / "make_project.py"), str(work)], check=True, capture_output=True)
    mig = work / "migration"
    shutil.copyfile(PROPOSED, mig / "tolerances.yaml")
    t = time.monotonic()
    cp = subprocess.run([str(AXPY), str(RUN_LOCAL), str(mig), "--json"], capture_output=True, text=True)
    wall = time.monotonic() - t
    if cp.returncode != 0:
        print(cp.stdout, cp.stderr, file=sys.stderr)
        return cp.returncode
    run = Path(json.loads(cp.stdout)["run_dir"])
    man = json.loads((run / "manifest.json").read_text())
    cr = subprocess.run([str(AXPY), str(COMPARE), "run", "--run", str(run), "--json"], capture_output=True, text=True)
    comp = json.loads(cr.stdout)
    ag = subprocess.run([str(AXPY), str(COMPARE), "aggregate", "--run", str(run), "--json"], capture_output=True,
                        text=True)
    verdict = json.loads(ag.stdout)
    spreads = {}
    for ds in comp["datasets"]:
        r = json.loads((run / "verify" / "compare" / f"{ds}.json").read_text())
        for c in r["checks"]:
            if c["state"] != "PASS_EXACT":
                spreads.setdefault(ds, {})[c["id"]] = {"state": c["state"], "codes": c["reason_codes"],
                                                       "value": c.get("value"), "stats": c.get("stats"),
                                                       "proposed": c.get("proposed")}
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    golden = {"schema": "axis-migrate.golden-outputs.v1", "case": "s1-buffers-legacy",
              "run_id": man["run_id"], "code_hash": man["code"]["code_hash"], "lock_hash": man["env"]["lock_hash"],
              "native": man["env"]["native"], "read_ledger": man["read_ledger"], "wall_time_s": round(wall, 2),
              "steps": {s["step_id"]: {"duration_s": s["duration_s"], "peak_rss_mb": s["peak_rss_mb"]}
                        for s in man["steps"]},
              "outputs": {o["dataset_id"]: {"path": o["path"][len("outputs/"):], "sha256": o["sha256"],
                                            "content_sha256": ac.content_sha256(run / o["path"])}
                          for o in man["outputs"]},
              "tolerances": {"file": str(PROPOSED.relative_to(REPO)), "sha256": ac.sha256_file(PROPOSED),
                             "signed": False},
              "compare_run": {"exit": comp["exit"], "datasets": comp["datasets"]},
              "aggregate": {"pipeline": verdict["pipeline"],
                            "steps": {k: {"verdict": v["verdict"], "reason_codes": v["reason_codes"]}
                                      for k, v in verdict["steps"].items()}},
              "non_exact_checks": spreads,
              "note": "Every geometry (buffers, intersections, dissolves) is identical to QGIS's; the only differences "
                      "are the planar areas in buffer_area_by_commune.gpkg (QGIS's own shoelace vs GEOS: max rel "
                      "3.5e-14), which stay NEEDS_DECISION until the proposed records are signed."}
    (out / "golden.json").write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    print(f"golden run {man['run_id']} wall {wall:.1f} s; compare exit {comp['exit']}:")
    for ds, x in sorted(comp["datasets"].items()):
        print(f"  {ds:34s} {x['verdict']:22s} {','.join(x['reason_codes'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
