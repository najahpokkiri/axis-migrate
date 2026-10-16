"""H3 guard_write (08 §5c/§5g): G1, G3, G4, G5, G6, G7, plan gate, params fitted->answer, G12, G13."""
from __future__ import annotations

from conftest import add_run, run_hook, run_ids, tool_payload


def write(pr, path, content="x", tool="Write", **ti):
    inp = {"file_path": str(path), "content": content} if tool == "Write" else dict(file_path=str(path), **ti)
    return run_hook("guard_write.py", tool_payload("PreToolUse", tool, pr.root, inp))


def edit(pr, path, old, new):
    return run_hook("guard_write.py", tool_payload("PreToolUse", "Edit", pr.root, {
        "file_path": str(path), "old_string": old, "new_string": new, "replace_all": False}))


# G1 -------------------------------------------------------------------------------------------------------------
def test_g1_legacy_write_denied(project):
    r = write(project, project.legacy / "04_FINAL" / "x.tif")
    assert r.rc == 0 and r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason and r.reason.isascii()
    r = edit(project, project.legacy / "NOTES.txt", "notes", "edited")
    assert r.decision == "deny"


def test_g1_new_input_file_denied(project):
    """08 §5f: the agent cannot 'restore' a missing input under the legacy root."""
    assert write(project, project.legacy / "03_INPUT" / "pop.tif").decision == "deny"


def test_g1_symlink_into_legacy_denied(project):
    link = project.mig / "analysis" / "link"
    try:
        link.symlink_to(project.legacy / "05_TEMP")
    except OSError:   # Windows without the symlink privilege: a junction, which any user can make
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(project.legacy / "05_TEMP")], check=True,
                       capture_output=True)
    assert write(project, link / "01_mid.tif").decision == "deny"


def test_hidden_sibling_denied(project):
    assert write(project, project.root / "legacy-demo.PROVENANCE.md").decision == "deny"


def test_analysis_write_allowed(project):
    r = write(project, project.mig / "analysis" / "probe.py", "print(1)\n")
    assert r.rc == 0 and r.out is None


# G3 / G7 / other script-owned files ----------------------------------------------------------------------------
def test_g3_runs_denied(project_run):
    rd = project_run.mig / "runs" / run_ids(project_run)[0]
    r = edit(project_run, rd / "manifest.json", "succeeded", "x")
    assert r.decision == "deny"
    assert write(project_run, project_run.mig / "runs" / "index.jsonl", "{}").decision == "deny"
    assert write(project_run, project_run.mig / "runs" / "r2" / "verify" / "visual-review.json").decision == "deny"


def test_g7_graph_json_denied(project):
    for rel in ("graph.json", "graph-edits.jsonl", ".graph/rev/1.json"):
        r = write(project, project.mig / rel, "{}")
        assert r.decision == "deny" and "graph.py apply" in r.reason


def test_script_owned_files_denied(project):
    for rel in ("inventory.json", ".axis/protected.json", "report/REPORT.json", "databricks/target-manifest.json"):
        assert write(project, project.mig / rel, "{}").decision == "deny", rel


# plan gate -------------------------------------------------------------------------------------------------------
def test_pipeline_module_needs_approved_plan(project):
    r = write(project, project.mig / "pipeline" / "s02_mask.py", "def run(ctx):\n    pass\n")
    assert r.decision == "deny" and "not approved" in r.reason
    r = write(project, project.mig / "pipeline" / "s01_prep.py", "def run(ctx):\n    pass\n")
    assert r.out is None
    r = write(project, project.mig / "pipeline" / "s09_unknown.py", "x = 1\n")
    assert r.decision == "deny" and "no graph step" in r.reason
    g = project.graph()
    g["steps"][1]["plan"]["approved"] = {"by": "user", "at": "2026-10-14T11:00:00Z"}
    project.set_graph(g)
    assert write(project, project.mig / "pipeline" / "s02_mask.py", "x = 1\n").out is None


