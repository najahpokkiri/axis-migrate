"""Run-dir mode: frozen contract, live-vs-snapshot, reference pins, pairing codes, sweep, stale results, exit codes."""
import json
import os
import shutil

import numpy as np
import pytest

import _runbuilder as rb
import axis_common as ac
import compare
from conftest import (legacy_raster_array, run_cli, table_rows, tol_record, write_csv, write_raster)


def project(tmp_path, *, mutate=None, **build_kw):
    """s1 -> ds.a (raster) ; s2(ds.a) -> ds.b (raster) ; s3(ds.b) -> ds.c (csv, final)."""
    legacy = tmp_path / "legacy"
    a = legacy_raster_array()
    write_raster(legacy / "in" / "src.tif", a)
    write_raster(legacy / "out" / "a.tif", a)
    write_raster(legacy / "out" / "b.tif", np.where(a == -9999.0, a, a * 2))
    write_csv(legacy / "out" / "c.csv", table_rows())
    steps = [rb.step("s1"), rb.step("s2"), rb.step("s3")]
    dsets = [rb.dataset("ds.src", "raster", "input", "in/src.tif"),
             rb.dataset("ds.a", "raster", "intermediate", "out/a.tif", produced_by="s1"),
             rb.dataset("ds.b", "raster", "intermediate", "out/b.tif", produced_by="s2"),
             rb.dataset("ds.c", "table", "final", "out/c.csv", produced_by="s3", compare={"key": ["id"]})]
    g = rb.graph(steps, dsets, reads=[("s1", "ds.src"), ("s2", "ds.a"), ("s3", "ds.b")])
    if mutate:
        mutate(g, legacy, tmp_path)
    outs = build_kw.pop("outputs", None)
    if outs is None:
        outs = {"ds.a": legacy / "out" / "a.tif", "ds.b": legacy / "out" / "b.tif", "ds.c": legacy / "out" / "c.csv"}
    return rb.build(tmp_path, g, outputs=outs, **build_kw)


def test_clean_run_all_pass_and_files_written(tmp_path):
    run = project(tmp_path)
    results, rc = compare.run_compare(run)
    assert rc == 0
    assert {k: v["verdict"] for k, v in results.items()} == {"ds.a": "PASS_EXACT", "ds.b": "PASS_EXACT",
                                                             "ds.c": "PASS_EXACT"}
    v = run / "verify"
    assert (v / "contract.json").exists() and (v / "pairing.json").exists()
    r = ac.load_json(v / "compare" / "ds.a.json")
    assert r["schema"] == "axis-migrate.compare-result.v1"
    assert r["contract_hash"] == ac.load_json(v / "contract.json")["contract_hash"]
    assert r["refs"]["out"].startswith("out:") and r["refs"]["ref"].startswith("ref:")
    assert r["refs"]["tol"] == []                     # no tolerances file in this project
    assert r["new"]["path"].startswith("migration/runs/") and r["legacy"]["path"] == "legacy/out/a.tif"
    assert not os.path.isabs(r["new"]["path"])
    assert r["pairing"]["method"] == "lineage" and r["pairing"]["evidence"] == ["scripts/s1.py:5"]
    assert "CANDIDATE_IDENTICAL_TO_REFERENCE" in r["diagnostic_codes"]   # diagnostic only


def test_contract_hash_deterministic(tmp_path):
    run = project(tmp_path)
    c1 = compare.build_contract(compare.RunContext(run))
    c2 = compare.build_contract(compare.RunContext(run))
    assert c1["contract_hash"] == c2["contract_hash"] and c1["contract_hash"].startswith("sha256:")
    body = {k: v for k, v in c1.items() if k not in ("contract_hash", "created_at")}
    assert c1["contract_hash"] == "sha256:" + ac.sha256_bytes(ac.cjson(body))


def test_live_tolerances_edited_after_run(tmp_path):
    run = project(tmp_path, tolerances="version: 1\nrecords: []\n")
    rb.edit_live(run, "tolerances.yaml", "version: 2\nrecords: []\n")
    results, rc = compare.run_compare(run)
    assert rc != 0
    for r in results.values():
        assert r["verdict"] == "EVALUATOR_ERROR" and "TOLERANCE_CHANGED_AFTER_RUN" in r["reason_codes"]


def test_snapshot_tampered_vs_manifest_pin(tmp_path):
    run = project(tmp_path, tolerances="version: 1\nrecords: []\n")
    snap = run / "snapshot" / "tolerances.yaml"
    snap.write_text("version: 1\nrecords: []\n# edited\n")
    rb.edit_live(run, "tolerances.yaml", snap.read_text())
    results, _ = compare.run_compare(run)
    assert all("TOLERANCE_CHANGED_AFTER_RUN" in r["reason_codes"] for r in results.values())


