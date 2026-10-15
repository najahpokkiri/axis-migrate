"""Build the golden lux outputs with the real runner (dev/eval only).

    axpy evals/golden/lux/build_golden.py [--work evals/work/golden]

1. assembles a project (fixture copy + migration/ with the lux graph fixture and this golden pipeline);
2. runs `run_local.py` chained (strace ledger, sealed run);
3. checks parity against the legacy files (check_parity.py);
4. copies outputs/<step>/… to evals/golden/lux/out/<step>/… and writes out/golden.json (run id, code hash, wall
   time, per-dataset sha256 and parity).
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
AXPY = REPO / "axis-migrate" / "scripts" / "axpy"
RUN_LOCAL = REPO / "axis-migrate" / "skills" / "run" / "run_local.py"
MAKE = REPO / "devtests" / "run" / "lux_project" / "make_lux_project.py"


def unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(REPO / "evals" / "work" / "golden"))
    a = ap.parse_args()
    work = Path(a.work)
    if work.exists():
        unlock(work)
        shutil.rmtree(work)
    subprocess.run([str(AXPY), str(MAKE), "--assemble", str(work)], check=True, capture_output=True)
    t = time.monotonic()
    cp = subprocess.run([str(AXPY), str(RUN_LOCAL), str(work / "migration"), "--json"], capture_output=True, text=True)
    wall = time.monotonic() - t
    if cp.returncode != 0:
        print(cp.stdout, cp.stderr, file=sys.stderr)
        return cp.returncode
    summary = json.loads(cp.stdout)
    run = Path(summary["run_dir"])
    man = json.loads((run / "manifest.json").read_text())
    par = subprocess.run([str(AXPY), str(HERE / "check_parity.py"), str(run), "--json"], capture_output=True,
                         text=True)
    parity = json.loads(par.stdout)
    out = HERE / "out"
    if out.exists():
        shutil.rmtree(out)
    for step in sorted({o["written_by_step"] for o in man["outputs"]}):
        shutil.copytree(run / "outputs" / step, out / step)
    unlock(out)
    golden = {"schema": "axis-migrate.golden-outputs.v1", "case": "lux-exposure-legacy",
              "run_id": man["run_id"], "code_hash": man["code"]["code_hash"], "lock_hash": man["env"]["lock_hash"],
              "native": man["env"]["native"], "read_ledger": man["read_ledger"], "wall_time_s": round(wall, 2),
              "steps": {s["step_id"]: {"duration_s": s["duration_s"], "peak_rss_mb": s["peak_rss_mb"]}
                        for s in man["steps"]},
              "outputs": {o["dataset_id"]: {"path": o["path"][len("outputs/"):], "sha256": o["sha256"],
                                            "content_sha256": o.get("content_sha256")} for o in man["outputs"]},
              "parity_vs_legacy": {k: {"exact": v["exact"], **({"diffs": v["attribute_diffs"]}
                                                                 if "attribute_diffs" in v else {})}
                                   for k, v in parity["results"].items()},
              "note": "area_m2 in 02_res_2169/02_res_4326 differs from QGIS's planar area by <= 8.7e-13 relative "
                      "(GEOS vs QGIS summation); every other value, geometry and raster cell is exact."}
    (out / "golden.json").write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
    print(f"golden run {man['run_id']}: {parity['exact']}/{parity['pairs']} exact, wall {wall:.1f} s → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
