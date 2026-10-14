"""01-discover acceptance tests AT-1..AT-17 on a tmp copy of lux-exposure-legacy (ids per DESIGN §3 / CONTRACTS §2)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_lux_util import (FIXTURE, PLUGIN, SCAN, copy_fixture, ds, op, provenance_inventory, run,  # noqa: E402
                               tree_state)


# OS runtime files the interpreter or its libraries read on their own (narrow, exact): macOS system version plist
# (stdlib platform / _osx_support via sysconfig, first triggered by importing pandas), mimetypes tables, tz database.
import mimetypes as _mt
import re as _re
OS_RUNTIME = [_re.compile(r"^/System/Library/CoreServices/SystemVersion(Compat)?\.plist$"),
              _re.compile(r"^/usr/share/zoneinfo/[A-Za-z0-9_+\-/]+$")]
OS_RUNTIME_FILES = set(_mt.knownfiles) | {"/etc/localtime"}


def os_runtime(p: str) -> bool:
    return p in OS_RUNTIME_FILES or any(r.match(p) for r in OS_RUNTIME)

TEMP_PRODUCERS = {  # AT-3: dataset -> (op id, call line, OUTPUT arg line, tool)
    "ds.01_lux_outline": ("s01_prepare_population.op1", 31, 35, "native:dissolve"),
    "ds.01_pop_lux": ("s01_prepare_population.op2", 40, 54, "gdal:cliprasterbymasklayer"),
    "ds.02_res_2169": ("s02_landuse_mask.op4", 45, 48, "native:extractbyexpression"),
    "ds.02_res_4326": ("s02_landuse_mask.op5", 51, 54, "native:reprojectlayer"),
    "ds.02_res_mask": ("s02_landuse_mask.op7", 61, 76, "gdal:rasterize"),
    "ds.03_pop_res": ("s03_zonal_population.op3", 31, 37, "native:rastercalc"),
    "ds.03_canton_pop": ("s03_zonal_population.op5", 49, 55, "native:zonalstatisticsfb"),
    "ds.04_canton_factor": ("s04_distribute_value.op2", 46, 46, "native:savefeatures"),
    "ds.04_factor": ("s04_distribute_value.op5", 79, 94, "gdal:rasterize"),
}


# ---------------------------------------------------------------- AT-1 read-only
def test_at1_read_only_and_audit(lux):
    assert lux["before"] == lux["after_scan"] == lux["after_all"]  # scan, map and lineage write nothing there
    assert not any(p.endswith(".aux.xml") and p not in lux["before"] for p in lux["after_all"])
    assert lux["inv"]["scan"]["read_only_verified"] is True
    legacy, mig = str(lux["legacy"]), str(lux["mig"])
    allowed = (legacy, mig, lux["base_prefix"], lux["prefix"], str(PLUGIN), str(lux["root"] / "opens.txt"))
    paths = [p for p in lux["opened"] if p.startswith("/")]  # '<unknown>' = in-memory code objects, not files
    assert len(paths) > 30
    bad = [p for p in paths if not os.path.realpath(p).startswith(allowed) and not p.startswith(allowed)
           and not os_runtime(p)]
    assert not bad, bad[:10]
    assert not any("PROVENANCE" in p or ".build" in p for p in lux["opened"])
    assert not any(p.startswith(str(FIXTURE)) for p in lux["opened"])  # the code's absolute BASE is never followed


# ---------------------------------------------------------------- AT-2 completeness
def test_at2_completeness(lux):
    inv = lux["inv"]
    paths = [f["path"] for f in inv["files"]]
    assert len(paths) == len(set(paths)) == 30
    side = [f for f in inv["files"] if f["kind"] == "sidecar"]
    assert len(side) == 4 and all(f["sidecar_of"] == f["path"][: -len(".aux.xml")] for f in side)
    assert inv["scan"]["mtime_reliable"] is False


# ---------------------------------------------------------------- AT-3 / AT-4 producers with file:line
@pytest.mark.parametrize("did", sorted(TEMP_PRODUCERS))
def test_at3_temp_producers(lux, did):
    oid, line, arg, tool = TEMP_PRODUCERS[did]
    d = ds(lux["inv"], did)
    assert d["role"] == "intermediate" and d["reference_candidate"]
    assert oid in d["produced_by"]
    o = op(lux["inv"], oid)
    assert (o["line"], o["tool"]) == (line, tool)
    w = next(w for w in o["writes"] if w["dataset"] == did)
    assert w["line"] == arg and w["mode"] == "create"


def test_at3_canton_factor_in_place_writes(lux):
    o = op(lux["inv"], "s04_distribute_value.op3")
    assert {(w["line"], w["mode"]) for w in o["writes"]} == {(51, "in_place"), (72, "in_place")}
    assert o["end_line"] == 72 and o["nested"] == ["addAttributes", "changeAttributeValues"]


def test_at4_finals_and_chain_to_base(lux):
    inv = lux["inv"]
    want = {"ds.lux_res_exposure_2020": ("s04_distribute_value.op6", 97, 106),
            "ds.exposure_by_canton_gpkg": ("s04_distribute_value.op7", 109, 115),
            "ds.exposure_by_canton_csv": ("s04_distribute_value.op9", 126, 126),
            "ds.exposure_by_canton_xlsx": ("s04_distribute_value.op10", 141, 141)}
    for did, (oid, line, arg) in want.items():
        d = ds(inv, did)
        assert d["role"] == "final" and d["reference_candidate"] and d["produced_by"] == [oid]
        o = op(inv, oid)
        assert o["line"] == line and next(w for w in o["writes"] if w["dataset"] == did)["line"] == arg
    assert op(inv, "s04_distribute_value.op6")["params"]["OUTPUT"]["chain"] == [17, 12, 7]


# ---------------------------------------------------------------- AT-5 consumers, ToolOut, memory layers
def test_at5_consumers_and_memory(lux):
    inv = lux["inv"]
    lines = {(op(inv, o)["file"].split("/")[-1][:2], op(inv, o)["line"]) for o in ds(inv, "ds.01_pop_lux")["consumed_by"]}
    assert {("02", 56), ("03", 27), ("03", 40)} <= lines
    clip = op(inv, "s01_prepare_population.op2")
    mask = next(r for r in clip["reads"] if r["param"] == "MASK")
    assert mask == {"dataset": "ds.01_lux_outline", "line": 42, "param": "MASK"}
    assert "s02_landuse_mask.op5" in ds(inv, "ds.02_res_2169")["consumed_by"]
    mem = [(m["file"].split("/")[-1][:2], m["line"]) for m in inv["memory_datasets"]
           if m["file"].split("/")[-1][:2] in ("02", "03") and "03b" not in m["file"]]
    assert sorted(mem) == [("02", 30), ("02", 33), ("02", 42), ("03", 46)]


# ---------------------------------------------------------------- AT-6 roles and code status
def test_at6_roles_and_code(lux):
    inv = lux["inv"]
    inputs = sorted(d["id"] for d in inv["datasets"] if d["role"] == "input")
    assert inputs == ["ds.cantons_lux_edit", "ds.geoboundaries_lux_adm1", "ds.lux_ppp_2020", "ds.osm_landuse_lux",
                      "ds.value_per_capita"]
    assert ds(inv, "ds.geoboundaries_lux_adm1")["input_of_manual_step"] == "m01_cantons_lux_edit"
    adm2 = ds(inv, "ds.geoboundaries_lux_adm2")
    assert adm2["role"] == "unused" and adm2["unused_kind"] == "display_only"
    inter = [d for d in inv["datasets"] if d["role"] == "intermediate"]
    final = [d for d in inv["datasets"] if d["role"] == "final"]
    assert len(inter) == 9 and len(final) == 4 and all(d["reference_candidate"] for d in inter + final)
    assert sum(d["reference_candidate"] for d in inv["datasets"]) == 13
    code = {c["path"].split("/")[-1]: c for c in inv["code"]}
    live = sorted((c["order"], c["step_id"]) for c in code.values() if c["status"] == "live")
    assert [s for _, s in live] == ["s01_prepare_population", "s02_landuse_mask", "s03_zonal_population",
                                    "s04_distribute_value"]
    old = code["03b_zonal_arcpy_OLD.py"]
    assert old["status"] == "superseded" and old["twin_of"] == "code.s03_zonal_population"
    assert {"name", "comment", "doc_mention"} <= {e["kind"] for e in old["status_evidence"]}
    assert any(e.get("line") == 13 and e.get("file") == "NOTES.txt" for e in old["status_evidence"])
    run_order = next(c for c in code.values() if c["step_id"] == "s01_prepare_population")["order_evidence"]
    assert {"kind": "doc_run_order", "file": "NOTES.txt", "line": 8} in run_order


# ---------------------------------------------------------------- AT-7 manual step
def test_at7_manual_step_derived_copy(lux):
    inv = lux["inv"]
    dc = inv["derived_copies"][0]
    assert len(inv["derived_copies"]) == 1
    assert (dc["source"], dc["target"], dc["wkb_equal"], dc["count"]) == ("ds.geoboundaries_lux_adm1",
                                                                          "ds.cantons_lux_edit", 12, 12)
    assert dc["schema"]["added"] == ["canton"] and dc["schema"]["removed"] == ["shapeGroup", "shapeType"]
    vm = dc["value_maps"][0]
    assert vm["column"] == "canton" and vm["from_column"] == "shapeName"
    assert vm["rule"] == {"kind": "strip_prefix", "value": "Canton "} and vm["rule_count"] == 11
    assert vm["exceptions"] == {"Canton Esch-sur-Alzette": "Esch/Alzette"}
    assert {"kind": "doc_mention", "file": "NOTES.txt", "line": 15, "end_line": 19} in dc["evidence"]
    assert inv["manual_step_candidates"][0]["id"] == "m01_cantons_lux_edit"
    assert any(i["kind"] == "manual_step" and "m01_cantons_lux_edit" in i["about"] for i in inv["open_items"])


# ---------------------------------------------------------------- AT-8 settings
def test_at8_settings(lux):
    S = lux["inv"]["settings"]

    def one(**kw):
        hits = [s for s in S if all(s.get(k) == v for k, v in kw.items())]
        assert len(hits) == 1, (kw, hits)
        return hits[0]
    assert one(op_id="s01_prepare_population.op2", name="crop_to_cutline")["value"] is False
    assert one(op_id="s01_prepare_population.op2", name="crop_to_cutline")["line"] == 47
    assert one(op_id="s01_prepare_population.op2", name="keep_resolution")["line"] == 48
    for oid, src in (("s02_landuse_mask.op7", "ds.01_pop_lux"), ("s04_distribute_value.op5", "ds.03_pop_res")):
        for n in ("extent", "width", "height"):
            s = one(op_id=oid, name=n)
            assert s["set_by"] == "derived_from_dataset" and s["value"]["dataset"] == src
    assert one(op_id="s02_landuse_mask.op7", name="all_touched")["value"] is True
    assert one(op_id="s04_distribute_value.op5", name="all_touched")["value"] is False
    assert one(op_id="s02_landuse_mask.op7", name="nodata")["set_by"] == "default_unset"
    assert one(op_id="s02_landuse_mask.op7", name="nodata")["line"] == 70
    assert one(op_id="s04_distribute_value.op5", name="nodata")["line"] == 88
    for n, line in (("snapRaster", 23), ("cellSize", 24), ("extent", 25)):
        s = one(code="code.s03b_zonal_arcpy_old", name=n, set_by="assignment")
        assert s["line"] == line and s["value"] == {"dataset": "ds.lux_ppp_2020"} and s["from_superseded"]
    ocs = one(code="code.s03b_zonal_arcpy_old", name="outputCoordinateSystem", set_by="assignment")
    assert ocs["line"] == 26 and ocs["value"] == {"derived": "spatialReference_of", "dataset": "ds.lux_ppp_2020"}
    assert one(code="code.s03b_zonal_arcpy_old", name="workspace")["line"] == 15


# ---------------------------------------------------------------- AT-9 ArcPy on 03b
def test_at9_arcpy_03b(lux):
    inv = lux["inv"]
    ops = [o for o in inv["ops"] if o["code"] == "code.s03b_zonal_arcpy_old"]
    by = [(o["line"], o["tool"]) for o in ops]
    assert (28, "sa.ExtractByMask") in by and (29, "save") in by and (34, "save") in by
    assert (32, "sa.Con") in by and (33, "sa.Con") in by
    con32 = next(o for o in ops if o["line"] == 32)
    assert set(con32["nested"]) == {"arcpy.sa.IsNull", "arcpy.Raster"}
    assert [l for l, t in by if t == "sa.ZonalStatisticsAsTable"] == [36, 37]
    cf = next(o for o in ops if o["line"] == 41)
    assert cf["tool"] == "management.CopyFeatures" and cf["api"] == "arcpy.CopyFeatures_management"
    assert [(o["line"], o["writes"][0]["mode"]) for o in ops if o["tool"] == "management.AddField"] == \
        [(42, "in_place"), (43, "in_place")]
    assert [o["line"] for o in ops if o["tool"] == "da.SearchCursor"] == [46, 50]
    uc = next(o for o in ops if o["tool"] == "da.UpdateCursor")
    assert uc["line"] == 54 and uc["writes"][0]["mode"] == "in_place"
    gdb = [d for d in inv["datasets"] if d["id"].startswith("ds.work__")]
    assert len(gdb) == 7 and not any(d["exists"] for d in gdb)
    assert not any(i["kind"] == "missing_input" for i in inv["open_items"])  # superseded refs never block
    roots = {r["value"]: r for r in inv["scan"]["legacy_roots"]}
    assert {"kind": "commented_code", "file": "02_SCRIPTS/01_prepare_population.py", "line": 7} in \
        roots["C:\\GIS\\LUX_exposure"]["evidence"]
    assert {"kind": "code_literal", "file": "02_SCRIPTS/03b_zonal_arcpy_OLD.py", "line": 13} in \
        roots["D:\\GIS\\LUX_exposure"]["evidence"]


# ---------------------------------------------------------------- AT-10 raster profiles
def test_at10_raster_profiles(lux):
    inv, mig = lux["inv"], lux["mig"]
    g = inv["grids"]
    assert len(g) == 1 and g[0]["reference"] == "ds.lux_ppp_2020" and len(g[0]["members"]) == 6
    assert all(m["offset_px"] == [0.0, 0.0] for m in g[0]["members"]) and not g[0]["misaligned"]
    p = json.loads((mig / "discover/profiles/ds.01_pop_lux.json").read_text())
    assert p["computed"]["valid"] == 456764 and abs(p["computed"]["sum"] - 588219.87) <= 0.01
    assert p["embedded_stats"]["approximate"] is True
    assert abs(p["res_m_at_centre"][0] - 59.9) <= 0.5 and abs(p["res_m_at_centre"][1] - 92.7) <= 0.5
    m = json.loads((mig / "discover/profiles/ds.02_res_mask.json").read_text())
    assert (m["dtype"], m["nodata"], m["unique"]) == ("uint8", 0.0, [1])
    oracle = provenance_inventory()
    for d in inv["datasets"]:
        if d["kind"] != "raster" or d["role"] == "input" or not d["exists"]:
            continue
        row = oracle[d["legacy_path"]]
        assert d["sha256"] == row["sha256"]
        pr = json.loads((mig / d["profile_ref"]).read_text())
        assert f"{pr['dtype']}, nodata={pr['nodata']!r}" in row["grid"]
        assert f"transform={tuple(pr['transform'])}" in row["grid"]


# ---------------------------------------------------------------- AT-11 vector profiles, content hashes
def test_at11_vector_profiles(lux):
    mig = lux["mig"]
    osm = json.loads((mig / "discover/profiles/ds.osm_landuse_lux.json").read_text())
    assert osm["count"] == 3879 and osm["invalid"]["count"] == 13 and len(osm["domains"]["landuse"]) == 4
    r = json.loads((mig / "discover/profiles/ds.02_res_2169.json").read_text())
    assert r["count"] == 1512 and r["crs"]["epsg"] == 2169
    a2 = json.loads((mig / "discover/profiles/ds.geoboundaries_lux_adm2.json").read_text())
    assert a2["count"] == 105 and a2["geom_types"] == {"MultiPolygon": 2, "Polygon": 103}
    files = {f["path"]: f for f in lux["inv"]["files"]}
    assert all(f["content_sha256"] for p, f in files.items() if p.endswith(".gpkg"))


def test_at11_content_hash_survives_a_rewrite_timestamp(tmp_path, lux):
    """A second legacy run changes gpkg_contents.last_change only (PROVENANCE Check 1): sha256 moves, content does not."""
    leg = copy_fixture(tmp_path)
    p = leg / "05_TEMP" / "03_canton_pop.gpkg"
    con = sqlite3.connect(p)
    con.execute("UPDATE gpkg_contents SET last_change = '2030-01-01T00:00:00.000Z'")
    con.commit()
    con.close()
    inv = json.loads(run(SCAN, leg, "--out", tmp_path / "m", check=True) and (tmp_path / "m/inventory.json").read_text())
    a = next(f for f in lux["inv"]["files"] if f["path"] == "05_TEMP/03_canton_pop.gpkg")
    b = next(f for f in inv["files"] if f["path"] == "05_TEMP/03_canton_pop.gpkg")
    assert a["sha256"] != b["sha256"] and a["content_sha256"] == b["content_sha256"]


# ---------------------------------------------------------------- AT-12 Excel
def test_at12_excel(lux):
    inv, mig = lux["inv"], lux["mig"]
    s = json.loads((mig / "discover/profiles/ds.value_per_capita.json").read_text())["sheets"][0]
    assert (s["name"], s["header_row"], s["header_state"], s["data_rows"]) == ("vpc", 4, "resolved", [5, 16])
    assert (s["header_corroboration"]["file"], s["header_corroboration"]["line"]) == \
        ("02_SCRIPTS/04_distribute_value.py", 39)
    assert s["units_cells"][0]["value_cell"] == "B2" and s["strip_issues"] == [{"cell": "A16", "value": "Wiltz "}]
    assert s["formula_no_cache"] == ["B18"]
    assert any(i["kind"] == "formula_no_cache" for i in inv["open_items"])
    lit = next(p for p in inv["params"] if p["id"] == "p.s04.L42.mult")
    assert lit["value"] == 1000.0
    jc = {(j["vector"], j["vector_column"]): j for j in inv["join_coverage"] if j["column"].startswith("A ")}
    c = jc[("ds.cantons_lux_edit", "canton")]
    assert c["matched_after_strip"] == 12 and c["table_values"] == 12 and c["matched_exact"] == 11
    assert jc[("ds.cantons_lux_edit", "shapeName")]["matched_after_strip"] == 0


# ---------------------------------------------------------------- AT-13 docs
def test_at13_docs(lux):
    inv = lux["inv"]
    P = {p["id"]: p for p in inv["params"]}
    assert P["p.s02.MIN_AREA"]["doc_mentions"] == [{"agrees": True, "file": "NOTES.txt", "line": 22, "unit": "m2",
                                                    "value": 2500}]
    assert P["p.s03.POP_MIN"]["doc_mentions"][0]["line"] == 25 and P["p.s03.POP_MIN"]["doc_mentions"][0]["agrees"]
    assert P["p.s04.L42.mult"]["doc_mentions"][0]["line"] == 28
    assert P["p.s02.MIN_AREA"]["used_at"][0]["op"] == "s02_landuse_mask.op4"
    c = [i for i in inv["open_items"] if i["kind"] == "doc_code_conflict"]
    assert len(c) == 1 and c[0]["severity"] == "info" and c[0]["evidence"][0]["line"] == 4
    assert c[0]["about"] == ["ds.lux_ppp_2020"]


# ---------------------------------------------------------------- AT-14 qgz
def test_at14_qgz(lux):
    p = lux["inv"]["projects"][0]
    assert p["path"] == "01_QGIS/lux_exposure.qgz" and p["crs"]["authid"] == "EPSG:2169"
    assert len(p["layers"]) == 7 and p["groups"] == ["input", "results", "working"]
    osm = next(l for l in p["layers"] if l["name"] == "osm_landuse")
    assert osm["path"] == "03_INPUT/osm_landuse_lux.gpkg" and osm["layer"] == "landuse"
    assert osm["corroborates"] == [{"file": "02_SCRIPTS/02_landuse_mask.py", "line": 47, "op": "s02_landuse_mask.op4"}]
    assert all(l.get("path") and l.get("exists") for l in p["layers"])


# ---------------------------------------------------------------- AT-15 map
def test_at15_map(lux):
    inv = json.loads((lux["mig"] / "inventory.json").read_text())
    pngs = {p["path"] for p in inv["map"]["pngs"]}
    assert pngs == {"discover/inputs_overview.png", "discover/grid_corners.png", "discover/legacy_rasters.png"}
    rel = inv["extent_relations"]
    out = next(r for r in rel if r["relation"] == "a_outside_b_extent")
    assert (out["a"], out["b"]) == ("ds.cantons_lux_edit", "ds.lux_ppp_2020")
    assert abs(out["area_outside_km2"] - 0.94) <= 0.05
    osm = next(r for r in rel if r["a"] == "ds.osm_landuse_lux")
    assert osm["features_outside_by_value"]["landuse"]["residential"] == 4


def test_at15_findings_with_metrics(lux, tmp_path):
    """The agent's three map findings go through annotate; the 46 % metric comes from the legacy 03_canton_pop sums."""
    import pyogrio
    mig = lux["mig"]
    leg = lux["legacy"]
    t = pyogrio.read_dataframe(leg / "05_TEMP/03_canton_pop.gpkg")
    share = float(t["res_sum"].sum() / t["pop_sum"].sum())
    assert abs(share - 0.46) < 0.01
    f = tmp_path / "f.json"
    for fid, obs, metric in (("MF-01", "cantons reach past the WorldPop extent on the west edge",
                              {"name": "area_outside_km2", "value": 0.94, "computed_by": "extent_relations"}),
                             ("MF-02", "residential polygons outside the cantons",
                              {"name": "features_outside", "value": 4, "computed_by": "extent_relations"}),
                             ("MF-03", "residential cells hold under half the population",
                              {"name": "res_share", "value": round(share, 3), "computed_by": "03_canton_pop sums"})):
        f.write_text(json.dumps({"id": fid, "png": "discover/inputs_overview.png", "observation": obs,
                                 "severity": "info", "metric": metric}))
        run(SCAN, "annotate", mig, "map-finding", "--json", f)
    inv = json.loads((mig / "inventory.json").read_text())
    assert [x["id"] for x in inv["map"]["findings"]] == ["MF-01", "MF-02", "MF-03"]


