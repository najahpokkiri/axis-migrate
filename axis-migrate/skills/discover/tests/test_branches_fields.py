"""Field reads are checked against the layer parameter they belong to (qgis_algs.json `parent`), and conditionals
that change what an op does become branches[] records (10 §10). Synthetic data only."""
from __future__ import annotations

import json

import pytest

from scan_synth import LINEAGE, run, scan, zones_gdf

SCRIPT = '''import processing

BASE = "/old/proj"
TMP = BASE + "/tmp"
NEAR, FAR = 50, 300

outs = []
for kind in ["a", "b"]:
    if kind == "a":
        d = FAR
    else:
        d = NEAR
    out = TMP + "/buf_%s.gpkg" % kind
    processing.run("native:buffer", {'INPUT': BASE + "/in/pts.gpkg", 'DISTANCE': d, 'OUTPUT': out})
    outs.append(out)

if not BASE:
    print("never")

processing.run("native:intersection", {
    'INPUT': TMP + "/buf_a.gpkg",
    'OVERLAY': BASE + "/in/zones.gpkg",
    'INPUT_FIELDS': ['kind'],
    'OVERLAY_FIELDS': ['name', 'code'],
    'OUTPUT': TMP + "/x.gpkg"})
'''


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    import pyogrio
    root = tmp_path_factory.mktemp("br")
    leg = root / "legacy"
    for d in ("in", "tmp", "scripts"):
        (leg / d).mkdir(parents=True)
    z = zones_gdf()
    pyogrio.write_dataframe(z, leg / "in" / "zones.gpkg", layer="zones", driver="GPKG")
    pts = z[["geometry"]].copy()
    pts["kind"] = ["a", "b", "a"]
    pts["geometry"] = pts.geometry.centroid
    pyogrio.write_dataframe(pts, leg / "in" / "pts.gpkg", layer="pts", driver="GPKG")
    for n in ("buf_a", "buf_b"):
        b = pts.copy()
        b["geometry"] = b.geometry.buffer(0.01)
        pyogrio.write_dataframe(b, leg / "tmp" / f"{n}.gpkg", layer=n, driver="GPKG")
    x = b.copy()
    x["name"], x["code"] = z["name"].values, z["code"].values
    pyogrio.write_dataframe(x, leg / "tmp" / "x.gpkg", layer="x", driver="GPKG")
    (leg / "scripts" / "01_buffers.py").write_text(SCRIPT)
    mig = root / "migration"
    inv = scan(leg, mig)
    run(LINEAGE, mig, "--draft-only")
    return {"inv": inv, "draft": json.loads((mig / ".graph" / "draft.json").read_text())}


def test_overlay_fields_checked_against_overlay_layer(proj):
    op = next(o for o in proj["inv"]["ops"] if o["tool"] == "native:intersection")
    got = {(f["dataset"], f["field"], f["of_param"]) for f in op["fields_read"]}
    assert got == {("ds.buf_a", "kind", "INPUT"), ("ds.zones", "name", "OVERLAY"), ("ds.zones", "code", "OVERLAY")}
    ch = proj["draft"]["lineage_checks"]
    assert not ch["failed"]
    assert {("ds.zones", "name"), ("ds.zones", "code")} <= {(c["dataset"], c["field"]) for c in ch["passed"]
                                                           if c["check"] == "c_field_in_schema"}
    assert not [q for q in proj["draft"]["questions"] if q["header"] == "Field not found"]


def test_loop_branch_record(proj):
    br = proj["inv"]["branches"]
    assert [b["id"] for b in br] == ["br.s01.L9"]  # the guard without ops at line 17 is not a branch
    b = br[0]
    assert b["condition"] == 'kind == "a"' and b["ref"] == "scripts/01_buffers.py:9-12" and b["status"] == "unknown"
    assert [(a["arm"], a["values"]) for a in b["arms"]] == [("if", {"d": [300]}), ("else", {"d": [50]})]
    buf = next(o for o in proj["inv"]["ops"] if o["tool"] == "native:buffer")
    assert b["affects_ops"] == [buf["id"]] and b["affects_datasets"] == ["ds.buf_a", "ds.buf_b"]
    assert b["visits"] == [{"loop": [{"var": "kind", "line": 8, "value": "a"}], "taken": "if"},
                           {"loop": [{"var": "kind", "line": 8, "value": "b"}], "taken": "else"}]
    g = proj["draft"]["branches"]
    assert [x["id"] for x in g] == ["br.s01.L9"] and g[0]["evidence"][0]["ref"] == "scripts/01_buffers.py:9-12"
