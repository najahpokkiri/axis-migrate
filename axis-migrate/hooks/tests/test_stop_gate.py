"""H7 Stop gate (08 §5b) and claims.py: G10, G11, unacked edits, 3-block cap, fail behaviour."""
from __future__ import annotations

import json

import pytest
from conftest import add_compared_run, add_receipt, add_run, payload, run_hook, run_ids

import claims as cl
import stop_gate as sg

# every in-process test here runs with an APFS-like listing order (files first, names reversed): the gate must
# never depend on directory listing order or pick up runs/index.jsonl as a run directory
pytestmark = pytest.mark.usefixtures("apfs_order")


def stop(pr, msg, active=False, session="s-1", env=None):
    return run_hook("stop_gate.py", payload("Stop", cwd=str(pr.root), last_assistant_message=msg,
                                            stop_hook_active=active, session_id=session), env=env)


def fake_ladder(rung, run_id=None, steps=None):
    def f(mig):
        return {"pipeline": {"rung": rung, "counts": {"pass": 3, "fail": 1, "not_compared": 0, "needs_review": 0},
                             "blockers": [{"code": "FAILED", "step": "s03_final"}]},
                "steps": steps or {"s01_prep": {"disposition": "VERIFIED"},
                                   "s03_final": {"disposition": "FAILED", "caps": [{"code": "ROW_COUNT_MISMATCH"}]}},
                "run_id": run_id, "status_line": f"axis-migrate status: {rung} | run {run_id} | 3 pass, 1 FAIL"}
    return f


def decide(pr, msg, monkeypatch, rung, run_id=None):
    monkeypatch.setattr(sg, "ladder_status", fake_ladder(rung, run_id))
    return sg.decide({"last_assistant_message": msg, "cwd": str(pr.root)}, pr.mig)


# claims ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text,rung", [
    ("Migration complete, parity achieved.", "reference-verified"),
    ("All checks pass.", "reference-verified"),
    ("The new outputs match the legacy outputs exactly; output identical to legacy.", "reference-verified"),
    ("The pipeline is target-ready.", "target-ready"),
    ("It is ready to deploy.", "target-ready"),
    ("Steps validated in the sandbox.", "sandbox-validated"),
    ("The pipeline ran successfully.", "sandbox-validated"),
    ("The job is deployed to Databricks.", "deployed"),
    ("After the chained run, all checks pass.", "reference-verified"),
    ("The pipeline is reference-verified.", "reference-verified"),
    ("Done.", "reference-verified"),
    ("All done.", "reference-verified"),
    ("I'm done.", "reference-verified"),
    ("We finished the migration.", "reference-verified"),
    ("The pipeline works.", "reference-verified"),
    ("It works end to end.", "reference-verified"),
    ("Everything is finished.", "reference-verified"),
    ("All steps are complete.", "reference-verified"),
    ("The project is done, see the report.", "reference-verified"),
    ("The chained run is verified.", "reference-verified"),
    ("Run r3 got verified: all checks pass.", "reference-verified"),
    ("The pipeline was validated in the sandbox.", "sandbox-validated"),
    # modal + assertion verb is a present claim (orchestrator ruling)
    ("We can confirm all checks pass.", "reference-verified"),
    ("I can report that parity is achieved.", "reference-verified"),
    ("I can say the outputs match legacy.", "reference-verified"),
    ("You will see that all steps pass.", "reference-verified"),
    ("You can see it is reference-verified.", "reference-verified"),
    ("Then I will show that all checks pass.", "reference-verified"),
    ("I'll confirm: the migration is complete.", "reference-verified"),
    ("Migration done", "reference-verified"),
    ("The migration is complete.", "reference-verified"),
    ("The migration is complete; all steps finished.", "reference-verified"),
    ("Status: currently sandbox-validated.", "sandbox-validated"),
    ("It is currently reference-verified and the report is written.", "reference-verified"),
    ("This is production-ready.", "deployed"),
])
def test_claim_rungs(text, rung):
    assert cl.strongest(cl.find_claims(text)).rung == rung


