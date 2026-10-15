#!/usr/bin/env python3
"""views.py — GeoLibre map views of a compared run (display only; nothing here feeds a verdict).

    python3 views.py run --run <run_dir> [--dataset ds]… [--json]
    python3 views.py url --run <run_dir> --dataset ds --view overview|worst|edges|nodata [--port N]
    python3 views.py url --run <run_dir> --dataset ds --cell worst|interior|edge [--port N]
    python3 views.py url --run <run_dir> --all [--port N] [--json]
    python3 views.py inputs <migration> [--json]                                  (discover, before compare)
    python3 views.py inputs-url <migration> --view inputs-overview|grid-<family> [--port N]
    python3 views.py inputs-url <migration> --dataset ds --cell <id> | --feature <id> [--port N]

`run` (re-executes itself in the map env from mapenv.py, so plain python3 or scripts/axpy both work) reads
verify/compare/<ds>.json and writes, per compared raster, under <run>/verify/views/<ds>/:
  legacy.tif, new.tif     cloud-optimised copies of the legacy reference (read-only; never written to) and the
                          new output, values and nodata untouched, overviews by nearest neighbour
  diff.tif                new − legacy (float64, NaN where either side is nodata) on the display grid
  validity.tif            uint8 on the display grid: 1 legacy only · 2 new only · 3 both (0 = neither, nodata)
  <view>.geolibre.json    one GeoLibre project per view: overview, worst, edges, nodata
and per compared vector: <ds>.legacy.geojson / <ds>.new.geojson (EPSG:4326, attributes kept; written by
report/map.py's vector_display) and the overview and worst projects. views.json indexes everything.

The display grid is the union of both grids when they share CRS and cell size and are offset by whole cells (exact,
no resampling); otherwise new is placed on the legacy grid by nearest neighbour and views.json says so.
Scales: legacy and new share one p2/p98 scale (verify/plot.py `_value_norm`, the rule of the plots and the swipe
map); diff uses a symmetric diverging scale ±max|d| (report/map.py's rule), PuOr reversed: orange = new larger,
purple = legacy larger.

Project URLs are origin-relative (`/data/<path under the migration>`): the bytes on disk do not depend on the
server port, so `project_sha256` in views.json is the sha of exactly what the server sends. Only the page URL
(`url`) carries the port: http://127.0.0.1:<port>/?url=<encoded absolute project URL>&panels=collapsed.

After seal (verify/ read-only), views go to <run>/amend/views/ instead (display only; never part of the evidence).

`inputs` (discover, no run yet) reads inventory.json (+ graph.json roles) and writes, always as a full deterministic
rebuild, under <migration>/discover/views/: per input raster and legacy intermediate/final raster <ds>/data.tif (COG
copy) and one cell-<id>.geolibre.json per cell to identify (max, an AOI-edge cell, one interior cell); per input
vector <ds>/data.geojson (EPSG:4326, attributes kept) and one feature-<id>.geolibre.json per feature to identify
(first, largest, central); the project inputs-overview (every input; legacy rasters present, hidden) and one
grid-<family> project per grid family at discover/map.py grid_corners' five windows (bookmarks) with every member's
cell edges. Index: <migration>/discover/views.json (same schema, `mode: inputs`). The legacy folder is only read.
Exit: run 0 every requested compared raster/vector has views · 1 some could not be built · 2 setup error.
      url 0 printed · 2 no such view / server not running.
      inputs 0 every input has views · 1 some could not be built (listed under skipped) · 2 setup error;
      inputs-url as url.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import stat
import sys

import tempfile
import urllib.parse
import uuid
from pathlib import Path

sys.dont_write_bytecode = True     # never write __pycache__ into the plugin folder
os.environ["GDAL_PAM_ENABLED"] = "NO"          # reading a legacy raster must never write an .aux.xml sidecar
os.environ.setdefault("MPLBACKEND", "Agg")

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(HERE))
import axis_common as ac  # noqa: E402

SCHEMA = "axis-migrate.map-views.v1"
GENERATOR = {"name": "axis-migrate.map.views", "version": "1.0.0"}
RASTER_VIEWS = ("overview", "worst", "edges", "nodata")
VECTOR_VIEWS = ("overview", "worst")
CAPTION = ("Display only. Verdicts are computed by compare.py on the native grid; nothing in these views feeds a "
           "verdict.")
NS = uuid.UUID("6d1d9a52-3a43-4c55-9a47-2b1f0e7c5a10")
COG_OPTS = {"driver": "COG", "COMPRESS": "DEFLATE", "BLOCKSIZE": "256", "OVERVIEW_RESAMPLING": "NEAREST",
            "RESAMPLING": "NEAREST", "NUM_THREADS": "1", "BIGTIFF": "IF_SAFER"}
PALETTE = "viridis"
DIFF_PALETTE = "puor"           # reversed: high (new larger) orange, low (legacy larger) purple — plot.py's convention
VALIDITY_PALETTE = "tab10"      # with rescale [0.5, 10.5]: 1 → blue (legacy), 2 → orange (new), 3 → green (both)
VALIDITY_RESCALE = [[0.5, 10.5]]
C_LEGACY, C_NEW, C_ZONES = "#2166ac", "#e66101", "#000000"
IDENTIFY_CELL_PX = 64           # a cell to identify spans ~64 px at its camera zoom (click target)
PLACE_CELL_PX = 16              # worst / edges cameras: ~48 cells across a 768 px pane
EDGE_BLOCK = 32
MAX_CELLS = 200_000_000
TILE_SIZE = 512                 # MapLibre zoom convention


# ---------------------------------------------------------------------------------------------------------------
# environment and shared modules
# ---------------------------------------------------------------------------------------------------------------
def ensure_map_env(argv: list[str], script: str | None = None) -> None:
    """Re-exec `script` (default: this file) in the map env when geolibre is not importable here (once)."""
    try:
        import geolibre  # noqa: F401
        import rasterio  # noqa: F401
        return
    except ImportError:
        pass
    if os.environ.get("AXIS_MAP_REEXEC"):
        print("map: geolibre/rasterio not importable even in the map env", file=sys.stderr)
        raise SystemExit(2)
    import mapenv
    try:
        py = mapenv.ensure()
    except mapenv.EnvError as e:
        print(f"map: {e}", file=sys.stderr)
        raise SystemExit(13)
    os.environ["AXIS_MAP_REEXEC"] = "1"
    os.execv(str(py), [str(py), str(Path(script or __file__).resolve()), *argv])


def _load_module(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def report_map():
    """report/map.py: file location rule (locate), GeoJSON writer (vector_display), attribute cleaning."""
    return _load_module("axis_report_map", PLUGIN / "skills" / "report" / "map.py")


def verify_plot():
    """verify/plot.py: raster side reader (RSide) and the shared p2/p98 scale (_value_norm)."""
    return _load_module("axis_verify_plot", PLUGIN / "skills" / "verify" / "plot.py")


# ---------------------------------------------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------------------------------------------
def migration_of(run_dir: Path) -> Path:
    run_dir = Path(run_dir).resolve()
    if run_dir.parent.name != "runs":
        raise SetupError(f"{run_dir} is not under <migration>/runs/")
    return run_dir.parents[1]


def is_sealed(run_dir: Path) -> bool:
    v = Path(run_dir) / "verify"
    return (v / "evidence.json").exists() or (v / "EVIDENCE_ID").exists() or (v.exists() and not os.access(v, os.W_OK))


def views_root(run_dir: Path, create: bool = False) -> Path:
    run_dir = Path(run_dir)
    if not is_sealed(run_dir):
        return run_dir / "verify" / "views"
    d = run_dir / "amend" / "views"
    if create and not d.is_dir():
        amend = run_dir / "amend"
        if not amend.is_dir():                      # the run dir is read-only: same rule as plot.py's _amend_dir
            mode = stat.S_IMODE(run_dir.stat().st_mode)
            if not mode & stat.S_IWUSR:
                os.chmod(run_dir, mode | stat.S_IWUSR)
            try:
                amend.mkdir(exist_ok=True)
            finally:
                os.chmod(run_dir, mode)
        d.mkdir(parents=True, exist_ok=True)
    return d


def find_index(run_dir: Path) -> Path | None:
    for c in (Path(run_dir) / "amend" / "views" / "views.json", Path(run_dir) / "verify" / "views" / "views.json"):
        if c.is_file():
            return c
    return None


def url_path(migration: Path, p: Path) -> str:
    rel = Path(p).resolve().relative_to(Path(migration).resolve()).as_posix()
    return "/data/" + urllib.parse.quote(rel, safe="/")


def page_url(port: int, upath: str) -> str:
    absu = f"http://127.0.0.1:{int(port)}{upath}"
    return f"http://127.0.0.1:{int(port)}/?url={urllib.parse.quote(absu, safe='')}&panels=collapsed"


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------------------------------------------
# raster helpers
# ---------------------------------------------------------------------------------------------------------------
def cog_copy(src: Path, dst: Path) -> None:
    import rasterio.shutil
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    if tmp.exists():
        tmp.unlink()
    rasterio.shutil.copy(str(src), str(tmp), **COG_OPTS)
    os.replace(tmp, dst)


def cog_write(dst: Path, arr, *, transform, crs, nodata) -> None:
    import rasterio
    with tempfile.TemporaryDirectory(prefix="axis-views-") as td:
        t = Path(td) / "a.tif"
        with rasterio.open(t, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1,
                           dtype=arr.dtype.name, crs=crs, transform=transform, nodata=nodata, tiled=True,
                           blockxsize=256, blockysize=256) as ds:
            ds.write(arr, 1)
        cog_copy(t, dst)


def _same_crs(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        from pyproj import CRS
        return CRS.from_user_input(a.to_wkt()).equals(CRS.from_user_input(b.to_wkt()))
    except Exception:
        return a == b


def display_grid(L, N):
    """(transform, shape, place_l, place_n, aligned, method). place_* = (row_off, col_off) of each side's array
    in the grid when aligned; None for new when it had to be warped."""
    from affine import Affine
    tl, tn = L.tr, N.tr
    aligned = (_same_crs(L.crs, N.crs) and abs(tl.b) < 1e-12 and abs(tl.d) < 1e-12 and abs(tn.b) < 1e-12
               and abs(tn.d) < 1e-12 and math.isclose(tl.a, tn.a, rel_tol=1e-9) and
               math.isclose(tl.e, tn.e, rel_tol=1e-9))
    if aligned:
        dc = (tn.c - tl.c) / tl.a
        dr = (tn.f - tl.f) / tl.e
        aligned = abs(dc - round(dc)) < 1e-6 and abs(dr - round(dr)) < 1e-6
    if aligned:
        dc, dr = int(round((tn.c - tl.c) / tl.a)), int(round((tn.f - tl.f) / tl.e))
        hl, wl = L.arr.shape
        hn, wn = N.arr.shape
        r0, c0 = min(0, dr), min(0, dc)
        r1, c1 = max(hl, dr + hn), max(wl, dc + wn)
        tr = tl * Affine.translation(c0, r0)
        return tr, (r1 - r0, c1 - c0), (-r0, -c0), (dr - r0, dc - c0), True, "union of both grids (exact)"
    # not aligned: legacy grid extended to cover new's footprint; new placed by nearest neighbour (display only)
    from rasterio.warp import transform_bounds
    from rasterio.transform import array_bounds
    hl, wl = L.arr.shape
    lb = array_bounds(hl, wl, tl)                                     # (west, south, east, north) in legacy CRS
    nb = array_bounds(N.arr.shape[0], N.arr.shape[1], tn)
    if not _same_crs(L.crs, N.crs) and L.crs is not None and N.crs is not None:
        nb = transform_bounds(N.crs, L.crs, *nb, densify_pts=21)
    cols0 = math.floor((min(lb[0], nb[0]) - tl.c) / tl.a)
    cols1 = math.ceil((max(lb[2], nb[2]) - tl.c) / tl.a)
    rows0 = math.floor((max(lb[3], nb[3]) - tl.f) / tl.e)
    rows1 = math.ceil((min(lb[1], nb[1]) - tl.f) / tl.e)
    rows0, cols0 = min(rows0, 0), min(cols0, 0)
    rows1, cols1 = max(rows1, hl), max(cols1, wl)
    tr = tl * Affine.translation(cols0, rows0)
    return tr, (rows1 - rows0, cols1 - cols0), (-rows0, -cols0), None, False, \
        "legacy grid extended; new placed by nearest neighbour (display only)"


def on_grid(side, shape, place, grid_tr, grid_crs):
    import numpy as np
    arr = np.full(shape, np.nan, dtype="float64")
    if place is not None:
        r, c = place
        h, w = side.arr.shape
        arr[r:r + h, c:c + w] = np.where(side.valid, side.arr, np.nan)
        return arr
    from rasterio.warp import Resampling, reproject
    reproject(source=np.where(side.valid, side.arr, np.nan), destination=arr, src_transform=side.tr,
              src_crs=side.crs or grid_crs, src_nodata=np.nan, dst_transform=grid_tr, dst_crs=grid_crs or side.crs,
              dst_nodata=np.nan, resampling=Resampling.nearest)
    return arr


def _transformer(crs):
    from pyproj import Transformer
    if crs is None:
        return None
    return Transformer.from_crs(crs.to_wkt(), "EPSG:4326", always_xy=True)


def lonlat(tr4326, x: float, y: float) -> tuple[float, float]:
    if tr4326 is None:
        return float(x), float(y)
    lo, la = tr4326.transform(x, y)
    return float(lo), float(la)


def zoom_for(tr4326, grid_tr, row: int, col: int, px: int) -> float:
    """MapLibre zoom at which one grid cell at (row, col) spans ~px pixels horizontally."""
    xa, ya = grid_tr * (col, row + 0.5)
    xb, yb = grid_tr * (col + 1, row + 0.5)
    la, _ = lonlat(tr4326, xa, ya)
    lb, _ = lonlat(tr4326, xb, yb)
    dlon = abs(lb - la) or 1e-9
    z = math.log2(px * 360.0 / (TILE_SIZE * dlon))
    return round(min(22.0, max(0.0, z)), 3)


def fit_zoom(bbox: list[float], width=1024, height=768, pad=40) -> tuple[list[float], float]:
    w, s, e, n = bbox
    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(max(-85.0, min(85.0, lat))) / 2)) / (2 * math.pi)  # noqa: E731
    fx = max((e - w) / 360.0, 1e-12)
    fy = max(abs(merc(n) - merc(s)), 1e-12)
    z = min(math.log2((width - 2 * pad) / (TILE_SIZE * fx)), math.log2((height - 2 * pad) / (TILE_SIZE * fy)))
    cy = (merc(n) + merc(s)) / 2
    lat = math.degrees(2 * math.atan(math.exp(cy * 2 * math.pi)) - math.pi / 2)
    return [round((w + e) / 2, 7), round(lat, 7)], round(min(22.0, max(0.0, z)), 3)


def _py(v, kind: str):
    if v is None:
        return None
    if kind in "iub":
        return int(v)
    f = float(v)
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------------------------------------------
# cell choice (deterministic)
# ---------------------------------------------------------------------------------------------------------------
def interior_cell(both):
    """A cell valid on both sides whose 8 neighbours are too, nearest the centroid of such cells (row-major ties)."""
    import numpy as np
    if not both.any():
        return None
    inner = both.copy()
    pad = np.pad(both, 1, constant_values=False)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            inner &= pad[1 + dr:1 + dr + both.shape[0], 1 + dc:1 + dc + both.shape[1]]
    cand = inner if inner.any() else both
    rr, cc = np.nonzero(cand)
    r0, c0 = rr.mean(), cc.mean()
    k = int(np.argmin((rr - r0) ** 2 + (cc - c0) ** 2))
    return int(rr[k]), int(cc[k])


def worst_cell(d, both):
    import numpy as np
    ad = np.where(both, np.abs(np.nan_to_num(d, nan=0.0)), -1.0)
    if not both.any():
        return None
    k = int(np.argmax(ad))
    r, c = divmod(k, d.shape[1])
    return (int(r), int(c)) if ad[r, c] > 0 else None


def densest_block(mask, block=EDGE_BLOCK):
    """(row, col) of the mask cell nearest the centre of the block×block window holding most mask cells."""
    import numpy as np
    if not mask.any():
        return None
    h, w = mask.shape
    bh, bw = math.ceil(h / block), math.ceil(w / block)
    padded = np.zeros((bh * block, bw * block), dtype="int64")
    padded[:h, :w] = mask
    sums = padded.reshape(bh, block, bw, block).sum(axis=(1, 3))
    k = int(np.argmax(sums))
    br, bc = divmod(k, bw)
    sub = mask[br * block:(br + 1) * block, bc * block:(bc + 1) * block]
    rr, cc = np.nonzero(sub)
    cy, cx = (sub.shape[0] - 1) / 2, (sub.shape[1] - 1) / 2
    j = int(np.argmin((rr - cy) ** 2 + (cc - cx) ** 2))
    return int(rr[j] + br * block), int(cc[j] + bc * block)


def boundary_cells(valid):
    import numpy as np
    pad = np.pad(valid, 1, constant_values=False)
    inner = valid.copy()
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        inner &= pad[1 + dr:1 + dr + valid.shape[0], 1 + dc:1 + dc + valid.shape[1]]
    return valid & ~inner


# ---------------------------------------------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------------------------------------------
def _stable_layer(layer: dict, key: str) -> dict:
    old = layer["id"]
    new = str(uuid.uuid5(NS, key))
    return json.loads(json.dumps(layer).replace(old, new))


def build_project(name: str, camera: dict, layers: list[dict], bookmarks: list[dict], key: str) -> dict:
    """layers: [{name, kind: cog|vector, url, visible, colormap?, rescale?, reversed?, style?}] bottom → top."""
    from geolibre import authoring as A, project as P
    pr = P.build_empty_project(name, center=camera["center"], zoom=camera["zoom"], basemap_url="")
    for spec in layers:
        if spec["kind"] == "cog":
            lay = P.cog_layer(spec["name"], spec["url"], colormap=spec["colormap"], rescale=spec["rescale"])
            if spec.get("reversed"):
                lay["metadata"]["rasterState"]["reversed"] = True
            lay["metadata"]["rasterState"]["nodata"] = "auto"
        else:
            lay = P.vector_layer(spec["name"], spec["url"], **spec.get("style", {}))
        lay = _stable_layer(lay, f"{key}/{spec['name']}")
        lay["visible"] = bool(spec.get("visible", True))
        A.add_layer(pr, lay)
    for i, b in enumerate(bookmarks):
        A.add_bookmark(pr, b["name"], center=b["center"], zoom=b["zoom"],
                       bookmark_id=str(uuid.uuid5(NS, f"{key}/bookmark/{i}/{b['name']}")), created_at=0)
    pr["basemapStyleUrl"] = ""
    pr["basemapVisible"] = False
    pr["interaction"] = {"identify": "all", "controls": {}}
    pr["metadata"] = {"axis-migrate": {"display_only": True, "caption": CAPTION}}
    return pr


def save_project(path: Path, pr: dict) -> str:
    from geolibre import authoring as A
    A.save_project(path, pr)
    os.chmod(path, 0o644)
    return ac.sha256_file(path)


def validate_project(path: Path, expect_urls: set[str] | None = None) -> list[str]:
    """Reload a project through geolibre (load_project, describe_project, layer_summary) and check the invariants
    the views rely on. Returns problems (empty = valid)."""
    from geolibre import authoring as A
    probs = []
    try:
        pr = A.load_project(path)
        desc = A.describe_project(pr)
    except Exception as e:
        return [f"{Path(path).name}: geolibre could not load it: {type(e).__name__}: {e}"]
    if pr.get("basemapStyleUrl") != "":
        probs.append("basemap is not off")
    if (pr.get("interaction") or {}).get("identify") != "all":
        probs.append("interaction.identify is not 'all'")
    if not pr.get("layers"):
        probs.append("no layers")
    ids = [lay.get("id") for lay in pr.get("layers") or []]
    if len(set(ids)) != len(ids):
        probs.append("duplicate layer ids")
    for lay in pr.get("layers") or []:
        try:
            A.layer_summary(lay) if hasattr(A, "layer_summary") else None
        except Exception as e:
            probs.append(f"layer {lay.get('name')}: {e}")
        u = (lay.get("source") or {}).get("url")
        if not isinstance(u, str) or not u.startswith("/data/"):
            probs.append(f"layer {lay.get('name')}: source url {u!r} is not origin-relative under /data/")
        elif expect_urls is not None and u not in expect_urls:
            probs.append(f"layer {lay.get('name')}: {u} not listed in views.json")
    mv = pr.get("mapView") or {}
    c = mv.get("center") or []
    if not (len(c) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c)
            and -180 <= c[0] <= 180 and -90 <= c[1] <= 90):
        probs.append(f"camera centre {c} invalid")
    if not isinstance(desc, dict):
        probs.append("describe_project returned no summary")
    return probs


# ---------------------------------------------------------------------------------------------------------------
# per dataset
# ---------------------------------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, run_dir: Path):
        self.run = Path(run_dir).resolve()
        self.migration = migration_of(self.run)
        self.run_id = self.run.name
        self.graph = _load(self.run / "snapshot" / "graph.json") or {}
        self.manifest = _load(self.run / "manifest.json") or {}
        self.datasets = {d["id"]: d for d in self.graph.get("datasets") or [] if isinstance(d, dict) and "id" in d}
        self.root = views_root(self.run, create=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self._context: dict[str, dict] = {}

    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.run).as_posix()

    def up(self, p: Path) -> str:
        return url_path(self.migration, p)

    def file_entry(self, p: Path) -> dict:
        return {"file": self.rel(p), "url_path": self.up(p), "sha256": ac.sha256_file(p), "bytes": p.stat().st_size}

    def zone_layer(self, dsid: str) -> dict | None:
        """GeoJSON of the dataset's declared zones (compare.zones 'ds.x:key'), written once per run."""
        z = ((self.datasets.get(dsid) or {}).get("compare") or {}).get("zones")
        if not z:
            return None
        zid = str(z).split(":", 1)[0]
        if zid in self._context:
            return self._context[zid]
        zd = self.datasets.get(zid) or {}
        lr = (self.graph.get("project") or {}).get("legacy_root")
        lp = (zd.get("legacy") or {}).get("path")
        if not (lr and lp) or not (self.migration / lr / lp).exists():
            self._context[zid] = None
            return None
        import geopandas as gpd
        rm = report_map()
        src = self.migration / lr / lp
        layer = (zd.get("legacy") or {}).get("layer")
        g = gpd.read_file(src, layer=layer) if layer else gpd.read_file(src)
        g4 = g.to_crs("EPSG:4326") if g.crs else g
        from shapely.geometry import mapping
        feats = [{"type": "Feature", "properties": rm._props(r),
                  "geometry": mapping(r["geometry"]) if r.get("geometry") is not None else None}
                 for r in g4.to_dict("records")]
        out = self.root / "_context" / f"{zid}.geojson"
        out.parent.mkdir(parents=True, exist_ok=True)
        ac.atomic_write_text(out, json.dumps({"type": "FeatureCollection", "features": feats}, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False) + "\n")
        self._context[zid] = {"dataset": zid, **self.file_entry(out)}
        return self._context[zid]


