#!/usr/bin/env python3
"""plot.py — deterministic legacy | new | difference plots and the model's visual-review receipts (06 §4g–h).

Subcommands (CONTRACTS §7):
  run           --run <run_dir> [--dataset ds]…     read verify/compare/<ds>.json + both files; write
                                                    verify/plots/<ds>/*.png + plots.json (stale plots removed first)
  review-add    --run <run_dir> --json finding.json the ONLY way a model writes a visual finding: schema-validated,
                                                    appended atomically to verify/visual-review.json. A PNG receipt
                                                    cites a plot (plot, plot_sha256); a view receipt cites GeoLibre
                                                    map views (view, views_seen, engine, cells_read; checked against
                                                    views.json by lib/visual_receipt.py); a skipped receipt reads
                                                    "skipped: identical (<sha256>)" for a dataset with nothing to
                                                    show (accepted only if the new output's bytes, hashed again,
                                                    are that sha). All count the same.
  review-status --run <run_dir> [--json]            which compared raster/vector datasets still lack a receipt
  review-template --run <run_dir> [--dataset ds]… [--kind auto|view|png]
                                                    print prefilled "looked, nothing found" Q-all receipts (a JSON
                                                    list for review-add) for the datasets still lacking one (or the
                                                    named ones): view (default when views.json has the dataset) cites
                                                    the required views and every cell-<id> project with shas from
                                                    views.json, the engine and the cells; png cites every PNG with its
                                                    sha from plots.json. The reviewer fills answer, model, note (view:
                                                    each cell's legacy/new as the popup shows them) — left empty,
                                                    review-add refuses them. Allowed `layer` values go to stderr.
                                                    Datasets views.py skipped (identical) get a skipped receipt.

Plots are evidence about verdicts, never inputs to them. Every PNG is rendered with matplotlib Agg at DPI 150 with a
fixed font and no timestamp, so the same inputs give the same bytes. Nothing is reprojected or resampled onto the
other side's grid: when the grids differ, each side is drawn on its own grid in map space.

Plot set per family:
  raster  overview.png (legacy | new | difference, one shared p2/p98 norm, symmetric PuOr_r difference scale, cells
          over tolerance outlined, block reduction by max |d| for the difference and mode for categorical data),
          mask_diff.png, hist.png (log y, tolerance lines, value distributions, per-zone totals for extensive data),
          worst_tiles.png (4 non-overlapping 48×48-cell tiles at native resolution with the cell grid; if the grid gate
          failed: both grids and valid-mask outlines drawn over each other in map space at the worst corner and edges)
  vector  overlay.png (legacy blue solid, new orange dashed, symmetric difference red, unmatched keys labelled),
          per_feature.png (geometry metric and attribute agreement per key), worst_features.png (vertices drawn)
  table   residuals.png (per-key residuals for up to 6 numeric columns + new-vs-legacy scatter with y = x)

Colour convention (all plots): legacy = blue, new = orange; difference n − l: orange = new larger, purple = legacy
larger, near-white = equal; light grey = no data.

Exit codes: run 0 every requested dataset plotted · 1 some dataset could not be plotted at all · 2 setup error ·
3 sealed run. review-add 0 appended (after seal: appended to runs/<id>/amend/visual-review.json and sealed with
`report/seal.py <migration> --run <id> --amend`) · 2 rejected (reasons printed) · 4 recorded in amend/ but the amend
seal failed.
review-status 0 every compared raster/vector dataset has a complete receipt · 1 otherwise · 2 setup error.
"""
from __future__ import annotations

import argparse
import fcntl
import io
import json
import math
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

os.environ["GDAL_PAM_ENABLED"] = "NO"          # reading a legacy raster must never write an .aux.xml sidecar
os.environ["MPLBACKEND"] = "Agg"

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import numpy as np  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE))
import axis_common as ac  # noqa: E402
import visual_receipt as vrc  # noqa: E402

GENERATOR = "axis-migrate.plot"
VERSION = "1.0.0"
PLOTS_SCHEMA = "axis-migrate.plots.v1"
REVIEW_SCHEMA = "axis-migrate.visual-review.v1"
RUBRIC_PATH = HERE / "rubric.v1.yaml"
DPI = 150
TILE = 48                     # worst-tile edge in cells (native resolution)
K_TILES = 4
GATE_WIN = 16                 # window edge in legacy cells for the grid-gate overlay views
OVERVIEW_PANEL_PX = 470       # an overview panel is ~470 px wide: larger rasters are block-reduced by plot.py,
                              # never interpolated by the renderer (the spec's 2048 px is an upper bound)
MASK_PANEL_PX = 1000
MAX_OUTLINE_SEGMENTS = 40000
UNMATCHED_LABELS = 20
NOTE_MAX = 200
EXIT_OK, EXIT_SOME, EXIT_SETUP, EXIT_SEALED = 0, 1, 2, 3
EXIT_REJECTED = 2
EXIT_AMEND_FAILED = 4

C_LEGACY, C_NEW, C_SYMDIFF = "#2166ac", "#e66101", "#d7301f"
C_NODATA, C_BOTH, C_ZERO = "#d4d4d4", "#bdbdbd", "#f7f7f7"
C_ZONE = "#1b7837"
MARK_MAX = 600                # over-tolerance cells also get a ring marker when there are at most this many
C_LEG_ONLY_DIFF, C_NEW_ONLY_DIFF = "#000000", "#7a7a7a"
NOT_COMPARED_STATES = ("NOT_COMPARED", "EVALUATOR_ERROR", "BLOCKED")


class SetupError(Exception):
    pass


# =================================================================================================================
# Deterministic matplotlib
# =================================================================================================================
def _mpl():
    import matplotlib.pyplot as plt
    matplotlib.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 7.5, "axes.titlesize": 7.5, "axes.labelsize": 7,
        "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 6.5, "figure.dpi": DPI,
        "savefig.dpi": DPI, "image.interpolation": "nearest", "image.resample": False, "svg.hashsalt": "axis",
        "axes.formatter.useoffset": False, "axes.formatter.limits": (-30, 30), "path.simplify": False, "figure.facecolor": "white",
        "axes.facecolor": "white", "savefig.facecolor": "white",
    })
    return plt


def _panel_boxes(fig, axes: list[tuple[str, Any]]) -> list[dict]:
    fig.canvas.draw()
    h = fig.bbox.height
    out = []
    for name, ax in axes:
        bb = ax.get_window_extent()
        out.append({"name": name, "bbox_px": [int(round(bb.x0)), int(round(h - bb.y1)), int(round(bb.x1)),
                                              int(round(h - bb.y0))]})
    return out


def _save(fig, path: Path) -> str:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, metadata={"Software": None})
    plt.close(fig)
    data = buf.getvalue()
    ac.atomic_write_bytes(path, data)
    return ac.sha256_bytes(data)


def _fmt(x: Any, p: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, (int, np.integer)):
        return f"{int(x):,}"
    x = float(x)
    if not math.isfinite(x):
        return str(x)
    if x == 0:
        return "0"
    return f"{x:.{p}g}"


def _short(p: str | None, n: int = 60) -> str:
    if not p:
        return "—"
    return p if len(p) <= n else "…" + p[-(n - 1):]


# =================================================================================================================
# Run context
# =================================================================================================================
class Run:
    def __init__(self, run_dir: str | os.PathLike):
        import yaml
        self.dir = Path(run_dir).resolve()
        if not (self.dir / "manifest.json").is_file():
            raise SetupError(f"{self.dir}: manifest.json missing (not a run directory)")
        self.manifest = ac.load_json(self.dir / "manifest.json")
        self.run_id = self.manifest.get("run_id") or self.dir.name
        self.verify = self.dir / "verify"
        snap = self.dir / "snapshot"
        self.graph = ac.load_json(snap / "graph.json") if (snap / "graph.json").is_file() else {}
        ptxt = (snap / "params.yaml").read_text(encoding="utf-8") if (snap / "params.yaml").is_file() else ""
        self.params = (yaml.safe_load(ptxt) or {}) if ptxt else {}
        mig = self.dir.parent.parent
        self.migration = mig if self.dir.parent.name == "runs" else None
        self.project_root = mig.parent if self.migration else None
        lr = (self.graph.get("project") or {}).get("legacy_root")
        self.legacy_root = (self.migration / lr).resolve() if (self.migration and lr) else None
        self.datasets = {d["id"]: d for d in self.graph.get("datasets", []) if isinstance(d, dict) and "id" in d}

    def sealed(self) -> str | None:
        if (self.verify / "evidence.json").exists() or (self.verify / "EVIDENCE_ID").exists():
            return "verify/evidence.json exists: the run is sealed"
        if self.verify.exists() and not os.access(self.verify, os.W_OK):
            return "verify/ is read-only: the run is sealed"
        return None

    def param(self, pid: str):
        for p in (self.params or {}).get("params", []) or []:
            if isinstance(p, dict) and p.get("id") == pid:
                return p.get("value")
        return None

    def measure_crs(self, dsid: str | None = None):
        d = self.datasets.get(dsid or "") or {}
        pid = (d.get("compare") or {}).get("measure_crs_param") or "p.measure_crs"
        v = self.param(pid)
        if v is None:
            pids = sorted({(x.get("compare") or {}).get("measure_crs_param") for x in self.datasets.values()} - {None})
            for p in pids:
                v = v or self.param(p)
        return v

    def results(self, only: list[str] | None = None) -> dict[str, dict]:
        cdir = self.verify / "compare"
        out = {}
        if not cdir.is_dir():
            return out
        for p in sorted(cdir.glob("*.json")):
            try:
                r = ac.load_json(p)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(r, dict) or r.get("run_id") != self.run_id or not r.get("dataset_id"):
                continue
            if only and r["dataset_id"] not in only:
                continue
            out[r["dataset_id"]] = r
        return out

    def resolve(self, side: dict | None, relkey: str | None = None) -> Path | None:
        if not side:
            return None
        p = side.get("path")
        cands = []
        if p:
            pp = Path(p)
            if pp.is_absolute():
                cands.append(pp)
            else:
                if self.project_root:
                    cands.append(self.project_root / pp)
                cands.append(self.dir / pp)
        if relkey and side.get(relkey) and self.legacy_root:
            cands.append(self.legacy_root / side[relkey])
        for c in cands:
            if c.exists():
                return c
        return cands[0] if cands else None


def _file_sha(p: Path | None) -> str | None:
    if p is None or not p.exists():
        return None
    return ac.tree_hash(p).split(":", 1)[1] if p.is_dir() else ac.sha256_file(p)


# =================================================================================================================
# Geometry helpers shared by raster and vector plots
# =================================================================================================================
def _crs_obj(crs):
    import pyproj
    if crs is None:
        return None
    try:
        return pyproj.CRS.from_user_input(crs.to_wkt() if hasattr(crs, "to_wkt") else crs)
    except Exception:
        return None


def _crs_label(crs) -> str:
    c = _crs_obj(crs)
    if c is None:
        return "CRS missing"
    auth = c.to_authority()
    name = f"{auth[0]}:{auth[1]}" if auth else c.name
    unit = c.axis_info[0].unit_name if c.axis_info else ""
    return f"{name} ({unit})" if unit else name


def _crs_text(crs) -> str | None:
    c = _crs_obj(crs)
    if c is None:
        return None
    auth = c.to_authority()
    return f"{auth[0]}:{auth[1]}" if auth else c.to_wkt()


def _metres_per_unit_x(crs, measure_crs, cx: float, cy: float, dx: float) -> tuple[float | None, str]:
    import pyproj
    c = _crs_obj(crs)
    if c is None or dx <= 0:
        return None, ""
    try:
        if measure_crs:
            m = pyproj.CRS.from_user_input(measure_crs)
            if not m.is_geographic:
                t = pyproj.Transformer.from_crs(c, m, always_xy=True)
                x1, y1 = t.transform(cx - dx / 2, cy)
                x2, y2 = t.transform(cx + dx / 2, cy)
                return math.hypot(x2 - x1, y2 - y1) / dx, f"measured in {_crs_text(m)}"
        if c.is_geographic:
            g = pyproj.Geod(ellps="WGS84")
            _, _, dist = g.inv(cx - dx / 2, cy, cx + dx / 2, cy)
            return dist / dx, "geodesic (no measure_crs)"
        f = c.axis_info[0].unit_conversion_factor if c.axis_info else 1.0
        return float(f), f"{_crs_text(c)} units"
    except Exception:
        return None, ""


def _scalebar(ax, crs, measure_crs, extent: tuple[float, float, float, float]) -> str | None:
    import matplotlib.patheffects as pe
    x0, y0, x1, y1 = extent
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    mpu, how = _metres_per_unit_x(crs, measure_crs, (x0 + x1) / 2, (y0 + y1) / 2, w / 4)
    if not mpu:
        return None
    target = 0.22 * w * mpu
    k = 10 ** math.floor(math.log10(target))
    nice = max(m * k for m in (1, 2, 5) if m * k <= target) if target >= k else k
    length = nice / mpu
    bx, by = x0 + 0.04 * w, y0 + 0.04 * h
    halo = [pe.withStroke(linewidth=4, foreground="white")]
    ax.plot([bx, bx + length], [by, by], color="black", linewidth=2.2, solid_capstyle="butt", zorder=10,
            path_effects=halo)
    label = f"{nice / 1000:g} km" if nice >= 1000 else f"{nice:g} m"
    ax.text(bx + length / 2, by + 0.015 * h, label, ha="center", va="bottom", fontsize=6, zorder=10,
            path_effects=[pe.withStroke(linewidth=2.5, foreground="white")])
    return f"{label} ({how})"


def _mask_segments(mask: np.ndarray, tr) -> list:
    """Cell-edge outline of a boolean mask as map-coordinate segments (exact cell boundaries, no contouring)."""
    m = np.pad(mask.astype(bool), 1)
    v = m[1:-1, 1:] != m[1:-1, :-1]          # (H, W+1): vertical edge at column boundary j between rows r and r+1
    hz = m[1:, 1:-1] != m[:-1, 1:-1]         # (H+1, W): horizontal edge at row boundary i between cols c and c+1
    segs = []
    a, b, c, d, e, f = tr.a, tr.b, tr.c, tr.d, tr.e, tr.f

    def xy(col, row):
        return (a * col + b * row + c, d * col + e * row + f)
    rr, cc = np.nonzero(v)
    for r, j in zip(rr.tolist(), cc.tolist()):
        segs.append((xy(j, r), xy(j, r + 1)))
    rr, cc = np.nonzero(hz)
    for i, col in zip(rr.tolist(), cc.tolist()):
        segs.append((xy(col, i), xy(col + 1, i)))
    return segs


def _draw_segments(ax, segs, **kw) -> None:
    from matplotlib.collections import LineCollection
    if segs:
        ax.add_collection(LineCollection(segs, **kw))


def _grid_lines(ax, tr, r0: int, r1: int, c0: int, c1: int, **kw) -> None:
    """Cell grid of rows r0..r1 and cols c0..c1 (boundaries) on the grid `tr`."""
    segs = []
    for col in range(c0, c1 + 1):
        segs.append((tr @ (col, r0), tr @ (col, r1)))
    for row in range(r0, r1 + 1):
        segs.append((tr @ (c0, row), tr @ (c1, row)))
    _draw_segments(ax, segs, **kw)


# =================================================================================================================
# Raster
# =================================================================================================================
class RSide:
    def __init__(self, path: Path | None, label: str):
        self.path, self.label = path, label
        self.ok, self.err = False, None
        self.arr = self.valid = self.tr = self.crs = None
        self.dtype = self.nodata = None
        self.shape = (0, 0)
        self.bands = 0
        if path is None:
            self.err = "no path recorded"
            return
        if not path.exists():
            self.err = f"missing: {_short(str(path))}"
            return
        try:
            import rasterio
            with rasterio.open(path) as ds:
                a = ds.read(1)
                msk = ds.read_masks(1) > 0
                self.tr, self.crs, self.dtype = ds.transform, ds.crs, ds.dtypes[0]
                self.nodata, self.bands = ds.nodata, ds.count
        except Exception as e:                                   # report, never hide: the panel says unreadable
            self.err = f"unreadable: {type(e).__name__}: {str(e)[:120]}"
            return
        v = msk.copy()
        if a.dtype.kind == "f":
            v &= np.isfinite(a)
        if self.nodata is not None and not (isinstance(self.nodata, float) and math.isnan(self.nodata)):
            v &= a != a.dtype.type(self.nodata) if a.dtype.kind in "iub" else a != self.nodata
        self.arr = a.astype("float64")
        self.valid = v
        self.shape = a.shape
        self.ok = True

    @property
    def extent(self) -> tuple[float, float, float, float]:
        from rasterio.transform import array_bounds
        b = array_bounds(self.shape[0], self.shape[1], self.tr)          # (west, south, east, north)
        return (b[0], b[1], b[2], b[3])


