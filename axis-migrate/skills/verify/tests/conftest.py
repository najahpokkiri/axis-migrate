"""Synthetic data for the verify tests: 50x50 rasters, 50-feature layers, 25-row tables (06 §6.1).

Nothing here encodes fixture facts; every value is generated from a fixed seed.
"""
from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

os.environ["GDAL_PAM_ENABLED"] = "NO"

import numpy as np  # noqa: E402
import pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
VERIFY = HERE.parent
PLUGIN = VERIFY.parents[1]
for p in (str(VERIFY), str(PLUGIN / "lib"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import compare  # noqa: E402

CRS_M = "EPSG:32632"
CRS_G = "EPSG:4326"
ORIGIN = (500000.0, 5500000.0)
RES = 10.0
N = 50


def transform(x0=ORIGIN[0], y0=ORIGIN[1], res=RES, res_y=None):
    from affine import Affine
    return Affine(res, 0.0, x0, 0.0, -(res_y if res_y is not None else res), y0)


def base_array(seed=1, dtype="float32"):
    rng = np.random.default_rng(seed)
    a = rng.gamma(2.0, 3.0, size=(N, N)).astype("float64")
    a = np.round(a, 3)
    return a.astype(dtype)


def write_raster(path, arr, *, tr=None, crs=CRS_M, nodata=-9999.0, dtype=None, count=1):
    import rasterio
    arr = np.asarray(arr)
    dtype = dtype or arr.dtype.name
    tr = tr if tr is not None else transform()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[-1], height=arr.shape[-2], count=count,
                       dtype=dtype, crs=crs, transform=tr, nodata=nodata) as ds:
        if count == 1:
            ds.write(arr.astype(dtype), 1)
        else:
            for b in range(count):
                ds.write(arr.astype(dtype), b + 1)
    return path


def legacy_raster_array():
    a = base_array()
    a[0:5, 0:7] = -9999.0          # an asymmetric nodata corner
    a[40:43, 10:12] = -9999.0
    return a


def squares(n=N, size=8.0, seed=3):
    from shapely.geometry import box
    rng = np.random.default_rng(seed)
    geoms = []
    for i in range(n):
        x = ORIGIN[0] + (i % 10) * 50 + rng.uniform(0, 10)
        y = ORIGIN[1] - 400 + (i // 10) * 60 + rng.uniform(0, 10)
        geoms.append(box(x, y, x + size + rng.uniform(0, 20), y + size + rng.uniform(0, 20)))
    return geoms


def vector_frame(n=N, geom="polygon", seed=3):
    import geopandas as gpd
    from shapely.geometry import Point
    rng = np.random.default_rng(seed)
    if geom == "polygon":
        geoms = squares(n, seed=seed)
    else:
        geoms = [Point(ORIGIN[0] + i * 13.0, ORIGIN[1] - i * 7.0) for i in range(n)]
    return gpd.GeoDataFrame({
        "fid_key": [f"k{i:03d}" for i in range(n)],
        "name": [f"name {i}" for i in range(n)],
        "value": np.round(rng.uniform(0, 1000, n), 4),
        "count": rng.integers(0, 100, n).astype("int64"),
    }, geometry=geoms, crs=CRS_M)


def write_vector(path, gdf, layer=None, driver="GPKG"):
    import pyogrio
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pyogrio.write_dataframe(gdf, path, layer=layer or path.stem, driver=driver)
    return path


def table_rows(n=25, seed=5):
    rng = np.random.default_rng(seed)
    return [{"id": f"r{i:02d}", "label": f"zone {i}", "amount": float(np.round(rng.uniform(10, 5000), 6)),
             "share": float(np.round(rng.uniform(0, 1), 9)), "n": int(rng.integers(1, 99))} for i in range(n)]


def write_csv(path, rows, columns=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r.get(c) for c in columns])
    return path


def write_xlsx(path, rows, columns=None, sheet="data", total=True, extra_sheet=False):
    import openpyxl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = columns or list(rows[0].keys())
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(columns)
    for r in rows:
        ws.append([r.get(c) for c in columns])
    if total:
        ws.append([])
        ws.append(["TOTAL"] + [sum(r[c] for r in rows) if isinstance(rows[0][c], (int, float)) else None
                               for c in columns[1:]])
    if extra_sheet:
        wb.create_sheet("other").append(["x"])
    wb.save(path)
    return path


def spec(kind, **kw):
    s = {"dataset_id": kw.pop("dataset_id", "ds.synthetic"), "kind": kind, "role": kw.pop("role", "intermediate"),
         "compare": kw.pop("compare", {}), "measure_crs": kw.pop("measure_crs", CRS_M),
         "run_started_at": kw.pop("run_started_at", "2026-10-16T10:00:00Z")}
    s.update(kw)
    return s


def pair(legacy, new, s):
    return compare.compare_pair(str(legacy), str(new), s)


def codes(result):
    return set(result["reason_codes"])


def diag(result):
    return set(result.get("diagnostic_codes", []))


def check(result, cid):
    for c in result["checks"]:
        if c["id"] == cid:
            return c
    raise KeyError(cid)


def run_cli(*args):
    return subprocess.run([str(PLUGIN / "bin" / "axpy"), str(VERIFY / "compare.py"), *map(str, args)],
                          capture_output=True, text=True)


@pytest.fixture
def raster_pair(tmp_path):
    leg = write_raster(tmp_path / "legacy.tif", legacy_raster_array())
    return tmp_path, leg


SIGNED = "human:tester"


def tol_record(ds, chk, *, rtol=0.0, atol=0.0, spread=0.0, wrong=1.0, agreed_by=SIGNED,
               agreed_at="2026-10-15T09:00:00Z", **extra):
    r = {"id": f"tol.{ds.replace('ds.', '')}.{chk}.v1", "dataset": ds, "check": chk, "metric": "abs+rel",
         "atol": atol, "rtol": rtol, "max_mismatch": 0, "units": "1", "basis": "measured_spread",
         "correct_spread": {"value": spread, "how": "independent", "evidence": "e1"},
         "nearest_wrong": {"value": wrong, "variant": "mutant", "evidence": "e2"},
         "agreed_by": agreed_by, "agreed_at": agreed_at, "decision_ref": None, "fixed_before": None}
    r.update(extra)
    return r


def kd_block(kid, kind, datasets, checks, rule, *, owner="human:tester", status="accepted",
             decided_at="2026-10-15T09:00:00Z", bound=None):
    import yaml
    body = {"id": kid, "kind": kind, "datasets": datasets, "checks": checks, "rule": rule, "bound": bound,
            "reason": "test", "evidence": ["e"], "owner": owner, "decided_at": decided_at,
            "decision_ref": None, "status": status}
    return f"## {kid} · test entry\n\nProse is ignored.\n\n```yaml\n{yaml.safe_dump(body, sort_keys=True)}```\n\n"
