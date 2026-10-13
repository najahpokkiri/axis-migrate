"""02 T-lin-1..T-lin-4 on lux, adapted to DESIGN ids (ds.01_pop_lux, m01_cantons_lux_edit, s03b drop) and to R15
(lineage raises questions only for unmatched items: on the clean fixture exactly one, the manual step)."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_lux_util import GRAPH, LINEAGE, SCAN, copy_fixture, run  # noqa: E402

STEPS = {"m01_cantons_lux_edit": ("manual", "keep_legacy"), "s01_prepare_population": ("script", "migrate"),
         "s02_landuse_mask": ("script", "migrate"), "s03_zonal_population": ("script", "migrate"),
         "s03b_zonal_arcpy_old": ("script", "drop"), "s04_distribute_value": ("script", "migrate")}
PAIRS = sorted(["ds.01_lux_outline", "ds.01_pop_lux", "ds.02_res_2169", "ds.02_res_4326", "ds.02_res_mask",
                "ds.03_pop_res", "ds.03_canton_pop", "ds.04_canton_factor", "ds.04_factor", "ds.lux_res_exposure_2020",
                "ds.exposure_by_canton_gpkg", "ds.exposure_by_canton_csv", "ds.exposure_by_canton_xlsx"])


def graph_or_draft(lux):
    return lux["graph"] or lux["draft"]


# ---------------------------------------------------------------- T-lin-1 static graph
def test_tlin1_steps_datasets_edges(lux):
    g = graph_or_draft(lux)
    st = {s["id"]: s for s in g["steps"]}
    assert {k: (v["kind"], v["scope"]) for k, v in st.items()} == STEPS
    assert st["s03b_zonal_arcpy_old"]["scope_reason"] == "alternative_impl"
    assert st["s01_prepare_population"]["tool"] == "pyqgis" and st["s03b_zonal_arcpy_old"]["tool"] == "arcpy"
    assert st["s01_prepare_population"]["legacy"]["lines"] == [[30, 54]]
    assert st["s03b_zonal_arcpy_old"]["legacy"]["executed"] == "no"
    d = {x["id"]: x for x in g["datasets"]}
    assert not any(k.startswith("ds.mem") for k in d)
    assert d["ds.cantons_lux_edit"]["legacy"]["path"] == "03_INPUT/cantons_lux_edit.gpkg"
    assert d["ds.cantons_lux_edit"]["legacy"]["layer"] == "cantons" and d["ds.cantons_lux_edit"]["role"] == "input"
    assert d["ds.04_canton_factor"]["legacy"]["layer"] == "canton_factor"
    assert d["ds.geoboundaries_lux_adm2"]["role"] == "unused"
    assert d["ds.01_pop_lux"]["new"]["path"] == "s01_prepare_population/01_pop_lux.tif"
    assert d["ds.03_canton_pop"]["new"] == {"path": "s03_zonal_population/03_canton_pop.gpkg", "layer": "03_canton_pop",
                                            "auto": True}
    E = {e["id"]: e for e in g["edges"]}
    e = E["e.s01_prepare_population>ds.01_pop_lux"]
    assert e["kind"] == "writes" and e["evidence"][0]["ref"] == "02_SCRIPTS/01_prepare_population.py:40-54"
    assert E["e.ds.01_pop_lux>s02_landuse_mask#reads_grid"]["evidence"]
    assert E["e.s04_distribute_value>ds.04_canton_factor#reads_write"]["lines"] == [[47, 72]]
    assert E["e.ds.lux_ppp_2020>s03b_zonal_arcpy_old#reads_grid"]["lines"] == [[23, 23], [24, 24], [25, 25], [26, 26]]
    assert E["e.ds.geoboundaries_lux_adm1>m01_cantons_lux_edit"]["evidence"][0]["ref"] == "NOTES.txt:15-19"
    for e in g["edges"]:
        assert e["evidence"] and all(x.get("ref") for x in e["evidence"]), e["id"]
    eq = sorted((e["from"], e["to"]) for e in g["edges"] if e["kind"] == "equivalent_to")
    assert eq == [("ds.work__canton_pop", "ds.03_canton_pop"), ("ds.work__cantons_edit", "ds.cantons_lux_edit"),
                  ("ds.work__pop_lux", "ds.01_pop_lux"), ("ds.work__pop_res", "ds.03_pop_res"),
                  ("ds.work__res_mask", "ds.02_res_mask")]
    assert all(e["state"] == "documented" for e in g["edges"] if e["kind"] == "equivalent_to")
    disp = [e for e in g["edges"] if e["kind"] == "displays"]
    assert len(disp) == 7 and any(e.get("subset") for e in disp)


def test_tlin1_graph_initialised_by_graph_py(lux):
    if not GRAPH.exists():
        pytest.skip("graph.py absent")
    g = lux["graph"]
    assert g is not None and g["revision"] == 1 and g["legacy_output_dirs"] == ["04_FINAL", "05_TEMP"]
    assert g["project"]["legacy_root"] == "../lux-exposure-legacy"
    assert run(GRAPH, "validate", lux["mig"]).returncode == 0


# ---------------------------------------------------------------- T-lin-2 one op per call
def test_tlin2_op_counts_no_duplicates(lux):
    g = graph_or_draft(lux)
    counts = {s["id"]: len(s["ops"]) for s in g["steps"] if s["kind"] != "manual"}
    # 02 T-lin-2 says s03b=10; we count one op per I/O call site: Describe(26), ExtractByMask(28), save(29),
    # Con(32, IsNull+Raster folded), Con(33), save(34), ZonalStatisticsAsTable(36, 37), CopyFeatures(41),
    # AddField(42, 43), SearchCursor(46, 50), UpdateCursor(54) = 14 (01 AT-9 lists the same calls).
    assert counts == {"s01_prepare_population": 2, "s02_landuse_mask": 7, "s03_zonal_population": 5,
                      "s03b_zonal_arcpy_old": 14, "s04_distribute_value": 10}
    keys = [(o["ref"].split(":")[0], o["lines"][0], o["call"]) for s in g["steps"] for o in s["ops"]]
    assert len(keys) == len(set(keys))


# ---------------------------------------------------------------- T-lin-3 checks pass on the clean fixture
def test_tlin3_checks_and_questions(lux):
    g = graph_or_draft(lux)
    ch = g["lineage_checks"]
    assert not ch["failed"] and not ch["warnings"]
    writes = {c["dataset"] for c in ch["passed"] if c["check"] == "a_write_exists"}
    assert sorted(writes) == PAIRS
    grid = {(c["dataset"], c["template"]) for c in ch["passed"] if c["check"] == "d_grid_equals_template"}
    assert grid == {("ds.02_res_mask", "ds.01_pop_lux"), ("ds.04_factor", "ds.03_pop_res")}
    fields = {(c["dataset"], c["field"]) for c in ch["passed"] if c["check"] == "c_field_in_schema"}
    assert {("ds.04_canton_factor", "canton"), ("ds.04_canton_factor", "pop_sum"),
            ("ds.04_canton_factor", "res_sum")} <= fields
    layers = {(c["dataset"], c["layer"]) for c in ch["passed"] if c["check"] == "b_layer_exists"}
    assert ("ds.03_canton_pop", "03_canton_pop") in layers
    qs = g["questions"]
    assert [(q["id"], q["kind"], q["node"], q["impact"]["level"]) for q in qs] == \
        [("q01", "manual_step", "m01_cantons_lux_edit", "high")]
    facts = {(f["node"], f["fact"]) for f in g["scope_facts"]}
    assert facts == {("s03b_zonal_arcpy_old", "superseded"), ("ds.geoboundaries_lux_adm2", "display_only"),
                     ("ds.osm_landuse_lux", "unused_branch")}
    assert lux["lineage_summary"]["pairs"] == 13
    sidecars = {s for d in g["datasets"] for s in (d.get("legacy") or {}).get("sidecars", [])}
    assert len(sidecars) == 4


def test_tlin3_pairs_are_13_by_lineage(lux):
    g = graph_or_draft(lux)
    st = {s["id"]: s for s in g["steps"]}
    prod = {e["to"]: e["from"] for e in g["edges"] if e["kind"] in ("writes", "reads_write")}
    pairs = sorted(d["id"] for d in g["datasets"] if d["role"] != "input" and (d.get("legacy") or {}).get("exists")
                   and st.get(prod.get(d["id"]), {}).get("scope") == "migrate")
    assert pairs == PAIRS


# ---------------------------------------------------------------- T-lin-4 mutations (each on its own copy)
def _mutated(tmp_path, mutate):
    leg = copy_fixture(tmp_path)
    mutate(leg)
    mig = tmp_path / "migration"
    run(SCAN, leg, "--out", mig)
    run(LINEAGE, mig, "--draft-only")
    return json.loads((mig / ".graph" / "draft.json").read_text())


def test_tlin4_missing_write(tmp_path):
    g = _mutated(tmp_path, lambda leg: [(leg / "05_TEMP/02_res_mask.tif").unlink(),
                                        (leg / "05_TEMP/02_res_mask.tif.aux.xml").unlink()])
    q = [q for q in g["questions"] if q["header"] == "Missing output"]
    assert len(q) == 1 and q[0]["node"] == "s02_landuse_mask" and "ds.02_res_mask" in q[0]["nodes"]
    assert q[0]["trigger"]["ref"] == "02_SCRIPTS/02_landuse_mask.py:76"


def test_tlin4_grid_shift_one_pixel(tmp_path):
    def shift(leg):
        import rasterio
        p = leg / "05_TEMP/02_res_mask.tif"
        with rasterio.open(p) as src:
            prof, a = src.profile, src.read()
        t = prof["transform"]
        prof["transform"] = rasterio.Affine(t.a, t.b, t.c + t.a, t.d, t.e, t.f)
        p.unlink()
        with rasterio.open(p, "w", **prof) as dst:
            dst.write(a)
    g = _mutated(tmp_path, shift)
    q = [q for q in g["questions"] if q["header"] == "Grid differs from template"]
    assert len(q) == 1 and q[0]["node"] == "ds.02_res_mask" and q[0]["impact"]["level"] == "high"
    assert q[0]["trigger"]["ref"] == "02_SCRIPTS/02_landuse_mask.py:61-76"
    f = next(c for c in g["lineage_checks"]["failed"] if c["check"] == "d_grid_equals_template")
    assert f["offset_px"] == [1.0, 0.0]


def test_tlin4_orphan_never_paired(tmp_path):
    g = _mutated(tmp_path, lambda leg: shutil.copy(leg / "05_TEMP/02_res_mask.tif", leg / "05_TEMP/old_mask_v2.tif"))
    q = [q for q in g["questions"] if q["header"] == "Unreferenced legacy files"]
    assert len(q) == 1 and q[0]["node"] == "ds.old_mask_v2"
    d = next(x for x in g["datasets"] if x["id"] == "ds.old_mask_v2")
    assert d["reference"]["source"] == "none" and d.get("new") is None


def test_tlin4_layer_renamed(tmp_path):
    def rename(leg):
        import sqlite3
        p = leg / "05_TEMP/03_canton_pop.gpkg"
        con = sqlite3.connect(p)
        con.execute("ALTER TABLE \"03_canton_pop\" RENAME TO \"canton_pop_x\"")
        for t, c in (("gpkg_contents", "table_name"), ("gpkg_geometry_columns", "table_name")):
            con.execute(f"UPDATE {t} SET {c}='canton_pop_x' WHERE {c}='03_canton_pop'")
        con.execute("DELETE FROM gpkg_extensions WHERE table_name='03_canton_pop'")
        con.commit()
        con.close()
    g = _mutated(tmp_path, rename)
    q = [q for q in g["questions"] if q["header"] == "Layer not found"]
    assert len(q) == 1 and q[0]["node"] == "ds.03_canton_pop"
    assert q[0]["trigger"]["ref"].startswith("02_SCRIPTS/04_distribute_value.py:")


def test_tlin4_touch_is_mtime_warning_only(tmp_path):
    def touch(leg):
        t = time.time()
        os.utime(leg / "05_TEMP/01_pop_lux.tif", (t, t))
    g = _mutated(tmp_path, touch)
    w = g["lineage_checks"]["warnings"]
    assert w and all(x["check"] == "f_mtime_order" and x["producer_output"] == "ds.01_pop_lux" for x in w)
    assert [q["kind"] for q in g["questions"]] == ["manual_step"]
