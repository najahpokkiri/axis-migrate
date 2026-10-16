"""ladder.py: rungs, dispositions, caps, integrity (07 §3.4, §4.3; DESIGN §4.3, §4.6)."""
from __future__ import annotations

import pytest
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import axis_common as ac
import ladder
import seal as sealmod
import synth

HERE = Path(__file__).resolve().parent
# a plain system Python (not the plugin env): proves ladder.py needs the standard library only
SYS_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else (shutil.which("python3") or shutil.which("python")
                                                                        or sys.executable)


def rerun_compare(run_dir: Path, dsid: str, fn):
    """Edit a compare result before sealing and keep it internally consistent (result_id recomputed)."""
    p = run_dir / "verify" / "compare" / f"{dsid}.json"
    doc = ac.load_json(p)
    fn(doc)
    doc["result_id"] = ladder.cmp_content_id(doc)
    ac.dump_json(p, doc)


def test_clean_run_reaches_reference_verified(sealed):
    res = ladder.compute(sealed["migration"])
    assert res["pipeline"]["rung"] == "reference-verified"
    assert res["integrity"] == []
    assert {s: e["disposition"] for s, e in res["steps"].items()} == {"s01_mask": "VERIFIED", "s02_total": "VERIFIED"}
    assert res["status_line"] == (f"axis-migrate status: reference-verified | run {sealed['run_id']} | 4 pass, 0 FAIL, "
                                  "0 NOT_COMPARED, 0 needs review | no report")
    import report
    report.build(sealed["migration"])
    assert ladder.compute(sealed["migration"])["status_line"].endswith("| report migration/report/index.html")


def test_status_line_no_report_without_run(proj):
    assert ladder.compute(proj["migration"])["status_line"] == (
        "axis-migrate status: none | run none | 0 pass, 0 FAIL, 0 NOT_COMPARED, 0 needs review | no report")


def test_ladder_is_stdlib_only_and_fast(sealed):
    code = ("import sys; sys.path.insert(0, %r); import ladder, time; t=time.time(); r=ladder.compute(%r); "
            "bad=[m for m in ('numpy','yaml','jinja2','rasterio') if m in sys.modules]; "
            "print(r['pipeline']['rung'], bad, round(time.time()-t, 3))") % (str(HERE.parent), str(sealed["migration"]))
    out = subprocess.run([SYS_PY, "-I", "-c", code], capture_output=True, text=True, check=True).stdout
    rung, rest = out.split(" ", 1)
    assert rung == "reference-verified"
    assert "[]" in rest
    assert float(rest.rsplit(" ", 1)[1]) < 5.0


