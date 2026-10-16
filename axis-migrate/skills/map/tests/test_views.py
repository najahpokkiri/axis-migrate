"""views.py on a synthetic run: COGs, validity encoding, projects (geolibre round trip), index, determinism, seal."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import stat
import urllib.parse
from pathlib import Path

import numpy as np
import pytest

import synth_map
from conftest import entry, map_py, run_cli, views_doc

VALIDATE = ("import json, views, pathlib; root = pathlib.Path(sys.argv[1]); "
            "print(json.dumps({p.name if p.parent == root else str(p.relative_to(root)): views.validate_project(p) "
            "for p in sorted(root.rglob('*.geolibre.json'))}))")


def read_cell(path: Path, row: int, col: int):
    import rasterio
    from rasterio.windows import Window
    with rasterio.open(path) as ds:
        v = ds.read(1, window=Window(col, row, 1, 1))[0, 0]
        m = ds.read_masks(1, window=Window(col, row, 1, 1))[0, 0]
        return v, bool(m), ds


def test_every_compared_dataset_has_views(synth_run):
    doc = views_doc(synth_run)
    assert doc["schema"] == "axis-migrate.map-views.v1" and doc["run_id"] == synth_run.name
    got = {e["dataset"]: sorted(e["views"]) for e in doc["datasets"]}
    assert got == {"ds.b": ["edges", "overview", "worst"], "ds.e": ["edges", "overview"],
                   "ds.v": ["overview", "worst"]}             # nodata folded into edges (map review C7)
    ed = json.loads((synth_run / entry(synth_run, "ds.b")["views"]["edges"]["project"]).read_text())
    assert {lay["name"].split(" ")[0]: lay["visible"] for lay in ed["layers"]} == \
        {"legacy": True, "new": False, "diff": False, "validity": True}   # validity over legacy
    assert "whole extent (validity everywhere)" in entry(synth_run, "ds.b")["views"]["edges"]["bookmarks"]
    # ds.e differs only in NoData (max|d| = 0): no worst view, and views.json says why
    assert entry(synth_run, "ds.e")["omitted_views"] == {"worst": "no difference: max |new − legacy| = 0"}
    assert entry(synth_run, "ds.b")["omitted_views"] == {}
    sk = {s["dataset"]: s for s in doc["skipped"]}
    assert set(sk) == {"ds.a", "ds.c"} and "no map view" in sk["ds.c"]["reason"]   # a table has no map view
    # ds.a is identical to legacy: nothing to show, no views built, and the skip counts as reviewed
    assert sk["ds.a"]["kind"] == "identical" and sk["ds.a"]["counts_as_review"] is True
    cmp = json.loads((synth_run / "verify" / "compare" / "ds.a.json").read_text())
    assert sk["ds.a"]["new_sha256"] == cmp["new"]["sha256"] and not (synth_run / "verify" / "views" / "ds.a").exists()
    for e in doc["datasets"]:
        for v, ve in e["views"].items():
            p = synth_run / ve["project"]
            assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() == ve["project_sha256"]
            assert ve["url_path"] == "/data/" + urllib.parse.quote(
                p.relative_to(synth_run.parents[1]).as_posix(), safe="/")
            for lay in ve["layers"]:
                assert lay["url_path"].startswith("/data/runs/") and (synth_run / lay["file"]).is_file()


def test_projects_reload_through_geolibre(synth_run):
    res = map_py(VALIDATE, str(synth_run / "verify" / "views"))
    assert len(res) == 3 + 1 + 2 + 1 + 2                                 # views + one cell project (ds.b, ds.e, ds.v)
    assert all(v == [] for v in res.values()), res


def test_project_shape(synth_run):
    ve = entry(synth_run, "ds.b")["views"]["worst"]
    pr = json.loads((synth_run / ve["project"]).read_text())
    assert pr["basemapStyleUrl"] == "" and pr["basemapVisible"] is False
    assert pr["interaction"] == {"identify": "all", "controls": {}}
    names = [lay["name"] for lay in pr["layers"]]
    assert names[:2] == ["legacy", "new"] and names[2].startswith("diff") and names[3].startswith("validity")
    vis = {lay["name"].split(" ")[0]: lay["visible"] for lay in pr["layers"]}
    assert vis == {"legacy": True, "new": True, "diff": True, "validity": False}
    leg, new = pr["layers"][0]["metadata"]["rasterState"], pr["layers"][1]["metadata"]["rasterState"]
    assert leg["rescale"] == new["rescale"] and leg["colormap"] == "viridis"     # one shared scale
    diff = pr["layers"][2]["metadata"]["rasterState"]
    assert diff["colormap"] == "puor" and diff["reversed"] is True
    assert diff["rescale"][0][0] == -diff["rescale"][0][1] > -math.inf       # symmetric
    assert pr["mapView"]["center"] == ve["camera"]["center"] and pr["mapView"]["zoom"] == ve["camera"]["zoom"]
    for lay in pr["layers"]:
        assert lay["source"]["url"].startswith("/data/") and "127.0.0.1" not in json.dumps(lay)
    # no animated starry sky behind the data; the layer control and the deck.gl overlay stay active
    act = pr["plugins"]["activePluginIds"]
    assert "maplibre-atmosphere-effects" not in act and {"maplibre-layer-control", "maplibre-deckgl-viz"} <= set(act)
    assert "fit_bounds" not in pr["metadata"]["axis-migrate"]               # worst opens on its place


def test_extent_views_fit_their_data(synth_run):
    """overview and nodata carry the data bounds (the page fits them into whatever canvas it has: 1280×960 or an
    800 px side pane); their stored camera already shows the whole extent in GeoLibre's 1192×749 canvas."""
    for ds in ("ds.b", "ds.e", "ds.v"):
        e = entry(synth_run, ds)
        for v in ("overview", "nodata"):
            if v not in e["views"]:
                continue
            ve = e["views"][v]
            pr = json.loads((synth_run / ve["project"]).read_text())
            w, s_, ea, n = pr["metadata"]["axis-migrate"]["fit_bounds"]
            assert [w, s_, ea, n] == pytest.approx(e["bounds_4326"], abs=1e-6)
            merc = lambda lat: math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)  # noqa: E731
            scale = 512 * 2 ** ve["camera"]["zoom"]
            assert (ea - w) / 360 * scale <= 1192 and (merc(n) - merc(s_)) * scale <= 749, (ds, v)
    for c in entry(synth_run, "ds.b")["views"]["worst"]["cells_to_identify"]:
        pr = json.loads((synth_run / c["project"]).read_text())
        assert "fit_bounds" not in pr["metadata"]["axis-migrate"]             # a cell stays at the canvas centre


