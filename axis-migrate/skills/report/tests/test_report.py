"""report.py: rendering from one sealed bundle (07 §4, §6 tests 2–13, 17)."""
from __future__ import annotations

import filecmp
import json
import re
import shutil
from pathlib import Path

import axis_common as ac
import ladder
import report
import seal as sealmod
import synth


def render(mig: Path, run_id=None):
    code, rep = report.build(mig, run_id)
    html = (mig / "report" / "index.html").read_text(encoding="utf-8")
    return code, rep, html


def section(html: str, sid: str) -> str:
    m = re.search(r'<(section|header|div|p)\b[^>]*\sid="%s"' % re.escape(sid), html)
    assert m, sid
    return html[m.start():html.find("</" + m.group(1) + ">", m.start())]


def test_clean_report(sealed):
    code, rep, html = render(sealed["migration"])
    assert code == 0
    assert rep["status"] == "reference-verified" and rep["rung"] == "reference-verified"
    assert rep["steps"] == {"s01_mask": "VERIFIED", "s02_total": "VERIFIED"}
    assert 'id="integrity"' not in html
    assert "✓ none: every in-scope step is verified" in html
    for g in ac.GREYED_RUNGS:
        assert re.search(rf'class="greyed"[^>]*>{g} <span>not attempted</span>', html)
    files = rep["files"]
    assert "index.html" in files and "assets/layers.js" in files
    for rel, h in files.items():
        assert ac.sha256_file(sealed["migration"] / "report" / rel) == h
    # page order (07 §4.1)
    order = [m.group(1) for m in re.finditer(r'<(?:section|header) id="([a-z-]+)"', html)]
    assert order == ["headline", "failures", "graph", "map", "failed-steps", "parity", "passing-steps", "assumptions",
                     "known-differences", "target", "not-checked", "history", "agent-notes", "reproducibility"]


def test_failure_listed_first_unfoldable(proj):  # 07 test 1 (structural half; browser half in test_browser.py)
    synth.make_run(proj, 1, shift_new=True)
    code, rep, html = render(proj["migration"])
    fails = section(html, "failures")
    assert "s02_total" in fails and "shifted by 1 px" in fails
    assert "<details" not in html and "hidden" not in fails
    assert html.index('id="failures"') < html.index('id="graph"') < html.index('id="passing-steps"')
    assert html.index('id="step-s02_total"') < html.index('id="step-s01_mask"')  # failing card before passing card
    assert rep["rung"] == "sandbox-validated"


def test_plot_deleted(sealed):  # 07 test 2
    p = sealed["run_dir"] / "verify" / "plots" / "ds.a_mask" / "overview.png"
    expected = ac.sha256_file(p)
    synth.make_writable(p.parent)
    p.unlink()
    code, rep, html = render(sealed["migration"])
    assert code == 2
    assert rep["status"] == "EVIDENCE_INCOMPLETE" and rep["rung"] is None
    assert "EVIDENCE INCOMPLETE" in section(html, "headline")
    assert "EVIDENCE_FILE_MISSING" in html and expected in html
    assert "plot missing or changed" in html
    assert 'class="reached' not in section(html, "headline")


def test_tolerance_changed_banner_and_same_as_ladder(sealed):  # 07 test 3
    p = sealed["migration"] / "tolerances.yaml"
    p.write_text(p.read_text() + "# edited\n")
    code, rep, html = render(sealed["migration"])
    assert code == 2
    assert "TOLERANCE_CHANGED_AFTER_RUN" in section(html, "integrity")
    assert set(rep["step_rungs"].values()) == {"sandbox-validated"}
    lad = ladder.compute(sealed["migration"])
    assert {s: e["rung"] for s, e in lad["steps"].items()} == rep["step_rungs"]
    assert lad["pipeline"]["rung"] == rep["rung"]


def test_narrative_cannot_lift(sealed):  # 07 test 5
    _, _, before = render(sealed["migration"])
    (sealed["migration"] / "narrative.md").write_text("All steps pass. <script>alert(1)</script> Parity achieved.")
    _, rep, after = render(sealed["migration"])
    cut = '<section id="agent-notes">'
    assert before.split(cut)[0] == after.split(cut)[0]
    notes = section(after, "agent-notes")
    assert "&lt;script&gt;" in notes and "<script>alert" not in after
    assert "Agent notes (model-written, not evidence)" in notes
    assert after.index(cut) > after.index('id="history"')


