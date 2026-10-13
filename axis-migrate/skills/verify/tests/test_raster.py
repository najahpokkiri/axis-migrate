"""Raster family rules: gates, far corner, CRS equality, masks before values, NaN, conservation, diagnostics,
never warps."""
import json
import re
from pathlib import Path

import numpy as np
import pytest

import axis_common as ac
import compare
from conftest import (CRS_M, N, ORIGIN, RES, VERIFY, check, codes, diag, legacy_raster_array, pair, spec,
                      transform, vector_frame, write_raster, write_vector)


def test_identity_is_exact_and_flags_identical_bytes(raster_pair):
    tmp, leg = raster_pair
    r = pair(leg, leg, spec("raster"))
    assert r["verdict"] == "PASS_EXACT"
    assert "CANDIDATE_IDENTICAL_TO_REFERENCE" in diag(r)
    assert r["refs"]["out"] == "out:" + ac.sha256_file(leg)
    assert r["comparator"]["sha256"] == ac.sha256_file(VERIFY / "compare.py")
    assert r["result_id"].startswith("cmp:")


def test_far_corner_term_catches_cell_size_drift(raster_pair):
    tmp, leg = raster_pair
    # origin identical, cell size off by 1e-7 relative: origin deviation 0, far corner 50*1e-7 = 5e-6 px
    new = write_raster(tmp / "n.tif", legacy_raster_array(), tr=transform(res=RES * (1 + 1e-7)))
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL" and "RASTER_TRANSFORM_MISMATCH" in codes(r)
    t = check(r, "raster.grid.transform")
    assert t["value"] > 1e-6 and t["threshold"] == 1e-6
    # below the threshold passes
    new2 = write_raster(tmp / "n2.tif", legacy_raster_array(), tr=transform(res=RES * (1 + 1e-9)))
    assert pair(leg, new2, spec("raster"))["verdict"] == "PASS_EXACT"


def test_half_cell_shift_keeps_total_but_fails_gate(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "n.tif", legacy_raster_array(), tr=transform(ORIGIN[0] + RES / 2, ORIGIN[1] - RES / 2))
    r = pair(leg, new, spec("raster", compare={"measure": "extensive"}))
    t = check(r, "raster.grid.transform")
    assert t["state"] == "FAIL" and t["diagnostics"]["shift_px"] == [0.5, 0.5]
    assert check(r, "raster.values")["reason_codes"] == ["GATED"]
    assert check(r, "raster.mask")["reason_codes"] == ["GATED"]
    assert check(r, "conservation.total")["state"] == "PASS_EXACT"     # runs even when gated


def test_crs_equality_never_via_to_epsg(raster_pair):
    tmp, leg = raster_pair
    a = legacy_raster_array()
    wkt1 = ('PROJCS["custom A",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],'
            'PARAMETER["latitude_of_origin",0],PARAMETER["central_meridian",9],PARAMETER["scale_factor",0.9996],'
            'PARAMETER["false_easting",500000],PARAMETER["false_northing",0],UNIT["metre",1]]')
    wkt2 = wkt1.replace('"central_meridian",9', '"central_meridian",10').replace("custom A", "custom B")
    l2 = write_raster(tmp / "l2.tif", a, crs=wkt1)
    n2 = write_raster(tmp / "n2.tif", a, crs=wkt2)
    r = pair(l2, n2, spec("raster"))
    assert r["verdict"] == "FAIL" and "CRS_MISMATCH" in codes(r)
    src = (VERIFY / "compare.py").read_text()
    assert "to_epsg()" not in src.replace("# never to_epsg()", "")


def test_crs_missing(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "n.tif", legacy_raster_array(), crs=None)
    r = pair(leg, new, spec("raster"))
    assert "CRS_MISSING" in codes(r)


def test_band_count_gate(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "n.tif", legacy_raster_array(), count=2)
    r = pair(leg, new, spec("raster"))
    assert "RASTER_BAND_COUNT_MISMATCH" in codes(r)
    assert check(r, "raster.values")["state"] == "NOT_COMPARED"


def test_dtype_and_nodata_are_hard_but_do_not_gate(raster_pair):
    tmp, leg = raster_pair
    a = legacy_raster_array().astype("float64")
    new = write_raster(tmp / "n.tif", np.where(a == -9999.0, -1.0, a), nodata=-1.0, dtype="float64")
    r = pair(leg, new, spec("raster"))
    assert {"DTYPE_MISMATCH", "NODATA_VALUE_MISMATCH"} <= codes(r)
    assert check(r, "raster.values")["state"] == "PASS_EXACT"
    assert check(r, "raster.mask")["state"] == "PASS_EXACT"


