"""Discover's inputs views (views.py inputs) and the map findings that cite them (scan.py annotate map-finding with a
view receipt), on the discover tests' synthetic legacy folder (generic; no fixture facts).

Covers: what is built (inputs-overview, grid-<family>, cell-/feature- projects), cells equal the sources, the grid
windows are grid_corners' windows, determinism, the legacy folder untouched, the headless check, URLs; receipts:
accepted, value mismatch refused, attribute mismatch refused, changed project refused, wrong engine refused, the
finding still counting (map.py findings) and stopping to count when a project's bytes change.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest

from conftest import PLUGIN, map_py, map_python, run_cli

DISCOVER = PLUGIN / "skills" / "discover"
sys.path.insert(0, str(DISCOVER / "tests"))
import scan_synth  # noqa: E402

AXPY = PLUGIN / "scripts" / "axpy"
SCAN = DISCOVER / "scan.py"
MAPPY = DISCOVER / "map.py"


def tree(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    map_python()
    root = tmp_path_factory.mktemp("inputs")
    leg = scan_synth.build_project(root)
    mig = root / "migration"
    scan_synth.scan(leg, mig)
    before = tree(leg)
    cp = run_cli("views.py", "inputs", mig)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return {"legacy": leg, "mig": mig, "before": before}


def doc(mig: Path) -> dict:
    return json.loads((mig / "discover" / "views.json").read_text())


def ent(d: dict, ds: str) -> dict:
    return next(e for e in d["datasets"] if e["dataset"] == ds)


def test_what_is_built(proj):
    d = doc(proj["mig"])
    assert d["schema"] == "axis-migrate.map-views.v1" and d["mode"] == "inputs" and d["root"] == "discover/views"
    assert set(d["projects"]) == {"inputs-overview", "grid-raw"}
    roles = {e["dataset"]: (e["role"], e["family"]) for e in d["datasets"]}
    assert roles == {"ds.raw": ("input", "raster"), "ds.zones": ("input", "vector"),
                     "ds.zones_src": ("input", "vector"), "ds.01_clip": ("intermediate", "raster"),
                     "ds.02_mask": ("final", "raster")}
    assert {s["dataset"] for s in d["skipped"]} == {"ds.rates"}               # a table has no map view
    ov = d["projects"]["inputs-overview"]
    vis = {lay["dataset"]: lay["visible"] for lay in ov["layers"]}
    assert vis == {"ds.raw": True, "ds.zones": True, "ds.zones_src": True, "ds.01_clip": False, "ds.02_mask": False}
    for e in d["datasets"]:
        if e["family"] == "raster":
            assert [c["id"] for c in e["cells_to_identify"]] == ["max", "aoi-edge", "interior"], e["dataset"]
            assert e["files"]["data"]["file"] == f"discover/views/{e['dataset']}/data.tif"
        else:
            assert e["features_to_identify"] and e["files"]["data"]["file"].endswith("data.geojson")
    g = d["projects"]["grid-raw"]
    assert g["reference"] == "ds.raw" and len(g["windows"]) == 5 and len(g["bookmarks"]) == 5
    names = [lay["name"] for lay in g["layers"]]
    assert {"cell edges ds.raw", "cell edges ds.01_clip", "cell edges ds.02_mask", "AOI (ds.zones)"} <= set(names)


def test_projects_validate(proj):
    res = map_py("import views, json, pathlib; r = pathlib.Path(sys.argv[1]); "
                 "print(json.dumps({str(p): views.validate_project(p) for p in sorted(r.rglob('*.geolibre.json'))}))",
                 str(proj["mig"] / "discover" / "views"))
    assert len(res) == 15 and all(v == [] for v in res.values()), {k: v for k, v in res.items() if v}


def test_cells_and_features_equal_the_sources(proj):
    import pyogrio
    import rasterio
    from rasterio.windows import Window
    d = doc(proj["mig"])
    for e in d["datasets"]:
        src = proj["legacy"] / e["source"]["path"]
        assert hashlib.sha256(src.read_bytes()).hexdigest() == e["source"]["sha256"] == e["source"]["inventory_sha256"]
        if e["family"] == "raster":
            with rasterio.open(src) as s, rasterio.open(proj["mig"] / e["files"]["data"]["file"]) as c:
                assert c.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
                assert (s.read(1) == c.read(1)).all() and s.nodata == c.nodata
                for cell in e["cells_to_identify"]:
                    v = s.read(1, window=Window(cell["col"], cell["row"], 1, 1))[0, 0]
                    assert cell["value"] == (None if v == s.nodata else float(v)), (e["dataset"], cell["id"])
                vals = s.read(1, masked=True)
                mx = next(c for c in e["cells_to_identify"] if c["id"] == "max")
                assert mx["value"] == float(vals.max())
        else:
            g = pyogrio.read_dataframe(src, layer=e["source"]["layer"])
            for f in e["features_to_identify"]:
                row = g.iloc[f["index"]]
                for k, v in f["properties"].items():
                    assert row[k] == v, (e["dataset"], f["id"], k)


def test_grid_windows_are_grid_corners_windows(proj):
    """The grid project's bookmarks are the five windows discover/map.py grid_corners draws (AOI extremes + inside)."""
    d = doc(proj["mig"])
    g = d["projects"]["grid-raw"]
    assert g["windows_at"] == "four extreme points of the AOI and one point inside"
    assert [w.split(" (")[0] for w in g["windows"]] == [
        "grid.raw west extreme of AOI", "grid.raw north extreme of AOI", "grid.raw east extreme of AOI",
        "grid.raw south extreme of AOI", "grid.raw inside AOI"]
    edges = json.loads((proj["mig"] / "discover/views/grid-raw/edges-ds.raw.geojson").read_text())
    # 14×14-cell windows: 15 column and 15 row edges each, clipped to the window
    per = {}
    for f in edges["features"]:
        per.setdefault(f["properties"]["window"], 0)
        per[f["properties"]["window"]] += 1
    assert set(per) == {w.split("grid.raw ", 1)[1].split(" (")[0] for w in g["windows"]}
    assert all(28 <= n <= 30 for n in per.values()), per


