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

AXPY = PLUGIN / "scripts" / "axpy"
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
    cp = review_add(run, tmp_path, vb.view_receipt(doc, "ds.e", engine="cog-tiler-wasm"))
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
    # ds.e: no worst view (max|d| = 0) is required; its 9 legacy-only cells must be flagged
    ce = vb.entry(doc, "ds.e")["validity_counts"]
    cp = review_add(run, tmp_path, vb.view_receipt(doc, "ds.e"))
    assert cp.returncode == 2 and "'nothing found' refused" in cp.stderr
    cp = review_add(run, tmp_path, vb.view_receipt(
        doc, "ds.e", flag=True, cells=("edge", "interior"),
        observations=[{"what": "valid on legacy only (blue)", "value": ce["1"], "unit": "cells"}],
        location={"crs": "EPSG:4326", "bbox": vb.entry(doc, "ds.e")["bounds_4326"]}))
    assert cp.returncode == 0, cp.stderr
    assert vrc.RunReceipts(run).missing_reviews() == ["ds.v"]
    cp = review_add(run, tmp_path, vb.view_receipt(doc, "ds.v", flag=True))
    assert cp.returncode == 0, cp.stderr
    # ds.a is identical to legacy: no views, and the skip counts as its review (Stop gate rule)
    assert vrc.skip_counts(run, "ds.a") and vrc.RunReceipts(run).missing_reviews() == []
    # views.py again: same bytes, so the receipts still count
    cp = run_cli("views.py", "run", "--run", run)
    assert cp.returncode == 0, cp.stderr
    assert vrc.RunReceipts(run).missing_reviews() == []
    cp = subprocess.run([str(AXPY), str(COMPARE), "aggregate", "--run", str(run)], capture_output=True, text=True,
                        timeout=300)
    v = json.loads((run / "verify" / "verdict.json").read_text())
    d = v["datasets"]
    assert not v["visual"]["rejected"]
    assert d["ds.b"]["verdict"] == d["ds.b"]["compare_verdict"] != "PASS_EXACT" and d["ds.b"]["visual"]["flags"]
    assert d["ds.v"]["visual"]["flags"] and d["ds.v"]["verdict"] not in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")
    assert d["ds.e"]["visual"]["flags"]
    st = subprocess.run([str(AXPY), str(PLOT), "review-status", "--run", str(run), "--json"], capture_output=True,
                        text=True, timeout=300)
    assert set(json.loads(st.stdout)["lacking"]) <= {"ds.a"}


@pytest.mark.xfail(strict=False, reason="compare.py aggregate, ladder.py and plot.py review-status must count "
                                        "visual_receipt.skip_counts (identical / reviewed before) like a receipt")
def test_identical_skip_counts_in_aggregate(run):
    subprocess.run([str(AXPY), str(COMPARE), "aggregate", "--run", str(run)], capture_output=True, text=True,
                   timeout=300)
    d = json.loads((run / "verify" / "verdict.json").read_text())["datasets"]
    assert d["ds.a"]["verdict"] == "PASS_EXACT" and "VISUAL_REVIEW_MISSING" not in d["ds.a"]["reason_codes"]
    st = subprocess.run([str(AXPY), str(PLOT), "review-status", "--run", str(run), "--json"], capture_output=True,
                        text=True, timeout=300)
    assert json.loads(st.stdout)["lacking"] == []


def test_reviewed_before_carries_by_sha(tmp_path):
    """A dataset whose legacy and new bytes equal an earlier run's, looked at there with nothing found, gets no views
    in the later run and counts as reviewed; a flag in the earlier run is never carried over."""
    import shutil
    r1 = synth_map.build(tmp_path / "proj")
    assert run_cli("views.py", "run", "--run", r1).returncode == 0
    doc = views_doc(r1)
    for f in (vb.view_receipt(doc, "ds.v"),                               # ds.v: looked, nothing found
              vb.view_receipt(doc, "ds.b", flag=True, cells=("edge",),   # ds.b: flagged
                              observations=[{"what": "legacy only", "value": 1, "unit": "cells"}],
                              location={"crs": "EPSG:4326", "bbox": vb.entry(doc, "ds.b")["bounds_4326"]})):
        cp = review_add(r1, tmp_path, f)
        assert cp.returncode == 0, cp.stderr
    rid2 = "r20261016T110000Z-cafef00d"
    r2 = r1.parent / rid2
    shutil.copytree(r1, r2, ignore=shutil.ignore_patterns("views", "visual-review.json", "plots"))
    for p in [r2 / "manifest.json", *(r2 / "verify" / "compare").glob("*.json")]:
        p.write_text(p.read_text().replace(r1.name, rid2))
    cp = run_cli("views.py", "run", "--run", r2)
    assert cp.returncode == 0, cp.stderr
    d2 = views_doc(r2)
    sk = {s["dataset"]: s for s in d2["skipped"]}
    assert sk["ds.v"]["kind"] == "reviewed_before" and sk["ds.v"]["carried_from"] == r1.name
    assert sk["ds.a"]["kind"] == "identical"
    assert {e["dataset"] for e in d2["datasets"]} == {"ds.b", "ds.e"}    # the flagged ds.b is looked at again
    assert vrc.skip_counts(r2, "ds.v") and vrc.skip_counts(r2, "ds.a") and not vrc.skip_counts(r2, "ds.b")
    assert set(vrc.RunReceipts(r2).missing_reviews()) == {"ds.b", "ds.e"}
    # the earlier receipt stops counting (its project changed): the carry-over stops too
    p = r1 / vb.entry(doc, "ds.v")["views"]["overview"]["project"]
    p.write_text(p.read_text() + " ")
    assert not vrc.skip_counts(r2, "ds.v")
