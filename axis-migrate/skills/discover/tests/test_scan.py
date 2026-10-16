"""scan.py on a synthetic PyQGIS-style project: read-only, completeness, ops, settings, roles, Excel, derived copies,
grids, docs, projects, determinism, annotate. Synthetic data only (CONTRACTS §0)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import pytest

from scan_synth import AXPY, AXPY_CMD, SCAN, build_project, ds, op_by, run, scan, tree_state


# OS runtime files the interpreter or its libraries read on their own (narrow, exact): macOS system version plist
# (stdlib platform / _osx_support via sysconfig, first triggered by importing pandas), mimetypes tables, tz database.
import mimetypes as _mt
import re as _re
OS_RUNTIME = [_re.compile(r"^/System/Library/CoreServices/SystemVersion(Compat)?\.plist$"),
              _re.compile(r"^/usr/share/zoneinfo/[A-Za-z0-9_+\-/]+$")]
OS_RUNTIME_FILES = set(_mt.knownfiles) | {"/etc/localtime"}


def os_runtime(p: str) -> bool:
    return p in OS_RUNTIME_FILES or any(r.match(p) for r in OS_RUNTIME)


@pytest.fixture(scope="module")
def proj(tmp_path_factory):
    root = tmp_path_factory.mktemp("proj")
    leg = build_project(root)
    before = tree_state(leg)
    inv = scan(leg, root / "migration")
    return {"root": root, "legacy": leg, "inv": inv, "before": before}


def test_read_only_and_no_new_files(proj):
    assert tree_state(proj["legacy"]) == proj["before"]
    assert proj["inv"]["scan"]["read_only_verified"] is True
    assert proj["inv"]["scan"]["gdal_pam_enabled"] == "NO"


def test_every_file_listed_once_and_sidecars_attached(proj):
    inv = proj["inv"]
    paths = [f["path"] for f in inv["files"]]
    assert len(paths) == len(set(paths)) == len(proj["before"])
    side = next(f for f in inv["files"] if f["path"] == "in/raw.tif.aux.xml")
    assert side["kind"] == "sidecar" and side["sidecar_of"] == "in/raw.tif"
    assert next(f for f in inv["files"] if f["path"] == "in/raw.tif")["sidecars"] == ["in/raw.tif.aux.xml"]
    assert inv["legacy_root"] == "../legacy"


def test_content_hash_for_gpkg_and_xlsx(proj):
    f = {x["path"]: x for x in proj["inv"]["files"]}
    assert f["in/zones.gpkg"]["content_sha256"] and f["in/rates.xlsx"]["content_sha256"]
    assert f["in/raw.tif"]["content_sha256"] is None


def test_pyqgis_ops_reads_writes_and_lines(proj):
    inv = proj["inv"]
    d = op_by(inv, "s01_clip", "native:dissolve")[0]
    assert d["line"] == 12 and d["reads"][0]["dataset"] == "ds.zones" and d["writes"][0]["dataset"] == "ds.01_outline"
    assert d["writes"][0]["line"] == 15 and d["params"]["OUTPUT"]["chain"] == [9, 6]
    clip = op_by(inv, "s01_clip", "gdal:cliprasterbymasklayer")[0]
    assert {r["dataset"] for r in clip["reads"]} == {"ds.raw", "ds.01_outline"}  # MASK through the ToolOut variable
    assert any(c["text"] == "keep the source grid" for c in clip["comments"])
    ras = op_by(inv, "s02_mask", "gdal:rasterize")[0]
    assert ras["writes"][0]["dataset"] == "ds.02_mask"
    assert {g["dataset"] for g in ras["reads_grid"]} == {"ds.01_clip"}
    assert ras["params"]["EXTENT"]["value"] == {"derived": "extent_of", "dataset": "ds.01_clip", "format": "%.10f"}
    assert ras["memory_reads"] and not ras["reads"]
    mem = [m for m in inv["memory_datasets"] if m["op"].startswith("s02_mask")]
    assert len(mem) == 2 and all(m["consumed_by"] for m in mem)
    assert len([o for o in inv["ops"] if o["id"].startswith("s02_mask.")]) == 4  # 2 algs + layer + rasterize


def test_python_io_ops(proj):
    inv = proj["inv"]
    wb = op_by(inv, "s03_table", "load_workbook")[0]
    assert wb["reads"][0]["dataset"] == "ds.rates"
    assert wb["params"]["rows"]["value"][0]["min_row"] == 5
    csvop = op_by(inv, "s03_table", "open")[0]
    assert csvop["writes"][0] == {"dataset": "ds.result", "line": 16, "mode": "create", "param": "file"}
    lyr = op_by(inv, "s03_table", "QgsVectorLayer")[0]
    assert {f["field"] for f in lyr["fields_read"]} == {"zone"}


def test_settings_qgis_and_arcpy(proj):
    S = {(s["id"]): s for s in proj["inv"]["settings"]}
    clip = [s for s in S.values() if s["name"] == "crop_to_cutline"][0]
    assert clip["value"] is False and clip["set_by"] == "tool_param" and clip["line"] == 22
    ext = [s for s in S.values() if s["name"] == "extent" and s["family"] == "qgis_param"][0]
    assert ext["set_by"] == "derived_from_dataset" and ext["value"]["dataset"] == "ds.01_clip"
    nd = [s for s in S.values() if s["name"] == "nodata" and s["code"] == "code.s02_mask"][0]
    assert nd["set_by"] == "default_unset" and nd["line"] == 24
    at = [s for s in S.values() if s["name"] == "all_touched"][0]
    assert at["value"] is True and at["via"] == "EXTRA -at"
    snap = [s for s in S.values() if s["name"] == "snapRaster"][0]
    assert snap["value"] == {"dataset": "ds.01_clip"} and snap["from_superseded"] is True


def test_code_status_order_twin(proj):
    code = {c["path"]: c for c in proj["inv"]["code"]}
    assert [code[f"scripts/{n}"]["status"] for n in ("01_clip.py", "02_mask.py", "03_table.py")] == ["live"] * 3
    old = code["scripts/03b_mask_arcpy_OLD.py"]
    assert old["status"] == "superseded" and old["twin_of"] == "code.s02_mask"
    kinds = {e["kind"] for e in old["status_evidence"]}
    assert {"name", "comment", "doc_mention"} <= kinds
    assert [code[f"scripts/{n}"]["order"] for n in ("01_clip.py", "02_mask.py", "03_table.py")] == [1, 2, 3]


def test_roles_and_reference_candidates(proj):
    inv = proj["inv"]
    roles = {d["id"]: d["role"] for d in inv["datasets"]}
    assert roles["ds.raw"] == "input" and roles["ds.zones"] == "input" and roles["ds.rates"] == "input"
    assert roles["ds.zones_src"] == "input"  # source of the manual step
    assert roles["ds.01_clip"] == "intermediate" and roles["ds.01_outline"] == "intermediate"
    assert roles["ds.02_mask"] == "final" and roles["ds.result"] == "final"
    assert ds(inv, "ds.extra_display")["unused_kind"] == "display_only"
    assert ds(inv, "ds.old__mask")["exists"] is False and roles["ds.old__mask"] == "unused"
    rc = sorted(d["id"] for d in inv["datasets"] if d["reference_candidate"])
    assert rc == ["ds.01_clip", "ds.01_outline", "ds.02_mask", "ds.result"]
    assert inv["legacy_output_dirs"] == ["out", "tmp"]


def test_legacy_roots_include_commented_and_dead_drives(proj):
    roots = {r["value"]: r for r in proj["inv"]["scan"]["legacy_roots"]}
    assert "/srv/old/demo" in roots
    assert any(v.startswith("E:") for v in roots), roots
    assert any(e["kind"] == "commented_code" for r in roots.values() for e in r["evidence"])


def test_derived_copy_exposes_manual_step(proj):
    inv = proj["inv"]
    dc = inv["derived_copies"][0]
    assert (dc["source"], dc["target"]) == ("ds.zones_src", "ds.zones")
    assert dc["wkb_equal"] == dc["count"] == 3
    assert dc["schema"]["added"] == ["zone"] and dc["schema"]["removed"] == ["code"]
    vm = dc["value_maps"][0]
    assert vm["rule"] == {"kind": "strip_prefix", "value": "Zone "} and vm["rule_count"] == 2
    assert vm["exceptions"] == {"Zone South-East": "SE"}
    m = inv["manual_step_candidates"][0]
    assert m["id"] == "m01_zones" and m["inputs"] == ["ds.zones_src"] and m["outputs"] == ["ds.zones"]
    assert any(i["kind"] == "manual_step" for i in inv["open_items"])


def test_excel_read_twice_header_from_code(proj):
    p = json.loads((proj["root"] / "migration/discover/profiles/ds.rates.json").read_text())
    s = p["sheets"][0]
    assert s["header_row"] == 4 and s["header_state"] == "resolved" and s["data_rows"] == [5, 7]
    assert s["header_corroboration"]["line"] == 10
    assert s["formula_no_cache"] == ["B9"]
    assert s["strip_issues"] == [{"cell": "A7", "value": "SE "}]
    assert s["units_cells"][0]["value"] == "k per item"
    assert any(i["kind"] == "formula_no_cache" for i in proj["inv"]["open_items"])


def test_excel_header_never_inferred_silently(tmp_path):
    leg = build_project(tmp_path)
    (leg / "scripts" / "03_table.py").write_text("import openpyxl\nwb = openpyxl.load_workbook('in/rates.xlsx')\n")
    inv = scan(leg, tmp_path / "migration")
    p = json.loads((tmp_path / "migration/discover/profiles/ds.rates.json").read_text())
    assert p["sheets"][0]["header_state"] == "unresolved" and p["sheets"][0]["header_candidates"][0]["row"] == 4
    assert any(i["kind"] == "header_unresolved" for i in inv["open_items"])


def test_grid_family_and_profiles(proj):
    g = proj["inv"]["grids"]
    assert len(g) == 1 and g[0]["reference"] == "ds.raw" and not g[0]["misaligned"]
    assert all(m["offset_px"] == [0.0, 0.0] for m in g[0]["members"])
    p = json.loads((proj["root"] / "migration/discover/profiles/ds.raw.json").read_text())
    assert p["computed"]["nodata"] == 1 and p["computed"]["valid"] == 599
    assert p["embedded_stats"]["aux_xml"]["approximate"] is True
    m = json.loads((proj["root"] / "migration/discover/profiles/ds.02_mask.json").read_text())
    assert m["unique"] == [1] and m["dtype"] == "uint8"


def test_half_cell_shift_is_misaligned(tmp_path):
    leg = build_project(tmp_path, shift_mask=True)
    inv = scan(leg, tmp_path / "migration")
    g = inv["grids"][0]
    assert g["misaligned"] == [{"dataset": "ds.02_mask", "offset_px": [0.5, 0.0]}]
    assert any(i["kind"] == "misaligned_grid" for i in inv["open_items"])


def test_docs_constants_multiplier_and_cell_size_conflict(proj):
    inv = proj["inv"]
    p = next(x for x in inv["params"] if x["id"] == "p.s02.THRESH")
    assert p["doc_mentions"][0]["agrees"] is True and p["used_at"][0]["param"] == "EXPRESSION"
    m = next(x for x in inv["params"] if x["kind"] == "inline_literal" and x["value"] == 1000)
    assert m["doc_mentions"] and m["doc_mentions"][0]["agrees"]
    conf = [i for i in inv["open_items"] if i["kind"] == "doc_code_conflict"]
    assert conf and conf[0]["severity"] == "info" and "500" in conf[0]["detail"]
    assert [r["path"] for r in inv["docs"][0]["run_order"]] == ["scripts/01_clip.py", "scripts/02_mask.py",
                                                                 "scripts/03_table.py"]


def test_qgis_project_layers_subset_and_redaction(proj):
    p = proj["inv"]["projects"][0]
    assert p["crs"]["authid"] == "EPSG:4326" and p["annotations_ignored"] == 1
    z = next(l for l in p["layers"] if l["name"] == "zones")
    assert z["path"] == "in/zones.gpkg" and z["subset_parsed"] == [{"field": "zone", "op": "=", "value": "North"}]
    db = next(l for l in p["layers"] if l["name"] == "db")
    assert "hunter2" not in json.dumps(p) and db.get("remote")


def test_missing_input_is_blocking(tmp_path):
    leg = build_project(tmp_path)
    (leg / "in" / "raw.tif").unlink()
    (leg / "in" / "raw.tif.aux.xml").unlink()
    inv = scan(leg, tmp_path / "migration")
    mi = [i for i in inv["open_items"] if i["kind"] == "missing_input"]
    assert mi and mi[0]["blocking"] and mi[0]["about"] == ["ds.raw"]
    assert any(c.startswith("s01_clip.") for c in mi[0]["consumers"])


def test_orphan_output_and_secret_excluded(tmp_path):
    leg = build_project(tmp_path)
    shutil.copy(leg / "tmp" / "02_mask.tif", leg / "tmp" / "old_mask_v2.tif")
    (leg / ".env").write_text("TOKEN=secret\n")
    inv = scan(leg, tmp_path / "migration")
    f = next(x for x in inv["files"] if x["path"] == ".env")
    assert f["kind"] == "excluded" and f["excluded_reason"] == "secret_like_name" and "sha256" not in f
    assert any(i["kind"] == "orphan_output" and i["about"] == ["ds.old_mask_v2"] for i in inv["open_items"])


def test_secret_never_opened_and_nothing_outside_root(tmp_path):
    leg = build_project(tmp_path)
    (leg / ".env").write_text("TOKEN=secret\n")
    out = tmp_path / "migration"
    log = tmp_path / "opens.txt"
    wrapper = f"""