@pytest.mark.parametrize("text", [
    "Migrated, not verified: s02 fails RASTER_TRANSFORM_MISMATCH.",
    "s03 is not verified yet; I have not finished.",
    "Nothing is verified until the chained run.",
    "Which canton naming rule should the pipeline use? Once you answer, step 2 can be done.",
    "I will run the comparison before the step is verified.",
    "axis-migrate status: reference-verified | run r1 | 13 pass, 0 FAIL",
    "The status is UPSTREAM_NOT_VERIFIED for s04.",
    "It is unverified.",
    "Code generated; it hasn't been validated.",
    'I did not end with "Migration complete, parity achieved." because the status is none.',
    "I will not write `all checks pass` here.",
    # requirement / condition / goal statements (orchestrator finding, first e2e trial)
    "The Databricks bundle was not generated. That step needs the pipeline at reference-verified.",
    "Target-ready requires every final to be reference-verified.",
    "The pipeline stops at sandbox-validated until a person signs.",
    "To reach reference-verified, s02 must pass.",
    "s03 is blocked: reference-verified only after the tolerance is signed.",
    "When the chained run passes, the step is verified.",
    "We are waiting for the user before it can be target-ready.",
    "Nothing above sandbox-validated is possible yet.",
    # manner / means: how something is done, not that it is done (orchestrator finding, eval trial)
    "The hand edit of canton names is now done in code, and the code's output is compared against your hand-made file.",
    "The rename was done by hand in QGIS.",
    "Staging is done by the runner, not by step code.",
    "The reprojection is done via pyproj and the clip with rasterio.",
    "Masking is completed using the declared nodata value.",
    "The copy is done automatically when the run starts.",
    "Zonal sums are finished in numpy through a bincount.",
    # progress on a named sub-task, not a migration claim (orchestrator finding, eval trial)
    "The scan and the input maps are finished, with seven map findings.",
    "The interview is complete: three questions answered.",
    "The plan is done; please approve it.",
    "Step s02's code is finished and scanned clean.",
    "The isolated run of s01 completed.",
    "The report rendering is done.",
    "I have finished the scan.",
    "Here is how the zonal step works.",
    # future / modal phrasing: a plan, not a claim (orchestrator finding, eval trial)
    "Getting there takes three things: you sign the records, I do a new chained run, and that run gets verified.",
    "The next run will be verified against the legacy outputs.",
    "It would be reference-verified after you sign the tolerance record.",
    "s02 can be validated once the plan is approved.",
    "The output is to be verified in the next run.",
    "It takes a sealed run to be target-ready.",
    "I cannot confirm that all checks pass.",
    "I can't say the outputs match legacy yet.",
    "We will confirm in the next run whether all checks pass.",
    "After you sign the records, the pipeline is reference-verified.",
])

def test_negated_or_absent_claims(text):
    assert cl.find_claims(text) == [], cl.find_claims(text)


def test_requirement_plus_current_status():
    text = ("The Databricks bundle was not generated. That step needs the pipeline at reference-verified, "
            "and it is currently sandbox-validated.")
    found = cl.find_claims(text)
    assert [c.rung for c in found] == ["sandbox-validated"]          # the current status is reported ...
    assert cl.unsupported(found, "sandbox-validated") == []          # ... and supported by the computed rung
    assert cl.unsupported(found, "generated")                        # but still above a lower computed rung


def test_target_claim_text_needs_target_ready():
    found = cl.find_claims("target-ready (Databricks), not deployed.")
    assert [c.rung for c in found] == ["target-ready"]  # "deployed" is negated, "target-ready" is a claim


def test_quoted_claim_still_counts():
    assert cl.find_claims('My summary: "all checks pass".')


# G10 ------------------------------------------------------------------------------------------------------------
def test_g10_claim_above_rung_blocks(project, monkeypatch):
    d = decide(project, "Migration complete, parity achieved.", monkeypatch, "sandbox-validated", "r1")
    assert d is not None and d["rung"] == "sandbox-validated"
    r = d["reason"]
    assert r.startswith('axis-migrate Stop gate: your message says "Migration complete, parity achieved"')
    assert "Status line: axis-migrate status: sandbox-validated" in r
    assert "First failing step: s03_final FAILED ROW_COUNT_MISMATCH" in r
    assert "Do not edit tolerances" in r and r.isascii()


@pytest.mark.parametrize("msg", [
    "Migrated, not verified: s02 fails RASTER_TRANSFORM_MISMATCH.",
    "Which option do you prefer for the canton names?",
    "axis-migrate status: sandbox-validated | run r1 | 3 pass, 1 FAIL. Step s03_final fails ROW_COUNT_MISMATCH.",
])
def test_g10_honest_messages_pass(project, monkeypatch, msg):
    assert decide(project, msg, monkeypatch, "sandbox-validated", "r1") is None


