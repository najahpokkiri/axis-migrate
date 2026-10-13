"""H5 guard_hidden (G14) and the always-on secrets rule."""
from __future__ import annotations

import os

from conftest import run_hook, tool_payload

ENV = {"AXIS_MIGRATE_HIDDEN_GLOBS": "*.PROVENANCE.md:*.build/**"}


def read(pr, path, env=None):
    return run_hook("guard_hidden.py", tool_payload("PreToolUse", "Read", pr.root, {"file_path": str(path)}), env=env)


def test_g14_provenance_hidden_in_eval_mode(project):
    r = read(project, project.root / "legacy-demo.PROVENANCE.md", ENV)
    assert r.decision == "deny" and r.reason.isascii()
    assert read(project, project.root / "legacy-demo.build" / "build.py", ENV).decision == "deny"


def test_g14_same_read_without_variable(project):
    assert read(project, project.root / "legacy-demo.PROVENANCE.md").out is None


def test_ordinary_reads_allowed_in_eval_mode(project):
    assert read(project, project.legacy / "NOTES.txt", ENV).out is None
    assert read(project, project.legacy / "05_TEMP" / "01_mid.tif", ENV).out is None  # the agent may read outputs


def test_grep_glob_over_hidden_tree(project):
    g = run_hook("guard_hidden.py", tool_payload("PreToolUse", "Grep", project.root,
                                                 {"pattern": "answer", "path": str(project.root)}), env=ENV)
    assert g.decision == "deny"
    g = run_hook("guard_hidden.py", tool_payload("PreToolUse", "Grep", project.root,
                                                 {"pattern": "answer", "path": str(project.mig)}), env=ENV)
    assert g.out is None
    g = run_hook("guard_hidden.py", tool_payload("PreToolUse", "Glob", project.root,
                                                 {"pattern": "**/*.md", "path": str(project.root)}), env=ENV)
    assert g.decision == "deny"
    g = run_hook("guard_hidden.py", tool_payload("PreToolUse", "Glob", project.root,
                                                 {"pattern": "**/*.py", "path": str(project.mig)}), env=ENV)
    assert g.out is None


def test_secrets_file_never_read(project):
    p = os.path.expanduser("~/.config/axis-migrate/secrets.env")
    r = read(project, p)
    assert r.decision == "deny" and "secrets" in r.reason


def test_idle(tmp_path):
    r = run_hook("guard_hidden.py", tool_payload("PreToolUse", "Read", tmp_path, {"file_path": "/etc/hosts"}), env=ENV)
    assert r.rc == 0 and r.raw == ""