def _load(p: Path):
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def _cell_record(ctx_tr, grid_tr, r: int, c: int, L, N, validity, d, why: str) -> dict:
    import numpy as np
    x, y = grid_tr * (c + 0.5, r + 0.5)
    lon, lat = lonlat(ctx_tr, x, y)
    rec = {"why": why, "x": float(x), "y": float(y), "lon": round(lon, 9), "lat": round(lat, 9),
           "grid_row": r, "grid_col": c, "validity": int(validity[r, c])}
    for side, S, key in (("legacy", L, "row"), ("new", N, "new_row")):
        sc, sr = ~S.tr * (x, y)
        sr, sc = int(math.floor(sr)), int(math.floor(sc))
        inside = 0 <= sr < S.arr.shape[0] and 0 <= sc < S.arr.shape[1]
        if side == "legacy":
            rec["row"], rec["col"] = (sr, sc) if inside else (None, None)
        else:
            rec["new_row"], rec["new_col"] = (sr, sc) if inside else (None, None)
        kind = np.dtype(S.dtype).kind if S.dtype else "f"
        val = S.arr[sr, sc] if inside and S.valid[sr, sc] else None
        rec[f"{side}_value"] = _py(val, kind)
    dv = d[r, c]
    rec["diff"] = float(dv) if math.isfinite(dv) else None
    rec["camera"] = {"center": [round(lon, 9), round(lat, 9)],
                     "zoom": zoom_for(ctx_tr, grid_tr, r, c, IDENTIFY_CELL_PX)}
    return rec


