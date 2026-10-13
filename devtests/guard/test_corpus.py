"""Corpus measurement for codescan (08 D8-5, §6 corpus tests). Dev-only: reads the archive and the lux fixture.

(1) 0 block findings on the clean corpora for every rule that applies to plain Python (S11 absolute paths and S14
    legacy engines are pipeline-context rules: the legacy scripts *are* the legacy engine and hard-code their paths);
(2) archive regression floors S01 >= 120 files, S03 >= 70, S07 >= 150, S12 >= 30;
(3) the golden lux pipeline (evals/golden/lux, once written) has 0 block findings, S11/S14 included.
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
from pathlib import Path

import pytest

DEV = Path(__file__).resolve().parents[2]
CODESCAN = DEV / "axis-migrate" / "skills" / "guard" / "codescan.py"
ARCHIVE = sorted(glob.glob(os.path.expanduser("~/src/axis-spatial/_archive/tasks/legacy-tasks/migration-runs/*/generated-code")))
CLEAN = [DEV / "fixtures" / "lux-exposure-legacy" / "02_SCRIPTS", DEV / "fixtures" / "lux-exposure-legacy.build"]
GOLDEN = DEV / "evals" / "golden" / "lux"
CONTEXT_ONLY = {"S11", "S14"}


def corpus(*dirs):
    cp = subprocess.run(["/usr/bin/python3", "-I", str(CODESCAN), "--corpus", *map(str, dirs)],
                        capture_output=True, text=True, check=True)
    return json.loads(cp.stdout)


def test_clean_corpora_have_no_block_findings():
    res = corpus(*CLEAN)
    assert res["rule_errors"] == []
    hits = {r: v for r, v in res["rules"].items() if v["findings"] and ":" not in r and r not in CONTEXT_ONLY}
    assert hits == {}


@pytest.mark.skipif(not ARCHIVE, reason="archive corpus not on this host")
def test_archive_floors():
    res = corpus(*ARCHIVE)
    r = res["rules"]
    files = lambda k: r[k]["files_pipeline"] + r[k]["files_tests"]  # noqa: E731
    assert res["rule_errors"] == []
    assert files("S01") >= 120 and files("S03") >= 70 and files("S07") >= 150 and r["S12"]["files_tests"] >= 30


@pytest.mark.skipif(not (GOLDEN / "pipeline").is_dir(), reason="golden lux pipeline not written yet")
def test_golden_pipeline_clean():
    """Model-written step modules only: the vendored runtime is skipped by --all when byte-identical."""
    steps = sorted(str(p) for p in (GOLDEN / "pipeline").glob("[sm][0-9][0-9]*.py"))
    assert steps
    cp = subprocess.run(["/usr/bin/python3", "-I", str(CODESCAN), "--file", *steps, "--as", "pipeline", "--json"],
                        capture_output=True, text=True)
    rep = json.loads(cp.stdout)
    assert cp.returncode == 0 and rep["counts"]["block"] == 0, rep["findings"]
