"""seal.py: evidence.json, ids, read-only verify/, index line, amendments, NaN refusal (07 §3.2–3.5, test 16)."""
from __future__ import annotations

import shutil
import json
import os
import subprocess
from pathlib import Path

import pytest

import axis_common as ac
import ladder
import seal as sealmod
import synth

HERE = Path(__file__).resolve().parent
AXPY = HERE.parents[2] / "scripts" / "axpy"
AXPY_CMD = ([shutil.which("sh") or "sh"] if os.name == "nt" else []) + [str(AXPY)]  # Windows cannot exec a shell script


def test_evidence_shape_and_ids(sealed):
    v = sealed["run_dir"] / "verify"
    ev = ac.load_json(v / "evidence.json")
    assert ev["schema"] == "axis-migrate.evidence.v1" and ev["parent_evidence_id"] is None
    assert set(ev) <= ladder.EVIDENCE_KEYS
    kinds = {o["kind"] for o in ev["objects"]}
    assert {"in", "ref", "code", "params", "tol", "graph", "dec", "kd", "lock", "envrt", "out", "cmp", "plot", "disp",
            "vis", "dec-e", "scan", "tests"} <= kinds
    eid = (v / "EVIDENCE_ID").read_text().strip()
    assert eid == ac.object_id("ev", ev)
    idx = [json.loads(l) for l in (sealed["migration"] / "runs" / "index.jsonl").read_text().splitlines()]
    assert idx[-1]["evidence_id"] == eid and idx[-1]["run_id"] == sealed["run_id"]
    assert idx[0].get("evidence_id") is None  # the runner's own line is kept
    # cmp objects reference this run's out:, the ref: and are content ids
    outs = {o["id"] for o in ev["objects"] if o["kind"] == "out"}
    refs = {o["id"] for o in ev["objects"] if o["kind"] == "ref"}
    for o in ev["objects"]:
        if o["kind"] == "cmp":
            assert any(r in outs for r in o["refs"]) and any(r in refs for r in o["refs"])
            assert o["id"] == ladder.cmp_content_id(ac.load_json(sealed["root"] / o["path"]))
    # no absolute paths in any hashed object
    assert str(sealed["root"]) not in json.dumps(ev)
    hist = ev["coverage"]["state_histogram"]
    assert set(hist) == set(ac.VERDICTS) and hist["PASS_EXACT"] == 4 and hist["FAIL"] == 0
    assert ev["secret_scan"]["matches"] == 0


def test_verify_is_read_only_and_seal_runs_once(sealed):
    v = sealed["run_dir"] / "verify"
    assert not os.access(v / "evidence.json", os.W_OK) or getattr(os, "geteuid", lambda: 1)() == 0
    assert not (v.stat().st_mode & 0o222)
    with pytest.raises(sealmod.SealError):
        sealmod.seal(sealed["migration"], sealed["run_id"])