def test_deterministic_and_legacy_untouched(proj):
    d1 = doc(proj["mig"])
    t1 = tree(proj["mig"] / "discover" / "views")
    cp = run_cli("views.py", "inputs", proj["mig"])
    assert cp.returncode == 0, cp.stderr
    assert doc(proj["mig"]) == d1 and tree(proj["mig"] / "discover" / "views") == t1
    after = tree(proj["legacy"])
    assert after == proj["before"]
    assert not [p for p in after if p.endswith((".aux.xml", "-wal", "-shm")) and p not in proj["before"]]


def test_check_and_url(proj):
    cp = run_cli("check.py", "--inputs", proj["mig"], "--json")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    res = json.loads(cp.stdout)
    assert res["ok"] and res["projects"] == 15 and res["problems"] == []
    cp = run_cli("views.py", "inputs-url", proj["mig"], "--view", "inputs-overview", "--port", "8765")
    want = ("http://127.0.0.1:8765/?url=" + urllib.parse.quote(
        "http://127.0.0.1:8765/data/discover/views/inputs-overview.geolibre.json", safe="") + "&panels=collapsed")
    assert cp.returncode == 0 and cp.stdout.strip() == want
    cp = run_cli("views.py", "inputs-url", proj["mig"], "--dataset", "ds.raw", "--cell", "max", "--port", "8765")
    assert cp.returncode == 0 and "ds.raw/cell-max.geolibre.json" in urllib.parse.unquote(cp.stdout)
    cp = run_cli("views.py", "inputs-url", proj["mig"], "--dataset", "ds.raw", "--cell", "worst", "--port", "8765")
    assert cp.returncode == 2


# ---------------------------------------------------------------------------------------------------------------
# map findings with a view receipt (scan.py annotate map-finding)
# ---------------------------------------------------------------------------------------------------------------
def popup(v):
    return float(f"{v:.3g}") if isinstance(v, float) else v


