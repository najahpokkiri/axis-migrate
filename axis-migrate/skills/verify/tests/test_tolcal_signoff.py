"""tolcal.py (deterministic mutants, proposed records never signed) and signoff.py (SIGN-OFF.md renderer)."""
import json
import subprocess

import numpy as np
import pytest
import yaml

import axis_common as ac
import compare
from conftest import (PLUGIN, VERIFY, kd_block, legacy_raster_array, spec, table_rows, tol_record, vector_frame,
                      write_csv, write_raster, write_vector)
import tolcal


def _cli(script, *args):
    return subprocess.run([str(PLUGIN / "bin" / "axpy"), str(VERIFY / script), *map(str, args)],
                          capture_output=True, text=True)


def test_raster_mutants_fail_with_named_codes(tmp_path):
    leg = write_raster(tmp_path / "l.tif", legacy_raster_array())
    out = tolcal.mutate(str(leg), "raster", str(tmp_path / "mut"), spec=spec("raster", compare={"measure": "extensive"}))
    by = {m["id"]: m for m in out["mutants"]}
    assert "RASTER_TRANSFORM_MISMATCH" in by["shift_k1"]["reason_codes"]
    assert by["shift_k1"]["metrics"]["raster.grid.transform"]["value"] == pytest.approx(0.5)
    assert "RASTER_VALUE_MISMATCH" in by["bilinear_roundtrip"]["reason_codes"]
    assert by["scale_k-3"]["verdict"] == "FAIL"
    assert "NUMERIC_SCALE_FACTOR" in by["scale_k-3"]["diagnostic_codes"]
    for m in out["mutants"]:
        assert m["verdict"] != "PASS_EXACT"
    # deterministic
    out2 = tolcal.mutate(str(leg), "raster", str(tmp_path / "mut2"), spec=spec("raster", compare={"measure": "extensive"}))
    assert [m["sha256"] for m in out["mutants"]] == [m["sha256"] for m in out2["mutants"]]


def test_all_touched_toggle_with_recipe(tmp_path):
    g = vector_frame()
    vp = write_vector(tmp_path / "polys.gpkg", g, layer="polys")
    from rasterio.features import rasterize
    from conftest import transform, N
    burned = rasterize(((geom, 1) for geom in g.geometry), out_shape=(N, N), transform=transform(), fill=0,
                       all_touched=True, dtype="uint8")
    leg = write_raster(tmp_path / "mask.tif", burned, nodata=0, dtype="uint8")
    recipe = {"vector": str(vp), "layer": "polys", "burn": 1, "all_touched": True, "fill": 0}
    out = tolcal.mutate(str(leg), "raster", str(tmp_path / "mut"), recipe=recipe, spec=spec("raster"),
                        only=["all_touched"])
    m = out["mutants"][0]
    assert m["id"] == "all_touched_toggle" and "NODATA_MASK_MISMATCH" in m["reason_codes"]
    assert m["metrics"]["raster.mask"]["value"] > 0


def test_table_and_vector_mutants(tmp_path):
    leg = write_csv(tmp_path / "t.csv", table_rows())
    out = tolcal.mutate(str(leg), "table", str(tmp_path / "mt"), spec=spec("table", compare={"key": ["id"]}),
                        key=["id"])
    by = {m["id"]: m for m in out["mutants"]}
    assert "KEY_SET_MISSING" in by["unstrip_key"]["reason_codes"]
    assert "ROW_COUNT_MISMATCH" in by["drop_rows"]["reason_codes"]
    assert "ATTRIBUTE_VALUE_MISMATCH" in by["scale_k3"]["reason_codes"]
    vl = write_vector(tmp_path / "v.gpkg", vector_frame(), layer="v")
    out = tolcal.mutate(str(vl), "vector", str(tmp_path / "mv"), spec=spec("vector", compare={"key": ["fid_key"]}),
                        key=["fid_key"])
    assert all(m["verdict"] == "FAIL" for m in out["mutants"])


def test_propose_is_never_signed():
    rec = tolcal.propose("ds.x", "raster.values", correct_spread=5.9e-8, nearest_wrong=1.8e-3, metric="abs+rel",
                         spread_evidence="controls/a.json", wrong_evidence="mutants/b.json", form="rtol")
    assert rec["agreed_by"] is None and rec["agreed_at"] is None
    assert rec["correct_spread"]["value"] < rec["rtol"] < rec["nearest_wrong"]["value"]
    assert rec["id"] == "tol.x.raster.values.v1"
    with pytest.raises(ValueError):
        tolcal.propose("ds.x", "raster.values", correct_spread=1e-3, nearest_wrong=1e-3)
    # the comparator treats it as unsigned: NEEDS_DECISION at best, never PASS
    unknown = set(rec) - compare.RECORD_KEYS
    assert not unknown


def test_propose_cli(tmp_path):
    p = _cli("tolcal.py", "propose", "--dataset", "ds.x", "--check", "raster.values", "--correct-spread", "0",
             "--nearest-wrong", "0.0018", "--form", "rtol")
    assert p.returncode == 0, p.stderr
    rec = yaml.safe_load(p.stdout)
    assert rec["agreed_by"] is None and 0 < rec["rtol"] < 0.0018