# ---------------------------------------------------------------- AT-16 determinism
def test_at16_determinism(lux, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    run(SCAN, lux["legacy"], "--out", a, env={"PYTHONHASHSEED": "0"})
    run(SCAN, lux["legacy"], "--out", b, env={"PYTHONHASHSEED": "1"})
    ja, jb = (json.loads((x / "inventory.json").read_text()) for x in (a, b))
    for j in (ja, jb):
        j["scan"].pop("started_at")
    assert json.dumps(ja, sort_keys=True) == json.dumps(jb, sort_keys=True)


# ---------------------------------------------------------------- AT-17 negative controls
def test_at17_missing_input_blocks(tmp_path):
    leg = copy_fixture(tmp_path)
    (leg / "03_INPUT/lux_ppp_2020.tif").unlink()
    run(SCAN, leg, "--out", tmp_path / "m")
    inv = json.loads((tmp_path / "m/inventory.json").read_text())
    mi = [i for i in inv["open_items"] if i["kind"] == "missing_input"]
    assert len(mi) == 1 and mi[0]["blocking"] and mi[0]["about"] == ["ds.lux_ppp_2020"]
    assert "s01_prepare_population.op2" in mi[0]["consumers"]
    assert any(c.startswith("s03b_zonal_arcpy_old.") for c in mi[0]["consumers"])


def test_at17_secret_excluded_and_orphan(tmp_path):
    leg = copy_fixture(tmp_path)
    (leg / ".env").write_text("PASSWORD=x\n")
    shutil.copy(leg / "04_FINAL/lux_res_exposure_2020.tif", leg / "04_FINAL/extra.tif")
    run(SCAN, leg, "--out", tmp_path / "m")
    inv = json.loads((tmp_path / "m/inventory.json").read_text())
    env = next(f for f in inv["files"] if f["path"] == ".env")
    assert env["kind"] == "excluded" and "sha256" not in env
    assert any(i["kind"] == "orphan_output" and i["about"] == ["ds.extra"] for i in inv["open_items"])
