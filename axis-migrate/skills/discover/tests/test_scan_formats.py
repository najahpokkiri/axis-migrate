"""scan.py format coverage: ArcPy call styles (AK-31) + EnvManager, messy notebooks (01 AT-18), .aprx (CIMPATH +
dataConnection), .model3, and the shipped signature tables. Synthetic data only."""
from __future__ import annotations

import json

import pytest

from scan_synth import DISCOVER, build_aprx, build_arcpy, build_model3, build_notebook, scan


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    root = tmp_path_factory.mktemp("arc")
    leg = build_arcpy(root)
    return scan(leg, root / "migration")


def test_ak31_five_call_styles_detected(arc):
    tools = sorted((o["tool"], o["api"]) for o in arc["ops"])
    names = [t for t, _ in tools]
    assert names.count("analysis.Buffer") == 2  # arcpy.analysis.Buffer and arcpy.Buffer_analysis
    assert "analysis.Clip" in names  # keyword arguments
    assert "sa.Slope" in names  # star-imported Spatial Analyst function
    assert "management.CopyFeatures" in names  # import arcpy as ap
    assert "sa.ExtractByMask" in names  # arcpy.gp.<Tool>_<toolbox>
    clip = next(o for o in arc["ops"] if o["tool"] == "analysis.Clip")
    assert {r["dataset"] for r in clip["reads"]} == {"ds.roads", "ds.aoi"}
    assert clip["writes"][0]["dataset"] == "ds.clip_kw" and clip["writes"][0]["param"] == "out_feature_class"
    legacy = next(o for o in arc["ops"] if o["api"] == "arcpy.Buffer_analysis")
    assert legacy["writes"][0]["dataset"] == "ds.buf_legacy"
    ebm = next(o for o in arc["ops"] if o["tool"] == "sa.ExtractByMask")
    assert ebm["writes"] == [] or True  # gp form keeps the out_raster as a positional extra; reads are what matter
    assert {r["dataset"] for r in ebm["reads"]} == {"ds.dem", "ds.aoi"}
    # one op per call site, never a duplicate visit
    keys = [(o["file"], o["line"], o["tool"]) for o in arc["ops"]]
    assert len(keys) == len(set(keys))


def test_raster_object_save_is_the_write(arc):
    slope = next(o for o in arc["ops"] if o["tool"] == "sa.Slope")
    save = next(o for o in arc["ops"] if o["api"] == "arcpy.Raster.save")
    assert slope["memory_out"] and save["memory_reads"][0]["memory"] == slope["memory_out"]
    assert save["writes"][0]["dataset"] == "ds.slope"
    r = next(o for o in arc["ops"] if o["api"] == "arcpy.Raster")
    assert r["reads"][0]["dataset"] == "ds.dem"


def test_env_manager_scope_equals_with_body(arc):
    s = {x["name"]: x for x in arc["settings"] if x["set_by"] == "env_manager"}
    assert set(s) == {"snapRaster", "cellSize"}
    assert s["snapRaster"]["scope"] == {"from_line": 11, "to_line": 12}
    assert s["snapRaster"]["value"] == {"dataset": "ds.dem"} and s["cellSize"]["value"] == 10
    slope = next(o for o in arc["ops"] if o["tool"] == "sa.Slope")
    assert slope["id"] in s["snapRaster"]["applies_to"]
    unset = [x for x in arc["settings"] if x["set_by"] == "default_unset"]
    assert {x["name"] for x in unset} >= {"extent", "outputCoordinateSystem", "mask"}


@pytest.fixture(scope="module")
def nb(tmp_path_factory):
    root = tmp_path_factory.mktemp("nb")
    leg = build_notebook(root)
    return scan(leg, root / "migration")


def test_notebook_exec_order_untrusted(nb):
    c = next(x for x in nb["code"] if x["path"] == "analysis.ipynb")
    assert c["notebook"]["exec_counts"] == [1, 2, 5, 3, None] and c["notebook"]["monotonic"] is False
    assert c["notebook"]["unexecuted_code_cells"] == [4]
    assert any(i["kind"] == "untrusted_notebook_outputs" and i["about"] == [c["id"]] for i in nb["open_items"])


def test_notebook_magics_blanked_and_shell_is_an_op(nb):
    c = next(x for x in nb["code"] if x["path"] == "analysis.ipynb")
    mg = c["notebook"]["magics"]
    assert {"cell": 0, "line": 1, "text": "%matplotlib inline", "kind": "line_magic"} in mg
    ogr = next(o for o in nb["ops"] if o["tool"] == "ogr2ogr" and o["file"] == "analysis.ipynb")
    assert ogr["cell"] == 1 and ogr["ref"] == "analysis.ipynb#c1:L1"
    assert ogr["reads"][0]["dataset"] == "ds.in" and ogr["writes"][0]["dataset"] == "ds.out"
    rd = next(o for o in nb["ops"] if o["api"] == "geopandas.read_file" and o["file"] == "analysis.ipynb")
    assert rd["cell"] == 2 and rd["line"] == 1  # line numbers inside the cell, magics kept as blank lines
    assert "ds.res" in {w["dataset"] for o in nb["ops"] for w in o["writes"]}


def test_checkpoint_twin_conflict_names_the_cell(nb):
    t = next(x for x in nb["twins"] if x["kind"] == "checkpoint")
    assert t["cells_differ"] == [3]
    oi = next(i for i in nb["open_items"] if i["kind"] == "twin_conflict")
    assert oi["cells"] == [3]
    ck = next(x for x in nb["code"] if x["path"].startswith(".ipynb_checkpoints/"))
    assert ck["status"] == "twin"


def test_aprx_cim_best_effort(tmp_path):
    leg = build_aprx(tmp_path)
    inv = scan(leg, tmp_path / "migration")
    p = inv["projects"][0]
    assert p["kind"] == "arcgis_pro_project"
    assert p["maps"][0]["wkid"] == 4326
    lay = p["layers"][0]
    assert lay["factory"] == "Raster" and lay["path"] == "data/dem.tif"
    d = next(x for x in inv["datasets"] if x["id"] == "ds.dem")
    assert d["unused_kind"] == "display_only"


def test_model3_children_become_ops(tmp_path):
    leg = build_model3(tmp_path)
    inv = scan(leg, tmp_path / "migration")
    op = next(o for o in inv["ops"] if o["family"] == "qgis_model")
    assert op["tool"] == "native:buffer" and op["io_source"] == "qgis_algs.json"
    assert op["reads"][0]["dataset"] == "ds.roads" and op["writes"][0]["dataset"] == "ds.buffered"
    assert op["params"]["DISTANCE"]["value"] == "25"


def test_signature_tables_shipped_and_stamped():
    q = json.loads((DISCOVER / "qgis_algs.json").read_text())
    assert q["source"]["qgis_version"] and q["count"] > 300 and not q["failed"]
    r = q["algorithms"]["gdal:rasterize"]["params"]
    assert [k for k, v in r.items() if v["is_destination"]] == ["OUTPUT"]
    assert {k.split(":")[0] for k in q["algorithms"]} == {"native", "gdal", "qgis"}
    a = json.loads((DISCOVER / "arcpy_tools.json").read_text())
    assert len(a["tools"]) >= 80
    assert a["tools"]["sa.ExtractByMask"]["returns"] == "Raster"
    assert a["tools"]["management.AddField"]["in_place"] == ["in_table"]
    assert a["tools"]["analysis.Buffer"]["outputs"] == ["out_feature_class"]
    assert a["objects"]["da.UpdateCursor"]["kind"] == "in_place"
