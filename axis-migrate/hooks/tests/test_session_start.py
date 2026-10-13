"""H1 session_start: status line, guard list, unacked edits, GDAL_PAM_ENABLED=NO into $CLAUDE_ENV_FILE (G2 half)."""
from __future__ import annotations

import json

from conftest import payload, run_hook


def test_context_and_env_file(project, tmp_path):
    env_file = tmp_path / "env.sh"
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)), env={"CLAUDE_ENV_FILE": str(env_file)})
    assert r.rc == 0
    ctx = r.out["hookSpecificOutput"]["additionalContext"]
    assert r.out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert ctx.startswith("axis-migrate status:") and "guards active" in ctx and ctx.isascii()
    assert env_file.read_text().count("export GDAL_PAM_ENABLED=NO") == 1
    run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)), env={"CLAUDE_ENV_FILE": str(env_file)})
    assert env_file.read_text().count("export GDAL_PAM_ENABLED=NO") == 1  # idempotent


def test_unacked_edits_listed(project, tmp_path):
    (project.mig / "graph-edits.jsonl").write_text(
        json.dumps({"seq": 9, "rev": 8, "actor": "user", "op": {"type": "add_note"}}) + "\n")
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert "1 unacked graph edit" in r.out["hookSpecificOutput"]["additionalContext"]


def test_fails_open(project, tmp_path):
    (project.mig / "graph.json").write_text("{broken")
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert r.rc == 0


def test_idle(tmp_path):
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(tmp_path)), env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert r.rc == 0 and r.raw == "" and not (tmp_path / "e").exists()