def test_nan_never_equal_and_nodata_nan_equal(tmp_path):
    a = legacy_raster_array()
    a = np.where(a == -9999.0, np.nan, a).astype("float32")
    leg = write_raster(tmp_path / "l.tif", a, nodata=np.nan)
    new = write_raster(tmp_path / "n.tif", a, nodata=np.nan)
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "PASS_EXACT"     # NaN nodata == NaN nodata (S2), NaN cells invalid on both sides
    b = a.copy()
    b[20, 20] = np.nan                      # a valid cell becomes NaN: mask mismatch, never "equal"
    new2 = write_raster(tmp_path / "n2.tif", b, nodata=np.nan)
    r2 = pair(leg, new2, spec("raster"))
    assert r2["verdict"] == "FAIL" and "NODATA_MASK_MISMATCH" in codes(r2)
    assert check(r2, "raster.mask")["detail"]["legacy_only"] == 1


def test_integer_exact_and_categorical_transitions(tmp_path):
    a = (np.arange(N * N).reshape(N, N) % 5).astype("uint8")
    leg = write_raster(tmp_path / "l.tif", a, nodata=255)
    b = a.copy()
    b[3, 3] = 4 if a[3, 3] != 4 else 3
    new = write_raster(tmp_path / "n.tif", b, nodata=255)
    r = pair(leg, new, spec("raster", compare={"categorical": True}))
    v = check(r, "raster.values")
    assert v["state"] == "FAIL" and v["reason_codes"] == ["RASTER_CATEGORY_MISMATCH"]
    assert v["detail"]["transitions"][0]["count"] == 1


def test_non_integer_counts(tmp_path):
    a = np.round(legacy_raster_array()).astype("float32")
    leg = write_raster(tmp_path / "l.tif", a)
    b = a.copy()
    b[10, 10] += 0.25
    new = write_raster(tmp_path / "n.tif", b)
    r = pair(leg, new, spec("raster", compare={"measure": "extensive", "count": True}))
    assert "RASTER_NON_INTEGER_COUNTS" in codes(r)


