"""Tolerance records (two numbers, human, dated) and known-difference rules (DESIGN §4.6)."""
import numpy as np
import yaml

import compare
from conftest import (SIGNED, check, codes, kd_block, legacy_raster_array, pair, spec, table_rows, tol_record,
                      write_csv, write_raster)


def _kd(**kw):
    text = kd_block(**kw)
    return yaml.safe_load(text.split("```yaml\n")[1].split("```")[0])


def _small_drift(tmp, rel=1e-7):
    a = legacy_raster_array()
    leg = write_raster(tmp / "l.tif", a, dtype="float64")
    b = np.where(a == -9999.0, a, a.astype("float64") * (1 + rel))
    new = write_raster(tmp / "n.tif", b, dtype="float64")
    return leg, new


def _tol(*records):
    return {"version": 1, "records": list(records)}


def test_exact_by_default(tmp_path):
    leg, new = _small_drift(tmp_path)
    r = pair(leg, new, spec("raster"))
    assert r["verdict"] == "FAIL" and "RASTER_VALUE_MISMATCH" in codes(r)


def test_signed_justified_record_passes_within_tolerance(tmp_path):
    leg, new = _small_drift(tmp_path)
    rec = tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3)
    r = pair(leg, new, spec("raster", tolerances=_tol(rec)))
    v = check(r, "raster.values")
    assert v["state"] == "PASS_WITHIN_TOLERANCE" and v["tolerance_id"] == rec["id"]
    assert r["verdict"] == "PASS_WITHIN_TOLERANCE"
    assert r["within_tolerance"] and r["within_tolerance"][0]["check"] == "raster.values"


def test_record_without_two_numbers_is_unjustified_and_runs_exact(tmp_path):
    leg, new = _small_drift(tmp_path)
    for bad in (tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-5, wrong=1e-3),   # spread > thr
                tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-7),   # wrong < thr
                {k: v for k, v in tol_record("ds.synthetic", "raster.values", rtol=1e-6).items()
                 if k != "nearest_wrong"}):
        r = pair(leg, new, spec("raster", tolerances=_tol(bad)))
        v = check(r, "raster.values")
        assert v["state"] == "FAIL" and "TOLERANCE_UNJUSTIFIED" in v["reason_codes"], v


def test_record_agreed_after_run_start_is_unjustified(tmp_path):
    leg, new = _small_drift(tmp_path)
    rec = tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3,
                     agreed_at="2026-10-16T11:00:00Z")
    r = pair(leg, new, spec("raster", tolerances=_tol(rec)))
    assert check(r, "raster.values")["state"] == "FAIL"
    assert "TOLERANCE_UNJUSTIFIED" in codes(r)
    # a date-only agreed_at on the run day is read as the end of that day (fail closed)
    rec2 = dict(rec, agreed_at="2026-10-16")
    assert check(pair(leg, new, spec("raster", tolerances=_tol(rec2))), "raster.values")["state"] == "FAIL"


def test_unsigned_record_that_would_pass_needs_decision(tmp_path):
    leg, new = _small_drift(tmp_path)
    rec = tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3, agreed_by=None,
                     agreed_at=None)
    r = pair(leg, new, spec("raster", tolerances=_tol(rec)))
    v = check(r, "raster.values")
    assert v["state"] == "NEEDS_DECISION" and "TOLERANCE_UNSIGNED" in v["reason_codes"]
    assert v["proposed"]["record_id"] == rec["id"]
    assert v["proposed"]["correct_spread"] == 1e-8 and v["proposed"]["nearest_wrong"] == 1e-3
    assert r["verdict"] == "NEEDS_DECISION"
    # a model cannot sign
    rec_m = dict(rec, agreed_by="agent", agreed_at="2026-10-15T00:00:00Z")
    assert check(pair(leg, new, spec("raster", tolerances=_tol(rec_m))), "raster.values")["state"] == "NEEDS_DECISION"


