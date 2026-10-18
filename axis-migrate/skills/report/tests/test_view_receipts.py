"""Sealed view receipts (GeoLibre map views): seal binds the view projects, ladder.py counts a view receipt exactly
like a PNG receipt and never higher; a project changed after the seal stops its receipts counting (VIEW_CHANGED,
information only, never EVIDENCE INCOMPLETE)."""
from __future__ import annotations

import sys
from pathlib import Path

import axis_common as ac
import ladder
import report  # noqa: E402
import seal as sealmod
import synth

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "verify" / "tests"))
import _viewsbuilder as vb  # noqa: E402

FAMS = {"ds.a_mask": "raster", "ds.a_outline": "vector", "ds.b_exposure": "raster"}


def receipts(rd: Path, doc: dict, *, flag=None, **over) -> list[dict]:
    out = []
    for ds in FAMS:
        f = vb.view_receipt(doc, ds, flag=(ds == flag))
        f.update(over if FAMS[ds] == "raster" else {})
        f.update(run_id=vb.run_id_of(rd), finding_id=f"vf-{ds}-view", receipt="view", rubric_id="rubric.v1",
                 rubric_sha256="2" * 64, created_at="2026-10-16T12:00:00Z")
        out.append(f)
    return out


def view_run(proj, *, flag=None, shift_new=False, findings=True, counts=None, before_seal=None, **over) -> dict:
    r = synth.make_run(proj, 1, visual="none", seal=False, shift_new=shift_new)
    rd = r["run_dir"]
    doc = vb.make_views(rd, FAMS, counts=counts)
    fs = receipts(rd, doc, flag=flag, **over) if findings else []
    ac.dump_json(rd / "verify" / "visual-review.json", {"schema": "axis-migrate.visual-review.v1",
                                                         "rubric_id": "rubric.v1", "rubric_sha256": "2" * 64,
                                                         "findings": fs})
    if before_seal:
        before_seal(rd)
    r.update(sealmod.seal(proj["migration"], r["run_id"]))
    r["doc"] = doc
    return r


def test_seal_binds_view_projects(proj):
    r = view_run(proj)
    ev = ac.load_json(r["run_dir"] / "verify" / "evidence.json")
    views = [o for o in ev["objects"] if o["kind"] == "view"]
    assert len(views) == (4 + 3) * 2 + 2          # two rasters (views + cell projects), one vector
    names = {(o["dataset"], o["meta"]["view"]) for o in views}
    assert ("ds.a_mask", "nodata") in names and ("ds.a_mask", "cell-interior") in names
    assert ("ds.a_outline", "worst") in names and ("ds.a_outline", "edges") not in names
    vis = [o for o in ev["objects"] if o["kind"] == "vis"]
    assert all(o["meta"]["receipt"] == "view" and o["refs"] for o in vis)
    assert all(ref in {o["id"] for o in views} for o in vis for ref in o["refs"])


def test_view_receipts_reach_the_png_rung_and_no_higher(proj, tmp_path):
    png = synth.build_project(tmp_path / "png")
    synth.make_run(png, 1)
    want = ladder.compute(png["migration"])
    view_run(proj)
    got = ladder.compute(proj["migration"])
    assert got["pipeline"]["rung"] == want["pipeline"]["rung"] == "reference-verified"
    assert got["integrity"] == []
    for ds in FAMS:
        assert got["datasets"][ds]["state"] == want["datasets"][ds]["state"]
        assert got["datasets"][ds]["visual"]["view_receipts"] == 1


def test_view_flag_on_pass_is_needs_review(proj):  # the PNG flag's effect (test_ladder.test_visual_flag_on_pass)
    view_run(proj, flag="ds.a_mask")
    res = ladder.compute(proj["migration"])
    d = res["datasets"]["ds.a_mask"]
    assert d["state"] == "NEEDS_REVIEW" and "VISUAL_FLAG" in d["reason_codes"]
    assert res["steps"]["s01_mask"]["disposition"] == "NEEDS_REVIEW"
    assert res["pipeline"]["rung"] == "sandbox-validated"


def test_view_receipt_cannot_lift_fail(proj):
    view_run(proj, shift_new=True)
    res = ladder.compute(proj["migration"])
    assert res["datasets"]["ds.b_exposure"]["state"] == "FAIL"
    assert res["datasets"]["ds.b_exposure"]["visual"]["receipts"] == 1
    assert res["pipeline"]["rung"] != "reference-verified"