def _same_grid(L: RSide, N: RSide) -> tuple[bool, str | None, list | None]:
    import pyproj
    if not (L.ok and N.ok):
        return False, "one side missing", None
    lc, nc = _crs_obj(L.crs), _crs_obj(N.crs)
    if lc is None or nc is None:
        return False, "CRS missing", None
    if not pyproj.CRS(lc).equals(pyproj.CRS(nc)):
        return False, f"CRS differs ({_crs_text(lc)} vs {_crs_text(nc)})", None
    lt, nt = L.tr, N.tr
    shift = [(nt.c - lt.c) / lt.a if lt.a else None, (nt.f - lt.f) / lt.e if lt.e else None]
    if L.shape != N.shape:
        return False, f"shape differs ({L.shape[0]}×{L.shape[1]} vs {N.shape[0]}×{N.shape[1]})", shift
    H, W = L.shape
    dev = max(abs(nt.c - lt.c) / abs(lt.a), abs(nt.f - lt.f) / abs(lt.e), W * abs(nt.a - lt.a) / abs(lt.a),
              H * abs(nt.e - lt.e) / abs(lt.e), H * abs(nt.b - lt.b) / abs(lt.a), W * abs(nt.d - lt.d) / abs(lt.e))
    if dev > 1e-6:
        return False, f"transform differs by {dev:.4g} px", shift
    return True, None, shift


def _check(result: dict, cid: str) -> dict | None:
    for c in result.get("checks") or []:
        if c.get("id") == cid:
            return c
    return None


def _threshold(result: dict, prefix: str = "raster.values") -> tuple[float, float, bool]:
    c = _check(result, prefix) or {}
    t = c.get("threshold")
    if isinstance(t, dict):
        atol, rtol = float(t.get("atol") or 0.0), float(t.get("rtol") or 0.0)
        return atol, rtol, (atol == 0 and rtol == 0)
    return 0.0, 0.0, True