def _plot_tiles(ctx: Ctx, dsid: str) -> list[dict]:
    doc = _load(ctx.run / "verify" / "plots" / dsid / "plots.json") or {}
    for e in doc.get("png") or doc.get("plots") or []:
        meta = e.get("meta") if isinstance(e.get("meta"), dict) else e
        if str(e.get("name", "")).startswith("worst_tiles") and isinstance(meta.get("tiles"), list):
            return [t for t in meta["tiles"] if isinstance(t, dict) and t.get("rows") and t.get("cols")]
    return []


def raster_views(ctx: Ctx, cmp: dict, dsid: str, Lp: Path, Np: Path, out: Path) -> dict:
    import numpy as np
    plot = verify_plot()
    L, N = plot.RSide(Lp, "legacy"), plot.RSide(Np, "new")
    for S in (L, N):
        if not S.ok:
            raise SetupError(f"{S.label} raster: {S.err}")
    gds = ctx.datasets.get(dsid) or {}
    cspec = gds.get("compare") or {}
    categorical = bool(cspec.get("categorical")) or cspec.get("measure") in ("categorical", "mask")
    norm = plot._value_norm(L, N, categorical)
    if norm["kind"] == "categorical":
        cl = norm["classes"]
        vmin, vmax = (cl[0] - 0.5, cl[-1] + 0.5) if len(cl) == 1 else (cl[0], cl[-1])
    else:
        vmin, vmax = norm["vmin"], norm["vmax"]
    gtr, shape, pl, pn, aligned, method = display_grid(L, N)
    if shape[0] * shape[1] > MAX_CELLS:
        raise SetupError(f"display grid {shape} exceeds {MAX_CELLS} cells")
    crs = L.crs or N.crs
    lg = on_grid(L, shape, pl, gtr, crs)
    ng = on_grid(N, shape, pn, gtr, crs)
    vl, vn = np.isfinite(lg), np.isfinite(ng)
    both = vl & vn
    validity = (vl & ~vn) * 1 + (vn & ~vl) * 2 + both * 3
    validity = validity.astype("uint8")
    d = np.where(both, ng - lg, np.nan)
    nz = both & (np.nan_to_num(d) != 0)
    lim = float(np.max(np.abs(d[nz]))) if nz.any() else 0.0
    dl = lim if lim > 0 else 1.0

    files = {"legacy": out / "legacy.tif", "new": out / "new.tif", "diff": out / "diff.tif",
             "validity": out / "validity.tif"}
    cog_copy(Lp, files["legacy"])
    cog_copy(Np, files["new"])
    cog_write(files["diff"], d.astype("float64"), transform=gtr, crs=crs, nodata=float("nan"))
    cog_write(files["validity"], validity, transform=gtr, crs=crs, nodata=0)
    fe = {k: ctx.file_entry(p) for k, p in files.items()}

    t4326 = _transformer(crs)
    # ---- places and cells (deterministic)
    cells: dict[str, dict] = {}
    wc = worst_cell(d, both)
    if wc:
        cells["worst"] = _cell_record(t4326, gtr, *wc, L, N, validity, d, "largest |new − legacy|")
    ic = interior_cell(both)
    if ic:
        cells["interior"] = _cell_record(t4326, gtr, *ic, L, N, validity, d,
                                         "valid interior cell nearest the centre of the common data")
    one_side = (validity == 1) | (validity == 2)
    ec = densest_block(one_side)
    if ec:
        cells["edge"] = _cell_record(t4326, gtr, *ec, L, N, validity, d,
                                     "valid on one side only, where such cells concentrate")
    edge_place = ec or densest_block(boundary_cells(vl | vn))
    # worst places: plot.py worst tiles (legacy grid) → compare samples → own max |d|
    places, src = [], None
    for t in _plot_tiles(ctx, dsid)[:4]:
        r = (t["rows"][0] + t["rows"][1]) / 2.0
        c = (t["cols"][0] + t["cols"][1]) / 2.0
        x, y = L.tr * (c, r)
        lon, lat = lonlat(t4326, x, y)
        places.append({"name": f"worst tile {t.get('tile', len(places) + 1)}", "center": [round(lon, 9),
                                                                                         round(lat, 9)]})
        src = "verify/plots worst_tiles.png"
    if not places:
        samples = []
        for chk in cmp.get("checks") or []:
            if str(chk.get("id", "")).startswith(("raster.values", "raster.mask")):
                for s in chk.get("samples") or []:
                    if isinstance(s, dict) and "x" in s and "y" in s:
                        dd = abs((s.get("new") or 0) - (s.get("legacy") or 0)) if s.get("legacy") is not None \
                            and s.get("new") is not None else 0.0
                        samples.append((-dd, int(s.get("row", 0)), int(s.get("col", 0)), s))
        for _, _, _, s in sorted(samples, key=lambda t: t[:3])[:4]:
            lon, lat = lonlat(t4326, s["x"], s["y"])
            places.append({"name": f"compare sample row {s.get('row')} col {s.get('col')}",
                           "center": [round(lon, 9), round(lat, 9)]})
            src = "verify/compare samples"
    if not places and wc:
        places.append({"name": "largest |new − legacy|", "center": cells["worst"]["camera"]["center"]})
        src = "views.py max |d|"
    # ---- cameras
    from rasterio.transform import array_bounds
    b = array_bounds(shape[0], shape[1], gtr)
    corners = [lonlat(t4326, x, y) for x, y in ((b[0], b[1]), (b[0], b[3]), (b[2], b[1]), (b[2], b[3]))]
    bbox = [min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners),
            max(p[1] for p in corners)]
    oc, oz = fit_zoom(bbox)
    cam = {"overview": {"center": oc, "zoom": oz, "source": "data extent"}}
    if wc:
        cam["worst"] = {"center": cells["worst"]["camera"]["center"],
                        "zoom": zoom_for(t4326, gtr, *wc, PLACE_CELL_PX), "source": "largest |new − legacy|"}
    elif places:
        cam["worst"] = {"center": places[0]["center"], "zoom": oz + 3, "source": src}
    else:
        cam["worst"] = {**cam["overview"], "source": "no differences: data extent"}
    if edge_place:
        x, y = gtr * (edge_place[1] + 0.5, edge_place[0] + 0.5)
        lon, lat = lonlat(t4326, x, y)
        cam["edges"] = {"center": [round(lon, 9), round(lat, 9)],
                        "zoom": zoom_for(t4326, gtr, *edge_place, PLACE_CELL_PX),
                        "source": "densest one-side-only cells" if ec else
                        "no one-side-only cells: densest data-extent edge"}
    else:
        cam["edges"] = {**cam["overview"], "source": "no valid cells: data extent"}
    cam["nodata"] = {**cam["overview"], "source": "data extent"}

    # ---- layers
    zone = ctx.zone_layer(dsid)
    rescale = [[float(vmin), float(vmax)]]
    base = [{"name": "legacy", "role": "legacy", "kind": "cog", "path": files["legacy"], "colormap": PALETTE,
             "rescale": rescale},
            {"name": "new", "role": "new", "kind": "cog", "path": files["new"], "colormap": PALETTE,
             "rescale": rescale},
            {"name": "diff (new − legacy)", "role": "diff", "kind": "cog", "path": files["diff"],
             "colormap": DIFF_PALETTE, "reversed": True, "rescale": [[-dl, dl]]},
            {"name": "validity (1 legacy only · 2 new only · 3 both)", "role": "validity", "kind": "cog",
             "path": files["validity"], "colormap": VALIDITY_PALETTE, "rescale": VALIDITY_RESCALE}]
    if zone:
        base.append({"name": f"zones ({zone['dataset']})", "role": "zones", "kind": "vector",
                     "path": ctx.run / zone["file"],
                     "style": {"fillOpacity": 0, "strokeColor": C_ZONES, "strokeWidth": 1.5}})
    visible = {"overview": {"legacy", "new", "zones"}, "worst": {"legacy", "new", "diff", "zones"},
               "edges": {"legacy", "new", "validity", "zones"}, "nodata": {"validity", "zones"}}
    view_cells = {"overview": ["interior"], "worst": ["worst", "interior"], "edges": ["edge", "interior"],
                  "nodata": ["edge", "interior"]}
    views = {}
    for v in RASTER_VIEWS:
        specs = []
        for lay in base:
            s = {k: lay[k] for k in ("name", "kind", "colormap", "rescale", "reversed", "style") if k in lay}
            s["url"] = ctx.up(lay["path"])
            s["visible"] = lay["role"] in visible[v]
            specs.append(s)
        cl = [dict(cells[k]) for k in view_cells[v] if k in cells]
        bms = [{"name": f"cell: {c['why']}", "center": c["camera"]["center"], "zoom": c["camera"]["zoom"]}
               for c in cl]
        if v == "worst":
            bms += [{"name": p["name"], "center": p["center"], "zoom": cam["worst"]["zoom"]} for p in places]
        ppath = out / f"{v}.geolibre.json"
        pr = build_project(f"{dsid} · {v}", cam[v], specs, bms, key=f"{ctx.run_id}/{dsid}/{v}")
        sha = save_project(ppath, pr)
        views[v] = {"project": ctx.rel(ppath), "project_sha256": sha, "url_path": ctx.up(ppath),
                    "camera": cam[v], "cells_to_identify": cl, "bookmarks": [b["name"] for b in bms],
                    "layers": [{"name": s["name"], "role": lay["role"], "kind": s["kind"], "visible": s["visible"],
                                "url_path": s["url"], "file": ctx.rel(lay["path"]),
                                "sha256": ac.sha256_file(lay["path"]),
                                **({"colormap": s["colormap"], "rescale": s["rescale"],
                                    "reversed": bool(s.get("reversed"))} if s["kind"] == "cog" else {})}
                               for s, lay in zip(specs, base)]}
    # one small project per cell to identify: it opens with the camera on the cell centre, so a click on the map
    # centre lands on that cell (the embed has no camera URL parameter)
    for k, c in sorted(cells.items()):
        specs = [{**{q: lay[q] for q in ("name", "kind", "colormap", "rescale", "reversed", "style") if q in lay},
                  "url": ctx.up(lay["path"]), "visible": True} for lay in base]     # the popup lists every visible layer
        ppath = out / f"cell-{k}.geolibre.json"
        pr = build_project(f"{dsid} · cell {k}", {"center": c["camera"]["center"], "zoom": c["camera"]["zoom"]},
                           specs, [], key=f"{ctx.run_id}/{dsid}/cell-{k}")
        c["project"], c["project_sha256"], c["url_path"] = ctx.rel(ppath), save_project(ppath, pr), ctx.up(ppath)
        c["id"] = k
        for ve in views.values():
            for vc in ve["cells_to_identify"]:
                if vc.get("why") == c["why"]:
                    vc.update(id=k, project=c["project"], project_sha256=c["project_sha256"], url_path=c["url_path"])
    counts = {str(k): int((validity == k).sum()) for k in (0, 1, 2, 3)}
    return {"dataset": dsid, "family": "raster", "verdict": cmp.get("verdict"),
            "sources": {"legacy": {"path": (cmp.get("legacy") or {}).get("path"), "sha256": ac.sha256_file(Lp)},
                        "new": {"path": (cmp.get("new") or {}).get("path"), "sha256": ac.sha256_file(Np)}},
            "files": fe, "grid": {"aligned": aligned, "method": method, "shape": list(shape),
                                  "transform": [gtr.a, gtr.b, gtr.c, gtr.d, gtr.e, gtr.f],
                                  "crs": crs.to_string() if crs else None,
                                  "legacy_offset": list(pl), "new_offset": list(pn) if pn else None},
            "band": 1, "bands": {"legacy": L.bands, "new": N.bands},
            "scale": {"min": float(vmin), "max": float(vmax), "palette": PALETTE, "kind": norm["kind"],
                      "basis": norm.get("source") or "categorical classes of both sides"},
            "diff_scale": {"min": -dl, "max": dl, "max_abs_diff": lim, "palette": DIFF_PALETTE + " reversed",
                           "meaning": "orange = new larger, purple = legacy larger, transparent = equal or nodata"},
            "validity_counts": counts, "bounds_4326": [round(x, 9) for x in bbox],
            "places": {"source": src, "items": places}, "views": views}


