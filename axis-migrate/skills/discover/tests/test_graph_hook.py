"""T-hook-1: the UserPromptSubmit hook graph_changed.py (system python3 -I, stdlib only, < 200 ms, never blocks)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

import graph_synth as gs
import graph as G


def hook(cwd: Path, payload: str | None = None) -> subprocess.CompletedProcess:
    stdin = payload if payload is not None else json.dumps({"hook_event_name": "UserPromptSubmit", "cwd": str(cwd),
                                                            "prompt": "go on"})
    return subprocess.run(["python3", "-I", str(gs.HOOK_PY)], input=stdin, capture_output=True, text=True, timeout=10)


@pytest.fixture()
def project(tmp_path: Path):
    mdir = gs.make_project(tmp_path)
    return tmp_path, mdir


def test_announces_then_silent_after_ack(project):
    root, mdir = project
    assert hook(root).stdout == ""
    G.apply(mdir, [{"type": "rename", "id": "s01_clip", "title": "Clip"}], "agent", 1)
    assert hook(root).stdout == ""  # the agent's own edits are not announced
    G.apply(mdir, [{"type": "add_step", "id": "s05_branch", "title": "Branch", "reads": ["ds.raw"]}], "user", 2,
            channel="graph")
    G.apply(mdir, [{"type": "delete_step", "id": "s01_clip"}], "user", 3, channel="graph")
    G.apply(mdir, [{"type": "submit", "message": "x"}], "user", 4, channel="graph")
    r = hook(root / "legacy")  # found by walking up from a subdirectory
    assert r.returncode == 0
    ctx = json.loads(r.stdout)["hookSpecificOutput"]
    assert ctx["hookEventName"] == "UserPromptSubmit"
    text = ctx["additionalContext"]
    assert "graph changed (rev 2→4, 2 user edits, 1 submitted)" in text
    assert "delete_step s01_clip (needs confirmation)" in text and "graph.py ack 5" in text
    assert len(text.splitlines()) <= 12
    G.apply(mdir, [{"type": "ack", "seq": 5}], "agent", 4)
    assert hook(root).stdout == ""


def test_twelve_lines_max(project):
    root, mdir = project
    for i in range(20):
        G.apply(mdir, [{"type": "add_note", "id": "s02_mask", "text": f"n{i}"}], "user", 1 + i, channel="graph")
    text = json.loads(hook(root).stdout)["hookSpecificOutput"]["additionalContext"]
    assert len(text.splitlines()) == 12 and "more" in text


def test_latency_p95(project):
    root, mdir = project
    G.apply(mdir, [{"type": "confirm", "id": "s01_clip"}], "user", 1, channel="graph")
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        r = hook(root)
        times.append(time.perf_counter() - t0)
        assert "graph changed (rev" in r.stdout
    times.sort()
    assert times[int(0.95 * len(times)) - 1] < 0.200, times


def test_fails_open(project):
    root, mdir = project
    with open(mdir / "graph-edits.jsonl", "a") as fh:
        fh.write("{not json\n")
    r = hook(root)
    assert r.returncode == 0 and "graph-change check failed" in r.stdout
    r = hook(root, payload="not json either")
    assert r.returncode == 0 and "graph-change check failed" in r.stdout


def test_idle_without_migration(tmp_path: Path):
    r = hook(tmp_path)
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_hook_is_short_and_stdlib_only():
    src = gs.HOOK_PY.read_text()
    assert len(src.splitlines()) <= 12
    r = subprocess.run(["python3", "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import graph; "
                        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('yaml','numpy','rasterio')))",
                        str(gs.DISCOVER)], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "[]", r.stderr
