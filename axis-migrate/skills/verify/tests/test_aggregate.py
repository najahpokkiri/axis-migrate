"""Aggregation (06 §4d): precedence, visual receipts and flags, blame (06 §4e), no rung, exit codes."""
import numpy as np

import _runbuilder as rb
import axis_common as ac
import compare
from conftest import legacy_raster_array, run_cli, write_raster
from test_run import project


def _plots(run, ds, name="overview.png", content=b"\x89PNG fake"):
    d = run / "verify" / "plots" / ds
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(content)
    sha = ac.sha256_bytes(content)
    ac.dump_json(d / "plots.json", {"generator": "test", "dataset_id": ds, "png": [{"name": name, "sha256": sha}]})
    return sha


def _review(run, findings):
    ac.dump_json(run / "verify" / "visual-review.json",
                 {"schema": "axis-migrate.visual-review.v1", "rubric_id": "rubric.v1", "rubric_sha256": "x",
                  "findings": findings})


def _finding(run, ds, sha, answer="no", effect="none", fid=None, qid="Q1"):
    return {"finding_id": fid or f"vf-{ds}-{qid}-{answer}", "run_id": run.name, "dataset_id": ds,
            "question_id": qid, "category": "none" if answer == "no" else "whole_offset", "answer": answer,
            "effect": effect, "plot": f"plots/{ds}/overview.png", "plot_sha256": sha, "panel": "diff",
            "location": None, "note": "test"}


def _receipts_for_all(run, extra=()):
    f = []
    for ds in ("ds.a", "ds.b"):
        f.append(_finding(run, ds, _plots(run, ds)))
    f.extend(extra)
    _review(run, f)


def test_worst_precedence():
    assert ac.worst(["PASS_EXACT", "NOT_COMPARED", "NEEDS_REVIEW"]) == "NEEDS_REVIEW"
    assert ac.worst(["PASS_EXACT", "PASS_WITHIN_TOLERANCE"]) == "PASS_WITHIN_TOLERANCE"
    assert ac.worst(["EVALUATOR_ERROR", "FAIL"]) == "FAIL"
    assert ac.worst([]) is None


