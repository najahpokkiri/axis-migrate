"""Alternative implementations and multi-writers (B1, B2) and raster aliases/sampling (m1). Synthetic data only."""
from __future__ import annotations

import json

import numpy as np

from scan_synth import LINEAGE, build_project, run, scan, write_raster, zones_gdf

READ_WRITE = '''import geopandas as gpd
z = gpd.read_file("../data/zones.gpkg")
z.to_file("../tmp/zones_out.gpkg")
'''
LOOP = '''import geopandas as gpd
for name in ["a", "b"]:
    z = gpd.read_file("../data/zones.gpkg")
    z["name2"] = name
    z.to_file("../tmp/zones_out.gpkg")
z.to_file("../tmp/zones_loop_extra.gpkg")
'''


def build_alternatives(root):
    import pyogrio
    leg = root / "legacy_alt"
    (leg / "data").mkdir(parents=True)
    (leg / "scripts").mkdir()
    pyogrio.write_dataframe(zones_gdf(), leg / "data" / "zones.gpkg", layer="zones", driver="GPKG")
    (leg / "scripts" / "zone_export.py").write_text(READ_WRITE)
    (leg / "scripts" / "zone_export_loop.py").write_text(LOOP)
    return leg


def lineage(mig, check=True):
    r = run(LINEAGE, mig, "--json", check=check)
    return r, json.loads(r.stdout)


def test_live_scripts_with_shared_writes_pair_as_alternatives(tmp_path):
    leg = build_alternatives(tmp_path)
    mig = tmp_path / "migration"
    inv = scan(leg, mig)
    code = {c["path"]: c for c in inv["code"]}
    canon, alt = code["scripts/zone_export.py"], code["scripts/zone_export_loop.py"]
    assert canon["status"] == "live"
    assert alt["status"] == "superseded" and alt["twin_of"] == canon["id"]
    assert alt["twin_evidence"]["kind"] == "shared_writes" and alt["twin_evidence"]["canonical_by"] == "stem_prefix"
    assert alt["twin_evidence"]["value"] == ["ds.zones_out"]
    t = next(t for t in inv["twins"] if t["a"] == alt["id"])
    assert t["kind"] == "alternative_impl" and t["b"] == canon["id"]
    r, summ = lineage(mig)
    assert r.returncode == 0 and not summ["errors"], summ
    g = json.loads((mig / "graph.json").read_text())
    st = {s["legacy"]["source"]: s for s in g["steps"]}
    a = st["scripts/zone_export_loop.py"]
    assert a["scope"] == "drop" and a["scope_reason"] == "alternative_impl"
    assert a["twin_of"] == st["scripts/zone_export.py"]["id"]
    fact = next(f for f in g["scope_facts"] if f["node"] == a["id"])
    assert "alternative implementation" in fact["summary"]
    # the dropped twin still writes the shared file (knowledge) but is not its producer
    out = next(d for d in g["datasets"] if d["id"] == "ds.zones_out")
    assert out["produced_by"]["step"] == st["scripts/zone_export.py"]["id"]


def test_in_scope_multi_writer_is_a_question_not_invalid(tmp_path):
    """Two in-scope writers of one file (here forced by undoing the pairing): versions + one question, exit 0."""
    leg = build_alternatives(tmp_path)
    mig = tmp_path / "migration"
    inv = scan(leg, mig)
    for c in inv["code"]:
        if c["status"] == "superseded":
            c["status"], c["twin_of"], c["status_evidence"] = "live", None, []
    for s in inv["step_candidates"]:
        s["status"] = "live"
    (mig / "inventory.json").write_text(json.dumps(inv))
    r, summ = lineage(mig)
    assert r.returncode == 0 and not summ["errors"], summ
    g = json.loads((mig / "graph.json").read_text())
    q = [q for q in g["questions"] if q["header"] == "Written by several steps"]
    assert len(q) == 1 and q[0]["node"] == "ds.zones_out"
    ver = [d for d in g["datasets"] if d["id"].startswith("ds.zones_out@")]
    assert len(ver) == 1 and ver[0]["version_of"] == "ds.zones_out" and not ver[0]["legacy"]["exists"]
    writers = {e["from"] for e in g["edges"] if e["to"] == "ds.zones_out" and e["kind"] == "writes"}
    assert len(writers) == 1


