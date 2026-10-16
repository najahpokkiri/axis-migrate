"""BYU flood impact legacy folder (public fixture): scan -> map inputs -> lineage -> graph show on a COPY.

Skips when the fixture is absent: AXIS_BYU_FIXTURE, default ~/public-fixtures/byu-flood-impact-legacy. The fixture
is only read (copied); assertions are about what discover must find in the files, not about expected answers."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from scan_synth import GRAPH, LINEAGE, MAP, run, scan, tree_state

FIXTURE = Path(os.environ.get("AXIS_BYU_FIXTURE", Path.home() / "public-fixtures" / "byu-flood-impact-legacy"))
pytestmark = pytest.mark.skipif(not FIXTURE.is_dir(), reason=f"BYU fixture not found at {FIXTURE}")

MODEL = "qgis/flood_impact_qgisModel.model3"


def ink(png: Path) -> float:
    import matplotlib.image as mpimg
    a = mpimg.imread(png)[..., :3]
    return float((a.min(axis=2) < 0.85).mean())


@pytest.fixture(scope="module")
def byu(tmp_path_factory):
    root = tmp_path_factory.mktemp("byu")
    before_fixture = tree_state(FIXTURE)
    leg = root / "byu-flood-impact-legacy"
    shutil.copytree(FIXTURE, leg, symlinks=True)
    copy_state = tree_state(leg)
    mig = root / "migration"
    t = {}
    t0 = time.monotonic()
    inv = scan(leg, mig)
    t["scan"] = time.monotonic() - t0
    t0 = time.monotonic()
    m = run(MAP, "inputs", mig, "--json")
    t["map"] = time.monotonic() - t0
    lin = run(LINEAGE, mig, "--json", check=False)
    show = run(GRAPH, "show", mig, check=False)
    return {"leg": leg, "mig": mig, "inv": inv, "map": json.loads(m.stdout), "lineage": lin,
            "summary": json.loads(lin.stdout) if lin.stdout.strip().startswith("{") else None, "show": show,
            "graph": json.loads((mig / "graph.json").read_text()) if (mig / "graph.json").exists() else None,
            "copy_state": copy_state, "before_fixture": before_fixture, "times": t}


def ds_of(inv, path):
    return next(d["id"] for d in inv["datasets"] if d["legacy_path"] == path)


def test_lineage_never_invalid_and_graph_shows(byu):
    assert byu["lineage"].returncode in (0, 3), byu["lineage"].stdout + byu["lineage"].stderr
    assert not byu["summary"]["errors"]
    assert byu["show"].returncode == 0 and byu["graph"] is not None


def test_model_step_reads_the_inirida_files_and_writes_the_reference_gpkgs(byu):
    inv, g = byu["inv"], byu["graph"]
    step = next(s for s in g["steps"] if s["legacy"]["source"] == MODEL)
    assert step["scope"] == "migrate"
    inputs = {ds_of(inv, p) for p in ("inirida_colombia/croplands_S00W70.tif", "inirida_colombia/worldpop_colombia.tif",
                                      "inirida_colombia/inirida_flood_map/Inirida_crs_10Aug18_SRH2D.shp",
                                      "inirida_colombia/inirida_osm/Inirida_osmpts.shp")}
    outputs = {ds_of(inv, f"reference_outputs/flood_impact_qgisModel/{n}.gpkg")
               for n in ("Agriculture_Flood", "Population_Flood", "OSM_Flood", "Merged_Flood_Impact")}
    assert set(step["inputs"]) == inputs
    assert set(step["outputs"]) == outputs
    for d in g["datasets"]:
        if d["id"] in outputs:
            assert d["role"] == "final" and d["legacy"]["exists"]
    assert byu["summary"]["pairs"] == 4


def test_no_dataset_from_a_formula(byu):
    assert not [d for d in byu["inv"]["datasets"] if d["id"] == "ds.10000" or "(" in d["legacy_path"]]


def test_custom_expression_function_and_food_flag(byu):
    inv = byu["inv"]
    oi = [i for i in inv["open_items"] if i["kind"] == "custom_expression_function" and i["function"] == "amen_group"]
    assert oi and all(i["agent_guess"]["python_def"].startswith("python/fldimpact_def.py:") for i in oi)
    src = (byu["leg"] / MODEL).read_text().splitlines()
    for i in oi:
        e = i["evidence"][0]
        if e["file"] == MODEL:
            assert "amen_group" in src[e["line"] - 1]
    rc = [i for i in inv["open_items"] if i["kind"] == "reversed_containment"]
    assert rc and rc[0]["empty_matches"] == "food"


def test_question_budget(byu):
    qs = byu["graph"]["questions"]
    assert len(qs) <= 8, [q["header"] for q in qs]
    heads = {q["header"] for q in qs}
    assert {"Parity reference", "Model inputs", "Custom expression function"} <= heads


def test_input_maps_are_not_blank(byu):
    pngs = {p["kind"]: p for p in byu["map"]["pngs"]}
    assert len(pngs["inputs_overview"]["datasets"]) >= 4
    for name in ("inputs_overview.png", "grid_corners.png", "legacy_rasters.png"):
        assert ink(byu["mig"] / "discover" / name) > 0.02, name


def test_legacy_copy_and_fixture_untouched_and_scan_fast(byu):
    assert tree_state(byu["leg"]) == byu["copy_state"]
    assert not list(byu["leg"].rglob("*.aux.xml"))
    assert tree_state(FIXTURE) == byu["before_fixture"]
    assert byu["inv"]["scan"]["read_only_verified"]
    assert byu["times"]["scan"] < 60, byu["times"]


def test_single_feature_finals_match_full_row(byu):
    """B9 (BYU e2e): Agriculture_Flood / Population_Flood hold one MultiPoint each and no key; lineage proposes
    compare.match full_row (the first compare no longer stops at KEY_UNDECLARED) and drops the trivial key."""
    ds = {d["id"]: d for d in byu["graph"]["datasets"]}
    for did in ("ds.agriculture_flood", "ds.population_flood"):
        assert ds[did]["compare"]["match"] == "full_row" and not ds[did]["compare"]["key"]
        assert ds[did]["compare_hints"]["key_candidates"] == []
        assert ds[did]["compare_hints"]["match_proposed"]["match"] == "full_row"
    assert ds["ds.osm_flood_gpkg"]["compare"]["match"] is None      # three rows: a real key question


def test_map_metrics_byu_numbers(byu):
    """map.py metrics on BYU: WorldPop has 97 valid of 6,927 cells inside the flood polygon (98.6 % NoData; the
    scan's nodata_inside_aoi skips a raster this large), 37 of 44 OSM points do not touch it."""
    cp = run(MAP, "metrics", byu["mig"], "--json")
    doc = json.loads(cp.stdout)
    assert doc == json.loads((byu["mig"] / "discover" / "metrics.json").read_text())
    r = {x["dataset"]: x for x in doc["rasters"]}
    wp = r["ds.worldpop_colombia_tif"]
    assert (wp["valid_inside_aoi"], wp["cells_inside_aoi"], wp["nodata_inside_aoi_pct"]) == (97, 6927, 98.6)
    assert wp["nodata"] == -99999.0 and wp["dtype"] == "float32" and "valid_cells" not in wp     # 434M cells
    v = {x["dataset"]: x for x in doc["vectors"]}
    assert (v["ds.inirida_osmpts"]["not_touching_aoi"], v["ds.inirida_osmpts"]["features"]) == (37, 44)
    assert tree_state(byu["leg"]) == byu["copy_state"]
