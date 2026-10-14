"""A tiny synthetic legacy project + migration for the run-skill tests (no fixture facts; CONTRACTS §0).

Layout built by `build_project(root)`:
  root/legacy_synth/03_INPUT/{pop.tif (10x10 float32, EPSG:32631), zones.gpkg (layer zones, 2 polygons)}
  root/legacy_synth/05_TEMP/01_pop_clip.tif            legacy intermediate (isolation feed)
  root/legacy_synth/04_FINAL/{zone_sum.csv, zone_stats.gpkg}   legacy finals (never staged)
  root/migration/{graph.json, params.yaml, inventory.json, pipeline/ (2 steps + vendored runtime), tests/}
"""
from __future__ import annotations

import csv
import json
import sys
import textwrap
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

PLUGIN = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

LEGACY = "legacy_synth"
NODATA_IN = -99999.0
GRID = dict(crs="EPSG:32631", transform=from_origin(500000.0, 5600000.0, 100.0, 100.0), width=10, height=10)

S01 = '''\
"""Clip population to the zones (cell-centre rule) on the population grid."""
import numpy as np
from rasterio.features import geometry_mask

STEP_ID = "s01_clip_population"
INPUTS = ["ds.pop", "ds.zones"]
OUTPUTS = ["ds.01_pop_clip"]
PARAMS = ["p.clip_nodata"]


def run(ctx):
    pop, _ = ctx.read_raster("ds.pop")
    grid = ctx.grid_of("ds.pop")
    zones = ctx.read_vector("ds.zones").to_crs(grid.crs)
    inside = geometry_mask(list(zones.geometry), out_shape=grid.shape, transform=grid.transform, invert=True)
    nodata = ctx.param("p.clip_nodata")
    out = np.where(inside & ~np.ma.getmaskarray(pop), np.ma.getdata(pop), np.float32(nodata)).astype("float32")
    ctx.write_raster("ds.01_pop_clip", out, grid=grid, dtype="float32", nodata=nodata, compress="lzw")
'''

S02 = '''\
"""Zone sums of the clipped population (cells >= min value), as CSV and GPKG."""
import numpy as np
from rasterio.features import rasterize

STEP_ID = "s02_zonal_sum"
INPUTS = ["ds.01_pop_clip", "ds.zones"]
OUTPUTS = ["ds.zone_stats", "ds.zone_sum"]
PARAMS = ["p.min_value"]


def run(ctx):
    pop, _ = ctx.read_raster("ds.01_pop_clip")
    grid = ctx.grid_of("ds.01_pop_clip")
    zones = ctx.read_vector("ds.zones")
    ids = rasterize(((g, i + 1) for i, g in enumerate(zones.to_crs(grid.crs).geometry)), out_shape=grid.shape,
                    transform=grid.transform, fill=0, dtype="int32")
    vals = np.ma.filled(pop.astype("float64"), 0.0)
    vals[vals < ctx.param("p.min_value")] = 0.0
    sums = np.bincount(ids.ravel(), weights=vals.ravel(), minlength=len(zones) + 1)[1:]
    zones["name"] = zones["name"].str.strip()
    zones["pop_sum"] = sums
    ctx.write_vector("ds.zone_stats", zones)
    ctx.write_csv("ds.zone_sum", ["name", "pop_sum"], [[n, float(s)] for n, s in zip(zones["name"], sums)])
'''

SMOKE_TEST = '''\
import axis_pipeline.s01_clip_population as s01
import axis_pipeline.s02_zonal_sum as s02


def test_contract_constants():
    assert s01.STEP_ID == "s01_clip_population" and callable(s01.run)
    assert set(s02.OUTPUTS) == {"ds.zone_sum", "ds.zone_stats"}
'''


def _write_tif(path: Path, arr: np.ndarray, nodata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", dtype=arr.dtype.name, count=1, nodata=nodata, compress="lzw",
                       **GRID) as dst:
        dst.write(arr, 1)


def _zones():
    import geopandas as gpd
    from shapely.geometry import box
    # zone A covers columns 0-4 rows 0-7; zone B columns 5-8 rows 2-9; column 9 and rows 8-9 of A outside
    a = box(500000, 5600000 - 800, 500500, 5600000)
    b = box(500500, 5600000 - 1000, 500900, 5600000 - 200)
    return gpd.GeoDataFrame({"name": ["Alpha ", "Beta"], "zone_id": [1, 2]}, geometry=[a, b], crs="EPSG:32631")


