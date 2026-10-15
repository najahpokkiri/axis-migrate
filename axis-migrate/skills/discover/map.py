#!/usr/bin/env python3
"""discover/map.py: the input map the agent looks at before any code exists (01 §4.7).

    map.py inputs <migration> [--json]
    map.py findings <migration> [--json]

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


def inputs_overview(inv: dict, legacy: Path, out: Path) -> dict:
    from pyproj import CRS
    ds = [d for d in inv["datasets"] if d["role"] == "input" and d["exists"] and d["kind"] in ("raster", "vector")]
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
    handles, notes = [], []
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
    ax.set_title(f"Inputs overview ({disp.to_string()}; display only, nothing reprojected for checks)", fontsize=9)
    ax.set_xlabel("x" if not geographic else "longitude")
    ax.set_ylabel("y" if not geographic else "latitude")
    caption = "\n".join(rel_lines + notes)
    fig.text(0.01, 0.01, caption, fontsize=7, va="bottom", family="monospace")
    fig.subplots_adjust(bottom=0.06 + 0.017 * len(caption.splitlines()))
    path = out / "inputs_overview.png"
    save(fig, path)
    return {"path": "discover/inputs_overview.png", "kind": "inputs_overview", "datasets": [d["id"] for d in ds],
            "metrics": rel_lines}


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
    aoi = by_id.get(inv.get("aoi")) if inv.get("aoi") else None
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
    q = sub.add_parser("findings", help="which recorded map findings still count")
    q.add_argument("migration", type=Path)
    q.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
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