def test_unsigned_record_that_would_not_pass_stays_fail(tmp_path):
    leg, new = _small_drift(tmp_path, rel=1e-4)
    rec = tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3, agreed_by=None)
    v = check(pair(leg, new, spec("raster", tolerances=_tol(rec))), "raster.values")
    assert v["state"] == "FAIL" and "TOLERANCE_UNSIGNED" not in v["reason_codes"]


def test_unknown_record_key_is_evaluator_error(tmp_path):
    leg, new = _small_drift(tmp_path)
    rec = tol_record("ds.synthetic", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3, sneaky=True)
    r = pair(leg, new, spec("raster", tolerances=_tol(rec)))
    assert check(r, "raster.values")["state"] == "EVALUATOR_ERROR"


def test_degree_units_invalid(tmp_path):
    from conftest import vector_frame, write_vector
    g = vector_frame()
    leg = write_vector(tmp_path / "l.gpkg", g)
    g2 = g.copy()
    g2.loc[0, "geometry"] = g2.loc[0, "geometry"].buffer(0.01)
    new = write_vector(tmp_path / "n.gpkg", g2, layer="l")
    rec = tol_record("ds.synthetic", "vector.hausdorff_m", spread=0.0, wrong=1.0, value=0.1, units="degree")
    r = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}, tolerances=_tol(rec)))
    assert "GEOMETRY_MEASURE_CRS_INVALID" in codes(r)
    r2 = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}, measure_crs="EPSG:3857"))
    assert "GEOMETRY_MEASURE_CRS_INVALID" in codes(r2)
    r3 = pair(leg, new, spec("vector", compare={"key": ["fid_key"]}, measure_crs="EPSG:4326"))
    assert "GEOMETRY_MEASURE_CRS_INVALID" in codes(r3)


# ------------------------------------------------------------------ known differences
def _widened(tmp):
    a = legacy_raster_array()
    leg = write_raster(tmp / "l.tif", a, dtype="float32")
    new = write_raster(tmp / "n.tif", a.astype("float64"), dtype="float64")
    return leg, new


def test_dtype_widening_kd_normalisation(tmp_path):
    leg, new = _widened(tmp_path)
    assert "DTYPE_MISMATCH" in codes(pair(leg, new, spec("raster")))
    kd = _kd(kid="KD-001", kind="normalisation", datasets=["ds.synthetic"], checks=["raster.dtype"],
             rule={"dtype_widening": {"from": "float32", "to": "float64"}})
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    assert r["verdict"] == "PASS_WITHIN_TOLERANCE"
    assert check(r, "raster.dtype")["kd"] == ["KD-001"]
    assert r["known_differences_applied"][0]["id"] == "KD-001"


def test_kd_model_owner_ignored(tmp_path):
    leg, new = _widened(tmp_path)
    kd = _kd(kid="KD-002", kind="normalisation", datasets=["ds.synthetic"], checks=["raster.dtype"],
             rule={"dtype_widening": {"from": "float32", "to": "float64"}}, owner="agent")
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    # a non-human owner makes it an unsigned proposal: never a pass
    assert r["verdict"] == "NEEDS_DECISION"
    assert "KD_UNSIGNED" in check(r, "raster.dtype")["reason_codes"]
    assert any(i["id"] == "KD-002" for i in r["known_differences_ignored"])


def test_kd_proposed_that_would_cover_needs_decision(tmp_path):
    leg, new = _widened(tmp_path)
    kd = _kd(kid="KD-003", kind="normalisation", datasets=["ds.synthetic"], checks=["raster.dtype"],
             rule={"dtype_widening": {"from": "float32", "to": "float64"}}, status="proposed")
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    c = check(r, "raster.dtype")
    assert c["state"] == "NEEDS_DECISION" and "KD_UNSIGNED" in c["reason_codes"]
    assert c["proposed_kd"] == ["KD-003"]


