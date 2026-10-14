"""View receipts against REAL views (views.py on the synthetic run): plot.py review-add accepts a receipt whose cells
are read as the identify popup shows them, refuses 'nothing found' on the shifted raster, and compare.py aggregate
and the Stop gate's rule count the receipts; a rebuilt (deterministic) view keeps them valid."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import synth_map
from conftest import PLUGIN, map_python, run_cli, views_doc

sys.path.append(str(PLUGIN / "skills" / "verify" / "tests"))
import _viewsbuilder as vb  # noqa: E402
import visual_receipt as vrc  # noqa: E402

AXPY = PLUGIN / "bin" / "axpy"
PLOT = PLUGIN / "skills" / "verify" / "plot.py"
COMPARE = PLUGIN / "skills" / "verify" / "compare.py"


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> Path:
    map_python()
    r = synth_map.build(tmp_path_factory.mktemp("receipts") / "proj")
    cp = run_cli("views.py", "run", "--run", r)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return r


def review_add(run: Path, tmp: Path, f) -> subprocess.CompletedProcess:
    p = tmp / "finding.json"
    p.write_text(json.dumps(f))
    return subprocess.run([str(AXPY), str(PLOT), "review-add", "--run", str(run), "--json", str(p)],
                          capture_output=True, text=True, timeout=300)


def test_review_loop_on_real_views(run, tmp_path):
    doc = views_doc(run)
    assert all("bounds_4326" in e for e in doc["datasets"])
    # wrong engine and a misread cell are refused
    cp = review_add(run, tmp_path, vb.view_receipt(doc, "ds.a", engine="cog-tiler-wasm"))
    assert cp.returncode == 2 and "maplibre-gl-raster" in cp.stderr
    f = vb.view_receipt(doc, "ds.b", flag=True, cells=("interior",))
    f["cells_read"][0]["legacy"] = f["cells_read"][0]["legacy"] * 1.05 + 1
    cp = review_add(run, tmp_path, f)
    assert cp.returncode == 2 and "wrong cell or wrong layer" in cp.stderr
    # ds.b is new = legacy one cell east: 'nothing found' cannot stand, a flag with the counts can
    cp = review_add(run, tmp_path, vb.view_receipt(doc, "ds.b"))
    assert cp.returncode == 2 and "'nothing found' refused" in cp.stderr
    counts = vb.entry(doc, "ds.b")["validity_counts"]
    flag = vb.view_receipt(doc, "ds.b", flag=True, cells=("edge", "interior", "worst"),
                           observations=[{"what": "valid on legacy only (blue)", "value": counts["1"],
                                          "unit": "cells"},
                                         {"what": "valid on new only (orange)", "value": counts["2"],
                                          "unit": "cells"}],
                           location={"crs": "EPSG:4326", "bbox": vb.entry(doc, "ds.b")["bounds_4326"]})
    cp = review_add(run, tmp_path, flag)
    assert cp.returncode == 0, cp.stderr
    for ds, fl in (("ds.a", False), ("ds.v", True)):
        cp = review_add(run, tmp_path, vb.view_receipt(doc, ds, flag=fl))
        assert cp.returncode == 0, (ds, cp.stderr)
    assert vrc.RunReceipts(run).missing_reviews() == []
    # views.py again: same bytes, so the receipts still count
    cp = run_cli("views.py", "run", "--run", run)
    assert cp.returncode == 0, cp.stderr
    assert vrc.RunReceipts(run).missing_reviews() == []
    cp = subprocess.run([str(AXPY), str(COMPARE), "aggregate", "--run", str(run)], capture_output=True, text=True,
                        timeout=300)
    v = json.loads((run / "verify" / "verdict.json").read_text())
    d = v["datasets"]
    assert not v["visual"]["rejected"]
    assert d["ds.a"]["verdict"] == "PASS_EXACT" and "VISUAL_REVIEW_MISSING" not in d["ds.a"]["reason_codes"]
    assert d["ds.b"]["verdict"] == d["ds.b"]["compare_verdict"] != "PASS_EXACT" and d["ds.b"]["visual"]["flags"]
    assert d["ds.v"]["visual"]["flags"] and d["ds.v"]["verdict"] not in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")
    st = subprocess.run([str(AXPY), str(PLOT), "review-status", "--run", str(run), "--json"], capture_output=True,
                        text=True, timeout=300)
    assert json.loads(st.stdout)["lacking"] == []
