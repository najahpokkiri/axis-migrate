"""plot.py on the lux fixture (06 §6.2 L17 + the plots the rubric calibration relies on). Tmp copies only.

Receipts here go through `plot.py review-add` (the only writer of visual-review.json), so L17 is exercised end to end:
a flag on a passing dataset gives NEEDS_REVIEW, a 'looks fine' on a failing one leaves it FAIL, a wrong sha is refused.
"""
import contextlib
import io
import json
from pathlib import Path

import numpy as np
import pytest

import axis_common as ac
import compare
import lux_lib as L
import plot

REPO = Path(__file__).resolve().parents[2]
CALIB = REPO / "evals" / "calib" / "rubric.v1.json"
RASTERS = ["ds.01_pop_lux", "ds.02_res_mask", "ds.04_factor", "ds.lux_res_exposure_2020"]


def lux_run(tmp: Path, outputs: dict) -> Path:
    if not (tmp / L.LEGACY_NAME).exists():
        L.copy_fixture(tmp)
    return L.build_run(tmp, outputs)


def pj(run, ds):
    return ac.load_json(run / "verify" / "plots" / ds / "plots.json")


def png(run, ds, name):
    return next(e for e in pj(run, ds)["png"] if e["name"] == name)


def add(run: Path, tmp: Path, f) -> tuple[int, str]:
    p = tmp / "finding.json"
    p.write_text(json.dumps(f))
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        code = plot.main(["review-add", "--run", str(run), "--json", str(p)])
    return code, err.getvalue()


def q_all(run: Path, ds: str) -> dict:
    d = pj(run, ds)
    seen = [{"plot": f"plots/{ds}/{e['name']}", "plot_sha256": e["sha256"]} for e in d["png"]]
    return {"dataset_id": ds, "question_id": "Q-all", "category": "none", "answer": "no", "effect": "none",
            "plot": seen[0]["plot"], "plot_sha256": seen[0]["plot_sha256"], "panel": "all", "note": "",
            "model": "devtest", "plots_seen": seen,
            "looked": [{"check": c, "answer": "ok", "where": Path(seen[0]["plot"]).name, "saw": "stand-in: " + c + " checked on " + Path(seen[0]["plot"]).name, **({"number": {"name": "cells valid on one side only", "value": 0, "computed_by": "test stand-in"}} if c == "edges" else {})} for c in ("place", "coverage", "edges", "values", "classes")]}


