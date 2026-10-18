"""mlview.py: the MapLibre page of a run (synthetic data; no fixture facts).

Covers: what is in the page (legacy and new of every compared raster/vector, no table), identify's data equals the
sources cell for cell (rasters warped from EPSG:32632, so marked not exact), one shared scale per dataset, the page
loads nothing from a CDN and carries its data's sha, the legacy folder is untouched, setup errors exit 2.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest

from conftest import MAP, map_py, map_python, run_cli


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    map_python()
    import synth_map
    run = synth_map.build(tmp_path_factory.mktemp("mlview") / "proj")
    legacy = run.parents[2] / "legacy"
    before = tree(legacy)
    cp = run_cli("mlview.py", "build", run.parents[1], "--run", run, "--json")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    doc = json.loads(cp.stdout)
    out = run.parents[1] / doc["folder"]
    data = (out / "data.js").read_bytes()
    page = json.loads(data.decode()[len("window.AXIS_DATA = "):].rstrip().rstrip(";"))
    return {"run": run, "legacy": legacy, "before": before, "doc": doc, "out": out, "data": data, "page": page}


def test_layers(built):
    ids = {l["id"]: l for l in built["doc"]["layers"]}
    want = {f"{d} · {s}" for d in ("ds.a", "ds.b", "ds.e", "ds.v") for s in ("legacy", "new")}
    assert set(ids) == want                                            # ds.c is a table: no layer
    assert all(ids[f"{d} · legacy"]["kind"] == "raster" for d in ("ds.a", "ds.b", "ds.e"))
    assert ids["ds.v · new"]["kind"] == "vector" and ids["ds.v · new"]["role"] == "new"
    r = ids["ds.b · legacy"]
    assert r["exact"] is False and "warped from EPSG:32632" in r["exact_note"]
    assert built["doc"]["skipped"] == []


def test_identify_data_equals_the_sources(built):
    """Every page cell holds the source value at that cell's centre (what identify(lon, lat) returns there)."""
    pairs = [[l["id"], l["source"], l["transform"], l["width"], l["height"], l["grid"], l["dtype"]]
             for l in built["page"]["layers"] if l["kind"] == "raster"]
    res = map_py(SAMPLE, json.dumps(pairs))
    assert res["checked"] > 6 * 1000 and res["bad"] == [], res["bad"][:5]


SAMPLE = r"""
import base64, json, sys
import numpy as np, rasterio
from rasterio.warp import transform
bad, n = [], 0
for lid, src, (x0, dx, y0, dy), w, h, grid, dt in json.loads(sys.argv[1]):
    v = np.frombuffer(base64.b64decode(grid), dtype="<f8" if dt == "float64" else "<f4").reshape(h, w)
    rows, cols = np.mgrid[0:h, 0:w]
    lon, lat = x0 + (cols.ravel() + 0.5) * dx, y0 + (rows.ravel() + 0.5) * dy
    with rasterio.open(src) as s:
        xs, ys = transform("EPSG:4326", s.crs, lon.tolist(), lat.tolist())
        inside = [(s.bounds.left < x < s.bounds.right and s.bounds.bottom < y < s.bounds.top) for x, y in zip(xs, ys)]
        sv = np.array([q[0] for q in s.sample(list(zip(xs, ys)))], dtype="float64")
        sv[(sv == s.nodata) | ~np.array(inside)] = np.nan
    pv = v.ravel().astype("float64")
    same = (pv == sv) | (np.isnan(pv) & np.isnan(sv))
    n += same.size
    bad += [[lid, int(i), pv[i], sv[i]] for i in np.flatnonzero(~same)[:3]]
print(json.dumps({"checked": n, "bad": bad}, default=float))
"""


def test_one_scale_per_dataset(built):
    L = {l["id"]: l for l in built["page"]["layers"]}
    for d in ("ds.a", "ds.b", "ds.e"):
        assert L[f"{d} · legacy"]["scale"] == L[f"{d} · new"]["scale"], d


def test_page_is_self_contained(built):
    html = (built["out"] / "index.html").read_text()
    assert built["doc"]["data_sha256"] == hashlib.sha256(built["data"]).hexdigest() in html
    assert not re.search(r'<(script|link)[^>]+(src|href)="(https?:)?//', html)          # no CDN
    for f in ("maplibre-gl.js", "maplibre-gl.css"):
        assert (built["out"] / f).read_bytes() == (MAP / "maplibre" / "vendor" / f).read_bytes()
    assert built["doc"]["url_path"].startswith("/data/runs/") and built["doc"]["url_path"].endswith("/index.html")


def test_legacy_untouched(built):
    assert tree(built["legacy"]) == built["before"]


def test_setup_errors(built, tmp_path):
    cp = run_cli("mlview.py", "build", tmp_path)                       # no inventory, no run
    assert cp.returncode == 2 and "inventory" in cp.stderr
    cp = run_cli("mlview.py", "build", tmp_path, "--run", built["run"])  # a run of another migration
    assert cp.returncode == 2 and "is not a run of" in cp.stderr
