"""lineage.py (graph draft checked against files; questions only for unmatched items) and map.py inputs on synthetic
projects. Synthetic data only."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from scan_synth import GRAPH, LINEAGE, MAP, build_project, run, scan


def lineage(mig, *args, check=True):
    r = run(LINEAGE, mig, "--json", *args, check=check)
    return r, json.loads(r.stdout) if r.stdout.strip().startswith("{") else None


def draft(mig):
    return json.loads((mig / ".graph" / "draft.json").read_text())


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    root = tmp_path_factory.mktemp("lin")
    leg = build_project(root)
    mig = root / "migration"
    scan(leg, mig)
    r, summ = lineage(mig)
    return {"root": root, "legacy": leg, "mig": mig, "summary": summ, "draft": draft(mig)}


def test_steps_scope_and_manual_step(clean):
    st = {s["id"]: s for s in clean["draft"]["steps"]}
    assert set(st) == {"m01_zones", "s01_clip", "s02_mask", "s03_table", "s03b_mask_arcpy_old"}
    assert st["m01_zones"]["kind"] == "manual" and st["m01_zones"]["scope"] == "keep_legacy"
    assert st["s03b_mask_arcpy_old"]["scope"] == "drop" and st["s03b_mask_arcpy_old"]["scope_reason"] == "alternative_impl"
    assert st["s03b_mask_arcpy_old"]["twin_of"] == "s02_mask"
    assert st["s01_clip"]["legacy"]["executed"] == "yes" and st["s03b_mask_arcpy_old"]["legacy"]["executed"] == "no"
    assert len(st["s02_mask"]["ops"]) == 4
    facts = {(f["node"], f["fact"]) for f in clean["draft"]["scope_facts"]}
    assert ("s03b_mask_arcpy_old", "superseded") in facts and ("ds.extra_display", "display_only") in facts


def test_persisted_datasets_only_and_edges_have_evidence(clean):
    g = clean["draft"]
    ids = {d["id"] for d in g["datasets"]}
    assert not any(i.startswith("ds.mem") for i in ids)
    for e in g["edges"]:
        assert e["evidence"] and all(x.get("ref") for x in e["evidence"]), e
    e = next(x for x in g["edges"] if x["id"] == "e.s02_mask>ds.02_mask")
    assert e["evidence"][0]["ref"].startswith("scripts/02_mask.py:")
    grid = next(x for x in g["edges"] if x["id"] == "e.ds.01_clip>s02_mask#reads_grid")
    assert any("transform, shape and CRS" in x["summary"] for x in grid["evidence"])
    eq = [x for x in g["edges"] if x["kind"] == "equivalent_to"]
    assert [(x["from"], x["to"]) for x in eq] == [("ds.old__mask", "ds.02_mask")]
    assert any(x["kind"] == "displays" and x["from"] == "project:project/map.qgs" for x in g["edges"])


def test_default_new_paths_and_pairs(clean):
    d = {x["id"]: x for x in clean["draft"]["datasets"]}
    assert d["ds.02_mask"]["new"]["path"] == "s02_mask/02_mask.tif"
    assert d["ds.01_outline"]["new"] == {"path": "s01_clip/01_outline.gpkg", "layer": "01_outline", "auto": True}
    assert d["ds.zones"].get("new") is None  # output of a kept-legacy manual step stays an input
    assert clean["summary"]["pairs"] == 4


def test_clean_project_raises_only_the_manual_step_question(clean):
    qs = clean["draft"]["questions"]
    assert [(q["id"], q["kind"], q["node"]) for q in qs] == [("q01", "manual_step", "m01_zones")]
    assert qs[0]["options"][0]["recommended"] and qs[0]["options"][-1]["value"] == "dont_know"
    assert "ds.zones" in qs[0]["impact"]["datasets"]
    assert not clean["draft"]["lineage_checks"]["failed"] and not clean["draft"]["lineage_checks"]["warnings"]
    checks = {c["check"] for c in clean["draft"]["lineage_checks"]["passed"]}
    assert {"a_write_exists", "b_layer_exists", "c_field_in_schema", "d_grid_equals_template"} <= checks


def test_graph_init_through_graph_py(clean):
    if not GRAPH.exists():
        pytest.skip("graph.py not built yet")
    g = json.loads((clean["mig"] / "graph.json").read_text())
    assert g["revision"] == 1 and g["project"]["legacy_root"] == "../legacy"
    assert run(GRAPH, "validate", clean["mig"]).returncode == 0
    r, summ = lineage(clean["mig"], check=False)
    assert r.returncode == 3  # an existing graph is never overwritten


def _fresh(tmp_path, **kw):
    leg = build_project(tmp_path, **kw)
    mig = tmp_path / "migration"
    return leg, mig


def test_mutation_missing_write(tmp_path):
    leg, mig = _fresh(tmp_path)
    (leg / "tmp" / "02_mask.tif").unlink()
    scan(leg, mig)
    lineage(mig, "--draft-only")
    q = [q for q in draft(mig)["questions"] if q["header"] == "Missing output"]
    assert q and q[0]["node"] == "s02_mask" and "ds.02_mask" in q[0]["nodes"]


def test_mutation_grid_shift_is_a_high_question(tmp_path):
    leg, mig = _fresh(tmp_path, shift_mask=True)
    scan(leg, mig)
    lineage(mig, "--draft-only")
    q = [q for q in draft(mig)["questions"] if q["header"] == "Grid differs from template"]
    assert q and q[0]["impact"]["level"] == "high" and q[0]["trigger"]["ref"].startswith("scripts/02_mask.py:")


def test_mutation_orphan_never_paired(tmp_path):
    leg, mig = _fresh(tmp_path)
    shutil.copy(leg / "tmp" / "02_mask.tif", leg / "tmp" / "old_mask_v2.tif")
    scan(leg, mig)
    lineage(mig, "--draft-only")
    g = draft(mig)
    q = [q for q in g["questions"] if q["header"] == "Unreferenced legacy files"]
    assert q and q[0]["node"] == "ds.old_mask_v2"
    d = next(x for x in g["datasets"] if x["id"] == "ds.old_mask_v2")
    assert d["reference"]["source"] == "none" and d.get("new") is None and d["orphan"]
    assert not any(e["to"] == "ds.old_mask_v2" or e["from"] == "ds.old_mask_v2" for e in g["edges"]
                   if e["kind"] != "displays")


def test_mutation_layer_renamed(tmp_path):
    import pyogrio
    leg, mig = _fresh(tmp_path)
    gdf = pyogrio.read_dataframe(leg / "in" / "zones.gpkg")
    (leg / "in" / "zones.gpkg").unlink()
    pyogrio.write_dataframe(gdf, leg / "in" / "zones.gpkg", layer="zones_v2", driver="GPKG")
    scan(leg, mig)
    lineage(mig, "--draft-only")
    q = [q for q in draft(mig)["questions"] if q["header"] == "Layer not found"]
    assert q and "scripts/01_clip.py" in q[0]["trigger"]["ref"]


def test_mutation_mtime_is_warning_only(tmp_path):
    leg, mig = _fresh(tmp_path)
    t = time.time()
    os.utime(leg / "tmp" / "02_mask.tif", (t - 100, t - 100))
    os.utime(leg / "tmp" / "01_clip.tif", (t, t))
    scan(leg, mig)
    lineage(mig, "--draft-only")
    g = draft(mig)
    w = g["lineage_checks"]["warnings"]
    assert w and w[0]["check"] == "f_mtime_order" and w[0]["producer_output"] == "ds.01_clip"
    assert [q["kind"] for q in g["questions"]] == ["manual_step"]


def test_map_inputs_pngs_registered_and_deterministic(tmp_path):
    leg = build_project(tmp_path)
    mig = tmp_path / "migration"
    scan(leg, mig)
    run(MAP, "inputs", mig)
    names = ["inputs_overview.png", "grid_corners.png", "legacy_rasters.png"]
    first = {n: (mig / "discover" / n).read_bytes() for n in names}
    assert all(b.startswith(b"\x89PNG") and len(b) > 5000 for b in first.values())
    inv = json.loads((mig / "inventory.json").read_text())
    assert sorted(p["path"] for p in inv["map"]["pngs"]) == sorted(f"discover/{n}" for n in names)
    run(MAP, "inputs", mig)
    assert {n: (mig / "discover" / n).read_bytes() for n in names} == first
    # the third PNG's other name from an earlier run is removed and unregistered
    import scan as scan_mod
    (mig / "discover" / "legacy_vectors.png").write_bytes(first["grid_corners.png"])
    assert scan_mod.annotate(mig, "png", None, "discover/legacy_vectors.png", "legacy_vectors")[0] == 0
    run(MAP, "inputs", mig)
    assert not (mig / "discover" / "legacy_vectors.png").exists()
    inv = json.loads((mig / "inventory.json").read_text())
    assert sorted(p["path"] for p in inv["map"]["pngs"]) == sorted(f"discover/{n}" for n in names)


def test_map_metrics_nodata_inside_aoi_and_cells_lost_along_a_family(tmp_path):
    """map.py metrics (map review C6): the numbers three runs computed with ad hoc scripts, checked here against
    a direct numpy count of the legacy files."""
    import numpy as np
    import rasterio
    leg = build_project(tmp_path)
    mig = tmp_path / "migration"
    scan(leg, mig)
    cp = run(MAP, "metrics", mig)
    assert "discover/metrics.json" in cp.stdout and "valid cells lost" in cp.stdout
    doc = json.loads((mig / "discover" / "metrics.json").read_text())
    assert doc["schema"] == "axis-migrate.discover-metrics.v1"
    inv = json.loads((mig / "inventory.json").read_text())
    by_path = {d["legacy_path"]: d["id"] for d in inv["datasets"]}

    def valid(rel):
        with rasterio.open(leg / rel) as s:
            a = s.read(1)
            return a != s.nodata if s.nodata is not None else np.ones(a.shape, bool)
    r = {x["dataset"]: x for x in doc["rasters"]}
    raw, clip, mask = (r[by_path[k]] for k in ("in/raw.tif", "tmp/01_clip.tif", "tmp/02_mask.tif"))
    assert raw["dtype"] == "float32" and raw["nodata"] == -1.0 and raw["valid_cells"] == int(valid("in/raw.tif").sum())
    assert doc["aoi"] == "ds.zones"
    for x in (raw, clip, mask):
        assert x["valid_inside_aoi"] + x["nodata_inside_aoi"] == x["cells_inside_aoi"] > 0
    t = {(x["from"], x["to"]): x for x in doc["transitions"]}
    assert list(t) == [(raw["dataset"], clip["dataset"]), (clip["dataset"], mask["dataset"])]   # role order
    for (a, b), x in t.items():                     # lost − gained = the drop in valid cells inside the AOI
        assert x["where"] == "inside AOI"
        assert x["valid_lost"] - x["valid_gained"] == r[a]["valid_inside_aoi"] - r[b]["valid_inside_aoi"]
    assert t[(clip["dataset"], mask["dataset"])]["valid_lost"] == 199 and mask["nodata_inside_aoi_pct"] == 47.84
    # deterministic, and the legacy folder is only read
    first = (mig / "discover" / "metrics.json").read_bytes()
    run(MAP, "metrics", mig)
    assert (mig / "discover" / "metrics.json").read_bytes() == first
    assert not list(leg.rglob("*.aux.xml")) or all(p.name == "raw.tif.aux.xml" for p in leg.rglob("*.aux.xml"))
