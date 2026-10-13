"""L1-L18 (06 §6.2) on tmp copies of the lux fixture. Numbers pinned here were measured with measure_lux.py
(lux_measurements.json) and agree with 06 §4 / PROVENANCE unless a comment says otherwise."""
import json
import shutil
import sqlite3
import subprocess
import zipfile
from pathlib import Path

import numpy as np
import pytest
import yaml

import _runbuilder as rb
import axis_common as ac
import compare
import lux_independent as I
import lux_lib as L

PROPOSALS = Path(__file__).resolve().parent / "lux_tolerance_proposals.yaml"


def signed_proposals_text() -> str:
    """The measured proposals signed by a TEST identity, for a tmp project only (never the real migration)."""
    doc = yaml.safe_load(PROPOSALS.read_text())
    doc["records"] = [L.signed(r) for r in doc["records"]]
    return yaml.safe_dump(doc, sort_keys=False)


def unsigned_proposals_text() -> str:
    return PROPOSALS.read_text()


def chk(r, cid):
    return next(c for c in r["checks"] if c["id"] == cid)


def lux_run(tmp: Path, outputs: dict, **kw) -> Path:
    if not (tmp / L.LEGACY_NAME).exists():
        L.copy_fixture(tmp)
    return L.build_run(tmp, outputs, **kw)


def receipts(run: Path, results: dict, *, flag=(), wrong_sha=()):
    """Fake but hash-consistent plots + receipts (plot.py is another builder's; receipts only cite plot hashes)."""
    findings = []
    for did, r in results.items():
        if r["family"] not in ("raster", "vector") or not r["checks"]:
            continue
        d = run / "verify" / "plots" / did
        d.mkdir(parents=True, exist_ok=True)
        png = b"\x89PNG\r\n\x1a\n" + did.encode()
        (d / "overview.png").write_bytes(png)
        sha = ac.sha256_bytes(png)
        ac.dump_json(d / "plots.json", {"dataset_id": did, "png": [{"name": "overview.png", "sha256": sha}]})
        yes = did in flag
        findings.append({"finding_id": f"vf-{did}", "run_id": run.name, "dataset_id": did, "question_id": "Q1",
                         "category": "whole_offset" if yes else "none", "answer": "yes" if yes else "no",
                         "effect": "flag" if yes else "none", "plot": f"plots/{did}/overview.png",
                         "plot_sha256": "0" * 64 if did in wrong_sha else sha, "panel": "diff", "location": None,
                         "note": "devtest receipt"})
    ac.dump_json(run / "verify" / "visual-review.json", {"schema": "axis-migrate.visual-review.v1",
                                                          "rubric_id": "rubric.v1", "rubric_sha256": "test",
                                                          "findings": findings})


# =================================================================================================== L1 identity
@pytest.mark.parametrize("did", L.PAIRED)
def test_L1_pair_identity_copy(tmp_path, did):
    src = L.FIXTURE / L.DATASETS[did][2]
    dst = tmp_path / src.name
    shutil.copy2(src, dst)
    r = compare.compare_pair(str(src), str(dst), L.pair_spec(did))
    assert r["verdict"] == "PASS_EXACT", (did, r["reason_codes"], r.get("detail"))
    assert "CANDIDATE_IDENTICAL_TO_REFERENCE" in r["diagnostic_codes"]


def test_L1_pair_cli_every_dataset(tmp_path):
    for did in L.PAIRED:
        src = L.FIXTURE / L.DATASETS[did][2]
        dst = tmp_path / src.name
        shutil.copy2(src, dst)
        spec = tmp_path / f"{did}.spec.json"
        spec.write_text(json.dumps(L.pair_spec(did)))
        p = subprocess.run([str(L.PLUGIN / "bin" / "axpy"), str(L.VERIFY / "compare.py"), "pair", "--legacy", str(src),
                            "--new", str(dst), "--spec", str(spec), "--out", str(tmp_path / f"{did}.json")],
                           capture_output=True, text=True)
        assert p.returncode == 0, (did, p.stdout, p.stderr)
        assert json.loads((tmp_path / f"{did}.json").read_text())["verdict"] == "PASS_EXACT"


