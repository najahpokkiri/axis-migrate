"""View receipts against REAL views (views.py on the synthetic run): plot.py review-add accepts a receipt whose cells
are read as the identify popup shows them, refuses 'nothing found' on the shifted raster, and compare.py aggregate
and the Stop gate's rule count the receipts; a rebuilt (deterministic) view keeps them valid."""
from __future__ import annotations

import shutil
import os
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
AXPY_CMD = ([shutil.which("sh") or "sh"] if os.name == "nt" else []) + [str(AXPY)]  # Windows cannot exec a shell script
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
    return subprocess.run([*AXPY_CMD, str(PLOT), "review-add", "--run", str(run), "--json", str(p)],
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
    cp = subprocess.run([*AXPY_CMD, str(COMPARE), "aggregate", "--run", str(run)], capture_output=True, text=True,
                        timeout=300)
    v = json.loads((run / "verify" / "verdict.json").read_text())
    d = v["datasets"]
    assert not v["visual"]["rejected"]
    assert d["ds.b"]["verdict"] == d["ds.b"]["compare_verdict"] != "PASS_EXACT" and d["ds.b"]["visual"]["flags"]
    assert d["ds.v"]["visual"]["flags"] and d["ds.v"]["verdict"] not in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")
    assert d["ds.e"]["visual"]["flags"]
    st = subprocess.run([*AXPY_CMD, str(PLOT), "review-status", "--run", str(run), "--json"], capture_output=True,
                        text=True, timeout=300)
    assert json.loads(st.stdout)["lacking"] == []                           # ds.a: skipped: identical (sha)


def test_identical_skip_receipt(run, tmp_path):
    """ds.a (identical to legacy): views.json says `skipped: identical (<sha>)`; review-status counts it complete
    without opening anything; review-template prints the skipped receipt; review-add accepts it only with the real
    sha (a made-up sha is refused), and compare.py aggregate then counts it as the dataset's receipt."""
    sha = json.loads((run / "verify" / "compare" / "ds.a.json").read_text())["new"]["sha256"]
    rec = {s["dataset"]: s for s in views_doc(run)["skipped"]}["ds.a"]
    assert rec["skipped"] == f"skipped: identical ({sha})" and rec["reason"].startswith(f"identical ({sha})")
    st = json.loads(subprocess.run([*AXPY_CMD, str(PLOT), "review-status", "--run", str(run), "--json"],
                                   capture_output=True, text=True, timeout=300).stdout)
    assert "ds.a" not in st["lacking"] and st["datasets"]["ds.a"]["via"] == "skipped"
    tp = subprocess.run([*AXPY_CMD, str(PLOT), "review-template", "--run", str(run), "--dataset", "ds.a"],
                        capture_output=True, text=True, timeout=300)
    tmpl = json.loads(tp.stdout)
    assert tmpl == [{"dataset_id": "ds.a", "skipped": f"skipped: identical ({sha})", "note": "", "model": ""}]
    cp = review_add(run, tmp_path, {**tmpl[0], "model": ""})
    assert cp.returncode == 2 and "model" in cp.stderr
    cp = review_add(run, tmp_path, {**tmpl[0], "skipped": f"skipped: identical ({'0' * 64})", "model": "m"})
    assert cp.returncode == 2 and "not the new output's sha256" in cp.stderr
    cp = review_add(run, tmp_path, {"dataset_id": "ds.b", "skipped": f"skipped: identical ({sha})", "model": "m"})
    assert cp.returncode == 2 and "is not identical" in cp.stderr          # a dataset with differences
    cp = review_add(run, tmp_path, {**tmpl[0], "model": "m"})
    assert cp.returncode == 0, cp.stderr
    subprocess.run([*AXPY_CMD, str(COMPARE), "aggregate", "--run", str(run)], capture_output=True, text=True,
                   timeout=300)
    d = json.loads((run / "verify" / "verdict.json").read_text())["datasets"]
    assert d["ds.a"]["verdict"] == "PASS_EXACT" and "VISUAL_REVIEW_MISSING" not in d["ds.a"]["reason_codes"]
    st = json.loads(subprocess.run([*AXPY_CMD, str(PLOT), "review-status", "--run", str(run), "--json"],
                                   capture_output=True, text=True, timeout=300).stdout)
    assert st["datasets"]["ds.a"]["skip_receipt"] is True


def test_skip_stops_counting_when_the_bytes_change(tmp_path):
    """Anti-fabrication: the skip and the skipped receipt are re-checked against the new output's bytes now; once
    they change (without a new compare) neither counts and the Stop gate's rule wants a look."""
    r = synth_map.build(tmp_path / "proj")
    assert run_cli("views.py", "run", "--run", r).returncode == 0
    sha = json.loads((r / "verify" / "compare" / "ds.a.json").read_text())["new"]["sha256"]
    cp = review_add(r, tmp_path, {"dataset_id": "ds.a", "skipped": f"identical ({sha})", "model": "m"})
    assert cp.returncode == 0, cp.stderr
    rr = vrc.RunReceipts(r)
    skip = next(f for f in rr.findings() if f.get("receipt") == "skipped")
    assert rr.problem(skip) is None and vrc.skip_counts(r, "ds.a") == f"skipped: identical ({sha})"
    assert "ds.a" not in rr.missing_reviews()
    out = vrc.new_output_path(r, json.loads((r / "verify" / "compare" / "ds.a.json").read_text()))
    out.chmod(0o644)
    out.write_bytes(out.read_bytes() + b"\0")
    rr = vrc.RunReceipts(r)
    assert "bytes now hash" in rr.problem(skip)
    assert not vrc.skip_counts(r, "ds.a") and "ds.a" in rr.missing_reviews()
    # a hand-edited views.json skip for a dataset with differences never counts either
    idx = r / "verify" / "views" / "views.json"
    doc = json.loads(idx.read_text())
    bsha = json.loads((r / "verify" / "compare" / "ds.b.json").read_text())["new"]["sha256"]
    doc["skipped"].append({"dataset": "ds.b", "kind": "identical", "skipped": f"skipped: identical ({bsha})",
                           "counts_as_review": True, "legacy_sha256": None, "new_sha256": bsha})
    idx.write_text(json.dumps(doc))
    assert not vrc.skip_counts(r, "ds.b")


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