def test_project_changed_after_seal_stops_counting(proj):
    r = view_run(proj)
    p = r["run_dir"] / "verify" / "views" / "ds.a_mask" / "edges.geolibre.json"
    synth.make_writable(p.parent)
    p.write_text(p.read_text() + " ")
    res = ladder.compute(proj["migration"])
    assert any(x["code"] == "VIEW_CHANGED" and x["severity"] == "info" for x in res["integrity"])
    assert not res["pipeline"]["incomplete"]
    d = res["datasets"]["ds.a_mask"]
    assert "VISUAL_REVIEW_MISSING" in d["reason_codes"] and d["visual"]["invalid_receipts"] == 1
    assert res["pipeline"]["rung"] == "sandbox-validated"
    assert "VISUAL_REVIEW_MISSING" not in res["datasets"]["ds.b_exposure"]["reason_codes"]


def test_project_changed_before_seal_is_not_bound(proj):
    def touch(rd):
        p = rd / "verify" / "views" / "ds.b_exposure" / "worst.geolibre.json"
        p.write_text(p.read_text() + " ")
    r = view_run(proj, before_seal=touch)
    ev = ac.load_json(r["run_dir"] / "verify" / "evidence.json")
    assert not any(o["kind"] == "view" and o["path"].endswith("ds.b_exposure/worst.geolibre.json")
                   for o in ev["objects"])
    res = ladder.compute(proj["migration"])
    assert "VISUAL_REVIEW_MISSING" in res["datasets"]["ds.b_exposure"]["reason_codes"]
    assert res["integrity"] == []


def test_hand_written_bad_view_receipts_do_not_count(proj):
    view_run(proj, engine="cog-tiler-wasm")
    res = ladder.compute(proj["migration"])
    for ds in ("ds.a_mask", "ds.b_exposure"):
        assert "VISUAL_REVIEW_MISSING" in res["datasets"][ds]["reason_codes"], ds
    assert "VISUAL_REVIEW_MISSING" not in res["datasets"]["ds.a_outline"]["reason_codes"]


def test_nothing_found_against_validity_stripes_does_not_count(proj):
    view_run(proj, counts={"ds.b_exposure": {"0": 0, "1": 12, "2": 12, "3": 80}})
    res = ladder.compute(proj["migration"])
    assert "VISUAL_REVIEW_MISSING" in res["datasets"]["ds.b_exposure"]["reason_codes"]
    assert "VISUAL_REVIEW_MISSING" not in res["datasets"]["ds.a_mask"]["reason_codes"]


def test_amend_adds_views_and_view_receipts(proj):
    r = view_run(proj, findings=False)
    rd = r["run_dir"]
    assert ladder.compute(proj["migration"])["pipeline"]["rung"] == "sandbox-validated"
    adoc = vb.make_views(rd, FAMS, root="amend/views")
    ac.dump_json(rd / "amend" / "visual-review.json", {"schema": "axis-migrate.visual-review.v1",
                                                         "amends": "verify/visual-review.json",
                                                         "findings": receipts(rd, adoc)})
    res = sealmod.amend(proj["migration"], r["run_id"])
    assert {a["kind"] for a in res["added"]} == {"view", "vis"}
    lad = ladder.compute(proj["migration"])
    assert lad["integrity"] == [] and lad["evidence_id"] == res["evidence_id"]
    assert lad["pipeline"]["rung"] == "reference-verified"
    # a view receipt citing the sealed verify/views projects after the seal counts as well
    p = rd / "amend" / "visual-review.json"
    doc = ac.load_json(p)
    more = receipts(rd, r["doc"])
    for f in more:
        f["finding_id"] += "-verify"
    doc["findings"] += more
    synth.make_writable(rd / "amend")
    ac.dump_json(p, doc)
    res2 = sealmod.amend(proj["migration"], r["run_id"])
    assert {a["kind"] for a in res2["added"]} == {"vis"}
    lad = ladder.compute(proj["migration"])
    assert lad["pipeline"]["rung"] == "reference-verified"
    assert lad["datasets"]["ds.a_mask"]["visual"]["view_receipts"] == 2


def test_report_prints_identified_points_and_labels_png_receipts(proj, tmp_path):
    """IDENTIFY-UPDATE §3–4: a view receipt's points as read off the MapLibre page; a PNG receipt says it has none."""
    view_run(proj)
    report.build(proj["migration"])
    html = (proj["migration"] / "report" / "index.html").read_text(encoding="utf-8")
    md = (proj["migration"] / "report" / "REPORT.md").read_text(encoding="utf-8")
    want = "Clicked 3 point(s): edge legacy 1 · new nodata; interior legacy 10.5678 · new 10.5678; worst legacy " \
           "12.25 · new 3.125"
    assert want in html and want in md
    png = synth.build_project(tmp_path / "png")
    synth.make_run(png, 1, looked=True)
    report.build(png["migration"])
    assert "PNG review, no map values" in (png["migration"] / "report" / "index.html").read_text(encoding="utf-8")
    assert "PNG review, no map values" in (png["migration"] / "report" / "REPORT.md").read_text(encoding="utf-8")
