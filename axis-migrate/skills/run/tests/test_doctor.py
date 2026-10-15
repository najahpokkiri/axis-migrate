"""doctor.py (scripts/axpy --doctor): preflight checks and their one-line messages."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import doctor

AXPY = Path(__file__).resolve().parents[3] / "scripts" / "axpy"


def test_strace_missing_says_what_the_fallback_is(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda name: None)
    monkeypatch.setattr(doctor.Path, "is_file", lambda self: False)
    state, msg = doctor.check_strace()
    assert state == "WARN" and "audit+cpl_debug" in msg and "capped at sandbox-validated" in msg


def test_missing_uv_fails(monkeypatch):
    monkeypatch.setattr(doctor, "find_uv", lambda: None)
    assert doctor.check_uv()[0] == "FAIL" and doctor.check_env(True)[0] == "FAIL"


def test_port_and_cache_ok():
    assert doctor.check_port()[0] == "OK" and doctor.check_cache()[0] == "OK"


def test_cli_exit_codes(tmp_path):
    cp = subprocess.run([str(AXPY), "--doctor", "--no-sync", "--json"], capture_output=True, text=True)
    doc = json.loads(cp.stdout)
    assert cp.returncode == (0 if doc["ok"] else 13)
    assert [c["check"] for c in doc["checks"]] == ["uv", "cache", "env", "strace", "port", "plugin"]
    bad = subprocess.run([str(AXPY), "--doctor", "--no-sync"], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "XDG_CACHE_HOME": "/proc/nope"})
    assert bad.returncode == 13 and "ENV_CACHE_NOT_WRITABLE" in bad.stdout


def test_print_python_is_the_shared_resolver():
    out = subprocess.run([str(AXPY), "--print-python"], capture_output=True, text=True).stdout.strip()
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