def receipt(d: dict, **over) -> dict:
    """A finding about ds.raw and ds.zones that passes every check: overview, one cell and one feature attribute."""
    ov = d["projects"]["inputs-overview"]
    raw, zones = ent(d, "ds.raw"), ent(d, "ds.zones")
    cell = raw["cells_to_identify"][0]
    feat = zones["features_to_identify"][0]
    ref = lambda x: {"project": x["project"], "project_sha256": x["project_sha256"]}  # noqa: E731
    f = {"view": ref(ov), "views_seen": [ref(ov), ref(cell), ref(feat)], "engine": "maplibre-gl-raster",
         "cells_read": [{"dataset": "ds.raw", "cell": cell["id"], "value": popup(cell["value"])}],
         "features_read": [{"dataset": "ds.zones", "feature": feat["id"], "attribute": "name",
                            "value": feat["properties"]["name"]}],
         "about": ["ds.raw", "ds.zones"], "observation": "raster covers every zone; west zone edge on cell edges",
         "severity": "info", "metric": {"name": "valid_cells", "value": raw["raster"]["counts"]["valid"],
                                        "computed_by": "views.json raster.counts"}}
    f.update(over)
    return f


def annotate(mig: Path, tmp: Path, f: dict) -> subprocess.CompletedProcess:
    p = tmp / "finding.json"
    p.write_text(json.dumps(f))
    return subprocess.run([str(AXPY), str(SCAN), "annotate", str(mig), "map-finding", "--json", str(p)],
                          capture_output=True, text=True, timeout=300)


def findings(mig: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(AXPY), str(MAPPY), "findings", str(mig), "--json"], capture_output=True, text=True,
                          timeout=300)


def test_view_finding_accepted_and_counts_like_a_png(proj, tmp_path):
    mig = proj["mig"]
    d = doc(mig)
    cp = annotate(mig, tmp_path, receipt(d, id="MF-view"))
    assert cp.returncode == 0, cp.stderr
    (mig / "discover" / "x.png").write_bytes(b"\x89PNG fake")
    cp = annotate(mig, tmp_path, {"id": "MF-png", "png": "discover/x.png", "observation": "slivers",
                                  "severity": "question", "about": ["ds.zones_src"]})
    assert cp.returncode == 0, cp.stderr
    inv = json.loads((mig / "inventory.json").read_text())
    rec = next(x for x in inv["map"]["findings"] if x["id"] == "MF-view")
    assert rec["receipt"] == "view" and rec["state"] == "ai_inferred" and rec["raises_question"] is False
    st = findings(mig)
    assert st.returncode == 0, st.stdout + st.stderr
    res = json.loads(st.stdout)
    got = {r["id"]: (r["kind"], r["counts"]) for r in res["findings"]}
    assert got["MF-view"] == ("view", True) and got["MF-png"] == ("png", True)
    assert res["inputs_without_finding"] == []
    # a rescan keeps it, and it still counts
    scan_synth.scan(proj["legacy"], mig)
    assert json.loads(findings(mig).stdout)["findings"]


def test_value_mismatch_refused(proj, tmp_path):
    d = doc(proj["mig"])
    f = receipt(d)
    v = f["cells_read"][0]["value"]
    f["cells_read"][0]["value"] = v * 1.05 + 1
    cp = annotate(proj["mig"], tmp_path, f)
    assert cp.returncode == 2 and "wrong cell or wrong layer" in cp.stderr
    f = receipt(d)
    f["features_read"][0]["value"] = "Zone Nowhere"
    cp = annotate(proj["mig"], tmp_path, f)
    assert cp.returncode == 2 and "wrong feature or wrong layer" in cp.stderr
    f = receipt(d, cells_read=[])                       # ds.raw is covered but nothing was identified on it
    f["views_seen"] = f["views_seen"][:1] + f["views_seen"][2:]
    cp = annotate(proj["mig"], tmp_path, f)
    assert cp.returncode == 2 and "ds.raw: identify at least one cell" in cp.stderr
    cp = annotate(proj["mig"], tmp_path, receipt(d, engine="cog-tiler-wasm"))
    assert cp.returncode == 2 and "maplibre-gl-raster" in cp.stderr
    f = receipt(d)
    f["views_seen"] = f["views_seen"][1:]
    f["view"] = f["views_seen"][0]
    cp = annotate(proj["mig"], tmp_path, f)
    assert cp.returncode == 2 and "inputs-overview" in cp.stderr


