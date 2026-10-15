"""Synthetic tests for params_check.py (no fixture facts)."""
import json
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import params_check as pc  # noqa: E402

SCRIPT = "THRESH = 40   # metres\nout = buffer(src, THRESH)\n"


def make(tmp_path, params, *, pipeline=None, questions=None, decisions=None):
    legacy = tmp_path / "legacy"
    (legacy / "scripts").mkdir(parents=True)
    (legacy / "scripts" / "a.py").write_text(SCRIPT)
    mig = tmp_path / "migration"
    mig.mkdir()
    graph = {"schema": "axis-migrate.graph.v1", "revision": 1,
             "project": {"name": "t", "legacy_root": "../legacy"},
             "steps": [{"id": "s01_a", "scope": "migrate", "params": [p["id"] for p in params]}],
             "datasets": [{"id": "ds.out"}], "edges": [], "questions": questions or []}
    (mig / "graph.json").write_text(json.dumps(graph))
    (mig / "params.yaml").write_text(yaml.safe_dump({"version": 1, "budget": {"max_questions": 8, "asked": 0},
                                                     "params": params}))
    if pipeline is not None:
        (mig / "pipeline").mkdir()
        (mig / "pipeline" / "s01_a.py").write_text(pipeline)
    if decisions is not None:
        (mig / "decisions.md").write_text(decisions)
    return mig


def good_param(**kw):
    p = {"id": "p.thresh_m", "node": "s01_a", "kind": "distance", "value": 40, "unit": "m",
         "resolution": "resolved", "source": "code", "status": "confirmed", "reason": "literal in script",
         "evidence": [{"ref": "scripts/a.py:1", "quote": "THRESH = 40   # metres"}],
         "impact": {"datasets": ["ds.out"], "level": "high"}, "used_by": []}
    p.update(kw)
    return p


def codes(f):
    return {i["code"] for i in f.items if i["severity"] == "block"}


def test_clean_investigate(tmp_path):
    f, _ = pc.check(make(tmp_path, [good_param()]), "investigate")
    assert not f.blocking


def test_quote_must_be_in_cited_line(tmp_path):
    p = good_param(evidence=[{"ref": "scripts/a.py:2", "quote": "THRESH = 40"}])
    f, _ = pc.check(make(tmp_path, [p]), "investigate")
    assert "EVIDENCE_QUOTE_MISMATCH" in codes(f)


def test_cited_file_must_exist(tmp_path):
    p = good_param(evidence=[{"ref": "scripts/nope.py:1", "quote": "x"}])
    assert "EVIDENCE_FILE_MISSING" in codes(pc.check(make(tmp_path, [p]), "investigate")[0])


def test_unit_required_and_inferred_needs_confidence(tmp_path):
    p = good_param(unit=None, resolution="inferred", status="confirmed")
    c = codes(pc.check(make(tmp_path, [p]), "investigate")[0])
    assert {"UNIT_MISSING", "INFERRED_CONFIDENCE", "INFERRED_METHOD"} <= c


def test_question_on_resolved_param_blocks(tmp_path):
    q = [{"id": "q01", "status": "open", "kind": "unresolved_value", "param_ids": ["p.thresh_m"]}]
    assert "QUESTION_ON_RESOLVED" in codes(pc.check(make(tmp_path, [good_param()], questions=q), "investigate")[0])


def test_wired_requires_reader(tmp_path):
    f, _ = pc.check(make(tmp_path, [good_param()], pipeline="PARAMS=[]\ndef run(ctx):\n    pass\n"), "wired")
    assert "PARAM_UNWIRED" in codes(f)


def test_wired_ok_after_fill(tmp_path):
    code = 'PARAMS = ["p.thresh_m"]\ndef run(ctx):\n    d = ctx.param("p.thresh_m")\n'
    mig = make(tmp_path, [good_param()], pipeline=code)
    pc.check(mig, "investigate", fill_used_by=True)
    data = yaml.safe_load((mig / "params.yaml").read_text())
    assert data["params"][0]["used_by"] == ["pipeline/s01_a.py"]
    f, _ = pc.check(mig, "wired")
    assert not f.blocking, f.items