def test_shared_scale_is_p2_p98_of_both_sides(synth_run):
    e = entry(synth_run, "ds.b")
    a = synth_map.legacy_array()
    b = synth_map.shifted_east(a)
    vals = np.concatenate([a[a != synth_map.NODATA], b[b != synth_map.NODATA]]).astype("float64")
    lo, hi = np.percentile(vals, [2, 98])
    assert math.isclose(e["scale"]["min"], lo) and math.isclose(e["scale"]["max"], hi)


def test_cogs_are_cogs_and_copies_keep_values(synth_run):
    import rasterio
    e = entry(synth_run, "ds.b")
    leg_src = synth_run.parents[2] / "legacy" / "out" / "b.tif"
    new_src = synth_run / "outputs" / "s2" / "b.tif"
    for side, src in (("legacy", leg_src), ("new", new_src)):
        cog = synth_run / e["files"][side]["file"]
        with rasterio.open(cog) as c, rasterio.open(src) as s:
            assert c.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"
            assert c.transform == s.transform and c.crs == s.crs and c.nodata == s.nodata and c.dtypes == s.dtypes
            assert np.array_equal(c.read(1), s.read(1))
    for k in ("diff", "validity"):
        with rasterio.open(synth_run / e["files"][k]["file"]) as c:
            assert c.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") == "COG"


