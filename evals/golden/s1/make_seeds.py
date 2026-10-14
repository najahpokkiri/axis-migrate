"""(Re)write the seeded S1 case material: cases/<id>/seed/migration/ from make_project.py (real scan + lineage +
graph ops, golden pipeline, optional mutant patch), without runs/ and without the legacy folder (run.py supplies it).
The seed's tolerances.yaml carries the T0 proposals (evals/calib/s1/tolerances.proposed.yaml) UNSIGNED, with the
evidence pointers rewritten so no eval-repo path enters the workspace; only Najah may sign them (agreed_by).

    axpy evals/golden/s1/make_seeds.py
"""
from __future__ import annotations

import shutil
import subprocess
import yaml
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
AXPY = REPO / "axis-migrate" / "bin" / "axpy"
CASES = REPO / "evals" / "cases"
PROPOSED = REPO / "evals" / "calib" / "s1" / "tolerances.proposed.yaml"
# case: (patch, carry the T0 proposals). S1-T5 gets none: their nearest-wrong labels would hint at the planted defect.
SEEDS = {"S1-T5": (REPO / "evals" / "mutants" / "S1-T5.patch", False),   # golden + planted Web Mercator buffer
         "PC-S1": (None, True)}                                          # clean positive control: golden as is
LABEL = {"clean": "golden implementation", "T3alt_quad8": "buffer with 8 segments per quarter circle",
         "T4alt_lookup": "class->distance lookup", "T5alt_utm": "buffer in EPSG:32632",
         "T5alt_aeqd": "buffer in a local azimuthal equidistant CRS", "S1-T3": "outline simplified (50 m) before buffer",
         "S1-T4": "industrial buffered at the default distance", "S1-T4drop": "industrial missing from the merge",
         "S1-T5": "buffer in EPSG:3857", "W_area_3035": "areas measured in EPSG:3035"}


def main() -> int:
    for case, (patch, carry) in SEEDS.items():
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "project"
            cmd = [str(AXPY), str(HERE / "make_project.py"), str(dest), "--no-legacy"]
            if patch:
                cmd += ["--patch", str(patch)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            out = CASES / case / "seed" / "migration"
            if out.exists():
                shutil.rmtree(out)
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(dest / "migration", out, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "runs"))
            if not carry:
                print(case, "->", out.relative_to(REPO), "(tolerances.yaml empty)")
                continue
            doc = yaml.safe_load(PROPOSED.read_text())
            for r in doc["records"]:
                for k in ("correct_spread", "nearest_wrong"):
                    if r.get(k):
                        r[k]["variant"] = LABEL.get(r[k].get("variant"), r[k].get("variant"))
                        r[k]["evidence"] = "T0 calibration of this project, measured outside this workspace"
                        r[k]["how"] = r[k]["how"].split(" (compare.py")[0].split("; masked")[0].split(" (masked")[0]
                assert r["agreed_by"] is None and r["agreed_at"] is None
            (out / "tolerances.yaml").write_text(
                "# Tolerance records proposed from the T0 calibration of this project (two measured numbers each).\n"
                "# UNSIGNED: agreed_by stays null until a person signs; unsigned records are ignored by compare.py.\n"
                + yaml.safe_dump(doc, sort_keys=False))
            print(case, "->", out.relative_to(REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
