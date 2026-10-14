"""Build the S1 T0 calibration outputs: the golden pipeline (clean), legitimate alternatives and mutants, each run
through the REAL runner on a copy of the real-QGIS legacy (dev/eval only).

    axpy evals/calib/s1/build_variants.py [--only NAME]…

Each variant = evals/golden/s1/pipeline + one patch (alt: evals/calib/s1/alt/*.patch; mutant: evals/mutants/*.patch;
probe: evals/calib/s1/probe/*.patch, a deliberately wrong variant measured only for nearest_wrong),
assembled by evals/golden/s1/make_project.py into evals/work/calib-s1/<variant>/ and run chained with run_local.py.
The outputs stay under evals/work (git-ignored, ~3 MB per variant); evals/calib/s1/variants.json pins every output by
sha256 and content_sha256 so run_t0.py can prove a rebuilt set is the calibrated one.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLUGIN = REPO / "axis-migrate"
AXPY = PLUGIN / "bin" / "axpy"
RUN_LOCAL = PLUGIN / "skills" / "run" / "run_local.py"
MAKE = REPO / "evals" / "golden" / "s1" / "make_project.py"
WORK = REPO / "evals" / "work" / "calib-s1"
MUT = REPO / "evals" / "mutants"
ALT = HERE / "alt"
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

# name: (trap, role, patch or None)
VARIANTS = {
    "clean": (None, "clean", None),
    "T3alt_quad8": ("T3", "alt", ALT / "T3alt_quad8.patch"),
    "S1-T3": ("T3", "mutant", MUT / "S1-T3.patch"),
    "T4alt_lookup": ("T4", "alt", ALT / "T4alt_lookup.patch"),
    "S1-T4": ("T4", "mutant", MUT / "S1-T4.patch"),
    "S1-T4drop": ("T4", "mutant", MUT / "S1-T4drop.patch"),
    "T5alt_utm": ("T5", "alt", ALT / "T5alt_utm.patch"),
    "T5alt_aeqd": ("T5", "alt", ALT / "T5alt_aeqd.patch"),
    "S1-T5": ("T5", "mutant", MUT / "S1-T5.patch"),
    # nearest-wrong probe for the s04 area attributes (no catalogue trap moves the commune areas)
    "W_area_3035": ("W-area", "probe", HERE / "probe" / "W_area_3035.patch"),
}


def unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


def build(name: str) -> dict:
    trap, role, patch = VARIANTS[name]
    dest = WORK / name
    if dest.exists():
        unlock(dest)
        shutil.rmtree(dest)
    cmd = [str(AXPY), str(MAKE), str(dest)] + (["--patch", str(patch)] if patch else [])
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    cp = subprocess.run([str(AXPY), str(RUN_LOCAL), str(dest / "migration"), "--json"], capture_output=True, text=True)
    if cp.returncode != 0:
        raise SystemExit(f"{name}: runner exit {cp.returncode}\n{cp.stdout[-2000:]}\n{cp.stderr[-2000:]}")
    s = json.loads(cp.stdout)
    run = Path(s["run_dir"])
    man = json.loads((run / "manifest.json").read_text())
    outs = {}
    for o in man["outputs"]:
        p = run / o["path"]
        outs[o["dataset_id"]] = {"path": str(p.relative_to(REPO)), "sha256": o["sha256"],
                                 "content_sha256": ac.content_sha256(p)}
    return {"trap": trap, "role": role, "patch": str(patch.relative_to(REPO)) if patch else None,
            "patch_sha256": ac.sha256_file(patch) if patch else None, "run_id": man["run_id"],
            "code_hash": man["code"]["code_hash"], "wall_time_s": s["wall_time_s"], "outputs": outs}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", action="append", default=[])
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    pin = HERE / "variants.json"
    doc = json.loads(pin.read_text()) if pin.exists() else {"schema": "axis-migrate.calib-variants.v1", "variants": {}}
    for name in (a.only or list(VARIANTS)):
        doc["variants"][name] = build(name)
        print(f"{name:14s} {doc['variants'][name]['run_id']} {doc['variants'][name]['wall_time_s']:.1f} s")
    pin.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