def test_validate_draft_counts_in_scope_producers_only():
    from lineage import validate_draft
    ev = [{"state": "directly_observed", "ref": "a.py:1", "summary": "x"}]
    g = {"steps": [{"id": "s01_a", "scope": "migrate"}, {"id": "s02_b", "scope": "drop"}],
         "datasets": [{"id": "ds.x", "role": "final", "legacy": {"path": "x.gpkg"}}],
         "edges": [{"id": "e.s01_a>ds.x", "kind": "writes", "from": "s01_a", "to": "ds.x", "evidence": ev},
                   {"id": "e.s02_b>ds.x", "kind": "writes", "from": "s02_b", "to": "ds.x", "evidence": ev}],
         "questions": []}
    assert validate_draft(g) == []
    g["steps"][1]["scope"] = "migrate"
    assert any("several steps" in e for e in validate_draft(g))


def test_graph_init_accepts_dropped_writers_and_prefers_the_in_scope_producer(tmp_path):
    import graph
    from graph_synth import LEGACY_FILES, draft
    for rel, data in LEGACY_FILES.items():
        p = tmp_path / "legacy" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    d = draft()
    d["edges"].insert(0, {"from": "s03b_alt", "to": "ds.c_stats", "kind": "writes",
                          "evidence": [{"state": "directly_observed", "ref": "scripts/03b_alt.py:16", "summary": "",
                                        "by": "scan", "rev": 1}]})
    (tmp_path / "migration").mkdir()
    graph.init(tmp_path / "migration", d)
    g = json.loads((tmp_path / "migration" / "graph.json").read_text())
    c = next(x for x in g["datasets"] if x["id"] == "ds.c_stats")
    assert c["produced_by"]["step"] == "s03_stats"
    next(s for s in g["steps"] if s["id"] == "s03b_alt")["scope"] = "migrate"
    assert [x for x in graph.validate_graph(g) if "several steps" in x]


VRT = '''<VRTDataset rasterXSize="{w}" rasterYSize="{h}">
  <SRS>EPSG:4326</SRS>
  <GeoTransform>{gt}</GeoTransform>
  <VRTRasterBand dataType="Float32" band="1">
    <NoDataValue>-9999</NoDataValue>
    <SimpleSource>
      <SourceFilename relativeToVRT="1">{src}</SourceFilename>
      <SourceBand>1</SourceBand>
      <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
      <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}" />
    </SimpleSource>
  </VRTRasterBand>
</VRTDataset>
'''


def test_vrt_of_a_sibling_is_an_alias_with_one_profile_and_family(tmp_path):
    import rasterio
    leg = tmp_path / "legacy_vrt"
    write_raster(leg / "in" / "dem.tif", np.arange(600).reshape(20, 30))
    with rasterio.open(leg / "in" / "dem.tif") as s:
        t = s.transform
    gt = ", ".join(repr(v) for v in (t.c, t.a, t.b, t.f, t.d, t.e))
    (leg / "in" / "dem.vrt").write_text(VRT.format(w=30, h=20, gt=gt, src="dem.tif"))
    inv = scan(leg, tmp_path / "migration")
    vrt = next(d for d in inv["datasets"] if d["legacy_path"] == "in/dem.vrt")
    tif = next(d for d in inv["datasets"] if d["legacy_path"] == "in/dem.tif")
    assert vrt["alias_of"] == tif["id"]
    prof = json.loads((tmp_path / "migration" / vrt["profile_ref"]).read_text())
    assert prof["alias_of"] == "in/dem.tif" and prof["computed_from"] == "in/dem.tif" and prof["driver"] == "VRT"
    assert prof["computed"]["valid"] > 0 and "nodata" in prof["alias_differs"]
    fams = [g for g in inv["grids"] if any(m["dataset"] in (vrt["id"], tif["id"]) for m in g["members"])]
    assert len(fams) == 1 and {m["dataset"] for m in fams[0]["members"]} == {vrt["id"], tif["id"]}


def test_large_rasters_get_sampled_statistics(tmp_path, monkeypatch):
    import scan as S
    p = tmp_path / "big.tif"
    write_raster(p, np.ones((20, 30)))
    full = S.profile_raster(p)
    assert full["stats_mode"] == "full" and "stats_sample" not in full
    monkeypatch.setattr(S, "STATS_MAX_CELLS", 100)
    samp = S.profile_raster(p)
    assert samp["stats_mode"] == "strided" and samp["stats_sample"]["cells_total"] == 600
    assert samp["computed"]["cells"] <= full["computed"]["cells"]


def test_existing_project_unchanged_by_alternative_pass(tmp_path):
    inv = scan(build_project(tmp_path), tmp_path / "migration")
    assert not [t for t in inv["twins"] if t["kind"] == "alternative_impl"]