def _block_reduce(arr: np.ndarray, valid: np.ndarray, f: int, how: str) -> tuple[np.ndarray, np.ndarray]:
    if f <= 1:
        return arr, valid
    H, W = arr.shape
    Hp, Wp = -(-H // f) * f, -(-W // f) * f
    a = np.zeros((Hp, Wp), dtype=arr.dtype)
    v = np.zeros((Hp, Wp), dtype=bool)
    a[:H, :W], v[:H, :W] = arr, valid
    a = a.reshape(Hp // f, f, Wp // f, f).transpose(0, 2, 1, 3).reshape(Hp // f, Wp // f, f * f)
    v = v.reshape(Hp // f, f, Wp // f, f).transpose(0, 2, 1, 3).reshape(Hp // f, Wp // f, f * f)
    cnt = v.sum(axis=2)
    rv = cnt > 0
    if how == "mean":
        s = np.where(v, a, 0.0).sum(axis=2)
        out = np.where(rv, s / np.maximum(cnt, 1), 0.0)
    elif how == "maxabs":                       # signed value of the cell with the largest |d| in the block
        key = np.where(v, np.abs(a), -1.0)
        idx = np.argmax(key, axis=2)
        out = np.take_along_axis(a, idx[..., None], axis=2)[..., 0]
    elif how == "mode":
        classes = np.unique(a[v]) if v.any() else np.array([0.0])
        best = np.full(cnt.shape, -1)
        out = np.zeros(cnt.shape, dtype=arr.dtype)
        for k in classes:                       # ties → the smallest class (classes ascending, strict >)
            n = (v & (a == k)).sum(axis=2)
            take = n > best
            out[take], best[take] = k, n[take]
    elif how == "max":                          # priority codes (mask categories)
        out = np.where(v, a, a.min() if a.size else 0).max(axis=2)
    elif how == "any":
        out = (v & (a != 0)).any(axis=2).astype(arr.dtype)
    else:
        raise ValueError(how)
    return out, rv


def _scaled_tr(tr, f: int):
    from affine import Affine
    return tr @ Affine.scale(f, f) if f > 1 else tr


def _factor(shape, px: int) -> int:
    return max(1, int(math.ceil(max(shape) / float(px))))


def _value_norm(L: RSide, N: RSide, categorical: bool) -> dict:
    vals = []
    for s in (L, N):
        if s.ok and s.valid.any():
            vals.append(s.arr[s.valid])
    u = np.concatenate(vals) if vals else np.array([], dtype="float64")
    if categorical and u.size:
        classes = np.unique(u)
        if classes.size <= 24:
            return {"kind": "categorical", "classes": [float(x) for x in classes.tolist()]}
    if u.size == 0:
        return {"kind": "shared_p2_p98", "vmin": 0.0, "vmax": 1.0, "source": "no valid values"}
    vmin, vmax = (float(x) for x in np.percentile(u, [2, 98]))
    if not vmax > vmin:
        pad = abs(vmin) * 0.01 or 0.5
        vmin, vmax = vmin - pad, vmax + pad
    return {"kind": "shared_p2_p98", "vmin": vmin, "vmax": vmax, "source": "p2/p98 of both sides' valid values",
            "data_min": float(u.min()), "data_max": float(u.max())}


def _value_cmap(norm: dict):
    import matplotlib as mpl
    from matplotlib.colors import BoundaryNorm, ListedColormap
    if norm["kind"] == "categorical":
        cl = norm["classes"]
        base = mpl.colormaps["tab20"] if len(cl) > 10 else mpl.colormaps["tab10"]
        cols = [base(i % base.N) for i in range(len(cl))]
        if len(cl) == 1:
            cols = ["#4d4d4d"]
        cmap = ListedColormap(cols)
        cmap = cmap.with_extremes(bad=(0, 0, 0, 0))
        edges = [cl[0] - 0.5] + [(cl[i] + cl[i + 1]) / 2 for i in range(len(cl) - 1)] + [cl[-1] + 0.5]
        return cmap, BoundaryNorm(edges, len(cl))
    from matplotlib.colors import Normalize
    cmap = mpl.colormaps["viridis"].copy()
    cmap = cmap.with_extremes(bad=(0, 0, 0, 0))
    return cmap, Normalize(norm["vmin"], norm["vmax"])


def _diff_cmap():
    """PuOr_r truncated to [0.12, 0.88]: the saturated ends stay clearly purple/brown-orange, never near-black, so
    black and dark grey remain free for cells valid on one side only."""
    import matplotlib as mpl
    from matplotlib.colors import ListedColormap
    base = mpl.colormaps["PuOr_r"]
    cmap = ListedColormap(base(np.linspace(0.12, 0.88, 256)), name="PuOr_r_trunc")
    return cmap.with_extremes(bad=(0, 0, 0, 0))


def _setup_map_ax(ax, extent, crs, title: str, *, labels=True):
    x0, y0, x1, y1 = extent
    ax.set_facecolor(C_NODATA)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal", adjustable="box")
    ax.set_title(title, fontsize=7)
    if labels:
        ax.set_xlabel(f"x · {_crs_label(crs)}", fontsize=6)
        ax.set_ylabel("y", fontsize=6)
    ax.tick_params(labelsize=5.5)


def _union_extent(*exts) -> tuple[float, float, float, float]:
    exts = [e for e in exts if e is not None]
    return (min(e[0] for e in exts), min(e[1] for e in exts), max(e[2] for e in exts), max(e[3] for e in exts))


def _missing_text(ax, text: str) -> None:
    ax.text(0.5, 0.5, text, ha="center", va="center", transform=ax.transAxes, fontsize=7, wrap=True,
            color="#b2182b", bbox={"facecolor": "white", "edgecolor": "#b2182b", "boxstyle": "round"})


class RasterPlotter:
    def __init__(self, run: Run, res: dict, out: Path):
        self.run, self.res, self.out = run, res, out
        self.ds = res["dataset_id"]
        self.spec = (run.datasets.get(self.ds) or {}).get("compare") or {}
        self.categorical = bool(self.spec.get("categorical")) or self.spec.get("measure") in ("categorical", "mask")
        self.mcrs = run.measure_crs(self.ds)
        lp = run.resolve(res.get("legacy"), "legacy_relpath")
        np_ = run.resolve(res.get("new"))
        self.L, self.N = RSide(lp, "legacy"), RSide(np_, "new")
        if self.L.ok and self.categorical is False and self.L.dtype and np.dtype(self.L.dtype).kind in "iub" \
                and self.spec.get("categorical") is None and self.spec.get("measure") == "mask":
            self.categorical = True
        self.gate_ok, self.gate_why, self.shift = _same_grid(self.L, self.N)
        self.missing = []
        for s in (self.L, self.N):
            if not s.ok:
                self.missing.append(f"{s.label} {s.err}")
        self.crs = self.L.crs if self.L.ok else (self.N.crs if self.N.ok else None)
        self.extent = _union_extent(self.L.extent if self.L.ok else None, self.N.extent if self.N.ok else None) \
            if (self.L.ok or self.N.ok) else None
        self.norm = _value_norm(self.L, self.N, self.categorical)
        self.atol, self.rtol, self.exact = _threshold(res)
        self.zone_segs, self.zone_label = self._zones()
        self._diff()

    def _zones(self) -> tuple[list, str | None]:
        """Boundaries of the declared zones (graph compare.zones = '<ds>:<col>'), from the legacy zone file as
        compare uses it, in the raster CRS: draws where zone-boundary defects sit (Q6)."""
        z = self.spec.get("zones")
        if not z or self.crs is None:
            return [], None
        zds = str(z).split(":", 1)[0]
        d = self.run.datasets.get(zds) or {}
        lg = d.get("legacy") or {}
        if not lg.get("path") or self.run.legacy_root is None:
            return [], None
        gdf, err = _read_gdf(self.run.legacy_root / lg["path"], lg.get("layer"))
        if gdf is None or gdf.crs is None:
            return [], None
        try:
            gdf = gdf.to_crs(_crs_obj(self.crs))
        except Exception:
            return [], None
        segs = []
        for g in gdf.geometry:
            for part in _parts(g):
                rings = [part.exterior, *part.interiors] if part.geom_type == "Polygon" else [part]
                for ring in rings:
                    xy = _geom_coords(ring)
                    if len(xy) > 1:
                        segs.append(xy)
        return segs, f"zone boundaries ({z}) in green"

    def _draw_zones(self, ax, lw=0.5, zorder=4) -> None:
        if self.zone_segs:
            _draw_segments(ax, self.zone_segs, colors=C_ZONE, linewidths=lw, alpha=0.9, zorder=zorder)

    def _mark_over(self, ax, mask: np.ndarray, tr, size=5.0) -> None:
        n = int(mask.sum())
        if 0 < n <= MARK_MAX:
            rr, cc = np.nonzero(mask)
            xs = tr.c + (cc + 0.5) * tr.a + (rr + 0.5) * tr.b
            ys = tr.f + (cc + 0.5) * tr.d + (rr + 0.5) * tr.e
            ax.scatter(xs, ys, s=size ** 2, facecolors="none", edgecolors="black", linewidths=0.7, zorder=6)

    # ---------------------------------------------------------------------------------------------- difference
    def _diff(self):
        self.d = self.both = self.over = self.xor_l = self.xor_n = None
        self.lim = 1.0
        self.thr_scalar = 0.0
        if not self.gate_ok:
            return
        L, N = self.L, self.N
        self.both = L.valid & N.valid
        self.xor_l = L.valid & ~N.valid
        self.xor_n = N.valid & ~L.valid
        if self.categorical:
            d = np.where(self.both & (N.arr != L.arr), 1.0, 0.0)
            self.over = self.both & (N.arr != L.arr)
            self.d = d
            self.lim = 1.0
            return
        d = np.where(self.both, N.arr - L.arr, 0.0)
        ad = np.abs(d)
        if self.exact:
            self.over = self.both & (d != 0)
        else:
            self.over = self.both & (ad > self.atol + self.rtol * np.abs(L.arr))
        self.d = d
        absl = np.abs(L.arr[self.both]) if self.both.any() else np.array([0.0])
        self.thr_scalar = 0.0 if self.exact else float(self.atol + self.rtol * np.percentile(absl, 99.5))
        p = float(np.percentile(ad[self.both], 99.5)) if self.both.any() else 0.0
        lim = max(p, self.thr_scalar)
        if lim == 0.0:
            lim = float(ad.max()) if ad.size else 0.0
        self.lim = lim if lim > 0 else 1.0

    def _title(self) -> str:
        r = self.res
        codes = ", ".join(r.get("reason_codes") or []) or "—"
        return f"{self.ds} · compare verdict {r.get('verdict')} ({codes})"

    def _png(self, name, kind, fig, panels, extra=None) -> dict:
        boxes = _panel_boxes(fig, panels)
        sha = _save(fig, self.out / name)
        e = {"name": name, "sha256": sha, "kind": kind, "crs": _crs_text(self.crs),
             "extent": [float(x) for x in self.extent] if self.extent else None,
             "norm": {k: v for k, v in self.norm.items()},
             "panel_names": [p["name"] for p in boxes], "panels": boxes}
        if self.gate_ok and self.d is not None:
            e["norm"]["diff"] = {"kind": "symmetric", "cmap": "PuOr_r[0.12:0.88]", "lim": self.lim,
                                 "source": "max(p99.5 |d|, tolerance)", "tolerance_scalar": self.thr_scalar,
                                 "atol": self.atol, "rtol": self.rtol, "exact": self.exact}
        e["norm"]["cmap"] = "categorical" if self.norm["kind"] == "categorical" else "viridis"
        if extra:
            e.update(extra)
        return e

    # ---------------------------------------------------------------------------------------------- overview
    def overview(self) -> dict:
        plt = _mpl()
        from matplotlib.colors import ListedColormap, Normalize
        fig = plt.figure(figsize=(10.4, 4.6))
        gs = fig.add_gridspec(2, 3, height_ratios=[1, 0.045], hspace=0.32, wspace=0.18, left=0.05, right=0.985,
                              top=0.84, bottom=0.1)
        axes = [fig.add_subplot(gs[0, i]) for i in range(3)]
        cmap, vnorm = _value_cmap(self.norm)
        notes = []
        im_val = None
        f_used = 1
        for ax, s in zip(axes[:2], (self.L, self.N)):
            title = f"{s.label} · {_short(s.path.name if s.path else None, 40)}"
            _setup_map_ax(ax, self.extent, self.crs, title)
            if not s.ok:
                _missing_text(ax, f"{s.label} output {s.err}")
                continue
            f = _factor(s.shape, OVERVIEW_PANEL_PX)
            f_used = max(f_used, f)
            how = "mode" if self.categorical else "mean"
            a, v = _block_reduce(s.arr, s.valid, f, how)
            ext = s.extent
            im_val = ax.imshow(np.ma.array(a, mask=~v), extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper",
                               cmap=cmap, norm=vnorm, interpolation="nearest", resample=False)
            _scalebar(ax, self.crs, self.mcrs, self.extent)
            ax.set_title(f"{title}\n{_fmt(int(s.valid.sum()))} valid cells", fontsize=7)
        ax = axes[2]
        _setup_map_ax(ax, self.extent, self.crs, "difference n − l (cells valid on both sides)")
        im_d = None
        if self.gate_ok and self.d is not None:
            f = _factor(self.L.shape, OVERVIEW_PANEL_PX)
            a, v = _block_reduce(self.d, self.both, f, "maxabs")
            ext = self.L.extent
            tr = _scaled_tr(self.L.tr, f)
            im_d = ax.imshow(np.ma.array(a, mask=~v), extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper",
                             cmap=_diff_cmap(), norm=Normalize(-self.lim, self.lim), interpolation="nearest",
                             resample=False)
            # cells valid on one side only
            xl, _ = _block_reduce(self.xor_l.astype("float64"), np.ones_like(self.xor_l), f, "any")
            xn, _ = _block_reduce(self.xor_n.astype("float64"), np.ones_like(self.xor_n), f, "any")
            ov = np.where(xl > 0, 1.0, np.where(xn > 0, 2.0, np.nan))
            ax.imshow(np.ma.masked_invalid(ov), extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper",
                      cmap=ListedColormap([C_LEG_ONLY_DIFF, C_NEW_ONLY_DIFF]), norm=Normalize(0.5, 2.5),
                      interpolation="nearest", resample=False, zorder=3)
            for msk, col in ((xl > 0, C_LEG_ONLY_DIFF), (xn > 0, C_NEW_ONLY_DIFF)):
                if 0 < int(msk.sum()) <= 50_000:        # sparse one-side cells stay visible (above zone lines)
                    _draw_segments(ax, _mask_segments(msk, tr), colors=col, linewidths=1.0, zorder=5)
            ob, _ = _block_reduce(self.over.astype("float64"), np.ones_like(self.over), f, "any")
            n_over = int(self.over.sum())
            many = n_over > 0.5 * max(int(self.both.sum()), 1)
            segs = _mask_segments(ob > 0, tr) if (n_over and not many) else []
            if n_over == 0:
                ol = "none over"
            elif many:
                ol = "most over: not outlined"
            elif len(segs) <= MAX_OUTLINE_SEGMENTS:
                _draw_segments(ax, segs, colors="black", linewidths=0.5)
                ol = "outlined" + (" and ringed" if n_over <= MARK_MAX else "")
                self._mark_over(ax, self.over, self.L.tr, size=7.0)
            else:
                ol = f"{_fmt(n_over)} cells over tolerance: too many to outline"
            tol = "exact" if self.exact else f"atol {_fmt(self.atol)} + rtol {_fmt(self.rtol)}·|l|"
            rel = ""
            maxd = float(np.abs(self.d[self.both]).max()) if self.both.any() else 0.0
            n_one = int(self.xor_l.sum() + self.xor_n.sum())
            if maxd == 0.0:
                rel = " (no both-valid cell differs)"
            elif not self.categorical and self.norm.get("vmax"):
                span = abs(self.norm["vmax"]) or 1.0
                rel = f" = {_fmt(self.lim / span, 2)} × data p98"
                if self.lim / span < 1e-6:
                    rel += " (rounding size)"
            red = f"; {f}×{f}-cell blocks show the largest |d|" if f > 1 else ""
            ax.set_title(f"difference n − l · scale ±{_fmt(self.lim, 3)}{rel}\n{_fmt(n_over)} over tol. "
                         f"({tol}) · {ol}" + (f" · {_fmt(n_one)} one side only" if n_one else "") +
                         (f"\n{red.lstrip('; ')}" if red else ""), fontsize=6.5)
            notes.append("black = valid in legacy only, dark grey = valid in new only (see mask_diff.png)")
            self._draw_zones(ax, zorder=2)
            if self.zone_label:
                notes.append(self.zone_label)
            _scalebar(ax, self.crs, self.mcrs, self.extent)
        else:
            why = self.gate_why or "not computed"
            sh = ""
            if self.shift and all(x is not None for x in self.shift):
                sh = f"\norigin shift (dx, dy) = ({self.shift[0]:.4g}, {self.shift[1]:.4g}) px"
            _missing_text(ax, f"difference not computed:\n{why}{sh}\n(grids are never resampled to compare;\n"
                              f"see worst_tiles.png for both grids in map space)")
        if im_val is not None:
            cax = fig.add_subplot(gs[1, 0:2])
            if self.norm["kind"] == "categorical":
                cb = fig.colorbar(im_val, cax=cax, orientation="horizontal", ticks=self.norm["classes"])
                cb.set_label("class (shared colours)", fontsize=6)
            else:
                cb = fig.colorbar(im_val, cax=cax, orientation="horizontal", extend="both")
                cb.set_label(f"value · ONE shared scale for legacy and new: p2 {_fmt(self.norm['vmin'])} … p98 "
                             f"{_fmt(self.norm['vmax'])} (arrows = beyond)", fontsize=6)
            cb.ax.tick_params(labelsize=5.5)
        if im_d is not None:
            cax = fig.add_subplot(gs[1, 2])
            ticks = [-self.lim, -self.lim / 2, 0.0, self.lim / 2, self.lim]
            cb = fig.colorbar(im_d, cax=cax, orientation="horizontal", extend="both", ticks=ticks)
            cb.ax.set_xticklabels([_fmt(t, 3) for t in ticks])
            cb.set_label("n − l: orange = new larger, purple = legacy larger", fontsize=6)
            cb.ax.tick_params(labelsize=5.5)
        sub = f"CRS {_crs_label(self.crs)} · {('missing: ' + '; '.join(self.missing)) if self.missing else 'both sides read'}"
        fig.suptitle(f"{self._title()}\n{sub}", fontsize=8, y=0.985)
        e = self._png("overview.png", "overview", fig, [("legacy", axes[0]), ("new", axes[1]), ("diff", axes[2])],
                      {"block_factor": f_used, "notes": notes})
        return e

    # ---------------------------------------------------------------------------------------------- mask_diff
    def mask_diff(self) -> dict:
        plt = _mpl()
        from matplotlib.colors import ListedColormap, BoundaryNorm
        from matplotlib.patches import Patch
        fig = plt.figure(figsize=(8.0, 7.6))
        ax = fig.add_axes([0.09, 0.07, 0.88, 0.8])
        _setup_map_ax(ax, self.extent, self.crs, "")
        if self.gate_ok:
            cat = np.zeros(self.L.shape, dtype="float64")
            cat[self.both] = 1
            cat[self.xor_l] = 2
            cat[self.xor_n] = 3
            f = _factor(self.L.shape, MASK_PANEL_PX)
            a, _ = _block_reduce(cat, np.ones_like(self.both), f, "max")
            ext = self.L.extent
            cmap = ListedColormap(["#ffffff", C_BOTH, C_LEGACY, C_NEW])
            ax.imshow(a, extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper", cmap=cmap,
                      norm=BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], 4), interpolation="nearest", resample=False)
            nl, nn, nb = int(self.xor_l.sum()), int(self.xor_n.sum()), int(self.both.sum())
            # sparse one-side cells (a one-cell ring, a few blocks) are ~1 px at full extent: outline them thickly in
            # their own colour, above the zone lines, so a ring along a boundary cannot disappear under it
            tr = _scaled_tr(self.L.tr, f)
            for msk, col in ((self.xor_l, C_LEGACY), (self.xor_n, C_NEW)):
                if 0 < int(msk.sum()) <= 50_000:
                    mb, _ = _block_reduce(msk.astype("float64"), np.ones_like(msk), f, "any")
                    _draw_segments(ax, _mask_segments(mb > 0, tr), colors=col, linewidths=1.6, zorder=5)
            title = (f"valid-data masks · both {_fmt(nb)} · legacy only {_fmt(nl)} (blue) · new only {_fmt(nn)} "
                     f"(orange)" + (f"\n{f}×{f}-cell blocks; a block shows legacy/new-only if any cell does"
                                    if f > 1 else " · native resolution"))
            ax.legend(handles=[Patch(color=C_BOTH, label="valid both"), Patch(color=C_LEGACY, label="legacy only"),
                               Patch(color=C_NEW, label="new only"),
                               Patch(facecolor="white", edgecolor="#999999", label="no data either side")],
                      loc="upper right", framealpha=0.9)
        else:
            parts = []
            for s, col, ls in ((self.L, C_LEGACY, "solid"), (self.N, C_NEW, "dashed")):
                if not s.ok:
                    parts.append(f"{s.label} {s.err}")
                    continue
                f = _factor(s.shape, MASK_PANEL_PX)
                _, v = _block_reduce(s.arr, s.valid, f, "mean")
                _draw_segments(ax, _mask_segments(v, _scaled_tr(s.tr, f)), colors=col, linewidths=0.8,
                               linestyles=ls)
                parts.append(f"{s.label} valid {_fmt(int(s.valid.sum()))}")
            ax.set_facecolor("white")
            title = ("valid-data outlines, each on its own grid (grids differ: " + (self.gate_why or "?") + ")\n"
                     + " · ".join(parts) + " · legacy blue solid, new orange dashed")
        self._draw_zones(ax, lw=0.5, zorder=3)
        if self.zone_label:
            title += f"\n{self.zone_label} (drawn under the one-side cells)"
        ax.set_title(title, fontsize=7)
        _scalebar(ax, self.crs, self.mcrs, self.extent)
        fig.suptitle(self._title(), fontsize=8, y=0.985)
        return self._png("mask_diff.png", "mask_diff", fig, [("mask", ax)])

    # ---------------------------------------------------------------------------------------------- hist
    def hist(self) -> dict:
        plt = _mpl()
        zt = _check(self.res, "conservation.zone_totals")
        tot = _check(self.res, "conservation.total")
        n = 3 if (zt and zt.get("table")) else 2
        fig = plt.figure(figsize=(10.4, 4.4))
        gs = fig.add_gridspec(1, n, wspace=0.3, left=0.06, right=0.985, top=0.78, bottom=0.27)
        axes = [fig.add_subplot(gs[0, i]) for i in range(n)]
        panels = [("diff_hist", axes[0]), ("values_hist", axes[1])]
        ax = axes[0]
        if self.gate_ok and self.d is not None and self.both.any() and not self.categorical:
            d = self.d[self.both]
            bins = np.linspace(-self.lim, self.lim, 101)
            ax.hist(np.clip(d, -self.lim, self.lim), bins=bins, color="#7570b3", log=True)
            ax.axvline(0, color="#555555", linewidth=0.6)
            if self.thr_scalar > 0:
                for s in (-1, 1):
                    ax.axvline(s * self.thr_scalar, color="#d7301f", linestyle="--", linewidth=0.9)
                tl = f"red dashed = ±tolerance at p99.5 |legacy| ({_fmt(self.thr_scalar, 3)})"
            else:
                tl = "exact contract: every d ≠ 0 is over tolerance"
            ax.set_title(f"d = n − l on {_fmt(int(self.both.sum()))} both-valid cells (log y)\nmax |d| "
                         f"{_fmt(float(np.abs(d).max()), 3)} · {_fmt(int(self.over.sum()))} over · {tl}\n"
                         f"values beyond ±{_fmt(self.lim, 3)} counted in the end bins", fontsize=6.5)
            ax.set_xlabel("n − l")
        elif self.gate_ok and self.categorical and self.both is not None:
            ch = int(self.over.sum())
            ax.bar(["unchanged", "changed"], [int(self.both.sum()) - ch, ch], color=[C_BOTH, C_NEW], log=True)
            ax.set_title(f"categorical cells valid on both sides: {_fmt(ch)} changed class\nlegacy-only "
                         f"{_fmt(int(self.xor_l.sum()))} · new-only {_fmt(int(self.xor_n.sum()))}", fontsize=6.5)
        else:
            _missing_text(ax, "difference histogram not computed:\n" + (self.gate_why or "no both-valid cells"))
        # value distributions
        ax = axes[1]
        vals = {s.label: s.arr[s.valid] for s in (self.L, self.N) if s.ok and s.valid.any()}
        if vals:
            u = np.concatenate(list(vals.values()))
            pos = u[u > 0]
            logx = pos.size >= 0.9 * u.size and pos.size > 0 and pos.max() / pos.min() > 1e3
            if logx:
                bins = np.geomspace(pos.min(), pos.max(), 81)
            else:
                lo, hi = float(u.min()), float(u.max())
                bins = np.linspace(lo, hi if hi > lo else lo + 1, 81)
            for lab, v in vals.items():
                v2 = v[v > 0] if logx else v
                ax.hist(v2, bins=bins, histtype="step", log=True, linewidth=1.1,
                        color=C_LEGACY if lab == "legacy" else C_NEW, linestyle="solid" if lab == "legacy" else "--",
                        label=f"{lab} (n={_fmt(v.size)}, median {_fmt(float(np.median(v)), 3)})")
            if logx:
                ax.set_xscale("log")
            ax.legend(loc="upper right")
            ax.set_title("value distributions (log y" + (", log x" if logx else "") + ")\nlegacy blue solid · "
                         "new orange dashed", fontsize=6.5)
        else:
            _missing_text(ax, "no valid values on either side")
        if n == 3:
            ax = axes[2]
            table = zt["table"]
            rows = sorted(table, key=lambda r: (-(r.get("rel") if isinstance(r.get("rel"), (int, float)) and
                                                  math.isfinite(r.get("rel")) else 1e300), str(r.get("zone"))))[:40]
            rows = sorted(rows, key=lambda r: str(r.get("zone")))
            x = np.arange(len(rows))
            lv = [float(r.get("legacy") or 0.0) for r in rows]
            nv = [float(r.get("new") or 0.0) for r in rows]
            ax.bar(x - 0.2, lv, 0.4, color=C_LEGACY, label="legacy")
            ax.bar(x + 0.2, nv, 0.4, color=C_NEW, label="new")
            labs = []
            for r in rows:
                rel = r.get("rel")
                drift = isinstance(rel, (int, float)) and rel != 0
                labs.append(f"{r.get('zone')} ({_fmt(rel, 2)})" if drift else str(r.get("zone")))
            ax.set_xticks(x, labs, rotation=70, fontsize=5.5, ha="right")
            for t, r in zip(ax.get_xticklabels(), rows):
                if isinstance(r.get("rel"), (int, float)) and r.get("rel") != 0:
                    t.set_color("#b2182b")
            top = max(lv + nv + [0.0])
            ax.set_ylim(0, top * 1.2 if top > 0 else 1)
            ax.legend(loc="upper left")
            ax.set_title(f"per-zone totals ({zt.get('zones')}, rule {(zt.get('detail') or {}).get('rule')})\n"
                         f"max rel drift {_fmt(zt.get('value'), 3)} · red zone names: drift (rel in brackets)",
                         fontsize=6.5)
            panels.append(("zone_totals", ax))
        sub = ""
        if tot:
            sub = f" · total legacy {_fmt(tot.get('legacy'), 10)} vs new {_fmt(tot.get('new'), 10)} (rel {_fmt(tot.get('value'), 3)})"
        fig.suptitle(f"{self._title()}" + (f"\n{sub.lstrip(' ·')}" if sub else ""), fontsize=8, y=0.985)
        return self._png("hist.png", "hist", fig, panels)

    # ---------------------------------------------------------------------------------------------- worst tiles
    def _pick_tiles(self) -> tuple[list[tuple[int, int, int, int]], str]:
        """Up to K non-overlapping tiles. Two candidate orders are interleaved so that both kinds of defect get a
        tile: cells with the largest |d| (valid on both sides) and the densest clusters of cells valid on one side
        only. Ranking mask cells first (by row order on ties) put every tile on one data edge and hid value defects
        such as smoothing at the peaks (found in the rubric calibration)."""
        H, W = self.L.shape
        th, tw = min(TILE, H), min(TILE, W)
        dval = np.where(self.over if self.over is not None else self.both, np.abs(self.d), 0.0)
        if not (dval > 0).any():
            dval = np.where(self.both, np.abs(self.d), 0.0)
        xor = (self.xor_l | self.xor_n).astype("float64")
        dens = np.zeros_like(xor)
        if xor.any():                                   # XOR count in a (th × tw) window centred on each cell
            c = np.pad(xor, ((th // 2 + 1, th - th // 2), (tw // 2 + 1, tw - tw // 2))).cumsum(0).cumsum(1)
            dens = (c[th:th + H, tw:tw + W] - c[:H, tw:tw + W] - c[th:th + H, :W] + c[:H, :W])
            dens = np.where(xor > 0, dens, 0.0)

        def order(score: np.ndarray) -> list[int]:
            flat = score.ravel()
            idx = np.flatnonzero(flat > 0)
            if idx.size > 200_000:
                idx = idx[np.argpartition(-flat[idx], 200_000)[:200_000]]
            return idx[np.lexsort((idx, -flat[idx]))].tolist()
        lists = [order(dval), order(dens)]
        basis = "alternating: largest |d| on both-valid cells, densest valid-on-one-side clusters"
        if not lists[0] and not lists[1]:
            base = np.where(self.L.valid, self.L.arr, -np.inf) if self.L.valid.any() else np.zeros(self.L.shape)
            base = np.where(np.isfinite(base), base - base[np.isfinite(base)].min() + 1.0, 0.0) \
                if np.isfinite(base).any() else np.zeros(self.L.shape)
            lists = [order(base), []]
            basis = "no differing cells: tiles at the largest legacy values"
        chosen: list[tuple[int, int, int, int]] = []
        pos = [0, 0]
        turn = 0
        while len(chosen) < K_TILES and (pos[0] < len(lists[0]) or pos[1] < len(lists[1])):
            li = turn if pos[turn] < len(lists[turn]) else 1 - turn
            turn = 1 - turn
            seq = lists[li]
            while pos[li] < len(seq):
                k = seq[pos[li]]
                pos[li] += 1
                r, c = divmod(k, W)
                if any(r0 <= r < r0 + th and c0 <= c < c0 + tw for (r0, c0, _, _) in chosen):
                    continue
                r0 = min(max(r - th // 2, 0), H - th)
                c0 = min(max(c - tw // 2, 0), W - tw)
                if any(not (r0 + th <= a0 or a0 + th <= r0 or c0 + tw <= b0 or b0 + tw <= c0)
                       for (a0, b0, _, _) in chosen):
                    continue
                chosen.append((r0, c0, th, tw))
                break
        return chosen, basis

    def worst_tiles(self) -> dict:
        if not self.gate_ok:
            return self._gate_views()
        plt = _mpl()
        from affine import Affine
        from matplotlib.colors import ListedColormap, Normalize
        tiles, basis = self._pick_tiles()
        nrow = max(1, len(tiles))
        fig_h = 1.1 + 2.25 * nrow
        fig = plt.figure(figsize=(8.0, fig_h))
        gs = fig.add_gridspec(nrow, 3, wspace=0.32, hspace=0.5, left=0.09, right=0.985,
                              top=1 - 1.1 / fig_h, bottom=0.25 / fig_h)
        cmap, vnorm = _value_cmap(self.norm)
        dn = Normalize(-self.lim, self.lim)
        panels = []
        tile_meta = []
        tr = self.L.tr
        for i, (r0, c0, th, tw) in enumerate(tiles):
            x0, y1 = tr @ (c0, r0)
            x1, y0 = tr @ (c0 + tw, r0 + th)
            ext = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
            sub = (slice(r0, r0 + th), slice(c0, c0 + tw))
            n_over = int(self.over[sub].sum())
            n_xor = int((self.xor_l[sub] | self.xor_n[sub]).sum())
            tile_meta.append({"tile": i + 1, "rows": [r0, r0 + th], "cols": [c0, c0 + tw],
                              "bbox": [float(v) for v in ext], "over": n_over, "valid_one_side_only": n_xor})
            for j, (lab, arr, valid) in enumerate((("legacy", self.L.arr, self.L.valid), ("new", self.N.arr,
                                                                                          self.N.valid),
                                                   ("diff", self.d, self.both))):
                ax = fig.add_subplot(gs[i, j])
                _setup_map_ax(ax, ext, self.crs, "", labels=False)
                a = np.ma.array(arr[sub], mask=~valid[sub])
                if lab == "diff":
                    ax.imshow(a, extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper", cmap=_diff_cmap(), norm=dn,
                              interpolation="nearest", resample=False)
                    ov = np.where(self.xor_l[sub], 1.0, np.where(self.xor_n[sub], 2.0, np.nan))
                    ax.imshow(np.ma.masked_invalid(ov), extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper",
                              cmap=ListedColormap([C_LEG_ONLY_DIFF, C_NEW_ONLY_DIFF]), norm=Normalize(0.5, 2.5),
                              interpolation="nearest", resample=False)
                else:
                    ax.imshow(a, extent=(ext[0], ext[2], ext[1], ext[3]), origin="upper", cmap=cmap, norm=vnorm,
                              interpolation="nearest", resample=False)
                _grid_lines(ax, tr, r0, r0 + th, c0, c0 + tw, colors="#9e9e9e", linewidths=0.25)
                if lab == "diff":
                    self._draw_zones(ax, lw=1.1)
                if lab == "diff" and n_over and n_over <= 0.5 * max(int(self.both[sub].sum()), 1):
                    _draw_segments(ax, _mask_segments(self.over[sub], tr @ Affine.translation(c0, r0)),
                                   colors="black", linewidths=0.6)
                head = f"tile {i + 1} {lab}"
                if j == 0:
                    head += f"\nbbox [{_fmt(ext[0], 7)}, {_fmt(ext[1], 7)}, {_fmt(ext[2], 7)}, {_fmt(ext[3], 7)}]"
                if lab == "diff":
                    outl = "outlined" if n_over and n_over <= 0.5 * max(int(self.both[sub].sum()), 1) else \
                        ("not outlined" if n_over else "")
                    head += f"\n{n_over} over tol.{' (' + outl + ')' if outl else ''} · {n_xor} one side only"
                ax.set_title(head, fontsize=6)
                ax.tick_params(labelsize=5)
                panels.append((f"tile {i + 1} {lab}", ax))
        if not tiles:
            ax = fig.add_subplot(gs[0, :])
            _missing_text(ax, "no valid cells to show")
            panels.append(("tile 1 legacy", ax))
        tol = "exact" if self.exact else f"atol {_fmt(self.atol)} + rtol {_fmt(self.rtol)}·|l|"
        fig.suptitle(f"{self._title()}\nworst tiles at native resolution ({TILE}×{TILE} cells, cell grid drawn) · "
                     f"{basis}\nvalues: shared scale · diff ±{_fmt(self.lim, 3)}: orange new larger, purple legacy "
                     f"larger, black legacy-only, grey new-only\noutlined = over tolerance ({tol})"
                     + (f" · {self.zone_label}" if self.zone_label else ""), fontsize=7, y=0.997)
        return self._png("worst_tiles.png", "worst_tiles", fig, panels,
                         {"mode": "tiles", "tiles": tile_meta, "tile_cells": TILE})

    def _gate_views(self) -> dict:
        """Grid gate failed (or one side missing): both grids + valid-mask outlines in map space."""
        plt = _mpl()
        sides = [s for s in (self.L, self.N) if s.ok]
        fig = plt.figure(figsize=(8.0, 8.4))
        gs = fig.add_gridspec(2, 2, wspace=0.25, hspace=0.32, left=0.08, right=0.985, top=0.86, bottom=0.05)
        panels, views = [], []
        if not sides:
            ax = fig.add_subplot(gs[:, :])
            _missing_text(ax, "neither side readable:\n" + "\n".join(self.missing))
            panels.append(("corner overlay", ax))
        else:
            ref = sides[0]
            H, W = ref.shape
            win = min(GATE_WIN, H, W)
            # worst corner: largest displacement between corresponding grid corners
            corners = [(0, 0), (W, 0), (0, H), (W, H)]
            best, best_i = -1.0, 0
            if len(sides) == 2:
                Hn, Wn = self.N.shape
                ncorners = [(0, 0), (Wn, 0), (0, Hn), (Wn, Hn)]
                for i, (lc, nc) in enumerate(zip(corners, ncorners)):
                    lx, ly = self.L.tr * lc
                    nx, ny = self.N.tr * nc
                    dpx = math.hypot((nx - lx) / abs(self.L.tr.a), (ny - ly) / abs(self.L.tr.e))
                    if dpx > best + 1e-12:
                        best, best_i = dpx, i
            cc, cr = corners[best_i]
            r0 = 0 if cr == 0 else H - win
            c0 = 0 if cc == 0 else W - win
            views.append(("corner overlay", r0, c0, f"worst grid corner (corner displacement {_fmt(best, 4)} px)"))
            v = ref.valid
            if v.any():
                rows = np.flatnonzero(v.any(axis=1))
                cols = np.flatnonzero(v.any(axis=0))
                top_r = int(rows[0])
                top_c = int(np.flatnonzero(v[top_r])[0])
                left_c = int(cols[0])
                left_r = int(np.flatnonzero(v[:, left_c])[0])
                right_c = int(cols[-1])
                right_r = int(np.flatnonzero(v[:, right_c])[-1])
                for name, (r, c) in (("edge overlay north", (top_r, top_c)), ("edge overlay west", (left_r, left_c)),
                                     ("edge overlay east", (right_r, right_c))):
                    views.append((name, min(max(r - win // 2, 0), H - win), min(max(c - win // 2, 0), W - win),
                                  f"{ref.label} valid-data edge ({name.split()[-1]}-most valid cell)"))
            for k, (name, r0, c0, why) in enumerate(views[:4]):
                ax = fig.add_subplot(gs[k // 2, k % 2])
                x0, y1 = ref.tr @ (c0, r0)
                x1, y0 = ref.tr @ (c0 + win, r0 + win)
                pad = 0.75 * abs(ref.tr.a)
                ext = (min(x0, x1) - pad, min(y0, y1) - pad, max(x0, x1) + pad, max(y0, y1) + pad)
                _setup_map_ax(ax, ext, self.crs, "", labels=False)
                ax.set_facecolor("white")
                for s, col, ls, lw in ((self.L, C_LEGACY, "solid", 0.35), (self.N, C_NEW, "dashed", 0.35)):
                    if not s.ok:
                        continue
                    inv = ~s.tr
                    cs = [inv @ (ext[0], ext[1]), inv @ (ext[2], ext[3]), inv @ (ext[0], ext[3]),
                          inv @ (ext[2], ext[1])]
                    sc0 = max(int(math.floor(min(p[0] for p in cs))) - 1, 0)
                    sc1 = min(int(math.ceil(max(p[0] for p in cs))) + 1, s.shape[1])
                    sr0 = max(int(math.floor(min(p[1] for p in cs))) - 1, 0)
                    sr1 = min(int(math.ceil(max(p[1] for p in cs))) + 1, s.shape[0])
                    if sc1 <= sc0 or sr1 <= sr0:
                        continue
                    _grid_lines(ax, s.tr, sr0, sr1, sc0, sc1, colors=col, linewidths=lw, linestyles=ls, alpha=0.7)
                    from affine import Affine
                    sub = s.valid[sr0:sr1, sc0:sc1]
                    _draw_segments(ax, _mask_segments(sub, s.tr @ Affine.translation(sc0, sr0)), colors=col,
                                   linewidths=2.0, linestyles=ls)
                ax.set_title(f"{name}: {why}\nbbox [{_fmt(ext[0], 7)}, {_fmt(ext[1], 7)}, {_fmt(ext[2], 7)}, "
                             f"{_fmt(ext[3], 7)}]", fontsize=6)
                ax.tick_params(labelsize=5)
                panels.append((name, ax))
        sh = ""
        if self.shift and all(x is not None for x in self.shift):
            sh = f" · origin shift (dx, dy) = ({self.shift[0]:.4g}, {self.shift[1]:.4g}) px"
        miss = (" · " + "; ".join(self.missing)) if self.missing else ""
        fig.suptitle(f"{self._title()}\ngrid gate: {self.gate_why or 'failed'}{sh}{miss}\nboth grids drawn in map "
                     f"space (legacy blue solid, new orange dashed); thick lines = valid-data outlines; "
                     f"{GATE_WIN}×{GATE_WIN} legacy cells", fontsize=7, y=0.995)
        return self._png("worst_tiles.png", "worst_tiles", fig, panels, {"mode": "grid_overlay"})

    def render(self) -> list[dict]:
        if not (self.L.ok or self.N.ok):
            raise RuntimeError("neither side readable: " + "; ".join(self.missing))
        return [self.overview(), self.mask_diff(), self.hist(), self.worst_tiles()]


# =================================================================================================================
# Vector
# =================================================================================================================
def _read_gdf(path: Path | None, layer: str | None):
    if path is None:
        return None, "no path recorded"
    if not path.exists():
        return None, f"missing: {_short(str(path))}"
    try:
        import pyogrio
        if layer is None:
            layers = [str(x[0]) for x in pyogrio.list_layers(path)]
            if len(layers) != 1:
                return None, f"layer ambiguous: {layers}"
            layer = layers[0]
        df = pyogrio.read_dataframe(path, layer=layer)
        return df, None
    except Exception as e:                                       # shown on the plot, never hidden
        return None, f"unreadable: {type(e).__name__}: {str(e)[:120]}"


def _key_tuple(row, cols) -> tuple:
    import compare
    return tuple(compare._canon_key(row[c]) for c in cols)


def _geom_coords(g) -> np.ndarray:
    import shapely
    if g is None or g.is_empty:
        return np.zeros((0, 2))
    return shapely.get_coordinates(g)


class VectorPlotter:
    def __init__(self, run: Run, res: dict, out: Path):
        import pyproj
        self.run, self.res, self.out = run, res, out
        self.ds = res["dataset_id"]
        self.spec = (run.datasets.get(self.ds) or {}).get("compare") or {}
        self.mcrs = run.measure_crs(self.ds)
        lp = run.resolve(res.get("legacy"), "legacy_relpath")
        npth = run.resolve(res.get("new"))
        self.lpath, self.npath = lp, npth
        self.L, lerr = _read_gdf(lp, (res.get("legacy") or {}).get("layer"))
        self.N, nerr = _read_gdf(npth, (res.get("new") or {}).get("layer") or (res.get("legacy") or {}).get("layer"))
        self.missing = [f"legacy {lerr}"] if lerr else []
        if nerr:
            self.missing.append(f"new {nerr}")
        self.notes = []
        base = self.L if self.L is not None else self.N
        self.crs = base.crs if base is not None and hasattr(base, "crs") else None
        if self.L is not None and self.N is not None and self.N.crs is not None and self.L.crs is not None and \
                not pyproj.CRS(self.L.crs).equals(pyproj.CRS(self.N.crs)):
            self.N = self.N.to_crs(self.L.crs)
            self.notes.append("new reprojected to the legacy CRS for display only (CRS differs)")
        rename = self.spec.get("rename") or {}
        self.key = list(self.spec.get("key") or [])
        self.nkey = [rename.get(k, k) for k in self.key]
        self.match = self._match()
        self.metrics = self._metrics()

    def _match(self) -> dict:
        m = {"pairs": [], "legacy_only": [], "new_only": [], "keyed": False}
        if self.L is None or self.N is None or not self.key:
            return m
        if not all(k in self.L.columns for k in self.key) or not all(k in self.N.columns for k in self.nkey):
            self.notes.append(f"declared key {self.key} not present on both sides")
            return m
        m["keyed"] = True
        li = {}
        for i, (_, row) in enumerate(self.L.iterrows()):
            li.setdefault(_key_tuple(row, self.key), i)
        ni = {}
        for i, (_, row) in enumerate(self.N.iterrows()):
            ni.setdefault(_key_tuple(row, self.nkey), i)
        for k in sorted(li, key=str):
            if k in ni:
                m["pairs"].append((k, li[k], ni[k]))
            else:
                m["legacy_only"].append((k, li[k]))
        m["new_only"] = [(k, ni[k]) for k in sorted(ni, key=str) if k not in li]
        return m

    def _metrics(self) -> list[dict]:
        import geopandas as gpd
        import pyproj
        import shapely
        out = []
        if not self.match["pairs"]:
            return out
        mcrs = None
        if self.mcrs:
            try:
                c = pyproj.CRS.from_user_input(self.mcrs)
                mcrs = None if c.is_geographic else c
            except Exception:
                mcrs = None
        lg = gpd.GeoSeries([self.L.geometry.iloc[i] for _, i, _ in self.match["pairs"]], crs=self.L.crs)
        ng = gpd.GeoSeries([self.N.geometry.iloc[j] for _, _, j in self.match["pairs"]], crs=self.L.crs)
        if mcrs is not None and self.L.crs is not None:
            lg, ng = lg.to_crs(mcrs), ng.to_crs(mcrs)
            unit = "m"
        else:
            unit = "CRS units"
            self.notes.append("geometry metrics in the layer CRS (no projected measure_crs)")
        self.metric_unit = unit
        rename = self.spec.get("rename") or {}
        ignore = set(self.spec.get("ignore") or [])
        geom_l = self.L.geometry.name
        attrs = [c for c in self.L.columns if c != geom_l and c not in ignore and c not in self.key
                 and rename.get(c, c) in self.N.columns]
        for (k, i, j), a, b in zip(self.match["pairs"], lg, ng):
            rec = {"key": k, "li": i, "ni": j, "ratio": None, "haus": None}
            if a is None or b is None or a.is_empty or b.is_empty:
                rec["ratio"] = rec["haus"] = math.inf if (a is None) != (b is None) else 0.0
            else:
                na, nb = shapely.normalize(a), shapely.normalize(b)
                if shapely.equals_exact(na, nb, 0.0):
                    rec["ratio"] = rec["haus"] = 0.0
                else:
                    rec["haus"] = float(shapely.hausdorff_distance(a, b))
                    if a.geom_type in ("Polygon", "MultiPolygon") and a.area > 0:
                        rec["ratio"] = float(shapely.symmetric_difference(shapely.make_valid(a),
                                                                          shapely.make_valid(b)).area / a.area)
            worst, cls = 0.0, "identical"
            for c in attrs:
                lv, nv = self.L[c].iloc[i], self.N[rename.get(c, c)].iloc[j]
                ln, nn = _isnull(lv), _isnull(nv)
                if ln and nn:
                    continue
                if ln != nn:
                    cls = "null_mismatch"
                    worst = math.inf
                    continue
                if isinstance(lv, (int, float, np.number)) and isinstance(nv, (int, float, np.number)) \
                        and not isinstance(lv, bool):
                    lf, nf = float(lv), float(nv)
                    if lf != nf:
                        r = abs(nf - lf) / max(abs(lf), 1e-300)
                        worst = max(worst, r)
                elif str(lv) != str(nv):
                    cls = "null_mismatch" if cls == "null_mismatch" else "text_mismatch"
                    worst = math.inf
            rec["attr_rel"] = worst
            rec["attr_class"] = cls if worst == math.inf else ("identical" if worst == 0 else "numeric")
            out.append(rec)
        return out

    def _title(self) -> str:
        codes = ", ".join(self.res.get("reason_codes") or []) or "—"
        t = f"{self.ds} · compare verdict {self.res.get('verdict')} ({codes})"
        if len(t) > 100:                           # vector figures are 8 in wide: keep the codes readable
            t = f"{self.ds} · compare verdict {self.res.get('verdict')}\n({codes})"
        return t

    def _extent(self):
        bs = [g.total_bounds for g in (self.L, self.N) if g is not None and len(g) and g.geometry.notna().any()]
        if not bs:
            return None
        e = _union_extent(*[tuple(float(x) for x in b) for b in bs])
        w, h = e[2] - e[0], e[3] - e[1]
        pad = 0.03 * max(w, h, 1e-9)
        return (e[0] - pad, e[1] - pad, e[2] + pad, e[3] + pad)

    def _png(self, name, kind, fig, panels, extent, extra=None) -> dict:
        boxes = _panel_boxes(fig, panels)
        sha = _save(fig, self.out / name)
        e = {"name": name, "sha256": sha, "kind": kind, "crs": _crs_text(self.crs),
             "extent": [float(x) for x in extent] if extent else None, "norm": {},
             "panel_names": [p["name"] for p in boxes], "panels": boxes}
        if extra:
            e.update(extra)
        return e

    def _draw_layer(self, ax, gdf, color, ls, lw=0.7, alpha=1.0):
        if gdf is None or not len(gdf):
            return
        gt = set(gdf.geom_type.dropna())
        if gt & {"Point", "MultiPoint"}:
            gdf.plot(ax=ax, color=color, markersize=6, marker="o" if ls == "solid" else "x", alpha=alpha)
        elif gt & {"LineString", "MultiLineString"}:
            gdf.plot(ax=ax, color=color, linewidth=lw, linestyle=ls, alpha=alpha)
        else:
            gdf.boundary.plot(ax=ax, color=color, linewidth=lw, linestyle=ls, alpha=alpha)

    def _symdiff_gdf(self):
        import geopandas as gpd
        import shapely
        geoms = []
        for rec in self.metrics:
            if (rec["ratio"] or 0) > 0 or (rec["haus"] or 0) > 0:
                a, b = self.L.geometry.iloc[rec["li"]], self.N.geometry.iloc[rec["ni"]]
                if a is not None and b is not None and a.geom_type in ("Polygon", "MultiPolygon"):
                    geoms.append(shapely.symmetric_difference(shapely.make_valid(a), shapely.make_valid(b)))
        for _, i in self.match["legacy_only"]:
            geoms.append(self.L.geometry.iloc[i])
        for _, j in self.match["new_only"]:
            geoms.append(self.N.geometry.iloc[j])
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        return gpd.GeoSeries(geoms, crs=self.L.crs if self.L is not None else self.crs)

    def overlay(self) -> dict:
        plt = _mpl()
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        ext = self._extent()
        fig = plt.figure(figsize=(8.0, 8.0))
        ax = fig.add_axes([0.09, 0.07, 0.88, 0.78])
        if ext is None:
            _missing_text(ax, "no geometry on either side:\n" + "\n".join(self.missing))
        else:
            _setup_map_ax(ax, ext, self.crs, "")
            ax.set_facecolor("white")
            if self.L is not None and self.N is not None and self.match["keyed"]:
                sd = self._symdiff_gdf()
                if len(sd):
                    sd.plot(ax=ax, color=C_SYMDIFF, alpha=0.75, linewidth=0)
            self._draw_layer(ax, self.L, C_LEGACY, "solid", lw=0.8)
            self._draw_layer(ax, self.N, C_NEW, "dashed", lw=0.8)
            lab = 0
            for side, items, gdf, col in (("legacy only", self.match["legacy_only"], self.L, C_LEGACY),
                                          ("new only", self.match["new_only"], self.N, C_NEW)):
                for k, i in items:
                    g = gdf.geometry.iloc[i]
                    if g is None or g.is_empty or lab >= UNMATCHED_LABELS:
                        continue
                    p = g.representative_point()
                    ax.annotate(f"{side}: {'|'.join(k)}", (p.x, p.y), fontsize=6, color=col,
                                xytext=(4, 4), textcoords="offset points",
                                bbox={"facecolor": "white", "edgecolor": col, "alpha": 0.85, "pad": 1.0})
                    lab += 1
            _scalebar(ax, self.crs, self.mcrs, ext)
            ax.legend(handles=[Line2D([], [], color=C_LEGACY, label="legacy (solid)"),
                               Line2D([], [], color=C_NEW, linestyle="--", label="new (dashed)"),
                               Patch(color=C_SYMDIFF, alpha=0.75, label="symmetric difference / unmatched")],
                      loc="upper right", framealpha=0.9)
        nL = len(self.L) if self.L is not None else 0
        nN = len(self.N) if self.N is not None else 0
        nd = sum(1 for r in self.metrics if (r["ratio"] or 0) > 0 or (r["haus"] or 0) > 0)
        keyinfo = (f"key {self.key}: {len(self.match['pairs'])} matched, {nd} differ in geometry, "
                   f"{len(self.match['legacy_only'])} legacy-only, {len(self.match['new_only'])} new-only"
                   if self.match["keyed"] else "no declared key on both sides: no per-key matching")
        ax.set_title(f"overlay · legacy {nL} features · new {nN} features\n{keyinfo}", fontsize=7)
        miss = ("\nmissing: " + "; ".join(self.missing)) if self.missing else ""
        notes = ("\n" + "; ".join(self.notes)) if self.notes else ""
        fig.suptitle(f"{self._title()}\nCRS {_crs_label(self.crs)}{miss}{notes}", fontsize=8, y=0.99)
        return self._png("overlay.png", "overlay", fig, [("overlay", ax)], ext,
                         {"unmatched": {"legacy_only": ["|".join(k) for k, _ in self.match["legacy_only"]][:200],
                                        "new_only": ["|".join(k) for k, _ in self.match["new_only"]][:200]}})

    def per_feature(self) -> dict:
        plt = _mpl()
        import matplotlib as mpl
        from matplotlib.colors import BoundaryNorm, ListedColormap
        from matplotlib.patches import Patch
        ext = self._extent()
        fig = plt.figure(figsize=(10.4, 5.6))
        gs = fig.add_gridspec(1, 2, wspace=0.12, left=0.05, right=0.985, top=0.8, bottom=0.08)
        a1, a2 = fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])
        panels = [("geometry", a1), ("attributes", a2)]
        if ext is None or self.L is None:
            for a in (a1, a2):
                _missing_text(a, "legacy layer not readable:\n" + "\n".join(self.missing))
        else:
            for a in (a1, a2):
                _setup_map_ax(a, ext, self.crs, "", labels=False)
                a.set_facecolor("white")
            polygonal = any(r["ratio"] is not None for r in self.metrics)
            metric = "ratio" if polygonal else "haus"
            mname = "symdiff ratio area(L△N)/area(L)" if polygonal else f"Hausdorff ({getattr(self, 'metric_unit', '')})"
            labels = ["0 (equal)", "(0, 1e-9]", "(1e-9, 1e-6]", "(1e-6, 1e-3]", "(1e-3, 1e-2]", "(1e-2, 0.1]",
                      "> 0.1"]
            cols = ["#ffffff", "#fee6ce", "#fdd0a2", "#fdae6b", "#f16913", "#d94801", "#7f2704"]
            edge_of = ["#bdbdbd"] + cols[1:]

            def cls_of(v) -> int:            # 0 only when exactly equal; any difference is at least class 1
                return 0 if not v else 1 + int(np.digitize([v], [1e-9, 1e-6, 1e-3, 1e-2, 0.1], right=True)[0])
            vals = np.full(len(self.L), np.nan)
            for r in self.metrics:
                vals[r["li"]] = cls_of(r[metric] if r[metric] is not None else 0.0)
            g = self.L.copy()
            g["_cls"] = vals
            all_cols = cols + ["#6a3d9a", "#cab2d6"]
            all_edges = edge_of + ["#6a3d9a", "#cab2d6"]

            def paint(ax, col):              # face AND edge coloured: tiny features stay visible at full extent
                mm = g[g[col].notna()]
                if len(mm):
                    mm.plot(ax=ax, color=[all_cols[int(c)] for c in mm[col]],
                            edgecolor=[all_edges[int(c)] for c in mm[col]], linewidth=0.7)
            paint(a1, "_cls")
            um = g[g["_cls"].isna()]
            if len(um):
                um.plot(ax=a1, facecolor="#bdbdbd", edgecolor="#555555", linewidth=0.3, hatch="///")
            if self.N is not None:
                for _, j in self.match["new_only"]:
                    self.N.iloc[[j]].plot(ax=a1, facecolor="none", edgecolor=C_NEW, linewidth=1.0, hatch="xx")
            a1.legend(handles=[Patch(facecolor=c, edgecolor="#555555", label=l) for c, l in zip(cols, labels)] +
                      [Patch(facecolor="#bdbdbd", hatch="///", edgecolor="#555555", label="legacy only (unmatched)"),
                       Patch(facecolor="none", hatch="xx", edgecolor=C_NEW, label="new only (unmatched)")],
                      loc="lower right", fontsize=5.5, framealpha=0.9)
            a1.set_title(f"geometry per key: {mname}", fontsize=7)
            # attributes
            acls = np.full(len(self.L), np.nan)
            for r in self.metrics:
                ar = r["attr_rel"]
                if r["attr_class"] in ("null_mismatch", "text_mismatch"):
                    acls[r["li"]] = 7 if r["attr_class"] == "null_mismatch" else 8
                else:
                    acls[r["li"]] = cls_of(ar)
            g["_acls"] = acls
            acols = all_cols
            alabels = [x.replace("(equal)", "(all attributes equal)") for x in labels] + \
                ["null on one side only", "text differs"]
            paint(a2, "_acls")
            if len(um):
                um.plot(ax=a2, facecolor="#bdbdbd", edgecolor="#555555", linewidth=0.3, hatch="///")
            a2.legend(handles=[Patch(facecolor=c, edgecolor="#555555", label=l) for c, l in zip(acols, alabels)],
                      loc="lower right", fontsize=5.5, framealpha=0.9)
            nnull = sum(1 for r in self.metrics if r["attr_class"] == "null_mismatch")
            a2.set_title(f"attributes per key: largest relative difference over shared columns (exact, no "
                         f"tolerance)\n{nnull} keys with a null on one side only", fontsize=7)
            for k, i in self.match["legacy_only"][:UNMATCHED_LABELS]:
                p = self.L.geometry.iloc[i]
                if p is not None and not p.is_empty:
                    q = p.representative_point()
                    for a in (a1, a2):
                        a.annotate("|".join(k), (q.x, q.y), fontsize=5.5, color=C_LEGACY,
                                   bbox={"facecolor": "white", "edgecolor": C_LEGACY, "alpha": 0.85, "pad": 0.8})
            for r in self.metrics:
                if r["attr_class"] == "null_mismatch":
                    p = self.L.geometry.iloc[r["li"]]
                    if p is not None and not p.is_empty:
                        q = p.representative_point()
                        a2.annotate("|".join(r["key"]), (q.x, q.y), fontsize=5.5, color="#6a3d9a",
                                    bbox={"facecolor": "white", "edgecolor": "#6a3d9a", "alpha": 0.85, "pad": 0.8})
            _scalebar(a1, self.crs, self.mcrs, ext)
        miss = ("\nmissing: " + "; ".join(self.missing)) if self.missing else ""
        fig.suptitle(f"{self._title()}\nper-feature agreement drawn on legacy geometries · CRS {_crs_label(self.crs)}"
                     f"{miss}", fontsize=8, y=0.99)
        return self._png("per_feature.png", "per_feature", fig, panels, ext)

    def worst_features(self) -> dict:
        plt = _mpl()
        import shapely
        cand = []
        for k, i in self.match["legacy_only"]:
            cand.append(("legacy only", k, i, None, math.inf, math.inf))
        for k, j in self.match["new_only"]:
            cand.append(("new only", k, None, j, math.inf, math.inf))
        rest = [r for r in self.metrics if (r["ratio"] or 0) > 0 or (r["haus"] or 0) > 0]
        rest.sort(key=lambda r: (-(r["ratio"] or 0), -(r["haus"] or 0), str(r["key"])))
        for r in rest:
            cand.append(("differs", r["key"], r["li"], r["ni"], r["ratio"], r["haus"]))
        cand = cand[:4]
        reference_only = False
        if not cand and self.metrics:               # nothing differs: show the most detailed features for reference
            import shapely
            ranked = sorted(self.metrics, key=lambda r: (-int(shapely.get_num_coordinates(self.L.geometry.iloc[r["li"]])),
                                                         str(r["key"])))
            cand = [("equal (reference view)", r["key"], r["li"], r["ni"], 0.0, 0.0) for r in ranked[:4]]
            reference_only = True
        fig = plt.figure(figsize=(8.0, 8.4))
        gs = fig.add_gridspec(2, 2, wspace=0.25, hspace=0.35, left=0.08, right=0.985, top=0.86, bottom=0.05)
        panels, meta = [], []
        if not cand:
            ax = fig.add_subplot(gs[:, :])
            why = "no feature differs in geometry and no key is unmatched" if self.match["keyed"] else \
                ("no declared key on both sides" if not self.missing else "; ".join(self.missing))
            _missing_text(ax, why)
            panels.append(("feature 1", ax))
        for n, (kind, k, i, j, ratio, haus) in enumerate(cand):
            ax = fig.add_subplot(gs[n // 2, n % 2])
            gl = self.L.geometry.iloc[i] if i is not None else None
            gn = self.N.geometry.iloc[j] if j is not None else None
            gs_ = [g for g in (gl, gn) if g is not None and not g.is_empty]
            if not gs_:
                _missing_text(ax, "empty geometry")
                panels.append((f"feature {n + 1}", ax))
                continue
            b = shapely.union_all(gs_).bounds
            w, h = b[2] - b[0], b[3] - b[1]
            pad = 0.12 * max(w, h, 1e-9)
            ext = (b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad)
            _setup_map_ax(ax, ext, self.crs, "", labels=False)
            ax.set_facecolor("white")
            if gl is not None and gn is not None and gl.geom_type in ("Polygon", "MultiPolygon"):
                import geopandas as gpd
                sd = shapely.symmetric_difference(shapely.make_valid(gl), shapely.make_valid(gn))
                if not sd.is_empty:
                    gpd.GeoSeries([sd]).plot(ax=ax, color=C_SYMDIFF, alpha=0.6, linewidth=0)
            for g, col, ls, mk in ((gl, C_LEGACY, "-", "o"), (gn, C_NEW, "--", "x")):
                if g is None:
                    continue
                for part in _parts(g):
                    xy = _geom_coords(part.exterior if part.geom_type == "Polygon" else part)
                    if xy.size:
                        ax.plot(xy[:, 0], xy[:, 1], color=col, linestyle=ls, linewidth=0.9, marker=mk,
                                markersize=2.5)
                    if part.geom_type == "Polygon":
                        for ring in part.interiors:
                            xy = _geom_coords(ring)
                            ax.plot(xy[:, 0], xy[:, 1], color=col, linestyle=ls, linewidth=0.9, marker=mk,
                                    markersize=2.5)
            unit = getattr(self, "metric_unit", "")
            ax.set_title(f"feature {n + 1}: {kind} · key {'|'.join(k)}\nsymdiff ratio {_fmt(ratio, 3)} · Hausdorff "
                         f"{_fmt(haus, 3)} {unit}\nbbox [{_fmt(ext[0], 7)}, {_fmt(ext[1], 7)}, {_fmt(ext[2], 7)}, "
                         f"{_fmt(ext[3], 7)}]", fontsize=6)
            ax.tick_params(labelsize=5)
            panels.append((f"feature {n + 1}", ax))
            meta.append({"feature": n + 1, "key": list(k), "kind": kind,
                         "symdiff_ratio": None if ratio is None or not math.isfinite(ratio) else ratio,
                         "hausdorff": None if haus is None or not math.isfinite(haus) else haus,
                         "bbox": [float(x) for x in ext]})
        head = ("no feature differs in geometry and no key is unmatched: the 4 most detailed features shown for "
                "reference" if reference_only else "worst features: unmatched keys first, then largest symdiff ratio")
        fig.suptitle(f"{self._title()}\n{head}\nvertices drawn: legacy blue o solid, new orange x dashed, red = "
                     f"symmetric difference", fontsize=7, y=0.995)
        return self._png("worst_features.png", "worst_features", fig, panels, self._extent(), {"features": meta})

    def render(self) -> list[dict]:
        if self.L is None and self.N is None:
            raise RuntimeError("neither side readable: " + "; ".join(self.missing))
        if self.L is None:
            self.L, self.N = self.N, None
            self.notes.append("legacy unreadable: only the new layer is drawn (as if legacy)")
        return [self.overlay(), self.per_feature(), self.worst_features()]


def _parts(g):
    if g is None:
        return []
    if hasattr(g, "geoms"):
        out = []
        for p in g.geoms:
            out.extend(_parts(p))
        return out
    return [g]


def _isnull(v) -> bool:
    if v is None:
        return True
    try:
        return bool(isinstance(v, float) and math.isnan(v)) or (hasattr(v, "__class__") and
                                                                 v.__class__.__name__ in ("NAType", "NaTType"))
    except Exception:
        return False


# =================================================================================================================
# Table
# =================================================================================================================
def _read_frame(run: Run, res: dict, side: str, path: Path | None):
    import compare
    if path is None or not path.exists():
        return None, f"missing: {_short(str(path) if path else None)}"
    spec = {"compare": (run.datasets.get(res["dataset_id"]) or {}).get("compare") or {},
            "sheet": ((run.datasets.get(res["dataset_id"]) or {}).get("legacy") or {}).get("sheet")}
    try:
        if str(path).lower().endswith(".gpkg"):
            layer = (res.get(side) or {}).get("layer") or (res.get("legacy") or {}).get("layer")
            if layer is None:
                import pyogrio
                layer = str(pyogrio.list_layers(path)[0][0])
            return compare._read_vector(str(path), layer, side), None
        return compare._read_table(SimpleNamespace(spec=spec), str(path), side), None
    except Exception as e:                                       # drawn as "unreadable", never hidden
        return None, f"unreadable: {type(e).__name__}: {str(e)[:120]}"


def _num(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class TablePlotter:
    def __init__(self, run: Run, res: dict, out: Path):
        self.run, self.res, self.out = run, res, out
        self.ds = res["dataset_id"]
        self.spec = (run.datasets.get(self.ds) or {}).get("compare") or {}
        self.L, le = _read_frame(run, res, "legacy", run.resolve(res.get("legacy"), "legacy_relpath"))
        self.N, ne = _read_frame(run, res, "new", run.resolve(res.get("new")))
        self.missing = [x for x in (f"legacy {le}" if le else None, f"new {ne}" if ne else None) if x]

    def render(self) -> list[dict]:
        import compare
        plt = _mpl()
        if self.L is None and self.N is None:
            raise RuntimeError("neither side readable: " + "; ".join(self.missing))
        rename = self.spec.get("rename") or {}
        key = list(self.spec.get("key") or [])
        fig = plt.figure(figsize=(10.4, 6.4))
        panels = []
        notes = []
        cols = []
        if self.L is not None and self.N is not None:
            ignore = set(self.spec.get("ignore") or [])
            for c in self.L.columns:
                nc = rename.get(c, c)
                if c in key or c in ignore or nc not in self.N.columns:
                    continue
                lv = [_num(v) for v in self.L.data[c]]
                if sum(v is not None for v in lv) >= max(1, len(lv) // 2):
                    cols.append((c, nc))
            cols = cols[:6]
        nk = key and self.L is not None and self.N is not None and all(k in self.L.columns for k in key) and \
            all(rename.get(k, k) in self.N.columns for k in key)
        gs = fig.add_gridspec(2, max(1, len(cols)) if cols else 1, height_ratios=[1, 1.15], hspace=0.6, wspace=0.35,
                              left=0.06, right=0.985, top=0.84, bottom=0.08)
        leg_only, new_only, pairs = [], [], []
        if nk:
            li = {tuple(compare._canon_key(self.L.data[k][i]) for k in key): i for i in range(self.L.nrows)}
            ni = {tuple(compare._canon_key(self.N.data[rename.get(k, k)][i]) for k in key): i
                  for i in range(self.N.nrows)}
            pairs = [(k, li[k], ni[k]) for k in sorted(li, key=str) if k in ni]
            leg_only = [k for k in sorted(li, key=str) if k not in ni]
            new_only = [k for k in sorted(ni, key=str) if k not in li]
        elif self.L is not None and self.N is not None:
            notes.append("no declared key on both sides: rows not matched")
        scat_pts = []
        for j, (c, nc) in enumerate(cols):
            ax = fig.add_subplot(gs[0, j])
            labels, vals, nulls = [], [], []
            for k, i, m in pairs:
                lv, nv = _num(self.L.data[c][i]), _num(self.N.data[nc][m])
                labels.append("|".join(k))
                if lv is None or nv is None:
                    vals.append(0.0)
                    nulls.append(len(vals) - 1 if (lv is None) != (nv is None) else None)
                else:
                    vals.append((nv - lv) / abs(lv) if lv != 0 else (0.0 if nv == 0 else math.copysign(1.0, nv)))
                    scat_pts.append((c, lv, nv))
            x = np.arange(len(vals))
            ax.bar(x, vals, color=[C_NEW if v > 0 else "#5e3c99" for v in vals])
            for idx in [n for n in nulls if n is not None]:
                ax.plot(idx, 0, marker="X", color="#b2182b", markersize=6)
            ax.axhline(0, color="#555555", linewidth=0.5)
            ax.set_xticks(x, labels, rotation=80, fontsize=4.5)
            ax.set_title(f"{c}: (n − l)/|l| per key\nX = null on one side only", fontsize=6)
            ax.tick_params(axis="y", labelsize=5)
            panels.append((f"residuals {c}", ax))
        if not cols:
            ax = fig.add_subplot(gs[0, 0])
            _missing_text(ax, "no shared numeric columns to compare" + (
                ("\n" + "; ".join(self.missing)) if self.missing else ""))
            panels.append(("residuals", ax))
        ax = fig.add_subplot(gs[1, :])
        if scat_pts:
            import matplotlib as mpl
            base = mpl.colormaps["tab10"]
            pos = all(lv > 0 and nv > 0 for _, lv, nv in scat_pts)
            for j, (c, _) in enumerate(cols):
                pts = [(lv, nv) for cc, lv, nv in scat_pts if cc == c]
                if pts:
                    a = np.array(pts)
                    ax.scatter(a[:, 0], a[:, 1], s=10, color=base(j), label=c)
            allv = np.array([[lv, nv] for _, lv, nv in scat_pts])
            lo, hi = float(allv.min()), float(allv.max())
            ax.plot([lo, hi], [lo, hi], color="#555555", linewidth=0.7, linestyle="--", label="y = x")
            if pos and hi / max(lo, 1e-300) > 100:
                ax.set_xscale("log")
                ax.set_yscale("log")
            ax.set_xlabel("legacy")
            ax.set_ylabel("new")
            ax.legend(loc="upper left", fontsize=5.5)
        else:
            _missing_text(ax, "nothing to scatter")
        ax.set_title(f"new vs legacy for matched keys (y = x dashed) · legacy-only keys {leg_only[:10]} · new-only "
                     f"keys {new_only[:10]}", fontsize=6.5)
        panels.append(("scatter", ax))
        codes = ", ".join(self.res.get("reason_codes") or []) or "—"
        miss = ("\nmissing: " + "; ".join(self.missing)) if self.missing else ""
        fig.suptitle(f"{self.ds} · compare verdict {self.res.get('verdict')} ({codes}) · key {key}{miss}"
                     + (("\n" + "; ".join(notes)) if notes else ""), fontsize=8, y=0.99)
        boxes = _panel_boxes(fig, panels)
        sha = _save(fig, self.out / "residuals.png")
        return [{"name": "residuals.png", "sha256": sha, "kind": "residuals", "crs": None, "extent": None,
                 "norm": {}, "panel_names": [p["name"] for p in boxes], "panels": boxes,
                 "unmatched": {"legacy_only": ["|".join(k) for k in leg_only][:200],
                               "new_only": ["|".join(k) for k in new_only][:200]}}]


# =================================================================================================================
# run
# =================================================================================================================
def plot_dataset(run: Run, res: dict) -> dict:
    ds = res["dataset_id"]
    out = run.verify / "plots" / ds
    if out.exists():                                   # stale-result removal (D6-17): only our own file types
        for p in sorted(out.iterdir()):
            if p.is_file() and (p.suffix == ".png" or p.name == "plots.json"):
                p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    fam = res.get("family")
    cls = {"raster": RasterPlotter, "vector": VectorPlotter, "table": TablePlotter}.get(fam)
    if cls is None:
        raise RuntimeError(f"family {fam!r} has no plots")
    p = cls(run, res, out)
    pngs = p.render()
    lpath = run.resolve(res.get("legacy"), "legacy_relpath")
    npath = run.resolve(res.get("new"))
    doc = {"schema": PLOTS_SCHEMA, "generator": GENERATOR, "version": VERSION,
           "generator_sha256": ac.sha256_file(Path(__file__)), "matplotlib": matplotlib.__version__, "dpi": DPI,
           "run_id": run.run_id, "dataset_id": ds, "family": fam, "compare_result_id": res.get("result_id"),
           "compare_verdict": res.get("verdict"), "reason_codes": list(res.get("reason_codes") or []),
           "inputs": {"legacy_sha": _file_sha(lpath), "new_sha": _file_sha(npath),
                      "legacy_path": (res.get("legacy") or {}).get("path"), "new_path": (res.get("new") or {}).get("path")},
           "missing": list(getattr(p, "missing", [])), "notes": list(getattr(p, "notes", [])),
           "png": sorted(pngs, key=lambda e: e["name"])}
    ac.dump_json(out / "plots.json", doc)
    return doc


def cmd_run(args) -> int:
    run = Run(args.run)
    why = run.sealed()
    if why:
        print(f"refused: {why}", file=sys.stderr)
        return EXIT_SEALED
    results = run.results(args.dataset or None)
    if not results:
        print("no compare results for this run" + (f" matching {args.dataset}" if args.dataset else "") +
              ": run `compare.py run --run <dir>` first", file=sys.stderr)
        return EXIT_SETUP
    missing = sorted(set(args.dataset or []) - set(results))
    rc = EXIT_OK
    summary = []
    for m in missing:
        summary.append({"dataset_id": m, "status": "no compare result"})
        rc = EXIT_SOME
    for ds, res in sorted(results.items()):
        try:
            doc = plot_dataset(run, res)
            summary.append({"dataset_id": ds, "status": "plotted", "png": [e["name"] for e in doc["png"]],
                            "missing": doc["missing"]})
        except Exception as e:                                   # reported per dataset, exit 1, never silent
            summary.append({"dataset_id": ds, "status": f"not plotted: {type(e).__name__}: {e}"})
            rc = EXIT_SOME
    if args.json:
        print(json.dumps({"run_id": run.run_id, "datasets": summary}, sort_keys=True, indent=1))
    else:
        for s in summary:
            extra = f" ({'; '.join(s['missing'])})" if s.get("missing") else ""
            pngs = (": " + ", ".join(f"verify/plots/{s['dataset_id']}/{n}" for n in s["png"])) if s.get("png") else ""
            print(f"{s['dataset_id']}: {s['status']}{extra}{pngs}")
    return rc


# =================================================================================================================
# Visual review (06 §4h, DESIGN §4.6)
# =================================================================================================================
FINDING_REQUIRED = ("dataset_id", "question_id", "category", "answer", "effect", "plot", "plot_sha256", "panel",
                    "note", "model")
FINDING_OPTIONAL = ("finding_id", "run_id", "location", "plots_seen")
FINDING_ADDED = ("rubric_id", "rubric_sha256", "created_at")
Q_ALL = "Q-all"
FID_RE = re.compile(r"^vf-[A-Za-z0-9_.-]{1,64}$")


def load_rubric() -> tuple[dict, str]:
    import yaml
    data = RUBRIC_PATH.read_bytes()
    return yaml.safe_load(data.decode("utf-8")), ac.sha256_bytes(data)


def _plots_doc(run: Run, ds: str) -> dict | None:
    p = run.verify / "plots" / ds / "plots.json"
    if not p.is_file():
        return None
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def _png_entry(doc: dict, plot: str, ds: str) -> dict | None:
    name = plot.split("/")[-1]
    if plot != f"plots/{ds}/{name}":
        return None
    for e in doc.get("png") or []:
        if e.get("name") == name:
            return e
    return None


def validate_finding(run: Run, f: Any, rubric: dict, existing_ids: set) -> tuple[list[str], dict | None]:
    errs: list[str] = []
    if not isinstance(f, dict):
        return ["a finding must be a JSON object"], None
    if vrc.is_view(f):
        return validate_view_finding(run, f, rubric, existing_ids)
    if vrc.is_skip(f):
        return validate_skip_finding(run, f, existing_ids)
    unknown = sorted(set(f) - set(FINDING_REQUIRED) - set(FINDING_OPTIONAL))
    added = sorted(set(f) & set(FINDING_ADDED))
    if added:
        errs.append(f"fields set by review-add, not by the reviewer: {added}")
        unknown = [u for u in unknown if u not in added]
    if unknown:
        errs.append(f"unknown fields: {unknown}")
    for k in FINDING_REQUIRED:
        if k not in f:
            errs.append(f"missing field {k}")
    if errs:
        return errs, None
    ds, q, ans, eff, cat = f["dataset_id"], f["question_id"], f["answer"], f["effect"], f["category"]
    if f.get("run_id") not in (None, run.run_id):
        errs.append(f"run_id {f.get('run_id')!r} is not this run ({run.run_id})")
    res = run.results([ds]).get(ds) if isinstance(ds, str) else None
    if res is None:
        errs.append(f"dataset {ds!r} has no compare result in this run")
    if ans not in ("yes", "no", "cannot_tell"):
        errs.append("answer must be yes | no | cannot_tell")
    if eff not in ("flag", "none"):
        errs.append("effect must be flag | none")
    if eff == "flag" and ans != "yes":
        errs.append("effect flag requires answer yes")
    if ans == "yes" and eff != "flag":
        errs.append("answer yes (defect present) requires effect flag: a reviewer may only flag")
    if cat not in ac.VISUAL_CATEGORIES:
        errs.append(f"category {cat!r} not in the closed list {ac.VISUAL_CATEGORIES}")
    qs = {x["id"]: x for x in rubric.get("questions", [])}
    fam = (res or {}).get("family")
    if q == Q_ALL:
        if ans != "no" or eff != "none" or cat != "none":
            errs.append(f"{Q_ALL} records 'looked, nothing found': answer no, effect none, category none")
    elif q not in qs:
        errs.append(f"question_id {q!r} not in rubric {rubric.get('id')} (Q1…Q7 or {Q_ALL})")
    else:
        qd = qs[q]
        if fam and fam not in qd.get("families", []):
            errs.append(f"{q} is not asked of {fam} datasets (rubric families {qd.get('families')})")
        if ans == "yes" and cat not in qd.get("categories", []):
            errs.append(f"category {cat!r} does not answer {q}; allowed: {qd.get('categories')}")
        if ans != "yes" and cat != "none":
            errs.append("category must be none unless the answer is yes")
    note = f["note"]
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        errs.append(f"note must be a string of at most {NOTE_MAX} characters")
    elif ans == "yes" and not note.strip():
        errs.append("a yes answer needs a note saying what is seen where")
    if not isinstance(f["model"], str) or not f["model"].strip():
        errs.append("model must name the reviewing model")
    if not isinstance(f["panel"], str) or not f["panel"].strip():
        errs.append("panel must name a panel of the plot")
    fid = f.get("finding_id")
    if fid is not None and (not isinstance(fid, str) or not FID_RE.match(fid)):
        errs.append("finding_id must look like vf-<letters, digits, . _ ->")
    if errs:
        return errs, None
    doc = _plots_doc(run, ds)
    if doc is None:
        return [f"no plots for {ds}: run `plot.py run --run <dir> --dataset {ds}` first"], None
    if doc.get("compare_result_id") != res.get("result_id"):
        return [f"plots of {ds} are stale (made from compare result {doc.get('compare_result_id')}, current "
                f"{res.get('result_id')}): rerun plot.py run"], None

    def check_plot(plot, sha) -> tuple[str | None, dict | None]:
        if not isinstance(plot, str):
            return "plot must be a path like plots/<dataset>/<name>.png", None
        e = _png_entry(doc, plot, ds)
        if e is None:
            return f"plot {plot!r} is not listed in plots/{ds}/plots.json of this run", None
        if sha != e.get("sha256"):
            return f"plot_sha256 does not match plots.json of this run for {plot}", None
        fp = run.verify / plot
        if not fp.is_file() or ac.sha256_file(fp) != sha:
            return f"plot_sha256 does not match the bytes of {plot}", None
        return None, e
    err, entry = check_plot(f["plot"], f["plot_sha256"])
    if err:
        return [err], None
    kind = entry.get("kind") or Path(f["plot"]).stem
    out = {k: f[k] for k in FINDING_REQUIRED}
    if q == Q_ALL:
        seen = f.get("plots_seen")
        want = {f"plots/{ds}/{e['name']}": e["sha256"] for e in doc.get("png") or []}
        if not isinstance(seen, list):
            return [f"{Q_ALL} needs plots_seen: [{{plot, plot_sha256}}] for every plot of {ds}: {sorted(want)}"], None
        got = {}
        for s in seen:
            if not isinstance(s, dict) or set(s) != {"plot", "plot_sha256"}:
                return ["plots_seen entries are {plot, plot_sha256}"], None
            e2, _ = check_plot(s["plot"], s["plot_sha256"])
            if e2:
                return [e2], None
            got[s["plot"]] = s["plot_sha256"]
        if got != want:
            return [f"{Q_ALL} must cite every plot of {ds}; missing {sorted(set(want) - set(got))}"], None
        if f["plot"] not in got:
            return [f"{Q_ALL}: plot must be one of plots_seen"], None
        if f["panel"] != "all":
            return [f"{Q_ALL}: panel must be 'all'"], None
        out["plots_seen"] = sorted(seen, key=lambda s: s["plot"])
    else:
        allowed = (qs[q].get("plots") or {}).get(fam, [])
        if kind not in allowed:
            return [f"{q} is answered from {allowed} for {fam} data, not from {kind}"], None
        if f["panel"] not in (entry.get("panel_names") or []):
            return [f"panel {f['panel']!r} is not a panel of {f['plot']}: {entry.get('panel_names')}"], None
        if f.get("plots_seen") is not None:
            return ["plots_seen is only for a Q-all receipt"], None
    loc = f.get("location")
    if loc is None:
        if ans == "yes":
            return ["a yes answer needs location {crs, bbox} in the plot's map coordinates"], None
        loc = {"crs": entry.get("crs"), "bbox": entry.get("extent")}
    else:
        if not isinstance(loc, dict) or set(loc) != {"crs", "bbox"}:
            return ["location must be {crs, bbox:[xmin, ymin, xmax, ymax]}"], None
        bb = loc.get("bbox")
        if not (isinstance(bb, list) and len(bb) == 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                               and math.isfinite(x) for x in bb)
                and bb[0] <= bb[2] and bb[1] <= bb[3]):
            return ["location.bbox must be 4 finite numbers xmin ≤ xmax, ymin ≤ ymax"], None
        if entry.get("crs") and loc.get("crs") != entry.get("crs"):
            return [f"location.crs {loc.get('crs')!r} differs from the plot's CRS {entry.get('crs')!r}"], None
        ext = entry.get("extent")
        if ext:
            w, h = ext[2] - ext[0], ext[3] - ext[1]
            m = 0.05 * max(w, h)
            if bb[2] < ext[0] - m or bb[0] > ext[2] + m or bb[3] < ext[1] - m or bb[1] > ext[3] + m:
                return [f"location.bbox {bb} lies outside the plot extent {ext}"], None
    out["location"] = loc
    out["run_id"] = run.run_id
    core = {k: out[k] for k in ("run_id", "dataset_id", "question_id", "answer", "effect", "category", "plot",
                                "plot_sha256", "panel", "location", "note", "model")}
    out["finding_id"] = fid or ("vf-" + ac.sha256_bytes(ac.cjson(core))[:16])
    if out["finding_id"] in existing_ids:
        return [f"finding_id {out['finding_id']} already recorded (identical finding or reused id)"], None
    return [], out


# ---- view receipts (GeoLibre map views, skills/map/views.py; acceptance rule in lib/visual_receipt.py)
VIEW_REQUIRED = ("dataset_id", "question_id", "category", "answer", "effect", "view", "views_seen", "note", "model")
VIEW_OPTIONAL = ("finding_id", "run_id", "location", "engine", "cells_read", "layer", "observations")
VIEW_ADDED = FINDING_ADDED + ("receipt",)
VIEW_LAYERS = ("legacy", "new", "diff", "validity", "zones")
OBS_MAX = 20


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def validate_view_finding(run: Run, f: dict, rubric: dict, existing_ids: set) -> tuple[list[str], dict | None]:
    """A receipt from the GeoLibre map views: cites view projects by path + sha256 (views.json of this run), the
    raster engine used and the cells read from the identify popup. Same answer/effect/category rules as a PNG receipt;
    a reviewer may only flag."""
    errs: list[str] = []
    unknown = sorted(set(f) - set(VIEW_REQUIRED) - set(VIEW_OPTIONAL))
    added = sorted(set(f) & set(VIEW_ADDED))
    if added:
        errs.append(f"fields set by review-add, not by the reviewer: {added}")
        unknown = [u for u in unknown if u not in added]
    png_only = sorted(set(unknown) & {"plot", "plot_sha256", "panel", "plots_seen"})
    if png_only:
        errs.append(f"a view receipt cites views, not plots: drop {png_only} (or send a PNG receipt without `view`)")
        unknown = [u for u in unknown if u not in png_only]
    if unknown:
        errs.append(f"unknown fields: {unknown}")
    for k in VIEW_REQUIRED:
        if k not in f:
            errs.append(f"missing field {k}")
    if errs:
        return errs, None
    ds, q, ans, eff, cat = f["dataset_id"], f["question_id"], f["answer"], f["effect"], f["category"]
    if f.get("run_id") not in (None, run.run_id):
        errs.append(f"run_id {f.get('run_id')!r} is not this run ({run.run_id})")
    res = run.results([ds]).get(ds) if isinstance(ds, str) else None
    if res is None:
        errs.append(f"dataset {ds!r} has no compare result in this run")
    fam = (res or {}).get("family")
    if res is not None and fam not in vrc.REQUIRED_VIEWS:
        errs.append(f"{ds} is a {fam} dataset: map views exist only for rasters and vectors")
    if ans not in ("yes", "no", "cannot_tell"):
        errs.append("answer must be yes | no | cannot_tell")
    if eff not in ("flag", "none"):
        errs.append("effect must be flag | none")
    if eff == "flag" and ans != "yes":
        errs.append("effect flag requires answer yes")
    if ans == "yes" and eff != "flag":
        errs.append("answer yes (defect present) requires effect flag: a reviewer may only flag")
    if cat not in ac.VISUAL_CATEGORIES:
        errs.append(f"category {cat!r} not in the closed list {ac.VISUAL_CATEGORIES}")
    qs = {x["id"]: x for x in rubric.get("questions", [])}
    if q == Q_ALL:
        if ans != "no" or eff != "none" or cat != "none":
            errs.append(f"{Q_ALL} records 'looked at every view, nothing found': answer no, effect none, "
                        "category none")
    elif q not in qs:
        errs.append(f"question_id {q!r} not in rubric {rubric.get('id')} (Q1…Q7 or {Q_ALL})")
    else:
        qd = qs[q]
        if fam and fam not in qd.get("families", []):
            errs.append(f"{q} is not asked of {fam} datasets (rubric families {qd.get('families')})")
        if ans == "yes" and cat not in qd.get("categories", []):
            errs.append(f"category {cat!r} does not answer {q}; allowed: {qd.get('categories')}")
        if ans != "yes" and cat != "none":
            errs.append("category must be none unless the answer is yes")
        if ans == "no":
            errs.append(f"a view receipt that found nothing is one {Q_ALL} receipt (answer no) for the dataset")
    note = f["note"]
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        errs.append(f"note must be a string of at most {NOTE_MAX} characters")
    elif ans == "yes" and not note.strip():
        errs.append("a yes answer needs a note saying what is seen where")
    if not isinstance(f["model"], str) or not f["model"].strip():
        errs.append("model must name the reviewing model")
    fid = f.get("finding_id")
    if fid is not None and (not isinstance(fid, str) or not FID_RE.match(fid)):
        errs.append("finding_id must look like vf-<letters, digits, . _ ->")
    layer = f.get("layer")
    parts = layer.split("+") if isinstance(layer, str) else None
    if layer is not None and not (parts and all(x in VIEW_LAYERS for x in parts) and len(set(parts)) == len(parts)):
        errs.append(f"layer must be one of {VIEW_LAYERS}, or several joined with '+' (e.g. legacy+new)")
    obs = f.get("observations")
    if obs is not None:
        if not isinstance(obs, list) or len(obs) > OBS_MAX or not all(
                isinstance(o, dict) and set(o) <= {"what", "value", "unit"} and {"what", "value"} <= set(o)
                and isinstance(o["what"], str) and 0 < len(o["what"].strip()) <= NOTE_MAX and _finite(o["value"])
                and (o.get("unit") is None or (isinstance(o["unit"], str) and len(o["unit"]) <= 20))
                for o in obs):
            errs.append(f"observations must be a list of at most {OBS_MAX} {{what (≤ {NOTE_MAX} characters), "
                        "value (a number), unit?}}")
    if ans == "yes" and not obs:
        errs.append("a flag from the map needs observations with numbers, e.g. "
                    "[{\"what\": \"cells valid on legacy only along the west edge\", \"value\": 812, "
                    "\"unit\": \"cells\"}]")
    loc = f.get("location")
    if loc is None:
        if ans == "yes":
            errs.append("a yes answer needs location {crs: \"EPSG:4326\", bbox: [west, south, east, north]} "
                        "(lon/lat as the popup prints them)")
    elif not isinstance(loc, dict) or set(loc) != {"crs", "bbox"} or loc.get("crs") != "EPSG:4326":
        errs.append("location must be {crs: \"EPSG:4326\", bbox: [west, south, east, north]} for a view receipt")
    else:
        bb = loc.get("bbox")
        if not (isinstance(bb, list) and len(bb) == 4 and all(_finite(x) for x in bb) and bb[0] <= bb[2]
                and bb[1] <= bb[3] and -180 <= bb[0] and bb[2] <= 180 and -90 <= bb[1] and bb[3] <= 90):
            errs.append("location.bbox must be 4 finite lon/lat numbers west ≤ east, south ≤ north")
    if errs:
        return errs, None
    probs = vrc.check_view_on_disk(run.dir, run.run_id, f, fam)
    if probs:
        return probs, None
    out = {k: f[k] for k in VIEW_REQUIRED}
    out["views_seen"] = sorted(f["views_seen"], key=lambda s: s["project"])
    out["engine"] = f.get("engine")
    out["cells_read"] = sorted(f.get("cells_read") or [], key=lambda c: str(c["cell"]))
    for k in ("location", "layer", "observations"):
        if f.get(k) is not None:
            out[k] = f[k]
    out["run_id"] = run.run_id
    out["receipt"] = "view"
    core = {k: out.get(k) for k in ("run_id", "dataset_id", "question_id", "answer", "effect", "category", "view",
                                     "views_seen", "engine", "cells_read", "location", "note", "model")}
    out["finding_id"] = fid or ("vf-" + ac.sha256_bytes(ac.cjson(core))[:16])
    if out["finding_id"] in existing_ids:
        return [f"finding_id {out['finding_id']} already recorded (identical finding or reused id)"], None
    return [], out


# ---- skipped receipts (nothing to show: identical to legacy, or reviewed before byte for byte)
SKIP_REQUIRED = ("dataset_id", "skipped", "model")
SKIP_OPTIONAL = ("finding_id", "run_id", "note")


def validate_skip_finding(run: Run, f: dict, existing_ids: set) -> tuple[list[str], dict | None]:
    """`{dataset_id, skipped: "skipped: identical (<sha256>)", model, note?}` (review-template prints it). Accepted only
    while lib/visual_receipt.skip_problem holds: the compare result says there is nothing to show and the new
    output's bytes, hashed again now, are that sha."""
    errs: list[str] = []
    unknown = sorted(set(f) - set(SKIP_REQUIRED) - set(SKIP_OPTIONAL))
    if unknown:
        errs.append(f"unknown fields for a skipped receipt: {unknown} (only {SKIP_REQUIRED + SKIP_OPTIONAL})")
    for k in SKIP_REQUIRED:
        if k not in f:
            errs.append(f"missing field {k}")
    if errs:
        return errs, None
    ds = f["dataset_id"]
    if f.get("run_id") not in (None, run.run_id):
        return [f"run_id {f.get('run_id')!r} is not this run ({run.run_id})"], None
    if not isinstance(f["model"], str) or not f["model"].strip():
        return ["model must name the reviewing model"], None
    note = f.get("note") or ""
    if not isinstance(note, str) or len(note) > NOTE_MAX:
        return [f"note must be a string of at most {NOTE_MAX} characters"], None
    res = run.results([ds]).get(ds) if isinstance(ds, str) else None
    why = vrc.skip_problem(run.dir, res, f["skipped"])
    if why:
        return [why], None
    now = vrc.nothing_to_show(run.dir, res)
    out = {"receipt": "skipped", "dataset_id": ds, "skipped": vrc.skip_text(now["new_sha256"]),
           "identical_to": now["identical_to"], "question_id": Q_ALL, "answer": "no", "effect": "none",
           "category": "none", "note": note, "model": f["model"], "run_id": run.run_id}
    if now.get("carried_from"):
        out["carried_from"] = now["carried_from"]
    core = {k: out[k] for k in ("run_id", "dataset_id", "skipped", "model")}
    out["finding_id"] = f.get("finding_id") or ("vf-" + ac.sha256_bytes(ac.cjson(core))[:16])
    if not FID_RE.match(out["finding_id"]):
        return ["finding_id must look like vf-<letters, digits, . _ ->"], None
    if out["finding_id"] in existing_ids:
        return [f"finding_id {out['finding_id']} already recorded (identical finding or reused id)"], None
    return [], out


def _locked_update(lock_dir: Path, fn) -> Any:
    fd = os.open(lock_dir, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fn()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _amend_dir(run: Run) -> Path:
    """runs/<id>/amend/ (seal.py --amend reads amend/visual-review.json). The runner leaves the run dir read-only,
    so the directory is created with a temporary owner write bit that is removed again at once."""
    import stat
    d = run.dir / "amend"
    if d.is_dir():
        return d
    mode = stat.S_IMODE(run.dir.stat().st_mode)
    if not mode & stat.S_IWUSR:
        os.chmod(run.dir, mode | stat.S_IWUSR)
    try:
        d.mkdir(exist_ok=True)
    finally:
        os.chmod(run.dir, mode)
    return d


def _seal_amend(run: Run) -> tuple[int, str]:
    import subprocess
    if run.migration is None:
        return 2, "run directory is not under <migration>/runs/: cannot call seal.py --amend"
    seal = HERE.parent / "report" / "seal.py"
    p = subprocess.run([sys.executable, str(seal), str(run.migration), "--run", run.dir.name, "--amend"],
                       capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def cmd_review_add(args) -> int:
    run = Run(args.run)
    sealed = run.sealed()
    if not run.verify.is_dir():
        print("refused: verify/ missing (run compare.py and plot.py first)", file=sys.stderr)
        return EXIT_REJECTED
    try:
        raw = json.loads(Path(args.json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"rejected: cannot read {args.json}: {e}", file=sys.stderr)
        return EXIT_REJECTED
    items = raw if isinstance(raw, list) else [raw]
    rubric, rsha = load_rubric()
    # Before seal: append to verify/visual-review.json. After seal (verify/ read-only): never write into verify/;
    # append to runs/<id>/amend/visual-review.json and seal it with `seal.py --amend` (07 §3.5).
    target_dir = _amend_dir(run) if sealed else run.verify

    def do():
        vr_path = target_dir / "visual-review.json"
        doc = ac.load_json(vr_path) if vr_path.is_file() else {"schema": REVIEW_SCHEMA, "rubric_id": rubric["id"],
                                                                  "rubric_sha256": rsha, "run_id": run.run_id,
                                                                  "findings": []}
        if sealed:
            doc.setdefault("amends", "verify/visual-review.json")
        if doc.get("rubric_sha256") != rsha:
            return [f"rubric changed since the first receipt of this run ({doc.get('rubric_sha256')} → {rsha}); "
                    f"a rubric change needs recalibration and a new run"], []
        ids = {x.get("finding_id") for x in doc.get("findings", [])}
        pre = run.verify / "visual-review.json"
        if sealed and pre.is_file():
            ids |= {x.get("finding_id") for x in (ac.load_json(pre).get("findings") or [])}
        new, errs = [], []
        for n, f in enumerate(items):
            e, out = validate_finding(run, f, rubric, ids)
            if e:
                errs.extend(f"finding {n + 1}: {x}" for x in e)
                continue
            out.update(rubric_id=rubric["id"], rubric_sha256=rsha, created_at=ac.utc_now_iso())
            ids.add(out["finding_id"])
            new.append(out)
        if errs:
            return errs, []
        doc["findings"] = list(doc.get("findings", [])) + new
        ac.dump_json(vr_path, doc)
        return [], new
    errs, new = _locked_update(target_dir, do)
    if errs:
        for e in errs:
            print(f"rejected: {e}", file=sys.stderr)
        return EXIT_REJECTED
    where = "amend/visual-review.json (run sealed)" if sealed else "verify/visual-review.json"
    for f in new:
        on = f"view {f['view']['project']} ({len(f['views_seen'])} views, engine {f.get('engine')})" \
            if f.get("receipt") == "view" else (f["skipped"] if f.get("receipt") == "skipped" else f["plot"])
        print(f"recorded {f['finding_id']} in {where}: {f['dataset_id']} {f['question_id']} {f['answer']} "
              f"({f['effect']}) on {on}")
    if sealed:
        code, msg = _seal_amend(run)
        print(msg, file=sys.stdout if code == 0 else sys.stderr)
        if code != 0:
            print("the finding is recorded in amend/ but NOT sealed: rerun "
                  "`axpy $PLUGIN/skills/report/seal.py <migration> --run <id> --amend`", file=sys.stderr)
            return EXIT_AMEND_FAILED
    return EXIT_OK


def review_status(run: Run) -> dict:
    rubric, rsha = load_rubric()
    vr_path = run.verify / "visual-review.json"
    vr = ac.load_json(vr_path) if vr_path.is_file() else {"findings": []}
    findings = list(vr.get("findings", []) or [])
    avr = run.dir / "amend" / "visual-review.json"
    if avr.is_file():
        findings += list(ac.load_json(avr).get("findings") or [])
    receipts = vrc.RunReceipts(run.dir)
    out = {"run_id": run.run_id, "rubric_id": rubric["id"], "rubric_sha256": rsha,
           "file_rubric_sha256": vr.get("rubric_sha256"), "datasets": {}, "optional": {}}
    for ds, res in sorted(run.results().items()):
        fam = res.get("family")
        compared = fam in ("raster", "vector") and bool(res.get("checks")) and \
            res.get("verdict") not in NOT_COMPARED_STATES
        doc = _plots_doc(run, ds)
        shas = set()
        stale = False
        if doc:
            stale = doc.get("compare_result_id") != res.get("result_id")
            for e in doc.get("png") or []:
                fp = run.verify / "plots" / ds / e.get("name", "")
                if fp.is_file() and ac.sha256_file(fp) == e.get("sha256"):
                    shas.add(e.get("sha256"))
        mine = [f for f in findings if f.get("dataset_id") == ds and f.get("run_id") == run.run_id]
        views_ok = [f for f in mine if vrc.is_view(f) and receipts.problem(f) is None]
        skips_ok = [f for f in mine if vrc.is_skip(f) and receipts.problem(f) is None]
        valid = [f for f in mine if not vrc.is_view(f) and not vrc.is_skip(f) and f.get("plot_sha256") in shas] + \
            views_ok + skips_ok
        skipped = (skips_ok[0]["skipped"] if skips_ok else None) or (vrc.skip_counts(run.dir, ds, res) if compared
                                                                     else None)
        required = sorted(q["id"] for q in rubric.get("questions", []) if fam in q.get("families", [])
                          and q.get("required", True))
        answered = sorted({f.get("question_id") for f in valid})
        qall = any(f.get("question_id") == Q_ALL for f in valid)
        missing = [] if qall else [q for q in required if q not in answered]
        receipted = any(f.get("answer") in ("yes", "no") for f in valid)
        by_views = any(f.get("answer") in ("yes", "no") for f in views_ok)   # views_seen covers every view
        if skipped:
            missing = []
            status = "complete"
        elif by_views:
            missing = []
            status = "complete"
        elif not doc:
            status = "no plots"
        elif stale:
            status = "stale plots"
        elif not valid:
            status = "no receipt"
        elif missing:
            status = "incomplete"
        elif not receipted:
            status = "cannot_tell only (counts as not visually assessed)"
        else:
            status = "complete"
        entry = {"family": fam, "compare_verdict": res.get("verdict"), "status": status, "missing_questions": missing,
                 "answered": answered, "q_all": qall, "invalid_receipts": len(mine) - len(valid),
                 "view_receipts": len(views_ok),
                 "via": "skipped" if skipped else ("views" if by_views else ("plots" if valid else None)),
                 "skipped": skipped, "skip_receipt": bool(skips_ok),
                 "flags": sorted(f["finding_id"] for f in valid if f.get("effect") == "flag"),
                 "cannot_tell": sorted(f["finding_id"] for f in valid if f.get("answer") == "cannot_tell")}
        (out["datasets"] if compared else out["optional"])[ds] = entry
    out["lacking"] = sorted(ds for ds, e in out["datasets"].items() if e["status"] != "complete")
    return out


def _first_plot(doc: dict) -> str | None:
    names = [e.get("name") for e in doc.get("png") or [] if e.get("name")]
    for pref in ("overview.png", "overlay.png"):
        if pref in names:
            return pref
    return names[0] if names else None


def review_template(run: Run, kind: str = "auto", datasets: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """(prefilled Q-all receipts, skipped-dataset notes) for every compared raster/vector dataset that still lacks a
    complete receipt (or the named ones). kind auto = view when views.json has views of the dataset, else png. Shas
    are copied from plots.json / views.json (whose dataset key is `dataset`; the receipt's is `dataset_id`) and
    re-checked against the bytes now, so a stale or changed file is skipped, never templated. answer, note and model
    (and a view receipt's cell values) are left empty on purpose: review-add refuses them until filled."""
    st = review_status(run)
    want = list(datasets) if datasets else sorted(set(st["lacking"]) | {
        ds for ds, e in st["datasets"].items() if e.get("skipped") and not e.get("skip_receipt")})
    out, skipped = [], []
    view_docs = {root: vrc.load_index(run.dir, root) for root in vrc.views_index_rel(run.dir)}

    def views_of(ds):
        found = None
        for d in view_docs.values():
            if d and d.get("run_id") == run.run_id and vrc.index_entry(d, ds):
                found = vrc.index_entry(d, ds)       # amend/views (later root) wins over verify/views
        return found
    for ds in want:
        e = st["datasets"].get(ds) or st["optional"].get(ds)
        if e is None:
            skipped.append(f"{ds}: not a compared raster/vector dataset of this run")
            continue
        if e.get("skipped"):              # nothing to show: the bytes say why (review-add re-hashes them)
            out.append({"dataset_id": ds, "skipped": e["skipped"], "note": "", "model": ""})
            continue
        base = {"dataset_id": ds, "question_id": Q_ALL, "answer": "", "effect": "none", "category": "none"}
        entry = views_of(ds)
        k = kind if kind != "auto" else ("view" if entry and vrc.entry_projects(entry)[0] else "png")
        if k == "png":
            doc = _plots_doc(run, ds)
            if not doc:
                skipped.append(f"{ds}: no plots (plot.py run --run <dir> --dataset {ds})")
                continue
            if doc.get("compare_result_id") != (run.results([ds]).get(ds) or {}).get("result_id"):
                skipped.append(f"{ds}: plots are stale (rerun plot.py run)")
                continue
            seen = []
            for pe in doc.get("png") or []:
                rel = f"plots/{ds}/{pe.get('name')}"
                fp = run.verify / rel
                if not fp.is_file() or ac.sha256_file(fp) != pe.get("sha256"):
                    seen = None
                    break
                seen.append({"plot": rel, "plot_sha256": pe["sha256"]})
            if not seen:
                skipped.append(f"{ds}: a plot is missing or differs from plots.json (rerun plot.py run)")
                continue
            out.append({**base, "panel": "all", "plot": f"plots/{ds}/{_first_plot(doc)}",
                        "plot_sha256": next(s["plot_sha256"] for s in seen
                                            if s["plot"] == f"plots/{ds}/{_first_plot(doc)}"),
                        "plots_seen": sorted(seen, key=lambda s: s["plot"]), "note": "", "model": ""})
            continue
        # view
        if entry is None:
            skipped.append(f"{ds}: no map views (views.py run --run <dir> --dataset {ds})")
            continue
        if not vrc.entry_projects(entry)[0]:
            why = entry.get("skipped") or entry.get("reason") or entry.get("note") or "no view projects"
            skipped.append(f"{ds}: views.json lists no view projects ({why}); use --kind png")
            continue
        fam = entry.get("family")
        vc = entry.get("validity_counts") or {}
        if fam == "raster" and (int(vc.get("1") or 0) or int(vc.get("2") or 0)):
            skipped.append(f"{ds}: views.json counts {vc.get('1') or 0} legacy-only and {vc.get('2') or 0} "
                           "new-only valid cells: 'nothing found' is refused, flag them instead")
            continue
        projects, cells = vrc.entry_projects(entry)
        by_name = {info["name"]: rel for rel, info in projects.items()}
        names = list(vrc.REQUIRED_VIEWS.get(fam, ())) + ([f"cell-{c}" for c in sorted(cells)] if fam == "raster"
                                                          else [])
        refs, bad = [], []
        for n in names:
            rel = by_name.get(n)
            fp = run.dir / rel if rel else None
            if rel is None or not fp.is_file() or ac.sha256_file(fp) != projects[rel]["sha256"]:
                bad.append(n)
                continue
            refs.append({"project": rel, "project_sha256": projects[rel]["sha256"]})
        if bad:
            skipped.append(f"{ds}: view project(s) {', '.join(bad)} missing or changed since views.json "
                           "(rerun views.py run)")
            continue
        sk = {**base, "view": refs[0], "views_seen": sorted(refs, key=lambda r: r["project"]), "layer": None}
        if fam == "raster":
            sk["engine"] = vrc.ENGINE_GPU
            sk["cells_read"] = [{"cell": c, "legacy": "", "new": ""} for c in sorted(cells)]
        out.append({**sk, "note": "", "model": ""})
    return out, skipped


def cmd_review_template(args) -> int:
    run = Run(args.run)
    items, skipped = review_template(run, args.kind, args.dataset)
    print(json.dumps(items, indent=1, ensure_ascii=False))
    for s in skipped:
        print(f"skipped {s}", file=sys.stderr)
    print(f"{len(items)} prefilled receipt(s). A skipped receipt ('skipped: identical (<sha256>)': no views, "
          "nothing to show) needs only model. Q-all receipts: fill answer (\"no\" only after looking at every cited plot or "
          "view), model, note (may stay empty for no); view receipts: each cells_read legacy/new as the popup shows "
          f"them (null = nodata). layer: null or one of {', '.join(VIEW_LAYERS)} (several joined with '+', e.g. "
          "legacy+new). A defect is a separate receipt with question_id Q1…Q7, answer yes, effect flag. Then "
          "plot.py review-add --run <run> --json <file>.", file=sys.stderr)
    return EXIT_OK if items or not skipped else EXIT_SOME


def cmd_review_status(args) -> int:
    run = Run(args.run)
    st = review_status(run)
    if args.json:
        print(json.dumps(st, sort_keys=True, indent=1))
    else:
        print(f"visual review · run {st['run_id']} · rubric {st['rubric_id']}")
        if not st["datasets"]:
            print("no compared raster/vector datasets")
        for ds, e in sorted(st["datasets"].items()):
            miss = f" missing {', '.join(e['missing_questions'])}" if e["missing_questions"] else ""
            fl = f" · {len(e['flags'])} flag(s)" if e["flags"] else ""
            how = f" ({e['skipped']}{'' if e['skip_receipt'] else '; review-template prints its receipt'})" \
                if e.get("skipped") else ""
            print(f"  {ds} [{e['family']}, {e['compare_verdict']}]: {e['status']}{how}{miss}{fl}")
        for ds, e in sorted(st["optional"].items()):
            print(f"  (optional) {ds} [{e['family']}, {e['compare_verdict']}]: {e['status']}")
        print(f"lacking a complete receipt: {len(st['lacking'])}" + (f" ({', '.join(st['lacking'])})"
                                                                     if st["lacking"] else ""))
    return EXIT_OK if not st["lacking"] else EXIT_SOME


# =================================================================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="plot.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="render verify/plots/<ds>/*.png + plots.json from the compare results")
    p.add_argument("--run", required=True)
    p.add_argument("--dataset", action="append", default=[])
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("review-add", help="append one finding (or a list) to verify/visual-review.json")
    p.add_argument("--run", required=True)
    p.add_argument("--json", required=True, help="path of a JSON file holding the finding")
    p = sub.add_parser("review-status", help="which compared raster/vector datasets still lack a receipt")
    p.add_argument("--run", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("review-template", help="prefilled Q-all receipts for review-add (views or plots)")
    p.add_argument("--run", required=True)
    p.add_argument("--dataset", action="append", default=[], help="only these datasets (default: every lacking one)")
    p.add_argument("--kind", choices=["auto", "view", "png"], default="auto")
    a = ap.parse_args(argv)
    try:
        return {"run": cmd_run, "review-add": cmd_review_add, "review-status": cmd_review_status,
                "review-template": cmd_review_template}[a.cmd](a)
    except SetupError as e:
        print(f"setup error: {e}", file=sys.stderr)
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())
