#!/usr/bin/env python3
"""discover/map.py: the input map the agent looks at before any code exists (01 §4.7).

    map.py inputs <migration> [--json]
    map.py metrics <migration> [--json]
    map.py findings <migration> [--json]

`metrics` writes <migration>/discover/metrics.json and prints it as a short table: per raster (dtype, nodata value)
the cells inside the AOI (cell centre rule), how many are valid and how many NoData (with km², on the ellipsoid for
geographic grids), and the whole-raster valid count up to 50M cells; between consecutive rasters of one grid family
(role order input → intermediate → final, then lineage order) the valid cells lost and gained; per vector the
features that do not touch the AOI. Map findings cite it as metric.computed_by. Exit 0, 2 no inventory.

`findings` lists every recorded map finding and whether it still counts (lib/visual_receipt.map_finding_problem: a
PNG finding whose PNG still has its bytes, or a view receipt on the inputs views that still passes; both kinds count
the same way) and the inputs no counting finding is about. Exit 0 every finding counts, 1 some do not.

Writes <migration>/discover/inputs_overview.png, grid_corners.png and legacy_rasters.png (deterministic matplotlib
Agg; no basemap, nothing fetched) and registers them in inventory.json through `scan.py annotate png`. Every number
printed in a caption comes from the inventory (extent_relations, profiles); the agent records what it sees with
`scan.py annotate <migration> map-finding --json f.json` (a finding without a computed metric can only raise a
question). Reads only the legacy folder named in inventory.json and the migration folder.
Exit codes: 0 ok, 2 usage error (no inventory).
"""
from __future__ import annotations

import os

os.environ["GDAL_PAM_ENABLED"] = "NO"

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import axis_common as ac  # noqa: E402

import matplotlib  # noqa: E402

matplotlib.use("Agg")
matplotlib.rcParams.update({"svg.hashsalt": "axis-migrate", "font.size": 8, "path.simplify": False,
                            "figure.dpi": 100, "savefig.dpi": 100})
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

PALETTE = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666", "#1f78b4",
           "#b2df8a"]
META = {"Software": None}


def save(fig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, metadata=META)
    plt.close(fig)


