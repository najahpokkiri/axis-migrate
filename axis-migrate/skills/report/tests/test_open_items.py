"""Owner items in "What needs attention" (BYU e2e B2/B3): open graph questions and a plan approved by standing
instruction are listed and counted, so the section never says "none" while either holds; with or without the
ladder's pipeline.open_questions / plan_approval keys. NO_REFERENCE reasons are phrased, never PHRASE_MISSING (B13)."""
from __future__ import annotations

import re
from pathlib import Path

import axis_common as ac
import ladder
import report
import synth

STANDING = "agent (standing instruction)"


def render(mig: Path):
    code, rep = report.build(mig)
    html = (mig / "report" / "index.html").read_text(encoding="utf-8")
    md = (mig / "report" / "REPORT.md").read_text(encoding="utf-8")
    return rep, html, md


def failures(html: str) -> str:
    i = html.index('<section id="failures">')
    return html[i:html.index("</section>", i)]


def count(html: str) -> int:
    return int(re.search(r'What needs attention <span class="count">(\d+) item', html).group(1))


def _question(g, status="open"):
    g["questions"].append({"id": "q05", "kind": "legacy_bug", "status": status, "node": "s02_total",
                           "header": "Empty amenity classed food"})


def _no_new_keys(monkeypatch):
    """The ladder before builder 3's B2/B3 change: no open_questions / plan_approval in pipeline."""
    real = ladder.compute

    def old(*a, **k):
        res = real(*a, **k)
        res["pipeline"].pop("open_questions", None)
        res["pipeline"].pop("plan_approval", None)
        return res
    monkeypatch.setattr(report.ladder, "compute", old)


def _new_keys(monkeypatch, oq, pa):
    real = ladder.compute

    def new(*a, **k):
        res = real(*a, **k)
        res["pipeline"]["open_questions"] = oq
        res["pipeline"]["plan_approval"] = pa
        return res
    monkeypatch.setattr(report.ladder, "compute", new)


def test_clean_run_still_says_none(sealed, monkeypatch):
    _no_new_keys(monkeypatch)
    rep, html, md = render(sealed["migration"])
    assert count(html) == 0 and "✓ none" in failures(html)
    assert rep["open_questions"] == [] and rep["plan_approval"] is None
    assert "Nothing: every in-scope output is verified." in md


def test_open_question_listed_without_ladder_keys(proj, monkeypatch):
    synth.update_graph(proj["migration"], _question)
    synth.make_run(proj, 1)
    _no_new_keys(monkeypatch)
    rep, html, md = render(proj["migration"])
    f = failures(html)
    assert count(html) == 1 and "✓ none" not in f
    assert "open question q05 (legacy_bug) on s02_total: Empty amenity classed food" in f
    assert "OPEN_QUESTION" in f and rep["open_questions"] == ["q05"]
    assert "open question q05 (legacy_bug) on s02_total" in md and "Nothing: every" not in md
    assert rep["phrase_missing"] == []


def test_answered_question_not_listed(proj, monkeypatch):
    synth.update_graph(proj["migration"], lambda g: _question(g, "answered"))
    synth.make_run(proj, 1)
    _no_new_keys(monkeypatch)
    rep, html, _ = render(proj["migration"])
    assert count(html) == 0 and rep["open_questions"] == []


def test_standing_approval_listed_without_ladder_keys(proj, monkeypatch):
    def standing(g):
        for s in g["steps"]:
            s.setdefault("plan", {})["approved"] = {"by": STANDING, "at": "2026-10-15T20:44:00Z"}
    synth.update_graph(proj["migration"], standing)
    synth.make_run(proj, 1)
    _no_new_keys(monkeypatch)
    rep, html, md = render(proj["migration"])
    f = failures(html)
    assert count(html) == 1 and rep["plan_approval"] == STANDING
    assert ("plan approved by standing instruction (steps: s01_mask, s02_total), not by the user on a shown "
            "plan.md") in f
    assert "PLAN_APPROVED_BY_STANDING_INSTRUCTION" in md


def test_ladder_keys_are_used_and_both_count(sealed, monkeypatch):
    _new_keys(monkeypatch, [{"id": "q05", "kind": "legacy_bug", "header": "Empty amen.", "node": "s02_total"},
                            {"id": "q07", "kind": "lineage", "header": "Which zones?", "node": "ds.zones"}],
              {"by": STANDING, "standing_steps": ["s02_total"]})
    rep, html, md = render(sealed["migration"])
    f = failures(html)
    assert count(html) == 3 and "✓ none" not in f
    assert "open question q07 (lineage) on ds.zones: Which zones?" in f
    assert "(steps: s02_total), not by the user" in f
    assert rep["open_questions"] == ["q05", "q07"] and rep["plan_approval"] == STANDING
    assert md.count("OPEN_QUESTION") == 2