def legacy_outputs(pop: np.ndarray, zones, min_value: float = 0.5):
    from rasterio.features import geometry_mask, rasterize
    inside = geometry_mask(list(zones.geometry), out_shape=(10, 10), transform=GRID["transform"], invert=True)
    clip = np.where(inside & (pop != NODATA_IN), pop, np.float32(-9999.0)).astype("float32")
    ids = rasterize(((g, i + 1) for i, g in enumerate(zones.geometry)), out_shape=(10, 10),
                    transform=GRID["transform"], fill=0, dtype="int32")
    vals = np.where(clip == -9999.0, 0.0, clip.astype("float64"))
    vals[vals < min_value] = 0.0
    sums = np.bincount(ids.ravel(), weights=vals.ravel(), minlength=3)[1:]
    return clip, sums


def graph_doc() -> dict:
    def ds(i, path, layer, kind, role, produced_by=None, consumed_by=(), new=None):
        return {"id": i, "title": i[3:], "kind": kind, "role": role, "persistence": "file",
                "legacy": {"path": path, "layer": layer, "sheet": None, "exists": True},
                "new": new, "produced_by": produced_by, "consumed_by": list(consumed_by),
                "reference": {"source": "legacy" if role != "input" else "none", "match_method": "lineage"},
                "legacy_in_memory": False, "no_reference_reason": None,
                "compare": {"may_be_empty": False}}
    steps = [
        {"id": "s01_clip_population", "title": "clip", "kind": "script", "tool": "python", "scope": "migrate",
         "scope_reason": None, "params": ["p.clip_nodata"], "inputs": ["ds.pop", "ds.zones"],
         "outputs": ["ds.01_pop_clip"], "timeout_s": 60,
         "new": {"module": "pipeline/s01_clip_population.py", "function": "run"}},
        {"id": "s02_zonal_sum", "title": "zonal", "kind": "script", "tool": "python", "scope": "migrate",
         "scope_reason": None, "params": ["p.min_value"], "inputs": ["ds.01_pop_clip", "ds.zones"],
         "outputs": ["ds.zone_stats", "ds.zone_sum"], "timeout_s": 60,
         "new": {"module": "pipeline/s02_zonal_sum.py", "function": "run"}},
    ]
    datasets = [
        ds("ds.pop", "03_INPUT/pop.tif", None, "raster", "input", consumed_by=["s01_clip_population"]),
        ds("ds.zones", "03_INPUT/zones.gpkg", "zones", "vector", "input",
           consumed_by=["s01_clip_population", "s02_zonal_sum"]),
        ds("ds.01_pop_clip", "05_TEMP/01_pop_clip.tif", None, "raster", "intermediate",
           produced_by={"step": "s01_clip_population", "op": "s01_clip_population.op1"},
           consumed_by=["s02_zonal_sum"], new={"path": "s01_clip_population/01_pop_clip.tif", "layer": None}),
        ds("ds.zone_sum", "04_FINAL/zone_sum.csv", None, "table", "final",
           produced_by={"step": "s02_zonal_sum", "op": "s02_zonal_sum.op1"},
           new={"path": "s02_zonal_sum/zone_sum.csv", "layer": None}),
        ds("ds.zone_stats", "04_FINAL/zone_stats.gpkg", "zone_stats", "vector", "final",
           produced_by={"step": "s02_zonal_sum", "op": "s02_zonal_sum.op2"},
           new={"path": "s02_zonal_sum/zone_stats.gpkg", "layer": "zone_stats"}),
    ]
    edges = []
    for s in steps:
        for i in s["inputs"]:
            edges.append({"id": f"e.{i}->{s['id']}", "from": i, "to": s["id"], "kind": "reads", "state": "verified"})
        for o in s["outputs"]:
            edges.append({"id": f"e.{s['id']}->{o}", "from": s["id"], "to": o, "kind": "writes", "state": "verified"})
    for s in steps:
        s["plan"] = {"approved": {"by": "user:synthetic", "at": "2026-10-13T00:00:00Z", "channel": "test",
                                  "verbatim": "approve"}}
    return {"schema": "axis-migrate.graph.v1",
            "project": {"name": "synth", "legacy_root": f"../{LEGACY}", "legacy_tree_sha256": None,
                        "plugin_version": ac.PLUGIN_VERSION, "created_at": "2026-10-13T00:00:00Z"},
            "revision": 1, "steps": steps, "datasets": datasets, "edges": edges, "questions": [], "invariants": [],
            "legacy_output_dirs": ["04_FINAL", "05_TEMP"]}


