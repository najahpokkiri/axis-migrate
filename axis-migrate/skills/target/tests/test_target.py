"""End-to-end target tests on the synthetic project (09 §7: AT9-01 analogue, 02–07, 09, 11–13).

    axpy -m pytest skills/target/tests -q
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import string
import zipfile
from pathlib import Path

import pytest
import yaml

from conftest import (EXPECTED_RUNG, LADDER, NO_STRACE, PLUGIN, SEAL, TARGET, check, make, manifest,
                      needs_reference_verified, run, set_step, states)
import synth_target

import make_databricks as md

ALL = [f"T{i}" for i in range(1, 14)]


def claim_regex() -> re.Pattern:
    t = (TARGET / "templates" / "claim.v1.md").read_text().strip()
    parts = re.split(r"(\$\{\w+\})", t)
    return re.compile("^" + "".join(r"(.+?)" if re.fullmatch(r"\$\{\w+\}", p) else re.escape(p) for p in parts) + "$")


def ladder(mig: Path) -> dict:
    return run(LADDER, mig, "--json", ok=(0,))[1]


# ---------------------------------------------------------------------------------------------------------------
@needs_reference_verified
def test_happy_path_target_ready_sealed_and_claimed(project):
    mig, run_id = project
    code, out = make(mig)
    assert code == 0, out
    assert out["tasks"] == ["s01_clip_population", "s02_zone_report"]
    bundle = yaml.safe_load((mig / "databricks" / "databricks.yml").read_text())
    job = next(iter(bundle["resources"]["jobs"].values()))
    tasks = {t["task_key"]: t for t in job["tasks"]}
    assert tasks["s02_zone_report"]["depends_on"] == [{"task_key": "s01_clip_population"}]
    assert "depends_on" not in tasks["s01_clip_population"]
    assert job["job_clusters"][0]["new_cluster"]["data_security_mode"] == "SINGLE_USER"
    assert job["job_clusters"][0]["new_cluster"]["num_workers"] == 0
    assert "workspace" not in bundle["targets"]["dev"] and "host" not in json.dumps(bundle)
    code, res = check(mig, run_id)
    assert code == 0, res
    st = states(mig)
    assert sorted(st, key=lambda x: int(x[1:])) == ALL
    assert all(st[c]["state"] == "PASS" for c in ALL), {c: (st[c]["state"], st[c]["reason_codes"]) for c in ALL}
    tm = manifest(mig)
    assert tm["status"] == "target-ready" and tm["deployment_status"] == "not_deployed" and tm["review"] == "not_run"
    m = claim_regex().match(tm["claim"])
    assert m, tm["claim"]
    wheel = next((mig / "databricks" / "dist").glob("*.whl"))
    assert f"`sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()[:12]}`" in tm["claim"]
    assert f"`{run_id}`" in tm["claim"] and "`3` of `3` step and final outputs" in tm["claim"]
    assert "**Not done:** nothing was deployed" in tm["claim"]
    assert tm["claim"] in (mig / "databricks" / "README-deploy.md").read_text()
    # evidence (the run dir is sealed read-only, so it goes to databricks/dryrun/, which seal --amend --target binds)
    ev = mig / "databricks" / "dryrun"
    for f in ("trace.log", "trace-findings.json", "compare-report.json", "rss.json", "layout.json"):
        assert (ev / f).is_file(), f
    lay = json.loads((ev / "layout.json").read_text())
    assert {"from": "/Volumes", "to": "<root>/Volumes"} in lay["prefix_remap"]
    assert lay["pythonpath"] == "unset" and "PYTHONPATH" not in lay["env"]
    assert "/home/" not in (ev / "layout.json").read_text()
    # not target-ready in the ladder until sealed; then target-ready
    lad = ladder(mig)
    assert lad["pipeline"]["rung"] == "reference-verified"
    assert {r["code"] for r in lad["pipeline"]["target"]["reasons"]} == {"TARGET_NOT_SEALED"}
    run(SEAL, mig, "--run", run_id, "--amend", "--target", ok=(0,))
    lad = ladder(mig)
    assert lad["pipeline"]["rung"] == "target-ready", lad["status_line"]
    assert lad["integrity"] == []
    # a hand edit after the seal withdraws it
    p = mig / "databricks" / "README-deploy.md"
    p.write_text(p.read_text() + "\nedited\n")
    assert ladder(mig)["pipeline"]["rung"] != "target-ready"     # (the ladder treats it as tampered evidence)


def test_at9_02_generation_is_deterministic(project, tmp_path):
    mig, run_id = project
    a = md.generate(mig, run_id)
    os.utime(mig / "pipeline" / "s01_clip_population.py", (1, 1))          # mtimes never enter the wheel
    b = md.generate(mig, run_id)
    assert a["files"].keys() == b["files"].keys()
    for k in a["files"]:
        assert a["files"][k] == b["files"][k], k
    code, _ = make(mig)
    assert code == 0
    first = {p.relative_to(mig / "databricks").as_posix(): p.read_bytes()
             for p in (mig / "databricks").rglob("*") if p.is_file()}
    code, _ = make(mig)
    second = {p.relative_to(mig / "databricks").as_posix(): p.read_bytes()
              for p in (mig / "databricks").rglob("*") if p.is_file()}
    assert first == second
    # a wheel: PEP 427 layout, sorted, fixed timestamps, RECORD hashes valid, code tree = the verified tree
    whl = next((mig / "databricks" / "dist").glob("*.whl"))
    assert whl.name == f"axis_pipeline-0.1.0+{run_id.rsplit('-', 1)[1]}-py3-none-any.whl"
    with zipfile.ZipFile(whl) as z:
        infos = z.infolist()
        names = [i.filename for i in infos]
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in infos)
        di = [n for n in names if n.endswith(".dist-info/RECORD")][0].rsplit("/", 1)[0]
        assert {f"{di}/METADATA", f"{di}/WHEEL", f"{di}/entry_points.txt", f"{di}/RECORD"} <= set(names)
        assert "axis-pipeline = axis_pipeline._axis_runtime:main" in z.read(f"{di}/entry_points.txt").decode()
        assert {"axis_pipeline/params.yaml", "axis_pipeline/_datasets.json"} <= set(names)
        for line in z.read(f"{di}/RECORD").decode().splitlines():
            path, h, size = line.split(",")
            if h:
                data = z.read(path)
                assert h == "sha256=" + md._b64(hashlib.sha256(data).digest()) and int(size) == len(data)
    members = md.wheel_members(whl)
    import axis_common as ac
    assert md.wheel_code_tree_hash(members) == ac.tree_hash(mig / "runs" / run_id / "snapshot" / "axis_pipeline")
    # SOURCE_DATE_EPOCH changes the timestamps only
    os.environ["SOURCE_DATE_EPOCH"] = "1700000000"
    try:
        c = md.generate(mig, run_id)
    finally:
        del os.environ["SOURCE_DATE_EPOCH"]
    assert c["files"]["databricks.yml"] == a["files"]["databricks.yml"]   # same wheel name, other bytes
    assert c["wheel_file"] == a["wheel_file"] and c["wheel_sha256"] != a["wheel_sha256"]
    assert md.wheel_code_tree_hash(md.wheel_members(c["files"][f"dist/{c['wheel_file']}"])) == \
        md.wheel_code_tree_hash(members)


def test_at9_03_static_volume_write_fails_t3(project):
    mig, run_id = project
    src = synth_target.S02.replace(
        '    ctx.save_workbook("ds.zone_report", out)\n',
        '    ctx.save_workbook("ds.zone_report", out)\n'
        '    vol = str(ctx.input_path("ds.01_pop_clip")).split("/work/")[0]\n'
        '    zones.to_file(vol + "/outputs/s02_zone_report/extra.gpkg", layer="extra")\n')
    set_step(mig, "s02_zone_report", src)
    assert make(mig)[0] == 0
    code, res = check(mig, run_id)
    assert code == 1
    st = states(mig)
    assert st["T3"]["state"] == "FAIL" and "TC_VOLUME_UNSAFE_WRITE" in st["T3"]["reason_codes"]
    assert any("s02_zone_report.py" in e and ".to_file(" in e for e in st["T3"]["evidence"])
    tm = manifest(mig)
    assert tm["status"] == EXPECTED_RUNG and "T3" in tm["failing"]
    assert tm["claim"].startswith("**Not target-ready (Databricks), not deployed.**")
    if NO_STRACE:
        assert st["T13"]["state"] == "BLOCKED" and st["T13"]["reason_codes"] == ["TC_NO_STRACE"]
    else:      # the trace sees the same write at run time (SQLite on the Volume)
        assert "TC_SQLITE_ON_VOLUME" in st["T13"]["reason_codes"]


def test_at9_04_dynamic_volume_write_caught_by_trace(project):
    mig, run_id = project
    src = synth_target.S01.replace(
        '    ctx.log.info(',
        '    from rasterio import shutil as rio_shutil\n'
        '    vol = str(ctx.input_path("ds.pop")).split("/inputs/")[0]\n'
        '    rio_shutil.copy(str(ctx.output_path("ds.01_pop_clip")), vol + "/work/" + "s01_extra" + ".tif",'
        ' driver="GTiff")\n'
        '    ctx.log.info(')
    set_step(mig, "s01_clip_population", src)
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id)
    assert code == 1
    st = states(mig)
    assert st["T3"]["state"] == "PASS", st["T3"]           # the static scan misses it ...
    assert st["T7"]["state"] == "PASS"
    if NO_STRACE:       # without the trace nothing sees the write: T13 is BLOCKED, so never target-ready
        assert st["T13"]["state"] == "BLOCKED" and st["T13"]["reason_codes"] == ["TC_NO_STRACE"]
    else:
        assert st["T13"]["state"] == "FAIL" and "TC_SEEK_ON_VOLUME" in st["T13"]["reason_codes"]   # ... the trace does not
        tf = json.loads((mig / "databricks" / "dryrun" / "trace-findings.json").read_text())
        hits = [f for f in tf["findings"] if f["code"] == "TC_SEEK_ON_VOLUME"]
        assert hits and all(f["path"].endswith("/work/s01_extra.tif") for f in hits)
    assert manifest(mig)["status"] != "target-ready"


def test_at9_05_gpkg_read_in_place_is_sqlite_on_volume(project):
    mig, run_id = project
    src = synth_target.S02.replace(
        '    zones = ctx.read_vector("ds.zones")\n',
        '    zones = ctx.read_vector("ds.zones")\n'
        '    import pyogrio\n'
        '    vol = str(ctx.input_path("ds.01_pop_clip")).split("/work/")[0]\n'
        '    pyogrio.read_dataframe(vol + "/inputs/03_INPUT/zones.gpkg", layer="zones")\n')
    set_step(mig, "s02_zone_report", src)
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id)
    assert code == 1
    st = states(mig)
    assert "TC_VOLUME_UNSAFE_READ" in st["T3"]["reason_codes"]
    if NO_STRACE:
        assert st["T13"]["state"] == "BLOCKED" and st["T13"]["reason_codes"] == ["TC_NO_STRACE"]
        return
    assert st["T13"]["state"] == "FAIL" and "TC_SQLITE_ON_VOLUME" in st["T13"]["reason_codes"]
    tf = json.loads((mig / "databricks" / "dryrun" / "trace-findings.json").read_text())
    assert any(f["code"] == "TC_SQLITE_ON_VOLUME" and f["path"].endswith("/inputs/03_INPUT/zones.gpkg")
               for f in tf["findings"])


def test_at9_06_dunder_file_fails_statically_and_in_the_dry_run(project):
    mig, run_id = project
    (mig / "analysis").mkdir(exist_ok=True)
    (mig / "analysis" / "lookup.csv").write_text("a,b\n1,2\n")
    src = synth_target.S01.replace(
        'def run(ctx):\n',
        'def run(ctx):\n'
        '    from pathlib import Path\n'
        '    (Path(__file__).resolve().parent.parent / "analysis" / "lookup.csv").read_text()\n')
    set_step(mig, "s01_clip_population", src)
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id)
    assert code == 1
    st = states(mig)
    assert st["T4"]["state"] == "FAIL" and "TC_DUNDER_FILE" in st["T4"]["reason_codes"]
    assert st["T7"]["state"] == "FAIL" and "TC_DRYRUN_TASK_FAILED" in st["T7"]["reason_codes"]
    err = (mig / "databricks" / "dryrun" / "logs" / "s01_clip_population.stderr").read_text()
    assert "FileNotFoundError" in err and "lookup.csv" in err
    assert st["T8"]["state"] == "NOT_RUN"


def test_at9_07_code_drift_after_verify(project):
    mig, run_id = project
    assert make(mig)[0] == 0
    p = mig / "pipeline" / "s01_clip_population.py"
    p.chmod(0o644)
    p.write_text(p.read_text().replace("clipped %d cells", "clipped %d cell(s)"))
    # (a) the bundle is stale: the live tree and a fresh generation both differ
    code, _ = check(mig, run_id)
    st = states(mig)
    assert code == 1 and st["T9"]["state"] == "FAIL"
    assert {"TC_CODE_CHANGED_SINCE_VERIFY", "TC_GENERATED_FILE_EDITED"} <= set(st["T9"]["reason_codes"])
    # (b) regenerated: the wheel itself now carries unverified code
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id)
    st = states(mig)
    assert code == 1 and "TC_CODE_CHANGED_SINCE_VERIFY" in st["T9"]["reason_codes"]
    assert any(e.startswith("wheel package tree") for e in st["T9"]["evidence"])
    assert manifest(mig)["status"] != "target-ready"


def test_at9_09_no_cloud_contact(project, tmp_path):
    mig, run_id = project
    home = tmp_path / "home"
    home.mkdir()
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    marker = tmp_path / "called"
    for tool in ("databricks", "curl", "wget"):
        f = bin_ / tool
        f.write_text(f"#!/bin/sh\necho {tool} \"$@\" >> {marker}\nexit 7\n")
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}
    real_home = Path(os.environ.get("HOME", str(Path.home())))
    env.update(HOME=str(home), PATH=f"{bin_}:{env.get('PATH', '/usr/bin:/bin')}",
               UV_CACHE_DIR=os.environ.get("UV_CACHE_DIR", str(real_home / ".cache" / "uv")),
               UV_PYTHON_INSTALL_DIR=os.environ.get("UV_PYTHON_INSTALL_DIR",
                                                    str(real_home / ".local" / "share" / "uv" / "python")),
               AXIS_TARGET_OFFLINE="1")
    assert not (home / ".databrickscfg").exists()
    assert make(mig, env=env)[0] == 0
    code, res = check(mig, run_id, env=env)
    st = states(mig)
    assert not marker.exists(), marker.read_text()
    assert not any(c["state"] == "NOT_RUN" for c in st.values())       # every check completes offline
    if NO_STRACE:
        assert code == 1 and st["T13"]["state"] == "BLOCKED"
        assert all(st[c]["state"] in ("PASS", "WARN") for c in ALL if c != "T13")
    else:
        assert code == 0, res
        assert all(st[c]["state"] in ("PASS", "WARN") for c in ALL)
        tf = json.loads((mig / "databricks" / "dryrun" / "trace-findings.json").read_text())
        assert all(c["network_calls"] == 0 for c in tf["counts"].values())
    dry = json.loads((mig / "databricks" / "dryrun" / "dryrun.json").read_text())
    assert dry["venv"]["packages_from"] == "uv cache (offline)"
    assert not any(k.startswith("DATABRICKS_") for k in dry["env_keys"])
    # the plugin's target code never shells out to a Databricks CLI or curl
    for f in ("make_databricks.py", "target_check.py", "dryrun.py"):
        src = (TARGET / f).read_text()
        assert not re.search(r"""\[\s*["'](databricks|curl|wget)["']""", src), f


def test_at9_11_identifier_scan(project, tmp_path):
    import target_check as tc
    mig, run_id = project
    base = (mig / "params.yaml").read_text()
    for what, value in (("host", "https://dbc-1a2b3c4d-5e6f.cloud.databricks.com/?o=1234567890123456"),
                        ("e-mail", "someone@example.org"), ("token", "dapi" + "0123456789abcdef" * 2)):
        (mig / "params.yaml").write_text(base.replace("value: rates\n", f"value: rates\n    note: '{value}'\n", 1))
        assert make(mig)[0] == 0
        t10 = tc.check_t10(mig / "databricks")
        assert t10["state"] == "FAIL" and t10["reason_codes"] == ["TC_IDENTIFIER_IN_BUNDLE"], what
        assert value[:12] not in json.dumps(t10)            # matches are redacted in the evidence
    # end to end once (host): T10 FAIL in target-check.json; params changed after verify fails T9 too
    (mig / "params.yaml").write_text(base.replace("value: rates\n", "value: rates\n    note: "
                                                  "'https://adb-1234567890123456.7.azuredatabricks.net'\n", 1))
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id)
    st = states(mig)
    assert code == 1 and st["T10"]["state"] == "FAIL" and "TC_PARAMS_CHANGED" in st["T9"]["reason_codes"]
    (mig / "params.yaml").write_text(base)
    assert make(mig)[0] == 0
    assert tc.check_t10(mig / "databricks")["state"] == "PASS"


def test_at9_12_unsupported_target(tmp_path):
    root = tmp_path / "snow"
    orig = synth_target.PARAMS
    synth_target.PARAMS = orig.replace("platform: databricks", "platform: snowflake")
    try:
        mig, run_id = synth_target.reference_verified(root)
    finally:
        synth_target.PARAMS = orig
    try:
        code, out = make(mig)
        assert code == 3 and out["status"] == "TARGET_NOT_SUPPORTED" and out["code"] == "TARGET_NOT_SUPPORTED"
        assert not (mig / "databricks").exists()
        code, out = check(mig, run_id)
        assert code == 3 and out["code"] == "TARGET_NOT_SUPPORTED"
        assert not (mig / "databricks").exists()
        lad = ladder(mig)
        assert lad["pipeline"]["rung"] == EXPECTED_RUNG
        assert lad["pipeline"]["target"]["state"] == "not_attempted"
    finally:
        from conftest import make_writable
        make_writable(root)


def test_at9_13_no_strace_blocks_t13(project):
    mig, run_id = project
    assert make(mig)[0] == 0
    code, _ = check(mig, run_id, "--no-strace")
    st = states(mig)
    assert code == 1
    assert st["T13"]["state"] == "BLOCKED" and st["T13"]["reason_codes"] == ["TC_NO_STRACE"]
    assert "E2B" in " ".join(st["T13"]["evidence"])
    assert st["T7"]["state"] == "PASS" and st["T8"]["state"] == "PASS"
    assert manifest(mig)["status"] == EXPECTED_RUNG


def test_dry_run_through_a_symlinked_temp_dir(project, tmp_path):
    """macOS TMPDIR lives under /var -> /private/var: the dry-run root must be canonical from the start, so parity
    paths and trace prefixes agree (it once raised in parity and left no target-check.json)."""
    mig, run_id = project
    real = tmp_path / "real-tmp"
    real.mkdir()
    link = tmp_path / "tmp-link"
    link.symlink_to(real, target_is_directory=True)
    env = dict(os.environ, TMPDIR=str(link))
    assert make(mig, env=env)[0] == 0
    check(mig, run_id, env=env)
    st = states(mig)                                       # written, whatever the verdict
    assert st["T7"]["state"] == "PASS", st["T7"]
    assert st["T8"]["state"] == "PASS", st["T8"]        # parity ran on canonical paths
