"""Fast unit tests: trace rules, schema subset validator, task plan rules, target lock sources (no dry run)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml

import dryrun as dr
import make_databricks as md
import target_check as tc
import synth_target

R = "/x/root"
V = R + "/Volumes/c/s/v"


def _trace(tmp_path: Path, lines: list[str]) -> list[dict]:
    p = tmp_path / "t.log"
    p.write_text("\n".join(lines) + "\n")
    return dr.parse_trace(p)


def test_trace_rules(tmp_path):
    lines = [
        # sequential write of an output: only the position query lseek(fd, 0, SEEK_CUR) → allowed
        f'1 openat(AT_FDCWD</c>, "{V}/work/a.tif", O_WRONLY|O_CREAT|O_TRUNC|O_CLOEXEC, 0666) = 4<{V}/work/a.tif>',
        f"1 lseek(4<{V}/work/a.tif>, 0, SEEK_CUR) = 0",
        # random READS are allowed
        f'1 openat(AT_FDCWD</c>, "{V}/inputs/p.tif", O_RDONLY) = 5<{V}/inputs/p.tif>',
        f"1 lseek(5<{V}/inputs/p.tif>, 4096, SEEK_SET) = 4096",
        # a seek back on a write fd → TC_SEEK_ON_VOLUME
        f'1 openat(AT_FDCWD</c>, "{V}/work/b.tif", O_RDWR|O_CREAT|O_TRUNC, 0666) = 6<{V}/work/b.tif>',
        f"1 lseek(6<{V}/work/b.tif>, 8, SEEK_SET) = 8",
        f"1 pwrite64(6<{V}/work/b.tif>, \"abc\"..., 3, 0) = 3",
        f"1 ftruncate(6<{V}/work/b.tif>, 0) = 0",
        # SQLite: O_RDWR open of a GPKG, a journal, a record lock
        f'2 openat(AT_FDCWD</c>, "{V}/work/c.gpkg", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC, 0644) = 7<{V}/work/c.gpkg>',
        f'2 openat(AT_FDCWD</c>, "{V}/work/c.gpkg-journal", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC, 0644) = 8<{V}/work/c.gpkg-journal>',
        f"2 fcntl(9<{V}/inputs/z.gpkg>, F_SETLK, {{l_type=F_RDLCK, l_whence=SEEK_SET, l_start=1073741824, l_len=1}}) = 0",
        # the same calls on local scratch are fine
        f'3 openat(AT_FDCWD</c>, "{R}/local_disk0/tmp/axis/x.gpkg", O_RDWR|O_CREAT, 0644) = 3<{R}/local_disk0/tmp/axis/x.gpkg>',
        f"3 pwrite64(3<{R}/local_disk0/tmp/axis/x.gpkg>, \"x\", 1, 0) = 1",
        # unfinished / resumed pairs are joined
        f"4 lseek(6<{V}/work/b.tif>, 16, SEEK_SET <unfinished ...>",
        "4 <... lseek resumed>) = 16",
        "5 connect(3<socket:[1]>, {sa_family=AF_INET, sin_port=htons(443)}, 16) = -1 ENETUNREACH (INJECTED)",
    ]
    calls = _trace(tmp_path, lines)
    f, kept, counts = dr.analyse_trace(calls, R + "/Volumes", R, "t")
    rules = [(x["code"], x["rule"], x["path"].rsplit("/", 1)[-1]) for x in f]
    assert ("TC_SEEK_ON_VOLUME", "seek_on_write_fd", "b.tif") in rules
    assert ("TC_SEEK_ON_VOLUME", "positional_write", "b.tif") in rules
    assert ("TC_SEEK_ON_VOLUME", "ftruncate", "b.tif") in rules
    assert ("TC_SQLITE_ON_VOLUME", "sqlite_rdwr_open", "c.gpkg") in rules
    assert ("TC_SQLITE_ON_VOLUME", "sqlite_side_file", "c.gpkg-journal") in rules
    assert ("TC_SQLITE_ON_VOLUME", "posix_record_lock", "z.gpkg") in rules
    assert not any(x[2] in ("a.tif", "p.tif", "x.gpkg") for x in rules)
    assert sum(1 for x in rules if x[1] == "seek_on_write_fd") == 2        # incl. the resumed one
    assert counts["network_calls"] == 1
    assert all("/x/root" not in k for k in kept if "Volumes" in k)


def _gen(tmp_path) -> tuple[Path, dict]:
    mig = synth_target.build_project(tmp_path / "p")
    return mig, md.generate(mig)


def test_bundle_validates_against_the_subset_and_rejects_drift(tmp_path):
    mig, out = _gen(tmp_path)
    doc = yaml.safe_load(out["files"]["databricks.yml"])
    schema = json.loads(md.SCHEMA_FILE.read_text())
    assert tc.validate(doc, schema, schema) == []
    bad = copy.deepcopy(doc)
    bad["targets"]["dev"]["workspace"] = {"host": "https://example.invalid"}
    assert any("unknown key 'workspace'" in e for e in tc.validate(bad, schema, schema))
    bad = copy.deepcopy(doc)
    job = next(iter(bad["resources"]["jobs"].values()))
    job["tasks"][0]["python_wheel_task"]["parameters"] = "not a list"
    assert tc.validate(bad, schema, schema)
    bad = copy.deepcopy(doc)
    next(iter(bad["resources"]["jobs"].values()))["job_clusters"][0]["new_cluster"]["data_security_mode"] = "NOPE"
    assert tc.validate(bad, schema, schema)
    meta = schema["x-axis-migrate"]
    assert meta["cli_version"] == md.SCHEMA_CLI_VERSION and meta["stricter_than_cli"] is True


def test_task_plan_rules(tmp_path):
    mig = synth_target.build_project(tmp_path / "p")
    g = json.loads((mig / "graph.json").read_text())
    plan = md.task_plan(g, mig / "pipeline")
    assert [t["task_key"] for t in plan["tasks"]] == ["s01_clip_population", "s02_zone_report"]
    assert [u["dataset"] for u in plan["uploads"]] == ["ds.pop", "ds.rates", "ds.zones"]
    # a manual step gets no task; its output becomes an input to upload
    g2 = copy.deepcopy(g)
    g2["steps"][0]["kind"] = "manual"
    plan2 = md.task_plan(g2, mig / "pipeline")
    assert [t["step"] for t in plan2["tasks"]] == ["s02_zone_report"]
    assert {"ds.01_pop_clip"} <= {u["dataset"] for u in plan2["uploads"]}
    assert plan2["skipped"][0]["why"] == "manual step"
    dsm = md.datasets_manifest(g2, plan2)
    assert dsm["datasets"]["ds.01_pop_clip"] == {**dsm["datasets"]["ds.01_pop_clip"], "root": "input",
                                                 "relpath": "05_TEMP/01_pop_clip.tif"}
    # a migrated step without a module fails generation
    g3 = copy.deepcopy(g)
    g3["steps"][1]["new"]["module"] = None
    with pytest.raises(md.Refused) as e:
        md.task_plan(g3, mig / "pipeline")
    assert "STEP_WITHOUT_MODULE:s02_zone_report" in e.value.reasons
    # sanitised task keys that collide fail (no renaming)
    g4 = copy.deepcopy(g)
    for s, sid in zip(g4["steps"], ("s01.a", "s01_a")):
        s["id"] = sid
    with pytest.raises(md.Refused) as e:
        md.task_plan(g4, mig / "pipeline")
    assert any(r.startswith("TC_TASK_KEY_COLLISION") for r in e.value.reasons)
    # a cycle fails
    g5 = copy.deepcopy(g)
    g5["steps"][0]["inputs"].append("ds.zone_stats")
    with pytest.raises(md.Refused) as e:
        md.task_plan(g5, mig / "pipeline")
    assert any(r.startswith("GRAPH_CYCLE") for r in e.value.reasons)


def test_target_lock_sources(tmp_path):
    mig, out = _gen(tmp_path)
    pins = {p["norm"]: p for p in out["pins"]["pins"]}
    for geo in md.GEO_STACK:
        assert pins[geo]["source"] == "geo_stack" and pins[geo]["version"] == pins[geo]["lock_version"]
    rt = json.loads((md.RUNTIMES / "dbr-17.3-lts.json").read_text())
    assert pins["numpy"]["source"] == "runtime" and pins["numpy"]["version"] == rt["python_packages"]["numpy"]
    lock = out["files"]["requirements-target.lock"].decode()
    assert "numpy==" + rt["python_packages"]["numpy"] in lock
    req = md.wheel_requires(out["pins"])
    assert not any(r.startswith("numpy==") for r in req) and any(r.startswith("rasterio==") for r in req)
    assert rt["kind"] == "declared" and rt["source_url"].startswith("https://docs.databricks.com/")
    readme = out["files"]["README-deploy.md"].decode()
    assert "Not checked yet" in readme and "03_INPUT/pop.tif" in readme
    assert tc.check_t10(_write(mig, out))["state"] == "PASS"


def _write(mig: Path, out: dict) -> Path:
    md.write(mig, out)
    return mig / "databricks"


def test_unsupported_and_unconfigured_platform(tmp_path):
    mig = synth_target.build_project(tmp_path / "p")
    p = mig / "params.yaml"
    p.write_text(p.read_text().replace("platform: databricks", "platform: Snowflake"))
    with pytest.raises(md.Refused) as e:
        md.generate(mig)
    assert e.value.code == "TARGET_NOT_SUPPORTED"
    assert md.main([str(mig)]) == 3 and not (mig / "databricks").exists()
    p.write_text(p.read_text().split("target:")[0])
    assert md.main([str(mig)]) == 2 and not (mig / "databricks").exists()
