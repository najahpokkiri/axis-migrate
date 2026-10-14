"""Lux target devtest (dev-only; encodes lux facts): AT9-01 on a copy of lux with the golden pipeline.

    ~/axis-migrate-dev/axis-migrate/bin/axpy -m pytest ~/axis-migrate-dev/devtests/target -q -s

Path: fixture copy + golden pipeline (run builder's make_lux_project) → devtest-only tolerance records signed by a
TEST identity inside the tmp project → chained run → compare → plots → Q-all receipts → aggregate → seal
(reference-verified) → make_databricks → target_check (dry run timed) → seal --amend --target → ladder target-ready
with the exact 09 §4.3 claim.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lux_target_lib as lt  # noqa: E402

PLUGIN = lt.PLUGIN
TARGET = PLUGIN / "skills" / "target"


def _unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


NO_STRACE = bool(os.environ.get("AXIS_TEST_NO_STRACE")) or (shutil.which("strace") is None
                                                            and not Path("/usr/bin/strace").is_file())
pytestmark = pytest.mark.skipif(NO_STRACE, reason="lux target-ready needs a reference-verified run (strace read "
                                                  "ledger, Linux) and the dry run's seek trace")


@pytest.fixture(scope="module")
def lux_verified(tmp_path_factory):
    root = tmp_path_factory.mktemp("lux_target")
    t0 = time.monotonic()
    mig, run_id = lt.reference_verified_project(root / "proj")
    lad = lt.ladder(mig, run_id)
    assert lad["pipeline"]["rung"] == "reference-verified", lad["status_line"]
    yield mig, run_id, round(time.monotonic() - t0, 1)
    _unlock(root)


def claim_regex() -> re.Pattern:
    t = (TARGET / "templates" / "claim.v1.md").read_text().strip()
    parts = re.split(r"(\$\{\w+\})", t)
    return re.compile("^" + "".join(r"(.+?)" if re.fullmatch(r"\$\{\w+\}", p) else re.escape(p) for p in parts) + "$")


def test_at9_01_lux_reaches_target_ready(lux_verified):
    mig, run_id, verify_s = lux_verified
    out = json.loads(lt.sh(TARGET / "make_databricks.py", mig, "--json").stdout)
    # tasks = non-manual graph steps (m01 manual, s03b dropped); depends_on = graph edges
    assert out["tasks"] == ["s01_prepare_population", "s02_landuse_mask", "s03_zonal_population",
                            "s04_distribute_value"]
    assert "ds.cantons_lux_edit" in out["uploads"]
    bundle = yaml.safe_load((mig / "databricks" / "databricks.yml").read_text())
    job = next(iter(bundle["resources"]["jobs"].values()))
    deps = {t["task_key"]: sorted(d["task_key"] for d in t.get("depends_on") or []) for t in job["tasks"]}
    assert deps == {"s01_prepare_population": [], "s02_landuse_mask": ["s01_prepare_population"],
                    "s03_zonal_population": ["s01_prepare_population", "s02_landuse_mask"],
                    "s04_distribute_value": ["s03_zonal_population"]}
    t0 = time.monotonic()
    cp = lt.sh(TARGET / "target_check.py", mig, "--run", run_id, "--json")
    check_s = round(time.monotonic() - t0, 1)
    res = json.loads(cp.stdout)
    assert res["status"] == "target-ready", res
    tc = json.loads((mig / "databricks" / "target-check.json").read_text())
    st = {c["id"]: c["state"] for c in tc["checks"]}
    assert st == {f"T{i}": "PASS" for i in range(1, 14)}, st
    tm = json.loads((mig / "databricks" / "target-manifest.json").read_text())
    assert tm["deployment_status"] == "not_deployed" and tm["status"] == "target-ready"
    m = claim_regex().match(tm["claim"])
    assert m, tm["claim"]
    assert f"run `{run_id}`" in tm["claim"] and "`13` of `13` step and final outputs" in tm["claim"]
    assert "runtime `dbr-17.3-lts`" in tm["claim"]
    lt.sh(PLUGIN / "skills/report/seal.py", mig, "--run", run_id, "--amend", "--target")
    lad = lt.ladder(mig, run_id)
    assert lad["pipeline"]["rung"] == "target-ready", lad["status_line"]
    assert lad["integrity"] == []
    dry = json.loads((mig / "databricks" / "dryrun" / "dryrun.json").read_text())
    report = {"verify_path_s": verify_s, "target_check_s": check_s, "dryrun_wall_time_s": dry["wall_time_s"],
              "tasks": {t["task_key"]: t["seconds"] for t in dry["tasks"]},
              "venv_s": dry["venv"]["seconds"], "python": dry["venv"]["python_used"],
              "packages_from": dry["venv"].get("packages_from"), "network": dry["network"],
              "status_line": lad["status_line"]}
    print("\nLUX TARGET:", json.dumps(report, indent=1))