def _signoff_project(tmp_path):
    mig = tmp_path / "migration"
    mig.mkdir()
    unsigned = tol_record("ds.a", "raster.values", rtol=1e-6, spread=5.9e-8, wrong=1.8e-3, agreed_by=None,
                          agreed_at=None)
    signed = tol_record("ds.b", "raster.values", rtol=1e-9, spread=1e-12, wrong=1e-3)
    (mig / "tolerances.yaml").write_text(yaml.safe_dump({"version": 1, "records": [unsigned, signed]},
                                                        sort_keys=False))
    md = kd_block("KD-001", "normalisation", ["ds.a"], ["raster.dtype"],
                  {"dtype_widening": {"from": "float32", "to": "float64"}}, status="proposed", owner="agent")
    md += kd_block("KD-002", "accepted_change", ["ds.c"], ["raster.values"], {"expected": "new = legacy * 1000"},
                   status="proposed", owner="agent", bound={"metric": "max_rel", "max": 1e-6})
    md += kd_block("KD-003", "normalisation", ["ds.b"], ["raster.dtype"], {"dtype_widening": {"from": "a", "to": "b"}})
    (mig / "known-differences.md").write_text(md)
    res = mig / "runs" / "r20261016T100000Z-deadbeef" / "verify" / "compare"
    res.mkdir(parents=True)
    ac.dump_json(res / "ds.a.json", {"dataset_id": "ds.a", "verdict": "NEEDS_DECISION",
                                     "checks": [{"id": "raster.values", "state": "NEEDS_DECISION",
                                                 "reason_codes": ["TOLERANCE_UNSIGNED"],
                                                 "proposed": {"record_id": unsigned["id"]}}]})
    return mig, unsigned


def test_signoff_renders_unsigned_only_and_never_signs(tmp_path):
    import signoff
    mig, unsigned = _signoff_project(tmp_path)
    before = {p.name: p.read_bytes() for p in mig.iterdir() if p.is_file()}
    signoff.render(mig)
    text = (mig / "SIGN-OFF.md").read_text()
    assert unsigned["id"] in text and "tol.b.raster.values.v1" not in text
    assert "5.9e-08" in text and "0.0018" in text
    assert "KD-001" in text and "KD-002" in text and "KD-003" not in text
    assert "r20261016T100000Z-deadbeef" in text
    lines = (mig / "tolerances.yaml").read_text().splitlines()
    import re
    for m in re.finditer(r"tolerances\.yaml:(\d+)", text):
        assert "agreed_" in lines[int(m.group(1)) - 1] or "decision_ref" in lines[int(m.group(1)) - 1]
    assert re.search(r"known-differences\.md:\d+", text)
    after = {p.name: p.read_bytes() for p in mig.iterdir() if p.is_file() and p.name != "SIGN-OFF.md"}
    assert before == after                                   # nothing signed, nothing edited
    p = _cli("signoff.py", mig)
    assert p.returncode == 0, p.stderr


def test_signoff_lists_proposals_file_and_how_to_sign(tmp_path):
    import signoff
    mig, _ = _signoff_project(tmp_path)
    prop = tol_record("ds.x", "vector.hausdorff_m", value=0.5, spread=0.12, wrong=13.0, agreed_by=None,
                      agreed_at=None)
    stray = tol_record("ds.y", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3)       # signed: wrong file
    (mig / "tolerances.proposed.yaml").write_text(yaml.safe_dump({"version": 1, "records": [prop, stray]},
                                                                 sort_keys=False))
    before = (mig / "tolerances.proposed.yaml").read_bytes(), (mig / "tolerances.yaml").read_bytes()
    text = signoff.render(mig).read_text()
    assert prop["id"] in text and "0.12" in text and "13.0" in text
    assert "copy the record" in text and "migration/tolerances.yaml" in text and "new run" in text
    assert "agreed_by: human:<your name>" in text and "decision_ref: decisions.md#D-nnn" in text
    assert f"{stray['id']} — IGNORED" in text
    assert ((mig / "tolerances.proposed.yaml").read_bytes(), (mig / "tolerances.yaml").read_bytes()) == before


def test_signoff_check_validates_records(tmp_path):
    import signoff
    mig, _ = _signoff_project(tmp_path)
    assert signoff.check_records(mig) == []
    skill = (VERIFY / "SKILL.md").read_text()
    template = skill.split("````markdown\n")[1].split("````")[0]
    template = "\n".join(line[3:] if line.startswith("   ") else line for line in template.splitlines()) + "\n"
    (mig / "known-differences.md").write_text((mig / "known-differences.md").read_text().replace("KD-003", "KD-009")
                                              + template)
    assert signoff.check_records(mig) == [], signoff.check_records(mig)       # the SKILL template is valid as written
    bad = ("## KD-010 broken heading\n\n```yaml\nid: KD-011\nkind: accepted_change\ndatasets: ds.a\nchecks: [raster.values]\n"
           "rule: {expected: 'new = legacy * 1000'}\nowner: agent\nstatus: accepted\n```\n")
    (mig / "known-differences.md").write_text((mig / "known-differences.md").read_text() + bad)
    (mig / "tolerances.proposed.yaml").write_text(yaml.safe_dump({"version": 1, "records": [
        tol_record("ds.a", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3)]}))
    probs = "\n".join(signoff.check_records(mig))
    for needle in ("heading must be", "missing keys", "only a human accepts", "needs a measured bound",
                   "signed record in tolerances.proposed.yaml"):
        assert needle in probs, (needle, probs)
    p = _cli("signoff.py", mig, "--check")
    assert p.returncode == 2