def test_known_cells_equal_the_sources(synth_run):
    """Every cell to identify: the value read from each COG equals the source file's value and views.json."""
    import rasterio
    from rasterio.windows import Window
    e = entry(synth_run, "ds.b")
    leg_src = synth_run.parents[2] / "legacy" / "out" / "b.tif"
    new_src = synth_run / "outputs" / "s2" / "b.tif"
    cells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
    assert set(cells) == {"worst"}                          # one cell to read: the worst when max |d| > 0
    for c in cells.values():
        for side, src, rk, ck in (("legacy", leg_src, "row", "col"), ("new", new_src, "new_row", "new_col")):
            with rasterio.open(src) as s:
                sv = s.read(1, window=Window(c[ck], c[rk], 1, 1))[0, 0]
                valid = sv != s.nodata
            cv, cm, _ = read_cell(synth_run / e["files"][side]["file"], c[rk], c[ck])
            assert cv == sv
            assert c[f"{side}_value"] == (float(sv) if valid else None)
            assert cm == valid
        dv, _, _ = read_cell(synth_run / e["files"]["diff"]["file"], c["grid_row"], c["grid_col"])
        vv, _, _ = read_cell(synth_run / e["files"]["validity"]["file"], c["grid_row"], c["grid_col"])
        assert int(vv) == c["validity"]
        if c["diff"] is None:
            assert math.isnan(dv)
        else:
            assert dv == c["diff"] == c["new_value"] - c["legacy_value"]
        # the cell's own project opens with the camera on the cell centre
        pr = json.loads((synth_run / c["project"]).read_text())
        assert pr["mapView"]["center"] == c["camera"]["center"] == [c["lon"], c["lat"]]
        assert hashlib.sha256((synth_run / c["project"]).read_bytes()).hexdigest() == c["project_sha256"]
    w = cells["worst"]
    a = synth_map.legacy_array().astype("float64")
    b = synth_map.shifted_east(synth_map.legacy_array()).astype("float64")
    both = (a != synth_map.NODATA) & (b != synth_map.NODATA)
    d = np.where(both, b - a, 0)
    assert abs(w["diff"]) == np.abs(d).max()


def test_validity_shows_a_one_cell_shift_as_stripes(synth_run):
    import rasterio
    e = entry(synth_run, "ds.b")
    with rasterio.open(synth_run / e["files"]["validity"]["file"]) as ds:
        v = ds.read(1)
        assert ds.dtypes[0] == "uint8" and ds.nodata == 0
    a = synth_map.legacy_array()
    # legacy is nodata in columns 0-3; new (shifted east) in 0-4: column 4 is valid in legacy only, full height
    assert (v[:, 4] == 1).all() and (v[:, :4] == 0).all()
    # the hole (rows 30-32, cols 20-22) moves to 21-23: col 20 new only, col 23 legacy only
    assert (v[30:33, 20] == 2).all() and (v[30:33, 23] == 1).all()
    assert (v[30:33, 21:23] == 0).all()
    assert e["validity_counts"] == {"0": int((v == 0).sum()), "1": int((v == 1).sum()), "2": int((v == 2).sum()),
                                    "3": int((v == 3).sum())}
    assert int((v == 1).sum()) == synth_map.N + 3
    e0 = entry(synth_run, "ds.e")                                          # hole in new only: 9 legacy-only cells
    with rasterio.open(synth_run / e0["files"]["validity"]["file"]) as ds:
        v0 = ds.read(1)
    assert int((v0 == 1).sum()) == 9 and not (v0 == 2).any() and (v0[10:13, 10:13] == 1).all()
    assert {c["id"] for ve in e0["views"].values() for c in ve["cells_to_identify"]} == {"edge"}   # no worst: edge
    # the edges view looks at the stripe
    cam = e["views"]["edges"]["camera"]
    assert cam["source"] == "densest one-side-only cells"


def test_vectors_are_geojson_4326_with_attributes(synth_run):
    e = entry(synth_run, "ds.v")
    for side in ("legacy", "new"):
        fc = json.loads((synth_run / e["files"][side]["file"]).read_text())
        assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 6
        p = fc["features"][0]["properties"]
        assert {"key", "name", "value"} <= set(p)
        x, y = fc["features"][0]["geometry"]["coordinates"][0][0]
        assert 8.9 < x < 9.1 and 49.6 < y < 49.7                          # lon/lat, not UTM metres
    w = e["views"]["worst"]
    assert w["camera"]["source"] == "largest piece of the symmetric difference"
    keys = {f["side"]: f["properties"]["key"] for f in w["features_to_identify"]}
    assert keys == {"legacy": "k4", "new": "k4"}                           # the moved feature


