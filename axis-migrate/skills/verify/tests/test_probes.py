"""P1-P14: the 14 probes that passed wrong outputs in the old comparators (06 §6.1). Synthetic data only."""
import numpy as np

from conftest import (N, check, codes, diag, legacy_raster_array, pair, spec, table_rows, vector_frame,
                      write_csv, write_raster, write_vector, transform)


# ---------------------------------------------------------------- rasters
def test_p1_all_nan_candidate(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "new.tif", np.full((N, N), np.nan, dtype="float32"))
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL"
    assert {"NODATA_MASK_MISMATCH", "OUTPUT_EMPTY"} <= codes(r)
    # NaN is never written: nulls carry a reason
    assert "NaN" not in str(r)


def test_p2_top_half_only(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "new.tif", legacy_raster_array()[:25, :])
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL"
    assert "RASTER_SHAPE_MISMATCH" in codes(r)
    assert check(r, "raster.values")["state"] == "NOT_COMPARED"
    assert check(r, "raster.values")["reason_codes"] == ["GATED"]


def test_p2_variant_bottom_half_nodata(raster_pair):
    tmp, leg = raster_pair
    a = legacy_raster_array()
    a[25:, :] = -9999.0
    new = write_raster(tmp / "new.tif", a)
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL"
    assert "NODATA_MASK_MISMATCH" in codes(r)
    assert "RASTER_SHAPE_MISMATCH" not in codes(r)


def test_p3_few_pixels_off_no_warn_band(raster_pair):
    tmp, leg = raster_pair
    a = legacy_raster_array()
    rows, cols = np.nonzero(a != -9999.0)
    idx = np.arange(0, len(rows), 110)[:22]           # 22 of 2500 cells = 0.9 %
    a[rows[idx], cols[idx]] += 50
    new = write_raster(tmp / "new.tif", a)
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL"
    v = check(r, "raster.values")
    assert v["reason_codes"] == ["RASTER_VALUE_MISMATCH"]
    assert v["stats"]["count_over"] == 22
    assert abs(v["stats"]["max_abs"] - 50) < 1e-3
    assert len(v["samples"]) == 20 and "x" in v["samples"][0] and "y" in v["samples"][0]


def test_p4_flipped(raster_pair):
    tmp, leg = raster_pair
    new = write_raster(tmp / "new.tif", legacy_raster_array()[::-1, ::-1])
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL"
    assert {"RASTER_VALUE_MISMATCH", "NODATA_MASK_MISMATCH"} <= codes(r)


# ---------------------------------------------------------------- tables
def _tspec(**kw):
    c = {"key": ["id"]}
    c.update(kw.pop("compare", {}))
    return spec("table", compare=c, **kw)


def test_p5_column_dropped(tmp_path):
    rows = table_rows()
    leg = write_csv(tmp_path / "l.csv", rows)
    new = write_csv(tmp_path / "n.csv", rows, columns=["id", "label", "amount", "n"])
    r = pair(leg, new, _tspec())
    assert r["verdict"] == "FAIL"
    assert "SCHEMA_MISMATCH" in codes(r)
    assert "share" in check(r, "table.schema")["detail"]["missing"]


def test_p6_zero_vs_nan(tmp_path):
    rows = table_rows()
    rows[3]["share"] = 0.0
    leg = write_csv(tmp_path / "l.csv", rows)
    rows2 = [dict(r) for r in rows]
    rows2[3]["share"] = None
    new = write_csv(tmp_path / "n.csv", rows2)
    r = pair(leg, new, _tspec())
    assert r["verdict"] == "FAIL"
    assert "NULL_PATTERN_MISMATCH" in codes(r)


def test_p7_shuffled_rows_with_key(tmp_path):
    rows = table_rows()
    leg = write_csv(tmp_path / "l.csv", rows)
    new = write_csv(tmp_path / "n.csv", list(reversed(rows[::2])) + rows[1::2])
    r = pair(leg, new, _tspec())
    assert r["verdict"] == "PASS_EXACT", r["reason_codes"]


