"""plot.py: deterministic plots and visual-review receipts on synthetic data only (06 §4g–h, §6.2 L17 analogue).

Nothing here encodes fixture facts. Rasters are 60×60 cells with features on a background, built from a fixed seed.
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

import _runbuilder as rb  # noqa: E402
import axis_common as ac  # noqa: E402
import compare  # noqa: E402
import plot  # noqa: E402
from conftest import CRS_M, transform, write_raster  # noqa: E402

N60 = 60


# ------------------------------------------------------------------------------------------------ builders
def feature_raster(seed: int = 7, textured: bool = True) -> np.ndarray:
    """Background 1.0, three rectangular 'settlements' (textured or flat values), a nodata corner."""
    rng = np.random.default_rng(seed)
    a = np.full((N60, N60), 1.0)
    for n, (r0, r1, c0, c1) in enumerate(((8, 22, 10, 26), (30, 50, 34, 52), (40, 55, 5, 18))):
        a[r0:r1, c0:c1] = (10.0 + np.round(rng.uniform(0, 5, size=(r1 - r0, c1 - c0)), 2)) if textured \
            else 10.0 + 2 * n
    a[0:4, 50:60] = -9999.0
    return a.astype("float32")


def raster_run(tmp: Path, legacy: np.ndarray, new: np.ndarray | None, *, new_tr=None, ds="ds.r", step="s1",
               new_missing=False, measure=None, run_id="r20261016T100000Z-deadbeef") -> Path:
    leg_root = tmp / "legacy"
    write_raster(leg_root / "out" / "r.tif", legacy)
    cmp_ = {"measure": measure} if measure else {}
    g = rb.graph([rb.step(step)], [rb.dataset(ds, "raster", "intermediate", "out/r.tif", produced_by=step,
                                              compare=cmp_)])
    outputs = {}
    if not new_missing:
        p = tmp / "new_src" / "r.tif"
        write_raster(p, new, tr=new_tr)
        outputs[ds] = p
    run = rb.build(tmp, g, outputs=outputs, run_id=run_id,
                   step_status={step: "failed"} if new_missing else None)
    _, code = compare.run_compare(run)
    return run


def vector_run(tmp: Path, *, drop_key=None, shrink=0.0, cmp=None, one_multipoint=False) -> Path:
    import geopandas as gpd
    from shapely.geometry import box
    leg = tmp / "legacy" / "out" / "v.gpkg"
    leg.parent.mkdir(parents=True, exist_ok=True)
    geoms = [box(500000 + 100 * i, 5500000, 500000 + 100 * i + 60, 5500060) for i in range(6)]
    df = gpd.GeoDataFrame({"zid": [f"z{i}" for i in range(6)], "val": [float(i) for i in range(6)]},
                          geometry=geoms, crs=CRS_M)
    if one_multipoint:   # BYU Agriculture_Flood: one aggregate MultiPoint row, no key column
        from shapely.geometry import MultiPoint
        df = gpd.GeoDataFrame({"Hectares": [88.38]}, geometry=[MultiPoint([(500000 + 30 * i, 5500000)
                                                                          for i in range(40)])], crs=CRS_M)
    df.to_file(leg, layer="v", driver="GPKG")
    nd = df.copy()
    if shrink:
        nd["geometry"] = nd.geometry.buffer(-shrink)
    if drop_key:
        nd = nd[nd["zid"] != drop_key]
    newp = tmp / "new_src" / "v.gpkg"
    newp.parent.mkdir(parents=True, exist_ok=True)
    nd.to_file(newp, layer="v", driver="GPKG")
    g = rb.graph([rb.step("s1")], [rb.dataset("ds.v", "vector", "intermediate", "out/v.gpkg", layer="v",
                                              produced_by="s1", compare=cmp or {"key": ["zid"],
                                                                         "measure_crs_param": "p.measure_crs"})])
    run = rb.build(tmp, g, outputs={"ds.v": newp})
    compare.run_compare(run)
    return run


def table_run(tmp: Path) -> Path:
    leg = tmp / "legacy" / "out" / "t.csv"
    leg.parent.mkdir(parents=True, exist_ok=True)
    leg.write_text("k,a,b\n" + "".join(f"k{i},{i + 1},{10 * (i + 1)}\n" for i in range(8)))
    newp = tmp / "new_src" / "t.csv"
    newp.parent.mkdir(parents=True, exist_ok=True)
    newp.write_text("k,a,b\n" + "".join(f"k{i},{(i + 1) * 1000},{10 * (i + 1)}\n" for i in range(7)))
    g = rb.graph([rb.step("s1")], [rb.dataset("ds.t", "table", "intermediate", "out/t.csv", produced_by="s1",
                                              compare={"key": ["k"]})])
    run = rb.build(tmp, g, outputs={"ds.t": newp})
    compare.run_compare(run)
    return run


def plot_run(run: Path, *extra) -> int:
    return plot.main(["run", "--run", str(run), *extra])


def pj(run: Path, ds: str) -> dict:
    return ac.load_json(run / "verify" / "plots" / ds / "plots.json")


def png_entry(run: Path, ds: str, name: str) -> dict:
    return next(e for e in pj(run, ds)["png"] if e["name"] == name)


def panel_pixels(run: Path, ds: str, name: str, panel: str) -> np.ndarray:
    import matplotlib.image as mpimg
    img = mpimg.imread(run / "verify" / "plots" / ds / name)[..., :3]
    e = png_entry(run, ds, name)
    box = next(p["bbox_px"] for p in e["panels"] if p["name"] == panel)
    x0, y0, x1, y1 = box
    return img[y0 + 2:y1 - 1, x0 + 2:x1 - 1]


def finding(run: Path, ds: str, **kw) -> dict:
    name = kw.pop("name", "worst_tiles.png")
    e = png_entry(run, ds, name)
    f = {"dataset_id": ds, "question_id": "Q1", "category": "none", "answer": "no", "effect": "none",
         "plot": f"plots/{ds}/{name}", "plot_sha256": e["sha256"], "panel": e["panel_names"][0],
         "note": "", "model": "test-model"}
    f.update(kw)
    return f


def add(run: Path, tmp: Path, f, name="f.json") -> tuple[int, str]:
    p = tmp / name
    p.write_text(json.dumps(f))
    import contextlib
    import io
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = plot.main(["review-add", "--run", str(run), "--json", str(p)])
    return code, err.getvalue()


# ------------------------------------------------------------------------------------------------ raster plots
def test_one_cell_shift_shows_one_cell_stripe_in_worst_tiles(tmp_path):
    leg = feature_raster(textured=False)
    new = np.roll(leg, 1, axis=1)                         # data offset by one cell, transform unchanged
    new[:, 0] = leg[:, 0]
    run = raster_run(tmp_path, leg, new)
    assert plot_run(run) == 0
    e = png_entry(run, "ds.r", "worst_tiles.png")
    assert e["mode"] == "tiles" and e["tiles"]
    px = panel_pixels(run, "ds.r", "worst_tiles.png", "tile 1 diff")
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    orange = (r - b > 0.25) & (r > 0.6)                   # PuOr_r positive: new larger
    purple = (b - g > 0.15) & (r - g > 0.05) & (b > 0.3)  # PuOr_r negative: legacy larger
    assert orange.any() and purple.any(), "a shift must give paired stripes of both signs"
    cell_px = px.shape[1] / plot.TILE
    for m in (orange, purple):
        cols = np.flatnonzero(m.sum(axis=0) > 3)
        # contiguous runs of coloured columns are about one cell wide (vertical stripes)
        runs, start = [], cols[0]
        for a, b_ in zip(cols, cols[1:]):
            if b_ != a + 1:
                runs.append(a - start + 1)
                start = b_
        runs.append(cols[-1] - start + 1)
        assert max(runs) <= cell_px + 3, (runs, cell_px)
        assert min(runs) >= cell_px / 2 - 1, (runs, cell_px)   # black over-tolerance outlines eat the rim


def test_identity_diff_panel_is_neutral(tmp_path):
    leg = feature_raster()
    run = raster_run(tmp_path, leg, leg.copy())
    assert plot_run(run) == 0
    px = panel_pixels(run, "ds.r", "overview.png", "diff")
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    assert not ((r - b > 0.25) & (r > 0.6)).any()
    assert not ((b - g > 0.15) & (b > 0.3) & (r - g > 0.05)).any()


def _viridis_index(px: np.ndarray) -> np.ndarray:
    import matplotlib as mpl
    lut = mpl.colormaps["viridis"](np.linspace(0, 1, 256))[:, :3]
    flat = px.reshape(-1, 3)
    d = ((flat[:, None, :] - lut[None, :, :]) ** 2).sum(axis=2)
    idx = d.argmin(axis=1)
    ok = d.min(axis=1) < 0.004                              # pixels that are a viridis colour (not background/text)
    return idx[ok]


def test_x1000_saturates_new_panel_under_shared_norm(tmp_path):
    leg = feature_raster()
    new = np.where(leg == -9999.0, leg, leg * 1000.0).astype("float32")
    run = raster_run(tmp_path, leg, new)
    assert plot_run(run) == 0
    norm = png_entry(run, "ds.r", "overview.png")["norm"]
    assert norm["kind"] == "shared_p2_p98"
    li = _viridis_index(panel_pixels(run, "ds.r", "overview.png", "legacy"))
    ni = _viridis_index(panel_pixels(run, "ds.r", "overview.png", "new"))
    assert li.size > 1000 and ni.size > 1000
    # union p2/p98 sits in the new range: legacy collapses to the bottom colour, new keeps its pattern and its
    # largest values run past vmax (saturated, extend arrow) — the two panels cannot look alike
    assert (li <= 5).mean() > 0.95
    assert (li >= 250).mean() == 0.0
    assert (ni >= 250).mean() > 0.02
    assert np.percentile(ni, 90) - np.percentile(ni, 10) > 100
    # control: identity gives identical panel statistics
    run2 = raster_run(tmp_path / "id", leg, leg.copy())
    assert plot_run(run2) == 0
    a = _viridis_index(panel_pixels(run2, "ds.r", "overview.png", "legacy"))
    b = _viridis_index(panel_pixels(run2, "ds.r", "overview.png", "new"))
    assert abs(np.median(a) - np.median(b)) <= 1 and abs((a >= 250).mean() - (b >= 250).mean()) < 0.01


def test_grid_gate_failure_draws_both_grids(tmp_path):
    leg = feature_raster()
    from affine import Affine
    t = transform()
    run = raster_run(tmp_path, leg, leg.copy(), new_tr=Affine(t.a, 0, t.c + 0.5 * t.a, 0, t.e, t.f + 0.5 * t.e))
    res = ac.load_json(run / "verify" / "compare" / "ds.r.json")
    assert "RASTER_TRANSFORM_MISMATCH" in res["reason_codes"]
    assert plot_run(run) == 0
    e = png_entry(run, "ds.r", "worst_tiles.png")
    assert e["mode"] == "grid_overlay"
    assert e["panel_names"][0] == "corner overlay"
    px = panel_pixels(run, "ds.r", "worst_tiles.png", "corner overlay")
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    assert ((b > 0.5) & (r < 0.3)).any() and ((r > 0.7) & (b < 0.3)).any()   # blue and orange lines both drawn


def test_missing_new_output_still_plots_legacy(tmp_path):
    run = raster_run(tmp_path, feature_raster(), None, new_missing=True)
    res = ac.load_json(run / "verify" / "compare" / "ds.r.json")
    assert res["verdict"] in ("FAIL", "BLOCKED")
    assert plot_run(run) == 0
    d = pj(run, "ds.r")
    assert d["inputs"]["new_sha"] is None and d["inputs"]["legacy_sha"]
    assert any("new" in m for m in d["missing"])
    assert {e["name"] for e in d["png"]} == {"overview.png", "mask_diff.png", "hist.png", "worst_tiles.png"}


def test_downsampling_keeps_isolated_cell(tmp_path):
    big = 1500
    a = np.ones((big, big), dtype="float32")
    b = a.copy()
    b[777, 1234] = 50.0                                    # one isolated differing cell
    leg_root = tmp_path / "legacy"
    write_raster(leg_root / "out" / "r.tif", a, nodata=-9999.0)
    p = tmp_path / "new_src" / "r.tif"
    write_raster(p, b, nodata=-9999.0)
    g = rb.graph([rb.step("s1")], [rb.dataset("ds.r", "raster", "intermediate", "out/r.tif", produced_by="s1")])
    run = rb.build(tmp_path, g, outputs={"ds.r": p})
    compare.run_compare(run)
    assert plot_run(run) == 0
    e = png_entry(run, "ds.r", "overview.png")
    assert e["block_factor"] > 1
    px = panel_pixels(run, "ds.r", "overview.png", "diff")
    r, g_, bb = px[..., 0], px[..., 1], px[..., 2]
    assert (((r - bb > 0.25) & (r > 0.6)) | ((r < 0.2) & (g_ < 0.2) & (bb < 0.2))).any()


def test_determinism_same_inputs_same_bytes(tmp_path):
    leg = feature_raster()
    new = np.roll(leg, 1, axis=0)
    run = raster_run(tmp_path, leg, new, measure="extensive")
    assert plot_run(run) == 0
    first = {p.name: p.read_bytes() for p in sorted((run / "verify" / "plots" / "ds.r").iterdir())}
    assert plot_run(run) == 0
    second = {p.name: p.read_bytes() for p in sorted((run / "verify" / "plots" / "ds.r").iterdir())}
    assert first == second
    # and in a fresh process
    env = dict(os.environ, GDAL_PAM_ENABLED="NO")
    subprocess.run([sys.executable, str(VERIFY / "plot.py"), "run", "--run", str(run)], check=True, env=env,
                   capture_output=True)
    third = {p.name: p.read_bytes() for p in sorted((run / "verify" / "plots" / "ds.r").iterdir())}
    assert first == third


def test_plots_json_shape(tmp_path):
    run = raster_run(tmp_path, feature_raster(), feature_raster())
    assert plot_run(run) == 0
    d = pj(run, "ds.r")
    res = ac.load_json(run / "verify" / "compare" / "ds.r.json")
    assert d["generator"] == "axis-migrate.plot" and d["version"] == plot.VERSION
    assert d["dataset_id"] == "ds.r" and d["compare_result_id"] == res["result_id"]
    assert set(d["inputs"]) >= {"legacy_sha", "new_sha"}
    for e in d["png"]:
        assert set(e) >= {"name", "sha256", "extent", "norm", "panel_names", "kind"}
        assert ac.sha256_file(run / "verify" / "plots" / "ds.r" / e["name"]) == e["sha256"]


def test_plot_refuses_sealed_run(tmp_path):
    run = raster_run(tmp_path, feature_raster(), feature_raster())
    (run / "verify" / "evidence.json").write_text("{}")
    assert plot_run(run) == plot.EXIT_SEALED


# ------------------------------------------------------------------------------------------------ vector/table
def test_vector_plots_label_unmatched_and_shrink(tmp_path):
    run = vector_run(tmp_path, drop_key="z3", shrink=5.0)
    assert plot_run(run) == 0
    d = pj(run, "ds.v")
    assert {e["name"] for e in d["png"]} == {"overlay.png", "per_feature.png", "worst_features.png"}
    ov = next(e for e in d["png"] if e["name"] == "overlay.png")
    assert ov["unmatched"]["legacy_only"] == ["z3"]
    wf = next(e for e in d["png"] if e["name"] == "worst_features.png")
    assert wf["features"][0]["key"] == ["z3"] and wf["features"][0]["kind"] == "legacy only"
    assert wf["features"][1]["symdiff_ratio"] > 0.1


@pytest.mark.parametrize("match", ["full_row", "geometry"])
def test_vector_plots_pair_by_compare_match_not_legacy_only(tmp_path, match):
    """B8 (BYU e2e): a keyless layer compared with compare.match full_row/geometry is paired the way compare pairs
    it; the plots must not draw every feature as 'legacy only' next to PASS_EXACT."""
    run = vector_run(tmp_path, cmp={"match": match, "measure_crs_param": "p.measure_crs"}, one_multipoint=True)
    assert plot_run(run) == 0
    d = pj(run, "ds.v")
    ov = next(e for e in d["png"] if e["name"] == "overlay.png")
    assert ov["unmatched"] == {"legacy_only": [], "new_only": []}
    # nothing differs: no reference-only worst_features / all-white per_feature (map review C1)
    assert {e["name"] for e in d["png"]} == {"overlay.png"}
    assert d["omitted"] == {"per_feature.png": "no difference", "worst_features.png": "no difference"}
    assert not (run / "verify" / "plots" / "ds.v" / "worst_features.png").exists()


def test_identical_raster_omits_worst_tiles(tmp_path):
    run = raster_run(tmp_path, feature_raster(), feature_raster())
    assert plot_run(run) == 0
    d = pj(run, "ds.r")
    assert {e["name"] for e in d["png"]} == {"overview.png", "mask_diff.png", "hist.png"}
    assert d["omitted"] == {"worst_tiles.png": "no difference"}
    assert not (run / "verify" / "plots" / "ds.r" / "worst_tiles.png").exists()


def test_one_sided_cells_keep_worst_tiles(tmp_path):
    leg = feature_raster()
    new = leg.copy()
    new[3, 3] = -9999.0                                  # equal values, one cell valid in legacy only
    run = raster_run(tmp_path, leg, new)
    assert plot_run(run) == 0
    d = pj(run, "ds.r")
    assert "worst_tiles.png" in {e["name"] for e in d["png"]} and "omitted" not in d


def test_table_residuals(tmp_path):
    run = table_run(tmp_path)
    assert plot_run(run) == 0
    e = png_entry(run, "ds.t", "residuals.png")
    assert e["unmatched"]["legacy_only"] == ["k7"]
    assert "scatter" in e["panel_names"]


# ------------------------------------------------------------------------------------------------ review-add
@pytest.fixture()
def shifted(tmp_path):
    leg = feature_raster()
    new = np.roll(leg, 1, axis=1)
    new[:, 0] = leg[:, 0]
    run = raster_run(tmp_path, leg, new)
    assert plot_run(run) == 0
    return run


def test_review_add_accepts_valid_flag(shifted, tmp_path):
    e = png_entry(shifted, "ds.r", "worst_tiles.png")
    f = finding(shifted, "ds.r", answer="yes", effect="flag", category="boundary_stripes", panel="tile 1 diff",
                note="paired one-cell orange/purple stripes on east/west edges",
                location={"crs": e["crs"], "bbox": e["tiles"][0]["bbox"]})
    code, err = add(shifted, tmp_path, f)
    assert code == 0, err
    vr = ac.load_json(shifted / "verify" / "visual-review.json")
    rec = vr["findings"][0]
    assert rec["run_id"] == ac.load_json(shifted / "manifest.json")["run_id"]
    assert rec["rubric_id"] == "rubric.v1" and rec["rubric_sha256"] == vr["rubric_sha256"]
    assert rec["finding_id"].startswith("vf-") and rec["created_at"]
    # the aggregator accepts it as a receipt and a flag
    v, _ = compare.aggregate(shifted)
    assert v["datasets"]["ds.r"]["visual"]["flags"]


def test_review_add_rejects_wrong_sha(shifted, tmp_path):
    code, err = add(shifted, tmp_path, finding(shifted, "ds.r", plot_sha256="0" * 64))
    assert code == plot.EXIT_REJECTED and "plot_sha256" in err
    assert not (shifted / "verify" / "visual-review.json").exists()


def test_review_add_rejects_flag_with_answer_no(shifted, tmp_path):
    code, err = add(shifted, tmp_path, finding(shifted, "ds.r", effect="flag", answer="no"))
    assert code == plot.EXIT_REJECTED and "flag requires answer yes" in err


def test_review_add_rejects_unknown_category(shifted, tmp_path):
    code, err = add(shifted, tmp_path, finding(shifted, "ds.r", answer="yes", effect="flag", category="edge_shift",
                                               note="x", location={"crs": "EPSG:32632",
                                                                   "bbox": [500000, 5499400, 500600, 5500000]}))
    assert code == plot.EXIT_REJECTED and "closed list" in err


def test_review_add_rejects_other_checks(shifted, tmp_path):
    cases = [
        (finding(shifted, "ds.r", question_id="Q3"), "not asked of raster"),
        (finding(shifted, "ds.r", name="hist.png", panel="diff_hist"), "answered from"),
        (finding(shifted, "ds.r", panel="tile 9 diff"), "not a panel"),
        (finding(shifted, "ds.r", note="x" * 201), "200"),
        (finding(shifted, "ds.r", answer="yes", effect="none", category="boundary_stripes", note="x"),
         "requires effect flag"),
        (finding(shifted, "ds.r", answer="yes", effect="flag", category="uniform_scale", note="x",
                 location={"crs": "EPSG:32632", "bbox": [500000, 5499400, 500600, 5500000]}), "does not answer Q1"),
        (finding(shifted, "ds.r", rubric_sha256="abc"), "set by review-add"),
        (finding(shifted, "ds.r", run_id="r-other"), "not this run"),
        (finding(shifted, "ds.r", answer="yes", effect="flag", category="whole_offset", note="x"), "location"),
        (finding(shifted, "ds.r", answer="yes", effect="flag", category="whole_offset", note="x",
                 location={"crs": "EPSG:32632", "bbox": [0, 0, 1, 1]}), "outside the plot extent"),
        (dict(finding(shifted, "ds.r"), dataset_id="ds.nope"), "no compare result"),
    ]
    for f, msg in cases:
        code, err = add(shifted, tmp_path, f)
        assert code == plot.EXIT_REJECTED and msg in err, (msg, err)


def test_review_add_rejects_stale_plots(shifted, tmp_path):
    p = shifted / "verify" / "plots" / "ds.r" / "plots.json"
    d = ac.load_json(p)
    d["compare_result_id"] = "cmp:old"
    ac.dump_json(p, d)
    code, err = add(shifted, tmp_path, finding(shifted, "ds.r"))
    assert code == plot.EXIT_REJECTED and "stale" in err


def test_q_all_receipt_and_review_status(shifted, tmp_path):
    st = plot.review_status(plot.Run(shifted))
    assert st["lacking"] == ["ds.r"] and st["datasets"]["ds.r"]["status"] == "no receipt"
    d = pj(shifted, "ds.r")
    seen = [{"plot": f"plots/ds.r/{e['name']}", "plot_sha256": e["sha256"]} for e in d["png"]]
    partial = finding(shifted, "ds.r", question_id="Q-all", name="overview.png", panel="all", plots_seen=seen[:2])
    code, err = add(shifted, tmp_path, partial)
    assert code == plot.EXIT_REJECTED and "every plot" in err
    full = finding(shifted, "ds.r", question_id="Q-all", name="overview.png", panel="all", plots_seen=seen,
                   looked=[{"check": c, "answer": "ok", "where": "overview.png", "saw": f"{c}: seen on the overview",
                            **({"number": {"name": "one-sided cells", "value": 3, "computed_by": "compare.py"}}
                               if c == "edges" else {})} for c in plot.LOOK_CHECKS])
    code, err = add(shifted, tmp_path, full)
    assert code == 0, err
    st = plot.review_status(plot.Run(shifted))
    assert st["lacking"] == [] and st["datasets"]["ds.r"]["q_all"]
    v, _ = compare.aggregate(shifted)
    assert "VISUAL_REVIEW_MISSING" not in v["datasets"]["ds.r"]["reason_codes"]
    code, err = add(shifted, tmp_path, full)                 # identical finding twice → refused
    assert code == plot.EXIT_REJECTED and "already recorded" in err


def test_review_status_per_question(shifted, tmp_path):
    rubric, _ = plot.load_rubric()
    req = [q for q in rubric["questions"] if "raster" in q["families"]]
    for q in req[:-1]:
        name = q["plots"]["raster"][0] + ".png"
        e = png_entry(shifted, "ds.r", name)
        code, err = add(shifted, tmp_path, finding(shifted, "ds.r", question_id=q["id"], name=name,
                                                   panel=e["panel_names"][0]))
        assert code == 0, err
    st = plot.review_status(plot.Run(shifted))
    assert st["datasets"]["ds.r"]["missing_questions"] == [req[-1]["id"]]
    assert plot.main(["review-status", "--run", str(shifted)]) == 1


def test_review_add_after_seal_goes_to_amend(shifted, tmp_path):
    """Sealed run: never write into verify/; append to amend/visual-review.json and call seal.py --amend."""
    mig = shifted.parents[1]
    code = subprocess.run([sys.executable, str(PLUGIN / "skills" / "report" / "seal.py"), str(mig), "--run",
                           shifted.name], capture_output=True, text=True)
    assert code.returncode == 0, code.stderr
    assert not os.access(shifted / "verify", os.W_OK)
    # the runner leaves the run dir read-only too
    shifted.chmod(stat.S_IMODE(shifted.stat().st_mode) & ~0o222)
    try:
        before = sorted(p.name for p in (shifted / "verify").iterdir())
        code, err = add(shifted, tmp_path, finding(shifted, "ds.r"))
        assert code == 0, err
        assert sorted(p.name for p in (shifted / "verify").iterdir()) == before + ["evidence.1.json"] or \
            "evidence.1.json" in [p.name for p in (shifted / "verify").iterdir()]
        assert not (shifted / "verify" / "visual-review.json").exists()
        doc = ac.load_json(shifted / "amend" / "visual-review.json")
        assert doc["findings"][0]["run_id"] and doc["findings"][0]["plot_sha256"]
        ev = ac.load_json(shifted / "verify" / "evidence.1.json")
        assert any(o["kind"] == "vis" for o in ev["objects"])
        # a bad finding after seal is still rejected and nothing is written
        code, err = add(shifted, tmp_path, finding(shifted, "ds.r", plot_sha256="0" * 64))
        assert code == plot.EXIT_REJECTED
        assert len(ac.load_json(shifted / "amend" / "visual-review.json")["findings"]) == 1
        st = plot.review_status(plot.Run(shifted))
        assert st["datasets"]["ds.r"]["answered"] == ["Q1"]
    finally:
        shifted.chmod(stat.S_IMODE(shifted.stat().st_mode) | 0o200)
