"""plot.py review-add with VIEW receipts (GeoLibre map views) and how compare.py aggregate counts them.

Synthetic runs and stand-in view indexes only (_viewsbuilder.py); the real views are tested in skills/map/tests.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
VERIFY = HERE.parent
PLUGIN = VERIFY.parents[1]
for p in (str(VERIFY), str(PLUGIN / "lib"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import _viewsbuilder as vb  # noqa: E402
import axis_common as ac  # noqa: E402
import compare  # noqa: E402
import plot  # noqa: E402
import visual_receipt as vrc  # noqa: E402
from test_plot import add, feature_raster, raster_run, table_run, vector_run  # noqa: E402


@pytest.fixture()
def same(tmp_path):
    """legacy == new: PASS_EXACT, views without one-side-only cells."""
    leg = feature_raster()
    run = raster_run(tmp_path, leg, leg.copy())
    doc = vb.make_views(run, {"ds.r": "raster"})
    return run, doc


@pytest.fixture()
def shifted(tmp_path):
    """new = legacy one cell east: not a pass; the validity layer has stripes of 1s and 2s."""
    leg = feature_raster()
    new = np.roll(leg, 1, axis=1)
    new[:, 0] = -9999.0
    run = raster_run(tmp_path, leg, new)
    doc = vb.make_views(run, {"ds.r": "raster"}, counts={"ds.r": {"0": 10, "1": 57, "2": 57, "3": 3476}})
    return run, doc


def stored(run: Path) -> list[dict]:
    return ac.load_json(run / "verify" / "visual-review.json")["findings"]


def refused(run, tmp, f, msg):
    code, err = add(run, tmp, f)
    assert code == plot.EXIT_REJECTED and msg in err, (msg, err)
    assert not (run / "verify" / "visual-review.json").exists() or \
        all(x.get("finding_id") != f.get("finding_id") for x in stored(run))


# ------------------------------------------------------------------------------------------------ accepted
def test_nothing_found_view_receipt_counts_like_q_all(same, tmp_path):
    run, doc = same
    v0, _ = compare.aggregate(run)
    assert "VISUAL_REVIEW_MISSING" in v0["datasets"]["ds.r"]["reason_codes"]
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r"))
    assert code == 0, err
    rec = stored(run)[0]
    assert rec["receipt"] == "view" and rec["run_id"] == vb.run_id_of(run) and rec["created_at"]
    assert rec["finding_id"].startswith("vf-") and rec["engine"] == "maplibre-gl-raster"
    assert "plot_sha256" not in rec and rec["rubric_sha256"] == plot.load_rubric()[1]
    v, _ = compare.aggregate(run)
    d = v["datasets"]["ds.r"]
    assert d["verdict"] == "PASS_EXACT" and "VISUAL_REVIEW_MISSING" not in d["reason_codes"]
    assert d["visual"]["view_receipts"] == [rec["finding_id"]] and not v["visual"]["rejected"]
    st = plot.review_status(plot.Run(run))
    assert st["lacking"] == [] and st["datasets"]["ds.r"]["via"] == "views"
    assert vrc.RunReceipts(run).missing_reviews() == []


def test_view_flag_has_the_png_flag_effect(same, tmp_path):
    run, doc = same
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", flag=True))
    assert code == 0, err
    v, _ = compare.aggregate(run)
    d = v["datasets"]["ds.r"]
    assert d["compare_verdict"] == "PASS_EXACT" and d["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_FLAG" in d["reason_codes"] and d["visual"]["flags"][0]["plot"].startswith("verify/views/ds.r/")


def test_view_receipt_never_lifts_a_failure(shifted, tmp_path):
    run, doc = shifted
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", flag=True))
    assert code == 0, err
    v, _ = compare.aggregate(run)
    d = v["datasets"]["ds.r"]
    assert d["verdict"] == d["compare_verdict"] and d["verdict"] not in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")
    assert "visual flag attached as a note (state not PASS_*)" in d["visual"]["notes"]


def test_popup_rounding():
    assert vrc.popup_match(10.6, 10.5678) and vrc.popup_match(10.57, 10.5678) and vrc.popup_match(10.5678, 10.5678)
    assert not vrc.popup_match(10.7, 10.5678) and not vrc.popup_match(11, 10.5678)
    assert vrc.popup_match(0.00123, 0.0012345) and not vrc.popup_match(0.0013, 0.0012345)
    assert vrc.popup_match(1234567, 1234567.4) and vrc.popup_match(1230000, 1234567.4)
    assert not vrc.popup_match(1240000, 1234567.4)
    assert vrc.popup_match(None, None) and not vrc.popup_match(0, None) and not vrc.popup_match(None, 0.0)
    assert vrc.popup_match(0, 0.0) and not vrc.popup_match(0.001, 0.0) and not vrc.popup_match(True, 1.0)
    assert vrc.popup_match(-3.12, -3.125) and not vrc.popup_match(3.12, -3.125)


def test_vector_view_receipt_needs_no_engine_or_cells(tmp_path):
    run = vector_run(tmp_path, drop_key="z3")
    doc = vb.make_views(run, {"ds.v": "vector"})
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.v", flag=True))
    assert code == 0, err
    refused(run, tmp_path, vb.view_receipt(doc, "ds.v", cells_read=[{"cell": "x", "legacy": 1, "new": 1}],
                                           note="x"), "cells_read is for rasters")
    refused(run, tmp_path, vb.view_receipt(doc, "ds.v", views_seen=[
        s for s in vb.view_receipt(doc, "ds.v")["views_seen"] if "worst" not in s["project"]]), "missing worst")


# ------------------------------------------------------------------------------------------------ refusals
def test_raster_needs_the_gpu_engine(same, tmp_path):
    run, doc = same
    for eng in ("cog-tiler-wasm", "titiler", None):
        f = vb.view_receipt(doc, "ds.r", engine=eng)
        if eng is None:
            f.pop("engine")
        refused(run, tmp_path, f, "maplibre-gl-raster")
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", engine="cog-tiler-wasm"))
    assert "Add Data" in err and "smooths cells" in err


def test_project_sha_and_bytes(same, tmp_path):
    run, doc = same
    f = vb.view_receipt(doc, "ds.r")
    f["view"] = dict(f["view"], project_sha256="0" * 64)
    f["views_seen"] = [dict(s, project_sha256="0" * 64) if s["project"] == f["view"]["project"] else s
                       for s in f["views_seen"]]
    refused(run, tmp_path, f, "does not match views.json")
    p = run / "verify" / "views" / "ds.r" / "nodata.geolibre.json"
    p.write_text(p.read_text() + " ")                     # bytes changed after views.json was written
    refused(run, tmp_path, vb.view_receipt(doc, "ds.r"), "bytes changed since views.json")
    p.unlink()
    refused(run, tmp_path, vb.view_receipt(doc, "ds.r"), "missing or its bytes changed")


def test_project_paths(same, tmp_path):
    run, doc = same
    good = vb.view_receipt(doc, "ds.r")
    sha = good["view"]["project_sha256"]
    for rel, msg in (("plots/ds.r/overview.png", "not a view project"),
                     ("verify/views/../views/ds.r/overview.geolibre.json", "plain relative path"),
                     ("/abs/verify/views/ds.r/overview.geolibre.json", "plain relative path"),
                     ("verify/views/ds.other/overview.geolibre.json", "is not a view of ds.r")):
        f = dict(good, view={"project": rel, "project_sha256": sha})
        f["views_seen"] = good["views_seen"] + [{"project": rel, "project_sha256": sha}]
        refused(run, tmp_path, f, msg)
    f = dict(good, view={"project": "verify/views/ds.r/overview.geolibre.json"})
    refused(run, tmp_path, f, "view must be {project, project_sha256}")
    refused(run, tmp_path, dict(good, views_seen=[s for s in good["views_seen"] if s != good["view"]]),
            "must be one of views_seen")
    refused(run, tmp_path, dict(good, views_seen=good["views_seen"] + good["views_seen"][:1]), "twice")


def test_required_views_and_cells(same, tmp_path):
    run, doc = same
    good = vb.view_receipt(doc, "ds.r")
    no_nodata = [s for s in good["views_seen"] if "nodata" not in s["project"]]
    refused(run, tmp_path, dict(good, views_seen=no_nodata), "missing nodata")
    refused(run, tmp_path, dict(good, cells_read=[]), "at least one cell")
    f = dict(good, cells_read=[{"cell": "interior", "legacy": 10.7, "new": 10.6}])
    refused(run, tmp_path, f, "wrong cell or wrong layer")
    f = dict(good, cells_read=[{"cell": "interior", "legacy": 10.6, "new": None}])
    refused(run, tmp_path, f, "new read None")
    f = dict(good, cells_read=[{"cell": "interior", "legacy": 10.6}])
    refused(run, tmp_path, f, "{cell, legacy, new}")
    no_cell_project = [s for s in good["views_seen"] if "cell-" not in s["project"]]
    refused(run, tmp_path, dict(good, views_seen=no_cell_project), "cite its project cell-interior")
    f = vb.view_receipt(doc, "ds.r", cells=("interior", "edge"))
    f["cells_read"][1]["cell"] = "nowhere"
    refused(run, tmp_path, f, "cite its project cell-nowhere")
    f = vb.view_receipt(doc, "ds.r", cells=("edge", "worst"))      # nodata on one side, rounding at the limit
    code, err = add(run, tmp_path, f)
    assert code == 0, err


def test_nothing_found_on_a_shifted_mask_is_refused(shifted, tmp_path):
    """e2e item 10 (scripted half): the validity layer has 1s and 2s, so 'looked, nothing found' cannot stand."""
    run, doc = shifted
    refused(run, tmp_path, vb.view_receipt(doc, "ds.r"), "'nothing found' refused")
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r"))
    assert "57 cell(s) valid on legacy only" in err and "57 valid on new only" in err
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", question_id="Q4", answer="cannot_tell"))
    assert code == 0, err                                  # allowed, but not a receipt
    v, _ = compare.aggregate(run)
    assert "VISUAL_REVIEW_MISSING" in v["datasets"]["ds.r"]["reason_codes"]
    assert vrc.RunReceipts(run).missing_reviews() == ["ds.r"]
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", flag=True))
    assert code == 0, err
    v, _ = compare.aggregate(run)
    assert "VISUAL_REVIEW_MISSING" not in v["datasets"]["ds.r"]["reason_codes"]
    assert v["datasets"]["ds.r"]["visual"]["flags"]


def test_answer_rules(same, tmp_path):
    run, doc = same
    good = vb.view_receipt(doc, "ds.r")
    flag = vb.view_receipt(doc, "ds.r", flag=True)
    cases = [
        (dict(good, question_id="Q2"), "one Q-all receipt"),
        (dict(good, question_id="Q3"), "not asked of raster"),
        (dict(good, effect="flag"), "flag requires answer yes"),
        (dict(flag, effect="none"), "requires effect flag"),
        (dict(flag, category="uniform_scale"), "does not answer Q6"),
        (dict(flag, category="edge_shift"), "closed list"),
        ({k: v for k, v in flag.items() if k != "observations"}, "observations with numbers"),
        (dict(flag, observations=[{"what": "stripe", "value": "many"}]), "value (a number)"),
        ({k: v for k, v in flag.items() if k != "location"}, "needs location"),
        (dict(flag, location={"crs": "EPSG:32632", "bbox": [0, 0, 1, 1]}), "EPSG:4326"),
        (dict(flag, location={"crs": "EPSG:4326", "bbox": [7.0, 49.5, 6.0, 49.6]}), "west ≤ east"),
        (dict(flag, location={"crs": "EPSG:4326", "bbox": [100.0, 10.0, 101.0, 11.0]}), "outside the dataset"),
        (dict(flag, note=""), "needs a note"),
        (dict(good, layer="hillshade"), "layer must be one of"),
        (dict(good, model=""), "model must name"),
        (dict(good, run_id="r-other"), "not this run"),
        (dict(good, dataset_id="ds.nope"), "no compare result"),
        (dict(good, receipt="view"), "set by review-add"),
        (dict(good, created_at="2026-10-14T10:00:00Z"), "set by review-add"),
        (dict(good, plot="plots/ds.r/overview.png", plot_sha256="0" * 64), "cites views, not plots"),
        (dict(good, colour="red"), "unknown fields"),
        ({k: v for k, v in good.items() if k != "views_seen"}, "missing field views_seen"),
    ]
    for f, msg in cases:
        refused(run, tmp_path, f, msg)
    assert not (run / "verify" / "visual-review.json").exists()
    code, err = add(run, tmp_path, good)
    assert code == 0, err
    code, err = add(run, tmp_path, good)
    assert code == plot.EXIT_REJECTED and "already recorded" in err


def test_no_views_or_wrong_index(same, tmp_path):
    run, doc = same
    good = vb.view_receipt(doc, "ds.r")
    idx = run / "verify" / "views" / "views.json"
    d = json.loads(idx.read_text())
    idx.write_text(json.dumps(dict(d, run_id="r-other")))
    refused(run, tmp_path, good, "not the views index of run")
    idx.write_text(json.dumps(dict(d, datasets=[])))
    refused(run, tmp_path, good, "has no views of ds.r")
    idx.unlink()
    refused(run, tmp_path, good, "views.py run")


def test_table_has_no_view_receipt(tmp_path):
    run = table_run(tmp_path)
    doc = vb.make_views(run, {"ds.t": "vector"})                # an index that pretends: still refused
    refused(run, tmp_path, vb.view_receipt(doc, "ds.t"), "map views exist only for rasters and vectors")


def test_mixed_list_is_all_or_nothing(same, tmp_path):
    run, doc = same
    code, err = add(run, tmp_path, [vb.view_receipt(doc, "ds.r"), vb.view_receipt(doc, "ds.r", engine="titiler")])
    assert code == plot.EXIT_REJECTED and "finding 2" in err
    assert not (run / "verify" / "visual-review.json").exists()


# ------------------------------------------------------------------------------------------------ aggregate re-check
def test_project_changed_after_review_does_not_count(same, tmp_path):
    run, doc = same
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r"))
    assert code == 0, err
    p = run / "verify" / "views" / "ds.r" / "edges.geolibre.json"
    p.write_text(p.read_text().replace("edges", "edges!"))
    v, _ = compare.aggregate(run)
    d = v["datasets"]["ds.r"]
    assert "VISUAL_REVIEW_MISSING" in d["reason_codes"] and d["verdict"] == "NEEDS_REVIEW"
    assert "bytes changed" in v["visual"]["rejected"][0]["why"]
    assert plot.review_status(plot.Run(run))["lacking"] == ["ds.r"]
    assert vrc.RunReceipts(run).missing_reviews() == ["ds.r"]


def test_hand_written_view_receipt_is_rechecked(same, tmp_path):
    """A receipt that did not come through review-add (wrong engine) is not counted by aggregate either."""
    run, doc = same
    f = vb.view_receipt(doc, "ds.r", engine="cog-tiler-wasm")
    f.update(run_id=vb.run_id_of(run), finding_id="vf-hand", receipt="view")
    ac.dump_json(run / "verify" / "visual-review.json", {"schema": plot.REVIEW_SCHEMA, "findings": [f]})
    v, _ = compare.aggregate(run)
    assert "VISUAL_REVIEW_MISSING" in v["datasets"]["ds.r"]["reason_codes"]
    assert "maplibre-gl-raster" in v["visual"]["rejected"][0]["why"]


def test_png_receipts_unchanged_beside_view_receipts(same, tmp_path):
    from test_plot import finding, plot_run
    run, doc = same
    assert plot_run(run) == 0
    code, err = add(run, tmp_path, finding(run, "ds.r", name="overview.png"))
    assert code == 0, err
    code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r"))
    assert code == 0, err
    v, _ = compare.aggregate(run)
    assert len(v["datasets"]["ds.r"]["visual"]["receipts"]) == 2 and not v["visual"]["rejected"]


# ------------------------------------------------------------------------------------------------ after seal
def test_view_receipt_after_seal_goes_to_amend(same, tmp_path):
    run, doc = same
    mig = run.parents[1]
    cp = subprocess.run([sys.executable, str(PLUGIN / "skills" / "report" / "seal.py"), str(mig), "--run", run.name],
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    ev0 = ac.load_json(run / "verify" / "evidence.json")
    views0 = {o["path"].split("/verify/views/")[-1] for o in ev0["objects"] if o["kind"] == "view"}
    assert "ds.r/nodata.geolibre.json" in views0 and "ds.r/cell-interior.geolibre.json" in views0
    # after seal, views.py writes amend/views/ (stand-in here); a receipt may cite either folder
    adoc = vb.make_views(run, {"ds.r": "raster"}, root="amend/views")
    run.chmod(stat.S_IMODE(run.stat().st_mode) & ~0o222)          # the runner leaves the run dir read-only
    try:
        code, err = add(run, tmp_path, vb.view_receipt(adoc, "ds.r"))
        assert code == 0, err
        assert not (run / "verify" / "visual-review.json").exists()
        rec = ac.load_json(run / "amend" / "visual-review.json")["findings"][0]
        assert rec["view"]["project"].startswith("amend/views/")
        ev = ac.load_json(run / "verify" / "evidence.1.json")
        vis = [o for o in ev["objects"] if o["kind"] == "vis"]
        amended_views = [o for o in ev["objects"] if o["kind"] == "view" and "/amend/views/" in o["path"]]
        assert vis and vis[0]["meta"]["receipt"] == "view" and amended_views
        assert set(vis[0]["refs"]) <= {o["id"] for o in amended_views}
        code, err = add(run, tmp_path, vb.view_receipt(doc, "ds.r", engine="titiler"))
        assert code == plot.EXIT_REJECTED
        assert len(ac.load_json(run / "amend" / "visual-review.json")["findings"]) == 1
        assert plot.review_status(plot.Run(run))["lacking"] == []
    finally:
        run.chmod(stat.S_IMODE(run.stat().st_mode) | 0o200)
        os.chmod(run / "verify", 0o755)


# ------------------------------------------------------------------------------------------------ Q-all templates
def test_png_template_is_a_ready_q_all_receipt(same, tmp_path):
    run, _ = same
    assert plot.review_template(plot.Run(run), "png")[1] == ["ds.r: no plots (plot.py run --run <dir> --dataset ds.r)"]
    assert plot.main(["run", "--run", str(run)]) == 0
    items, skipped = plot.review_template(plot.Run(run), "png")
    assert skipped == [] and len(items) == 1
    sk = items[0]
    pj = ac.load_json(run / "verify" / "plots" / "ds.r" / "plots.json")
    assert sk["plots_seen"] == sorted(({"plot": f"plots/ds.r/{e['name']}", "plot_sha256": e["sha256"]}
                                       for e in pj["png"]), key=lambda s: s["plot"])
    assert sk["plot"] == "plots/ds.r/overview.png" and sk["panel"] == "all" and sk["question_id"] == "Q-all"
    refused(run, tmp_path, sk, "answer must be")                 # left unfilled: refused
    code, err = add(run, tmp_path, dict(sk, answer="no", model="test-model"))
    assert code == 0, err
    assert plot.review_status(plot.Run(run))["lacking"] == []
    assert plot.review_template(plot.Run(run), "png") == ([], [])  # nothing left to template


def test_view_template_cites_required_views_and_cells(same, tmp_path):
    run, doc = same
    items, skipped = plot.review_template(plot.Run(run), "view")
    assert skipped == [] and len(items) == 1
    sk = items[0]
    e = vb.entry(doc, "ds.r")
    names = {s["project"].rsplit("/", 1)[1].split(".")[0] for s in sk["views_seen"]}
    assert set(vrc.REQUIRED_VIEWS["raster"]) <= names and sk["engine"] == vrc.ENGINE_GPU
    cells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
    assert [c["cell"] for c in sk["cells_read"]] == sorted(cells)
    refused(run, tmp_path, dict(sk, answer="no", model="m"), "views.json has")   # cell values not read yet
    filled = dict(sk, answer="no", model="test-model",
                  cells_read=[{"cell": c["cell"], "legacy": vb._popup(cells[c["cell"]]["legacy_value"]),
                               "new": vb._popup(cells[c["cell"]]["new_value"])} for c in sk["cells_read"]])
    code, err = add(run, tmp_path, filled)
    assert code == 0, err
    assert plot.review_status(plot.Run(run))["datasets"]["ds.r"]["via"] == "views"


def test_view_template_refuses_nothing_found_with_one_sided_cells(shifted, capsys):
    run, _ = shifted
    items, skipped = plot.review_template(plot.Run(run), "view")
    assert items == [] and "flag them instead" in skipped[0]
    assert plot.main(["review-template", "--run", str(run), "--kind", "view"]) == plot.EXIT_SOME
    assert json.loads(capsys.readouterr().out) == []
    assert plot.main(["run", "--run", str(run)]) == 0
    capsys.readouterr()
    assert plot.main(["review-template", "--run", str(run), "--kind", "png"]) == plot.EXIT_OK
    sk = json.loads(capsys.readouterr().out)
    assert [s["dataset_id"] for s in sk] == ["ds.r"] and sk[0]["answer"] == "" and sk[0]["model"] == ""


def test_template_auto_picks_views_and_layer_accepts_joined_values(same, tmp_path):
    run, doc = same
    sk = plot.review_template(plot.Run(run))[0][0]               # auto: views.json has ds.r → a view receipt
    assert "views_seen" in sk and "plots_seen" not in sk and sk["layer"] is None
    e = vb.entry(doc, "ds.r")
    cells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
    sk["cells_read"] = [{"cell": c["cell"], "legacy": vb._popup(cells[c["cell"]]["legacy_value"]),
                         "new": vb._popup(cells[c["cell"]]["new_value"])} for c in sk["cells_read"]]
    for bad in ("legacy+legacy", "both", "legacy+"):
        refused(run, tmp_path, dict(sk, answer="no", model="m", layer=bad), "joined with '+'")
    code, err = add(run, tmp_path, dict(sk, answer="no", model="test-model", layer="legacy+new"))
    assert code == 0, err
    assert stored(run)[0]["layer"] == "legacy+new"