def test_changed_project_refused_and_stops_counting(proj, tmp_path):
    mig = proj["mig"]
    d = doc(mig)
    assert annotate(mig, tmp_path, receipt(d, id="MF-change")).returncode == 0
    p = mig / ent(d, "ds.raw")["cells_to_identify"][0]["project"]
    keep = p.read_bytes()
    try:
        p.write_bytes(keep.replace(b'"basemapVisible": false', b'"basemapVisible": true'))
        assert p.read_bytes() != keep
        cp = annotate(mig, tmp_path, receipt(d))
        assert cp.returncode == 2 and "bytes changed" in cp.stderr
        st = findings(mig)
        assert st.returncode == 1
        r = next(x for x in json.loads(st.stdout)["findings"] if x["id"] == "MF-change")
        assert r["counts"] is False and "bytes changed" in r["problem"]
    finally:
        p.write_bytes(keep)
    assert findings(mig).returncode == 0


# ---------------------------------------------------------------------------------------------------------------
# Lux e2e feedback: cameras fit a 1280×960 pane, no $TMPDIR writes, GPU engine preset + readiness line (headless)
# ---------------------------------------------------------------------------------------------------------------
def test_overview_fits_its_data_at_the_viewport(proj):
    import math
    d = doc(proj["mig"])
    cam = d["projects"]["inputs-overview"]["camera"]
    bbs = [e["bounds_4326"] for e in d["datasets"] if e["role"] == "input"]
    w, s, e, n = (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))
    merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)  # noqa: E731
    scale = 512 * 2 ** cam["zoom"]
    width_px, height_px = (e - w) / 360 * scale, (merc(n) - merc(s)) * scale
    assert width_px <= 1192 and height_px <= 749                            # whole extent in the map canvas
    assert max(width_px / 1192, height_px / 749) > 0.5                      # and not a tiny speck in it
    assert cam["center"][0] == pytest.approx((w + e) / 2, abs=1e-6)
    # the page refits the overview to the canvas it really has (an 800 px side pane too): bounds in the project
    pr = json.loads((proj["mig"] / d["projects"]["inputs-overview"]["project"]).read_text())
    assert pr["metadata"]["axis-migrate"]["fit_bounds"] == pytest.approx([w, s, e, n], abs=1e-6)


def test_no_input_role_falls_back_to_unknown_and_unused(proj):
    """B4: an inventory where nothing has role input (the scan could not tell what the code reads) still gets views:
    every on-disk raster/vector with role unknown/unused, said so in views.json and the overview title; with no AOI
    named, the single polygon layer is the AOI; a VRT alias (alias_of) is never shown twice."""
    import shutil as sh
    fb = proj["mig"].parent / "migration_fb"
    if fb.exists():
        sh.rmtree(fb)
    fb.mkdir()
    inv = json.loads((proj["mig"] / "inventory.json").read_text())
    inv["aoi"] = None
    by = {d["id"]: d for d in inv["datasets"]}
    for d in inv["datasets"]:
        if d.get("role") == "input":
            d["role"] = "unknown"
    by["ds.zones_src"]["role"] = "unused"
    by["ds.zones_src"]["kind"] = "table"                  # leaves ds.zones as the only polygon layer shown
    by["ds.extra_display"]["role"] = "intermediate"
    inv["datasets"].append({**by["ds.raw"], "id": "ds.raw_alias", "role": "unknown", "alias_of": "ds.raw"})
    (fb / "inventory.json").write_text(json.dumps(inv))
    cp = run_cli("views.py", "inputs", fb)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "no dataset has role input" in cp.stdout and "AOI: ds.zones (the only polygon layer" in cp.stdout
    d = doc(fb)
    assert d["shown"].startswith("no dataset has role input: showing every on-disk raster/vector")
    assert d["aoi"] == "ds.zones" and d["aoi_note"].startswith("AOI: ds.zones (the only polygon layer")
    roles = {e["dataset"]: e["role"] for e in d["datasets"]}
    assert roles == {"ds.raw": "unknown", "ds.zones": "unknown", "ds.01_clip": "intermediate",
                     "ds.02_mask": "final"}
    assert "ds.raw_alias" not in roles
    ov = d["projects"]["inputs-overview"]
    vis = {lay["dataset"]: lay["visible"] for lay in ov["layers"]}
    assert vis["ds.raw"] is True and vis["ds.zones"] is True and vis["ds.01_clip"] is False
    pr = json.loads((fb / ov["project"]).read_text())
    assert "no dataset has role input" in pr["name"]
    assert [c["id"] for c in next(e for e in d["datasets"] if e["dataset"] == "ds.raw")["cells_to_identify"]][1] \
        == "aoi-edge"                                     # the derived AOI is used for the edge cell
    # with an input present nothing falls back
    assert doc(proj["mig"])["shown"] == "inputs (role input) and legacy rasters"