def test_deterministic_and_legacy_untouched(synth_run, tmp_path):
    legacy = synth_run.parents[2] / "legacy"
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()}
    doc1 = views_doc(synth_run)
    cp = run_cli("views.py", "run", "--run", synth_run)
    assert cp.returncode == 0, cp.stderr
    doc2 = views_doc(synth_run)
    assert doc1 == doc2                                                    # same bytes, same shas
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in legacy.rglob("*") if p.is_file()}
    assert after == before and not any(n.endswith(".aux.xml") for n in after)
    cp = run_cli("views.py", "run", "--run", synth_run, "--dataset", "ds.a")   # partial rerun keeps the others
    assert cp.returncode == 0 and views_doc(synth_run) == doc1


def test_url_printing(synth_run):
    cp = run_cli("views.py", "url", "--run", synth_run, "--dataset", "ds.b", "--view", "worst", "--port", "8765")
    assert cp.returncode == 0
    ve = entry(synth_run, "ds.b")["views"]["worst"]
    want = ("http://127.0.0.1:8765/?url=" + urllib.parse.quote("http://127.0.0.1:8765" + ve["url_path"], safe="")
            + "&panels=collapsed")
    assert cp.stdout.strip() == want
    cp = run_cli("views.py", "url", "--run", synth_run, "--dataset", "ds.b", "--cell", "worst", "--port", "8765")
    assert cp.returncode == 0 and "cell-worst.geolibre.json" in urllib.parse.unquote(cp.stdout)
    cp = run_cli("views.py", "url", "--run", synth_run, "--dataset", "ds.b", "--view", "worst")
    assert cp.returncode == 2 and "not running" in cp.stderr               # no server and no --port
    cp = run_cli("views.py", "url", "--run", synth_run, "--dataset", "ds.v", "--view", "edges", "--port", "1")
    assert cp.returncode == 2


def test_after_seal_views_go_to_amend(tmp_path):
    run = synth_map.build(tmp_path / "proj")
    (run / "verify" / "evidence.json").write_text("{}\n")
    for p in [run / "verify", *run.joinpath("verify").rglob("*")]:
        p.chmod(stat.S_IMODE(p.stat().st_mode) & ~0o222)
    run.chmod(stat.S_IMODE(run.stat().st_mode) & ~0o222)
    try:
        cp = run_cli("views.py", "run", "--run", run, "--dataset", "ds.b")
        assert cp.returncode == 0, cp.stderr
        assert not (run / "verify" / "views").exists()
        doc = json.loads((run / "amend" / "views" / "views.json").read_text())
        assert doc["sealed_run"] is True and doc["root"] == "amend/views"
        assert doc["datasets"][0]["views"]["worst"]["url_path"].startswith(f"/data/runs/{run.name}/amend/views/")
        assert not os.access(run, os.W_OK)                                 # run dir left read-only
    finally:
        for p in [run, *run.rglob("*")]:
            if p.is_dir() or p.is_file():
                p.chmod(stat.S_IMODE(p.stat().st_mode) | 0o200)
        shutil.rmtree(tmp_path / "proj")


def test_check_script(synth_run, tmp_path):
    cp = run_cli("check.py", "--run", synth_run, "--json")
    assert cp.returncode == 0, cp.stdout + cp.stderr
    res = json.loads(cp.stdout)
    assert res["ok"] and res["projects"] == 9 and res["layer_files"] == 10 and res["problems"] == []
    assert not (synth_run.parents[1] / ".axis" / "map" / "serve.pid").exists()     # it stopped its server
    # a project edited after views.json was written is caught
    p = synth_run / entry(synth_run, "ds.b")["views"]["overview"]["project"]
    keep = p.read_bytes()
    try:
        p.write_bytes(keep.replace(b'"basemapStyleUrl": ""', b'"basemapStyleUrl": "https://example.org/s.json"'))
        cp = run_cli("check.py", "--run", synth_run)
        assert cp.returncode == 2 and "project_sha256" in cp.stdout and "basemap is not off" in cp.stdout
    finally:
        p.write_bytes(keep)


