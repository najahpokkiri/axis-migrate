"""Stop gate scope by session (review §3 cause 1) and cost (§3 "Cost on every Stop").

Guards record a session in migration/.axis/sessions.json when one of its tool calls writes or reads under
migration/ or runs a plugin script (_hooklib.touch_session); the Stop gate applies only to recorded sessions and finds
migration/ from the payload cwd only. A session that only talks about or works around the folder is never gated.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

from conftest import make_project, payload, run_hook, tool_payload

import _hooklib as hl
import stop_gate as sg

CLAIM = "Migration complete, parity achieved."


def listed(pr, sid):
    return sid in hl.sessions(pr.mig)


def stop(pr, sid, msg=CLAIM, cwd=None, proc_cwd=None):
    return run_hook("stop_gate.py", payload("Stop", cwd=str(cwd or pr.root), last_assistant_message=msg,
                                            stop_hook_active=False, session_id=sid), proc_cwd=proc_cwd)


def bash(pr, sid, cmd, cwd=None):
    d = tool_payload("PreToolUse", "Bash", cwd or pr.root, {"command": cmd}, session_id=sid)
    return run_hook("guard_bash.py", d)


def test_unlisted_session_is_never_gated(tmp_path):
    pr = make_project(tmp_path, sessions=())
    r = stop(pr, "orchestrator")
    assert r.rc == 0 and r.raw == ""
    assert not (pr.mig / ".axis" / "stop_gate.json").exists()   # not even state written


def test_orchestrator_in_the_project_folder_is_not_gated(tmp_path):
    """The live false positive: a session whose shell sits in the folder holding migration/, talking about it."""
    pr = make_project(tmp_path, sessions=())
    for cmd in ("git status", "ls", "ls legacy-demo", "echo the folder is fine",
                "python3 -c 'print(1)'"):
        assert bash(pr, "orchestrator", cmd).rc == 0
    assert not listed(pr, "orchestrator")
    assert stop(pr, "orchestrator").raw == ""


def test_listed_session_is_gated(tmp_path):
    pr = make_project(tmp_path, sessions=())
    assert bash(pr, "migrator", f'"{hl.PLUGIN}/scripts/axpy" "{hl.PLUGIN}/skills/discover/scan.py" legacy-demo').rc == 0
    assert listed(pr, "migrator")
    assert stop(pr, "migrator").decision == "block"
    assert stop(pr, "someone-else").raw == ""


def test_bash_reads_or_writes_under_migration_record(tmp_path):
    pr = make_project(tmp_path, sessions=())
    bash(pr, "s-read", "cat migration/graph.json")
    bash(pr, "s-cd", "cd migration && ls")
    bash(pr, "s-abs", f"head -1 {pr.mig}/params.yaml")
    bash(pr, "s-inside", "ls", cwd=pr.mig / "analysis")
    bash(pr, "s-opt", "tool --out=migration/analysis/x.txt")
    for sid in ("s-read", "s-cd", "s-abs", "s-inside", "s-opt"):
        assert listed(pr, sid), sid


def test_write_and_post_scan_record(tmp_path):
    pr = make_project(tmp_path, sessions=())
    d = tool_payload("PreToolUse", "Write", pr.root, {"file_path": "migration/analysis/n.md", "content": "x"},
                     session_id="s-write")
    assert run_hook("guard_write.py", d).rc == 0 and listed(pr, "s-write")
    d = tool_payload("PreToolUse", "Write", pr.root, {"file_path": str(pr.root / "notes.md"), "content": "x"},
                     session_id="s-outside")
    assert run_hook("guard_write.py", d).rc == 0 and not listed(pr, "s-outside")
    (pr.mig / "analysis" / "a.py").write_text("x = 1\n")
    d = tool_payload("PostToolUse", "Write", pr.root, {"file_path": str(pr.mig / "analysis" / "a.py"),
                                                       "content": "x = 1\n"}, session_id="s-post")
    assert run_hook("post_scan.py", d).rc == 0 and listed(pr, "s-post")


def test_stop_ignores_the_hook_process_cwd(tmp_path):
    """No os.getcwd() fallback for Stop: payload cwd elsewhere, process cwd in the project -> not gated."""
    pr = make_project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert stop(pr, "s-1", cwd=elsewhere, proc_cwd=str(pr.root)).raw == ""
    assert stop(pr, "s-1").decision == "block"


def test_touch_session_never_raises(tmp_path):
    pr = make_project(tmp_path, sessions=())
    (pr.mig / ".axis").mkdir(exist_ok=True)
    (pr.mig / ".axis" / "sessions.json").write_text("{torn")
    assert hl.touch_session(pr.mig, "s-x") and listed(pr, "s-x")      # a torn file is replaced
    assert hl.touch_session(pr.mig, None) is False and hl.touch_session(pr.mig, "") is False
    blocked = tmp_path / "ro"
    blocked.mkdir()
    (blocked / ".axis").write_text("a file, not a folder")
    assert hl.touch_session(blocked, "s-y") is False


def test_no_claim_skips_ladder_and_hashing(tmp_path, monkeypatch):
    pr = make_project(tmp_path)

    def boom(*a, **k):
        raise AssertionError("ladder/drift must not run for a message without a claim or status line")
    monkeypatch.setattr(sg, "ladder_status", boom)
    monkeypatch.setattr(sg, "drift", boom)
    assert sg.decide({"last_assistant_message": "Which option do you prefer?"}, pr.mig) is None


def test_second_stop_is_fast_on_a_large_legacy_tree(tmp_path):
    """~300 MB of sparse legacy files: the first check hashes them, the second costs a stat per file."""
    pr = make_project(tmp_path)
    size = 100 * 1024 * 1024
    zero_sha = hashlib.sha256()
    chunk = b"\0" * (1 << 20)
    for _ in range(size >> 20):
        zero_sha.update(chunk)
    zero_sha = zero_sha.hexdigest()
    inv = json.loads((pr.mig / "inventory.json").read_text())
    old = time.time() - 86400
    for i in range(3):
        rel = f"06_BIG/big{i}.tif"
        p = pr.legacy / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.truncate(size)
        os.utime(p, (old, old))
        inv["files"].append({"path": rel, "size": size, "sha256": zero_sha, "sidecar_of": None})
    for f in inv["files"]:
        os.utime(pr.legacy / f["path"], (old, old))
    (pr.mig / "inventory.json").write_text(json.dumps(inv))
    t0 = time.perf_counter()
    assert sg.legacy_drift(pr.mig) == []
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    assert sg.legacy_drift(pr.mig) == []
    second = time.perf_counter() - t0
    assert second < 0.25 and second < first / 4, (first, second)
    # a changed file is still caught on the cached path (new mtime -> hashed again)
    with open(pr.legacy / "06_BIG/big1.tif", "r+b") as fh:
        fh.seek(5)
        fh.write(b"x")
    os.utime(pr.legacy / "06_BIG/big1.tif", (old + 10, old + 10))
    assert "LEGACY_REF_CHANGED" in sg.legacy_drift(pr.mig)[0]
    # and a whole Stop with a claim stays well inside the hook's 15 s once the cache is warm
    t0 = time.perf_counter()
    r = stop(pr, "s-1")
    assert r.decision == "block" and time.perf_counter() - t0 < 5