def test_tests_and_helpers_not_plan_gated(project):
    assert write(project, project.mig / "tests" / "test_s02.py", "def test_x():\n    pass\n").out is None


# G5 append-only --------------------------------------------------------------------------------------------------
def test_g5_change_inside_block_denied(project):
    r = edit(project, project.mig / "decisions.md", "answer: derive", "answer: read_edited")
    assert r.decision == "deny" and "D-001 changed" in r.reason


def test_g5_append_allowed(project):
    cur = (project.mig / "decisions.md").read_text()
    new = cur + "\n## D-002 · Correction · 2026-10-15T10:00:00Z\n```yaml\nid: D-002\nsupersedes: D-001\n```\n"
    r = write(project, project.mig / "decisions.md", new)
    assert r.out is None


def test_g5_removal_and_reorder_denied(project):
    cur = (project.mig / "decisions.md").read_text()
    assert write(project, project.mig / "decisions.md", "# Decisions\n").decision == "deny"
    two = cur + "\n## D-002 · b\n```yaml\nid: D-002\n```\n"
    (project.mig / "decisions.md").write_text(two)
    head, d1, d2 = two.split("## ")[0], "## " + two.split("## ")[1], "## " + two.split("## ")[2]
    assert write(project, project.mig / "decisions.md", head + d2.rstrip() + "\n\n" + d1).decision == "deny"


# G6 KD owner ----------------------------------------------------------------------------------------------------
KD_HUMAN = "\n## KD-002 · Accepted change\n```yaml\nid: KD-002\nowner: human:najah\nstatus: accepted\n```\n"
KD_AGENT = "\n## KD-002 · Proposed\n```yaml\nid: KD-002\nowner: agent\nstatus: proposed\n```\n"


def test_g6_kd_human_owner_asks(project):
    cur = (project.mig / "known-differences.md").read_text()
    r = write(project, project.mig / "known-differences.md", cur + KD_HUMAN)
    assert r.decision == "ask" and "KD-002" in r.reason


def test_g6_kd_agent_owner_allowed(project):
    cur = (project.mig / "known-differences.md").read_text()
    assert write(project, project.mig / "known-differences.md", cur + KD_AGENT).out is None


def test_g6_kd_edit_existing_denied(project):
    r = edit(project, project.mig / "known-differences.md", "owner: agent", "owner: human:najah")
    assert r.decision == "deny"


# G4 tolerances -----------------------------------------------------------------------------------------------------
NEW_REC = """- id: tol.final.values.v2
  dataset: ds.final
  check: table.attr:value
  rtol: 1.0e-5
  reason: legit summation order
  correct_spread: {value: 1.0e-7}
  nearest_wrong: {value: 1.0e-3}
  agreed_by: null
"""


def test_g4_before_any_run_edit_allowed(project):
    assert edit(project, project.mig / "tolerances.yaml", "rtol: 1.0e-6", "rtol: 1.0e-5").out is None


def test_g4_after_run_edit_old_record_denied(project_run):
    r = edit(project_run, project_run.mig / "tolerances.yaml", "rtol: 1.0e-6", "rtol: 1.0e-5")
    assert r.decision == "deny" and "tol.final.values.v1" in r.reason


def test_g4_after_run_new_record_asks(project_run):
    cur = (project_run.mig / "tolerances.yaml").read_text()
    r = write(project_run, project_run.mig / "tolerances.yaml", cur + NEW_REC)
    assert r.decision == "ask" and "tol.final.values.v2" in r.reason


def test_g4_after_run_new_record_without_reason_asks(project_run):
    cur = (project_run.mig / "tolerances.yaml").read_text()
    r = write(project_run, project_run.mig / "tolerances.yaml", cur + NEW_REC.replace("  reason: legit summation order\n", ""))
    assert r.decision == "ask" and "reason" in r.reason


EMPTY_TOL = """version: 1
defaults:
  raster.values: {metric: abs+rel, atol: 0, rtol: 0, max_mismatch: 0}
records: []
"""


