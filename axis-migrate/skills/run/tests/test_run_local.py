"""Runner acceptance tests on the synthetic project (05 §6 AT-1…AT-11, AT-13, AT-15 + timeouts/resume/scan)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

import runlib as rl
import synth
from conftest import LEDGER, NO_STRACE, RUN_SKILL, HERE, scanner_blocks, step_rec

S01, S02 = "s01_clip_population", "s02_zonal_sum"


def _graph_edit(mig: Path, fn) -> None:
    synth.edit_graph(mig, fn)


# ---------------------------------------------------------------------------------------------------------------
def test_clean_chained_run_is_sealed_and_complete(runner, project):
    code, man = runner()
    assert code == 0, man["reason_codes"]
    assert man["status"] == "succeeded" and man["full"] is True and man["chain"] == "chained"
    assert man["steps_planned"] == man["steps_succeeded"] == 2
    # with strace or (macOS) the audit+CPL_DEBUG fallback, a lock-matched chained run may support sandbox-validated;
    # the fallback records that it can never support more (D5-9)
    assert man["eligible_for"] == "sandbox-validated" and man["read_ledger"] == LEDGER
    assert man["read_ledger_rung_cap"] == ("sandbox-validated" if NO_STRACE else None)
    rd = runner.run_dir(man)
    assert man["run_id"].startswith("r") and man["run_id"].endswith(man["code"]["code_hash"].split(":")[1][:8])
    # outputs, hashes, write ledger
    outs = {o["dataset_id"]: o for o in man["outputs"]}
    assert set(outs) == {"ds.01_pop_clip", "ds.zone_sum", "ds.zone_stats"}
    for o in outs.values():
        assert rl.sha256_file(rd / o["path"]) == o["sha256"]
    s2 = step_rec(man, S02)
    assert s2["writes_ledger"] == ["outputs/s02_zonal_sum/zone_stats.gpkg", "outputs/s02_zonal_sum/zone_sum.csv"]
    assert s2["fed_from"] == {"ds.01_pop_clip": "run", "ds.zones": "input"}
    assert s2["reads"]["undeclared"] == []
    # snapshot, env, io.json
    assert (rd / "snapshot" / "axis_pipeline" / "s01_clip_population.py").is_file()
    for f in ("params.yaml", "graph.json", "inventory.json", "tolerances.yaml", "uv.lock", "pyproject.toml"):
        assert (rd / "snapshot" / f).is_file(), f
    env = man["env"]
    assert env["freeze_matches_lock"] is True and env["native"]["rasterio.gdal"].startswith("3.")
    assert env["proj"]["network_enabled"] is False and env["stub_check"]["passed"] is True
    io = json.loads((rd / "logs" / f"{S02}.io.json").read_text())
    assert io["params_read"] == {"p.min_value": 0.5}
    assert {w["path"] for w in io["writes"]} == {"outputs/s02_zonal_sum/zone_stats.gpkg",
                                                 "outputs/s02_zonal_sum/zone_sum.csv"}
    assert all(not w["path"].startswith("/") for w in io["reads"] + io["writes"])
    res = json.loads((rd / "logs" / f"{S01}.result.json").read_text())
    assert res["ok"] is True and res["stub_check"]["passed"] is True
    assert res["python_flags"] == {"hash_randomization": 0, "dont_write_bytecode": True,
                                   "ignore_environment": False, "no_user_site": True}
    assert not list((rd / "snapshot").rglob("__pycache__"))
    # sealed: checksums intact, files read-only, verify/ writable, index appended
    assert rl.verify_checksums(rd) == []
    assert not os.access(rd / "manifest.json", os.W_OK) and not os.access(rd / "outputs", os.W_OK)
    assert os.access(rd / "verify", os.W_OK)
    assert sorted(p.name for p in (rd / "verify").iterdir()) == ["scan.json", "tests.json"]
    assert not (rd / "scratch").exists()
    idx = [json.loads(line) for line in (project / "runs" / "index.jsonl").read_text().splitlines()]
    assert idx[-1]["run_id"] == man["run_id"] and idx[-1]["status"] == "succeeded"
    assert man["tests"]["status"] == "passed" and man["tests"]["tests"] == 1
    assert man["code"]["worktree_changed_during_run"] is False


def test_at1_step_exception_stops_the_run(runner, project):
    synth.set_step(project, S02, synth.S02.replace("    ctx.write_vector(", "    raise ValueError('boom')\n"
                                                                       "    ctx.write_vector("))
    code, man = runner()
    assert code == 10 and man["status"] == "failed"
    assert man["steps_succeeded"] == 1
    s2 = step_rec(man, S02)
    assert any(r.startswith("STEP_EXCEPTION") for r in s2["reason_codes"])
    assert s2["exception"]["type"] == "ValueError" and "s02_zonal_sum.py" in s2["exception"]["innermost_frame"]
    rd = runner.run_dir(man)
    assert (rd / "outputs" / f"{S02}.failed").is_dir() and not (rd / "outputs" / S02).exists()
    assert (rd / "outputs" / S01).is_dir()


def test_at1_first_step_failure_never_starts_the_next(runner, project):
    synth.set_step(project, S01, synth.S01.replace("    pop, _ =", "    raise ValueError('early')\n    pop, _ ="))
    code, man = runner()
    assert code == 10 and man["steps_succeeded"] == 0
    assert [s["step_id"] for s in man["steps"]] == [S01]
    assert not (runner.run_dir(man) / "logs" / f"{S02}.stdout").exists()


def test_at2_swallowed_exception_without_output_is_a_contract_failure(runner, project, no_scan, monkeypatch):
    body = synth.S02.replace("def run(ctx):\n", "def run(ctx):\n    try:\n        raise RuntimeError('x')\n"
                                                "    except Exception:\n        ctx.log.warning('ignored')\n"
                                                "        return\n")
    synth.set_step(project, S02, body)
    code, man = runner()
    assert code == 11, man["reason_codes"]
    assert any(r.startswith("OUTPUT_MISSING:ds.zone_s") for r in man["reason_codes"])
    assert scanner_blocks(runner, monkeypatch)   # the scanner also flags the handler (S01)


def test_at3_sys_exit_inside_a_step_is_a_failure(runner, project, no_scan):
    synth.set_step(project, S01, synth.S01.replace("    pop, _ =", "    import sys\n    sys.exit(0)\n    pop, _ ="))
    code, man = runner()
    assert code == 10
    s1 = step_rec(man, S01)
    assert f"STEP_CALLED_EXIT:{S01}" in s1["reason_codes"]
    assert s1["exception"]["type"] == "SystemExit"


def test_at4_missing_input_blocks_before_any_step(runner, project):
    (project.parent / synth.LEGACY / "03_INPUT" / "zones.gpkg").unlink()
    code, man = runner()
    assert code == 12 and man["status"] == "blocked"
    assert "INPUT_MISSING:ds.zones" in man["reason_codes"]
    assert man["steps"] == [] and man["outputs"] == []
    assert list((runner.run_dir(man) / "outputs").iterdir()) == []


def test_changed_input_and_new_pam_sidecar_block(runner, project):
    pop = project.parent / synth.LEGACY / "03_INPUT" / "pop.tif"
    aux = pop.with_name("pop.tif.aux.xml")
    aux.write_text("<PAMDataset/>")
    code, man = runner()
    assert code == 12 and "LEGACY_REF_CHANGED:ds.pop:aux.xml" in man["reason_codes"]
    aux.unlink()
    with open(pop, "r+b") as fh:
        fh.seek(-1, 2)
        last = fh.read(1)
        fh.seek(-1, 2)
        fh.write(bytes([last[0] ^ 1]))
    time.sleep(1.05)
    code, man = runner()
    assert code == 12 and "INPUT_CHANGED:ds.pop" in man["reason_codes"]


def test_at5_undeclared_read_of_a_legacy_final_by_absolute_path(runner, project, no_scan, monkeypatch):
    final = (project.parent / synth.LEGACY / "04_FINAL" / "zone_sum.csv").resolve()
    synth.set_step(project, S02, synth.S02.replace(
        "def run(ctx):\n", f"def run(ctx):\n    open({str(final)!r}).read()\n"))
    code, man = runner()
    assert code == 11 and f"UNDECLARED_READ:{S02}" in man["reason_codes"]
    und = step_rec(man, S02)["reads"]["undeclared"]
    assert {"path": f"project:{synth.LEGACY}/04_FINAL/zone_sum.csv", "access": "read"} in und
    assert scanner_blocks(runner, monkeypatch)   # S11 absolute path literal


def test_at5_gdal_level_read_of_a_legacy_intermediate_is_seen(runner, project, no_scan):
    legacy_tif = (project.parent / synth.LEGACY / "05_TEMP" / "01_pop_clip.tif").resolve()
    synth.set_step(project, S02, synth.S02.replace(
        "def run(ctx):\n", f"def run(ctx):\n    import rasterio\n    rasterio.open({str(legacy_tif)!r}).close()\n"))
    code, man = runner()
    assert code == 11
    und = step_rec(man, S02)["reads"]["undeclared"]
    assert any(u["path"].endswith("05_TEMP/01_pop_clip.tif") for u in und)


def test_undeclared_write_into_the_project(runner, project, no_scan):
    target = (project / "analysis" / "leak.txt").resolve()
    synth.set_step(project, S01, synth.S01.replace(
        "def run(ctx):\n", f"def run(ctx):\n    import os\n    os.makedirs({str(target.parent)!r}, exist_ok=True)\n"
                           f"    open({str(target)!r}, 'w').write('x')\n"))
    code, man = runner()
    assert code == 11 and f"UNDECLARED_WRITE:{S01}" in man["reason_codes"]


def _stub_source(pkgs: list[str], purge: bool) -> str:
    pre = (HERE / "fixtures" / "stub_preamble_archived.py").read_text().replace("__STUB_PKGS__", repr(pkgs))
    if purge:
        pre += "for _m in list(_sys.modules):\n    if _m.split('.')[0] in _STUB_PKGS:\n        del _sys.modules[_m]\n"
    return pre + synth.S01


@pytest.mark.parametrize("purge", [False, True], ids=["finder_installed", "real_modules_replaced"])
def test_at6_archived_stub_preamble_is_refused(runner, project, purge, no_scan, monkeypatch):
    synth.set_step(project, S01, _stub_source(["numpy", "pandas", "geopandas", "rasterio", "shapely", "pyproj",
                                               "boto3"], purge))
    code, man = runner()
    assert code == 11, man["reason_codes"]
    s1 = step_rec(man, S01)
    assert f"STUB_DETECTED:{S01}" in s1["reason_codes"] and s1["stub_check_passed"] is False
    res = json.loads((runner.run_dir(man) / "logs" / f"{S01}.result.json").read_text())
    problems = " ".join(res["stub_check"]["post"]["problems"])
    assert "meta_path" in problems
    assert man["eligible_for"] == "none"
    assert scanner_blocks(runner, monkeypatch)   # S05 stub injection (sys.meta_path.insert)


def test_at7_reruns_are_byte_identical(runner, project):
    code1, m1 = runner()
    time.sleep(1.1)
    code2, m2 = runner()
    assert code1 == code2 == 0 and m1["run_id"] != m2["run_id"]
    o1 = {o["dataset_id"]: o["sha256"] for o in m1["outputs"]}
    o2 = {o["dataset_id"]: o["sha256"] for o in m2["outputs"]}
    assert o1 == o2  # GeoTIFF, CSV and GPKG byte-identical (OGR_CURRENT_DATE)
    assert all(step_rec(m2, s)["action"] == "executed" for s in (S01, S02))


def test_at8_edit_during_run_is_recorded_and_snapshot_code_runs(project):
    slow = synth.S01.replace("def run(ctx):\n", "def run(ctx):\n    import time\n    time.sleep(4)\n")
    synth.set_step(project, S01, slow)
    original_s02 = (project / "pipeline" / f"{S02}.py").read_bytes()
    axpy = RUN_SKILL.parents[1] / "scripts" / "axpy"
    proc = subprocess.Popen([str(axpy), str(RUN_SKILL / "run_local.py"), str(project), "--json"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        if list((project / "runs").glob(f"*/logs/{S01}.stdout")):
            break
        time.sleep(0.1)
    time.sleep(0.5)
    (project / "pipeline" / f"{S02}.py").write_text(synth.S02 + "\n# edited during the run\n")
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 0, err
    summary = json.loads(out)
    man = json.loads((Path(summary["run_dir"]) / "manifest.json").read_text())
    assert man["code"]["worktree_changed_during_run"] is True and "pipeline" in man["code"]["worktree_changes"]
    import hashlib
    assert step_rec(man, S02)["module_sha256"] == hashlib.sha256(original_s02).hexdigest()


def test_at9_immutability(runner, project):
    code, man = runner("--run-stamp", "20260101T000000Z")
    assert code == 0
    rd = runner.run_dir(man)
    code2, _ = runner("--run-stamp", "20260101T000000Z")
    assert code2 == 15
    with pytest.raises(PermissionError):
        open(rd / "manifest.json", "a").close()
    with pytest.raises(PermissionError):
        (rd / "outputs" / S01 / "extra.tif").write_bytes(b"x")
    import run_local
    assert run_local.main([str(project), "--check-sealed", man["run_id"]]) == 0
    # tamper (as the owner could) → detected, and resume from it is refused
    victim = rd / "outputs" / S01 / "01_pop_clip.tif"
    os.chmod(victim, 0o644)
    victim.write_bytes(victim.read_bytes() + b"\0")
    assert run_local.main([str(project), "--check-sealed", man["run_id"]]) == 15
    code3, man3 = runner("--resume-from", man["run_id"])
    assert code3 == 15 and any(r.startswith("SEALED_RUN_TOUCHED") for r in man3["reason_codes"])


def test_at10_proj_network_forced_off_inside_the_step(runner, project, monkeypatch, no_scan):
    monkeypatch.setenv("PROJ_NETWORK", "ON")
    monkeypatch.setenv("PYTHONPATH", "/nonexistent/stub/path")
    synth.set_step(project, S01, synth.S01.replace(
        "def run(ctx):\n", "def run(ctx):\n    import os, pyproj.network\n"
                           "    ctx.log.info('PROJ_NETWORK=%s enabled=%s', os.environ.get('PROJ_NETWORK'),"
                           " pyproj.network.is_network_enabled())\n"))
    code, man = runner()
    assert code == 0
    rd = runner.run_dir(man)
    res = json.loads((rd / "logs" / f"{S01}.result.json").read_text())
    assert res["proj"] == {"PROJ_NETWORK": "OFF", "network_enabled": False}
    assert "PROJ_NETWORK=OFF enabled=False" in (rd / "logs" / f"{S01}.stderr").read_text()


def test_at11_lock_not_synced_is_refused(runner, project, monkeypatch, tmp_path):
    fake = tmp_path / "uv.lock"
    text = rl.LOCK.read_text()
    assert 'name = "numpy"\nversion = "2.5.4"' in text
    fake.write_text(text.replace('name = "numpy"\nversion = "2.5.4"', 'name = "numpy"\nversion = "2.5.3"'))
    monkeypatch.setattr(rl, "LOCK", fake)
    code, man = runner()
    assert code == 13 and man["status"] == "blocked"
    assert "FREEZE_MISMATCH" in man["reason_codes"] and "FREEZE_MISMATCH:lock_not_synced" in man["reason_codes"]
    assert man["steps"] == []


def test_env_major_mismatch_blocks_unless_allowed(runner, project, monkeypatch):
    real = rl.reference_env_record

    def fake(python, crs):
        r = real(python, crs)
        r["gdal"] = "4.0.0"
        return r
    monkeypatch.setattr(rl, "reference_env_record", fake)
    code, man = runner()
    assert code == 13 and "ENV_MAJOR_MISMATCH:gdal" in man["reason_codes"]
    time.sleep(1.05)
    code, man = runner("--allow-env-major")
    assert code == 0 and man["eligible_for"] == "none"
    assert next(r for r in man["env_comparison"] if r["key"] == "gdal")["allowed_by"] == "--allow-env-major"


def test_at13_isolated_step_reads_staged_legacy_copies(runner, project):
    legacy = project.parent / synth.LEGACY
    before = {p: rl.sha256_file(p) for p in legacy.rglob("*") if p.is_file()}
    code, man = runner("--chain", "isolated", "--steps", S02)
    assert code == 0, man["reason_codes"]
    assert man["chain"] == "isolated" and man["eligible_for"] == "none" and man["full"] is False
    feeds = man["legacy_feeds"]
    assert [f["legacy_path"] for f in feeds] == ["05_TEMP/01_pop_clip.tif"]
    assert feeds[0]["sha256"] == rl.sha256_file(legacy / "05_TEMP" / "01_pop_clip.tif")
    assert feeds[0]["staged"] == "stage/legacy_ro/05_TEMP/01_pop_clip.tif" and feeds[0]["fed_to"] == [S02]
    assert step_rec(man, S02)["fed_from"] == {"ds.01_pop_clip": "legacy", "ds.zones": "input"}
    rd = runner.run_dir(man)
    assert not (rd / "stage" / "legacy_ro" / "04_FINAL").exists()
    assert {p: rl.sha256_file(p) for p in legacy.rglob("*") if p.is_file()} == before


def test_chained_subset_without_parent_is_refused(runner, project):
    code, man = runner("--steps", S02)
    assert code == 12 and "UPSTREAM_NOT_IN_PLAN:ds.01_pop_clip" in man["reason_codes"]


def test_timeout_then_resume_reruns_only_the_unfinished_step(runner, project):
    synth.set_step(project, S02, synth.S02.replace("def run(ctx):\n", "def run(ctx):\n    import time\n"
                                                                       "    time.sleep(6)\n"))
    _graph_edit(project, lambda g: next(s for s in g["steps"] if s["id"] == S02).update(timeout_s=3))
    code, man = runner()
    assert code == 14 and man["status"] == "timed_out" and man["steps_succeeded"] == 1
    assert step_rec(man, S02)["timed_out"] is True
    parent = man["run_id"]
    _graph_edit(project, lambda g: next(s for s in g["steps"] if s["id"] == S02).update(timeout_s=60))
    time.sleep(1.05)
    code, man2 = runner("--resume-from", parent)
    assert code == 0, man2["reason_codes"]
    assert step_rec(man2, S01)["action"] == "reused" and step_rec(man2, S01)["reused_from"] == parent
    assert step_rec(man2, S02)["action"] == "executed"
    assert step_rec(man2, S02)["fed_from"]["ds.01_pop_clip"] == f"reused:{parent}"
    assert man2["full"] is True and man2["resumed_from"] == parent
    o = {x["dataset_id"]: x for x in man2["outputs"]}
    assert o["ds.01_pop_clip"]["reused_from"] == parent


def test_resume_refused_when_code_changed(runner, project):
    code, man = runner()
    assert code == 0
    synth.set_step(project, S02, synth.S02 + "\n# changed\n")
    time.sleep(1.05)
    code, man2 = runner("--resume-from", man["run_id"])
    assert code == 12 and "RESUME_MISMATCH:code_hash" in man2["reason_codes"]


def test_param_missing_is_blocked_not_a_code_failure(runner, project, no_scan):
    synth.set_step(project, S01, synth.S01.replace('ctx.param("p.clip_nodata")', 'ctx.param("p.not_declared")'))
    code, man = runner()
    assert code == 12 and man["status"] == "blocked"
    assert "PARAM_MISSING:p.not_declared" in man["reason_codes"]


def test_module_contract_mismatch(runner, project, no_scan):
    synth.set_step(project, S01, synth.S01.replace('INPUTS = ["ds.pop", "ds.zones"]', 'INPUTS = ["ds.pop"]'))
    code, man = runner()
    assert code == 10 and f"MODULE_CONTRACT_MISMATCH:{S01}" in man["reason_codes"]


def test_vendored_runtime_must_be_byte_identical(runner, project):
    rt = project / "pipeline" / "_axis_runtime.py"
    rt.write_text(rt.read_text() + "\n# local tweak\n")
    code, man = runner()
    assert code == 11 and "RUNTIME_MODIFIED:_axis_runtime.py" in man["reason_codes"]
    import run_local
    assert run_local.main([str(project), "--vendor-runtime"]) == 0
    time.sleep(1.05)
    code, man = runner()
    assert code == 0


def test_scan_block_stops_before_steps(runner, project, monkeypatch, tmp_path):
    fake = tmp_path / "codescan.py"
    fake.write_text("import json, sys\nprint(json.dumps({'schema': 'fake', 'findings': [{'rule': 'S05'}]}))\n"
                    "sys.exit(2)\n")
    monkeypatch.setattr(rl, "CODESCAN", fake)
    code, man = runner()
    assert code == 11 and "SCAN_BLOCKED" in man["reason_codes"] and man["steps"] == []
    scan = json.loads((runner.run_dir(man) / "verify" / "scan.json").read_text())
    assert scan["findings"] == [{"rule": "S05"}] and scan["exit_code"] == 2


@pytest.mark.parametrize("how", ["python_open", "rasterio_gdal", "pyogrio_gdal", "listdir"])
def test_audit_fallback_ledger_when_strace_is_missing(runner, project, monkeypatch, no_scan, how):
    """No strace (macOS): Python-level and GDAL-level forbidden reads are still caught (audit hook, CPL_DEBUG)."""
    import run_local
    monkeypatch.setattr(run_local, "STRACE", None)
    legacy = (project.parent / synth.LEGACY).resolve()
    body = {"python_open": f"    open({str(legacy / '04_FINAL' / 'zone_sum.csv')!r}).read()\n",
            "rasterio_gdal": f"    import rasterio\n    rasterio.open({str(legacy / '05_TEMP' / '01_pop_clip.tif')!r}).close()\n",
            "pyogrio_gdal": f"    import pyogrio\n    pyogrio.read_dataframe({str(legacy / '04_FINAL' / 'zone_stats.gpkg')!r})\n",
            "listdir": f"    import os\n    os.listdir({str(legacy / '04_FINAL')!r})\n"}[how]
    synth.set_step(project, S02, synth.S02.replace("def run(ctx):\n", "def run(ctx):\n" + body))
    code, man = runner()
    assert man["read_ledger"] == "audit+cpl_debug" and man["eligible_for"] == "none"
    assert code == 11 and f"UNDECLARED_READ:{S02}" in man["reason_codes"]


def test_audit_fallback_has_a_write_ledger_and_can_be_sandbox_validated(runner, project, monkeypatch):
    import run_local
    monkeypatch.setattr(run_local, "STRACE", None)
    code, man = runner()
    assert code == 0 and man["read_ledger"] == "audit+cpl_debug"
    assert man["eligible_for"] == "sandbox-validated" and man["read_ledger_rung_cap"] == "sandbox-validated"
    assert step_rec(man, S02)["writes_ledger"] == ["outputs/s02_zonal_sum/zone_stats.gpkg",
                                                   "outputs/s02_zonal_sum/zone_sum.csv"]
    assert step_rec(man, S01)["reads"]["declared_opened"] == ["stage/inputs/03_INPUT/pop.tif",
                                                              "stage/inputs/03_INPUT/zones.gpkg"]


def test_a_blind_ledger_refuses(runner, project, monkeypatch):
    """If the ledger did not see a read the runtime recorded, it cannot vouch for anything: refuse."""
    import run_local
    monkeypatch.setattr(run_local, "STRACE", None)
    real = rl.parse_audit
    monkeypatch.setattr(rl, "parse_audit", lambda p, e, cwd=None: [x for x in real(p, e, cwd) if not x["path"].endswith("/pop.tif")])
    code, man = runner()
    assert code == 11 and "LEDGER_BLIND:ds.pop" in man["reason_codes"]


@pytest.mark.skipif(not rl.REFENV_PY.is_file(), reason="reference (QGIS) environment not installed")
def test_at15_refenv_control_run(runner, project):
    code, m1 = runner()
    assert code == 0
    time.sleep(1.05)
    code, m2 = runner("--env", "refenv")
    assert code == 0, m2["reason_codes"]
    assert m2["executor"] == "refenv" and m2["eligible_for"] == "none"
    assert m2["env"]["native"]["rasterio.gdal"] == m2["reference_env"]["gdal"]
    assert m2["env"]["python"] != m1["env"]["python"]
    ctl = m2["env_control"]
    assert ctl["against_run"] == m1["run_id"]
    assert set(ctl["byte_identical"] + ctl["content_identical"] + ctl["differ"]) == {
        "ds.01_pop_clip", "ds.zone_sum", "ds.zone_stats"}


def test_uv_missing_is_an_environment_refusal(project, monkeypatch):
    import run_local
    monkeypatch.setattr(rl, "find_uv", lambda: None)
    assert run_local.main([str(project)]) == 13
    assert not (project / "runs").exists() or not any((project / "runs").iterdir())


def test_scan_binds_to_the_snapshot(runner, project, monkeypatch, tmp_path):
    code, man = runner()
    assert code == 0
    if rl.CODESCAN.is_file():
        assert man["scan"]["bound_to_snapshot"] is True and man["scan"]["code_hash"] == man["code"]["code_hash"]
    # a scanner that sees different code than the snapshot (edited mid-preflight) is refused
    fake = tmp_path / "codescan.py"
    target = project / "pipeline" / f"{S02}.py"
    fake.write_text("import json, sys, pathlib\n"
                    f"p = pathlib.Path({str(target)!r}); p.write_text(p.read_text() + '\\n# sneaked in\\n')\n"
                    "print(json.dumps({'findings': []}))\n")
    monkeypatch.setattr(rl, "CODESCAN", fake)
    time.sleep(1.05)
    code, man = runner()
    assert code == 11 and "SCAN_TARGET_CHANGED:pipeline" in man["reason_codes"] and man["steps"] == []


def test_tolerance_proposals_are_snapshotted_but_optional(runner, project):
    code, man = runner()
    assert code == 0 and man["tolerances_proposed"] is None
    prop = project / "tolerances.proposed.yaml"
    prop.write_text("version: 1\nrecords: []\n")
    time.sleep(1.05)
    code, man = runner()
    assert code == 0 and man["tolerances_proposed"] == {"sha256": rl.sha256_file(prop)}
    assert (runner.run_dir(man) / "snapshot" / "tolerances.proposed.yaml").read_bytes() == prop.read_bytes()
    assert man["code"]["worktree_changed_during_run"] is False


def test_unapproved_plan_is_refused(runner, project):
    _graph_edit(project, lambda g: next(s for s in g["steps"] if s["id"] == S02).update(plan=None))
    code, man = runner()
    assert code == 11 and man["reason_codes"] == [f"PLAN_NOT_APPROVED:{S02}"] and man["steps"] == []
    assert man["preflight"]["plan_approval"] == {"status": "refused", "checked": [S01, S02], "unapproved": [S02]}
    # only steps this run executes need approval: an isolated run of s01 is fine
    time.sleep(1.05)
    code, man = runner("--chain", "isolated", "--steps", S01)
    assert code == 0 and man["preflight"]["plan_approval"]["status"] == "ok"


def test_graph_integrity_failure_refuses(runner, project, monkeypatch, tmp_path):
    code, man = runner()
    assert code == 0 and man["preflight"]["graph_integrity"]["status"] in ("ok", "not_available")
    if man["preflight"]["graph_integrity"]["status"] == "ok":   # the real check: a direct write is refused
        g = json.loads((project / "graph.json").read_text())
        g["steps"][0]["timeout_s"] = 61
        (project / "graph.json").write_text(json.dumps(g))
        time.sleep(1.05)
        code, man = runner()
        assert code == 11 and man["reason_codes"] == ["GRAPH_EDITED_OUTSIDE_APPLY"] and man["steps"] == []
    fake = tmp_path / "graph.py"
    fake.write_text("import sys\nif '--help' in sys.argv:\n    print('usage: graph.py {check-integrity,apply}')\n"
                    "    sys.exit(0)\nprint('graph.json sha differs from the last apply revision')\nsys.exit(3)\n")
    monkeypatch.setattr(rl, "GRAPH_PY", fake)
    time.sleep(1.05)
    code, man = runner()
    assert code == 11 and man["reason_codes"] == ["GRAPH_EDITED_OUTSIDE_APPLY"] and man["steps"] == []
    gi = man["preflight"]["graph_integrity"]
    assert gi["status"] == "failed" and gi["exit_code"] == 3 and "differs" in gi["output_tail"]
