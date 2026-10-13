#!/usr/bin/env python3
"""map.py display — display layers for the report's swipe map (07 §9, DESIGN §4.7).

    axpy map.py display --run <run_dir> [--dataset ds]… [--json]

For every compared raster: legacy, new and diff PNGs **pre-warped to EPSG:3857 by nearest neighbour** at full
resolution (every native cell covers ≥ `native_cell_px` display pixels, so a one-cell stripe can never be dropped),
on ONE shared colour scale (p2/p98 of the valid values of both sides) and a symmetric diverging diff scale.
For every compared vector: GeoJSON in EPSG:4326 for legacy and new with their attributes.
Writes runs/<id>/verify/display/<ds>.{legacy,new,diff,triptych}.png|.geojson + display.json.

Display only. Verdicts are computed by compare.py on the native grid; nothing here feeds a verdict.
Exit: 0 written · 2 nothing to display / bad input · 70 harness error.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import axis_common as ac  # noqa: E402

GENERATOR = {"name": "axis-migrate.map.display", "version": "1.0.0"}
PALETTE = "viridis"
DIFF_PALETTE = "PuOr"
MAX_DIM = 8192
LEGACY_ONLY_RGBA = (33, 102, 172, 255)   # valid in legacy only
NEW_ONLY_RGBA = (215, 25, 28, 255)       # valid in new only


def _load(p: Path):
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def _resolve(cands: list[Path]) -> Path | None:
    for c in cands:
        if c is not None and c.exists():
            return c
    return None


def locate(run_dir: Path, cmp: dict, graph: dict | None, manifest: dict | None) -> tuple[Path | None, Path | None]:
    migration = run_dir.parents[1]
    project = migration.parent
    legacy_root = (migration / graph["project"]["legacy_root"]).resolve() if graph else None
    dsid = cmp.get("dataset_id") or cmp.get("dataset")
    lpath = (cmp.get("legacy") or {}).get("path")
    npath = (cmp.get("new") or {}).get("path")
    gds = {d["id"]: d for d in (graph or {}).get("datasets") or []}.get(dsid) or {}
    glp = (gds.get("legacy") or {}).get("path")
    mout = {o.get("dataset_id"): o for o in (manifest or {}).get("outputs") or []}.get(dsid) or {}
    L = _resolve([project / lpath if lpath else None, legacy_root / lpath if lpath and legacy_root else None,
                  legacy_root / glp if glp and legacy_root else None])
    N = _resolve([project / npath if npath else None, run_dir / npath if npath else None,
                  run_dir / "outputs" / npath if npath else None, run_dir / mout["path"] if mout.get("path") else None])
    return L, N


def _rgba_png(path: Path, rgba: np.ndarray):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(path, format="PNG", optimize=False, compress_level=6)


def _cmap(name: str):
    import matplotlib
    return matplotlib.colormaps[name]


def _colour(values: np.ndarray, valid: np.ndarray, vmin: float, vmax: float, cmap) -> np.ndarray:
    span = vmax - vmin if vmax > vmin else 1.0
    t = np.clip((values - vmin) / span, 0.0, 1.0)
    t = np.where(valid, t, 0.0)
    rgba = (cmap(t) * 255).round().astype(np.uint8)
    rgba[~valid] = (0, 0, 0, 0)
    return rgba


def raster_display(dsid: str, L: Path, N: Path, out_dir: Path, units: str) -> dict:
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.transform import from_origin

    dst_crs = "EPSG:3857"
    sides = {}
    for side, p in (("legacy", L), ("new", N)):
        with rasterio.open(p) as src:
            arr = src.read(1).astype("float64")
            mask = src.read_masks(1) > 0
            if src.nodata is not None and not (isinstance(src.nodata, float) and math.isnan(src.nodata)):
                mask &= arr != src.nodata
            mask &= np.isfinite(arr)
            # native cell size in 3857 along each axis at the raster centre
            b3857 = transform_bounds(src.crs, dst_crs, *src.bounds, densify_pts=21)
            cell_x = (b3857[2] - b3857[0]) / src.width
            cell_y = (b3857[3] - b3857[1]) / src.height
            sides[side] = {"arr": arr, "mask": mask, "crs": src.crs, "transform": src.transform,
                           "bounds3857": b3857, "cell": (cell_x, cell_y), "shape": (src.height, src.width)}
    native_crs = sides["legacy"]["crs"]
    crs_label = native_crs.to_string() if native_crs else "unknown CRS"
    bx0 = min(s["bounds3857"][0] for s in sides.values())
    by0 = min(s["bounds3857"][1] for s in sides.values())
    bx1 = max(s["bounds3857"][2] for s in sides.values())
    by1 = max(s["bounds3857"][3] for s in sides.values())
    min_cell = min(min(s["cell"]) for s in sides.values())
    native_cell_px = 2
    res = min_cell / native_cell_px
    while max((bx1 - bx0) / res, (by1 - by0) / res) > MAX_DIM and native_cell_px > 1:
        native_cell_px -= 1
        res = min_cell / native_cell_px
    width = int(math.ceil((bx1 - bx0) / res))
    height = int(math.ceil((by1 - by0) / res))
    dst_transform = from_origin(bx0, by0 + height * res, res, res)
    warped = {}
    for side, s in sides.items():
        src = np.where(s["mask"], s["arr"], np.nan)
        dst = np.full((height, width), np.nan, dtype="float64")
        reproject(source=src, destination=dst, src_transform=s["transform"], src_crs=s["crs"],
                  src_nodata=np.nan, dst_transform=dst_transform, dst_crs=dst_crs, dst_nodata=np.nan,
                  resampling=Resampling.nearest)
        warped[side] = dst
    both_vals = np.concatenate([sides[k]["arr"][sides[k]["mask"]] for k in sides])
    if both_vals.size:
        vmin, vmax = (float(x) for x in np.percentile(both_vals, [2, 98]))
        if vmax <= vmin:
            vmin, vmax = float(both_vals.min()), float(both_vals.max())
        if vmax <= vmin:
            vmax = vmin + 1.0
    else:
        vmin, vmax = 0.0, 1.0
    cm = _cmap(PALETTE)
    files = {}
    rgba = {}
    for side in ("legacy", "new"):
        v = warped[side]
        rgba[side] = _colour(v, np.isfinite(v), vmin, vmax, cm)
        files[side] = f"{dsid}.{side}.png"
        _rgba_png(out_dir / files[side], rgba[side])
    lv, nv = warped["legacy"], warped["new"]
    both = np.isfinite(lv) & np.isfinite(nv)
    d = np.where(both, nv - lv, 0.0)
    nz = both & (d != 0)
    lim = float(np.max(np.abs(d[nz]))) if nz.any() else 0.0
    dl = lim if lim > 0 else 1.0
    t = np.clip(0.5 + d / (2 * dl), 0, 1)
    drgba = (_cmap(DIFF_PALETTE)(t) * 255).round().astype(np.uint8)
    drgba[~nz] = (0, 0, 0, 0)
    drgba[np.isfinite(lv) & ~np.isfinite(nv)] = LEGACY_ONLY_RGBA
    drgba[~np.isfinite(lv) & np.isfinite(nv)] = NEW_ONLY_RGBA
    files["diff"] = f"{dsid}.diff.png"
    _rgba_png(out_dir / files["diff"], drgba)
    # static fallback: legacy | new | diff side by side, separated by a 4 px gap, on white
    gap = np.full((height, 4, 4), 255, dtype=np.uint8)
    white = lambda a: np.where(a[..., 3:4] == 0, np.uint8(255), a)  # noqa: E731
    tri = np.concatenate([white(rgba["legacy"]), gap, white(rgba["new"]), gap, white(drgba)], axis=1)
    tri[..., 3] = 255
    files["triptych"] = f"{dsid}.triptych.png"
    _rgba_png(out_dir / files["triptych"], tri)
    x0, y0 = bx0, by0 + height * res
    x1, y1 = bx0 + width * res, by0
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    corners = [tr.transform(x0, y0), tr.transform(x1, y0), tr.transform(x1, y1), tr.transform(x0, y1)]
    return {"dataset": dsid, "family": "raster", "crs": crs_label, "files": files,
            "bounds_3857": [x0, y1, x1, y0], "corners_lonlat": [[round(a, 10), round(b, 10)] for a, b in corners],
            "width": width, "height": height,
            "scale": {"min": vmin, "max": vmax, "palette": PALETTE, "units": units, "basis": "p2/p98 of both sides"},
            "diff_scale": {"min": -lim if lim else 0.0, "max": lim, "palette": DIFF_PALETTE, "units": units,
                           "zero": "transparent", "legacy_only_rgba": list(LEGACY_ONLY_RGBA),
                           "new_only_rgba": list(NEW_ONLY_RGBA)},
            "diff_cells_nonzero_px": int(nz.sum()), "resampling": "nearest", "native_cell_px": native_cell_px,
            "native_shape": {k: list(v["shape"]) for k, v in sides.items()}}


def _props(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if k == "geometry":
            continue
        if v is None or isinstance(v, (bool, int, str)):
            out[k] = v
        elif isinstance(v, float):
            out[k] = v if math.isfinite(v) else None
        elif hasattr(v, "item"):
            x = v.item()
            out[k] = x if not (isinstance(x, float) and not math.isfinite(x)) else None
        else:
            out[k] = str(v)
    return out


def vector_display(dsid: str, L: Path, N: Path, out_dir: Path, layers: tuple) -> dict:
    import geopandas as gpd
    from shapely.geometry import mapping
    files, bounds = {}, []
    crs_label = None
    for side, p, layer in (("legacy", L, layers[0]), ("new", N, layers[1])):
        g = gpd.read_file(p, layer=layer) if layer else gpd.read_file(p)
        crs_label = crs_label or (g.crs.to_string() if g.crs else "unknown CRS")
        g4 = g.to_crs("EPSG:4326") if g.crs else g
        feats = []
        for rec in g4.to_dict("records"):
            geom = rec.get("geometry")
            feats.append({"type": "Feature", "properties": {**_props(rec), "_side": side},
                          "geometry": mapping(geom) if geom is not None else None})
        fc = {"type": "FeatureCollection", "features": feats}
        files[side] = f"{dsid}.{side}.geojson"
        txt = json.dumps(fc, sort_keys=True, separators=(",", ":"), allow_nan=False,
                         default=lambda o: round(o, 9) if isinstance(o, float) else str(o))
        ac.atomic_write_text(out_dir / files[side], txt + "\n")
        if len(g4):
            bounds.append(g4.total_bounds.tolist())
    from pyproj import Transformer
    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    b = [min(x[0] for x in bounds), min(x[1] for x in bounds), max(x[2] for x in bounds), max(x[3] for x in bounds)] \
        if bounds else [0, 0, 0, 0]
    x0, y0 = tr.transform(b[0], b[1])
    x1, y1 = tr.transform(b[2], b[3])
    return {"dataset": dsid, "family": "vector", "crs": crs_label, "files": files, "bounds_4326": b,
            "bounds_3857": [x0, y0, x1, y1], "resampling": "nearest", "native_cell_px": None,
            "scale": {"min": None, "max": None, "palette": "outline", "units": ""}}


def display(run_dir: Path, datasets: list[str] | None = None) -> dict:
    run_dir = Path(run_dir).resolve()
    verify = run_dir / "verify"
    out_dir = verify / "display"
    graph = _load(run_dir / "snapshot" / "graph.json")
    manifest = _load(run_dir / "manifest.json")
    gds = {d["id"]: d for d in (graph or {}).get("datasets") or []}
    entries, skipped = [], []
    for p in sorted((verify / "compare").glob("*.json")):
        cmp = _load(p)
        if not isinstance(cmp, dict):
            continue
        dsid = cmp.get("dataset_id") or cmp.get("dataset")
        if datasets and dsid not in datasets:
            continue
        fam = cmp.get("family") or (gds.get(dsid) or {}).get("kind")
        L, N = locate(run_dir, cmp, graph, manifest)
        if L is None or N is None:
            skipped.append({"dataset": dsid, "reason": "legacy or new file not found"})
            continue
        units = str((gds.get(dsid) or {}).get("units") or ((gds.get(dsid) or {}).get("compare") or {}).get("units")
                    or "")
        try:
            if fam == "raster":
                entries.append(raster_display(dsid, L, N, out_dir, units))
            elif fam == "vector":
                layer = (cmp.get("legacy") or {}).get("layer") or ((gds.get(dsid) or {}).get("legacy") or {}).get(
                    "layer")
                nlayer = (cmp.get("new") or {}).get("layer") or layer
                entries.append(vector_display(dsid, L, N, out_dir, (layer, nlayer)))
            else:
                skipped.append({"dataset": dsid, "reason": f"family {fam} has no map layer"})
        except Exception as e:  # recorded, never silent
            skipped.append({"dataset": dsid, "reason": f"{type(e).__name__}: {e}"[:300]})
    doc = {"schema": "axis-migrate.display.v1", "generator": GENERATOR, "run_id": run_dir.name,
           "caption": "Display only: warped to Web Mercator, nearest neighbour. Verdicts are computed on the native "
                      "grid.",
           "datasets": sorted(entries, key=lambda e: e["dataset"]), "skipped": sorted(skipped,
                                                                                       key=lambda e: e["dataset"])}
    out_dir.mkdir(parents=True, exist_ok=True)
    ac.dump_json(out_dir / "display.json", doc)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Display layers for the report swipe map (display only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("display", help="write verify/display/ for a run")
    d.add_argument("--run", required=True, help="run directory (migration/runs/<id>)")
    d.add_argument("--dataset", action="append", default=None)
    d.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    run_dir = Path(a.run)
    if not (run_dir / "verify").is_dir():
        print(f"map.py: {run_dir}/verify not found", file=sys.stderr)
        return 2
    if (run_dir / "verify" / "evidence.json").exists():
        print("map.py: run already sealed; display layers must be written before seal.py", file=sys.stderr)
        return 2
    doc = display(run_dir, a.dataset)
    if a.json:
        print(json.dumps(doc, sort_keys=True, indent=1))
    else:
        for e in doc["datasets"]:
            print(f"{e['dataset']}: {e['family']} {', '.join(sorted(e['files'].values()))}")
        for s in doc["skipped"]:
            print(f"{s['dataset']}: skipped ({s['reason']})")
    return 0 if doc["datasets"] else 2


if __name__ == "__main__":
    sys.exit(main())