def _zones(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    x0, y0 = ORIGIN
    z = gpd.GeoDataFrame({"zone": ["west", "east"]},
                         geometry=[box(x0, y0 - N * RES, x0 + 25 * RES, y0),
                                   box(x0 + 25 * RES, y0 - N * RES, x0 + N * RES, y0)], crs=CRS_M)
    return write_vector(tmp_path / "zones.gpkg", z, layer="zones")


def test_zone_totals_and_total_drift(tmp_path):
    zp = _zones(tmp_path)
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    b = a.copy()
    b[10:20, 30:40] = np.where(b[10:20, 30:40] != -9999.0, b[10:20, 30:40] * 1.5, b[10:20, 30:40])
    new = write_raster(tmp_path / "n.tif", b)
    s = spec("raster", compare={"measure": "extensive", "zones": "ds.zones:zone", "zone_rule": "centre"},
             zones={"path": str(zp), "layer": "zones", "key": "zone"})
    r = pair(leg, new, s)
    z = check(r, "conservation.zone_totals")
    assert z["state"] == "FAIL" and z["reason_codes"] == ["ZONE_TOTAL_DRIFT"]
    rows = {t["zone"]: t for t in z["table"]}
    assert rows["west"]["rel"] == 0.0 and rows["east"]["rel"] > 0
    assert check(r, "conservation.total")["reason_codes"] == ["TOTAL_DRIFT"]


def test_zone_rule_must_be_declared_for_zones(tmp_path):
    zp = _zones(tmp_path)
    leg = write_raster(tmp_path / "l.tif", legacy_raster_array())
    s = spec("raster", compare={"measure": "extensive", "zones": "ds.zones:zone"},
             zones={"path": str(zp), "layer": "zones", "key": "zone"})
    r = pair(leg, leg, s)
    assert check(r, "conservation.zone_totals")["state"] == "EVALUATOR_ERROR"


def test_invariant_evaluated_on_legacy_first(tmp_path):
    a = legacy_raster_array()
    a[30, 30] = -5.0                       # legacy itself violates min >= 0
    leg = write_raster(tmp_path / "l.tif", a)
    s = spec("raster", invariants=[{"id": "inv.nonneg", "kind": "min_ge", "lhs": "ds.synthetic", "rhs": 0}])
    r = pair(leg, leg, s)
    c = check(r, "invariant:inv.nonneg")
    assert c["state"] == "NEEDS_DECISION" and "LEGACY_DEFECT_SUSPECTED" in c["reason_codes"]
    assert r["verdict"] == "NEEDS_DECISION"
    # legacy fine, new violates -> FAIL
    leg2 = write_raster(tmp_path / "l2.tif", legacy_raster_array())
    b = legacy_raster_array()
    b[30, 30] = -5.0
    new2 = write_raster(tmp_path / "n2.tif", b)
    r2 = pair(leg2, new2, s)
    assert check(r2, "invariant:inv.nonneg")["reason_codes"] == ["INVARIANT_VIOLATED"]


def test_zone_sum_invariant(tmp_path):
    import geopandas as gpd
    zp = _zones(tmp_path)
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    import rasterio
    from rasterio.features import rasterize
    z = gpd.read_file(zp)
    zid = rasterize(((g, i + 1) for i, g in enumerate(z.geometry)), out_shape=a.shape, transform=transform())
    vals = np.where(a == -9999.0, 0, a).astype("float64")
    tab = z.copy()
    tab["total"] = [float(vals[zid == 1].sum()), float(vals[zid == 2].sum())]
    tp = write_vector(tmp_path / "tot.gpkg", tab, layer="tot")
    inv = {"id": "inv.zs", "kind": "zone_sum_equals", "lhs": "ds.synthetic", "zones": "ds.zones:zone",
           "rhs": "ds.tot:total", "rule": "centre",
           "resolved": {"zones": {"path": str(zp), "layer": "zones"},
                        "rhs": {"legacy": {"path": str(tp), "layer": "tot"}, "new": {"path": str(tp), "layer": "tot"}}}}
    r = pair(leg, leg, spec("raster", invariants=[inv]))
    c = check(r, "invariant:inv.zs")
    assert c["state"] in ("PASS_EXACT",), c


def test_diagnostic_data_offset(tmp_path):
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    b = np.full_like(a, -9999.0)
    b[:, 1:] = a[:, :-1]                   # data moved one cell east, transform unchanged
    new = write_raster(tmp_path / "n.tif", b)
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL" and "DATA_OFFSET_SUSPECTED" in diag(r)
    d = [c for c in r["checks"] if "DATA_OFFSET_SUSPECTED" in c.get("diagnostic_codes", [])][0]
    assert d["diagnostics"]["data_offset"]["shift"] == [0, 1]


def test_diagnostic_scale_factor(tmp_path):
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    new = write_raster(tmp_path / "n.tif", np.where(a == -9999.0, a, a / 1000.0))
    r = pair(leg, new, spec("raster"))
    assert "NUMERIC_SCALE_FACTOR" in diag(r)
    assert check(r, "raster.values")["diagnostics"]["scale_factor"]["k"] == -3


def _blur(a, valid):
    v = np.where(valid, a, 0.0).astype("float64")
    w = valid.astype("float64")
    acc = np.zeros_like(v)
    cnt = np.zeros_like(v)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            acc += np.roll(np.roll(v, dy, 0), dx, 1)
            cnt += np.roll(np.roll(w, dy, 0), dx, 1)
    out = np.where(cnt > 0, acc / np.maximum(cnt, 1), 0)
    return out


def test_diagnostic_smoothing_suspected(tmp_path):
    rng = np.random.default_rng(9)
    a = (rng.random((N, N)) < 0.15) * rng.uniform(10, 100, (N, N))
    a = a.astype("float32")
    leg = write_raster(tmp_path / "l.tif", a)
    valid = np.ones_like(a, dtype=bool)
    b = _blur(a, valid)
    b = (b * (a.sum(dtype="float64") / b.sum())).astype("float32")     # conserve the total
    new = write_raster(tmp_path / "n.tif", b)
    r = pair(leg, new, spec("raster", compare={"measure": "extensive"}))
    assert r["verdict"] == "FAIL" and "SMOOTHING_SUSPECTED" in diag(r)


def test_never_warps_runtime(raster_pair, monkeypatch):
    """No resampling/reprojection API is ever called on raster candidates."""
    import rasterio
    import rasterio.io
    import rasterio.warp
    import rasterio.vrt
    tmp, leg = raster_pair
    calls = []

    def _boom(*a, **k):
        calls.append(("warp", a, k))
        raise AssertionError("compare must never warp")
    monkeypatch.setattr(rasterio.warp, "reproject", _boom)
    monkeypatch.setattr(rasterio.warp, "calculate_default_transform", _boom)
    monkeypatch.setattr(rasterio.vrt.WarpedVRT, "__init__", _boom)
    orig_read = rasterio.io.DatasetReader.read

    def _read(self, *a, **k):
        assert "out_shape" not in k and "resampling" not in k and "boundless" not in k, k
        return orig_read(self, *a, **k)
    monkeypatch.setattr(rasterio.io.DatasetReader, "read", _read)
    for tr in (transform(), transform(ORIGIN[0] + 5, ORIGIN[1]), transform(res=20)):
        new = write_raster(tmp / f"n{len(calls)}_{tr.c}_{tr.a}.tif", legacy_raster_array(), tr=tr)
        pair(leg, new, spec("raster", compare={"measure": "extensive"}))
    new = write_raster(tmp / "crs.tif", legacy_raster_array(), crs="EPSG:32633")
    pair(leg, new, spec("raster", compare={"measure": "extensive"}))
    assert calls == []


def test_never_warps_static():
    src = (VERIFY / "compare.py").read_text()
    for banned in ("reproject(", "WarpedVRT", "Resampling.", "out_shape=(h", "boundless=True",
                   "calculate_default_transform"):
        assert banned not in src, banned


def test_no_except_pass():
    src = (VERIFY / "compare.py").read_text()
    assert not re.search(r"except[^\n]*:\s*\n\s*pass\b", src)
    assert not re.search(r"except[^\n]*:\s*pass\b", src)


def test_checker_exception_becomes_evaluator_error(raster_pair, monkeypatch):
    tmp, leg = raster_pair

    def _explode(*a, **k):
        raise RuntimeError("synthetic checker fault")
    monkeypatch.setattr(compare, "_raster_values", _explode)
    r = pair(leg, leg, spec("raster"))
    assert r["verdict"] == "EVALUATOR_ERROR" and "CHECKER_EXCEPTION" in codes(r)
    assert "synthetic checker fault" in r["traceback"]


def test_new_unreadable_is_fail_legacy_unreadable_is_evaluator_error(raster_pair):
    tmp, leg = raster_pair
    bad = tmp / "bad.tif"
    bad.write_bytes(b"not a tiff")
    r = pair(leg, bad, spec("raster"))
    assert r["verdict"] == "FAIL" and codes(r) == {"OUTPUT_UNREADABLE"}
    r2 = pair(bad, leg, spec("raster"))
    assert r2["verdict"] == "EVALUATOR_ERROR"


def test_nan_refused_in_json_and_null_with_reason(tmp_path):
    with pytest.raises(ac.CJSONError):
        ac.dump_json(tmp_path / "x.json", {"v": float("nan")})
    a = np.zeros((N, N), dtype="float32")
    leg = write_raster(tmp_path / "l.tif", a)
    b = a.copy()
    b[1, 1] = 5.0
    new = write_raster(tmp_path / "n.tif", b)
    r = pair(leg, new, spec("raster"))       # max_rel undefined: legacy is all zero
    v = check(r, "raster.values")
    assert v["stats"]["max_rel"] is None
    assert v["nulls"]["stats.max_rel"] == "UNDEFINED_ZERO_REFERENCE"
    json.dumps(r, allow_nan=False)


def test_mismatches_parquet(raster_pair):
    import pyarrow.parquet as pq
    tmp, leg = raster_pair
    a = legacy_raster_array()
    a[20:22, 20:22] += 1
    new = write_raster(tmp / "n.tif", a)
    out = tmp / "res.json"
    r = compare.compare_pair(str(leg), str(new), spec("raster"), mismatches_path=str(tmp / "m.parquet"))
    t = pq.read_table(tmp / "m.parquet")
    assert t.num_rows == 4 and {"check", "row", "col", "x", "y", "legacy", "new"} <= set(t.column_names)
    assert r["mismatches"].endswith("m.parquet")
    del out


def test_zone_sum_invariant_null_rhs_is_violation_never_zero(tmp_path):
    import geopandas as gpd
    zp = _zones(tmp_path)
    a = np.where(np.arange(N * N).reshape(N, N) % 25 < 25, 0.0, 1.0).astype("float32")   # all zero raster
    leg = write_raster(tmp_path / "l.tif", a)
    z = gpd.read_file(zp)
    tab = z.copy()
    tab["total"] = [0.0, 0.0]
    tl = write_vector(tmp_path / "tl.gpkg", tab, layer="tot")
    tab2 = z.copy()
    tab2["total"] = [0.0, None]                         # a null total would "equal" 0 under fillna(0)
    tn = write_vector(tmp_path / "tn.gpkg", tab2, layer="tot")
    inv = {"id": "inv.zs", "kind": "zone_sum_equals", "lhs": "ds.synthetic", "zones": "ds.zones:zone",
           "rhs": "ds.tot:total", "rule": "centre",
           "resolved": {"zones": {"path": str(zp), "layer": "zones"},
                        "rhs": {"legacy": {"path": str(tl), "layer": "tot"}, "new": {"path": str(tn), "layer": "tot"}}}}
    c = check(pair(leg, leg, spec("raster", invariants=[inv])), "invariant:inv.zs")
    assert c["state"] == "FAIL" and c["reason_codes"] == ["INVARIANT_VIOLATED"]