def test_deterministic_and_path_independent(sealed, tmp_path):  # 07 test 6
    mig = sealed["migration"]
    render(mig)
    first = {p.relative_to(mig / "report"): p.read_bytes() for p in (mig / "report").rglob("*") if p.is_file()}
    render(mig)
    second = {p.relative_to(mig / "report"): p.read_bytes() for p in (mig / "report").rglob("*") if p.is_file()}
    assert first == second
    other = tmp_path / "elsewhere" / "deeper"
    shutil.copytree(sealed["root"], other, symlinks=True)
    synth.make_writable(other)
    code, _, _ = render(other / "migration")
    assert code == 0
    third = {p.relative_to(other / "migration" / "report"): p.read_bytes()
             for p in (other / "migration" / "report").rglob("*") if p.is_file()}
    assert first == third


def test_nothing_verified(proj):  # 07 test 7
    synth.make_run(proj, 1, skip_compare=("ds.a_mask", "ds.a_outline", "ds.b_exposure", "ds.b_by_zone"))
    code, rep, html = render(proj["migration"])
    assert code == 2
    assert rep["status"] == "NOTHING_VERIFIED"
    assert "NOTHING VERIFIED" in section(html, "headline")


def test_coverage_not_compared_in_failures(proj):  # 07 test 8
    synth.make_run(proj, 1, skip_compare=("ds.b_by_zone",))
    _, rep, html = render(proj["migration"])
    fails = section(html, "failures")
    assert "s02_total" in fails and "NOT_COMPARED" in fails
    assert rep["datasets"]["ds.b_by_zone"]["disposition"] == "NOT_COMPARED"


def test_visual_flag_amber_with_picture(proj):  # 07 test 9
    synth.make_run(proj, 1, flag="ds.a_mask")
    _, rep, html = render(proj["migration"])
    assert rep["steps"]["s01_mask"] == "NEEDS_REVIEW"
    card = html[html.index('id="step-s01_mask"'):html.index('id="parity"')]
    assert 'class="card amber"' in html[html.index('id="step-s01_mask"') - 30:html.index('id="step-s01_mask"')]
    assert 'src="assets/plots/ds.a_mask/worst_tiles.png"' in card
    assert "VISUAL_FLAG" in card and "vf-ds.a_mask-q1" in card
    assert rep["rung"] == "sandbox-validated"


def test_fitted_param_shown(proj):  # 07 test 10 (report half)
    t = (proj["migration"] / "params.yaml").read_text()
    t = t.replace("    source: code\n    evidence: [{ref: \"scripts/01_mask.py:4\"",
                  "    source: fitted\n    fitted_against: ds.a_mask\n    evidence: [{ref: \"scripts/01_mask.py:4\"")
    (proj["migration"] / "params.yaml").write_text(t)
    synth.make_run(proj, 1)
    _, rep, html = render(proj["migration"])
    assert rep["step_rungs"]["s01_mask"] == "sandbox-validated"
    card = html[html.index('id="step-s01_mask"'):]
    assert "PARAM_FITTED_TO_REFERENCE" in card
    assert any(a["id"] == "p.threshold" and a["source"] == "fitted" for a in rep["assumptions"])


def test_known_difference_amber_parity_row_fail(proj):  # 07 test 11
    from test_ladder import _kd_run
    _kd_run(proj, "human:najah", "PASS_EXACT")
    _, rep, html = render(proj["migration"])
    assert rep["steps"]["s02_total"] == "ACCEPTED_DIFFERENCE"
    assert rep["datasets"]["ds.b_exposure"]["state"] == "FAIL"
    row = [r for r in section(html, "parity").split("<tr>") if "ds.b_exposure" in r][0]
    assert "✗ FAIL" in row and "ACCEPTED_DIFFERENCE" in row
    assert "KD-001" in section(html, "known-differences")
    assert "s02_total" in section(html, "failures")  # amber is listed, never green


