"""QGIS model inputs, outputs, expression values and XML lines (B3, B5, M2). Synthetic data only."""
from __future__ import annotations

import json

import pytest

from model_synth import build_param_model
from scan_synth import LINEAGE, run, scan


@pytest.fixture(scope="module")
def pm(tmp_path_factory):
    root = tmp_path_factory.mktemp("pm")
    leg = build_param_model(root)
    mig = root / "migration"
    inv = scan(leg, mig)
    r = run(LINEAGE, mig, "--json")
    return {"leg": leg, "mig": mig, "inv": inv, "lineage": json.loads(r.stdout),
            "graph": json.loads((mig / "graph.json").read_text())}


def _ds(inv, path):
    return next(d for d in inv["datasets"] if d["legacy_path"] == path)


def test_outputs_bind_through_the_model_named_folder(pm):
    inv = pm["inv"]
    for path in ("results/impact_model/Area_Summary.gpkg", "results/impact_model/Poi_Buffer.gpkg"):
        d = _ds(inv, path)
        assert d["role"] == "final" and d["reference_candidate"], d
        w = next(x for x in d["role_evidence"] if x["kind"] == "code_write")
        assert w["binding"] == "model_output_folder" and w["file"] == "models/impact_model.model3"


def test_inputs_are_guessed_by_type_and_name_and_confirmed_once(pm):
    inv = pm["inv"]
    oi = next(i for i in inv["open_items"] if i["kind"] == "model_input_binding")
    assert oi["state"] == "agent_guess" and not oi["unbound"]
    assert oi["agent_guess"] == {"Elevation": _ds(inv, "data/elevation_dem.tif")["id"],
                                 "StudyArea": _ds(inv, "data/boundary/outline.shp")["id"],
                                 "PointsOfInterest": _ds(inv, "data/poi_points.shp")["id"]}
    for path in ("data/elevation_dem.tif", "data/boundary/outline.shp", "data/poi_points.shp"):
        d = _ds(inv, path)
        assert d["role"] == "input" and d["role_state"] == "ai_inferred"
    g = pm["graph"]
    step = next(s for s in g["steps"] if s["legacy"]["source"] == "models/impact_model.model3")
    assert set(step["inputs"]) == {_ds(inv, p)["id"] for p in ("data/elevation_dem.tif", "data/boundary/outline.shp",
                                                                "data/poi_points.shp")}
    assert set(step["outputs"]) == {_ds(inv, p)["id"] for p in ("results/impact_model/Area_Summary.gpkg",
                                                                 "results/impact_model/Poi_Buffer.gpkg")}
    e = next(e for e in g["edges"] if e["from"] == _ds(inv, "data/elevation_dem.tif")["id"])
    assert e["state"] == "ai_inferred"
    q = [q for q in g["questions"] if q["header"] == "Model inputs"]
    assert len(q) == 1 and q[0]["agent_guess"]["option"] == "confirm"
    assert pm["lineage"]["pairs"] == 2


def test_formula_is_never_a_dataset_and_its_numbers_are_params(pm):
    inv = pm["inv"]
    assert not [d for d in inv["datasets"] if "30" in d["legacy_path"] or "10000" in d["legacy_path"]]
    src = (pm["leg"] / "models" / "impact_model.model3").read_text().splitlines()
    nums = [p for p in inv["params"] if p["kind"] == "expression_literal" and p["expression"] == "(30*30)/10000"]
    assert [p["value"] for p in nums] == [30, 30, 10000]
    assert "(30*30)/10000" in src[nums[0]["line"] - 1]
    lit = next(p for p in inv["params"] if p["kind"] == "tool_literal" and p["value"] == "(30*30)/10000")
    assert lit["line"] == nums[0]["line"]


def test_model_ops_cite_xml_lines(pm):
    inv = pm["inv"]
    src = (pm["leg"] / "models" / "impact_model.model3").read_text().splitlines()
    for o in [o for o in inv["ops"] if o["family"] == "qgis_model"]:
        child = next(f for f in o["flags"] if f.startswith("model_child:")).split(":", 1)[1]
        assert f'name="{child}"' in src[o["line"] - 1], (o["line"], child)
        assert o["end_line"] >= o["line"]
