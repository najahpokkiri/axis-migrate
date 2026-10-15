"""05 AT-12 for real: golden lux chained run locally and in E2B; GeoTIFF, CSV and GPKG byte-identical, XLSX
content-identical. Needs E2B_API_KEY in the environment (source the secrets file; never read it here):

    set -a; . ~/.config/axis-migrate/secrets.env; set +a
    ~/axis-migrate-dev/axis-migrate/scripts/axpy -m pytest ~/axis-migrate-dev/devtests/run/test_lux_e2b.py -q -s
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from test_lux_runner import AXPY, PLUGIN, RUN_LOCAL, lux, run  # noqa: F401  (fixture re-export)

RUN_E2B = PLUGIN / "skills" / "run" / "run_e2b.py"

pytestmark = pytest.mark.skipif(not os.environ.get("E2B_API_KEY"), reason="E2B_API_KEY not in the environment")


def test_at12_lux_local_vs_e2b(lux):  # noqa: F811
    code, local = run(lux)
    assert code == 0
    time.sleep(1.05)
    t = time.monotonic()
    cp = subprocess.run([str(AXPY), str(RUN_E2B), str(lux), "--confirm-upload", "--json"], capture_output=True,
                        text=True, timeout=900)
    wall = time.monotonic() - t
    summary = json.loads(cp.stdout.strip().splitlines()[-1])
    man = json.loads((Path(summary["run_dir"]) / "manifest.json").read_text())
    print(f"\nE2B lux chained run {man['run_id']}: wall {wall:.1f} s (manifest {man['wall_time_s']} s), "
          f"sandbox {man['env']['e2b']['sandbox_id']}, up {man['env']['e2b']['uploaded_bytes']} B, "
          f"down {man['env']['e2b']['downloaded_bytes']} B, cpu {man['env']['cpu']['model']}")
    assert cp.returncode == 0, man["reason_codes"]
    assert man["executor"] == "e2b" and man["full"] and man["eligible_for"] == "sandbox-validated"
    e = man["env"]["e2b"]
    assert e["sandbox_destroyed"] is True and e["allow_internet_access"] is False and e["strace_in_sandbox"]
    lo = {o["dataset_id"]: o for o in local["outputs"]}
    eo = {o["dataset_id"]: o for o in man["outputs"]}
    assert set(lo) == set(eo) and len(lo) == 14
    for ds in lo:
        if ds.endswith("_xlsx"):
            assert lo[ds]["content_sha256"] == eo[ds]["content_sha256"], ds
        else:
            assert lo[ds]["sha256"] == eo[ds]["sha256"], ds
    assert all(not s["reads"]["undeclared"] for s in man["steps"])
