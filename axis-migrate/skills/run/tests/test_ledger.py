"""strace ledger parsing and the D5-9 read-ledger rule, on hand-written strace lines."""
from __future__ import annotations

from pathlib import Path

import runlib as rl

STRACE_TEXT = r'''100 openat(AT_FDCWD</run>, "/venv/lib/numpy/__init__.py", O_RDONLY|O_CLOEXEC) = 3</venv/lib/numpy/__init__.py>
100 openat(AT_FDCWD</run>, "stage/inputs/03_INPUT/pop.tif", O_RDONLY|O_CLOEXEC) = 4</run/stage/inputs/03_INPUT/pop.tif>
100 openat(AT_FDCWD</run>, "/run/stage/inputs/03_INPUT", O_RDONLY|O_NONBLOCK|O_CLOEXEC|O_DIRECTORY) = 5</run/stage/inputs/03_INPUT>
101 openat(AT_FDCWD</run>, "/proj/legacy/04_FINAL/out.tif", O_RDONLY|O_CLOEXEC <unfinished ...>
100 openat(AT_FDCWD</run>, "/run/stage/inputs/03_INPUT/pop.tif.aux.xml", O_RDONLY) = -1 ENOENT (No such file or directory)
101 <... openat resumed>) = 6</proj/legacy/04_FINAL/out.tif>
100 openat(AT_FDCWD</run>, "/run/scratch/s1/out/ab/x.tif", O_RDWR|O_CREAT|O_TRUNC|O_CLOEXEC, 0666) = 7</run/scratch/s1/out/ab/x.tif>
100 rename("/run/scratch/s1/out/ab/x.tif", "/run/outputs/s1.tmp/x.tif") = 0
100 renameat2(AT_FDCWD</run>, "a\"b.tif", AT_FDCWD</run>, "/proj/migration/leak.tif", RENAME_NOREPLACE) = 0
100 openat(AT_FDCWD</run>, "/proj/legacy/05_TEMP/missing.tif", O_RDONLY) = -1 ENOENT (No such file or directory)
100 openat(3</proj/legacy>, "05_TEMP", O_RDONLY|O_DIRECTORY) = 8</proj/legacy/05_TEMP>
100 --- SIGCHLD {si_signo=SIGCHLD, si_code=CLD_EXITED} ---
'''


def test_parse_strace_handles_resumed_relative_and_escaped(tmp_path):
    p = tmp_path / "x.reads"
    p.write_text(STRACE_TEXT)
    ev = rl.parse_strace(p)
    paths = [(e["access"], e["path"], e["ok"]) for e in ev]
    assert ("read", "/proj/legacy/04_FINAL/out.tif", True) in paths          # unfinished + resumed joined
    assert ("read", "/run/stage/inputs/03_INPUT/pop.tif", True) in paths      # relative resolved via -y
    assert ("write", "/run/outputs/s1.tmp/x.tif", True) in paths              # rename destination
    assert ("move_from", '/run/a"b.tif', True) in paths                       # escaped quote, dirfd join
    assert ("dir", "/proj/legacy/05_TEMP", True) in paths                     # openat relative to a dir fd
    assert ("read", "/proj/legacy/05_TEMP/missing.tif", False) in paths


def test_classify_applies_the_read_ledger_rule(tmp_path, monkeypatch):
    p = tmp_path / "x.reads"
    p.write_text(STRACE_TEXT)
    monkeypatch.setattr(rl, "_real", lambda x: str(x))   # synthetic paths do not exist on disk
    pol = rl.LedgerPolicy(run_dir="/run", step_id="s1", input_files=["/run/stage/inputs/03_INPUT/pop.tif"],
                          allowed_prefixes=["/run/outputs/s1.tmp", "/run/scratch/s1"], allowed_files=[],
                          system_allow=["/venv"], protected=["/proj/legacy", "/proj/migration", "/proj"],
                          out_tmp="/run/outputs/s1.tmp", strict=["/proj/legacy", "/proj/migration"])
    led = rl.classify_events(rl.parse_strace(p), pol)
    und = {(u["path"], u["access"]) for u in led["undeclared"]}
    assert ("/proj/legacy/04_FINAL/out.tif", "read") in und
    assert ("/proj/legacy/05_TEMP", "dir") in und
    assert led["writes"] == ["/run/outputs/s1.tmp/x.tif"]
    assert led["undeclared_writes"] == ["/proj/migration/leak.tif"]
    assert {u["path"] for u in led["attempted_protected"]} == {"/proj/legacy/05_TEMP/missing.tif"}
    assert led["declared_opened"] == ["/run/stage/inputs/03_INPUT/pop.tif"]


def test_freeze_vs_lock(tmp_path):
    lock = tmp_path / "uv.lock"
    lock.write_text('version = 1\n[[package]]\nname = "numpy"\nversion = "2.5.4"\nsource = { registry = "x" }\n'
                    '[[package]]\nname = "axis-migrate-env"\nversion = "0.1.0"\nsource = { virtual = "." }\n')
    assert rl.freeze_vs_lock(["numpy==2.5.4"], lock)["matches"] is True
    r = rl.freeze_vs_lock(["numpy==2.5.3", "evil==1.0"], lock)
    assert r["matches"] is False and len(r["problems"]) == 2


def test_checksums_detect_changes_and_additions(tmp_path):
    d = tmp_path / "run"
    (d / "verify").mkdir(parents=True)
    (d / "a.txt").write_text("a")
    rl.write_checksums(d)
    assert rl.verify_checksums(d) == []
    (d / "verify" / "later.json").write_text("{}")      # verify/ is written after sealing
    assert rl.verify_checksums(d) == []
    (d / "b.txt").write_text("b")
    (d / "a.txt").write_text("A")
    assert sorted(rl.verify_checksums(d)) == ["ADDED:b.txt", "CHANGED:a.txt"]


def test_topo_order_and_cycle():
    steps = [{"id": "s2", "inputs": ["ds.a"], "outputs": ["ds.b"]}, {"id": "s1", "inputs": [], "outputs": ["ds.a"]},
             {"id": "s3", "inputs": ["ds.b", "ds.a"], "outputs": []}]
    assert rl.topo_order(steps) == ["s1", "s2", "s3"]
    import pytest
    with pytest.raises(rl.RunAbort) as e:
        rl.topo_order([{"id": "x", "inputs": ["ds.y"], "outputs": ["ds.x"]},
                       {"id": "y", "inputs": ["ds.x"], "outputs": ["ds.y"]}])
    assert e.value.exit_code == 11