def test_unknown_and_undeclared_param_in_code(tmp_path):
    code = 'PARAMS = ["p.thresh_m"]\ndef run(ctx):\n    ctx.param("p.thresh_m"); ctx.param("p.typo")\n'
    mig = make(tmp_path, [good_param(used_by=["pipeline/s01_a.py"])], pipeline=code)
    assert "PARAM_UNKNOWN" in codes(pc.check(mig, "wired")[0])


def test_literal_duplicate_warns(tmp_path):
    code = 'PARAMS = ["p.thresh_m"]\ndef run(ctx):\n    ctx.param("p.thresh_m"); x = 40\n'
    mig = make(tmp_path, [good_param(used_by=["pipeline/s01_a.py"])], pipeline=code)
    f, _ = pc.check(mig, "wired")
    assert any(i["code"] == "PARAM_LITERAL" for i in f.items)


def test_answer_source_needs_answered_question(tmp_path):
    p = good_param(source="answer", question="q01")
    assert "ANSWER_NOT_FOUND" in codes(pc.check(make(tmp_path, [p]), "investigate")[0])


def test_decisions_format(tmp_path):
    good = "## D-001 · Title · 2026-10-14T10:00:00Z\n```yaml\nid: D-001\nkind: answer\nparams: [p.thresh_m]\n```\n"
    bad = good + "## D-002 · Missing yaml · 2026-10-14T10:01:00Z\ntext only\n"
    assert not pc.check(make(tmp_path, [good_param()], decisions=good), "investigate")[0].blocking
    assert "DECISION_FORMAT" in codes(pc.check(make(tmp_path / "b", [good_param()], decisions=bad), "investigate")[0])


def test_cli_exit_codes(tmp_path):
    mig = make(tmp_path, [good_param()])
    assert pc.main([str(mig)]) == 0
    assert pc.main([str(mig), "--stage", "wired"]) == 2
    assert pc.main([str(tmp_path / "missing")]) == 3


def test_malformed_decision_can_be_superseded(tmp_path):
    bad = ("## D-001 · Broken · 2026-10-14T10:00:00Z\n```yaml\nid: D-001\nchanges:\n  - a {b: c} d: e\n    f\n```\n")
    mig = make(tmp_path, [good_param()], decisions=bad)
    assert "DECISION_FORMAT" in codes(pc.check(mig, "investigate")[0])
    import record
    nid = record.append(mig, "decision", {"title": "Fix D-001", "kind": "agent_decision", "by": "agent",
                                          "supersedes": "D-001", "changes": ["a {b: c} d: e f"]})
    assert nid == "D-002"
    f, _ = pc.check(mig, "investigate")
    assert not f.blocking, f.items
    assert any(i["code"] == "DECISION_FORMAT_SUPERSEDED" for i in f.items)


def test_record_refuses_human_kd_owner_and_round_trips(tmp_path):
    import record
    mig = make(tmp_path, [good_param()])
    with pytest.raises(ValueError):
        record.append(mig, "kd", {"title": "x", "kind": "normalisation", "owner": "human:someone"})
    nid = record.append(mig, "kd", {"title": "Raise on unmatched key", "kind": "legacy_defect_preserved",
                                    "datasets": ["ds.out"], "checks": [], "rule": None, "bound": {"metric": "x", "max": 0},
                                    "reason": "zero effect: {a: b} quoted safely", "evidence": ["scripts/a.py:1"]})
    assert nid == "KD-001"
    f, _ = pc.check(mig, "investigate")
    assert not f.blocking, f.items


def warns(f):
    return {i["code"] for i in f.items if i["severity"] == "warn"}


def test_budget_is_a_soft_limit(tmp_path):
    mig = make(tmp_path, [good_param()])
    doc = yaml.safe_load((mig / "params.yaml").read_text())
    doc["budget"] = {"max_questions": 2, "asked": 5}
    (mig / "params.yaml").write_text(yaml.safe_dump(doc))
    f, _ = pc.check(mig, "investigate")
    assert not f.blocking and "BUDGET_EXCEEDED" in warns(f)


def test_impact_level_enum(tmp_path):
    f, _ = pc.check(make(tmp_path, [good_param(impact={"datasets": ["ds.out"], "level": "High"})]), "investigate")
    assert "ENUM" in codes(f)


