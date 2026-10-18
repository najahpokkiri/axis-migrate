"""Synthetic GeoLibre view indexes (views.json + project files) and view receipts for tests (no geolibre needed).

The real views are built by skills/map/views.py (tested in skills/map/tests); the receipt rules only read the index
and the project bytes, so a small stand-in project file per view is enough here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

VIEWS = {"raster": ("overview", "worst", "edges", "nodata"), "vector": ("overview", "worst")}
CELLS = {"interior": (10.5678, 10.5678), "edge": (1.0, None), "worst": (12.25, 3.125)}
BOUNDS = [10.0, 45.0, 10.6, 45.8]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run_id_of(run: Path) -> str:
    m = run / "manifest.json"
    return (json.loads(m.read_text()).get("run_id") if m.is_file() else None) or run.name


def make_views(run: Path, datasets: dict[str, str], *, root: str = "verify/views", counts: dict | None = None,
               cells: dict | None = None) -> dict:
    """datasets: {ds: family}. counts: {ds: validity_counts} (default: no one-side-only cells)."""
    entries = []
    for ds, fam in sorted(datasets.items()):
        d = run / root / ds
        d.mkdir(parents=True, exist_ok=True)
        recs = []
        if fam == "raster":
            for cid, (lv, nv) in sorted((cells or CELLS).items()):
                p = d / f"cell-{cid}.geolibre.json"
                p.write_text(json.dumps({"name": f"{ds} · cell {cid}", "layers": []}, sort_keys=True))
                recs.append({"id": cid, "why": f"cell {cid}", "legacy_value": lv, "new_value": nv, "validity": 3,
                             "project": f"{root}/{ds}/{p.name}", "project_sha256": _sha(p)})
        views = {}
        for v in VIEWS[fam]:
            p = d / f"{v}.geolibre.json"
            p.write_text(json.dumps({"name": f"{ds} · {v}", "layers": []}, sort_keys=True))
            views[v] = {"project": f"{root}/{ds}/{p.name}", "project_sha256": _sha(p),
                        "cells_to_identify": copy.deepcopy(recs) if v != "worst" else copy.deepcopy(recs[-1:]),
                        "layers": []}
        e = {"dataset": ds, "family": fam, "views": views, "bounds_4326": list(BOUNDS)}
        if fam == "raster":
            e["validity_counts"] = (counts or {}).get(ds) or {"0": 4, "1": 0, "2": 0, "3": 96}
        entries.append(e)
    doc = {"schema": "axis-migrate.map-views.v1", "run_id": run_id_of(run), "root": root, "datasets": entries,
           "skipped": []}
    (run / root).mkdir(parents=True, exist_ok=True)
    (run / root / "views.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    doc["_mlview"] = make_mlview(run / root / "mlview", datasets, cells or CELLS)
    return doc


def make_mlview(out: Path, datasets: dict[str, str], cells: dict) -> dict:
    """A stand-in MapLibre page (skills/map/mlview.py): data.js bytes and mlview.json with the points to identify
    and, per layer, what the page's identify returns there (exact layers holding compare's values)."""
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data.js"
    data.write_text("window.AXIS_DATA = " + json.dumps(sorted(datasets.items())) + ";\n")
    points = {}
    for ds, fam in sorted(datasets.items()):
        if fam == "raster":
            pts = []
            for i, (cid, (lv, nv)) in enumerate(sorted(cells.items())):
                lay = {}
                for side, v in (("legacy", lv), ("new", nv)):
                    page = {"row": i, "col": i, "value": v} if v is not None else \
                        {"row": i, "col": i, "value": None, "note": "nodata"}
                    lay[f"{ds} · {side}"] = {"page": page, "compare": v, "exact": True, "compare_match": True}
                pts.append({"id": cid, "why": f"cell {cid}", "lon": 10.1 + i / 10, "lat": 45.1 + i / 10,
                            "in_page": True, "layers": lay})
        else:
            feat = {"key": "k1", "name": "zone 1"}
            pts = [{"id": "feature", "why": "feature", "lon": 10.3, "lat": 45.3, "in_page": True,
                    "layers": {f"{ds} · {side}": {"page": [feat], "compare": feat, "exact": True,
                                                   "compare_match": True} for side in ("legacy", "new")}}]
        points[ds] = pts
    doc = {"schema": "axis-migrate.mlview.v1", "data_sha256": _sha(data), "points": points, "points_note": None,
           "identify": {ds: "window.__AXIS_MAP__.identifyMany(" + json.dumps([[p["lon"], p["lat"]] for p in pts])
                        + ")" for ds, pts in points.items()}}
    (out / "mlview.json").write_text(json.dumps(doc, indent=1, sort_keys=True))
    return doc


def identified(ml: dict, ds: str, *, dataset: bool = False) -> list[dict]:
    """What a reviewer pastes after running identifyMany on the page: one entry per point, the page's own values."""
    return [{**({"dataset": ds} if dataset else {}), "point": p["id"],
             "exact": all(l["exact"] for l in p["layers"].values()),
             "identify": {"lon": p["lon"], "lat": p["lat"], "layers": {k: v["page"] for k, v in p["layers"].items()}}}
            for p in ml["points"][ds] if p["in_page"]]