def test_L1_run_identity_and_pipeline(tmp_path):
    leg = L.copy_fixture(tmp_path)
    run = lux_run(tmp_path, L.legacy_copies(leg))
    results, rc = compare.run_compare(run)
    assert rc == 0 and set(results) == set(L.PAIRED)
    assert all(r["verdict"] == "PASS_EXACT" for r in results.values())
    receipts(run, results)
    v, arc = compare.aggregate(run)
    assert v["pipeline"]["verdict"] == "PASS_EXACT" and arc == 0
    notes = json.loads((run / "verify" / "pairing.json").read_text())["notes"]
    assert notes == []                                   # every legacy output file is paired; sidecars excluded


def _bump_gpkg(p: Path):
    con = sqlite3.connect(p)
    con.execute("UPDATE gpkg_contents SET last_change = '2031-01-01T00:00:00.000Z'")
    con.commit()
    con.close()


def _bump_xlsx(p: Path):
    tmp = p.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(p) as zi, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zo:
        for item in zi.infolist():
            data = zi.read(item.filename)
            if item.filename == "docProps/core.xml":
                data = data.replace(b"2026", b"2031")
            zo.writestr(item, data)
    tmp.replace(p)


def test_L1_run_A_vs_run_B_bytes_differ_content_equal(tmp_path):
    """GPKG/XLSX bytes change per write (PROV Check 1): pins hold by content hash, compare is exact."""
    leg = L.copy_fixture(tmp_path)
    newdir = tmp_path / "runB"
    outs = {}
    for did, p in L.legacy_copies(leg).items():
        q = newdir / L.DATASETS[did][4] / p.name
        q.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, q)
        if q.suffix == ".gpkg":
            _bump_gpkg(q)
        if q.suffix == ".xlsx":
            _bump_xlsx(q)
        outs[did] = q
    run = lux_run(tmp_path, outs)
    # and the legacy GPKG itself is re-saved after the scan pin (bytes differ, content equal)
    _bump_gpkg(leg / "05_TEMP" / "03_canton_pop.gpkg")
    results, rc = compare.run_compare(run)
    assert rc == 0, {k: r["reason_codes"] for k, r in results.items() if r["verdict"] != "PASS_EXACT"}
    assert ac.sha256_file(outs["ds.04_canton_factor"]) != ac.sha256_file(leg / "05_TEMP/04_canton_factor.gpkg")


# ======================================================================================== L2 independent implementation
def test_L2_independent_implementation_unsigned_then_signed(tmp_path, work):
    tree = work / "A1_clean"
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=unsigned_proposals_text())
    results, rc = compare.run_compare(run)
    exact = {d for d, r in results.items() if r["verdict"] == "PASS_EXACT"}
    assert exact == set(L.PAIRED) - {"ds.02_res_2169", "ds.02_res_4326"}         # 11 of 13 bit-for-bit or exact
    for did in ("ds.02_res_2169", "ds.02_res_4326"):
        c = chk(results[did], "vector.attr:area_m2")
        assert c["state"] == "NEEDS_DECISION" and "TOLERANCE_UNSIGNED" in c["reason_codes"]   # never PASS unsigned
        assert c["stats"]["max_rel"] < 1e-12                                           # measured spread 8.6e-13
    run2 = lux_run(tmp_path / "signed", L.outputs_from(tree), tolerances=signed_proposals_text())
    results2, rc2 = compare.run_compare(run2)
    assert rc2 == 0
    assert results2["ds.02_res_2169"]["verdict"] == "PASS_WITHIN_TOLERANCE"
    assert sum(r["verdict"] == "PASS_EXACT" for r in results2.values()) == 11


