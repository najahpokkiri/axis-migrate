"""Synthetic project + run for the map tests (generic; no fixture facts). Built with the verify tests' run builder,
compared with the real compare.py (pipeline env via scripts/axpy).

    s1 -> ds.a  raster, new identical to legacy
    s2 -> ds.b  raster, new shifted one cell east (data offset on the same grid)
    s3 -> ds.v  vector polygons, new with one feature moved
    s4 -> ds.c  table (no map view)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

os.environ["GDAL_PAM_ENABLED"] = "NO"

import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[2]
VTESTS = PLUGIN / "skills" / "verify" / "tests"
sys.path.insert(0, str(VTESTS))
sys.path.insert(0, str(PLUGIN / "lib"))
import _runbuilder as rb  # noqa: E402

CRS = "EPSG:32632"
N = 40
RES = 10.0
X0, Y0 = 500000.0, 5500000.0
NODATA = -9999.0


def _tr():
    from affine import Affine
    return Affine(RES, 0.0, X0, 0.0, -RES, Y0)


def legacy_array() -> np.ndarray:
    rng = np.random.default_rng(7)
    a = np.round(rng.gamma(2.0, 3.0, size=(N, N)), 3).astype("float32")
    a[:, :4] = NODATA            # nodata band on the west edge
    a[30:33, 20:23] = NODATA     # a hole
    return a


def shifted_east(a: np.ndarray) -> np.ndarray:
    b = np.full_like(a, NODATA)
    b[:, 1:] = a[:, :-1]
    return b


def write_raster(path: Path, arr: np.ndarray, nodata=NODATA):
    import rasterio
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                       dtype=arr.dtype.name, crs=CRS, transform=_tr(), nodata=nodata) as ds:
        ds.write(arr, 1)
    return path


def frame(moved: bool = False):
    import geopandas as gpd
    from shapely.geometry import box
    geoms, keys = [], []
    for i in range(6):
        x = X0 + 40 + (i % 3) * 110
        y = Y0 - 120 - (i // 3) * 110
        if moved and i == 4:
            x += 25
        geoms.append(box(x, y, x + 60, y + 60))
        keys.append(f"k{i}")
    return gpd.GeoDataFrame({"key": keys, "name": [f"zone {i}" for i in range(6)],
                             "value": [float(i) * 1.5 for i in range(6)]}, geometry=geoms, crs=CRS)


def write_vector(path: Path, gdf):
    import pyogrio
    path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(gdf, path, layer=path.stem, driver="GPKG")
    return path


def build(root: Path, *, compare: bool = True) -> Path:
    root = Path(root)
    legacy = root / "legacy"
    a = legacy_array()
    write_raster(legacy / "out" / "a.tif", a)
    write_raster(legacy / "out" / "b.tif", a)
    write_vector(legacy / "out" / "v.gpkg", frame())
    (legacy / "out").mkdir(parents=True, exist_ok=True)
    (legacy / "out" / "c.csv").write_text("id,amount\nr1,1.5\nr2,2.5\n")
    newd = root / "_new"
    write_raster(newd / "a.tif", a)
    write_raster(newd / "b.tif", shifted_east(a))
    write_vector(newd / "v.gpkg", frame(moved=True))
    (newd / "c.csv").write_text("id,amount\nr1,1.5\nr2,2.5\n")
    steps = [rb.step("s1"), rb.step("s2"), rb.step("s3"), rb.step("s4")]
    dsets = [rb.dataset("ds.a", "raster", "intermediate", "out/a.tif", produced_by="s1"),
             rb.dataset("ds.b", "raster", "intermediate", "out/b.tif", produced_by="s2"),
             rb.dataset("ds.v", "vector", "intermediate", "out/v.gpkg", layer="v", produced_by="s3",
                        compare={"key": ["key"], "measure_crs_param": "p.measure_crs"}),
             rb.dataset("ds.c", "table", "final", "out/c.csv", produced_by="s4", compare={"key": ["id"]})]
    g = rb.graph(steps, dsets)
    run = rb.build(root, g, outputs={"ds.a": newd / "a.tif", "ds.b": newd / "b.tif", "ds.v": newd / "v.gpkg",
                                     "ds.c": newd / "c.csv"})
    if compare:
        cp = subprocess.run([str(PLUGIN / "scripts" / "axpy"), str(PLUGIN / "skills" / "verify" / "compare.py"), "run",
                             "--run", str(run)], capture_output=True, text=True)
        if not (run / "verify" / "compare" / "ds.a.json").is_file():
            raise RuntimeError(f"compare.py failed: {cp.stdout[-1500:]} {cp.stderr[-1500:]}")
    return run


if __name__ == "__main__":
    print(build(Path(sys.argv[1])))