def test_cli_exit_codes(proj):
    r = synth.make_run(proj, 1, seal=False, display=False)
    out = subprocess.run([*AXPY_CMD, str(HERE.parent / "seal.py"), str(proj["migration"]), "--run", r["run_id"],
                          "--json"], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["evidence_id"].startswith("ev:")
    again = subprocess.run([*AXPY_CMD, str(HERE.parent / "seal.py"), str(proj["migration"]), "--run", r["run_id"]],
                           capture_output=True, text=True)
    assert again.returncode == 2 and "already sealed" in again.stderr


def test_nan_in_compare_refused(proj):  # 07 test 16
    with pytest.raises(ac.CJSONError):
        ac.cjson({"value": float("nan")})
    r = synth.make_run(proj, 1, seal=False, display=False)
    p = r["run_dir"] / "verify" / "compare" / "ds.a_mask.json"
    doc = ac.load_json(p)
    doc["checks"][-1]["value"] = float("nan")
    p.write_text(json.dumps(doc, allow_nan=True))  # a writer that ignored the rule
    with pytest.raises(ac.CJSONError):
        sealmod.seal(proj["migration"], r["run_id"])
    assert not (r["run_dir"] / "verify" / "evidence.json").exists()


def test_amend_adds_visual_receipt_and_decision(proj):
    r = synth.make_run(proj, 1, flag="ds.a_mask")
    assert ladder.compute(proj["migration"])["steps"]["s01_mask"]["disposition"] == "NEEDS_REVIEW"
    with open(proj["migration"] / "decisions.md", "a") as fh:
        fh.write("\n## D-002 · Visual flag reviewed · 2026-10-16T15:00:00Z\n```yaml\nid: D-002\nquestion: null\n"
                 "kind: answer\nanswer: stripes are the legacy colour ramp, not data\nby: user (chat)\nparams: []\n"
                 "nodes: [s01_mask]\nclears: [vf-ds.a_mask-q1]\nsupersedes: null\nevidence: []\n```\n")
    res = sealmod.amend(proj["migration"], r["run_id"])
    assert {a["kind"] for a in res["added"]} == {"dec-e"}
    assert (r["run_dir"] / "verify" / "evidence.1.json").is_file()
    lad = ladder.compute(proj["migration"])
    assert lad["integrity"] == [] or all(x["severity"] != "incomplete" for x in lad["integrity"])
    assert lad["evidence_id"] == res["evidence_id"]
    assert lad["datasets"]["ds.a_mask"]["visual"]["cleared"] == ["vf-ds.a_mask-q1"]
    assert lad["steps"]["s01_mask"]["disposition"] == "VERIFIED"
    # B15: amending again with nothing new is not a failure: exit 0, same evidence, no new evidence file
    assert sealmod.main([str(proj["migration"]), "--run", r["run_id"], "--amend"]) == 0
    again = sealmod.amend(proj["migration"], r["run_id"])
    assert again["unchanged"] and again["evidence_id"] == res["evidence_id"] and again["added"] == []
    assert not (r["run_dir"] / "verify" / "evidence.2.json").exists()
    assert ladder.compute(proj["migration"])["evidence_id"] == res["evidence_id"]


def test_amend_visual_from_amend_folder(proj):
    r = synth.make_run(proj, 1, visual="none")
    assert ladder.compute(proj["migration"])["pipeline"]["rung"] == "sandbox-validated"
    plots = ac.load_json(r["run_dir"] / "verify" / "plots" / "ds.a_mask" / "plots.json")
    findings = []
    for ds in ("ds.a_mask", "ds.a_outline", "ds.b_exposure"):
        pj = ac.load_json(r["run_dir"] / "verify" / "plots" / ds / "plots.json")
        findings.append({"finding_id": f"vf-{ds}", "run_id": r["run_id"], "dataset_id": ds, "question_id": "Q1", "answer": "no",
                         "effect": "none", "category": "none", "plot": f"plots/{ds}/worst_tiles.png",
                         "plot_sha256": pj["png"][1]["sha256"], "note": "nothing visible"})
    assert plots
    ac.dump_json(r["run_dir"] / "amend" / "visual-review.json", {"findings": findings})
    sealmod.amend(proj["migration"], r["run_id"])
    assert ladder.compute(proj["migration"])["pipeline"]["rung"] == "reference-verified"


def test_amendment_cannot_add_other_kinds(proj):
    r = synth.make_run(proj, 1)
    v = r["run_dir"] / "verify"
    ev = ac.load_json(v / "evidence.json")
    forged = dict(ev)
    forged["parent_evidence_id"] = ac.object_id("ev", ev)
    forged["amendment"] = 1
    forged["objects"] = ev["objects"] + [{"id": "out:" + "a" * 64, "kind": "out", "path": "x", "sha256": "sha256:"
                                          + "a" * 64}]
    synth.make_writable(v)
    ac.dump_json(v / "evidence.1.json", forged)
    eid = ac.object_id("ev", forged)
    (v / "EVIDENCE_ID").write_text(eid + "\n")
    with open(proj["migration"] / "runs" / "index.jsonl", "a") as fh:
        fh.write(json.dumps({"run_id": r["run_id"], "evidence_id": eid}) + "\n")
    lad = ladder.compute(proj["migration"])
    assert "EVIDENCE_CHAIN_BROKEN" in {x["code"] for x in lad["integrity"]}
    assert lad["pipeline"]["incomplete"]


def test_evidence_id_must_be_in_index(sealed):
    idx = sealed["migration"] / "runs" / "index.jsonl"
    lines = idx.read_text().splitlines()
    idx.write_text("\n".join(l for l in lines if "evidence_id" not in l) + "\n" +
                   json.dumps({"run_id": sealed["run_id"], "evidence_id": "ev:" + "0" * 64}) + "\n")
    lad = ladder.compute(sealed["migration"])
    assert "EVIDENCE_ID_MISMATCH" in {x["code"] for x in lad["integrity"]}


def test_unknown_evidence_field_rejected(sealed):
    v = sealed["run_dir"] / "verify"
    synth.make_writable(v)
    ev = ac.load_json(v / "evidence.json")
    ev["verdict"] = "PASS"
    ac.dump_json(v / "evidence.json", ev)
    lad = ladder.compute(sealed["migration"])
    assert "EVIDENCE_SCHEMA_INVALID" in {x["code"] for x in lad["integrity"]}
    assert lad["pipeline"]["rung"] == "none"


def test_records_parse_mismatch_detected_by_report(sealed):
    import report
    v = sealed["run_dir"] / "verify"
    synth.make_writable(v)
    ev = ac.load_json(v / "evidence.json")
    ev["records"]["params"][0]["source"] = "answer"  # forged record, re-sealed consistently
    ac.dump_json(v / "evidence.json", ev)
    eid = ac.object_id("ev", ev)
    (v / "EVIDENCE_ID").write_text(eid + "\n")
    with open(sealed["migration"] / "runs" / "index.jsonl", "a") as fh:
        fh.write(json.dumps({"run_id": sealed["run_id"], "evidence_id": eid}) + "\n")
    assert ladder.compute(sealed["migration"])["pipeline"]["rung"] == "reference-verified"  # stdlib cannot see it
    code, rep = report.build(sealed["migration"])
    assert code == 2 and rep["status"] == "EVIDENCE_INCOMPLETE"
    assert "RECORDS_PARSE_MISMATCH" in {x["code"] for x in rep["integrity"]}