def test_g10_supported_claim_passes(project_run, monkeypatch):
    rid = run_ids(project_run)[0]
    rd = project_run.mig / "runs" / rid
    for ds in ("ds.01_mid", "ds.final"):
        (rd / "verify" / "compare" / f"{ds}.json").write_text("{}")
    (project_run.mig / "report").mkdir()
    (project_run.mig / "report" / "index.html").write_text("<html></html>")
    (project_run.mig / "report" / "REPORT.json").write_text(json.dumps({"evidence_id": "evidence:abc"}))
    assert decide(project_run, "All checks pass.", monkeypatch, "reference-verified", rid) is None


def test_g10_missing_compare_blocks_verified_claim(project_run, monkeypatch):
    rid = run_ids(project_run)[0]
    d = decide(project_run, "All checks pass.", monkeypatch, "reference-verified", rid)
    assert d is not None and "NOT_COMPARED" in d["reason"]


def test_g10_deployed_claim_always_blocks(project, monkeypatch):
    assert decide(project, "Deployed to the workspace.", monkeypatch, "target-ready", "r1") is not None


# G11 drift ------------------------------------------------------------------------------------------------------
def _verified_bundle(pr):
    rid = run_ids(pr)[0]
    rd = pr.mig / "runs" / rid
    for ds in ("ds.01_mid", "ds.final"):
        (rd / "verify" / "compare" / f"{ds}.json").write_text("{}")
    (pr.mig / "report").mkdir(exist_ok=True)
    (pr.mig / "report" / "index.html").write_text("<html></html>")
    (pr.mig / "report" / "REPORT.json").write_text(json.dumps({"evidence_id": "evidence:abc"}))
    return rid