def vector_views(ctx: Ctx, cmp: dict, dsid: str, Lp: Path, Np: Path, out: Path) -> dict:
    rm = report_map()
    layer = (cmp.get("legacy") or {}).get("layer") or (((ctx.datasets.get(dsid) or {}).get("legacy") or {})
                                                       .get("layer"))
    nlayer = (cmp.get("new") or {}).get("layer") or layer
    disp = rm.vector_display(dsid, Lp, Np, out, (layer, nlayer))
    files = {k: out / v for k, v in disp["files"].items()}
    from shapely.geometry import shape as shp
    from shapely.ops import unary_union
    fcs = {k: json.loads(p.read_text()) for k, p in files.items()}
    geoms = {k: [shp(f["geometry"]) for f in fc["features"] if f.get("geometry")] for k, fc in fcs.items()}
    ul = unary_union(geoms["legacy"]) if geoms["legacy"] else None
    un = unary_union(geoms["new"]) if geoms["new"] else None
    bbox = disp["bounds_4326"]
    oc, oz = fit_zoom(bbox)
    cam = {"overview": {"center": oc, "zoom": oz, "source": "data extent"}}
    features = []
    piece = None
    if ul is not None and un is not None:
        sd = ul.symmetric_difference(un)
        parts = list(getattr(sd, "geoms", [sd])) if not sd.is_empty else []
        parts = [p for p in parts if not p.is_empty]
        if parts:
            piece = max(parts, key=lambda p: (p.area, p.length, -p.bounds[0], -p.bounds[1]))
    elif ul is not None or un is not None:
        piece = ul if ul is not None else un
    if piece is not None:
        w, s, e, n = piece.bounds
        dx, dy = max(e - w, 1e-6), max(n - s, 1e-6)
        c, z = fit_zoom([w - dx, s - dy, e + dx, n + dy])
        cam["worst"] = {"center": c, "zoom": z, "source": "largest piece of the symmetric difference"}
        for side in ("legacy", "new"):
            for f in fcs[side]["features"]:
                g = shp(f["geometry"]) if f.get("geometry") else None
                if g is not None and g.intersects(piece.buffer(max(dx, dy) * 0.01)):
                    pt = g.representative_point()
                    features.append({"why": "feature touching the largest difference", "side": side,
                                     "lon": round(pt.x, 9), "lat": round(pt.y, 9),
                                     "properties": {k: v for k, v in sorted(f["properties"].items())},
                                     "camera": {"center": [round(pt.x, 9), round(pt.y, 9)], "zoom": z}})
                    break
    else:
        cam["worst"] = {**cam["overview"], "source": "no geometric difference: data extent"}
    specs = [{"name": "legacy", "role": "legacy", "kind": "vector", "path": files["legacy"],
              "style": {"fillColor": C_LEGACY, "fillOpacity": 0.15, "strokeColor": C_LEGACY, "strokeWidth": 2}},
             {"name": "new", "role": "new", "kind": "vector", "path": files["new"],
              "style": {"fillColor": C_NEW, "fillOpacity": 0.0, "strokeColor": C_NEW, "strokeWidth": 1.5}}]
    views = {}
    for v in VECTOR_VIEWS:
        ls = [{"name": s["name"], "kind": "vector", "url": ctx.up(s["path"]), "visible": True, "style": s["style"]}
              for s in specs]
        bms = [{"name": f"feature: {f['side']}", "center": f["camera"]["center"], "zoom": f["camera"]["zoom"]}
               for f in features] if v == "worst" else []
        ppath = out / f"{v}.geolibre.json"
        pr = build_project(f"{dsid} · {v}", cam[v], ls, bms, key=f"{ctx.run_id}/{dsid}/{v}")
        sha = save_project(ppath, pr)
        views[v] = {"project": ctx.rel(ppath), "project_sha256": sha, "url_path": ctx.up(ppath), "camera": cam[v],
                    "cells_to_identify": [], "features_to_identify": features if v == "worst" else [],
                    "bookmarks": [b["name"] for b in bms],
                    "layers": [{"name": s["name"], "role": s["role"], "kind": "vector", "visible": True,
                                "url_path": ctx.up(s["path"]), "file": ctx.rel(s["path"]),
                                "sha256": ac.sha256_file(s["path"])} for s in specs]}
    return {"dataset": dsid, "family": "vector", "verdict": cmp.get("verdict"),
            "sources": {"legacy": {"path": (cmp.get("legacy") or {}).get("path"), "sha256": ac.sha256_file(Lp)
                                   if Lp.is_file() else ac.tree_hash(Lp)},
                        "new": {"path": (cmp.get("new") or {}).get("path"), "sha256": ac.sha256_file(Np)
                                if Np.is_file() else ac.tree_hash(Np)}},
            "files": {k: ctx.file_entry(p) for k, p in files.items()}, "crs": disp.get("crs"),
            "bounds_4326": bbox, "views": views}


