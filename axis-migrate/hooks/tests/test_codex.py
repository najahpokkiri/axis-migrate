"""Codex compatibility of the same hooks (one hooks/hooks.json for both harnesses).

Payload shapes follow the Codex hook contract (https://learn.chatgpt.com/docs/hooks.md): common fields session_id,
transcript_path, cwd, hook_event_name, model (+ permission_mode); file edits arrive as tool_name "apply_patch" with the
patch text in tool_input.command; Stop carries turn_id, stop_hook_active, last_assistant_message. Fixtures:
payloads/codex_*.json (built from the docs' format; no Codex run on this host).
"""
from __future__ import annotations

import json

import pytest
from conftest import MARKER_DIR, PAYLOADS, add_run, run_hook

import recordparse as rp

CODEX_ENV = {"PLUGIN_ROOT": "/plugins/axis-migrate", "PLUGIN_DATA": MARKER_DIR}


def codex(name: str, **over) -> dict:
    d = json.loads((PAYLOADS / f"codex_{name}.json").read_text())
    d.update(over)
    return d


def patch(*sections: str) -> str:
    return "*** Begin Patch\n" + "\n".join(sections) + "\n*** End Patch"


def pre_patch(pr, text, env=None, tool="apply_patch"):
    data = codex("PreToolUse_apply_patch", cwd=str(pr.root), tool_name=tool, tool_input={"command": text})
    return run_hook("guard_bash.py" if tool == "Bash" else "guard_write.py", data, env=env or CODEX_ENV)


# --- parser -------------------------------------------------------------------------------------------------------
def test_parse_docs_shape_multi_file():
    d = codex("PreToolUse_apply_patch")
    ops = rp.parse_patch(rp.patch_texts(d["tool_name"], d["tool_input"])[0])
    assert [(o.kind, o.path, o.move_to) for o in ops] == [
        ("add", "hello.txt", None), ("update", "notes.md", None), ("delete", "old.txt", None), ("update", "a.py", "b.py")]
    assert ops[0].add_lines == ["hello"]


def test_apply_update_anchor_context_and_eof():
    cur = "## Notes\nold line\nkeep\n"
    assert rp.apply_patch_update(cur, [{"anchor": "## Notes", "lines": ["-old line", "+new line"], "eof": False}]) \
        == "## Notes\nnew line\nkeep\n"
    assert rp.apply_patch_update(cur, [{"anchor": "", "lines": [" keep", "+tail"], "eof": True}]) \
        == "## Notes\nold line\nkeep\ntail\n"
    with pytest.raises(rp.PatchError):
        rp.apply_patch_update(cur, [{"anchor": "", "lines": ["-missing"], "eof": False}])


@pytest.mark.parametrize("bad", ["*** Add File: x\n+1\n*** End Patch", "*** Begin Patch\n*** Add File: x\n+1",
                                 "*** Begin Patch\n*** Add File: x\nno plus\n*** End Patch"])
def test_parse_errors(bad):
    with pytest.raises(rp.PatchError):
        rp.parse_patch(bad)


# --- guard_write via apply_patch ----------------------------------------------------------------------------------
def test_add_into_legacy_denied(project):
    r = pre_patch(project, patch("*** Add File: legacy-demo/05_TEMP/new.tif", "+x"))
    assert r.rc == 0 and r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason


def test_multi_file_one_bad_target_denies(project):
    r = pre_patch(project, patch("*** Add File: migration/analysis/a.py", "+print(1)",
                                 "*** Update File: legacy-demo/NOTES.txt", "@@", "-notes", "+edited"))
    assert r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason


def test_allowed_patch(project):
    r = pre_patch(project, patch("*** Add File: migration/analysis/a.py", "+print(1)",
                                 "*** Add File: migration/pipeline/s01_prep.py", "+def run(ctx):", "+    pass"))
    assert r.rc == 0 and r.out is None


def test_plan_gate_and_graph_and_runs(project_run):
    assert pre_patch(project_run, patch("*** Add File: migration/pipeline/s02_mask.py", "+x = 1")).decision == "deny"
    assert pre_patch(project_run, patch("*** Update File: migration/graph.json", "@@", '-  "revision": 7,',
                                        '+  "revision": 8,')).decision == "deny"
    assert pre_patch(project_run, patch("*** Update File: migration/analysis/x.py", "*** Move to: migration/runs/x.py",
                                        "@@", " a")).decision == "deny"


