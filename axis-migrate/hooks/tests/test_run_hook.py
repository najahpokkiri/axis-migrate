"""hooks/run-hook.sh: the POSIX sh wrapper every hooks.json command goes through.

A fresh Mac has no working python3 (only the Xcode Command Line Tools stub); Windows may have none. Outside a
migration the wrapper exits 0 without any Python; inside one it prefers the plugin's own uv environment, then a python3
that really runs, else refuses with NEEDS_PYTHON3 in the mode of each hook. The payload reaches Python byte for byte.
Run under /bin/sh and, when present, dash and bash --posix.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from conftest import PLUGIN, SYS_PY, payload, tool_payload

WRAPPER = PLUGIN / "hooks" / "run-hook.sh"
SHELLS = [["/bin/sh"]] + [[p] for p in ("/usr/bin/dash", "/bin/dash") if os.path.exists(p)][:1] + \
    ([["/bin/bash", "--posix"]] if os.path.exists("/bin/bash") else [])
LOCK8 = hashlib.sha256((PLUGIN / "skills" / "run" / "env" / "uv.lock").read_bytes()).hexdigest()[:8]


@pytest.fixture(params=SHELLS, ids=lambda s: " ".join(s))
def shell(request):
    return request.param


@pytest.fixture
def nopy(tmp_path):
    """PATH with every tool of /usr/bin and /bin except any python*, HOME and plugin data empty: no interpreter."""
    b = tmp_path / "nopy-bin"
    b.mkdir()
    for d in ("/usr/local/bin", "/usr/bin", "/bin"):
        if not os.path.isdir(d):
            continue
        for n in os.listdir(d):
            if n.startswith("python") or (b / n).exists() or n in ("uv", "xcode-select"):
                continue
            src = os.path.join(d, n)
            if os.path.isfile(src) and os.access(src, os.X_OK):
                (b / n).symlink_to(src)
    home = tmp_path / "home"
    home.mkdir()
    return {"PATH": str(b), "HOME": str(home), "CLAUDE_PLUGIN_DATA": str(tmp_path / "pd-empty")}


def run(hook, data, *, env=None, shell=("/bin/sh",), proc_cwd=None, timeout=30):
    e = {k: v for k, v in os.environ.items()
         if k not in ("PLUGIN_ROOT", "PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "XDG_CACHE_HOME")
         and not k.startswith(("AXIS_MIGRATE_HIDDEN", "AXIS_MIGRATE_CODESCAN"))}
    e.update(env or {})
    stdin = data if isinstance(data, (str, bytes)) else json.dumps(data)
    stdin = stdin.encode() if isinstance(stdin, str) else stdin
    cwd = proc_cwd or "/"
    t0 = time.perf_counter()
    cp = subprocess.run([*shell, str(WRAPPER), hook], input=stdin, capture_output=True, env=e, cwd=str(cwd),
                        timeout=timeout)
    cp.ms = (time.perf_counter() - t0) * 1000
    cp.out = cp.stdout.decode()
    cp.err = cp.stderr.decode()
    return cp


def bash(cwd, cmd, **kw):
    return tool_payload("PreToolUse", "Bash", cwd, {"command": cmd}, **kw)


def write(cwd, fp):
    return tool_payload("PreToolUse", "Write", cwd, {"file_path": fp, "content": "x"})


# --- no migration/: exit 0 without any Python ------------------------------------------------------------------------
@pytest.mark.parametrize("hook,data", [
    ("guard_bash", lambda d: bash(d, "rm -rf /")),
    ("guard_write", lambda d: write(d, "x.txt")),
    ("guard_hidden", lambda d: tool_payload("PreToolUse", "Read", d, {"file_path": "x"})),
    ("post_scan", lambda d: tool_payload("PostToolUse", "Write", d, {"file_path": str(d / "x.py"), "content": ""})),
    ("stop_gate", lambda d: payload("Stop", cwd=str(d), last_assistant_message="All done.")),
    ("graph_changed", lambda d: payload("UserPromptSubmit", cwd=str(d))),
    ("session_start", lambda d: payload("SessionStart", cwd=str(d))),
    ("guard_bash", lambda d: "not json at all"),
    ("guard_bash", lambda d: ""),
])
def test_no_migration_needs_no_python(tmp_path, nopy, shell, hook, data):
    work = tmp_path / "work"
    work.mkdir()
    r = run(hook, data(work), env=nopy, shell=shell, proc_cwd=work)
    assert r.returncode == 0 and r.stdout == b"" and r.stderr == b"", (r.out, r.err)


def test_no_migration_is_fast_with_python(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    times = [run("guard_bash", bash(work, "ls"), proc_cwd=work).ms for _ in range(5)]
    assert min(times) < 100, times


# --- migration/ and no interpreter: NEEDS_PYTHON3 -----------------------------------------------------------------------
@pytest.mark.parametrize("hook,mk", [
    ("guard_bash", lambda pr: bash(pr.root, "ls")),
    ("guard_write", lambda pr: write(pr.root, "migration/analysis/x.md")),
    ("guard_hidden", lambda pr: tool_payload("PreToolUse", "Read", pr.root, {"file_path": "migration/graph.json"})),
    ("post_scan", lambda pr: tool_payload("PostToolUse", "Write", pr.root,
                                          {"file_path": str(pr.mig / "analysis" / "x.py"), "content": ""})),
])
def test_guards_refuse_without_python(project, nopy, shell, hook, mk):
    r = run(hook, mk(project), env=nopy, shell=shell, proc_cwd=project.root)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err
    assert f'"{PLUGIN}/scripts/axpy" --print-python' in r.err and "--doctor" in r.err and "uv" in r.err
    assert r.err.isascii()


def test_fix_commands_pass_and_nothing_else(project, nopy, shell):
    for cmd in (f'"{PLUGIN}/scripts/axpy" --print-python', f'"{PLUGIN}/scripts/axpy" --doctor',
                '"${CLAUDE_PLUGIN_ROOT}/scripts/axpy" --doctor'):
        assert run("guard_bash", bash(project.root, cmd), env=nopy, shell=shell).returncode == 0, cmd
    for cmd in (f'"{PLUGIN}/scripts/axpy" --print-python; rm -rf legacy-demo',
                f'"{PLUGIN}/scripts/axpy" --doctor && touch legacy-demo/x',
                f'"{PLUGIN}/scripts/axpy" "{PLUGIN}/skills/discover/scan.py" legacy-demo',
                '"/elsewhere/scripts/axpy" --doctor'):
        r = run("guard_bash", bash(project.root, cmd), env=nopy, shell=shell)
        assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err, cmd


def test_session_start_and_prompt_without_python(project, nopy, shell):
    r = run("session_start", payload("SessionStart", cwd=str(project.root)), env=nopy, shell=shell)
    ctx = json.loads(r.out)["hookSpecificOutput"]
    assert r.returncode == 0 and ctx["hookEventName"] == "SessionStart" and "NEEDS_PYTHON3" in ctx["additionalContext"]
    r = run("graph_changed", payload("UserPromptSubmit", cwd=str(project.root)), env=nopy, shell=shell)
    assert r.returncode == 0 and r.stdout == b""


def test_stop_without_python(project, nopy, shell):
    def stop(sid, active=False, env=None):
        return run("stop_gate", payload("Stop", cwd=str(project.root), session_id=sid, stop_hook_active=active,
                                        last_assistant_message="All checks pass."), env=dict(nopy, **(env or {})),
                   shell=shell)
    r = stop("s-1")                                  # a session that worked on this migration
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err and "do not claim" in r.err
    assert stop("s-1", active=True).returncode == 0  # once only: never a loop
    other = stop("orchestrator")
    assert other.returncode == 0 and other.stdout == b""
    codex = stop("orchestrator", env={"PLUGIN_ROOT": str(PLUGIN)})
    assert codex.returncode == 0 and json.loads(codex.out) == {}


# --- payload cwd vs process cwd (the Codex desktop cwd trap) ---------------------------------------------------------
def test_payload_cwd_or_process_cwd_finds_migration(project, nopy, tmp_path, shell):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = run("guard_bash", bash(project.mig / "analysis", "ls"), env=nopy, shell=shell, proc_cwd=elsewhere)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err      # payload cwd inside, process elsewhere
    r = run("guard_bash", bash(elsewhere, "ls"), env=nopy, shell=shell, proc_cwd=project.root)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err      # process cwd inside, payload elsewhere
    r = run("guard_write", write(elsewhere, str(project.legacy / "NOTES.txt")), env=nopy, shell=shell,
            proc_cwd=elsewhere)
    assert r.returncode == 2                                    # an absolute file_path into a migration
    stop = payload("Stop", cwd=str(elsewhere), session_id="s-1", last_assistant_message="All checks pass.")
    r = run("stop_gate", stop, env=nopy, shell=shell, proc_cwd=project.root)
    assert r.returncode == 0 and r.stdout == b""                # Stop: payload cwd only


def test_paths_with_spaces_and_escapes(tmp_path, nopy, shell):
    from conftest import make_project
    pr = make_project(tmp_path / "a b \"q\" \\x")
    r = run("guard_bash", bash(pr.root, "ls"), env=nopy, shell=shell)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err
    uni = tmp_path / "café"
    uni.mkdir()
    data = json.dumps(bash(uni, "ls"), ensure_ascii=True)       # "café": cannot be decoded in sh
    r = run("guard_bash", data, env=nopy, shell=shell, proc_cwd=uni)
    assert r.returncode == 0                                    # unsure -> Python's turn; none -> nothing to guard


def test_apply_patch_payload_outside_migration_without_python(tmp_path, nopy, shell):
    p = "*** Begin Patch\n*** Add File: /nowhere/x.txt\n+x\n*** End Patch"
    d = json.loads((PLUGIN / "hooks" / "tests" / "payloads" / "codex_PreToolUse_apply_patch.json").read_text())
    d.update(cwd=str(tmp_path), tool_input={"command": p})
    r = run("guard_write", d, env=nopy, shell=shell, proc_cwd=tmp_path)
    assert r.returncode == 0


# --- interpreter choice -------------------------------------------------------------------------------------------------
def fake_env_python(base: Path, name: str, log: Path) -> Path:
    py = base / name / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.write_text(f'#!/bin/sh\necho "$0" >> "{log}"\nexec "{SYS_PY}" "$@"\n')
    py.chmod(0o755)
    return py


def test_env_python_preferred_over_python3(project, tmp_path, shell):
    pd = tmp_path / "pd"
    log = tmp_path / "used.log"
    other = fake_env_python(pd, "env-00000000", log)
    mine = fake_env_python(pd, f"env-{LOCK8}", log)
    env = {"CLAUDE_PLUGIN_DATA": str(pd), "HOME": str(tmp_path)}   # not the real ~/.cache env
    r = run("guard_write", write(project.root, "legacy-demo/NOTES.txt"), env=env, shell=shell)
    assert r.returncode == 0 and json.loads(r.out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert log.read_text().split() == [str(mine)]
    shutil.rmtree(mine.parent.parent)                           # stale lock: any env-* still serves (stdlib only)
    log.unlink()
    r = run("guard_write", write(project.root, "legacy-demo/NOTES.txt"), env=env, shell=shell)
    assert "deny" in r.out and log.read_text().split() == [str(other)]


def test_env_python_under_xdg_cache_and_codex_plugin_data(project, tmp_path, nopy):
    for var, base in (("XDG_CACHE_HOME", tmp_path / "xdg"), ("PLUGIN_DATA", tmp_path / "codex-pd")):
        log = tmp_path / f"{var}.log"
        root = base / "axis-migrate" if var == "XDG_CACHE_HOME" else base
        fake_env_python(root, f"env-{LOCK8}", log)
        r = run("guard_write", write(project.root, "legacy-demo/NOTES.txt"), env=dict(nopy, **{var: str(base)}))
        assert r.returncode == 0 and "deny" in r.out and log.is_file(), (var, r.err)


@pytest.mark.parametrize("body,why", [
    ("exit 1", "the macOS / Windows store stub: runs, fails"),
    ("echo 'Python 2.7'; exit 1", "too old (the version check exits 1)"),
    ("sleep 20", "hangs (dialog): the 3 s guard kills it"),
])
def test_broken_python3_is_not_trusted(project, nopy, tmp_path, body, why):
    fake = Path(nopy["PATH"]) / "python3"
    fake.write_text(f"#!/bin/sh\n{body}\n")
    fake.chmod(0o755)
    t0 = time.perf_counter()
    r = run("guard_bash", bash(project.root, "ls"), env=nopy)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err, why
    assert time.perf_counter() - t0 < 8


def test_working_python3_on_path_is_used(project, nopy):
    (Path(nopy["PATH"]) / "python3").symlink_to(SYS_PY)
    r = run("guard_write", write(project.root, "legacy-demo/NOTES.txt"), env=nopy)
    assert r.returncode == 0 and "deny" in r.out


@pytest.mark.skipif(shutil.which("python") is not None or os.path.exists("/usr/local/bin/python3")
                    or not os.path.exists("/usr/bin/python3"), reason="needs /usr/bin/python3 as the only python")
def test_macos_clt_stub_never_run(project, tmp_path):
    """Simulated macOS: uname says Darwin, `xcode-select -p` fails (no Command Line Tools) -> /usr/bin/python3 (the
    stub on a real Mac) is skipped, not run, and the hook refuses with NEEDS_PYTHON3."""
    fb = tmp_path / "fakebin"
    fb.mkdir()
    calls = tmp_path / "xcode.log"
    (fb / "uname").write_text("#!/bin/sh\necho Darwin\n")
    (fb / "xcode-select").write_text(f'#!/bin/sh\necho "$@" >> "{calls}"\necho "xcode-select: error" >&2\nexit 2\n')
    for f in fb.iterdir():
        f.chmod(0o755)
    env = {"PATH": f"{fb}:/usr/bin:/bin", "HOME": str(tmp_path / "h"), "CLAUDE_PLUGIN_DATA": str(tmp_path / "none")}
    r = run("guard_bash", bash(project.root, "ls"), env=env)
    assert r.returncode == 2 and "NEEDS_PYTHON3" in r.err and calls.read_text().strip() == "-p"
    (fb / "xcode-select").write_text("#!/bin/sh\necho /Library/Developer/CommandLineTools\n")
    r = run("guard_bash", bash(project.root, "ls"), env=env)
    assert r.returncode == 0                                   # CLT present: /usr/bin/python3 is real and used


# --- passthrough -------------------------------------------------------------------------------------------------------
def test_payload_reaches_python_byte_for_byte(project, tmp_path, shell):
    pd = tmp_path / "pd"
    py = pd / f"env-{LOCK8}" / "bin" / "python"
    py.parent.mkdir(parents=True)
    out = tmp_path / "got.bin"
    py.write_text(f'#!/bin/sh\ncat > "{out}"\n')
    py.chmod(0o755)
    d = bash(project.root, "printf 'a\\tb' && echo \"café \\\\ — x\"\r\n")
    for raw in (json.dumps(d) + "\n\n\n", json.dumps(d, ensure_ascii=False, indent=2), json.dumps(d) + "  \r\n"):
        r = run("guard_bash", raw.encode("utf-8"), env={"CLAUDE_PLUGIN_DATA": str(pd)}, shell=shell)
        assert r.returncode == 0 and out.read_bytes() == raw.encode("utf-8")


def test_hooks_through_wrapper_behave_as_before(project, tmp_path, shell):
    """With a working interpreter the wrapper is transparent: the real guard decides (here a legacy write)."""
    r = run("guard_bash", bash(project.root, "rm legacy-demo/NOTES.txt"), shell=shell, proc_cwd=project.root)
    assert r.returncode == 0 and json.loads(r.out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    r = run("guard_bash", "{broken", shell=shell, proc_cwd=project.root)
    assert r.returncode == 2                                    # malformed payload in a migration: fail closed
