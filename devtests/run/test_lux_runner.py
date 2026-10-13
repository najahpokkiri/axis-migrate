"""Lux runner tests (dev-only; encode lux facts): AT-4, AT-5, AT-7, AT-12 (local half), AT-13, golden parity.

    ~/axis-migrate-dev/axis-migrate/bin/axpy -m pytest ~/axis-migrate-dev/devtests/run -q

Each test assembles a fresh project (fixture copy + migration/ with the lux graph fixture and the golden pipeline)
under tmp; the fixture itself is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "axis-migrate"
sys.path.insert(0, str(HERE / "lux_project"))
sys.path.insert(0, str(PLUGIN / "skills" / "run"))
import make_lux_project as mlp  # noqa: E402
import runlib as rl  # noqa: E402

os.environ.setdefault("GDAL_PAM_ENABLED", "NO")
AXPY = PLUGIN / "bin" / "axpy"
RUN_LOCAL = PLUGIN / "skills" / "run" / "run_local.py"
FINAL_TIF = "04_FINAL/lux_res_exposure_2020.tif"


def _unlock(root: Path) -> None:
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


@pytest.fixture
def lux(tmp_path):
    mig = mlp.assemble(tmp_path / "proj")
    yield mig
    _unlock(tmp_path / "proj")


def run(mig: Path, *args: str) -> tuple[int, dict]:
    cp = subprocess.run([str(AXPY), str(RUN_LOCAL), str(mig), "--json", *args], capture_output=True, text=True,
                        timeout=900)
    summary = json.loads(cp.stdout.strip().splitlines()[-1])
    man = json.loads((Path(summary["run_dir"]) / "manifest.json").read_text())
    assert cp.returncode == man["exit_code"]
    return cp.returncode, man


def step(man, sid):
    return next(s for s in man["steps"] if s["step_id"] == sid)


def test_at4_missing_landuse_blocks_before_any_step(lux):
    (lux.parent / mlp.LEGACY_NAME / "03_INPUT" / "osm_landuse_lux.gpkg").rename(
        lux.parent / mlp.LEGACY_NAME / "03_INPUT" / "osm_landuse_lux.gpkg.away")
    code, man = run(lux)
    assert code == 12 and man["status"] == "blocked"
    assert man["reason_codes"] == ["INPUT_MISSING:ds.osm_landuse_lux"]
    assert man["steps"] == [] and man["outputs"] == []
    rd = lux / "runs" / man["run_id"]
    assert list((rd / "outputs").iterdir()) == []
    assert not (rd / "stage" / "inputs" / "03_INPUT" / "osm_landuse_lux.gpkg").exists()


def test_at5_reading_04_final_by_absolute_path_is_undeclared(lux, monkeypatch):
    target = (lux.parent / mlp.LEGACY_NAME / FINAL_TIF).resolve()
    mod = lux / "pipeline" / "s04_distribute_value.py"
    src = mod.read_text().replace("def run(ctx):\n", "def run(ctx):\n    import rasterio\n"
                                  f"    with rasterio.open({str(target)!r}) as _peek:\n        _peek.read(1)\n", 1)
    mod.write_text(src)
    # the static scanner blocks this first (S11); switch it off to prove the dynamic ledger on its own
    code, man = run(lux)
    assert code == 11 and "SCAN_BLOCKED" in man["reason_codes"]
    sys.path.insert(0, str(PLUGIN / "skills" / "run"))
    import run_local
    monkeypatch.setattr(rl, "CODESCAN", lux / "no-scanner.py")
    time.sleep(1.05)
    code = run_local.main([str(lux), "--json"])
    runs = sorted(p for p in (lux / "runs").iterdir() if p.is_dir())
    man = json.loads((runs[-1] / "manifest.json").read_text())
    assert code == 11 and "UNDECLARED_READ:s04_distribute_value" in man["reason_codes"]
    und = step(man, "s04_distribute_value")["reads"]["undeclared"]
    assert {"path": f"project:{mlp.LEGACY_NAME}/{FINAL_TIF}", "access": "read"} in und
    assert man["steps_succeeded"] == 4 and man["eligible_for"] == "none"


def test_at7_at12_local_reruns_are_byte_identical_and_match_golden(lux):
    code1, m1 = run(lux)
    time.sleep(1.05)
    code2, m2 = run(lux)
    assert code1 == code2 == 0 and m1["run_id"] != m2["run_id"]
    assert m1["full"] and m2["full"] and m2["eligible_for"] == "sandbox-validated"
    o1 = {o["dataset_id"]: o for o in m1["outputs"]}
    o2 = {o["dataset_id"]: o for o in m2["outputs"]}
    assert set(o1) == set(o2) and len(o1) == 14
    for ds in o1:
        if ds.endswith("_xlsx"):     # openpyxl timestamps: content-identical (verify compares XLSX by content)
            assert o1[ds]["content_sha256"] == o2[ds]["content_sha256"]
        else:                        # GeoTIFF, CSV, GPKG byte-identical (OGR_CURRENT_DATE, PAM off)
            assert o1[ds]["sha256"] == o2[ds]["sha256"], ds
    golden = json.loads((REPO / "evals" / "golden" / "lux" / "out" / "golden.json").read_text())
    if golden["code_hash"] == m1["code"]["code_hash"]:
        for ds, g in golden["outputs"].items():
            key = "content_sha256" if ds.endswith("_xlsx") else "sha256"
            assert g[key] == o1[ds][key], ds


def test_at13_isolated_s03_reads_staged_legacy_feeds(lux):
    legacy = lux.parent / mlp.LEGACY_NAME
    before = {p.relative_to(legacy).as_posix(): rl.sha256_file(p) for p in legacy.rglob("*") if p.is_file()}
    code, man = run(lux, "--chain", "isolated", "--steps", "s03_zonal_population")
    assert code == 0, man["reason_codes"]
    assert man["chain"] == "isolated" and man["eligible_for"] == "none" and man["full"] is False
    feeds = {f["legacy_path"]: f for f in man["legacy_feeds"]}
    assert sorted(feeds) == ["05_TEMP/01_pop_lux.tif", "05_TEMP/02_res_mask.tif"]
    for p, f in feeds.items():
        assert f["sha256"] == before[p] and f["fed_to"] == ["s03_zonal_population"]
        assert f["staged"] == f"stage/legacy_ro/{p}" and f["unchanged_after_run"] is True
    s3 = step(man, "s03_zonal_population")
    assert s3["fed_from"] == {"ds.01_pop_lux": "legacy", "ds.02_res_mask": "legacy", "ds.cantons_lux_edit": "input"}
    rd = lux / "runs" / man["run_id"]
    staged = {p.relative_to(rd / "stage").as_posix() for p in (rd / "stage").rglob("*") if p.is_file()}
    assert staged == {"legacy_ro/05_TEMP/01_pop_lux.tif", "legacy_ro/05_TEMP/02_res_mask.tif",
                      "inputs/03_INPUT/cantons_lux_edit.gpkg"}           # never 04_FINAL, never its own outputs
    assert not os.access(rd / "stage" / "legacy_ro" / "05_TEMP" / "01_pop_lux.tif", os.W_OK)
    after = {p.relative_to(legacy).as_posix(): rl.sha256_file(p) for p in legacy.rglob("*") if p.is_file()}
    assert after == before
    # isolated s03 on the legacy feeds reproduces the legacy s03 outputs
    par = subprocess.run([str(AXPY), str(REPO / "evals" / "golden" / "lux" / "check_parity.py"), str(rd), "--json",
                          "--legacy", str(legacy)], capture_output=True, text=True)
    res = json.loads(par.stdout)["results"]
    assert res["ds.03_pop_res"]["exact"] and res["ds.03_canton_pop"]["exact"]


def test_golden_chained_parity(lux):
    code, man = run(lux)
    assert code == 0
    rd = lux / "runs" / man["run_id"]
    par = subprocess.run([str(AXPY), str(REPO / "evals" / "golden" / "lux" / "check_parity.py"), str(rd), "--json",
                          "--legacy", str(lux.parent / mlp.LEGACY_NAME)], capture_output=True, text=True)
    res = json.loads(par.stdout)["results"]
    assert len(res) == 14
    not_exact = {k for k, v in res.items() if not v["exact"]}
    assert not_exact == {"ds.02_res_2169", "ds.02_res_4326"}
    for k in not_exact:
        assert [d["column"] for d in res[k]["attribute_diffs"]] == ["area_m2"]
        assert res[k]["attribute_diffs"][0]["max_rel"] < 1e-12
        assert res[k]["geometry_exact"] is True
