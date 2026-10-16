"""doctor.py (scripts/axpy --doctor): preflight checks and their one-line messages."""
from __future__ import annotations

import shutil
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import doctor

AXPY = Path(__file__).resolve().parents[3] / "scripts" / "axpy"
AXPY_CMD = ([shutil.which("sh") or "sh"] if os.name == "nt" else []) + [str(AXPY)]  # Windows cannot exec a shell script


def test_strace_missing_says_what_the_fallback_is(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: False)
    state, msg = doctor.check_strace()
    assert state == "WARN" and "audit+cpl_debug" in msg and "capped at sandbox-validated" in msg
    assert "E2B" in msg                                                      # the way past the cap is named


def test_missing_uv_fails(monkeypatch):
    monkeypatch.setattr(doctor, "find_uv", lambda: None)
    assert doctor.check_uv()[0] == "FAIL" and doctor.check_env(True)[0] == "FAIL"


def test_port_and_cache_ok():
    assert doctor.check_port()[0] == "OK" and doctor.check_cache()[0] == "OK"


def test_cli_exit_codes(tmp_path):
    cp = subprocess.run([*AXPY_CMD, "--doctor", "--no-sync", "--json"], capture_output=True, text=True)
    doc = json.loads(cp.stdout)
    assert cp.returncode == (0 if doc["ok"] else 13)
    assert [c["check"] for c in doc["checks"]] == ["uv", "cache", "env", "strace", "port", "plugin", "e2b", "refenv",
                                                   "rung"]
    assert "sandbox_choice" in doc and isinstance(doc["ask_sandbox"], bool)
    bad = subprocess.run([*AXPY_CMD, "--doctor", "--no-sync"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CACHE_HOME": "/proc/nope"})
    assert bad.returncode == 13 and "ENV_CACHE_NOT_WRITABLE" in bad.stdout
    # one plain sentence for the person: the sandbox exists; what it blocks; the settings that allow it
    note = [l for l in bad.stdout.splitlines() if l.startswith("NOTE  ")]
    assert len(note) == 1 and "agent's sandbox doing its job" in note[0] and "/proc/nope/axis-migrate" in note[0]
    assert "sandbox.network.allowLocalBinding: true" in note[0] and "127.0.0.1" in note[0]
    assert "outside the sandbox" not in bad.stdout


def test_no_sandbox_note_when_nothing_was_blocked():
    assert doctor.sandbox_note([("cache", "OK", "x"), ("port", "OK", "y"), ("uv", "FAIL", "UV_MISSING: …")]) is None
    assert doctor.sandbox_note([("port", "FAIL", "PORT_BIND_FAILED: 127.0.0.1: denied")]) is not None


def test_axpy_gives_matplotlib_a_writable_config_dir(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "MPLCONFIGDIR"}
    out = subprocess.run([*AXPY_CMD, "-c", "import os; print(os.environ['MPLCONFIGDIR'])"], capture_output=True,
                         text=True, env=env).stdout.strip()
    assert out == str(doctor.ac.env_venv_dir() / ".mplconfig") and Path(out).is_dir() and os.access(out, os.W_OK)
    mine = subprocess.run([*AXPY_CMD, "-c", "import os; print(os.environ['MPLCONFIGDIR'])"], capture_output=True,
                          text=True, env=dict(env, MPLCONFIGDIR=str(tmp_path))).stdout.strip()
    assert mine == str(tmp_path)                                          # a caller's own setting wins


def test_print_python_is_the_shared_resolver():
    out = subprocess.run([*AXPY_CMD, "--print-python"], capture_output=True, text=True).stdout.strip()
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
    import axis_common as ac
    assert out == str(ac.env_python()) and "/skills/run/env/.venv" not in out


def test_plugin_check_flags_git_ignored_leftovers(tmp_path):
    root = tmp_path / "axis-migrate"
    (root / "skills" / "run").mkdir(parents=True)
    assert doctor.check_plugin(root)[0] == "OK"
    (root / "skills" / "run" / "env" / ".venv" / "lib").mkdir(parents=True)
    (root / "lib" / "__pycache__").mkdir(parents=True)
    (root / "migration").mkdir()
    (root / ".git" / "__pycache__").mkdir(parents=True)          # inside .git: not part of the installed copy
    state, msg = doctor.check_plugin(root)
    assert state == "WARN" and msg.startswith("PLUGIN_NOT_CLEAN")
    assert [str(p.relative_to(root)) for p in doctor.stray_paths(root)] == [
        "lib/__pycache__", "migration", "skills/run/env/.venv"]
    assert "GitHub" in msg and "fresh clone" in msg


FAKE = "e2b_fake_key_for_doctor_0123456789abcdef"


@pytest.fixture
def home(monkeypatch, tmp_path):
    """HOME in tmp, no harness variables: the doctor sees no real key, config or template record."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("E2B_API_KEY", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "AXIS_MIGRATE_SECRETS", "CLAUDE_CONFIG_DIR",
              "CODEX_HOME"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def test_e2b_not_configured_is_an_optional_warn(home):
    state, msg = doctor.check_e2b()
    assert state == "WARN" and msg.startswith("not configured (optional: E2B sandbox for the strace read ledger")
    assert "--configure e2b" in msg


def test_e2b_ok_names_the_source_and_template_never_the_key(home):
    p = home / ".config" / "axis-migrate" / "secrets.env"
    p.parent.mkdir(parents=True)
    p.write_text(f"E2B_API_KEY={FAKE}\n")
    p.chmod(0o600)
    state, msg = doctor.check_e2b()
    assert state == "OK" and str(p) in msg and doctor.ac.e2b_template_name() in msg and "not checked offline" in msg
    assert FAKE not in msg and str(len(FAKE)) not in msg.replace(str(p), "")   # a tmp path may hold "40"
    p.chmod(0o644)
    state, msg = doctor.check_e2b()
    assert state == "FAIL" and msg.startswith("SECRETS_FILE_MODE") and FAKE not in msg


def test_e2b_from_the_environment(home, monkeypatch):
    monkeypatch.setenv("E2B_API_KEY", FAKE)
    state, msg = doctor.check_e2b()
    assert state == "OK" and "key from env E2B_API_KEY" in msg and FAKE not in msg


def test_cache_line_says_which_rule_chose_the_path(monkeypatch):
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    state, msg = doctor.check_cache()
    assert state == "OK" and "CLAUDE_PLUGIN_DATA unset in this shell → ~/.cache/axis-migrate" in msg


def test_refenv_line(monkeypatch, tmp_path):
    monkeypatch.setenv("AXIS_REFENV_PYTHON", str(tmp_path / "nope" / "python"))
    state, msg = doctor.check_refenv()
    assert state == "WARN" and "REFENV_MISSING" in msg and "AXIS_REFENV_PYTHON" in msg and "code repair" in msg
    py = tmp_path / "python"
    py.write_text("#!/bin/sh\n")
    py.chmod(0o755)
    monkeypatch.setenv("AXIS_REFENV_PYTHON", str(py))
    assert doctor.check_refenv() == ("OK", f"reference env {py}: after a parity failure run "
                                           "`run_local.py migration --env refenv`")


@pytest.mark.parametrize("strace,e2b,state,word", [("OK", "WARN", "OK", "target-ready (strace"),
                                                   ("WARN", "OK", "OK", "through E2B"),
                                                   ("WARN", "WARN", "WARN", "sandbox-validated")])
def test_max_rung_line(strace, e2b, state, word):
    st, msg = doctor.check_rung([("strace", strace, ""), ("e2b", e2b, "")])
    assert st == state and msg.startswith("max rung on this machine: ") and word in msg


def test_first_run_question_is_asked_once(home, monkeypatch, capsys):
    monkeypatch.setattr(doctor, "check_strace", lambda: ("WARN", "no strace"))
    monkeypatch.setattr(doctor, "check_env", lambda no_sync: ("OK", "fake"))
    doctor.main(["--no-sync", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ask_sandbox"] is True and doc["sandbox_choice"] is None
    assert doctor.main(["--record-sandbox", "local"]) == 0
    assert "config.json" in capsys.readouterr().out
    doctor.main(["--no-sync", "--json"])
    doc = json.loads(capsys.readouterr().out)
    assert doc["ask_sandbox"] is False and doc["sandbox_choice"] == "local"
    assert json.loads((home / ".config/axis-migrate/config.json").read_text())["sandbox"] == "local"