def entry(doc: dict, ds: str) -> dict:
    return next(e for e in doc["datasets"] if e["dataset"] == ds)


def view_receipt(doc: dict, ds: str, *, flag: bool = False, cells=("interior",), main: str | None = None,
                 **over) -> dict:
    """A receipt that passes every check: every view seen, the cell projects of the cells read, values as in
    views.json (rounded like the popup)."""
    e = entry(doc, ds)
    fam = e["family"]
    seen = [{"project": ve["project"], "project_sha256": ve["project_sha256"]} for _, ve in sorted(e["views"].items())]
    allcells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
    read = []
    for cid in ([x for x in cells if x in allcells] or sorted(allcells) if fam == "raster" else ()):
        c = allcells[cid]                    # views.py builds one cell now: the asked ones it has, else that one
        seen.append({"project": c["project"], "project_sha256": c["project_sha256"]})
        read.append({"cell": cid, "legacy": _popup(c["legacy_value"]), "new": _popup(c["new_value"])})
    mv = e["views"][main or ("edges" if (flag and fam == "raster") else "overview")]
    f = {"dataset_id": ds, "question_id": "Q-all", "answer": "no", "effect": "none", "category": "none",
         "view": {"project": mv["project"], "project_sha256": mv["project_sha256"]}, "views_seen": seen,
         "note": "", "model": "test-model"}
    if fam == "raster":
        f["engine"] = "maplibre-gl-raster"
        f["cells_read"] = read
    if doc.get("_mlview"):
        f["mlview"] = {"data_sha256": doc["_mlview"]["data_sha256"]}
        f["identified"] = identified(doc["_mlview"], ds)
    w = Path(mv["project"]).name.replace(".geolibre.json", "")
    f["looked"] = [{"check": c, "answer": "ok", "where": w, "saw": "stand-in: " + c + " checked on " + w, **({"number": {"name": "cells valid on one side only", "value": 0, "computed_by": "test stand-in"}} if c == "edges" else {})} for c in ("place", "coverage", "edges", "values", "classes")]
    if flag:
        f.update(question_id="Q1" if fam == "vector" else "Q6", answer="yes", effect="flag",
                 category="boundary_stripes", layer="validity" if fam == "raster" else "new",
                 note="one-cell blue stripe on the west edge, orange on the east",
                 location={"crs": "EPSG:4326", "bbox": list(e.get("bounds_4326") or [10.1, 45.1, 10.2, 45.2])},
                 observations=[{"what": "cells valid on legacy only", "value": 40, "unit": "cells"}])
    f.update(over)
    return f


def _popup(v):
    """What the identify popup prints: about 3 significant figures."""
    if v is None:
        return None
    return float(f"{v:.3g}")
