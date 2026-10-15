"""record.py: decision/KD entries and `answer` (decision + params.yaml change together; review §1.6)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import params_check as pc  # noqa: E402
import record  # noqa: E402


def make(tmp_path, *, q_status="answered", asked=0, max_q=8, param_ids=("p.policy",)):
    legacy = tmp_path / "legacy"
    legacy.mkdir(parents=True)
    (legacy / "a.py").write_text("x = 1\n")
    mig = tmp_path / "migration"
    mig.mkdir()
    graph = {"schema": "axis-migrate.graph.v1", "revision": 3, "project": {"name": "t", "legacy_root": "../legacy"},
             "steps": [{"id": "s01_a", "scope": "migrate", "params": ["p.policy", "p.other"]}],
             "datasets": [{"id": "ds.out"}], "edges": [],
             "questions": [{"id": "q01", "status": q_status, "kind": "manual_step", "param_ids": list(param_ids),
                            "impact": {"datasets": ["ds.out"], "level": "high"},
                            "priority": {"impact": 3, "uncertainty": 3, "cost": 3, "score": 27}}]}
    (mig / "graph.json").write_text(json.dumps(graph))
    base = {"node": "s01_a", "kind": "manual_step", "value": None, "resolution": "unresolved", "source": "code",
            "status": "open", "reason": "a hand edit; the files cannot say what to do from now on", "evidence": [],
            "impact": {"datasets": ["ds.out"], "level": "high"}, "question": "q01"}
    params = [dict(base, id="p.policy"), dict(base, id="p.other", question=None)]
    (mig / "params.yaml").write_text(yaml.safe_dump({"version": 1, "budget": {"max_questions": max_q, "asked": asked},
                                                     "params": params}, sort_keys=False))
    return mig


def params(mig):
    return {p["id"]: p for p in yaml.safe_load((mig / "params.yaml").read_text())["params"]}


def entry(**kw):
    e = {"title": "Zone names derived in code", "kind": "answer", "answer": "derive",
         "by": "user (AskUserQuestion)", "nodes": ["s01_a"], "evidence": ["answer:q01"]}
    e.update(kw)
    return e


def test_answer_writes_decision_and_params(tmp_path):
    mig = make(tmp_path)
    res = record.answer(mig, ["p.policy"], "q01", entry(value="derive"))
    assert res["id"] == "D-001" and res["status"] == "confirmed" and res["asked"] == 1
    p = params(mig)["p.policy"]
    assert (p["source"], p["status"], p["question"], p["decisions"], p["value"]) == \
        ("answer", "confirmed", "q01", ["D-001"], "derive")
    assert params(mig)["p.other"]["status"] == "open"
    dec = pc.parse_blocks((mig / "decisions.md").read_text(), pc.DEC_HEAD_RE)
    y = dec[0]["yaml"]
    assert (y["id"], y["question"], y["params"], y["kind"]) == ("D-001", "q01", ["p.policy"], "answer")
    assert y["changes"] == {"p.policy": {"from": None, "to": "derive"}}
    f, _ = pc.check(mig, "investigate")
    assert not f.blocking


def test_dont_know_is_a_visible_assumption(tmp_path):
    mig = make(tmp_path, q_status="assumed")
    res = record.answer(mig, ["p.policy"], "q01", entry(kind="assumption", answer="dont_know", value="derive",
                                                       by="user (AskUserQuestion)"))
    p = params(mig)["p.policy"]
    assert (p["source"], p["status"]) == ("assumption", "assumed")
    assert any(w.startswith("ASSUMED_PARITY_BLIND p.policy") for w in res["warnings"])


def test_budget_counts_once_per_question_and_is_soft(tmp_path):
    mig = make(tmp_path, asked=2, max_q=2)
    res = record.answer(mig, ["p.policy"], "q01", entry())
    assert res["asked"] == 3 and any(w.startswith("BUDGET_EXCEEDED") for w in res["warnings"])
    res2 = record.answer(mig, ["p.policy"], "q01", entry(title="Restated Other answer", supersedes="D-001"))
    assert res2["id"] == "D-002" and res2["asked"] == 3
    assert params(mig)["p.policy"]["decisions"] == ["D-001", "D-002"]


def test_agent_cannot_confirm(tmp_path):
    mig = make(tmp_path)
    with pytest.raises(ValueError, match="only a person"):
        record.answer(mig, ["p.policy"], "q01", entry(by="agent"))
    assert not (mig / "decisions.md").exists()


def test_param_must_belong_to_the_question(tmp_path):
    mig = make(tmp_path)
    with pytest.raises(ValueError, match="param_ids"):
        record.answer(mig, ["p.other"], "q01", entry())


def test_validation_failure_writes_nothing(tmp_path):
    mig = make(tmp_path, q_status="open")          # graph answer_question not applied: ANSWER_NOT_FOUND
    before = (mig / "params.yaml").read_text()
    with pytest.raises(ValueError, match="ANSWER_NOT_FOUND"):
        record.answer(mig, ["p.policy"], "q01", entry())
    assert (mig / "params.yaml").read_text() == before
    assert not (mig / "decisions.md").exists()


def test_unknown_question_and_value_with_two_params(tmp_path):
    mig = make(tmp_path, param_ids=("p.policy", "p.other"))
    with pytest.raises(ValueError, match="not in graph.json"):
        record.answer(mig, ["p.policy"], "q09", entry())
    with pytest.raises(ValueError, match="exactly one"):
        record.answer(mig, ["p.policy", "p.other"], "q01", entry(value=1))
    res = record.answer(mig, ["p.policy", "p.other"], "q01", entry(values={"p.policy": "derive", "p.other": "keep"}))
    assert params(mig)["p.other"]["value"] == "keep" and res["params"] == ["p.policy", "p.other"]


def test_cli(tmp_path):
    mig = make(tmp_path)
    e = tmp_path / "entry.json"
    e.write_text(json.dumps(entry()))
    cmd = [sys.executable, str(HERE.parent / "record.py"), "answer", str(mig), "--param", "p.policy",
           "--question", "q01", "--json", str(e)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[0] == "D-001" and "p.policy confirmed; budget asked 1 of 8" in out.stdout
    bad = subprocess.run([sys.executable, str(HERE.parent / "record.py"), "answer", str(mig), "--json", str(e)],
                         capture_output=True, text=True)
    assert bad.returncode == 2
    bad2 = subprocess.run([sys.executable, str(HERE.parent / "record.py"), "decision", str(mig), "--param", "p.policy",
                           "--json", str(e)], capture_output=True, text=True)   # --param belongs to answer
    assert bad2.returncode == 2


# ---------------------------------------------------------------- one writer for params.yaml
def test_answer_resolves_so_the_runtime_accepts_it(tmp_path):
    mig = make(tmp_path)
    record.answer(mig, ["p.policy"], "q01", entry(value="derive"))
    p = params(mig)["p.policy"]
    assert p["resolution"] == "resolved" and {"ref": "answer:q01"} in p["evidence"]
    mig2 = make(tmp_path / "b", q_status="assumed")
    record.answer(mig2, ["p.policy"], "q01", entry(kind="assumption", value="derive"))
    p2 = params(mig2)["p.policy"]
    assert (p2["resolution"], p2["confidence"], p2["method"]) == ("inferred", "low", "assumption")
    assert not pc.check(mig2, "investigate")[0].blocking


def test_param_creates_and_updates(tmp_path):
    mig = make(tmp_path)
    res = record.set_param(mig, "p.cell_m", {"node": "s01_a", "kind": "cell_size", "value": 100, "unit": "m",
                                             "resolution": "resolved", "source": "code",
                                             "reason": "literal", "evidence": [{"ref": "a.py:1", "quote": "x = 1"}],
                                             "impact": {"datasets": ["ds.out"], "level": "high"}}, new=True)
    assert res["created"] and params(mig)["p.cell_m"]["value"] == 100
    assert params(mig)["p.cell_m"]["status"] == "confirmed"      # stated by the files: the script confirms it
    assert list(params(mig)["p.cell_m"])[:3] == ["id", "node", "kind"]
    record.set_param(mig, "p.cell_m", {"value": 30})
    assert params(mig)["p.cell_m"]["value"] == 30
    with pytest.raises(ValueError, match="already exists"):
        record.set_param(mig, "p.cell_m", {"value": 1}, new=True)
    with pytest.raises(ValueError, match="not in params.yaml"):
        record.set_param(mig, "p.nope", {"value": 1})


def test_param_refuses_what_belongs_to_answer_and_bad_states(tmp_path):
    mig = make(tmp_path)
    before = (mig / "params.yaml").read_text()
    with pytest.raises(ValueError, match="record.py answer"):
        record.set_param(mig, "p.policy", {"source": "answer"})
    with pytest.raises(ValueError, match="cannot set"):
        record.set_param(mig, "p.policy", {"question": "q01"})
    with pytest.raises(ValueError, match="QUESTION_ON_RESOLVED"):  # validated first: q01 asks about it
        record.set_param(mig, "p.policy", {"resolution": "resolved", "evidence": [{"ref": "a.py:1", "quote": "x = 1"}]})
    with pytest.raises(ValueError, match="not in decisions.md"):
        record.set_param(mig, "p.policy", {"decisions": ["D-009"]})
    assert (mig / "params.yaml").read_text() == before
    record.answer(mig, ["p.policy"], "q01", entry(value="derive"))
    with pytest.raises(ValueError, match="settled by q01"):
        record.set_param(mig, "p.policy", {"value": "keep"})
    record.set_param(mig, "p.policy", {"reason": "hand edit; answered in q01", "decisions": ["D-001"]})
    assert params(mig)["p.policy"]["decisions"] == ["D-001"]


def test_param_cli(tmp_path):
    mig = make(tmp_path)
    e = tmp_path / "f.json"
    e.write_text(json.dumps({"reason": "restated"}))
    out = subprocess.run([sys.executable, str(HERE.parent / "record.py"), "param", str(mig), "--param", "p.other",
                          "--json", str(e)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "p.other updated (reason)" in out.stdout


# B6 / B11 / B14 (BYU e2e) ------------------------------------------------------------------------------------------
def test_param_never_takes_confirmed_from_the_agent(tmp_path):
    mig = make(tmp_path)
    before = (mig / "params.yaml").read_text()
    with pytest.raises(ValueError, match="record.py answer"):
        record.set_param(mig, "p.other", {"status": "confirmed"})
    with pytest.raises(ValueError, match="resolution resolved needs"):      # an inference is not a file fact
        record.set_param(mig, "p.other", {"resolution": "resolved", "source": "agent_decision", "value": 0.0892,
                                          "evidence": [{"ref": "a.py:1", "quote": "x = 1"}]})
    assert (mig / "params.yaml").read_text() == before
    record.set_param(mig, "p.other", {"resolution": "inferred", "confidence": "low", "method": "unit-cell",
                                      "status": "assumed", "value": 0.0892,
                                      "evidence": [{"ref": "a.py:1", "quote": "x = 1"}]})
    assert params(mig)["p.other"]["status"] == "assumed"


def test_decision_with_question_points_to_answer(tmp_path):
    mig = make(tmp_path)
    with pytest.raises(ValueError, match="record.py answer"):
        record.append(mig, "decision", entry(question="q01"))
    assert not (mig / "decisions.md").exists()
    assert record.append(mig, "decision", entry(kind="scope")) == "D-001"     # no question: fine


def test_init_creates_params_yaml_once(tmp_path):
    mig = make(tmp_path)
    (mig / "params.yaml").unlink()
    r = subprocess.run([sys.executable, str(HERE.parent / "params_check.py"), str(mig)], capture_output=True, text=True)
    assert r.returncode != 0 and "record.py init" in r.stderr and "Traceback" not in r.stderr
    assert record.main(["init", str(mig), "--max-questions", "5"]) == 0
    doc = yaml.safe_load((mig / "params.yaml").read_text())
    assert doc["version"] == 1 and doc["budget"] == {"max_questions": 5, "asked": 0} and doc["params"] == []
    assert doc["target"]["platform"] == "databricks"
    assert record.main(["init", str(mig)]) == 2                                 # never overwrites


def test_assumption_decision_on_a_confirmed_param_blocks(tmp_path):
    """B6: the decision says assumption, params.yaml says confirmed: params_check flags it (report count honest)."""
    mig = make(tmp_path)
    data = yaml.safe_load((mig / "params.yaml").read_text())
    data["params"].append({"id": "p.cell_area_ha", "node": "s01_a", "kind": "other", "value": 0.0892,
                           "resolution": "resolved", "source": "code", "status": "confirmed", "reason": "r",
                           "evidence": [{"ref": "a.py:1", "quote": "x = 1"}]})
    (mig / "params.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    record.append(mig, "decision", {"title": "Real cell area", "kind": "assumption", "answer": "0.0892 ha",
                                    "by": "agent", "params": ["p.cell_area_ha"]})
    codes = {(i["code"], i["where"]) for i in pc.check(mig, "investigate")[0].blocking}
    assert ("ASSUMPTION_NOT_ASSUMED", "p.cell_area_ha") in codes