def test_append_only_append_allowed_edit_denied(project):
    ok = patch("*** Update File: migration/decisions.md", "@@", " evidence: [\"NOTES.txt:1\"]", " ```",
               "+", "+## D-002 · More · 2026-10-15T10:00:00Z", "+```yaml", "+id: D-002", "+```")
    assert pre_patch(project, ok).out is None
    bad = patch("*** Update File: migration/decisions.md", "@@", "-answer: derive", "+answer: read_edited")
    r = pre_patch(project, bad)
    assert r.decision == "deny" and "D-001 changed" in r.reason


def test_unapplicable_hunk_fails_closed(project):
    r = pre_patch(project, patch("*** Update File: migration/decisions.md", "@@", "-this line is not there", "+x"))
    assert r.decision == "deny" and "cannot apply the patch" in r.reason and "fail closed" in r.reason


def test_delete_or_move_append_only_denied(project):
    assert pre_patch(project, patch("*** Delete File: migration/decisions.md")).decision == "deny"
    r = pre_patch(project, patch("*** Update File: migration/known-differences.md",
                                 "*** Move to: migration/kd-old.md", "@@", " # Known differences"))
    assert r.decision == "deny" and "KD-001 removed" in r.reason


def test_kd_human_owner_ask_becomes_deny_under_codex(project):
    p = patch("*** Update File: migration/known-differences.md", "@@", " status: proposed", " ```", "+",
              "+## KD-002 · Accepted", "+```yaml", "+id: KD-002", "+owner: human:najah", "+```")
    r = pre_patch(project, p)
    assert r.decision == "deny" and "Codex has no permission prompt" in r.reason and "KD-002" in r.reason


def test_tolerance_new_record_after_run(project_run):
    p = patch("*** Update File: migration/tolerances.yaml", "@@", "   agreed_at: null",
              "+- id: tol.final.values.v2", "+  dataset: ds.final", "+  rtol: 1.0e-5", "+  reason: x",
              "+  agreed_by: null")
    r = pre_patch(project_run, p)
    assert r.decision == "deny" and "tol.final.values.v2" in r.reason and "Codex" in r.reason
    edit_old = patch("*** Update File: migration/tolerances.yaml", "@@", "-  rtol: 1.0e-6", "+  rtol: 1.0e-5")
    r = pre_patch(project_run, edit_old)
    assert r.decision == "deny" and "TOLERANCE_CHANGED_AFTER_RUN" in r.reason


def test_codex_detected_from_payload_without_env(project_run):
    """tool_name apply_patch alone marks Codex: an ask must still become a deny."""
    p = patch("*** Update File: migration/tolerances.yaml", "@@", "   agreed_at: null",
              "+- id: tol.final.values.v2", "+  reason: x")
    r = pre_patch(project_run, p, env={"PLUGIN_ROOT": ""})
    assert r.decision == "deny"


def test_proposed_tolerances_via_patch(project):
    good = patch("*** Add File: migration/tolerances.proposed.yaml", "+version: 1", "+records:",
                 "+- id: tol.x.v1", "+  agreed_by: null")
    assert pre_patch(project, good).out is None
    bad = patch("*** Add File: migration/tolerances.proposed.yaml", "+version: 1", "+records:",
                "+- id: tol.x.v1", "+  agreed_by: najah")
    assert pre_patch(project, bad).decision == "deny"


def test_malformed_apply_patch_fails_closed(project):
    r = pre_patch(project, "*** Begin Patch\n*** Add File: x.txt\n+no end marker")
    assert r.rc == 2 and "failed closed" in r.err


def test_bash_heredoc_patch(project):
    cmd = "apply_patch <<'EOF'\n" + patch("*** Add File: legacy-demo/03_INPUT/new.csv", "+a,b") + "\nEOF"
    r = pre_patch(project, cmd, tool="Bash")
    assert r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason
    ok = "apply_patch <<'EOF'\n" + patch("*** Add File: migration/analysis/n.py", "+x = 1") + "\nEOF"
    assert pre_patch(project, ok, tool="Bash").out is None


# --- post_scan via apply_patch -----------------------------------------------------------------------------------
def test_post_scan_scans_every_touched_file(project):
    (project.mig / "tests" / "test_a.py").write_text("from unittest.mock import patch\n\ndef test_x():\n"
                                                    "    with patch('rasterio.open'):\n        pass\n")
    (project.mig / "pipeline" / "s01_prep.py").write_text("def run(ctx):\n    return ctx.param('p.min_area')\n")
    data = codex("PostToolUse_apply_patch", cwd=str(project.root), tool_input={"command": patch(
        "*** Add File: migration/tests/test_a.py", "+...", "*** Update File: migration/pipeline/s01_prep.py", "@@",
        " def run(ctx):", "*** Delete File: migration/analysis/gone.py")})
    r = run_hook("post_scan.py", data, env=CODEX_ENV)
    assert r.rc == 0 and r.decision == "block" and "tests/test_a.py" in r.reason and "S12" in r.reason
    assert "s01_prep.py line" not in r.reason


