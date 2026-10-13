"""09 §7.7 extras: LAYER_AMBIGUOUS, OUTPUT_MISSING, legacy input refused as reference, shapefile truncation."""
import json

import _runbuilder as rb
import compare
from conftest import (CRS_M, check, codes, kd_block, legacy_raster_array, pair, spec, vector_frame, write_raster,
                      write_vector)


def test_multilayer_gpkg_without_layer_is_ambiguous(tmp_path):
    g = vector_frame()
    leg = tmp_path / "multi.gpkg"
    write_vector(leg, g, layer="a")
    write_vector(leg, g, layer="b")
    new = write_vector(tmp_path / "n.gpkg", g, layer="a")
    r = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}))
    assert r["verdict"] == "EVALUATOR_ERROR"
    assert codes(r) == {"LAYER_AMBIGUOUS"}
    # with the layer declared it compares
    r2 = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}, layer="a"))
    assert r2["verdict"] == "PASS_EXACT"


def test_missing_new_output_pair_mode(raster_pair):
    tmp, leg = raster_pair
    r = pair(leg, tmp / "does_not_exist.tif", spec("raster"))
    assert r["verdict"] == "FAIL" and codes(r) == {"OUTPUT_MISSING"}


def _one_raster_project(tmp_path, *, produce=True, step_status=None):
    legacy = tmp_path / "legacy"
    write_raster(legacy / "in" / "src.tif", legacy_raster_array())
    write_raster(legacy / "out" / "a.tif", legacy_raster_array())
    g = rb.graph([rb.step("s1")],
                 [rb.dataset("ds.src", "raster", "input", "in/src.tif"),
                  rb.dataset("ds.a", "raster", "intermediate", "out/a.tif", produced_by="s1")],
                 reads=[("s1", "ds.src")])
    outs = {"ds.a": legacy / "out" / "a.tif"} if produce else {}
    return rb.build(tmp_path, g, outputs=outs, step_status=step_status)


def test_missing_new_output_run_mode(tmp_path):
    run = _one_raster_project(tmp_path, produce=False)
    results, rc = compare.run_compare(run)
    assert rc != 0
    assert results["ds.a"]["verdict"] == "FAIL"
    assert "OUTPUT_MISSING" in results["ds.a"]["reason_codes"]


def test_legacy_input_offered_as_reference_is_refused(tmp_path):
    legacy = tmp_path / "legacy"
    write_raster(legacy / "in" / "src.tif", legacy_raster_array())
    g = rb.graph([rb.step("s1")],
                 [rb.dataset("ds.src", "raster", "input", "in/src.tif"),
                  # a non-input dataset whose "reference" is the legacy input file
                  rb.dataset("ds.a", "raster", "intermediate", "in/src.tif", produced_by="s1",
                             new_path="s1/a.tif")],
                 reads=[("s1", "ds.src")], legacy_output_dirs=["out"])
    run = rb.build(tmp_path, g, outputs={"ds.a": legacy / "in" / "src.tif"},
                   inventory_roles={"in/src.tif": "input"})
    results, rc = compare.run_compare(run)
    assert "ds.src" not in results                       # inputs are never compared
    r = results["ds.a"]
    assert r["verdict"] == "EVALUATOR_ERROR"
    assert "REFERENCE_NOT_INDEPENDENT" in r["reason_codes"]
    assert rc != 0


def test_pair_mode_refuses_input_role(raster_pair):
    tmp, leg = raster_pair
    r = pair(leg, leg, spec("raster", role="input"))
    assert r["verdict"] == "EVALUATOR_ERROR" and "REFERENCE_NOT_INDEPENDENT" in codes(r)


def _shapefile_case(tmp_path):
    g = vector_frame()
    g = g.rename(columns={"value": "value"})
    g["population_total"] = g["value"] * 2
    short = g.rename(columns={"population_total": "populati_1"})
    leg = tmp_path / "shp" / "zones.shp"
    leg.parent.mkdir()
    write_vector(leg, short, layer="zones", driver="ESRI Shapefile")
    new = write_vector(tmp_path / "zones.gpkg", g, layer="zones")
    return leg, new


def test_shapefile_truncation_with_declared_rename_is_exact(tmp_path):
    leg, new = _shapefile_case(tmp_path)
    s = spec("vector", compare={"key": ["fid_key"], "rename": {"populati_1": "population_total"}})
    r = pair(leg, new, s)
    assert r["verdict"] == "PASS_EXACT", (r["reason_codes"], [c for c in r["checks"] if c["state"] != "PASS_EXACT"])
    # without the rename it is a schema failure
    r2 = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}))
    assert r2["verdict"] == "FAIL" and "SCHEMA_MISMATCH" in codes(r2)


def test_shapefile_truncation_with_rename_kd_is_listed(tmp_path):
    """DESIGN §4.6: a KD normalisation passes as PASS_WITHIN_TOLERANCE with the KD listed."""
    import yaml
    leg, new = _shapefile_case(tmp_path)
    kd = yaml.safe_load(kd_block("KD-001", "normalisation", ["ds.synthetic"], ["vector.schema"],
                                 {"rename": {"populati_1": "population_total"}}).split("```yaml\n")[1].split("```")[0])
    r = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}, known_differences=[kd]))
    assert r["verdict"] == "PASS_WITHIN_TOLERANCE"
    assert [k["id"] for k in r["known_differences_applied"]] == ["KD-001"]
    assert check(r, "vector.schema")["kd"] == ["KD-001"]
    assert CRS_M  # synthetic CRS only
    json.dumps(r, allow_nan=False)