def build_views(run_dir: Path, datasets: list[str] | None = None) -> tuple[dict, int]:
    ctx = Ctx(run_dir)
    rm = report_map()
    idx_path = ctx.root / "views.json"
    prev = _load(idx_path) or {}
    entries = {e["dataset"]: e for e in prev.get("datasets") or [] if isinstance(e, dict)} if datasets else {}
    skipped = {s["dataset"]: s for s in prev.get("skipped") or [] if isinstance(s, dict)} if datasets else {}
    rc = 0
    cdir = ctx.run / "verify" / "compare"
    found = set()
    for p in sorted(cdir.glob("*.json")) if cdir.is_dir() else []:
        cmp = _load(p)
        if not isinstance(cmp, dict):
            continue
        dsid = cmp.get("dataset_id") or cmp.get("dataset")
        if not dsid or (datasets and dsid not in datasets):
            continue
        found.add(dsid)
        fam = cmp.get("family") or (ctx.datasets.get(dsid) or {}).get("kind")
        entries.pop(dsid, None)
        skipped.pop(dsid, None)
        if fam not in ("raster", "vector"):
            skipped[dsid] = {"dataset": dsid, "reason": f"family {fam} has no map view"}
            continue
        L, N = rm.locate(ctx.run, cmp, ctx.graph, ctx.manifest)
        if L is None or N is None:
            skipped[dsid] = {"dataset": dsid, "reason": "legacy or new file not found"}
            rc = 1
            continue
        out = ctx.root / dsid
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        try:
            e = raster_views(ctx, cmp, dsid, L, N, out) if fam == "raster" else vector_views(ctx, cmp, dsid, L, N,
                                                                                            out)
            entries[dsid] = e
        except Exception as ex:                              # recorded per dataset, exit 1, never silent
            skipped[dsid] = {"dataset": dsid, "reason": f"{type(ex).__name__}: {ex}"[:300]}
            rc = 1
    for m in sorted(set(datasets or []) - found):
        skipped[m] = {"dataset": m, "reason": "no compare result"}
        rc = 1
    doc = {"schema": SCHEMA, "generator": GENERATOR, "run_id": ctx.run_id, "caption": CAPTION,
           "root": ctx.rel(ctx.root), "sealed_run": is_sealed(ctx.run),
           "served_as": "/data/ = the migration folder (start.py); project and layer URLs are origin-relative",
           "page_url": "http://127.0.0.1:<port>/?url=<urlencoded http://127.0.0.1:<port><url_path>>&panels=collapsed",
           "datasets": sorted(entries.values(), key=lambda e: e["dataset"]),
           "skipped": sorted(skipped.values(), key=lambda s: s["dataset"])}
    ac.dump_json(idx_path, doc)
    return doc, rc


# ---------------------------------------------------------------------------------------------------------------
# inputs mode (discover, before compare): the legacy inputs and legacy rasters as they are; display only
# ---------------------------------------------------------------------------------------------------------------
INPUTS_ROOT = "discover/views"                  # projects and layer files (relative to the migration folder)
INPUTS_INDEX = "discover/views.json"
OVERVIEW = "inputs-overview"
INPUTS_CAPTION = ("Display only. Legacy inputs and legacy rasters as they are (cloud-optimised copies; the legacy "
                  "folder is only read). A finding's numbers come from the inventory, the profiles or analysis code.")
CLASS_MAX = 12                  # integer rasters with at most this many distinct values use min/max, not p2/p98
MAX_FEATURES_TO_IDENTIFY = 3
GRID_WINDOW_CELLS = 14          # discover/map.py grid_corners: 14×14-cell windows
GRID_CELL_PX = 48               # a grid window spans ~670 px
LINE_COLOURS = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"]


def _pyval(v):
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def inputs_datasets(migration: Path) -> tuple[dict, list[dict], list[dict], Path]:
    """(inventory, [dataset to show], [skipped], legacy root). Shown: every input raster and vector and every legacy
    intermediate/final raster that exists; roles from graph.json when it exists (the graph may correct a role),
    else from inventory.json."""
    migration = Path(migration)
    inv = _load(migration / "inventory.json")
    if not isinstance(inv, dict) or not inv.get("legacy_root"):
        raise SetupError("no inventory.json: run discover's scan.py first")
    legacy = (migration / inv["legacy_root"]).resolve()
    graph = _load(migration / "graph.json") or {}
    groles = {d["id"]: d.get("role") for d in graph.get("datasets") or [] if isinstance(d, dict) and "id" in d}
    keep, skipped = [], []
    for d in sorted(inv.get("datasets") or [], key=lambda x: x.get("id", "")):
        role = groles.get(d.get("id"), d.get("role"))
        legacy_raster = d.get("kind") == "raster" and role in ("intermediate", "final")
        if role != "input" and not legacy_raster:
            continue
        if d.get("kind") not in ("raster", "vector"):
            skipped.append({"dataset": d["id"], "reason": f"{d.get('kind')}: no map view"})
        elif not d.get("exists") or not d.get("legacy_path"):
            skipped.append({"dataset": d["id"], "reason": "file not found in the legacy folder"})
        else:
            keep.append({**d, "role": role})
    return inv, keep, skipped, legacy


class ICtx:
    """Paths of the inputs views: everything under <migration>/discover/views, relative to the migration."""

    def __init__(self, migration: Path, legacy: Path):
        self.migration = Path(migration).resolve()
        self.legacy = Path(legacy).resolve()
        self.root = self.migration / INPUTS_ROOT
        if self.root == self.legacy or self.legacy in self.root.parents or self.root in self.legacy.parents:
            raise SetupError(f"{self.root} overlaps the legacy folder {self.legacy} (the legacy folder is read-only)")

    def rel(self, p: Path) -> str:
        return Path(p).resolve().relative_to(self.migration).as_posix()

    def up(self, p: Path) -> str:
        return url_path(self.migration, p)

    def file_entry(self, p: Path) -> dict:
        return {"file": self.rel(p), "url_path": self.up(p), "sha256": ac.sha256_file(p), "bytes": p.stat().st_size}

    def save(self, path: Path, name: str, camera: dict, specs: list[dict], bookmarks: list[dict]) -> dict:
        """specs: [{name, role, kind, path, visible, colormap?, rescale?, style?}] bottom → top."""
        layers = [{**{k: s[k] for k in ("name", "kind", "colormap", "rescale", "style") if k in s},
                   "url": self.up(s["path"]), "visible": s["visible"]} for s in specs]
        pr = build_project(name, camera, layers, bookmarks, key=f"inputs/{self.rel(path)}")
        pr["metadata"]["axis-migrate"]["caption"] = INPUTS_CAPTION
        path.parent.mkdir(parents=True, exist_ok=True)
        sha = save_project(path, pr)
        rows = []
        for s in specs:
            row = {"name": s["name"], "role": s["role"], "kind": s["kind"], "visible": s["visible"],
                   "url_path": self.up(s["path"]), "file": self.rel(s["path"]), "sha256": ac.sha256_file(s["path"])}
            if s.get("dataset"):
                row["dataset"] = s["dataset"]
            if s["kind"] == "cog":
                row.update(colormap=s["colormap"], rescale=s["rescale"])
            rows.append(row)
        return {"project": self.rel(path), "project_sha256": sha, "url_path": self.up(path), "camera": camera,
                "bookmarks": [b["name"] for b in bookmarks], "layers": rows}


