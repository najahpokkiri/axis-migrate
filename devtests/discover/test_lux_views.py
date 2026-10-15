"""Discover's GeoLibre inputs views on the lux fixture (dev-only; encodes lux facts).

    ~/axis-migrate-dev/axis-migrate/scripts/axpy -m pytest ~/axis-migrate-dev/devtests/discover/test_lux_views.py -q

A tmp copy of the fixture is scanned; `views.py inputs` builds discover/views/ for the lux inputs and legacy rasters;
every project is reloaded and validated through geolibre (views.validate_project: geolibre.authoring.load_project,
describe_project, layer_summary), check.py --inputs serves and checks them headlessly, the cells to identify equal
the source rasters, the legacy copy is untouched, and a view finding is accepted by scan.py annotate while a misread
cell is refused. The pane itself is not driven here.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_lux_util import AXPY, PLUGIN, SCAN, copy_fixture, run, tree_state  # noqa: E402

MAP = PLUGIN / "skills" / "map"
PY3 = shutil.which("python3")
INPUTS = {"ds.lux_ppp_2020": "raster"}           # at least these; the vectors are checked by family below


def sh(args, ok=(0,), timeout=1800):
    cp = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    assert cp.returncode in ok, (args, cp.stdout[-3000:], cp.stderr[-3000:])
    return cp


@pytest.fixture(scope="module")
def lux(tmp_path_factory):
    root = tmp_path_factory.mktemp("luxviews")
    leg = copy_fixture(root)
    mig = root / "migration"
    run(SCAN, leg, "--out", mig)
    before = tree_state(leg)
    sh([PY3, MAP / "views.py", "inputs", mig])
    return {"legacy": leg, "mig": mig, "before": before,
            "doc": json.loads((mig / "discover" / "views.json").read_text()),
            "inv": json.loads((mig / "inventory.json").read_text())}


def test_inputs_and_legacy_rasters_have_views(lux):
    d, inv = lux["doc"], lux["inv"]
    want = {x["id"] for x in inv["datasets"] if x["exists"] and (
        (x["role"] == "input" and x["kind"] in ("raster", "vector")) or
        (x["role"] in ("intermediate", "final") and x["kind"] == "raster"))}
    got = {e["dataset"]: e for e in d["datasets"]}
    assert set(got) == want, (sorted(want - set(got)), d["skipped"])
    for ds, fam in INPUTS.items():
        assert got[ds]["family"] == fam and got[ds]["role"] == "input"
    for e in got.values():
        if e["family"] == "raster":
            ids = [c["id"] for c in e["cells_to_identify"]]
            assert ids[0] == "max" and "interior" in ids and ({"aoi-edge", "edge"} & set(ids)), (e["dataset"], ids)
        else:
            assert e["features_to_identify"] and e["features_to_identify"][0]["properties"], e["dataset"]
    assert "inputs-overview" in d["projects"]
    grids = [k for k in d["projects"] if k.startswith("grid-")]
    assert len(grids) == len(inv["grids"]) >= 1
    for k in grids:
        assert len(d["projects"][k]["bookmarks"]) == 5


def test_projects_validate_through_geolibre(lux):
    py = sh([PY3, "-I", MAP / "mapenv.py"]).stdout.strip()
    code = ("import sys, json, pathlib; sys.path.insert(0, %r); import views; r = pathlib.Path(sys.argv[1]); "
            "print(json.dumps({str(p): views.validate_project(p) for p in sorted(r.rglob('*.geolibre.json'))}))"
            % str(MAP))
    res = json.loads(sh([py, "-c", code, lux["mig"] / "discover" / "views"]).stdout)
    n = len(lux["doc"]["projects"]) + sum(len(e["cells_to_identify"]) + len(e["features_to_identify"])
                                          for e in lux["doc"]["datasets"])
    assert len(res) == n and all(v == [] for v in res.values()), {k: v for k, v in res.items() if v}


def test_cells_equal_the_sources(lux):
    import rasterio
    from rasterio.windows import Window
    for e in lux["doc"]["datasets"]:
        if e["family"] != "raster":
            continue
        with rasterio.open(lux["legacy"] / e["source"]["path"]) as s:
            for c in e["cells_to_identify"]:
                w = Window(c["col"], c["row"], 1, 1)
                v = s.read(1, window=w)[0, 0]
                valid = bool(s.read_masks(1, window=w)[0, 0]) and v == v
                assert c["value"] == (v.item() if valid else None), (e["dataset"], c["id"])


def test_check_headless_and_legacy_untouched(lux):
    cp = sh([PY3, MAP / "check.py", "--inputs", lux["mig"], "--json"])
    res = json.loads(cp.stdout)
    assert res["ok"] and res["problems"] == []
    assert tree_state(lux["legacy"]) == lux["before"]


def test_view_finding_accepted_and_misread_refused(lux, tmp_path):
    d = lux["doc"]
    ov = d["projects"]["inputs-overview"]
    e = next(x for x in d["datasets"] if x["dataset"] == "ds.lux_ppp_2020")
    c = e["cells_to_identify"][0]
    ref = lambda x: {"project": x["project"], "project_sha256": x["project_sha256"]}  # noqa: E731
    f = {"view": ref(ov), "views_seen": [ref(ov), ref(c)], "engine": "maplibre-gl-raster",
         "cells_read": [{"dataset": e["dataset"], "cell": c["id"], "value": float(f"{c['value']:.3g}")}],
         "about": [e["dataset"]], "observation": "largest population cell sits inside the country outline",
         "severity": "info", "metric": {"name": "max_value", "value": c["value"], "computed_by": "views.json"}}
    p = tmp_path / "f.json"
    p.write_text(json.dumps(f))
    sh([AXPY, SCAN, "annotate", lux["mig"], "map-finding", "--json", p])
    f["cells_read"][0]["value"] = c["value"] * 2 + 1
    p.write_text(json.dumps(f))
    cp = sh([AXPY, SCAN, "annotate", lux["mig"], "map-finding", "--json", p], ok=(2,))
    assert "wrong cell or wrong layer" in cp.stderr
    cp = sh([AXPY, PLUGIN / "skills" / "discover" / "map.py", "findings", lux["mig"], "--json"])
    assert all(r["counts"] for r in json.loads(cp.stdout)["findings"])
