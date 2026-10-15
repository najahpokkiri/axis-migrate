"""Foreign working directories (M4), imported helpers (M5) and grouped questions (M3). Synthetic data only."""
from __future__ import annotations

import json

import pytest

from model_synth import build_param_model
from scan_synth import LINEAGE, run, scan, write_raster

FOREIGN = '''import os
import geopandas as gpd
path = "/home/someone_else/area_study/"
os.chdir(path)
pts = gpd.read_file("inputs/poi.shp")
pts["Hectares"] = (30 ** 2) / 10000
pts["Group"] = "x"
pts["n"] = pts["n"].astype(int)
pts.to_file("temp/out.shp")
'''
SNIPPET = '''import geopandas as gpd
g = gpd.read_file("old_area.gdb")
g.to_file("temp/area.geojson")
'''
HELPER_LIB = '''import geopandas as gpd


def copy_layer(src, dst):
    g = gpd.read_file(src)
    g.to_file(dst)
'''
CALLER = '''from layerlib import *
copy_layer("../data/poi_points.shp", "../work/poi_copy.gpkg")
'''


@pytest.fixture(scope="module")
def fam(tmp_path_factory):
    import numpy as np
    root = tmp_path_factory.mktemp("fam")
    leg = build_param_model(root)
    (leg / "scripts" / "impact_script.py").write_text(FOREIGN)
    (leg / "scripts" / "snippets.py").write_text(SNIPPET)
    (leg / "scripts" / "layerlib.py").write_text(HELPER_LIB)
    (leg / "scripts" / "export_poi.py").write_text(CALLER)
    (leg / "work").mkdir()
    write_raster(leg / "data" / "spare.tif", np.ones((20, 30)))
    mig = root / "migration"
    inv = scan(leg, mig)
    r = run(LINEAGE, mig, "--json")
    return {"leg": leg, "inv": inv, "lineage": json.loads(r.stdout), "graph": json.loads((mig / "graph.json").read_text())}


def code(inv, path):
    return next(c for c in inv["code"] if c["path"] == path)


def test_chdir_to_another_machine_is_a_foreign_host_path(fam):
    inv = fam["inv"]
    c = code(inv, "scripts/impact_script.py")
    fl = [f for f in c["flags"] if f["kind"] == "foreign_host_path"]
    assert fl[0]["value"] == "/home/someone_else/area_study" and fl[0]["via"] == "os.chdir" and fl[0]["line"] == 4
    assert not [d for d in inv["datasets"] if "poi.shp" in d["legacy_path"] or "out.shp" in d["legacy_path"]]
    un = [u for u in inv["unresolved"] if u["file"] == "scripts/impact_script.py"]
    assert {u["reason"] for u in un} == {"outside_root"}


def test_imported_helper_runs_at_the_call_site(fam):
    inv = fam["inv"]
    assert code(inv, "scripts/layerlib.py")["status"] == "helper"
    ops = [o for o in inv["ops"] if o["file"] == "scripts/export_poi.py"]
    assert ops and all(o["line"] == 2 for o in ops)
    assert all("via_helper:scripts/layerlib.py:4:copy_layer" in o["flags"] for o in ops)
    assert {r["dataset"] for o in ops for r in o["reads"]} == {"ds.poi_points"}
    assert {w["dataset"] for o in ops for w in o["writes"]} == {"ds.poi_copy"}


def test_one_parity_reference_question_absorbs_the_alternatives(fam):
    inv, g = fam["inv"], fam["graph"]
    grp = next(x for x in inv["implementations"])
    srcs = {m["source"] for m in grp["members"]}
    assert {"models/impact_model.model3", "scripts/impact_script.py"} <= srcs
    assert "scripts/snippets.py" not in srcs and "scripts/export_poi.py" not in srcs
    model = code(inv, "models/impact_model.model3")
    assert grp["reference_guess"] == model["id"]
    st = {s["legacy"]["source"]: s for s in g["steps"]}
    py = st["scripts/impact_script.py"]
    assert py["scope"] == "drop" and py["scope_reason"] == "alternative_impl" and py["twin_of"] == model["step_id"]
    q = [q for q in g["questions"] if q["header"] == "Parity reference"]
    assert len(q) == 1 and q[0]["agent_guess"]["option"] == model["step_id"]
    assert "/home/someone_else/area_study" in q[0]["text"]
    assert not [q for q in g["questions"] if q["header"] in ("Unresolved path", "Missing input", "Unused file")]