def test_user_approval_and_no_questions_from_ladder_keys(sealed, monkeypatch):
    _new_keys(monkeypatch, [], {"by": "user", "standing_steps": []})
    rep, html, _ = render(sealed["migration"])
    assert count(html) == 0 and rep["plan_approval"] == "user"


def test_no_reference_reason_phrased_not_phrase_missing(proj):
    def unused(g):
        g["datasets"].append({"id": "ds.side_vrt", "kind": "raster", "role": "unused",
                              "legacy": {"path": "inputs/side.vrt", "exists": True},
                              "no_reference_reason": "unused"})
        g["datasets"].append({"id": "ds.odd", "kind": "raster", "role": "unused",
                              "legacy": {"path": "inputs/odd.tif", "exists": True},
                              "no_reference_reason": "kept for the atlas"})
    synth.update_graph(proj["migration"], unused)
    synth.make_run(proj, 1)
    rep, html, _ = render(proj["migration"])
    assert rep["phrase_missing"] == [] and "PHRASE_MISSING" not in html
    assert "no legacy reference exists for this output (no step reads or writes this file)" in html
    assert "no legacy reference exists for this output (kept for the atlas)" in html
    assert "<code>unused</code> unused" not in html


def test_phrases_for_known_no_reference_reasons():
    ph = report.Phrases(Path(report.__file__).parent / "phrases.v1.yaml")
    for r in ("unused", "orphan_legacy_file", "superseded_only", "display_only", "doc_only",
              "version_of_shared_file", "declared_by_user"):
        assert r in ph.no_ref, r
    assert ph.no_reference(None) == ph.no_reference("NO_REFERENCE") == ph.say("NO_REFERENCE")
    assert not ph.missing


# ---- version drift (BYU: a QGIS 3.x model, references produced on QGIS 4.2.3)
def _drift(mig, saved="3.x", ref="4.2.3", where="inventory"):
    inv = ac.load_json(mig / "inventory.json")
    inv["projects"] = [{"path": "scripts/02_total.py", "kind": "qgis_model",
                        "saved_with": {"app": "QGIS", "version": saved, "evidence": "scripts/02_total.py:1"}}]
    env = {"app": "QGIS", "version": ref, "source": "answer:q07"}
    if where == "inventory":
        inv["reference_env"] = env
    else:
        synth.update_graph(mig, lambda g: g["project"].__setitem__("reference_env", env))
    ac.dump_json(mig / "inventory.json", inv)


def test_version_drift_note_under_parity(proj):
    _drift(proj["migration"])
    synth.make_run(proj, 1)
    rep, html, md = render(proj["migration"])
    i = html.index('<section id="parity">')
    par = html[i:html.index("</section>", i)]
    note = re.search(r'<p class="notice version-drift" data-step="s02_total">(.*?)</p>', par, re.S).group(1)
    assert ("Version drift: <code>scripts/02_total.py</code> (step <code>s02_total</code>) was saved with QGIS 3.x "
            "(scripts/02_total.py:1), but the legacy reference outputs were produced with QGIS 4.2.3 (answer:q07)") \
        in note
    assert "Version drift: `scripts/02_total.py`" in md.split("## Parity", 1)[1]
    assert rep["version_drift"][0]["saved"] == "3.x" and rep["version_drift"][0]["reference"] == "4.2.3"


def test_version_drift_reference_env_from_graph(proj):
    _drift(proj["migration"], where="graph")
    synth.make_run(proj, 1)
    rep, _, _ = render(proj["migration"])
    assert [d["step"] for d in rep["version_drift"]] == ["s02_total"]


def test_no_drift_note_same_major_or_unrecorded(proj, tmp_path):
    _drift(proj["migration"], saved="4.0", ref="4.2.3")
    synth.make_run(proj, 1)
    rep, html, md = render(proj["migration"])
    assert rep["version_drift"] == [] and "version-drift" not in html and "Version drift" not in md


def test_no_drift_note_without_records(sealed):
    rep, html, _ = render(sealed["migration"])
    assert rep["version_drift"] == [] and "version-drift" not in html