PARAMS_YAML = """\
version: 1
params:
  - id: p.clip_nodata
    node: s01_clip_population
    kind: nodata
    value: -9999.0
    resolution: resolved
    source: code
    status: confirmed
  - id: p.min_value
    node: s02_zonal_sum
    kind: threshold
    value: 0.5
    resolution: resolved
    source: code
    status: confirmed
"""


def build_project(root: Path) -> Path:
    """Write the synthetic project under root; returns the migration dir."""
    import pyogrio
    legacy = root / LEGACY
    rng = np.random.default_rng(7)
    pop = (rng.random((10, 10)) * 3).astype("float32")
    pop[0, 9] = NODATA_IN
    pop[9, 0] = NODATA_IN
    _write_tif(legacy / "03_INPUT" / "pop.tif", pop, NODATA_IN)
    zones = _zones()
    (legacy / "03_INPUT").mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(zones, legacy / "03_INPUT" / "zones.gpkg", layer="zones", driver="GPKG")
    clip, sums = legacy_outputs(pop, zones)
    _write_tif(legacy / "05_TEMP" / "01_pop_clip.tif", clip, -9999.0)
    (legacy / "04_FINAL").mkdir(parents=True, exist_ok=True)
    with open(legacy / "04_FINAL" / "zone_sum.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "pop_sum"])
        for n, s in zip(["Alpha", "Beta"], sums):
            w.writerow([n, float(s)])
    z2 = zones.copy()
    z2["name"] = z2["name"].str.strip()
    z2["pop_sum"] = sums
    pyogrio.write_dataframe(z2, legacy / "04_FINAL" / "zone_stats.gpkg", layer="zone_stats", driver="GPKG")
    (legacy / "NOTES.txt").write_text("synthetic legacy project for runner tests\n")

    mig = root / "migration"
    pipe = mig / "pipeline"
    pipe.mkdir(parents=True)
    for f in ("_axis_runtime.py", "__main__.py", "__init__.py"):
        (pipe / f).write_bytes((PLUGIN / "skills" / "build" / "runtime" / f).read_bytes())
    (pipe / "s01_clip_population.py").write_text(S01)
    (pipe / "s02_zonal_sum.py").write_text(S02)
    (mig / "tests").mkdir()
    (mig / "tests" / "test_smoke.py").write_text(SMOKE_TEST)
    graph_py().init(mig, graph_doc())          # graph.json is only ever written by graph.py
    (mig / "params.yaml").write_text(PARAMS_YAML)
    (mig / "tolerances.yaml").write_text("version: 1\ndefaults: {}\nrecords: []\n")
    (mig / "decisions.md").write_text("# decisions\n")
    (mig / "known-differences.md").write_text("# known differences\n")
    files = []
    for p in sorted(legacy.rglob("*")):
        if p.is_file():
            r = p.relative_to(legacy).as_posix()
            files.append({"path": r, "size": p.stat().st_size, "sha256": ac.sha256_file(p),
                          "content_sha256": ac.content_sha256(p)})
    (mig / "inventory.json").write_text(json.dumps({"schema": "axis-migrate.inventory.v1",
                                                    "legacy_root": f"../{LEGACY}", "files": files}, indent=1))
    return mig


def graph_py():
    sys.path.insert(0, str(PLUGIN / "skills" / "discover"))
    import graph
    return graph


def edit_graph(mig: Path, fn) -> None:
    """Test-only: change graph.json the way graph.py writes it (lock, write, head record), so integrity holds."""
    g_mod = graph_py()
    with g_mod.locked(mig):
        g = json.loads((mig / "graph.json").read_text())
        fn(g)
        g["revision"] = int(g.get("revision", 1)) + 1
        g_mod._write_graph(mig, g)
        g_mod._record_head(mig, g["revision"])


def set_step(mig: Path, step_id: str, source: str) -> None:
    (mig / "pipeline" / f"{step_id}.py").write_text(textwrap.dedent(source))