@pytest.fixture(scope="module")
def l1(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("l1")
    leg = L.copy_fixture(tmp)
    run = lux_run(tmp, L.legacy_copies(leg))
    compare.run_compare(run)
    assert plot.main(["run", "--run", str(run)]) == 0
    return tmp, run


def test_L1_every_paired_dataset_plotted_and_deterministic(l1):
    tmp, run = l1
    res = plot.Run(run).results()
    for ds, r in res.items():
        d = pj(run, ds)
        assert d["compare_result_id"] == r["result_id"]
        assert d["inputs"]["legacy_sha"] and d["inputs"]["new_sha"]
    before = {p: p.read_bytes() for p in sorted((run / "verify" / "plots").rglob("*.png"))}
    assert plot.main(["run", "--run", str(run), "--dataset", "ds.01_pop_lux", "--dataset", "ds.02_res_2169"]) == 0
    for p, b in before.items():
        assert p.read_bytes() == b, p


def test_L1_q_all_receipts_complete_and_pass(l1, tmp_path):
    _, run = l1
    st = plot.review_status(plot.Run(run))
    assert st["lacking"], "nothing reviewed yet"
    for ds in st["datasets"]:
        code, err = add(run, tmp_path, q_all(run, ds))
        assert code == 0, (ds, err)
    st = plot.review_status(plot.Run(run))
    assert st["lacking"] == []
    v, _ = compare.aggregate(run)
    for ds in st["datasets"]:
        assert "VISUAL_REVIEW_MISSING" not in v["datasets"][ds]["reason_codes"], ds
        assert v["datasets"][ds]["verdict"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE"), (ds, v["datasets"][ds])


def test_L17_flag_on_identity_needs_review_and_wrong_sha_refused(l1, tmp_path):
    _, run = l1
    e = png(run, "ds.01_pop_lux", "overview.png")
    f = {"dataset_id": "ds.01_pop_lux", "question_id": "Q2", "category": "smoothed_blurred", "answer": "yes",
         "effect": "flag", "plot": "plots/ds.01_pop_lux/overview.png", "plot_sha256": "0" * 64, "panel": "new",
         "note": "devtest flag on identity", "model": "devtest",
         "location": {"crs": e["crs"], "bbox": e["extent"]}}
    code, err = add(run, tmp_path, f)
    assert code == plot.EXIT_REJECTED and "plot_sha256" in err
    f["plot_sha256"] = e["sha256"]
    code, err = add(run, tmp_path, f)
    assert code == 0, err
    v, _ = compare.aggregate(run)
    assert v["datasets"]["ds.01_pop_lux"]["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_FLAG" in v["datasets"]["ds.01_pop_lux"]["reason_codes"]


def test_L3_grid_overlay_and_receipt_cannot_upgrade(tmp_path, variant):
    run = lux_run(tmp_path, L.outputs_from(variant("W_s01_tap")))
    compare.run_compare(run, ["ds.01_pop_lux"])
    assert plot.main(["run", "--run", str(run), "--dataset", "ds.01_pop_lux"]) == 0
    wt = png(run, "ds.01_pop_lux", "worst_tiles.png")
    assert wt["mode"] == "grid_overlay" and "corner overlay" in wt["panel_names"]
    code, err = add(run, tmp_path, q_all(run, "ds.01_pop_lux"))       # "looks fine" on a FAIL
    assert code == 0, err
    v, _ = compare.aggregate(run)
    assert v["datasets"]["ds.01_pop_lux"]["verdict"] == "FAIL"


def test_L5_worst_tiles_reach_the_values_not_only_the_mask_edge(tmp_path, variant):
    """Plot-blind fix from calibration: smoothing must get a tile with both-valid cells only."""
    run = lux_run(tmp_path, L.outputs_from(variant("W_s01_bilinear")))
    compare.run_compare(run, ["ds.01_pop_lux"])
    assert plot.main(["run", "--run", str(run), "--dataset", "ds.01_pop_lux"]) == 0
    tiles = png(run, "ds.01_pop_lux", "worst_tiles.png")["tiles"]
    assert len(tiles) == 4
    assert any(t["valid_one_side_only"] == 0 and t["over"] > 1000 for t in tiles)
    assert any(t["valid_one_side_only"] > 0 for t in tiles)


def _viridis_idx(run, ds, panel):
    import matplotlib as mpl
    import matplotlib.image as mpimg
    e = png(run, ds, "overview.png")
    img = mpimg.imread(run / "verify" / "plots" / ds / "overview.png")[..., :3]
    x0, y0, x1, y1 = next(p["bbox_px"] for p in e["panels"] if p["name"] == panel)
    px = img[y0 + 2:y1 - 1, x0 + 2:x1 - 1].reshape(-1, 3)
    lut = mpl.colormaps["viridis"](np.linspace(0, 1, 256))[:, :3]
    d = ((px[:, None, :] - lut[None]) ** 2).sum(axis=2)
    return d.argmin(axis=1)[d.min(axis=1) < 0.004]


def test_L9_new_panel_dim_under_shared_norm(tmp_path, variant):
    run = lux_run(tmp_path, L.outputs_from(variant("W_s04_noscale")))
    compare.run_compare(run, ["ds.lux_res_exposure_2020"])
    assert plot.main(["run", "--run", str(run), "--dataset", "ds.lux_res_exposure_2020"]) == 0
    li = _viridis_idx(run, "ds.lux_res_exposure_2020", "legacy")
    ni = _viridis_idx(run, "ds.lux_res_exposure_2020", "new")
    assert (ni <= 3).mean() > 0.97                    # ×1/1000: the new panel collapses onto the bottom colour
    assert (li > 30).mean() > 0.2                      # legacy keeps its pattern


def test_L8_zone_missing_visible_in_mask_and_per_feature(tmp_path, variant):
    run = lux_run(tmp_path, L.outputs_from(variant("W_s04_nostrip")))
    compare.run_compare(run, ["ds.lux_res_exposure_2020", "ds.04_canton_factor"])
    assert plot.main(["run", "--run", str(run), "--dataset", "ds.lux_res_exposure_2020",
                      "--dataset", "ds.04_canton_factor"]) == 0
    res = ac.load_json(run / "verify" / "compare" / "ds.lux_res_exposure_2020.json")
    m = next(c for c in res["checks"] if c["id"] == "raster.mask")
    assert m["detail"]["legacy_only"] > 1000 and m["detail"]["new_only"] == 0
    e = png(run, "ds.lux_res_exposure_2020", "mask_diff.png")
    assert e["kind"] == "mask_diff" and e["panel_names"] == ["mask"]


def test_calibration_file_is_current():
    """evals/calib/rubric.v1.json must be made with the current rubric and plot.py (else: recalibrate with
    calibrate_rubric.py build + look again + write)."""
    doc = ac.load_json(CALIB)
    _, rsha = plot.load_rubric()
    assert doc["rubric_sha256"] == rsha, "rubric.v1.yaml changed after calibration: recalibrate"
    assert doc["plot_sha256"] == ac.sha256_file(Path(plot.__file__)), "plot.py changed after calibration: recalibrate"
    s = doc["summary"]
    assert s["graded"] > 0 and s["agree"] == s["graded"] and not s["stale"] and not s["missing"]
