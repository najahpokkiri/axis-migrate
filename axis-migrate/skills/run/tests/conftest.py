"""Run-skill tests: synthetic data only (CONTRACTS §0). The project is built once and copied per test."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
RUN_SKILL = HERE.parent
sys.path.insert(0, str(RUN_SKILL))
sys.path.insert(0, str(HERE))

import synth  # noqa: E402

os.environ.setdefault("GDAL_PAM_ENABLED", "NO")


def make_writable(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for fn in filenames:
            p = Path(dirpath) / fn
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


@pytest.fixture(scope="session")
def template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("synth_template")
    synth.build_project(root)
    return root


@pytest.fixture
def project(template, tmp_path) -> Path:
    """A fresh copy of the synthetic project; returns its migration dir. Sealed runs are unlocked at teardown."""
    root = tmp_path / "proj"
    shutil.copytree(template, root)
    yield root / "migration"
    if root.exists():
        make_writable(root)


class Runner:
    """Calls run_local.main in-process (so tests can monkeypatch runlib) and loads the newest manifest."""

    def __init__(self, mig: Path):
        self.mig = mig

    def __call__(self, *args: str) -> tuple[int, dict | None]:
        import run_local
        before = set(self.runs())
        code = run_local.main([str(self.mig), *args])
        new = sorted(set(self.runs()) - before)
        man = json.loads((self.mig / "runs" / new[-1] / "manifest.json").read_text()) if new else None
        return code, man

    def runs(self) -> list[str]:
        d = self.mig / "runs"
        return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []

    def run_dir(self, man: dict) -> Path:
        return self.mig / "runs" / man["run_id"]


@pytest.fixture
def runner(project) -> Runner:
    return Runner(project)


def step_rec(man: dict, step_id: str) -> dict:
    return next(s for s in man["steps"] if s["step_id"] == step_id)


@pytest.fixture
def no_scan(monkeypatch, tmp_path):
    """Switch the static scanner off so the dynamic guard under test is the one that fires."""
    import runlib
    monkeypatch.setattr(runlib, "CODESCAN", tmp_path / "no-codescan.py")


def scanner_blocks(runner, monkeypatch) -> bool:
    """With the real scanner installed, the same code is blocked before any step runs (exit 11 SCAN_BLOCKED)."""
    import time
    import runlib
    real = runlib.PLUGIN / "skills" / "guard" / "codescan.py"
    if not real.is_file():
        return True
    off = runlib.CODESCAN
    monkeypatch.setattr(runlib, "CODESCAN", real)
    time.sleep(1.05)   # a new run id stamp
    try:
        code, man = runner()
    finally:
        monkeypatch.setattr(runlib, "CODESCAN", off)
    return code == 11 and "SCAN_BLOCKED" in man["reason_codes"] and man["steps"] == []