def test_assumed_parity_blind_is_warned(tmp_path):
    p = good_param(id="p.policy", kind="manual_step", resolution="inferred", confidence="low", method="assumption", source="assumption",
                   status="assumed", evidence=[{"ref": "answer:q01"}], unit=None)
    f, _ = pc.check(make(tmp_path, [p]), "investigate")
    assert not f.blocking and "ASSUMED_PARITY_BLIND" in warns(f)
    q = good_param(id="p.policy", kind="threshold", resolution="inferred", confidence="low", method="assumption", source="assumption",
                   status="assumed", evidence=[{"ref": "answer:q01"}], unit=None)
    f2, _ = pc.check(make(tmp_path / "b", [q]), "investigate")
    assert "ASSUMED_PARITY_BLIND" not in warns(f2)


def test_assumed_without_impact_level_is_warned(tmp_path):
    p = good_param(kind="threshold", resolution="inferred", confidence="low", method="assumption",
                   source="assumption", status="assumed", evidence=[{"ref": "answer:q01"}],
                   impact={"datasets": ["ds.out"]})
    assert "ASSUMED_NO_IMPACT_LEVEL" in warns(pc.check(make(tmp_path, [p]), "investigate")[0])


# ---------------------------------------------------------------- params_check agrees with the runtime (Lux e2e, 15 Oct)
RUNTIME = Path(__file__).resolve().parents[2] / "migrate" / "runtime"


def runtime_refuses(mig, pid, declared) -> bool:
    sys.path.insert(0, str(RUNTIME))
    import _axis_runtime as rt
    ctx = rt.Ctx(step_id="s01_a", inputs={}, outputs={}, params_file=str(mig / "params.yaml"),
                 declared_params=declared, scratch_dir=str(mig / ".scratch"))
    try:
        ctx.param(pid)
        return False
    except rt.ParamMissing:
        return True


ANSWERED_UNRESOLVED = dict(resolution="unresolved", source="answer", status="confirmed", question="q01",
                           evidence=[], value=40)
Q_ANSWERED = [{"id": "q01", "status": "answered", "kind": "unresolved_value", "param_ids": ["p.thresh_m"]}]
READER = 'PARAMS = ["p.thresh_m"]\ndef run(ctx):\n    ctx.param("p.thresh_m")\n'


def test_answered_but_unresolved_blocks_like_the_runtime(tmp_path):
    """The Lux run: params_check passed '0 blocking', then run_local refused with PARAM_MISSING (exit 12)."""
    mig = make(tmp_path, [good_param(**ANSWERED_UNRESOLVED)], questions=Q_ANSWERED)
    assert runtime_refuses(mig, "p.thresh_m", ["p.thresh_m"])
    f, _ = pc.check(mig, "investigate")
    assert "PARAM_UNRESOLVED_AFTER_ANSWER" in codes(f)
    resolved = make(tmp_path / "ok", [good_param(**dict(ANSWERED_UNRESOLVED, resolution="resolved",
                                                          evidence=[{"ref": "answer:q01"}]))], questions=Q_ANSWERED)
    assert not runtime_refuses(resolved, "p.thresh_m", ["p.thresh_m"])
    assert not pc.check(resolved, "investigate")[0].blocking


def test_wired_mirrors_every_runtime_refusal(tmp_path):
    def wired(sub, param, step_params=None):
        mig = make(tmp_path / sub, [param], pipeline=READER)
        g = json.loads((mig / "graph.json").read_text())
        g["steps"][0]["new"] = {"module": "pipeline/s01_a.py"}
        if step_params is not None:
            g["steps"][0]["params"] = step_params
        (mig / "graph.json").write_text(json.dumps(g))
        pc.check(mig, "wired", fill_used_by=True)
        f, _ = pc.check(mig, "wired")
        return mig, f, g["steps"][0]["params"]

    mig, f, decl = wired("open", good_param(resolution="unresolved", source="code", status="open", evidence=[],
                                            question="q01"))
    assert runtime_refuses(mig, "p.thresh_m", decl) and "PARAM_MISSING_AT_RUN" in codes(f)
    mig, f, decl = wired("undeclared", good_param(), step_params=[])
    assert runtime_refuses(mig, "p.thresh_m", decl) and "PARAM_MISSING_AT_RUN" in codes(f)
    mig, f, decl = wired("fine", good_param())
    assert not runtime_refuses(mig, "p.thresh_m", decl) and "PARAM_MISSING_AT_RUN" not in codes(f)