def test_projects_carry_the_caption_label(synth_run):
    """Every view project names what it shows (map review C4): title = dataset · view, each layer's colour scale."""
    ve = entry(synth_run, "ds.b")["views"]
    pr = json.loads((synth_run / ve["worst"]["project"]).read_text())
    lb = pr["metadata"]["axis-migrate"]["label"]
    assert lb["title"] == "ds.b · worst"
    assert [x["id"] for x in lb["layers"]] == [lay["id"] for lay in pr["layers"]]
    by = {x["name"]: x["scale"] for x in lb["layers"]}
    lo, hi = pr["layers"][0]["metadata"]["rasterState"]["rescale"][0]
    assert by["legacy"] == by["new"] == f"viridis p2/p98 {lo:.4g}–{hi:.4g}"
    assert by["diff"].startswith("±") and "orange new larger" in by["diff"]
    assert by["validity"] == "blue legacy only · orange new only · green both"
    vec = json.loads((synth_run / entry(synth_run, "ds.v")["views"]["overview"]["project"]).read_text())
    assert [(x["name"], x["scale"]) for x in vec["metadata"]["axis-migrate"]["label"]["layers"]] == \
        [("legacy", "blue outline"), ("new", "orange outline")]


def _chrome() -> bool:
    return any(shutil.which(c) for c in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"))


@pytest.mark.skipif(not _chrome(), reason="no Chrome/Chromium for the headless render check")
def test_page_caption_names_view_layers_and_follows_toggles(synth_run):
    """The override draws the caption in the canvas corner from the project label and updates it when a layer is
    toggled in GeoLibre's layer panel."""
    mig = synth_run.parents[1]
    cp = run_cli("start.py", mig)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    try:
        port = json.loads((mig / ".axis" / "map" / "server.json").read_text())["port"]
        ov = entry(synth_run, "ds.b")["views"]["overview"]["url_path"]
        toggle = ('(async function(){var w=function(ms){return new Promise(function(r){setTimeout(r,ms)})};'
                  'var b=[].slice.call(document.querySelectorAll("button,[role=tab],div")).filter(function(e){'
                  'return e.textContent.trim()==="Layers"})[0]; if(b) b.click(); await w(800);'
                  'var n=document.querySelector("[aria-label=visible-new],[title=visible-new]"); if(!n) return null;'
                  'n.click(); await w(2000); return window.__AXIS_MAP__.caption;})()')
        out = map_py("import render_check as R, json, sys, time, views\n"
                     f"u = views.page_url({port}, {ov!r})\n"
                     "ch = R.Chrome()\n"
                     "try:\n"
                     " ch.cmd('Page.enable'); ch.cmd('Runtime.enable'); r = R.check_page(ch, u)\n"
                     " box = ch.js(\"(function(){var c=document.getElementById('axis-map-caption');"
                     "var r=c.getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height,"
                     "text:c.textContent};})()\")\n"
                     f" after = ch.js({toggle!r})\n"
                     "finally:\n ch.close()\n"
                     "print(json.dumps({'ready': r['ready'], 'centre': r['status']['centre'], "
                     "'caption': r['status']['caption'], 'box': box, 'after': after}))")
        assert out["ready"]
        cap = out["caption"]
        assert cap.startswith("ds.b · overview · legacy + new: viridis p2/p98 ") and "display only" in cap, cap
        assert "diff" not in cap and "validity" not in cap                     # hidden in this view
        c, b = out["centre"], out["box"]
        assert c["x"] - c["width"] / 2 <= b["x"] <= c["x"] - c["width"] / 2 + 20                  # canvas corner
        assert c["y"] - c["height"] / 2 <= b["y"] <= c["y"] - c["height"] / 2 + 20 and b["h"] > 30
        assert out["after"] and out["after"].startswith("ds.b · overview · legacy: viridis"), out["after"]
    finally:
        run_cli("stop.py", mig)
