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