def test_cli_status_line(sealed):
    out = subprocess.run([SYS_PY, "-I", str(HERE.parent / "ladder.py"), str(sealed["migration"])],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines()[0].startswith("axis-migrate status: reference-verified | run ")
    js = subprocess.run([SYS_PY, "-I", str(HERE.parent / "ladder.py"), str(sealed["migration"]), "--json"],
                        capture_output=True, text=True, check=True).stdout
    assert json.loads(js)["pipeline"]["rung"] == "reference-verified"


def test_never_emits_rungs_above_target_ready(sealed):
    res = ladder.compute(sealed["migration"])
    text = json.dumps({k: v for k, v in res.items() if k != "pipeline"}) + res["pipeline"]["rung"]
    for g in ac.GREYED_RUNGS:
        assert g not in text
    src = (HERE.parent / "ladder.py").read_text()
    assert '"deployed"' not in src.replace("GREYED", "")


def test_shift_fails_and_caps(proj):
    synth.make_run(proj, 1, shift_new=True)
    res = ladder.compute(proj["migration"])
    assert res["steps"]["s02_total"]["disposition"] == "FAILED"
    assert res["steps"]["s02_total"]["rung"] == "sandbox-validated"
    assert res["pipeline"]["rung"] == "sandbox-validated"
    assert res["datasets"]["ds.b_exposure"]["first_reason"] == "RASTER_TRANSFORM_MISMATCH"


def test_tolerance_edited_after_run_caps_every_step(sealed):  # 07 test 3 (ladder half)
    p = sealed["migration"] / "tolerances.yaml"
    p.write_text(p.read_text().replace("value: 1.0e-6", "value: 1.0e-3"))
    res = ladder.compute(sealed["migration"])
    codes = {x["code"] for x in res["integrity"]}
    assert "TOLERANCE_CHANGED_AFTER_RUN" in codes
    assert all(e["rung"] == "sandbox-validated" for e in res["steps"].values())
    assert res["pipeline"]["rung"] == "sandbox-validated"
    assert not res["pipeline"]["incomplete"]  # stale, not incomplete


def test_compare_tampered_after_seal(proj):  # 07 test 4, first half
    r = synth.make_run(proj, 1, shift_new=True)
    p = r["run_dir"] / "verify" / "compare" / "ds.b_exposure.json"
    synth.make_writable(p.parent)
    p.write_text(p.read_text().replace('"verdict": "FAIL"', '"verdict": "PASS_EXACT"'))
    res = ladder.compute(proj["migration"])
    assert "EVIDENCE_HASH_MISMATCH" in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["incomplete"] and res["pipeline"]["rung"] == "none"


def test_compare_tampered_and_resealed(proj):  # 07 test 4, second half
    r = synth.make_run(proj, 1, shift_new=True, seal=False)

    def lie(doc):
        doc["verdict"] = "PASS_EXACT"
        doc["reason_codes"] = []
    rerun_compare(r["run_dir"], "ds.b_exposure", lie)
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert "STATE_RECOMPUTE_MISMATCH" in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["headline"] == "EVIDENCE INCOMPLETE"


def test_unbound_compare(proj):
    r = synth.make_run(proj, 1, seal=False)
    rerun_compare(r["run_dir"], "ds.a_mask", lambda d: d["refs"].update(out="out:" + "0" * 64))
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert "UNBOUND_COMPARE" in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["rung"] == "none"


def test_new_aux_xml_in_legacy_is_legacy_ref_changed(sealed):  # DESIGN R23
    (sealed["legacy"] / "tmp" / "a_mask.tif.aux.xml").write_text("<PAMDataset/>")
    res = ladder.compute(sealed["migration"])
    assert "LEGACY_REF_CHANGED" in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["incomplete"]


def test_legacy_reference_modified(sealed):
    p = sealed["legacy"] / "final" / "b_by_zone.csv"
    p.write_text(p.read_text() + "\n")
    res = ladder.compute(sealed["migration"])
    assert any(x["code"] == "LEGACY_REF_CHANGED" and "b_by_zone" in str(x.get("path")) for x in res["integrity"])


def test_empty_is_not_pass(proj):  # 07 test 7
    synth.make_run(proj, 1, skip_compare=("ds.a_mask", "ds.a_outline", "ds.b_exposure", "ds.b_by_zone"))
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["headline"] == "NOTHING VERIFIED"
    assert res["pipeline"]["rung"] != "reference-verified"
    assert res["pipeline"]["counts"]["not_compared"] == 4


def test_all_blocked_gives_no_verification(proj):  # 07 test 7, second half
    blocked = ("BLOCKED", [], ["UPSTREAM_BLOCKED"])
    synth.make_run(proj, 1, compares={d: blocked for d in ("ds.a_mask", "ds.a_outline", "ds.b_exposure",
                                                           "ds.b_by_zone")})
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["headline"] == "NOTHING VERIFIED"
    assert all(e["disposition"] == "BLOCKED" for e in res["steps"].values())


def test_coverage_missing_compare_is_not_compared(proj):  # 07 test 8
    synth.make_run(proj, 1, skip_compare=("ds.b_by_zone",))
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.b_by_zone"]["disposition"] == "NOT_COMPARED"
    assert res["steps"]["s02_total"]["disposition"] == "NOT_COMPARED"
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_visual_flag_on_pass(proj):  # 07 test 9
    synth.make_run(proj, 1, flag="ds.a_mask")
    res = ladder.compute(proj["migration"])
    d = res["datasets"]["ds.a_mask"]
    assert d["state"] == "NEEDS_REVIEW" and "VISUAL_FLAG" in d["reason_codes"]
    assert res["steps"]["s01_mask"]["disposition"] == "NEEDS_REVIEW"
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_looks_fine_receipt_cannot_lift_fail(proj):  # 07 test 9, second half
    synth.make_run(proj, 1, shift_new=True)  # receipts say "no" (looks fine) on every plot
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.b_exposure"]["state"] == "FAIL"
    assert res["datasets"]["ds.b_exposure"]["visual"]["receipts"] == 1


def test_visual_review_missing_blocks_reference_verified(proj):
    synth.make_run(proj, 1, visual="none")
    res = ladder.compute(proj["migration"])
    assert "VISUAL_REVIEW_MISSING" in res["datasets"]["ds.a_mask"]["reason_codes"]
    assert res["datasets"]["ds.b_by_zone"]["state"] == "PASS_EXACT"  # tables need no picture
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_receipt_citing_foreign_plot_does_not_count(proj):
    r = synth.make_run(proj, 1, seal=False)
    p = r["run_dir"] / "verify" / "visual-review.json"
    doc = ac.load_json(p)
    for f in doc["findings"]:
        f["plot_sha256"] = "9" * 64
    ac.dump_json(p, doc)
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.a_mask"]["visual"]["invalid_receipts"] == 1
    assert "VISUAL_REVIEW_MISSING" in res["datasets"]["ds.a_mask"]["reason_codes"]


def _set_param(mig: Path, pid: str, extra: str):
    t = (mig / "params.yaml").read_text()
    head, rest = t.split(f"  - id: {pid}\n", 1)
    block, tail = (rest.split("  - id:", 1) + [""])[:2]
    block = "\n".join(l for l in block.splitlines() if not l.strip().startswith(("source:", "resolution:",
                                                                                  "status:"))) + "\n"
    new = head + f"  - id: {pid}\n" + block + extra + ("  - id:" + tail if tail else "")
    (mig / "params.yaml").write_text(new)


def test_fitted_param_caps_then_answer_lifts(proj):  # 07 test 10 + DESIGN §4.3
    _set_param(proj["migration"], "p.threshold", "    source: fitted\n    resolution: inferred\n    status: assumed\n"
               "    flags: [fitted_on_reference]\n    fitted_against: ds.a_mask\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    caps = {c["code"] for c in res["steps"]["s01_mask"]["caps"]}
    assert "PARAM_FITTED_TO_REFERENCE" in caps
    assert res["steps"]["s01_mask"]["rung"] == "sandbox-validated"
    _set_param(proj["migration"], "p.threshold", "    source: answer\n    resolution: resolved\n    status: confirmed\n")
    synth.make_run(proj, 2)
    res2 = ladder.compute(proj["migration"])
    assert res2["steps"]["s01_mask"]["rung"] == "reference-verified"
    assert any(a["code"] == "PARAM_FITTED_CONFIRMED" or "confirmed" in a["text"]
               for a in res2["steps"]["s01_mask"]["annotations"]) or \
        "PARAM_FITTED_TO_REFERENCE" not in {c["code"] for c in res2["steps"]["s01_mask"]["caps"]}


def test_fitted_with_holdout_reaches_reference_verified_labelled(proj):
    _set_param(proj["migration"], "p.threshold", "    source: fitted\n    resolution: inferred\n    status: assumed\n"
               "    fit: {reference: ds.a_mask, split: {kind: zones}, result: {best: 60, best_score: 0, "
               "holdout_score: 0, unique: true}}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    s = res["steps"]["s01_mask"]
    assert s["rung"] == "reference-verified"
    assert any(a["text"] == "fitted, held-out pass" for a in s["annotations"])
    assert s["fitted_unconfirmed"] == ["p.threshold"]  # target-ready still needs a person


def test_global_fitted_param_holdout_is_not_enough(proj):
    _set_param(proj["migration"], "p.scale", "    source: fitted\n    resolution: inferred\n    status: assumed\n"
               "    fit: {reference: ds.b_exposure, split: {kind: zones}, result: {best: 2.0, best_score: 0, "
               "holdout_score: 0, unique: true}}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    cap = [c for c in res["steps"]["s02_total"]["caps"] if c["code"] == "PARAM_FITTED_TO_REFERENCE"]
    assert cap and "globally" in cap[0]["detail"]


def test_assumption_annotates_without_cap(proj):
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: inferred\n    status: assumed\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    s = res["steps"]["s01_mask"]
    assert s["rung"] == "reference-verified"
    assert any(a["code"] == "ASSUMPTION" for a in s["annotations"])


KD_TEMPLATE = """
## KD-001 · Exposure grid moved on purpose · 2026-10-15T09:00:00Z
```yaml
id: KD-001
kind: accepted_change
datasets: [ds.b_exposure]
checks: [raster.grid.transform]
reason_codes: [RASTER_TRANSFORM_MISMATCH]
rule: {expected: "grid shifted one cell east"}
bound: {metric: transform_dev_px, max: 1.0}
reason: synthetic
evidence: []
owner: {owner}
decided_at: 2026-10-15
decision_ref: decisions.md#D-001
status: accepted
```
"""


def _kd_run(proj, owner: str, bound_state: str):
    (proj["migration"] / "known-differences.md").write_text(synth.KD_MD + KD_TEMPLATE.replace("{owner}", owner))
    checks = json.loads(json.dumps(synth.SHIFT_CHECKS)) + [
        {"id": "kd.KD-001", "kd_id": "KD-001", "order": 9, "state": bound_state, "reason_codes": [],
         "metric": "transform_dev_px", "value": 1.0, "threshold": 1.0}]
    synth.make_run(proj, 1, shift_new=True,
                   compares={"ds.b_exposure": ("FAIL", checks, ["RASTER_TRANSFORM_MISMATCH"])})
    return ladder.compute(proj["migration"])


def test_kd_human_owner_bound_held_is_accepted_difference(proj):  # 07 test 11
    res = _kd_run(proj, "human:najah", "PASS_EXACT")
    assert res["datasets"]["ds.b_exposure"]["state"] == "FAIL"  # the parity row stays FAIL
    assert res["datasets"]["ds.b_exposure"]["disposition"] == "ACCEPTED_DIFFERENCE"
    assert res["steps"]["s02_total"]["disposition"] == "ACCEPTED_DIFFERENCE"
    assert res["steps"]["s02_total"]["rung"] == "reference-verified"


def test_kd_model_owner_is_ignored(proj):
    res = _kd_run(proj, "agent", "PASS_EXACT")
    assert res["steps"]["s02_total"]["disposition"] == "FAILED"


def test_kd_broken_bound_is_ignored(proj):
    res = _kd_run(proj, "human:najah", "FAIL")
    assert res["steps"]["s02_total"]["disposition"] == "FAILED"


def test_tolerance_unsigned_is_needs_decision(proj):
    checks = synth.default_checks({"kind": "raster"})
    checks[-1].update(state="NEEDS_DECISION", reason_codes=["TOLERANCE_UNSIGNED"], value=3e-7)
    synth.make_run(proj, 1, compares={"ds.b_exposure": ("NEEDS_DECISION", checks, ["TOLERANCE_UNSIGNED"])})
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.b_exposure"]["disposition"] == "NEEDS_DECISION"
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_kd_unsigned_dataset_code_cannot_be_declared_pass(proj):
    r = synth.make_run(proj, 1, seal=False)
    rerun_compare(r["run_dir"], "ds.a_mask", lambda d: d.update(reason_codes=["KD_UNSIGNED"]))
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert "STATE_RECOMPUTE_MISMATCH" in {x["code"] for x in res["integrity"]}


def test_bridge_suffix(proj):  # 07 test 12
    def add_bridge(g):
        g["steps"].insert(0, {"id": "s00_zones_bridge", "title": "Edited zones (bridge)", "kind": "bridge",
                              "scope": "migrate", "direction": "in", "inputs": [], "outputs": ["ds.zones"],
                              "new": {"module": None}})
    synth.update_graph(proj["migration"], add_bridge)
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["suffix"] == " · partial (1 bridged)"
    assert res["steps"]["s00_zones_bridge"]["disposition"] == "BRIDGED"
    assert res["status_line"].startswith("axis-migrate status: reference-verified · partial (1 bridged) | run ")


def test_isolated_run_caps_at_generated(proj):
    synth.make_run(proj, 1, chain="isolated")
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["rung"] == "generated"
    assert any(c["code"] == "RUN_NOT_CHAINED" for c in res["steps"]["s01_mask"]["caps"])


def test_failed_step(proj):
    synth.make_run(proj, 1, step_status={"s02_total": "failed"})
    res = ladder.compute(proj["migration"])
    assert res["steps"]["s02_total"]["disposition"] == "FAILED"
    assert res["steps"]["s02_total"]["rung"] == "generated"
    assert res["pipeline"]["full"] is False


def test_no_sealed_run(proj):
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["rung"] == "none" and res["run_id"] is None
    assert res["status_line"].startswith("axis-migrate status: none | run none")


def test_scan_block_prevents_generated(proj):
    r = synth.make_run(proj, 1, seal=False)
    ac.dump_json(r["run_dir"] / "verify" / "scan.json",
                 {"findings": [{"rule": "S02", "sev": "block", "file": "pipeline/s01_mask.py", "line": 3}]})
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert res["steps"]["s01_mask"]["rung"] == "none"
    assert res["steps"]["s02_total"]["rung"] == "reference-verified"
    assert res["pipeline"]["rung"] == "none"


def test_performance_many_calls(sealed):
    t = time.time()
    for _ in range(5):
        ladder.compute(sealed["migration"])
    assert (time.time() - t) / 5 < 5.0


def test_graph_projection_after_seal_is_not_drift(sealed):
    """graph.py derive writes ladder/verify projections into graph.json after the run: not GRAPH_CHANGED_AFTER_RUN."""
    def derive(g):
        g["revision"] = g.get("revision", 0) + 1
        g["updated"] = {"rev": g["revision"], "by": "derive", "at": "2026-10-16T15:00:00Z"}
        g["ladder"] = {"project": "reference-verified", "derived_rev": g["revision"], "blocking": []}
        for s in g["steps"]:
            s["ladder"] = {"rung": "reference-verified"}
            s["verify"] = {"verdict": "PASS_EXACT", "blame": None}
            s.setdefault("new", {})["code_sha256"] = "f" * 64
        for d in g["datasets"]:
            d["verify"] = {"run_id": sealed["run_id"], "verdict": "PASS_EXACT"}
    synth.update_graph(sealed["migration"], derive)
    res = ladder.compute(sealed["migration"])
    assert "GRAPH_CHANGED_AFTER_RUN" not in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["rung"] == "reference-verified"


def test_graph_contract_change_after_seal_is_drift(sealed):
    def change(g):
        for d in g["datasets"]:
            if d["id"] == "ds.b_by_zone":
                d.setdefault("compare", {})["key"] = ["zone"]
    synth.update_graph(sealed["migration"], change)
    res = ladder.compute(sealed["migration"])
    assert "GRAPH_CHANGED_AFTER_RUN" in {x["code"] for x in res["integrity"]}
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_graph_scope_change_after_seal_is_drift(sealed):
    synth.update_graph(sealed["migration"], lambda g: g["steps"][1].update(scope="keep_legacy"))
    res = ladder.compute(sealed["migration"])
    assert "GRAPH_CHANGED_AFTER_RUN" in {x["code"] for x in res["integrity"]}


def _add_equivalent_step(mig, confirmed=True):
    """An alternative implementation (like an old ArcPy step) whose own output never existed on disk, paired by a
    person with the QGIS step's final via an equivalent reference (DESIGN §4.2 set_reference)."""
    def add(g):
        g["steps"].append({"id": "s03_alt_arcpy", "title": "Old ArcPy variant", "kind": "script", "tool": "arcpy",
                           "scope": "migrate", "inputs": ["ds.a_mask", "ds.pop"], "outputs": ["ds.c_alt"],
                           "params": [], "new": {"module": "pipeline/s03_alt_arcpy.py", "function": "run"}})
        g["steps"][1]["tool"] = "qgis"
        g["datasets"].append({"id": "ds.c_alt", "kind": "raster", "role": "final",
                              "legacy": {"path": "work.gdb", "layer": "c_alt", "exists": False},
                              "new": {"path": "s03_alt_arcpy/c_alt.tif"}, "produced_by": {"step": "s03_alt_arcpy"},
                              "no_reference_reason": "superseded_only",
                              "reference": {"source": "equivalent", "match_method": "human",
                                            "via_dataset": "ds.b_exposure",
                                            "confirmed_by": "user" if confirmed else None}})
    synth.update_graph(mig, add)
    (mig / "pipeline" / "s03_alt_arcpy.py").write_text('STEP_ID = "s03_alt_arcpy"\n\ndef run(ctx) -> None:\n'
                                                       '    raise NotImplementedError\n')


def test_equivalent_reference_is_compared_and_bound(proj):  # rehearsal D1
    _add_equivalent_step(proj["migration"])
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    assert res["integrity"] == [], res["integrity"]
    d = res["datasets"]["ds.c_alt"]
    assert d["has_reference"] and d["state"] == "PASS_EXACT" and d["legacy_path"] == "final/b_exposure.tif"
    s = res["steps"]["s03_alt_arcpy"]
    assert s["disposition"] == "VERIFIED" and s["rung"] == "reference-verified"
    assert any(a["code"] == "LEGACY_EQUIVALENT_REFERENCE" and "equivalent QGIS step s02_total" in a["text"]
               for a in s["annotations"])
    ev = ac.load_json(proj["migration"] / "runs" / synth.run_id_for(1) / "verify" / "evidence.json")
    refs = [o for o in ev["objects"] if o["kind"] == "ref" and o["path"].endswith("final/b_exposure.tif")]
    assert sorted(o["dataset"] for o in refs) == ["ds.b_exposure", "ds.c_alt"]  # one shared file, two ref objects
    assert len({o["id"] for o in refs}) == 1


def test_equivalent_reference_with_human_kd_is_accepted_difference(proj):
    _add_equivalent_step(proj["migration"])
    kd = KD_TEMPLATE.replace("{owner}", "human:najah").replace("ds.b_exposure", "ds.c_alt") \
        .replace("[raster.grid.transform]", "[raster.mask]").replace("[RASTER_TRANSFORM_MISMATCH]",
                                                                     "[NODATA_MASK_MISMATCH]")
    (proj["migration"] / "known-differences.md").write_text(synth.KD_MD + kd)
    checks = synth.default_checks({"kind": "raster"})
    checks[5].update(state="FAIL", reason_codes=["NODATA_MASK_MISMATCH"], value=404, threshold=0)
    checks.append({"id": "kd:KD-001", "kd_check": True, "kd": ["KD-001"], "order": 9, "state": "PASS_WITHIN_TOLERANCE",
                   "reason_codes": [], "held": True, "detail": {"signed": True}})
    synth.make_run(proj, 1, compares={"ds.c_alt": ("FAIL", checks, ["NODATA_MASK_MISMATCH"])})
    res = ladder.compute(proj["migration"])
    assert res["integrity"] == [], res["integrity"]
    assert res["datasets"]["ds.c_alt"]["state"] == "FAIL"
    assert res["steps"]["s03_alt_arcpy"]["disposition"] == "ACCEPTED_DIFFERENCE"
    assert res["pipeline"]["counts"]["fail"] == 1  # the parity row stays FAIL in the counts


def test_unconfirmed_equivalent_is_not_compared_not_no_reference(proj):
    _add_equivalent_step(proj["migration"], confirmed=False)
    r = synth.make_run(proj, 1, compares={"ds.c_alt": ("NOT_COMPARED", [], ["PAIRING_UNCONFIRMED"])}, seal=False)
    rerun_compare(r["run_dir"], "ds.c_alt", lambda d: d["refs"].update(out=None, ref=None))  # as compare.py writes it
    sealmod.seal(proj["migration"], r["run_id"])
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.c_alt"]["disposition"] == "NOT_COMPARED"
    assert res["steps"]["s03_alt_arcpy"]["disposition"] == "NOT_COMPARED"
    assert res["pipeline"]["rung"] == "sandbox-validated"


# ---------------------------------------------------------------- assumptions parity cannot see (review §4.8)
def test_assumed_parity_blind_caps_step_and_pipeline(proj):
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: inferred\n    status: assumed\n"
               "    flags: [policy]\n    impact: {datasets: [ds.a_mask], level: high}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    s = res["steps"]["s01_mask"]
    cap = [c for c in s["caps"] if c["code"] == "ASSUMED_PARITY_BLIND"]
    assert cap and cap[0]["params"] == ["p.threshold"] and cap[0]["max"] == "sandbox-validated"
    assert s["rung"] == "sandbox-validated" and s["disposition"] == "VERIFIED"
    assert res["steps"]["s02_total"]["rung"] == "reference-verified"   # impact names only ds.a_mask
    assert res["pipeline"]["rung"] == "sandbox-validated"
    assert res["pipeline"]["confirm_params"] == ["p.threshold"]
    assert any(b["code"] == "ASSUMED_PARITY_BLIND" and "p.threshold" in b["detail"]
               for b in res["pipeline"]["blockers"])
    assert "| confirm p.threshold (assumed, parity-blind) |" in res["status_line"]
    assert any(a["code"] == "ASSUMPTION" and a["parity_blind"] for a in s["annotations"])
    import report
    report.build(proj["migration"])
    html = (proj["migration"] / "report" / "index.html").read_text()
    assert "ASSUMED_PARITY_BLIND" in html and "confirm p.threshold" in html


def test_assumed_parity_blind_caps_steps_producing_impact_datasets(proj):
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: unresolved\n    status: assumed\n"
               "    kind: manual_step\n    impact: {datasets: [ds.a_mask, ds.b_exposure], level: high}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    for sid in ("s01_mask", "s02_total"):
        assert res["steps"][sid]["rung"] == "sandbox-validated"
        assert "ASSUMED_PARITY_BLIND" in {c["code"] for c in res["steps"][sid]["caps"]}


def test_confirmed_parity_blind_param_does_not_cap(proj):
    _set_param(proj["migration"], "p.threshold", "    source: answer\n    resolution: unresolved\n    status: confirmed\n"
               "    question: q01\n    flags: [parity_blind]\n    impact: {datasets: [ds.a_mask], level: high}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["rung"] == "reference-verified"
    assert "ASSUMED_PARITY_BLIND" not in {c["code"] for c in res["steps"]["s01_mask"]["caps"]}
    assert res["pipeline"]["confirm_params"] == []
    assert "confirm" not in res["status_line"]


def test_assumed_parity_visible_param_annotates_only(proj):
    # high impact, but a threshold with a legacy reference downstream: compare catches a wrong value
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: inferred\n    status: assumed\n"
               "    impact: {datasets: [ds.a_mask], level: high}\n")
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    s = res["steps"]["s01_mask"]
    assert s["rung"] == "reference-verified" and res["pipeline"]["rung"] == "reference-verified"
    assert any(a["code"] == "ASSUMPTION" and not a["parity_blind"] for a in s["annotations"])
    assert "ASSUMED_PARITY_BLIND" not in {c["code"] for c in s["caps"]}


def test_assumed_low_impact_policy_param_annotates_only(proj):
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: inferred\n    status: assumed\n"
               "    flags: [policy]\n    impact: {datasets: [ds.a_mask], level: medium}\n")
    synth.make_run(proj, 1)
    assert ladder.compute(proj["migration"])["pipeline"]["rung"] == "reference-verified"


def test_question_cost_three_makes_an_assumption_parity_blind(proj):
    _set_param(proj["migration"], "p.threshold", "    source: assumption\n    resolution: unresolved\n    status: assumed\n"
               "    question: q01\n    impact: {datasets: [ds.a_mask], level: high}\n")
    synth.update_graph(proj["migration"], lambda g: g["questions"].append(
        {"id": "q01", "status": "assumed", "node": "s01_mask", "param_ids": ["p.threshold"],
         "impact": {"datasets": ["ds.a_mask"], "level": "high"},
         "priority": {"impact": 3, "uncertainty": 3, "cost": 3, "score": 27}}))
    synth.make_run(proj, 1)
    res = ladder.compute(proj["migration"])
    assert res["steps"]["s01_mask"]["rung"] == "sandbox-validated"
    assert res["pipeline"]["confirm_params"] == ["p.threshold"]


def test_is_parity_blind_rule():
    hi = {"impact": {"level": "high"}}
    assert ladder.is_parity_blind({**hi, "kind": "manual_step"})
    assert ladder.is_parity_blind({**hi, "kind": "branch"})
    assert ladder.is_parity_blind({**hi, "kind": "threshold", "flags": ["latent_legacy_bug"]})
    assert not ladder.is_parity_blind({**hi, "kind": "threshold"})
    assert not ladder.is_parity_blind({**hi, "kind": "target", "flags": ["policy"]})
    assert not ladder.is_parity_blind({"impact": {"level": "medium"}, "kind": "manual_step"})
    assert ladder.is_parity_blind({"impact_level": "high", "kind": "other", "id": "p.x"},
                                  [{"id": "q9", "param_ids": ["p.x"], "priority": {"cost": 3}}])
    # a bundle sealed before impact_level was recorded: the question's impact level stands in
    assert ladder.is_parity_blind({"kind": "manual_step", "id": "p.x", "question": "q9"},
                                  [{"id": "q9", "impact": {"level": "high"}}])


# ---------------------------------------------------------------- read ledger (review §1.5)
def _seal_with_ledger(proj, kind):
    r = synth.make_run(proj, 1, seal=False)
    man = ac.load_json(r["run_dir"] / "manifest.json")
    man["read_ledger"] = kind
    ac.dump_json(r["run_dir"] / "manifest.json", man)
    sealmod.seal(proj["migration"], r["run_id"])
    return ladder.compute(proj["migration"])


def test_e2b_without_strace_is_read_ledger_weak(proj):
    res = _seal_with_ledger(proj, "e2b-physical+audit")
    caps = [c for c in res["steps"]["s01_mask"]["caps"] if c["code"] == "READ_LEDGER_WEAK"]
    assert caps and caps[0]["detail"] == "e2b-physical+audit"
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_e2b_with_strace_is_not_capped(proj):
    res = _seal_with_ledger(proj, "e2b-physical+strace")
    assert res["pipeline"]["rung"] == "reference-verified"


def test_unknown_read_ledger_is_weak(proj):
    res = _seal_with_ledger(proj, None)
    assert "READ_LEDGER_WEAK" in {c["code"] for c in res["steps"]["s02_total"]["caps"]}


# ---------------------------------------------------------------- hash cache (review §3 fix 3)
def _age_legacy(legacy: Path, seconds: float = 600) -> None:
    t = time.time() - seconds
    for p in legacy.rglob("*"):
        if p.is_file():
            os.utime(p, (t, t))


def _flip_digit(p: Path) -> None:
    """Rewrite one digit in place: same size, new content."""
    raw = bytearray(p.read_bytes())
    i = max(j for j, c in enumerate(raw) if chr(c).isdigit())
    raw[i] = ord("8") if raw[i] == ord("9") else ord("9")
    p.write_bytes(bytes(raw))


def test_hash_cache_serves_legacy_refs_and_catches_a_change(sealed):
    import hashcache
    _age_legacy(sealed["legacy"])
    assert ladder.compute(sealed["migration"])["pipeline"]["rung"] == "reference-verified"
    cache = hashcache.cache_path(sealed["migration"])
    entries = ac.load_json(cache)["entries"]
    ref = os.path.realpath(sealed["legacy"] / "final" / "b_by_zone.csv")
    assert ref in entries                                   # warm: a second compute costs a stat per file
    assert ladder.compute(sealed["migration"])["pipeline"]["rung"] == "reference-verified"
    p = sealed["legacy"] / "final" / "b_by_zone.csv"
    _flip_digit(p)   # same size, new mtime
    res = ladder.compute(sealed["migration"])
    assert any(x["code"] == "LEGACY_REF_CHANGED" and "b_by_zone" in str(x.get("path")) for x in res["integrity"])


def test_hash_cache_known_limit_restored_mtime(sealed):
    """Documented limit (ladder docstring): a same-size rewrite with its mtime_ns restored is a cache hit. The Bash
    guard refuses `touch` in the legacy tree; deleting the cache restores full hashing."""
    import hashcache
    _age_legacy(sealed["legacy"])
    ladder.compute(sealed["migration"])
    p = sealed["legacy"] / "final" / "b_by_zone.csv"
    st = p.stat()
    _flip_digit(p)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert not any(x["code"] == "LEGACY_REF_CHANGED" for x in ladder.compute(sealed["migration"])["integrity"])
    hashcache.cache_path(sealed["migration"]).unlink()
    assert any(x["code"] == "LEGACY_REF_CHANGED" for x in ladder.compute(sealed["migration"])["integrity"])


# ---------------------------------------------------------------- --brief (Lux e2e item 10)
def test_brief_names_every_non_pass_dataset_of_an_unsealed_run(proj):
    assert ladder.brief(proj["migration"]) == "axis-migrate brief: no run yet | computed status: none (no sealed run)"
    r = synth.make_run(proj, 1, shift_new=True, seal=False)
    line = ladder.brief(proj["migration"])
    assert line.startswith(f"axis-migrate brief: run {r['run_id']} (chained, unsealed: no rung): ")
    assert "s02_total: ds.b_exposure FAIL (RASTER_TRANSFORM_MISMATCH)" in line
    assert "PASS_EXACT" in line and "computed status: none" in line and "(no sealed run)" in line
    assert "axis-migrate status:" not in line          # quoting it mid-run is not a finishing message


def test_brief_on_a_sealed_clean_run_and_cli(sealed):
    line = ladder.brief(sealed["migration"])
    assert "(chained, sealed): 4 PASS_EXACT | not PASS: none |" in line
    assert line.endswith(f"computed status: reference-verified from sealed run {sealed['run_id']}")
    out = subprocess.run([SYS_PY, "-I", str(HERE.parent / "ladder.py"), str(sealed["migration"]),
                          "--brief"], capture_output=True, text=True, check=True).stdout
    assert out.strip() == line


@pytest.mark.parametrize("good", [True, False])
def test_skipped_identical_receipt_counts_only_with_the_right_sha(proj, good):
    """map skill: an output identical to legacy needs no view; its skipped receipt counts only when the sha it cites
    is the sealed compare result's new sha256 (same rule as compare.py aggregate)."""
    import visual_receipt as vrc
    if True:
        r = synth.make_run(proj, 1, seal=False)
        cp = r["run_dir"] / "verify" / "compare" / "ds.a_mask.json"
        cmp = ac.load_json(cp)
        cmp["verdict"] = "PASS_EXACT"
        cmp.setdefault("diagnostic_codes", []).append(vrc.IDENTICAL_CODE)
        old_id, cmp["result_id"] = cmp["result_id"], None
        cmp.pop("result_id")
        cmp["result_id"] = ladder.cmp_content_id(cmp)
        ac.dump_json(cp, cmp)
        pj = r["run_dir"] / "verify" / "plots.json"
        if pj.is_file():
            pj.write_text(pj.read_text().replace(old_id, cmp["result_id"]))
        sha = (cmp.get("new") or {}).get("sha256") if good else "0" * 64
        p = r["run_dir"] / "verify" / "visual-review.json"
        doc = ac.load_json(p)
        doc["findings"] = [f for f in doc["findings"] if f.get("dataset_id") != "ds.a_mask"] + [
            {"receipt": "skipped", "dataset_id": "ds.a_mask", "skipped": vrc.skip_text(sha), "question_id": "Q-all",
             "answer": "no", "effect": "none", "category": "none", "note": "", "model": "m", "run_id": r["run_id"],
             "finding_id": "vf-skip" + ("ok" if good else "bad")}]
        ac.dump_json(p, doc)
        sealmod.seal(proj["migration"], r["run_id"])
        res = ladder.compute(proj["migration"], r["run_id"])
        codes = res["datasets"]["ds.a_mask"]["reason_codes"]
        assert "UNBOUND_COMPARE" not in codes and res["datasets"]["ds.a_mask"]["state"] != "EVALUATOR_ERROR", codes
        assert ("VISUAL_REVIEW_MISSING" in codes) is (not good), (good, codes)


def test_status_suffix_standing_instruction_and_open_questions(proj):
    """B2/B3 (BYU e2e): a plan approved by a standing instruction and an open question show in the status line; they
    cap no rung but can never read as user approval or '0 open items'."""
    def edit(g):
        for s in g["steps"]:
            s["plan"] = {"summary": "x", "approved": {"by": ladder.STANDING_INSTRUCTION, "at": "2026-10-15T00:00:00Z"}}
        g.setdefault("questions", []).append({"id": "q99", "kind": "legacy_bug", "status": "open", "node": "s01_mask",
                                              "header": "Empty value"})
    synth.update_graph(proj["migration"], edit)
    proj.update(synth.make_run(proj, 1))
    res = ladder.compute(proj["migration"])
    assert res["pipeline"]["rung"] == "reference-verified"
    assert res["status_line"].startswith(
        "axis-migrate status: reference-verified · plan approved by standing instruction · 1 open question | run ")
    assert [q["id"] for q in res["pipeline"]["open_questions"]] == ["q99"]
    assert res["pipeline"]["plan_approval"]["by"] == ladder.STANDING_INSTRUCTION


def test_open_question_suffix_before_any_run(proj):
    """BYU re-run 2: the plan gate stopped the run at an open question; the status line must say so with no run."""
    synth.update_graph(proj["migration"], lambda g: g.setdefault("questions", []).append(
        {"id": "q07", "kind": "legacy_bug", "status": "open", "node": "s01_mask", "header": "Empty amenity"}))
    res = ladder.compute(proj["migration"])
    assert res["status_line"].startswith("axis-migrate status: none · 1 open question | run none")
    assert [q["id"] for q in res["pipeline"]["open_questions"]] == ["q07"]
