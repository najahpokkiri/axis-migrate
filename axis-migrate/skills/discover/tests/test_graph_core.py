"""graph.py core: init, derivation, validation (T-schema-1), ACL and concurrency (T-cc-1), ops, plan, protected."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import graph_synth as gs
import graph as G


@pytest.fixture()
def mdir(tmp_path: Path) -> Path:
    return gs.make_project(tmp_path)


def rev(m: Path) -> int:
    return G.load_graph(m)["revision"]


def node(m: Path, nid: str) -> dict:
    return G._index(G.load_graph(m))[nid][1]


def user(m: Path, ops: list[dict], base: int | None = None, rebase: bool = True) -> dict:
    return G.apply(m, ops, "user", rev(m) if base is None else base, channel="graph", rebase=rebase)


def agent(m: Path, ops: list[dict], **kw) -> dict:
    return G.apply(m, ops, "agent", rev(m), **kw)


# ------------------------------------------------------------------------------------------------ init + derivation
def test_init_derives_io_and_logs(mdir: Path):
    g = G.load_graph(mdir)
    assert g["schema"] == G.SCHEMA and g["revision"] == 1 and g["acks"] == {"agent_seq": 0}
    s01 = node(mdir, "s01_clip")
    assert s01["inputs"] == ["ds.raw", "ds.zones"] and s01["outputs"] == ["ds.a_clip"]
    assert node(mdir, "s02_mask")["inputs"] == ["ds.a_clip"]  # reads + reads_grid collapse to one input
    assert node(mdir, "ds.a_clip")["produced_by"] == {"step": "s01_clip", "op": None}
    assert node(mdir, "ds.b_mask")["consumed_by"] == ["s03_stats", "s03b_alt"]
    assert node(mdir, "ds.final")["new"] == {"path": "s03_stats/final.tif", "layer": None, "auto": True}
    assert node(mdir, "ds.c_stats")["new"]["layer"] == "c_stats"
    assert node(mdir, "ds.zones")["new"] is None and node(mdir, "ds.zones")["role"] == "input"
    assert node(mdir, "ds.alt_out")["new"] is None  # producer dropped
    assert (mdir / ".graph" / "rev" / "1.json").is_file()
    lines = (mdir / "graph-edits.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["op"]["type"] == "init"
    with pytest.raises(G.ValidationError):
        G.init(mdir, gs.draft())


def test_written_json_is_deterministic(mdir: Path):
    text = (mdir / "graph.json").read_text()
    assert text == json.dumps(json.loads(text), sort_keys=True, indent=1, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------------------------------------ T-schema-1
def _broken(mutate) -> list[str]:
    d = gs.draft()
    mutate(d)
    g = {"schema": G.SCHEMA, "revision": 1, "project": d["project"],
         "steps": [G._norm_step(s, "agent", 1) for s in d["steps"]],
         "datasets": [G._norm_dataset(x, "agent", 1) for x in d["datasets"]],
         "edges": [G._norm_edge(e, "agent", 1) for e in d["edges"]],
         "questions": [G._norm_question(q, "agent", 1) for q in d["questions"]],
         "invariants": [], "legacy_output_dirs": [], "acks": {"agent_seq": 0}}
    G.derive_structure(g)
    return G.validate_graph(g)


def test_schema_clean_draft_is_valid():
    assert _broken(lambda d: None) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda d: d["edges"].append({"from": "ds.nope", "to": "s01_clip", "kind": "reads",
                                  "evidence": [{"ref": "x.py:1"}]}), "must go from a dataset"),
    (lambda d: d["edges"].append({"from": "ds.final", "to": "s01_clip", "kind": "reads",
                                  "evidence": [{"ref": "x.py:1"}]}), "cycle"),
    (lambda d: d["steps"].append(dict(d["steps"][1])), "duplicate id"),
    (lambda d: d["steps"][1].update(scope="out_of_scope"), "scope"),
    (lambda d: d["datasets"][0].update(kind="grid"), "kind"),
    (lambda d: d["steps"][1].update(id="S1 clip"), "id must match"),
    (lambda d: d["edges"].append({"from": "s02_mask", "to": "ds.a_clip", "kind": "writes",
                                  "evidence": [{"ref": "x.py:1"}]}), "written by several steps"),
    (lambda d: d["questions"][0].update(node="s99_missing"), "unknown"),
    (lambda d: d["steps"][1].update(params=["min_area"]), "bad param id"),
    (lambda d: d["datasets"][0]["legacy"].update(path="/abs/raw.tif"), "POSIX path relative"),
    (lambda d: d["project"].update(legacy_root="/abs/legacy"), "relative to migration"),
    (lambda d: d["steps"][1].update(legacy={"source": "x.py", "lines": [[30, 10]]}), "bad line range"),
])
def test_schema_rejects(mutate, needle):
    errs = _broken(mutate)
    assert any(needle in e for e in errs), errs


def test_validate_checks_params_yaml(mdir: Path):
    (mdir / "params.yaml").write_text("version: 1\nparams:\n  - id: p.clip_nodata\n    value: 0\n")
    res = G.validate_full(mdir)
    assert any("p.min_area" in e for e in res["errors"])
    (mdir / "params.yaml").write_text("version: 1\nparams:\n  - id: p.clip_nodata\n  - id: p.min_area\n")
    assert G.validate_full(mdir)["errors"] == []
    r = gs.run_cli("validate", str(mdir))
    assert r.returncode == 0, r.stderr


def test_invalid_op_writes_nothing(mdir: Path):
    before = (mdir / "graph-edits.jsonl").read_text()
    with pytest.raises(G.ValidationError):
        agent(mdir, [{"type": "rename", "id": "s01_clip", "title": "ok"},
                     {"type": "add_edge", "from": "ds.final", "to": "s01_clip", "kind": "reads",
                      "evidence": [{"ref": "x.py:1"}]}])  # second op makes a cycle
    assert rev(mdir) == 1 and (mdir / "graph-edits.jsonl").read_text() == before
    assert node(mdir, "s01_clip")["title"] == "Clip raster"


# ------------------------------------------------------------------------------------------------ T-cc-1
def test_stale_base_rev_conflicts(mdir: Path, tmp_path: Path):
    agent(mdir, [{"type": "rename", "id": "s02_mask", "title": "Mask v2"}])
    with pytest.raises(G.ConflictError) as ei:
        G.apply(mdir, [{"type": "rename", "id": "s01_clip", "title": "x"}], "agent", 1)
    assert ei.value.revision == 2 and ei.value.conflicting_seqs == [2]
    ops = gs.write_ops(tmp_path / "o.json", [{"type": "rename", "id": "s01_clip", "title": "x"}])
    r = gs.run_cli("apply", str(mdir), "--actor", "agent", "--base-rev", "1", "--ops", str(ops), "--json")
    assert r.returncode == 3 and json.loads(r.stdout)["error"] == "conflict"


def test_user_rebase_disjoint_and_overlapping(mdir: Path):
    agent(mdir, [{"type": "rename", "id": "s02_mask", "title": "Mask v2"}])  # rev 2
    res = user(mdir, [{"type": "confirm", "id": "s03_stats"}], base=1)  # disjoint: rebased onto rev 2
    assert res["revision"] == 3 and node(mdir, "s03_stats")["review"] == "confirmed"
    with pytest.raises(G.ConflictError) as ei:  # overlapping: same field changed since base
        user(mdir, [{"type": "rename", "id": "s02_mask", "title": "mine"}], base=1)
    assert ei.value.conflicting_seqs == [2]
    with pytest.raises(G.ConflictError):  # agents never rebase
        G.apply(mdir, [{"type": "confirm", "id": "s01_clip"}], "agent", 1, rebase=True)


def test_user_owned_title_locked_against_agent(mdir: Path, tmp_path: Path):
    user(mdir, [{"type": "rename", "id": "s01_clip", "title": "Clip to outline"}])
    with pytest.raises(G.AclError) as ei:
        agent(mdir, [{"type": "rename", "id": "s01_clip", "title": "agent title"}])
    assert ei.value.code == "user_owned_field"
    with pytest.raises(G.AclError):
        agent(mdir, [{"type": "set_field", "id": "s01_clip", "field": "title", "value": "x"}])
    ops = gs.write_ops(tmp_path / "o.json", [{"type": "rename", "id": "s01_clip", "title": "agent"}])
    r = gs.run_cli("apply", str(mdir), "--actor", "agent", "--base-rev", str(rev(mdir)), "--ops", str(ops), "--json")
    assert r.returncode == 4 and json.loads(r.stdout)["error"] == "user_owned_field"
    agent(mdir, [{"type": "rename", "id": "s02_mask", "title": "fine"}])  # other nodes unaffected


def test_cli_refuses_actor_user(mdir: Path, tmp_path: Path):
    ops = gs.write_ops(tmp_path / "o.json", [{"type": "confirm", "id": "s01_clip"}])
    r = gs.run_cli("apply", str(mdir), "--actor", "user", "--base-rev", "1", "--ops", str(ops))
    assert r.returncode == 4 and "refused" in r.stderr
    assert rev(mdir) == 1


def _one_writer(args) -> int:
    mdir, i = args
    attempts = 0
    while True:
        attempts += 1
        base = rev(mdir)
        ops = json.dumps([{"type": "add_note", "id": "s01_clip", "text": f"note {i}"}])
        r = subprocess.run(["python3", "-I", str(gs.GRAPH_PY), "apply", str(mdir), "--actor", "agent",
                            "--base-rev", str(base), "--ops", "-"], input=ops, capture_output=True, text=True)
        if r.returncode == 0:
            return attempts
        assert r.returncode == 3, r.stderr
        assert attempts < 200


def test_twenty_concurrent_applies(mdir: Path):
    with ThreadPoolExecutor(max_workers=20) as ex:
        list(ex.map(_one_writer, [(mdir, i) for i in range(20)]))
    g = G.load_graph(mdir)
    assert g["revision"] == 21  # init is revision 1; the 20 applies are revisions 2..21
    assert sorted(int(p.stem) for p in (mdir / ".graph" / "rev").glob("*.json")) == list(range(1, 22))
    recs = G.read_edits(mdir)[1:]
    assert len(recs) == 20 and [r["rev"] for r in recs] == list(range(2, 22))
    assert [r["seq"] for r in recs] == list(range(2, 22))
    notes = sorted(n["text"] for n in node(mdir, "s01_clip")["notes"])
    assert notes == sorted(f"note {i}" for i in range(20))  # no lost op


# ------------------------------------------------------------------------------------------------ ACL details
def test_agent_cannot_write_projections_or_user_ops(mdir: Path):
    for op, code in [
        ({"type": "set_field", "id": "ds.b_mask", "field": "verify.verdict", "value": "PASS_EXACT"}, "verify_projection"),
        ({"type": "set_field", "id": "s01_clip", "field": "ladder", "value": {}}, "ladder_projection"),
        ({"type": "set_field", "id": "s01_clip", "field": "review", "value": "confirmed"}, "review_user_only"),
        ({"type": "confirm", "id": "s01_clip"}, "actor_not_allowed"),
        ({"type": "set_reference", "id": "ds.c_stats", "source": "equivalent", "via_dataset": "ds.alt_out"},
         "actor_not_allowed"),
        ({"type": "waive_verify", "id": "ds.b_mask", "reason": "x"}, "actor_not_allowed"),
        ({"type": "submit"}, "actor_not_allowed"),
        ({"type": "set_scope", "id": "s01_clip", "scope": "drop"}, "scope_needs_answer"),
        ({"type": "answer_question", "id": "q01", "value": "derive"}, "answer_needs_interview_verbatim"),
        ({"type": "approve_plan", "steps": "*"}, "approval_needs_interview_verbatim"),
        ({"type": "set_field", "id": "ds.zones", "field": "role", "value": "final"}, "actor_not_allowed"),
        ({"type": "set_field", "id": "s01_clip", "field": "inputs", "value": []}, "field_not_settable"),
        ({"type": "add_edge", "from": "ds.raw", "to": "s02_mask", "kind": "reads"}, None),
    ]:
        with pytest.raises(G.GraphError) as ei:
            agent(mdir, [op])
        if code:
            assert ei.value.code == code, (op, ei.value.code, ei.value.message)
        else:
            assert ei.value.code == "evidence_required"
    for op in ({"type": "add_question", "id": "q09", "node": "s01_clip", "text": "?"},
               {"type": "set_field", "id": "s01_clip", "field": "legacy.source", "value": "x.py"},
               {"type": "ack", "seq": 1}):
        with pytest.raises(G.AclError):
            user(mdir, [op])
    with pytest.raises(G.AclError):
        G.apply(mdir, [{"type": "set_verify", "id": "ds.b_mask", "verify": {"verdict": "FAIL"}}], "agent", 1)
    with pytest.raises(G.ValidationError):
        G.apply(mdir, [{"type": "set_verify", "id": "ds.b_mask", "verify": {"waiver": {"reason": "x"}}}], "verify", 1)


def test_answers_scope_and_user_owned_answers(mdir: Path):
    agent(mdir, [{"type": "answer_question", "id": "q01", "value": "derive"}], channel="interview",
          verbatim="Derive it in code please")
    q = node(mdir, "q01")
    assert q["status"] == "answered" and q["answer"]["by"] == "user" and q["answer"]["recorded_by"] == "agent"
    assert q["answer"]["verbatim"] == "Derive it in code please" and q["answered_by"] == "user (interview)"
    with pytest.raises(G.AclError) as ei:
        agent(mdir, [{"type": "answer_question", "id": "q01", "value": "read_edited"}], channel="interview",
              verbatim="x")
    assert ei.value.code == "user_owned_field"
    agent(mdir, [{"type": "set_scope", "id": "m01_zones_edit", "scope": "migrate", "question": "q01"}])
    assert node(mdir, "m01_zones_edit")["scope"] == "migrate"
    assert node(mdir, "ds.zones")["role"] == "intermediate"  # automated manual step: its output is now paired
    # dont_know -> assumed with the recommended value
    agent(mdir, [{"type": "add_question", "id": "q02", "node": "s02_mask", "kind": "unresolved_value", "text": "?",
                  "options": [{"value": "a", "label": "A", "recommended": True}, {"value": "b", "label": "B"}]}])
    user(mdir, [{"type": "answer_question", "id": "q02", "value": "dont_know"}])
    q2 = node(mdir, "q02")
    assert q2["status"] == "assumed" and q2["answer"]["assumed_value"] == "a" and q2["answer"]["channel"] == "graph"
    with pytest.raises(G.ValidationError):
        user(mdir, [{"type": "answer_question", "id": "q02", "value": "zzz"}])


def test_user_scope_lock_needs_cited_user_answer(mdir: Path):
    user(mdir, [{"type": "set_scope", "id": "s03b_alt", "scope": "keep_legacy"}])
    agent(mdir, [{"type": "add_question", "id": "q03", "node": "s03b_alt", "kind": "scope", "text": "Drop it?",
                  "options": [{"value": "drop", "label": "Drop"}, {"value": "keep", "label": "Keep"}]}])
    agent(mdir, [{"type": "assume_question", "id": "q03", "value": "drop", "reason": "budget"}])
    with pytest.raises(G.AclError) as ei:  # an agent assumption does not unlock a user-owned field
        agent(mdir, [{"type": "set_scope", "id": "s03b_alt", "scope": "drop", "question": "q03"}])
    assert ei.value.code == "user_owned_field"
    agent(mdir, [{"type": "add_question", "id": "q04", "node": "s03b_alt", "kind": "scope", "text": "Drop it?",
                  "options": [{"value": "drop", "label": "Drop"}, {"value": "keep", "label": "Keep"}]}])
    user(mdir, [{"type": "answer_question", "id": "q04", "value": "drop"}])
    agent(mdir, [{"type": "set_scope", "id": "s03b_alt", "scope": "drop", "scope_reason": "alternative_impl",
                  "question": "q04"}])
    assert node(mdir, "s03b_alt")["scope"] == "drop"


# ------------------------------------------------------------------------------------------------ editor ops
def test_delete_step_never_removes(mdir: Path):
    res = user(mdir, [{"type": "delete_step", "id": "s01_clip"}])
    s01 = node(mdir, "s01_clip")
    assert s01["scope"] == "migrate" and s01["requires_confirmation"]["datasets"] == ["ds.a_clip"]
    assert s01["requires_confirmation"]["finals"] == ["ds.final", "ds.table"]
    assert res["results"][0]["requires_confirmation"] is True
    # a user-added step without legacy outputs is simply dropped
    user(mdir, [{"type": "add_step", "id": "s05_branch", "title": "Branch", "reads": ["ds.raw"]}])
    user(mdir, [{"type": "delete_step", "id": "s05_branch"}])
    assert node(mdir, "s05_branch")["scope"] == "drop" and node(mdir, "s05_branch")["kind"] == "new"
    # the agent resolves the confirmation after asking
    agent(mdir, [{"type": "add_question", "id": "q05", "node": "s01_clip", "kind": "scope", "text": "Really drop?",
                  "options": [{"value": "drop", "label": "Drop"}, {"value": "keep", "label": "Keep"}]}])
    user(mdir, [{"type": "answer_question", "id": "q05", "value": "keep"}])
    agent(mdir, [{"type": "resolve_confirmation", "id": "s01_clip", "decision": "keep", "question": "q05"}])
    s01 = node(mdir, "s01_clip")
    assert s01["requires_confirmation"] is None and s01["scope"] == "migrate"
    assert s01["confirmations"][0]["decision"] == "keep"


def test_add_edge_reject_edge_and_notes(mdir: Path):
    user(mdir, [{"type": "add_edge", "from": "ds.zones", "to": "s02_mask", "kind": "reads"}])
    e = node(mdir, "e.ds.zones>s02_mask")
    assert e["state"] == "user_asserted" and node(mdir, "s02_mask")["inputs"] == ["ds.a_clip", "ds.zones"]
    res = user(mdir, [{"type": "reject_edge", "id": "e.ds.raw>s01_clip", "reason": "not used"}])
    e = node(mdir, "e.ds.raw>s01_clip")
    assert e["state"] == "rejected" and e["conflict"] is True
    assert res["results"][0]["code_evidence"] == ["scripts/01_clip.py:12"]
    assert node(mdir, "s01_clip")["inputs"] == ["ds.zones"]  # rejected edges do not count
    user(mdir, [{"type": "add_note", "id": "ds.b_mask", "text": "looks shifted"}])
    agent(mdir, [{"type": "add_note", "id": "ds.b_mask", "text": "checked"}])
    notes = node(mdir, "ds.b_mask")["notes"]
    assert [n["state"] for n in notes] == ["user_asserted", "ai_inferred"]


def test_set_reference_equivalent_and_orphan(mdir: Path):
    user(mdir, [{"type": "set_reference", "id": "ds.c_stats", "source": "equivalent", "via_dataset": "ds.alt_out"}])
    d = node(mdir, "ds.c_stats")
    assert d["reference"] == {"source": "equivalent", "match_method": "human", "via_dataset": "ds.alt_out",
                              "confirmed_by": "user"}
    assert node(mdir, "e.ds.alt_out>ds.c_stats#equivalent_to")["review"] == "confirmed"
    with pytest.raises(G.ValidationError):
        user(mdir, [{"type": "set_reference", "id": "ds.final", "source": "equivalent", "via_dataset": "ds.table"}])
    user(mdir, [{"type": "add_dataset", "id": "ds.extra", "kind": "raster", "title": "Extra"}])
    user(mdir, [{"type": "set_reference", "id": "ds.extra", "source": "legacy", "legacy_path": "tmp/b_mask.tif"}])
    d = node(mdir, "ds.extra")
    assert d["legacy"]["path"] == "tmp/b_mask.tif" and d["legacy"]["exists"] is True
    with pytest.raises(G.ValidationError):
        user(mdir, [{"type": "set_reference", "id": "ds.extra", "source": "legacy", "legacy_path": "../x.tif"}])


def test_waiver_and_role_override(mdir: Path):
    user(mdir, [{"type": "waive_verify", "id": "ds.b_mask", "reason": "intermediate never used"}])
    assert node(mdir, "ds.b_mask")["verify"]["waiver"]["by"] == "user"
    user(mdir, [{"type": "set_field", "id": "ds.c_stats", "field": "role", "value": "final"}])
    agent(mdir, [{"type": "rename", "id": "s02_mask", "title": "x"}])  # re-derivation keeps the override
    assert node(mdir, "ds.c_stats")["role"] == "final"


def test_automate_manual_step_from_editor(mdir: Path):
    user(mdir, [{"type": "set_scope", "id": "m01_zones_edit", "scope": "migrate"}])
    z = node(mdir, "ds.zones")
    assert z["role"] == "intermediate" and z["new"]["path"] == "m01_zones_edit/zones.gpkg"
    d = G.diff(mdir, since_rev=1)
    assert [o["code"] for o in d["obligations"]] == ["automate_manual_step"]


# ------------------------------------------------------------------------------------------------ plan
PLAN = {"summary": "Mask from clip", "legacy_ops": [{"tool": "gdal:rasterize", "ref": "scripts/02_mask.py:30-39"}],
        "oss": [{"op": "rasterio.features.rasterize", "why": "same cell rule", "skill_refs": ["F4-2"]}],
        "params": ["p.min_area"], "risks": [{"skill_ref": "F3-1", "risk": "nodata"}],
        "outputs_checked": [{"dataset": "ds.b_mask", "family": "raster", "measure": "mask",
                             "checks": ["grid", "values"], "tolerance_ids": []}],
        "isolation": {"possible": True, "legacy_inputs": ["ds.a_clip"], "missing": []}}


def test_plan_set_render_approve(mdir: Path):
    agent(mdir, [{"type": "set_plan", "id": "s02_mask", "plan": PLAN}])
    with pytest.raises(G.ValidationError):
        agent(mdir, [{"type": "set_plan", "id": "s01_clip", "plan": {**PLAN, "legacy_ops": [{"ref": "no line"}]}}])
    assert not G.plan_approved(G.load_graph(mdir), "s02_mask")
    out = G.render_plan(mdir)
    text = out.read_text()
    assert "## s02_mask · Mask" in text and "`rasterio.features.rasterize`" in text and "not yet" in text
    assert "s03b_alt" in text.split("## Not migrated")[1]
    agent(mdir, [{"type": "approve_plan", "steps": "*"}], channel="interview", verbatim="Yes, go ahead")
    g = G.load_graph(mdir)
    assert G.plan_approved(g, "s02_mask") and not G.plan_approved(g, "s01_clip")
    assert node(mdir, "s02_mask")["plan"]["approved"]["verbatim"] == "Yes, go ahead"
    res = agent(mdir, [{"type": "set_plan", "id": "s02_mask", "plan": {**PLAN, "summary": "changed"}}])
    assert res["results"][0]["approval_invalidated"] is True
    assert not G.plan_approved(G.load_graph(mdir), "s02_mask")
    assert G.render_plan(mdir).read_text() == G.render_plan(mdir).read_text()  # deterministic


# ------------------------------------------------------------------------------------------------ protected, derive
def test_protected_set(mdir: Path):
    out = G.protected(mdir)
    assert out["graph_revision"] == 1 and out["root_protected"] is True
    assert out["paths"] == ["out/final.tif", "out/table.csv", "tmp/a_clip.tif", "tmp/b_mask.tif", "tmp/c_stats.gpkg",
                            "work.gdb/alt_out"]
    assert out["input_exceptions"] == ["in/raw.tif", "in/zones.gpkg", "in/zones_raw.geojson"]
    assert json.loads((mdir / ".axis" / "protected.json").read_text()) == out
    user(mdir, [{"type": "set_scope", "id": "m01_zones_edit", "scope": "migrate"}])
    out = G.protected(mdir)  # an automated manual step's output becomes a reference, hence protected
    assert "in/zones.gpkg" in out["paths"] and "in/zones.gpkg" not in out["input_exceptions"]


def test_derive_without_ladder_and_with_fake(mdir: Path, monkeypatch):
    (mdir / "pipeline").mkdir()
    (mdir / "pipeline" / "s01_clip.py").write_text("def run(ctx):\n    raise NotImplementedError\n")
    agent(mdir, [{"type": "set_new", "id": "s01_clip", "new": {"module": "pipeline/s01_clip.py"}}])
    monkeypatch.setattr(G, "_load_ladder", lambda: (None, "absent"))
    res = G.derive(mdir)
    assert res["changed"] and "untouched" in res["note"]
    g = G.load_graph(mdir)
    assert node(mdir, "s01_clip")["new"]["code_sha256"] and g["ladder"] is None

    class Fake:
        @staticmethod
        def compute(m, run_id=None):
            return {"pipeline": {"rung": "generated", "suffix": "", "blockers": ["s02: NOT_PORTED"], "counts": {}},
                    "steps": {"s01_clip": {"rung": "generated", "disposition": "NOT_COMPARED", "caps": [],
                                           "annotations": []}}, "run_id": None, "status_line": "axis-migrate status"}
    monkeypatch.setattr(G, "_load_ladder", lambda: (Fake, None))
    res = G.derive(mdir)
    g = G.load_graph(mdir)
    assert g["ladder"]["project"] == "generated" and g["ladder"]["derived_rev"] == g["revision"]
    assert node(mdir, "s01_clip")["ladder"]["rung"] == "generated"
    assert G.read_edits(mdir)[-1]["actor"] == "derive"


# ------------------------------------------------------------------------------------------------ edits, ack, diff
def test_unacked_ack_and_diff(mdir: Path):
    assert G.unacked_user_edits(mdir) == []
    agent(mdir, [{"type": "rename", "id": "s02_mask", "title": "agent edit"}])
    user(mdir, [{"type": "add_step", "id": "s05_branch", "title": "Branch", "reads": ["ds.raw"]}])
    user(mdir, [{"type": "delete_step", "id": "s01_clip"}])
    G.apply(mdir, [{"type": "submit", "message": "two edits"}], "user", rev(mdir), channel="graph")
    un = G.unacked_user_edits(mdir)
    assert [r["op"]["type"] for r in un] == ["add_step", "delete_step", "submit"]
    assert un[-1]["rev"] == un[-2]["rev"]  # submit does not create a revision
    d = G.diff(mdir, since_seq=0, user_only=True)
    assert [r["op"]["type"] for r in d["ops"]] == ["add_step", "delete_step"]
    assert d["submitted"][0]["message"] == "two edits"
    assert {o["code"] for o in d["obligations"]} == {"investigate_new_step", "confirm_scope_change"}
    assert d["requires_confirmation"] == ["s01_clip"]
    r = gs.run_cli("ack", str(mdir), str(un[-1]["seq"]))
    assert r.returncode == 0, r.stderr
    assert G.unacked_user_edits(mdir) == [] and G.load_graph(mdir)["acks"]["agent_seq"] == un[-1]["seq"]
    r = gs.run_cli("ack", str(mdir), "999")
    assert r.returncode == 2
    r = gs.run_cli("diff", str(mdir), "--since", "1", "--json")
    assert r.returncode == 0 and json.loads(r.stdout)["schema"] == G.DIFF_SCHEMA


def test_cli_show_and_help(mdir: Path):
    r = gs.run_cli("show", str(mdir))
    assert r.returncode == 0 and "s03_stats" in r.stdout and "open questions" in r.stdout
    r = gs.run_cli("show", str(mdir), "--json")
    assert json.loads(r.stdout)["revision"] == 1
    assert gs.run_cli("--help").returncode == 0