def test_g11_tolerance_drift_blocks_verified(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    assert decide(project_run, "Verified.", monkeypatch, "reference-verified", rid) is None
    t = project_run.mig / "tolerances.yaml"
    t.write_text(t.read_text().replace("rtol: 1.0e-6", "rtol: 1.0e-3"))
    d = decide(project_run, "Verified.", monkeypatch, "reference-verified", rid)
    assert d is not None and "TOLERANCE_CHANGED_AFTER_RUN" in d["reason"]


def test_g11_new_aux_xml_in_legacy_blocks(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    (project_run.legacy / "03_INPUT" / "pop.tif.aux.xml").write_text("<PAMDataset/>")
    d = decide(project_run, "Verified.", monkeypatch, "reference-verified", rid)
    assert d is not None and "LEGACY_REF_CHANGED" in d["reason"]


def test_g11_code_drift(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    project_run.write("pipeline/s01_prep.py", "def run(ctx):\n    pass\n")
    d = decide(project_run, "Verified.", monkeypatch, "reference-verified", rid)
    assert d is not None and "CODE_CHANGED_AFTER_RUN" in d["reason"]


def test_g11_graph_projection_after_run_is_not_drift(project_run, monkeypatch):
    """e2e finding: graph.py derive / ingest-verify writing ladder+verify projections after the run is not drift."""
    rid = _verified_bundle(project_run)
    g = project_run.graph()
    g["revision"] += 2
    g["ladder"] = {"project": "reference-verified", "derived_rev": g["revision"], "blocking": []}
    for st in g["steps"]:
        st["ladder"] = {"state": "reference-verified"}
        st["verify"] = {"verdict": "PASS_EXACT", "blame": None}
    for d in g["datasets"]:
        d["verify"] = {"run_id": rid, "verdict": "PASS_EXACT", "reason_codes": []}
    project_run.set_graph(g)
    assert decide(project_run, "Verified.", monkeypatch, "reference-verified", rid) is None


def test_g11_graph_contract_change_is_drift(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    g = project_run.graph()
    g["steps"][0]["scope"] = "keep_legacy"
    project_run.set_graph(g)
    d = decide(project_run, "Verified.", monkeypatch, "reference-verified", rid)
    assert d is not None and "GRAPH_CHANGED_AFTER_RUN" in d["reason"]


def test_graph_edited_outside_apply_blocks_claims(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    assert decide(project_run, "Verified.", monkeypatch, "reference-verified", rid) is None
    project_run.set_graph_outside(project_run.graph())  # same content, bytes not written by graph.py
    d = decide(project_run, "Verified.", monkeypatch, "reference-verified", rid)
    assert d is not None and "GRAPH_EDITED_OUTSIDE_APPLY" in d["reason"]
    assert decide(project_run, "Which option do you prefer?", monkeypatch, "reference-verified", rid) is None


def test_open_high_impact_question_blocks_claim(project_run, monkeypatch):
    rid = _verified_bundle(project_run)
    g = project_run.graph()
    g["questions"].append({"id": "q02", "status": "open", "impact": {"level": "high"}})
    project_run.set_graph(g)
    d = decide(project_run, "All checks pass.", monkeypatch, "reference-verified", rid)
    assert d is not None and "q02" in d["reason"]
    assert decide(project_run, "Which option do you prefer?", monkeypatch, "reference-verified", rid) is None


# unacked graph edits (DESIGN §4.2) -------------------------------------------------------------------------------
def test_unacked_user_edits_block_without_claim(project, monkeypatch):
    project.append_edit({"seq": 4, "rev": 8, "actor": "user", "op": {"type": "add_note", "id": "s01_prep"}})
    d = decide(project, "Which option do you prefer?", monkeypatch, "generated")
    assert d is not None and "unacked" in d["reason"]
    g = project.graph()
    g["acks"]["agent_seq"] = 4
    project.set_graph(g)
    assert decide(project, "Which option do you prefer?", monkeypatch, "generated") is None


# subprocess: real ladder, 3-block cap, fail behaviour ---------------------------------------------------------------
def test_g10_real_ladder_and_three_block_cap(project):
    msg = "Migration complete, parity achieved."
    r1 = stop(project, msg)
    assert r1.rc == 0 and r1.decision == "block" and "computed status is none" in r1.reason
    r2 = stop(project, msg, active=True)
    assert r2.decision == "block"
    r3 = stop(project, msg, active=True)
    assert r3.rc == 0 and r3.out.get("decision") is None
    assert "computed status is none" in r3.out["systemMessage"] and r3.out["systemMessage"].isascii()
    r4 = stop(project, msg, active=False)  # a new turn starts counting again
    assert r4.decision == "block"


def test_no_claim_allows_silently(project):
    r = stop(project, "Here is the plan for s02. Shall I proceed?")
    assert r.rc == 0 and r.raw == ""


def test_idle_without_migration(tmp_path):
    r = run_hook("stop_gate.py", payload("Stop", cwd=str(tmp_path), last_assistant_message="All done."))
    assert r.rc == 0 and r.raw == ""


def test_transcript_fallback(project, tmp_path):
    tp = tmp_path / "t.jsonl"
    tp.write_text(json.dumps({"type": "assistant", "message": {"content": [{"type": "text",
                                                                              "text": "All checks pass."}]}}) + "\n")
    d = payload("Stop", cwd=str(project.root), transcript_path=str(tp), stop_hook_active=False)
    d.pop("last_assistant_message")
    assert run_hook("stop_gate.py", d).decision == "block"


def test_status_error_with_claim_blocks(project, monkeypatch):
    def boom(mig):
        raise RuntimeError("x")
    monkeypatch.setattr(sg, "unacked", boom)
    d = sg.decide({"last_assistant_message": "All checks pass.", "cwd": str(project.root)}, project.mig)
    assert d is not None and "could not be computed" in d["reason"]
    assert sg.decide({"last_assistant_message": "A question?", "cwd": str(project.root)}, project.mig) is None


def test_malformed_input_exits_2():
    assert run_hook("stop_gate.py", "nope").rc == 2


def test_stop_state_is_not_in_sealed_paths(project):
    stop(project, "All checks pass.")
    assert (project.mig / ".axis" / "stop_gate.json").is_file()
    add_run(project, "r20261016T100000Z-00000000")


# visual review from the current run, on a finishing message (claim or status line) --------------------------------
RID = "r20261016T100000Z-00000000"
QUESTION = "Here is the plan for s02. Shall I proceed?"
FINISH = ("axis-migrate status: sandbox-validated | run r20261016T100000Z-00000000 | 2 pass\n"
          "That is where the migration stands.")


def test_review_missing_never_blocks_a_question(project):
    add_compared_run(project, RID, ("ds.01_mid", "ds.02_more"))
    r = stop(project, QUESTION)
    assert r.rc == 0 and r.raw == ""


def test_review_missing_blocks_the_finishing_status_message(project):
    add_compared_run(project, RID, ("ds.01_mid", "ds.02_more"))
    r = stop(project, FINISH)
    assert r.rc == 0 and r.decision == "block" and r.reason.isascii()
    assert f"run {RID} has 2 compared raster/vector dataset(s) without a visual review" in r.reason
    assert "ds.01_mid, ds.02_more" in r.reason and "maplibre-gl-raster" in r.reason
    assert "plot.py review-status --run migration/runs/" + RID in r.reason


def test_review_png_or_view_receipt_allows(project):
    rd = add_compared_run(project, RID, ("ds.01_mid", "ds.02_more"))
    add_receipt(rd, "ds.01_mid", "png")
    r = stop(project, FINISH)
    assert r.decision == "block" and "ds.02_more" in r.reason and "ds.01_mid" not in r.reason.split("Look")[0]
    add_receipt(rd, "ds.02_more", "view")
    r = stop(project, FINISH)
    assert r.rc == 0 and r.raw == ""


@pytest.mark.parametrize("over,kind", [
    ({"run_id": "r20261015T100000Z-11111111"}, "png"),      # a receipt from another run
    ({"plot_sha256": "0" * 64}, "png"),                     # a plot hash that is not this run's
    ({"answer": "cannot_tell", "question_id": "Q1"}, "png"),  # not visually assessed
    ({"engine": "cog-tiler-wasm"}, "view"),                 # wrong raster engine
    ({"cells_read": [{"cell": "interior", "legacy": 99.0, "new": 10.6}]}, "view"),  # a cell it did not read
])
def test_review_that_does_not_count_still_blocks(project, over, kind):
    rd = add_compared_run(project, RID)
    add_receipt(rd, "ds.01_mid", kind, **over)
    r = stop(project, FINISH)
    assert r.decision == "block" and "ds.01_mid" in r.reason


def test_view_project_changed_after_review_blocks(project):
    rd = add_compared_run(project, RID)
    add_receipt(rd, "ds.01_mid", "view")
    assert stop(project, FINISH).raw == ""
    p = rd / "verify" / "views" / "ds.01_mid" / "worst.geolibre.json"
    p.write_text(p.read_text() + " ")
    r = stop(project, FINISH)
    assert r.decision == "block" and "ds.01_mid" in r.reason


def test_review_after_seal_in_amend_counts(project):
    import json
    rd = add_compared_run(project, RID)
    f = add_receipt(rd, "ds.01_mid", "png")
    vr = rd / "verify" / "visual-review.json"
    (rd / "amend").mkdir()
    (rd / "amend" / "visual-review.json").write_text(vr.read_text())
    vr.write_text(json.dumps({"findings": []}))
    assert f["finding_id"] and stop(project, FINISH).raw == ""


def test_only_the_latest_run_counts(project):
    add_compared_run(project, "r20261015T100000Z-11111111")          # older run never reviewed
    add_run(project, RID)                                           # latest run: not compared yet
    assert stop(project, FINISH).raw == ""
    add_compared_run(project, "r20261017T100000Z-22222222", views=False)
    r = stop(project, FINISH)
    assert r.decision == "block" and "r20261017T100000Z-22222222" in r.reason


def test_tables_and_uncompared_need_no_review(project):
    import json
    rd = add_compared_run(project, RID)
    p = rd / "verify" / "compare" / "ds.01_mid.json"
    d = json.loads(p.read_text())
    for change in ({"family": "table"}, {"verdict": "BLOCKED"}, {"checks": []}):
        p.write_text(json.dumps(dict(d, **change)))
        assert stop(project, FINISH).raw == "", change


def test_review_block_cap_and_claim_together(project):
    add_compared_run(project, RID)
    msg = "Migration complete, parity achieved."
    r1 = stop(project, msg)
    assert r1.decision == "block" and "computed status is" in r1.reason and "without a visual review" in r1.reason
    assert stop(project, FINISH, active=True).decision == "block"
    r3 = stop(project, FINISH, active=True)
    assert r3.out.get("decision") is None and r3.out["systemMessage"].isascii()
    assert "ds.01_mid" in r3.out["systemMessage"] and "not visually assessed" in r3.out["systemMessage"]
    assert stop(project, FINISH).decision == "block"              # a new turn counts again