def test_scripts_touching_no_existing_file_are_one_grouped_question(fam):
    g = fam["graph"]
    st = {s["legacy"]["source"]: s for s in g["steps"]}
    assert st["scripts/snippets.py"]["scope"] == "drop" and st["scripts/snippets.py"]["scope_reason"] == "not_run_here"
    assert st["scripts/export_poi.py"]["scope"] == "migrate"  # its input exists
    q = [q for q in g["questions"] if q["header"] == "Not run with this folder"]
    assert len(q) == 1 and "scripts/snippets.py" in q[0]["text"]


def test_unused_files_are_one_scope_fact(fam):
    facts = [f for f in fam["graph"]["scope_facts"] if f["fact"] == "unused_files"]
    assert len(facts) == 1 and "ds.spare" in facts[0]["nodes"]


def test_numeric_operands_dtype_casts_and_nominal_cell(fam):
    inv = fam["inv"]
    ps = {(p["file"], p["line"], p["context"]): p for p in inv["params"] if p["kind"] in ("inline_literal", "dtype")}
    f = "scripts/impact_script.py"
    assert ps[(f, 6, "pow")]["value"] == 30 and ps[(f, 6, "div")]["value"] == 10000
    assert ps[(f, 6, "div")]["cell_size_area"] == {"cell_m": 30, "unit": "ha", "area": 0.09}
    d = ps[(f, 8, "dtype")]
    assert d["kind"] == "dtype" and d["value"] == "int" and d["via"] == "astype"
    lit = next(p for p in inv["params"] if p["kind"] == "tool_literal" and p["name"] == "DATA_TYPE")
    assert lit["value"] == "5" and lit["label"] == "Int32"
    st = next(s for s in inv["settings"] if s.get("via") == "DATA_TYPE")
    assert st["label"] == "Int32"
    model_formula = next(p for p in inv["params"] if p["kind"] == "tool_literal" and p["value"] == "(30*30)/10000")
    assert model_formula["cell_size_area"]["cell_m"] == 30
    oi = [i for i in inv["open_items"] if i["kind"] == "nominal_vs_real_cell"]
    model = next(i for i in oi if i["about"][0] == code(inv, "models/impact_model.model3")["id"])
    assert model["nominal_m"] == 30 and model["rasters_from"] == "same_code"
    r = next(e for e in model["evidence"] if e["kind"] == "raster_profile")
    assert r["dataset"] == "ds.elevation_dem" and r["res_m"][0] > 600  # 0.01 degree cells, far from 30 m


def test_constant_group_by_has_a_semantics_note(fam):
    p = next(p for p in fam["inv"]["params"] if p["name"] == "GROUP_BY" and p["kind"] == "tool_literal")
    assert p["value"] == "Null" and "one group" in p["semantics"]


def test_epoch_mtimes_and_absolute_path_columns(tmp_path):
    import os

    import pyogrio
    from scan_synth import zones_gdf
    leg = tmp_path / "legacy_epoch"
    (leg / "out").mkdir(parents=True)
    g = zones_gdf()
    g["path"] = ["/srv/producer/run1/out/a.gpkg", "/srv/producer/run1/out/b.gpkg", "C:\\\\work\\\\c.gpkg"]
    pyogrio.write_dataframe(g, leg / "out" / "merged.gpkg", layer="merged", driver="GPKG")
    pyogrio.write_dataframe(zones_gdf(), leg / "out" / "other.gpkg", layer="other", driver="GPKG")
    for f in (leg / "out").iterdir():
        os.utime(f, (315532800, 315532800))
    inv = scan(leg, tmp_path / "migration")
    assert inv["scan"]["mtime_reliable"] is False and "epoch" in inv["scan"]["mtime_reason"]
    d = next(x for x in inv["datasets"] if x["id"] == "ds.merged")
    assert d["compare_hints"]["ignore_proposed"] == [{"column": "path", "why": "absolute paths of the producing machine"}]
    assert [i["column"] for i in inv["open_items"] if i["kind"] == "absolute_path_column"] == ["path"]
