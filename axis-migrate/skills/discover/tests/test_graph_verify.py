"""T-ver-1: ingest-verify projects compare results, blame origin/inherited; only actor verify writes verify.*."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import graph_synth as gs
import graph as G

RUN = "r20261013T120000Z-abcdef12"


def _cmp(ds: str, step: str, verdict: str, codes: list[str]) -> dict:
    return {"schema": "axis-migrate.compare-result.v1", "run_id": RUN, "mode": "chained", "dataset_id": ds,
            "step_id": step, "verdict": verdict, "reason_codes": codes, "checks": [],
            "plots": f"plots/{ds}/plots.json"}


@pytest.fixture()
def run_dir(tmp_path: Path):
    mdir = gs.make_project(tmp_path)
    v = mdir / "runs" / RUN / "verify"
    (v / "compare").mkdir(parents=True)
    results = [("ds.a_clip", "s01_clip", "PASS_EXACT", []),
               ("ds.b_mask", "s02_mask", "FAIL", ["RASTER_TRANSFORM_MISMATCH"]),
               ("ds.c_stats", "s03_stats", "FAIL", ["ATTRIBUTE_VALUE_MISMATCH"]),
               ("ds.final", "s03_stats", "FAIL", ["RASTER_VALUE_MISMATCH", "INHERITED_FROM:ds.b_mask"]),
               ("ds.table", "s03_stats", "PASS_EXACT", [])]
    for ds, st, verdict, codes in results:
        (v / "compare" / f"{ds}.json").write_text(json.dumps(_cmp(ds, st, verdict, codes)))
        pdir = v / "plots" / ds
        pdir.mkdir(parents=True)
        (pdir / "triptych.png").write_bytes(b"\x89PNG")
        (pdir / "plots.json").write_text(json.dumps({"png": [{"name": "triptych.png", "sha256": "x"}]}))
    (v / "compare" / "ds.b_mask.mismatches.parquet").write_bytes(b"PAR1")
    (v / "verdict.json").write_text(json.dumps({
        "run_id": RUN, "mode": "chained", "pipeline": {"verdict": "FAIL", "reason_codes": []},
        "steps": {"s01_clip": {"verdict": "PASS_EXACT", "blame": None},
                  "s02_mask": {"verdict": "FAIL", "blame": "own", "reason_codes": ["RASTER_TRANSFORM_MISMATCH"]},
                  "s03_stats": {"verdict": "FAIL", "blame": "inherited"}},
        "first_failing_steps": ["s02_mask"]}))
    (v / "visual-review.json").write_text(json.dumps({"schema": "x", "findings": [
        {"finding_id": "f1", "dataset_id": "ds.a_clip", "question_id": "Q1", "category": "whole_offset",
         "answer": "yes", "effect": "flag"},
        {"finding_id": "f2", "dataset_id": "ds.table", "question_id": "Q2", "category": "none",
         "answer": "no", "effect": "none"}]}))
    return mdir


def node(m: Path, nid: str) -> dict:
    return G._index(G.load_graph(m))[nid][1]


def test_ingest_projects_verdicts_and_blame(run_dir: Path):
    G.apply(run_dir, [{"type": "waive_verify", "id": "ds.c_stats", "reason": "kept"}], "user", 1, channel="graph")
    r = gs.run_cli("ingest-verify", str(run_dir), "--run", RUN, "--json")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["datasets"] == ["ds.a_clip", "ds.b_mask", "ds.c_stats", "ds.final", "ds.table"]
    b = node(run_dir, "ds.b_mask")["verify"]
    assert b["verdict"] == "FAIL" and b["blame"] == "origin" and b["reason_codes"] == ["RASTER_TRANSFORM_MISMATCH"]
    assert b["report"] == f"runs/{RUN}/verify/compare/ds.b_mask.json" and len(b["report_sha256"]) == 64
    assert b["plots"] == [f"runs/{RUN}/verify/plots/ds.b_mask/triptych.png"] and b["run_id"] == RUN
    assert node(run_dir, "ds.c_stats")["verify"]["blame"] == "inherited"  # downstream of a failing dataset
    assert node(run_dir, "ds.c_stats")["verify"]["waiver"]["by"] == "user"  # user's waiver kept
    assert node(run_dir, "ds.final")["verify"]["blame"] == "inherited"
    assert node(run_dir, "ds.table")["verify"]["blame"] is None
    a = node(run_dir, "ds.a_clip")["verify"]
    assert a["visual_flag"] == {"finding_ids": ["f1"], "categories": ["whole_offset"], "question_ids": ["Q1"]}
    assert node(run_dir, "ds.table")["verify"]["visual_flag"] is None
    s02, s03 = node(run_dir, "s02_mask")["verify"], node(run_dir, "s03_stats")["verify"]
    assert (s02["verdict"], s02["blame"]) == ("FAIL", "own")
    assert (s03["verdict"], s03["blame"]) == ("FAIL", "inherited") and s03["datasets"] == ["ds.c_stats", "ds.final",
                                                                                           "ds.table"]
    assert G.read_edits(run_dir)[-1]["actor"] == "verify"


def test_blame_computed_without_verdict_json(run_dir: Path):
    (run_dir / "runs" / RUN / "verify" / "verdict.json").unlink()
    G.ingest_verify(run_dir, RUN)
    assert node(run_dir, "s02_mask")["verify"]["blame"] == "own"
    assert node(run_dir, "s03_stats")["verify"]["blame"] == "inherited"
    assert node(run_dir, "s03_stats")["verify"]["verdict"] == "FAIL"
    assert node(run_dir, "s01_clip")["verify"]["blame"] is None


def test_agent_cannot_write_verify(run_dir: Path, tmp_path: Path):
    G.ingest_verify(run_dir, RUN)
    rev = G.load_graph(run_dir)["revision"]
    ops = gs.write_ops(tmp_path / "o.json", [{"type": "set_field", "id": "ds.b_mask", "field": "verify.verdict",
                                              "value": "PASS_EXACT"}])
    r = gs.run_cli("apply", str(run_dir), "--actor", "agent", "--base-rev", str(rev), "--ops", str(ops), "--json")
    assert r.returncode == 4 and json.loads(r.stdout)["error"] == "verify_projection"
    with pytest.raises(G.AclError):  # verify itself may not set the user's waiver
        G.apply(run_dir, [{"type": "set_field", "id": "ds.b_mask", "field": "verify.waiver", "value": {}}],
                "verify", rev)
    with pytest.raises(G.AclError):  # and may not touch anything but verify.*
        G.apply(run_dir, [{"type": "rename", "id": "s01_clip", "title": "x"}], "verify", rev)
    assert node(run_dir, "ds.b_mask")["verify"]["verdict"] == "FAIL"


def test_ingest_rejects_foreign_results(run_dir: Path):
    v = run_dir / "runs" / RUN / "verify" / "compare"
    (v / "ds.nope.json").write_text(json.dumps(_cmp("ds.nope", "s01_clip", "FAIL", [])))
    with pytest.raises(G.ValidationError):
        G.ingest_verify(run_dir, RUN)
    (v / "ds.nope.json").unlink()
    bad = _cmp("ds.a_clip", "s01_clip", "PASS", [])
    (v / "ds.a_clip.json").write_text(json.dumps(bad))
    with pytest.raises(G.ValidationError):
        G.ingest_verify(run_dir, RUN)
    assert gs.run_cli("ingest-verify", str(run_dir), "--run", "r-missing").returncode == 2