import sys, runpy
opened = []
def hook(ev, args):
    if ev == 'open' and isinstance(args[0], str):
        opened.append(args[0])
sys.addaudithook(hook)
sys.argv = ['scan.py', {str(leg)!r}, '--out', {str(out)!r}]
try:
    runpy.run_path({str(SCAN)!r}, run_name='__main__')
except SystemExit:
    pass
open({str(log)!r}, 'w').write('\\n'.join([sys.base_prefix, sys.prefix] + opened))
"""
    r = subprocess.run([*AXPY_CMD, "-c", wrapper], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    base, prefix, *opened = log.read_text().splitlines()
    opened = [p for p in opened if p.startswith("/")]  # '<unknown>' = in-memory code objects
    assert not any(p.endswith(".env") for p in opened)
    allowed = (str(leg), str(out), base, prefix, str(SCAN.parents[2]), str(log))
    import os.path as osp
    venv = osp.realpath(osp.join(osp.dirname(str(AXPY)), "..", "skills", "run", "env", ".venv"))
    bad = [p for p in opened if not osp.realpath(p).startswith(allowed + (venv,)) and not p.startswith(allowed)
           and not os_runtime(p)]
    assert not bad, bad[:10]


def test_determinism_across_runs_and_hash_seeds(tmp_path, proj):
    a = tmp_path / "a"
    b = tmp_path / "b"
    scan(proj["legacy"], a, env={"PYTHONHASHSEED": "0"})
    scan(proj["legacy"], b, env={"PYTHONHASHSEED": "1"})
    ja = json.loads((a / "inventory.json").read_text())
    jb = json.loads((b / "inventory.json").read_text())
    for j in (ja, jb):
        j["scan"].pop("started_at")
    assert json.dumps(ja, sort_keys=True) == json.dumps(jb, sort_keys=True)
    pa = sorted(p.name for p in (a / "discover/profiles").glob("*.json"))
    assert pa == sorted(p.name for p in (b / "discover/profiles").glob("*.json"))
    for n in pa:
        assert (a / "discover/profiles" / n).read_bytes() == (b / "discover/profiles" / n).read_bytes()


def test_annotate_rules_and_survival_across_rescan(tmp_path):
    leg = build_project(tmp_path)
    out = tmp_path / "migration"
    scan(leg, out)
    png = out / "discover" / "x.png"
    png.write_bytes(b"\x89PNG fake")
    f = tmp_path / "f.json"
    f.write_text(json.dumps({"png": "discover/x.png", "observation": "slivers outside", "severity": "info"}))
    r = run(SCAN, "annotate", out, "map-finding", "--json", f, check=False)
    assert r.returncode == 2 and "only raise a question" in r.stderr
    f.write_text(json.dumps({"png": "discover/x.png", "observation": "slivers outside", "severity": "info",
                             "metric": {"name": "area_outside_km2", "value": 0.5, "computed_by": "extent_relations"}}))
    run(SCAN, "annotate", out, "map-finding", "--json", f)
    f.write_text(json.dumps({"png": "discover/x.png", "observation": "odd stripe", "severity": "question",
                             "id": "MF-q"}))
    run(SCAN, "annotate", out, "map-finding", "--json", f)
    f.write_text(json.dumps({"id": "AN-1", "text": "zones look hand edited", "about": ["ds.zones"]}))
    run(SCAN, "annotate", out, "note", "--json", f)
    inv = scan(leg, out)  # rescan keeps agent findings and annotations
    ids = {x["id"] for x in inv["map"]["findings"]}
    assert "MF-q" in ids and len(ids) == 2
    assert inv["annotations"]["AN-1"]["state"] == "ai_inferred"
    q = next(x for x in inv["map"]["findings"] if x["id"] == "MF-q")
    assert q["raises_question"] is True and q["state"] == "ai_inferred"


def test_out_inside_legacy_refused(tmp_path):
    leg = build_project(tmp_path)
    r = run(SCAN, leg, "--out", leg / "migration", check=False)
    assert r.returncode == 2 and not (leg / "migration").exists()
