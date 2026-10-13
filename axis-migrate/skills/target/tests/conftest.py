"""Target-skill tests: synthetic data only (CONTRACTS §0). A reference-verified project is built once per session with
the real runner, comparator, plots and seal, then copied per test."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent
PLUGIN = TARGET.parents[1]
sys.path.insert(0, str(TARGET))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "skills" / "report"))

import synth_target  # noqa: E402

os.environ.setdefault("GDAL_PAM_ENABLED", "NO")
AXPY = str(PLUGIN / "bin" / "axpy")
MAKE = TARGET / "make_databricks.py"
CHECK = TARGET / "target_check.py"
SEAL = PLUGIN / "skills" / "report" / "seal.py"
LADDER = PLUGIN / "skills" / "report" / "ladder.py"


def make_writable(root: Path) -> None:
    for dirpath, _, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for fn in filenames:
            p = Path(dirpath) / fn
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


def _copy(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, symlinks=True)


@pytest.fixture(scope="session")
def verified_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("target_template")
    mig, run_id = synth_target.reference_verified(root)
    yield root, run_id
    make_writable(root)


@pytest.fixture
def project(verified_template, tmp_path):
    """(migration dir, run id) of a fresh copy of the reference-verified synthetic project."""
    src, run_id = verified_template
    root = tmp_path / "proj"
    _copy(src, root)
    yield root / "migration", run_id
    if root.exists():
        make_writable(root)


def run(script: Path, *args, ok=(0, 1, 2, 3), env=None) -> tuple[int, dict | str]:
    cp = subprocess.run([AXPY, str(script), *map(str, args)], capture_output=True, text=True, timeout=900, env=env)
    assert cp.returncode in ok, (script.name, cp.returncode, cp.stdout[-3000:], cp.stderr[-3000:])
    try:
        return cp.returncode, json.loads(cp.stdout)
    except json.JSONDecodeError:
        return cp.returncode, cp.stdout + cp.stderr


def make(mig: Path, *extra, env=None):
    return run(MAKE, mig, "--json", *extra, env=env)


def check(mig: Path, run_id: str, *extra, env=None):
    return run(CHECK, mig, "--run", run_id, "--json", *extra, env=env)


def states(mig: Path) -> dict:
    doc = json.loads((mig / "databricks" / "target-check.json").read_text())
    return {c["id"]: c for c in doc["checks"]}


def manifest(mig: Path) -> dict:
    return json.loads((mig / "databricks" / "target-manifest.json").read_text())


def set_step(mig: Path, step: str, text: str) -> None:
    p = mig / "pipeline" / f"{step}.py"
    p.chmod(0o644)
    p.write_text(text)
