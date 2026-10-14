"""Lux project fixture for the run layer (dev-only; encodes lux facts, so it lives outside the plugin).

    axpy make_lux_project.py --write                  # (re)write graph.json + inventory.json next to this file
    axpy make_lux_project.py --assemble DEST [--pipeline DIR] [--params FILE]
        # DEST/lux-exposure-legacy (copy of the fixture) + DEST/migration/{graph,inventory,params,pipeline,…}

graph.json is a hand-written lineage *draft* following CONTRACTS §2/§4 ids exactly; `--assemble` turns it into
migration/graph.json through `graph.py init` (the single writer), so graph integrity holds. inventory.json carries only what the runner consumes: file pins (sha256, content_sha256) including the
pre-existing QGIS `.aux.xml` sidecars, and per-dataset roles.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLUGIN = REPO / "axis-migrate"
FIXTURE = REPO / "fixtures" / "lux-exposure-legacy"
GOLDEN = REPO / "evals" / "golden" / "lux"
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

LEGACY_NAME = "lux-exposure-legacy"


def _ds(i, path, kind, role, *, layer=None, sheet=None, new=None, compare=None, exists=True, title=None):
    return {"id": i, "title": title or i[3:], "kind": kind, "role": role,
            "persistence": "file" if exists else "gdb",
            "legacy": {"path": path, "layer": layer, "sheet": sheet, "exists": exists},
            "new": new, "reference": {"source": "legacy" if role in ("intermediate", "final") and exists else "none",
                                      "match_method": "lineage", "via_dataset": None, "confirmed_by": None},
            "legacy_in_memory": False, "no_reference_reason": None,
            "compare": {"measure": None, "count": False, "categorical": False, "key": None, "match": None,
                        "layer": layer, "zones": None, "zone_rule": None, "ignore": [], "rename": {}, "sheet": sheet,
                        "header_row": None, "data_rows": None, "total_row": None,
                        "measure_crs_param": "p.measure_crs" if kind == "vector" else None,
                        "may_be_empty": False, **(compare or {})},
            "evidence": []}


def _new(step, name, layer=None):
    return {"path": f"{step}/{name}", "layer": layer}


def _step(i, title, kind, tool, scope, src, lines, ops, params, inputs, outputs, *, module=True, reason=None):
    return {"id": i, "title": title, "kind": kind, "tool": tool, "scope": scope, "scope_reason": reason,
            "review": "confirmed", "legacy": {"source": src, "lines": lines, "executed": scope != "drop"},
            "ops": ops, "params": sorted(params), "inputs": sorted(inputs), "outputs": sorted(outputs),
            "new": {"module": f"pipeline/{i}.py" if module else None, "function": "run", "code_sha256": None},
            "plan": {"approved": {"by": "user:fixture", "at": "2026-10-13T00:00:00Z", "channel": "fixture",
                                  "verbatim": "approved"}} if scope == "migrate" else None, "timeout_s": 120, "ladder": None, "verify": None}


def graph_doc() -> dict:
    M01, S01, S02, S03, S03B, S04 = ("m01_cantons_lux_edit", "s01_prepare_population", "s02_landuse_mask",
                                     "s03_zonal_population", "s03b_zonal_arcpy_old", "s04_distribute_value")
    steps = [
        _step(M01, "Canton names for the value table (manual attribute edit)", "manual", "human", "migrate",
              "NOTES.txt", [[14, 18]], [{"id": f"{M01}.op1", "tool": "manual:attribute_edit", "ref": "NOTES.txt:14-18"}],
              ["p.canton_name_prefix", "p.canton_rename_map"], ["ds.geoboundaries_lux_adm1"], ["ds.cantons_lux_edit"]),
        _step(S01, "Country outline and population clipped to it", "script", "pyqgis", "migrate",
              "02_SCRIPTS/01_prepare_population.py", [[30, 56]],
              [{"id": f"{S01}.op1", "tool": "native:dissolve", "ref": "02_SCRIPTS/01_prepare_population.py:31-35"},
               {"id": f"{S01}.op2", "tool": "gdal:cliprasterbymasklayer",
                "ref": "02_SCRIPTS/01_prepare_population.py:40-55"}],
              ["p.clip_nodata"], ["ds.lux_ppp_2020", "ds.cantons_lux_edit"], ["ds.01_lux_outline", "ds.01_pop_lux"]),
        _step(S02, "Residential polygons and all-touched mask on the population grid", "script", "pyqgis", "migrate",
              "02_SCRIPTS/02_landuse_mask.py", [[27, 89]],
              [{"id": f"{S02}.op1", "tool": "native:reprojectlayer", "ref": "02_SCRIPTS/02_landuse_mask.py:28-31"},
               {"id": f"{S02}.op2", "tool": "native:fixgeometries", "ref": "02_SCRIPTS/02_landuse_mask.py:33-34"},
               {"id": f"{S02}.op3", "tool": "native:fieldcalculator", "ref": "02_SCRIPTS/02_landuse_mask.py:36-43"},
               {"id": f"{S02}.op4", "tool": "native:extractbyexpression",
                "ref": "02_SCRIPTS/02_landuse_mask.py:46-49"},
               {"id": f"{S02}.op5", "tool": "native:reprojectlayer", "ref": "02_SCRIPTS/02_landuse_mask.py:52-55"},
               {"id": f"{S02}.op6", "tool": "gdal:rasterize", "ref": "02_SCRIPTS/02_landuse_mask.py:62-77"}],
              ["p.area_crs", "p.landuse_class", "p.min_area_m2", "p.rasterize_all_touched", "p.mask_nodata",
               "p.mask_burn_value"],
              ["ds.osm_landuse_lux", "ds.01_pop_lux"], ["ds.02_res_2169", "ds.02_res_4326", "ds.02_res_mask"]),
        _step(S03, "Residential weights and zonal population per canton", "script", "pyqgis", "migrate",
              "02_SCRIPTS/03_zonal_population.py", [[28, 58]],
              [{"id": f"{S03}.op1", "tool": "native:rastercalc", "ref": "02_SCRIPTS/03_zonal_population.py:31-38"},
               {"id": f"{S03}.op2", "tool": "native:zonalstatisticsfb",
                "ref": "02_SCRIPTS/03_zonal_population.py:41-47"},
               {"id": f"{S03}.op3", "tool": "native:zonalstatisticsfb",
                "ref": "02_SCRIPTS/03_zonal_population.py:50-56"}],
              ["p.pop_min", "p.pop_res_nodata"], ["ds.cantons_lux_edit", "ds.01_pop_lux", "ds.02_res_mask"],
              ["ds.03_pop_res", "ds.03_canton_pop"]),
        _step(S03B, "Old ArcGIS version of step 03 (never run)", "script", "arcpy", "drop",
              "02_SCRIPTS/03b_zonal_arcpy_OLD.py", [[1, 62]],
              [{"id": f"{S03B}.op1", "tool": "arcpy.sa.ExtractByMask", "ref": "02_SCRIPTS/03b_zonal_arcpy_OLD.py:28-29"}],
              [], ["ds.lux_ppp_2020"], ["ds.work__pop_lux", "ds.work__pop_res", "ds.work__canton_pop"],
              module=False, reason="alternative_impl"),
        _step(S04, "Distribute canton value over residential cells; final outputs", "script", "pyqgis", "migrate",
              "02_SCRIPTS/04_distribute_value.py", [[35, 154]],
              [{"id": f"{S04}.op{k + 1}", "tool": t, "ref": f"02_SCRIPTS/04_distribute_value.py:{r}"} for k, (t, r) in
               enumerate([("openpyxl.load_workbook", "36-43"), ("native:savefeatures", "46"),
                          ("QgsVectorDataProvider.changeAttributeValues", "57-75"), ("gdal:rasterize", "80-95"),
                          ("gdal:rastercalculator", "98-108"), ("native:zonalstatisticsfb", "111-117"),
                          ("csv.writer", "126-130"), ("openpyxl.Workbook.save", "132-140")])],
              ["p.vpc_sheet", "p.vpc_first_row", "p.vpc_last_row", "p.vpc_unit_factor", "p.factor_nodata",
               "p.exposure_nodata", "p.report_sheet"],
              ["ds.value_per_capita", "ds.03_canton_pop", "ds.03_pop_res"],
              ["ds.04_canton_factor", "ds.04_factor", "ds.lux_res_exposure_2020", "ds.exposure_by_canton_gpkg",
               "ds.exposure_by_canton_csv", "ds.exposure_by_canton_xlsx"]),
    ]
    canton_key = {"key": ["canton"], "match": "key"}
    datasets = [
        _ds("ds.lux_ppp_2020", "03_INPUT/lux_ppp_2020.tif", "raster", "input", compare={"measure": "extensive"}),
        _ds("ds.geoboundaries_lux_adm1", "03_INPUT/geoBoundaries-LUX-ADM1.geojson", "vector", "input",
            layer="geoBoundaries-LUX-ADM1"),
        _ds("ds.geoboundaries_lux_adm2", "03_INPUT/geoBoundaries-LUX-ADM2.geojson", "vector", "unused",
            layer="geoBoundaries-LUX-ADM2"),
        _ds("ds.cantons_lux_edit", "03_INPUT/cantons_lux_edit.gpkg", "vector", "input", layer="cantons",
            new=_new(M01, "cantons_lux_edit.gpkg", "cantons"), compare=canton_key),
        _ds("ds.osm_landuse_lux", "03_INPUT/osm_landuse_lux.gpkg", "vector", "input", layer="landuse"),
        _ds("ds.value_per_capita", "03_INPUT/value_per_capita.xlsx", "table", "input", sheet="vpc",
            compare={"header_row": 4, "data_rows": [5, 16]}),
        _ds("ds.01_lux_outline", "05_TEMP/01_lux_outline.gpkg", "vector", "intermediate", layer="01_lux_outline",
            new=_new(S01, "01_lux_outline.gpkg", "01_lux_outline"), compare={"match": "full_row"}),
        _ds("ds.01_pop_lux", "05_TEMP/01_pop_lux.tif", "raster", "intermediate", new=_new(S01, "01_pop_lux.tif"),
            compare={"measure": "extensive", "zones": "ds.cantons_lux_edit:canton", "zone_rule": "centre"}),
        _ds("ds.02_res_2169", "05_TEMP/02_res_2169.gpkg", "vector", "intermediate", layer="02_res_2169",
            new=_new(S02, "02_res_2169.gpkg", "02_res_2169"), compare={"key": ["osm_id", "osm_way_id"], "match": "key"}),
        _ds("ds.02_res_4326", "05_TEMP/02_res_4326.gpkg", "vector", "intermediate", layer="02_res_4326",
            new=_new(S02, "02_res_4326.gpkg", "02_res_4326"), compare={"key": ["osm_id", "osm_way_id"], "match": "key"}),
        _ds("ds.02_res_mask", "05_TEMP/02_res_mask.tif", "raster", "intermediate", new=_new(S02, "02_res_mask.tif"),
            compare={"measure": "mask", "categorical": True}),
        _ds("ds.03_pop_res", "05_TEMP/03_pop_res.tif", "raster", "intermediate", new=_new(S03, "03_pop_res.tif"),
            compare={"measure": "extensive", "zones": "ds.cantons_lux_edit:canton", "zone_rule": "centre"}),
        _ds("ds.03_canton_pop", "05_TEMP/03_canton_pop.gpkg", "vector", "intermediate", layer="03_canton_pop",
            new=_new(S03, "03_canton_pop.gpkg", "03_canton_pop"), compare=canton_key),
        _ds("ds.04_canton_factor", "05_TEMP/04_canton_factor.gpkg", "vector", "intermediate", layer="canton_factor",
            new=_new(S04, "04_canton_factor.gpkg", "canton_factor"), compare=canton_key),
        _ds("ds.04_factor", "05_TEMP/04_factor.tif", "raster", "intermediate", new=_new(S04, "04_factor.tif"),
            compare={"measure": "intensive"}),
        _ds("ds.lux_res_exposure_2020", "04_FINAL/lux_res_exposure_2020.tif", "raster", "final",
            new=_new(S04, "lux_res_exposure_2020.tif"),
            compare={"measure": "extensive", "zones": "ds.cantons_lux_edit:canton", "zone_rule": "centre"}),
        _ds("ds.exposure_by_canton_gpkg", "04_FINAL/exposure_by_canton.gpkg", "vector", "final",
            layer="exposure_by_canton", new=_new(S04, "exposure_by_canton.gpkg", "exposure_by_canton"),
            compare=canton_key),
        _ds("ds.exposure_by_canton_csv", "04_FINAL/exposure_by_canton.csv", "table", "final",
            new=_new(S04, "exposure_by_canton.csv"), compare={"key": ["canton"], "match": "key", "header_row": 1}),
        _ds("ds.exposure_by_canton_xlsx", "04_FINAL/exposure_by_canton.xlsx", "table", "final", sheet="by_canton",
            new=_new(S04, "exposure_by_canton.xlsx"),
            compare={"key": ["canton"], "match": "key", "header_row": 1, "data_rows": [2, 13], "total_row": 15}),
    ]
    for n in ("res_mask", "cantons_edit", "pop_lux", "pop_res", "zs_pop", "zs_res", "canton_pop"):
        datasets.append(_ds(f"ds.work__{n}", f"work.gdb/{n}", "raster" if n in ("res_mask", "pop_lux", "pop_res")
                            else "table", "intermediate", exists=False))
    prod = {}
    for s in steps:
        for o in s["outputs"]:
            prod[o] = s["id"]
    for d in datasets:
        p = prod.get(d["id"])
        d["produced_by"] = {"step": p, "op": None} if p else None
        d["consumed_by"] = sorted(s["id"] for s in steps if d["id"] in s["inputs"])
    edges = []
    by_id = {d["id"]: d for d in datasets}

    def ev(s, d, verb):
        a, b = s["legacy"]["lines"][0]
        return [{"state": "directly_observed" if s["kind"] != "manual" else "documented",
                 "ref": f"{s['legacy']['source']}:{a}-{b}",
                 "summary": f"{s['id']} {verb} {by_id[d]['legacy']['path']}", "by": "fixture", "rev": 1}]
    for s in steps:
        for i in s["inputs"]:
            edges.append({"id": f"e.{i}->{s['id']}", "from": i, "to": s["id"], "kind": "reads", "op": None,
                          "lines": s["legacy"]["lines"], "state": "structurally_inferred", "conflict": False,
                          "review": "confirmed", "evidence": ev(s, i, "reads")})
        for o in s["outputs"]:
            edges.append({"id": f"e.{s['id']}->{o}", "from": s["id"], "to": o, "kind": "writes", "op": None,
                          "lines": s["legacy"]["lines"], "state": "structurally_inferred", "conflict": False,
                          "review": "confirmed", "evidence": ev(s, o, "writes")})
    edges.sort(key=lambda e: e["id"])
    return {"schema": "axis-migrate.graph.v1",
            "project": {"name": LEGACY_NAME, "legacy_root": f"../{LEGACY_NAME}", "legacy_tree_sha256": None,
                        "plugin_version": ac.PLUGIN_VERSION, "created_at": "2026-10-13T00:00:00Z"},
            "revision": 1, "updated": {"rev": 1, "by": "fixture", "at": "2026-10-13T00:00:00Z"},
            "acks": {"agent_seq": 0}, "steps": steps, "datasets": datasets, "edges": edges, "questions": [],
            "invariants": [], "legacy_output_dirs": ["04_FINAL", "05_TEMP"],
            "ladder": {"project": None, "derived_rev": None, "blocking": []}}


def inventory_doc(legacy: Path, graph: dict) -> dict:
    by_path = {}
    for d in graph["datasets"]:
        by_path.setdefault(d["legacy"]["path"], []).append(d["id"])
    files = []
    for p in sorted(legacy.rglob("*")):
        if not p.is_file():
            continue
        r = p.relative_to(legacy).as_posix()
        side_of = r[: -len(".aux.xml")] if r.endswith(".aux.xml") else None
        files.append({"path": r, "size": p.stat().st_size, "sha256": ac.sha256_file(p),
                      "content_sha256": ac.content_sha256(p), "kind": p.suffix.lstrip(".").lower(),
                      "dataset_ids": sorted(by_path.get(r, [])), "sidecar_of": side_of,
                      "sidecars": [f"{r}.aux.xml"] if (p.parent / f"{p.name}.aux.xml").exists() else []})
    datasets = [{"id": d["id"], "legacy_path": d["legacy"]["path"], "layer": d["legacy"]["layer"],
                 "kind": d["kind"], "exists": d["legacy"]["exists"], "role": d["role"],
                 "reference_candidate": d["legacy"]["exists"] and d["role"] in ("intermediate", "final")}
                for d in graph["datasets"]]
    return {"schema": "axis-migrate.inventory.v1", "legacy_root": f"../{LEGACY_NAME}",
            "scan": {"read_only_verified": True, "mtime_reliable": False, "legacy_roots": [LEGACY_NAME],
                     "note": "devtests fixture; scan.py writes the real inventory"},
            "files": files, "datasets": datasets}


def write_fixture() -> None:
    g = graph_doc()
    (HERE / "graph.json").write_text(json.dumps(g, indent=1, sort_keys=True) + "\n")
    (HERE / "inventory.json").write_text(json.dumps(inventory_doc(FIXTURE, g), indent=1, sort_keys=True) + "\n")


def assemble(dest: Path, pipeline: Path | None = None, params: Path | None = None) -> Path:
    """dest/lux-exposure-legacy (fixture copy) + dest/migration with the fixture graph and the golden pipeline."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, dest / LEGACY_NAME)
    mig = dest / "migration"
    mig.mkdir()
    sys.path.insert(0, str(PLUGIN / "skills" / "discover"))
    import graph as graph_py
    graph_py.init(mig, json.loads((HERE / "graph.json").read_text()))   # only graph.py writes graph.json
    shutil.copyfile(HERE / "inventory.json", mig / "inventory.json")
    shutil.copyfile(params or GOLDEN / "params.yaml", mig / "params.yaml")
    shutil.copytree(pipeline or GOLDEN / "pipeline", mig / "pipeline",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (mig / "tolerances.yaml").write_text("version: 1\ndefaults: {}\nrecords: []\n")
    (mig / "decisions.md").write_text("# Decisions\n")
    (mig / "known-differences.md").write_text("# Known differences\n")
    return mig


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true", help="rewrite graph.json and inventory.json here")
    ap.add_argument("--assemble", metavar="DEST", help="build a project dir at DEST")
    ap.add_argument("--pipeline", help="pipeline package dir (default: evals/golden/lux/pipeline)")
    ap.add_argument("--params", help="params.yaml (default: evals/golden/lux/params.yaml)")
    a = ap.parse_args()
    if a.write:
        write_fixture()
    if a.assemble:
        print(assemble(Path(a.assemble).resolve(), Path(a.pipeline) if a.pipeline else None,
                       Path(a.params) if a.params else None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