def _empty_records_after_run(pr):
    from conftest import add_run
    import shutil
    pr.write("tolerances.yaml", EMPTY_TOL)
    shutil.rmtree(pr.mig / "runs", ignore_errors=True)
    add_run(pr, "r20261014T120000Z-0000aaaa")


def test_g4_empty_records_first_new_id_asks(project):
    """e2e finding: `records: []` -> one new record after a run is the new-id case (ask), not a top-level change."""
    _empty_records_after_run(project)
    new = EMPTY_TOL.replace("records: []\n", "records:\n" + NEW_REC.replace("tol.final.values.v2", "tol.final.values.v1"))
    r = write(project, project.mig / "tolerances.yaml", new)
    assert r.decision == "ask" and "tol.final.values.v1" in r.reason
    r = edit(project, project.mig / "tolerances.yaml", "records: []",
             "records:\n- {id: tol.final.values.v1, dataset: ds.final, rtol: 1.0e-5, agreed_by: null}")
    assert r.decision == "ask"


def test_g4_empty_records_defaults_change_still_denied(project):
    _empty_records_after_run(project)
    r = edit(project, project.mig / "tolerances.yaml", "max_mismatch: 0}\nrecords: []", "max_mismatch: 3}\nrecords: []")
    assert r.decision == "deny" and "defaults" in r.reason


PROPOSED = """version: 1
records:
- id: tol.final.values.v1
  dataset: ds.final
  rtol: 1.0e-6
  correct_spread: {value: 0.0}
  nearest_wrong: {value: 1.0e-3}
  agreed_by: null
  agreed_at: null
"""


def test_g4_proposed_file_unsigned_allowed_any_time(project_run):
    p = project_run.mig / "tolerances.proposed.yaml"
    assert write(project_run, p, PROPOSED).out is None
    p.write_text(PROPOSED)
    assert edit(project_run, p, "rtol: 1.0e-6", "rtol: 1.0e-5").out is None
    r = run_hook("guard_bash.py", tool_payload("PreToolUse", "Bash", project_run.root, {
        "command": "cat migration/analysis/p.yaml > migration/tolerances.proposed.yaml"}))
    assert r.out is None


def test_g4_proposed_file_signed_denied(project):
    p = project.mig / "tolerances.proposed.yaml"
    r = write(project, p, PROPOSED.replace("agreed_by: null", "agreed_by: najah"))
    assert r.decision == "deny" and "Only a person signs" in r.reason
    p.write_text(PROPOSED)
    assert edit(project, p, "agreed_at: null", "agreed_at: 2026-10-14").decision == "deny"
    r = write(project, p, "version: 1\nrecords:\n- {id: tol.x.v1, rtol: 1e-6, agreed_by: najah}\n")
    assert r.decision == "deny"


def test_g4_after_run_defaults_change_denied(project_run):
    r = edit(project_run, project_run.mig / "tolerances.yaml", "max_mismatch: 0}", "max_mismatch: 5}")
    assert r.decision == "deny"


def test_tolerance_signature_asks_even_before_run(project):
    r = edit(project, project.mig / "tolerances.yaml", "agreed_by: null", "agreed_by: najah")
    assert r.decision == "ask" and "human" in r.reason


# B1 (BYU e2e): records are read with yaml.safe_load; anything not plain block style fails closed (ask) ------------
def test_b1_flow_style_records_after_run_asks(project):
    """The tester's probes (~/e2e-byu/tools/probe_flow*.json): a forged signature in a flow list must never pass."""
    _empty_records_after_run(project)
    p = project.mig / "tolerances.yaml"
    for new in ("records: [{id: t1, agreed_by: agent}]",
                'records: [{id: hectares.v1, check: "vector.attr:Hectares", atol: 0.01, agreed_by: "Najah", '
                'agreed_at: "2026-10-15T21:00:00Z", reason: "float noise"}]'):
        r = edit(project, p, "records: []", new)
        assert r.decision == "ask" and "plain block style" in r.reason, new


