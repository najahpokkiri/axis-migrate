#!/usr/bin/env python3
"""mlview.py — one MapLibre page with every map layer of a migration and an exact identify (display only).

    python3 mlview.py build <migration> [--run <run_dir>] [--json]

Without --run (discover): the legacy inputs as `views.py inputs` picks them, into <migration>/discover/mlview/.
With --run (verify): the same inputs (hidden, AOI shown) plus legacy and new of every compared raster/vector, into
<run>/verify/views/mlview/ (<run>/amend/views/mlview/ after seal). Each folder holds:
  index.html          maplibre/view.html, its ready line carrying data.js's sha256
  data.js             per raster a nearest-neighbour PNG plus the cell values (float32; float64 and 32/64-bit
                      integers as float64) and the geotransform in EPSG:4326, clipped to the AOI + 25 %; per vector
                      GeoJSON in EPSG:4326, attributes kept (inputs clipped to the AOI box)
  maplibre-gl.js/.css vendored MapLibre GL JS (maplibre/vendor/SOURCE.md): no CDN
  mlview.json         index: layers, AOI, data sha256, and why a layer is not exact (warped or decimated)
Legacy and new rasters of one dataset share one p2/p98 colour scale. Colours as LOOK.md: legacy blue, new orange.
Served by start.py's server like every view: http://127.0.0.1:<port><url_path>. In the page,
window.__AXIS_MAP__ has identify(lon, lat) (every data layer's value at a point, basemap excluded),
identifyMany, setBasemap(name), setOpacity({basemap, data}), setLayer(id, on) and state(); map SKILL §6.
The legacy folder is only read. Exit: 0 built · 1 some layers could not be built (listed) · 2 setup error.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import shutil
import sys
from pathlib import Path

sys.dont_write_bytecode = True
os.environ.setdefault("GDAL_PAM_ENABLED", "NO")           # never write .aux.xml beside a legacy file
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import views  # noqa: E402

TEMPLATE = HERE / "maplibre" / "view.html"
VENDOR = HERE / "maplibre" / "vendor"
MAX_CELLS = 4_000_000          # per raster layer in the page; above it the layer is decimated and marked not exact
MAX_FEATURES = 20_000          # per vector layer
EXACT64 = {"float64", "int32", "uint32", "int64", "uint64"}


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _aoi(inv: dict, shown: list[dict], legacy: Path):
    """The AOI as views.py inputs picks it: the inventory's, else the single polygon layer shown; (gdf, note)."""
    aoi_id = inv.get("aoi")
    if aoi_id:
        ad = next((d for d in inv.get("datasets") or [] if d.get("id") == aoi_id), None)
        try:
            g = views._read_vector((legacy / ad["legacy_path"]).resolve(), ad.get("layer")) if ad else None
            if g is not None and g.crs is not None and len(g):
                return aoi_id, g, f"AOI: {aoi_id} (inventory)"
        except Exception:
            pass
        return None, None, None
    polys = []
    for d in (x for x in shown if x["kind"] == "vector"):
        try:
            g = views._read_vector((legacy / d["legacy_path"]).resolve(), d.get("layer"))
        except Exception:
            continue
        types = set(g.geometry.geom_type.dropna().unique()) if len(g) else set()
        if types and all(t.endswith("Polygon") for t in types) and g.crs is not None:
            polys.append((d["id"], g))
    if len(polys) == 1:
        return polys[0][0], polys[0][1], f"AOI: {polys[0][0]} (the only polygon layer; the inventory names no AOI)"
    return None, None, None


def _pad(b, f=0.25):
    x0, y0, x1, y1 = b
    px, py = (x1 - x0) * f or 0.01, (y1 - y0) * f or 0.01
    return [x0 - px, y0 - py, x1 + px, y1 + py]


