"""Fixtures for the report tests: synthetic projects only (CONTRACTS §0); see synth.py."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[2] / "lib"))
os.environ.setdefault("GDAL_PAM_ENABLED", "NO")

import synth  # noqa: E402


@pytest.fixture
def proj(tmp_path):
    """A fresh synthetic project (legacy folder + migration/), no run yet."""
    return synth.build_project(tmp_path / "project")


@pytest.fixture
def sealed(proj):
    """The project with one passing, chained, sealed run (display layers included)."""
    r = synth.make_run(proj, 1)
    proj.update(r)
    return proj
