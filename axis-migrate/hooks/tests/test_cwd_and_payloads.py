"""Codex desktop findings (founder's laptop, CLI 0.162.0): (b) the hook process cwd may differ from stdin `cwd`;
(c) a missing, empty or unparseable PreToolUse payload must deny (exit 2), in both harness payload shapes."""
from __future__ import annotations

import json

import pytest
from conftest import PAYLOADS, payload, run_hook, tool_payload

CODEX_ENV = {"PLUGIN_ROOT": "/plugins/axis-migrate"}


def codex(name, **over):
    d = json.loads((PAYLOADS / f"codex_{name}.json").read_text())
    d.update(over)
    return d


# (b) both bases ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("swap", [False, True])
def test_bash_relative_legacy_path_from_either_base(project, swap):
    """`legacy-demo/NOTES.txt` lands in the legacy folder only from the project root; the other base is migration/."""
    root, sub = str(project.root), str(project.mig)
    stdin_cwd, proc = (sub, root) if not swap else (root, sub)
    data = tool_payload("PreToolUse", "Bash", stdin_cwd, {"command": "rm legacy-demo/NOTES.txt"})
    r = run_hook("guard_bash.py", data, proc_cwd=proc)
    assert r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason


def test_bash_same_base_unprotected_allowed(project):
    data = tool_payload("PreToolUse", "Bash", str(project.mig), {"command": "rm analysis/x.txt"})
    assert run_hook("guard_bash.py", data, proc_cwd=str(project.mig)).out is None


@pytest.mark.parametrize("swap", [False, True])
def test_write_relative_path_from_either_base(project, swap):
    root, sub = str(project.root), str(project.mig)
    stdin_cwd, proc = (sub, root) if not swap else (root, sub)
    data = tool_payload("PreToolUse", "Write", stdin_cwd, {"file_path": "legacy-demo/04_FINAL/x.csv", "content": "x"})
    r = run_hook("guard_write.py", data, proc_cwd=proc)
    assert r.decision == "deny" and "LEGACY_READ_ONLY" in r.reason


@pytest.mark.parametrize("swap", [False, True])
def test_codex_patch_relative_path_from_either_base(project, swap):
    root, sub = str(project.root), str(project.mig)
    stdin_cwd, proc = (sub, root) if not swap else (root, sub)
    p = "*** Begin Patch\n*** Add File: legacy-demo/05_TEMP/new.txt\n+x\n*** End Patch"
    data = codex("PreToolUse_apply_patch", cwd=stdin_cwd, tool_input={"command": p})
    r = run_hook("guard_write.py", data, env=CODEX_ENV, proc_cwd=proc)
    assert r.decision == "deny"


def test_codex_absolute_patch_paths(project):
    """Probe result: Codex sends ABSOLUTE paths in apply_patch."""
    p = f"*** Begin Patch\n*** Update File: {project.legacy}/NOTES.txt\n@@\n-notes\n+x\n*** End Patch"
    data = codex("PreToolUse_apply_patch", cwd=str(project.mig / "analysis"), tool_input={"command": p})
    assert run_hook("guard_write.py", data, env=CODEX_ENV).decision == "deny"


def test_migration_found_from_process_cwd(project, tmp_path):
    """stdin cwd outside the project (session root elsewhere), hook process in the project: still guarded."""
    data = tool_payload("PreToolUse", "Bash", str(tmp_path), {"command": f"rm {project.legacy}/NOTES.txt"})
    r = run_hook("guard_bash.py", data, proc_cwd=str(project.root))
    assert r.decision == "deny"


def test_post_scan_relative_path_from_process_cwd(project):
    (project.mig / "tests" / "test_a.py").write_text("from unittest.mock import patch\n\ndef test_x():\n"
                                                    "    with patch('rasterio.open'):\n        pass\n")
    data = tool_payload("PostToolUse", "Write", "/", {"file_path": "migration/tests/test_a.py"})
    r = run_hook("post_scan.py", data, proc_cwd=str(project.root))
    assert r.decision == "block" and "S12" in r.reason


# (c) missing / empty / unparseable payloads deny ---------------------------------------------------------------------
BAD = ["", "   ", "not json", "null", "[]", "{}", '{"cwd": "/tmp"}']


@pytest.mark.parametrize("script", ["guard_write.py", "guard_bash.py", "guard_hidden.py"])
@pytest.mark.parametrize("stdin", BAD)
def test_pretooluse_bad_stdin_denies(script, stdin, project):
    r = run_hook(script, stdin, proc_cwd=str(project.root))
    assert r.rc == 2 and r.err.strip() and r.err.isascii(), (script, stdin, r)


@pytest.mark.parametrize("script", ["guard_write.py", "guard_bash.py", "guard_hidden.py"])
@pytest.mark.parametrize("shape", ["claude", "codex"])
def test_pretooluse_payload_without_tool_name_denies(script, shape, project):
    d = payload("PreToolUse_Bash", cwd=str(project.root)) if shape == "claude" else \
        codex("PreToolUse_Bash", cwd=str(project.root))
    d.pop("tool_name")
    r = run_hook(script, d, env=CODEX_ENV if shape == "codex" else None, proc_cwd=str(project.root))
    assert r.rc == 2 and "tool_name" in r.err


@pytest.mark.parametrize("shape", ["claude", "codex"])
def test_bash_payload_without_command_denies(shape, project):
    d = payload("PreToolUse_Bash", cwd=str(project.root)) if shape == "claude" else \
        codex("PreToolUse_Bash", cwd=str(project.root))
    d["tool_input"] = {}
    assert run_hook("guard_bash.py", d, env=CODEX_ENV if shape == "codex" else None).rc == 2


def test_codex_apply_patch_without_patch_text_denies(project):
    d = codex("PreToolUse_apply_patch", cwd=str(project.root), tool_input={})
    assert run_hook("guard_write.py", d, env=CODEX_ENV).rc == 2


# (d) PLUGIN_DATA may not exist on disk ---------------------------------------------------------------------------------
def test_marker_dir_created_when_missing(project, tmp_path):
    data_dir = tmp_path / "codex" / "plugins" / "data" / "axis-migrate-axis-migrate-dev"
    assert not data_dir.exists()
    d = codex("SessionStart", cwd=str(project.root))
    r = run_hook("session_start.py", d, env={"PLUGIN_ROOT": "/p", "PLUGIN_DATA": str(data_dir)})
    assert r.rc == 0 and json.loads((data_dir / "hooks-active.json").read_text())["harness"] == "codex"