def read_raster(path: Path, box):
    """(values as float64 with NaN for NoData, transform [x0, dx, y0, dy] in EPSG:4326, source dtype, notes)."""
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window, from_bounds
    notes = []
    with rasterio.open(path) as src:
        dtype = src.dtypes[0]
        ds = src
        if src.crs is None:
            raise ValueError("raster has no CRS")
        if src.crs.to_epsg() != 4326:
            ds = WarpedVRT(src, crs="EPSG:4326", resampling=Resampling.nearest)
            notes.append(f"warped from {src.crs.to_string()} to EPSG:4326 by nearest neighbour: values are source "
                         "values, cell outlines approximate")
        try:
            full = Window(0, 0, ds.width, ds.height)
            win = full if box is None else from_bounds(*box, transform=ds.transform)
            win = win.round_offsets().round_lengths().intersection(full)
            if win.width < 1 or win.height < 1:
                raise ValueError("outside the AOI")
            k = max(1, int(np.ceil(np.sqrt(win.width * win.height / MAX_CELLS))))
            shape = (max(1, int(win.height // k)), max(1, int(win.width // k)))
            if k > 1:
                notes.append(f"decimated ×{k} (nearest) to stay under {MAX_CELLS:,} cells: values are a sample")
            a = ds.read(1, window=win, out_shape=shape, masked=True, resampling=Resampling.nearest)
            t = ds.window_transform(win) * views_scale(win, shape)
        finally:
            if ds is not src:
                ds.close()
    vals = np.ma.filled(a.astype("float64"), np.nan)
    return vals, [t.c, t.a, t.f, t.e], dtype, notes


def views_scale(win, shape):
    from affine import Affine
    return Affine.scale(win.width / shape[1], win.height / shape[0])


def raster_layer(lid, role, path, box, visible, scale=None, classes=None):
    import numpy as np
    from matplotlib import colormaps
    from PIL import Image
    vals, tr, dtype, notes = read_raster(path, box)
    valid = ~np.isnan(vals)
    h, w = vals.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    if classes is None:
        u = np.unique(vals[valid]) if valid.any() else np.array([])
        classes = u.tolist() if np.issubdtype(np.dtype(dtype), np.integer) and len(u) <= views.CLASS_MAX else None
    if classes is not None:
        legend = []
        for i, v in enumerate(classes):
            c = (np.array(colormaps["tab10"](i % 10)) * 255).astype(np.uint8)
            rgba[valid & (vals == v)] = c
            legend.append({"value": v, "color": "#%02x%02x%02x" % tuple(int(x) for x in c[:3])})
        sc = {"type": "classes", "legend": legend}
    else:
        lo, hi = scale if scale else (np.percentile(vals[valid], [2, 98]) if valid.any() else (0.0, 1.0))
        z = np.clip((vals - lo) / ((hi - lo) or 1), 0, 1)
        rgba[valid] = (colormaps["viridis"](z[valid]) * 255).astype(np.uint8)
        sc = {"type": "p2-p98", "min": float(lo), "max": float(hi)}
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    grid_dtype = "float64" if dtype in EXACT64 else "float32"
    grid = vals.astype("<f8" if grid_dtype == "float64" else "<f4")
    x0, dx, y0, dy = tr
    return {"id": lid, "kind": "raster", "role": role, "visible": visible, "source": str(path),
            "png": _b64(buf.getvalue()), "width": w, "height": h, "transform": tr,
            "corners": [[x0, y0], [x0 + w * dx, y0], [x0 + w * dx, y0 + h * dy], [x0, y0 + h * dy]],
            "grid": _b64(grid.tobytes()), "dtype": grid_dtype, "source_dtype": dtype, "scale": sc,
            "valid_cells": int(valid.sum()), "exact": not notes, "exact_note": "; ".join(notes) or None}


def vector_layer(lid, role, path, layer, box, visible):
    g = views._read_vector(path, layer)
    if g.crs is None:
        raise ValueError("vector has no CRS")
    g = g.to_crs(4326)
    notes = []
    if box is not None:
        n = len(g)
        g = g.cx[box[0]:box[2], box[1]:box[3]]
        if len(g) < n:
            notes.append(f"{n - len(g)} of {n} features outside the AOI box left out")
    if len(g) > MAX_FEATURES:
        notes.append(f"first {MAX_FEATURES:,} of {len(g):,} features")
        g = g.iloc[:MAX_FEATURES]
    for c in g.columns:
        if c != "geometry" and str(g[c].dtype).startswith(("datetime", "timedelta")):
            g[c] = g[c].astype(str)
    return {"id": lid, "kind": "vector", "role": role, "visible": visible, "source": str(path),
            "geojson": json.loads(g.to_json(drop_id=True)), "count": len(g), "exact": True,
            "exact_note": "; ".join(notes) or None}


def _bounds4326(path: Path, kind: str, layer=None):
    if kind == "raster":
        import rasterio
        from rasterio.warp import transform_bounds
        with rasterio.open(path) as s:
            return list(transform_bounds(s.crs, "EPSG:4326", *s.bounds))
    return list(views._read_vector(path, layer).to_crs(4326).total_bounds)


def compared(run: Path):
    """[(dataset, family, legacy path, new path, layer)] of every compared raster/vector with both files found."""
    rm = views.report_map()
    graph = views._load(run / "snapshot" / "graph.json") or {}
    manifest = views._load(run / "manifest.json") or {}
    gds = {d["id"]: d for d in graph.get("datasets") or [] if isinstance(d, dict) and "id" in d}
    out, skipped = [], []
    cdir = run / "verify" / "compare"
    for p in sorted(cdir.glob("*.json")) if cdir.is_dir() else []:
        cmp = views._load(p)
        if not isinstance(cmp, dict):
            continue
        dsid = cmp.get("dataset_id") or cmp.get("dataset")
        fam = cmp.get("family") or (gds.get(dsid) or {}).get("kind")
        if fam not in ("raster", "vector"):
            continue
        L, N = rm.locate(run, cmp, graph, manifest)
        if L is None or N is None:
            skipped.append({"layer": dsid, "reason": "legacy or new file not found"})
            continue
        out.append((dsid, fam, Path(L), Path(N), (gds.get(dsid) or {}).get("layer")))
    return out, skipped


def build(migration: Path, run: Path | None) -> tuple[dict, int]:
    import numpy as np
    migration = Path(migration).resolve()
    if run is not None:
        run = Path(run).resolve()
        if views.migration_of(run) != migration:
            raise views.SetupError(f"{run} is not a run of {migration}")
        if not (run / "verify" / "compare").is_dir():
            raise views.SetupError("no compare results: run `compare.py run --run <dir>` first")
    try:
        inv, shown, _, legacy = views.inputs_datasets(migration)
    except views.SetupError:
        if run is None:
            raise
        inv, shown, legacy = {}, [], None                       # a run without an inventory: compared data only
    pairs, skipped = compared(run) if run is not None else ([], [])
    aoi_id, aoi, aoi_note = _aoi(inv, shown, legacy) if legacy else (None, None, None)
    if aoi is not None:
        box = _pad(list(aoi.to_crs(4326).total_bounds))
    else:
        bs = [_bounds4326(L, fam, lay) for _, fam, L, _, lay in pairs] or \
             [_bounds4326((legacy / d["legacy_path"]).resolve(), d["kind"], d.get("layer")) for d in shown]
        if not bs:
            raise views.SetupError("nothing to show: no inputs and no compared raster/vector")
        b = np.array(bs)
        box = _pad([b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()], 0.05)
        aoi_note = "no AOI: the extent of every layer shown"
    layers, rc = [], 0

    def add(fn, *a):
        nonlocal rc
        try:
            layers.append(fn(*a))
        except Exception as ex:                                # listed, exit 1, never silent
            skipped.append({"layer": a[0], "reason": f"{type(ex).__name__}: {ex}"[:300]})
            rc = 1

    compared_ids = {p[0] for p in pairs}
    for d in shown:
        if d["id"] in compared_ids:                          # a compared legacy raster is shown as "· legacy" below
            continue
        src = (legacy / d["legacy_path"]).resolve()
        vis = run is None or d["id"] == aoi_id
        lid = f"{d['id']} (input)"
        if d["kind"] == "raster":
            add(raster_layer, lid, "input", src, box, vis)
        else:
            add(vector_layer, lid, "input", src, d.get("layer"), box, vis)
    for dsid, fam, L, N, lay in pairs:
        if fam == "vector":
            add(vector_layer, f"{dsid} · legacy", "legacy", L, lay, None, True)
            add(vector_layer, f"{dsid} · new", "new", N, lay, None, True)
            continue
        try:                                                   # one shared scale (or class list) for both sides
            lv, _, ld, _ = read_raster(L, box)
            nv, _, _, _ = read_raster(N, box)
            both = np.concatenate([lv[~np.isnan(lv)], nv[~np.isnan(nv)]])
            u = np.unique(both)
            classes = u.tolist() if np.issubdtype(np.dtype(ld), np.integer) and len(u) <= views.CLASS_MAX else None
            scale = None if classes is not None or not both.size else tuple(np.percentile(both, [2, 98]))
        except Exception as ex:
            skipped.append({"layer": dsid, "reason": f"{type(ex).__name__}: {ex}"[:300]})
            rc = 1
            continue
        add(raster_layer, f"{dsid} · legacy", "legacy", L, box, True, scale, classes)
        add(raster_layer, f"{dsid} · new", "new", N, box, True, scale, classes)
    layers.sort(key=lambda l: l["kind"] != "raster")             # rasters under vectors; stable otherwise
    title = (f"{inv.get('project') or migration.parent.name} · " if inv else "") + \
        (f"run {run.name}" if run is not None else "legacy inputs")
    page = {"title": title, "aoi": box, "aoi_note": aoi_note,
            "caption": "Display only: verdicts come from compare.py; identify reads the values in this page."}
    out = (views.views_root(run, create=True) if run is not None else migration / "discover") / "mlview"
    if out.is_dir():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    data = ("window.AXIS_DATA = " + json.dumps({**page, "layers": layers}, allow_nan=False) + ";\n").encode()
    (out / "data.js").write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    (out / "index.html").write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA_SHA256__", sha),
                                    encoding="utf-8")
    for f in ("maplibre-gl.js", "maplibre-gl.css"):
        shutil.copyfile(VENDOR / f, out / f)
    st = views.server_state(migration)
    upath = views.url_path(migration, out / "index.html")
    doc = {"schema": "axis-migrate.mlview.v1", "title": title, "folder": out.relative_to(migration).as_posix(),
           "url_path": upath, "url": f"http://127.0.0.1:{st['port']}{upath}" if st else None,
           "data_sha256": sha, "data_bytes": len(data), "aoi": box, "aoi_note": aoi_note,
           "layers": [{k: l.get(k) for k in ("id", "kind", "role", "visible", "exact", "exact_note", "count",
                                              "width", "height", "valid_cells", "source")} for l in layers],
           "skipped": skipped}
    (out / "mlview.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc, rc


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    views.ensure_map_env(argv, __file__)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the MapLibre page of a migration (inputs) or a run (inputs + compared)")
    b.add_argument("migration")
    b.add_argument("--run")
    b.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        doc, rc = build(Path(a.migration), Path(a.run) if a.run else None)
    except views.SetupError as e:
        print(f"mlview.py: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(doc, indent=1, sort_keys=True))
    else:
        for l in doc["layers"]:
            print(f"{l['id']}: {l['kind']} {l['role']}" + ("" if l["exact"] else f" (not exact: {l['exact_note']})"))
        for s in doc["skipped"]:
            print(f"{s['layer']}: skipped ({s['reason']})")
        print(f"page: {doc['url'] or doc['url_path'] + '  (start.py first, then http://127.0.0.1:<port> + this)'}")
        print(f"data sha256 {doc['data_sha256']} · {doc['data_bytes'] / 1e6:.1f} MB · {doc['aoi_note']}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
