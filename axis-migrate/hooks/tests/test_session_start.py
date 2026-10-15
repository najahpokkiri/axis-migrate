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
    project.append_edit({"seq": 9, "rev": 8, "actor": "user", "op": {"type": "add_note"}})
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert "1 unacked graph edit" in r.out["hookSpecificOutput"]["additionalContext"]


def test_graph_edited_outside_apply_warned(project, tmp_path):
    env = {"CLAUDE_ENV_FILE": str(tmp_path / "e")}
    ok = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)), env=env)
    assert "GRAPH_EDITED_OUTSIDE_APPLY" not in ok.out["hookSpecificOutput"]["additionalContext"]
    project.set_graph_outside(project.graph())
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)), env=env)
    assert r.rc == 0 and "GRAPH_EDITED_OUTSIDE_APPLY" in r.out["hookSpecificOutput"]["additionalContext"]


def test_fails_open(project, tmp_path):
    (project.mig / "graph.json").write_text("{broken")
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert r.rc == 0


def test_idle(tmp_path):
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(tmp_path)), env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert r.rc == 0 and r.raw == "" and not (tmp_path / "e").exists()


def test_scope_line(project, tmp_path):
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert ("QGIS projects/models and Python scripts today; ArcGIS Pro projects partial; ModelBuilder (.tbx/.mxd) "
            "inventory only") in r.out["hookSpecificOutput"]["additionalContext"]


def test_e2b_key_option_written_to_plugin_data(project, tmp_path):
    """Claude userConfig (sensitive) reaches hooks as CLAUDE_PLUGIN_OPTION_E2B_API_KEY; session_start copies it to
    ${CLAUDE_PLUGIN_DATA}/secrets.env (0600, one line, atomic) and never prints it."""
    import os
    import stat
    data = tmp_path / "plugin-data" / "not-yet"
    key = "e2b_" + "0123456789abcdef" * 2
    env = {"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_PLUGIN_OPTION_E2B_API_KEY": key,
           "CLAUDE_ENV_FILE": str(tmp_path / "e")}
    for cwd in (project.root, tmp_path):        # with and without a migration/
        r = run_hook("session_start.py", payload("SessionStart", cwd=str(cwd)), env=env)
        assert r.rc == 0 and key not in r.raw and key not in r.err
        f = data / "secrets.env"
        assert f.read_text() == f"E2B_API_KEY={key}\n"
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
        assert not [p for p in data.iterdir() if p.name.endswith(".tmp")]
    bad = run_hook("session_start.py", payload("SessionStart", cwd=str(tmp_path)),
                   env=dict(env, CLAUDE_PLUGIN_OPTION_E2B_API_KEY="x y\nE2B_OTHER=1"))
    assert bad.rc == 0 and "unexpected format" in bad.raw and "E2B_OTHER" not in bad.raw
    assert (data / "secrets.env").read_text() == f"E2B_API_KEY={key}\n"   # left as it was


def test_no_option_no_file(tmp_path):
    data = tmp_path / "pd"
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(tmp_path)),
                 env={"CLAUDE_PLUGIN_DATA": str(data), "CLAUDE_ENV_FILE": str(tmp_path / "e")})
    assert r.rc == 0 and not (data / "secrets.env").exists()


def test_latest_unsealed_run_listed(project, tmp_path):
    rd = project.mig / "runs" / "r20261015T120000Z-abcdef12"
    rd.mkdir(parents=True)
    (rd / "manifest.json").write_text(json.dumps({"chain": "isolated", "steps": [{"step_id": "s01",
                                                                                   "status": "succeeded"}]}))
    r = run_hook("session_start.py", payload("SessionStart", cwd=str(project.root)),
                 env={"CLAUDE_ENV_FILE": str(tmp_path / "e")})
    ctx = r.out["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("axis-migrate status:")
    assert "axis-migrate brief: run r20261015T120000Z-abcdef12 (isolated, unsealed: no rung)" in ctx