# --- BYU dry run: NO_GRAPH, PARAM_COVERAGE, XML-escaped quotes ----------------------------------------------------
def test_no_graph_is_a_clean_block(tmp_path):
    mig = tmp_path / "migration"
    mig.mkdir()
    f, _ = pc.check(mig, "investigate")
    assert [i["code"] for i in f.blocking] == ["NO_GRAPH"]


def _coverage_project(tmp_path):
    import json as _json
    import yaml as _yaml
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "a.py").write_text("\n".join(f"line {i}" for i in range(1, 21)) + "\n")
    (legacy / "m.model3").write_text('<x>\n<v value="amen_group( &quot;Amenity&quot;)"/>\n</x>\n')
    mig = tmp_path / "migration"
    mig.mkdir()
    graph = {"legacy_root": str(legacy), "steps": [
        {"id": "s01", "scope": "migrate", "legacy": {"source": "a.py", "lines": [[1, 10]]}, "params": []},
        {"id": "s02", "scope": "drop", "legacy": {"source": "a.py", "lines": [[11, 20]]}, "params": []}],
        "datasets": [], "questions": []}
    (mig / "graph.json").write_text(_json.dumps(graph))
    inv = {"settings": [{"id": "set.s01.op1.dtype", "file": "a.py", "line": 3},
                        {"id": "set.s02.op1.dtype", "file": "a.py", "line": 12}],
           "params": [{"id": "p.s01.op1.OPTS", "kind": "tool_literal", "file": "a.py", "line": 5},
                      {"id": "p.s01.BASE", "kind": "assignment", "file": "a.py", "line": 1}]}
    (mig / "inventory.json").write_text(_json.dumps(inv))
    (mig / "params.yaml").write_text(_yaml.safe_dump({"version": 1, "params": []}))
    return mig


def test_param_coverage_blocks_until_cited_or_waived(tmp_path, monkeypatch):
    import record
    mig = _coverage_project(tmp_path)
    monkeypatch.setattr(pc.ac, "legacy_root", lambda m, g: tmp_path / "legacy")
    f, _ = pc.check(mig, "investigate")
    cov = [i for i in f.blocking if i["code"] == "PARAM_COVERAGE"]
    assert len(cov) == 1 and cov[0]["where"] == "s01"                      # dropped step s02 not counted
    assert "set.s01.op1.dtype" in cov[0]["message"] and "p.s01.op1.OPTS" in cov[0]["message"]
    assert "p.s01.BASE" not in cov[0]["message"]                           # plain assignments are not settings
    record.waive(mig, ["p.s01.op1.OPTS"], "creation option: compression only")
    f, _ = pc.check(mig, "investigate")
    cov = [i for i in f.blocking if i["code"] == "PARAM_COVERAGE"]
    assert len(cov) == 1 and "OPTS" not in cov[0]["message"]
    import yaml as _yaml
    d = _yaml.safe_load((mig / "params.yaml").read_text())
    d["params"] = [{"id": "p.dtype", "node": "s01", "kind": "dtype", "value": "Int32", "resolution": "resolved",
                    "source": "code", "status": "confirmed", "reason": "r",
                    "evidence": [{"ref": "a.py:2-4", "quote": "line 3"}]}]
    (mig / "params.yaml").write_text(_yaml.safe_dump(d))
    f, _ = pc.check(mig, "investigate")
    assert not [i for i in f.blocking if i["code"] == "PARAM_COVERAGE"]
    d["not_output_changing"].append({"id": "set.nope", "reason": "x"})
    (mig / "params.yaml").write_text(_yaml.safe_dump(d))
    f, _ = pc.check(mig, "investigate")
    assert [i["code"] for i in f.blocking] == ["WAIVER_UNKNOWN"]


def test_xml_quote_decoded_or_raw(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "m.model3").write_text('<x>\n<v value="amen_group( &quot;Amenity&quot;)"/>\n</x>\n')
    for quote in ('amen_group( "Amenity")', "amen_group( &quot;Amenity&quot;)"):
        f = pc.Findings()
        pc.check_quote(legacy, "m.model3:2", quote, f, "p.x")
        assert not f.blocking, quote
    f = pc.Findings()
    pc.check_quote(legacy, "m.model3:2", 'other( "Amenity")', f, "p.x")
    assert [i["code"] for i in f.blocking] == ["EVIDENCE_QUOTE_MISMATCH"]