def test_bridge_label(proj):  # 07 test 12
    from test_ladder import test_bridge_suffix
    test_bridge_suffix(proj)
    _, rep, html = render(proj["migration"])
    assert "· partial (1 bridged)" in section(html, "headline")
    assert rep["suffix"] == " · partial (1 bridged)"


def _target_files(mig: Path, run_id: str, *, deployment="not_deployed", t8="PASS"):
    snap = mig / "runs" / run_id / "snapshot"
    db = mig / "databricks"
    db.mkdir(exist_ok=True)
    (db / "databricks.yml").write_text("bundle: {name: demo}\n")
    claim = ("target-ready (Databricks), not deployed. Job files are in migration/databricks/ and were checked offline.\n"
             "Not done: nothing was deployed, no Databricks workspace was contacted, and the code has not run on a "
             "Databricks cluster.")
    ac.dump_json(db / "target-manifest.json", {
        "schema": "axis-migrate.target-manifest.v1", "run_id": run_id, "deployment_status": deployment,
        "runtime": "dbr-test", "claim": claim,
        "hashes": {"pipeline_tree": "sha256:" + ladder.tree_hex(snap / "axis_pipeline"),
                   "params": "sha256:" + ac.sha256_file(snap / "params.yaml"),
                   "lock": "sha256:" + ac.sha256_file(snap / "uv.lock"),
                   "graph": "sha256:" + ac.sha256_file(snap / "graph.json")}})
    checks = [{"id": f"T{i}", "state": "PASS", "reason_codes": [], "evidence": []} for i in range(1, 14)]
    checks[10]["state"] = "WARN"
    checks[7]["state"] = t8
    ac.dump_json(db / "target-check.json", {"checks": checks})
    return claim


def test_target_ready_and_code_drift(sealed):  # 07 test 13 + 09 §4.1/§4.3
    mig = sealed["migration"]
    claim = _target_files(mig, sealed["run_id"])
    lad = ladder.compute(mig)
    assert lad["pipeline"]["rung"] == "reference-verified"
    assert any(r["code"] == "TARGET_NOT_SEALED" for r in lad["pipeline"]["target"]["reasons"])
    sealmod.amend(mig, sealed["run_id"], target=True)
    code, rep, html = render(mig)
    assert rep["rung"] == "target-ready", ladder.compute(mig)["pipeline"]["target"]
    assert code == 0
    assert claim.replace("\n", "") in section(html, "target-claim").replace("\n", "")
    assert "(not deployed)" in section(html, "headline")
    # code drift withdraws target-ready
    p = mig / "pipeline" / "s02_total.py"
    p.write_text(p.read_text() + "# tweak\n")
    code, rep, html = render(mig)
    assert code == 2
    assert rep["rung"] == "reference-verified"
    assert "CODE_CHANGED_AFTER_RUN" in section(html, "integrity")


def test_target_requires_not_deployed_and_t8(sealed):
    mig = sealed["migration"]
    _target_files(mig, sealed["run_id"], deployment="deployed")
    sealmod.amend(mig, sealed["run_id"], target=True)
    lad = ladder.compute(mig)
    assert lad["pipeline"]["rung"] == "reference-verified"
    assert any(r["code"] == "TC_DEPLOYMENT_STATUS_INVALID" for r in lad["pipeline"]["target"]["reasons"])


def test_target_t8_fail_blocks(sealed):
    mig = sealed["migration"]
    _target_files(mig, sealed["run_id"], t8="FAIL")
    sealmod.amend(mig, sealed["run_id"], target=True)
    assert ladder.compute(mig)["pipeline"]["rung"] == "reference-verified"


def test_fitted_holdout_blocks_target_ready(proj):
    from test_ladder import _set_param
    _set_param(proj["migration"], "p.threshold", "    source: fitted\n    resolution: inferred\n    status: assumed\n"
               "    fit: {reference: ds.a_mask, split: {kind: zones}, result: {best: 60, best_score: 0, "
               "holdout_score: 0, unique: true}}\n")
    r = synth.make_run(proj, 1)
    _target_files(proj["migration"], r["run_id"])
    sealmod.amend(proj["migration"], r["run_id"], target=True)
    lad = ladder.compute(proj["migration"])
    assert lad["pipeline"]["rung"] == "reference-verified"
    assert any(x["code"] == "PARAM_FITTED_NOT_CONFIRMED" for x in lad["pipeline"]["target"]["reasons"])