def test_post_scan_bash_without_patch_is_silent(project):
    data = codex("PreToolUse_Bash", cwd=str(project.root), hook_event_name="PostToolUse")
    r = run_hook("post_scan.py", data, env=CODEX_ENV)
    assert r.rc == 0 and r.raw == ""


# --- read coverage for Codex (no Read/Grep/Glob tools: reads go through the shell) ---------------------------------
@pytest.mark.parametrize("cmd", [
    "cat legacy-demo.PROVENANCE.md", "head -3 legacy-demo.PROVENANCE.md", "tail -n 2 legacy-demo.PROVENANCE.md",
    "less legacy-demo.PROVENANCE.md", "sed -n 1,5p legacy-demo.build/build.py", "grep -n x legacy-demo.PROVENANCE.md",
    "rg answer", "find . -name '*.md'",
    "python3 -c \"print(open('legacy-demo.PROVENANCE.md').read())\"",
    "python3 - <<'EOF'\nimport glob; print(glob.glob('*PROVENANCE*'))\nEOF",
])
def test_hidden_reads_through_the_shell(project, cmd):
    env = dict(CODEX_ENV, AXIS_MIGRATE_HIDDEN_GLOBS="*.PROVENANCE.md:*.build/**")
    data = codex("PreToolUse_Bash", cwd=str(project.root), tool_input={"command": cmd})
    r = run_hook("guard_bash.py", data, env=env)
    assert r.decision == "deny", (cmd, r.out)
    assert run_hook("guard_bash.py", data, env=CODEX_ENV).out is None or "find" in cmd or "rg" in cmd


# --- Stop under Codex -------------------------------------------------------------------------------------------
def test_stop_codex_block_cap_and_json_on_allow(project):
    def stop(active, msg="Migration complete, parity achieved."):
        return run_hook("stop_gate.py", codex("Stop", cwd=str(project.root), last_assistant_message=msg,
                                              stop_hook_active=active), env=CODEX_ENV)
    assert stop(False).decision == "block"
    assert stop(True).decision == "block"
    r3 = stop(True)
    assert r3.out.get("decision") is None and "systemMessage" in r3.out
    ok = stop(False, "Which option do you prefer?")
    assert ok.rc == 0 and ok.out == {}


# --- SessionStart marker -------------------------------------------------------------------------------------------
def _marker():
    return json.loads((__import__("pathlib").Path(MARKER_DIR) / "hooks-active.json").read_text())


def test_marker_codex_and_claude(project):
    import hashlib
    import os
    r = run_hook("session_start.py", codex("SessionStart", cwd=str(project.root)), env=CODEX_ENV)
    assert r.rc == 0 and "Codex" in r.out["hookSpecificOutput"]["additionalContext"]
    m = _marker()
    assert m["schema"] == "axis-migrate.hooks-active.v1" and m["harness"] == "codex" and m["migration_found"]
    assert m["cwd_sha256"] == hashlib.sha256(os.path.realpath(project.root).encode()).hexdigest()
    run_hook("session_start.py", {"cwd": str(project.root), "hook_event_name": "SessionStart", "source": "startup",
                                  "session_id": "c1"}, env={"CLAUDE_ENV_FILE": "/dev/null"})
    assert _marker()["harness"] == "claude"


def test_marker_written_before_migration_exists(tmp_path):
    r = run_hook("session_start.py", codex("SessionStart", cwd=str(tmp_path)), env=CODEX_ENV)
    assert r.rc == 0 and r.raw == ""
    assert _marker()["migration_found"] is False


def test_hooks_active_check_cli(project, tmp_path):
    import subprocess
    from conftest import GUARD, SYS_PY
    run_hook("session_start.py", codex("SessionStart", cwd=str(project.root)), env=CODEX_ENV)
    env = {"PLUGIN_DATA": MARKER_DIR, "PATH": "/usr/bin:/bin"}
    ok = subprocess.run([SYS_PY, "-I", str(GUARD / "hooks_active.py"), "--cwd", str(project.root), "--json"],
                        capture_output=True, text=True, env=env)
    assert ok.returncode == 0 and json.loads(ok.stdout)["active"] is True
    other = subprocess.run([SYS_PY, "-I", str(GUARD / "hooks_active.py"), "--cwd", str(tmp_path)],
                           capture_output=True, text=True, env=env)
    assert other.returncode == 1