def test_kd_decided_after_run_start_enters_only_next_run(tmp_path):
    leg, new = _widened(tmp_path)
    kd = _kd(kid="KD-004", kind="normalisation", datasets=["ds.synthetic"], checks=["raster.dtype"],
             rule={"dtype_widening": {"from": "float32", "to": "float64"}}, decided_at="2026-10-16T12:00:00Z")
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    assert check(r, "raster.dtype")["state"] == "FAIL"
    assert any(i["id"] == "KD-004" and "after" in i["why"] for i in r["known_differences_ignored"])


def test_accepted_change_never_changes_state(tmp_path):
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    new = write_raster(tmp_path / "n.tif", np.where(a == -9999.0, a, a * 1000))
    kd = _kd(kid="KD-005", kind="accepted_change", datasets=["ds.synthetic"], checks=["raster.values"],
             rule={"expected": "new = legacy * 1000"}, bound={"metric": "max_rel", "max": 1e-6})
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    assert r["verdict"] == "FAIL" and check(r, "raster.values")["state"] == "FAIL"
    k = check(r, "kd:KD-005")
    assert k["kd_check"] is True and k["held"] is True
    applied = r["known_differences_applied"][0]
    assert applied["held"] is True and applied["covers_checks"] == ["raster.values"]
    # bound violated on another difference
    b = np.where(a == -9999.0, a, a * 1000)
    b[30, 30] += 5
    new2 = write_raster(tmp_path / "n2.tif", b)
    assert check(pair(leg, new2, spec("raster", known_differences=[kd])), "kd:KD-005")["held"] is False


def test_exclusion_kd_and_never_for_final(tmp_path):
    leg, new = _widened(tmp_path)
    kd = _kd(kid="KD-006", kind="exclusion", datasets=["ds.synthetic"], checks=[],
             rule={"reason": "NO_REFERENCE"})
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    assert r["verdict"] == "NOT_COMPARED" and r["excluded"] is True
    r2 = pair(leg, new, spec("raster", role="final", known_differences=[kd]))
    assert r2["verdict"] == "FAIL"
    assert any(i["id"] == "KD-006" for i in r2["known_differences_ignored"])


def test_nodata_equivalence_and_string_strip(tmp_path):
    a = legacy_raster_array()
    leg = write_raster(tmp_path / "l.tif", a)
    new = write_raster(tmp_path / "n.tif", np.where(a == -9999.0, -1.0, a), nodata=-1.0)
    kd = _kd(kid="KD-007", kind="normalisation", datasets=["ds.synthetic"], checks=["raster.nodata_value"],
             rule={"nodata_equivalent": [-9999.0, -1.0]})
    r = pair(leg, new, spec("raster", known_differences=[kd]))
    assert r["verdict"] == "PASS_WITHIN_TOLERANCE"
    rows = table_rows()
    lt = write_csv(tmp_path / "l.csv", rows)
    rows2 = [dict(x, label=x["label"] + " ") for x in rows]
    nt = write_csv(tmp_path / "n.csv", rows2)
    s = spec("table", compare={"key": ["id"]})
    assert pair(lt, nt, s)["verdict"] == "FAIL"
    kd2 = _kd(kid="KD-008", kind="normalisation", datasets=["ds.synthetic"], checks=["table.attr:label"],
              rule={"string_strip": {"columns": ["label"]}})
    r2 = pair(lt, nt, dict(s, known_differences=[kd2]))
    assert r2["verdict"] == "PASS_WITHIN_TOLERANCE"
    assert check(r2, "table.attr:label")["kd"] == ["KD-008"]


def test_kd_markdown_parsing(tmp_path):
    md = "# Known differences\n\n" + kd_block("KD-001", "normalisation", ["ds.a"], ["raster.dtype"],
                                                {"dtype_widening": {"from": "float32", "to": "float64"}})
    md += kd_block("KD-002", "legacy_defect_preserved", ["ds.b"], [], None)
    p = tmp_path / "kd.md"
    p.write_text(md)
    kds, errs = compare.parse_known_differences(p.read_text())
    assert [k["id"] for k in kds] == ["KD-001", "KD-002"] and errs == []
    assert SIGNED