def _source_sha(p: Path) -> str:
    return ac.sha256_file(p) if p.is_file() else ac.tree_hash(p)


def _read_vector(src: Path, layer: str | None):
    import pyogrio
    kw = {"IMMUTABLE": "YES"} if src.suffix.lower() in (".gpkg", ".sqlite") else {}   # never a -wal/-shm beside it
    return pyogrio.read_dataframe(src, layer=layer, **kw) if layer else pyogrio.read_dataframe(src, **kw)


def _erode(mask, outside: bool):
    """Cells of `mask` whose four neighbours are in `mask` too (cells beyond the array count as `outside`)."""
    import numpy as np
    h, w = mask.shape
    pad = np.pad(mask, 1, constant_values=outside)
    inner = mask.copy()
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        inner &= pad[1 + dr:1 + dr + h, 1 + dc:1 + dc + w]
    return inner


def inputs_raster(ctx: ICtx, d: dict, src: Path, out: Path, aoi) -> dict:
    """COG copy, display scale and the cells to identify (largest value, an AOI-edge cell, one interior cell), each
    with a one-layer cell project centred on it."""
    import numpy as np
    plot = verify_plot()
    S = plot.RSide(src, "input")
    if not S.ok:
        raise SetupError(f"raster: {S.err}")
    if S.crs is None:
        raise SetupError("no CRS: it cannot be placed on a map")
    h, w = S.arr.shape
    if h * w > MAX_CELLS:
        raise SetupError(f"raster {S.arr.shape} exceeds {MAX_CELLS} cells")
    kind = np.dtype(S.dtype).kind if S.dtype else "f"
    valid = S.valid
    n_valid = int(valid.sum())
    vals = S.arr[valid]
    if vals.size and kind in "iub" and np.unique(vals).size <= CLASS_MAX:
        vmin, vmax, basis = float(vals.min()), float(vals.max()), "min/max of the integer classes"
    elif vals.size:
        vmin, vmax = (float(x) for x in np.percentile(vals, [2, 98]))
        basis = "p2/p98 of the valid cells"
    else:
        vmin, vmax, basis = 0.0, 1.0, "no valid cells"
    if not vmax > vmin:
        vmax = vmin + 1.0
    data = out / "data.tif"
    cog_copy(src, data)
    t4326 = _transformer(S.crs)

    picks = []
    if n_valid:
        picks.append(("max", "largest value (first in row order)",
                      divmod(int(np.argmax(np.where(valid, S.arr, -np.inf))), w)))
    edge, edge_why = None, None
    if aoi is not None and n_valid:
        try:
            from rasterio.features import geometry_mask
            g = aoi.to_crs(S.crs.to_wkt()) if not aoi.crs.equals(S.crs.to_wkt()) else aoi
            geoms = [x for x in g.geometry if x is not None and not x.is_empty]
            inside = geometry_mask(geoms, out_shape=(h, w), transform=S.tr, invert=True) if geoms else None
        except Exception:
            inside = None
        if inside is not None and inside.any():
            ring = inside & ~_erode(inside, True)
            edge = densest_block(ring & valid) or densest_block(ring)
            edge_why = "cell on the AOI boundary (cell centre inside the AOI), where such cells concentrate"
    if edge is None and n_valid:
        edge = densest_block(valid & ~_erode(valid, False))
        edge_why = "valid cell on the edge of the valid data (next to NoData or the raster edge)"
    if edge is not None:
        picks.append(("aoi-edge" if edge_why.startswith("cell on the AOI") else "edge", edge_why, edge))
    picks.append(("interior", "valid interior cell nearest the centre of the valid cells", interior_cell(valid)))
    cells, taken = [], set()
    for cid, why, rc in picks:
        if rc is None or tuple(rc) in taken:
            continue
        taken.add(tuple(rc))
        r, c = int(rc[0]), int(rc[1])
        x, y = S.tr * (c + 0.5, r + 0.5)
        lon, lat = lonlat(t4326, x, y)
        cam = {"center": [round(lon, 9), round(lat, 9)], "zoom": zoom_for(t4326, S.tr, r, c, IDENTIFY_CELL_PX)}
        cells.append({"id": cid, "why": why, "row": r, "col": c, "x": float(x), "y": float(y),
                      "lon": round(lon, 9), "lat": round(lat, 9),
                      "value": _py(S.arr[r, c], kind) if valid[r, c] else None, "camera": cam})
    spec = {"name": d["id"], "dataset": d["id"], "role": d["role"], "kind": "cog", "path": data,
            "colormap": PALETTE, "rescale": [[vmin, vmax]]}
    for c in cells:              # one-layer project per cell: camera on its centre, so a centre click identifies it
        c.update({k: v for k, v in ctx.save(out / f"cell-{c['id']}.geolibre.json", f"{d['id']} · cell {c['id']}",
                                            c["camera"], [{**spec, "visible": True}], []).items()
                  if k in ("project", "project_sha256", "url_path")})
    from rasterio.transform import array_bounds
    b = array_bounds(h, w, S.tr)
    corners = [lonlat(t4326, x, y) for x, y in ((b[0], b[1]), (b[0], b[3]), (b[2], b[1]), (b[2], b[3]))]
    bbox = [min(p[0] for p in corners), min(p[1] for p in corners), max(p[0] for p in corners),
            max(p[1] for p in corners)]
    return {"entry": {"family": "raster", "files": {"data": ctx.file_entry(data)}, "crs": S.crs.to_string(),
                      "bounds_4326": [round(x, 9) for x in bbox],
                      "raster": {"band": 1, "bands": S.bands, "dtype": str(S.dtype),
                                 "nodata": _py(S.nodata, "f") if S.nodata is not None else None, "shape": [h, w],
                                 "transform": [S.tr.a, S.tr.b, S.tr.c, S.tr.d, S.tr.e, S.tr.f],
                                 "counts": {"valid": n_valid, "nodata": int(h * w - n_valid)}},
                      "scale": {"min": vmin, "max": vmax, "palette": PALETTE, "basis": basis},
                      "cells_to_identify": cells, "features_to_identify": [],
                      "nothing_to_identify": None if cells else "no valid cells"},
            "spec": spec, "tr": S.tr, "crs": S.crs}


def inputs_vector(ctx: ICtx, d: dict, src: Path, out: Path, colour: str) -> dict:
    """GeoJSON (EPSG:4326, attributes kept) and the features to identify (first, largest, central), each with a
    one-layer feature project centred on a point inside it."""
    from shapely.geometry import mapping
    rm = report_map()
    g = _read_vector(src, d.get("layer"))
    if g.crs is None:
        raise SetupError("no CRS: it cannot be placed on a map")
    g4 = g.to_crs("EPSG:4326")
    feats = []
    for rec in g4.to_dict("records"):
        geom = rec.get("geometry")
        feats.append({"type": "Feature", "properties": rm._props(rec),
                      "geometry": mapping(geom) if geom is not None and not geom.is_empty else None})
    data = out / "data.geojson"
    ac.atomic_write_text(data, json.dumps({"type": "FeatureCollection", "features": feats}, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False,
                                          default=lambda o: round(o, 9) if isinstance(o, float) else str(o)) + "\n")
    geoms = list(g4.geometry)
    ok = [i for i, gg in enumerate(geoms) if gg is not None and not gg.is_empty]
    bbox = [float(x) for x in g4.geometry.iloc[ok].total_bounds.tolist()] if ok else None
    picks = []
    if ok:
        picks.append(("first", "first feature in file order", ok[0]))
        size = max((geoms[i].area or geoms[i].length, -i) for i in ok)
        if size[0] > 0:
            picks.append(("largest", "largest feature (area, else length)", -size[1]))
        cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        reps = {i: geoms[i].representative_point() for i in ok}
        picks.append(("central", "feature nearest the centre of the layer extent",
                      min(ok, key=lambda i: ((reps[i].x - cx) ** 2 + (reps[i].y - cy) ** 2, i))))
    spec = {"name": d["id"], "dataset": d["id"], "role": d["role"], "kind": "vector", "path": data,
            "style": {"fillColor": colour, "fillOpacity": 0.15, "strokeColor": colour, "strokeWidth": 1.5}}
    features, taken = [], set()
    for fid, why, i in picks:
        if i in taken or len(features) >= MAX_FEATURES_TO_IDENTIFY:
            continue
        taken.add(i)
        gg = geoms[i]
        pt = gg.representative_point()
        w, s, e, n = gg.bounds
        dx, dy = max(e - w, 1e-5), max(n - s, 1e-5)
        _, z = fit_zoom([w - dx, s - dy, e + dx, n + dy])
        f = {"id": fid, "why": why, "index": i, "lon": round(pt.x, 9), "lat": round(pt.y, 9),
             "geometry_type": gg.geom_type,
             "properties": {k: _pyval(v) for k, v in sorted(feats[i]["properties"].items())},
             "camera": {"center": [round(pt.x, 9), round(pt.y, 9)], "zoom": z}}
        f.update({k: v for k, v in ctx.save(out / f"feature-{fid}.geolibre.json", f"{d['id']} · feature {fid}",
                                            f["camera"], [{**spec, "visible": True}], []).items()
                  if k in ("project", "project_sha256", "url_path")})
        features.append(f)
    return {"entry": {"family": "vector", "files": {"data": ctx.file_entry(data)}, "crs": g.crs.to_string(),
                      "bounds_4326": [round(x, 9) for x in bbox] if bbox else None,
                      "vector": {"features": len(g4), "with_geometry": len(ok), "layer": d.get("layer"),
                                 "attributes": sorted(c for c in g4.columns if c != "geometry")},
                      "cells_to_identify": [], "features_to_identify": features,
                      "nothing_to_identify": None if features else "no features with a geometry"},
            "spec": spec, "gdf": g}