def stretch(a: np.ndarray) -> tuple[float, float]:
    v = a.compressed() if np.ma.isMaskedArray(a) else a[np.isfinite(a)]
    if v.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(v, [2, 98])
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def read_thumb(path: Path, max_px: int = 900):
    import rasterio
    with rasterio.open(path) as src:
        f = max(1, math.ceil(max(src.width, src.height) / max_px))
        shape = (max(1, src.height // f), max(1, src.width // f))
        a = src.read(1, out_shape=shape, resampling=rasterio.enums.Resampling.nearest, masked=True).astype("float64")
        b = src.bounds
        return a, (b.left, b.right, b.bottom, b.top), src.crs, f


def read_vec(legacy: Path, d: dict, to_crs=None):
    import pyogrio
    p = legacy / d["legacy_path"]
    kw = {"IMMUTABLE": "YES"} if p.suffix.lower() in (".gpkg", ".sqlite") else {}
    g = pyogrio.read_dataframe(p, layer=d["layer"], **kw)
    if to_crs is not None and g.crs is not None and not g.crs.equals(to_crs):
        g = g.to_crs(to_crs)
    return g


def km_scalebar(ax, extent, geographic: bool) -> None:
    x0, x1, y0, y1 = extent
    lat = (y0 + y1) / 2
    m_per_unit = 111_320 * math.cos(math.radians(lat)) if geographic else 1.0
    width_m = (x1 - x0) * m_per_unit
    nice = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
    km = next((n for n in nice if n * 1000 > width_m / 6), nice[-1])
    L = km * 1000 / m_per_unit
    xs = x0 + 0.05 * (x1 - x0)
    ys = y0 + 0.05 * (y1 - y0)
    ax.plot([xs, xs + L], [ys, ys], color="black", lw=3, solid_capstyle="butt")
    ax.text(xs + L / 2, ys + 0.015 * (y1 - y0), f"{km} km", ha="center", va="bottom", fontsize=8)


def map_datasets(inv: dict) -> tuple[list[dict], str | None]:
    """Datasets the input maps draw: the inputs that exist; when lineage found none, every raster/vector on disk that
    no code writes (role unknown/unused), with a note for the caption (B4). VRT aliases of a drawn file are skipped."""
    ds = [d for d in inv["datasets"] if d["role"] == "input" and d["exists"] and d["kind"] in ("raster", "vector")]
    if ds:
        return ds, None
    ds = [d for d in inv["datasets"] if d["exists"] and d["kind"] in ("raster", "vector")
          and d["role"] in ("unknown", "unused") and not d.get("alias_of")]
    if not ds:
        return [], None
    return ds, (f"no dataset has role input: drawing all {len(ds)} on-disk rasters/vectors no code writes "
                f"(roles unknown/unused); their roles are not established")


def aoi_dataset(inv: dict, ds: list[dict], migration: Path) -> tuple[dict | None, str | None]:
    """The inventory's AOI; when it is null, the single polygon layer among the drawn datasets (said in the caption)."""
    by_id = {d["id"]: d for d in inv["datasets"]}
    if inv.get("aoi") and inv["aoi"] in by_id:
        return by_id[inv["aoi"]], None
    polys = []
    for d in ds:
        if d["kind"] != "vector":
            continue
        gt = (inv_profile(migration, d) or {}).get("geom_types") or {}
        if gt and all("Polygon" in k for k in gt):
            polys.append(d)
    if len(polys) == 1:
        return polys[0], f"AOI: {polys[0]['id']} (the only polygon layer drawn; the inventory names no AOI)"
    return None, None


def inputs_overview(inv: dict, legacy: Path, out: Path) -> dict:
    from pyproj import CRS
    ds, fallback = map_datasets(inv)
    aoi, aoi_note = aoi_dataset(inv, ds, out.parent)
    profiles = {}
    for d in ds:
        pr = inv_profile(out.parent.parent, d)
        if pr:
            profiles[d["id"]] = pr
    crss = []
    for d in ds:
        p = profiles.get(d["id"], {})
        c = p.get("crs") or {}
        if c.get("wkt"):
            crss.append(CRS.from_wkt(c["wkt"]))
    geographic = all(c.is_geographic for c in crss) if crss else True
    disp = CRS.from_epsg(4326) if geographic else next(
        (CRS.from_user_input(p["crs"]["authid"]) for p in inv.get("projects", []) if (p.get("crs") or {}).get("authid")),
        crss[0] if crss else CRS.from_epsg(4326))
    fig, ax = plt.subplots(figsize=(10, 9))
    handles, notes = [], [x for x in (fallback, aoi_note) if x]
    rasters = sorted([d for d in ds if d["kind"] == "raster"], key=lambda d: d["id"])
    vectors = sorted([d for d in ds if d["kind"] == "vector"], key=lambda d: d["id"])
    all_bounds = []
    for i, d in enumerate(rasters):
        a, ext, rcrs, f = read_thumb(legacy / d["legacy_path"])
        if rcrs is not None and not CRS.from_wkt(rcrs.to_wkt()).equals(disp):
            notes.append(f"{d['id']}: native CRS differs from the display CRS, drawn as bbox only")
        else:
            lo, hi = stretch(a)
            ax.imshow(a, extent=ext, cmap="Greys", vmin=lo, vmax=hi, interpolation="nearest", zorder=1, alpha=0.9)
            notes.append(f"{d['id']}: 2-98 % stretch [{lo:.3g}, {hi:.3g}], thumbnail 1:{f} nearest")
        ax.plot([ext[0], ext[1], ext[1], ext[0], ext[0]], [ext[2], ext[2], ext[3], ext[3], ext[2]], color="black",
                lw=1, ls="--", zorder=5)
        ax.text(ext[0], ext[3], " " + d["id"] + " (bbox)", va="bottom", fontsize=7)
        all_bounds.append(ext)
    for i, d in enumerate(vectors):
        col = PALETTE[i % len(PALETTE)]
        style = ["-", "--", ":", "-."][i % 4]
        try:
            g = read_vec(legacy, d, disp)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{d['id']}: not drawn ({exc})")
            continue
        lw = 2.4 - 0.5 * (i % 3) if len(g) <= 200 else 0.4
        if len(g) and all(t.endswith("Point") for t in g.geom_type.dropna().unique()):
            g.plot(ax=ax, color=col, markersize=4 if len(g) <= 200 else 1, zorder=3 + i)  # points have no boundary
        else:
            g.boundary.plot(ax=ax, color=col, lw=lw, ls=style, zorder=3 + i)
        b = g.total_bounds
        all_bounds.append((b[0], b[2], b[1], b[3]))
        handles.append(plt.Line2D([0], [0], color=col, lw=2, ls=style, label=f"{d['id']} ({len(g)} features)"))
    if all_bounds:
        x0 = min(b[0] for b in all_bounds)
        x1 = max(b[1] for b in all_bounds)
        y0 = min(b[2] for b in all_bounds)
        y1 = max(b[3] for b in all_bounds)
        pad = 0.03 * max(x1 - x0, y1 - y0)
        ax.set_xlim(x0 - pad, x1 + pad)
        ax.set_ylim(y0 - pad, y1 + pad)
        if geographic:
            ax.set_aspect(1 / math.cos(math.radians((y0 + y1) / 2)))
        km_scalebar(ax, (x0 - pad, x1 + pad, y0 - pad, y1 + pad), geographic)
    rel_lines = []
    for r in inv.get("extent_relations", []):
        if r["relation"] == "a_outside_b_extent":
            rel_lines.append(f"{r['a']} area outside {r['b']} extent: {r['area_outside_km2']:.2f} km2")
        elif r["relation"] == "features_vs_aoi":
            by = "; ".join(f"{c}: " + ", ".join(f"{k} {v}" for k, v in vals.items())
                           for c, vals in r.get("features_outside_by_value", {}).items())
            rel_lines.append(f"{r['a']} features not touching {r['b']}: {r['features_outside']}/{r['count']}"
                             + (f" ({by})" if by else ""))
        elif r["relation"] == "nodata_inside_aoi":
            rel_lines.append(f"{r['a']} NoData cells inside {r['b']}: {r['nodata_cells_inside_aoi']} "
                             f"({r['nodata_inside_aoi_km2']:.2f} km2, cell centre)")
    ax.legend(handles=handles, loc="upper right", fontsize=7, framealpha=0.9)
    ax.set_title(f"Inputs overview ({disp.to_string()}; display only, nothing reprojected for checks)"
                 + (" — no inputs: all on-disk data" if fallback else ""), fontsize=9)
    ax.set_xlabel("x" if not geographic else "longitude")
    ax.set_ylabel("y" if not geographic else "latitude")
    zoom = aoi_zoom(fig, ax, legacy, aoi, rasters, vectors, disp, all_bounds, geographic)
    if zoom:
        notes.append(zoom)
    caption = "\n".join(rel_lines + notes)
    fig.text(0.01, 0.01, caption, fontsize=7, va="bottom", family="monospace")
    fig.subplots_adjust(bottom=0.06 + 0.017 * len(caption.splitlines()))
    path = out / "inputs_overview.png"
    save(fig, path)
    return {"path": "discover/inputs_overview.png", "kind": "inputs_overview", "datasets": [d["id"] for d in ds],
            "metrics": rel_lines + [x for x in (fallback, aoi_note) if x], "fallback": bool(fallback),
            "aoi": aoi["id"] if aoi else None}


def aoi_zoom(fig, ax, legacy: Path, aoi: dict | None, rasters: list, vectors: list, disp, all_bounds: list,
             geographic: bool) -> str | None:
    """When the AOI covers under 1 % of the drawn extent it would be a speck: an inset zoomed to the AOI (+25 %),
    rasters read for that window only (nearest), vectors on top."""
    import rasterio
    from pyproj import CRS
    from rasterio.windows import from_bounds
    if aoi is None or not all_bounds:
        return None
    try:
        g = read_vec(legacy, aoi, disp)
    except Exception:  # noqa: BLE001
        return None
    b = g.total_bounds
    x0, x1 = min(x[0] for x in all_bounds), max(x[1] for x in all_bounds)
    y0, y1 = min(x[2] for x in all_bounds), max(x[3] for x in all_bounds)
    full = max((x1 - x0) * (y1 - y0), 1e-12)
    if (b[2] - b[0]) * (b[3] - b[1]) / full >= 0.01:
        return None
    pad = 0.25 * max(b[2] - b[0], b[3] - b[1])
    zx0, zx1, zy0, zy1 = b[0] - pad, b[2] + pad, b[1] - pad, b[3] + pad
    ins = ax.inset_axes([0.58, 0.04, 0.40, 0.40])
    ins.set_facecolor("#f4f4f4")
    for d in rasters:
        with rasterio.open(legacy / d["legacy_path"]) as src:
            if src.crs is not None and not CRS.from_wkt(src.crs.to_wkt()).equals(disp):
                continue
            w = from_bounds(zx0, zy0, zx1, zy1, src.transform).round_offsets().round_lengths()
            if w.width <= 0 or w.height <= 0:
                continue
            f = max(1, math.ceil(max(w.width, w.height) / 600))
            a = src.read(1, window=w, out_shape=(max(1, int(w.height) // f), max(1, int(w.width) // f)),
                         boundless=True, masked=True,
                         resampling=rasterio.enums.Resampling.nearest).astype("float64")
            wb = rasterio.windows.bounds(w, src.transform)
            lo, hi = stretch(a) if a.count() else (0, 1)
            ins.imshow(a, extent=(wb[0], wb[2], wb[1], wb[3]), cmap="Greys", vmin=lo, vmax=hi,
                       interpolation="nearest", alpha=0.6)
    for i, d in enumerate(vectors):
        try:
            gv = read_vec(legacy, d, disp)
        except Exception:  # noqa: BLE001
            continue
        col = PALETTE[i % len(PALETTE)]
        if all(t.endswith("Point") for t in gv.geom_type.unique()):
            gv.plot(ax=ins, color=col, markersize=6, zorder=4 + i)
        else:
            gv.boundary.plot(ax=ins, color=col, lw=1.2, zorder=4 + i)
    ins.set_xlim(zx0, zx1)
    ins.set_ylim(zy0, zy1)
    if geographic:
        ins.set_aspect(1 / math.cos(math.radians((zy0 + zy1) / 2)))
    ins.tick_params(labelsize=5)
    ins.set_title(f"zoom to AOI {aoi['id']}", fontsize=7)
    ax.indicate_inset_zoom(ins, edgecolor="red")
    return f"inset: zoom to the AOI {aoi['id']} (it covers under 1 % of the drawn extent)"


def inv_profile(migration: Path, d: dict) -> dict | None:
    if not d.get("profile_ref"):
        return None
    p = migration / d["profile_ref"]
    return ac.load_json(p) if p.exists() else None


def grid_corners(inv: dict, legacy: Path, out: Path, cells: int = 14) -> dict:
    import rasterio
    from rasterio.windows import Window
    migration = out.parent
    by_id = {d["id"]: d for d in inv["datasets"]}
    fams = inv.get("grids", [])
    aoi, _ = aoi_dataset(inv, map_datasets(inv)[0], migration)
    aoi_g = None
    if aoi:
        try:
            aoi_g = read_vec(legacy, aoi)
        except Exception:  # noqa: BLE001
            aoi_g = None
    rows = max(1, len(fams))
    fig, axes = plt.subplots(rows, 5, figsize=(17, 3.6 * rows + 0.6), squeeze=False)
    info = []
    for fi, fam in enumerate(fams):
        ref = by_id[fam["reference"]]
        members = [by_id[m["dataset"]] for m in fam["members"]] + [by_id[m["dataset"]] for m in fam["misaligned"]]
        with rasterio.open(legacy / ref["legacy_path"]) as src:
            T = src.transform
            H, W = src.height, src.width
            inv_t = ~T
            if aoi_g is not None and aoi_g.crs is not None and src.crs is not None:
                import shapely
                g = aoi_g.to_crs(src.crs.to_wkt()) if not aoi_g.crs.equals(src.crs.to_wkt()) else aoi_g
                xy = shapely.get_coordinates(g.geometry.values)
                ext = {"west": xy[np.argmin(xy[:, 0])], "north": xy[np.argmax(xy[:, 1])],
                       "east": xy[np.argmax(xy[:, 0])], "south": xy[np.argmin(xy[:, 1])]}
                cen = shapely.union_all(g.geometry.values).representative_point()
                pts = []
                for name in ("west", "north", "east", "south"):
                    cc, rr = inv_t * (float(ext[name][0]), float(ext[name][1]))
                    pts.append((f"{name} extreme of AOI", int(cc), int(rr)))
                cc, rr = inv_t * (cen.x, cen.y)
                pts.append(("inside AOI", int(cc), int(rr)))
                where = "four extreme points of the AOI (" + aoi["id"] + ") and one point inside"
            else:
                g = None
                pts = [("top-left", 0, 0), ("top-right", W - 1, 0), ("bottom-left", 0, H - 1),
                       ("bottom-right", W - 1, H - 1), ("centre", W // 2, H // 2)]
                where = "corners and centre of the reference raster"
            for pi, (name, cc, rr) in enumerate(pts):
                ax = axes[fi][pi]
                col0, row0 = int(cc - cells // 2), int(rr - cells // 2)
                win = Window(col0, row0, cells, cells)
                a = src.read(1, window=win, masked=True, boundless=True)  # outside the raster stays visible (grey)
                wt = src.window_transform(win)
                x0, y0 = wt.c, wt.f
                x1, y1 = x0 + wt.a * win.width, y0 + wt.e * win.height
                a = a.astype("float64")
                lo, hi = stretch(a) if a.count() else (0, 1)
                ax.set_facecolor("#c8c8c8")
                ax.imshow(a, extent=(x0, x1, y1, y0) if wt.e > 0 else (x0, x1, y1, y0), cmap="viridis", vmin=lo,
                          vmax=hi, interpolation="nearest", origin="upper")
                # pixel lines of every family member (aligned members coincide; a shift shows as a second grid)
                for mi, m in enumerate(members):
                    with rasterio.open(legacy / m["legacy_path"]) as ms:
                        mt = ms.transform
                    colr = "black" if m["id"] == ref["id"] else PALETTE[mi % len(PALETTE)]
                    ls = "-" if m["id"] == ref["id"] else (0, (2, 2 + mi))
                    k0 = math.floor((min(x0, x1) - mt.c) / mt.a) - 1
                    k1 = math.ceil((max(x0, x1) - mt.c) / mt.a) + 1
                    for k in range(k0, k1 + 1):
                        x = mt.c + k * mt.a
                        if min(x0, x1) <= x <= max(x0, x1):
                            ax.axvline(x, color=colr, lw=0.6 if m["id"] == ref["id"] else 1.0, ls=ls, alpha=0.9)
                    j0 = math.floor((max(y0, y1) - mt.f) / mt.e) - 1
                    j1 = math.ceil((min(y0, y1) - mt.f) / mt.e) + 1
                    for j in range(min(j0, j1), max(j0, j1) + 1):
                        y = mt.f + j * mt.e
                        if min(y0, y1) <= y <= max(y0, y1):
                            ax.axhline(y, color=colr, lw=0.6 if m["id"] == ref["id"] else 1.0, ls=ls, alpha=0.9)
                if g is not None:
                    g.boundary.plot(ax=ax, color="red", lw=1.4)
                ax.set_xlim(min(x0, x1), max(x0, x1))
                ax.set_ylim(min(y0, y1), max(y0, y1))
                ax.set_title(f"{fam['id']} {name}\ncols {col0}-{col0 + win.width - 1}, rows {row0}-{row0 + win.height - 1}",
                             fontsize=7)
                ax.tick_params(labelsize=5)
                ax.ticklabel_format(useOffset=False)
                if a.count() == 0:
                    ax.text(0.5, 0.5, "all NoData", transform=ax.transAxes, ha="center", color="black")
        offs = ", ".join(f"{m['dataset']} ({m['offset_px'][0]:g}, {m['offset_px'][1]:g})" for m in fam["members"])
        mis = ", ".join(f"{m['dataset']} ({m['offset_px'][0]:g}, {m['offset_px'][1]:g})" for m in fam["misaligned"])
        info.append(f"{fam['id']}: reference {fam['reference']}, res {fam['res'][0]:.10g} x {fam['res'][1]:.10g}, "
                    f"{len(fam['members'])} aligned members, pixel offsets: {offs}"
                    + (f"; MISALIGNED: {mis}" if mis else ""))
        info.append(f"  windows at the {where}; black = reference cell edges, dashed colours = other members' "
                    "cell edges (coincide when aligned), red = AOI boundary, grey = NoData / outside the raster")
    if not fams:
        axes[0][0].text(0.5, 0.5, "no rasters", ha="center")
    fig.suptitle("Grid families at native resolution (nearest, no resampling)", fontsize=10)
    fig.text(0.01, 0.005, "\n".join(info), fontsize=7, family="monospace", va="bottom")
    fig.subplots_adjust(bottom=0.08 + 0.04 * len(info) / max(rows, 1), top=0.88, wspace=0.35, hspace=0.45)
    path = out / "grid_corners.png"
    save(fig, path)
    return {"path": "discover/grid_corners.png", "kind": "grid_corners", "grids": [f["id"] for f in fams],
            "metrics": info}


def legacy_rasters(inv: dict, legacy: Path, out: Path) -> dict:
    ds = sorted([d for d in inv["datasets"] if d["kind"] == "raster" and d["exists"]
                 and d["role"] in ("intermediate", "final")], key=lambda d: (d["role"] != "intermediate", d["id"]))
    if not ds:
        return legacy_vectors(inv, legacy, out)
    n = max(1, len(ds))
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 4.6 * rows + 0.4), squeeze=False)
    for i in range(rows * cols):
        ax = axes[i // cols][i % cols]
        if i >= len(ds):
            ax.axis("off")
            continue
        d = ds[i]
        a, ext, crs, f = read_thumb(legacy / d["legacy_path"], 700)
        lo, hi = stretch(a)
        im = ax.imshow(a, extent=ext, cmap="magma", vmin=lo, vmax=hi, interpolation="nearest")
        prof = inv_profile(out.parent, d) or {}
        comp = prof.get("computed") or {}
        ax.set_title(f"{d['id']} [{d['role']}]\n{prof.get('dtype')} nodata={prof.get('nodata')} "
                     f"valid={comp.get('valid')} sum={comp.get('sum') if comp.get('sum') is None else round(comp['sum'], 2)}",
                     fontsize=7)
        if crs is not None and crs.is_geographic:
            ax.set_aspect(1 / math.cos(math.radians((ext[2] + ext[3]) / 2)))
        ax.tick_params(labelsize=5)
        fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02).ax.tick_params(labelsize=5)
        ax.text(0.01, 0.01, f"2-98 % stretch, 1:{f} nearest", transform=ax.transAxes, fontsize=5, color="white")
    fig.suptitle("Legacy intermediate and final rasters (the references every step is compared against)", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    path = out / "legacy_rasters.png"
    save(fig, path)
    return {"path": "discover/legacy_rasters.png", "kind": "legacy_rasters", "datasets": [d["id"] for d in ds]}


def legacy_vectors(inv: dict, legacy: Path, out: Path) -> dict:
    """No intermediate or final raster exists: legacy_rasters.png draws the intermediate/final vectors instead (the
    references), each with its feature count, and says so; with none at all it says that in words."""
    ds = sorted([d for d in inv["datasets"] if d["kind"] == "vector" and d["exists"]
                 and d["role"] in ("intermediate", "final")], key=lambda d: (d["role"] != "intermediate", d["id"]))
    n = max(1, len(ds))
    cols = min(3, n)
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(5.2 * cols, 4.6 * rows + 0.4), squeeze=False)
    for i in range(rows * cols):
        ax = axes[i // cols][i % cols]
        if i >= len(ds):
            ax.axis("off")
            if not ds:
                ax.text(0.5, 0.5, "No intermediate or final raster or vector exists in the legacy folder",
                        ha="center", va="center", fontsize=9, wrap=True)
            continue
        d = ds[i]
        try:
            g = read_vec(legacy, d)
        except Exception as exc:  # noqa: BLE001
            ax.text(0.5, 0.5, f"{d['id']}: not drawn ({exc})"[:120], ha="center", fontsize=7)
            continue
        kinds = set(g.geom_type.dropna().unique())
        if kinds and all(k.endswith("Point") for k in kinds):
            g.plot(ax=ax, color=PALETTE[i % len(PALETTE)], markersize=8)
        elif len(g):
            g.plot(ax=ax, color=PALETTE[i % len(PALETTE)], alpha=0.5, edgecolor="black", lw=0.6)
        ax.set_title(f"{d['id']} [{d['role']}]\n{len(g)} feature(s), {', '.join(sorted(kinds)) or 'no geometry'}; "
                     f"columns {', '.join(c for c in g.columns if c != 'geometry')[:60]}", fontsize=7)
        if g.crs is not None and g.crs.is_geographic and len(g):
            b = g.total_bounds
            ax.set_aspect(1 / math.cos(math.radians((b[1] + b[3]) / 2)))
        ax.tick_params(labelsize=5)
    fig.suptitle("Legacy intermediate and final datasets: no raster among them, the vectors are drawn", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    path = out / "legacy_rasters.png"
    save(fig, path)
    return {"path": "discover/legacy_rasters.png", "kind": "legacy_rasters", "datasets": [d["id"] for d in ds],
            "fallback": "vectors"}


def run_inputs(migration: Path) -> tuple[int, dict]:
    inv_p = migration / "inventory.json"
    if not inv_p.exists():
        return 2, {"error": "inventory.json not found: run scan.py first"}
    inv = ac.load_json(inv_p)
    legacy = (migration / inv["legacy_root"]).resolve()
    out = migration / "discover"
    res = [inputs_overview(inv, legacy, out), grid_corners(inv, legacy, out), legacy_rasters(inv, legacy, out)]
    import scan  # same directory: the inventory has one writer
    for r in res:
        code, msg = scan.annotate(migration, "png", None, r["path"], r["kind"])
        if code:
            return code, {"error": msg}
    return 0, {"pngs": res}


METRICS_SCHEMA = "axis-migrate.discover-metrics.v1"
WHOLE_MAX = 50_000_000            # whole-raster counts only up to this many cells (inside the AOI: always)
ROLE_RANK = {"input": 0, "unknown": 1, "unused": 1, "intermediate": 2, "final": 3}


def _cell_km2(t, rows: np.ndarray, geographic: bool) -> np.ndarray:
    """Area of one cell per row (km²): on the ellipsoid for a geographic grid, |a·e| otherwise."""
    if not geographic:
        return np.full(len(rows), abs(t.a * t.e) / 1e6)
    from pyproj import Geod
    geod = Geod(ellps="WGS84")
    lat0 = t.f + rows * t.e
    return np.array([abs(geod.polygon_area_perimeter([t.c, t.c + t.a, t.c + t.a, t.c],
                                                     [la, la, la + t.e, la + t.e])[0]) for la in lat0]) / 1e6


def _valid(a: np.ndarray, nd) -> np.ndarray:
    bad = np.isnan(a) if np.issubdtype(a.dtype, np.floating) else np.zeros(a.shape, bool)
    if nd is not None:
        bad |= a == np.array(nd).astype(a.dtype)
    return ~bad


def _aoi_window(src, aoi_g):
    """(window, inside mask) of the AOI on this raster's grid, cell-centre rule; None when they do not overlap."""
    import shapely
    from pyproj import CRS, Transformer
    from rasterio import features, windows
    rc = CRS.from_wkt(src.crs.to_wkt()) if src.crs is not None else None
    g = shapely.union_all(shapely.make_valid(aoi_g.geometry.values))
    if rc is not None and aoi_g.crs is not None and not rc.equals(CRS(aoi_g.crs)):
        tr = Transformer.from_crs(aoi_g.crs, rc, always_xy=True)
        g = shapely.transform(g, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
    f = windows.from_bounds(*g.bounds, src.transform)
    c0, r0 = max(math.floor(f.col_off), 0), max(math.floor(f.row_off), 0)
    c1, r1 = min(math.ceil(f.col_off + f.width), src.width), min(math.ceil(f.row_off + f.height), src.height)
    if c1 <= c0 or r1 <= r0:
        return None
    w = windows.Window(c0, r0, c1 - c0, r1 - r0)
    inside = features.geometry_mask([g], out_shape=(int(w.height), int(w.width)),
                                    transform=src.window_transform(w), invert=True)
    return w, inside


def metrics(migration: Path) -> tuple[int, dict]:
    """The numbers the agents kept computing with ad hoc scripts (map review C6): per raster NoData and valid cells
    inside the AOI (cell centre) with km², nodata value and dtype; between consecutive rasters of one grid family the
    valid cells lost/gained (inside the AOI, else the whole raster when small enough); per vector the features not
    touching the AOI. Display-independent; reads the legacy folder only."""
    import rasterio
    import shapely
    from pyproj import CRS, Transformer
    inv_p = migration / "inventory.json"
    if not inv_p.exists():
        return 2, {"error": "inventory.json not found: run scan.py first"}
    inv = ac.load_json(inv_p)
    legacy = (migration / inv["legacy_root"]).resolve()
    shown, fallback = map_datasets(inv)
    aoi, aoi_note = aoi_dataset(inv, shown, migration)
    aoi_g = None
    if aoi is not None:
        try:
            aoi_g = read_vec(legacy, aoi)
        except Exception as exc:  # noqa: BLE001
            aoi_note = f"AOI {aoi['id']} not readable: {exc}"
    on_disk = [d for d in inv["datasets"] if d["exists"] and d["kind"] in ("raster", "vector") and not d.get("alias_of")]
    op_pos = {o["id"]: i for i, o in enumerate(inv.get("ops") or [])}

    def order(d):
        return (ROLE_RANK.get(d["role"], 1), min((op_pos.get(o, 10 ** 9) for o in d.get("produced_by") or []),
                                                 default=-1), d["id"])
    rasters, masks = [], {}
    for d in sorted((d for d in on_disk if d["kind"] == "raster"), key=order):
        rec = {"dataset": d["id"], "role": d["role"]}
        try:
            with rasterio.open(legacy / d["legacy_path"]) as src:
                rec.update(dtype=src.dtypes[0], nodata=None if src.nodata is None else float(src.nodata),
                           shape=[src.height, src.width])
                geographic = src.crs is not None and src.crs.is_geographic
                if src.width * src.height <= WHOLE_MAX:
                    rec["valid_cells"] = int(_valid(src.read(1), src.nodata).sum())
                hit = _aoi_window(src, aoi_g) if aoi_g is not None and src.crs is not None else None
                if aoi_g is not None and hit is None:
                    rec["aoi"] = "no overlap"
                elif hit is not None:
                    w, inside = hit
                    v = _valid(src.read(1, window=w), src.nodata)
                    nd = inside & ~v
                    rows = np.arange(int(w.row_off), int(w.row_off + w.height))
                    km2 = float((nd.sum(axis=1) * _cell_km2(src.transform, rows, geographic)).sum())
                    n_in = int(inside.sum())
                    rec.update(cells_inside_aoi=n_in, valid_inside_aoi=int((inside & v).sum()),
                               nodata_inside_aoi=int(nd.sum()), nodata_inside_aoi_km2=round(km2, 4),
                               nodata_inside_aoi_pct=round(100 * int(nd.sum()) / n_in, 2) if n_in else None)
                    masks[d["id"]] = (src.transform, w, inside, v)
                elif src.width * src.height <= WHOLE_MAX:
                    masks[d["id"]] = (src.transform, None, None, _valid(src.read(1), src.nodata))
        except Exception as exc:  # noqa: BLE001
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        rasters.append(rec)
    by_id = {d["id"]: d for d in on_disk}
    transitions = []
    for fam in inv.get("grids") or []:
        mem = [by_id[m["dataset"]] for m in fam.get("members") or [] if m["dataset"] in by_id]
        mem.sort(key=order)
        for a, b in zip(mem, mem[1:]):
            t = {"family": fam["id"], "from": a["id"], "to": b["id"]}
            ma, mb = masks.get(a["id"]), masks.get(b["id"])
            if ma is None or mb is None:
                t["note"] = "not compared (raster too large without an AOI window, or unreadable)"
            elif ma[0] != mb[0] or (ma[1] is not None and ma[1] != mb[1]) or ma[3].shape != mb[3].shape:
                t["note"] = "not compared (members of the family do not share one transform and window)"
            else:
                sel = ma[2] if ma[2] is not None else np.ones(ma[3].shape, bool)
                t.update(where="inside AOI" if ma[2] is not None else "whole raster",
                         valid_lost=int((sel & ma[3] & ~mb[3]).sum()), valid_gained=int((sel & ~ma[3] & mb[3]).sum()))
            transitions.append(t)
    vectors = []
    if aoi_g is not None:
        u = shapely.union_all(shapely.make_valid(aoi_g.geometry.values))
        for d in sorted((d for d in on_disk if d["kind"] == "vector" and d["id"] != aoi["id"]), key=order):
            rec = {"dataset": d["id"], "role": d["role"]}
            try:
                g = read_vec(legacy, d)
                geoms = g.geometry.values
                if g.crs is not None and aoi_g.crs is not None and not CRS(g.crs).equals(CRS(aoi_g.crs)):
                    tr = Transformer.from_crs(g.crs, aoi_g.crs, always_xy=True)
                    geoms = shapely.transform(geoms, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
                rec.update(features=int(len(g)), not_touching_aoi=int((~shapely.intersects(geoms, u)).sum()))
            except Exception as exc:  # noqa: BLE001
                rec["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            vectors.append(rec)
    doc = {"schema": METRICS_SCHEMA, "generator": "axis-migrate.discover.map", "aoi": aoi["id"] if aoi else None,
           "rule": "a cell is inside the AOI when its centre is; km² on the ellipsoid for geographic grids",
           "notes": [x for x in (fallback, aoi_note) if x] + ([] if aoi else ["no AOI: nothing measured inside one"]),
           "rasters": rasters, "transitions": transitions, "vectors": vectors}
    out = migration / "discover" / "metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    ac.dump_json(out, doc)
    return 0, doc


def metrics_table(doc: dict) -> list[str]:
    def n(v):
        return "-" if v is None else (f"{v:,}" if isinstance(v, int) else f"{v:g}")
    lines = [f"discover/metrics.json · AOI {doc['aoi'] or 'none'} · cell centre rule"]
    for r in doc["rasters"]:
        if r.get("error"):
            lines.append(f"  {r['dataset']}: not read ({r['error']})")
            continue
        head = f"  {r['dataset']} [{r['role']}] {r['dtype']} nodata {n(r['nodata'])}"
        if "cells_inside_aoi" in r:
            head += (f": inside AOI {n(r['valid_inside_aoi'])} valid of {n(r['cells_inside_aoi'])} cells, NoData "
                     f"{n(r['nodata_inside_aoi'])} ({n(r['nodata_inside_aoi_pct'])} %, "
                     f"{n(r['nodata_inside_aoi_km2'])} km²)")
        elif r.get("aoi"):
            head += f": AOI {r['aoi']}"
        if "valid_cells" in r:
            head += f"; whole raster {n(r['valid_cells'])} valid"
        lines.append(head)
    for t in doc["transitions"]:
        lines.append(f"  {t['from']} → {t['to']}: " + (t.get("note") or f"{n(t['valid_lost'])} valid cells lost, "
                                                        f"{n(t['valid_gained'])} gained ({t['where']})"))
    for v in doc["vectors"]:
        lines.append(f"  {v['dataset']} [{v['role']}]: " + (f"not read ({v['error']})" if v.get("error") else
                     f"{n(v['not_touching_aoi'])} of {n(v['features'])} features do not touch the AOI"))
    lines += [f"  note: {x}" for x in doc["notes"]]
    return lines


def findings_status(migration: Path) -> tuple[int, dict]:
    import visual_receipt as vr
    inv_p = migration / "inventory.json"
    if not inv_p.exists():
        return 2, {"error": "inventory.json not found: run scan.py first"}
    inv = ac.load_json(inv_p)
    rows, about = [], set()
    for f in (inv.get("map") or {}).get("findings") or []:
        prob = vr.map_finding_problem(migration, f)
        rows.append({"id": f.get("id"), "kind": "view" if f.get("receipt") == "view" else "png",
                     "severity": f.get("severity"), "about": f.get("about") or [], "counts": prob is None,
                     "problem": prob})
        if prob is None:
            about |= set(f.get("about") or [])
    inputs = sorted(d["id"] for d in inv.get("datasets") or [] if d.get("role") == "input" and d.get("exists")
                    and d.get("kind") in ("raster", "vector"))
    res = {"findings": rows, "inputs_without_finding": [d for d in inputs if d not in about]}
    return (0 if all(r["counts"] for r in rows) else 1), res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="map.py", description="Input maps for discover (deterministic PNGs).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inputs", help="inputs_overview.png, grid_corners.png, legacy_rasters.png")
    p.add_argument("migration", type=Path)
    p.add_argument("--json", action="store_true")
    m = sub.add_parser("metrics", help="discover/metrics.json: NoData inside the AOI, valid cells lost between "
                                       "family members, features outside the AOI")
    m.add_argument("migration", type=Path)
    m.add_argument("--json", action="store_true")
    q = sub.add_parser("findings", help="which recorded map findings still count")
    q.add_argument("migration", type=Path)
    q.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "metrics":
        code, res = metrics(a.migration.resolve())
        if code:
            print(res["error"], file=sys.stderr)
        elif a.json:
            print(json.dumps(res, sort_keys=True, indent=1))
        else:
            print("\n".join(metrics_table(res)))
            print("Cite these in map findings: metric.computed_by \"map.py metrics (discover/metrics.json)\"")
        return code
    if a.cmd == "findings":
        code, res = findings_status(a.migration.resolve())
        if a.json or code == 2:
            print(json.dumps(res, sort_keys=True, indent=1), file=sys.stderr if code == 2 else sys.stdout)
        else:
            for r in res["findings"]:
                print(f"{r['id']} [{r['kind']}, {r['severity']}] about {', '.join(r['about']) or '-'}: "
                      + ("counts" if r["counts"] else f"does not count ({r['problem']})"))
            print("inputs no counting finding is about: " + (", ".join(res["inputs_without_finding"]) or "none"))
        return code
    code, res = run_inputs(a.migration.resolve())
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1))
    elif code:
        print(res["error"], file=sys.stderr)
    else:
        for r in res["pngs"]:
            print(r["path"])
            for m in r.get("metrics", []):
                print("  " + m)
        print("Look at each PNG, then record findings with a computed metric: "
              "scan.py annotate <migration> map-finding --json finding.json")
    return code


if __name__ == "__main__":
    sys.exit(main())