def test_run_history_keeps_failing_run(proj):  # 07 test 17
    synth.make_run(proj, 1, shift_new=True)
    synth.make_run(proj, 2)
    code, rep, html = render(proj["migration"])
    hist = section(html, "history")
    assert synth.run_id_for(1) in hist and synth.run_id_for(2) in hist
    row1 = [r for r in hist.split("<tr") if synth.run_id_for(1) in r][0]
    assert "sandbox-validated" in row1 and ">1<" in row1  # one FAIL
    code1, rep1, html1 = render(proj["migration"], synth.run_id_for(1))  # the failing one still renders
    assert rep1["rung"] == "sandbox-validated" and "s02_total" in section(html1, "failures")


def test_no_sealed_run_page(proj):
    code, rep, html = render(proj["migration"])
    assert code == 3
    assert "No sealed run: nothing verified" in html


def test_phrase_missing_rendered_literally(proj):
    checks = synth.default_checks({"kind": "table"})
    checks[2].update(state="FAIL", reason_codes=["SOME_FUTURE_CODE"])
    synth.make_run(proj, 1, compares={"ds.b_by_zone": ("FAIL", checks, ["SOME_FUTURE_CODE"])})
    _, rep, html = render(proj["migration"])
    assert "SOME_FUTURE_CODE" in section(html, "failures")
    assert rep["phrase_missing"] == ["SOME_FUTURE_CODE"]
    assert "PHRASE_MISSING" in html


def test_signoff_link_when_present(sealed):
    _, _, html = render(sealed["migration"])
    assert "SIGN-OFF.md" not in section(html, "caveats")
    (sealed["migration"] / "SIGN-OFF.md").write_text("# Sign-off\n")
    _, _, html = render(sealed["migration"])
    assert 'href="../SIGN-OFF.md"' in section(html, "caveats")


def test_tolerance_unsigned_phrase(proj):
    checks = synth.default_checks({"kind": "raster"})
    checks[-1].update(state="NEEDS_DECISION", reason_codes=["TOLERANCE_UNSIGNED"])
    synth.make_run(proj, 1, compares={"ds.b_exposure": ("NEEDS_DECISION", checks, ["TOLERANCE_UNSIGNED"])})
    _, rep, html = render(proj["migration"])
    assert "proposed tolerance that has not been signed" in section(html, "failures")
    assert rep["steps"]["s02_total"] == "NEEDS_DECISION"


def test_assets_copied_only_after_hash_check(sealed):
    _, rep, _ = render(sealed["migration"])
    assert "assets/plots/ds.a_mask/overview.png" in rep["files"]
    p = sealed["run_dir"] / "verify" / "plots" / "ds.a_mask" / "overview.png"
    synth.make_writable(p.parent)
    p.write_bytes(b"not the sealed plot")
    _, rep2, _ = render(sealed["migration"])
    assert "assets/plots/ds.a_mask/overview.png" not in rep2["files"]


def test_secret_in_bundle(proj):
    r = synth.make_run(proj, 1, seal=False)
    (r["run_dir"] / "logs" / "s01_mask.stdout").chmod(0o644)
    (r["run_dir"] / "logs" / "s01_mask.stdout").write_text("api_key = 'ABCDEFGHIJKLMNOPQRST'\n")
    res = sealmod.seal(proj["migration"], r["run_id"])
    assert res["secret_matches"] == 1
    lad = ladder.compute(proj["migration"])
    assert "SECRET_IN_BUNDLE" in {x["code"] for x in lad["integrity"]}
    assert lad["pipeline"]["incomplete"]


def test_transform_phrase_sign():  # rehearsal D6: +y pixels = south on a north-up grid (e < 0)
    from pathlib import Path as _P
    ph = report.Phrases(_P(report.__file__).parent / "phrases.v1.yaml")
    txt = ph.say("RASTER_TRANSFORM_MISMATCH", {"value": 2.0, "threshold": 1e-6,
                                               "diagnostics": {"shift_px": [1, 2]}})
    assert "1 px east, 2 px south" in txt
    assert "north" not in txt.split("(")[0]