def test_L2_golden_pipeline_if_present(tmp_path):
    golden = L.REPO / "evals" / "golden" / "lux" / "out"
    if not golden.exists():
        pytest.skip("evals/golden/lux/out not built")
    for did, p in L.outputs_from(golden).items():
        r = compare.compare_pair(str(L.FIXTURE / L.DATASETS[did][2]), str(p),
                                 L.pair_spec(did, tolerances=yaml.safe_load(signed_proposals_text())))
        assert r["verdict"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE"), (did, r["reason_codes"])


def test_L2b_make_valid_linework(tmp_path, variant):
    tree = variant("W_s02_linework")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    for did in ("ds.02_res_2169", "ds.02_res_4326"):
        assert "GEOMETRY_TYPE_MISMATCH" in results[did]["reason_codes"]
        assert chk(results[did], "vector.geometry_type")["detail"]["new_counts"].get("GeometryCollection") == 13
    m = chk(results["ds.02_res_mask"], "raster.mask")
    assert m["value"] == 3 and m["reason_codes"] == ["NODATA_MASK_MISMATCH"]
    v, _ = compare.aggregate(run)
    assert v["first_failing_steps"] == ["s02_landuse_mask"] and v["steps"]["s02_landuse_mask"]["blame"] == "own"


# ================================================================================================== L3 -tap shift
def test_L3_tap_half_cell_shift(tmp_path, variant):
    tree = variant("W_s01_tap")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    r = results["ds.01_pop_lux"]
    t = chk(r, "raster.grid.transform")
    assert t["state"] == "FAIL" and t["reason_codes"] == ["RASTER_TRANSFORM_MISMATCH"]
    sx, sy = t["diagnostics"]["shift_px"]
    # measured with the 10 §3 T1a formula: (-0.500019, -0.499726); PROV reports (+0.5, -0.4997) — same magnitude
    assert abs(abs(sx) - 0.5) < 1e-3 and abs(abs(sy) - 0.4997) < 1e-3
    assert chk(r, "conservation.total")["state"] == "PASS_EXACT"        # why the grid gate exists
    assert chk(r, "raster.values")["reason_codes"] == ["GATED"]
    v, _ = compare.aggregate(run)
    assert v["first_failing_steps"] == ["s01_prepare_population"]
    for s in ("s02_landuse_mask", "s03_zonal_population", "s04_distribute_value"):
        assert v["steps"][s]["blame"] == "inherited", (s, v["steps"][s])
    assert any(c.startswith("INHERITED_FROM:ds.01_") for c in v["steps"]["s02_landuse_mask"]["reason_codes"])


# ================================================================================================== L4 crop
def test_L4_crop_to_cutline(variant):
    tree = variant("W_s01_crop")
    p = L.outputs_from(tree, ["ds.01_pop_lux"])["ds.01_pop_lux"]
    r = compare.compare_pair(str(L.FIXTURE / "05_TEMP/01_pop_lux.tif"), str(p), L.pair_spec("ds.01_pop_lux"))
    s = chk(r, "raster.grid.shape")
    assert s["new"] == [881, 953] and s["diagnostics"]["offset_cells"] == [-10.0, -1.0]
    assert "RASTER_SHAPE_MISMATCH" in r["reason_codes"] and r["verdict"] == "FAIL"
    assert chk(r, "conservation.total")["state"] == "PASS_EXACT"


# ================================================================================================== L5 bilinear
def test_L5_bilinear_round_trip(tmp_path, variant):
    tree = variant("W_s01_bilinear")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    r = results["ds.01_pop_lux"]
    assert {"RASTER_VALUE_MISMATCH", "NODATA_MASK_MISMATCH", "TOTAL_DRIFT"} <= set(r["reason_codes"])
    assert chk(r, "raster.mask")["value"] == 4675                               # 06 §6.2: 4,675
    assert abs(chk(r, "conservation.total")["value"] - 0.00265) < 1e-4          # 06: -0.27 %
    assert abs(chk(r, "conservation.zone_totals")["value"] - 0.0449) < 1e-3     # 06: <= 4.5 %
    assert "SMOOTHING_SUSPECTED" in r["diagnostic_codes"]
    assert "ZONE_TOTAL_DRIFT" in results["ds.03_pop_res"]["reason_codes"]


# ================================================================================================== L6 centre mask
def test_L6_cell_centre_mask(tmp_path, variant):
    tree = variant("W_s02_centre_mask")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    assert chk(results["ds.02_res_mask"], "raster.mask")["value"] == 24377      # 06: 24,377
    e = results["ds.lux_res_exposure_2020"]
    assert chk(e, "conservation.zone_totals")["state"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")   # by construction
    assert chk(e, "raster.mask")["state"] == "FAIL" and chk(e, "raster.values")["state"] == "FAIL"
    assert e["verdict"] == "FAIL"


# ================================================================================================== L7 factor all_touched
def test_L7_factor_all_touched(tmp_path, variant):
    tree = variant("W_s04_factor_all_touched")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    e = results["ds.lux_res_exposure_2020"]
    assert chk(e, "raster.values")["stats"]["count_over"] == 95                  # 06: 95 cells
    z = chk(e, "conservation.zone_totals")
    assert z["state"] == "FAIL" and abs(z["value"] - 1.794e-3) < 1e-5           # 06: 1.79e-3
    assert results["ds.04_factor"]["verdict"] == "FAIL"


# ================================================================================================== L8 unstripped names
def test_L8_unstripped_names(tmp_path, variant):
    tree = variant("W_s04_nostrip")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    cf = results["ds.04_canton_factor"]
    assert chk(cf, "vector.attr:vpc_eur")["reason_codes"][0] == "NULL_PATTERN_MISMATCH"
    assert chk(cf, "vector.attr:vpc_eur")["samples"][0]["key"] == ["Wiltz"]
    z = chk(results["ds.lux_res_exposure_2020"], "conservation.zone_totals")
    wiltz = next(t for t in z["table"] if t["zone"] == "Wiltz")
    assert z["state"] == "FAIL" and wiltz["rel"] == 1.0 and wiltz["new"] == 0.0


# ================================================================================================== L9 no x1000
def test_L9_kEUR_not_scaled(tmp_path, variant):
    tree = variant("W_s04_noscale")
    p = L.outputs_from(tree, ["ds.lux_res_exposure_2020"])["ds.lux_res_exposure_2020"]
    r = compare.compare_pair(str(L.FIXTURE / "04_FINAL/lux_res_exposure_2020.tif"), str(p),
                             L.pair_spec("ds.lux_res_exposure_2020"))
    assert "RASTER_VALUE_MISMATCH" in r["reason_codes"]
    assert chk(r, "raster.values")["diagnostics"]["scale_factor"]["k"] == -3


# ================================================================================================== L10 area CRS
def test_L10_area_in_3857_and_degrees(variant):
    for name, expect in (("W_s02_area3857", "KEY_SET_EXTRA"), ("W_s02_area_degrees", "OUTPUT_EMPTY")):
        p = L.outputs_from(variant(name), ["ds.02_res_2169"])["ds.02_res_2169"]
        r = compare.compare_pair(str(L.FIXTURE / "05_TEMP/02_res_2169.gpkg"), str(p), L.pair_spec("ds.02_res_2169"))
        assert "FEATURE_COUNT_MISMATCH" in r["reason_codes"] and expect in r["reason_codes"], (name, r["reason_codes"])
        if name == "W_s02_area3857":
            assert chk(r, "vector.key_set")["detail"]["extra"] == 573          # 06: 2,085 vs 1,512
            assert chk(r, "vector.feature_count")["new"] == 2085


# ================================================================================================== L11 ArcPy 03b semantics
def test_L11_arcpy_semantics(variant):
    p = L.outputs_from(variant("W_s03_arcpy"), ["ds.03_pop_res"])["ds.03_pop_res"]
    r = compare.compare_pair(str(L.FIXTURE / "05_TEMP/03_pop_res.tif"), str(p), L.pair_spec("ds.03_pop_res"))
    assert r["reason_codes"] == ["NODATA_MASK_MISMATCH"]
    assert chk(r, "raster.mask")["value"] == 404349                            # 06: ~404k
    assert chk(r, "conservation.total")["state"] == "PASS_EXACT"
    assert chk(r, "conservation.zone_totals")["state"] == "PASS_EXACT"


# ================================================================================================== L12 float64 exposure
def test_L12_float64_exposure(tmp_path, variant):
    tree = variant("A3_float64_exposure")
    p = L.outputs_from(tree, ["ds.lux_res_exposure_2020"])["ds.lux_res_exposure_2020"]
    r = compare.compare_pair(str(L.FIXTURE / "04_FINAL/lux_res_exposure_2020.tif"), str(p),
                             L.pair_spec("ds.lux_res_exposure_2020"))
    assert "DTYPE_MISMATCH" in r["reason_codes"] and r["verdict"] == "FAIL"
    kd = ("## KD-001 · exposure written as float64\n\n```yaml\n" + yaml.safe_dump(
        {"id": "KD-001", "kind": "normalisation", "datasets": ["ds.lux_res_exposure_2020"], "checks": ["raster.dtype"],
         "rule": {"dtype_widening": {"from": "float32", "to": "float64"}}, "bound": None, "reason": "devtest",
         "evidence": ["L12"], "owner": "human:devtest-fixture", "decided_at": "2026-10-15T09:00:00Z",
         "decision_ref": None, "status": "accepted"}) + "```\n")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text(), kd=kd)
    results, _ = compare.run_compare(run)
    e = results["ds.lux_res_exposure_2020"]
    assert e["verdict"] == "PASS_WITHIN_TOLERANCE", e["reason_codes"]
    assert chk(e, "raster.dtype")["kd"] == ["KD-001"]
    v = chk(e, "raster.values")
    assert v["state"] == "PASS_WITHIN_TOLERANCE" and abs(v["stats"]["max_rel"] - 5.948e-8) < 1e-10   # 06: 5.9e-8
    # finding: diff_eur (float32 rounding noise, ±4.4 EUR) cannot follow a float64 exposure: tables FAIL on it
    assert "table.attr:diff_eur" in [c["id"] for c in results["ds.exposure_by_canton_csv"]["checks"]
                                      if c["state"] == "FAIL"]


# ================================================================================================== L13 missing pair
def test_L13_unported_intermediate(tmp_path):
    leg = L.copy_fixture(tmp_path)
    outs = L.legacy_copies(leg)
    outs.pop("ds.02_res_4326")
    run = lux_run(tmp_path, outs, drop_new=("ds.02_res_4326",))
    results, rc = compare.run_compare(run)
    assert results["ds.02_res_4326"]["verdict"] == "NOT_COMPARED"
    assert results["ds.02_res_4326"]["reason_codes"] == ["UNPAIRED_LEGACY"]
    receipts(run, results)
    v, arc = compare.aggregate(run)
    assert v["pipeline"]["verdict"] == "NOT_COMPARED" and arc != 0


# ================================================================================================== L14 tolerance edited
def test_L14_tolerance_edited_after_run(tmp_path):
    leg = L.copy_fixture(tmp_path)
    run = lux_run(tmp_path, L.legacy_copies(leg), tolerances=unsigned_proposals_text())
    rb.edit_live(run, "tolerances.yaml", signed_proposals_text())
    results, rc = compare.run_compare(run)
    assert rc != 0
    assert all(r["verdict"] == "EVALUATOR_ERROR" and "TOLERANCE_CHANGED_AFTER_RUN" in r["reason_codes"]
               for r in results.values())


# ================================================================================================== L15 reference modified
def test_L15_reference_modified(tmp_path):
    leg = L.copy_fixture(tmp_path)
    run = lux_run(tmp_path, L.legacy_copies(leg))
    import rasterio
    p = leg / "05_TEMP" / "02_res_mask.tif"
    with rasterio.open(p, "r+") as ds:
        a = ds.read(1)
        a[400, 400] = 1 - a[400, 400]
        ds.write(a, 1)
    results, _ = compare.run_compare(run)
    r = results["ds.02_res_mask"]
    assert r["verdict"] == "EVALUATOR_ERROR" and r["reason_codes"] == ["REFERENCE_HASH_MISMATCH"]
    assert results["ds.01_pop_lux"]["verdict"] == "PASS_EXACT"


# ================================================================================================== L16 isolation blame
def test_L16_isolation_blame(tmp_path, work, variant):
    bad = variant("W_s04_noscale")                    # s01-s03 clean independent, s04 with the bug
    outs = L.outputs_from(bad)
    run = lux_run(tmp_path, outs, tolerances=signed_proposals_text())
    compare.run_compare(run)
    iso_out = tmp_path / "iso_s04"
    shutil.copytree(work / "A1_clean" / "s03_zonal_population", iso_out / "s03_zonal_population")
    I.s04(L.FIXTURE / "03_INPUT", iso_out, pop_res=L.FIXTURE / "05_TEMP/03_pop_res.tif",
          canton_pop=L.FIXTURE / "05_TEMP/03_canton_pop.gpkg", scale=1.0)
    iso_outputs = {d: p for d, p in L.outputs_from(iso_out).items() if L.DATASETS[d][4] == "s04_distribute_value"}
    iso = lux_run(tmp_path, iso_outputs, tolerances=signed_proposals_text(), chain="isolated",
                  run_id="r20261016T110000Z-luxiso04",
                  step_status={"s01_prepare_population": "absent", "s02_landuse_mask": "absent",
                               "s03_zonal_population": "absent"})
    ires, _ = compare.run_compare(iso)
    assert set(ires) == {d for d in L.PAIRED if L.DATASETS[d][4] == "s04_distribute_value"}
    v, _ = compare.aggregate(run, isolation_runs=[iso])
    assert v["first_failing_steps"] == ["s04_distribute_value"]
    assert v["steps"]["s04_distribute_value"]["blame"] == "own"
    assert v["steps"]["s04_distribute_value"]["compare_verdict"] == "FAIL"
    for s in ("s01_prepare_population", "s02_landuse_mask", "s03_zonal_population"):
        assert v["steps"][s]["compare_verdict"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE"), (s, v["steps"][s])
        assert v["steps"][s]["blame"] is None


# ================================================================================================== L17 visual receipts
def test_L17_visual_receipts(tmp_path, variant):
    # "looks fine" on the -tap shift: stays FAIL
    run3 = lux_run(tmp_path / "l3", L.outputs_from(variant("W_s01_tap")), tolerances=signed_proposals_text())
    res3, _ = compare.run_compare(run3)
    receipts(run3, res3)
    v3, _ = compare.aggregate(run3)
    assert v3["datasets"]["ds.01_pop_lux"]["verdict"] == "FAIL"
    # a flag on identity: PASS -> NEEDS_REVIEW, never further
    leg = L.copy_fixture(tmp_path / "l1")
    run1 = lux_run(tmp_path / "l1", L.legacy_copies(leg))
    res1, _ = compare.run_compare(run1)
    receipts(run1, res1, flag=("ds.01_pop_lux",))
    v1, _ = compare.aggregate(run1)
    assert v1["datasets"]["ds.01_pop_lux"]["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_FLAG" in v1["datasets"]["ds.01_pop_lux"]["reason_codes"]
    assert v1["pipeline"]["verdict"] == "NEEDS_REVIEW"
    # a receipt citing a wrong plot hash is rejected (and is no receipt)
    receipts(run1, res1, wrong_sha=("ds.02_res_mask",))
    v1b, _ = compare.aggregate(run1)
    assert any(x["dataset_id"] == "ds.02_res_mask" for x in v1b["visual"]["rejected"])
    assert "VISUAL_REVIEW_MISSING" in v1b["datasets"]["ds.02_res_mask"]["reason_codes"]


# ================================================================================================== L18 repair loop
@pytest.mark.xfail(reason="the repair loop (migrate + runner orchestration, 06 §4f) is not built yet; compare's part "
                          "— a stable failure signature for L7 — is asserted below and passes", strict=True)
def test_L18_repair_loop(tmp_path, variant):
    tree = variant("W_s04_factor_all_touched")
    run = lux_run(tmp_path, L.outputs_from(tree), tolerances=signed_proposals_text())
    results, _ = compare.run_compare(run)
    e = results["ds.lux_res_exposure_2020"]
    assert e["verdict"] == "FAIL" and "ZONE_TOTAL_DRIFT" in e["reason_codes"]
    from importlib import import_module
    import_module("repair_loop")                       # not built: xfail until the loop exists


# ============================================================================ invariant: canton sums come back (NOTES)
def test_invariant_canton_sums_legacy_first(work, variant):
    clean = work / "A1_clean"
    exp = lambda t: str(L.outputs_from(t, ["ds.lux_res_exposure_2020"])["ds.lux_res_exposure_2020"])  # noqa: E731
    ref = str(L.FIXTURE / "04_FINAL/lux_res_exposure_2020.tif")
    cid = f"invariant:{L.INVARIANT_ID}"
    # exact (no signed record): the legacy itself misses by 1.57e-9 (float32) -> suspected legacy defect
    c = chk(compare.compare_pair(ref, exp(clean), L.invariant_pair_spec(clean)), cid)
    assert c["state"] == "NEEDS_DECISION" and "LEGACY_DEFECT_SUSPECTED" in c["reason_codes"]
    assert abs(c["legacy"] - 1.566e-9) < 1e-11                                  # PROV Check 2: 1.57e-9
    # the measured, still unsigned proposal would cover it -> NEEDS_DECISION / TOLERANCE_UNSIGNED, never PASS
    c = chk(compare.compare_pair(ref, exp(clean), L.invariant_pair_spec(
        clean, tolerances=yaml.safe_load(unsigned_proposals_text()))), cid)
    assert c["state"] == "NEEDS_DECISION" and "TOLERANCE_UNSIGNED" in c["reason_codes"]
    signed = yaml.safe_load(signed_proposals_text())
    c = chk(compare.compare_pair(ref, exp(clean), L.invariant_pair_spec(clean, tolerances=signed)), cid)
    assert c["state"] == "PASS_WITHIN_TOLERANCE"
    for name in ("W_s04_factor_all_touched", "W_s04_nostrip"):
        t = variant(name)
        c = chk(compare.compare_pair(ref, exp(t), L.invariant_pair_spec(t, tolerances=signed)), cid)
        assert c["state"] == "FAIL" and c["reason_codes"] == ["INVARIANT_VIOLATED"], (name, c)


def test_invariant_from_graph_in_run_mode(tmp_path):
    leg = L.copy_fixture(tmp_path)
    run = lux_run(tmp_path, L.legacy_copies(leg), tolerances=signed_proposals_text(),
                  invariants=[L.canton_sums_invariant()])
    results, rc = compare.run_compare(run)
    c = chk(results["ds.lux_res_exposure_2020"], f"invariant:{L.INVARIANT_ID}")
    assert c["state"] == "PASS_WITHIN_TOLERANCE" and abs(c["legacy"] - 1.566e-9) < 1e-11
    assert rc == 0


# ======================================================== 14th pair: the manual canton rename ported as code (q01)
def test_m01_ported_gives_14_pairs(tmp_path):
    golden = L.REPO / "evals" / "golden" / "lux" / "out"
    gm01 = golden / "m01_cantons_lux_edit" / "cantons_lux_edit.gpkg"
    if not gm01.exists():
        pytest.skip("golden m01 output not built")
    leg = L.copy_fixture(tmp_path)
    outs = L.legacy_copies(leg)
    outs["ds.cantons_lux_edit"] = gm01
    run = lux_run(tmp_path, outs, tolerances=signed_proposals_text(), migrate_m01=True)
    results, rc = compare.run_compare(run)
    assert len(results) == 14
    r = results["ds.cantons_lux_edit"]
    assert r["verdict"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE"), (r["reason_codes"], r.get("detail"))
    assert r["pairing"]["reference_kind"] == "manual_step_artefact" and r["step_id"] == "m01_cantons_lux_edit"
    assert rc == 0
    # not ported (keep_legacy): the same file stays an input and is never compared
    run2 = lux_run(tmp_path / "kept", L.legacy_copies(leg))
    assert "ds.cantons_lux_edit" not in compare.run_compare(run2)[0]