def grid_windows(ref_tr, ref_crs, shape, aoi) -> tuple[list[tuple[str, int, int]], str]:
    """discover/map.py grid_corners' five windows: the four extreme points of the AOI and one point inside it, else
    the corners and the centre of the reference raster. → [(name, col, row)], description."""
    import numpy as np
    H, W = shape
    if aoi is not None and aoi.crs is not None and ref_crs is not None:
        import shapely
        g = aoi.to_crs(ref_crs.to_wkt()) if not aoi.crs.equals(ref_crs.to_wkt()) else aoi
        xy = shapely.get_coordinates(g.geometry.values)
        if len(xy):
            inv_t = ~ref_tr
            ext = {"west": xy[np.argmin(xy[:, 0])], "north": xy[np.argmax(xy[:, 1])],
                   "east": xy[np.argmax(xy[:, 0])], "south": xy[np.argmin(xy[:, 1])]}
            pts = []
            for name in ("west", "north", "east", "south"):
                cc, rr = inv_t * (float(ext[name][0]), float(ext[name][1]))
                pts.append((f"{name} extreme of AOI", int(cc), int(rr)))
            cen = shapely.union_all(g.geometry.values).representative_point()
            cc, rr = inv_t * (cen.x, cen.y)
            pts.append(("inside AOI", int(cc), int(rr)))
            return pts, "four extreme points of the AOI and one point inside"
    return ([("top-left", 0, 0), ("top-right", W - 1, 0), ("bottom-left", 0, H - 1), ("bottom-right", W - 1, H - 1),
             ("centre", W // 2, H // 2)], "corners and centre of the reference raster")


def grid_edges(ctx: ICtx, path: Path, member_tr, ref_tr, crs, windows) -> None:
    """GeoJSON of one member's cell edges inside each window (the lines grid_corners draws), in EPSG:4326."""
    t4326 = _transformer(crs)
    half = GRID_WINDOW_CELLS // 2
    feats = []
    for name, cc, rr in windows:
        x0, y0 = ref_tr * (cc - half, rr - half)
        x1, y1 = ref_tr * (cc - half + GRID_WINDOW_CELLS, rr - half + GRID_WINDOW_CELLS)
        xa, xb, ya, yb = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
        k0, k1 = math.floor((xa - member_tr.c) / member_tr.a) - 1, math.ceil((xb - member_tr.c) / member_tr.a) + 1
        for k in range(min(k0, k1), max(k0, k1) + 1):
            x = member_tr.c + k * member_tr.a
            if xa <= x <= xb:
                feats.append([name, "column edge", [lonlat(t4326, x, ya), lonlat(t4326, x, yb)]])
        j0, j1 = math.floor((yb - member_tr.f) / member_tr.e) - 1, math.ceil((ya - member_tr.f) / member_tr.e) + 1
        for j in range(min(j0, j1), max(j0, j1) + 1):
            y = member_tr.f + j * member_tr.e
            if ya <= y <= yb:
                feats.append([name, "row edge", [lonlat(t4326, xa, y), lonlat(t4326, xb, y)]])
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"window": n, "edge": k},
         "geometry": {"type": "LineString", "coordinates": [[round(a, 9), round(b, 9)] for a, b in pts]}}
        for n, k, pts in feats]}
    path.parent.mkdir(parents=True, exist_ok=True)
    ac.atomic_write_text(path, json.dumps(fc, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")


def build_inputs_views(migration: Path) -> tuple[dict, int]:
    """Write <migration>/discover/views/** and <migration>/discover/views.json (always a full, deterministic
    rebuild). Reads the legacy folder only (GDAL_PAM_ENABLED=NO, GeoPackages opened IMMUTABLE); writes nothing
    outside <migration>/discover/views and the index."""
    migration = Path(migration).resolve()
    inv, shown, skipped, legacy = inputs_datasets(migration)
    ctx = ICtx(migration, legacy)
    if ctx.root.is_dir():
        shutil.rmtree(ctx.root)                                  # nothing stale survives a rebuild
    ctx.root.mkdir(parents=True)
    rc = 0
    aoi_id = inv.get("aoi")
    aoi = None
    if aoi_id:
        ad = next((d for d in inv.get("datasets") or [] if d.get("id") == aoi_id), None)
        try:
            aoi = _read_vector((legacy / ad["legacy_path"]).resolve(), ad.get("layer")) if ad else None
            aoi = aoi if aoi is not None and aoi.crs is not None and len(aoi) else None
        except Exception:
            aoi = None
    entries, built = {}, {}
    vi = 0
    for d in shown:
        dsid = d["id"]
        src = (legacy / d["legacy_path"]).resolve()
        out = ctx.root / dsid
        out.mkdir(parents=True)
        try:
            if legacy not in src.parents:
                raise SetupError(f"{d['legacy_path']} resolves outside the legacy folder")
            if d["kind"] == "raster":
                res = inputs_raster(ctx, d, src, out, aoi)
            else:
                res = inputs_vector(ctx, d, src, out, LINE_COLOURS[vi % len(LINE_COLOURS)])
                vi += 1
            built[dsid] = res
            entries[dsid] = {"dataset": dsid, "role": d["role"],
                             "source": {"path": d["legacy_path"], "layer": d.get("layer"), "sha256": _source_sha(src),
                                        "inventory_sha256": d.get("sha256")},
                             "views": [OVERVIEW], **res["entry"]}
        except Exception as ex:                                  # recorded per dataset, exit 1, never silent
            shutil.rmtree(out, ignore_errors=True)
            skipped.append({"dataset": dsid, "reason": f"{type(ex).__name__}: {ex}"[:300]})
            rc = 1
    projects = {}
    # ---- inputs-overview: every input (vectors on top), legacy rasters present but hidden
    rasters = [k for k in sorted(built) if built[k]["spec"]["kind"] == "cog"]
    vectors = [k for k in sorted(built) if built[k]["spec"]["kind"] == "vector"]
    specs = [{**built[k]["spec"], "visible": entries[k]["role"] == "input"} for k in rasters]
    specs += [{**built[k]["spec"], "visible": True} for k in vectors]
    bbs = [entries[k]["bounds_4326"] for k in built if entries[k]["bounds_4326"] and entries[k]["role"] == "input"] \
        or [entries[k]["bounds_4326"] for k in built if entries[k]["bounds_4326"]]
    if specs and bbs:
        bbox = [min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs)]
        oc, oz = fit_zoom(bbox)
        cam = {"center": oc, "zoom": oz, "source": "extent of the inputs"}
        bms = [{"name": f"{k} cell {c['id']}", "center": c["camera"]["center"], "zoom": c["camera"]["zoom"]}
               for k in rasters for c in entries[k]["cells_to_identify"]]
        bms += [{"name": f"{k} feature {f['id']}", "center": f["camera"]["center"], "zoom": f["camera"]["zoom"]}
                for k in vectors for f in entries[k]["features_to_identify"]]
        projects[OVERVIEW] = ctx.save(ctx.root / f"{OVERVIEW}.geolibre.json", "inputs overview", cam, specs, bms)
    # ---- grid-<family>: the family's rasters at the five grid_corners windows, with every member's cell edges
    for fam in inv.get("grids") or []:
        fid = str(fam.get("id"))
        members = [m["dataset"] for m in (fam.get("members") or []) + (fam.get("misaligned") or [])]
        ref = fam.get("reference")
        have = [m for m in members if m in built and built[m]["spec"]["kind"] == "cog"]
        if ref not in have:
            continue
        name = "grid-" + (fid[len("grid."):] if fid.startswith("grid.") else fid)
        R = built[ref]
        windows, where = grid_windows(R["tr"], R["crs"], entries[ref]["raster"]["shape"], aoi)
        t4326 = _transformer(R["crs"])
        specs = [{**built[m]["spec"], "visible": True} for m in [ref] + [x for x in have if x != ref]]
        for i, m in enumerate([ref] + [x for x in have if x != ref]):
            p = ctx.root / name / f"edges-{m}.geojson"
            grid_edges(ctx, p, built[m]["tr"], R["tr"], R["crs"], windows)
            colour = "#000000" if m == ref else LINE_COLOURS[i % len(LINE_COLOURS)]
            specs.append({"name": f"cell edges {m}", "dataset": m, "role": "cell edges", "kind": "vector",
                          "path": p, "visible": True,
                          "style": {"strokeColor": colour, "strokeWidth": 0.8 if m == ref else 1.4,
                                    "fillOpacity": 0}})
        if aoi_id in built and built[aoi_id]["spec"]["kind"] == "vector":
            specs.append({**built[aoi_id]["spec"], "name": f"AOI ({aoi_id})", "role": "aoi", "visible": True,
                          "style": {"strokeColor": "#ff0000", "strokeWidth": 1.4, "fillOpacity": 0}})
        bms = []
        for wname, cc, rr in windows:
            x, y = R["tr"] * (cc, rr)
            lon, lat = lonlat(t4326, x, y)
            bms.append({"name": f"{fid} {wname} (cols {cc - GRID_WINDOW_CELLS // 2}-"
                                f"{cc - GRID_WINDOW_CELLS // 2 + GRID_WINDOW_CELLS - 1}, rows "
                                f"{rr - GRID_WINDOW_CELLS // 2}-{rr - GRID_WINDOW_CELLS // 2 + GRID_WINDOW_CELLS - 1})",
                        "center": [round(lon, 9), round(lat, 9)],
                        "zoom": zoom_for(t4326, R["tr"], rr, cc, GRID_CELL_PX)})
        cam = {"center": bms[0]["center"], "zoom": bms[0]["zoom"], "source": f"first window: {windows[0][0]}"}
        projects[name] = {**ctx.save(ctx.root / f"{name}.geolibre.json", f"grid family {fid}", cam, specs, bms),
                          "family": fid, "reference": ref, "windows_at": where,
                          "windows": [b["name"] for b in bms],
                          "offsets_px": {m["dataset"]: m.get("offset_px") for m in
                                         (fam.get("members") or []) + (fam.get("misaligned") or [])},
                          "misaligned": [m["dataset"] for m in fam.get("misaligned") or []]}
        for m in have:
            entries[m]["views"].append(name)
    doc = {"schema": SCHEMA, "mode": "inputs", "generator": GENERATOR, "caption": INPUTS_CAPTION, "run_id": None,
           "root": INPUTS_ROOT, "legacy_root": os.path.relpath(legacy, migration), "aoi": aoi_id,
           "served_as": "/data/ = the migration folder (start.py); project and layer URLs are origin-relative",
           "page_url": "http://127.0.0.1:<port>/?url=<urlencoded http://127.0.0.1:<port><url_path>>&panels=collapsed",
           "identify": {"raster": "cell-<id> project: switch the engine to maplibre-gl-raster, click the map centre, "
                                  "read Band 1 (the popup rounds to ~3 significant figures)",
                        "vector": "feature-<id> project: click the map centre, read the feature's attributes"},
           "projects": projects,
           "datasets": [entries[k] for k in sorted(entries)],
           "skipped": sorted(skipped, key=lambda s: s["dataset"])}
    ac.dump_json(migration / INPUTS_INDEX, doc)
    return doc, rc


def inputs_index(migration: Path) -> Path | None:
    p = Path(migration) / INPUTS_INDEX
    return p if p.is_file() else None

# ---------------------------------------------------------------------------------------------------------------
# server state (written by start.py)
# ---------------------------------------------------------------------------------------------------------------
def server_state(migration: Path) -> dict | None:
    st = _load(Path(migration) / ".axis" / "map" / "server.json")
    if not isinstance(st, dict) or not st.get("port") or not st.get("pid"):
        return None
    try:
        os.kill(int(st["pid"]), 0)
    except (OSError, ValueError):
        return None
    return st


# ---------------------------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------------------------
def cmd_run(a) -> int:
    run = Path(a.run)
    if not (run / "verify").is_dir():
        print(f"views.py: {run}/verify not found", file=sys.stderr)
        return 2
    if not (run / "verify" / "compare").is_dir():
        print("views.py: no compare results: run `compare.py run --run <dir>` first", file=sys.stderr)
        return 2
    try:
        doc, rc = build_views(run, a.dataset)
    except SetupError as e:
        print(f"views.py: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(doc, sort_keys=True, indent=1))
    else:
        where = doc["root"]
        for e in doc["datasets"]:
            print(f"{e['dataset']}: {e['family']} views {', '.join(e['views'])} under {where}/{e['dataset']}/")
        for s in doc["skipped"]:
            print(f"{s['dataset']}: skipped ({s['reason']})")
        print(f"index: {where}/views.json · open one with `views.py url --run {run} --dataset <ds> --view <v>`")
    if not doc["datasets"] and rc == 0:
        return 2 if not doc["skipped"] else 0
    return rc


def cmd_url(a) -> int:
    run = Path(a.run).resolve()
    idx = find_index(run)
    if idx is None:
        print("views.py: no views.json: run `views.py run --run <dir>` first", file=sys.stderr)
        return 2
    doc = _load(idx) or {}
    port = a.port
    if not port:
        try:
            st = server_state(migration_of(run))
        except SetupError as e:
            print(f"views.py: {e}", file=sys.stderr)
            return 2
        if not st:
            print("views.py: map server not running: start it with `python3 $PLUGIN/skills/map/start.py "
                  "<migration>`", file=sys.stderr)
            return 2
        port = int(st["port"])
    rows = []
    for e in doc.get("datasets") or []:
        if a.dataset and e["dataset"] != a.dataset:
            continue
        if a.cell:
            seen = {}
            for ve in e["views"].values():
                for c in ve.get("cells_to_identify") or []:
                    if c.get("id") == a.cell and c.get("url_path"):
                        seen[c["id"]] = c
            for k, c in sorted(seen.items()):
                rows.append({"dataset": e["dataset"], "cell": k, "url": page_url(port, c["url_path"]),
                             "project_sha256": c["project_sha256"], "row": c.get("row"), "col": c.get("col")})
            continue
        for v, ve in sorted(e["views"].items()):
            if a.view and v != a.view:
                continue
            rows.append({"dataset": e["dataset"], "view": v, "url": page_url(port, ve["url_path"]),
                         "project_sha256": ve["project_sha256"]})
    if not rows:
        print(f"views.py: no view {a.dataset}/{a.cell or a.view} in {idx}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, sort_keys=True, indent=1))
    elif len(rows) == 1:
        print(rows[0]["url"])
    else:
        for r in rows:
            print(f"{r['dataset']} {r['view']}: {r['url']}")
    return 0


def _port(migration: Path, port: int) -> int | None:
    if port:
        return int(port)
    st = server_state(migration)
    if not st:
        print("views.py: map server not running: start it with `python3 $PLUGIN/skills/map/start.py "
              "<migration>`", file=sys.stderr)
        return None
    return int(st["port"])


def cmd_inputs(a) -> int:
    mig = Path(a.migration)
    try:
        doc, rc = build_inputs_views(mig)
    except SetupError as e:
        print(f"views.py: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(doc, sort_keys=True, indent=1))
    else:
        for name, pe in sorted(doc["projects"].items()):
            print(f"{name}: {pe['project']} ({len(pe['layers'])} layers, bookmarks: {len(pe['bookmarks'])})")
        for e in doc["datasets"]:
            ids = [c["id"] for c in e["cells_to_identify"]] or [f["id"] for f in e["features_to_identify"]]
            what = "cells" if e["family"] == "raster" else "features"
            print(f"{e['dataset']} [{e['role']}, {e['family']}]: {what} to identify {', '.join(ids) or 'none'}")
        for s in doc["skipped"]:
            print(f"{s['dataset']}: skipped ({s['reason']})")
        print(f"index: {INPUTS_INDEX} · open one with `views.py inputs-url {a.migration} --view {OVERVIEW}` "
              "(or --dataset <ds> --cell <id> | --feature <id>)")
    if not doc["datasets"] and rc == 0:
        return 2 if not doc["skipped"] else 0
    return rc


def cmd_inputs_url(a) -> int:
    mig = Path(a.migration).resolve()
    idx = inputs_index(mig)
    if idx is None:
        print(f"views.py: no {INPUTS_INDEX}: run `views.py inputs <migration>` first", file=sys.stderr)
        return 2
    doc = _load(idx) or {}
    port = _port(mig, a.port)
    if port is None:
        return 2
    rows = []
    for name, pe in sorted((doc.get("projects") or {}).items()):
        if a.all or (a.view and name == a.view):
            rows.append({"view": name, "url": page_url(port, pe["url_path"]), "project_sha256": pe["project_sha256"]})
    for e in doc.get("datasets") or []:
        if not a.all and e["dataset"] != a.dataset:
            continue
        for key, want in (("cells_to_identify", a.cell), ("features_to_identify", a.feature)):
            for c in e.get(key) or []:
                if a.all or c["id"] == want:
                    kind = "cell" if key == "cells_to_identify" else "feature"
                    rows.append({"view": f"{e['dataset']} {kind}-{c['id']}", "dataset": e["dataset"], kind: c["id"],
                                 "url": page_url(port, c["url_path"]), "project_sha256": c["project_sha256"]})
    if not rows:
        have = sorted(doc.get("projects") or {})
        print(f"views.py: no such view in {INPUTS_INDEX} (projects: {', '.join(have)}; cells and features per "
              "dataset: see the index)", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rows, sort_keys=True, indent=1))
    elif len(rows) == 1:
        print(rows[0]["url"])
    else:
        for r in rows:
            print(f"{r['view']}: {r['url']}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="GeoLibre map views of a compared run (display only).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="write the views of every compared raster/vector")
    r.add_argument("--run", required=True, help="run directory (migration/runs/<id>)")
    r.add_argument("--dataset", action="append", default=None)
    r.add_argument("--json", action="store_true")
    u = sub.add_parser("url", help="print the page URL of a view for the running map server")
    u.add_argument("--run", required=True)
    u.add_argument("--dataset")
    u.add_argument("--view", choices=sorted(set(RASTER_VIEWS) | set(VECTOR_VIEWS)))
    u.add_argument("--cell", choices=("worst", "interior", "edge"),
                   help="the project centred on that cell to identify (instead of --view)")
    u.add_argument("--all", action="store_true", help="every view of every dataset")
    u.add_argument("--port", type=int, default=0, help="server port (default: the one start.py recorded)")
    u.add_argument("--json", action="store_true")
    i = sub.add_parser("inputs", help="discover (before compare): views of the legacy inputs and legacy rasters")
    i.add_argument("migration", help="the migration folder (with inventory.json)")
    i.add_argument("--json", action="store_true")
    iu = sub.add_parser("inputs-url", help="print the page URL of an inputs view for the running map server")
    iu.add_argument("migration")
    g = iu.add_mutually_exclusive_group(required=True)
    g.add_argument("--view", help=f"{OVERVIEW} or grid-<family>")
    g.add_argument("--cell", help="with --dataset: the project centred on that cell (max, aoi-edge|edge, interior)")
    g.add_argument("--feature", help="with --dataset: the project centred on that feature (first, largest, central)")
    g.add_argument("--all", action="store_true", help="every project")
    iu.add_argument("--dataset")
    iu.add_argument("--port", type=int, default=0, help="server port (default: the one start.py recorded)")
    iu.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        ensure_map_env(argv)
        return cmd_run(a)
    if a.cmd == "inputs":
        ensure_map_env(argv)
        return cmd_inputs(a)
    if a.cmd == "inputs-url":
        if (a.cell or a.feature) and not a.dataset:
            ap.error("inputs-url --cell/--feature needs --dataset")
        return cmd_inputs_url(a)
    if not a.all and not (a.dataset and (a.view or a.cell)):
        ap.error("url needs --dataset and --view or --cell (or --all)")
    return cmd_url(a)


if __name__ == "__main__":
    sys.exit(main())