def test_b1_flow_style_signature_before_run_asks(project):
    p = project.write("tolerances.yaml", EMPTY_TOL)
    r = edit(project, p, "records: []", 'records: [{id: t1, atol: 0.01, agreed_by: "Najah"}]')
    assert r.decision == "ask"


def test_b1_hidden_fields_ask(project):
    """Quoted keys, a value on a continuation line, anchors/merge keys and unparseable text: never judged by guesswork."""
    _empty_records_after_run(project)
    p = project.mig / "tolerances.yaml"
    for body in ('records:\n- id: t1\n  "agreed_by": Najah\n  reason: x\n',
                 "records:\n- id: t1\n  reason: x\n  agreed_by:\n    Najah\n",
                 "base: &b {agreed_by: Najah}\nrecords:\n- id: t1\n  reason: x\n  <<: *b\n",
                 "records:\n- id: t1\n  reason: [unclosed\n"):
        r = edit(project, p, "records: []\n", body)
        assert r.decision in ("ask", "deny"), body
    # the continuation-line signature is plain block style: read by YAML it is signed, so the reason says so
    r = edit(project, p, "records: []\n", "records:\n- id: t1\n  reason: x\n  agreed_by:\n    Najah\n")
    assert r.decision == "ask" and "only a human signs" in r.reason


def test_b1_block_style_unchanged_record_allowed_by_value(project_run):
    """Records are compared by id and value: re-quoting a value of an existing record is not an edit of it."""
    p = project_run.mig / "tolerances.yaml"
    r = edit(project_run, p, "agreed_by: null", "agreed_by: ~")
    assert r.out is None


def test_b1_kd_flow_owner_asks(project):
    kd = project.mig / "known-differences.md"
    cur = kd.read_text() if kd.is_file() else ""
    block = ("\n## KD-901 forged\n\n```yaml\n{id: KD-901, kind: normalisation, owner: \"human:Najah\", "
             "rule: {round: 2}}\n```\n")
    r = write(project, kd, cur + block)
    assert r.decision == "ask"
    quoted = "\n## KD-902 forged\n\n```yaml\nid: KD-902\nkind: normalisation\n\"owner\": human:Najah\n```\n"
    assert write(project, kd, cur + quoted).decision == "ask"


def test_b1_params_confirmed_by_edit_asks(project):
    r = edit(project, project.mig / "params.yaml", "    status: assumed\n", "    status: confirmed\n")
    assert r.decision == "ask" and "p.threshold" in r.reason and "record.py answer" in r.reason


# params fitted -> answer -------------------------------------------------------------------------------------------
def test_params_fitted_to_answer_asks(project):
    r = edit(project, project.mig / "params.yaml", "    source: fitted\n", "    source: answer\n")
    assert r.decision == "ask" and "p.threshold" in r.reason


def test_params_other_edit_allowed(project):
    assert edit(project, project.mig / "params.yaml", "value: 2500", "value: 2600").out is None


# G12 / G13 ---------------------------------------------------------------------------------------------------------
def test_g12_malformed_stdin_fails_closed():
    r = run_hook("guard_write.py", "this is not json")
    assert r.rc == 2 and "failed closed" in r.err and r.err.isascii() and r.raw == ""


def test_g12_broken_graph_fails_closed(project):
    (project.mig / "graph.json").write_text("{broken")
    r = write(project, project.mig / "analysis" / "x.py")
    assert r.rc == 2


def test_g13_idle_outside_migration(tmp_path):
    r = run_hook("guard_write.py", tool_payload("PreToolUse", "Write", tmp_path, {"file_path": str(tmp_path / "x")}))
    assert r.rc == 0 and r.raw == "" and r.err == ""
    assert r.ms < 300  # wall time incl. process start under pytest; the hook itself is measured in HOOK_MECHANICS.md


def test_new_run_does_not_block_unrelated_edits(project_run):
    add_run(project_run, "r20261015T100000Z-12345678")
    assert write(project_run, project_run.mig / "narrative.md", "notes").out is None