def test_no_writes_to_tmpdir(proj, tmp_path):
    import os
    td = tmp_path / "tmpdir"
    td.mkdir()
    env = dict(os.environ, TMPDIR=str(td), TMP=str(td), TEMP=str(td))
    cp = run_cli("views.py", "inputs", proj["mig"], env=env)
    assert cp.returncode == 0, cp.stderr
    assert list(td.iterdir()) == []


def chrome() -> str | None:
    import shutil as sh
    return next((c for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser") if sh.which(c)),
                None)


def test_crisp_metric_separates_squares_from_smoothing():
    res = map_py("import render_check as R, io, json, numpy as np; from PIL import Image\n"
                 "rng = np.random.default_rng(1); cells = rng.integers(0, 255, (12, 12, 3)).astype('uint8')\n"
                 "sq = np.kron(cells, np.ones((64, 64, 1), dtype='uint8'))\n"
                 "img = Image.fromarray(sq); sm = img.resize((48, 48)).resize(img.size, Image.BILINEAR)\n"
                 "def png(i):\n b = io.BytesIO(); i.save(b, 'PNG'); return b.getvalue()\n"
                 "rect = {'x': 384, 'y': 384, 'width': 768, 'height': 768}\n"
                 "print(json.dumps([R.crisp_ratio(png(img), rect), R.crisp_ratio(png(sm), rect)]))")
    crisp, smooth = res
    assert crisp < 0.03 and smooth > 0.15


@pytest.mark.skipif(chrome() is None, reason="no Chrome/Chromium for the headless render check")
def test_engine_preset_and_readiness_headless(proj):
    """Served override: the page switches rasters to maplibre-gl-raster itself (no Add Raster Layer dialog), marks
    itself ready (data-axis-map, status line with the project sha256 and the click centre), and the cells are drawn
    as squares. Without the override there is no readiness marker."""
    cp = run_cli("check.py", "--inputs", proj["mig"], "--render", "--json", "--keep-server")
    try:
        assert cp.returncode == 0, cp.stdout + cp.stderr
        res = json.loads(cp.stdout)
        r = {x["view"]: x for x in res["render"]["results"]}
        assert res["render"]["pages"] == 5 and set(r) >= {"inputs-overview", "grid-raw", "ds.raw/cell-max"}
        for x in r.values():
            assert x["ready"] and x["engine"] == "maplibre-gl-raster" and x["crisp"] <= 0.06, x
        st = json.loads((proj["mig"] / ".axis" / "map" / "server.json").read_text())
        d = doc(proj["mig"])
        cell = ent(d, "ds.raw")["cells_to_identify"][0]
        out = map_py("import render_check as R, json, views\n"
                     f"u = views.page_url({st['port']}, {cell['url_path']!r})\n"
                     "print(json.dumps(R.check_pages([u])[0]))")
        assert out["status"]["projectSha256"] == cell["project_sha256"]           # the page hashed what it shows
        assert "click centre" in out["status_line"] and out["status"]["centre"]["x"] > 0
        (proj["mig"] / ".axis" / "map" / "override" / "geolibre-runtime-config.js").unlink()
        ov = d["projects"]["inputs-overview"]["url_path"]
        out = map_py("import render_check as R, json, views\n"
                     f"print(json.dumps(R.check_pages([views.page_url({st['port']}, {ov!r})])[0]))")
        assert "no readiness marker" in out["problem"]
        # and without the preset the stock engine smooths the cells: the crisp check is what fails then
        assert out["crisp"] is not None and out["crisp"] > 0.06, out
    finally:
        run_cli("stop.py", proj["mig"])
