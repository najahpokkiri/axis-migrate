"""ladder.py: rungs, dispositions, caps, integrity (07 §3.4, §4.3; DESIGN §4.3, §4.6)."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import axis_common as ac
import ladder
import seal as sealmod
import synth

HERE = Path(__file__).resolve().parent


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
                                  "0 NOT_COMPARED, 0 needs review | report migration/report/index.html")


def test_ladder_is_stdlib_only_and_fast(sealed):
    code = ("import sys; sys.path.insert(0, %r); import ladder, time; t=time.time(); r=ladder.compute(%r); "
            "bad=[m for m in ('numpy','yaml','jinja2','rasterio') if m in sys.modules]; "
            "print(r['pipeline']['rung'], bad, round(time.time()-t, 3))") % (str(HERE.parent), str(sealed["migration"]))
    out = subprocess.run(["/usr/bin/python3", "-I", "-c", code], capture_output=True, text=True, check=True).stdout
    rung, rest = out.split(" ", 1)
    assert rung == "reference-verified"
    assert "[]" in rest
    assert float(rest.rsplit(" ", 1)[1]) < 5.0


def test_cli_status_line(sealed):
    out = subprocess.run(["/usr/bin/python3", "-I", str(HERE.parent / "ladder.py"), str(sealed["migration"])],
                         capture_output=True, text=True, check=True).stdout
    assert out.splitlines()[0].startswith("axis-migrate status: reference-verified | run ")
    js = subprocess.run(["/usr/bin/python3", "-I", str(HERE.parent / "ladder.py"), str(sealed["migration"]), "--json"],
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