def test_visual_review_missing(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    v, rc = compare.aggregate(run)
    assert rc != 0
    assert v["pipeline"]["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_REVIEW_MISSING" in v["pipeline"]["reason_codes"]
    assert v["datasets"]["ds.a"]["verdict"] == "NEEDS_REVIEW"
    assert v["datasets"]["ds.c"]["verdict"] == "PASS_EXACT"       # tables need no visual receipt
    assert "ladder" not in v and "rung" not in str(sorted(v))
    assert (run / "verify" / "verdict.json").exists()


def test_receipts_make_pipeline_pass(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    _receipts_for_all(run)
    v, rc = compare.aggregate(run)
    assert v["pipeline"]["verdict"] == "PASS_EXACT", v["pipeline"]
    assert rc == 0
    p = run_cli("aggregate", "--run", run)
    assert p.returncode == 0, p.stdout + p.stderr


def test_flag_moves_only_pass_to_needs_review(tmp_path):
    a = legacy_raster_array()
    a[3, 30] += 1
    bad_b = write_raster(tmp_path / "bad_b.tif", np.where(a == -9999.0, a, a * 2))
    run = project(tmp_path, outputs={"ds.a": tmp_path / "legacy" / "out" / "a.tif", "ds.b": bad_b,
                                     "ds.c": tmp_path / "legacy" / "out" / "c.csv"})
    compare.run_compare(run)
    sha_a, sha_b = _plots(run, "ds.a"), _plots(run, "ds.b")
    _review(run, [_finding(run, "ds.a", sha_a, "yes", "flag"),           # flag on a PASS
                  _finding(run, "ds.b", sha_b, "no", "none")])           # "looks fine" on a FAIL
    v, _ = compare.aggregate(run)
    assert v["datasets"]["ds.a"]["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_FLAG" in v["datasets"]["ds.a"]["reason_codes"]
    assert v["datasets"]["ds.b"]["verdict"] == "FAIL"                    # nothing moves a state up
    # a flag on a FAIL is attached as a note only
    _review(run, [_finding(run, "ds.a", sha_a), _finding(run, "ds.b", sha_b, "yes", "flag")])
    v, _ = compare.aggregate(run)
    assert v["datasets"]["ds.b"]["verdict"] == "FAIL"
    assert v["datasets"]["ds.b"]["visual"]["flags"] and v["datasets"]["ds.a"]["verdict"] == "PASS_EXACT"


def test_receipt_with_wrong_plot_hash_rejected(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    _plots(run, "ds.a")
    sha_b = _plots(run, "ds.b")
    _review(run, [_finding(run, "ds.a", "0" * 64, "yes", "flag"), _finding(run, "ds.b", sha_b)])
    v, _ = compare.aggregate(run)
    assert v["datasets"]["ds.a"]["verdict"] == "NEEDS_REVIEW"
    assert "VISUAL_REVIEW_MISSING" in v["datasets"]["ds.a"]["reason_codes"]
    assert "VISUAL_FLAG" not in v["datasets"]["ds.a"]["reason_codes"]
    assert v["visual"]["rejected"][0]["why"].startswith("plot_sha256")


def test_flag_without_yes_or_other_run_rejected(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    sha_a, sha_b = _plots(run, "ds.a"), _plots(run, "ds.b")
    bad = _finding(run, "ds.a", sha_a, "no", "flag")
    other = dict(_finding(run, "ds.b", sha_b), run_id="r-other")
    _review(run, [bad, other])
    v, _ = compare.aggregate(run)
    assert len(v["visual"]["rejected"]) == 2
    # cannot_tell is not a receipt and is listed
    _review(run, [_finding(run, "ds.a", sha_a, "cannot_tell"), _finding(run, "ds.b", sha_b)])
    v, _ = compare.aggregate(run)
    assert "VISUAL_REVIEW_MISSING" in v["datasets"]["ds.a"]["reason_codes"]
    assert v["visual"]["not_visually_assessed"]


def _bad(tmp_path, name, factor):
    a = legacy_raster_array()
    a[7, 33] += 4
    return write_raster(tmp_path / name, np.where(a == -9999.0, a, a * factor))


def test_blame_chained_only_inherited(tmp_path):
    legacy = tmp_path / "legacy"
    run = project(tmp_path, outputs={"ds.a": _bad(tmp_path, "bad_a.tif", 1), "ds.b": _bad(tmp_path, "bad_b.tif", 2),
                                     "ds.c": legacy / "out" / "c.csv"})
    compare.run_compare(run)
    v, _ = compare.aggregate(run)
    assert v["first_failing_steps"] == ["s1"]
    assert v["steps"]["s1"]["blame"] == "own"
    assert v["steps"]["s2"]["blame"] == "inherited"
    assert "INHERITED_FROM:ds.a" in v["steps"]["s2"]["reason_codes"]


def _iso(tmp_path, step, out_ds, src, run_id):
    import json
    status = {s: "absent" for s in ("s1", "s2", "s3") if s != step}
    g = json.loads((tmp_path / "migration" / "graph.json").read_text())
    tol = (tmp_path / "migration" / "tolerances.yaml")
    iso = rb.build(tmp_path, g, outputs={out_ds: src}, chain="isolated", step_status=status, run_id=run_id,
                   tolerances=tol.read_text() if tol.exists() else None)
    compare.run_compare(iso)
    return iso


def test_blame_with_isolation_own_accumulated_compensating(tmp_path):
    legacy = tmp_path / "legacy"
    # chained: s1 PASS, s2 PASS, s3 ... use b FAIL in chained, isolation of s2 PASS -> accumulated
    run = project(tmp_path, outputs={"ds.a": legacy / "out" / "a.tif", "ds.b": _bad(tmp_path, "bad_b.tif", 2),
                                     "ds.c": legacy / "out" / "c.csv"})
    compare.run_compare(run)
    iso_s2 = _iso(tmp_path, "s2", "ds.b", legacy / "out" / "b.tif", "r20261016T110000Z-iso00002")
    v, _ = compare.aggregate(run, isolation_runs=[iso_s2])
    assert v["steps"]["s2"]["blame"] == "accumulated"
    assert v["steps"]["s2"]["verdict"] == "FAIL"           # never upgraded by blame
    assert "ACCUMULATED_UPSTREAM_DIFFERENCE" in v["steps"]["s2"]["reason_codes"]
    # isolation FAIL -> own
    iso_bad = _iso(tmp_path, "s2", "ds.b", _bad(tmp_path, "bad_b2.tif", 2), "r20261016T110100Z-iso00003")
    v, _ = compare.aggregate(run, isolation_runs=[iso_bad])
    assert v["steps"]["s2"]["blame"] == "own" and v["first_failing_steps"] == ["s2"]
    # compensating: chained PASS for s1 but isolation FAIL
    iso_s1 = _iso(tmp_path, "s1", "ds.a", _bad(tmp_path, "bad_a.tif", 1), "r20261016T110200Z-iso00004")
    v, _ = compare.aggregate(run, isolation_runs=[iso_s1])
    assert v["steps"]["s1"]["blame"] == "compensating"
    assert v["steps"]["s1"]["verdict"] == "NEEDS_REVIEW"
    assert "COMPENSATING_DEFECT" in v["steps"]["s1"]["reason_codes"]


def test_aggregate_requires_compare_results(tmp_path):
    run = project(tmp_path)
    v, rc = compare.aggregate(run)
    assert rc != 0 and v["pipeline"]["verdict"] == "EVALUATOR_ERROR"


def test_unpaired_legacy_note_blocks_pipeline(tmp_path):
    def mut(g, legacy, root):
        write_raster(legacy / "out" / "orphan.tif", legacy_raster_array())
    run = project(tmp_path, mutate=mut)
    compare.run_compare(run)
    _receipts_for_all(run)
    v, rc = compare.aggregate(run)
    assert v["pipeline"]["verdict"] == "NOT_COMPARED" and "UNPAIRED_LEGACY" in v["pipeline"]["reason_codes"]
    assert rc != 0


def test_aggregate_corrupt_review_is_evaluator_error_exit_2(tmp_path):
    run = project(tmp_path)
    compare.run_compare(run)
    (run / "verify" / "visual-review.json").write_text("{not json")
    p = run_cli("aggregate", "--run", run)
    assert p.returncode == 2 and "CHECKER_EXCEPTION" in p.stdout