def test_contract_changed_between_compare_calls(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    c = ac.load_json(run / "verify" / "contract.json")
    c["references"]["out/a.tif"]["sha256"] = "0" * 64
    ac.dump_json(run / "verify" / "contract.json", c)
    results, rc = compare.run_compare(run, datasets=["ds.a"])
    assert rc != 0 and "TOLERANCE_CHANGED_AFTER_RUN" in results["ds.a"]["reason_codes"]


def test_live_graph_pairing_changed(tmp_path):
    run = project(tmp_path)
    g = rb.live_graph(run)
    g["datasets"][1]["compare"] = {"measure": "extensive"}
    rb.set_live_graph(run, g)
    results, rc = compare.run_compare(run)
    assert all("GRAPH_CHANGED_AFTER_RUN" in r["reason_codes"] for r in results.values())
    # projections that verify itself writes (verify.*, ladder) are not part of the pairing contract
    run2 = project(tmp_path / "p2")
    g2 = rb.live_graph(run2)
    g2["datasets"][1]["verify"]["verdict"] = "PASS_EXACT"
    g2["ladder"] = {"project": "generated"}
    rb.set_live_graph(run2, g2)
    assert compare.run_compare(run2)[1] == 0


def test_reference_modified_is_evaluator_error_not_fail(tmp_path):
    run = project(tmp_path)
    p = tmp_path / "legacy" / "out" / "a.tif"
    a = legacy_raster_array()
    a[1, 1] += 1
    write_raster(p, a)
    results, rc = compare.run_compare(run)
    assert results["ds.a"]["verdict"] == "EVALUATOR_ERROR"
    assert results["ds.a"]["reason_codes"] == ["REFERENCE_HASH_MISMATCH"]
    assert results["ds.b"]["verdict"] == "PASS_EXACT"


def test_unpaired_legacy_and_sweep(tmp_path):
    def mut(g, legacy, root):
        g["datasets"][2]["new"] = None                    # ds.b not ported
        write_raster(legacy / "out" / "orphan.tif", legacy_raster_array())
        (legacy / "out" / "orphan.tif.aux.xml").write_text("<PAMDataset/>")
    run = project(tmp_path, mutate=mut)
    results, rc = compare.run_compare(run)
    assert results["ds.b"]["verdict"] == "NOT_COMPARED"
    assert results["ds.b"]["reason_codes"] == ["UNPAIRED_LEGACY"]
    notes = ac.load_json(run / "verify" / "pairing.json")["notes"]
    assert {"code": "UNPAIRED_LEGACY", "path": "out/orphan.tif"} in notes
    assert not any(n["path"].endswith(".aux.xml") for n in notes)
    assert rc != 0


def test_unpaired_new_and_no_reference(tmp_path):
    def mut(g, legacy, root):
        g["datasets"].append(rb.dataset("ds.mem", "raster", "intermediate", None, produced_by="s1",
                                        new_path="s1/mem.tif", legacy_in_memory=True))
        g["datasets"].append(rb.dataset("ds.newonly", "raster", "intermediate", None, produced_by="s1",
                                        new_path="s1/newonly.tif"))
    legacy_a = None
    run = project(tmp_path, mutate=mut)
    del legacy_a
    results, _ = compare.run_compare(run)
    assert results["ds.mem"]["reason_codes"] == ["NO_REFERENCE"]
    assert results["ds.newonly"]["reason_codes"] == ["UNPAIRED_NEW"]


def test_pairing_unconfirmed_without_edge_evidence(tmp_path):
    def mut(g, legacy, root):
        for e in g["edges"]:
            if e["to"] == "ds.a":
                e["evidence"] = []
    run = project(tmp_path, mutate=mut)
    results, _ = compare.run_compare(run)
    assert results["ds.a"]["verdict"] == "NOT_COMPARED"
    assert results["ds.a"]["reason_codes"] == ["PAIRING_UNCONFIRMED"]


def test_lineage_mismatch_and_write_ledger(tmp_path):
    run = project(tmp_path, written_by={"ds.b": "s1"})
    results, _ = compare.run_compare(run)
    assert results["ds.b"]["reason_codes"] == ["LINEAGE_MISMATCH"]
    run2 = project(tmp_path / "p2", ledger_drop=("ds.a",))
    results2, _ = compare.run_compare(run2)
    assert results2["ds.a"]["verdict"] == "FAIL"
    assert results2["ds.a"]["reason_codes"] == ["OUTPUT_NOT_PRODUCED_BY_RUN"]


def test_output_changed_after_run(tmp_path):
    run = project(tmp_path)
    p = run / "outputs" / "s1" / "a.tif"
    write_raster(p, legacy_raster_array() + 1)
    results, _ = compare.run_compare(run)
    assert results["ds.a"]["reason_codes"] == ["OUTPUT_NOT_PRODUCED_BY_RUN"]


def test_step_failed_and_upstream_blocked(tmp_path):
    run = project(tmp_path, outputs={"ds.a": tmp_path / "legacy" / "out" / "a.tif"},
                  step_status={"s2": "failed", "s3": "absent"})
    results, rc = compare.run_compare(run)
    assert results["ds.b"]["verdict"] == "FAIL" and results["ds.b"]["reason_codes"] == ["OUTPUT_MISSING"]
    assert results["ds.c"]["verdict"] == "BLOCKED" and results["ds.c"]["reason_codes"] == ["UPSTREAM_BLOCKED"]


def test_bridged_and_dropped_steps(tmp_path):
    def mut(g, legacy, root):
        g["steps"][1]["kind"] = "bridge"
        g["steps"].append(rb.step("s9", scope="drop"))
        g["datasets"].append(rb.dataset("ds.gone", "raster", "intermediate", "out/gone.tif", produced_by="s9",
                                        exists=False))
    run = project(tmp_path, mutate=mut)
    results, _ = compare.run_compare(run)
    assert results["ds.b"]["verdict"] == "NOT_COMPARED" and results["ds.b"]["reason_codes"] == ["BRIDGED"]
    assert "ds.gone" not in results


def test_isolated_run_compares_only_its_steps(tmp_path):
    run = project(tmp_path, chain="isolated", outputs={"ds.b": tmp_path / "legacy" / "out" / "b.tif"},
                  step_status={"s1": "absent", "s3": "absent"})
    results, rc = compare.run_compare(run)
    assert set(results) == {"ds.b"} and rc == 0


def test_stale_result_deleted_before_writing(tmp_path, monkeypatch):
    run = project(tmp_path)
    compare.run_compare(run)
    stale = run / "verify" / "compare" / "ds.a.mismatches.parquet"
    stale.write_bytes(b"stale")
    compare.run_compare(run, datasets=["ds.a"])
    assert not stale.exists()


def test_dataset_selection_and_exit_codes_cli(tmp_path):
    def mut(g, legacy, root):
        pass
    a = legacy_raster_array()
    a[5, 20] += 3
    bad = write_raster(tmp_path / "bad_b.tif", np.where(a == -9999.0, a, a * 2))
    run = project(tmp_path, mutate=mut, outputs={"ds.a": tmp_path / "legacy" / "out" / "a.tif",
                                                  "ds.b": bad, "ds.c": tmp_path / "legacy" / "out" / "c.csv"})
    p = run_cli("run", "--run", run, "--dataset", "ds.a")
    assert p.returncode == 0, p.stderr
    p = run_cli("run", "--run", run, "--dataset", "ds.b", "--json")
    assert p.returncode == 1
    out = json.loads(p.stdout)
    assert out["datasets"]["ds.b"]["verdict"] == "FAIL"
    p = run_cli("run", "--run", run, "--dataset", "ds.nope")
    assert p.returncode == 2
    assert run_cli("--help").returncode == 0


def test_tolerance_records_in_contract_with_validation(tmp_path):
    rec = tol_record("ds.a", "raster.values", rtol=1e-6, spread=1e-8, wrong=1e-3)
    import yaml
    run = project(tmp_path, tolerances=yaml.safe_dump({"version": 1, "records": [rec]}))
    c = compare.build_contract(compare.RunContext(run))
    assert c["tolerances"]["records"][0]["id"] == rec["id"]
    assert c["tolerances"]["records"][0]["status"] == "signed"
    results, rc = compare.run_compare(run)
    assert results["ds.a"]["refs"]["tol"] == ["tol:" + ac.sha256_file(run / "snapshot" / "tolerances.yaml")]


def test_run_dir_not_under_migration_is_error(tmp_path):
    run = project(tmp_path)
    moved = tmp_path / "elsewhere"
    shutil.copytree(run, moved)
    results, rc = compare.run_compare(moved)
    assert rc != 0
    assert all(r["verdict"] == "EVALUATOR_ERROR" for r in results.values())


@pytest.mark.parametrize("missing", ["manifest.json", "snapshot/graph.json"])
def test_missing_run_files(tmp_path, missing):
    run = project(tmp_path)
    (run / missing).unlink()
    p = run_cli("run", "--run", run)
    assert p.returncode == 2 and "EVALUATOR_ERROR" in (p.stdout + p.stderr)