def test_p8_some_rows_plus_1000(tmp_path):
    rows = table_rows()
    leg = write_csv(tmp_path / "l.csv", rows)
    rows2 = [dict(r) for r in rows]
    rows2[7]["amount"] += 1000          # 1 of 25 rows = 4 %
    new = write_csv(tmp_path / "n.csv", rows2)
    r = pair(leg, new, _tspec())
    assert r["verdict"] == "FAIL"
    assert "ATTRIBUTE_VALUE_MISMATCH" in codes(r)
    assert "NUMERIC_OFFSET" in diag(r)
    c = check(r, "table.attr:amount")
    assert c["samples"][0]["key"] == ["r07"]


# ---------------------------------------------------------------- vectors
def _vspec(**kw):
    c = {"key": ["fid_key"]}
    c.update(kw.pop("compare", {}))
    return spec("vector", compare=c, **kw)


def test_p9_all_attributes_renamed_and_wrong(tmp_path):
    g = vector_frame()
    leg = write_vector(tmp_path / "l.gpkg", g)
    g2 = g.rename(columns={"name": "nm", "value": "val", "count": "cnt"})
    g2["val"] = g2["val"] * 3
    new = write_vector(tmp_path / "n.gpkg", g2, layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "FAIL"
    assert "SCHEMA_MISMATCH" in codes(r)


def test_p10_vector_column_dropped(tmp_path):
    g = vector_frame()
    leg = write_vector(tmp_path / "l.gpkg", g)
    new = write_vector(tmp_path / "n.gpkg", g.drop(columns=["count"]), layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "FAIL"
    assert "SCHEMA_MISMATCH" in codes(r)


def test_p11_identical_reversed_order(tmp_path):
    g = vector_frame()
    leg = write_vector(tmp_path / "l.gpkg", g)
    new = write_vector(tmp_path / "n.gpkg", g.iloc[::-1].reset_index(drop=True), layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "PASS_EXACT", r["reason_codes"]


def test_p12_one_attribute_wrong(tmp_path):
    g = vector_frame()
    leg = write_vector(tmp_path / "l.gpkg", g)
    g2 = g.copy()
    g2.loc[17, "value"] = g2.loc[17, "value"] + 0.5
    new = write_vector(tmp_path / "n.gpkg", g2, layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "FAIL"
    c = check(r, "vector.attr:value")
    assert c["reason_codes"] == ["ATTRIBUTE_VALUE_MISMATCH"]
    assert c["samples"][0]["key"] == ["k017"]
    assert c["stats"]["count_over"] == 1


def test_p13_identical_points(tmp_path):
    g = vector_frame(geom="point")
    leg = write_vector(tmp_path / "l.gpkg", g)
    new = write_vector(tmp_path / "n.gpkg", g, layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "PASS_EXACT"
    ids = {c["id"] for c in r["checks"]}
    assert "vector.hausdorff_m" in ids and "vector.symdiff_ratio" not in ids


def test_p14_strings_wrong_and_one_geometry_moved(tmp_path):
    from shapely.affinity import translate
    g = vector_frame(geom="point")
    leg = write_vector(tmp_path / "l.gpkg", g)
    g2 = g.copy()
    g2["name"] = [s.upper() for s in g2["name"]]
    for i in range(0, N, 20)[:1]:
        g2.loc[i, "geometry"] = translate(g2.loc[i, "geometry"], 3.0, 0.0)
    new = write_vector(tmp_path / "n.gpkg", g2, layer="l")
    r = pair(leg, new, _vspec())
    assert r["verdict"] == "FAIL"
    assert {"ATTRIBUTE_VALUE_MISMATCH", "GEOMETRY_HAUSDORFF_EXCEEDED"} <= codes(r)
    h = check(r, "vector.hausdorff_m")
    assert abs(h["value"] - 3.0) < 1e-6
