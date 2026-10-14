"""map.py display: full resolution, nearest, shared scale (07 §6 test 15, §9)."""
from __future__ import annotations

import numpy as np

import axis_common as ac
import synth


def test_one_cell_stripe_visible_not_downsampled(proj):
    import rasterio
    from PIL import Image
    r = synth.make_run(proj, 1, seal=False, display=False)
    out = r["run_dir"] / "outputs" / "s02_total" / "b_exposure.tif"
    with rasterio.open(out) as src:
        arr = src.read(1)
    col = 7
    arr2 = arr.copy()
    valid = arr2[:, col] != -9999.0
    arr2[valid, col] += 1.0  # a one-cell-wide defect
    synth._write_raster(out, arr2, -9999.0, "float32")
    dispmap = synth.display_module()
    doc = dispmap.display(r["run_dir"])
    e = next(x for x in doc["datasets"] if x["dataset"] == "ds.b_exposure")
    assert e["resampling"] == "nearest" and e["native_cell_px"] >= 1
    assert e["width"] >= synth.W and e["height"] >= synth.H  # never smaller than the native grid
    diff = np.asarray(Image.open(r["run_dir"] / "verify" / "display" / e["files"]["diff"]))
    alpha = diff[..., 3] > 0
    cols = np.where(alpha.any(axis=0))[0]
    assert cols.size > 0
    # one contiguous stripe whose width equals one native cell in display pixels
    assert cols.max() - cols.min() + 1 == cols.size
    cell_px = e["width"] / synth.W
    assert abs(cols.size - cell_px) <= 1
    expected_x = (col + 0.5) * cell_px
    assert cols.min() <= expected_x <= cols.max() + 1
    # shared scale: legacy and new PNGs use one norm from both sides
    assert e["scale"]["min"] < e["scale"]["max"] and e["diff_scale"]["min"] == -e["diff_scale"]["max"] == -1.0


def test_shared_scale_shows_scale_error(proj):
    import rasterio
    r = synth.make_run(proj, 1, seal=False, display=False)
    out = r["run_dir"] / "outputs" / "s02_total" / "b_exposure.tif"
    with rasterio.open(out) as src:
        arr = src.read(1)
    arr2 = np.where(arr != -9999.0, arr * 1000, arr)
    synth._write_raster(out, arr2, -9999.0, "float32")
    dispmap = synth.display_module()
    from PIL import Image
    doc = dispmap.display(r["run_dir"])
    e = next(x for x in doc["datasets"] if x["dataset"] == "ds.b_exposure")
    leg = np.asarray(Image.open(r["run_dir"] / "verify" / "display" / e["files"]["legacy"]))
    new = np.asarray(Image.open(r["run_dir"] / "verify" / "display" / e["files"]["new"]))
    # the ×1000 side saturates at the top colour; legacy collapses to the bottom: they cannot look alike
    m = (leg[..., 3] > 0) & (new[..., 3] > 0)
    assert np.abs(leg[m][:, :3].astype(int) - new[m][:, :3].astype(int)).mean() > 50


def test_vectors_as_geojson_4326(proj):
    r = synth.make_run(proj, 1, seal=False, display=False)
    dispmap = synth.display_module()
    doc = dispmap.display(r["run_dir"])
    e = next(x for x in doc["datasets"] if x["dataset"] == "ds.a_outline")
    gj = ac.load_json(r["run_dir"] / "verify" / "display" / e["files"]["legacy"])
    assert gj["type"] == "FeatureCollection" and gj["features"][0]["properties"]["_side"] == "legacy"
    x, y = gj["features"][0]["geometry"]["coordinates"][0][0]
    assert 5.9 < x < 6.3 and 49.7 < y < 50.1
    assert {"display.json"} <= {p.name for p in (r["run_dir"] / "verify" / "display").iterdir()}
    dj = ac.load_json(r["run_dir"] / "verify" / "display" / "display.json")
    for d in dj["datasets"]:
        assert {"bounds_3857", "scale", "resampling", "native_cell_px"} <= set(d)
        assert {"min", "max", "palette", "units"} <= set(d["scale"])
