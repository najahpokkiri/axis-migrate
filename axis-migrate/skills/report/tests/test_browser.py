"""Headless browser checks (07 §6 tests 1 and 14). Skipped when node, playwright-core or Chrome are absent."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

import report
import synth

HERE = Path(__file__).resolve().parent
PW = Path(os.environ.get("AXIS_PLAYWRIGHT_DIR", Path.home() / "axis-migrate-dev/devtools/report-vendor/node_modules"))
CHROME = os.environ.get("AXIS_CHROME", "/usr/bin/google-chrome")

pytestmark = pytest.mark.skipif(not (shutil.which("node") and (PW / "playwright-core").is_dir()
                                     and Path(CHROME).exists()),
                                reason="node + playwright-core + Chrome needed (devtools/report-vendor)")


def check(html: Path, *extra) -> dict:
    out = subprocess.run(["node", str(HERE / "browser_check.cjs"), str(html), *extra], capture_output=True,
                         text=True, timeout=180)
    data = json.loads(out.stdout or "{}")
    assert "fatal" not in data, data
    return data


def test_failure_in_first_screen(proj, tmp_path):  # 07 test 1
    synth.make_run(proj, 1, shift_new=True)
    report.build(proj["migration"])
    d = check(proj["migration"] / "report" / "index.html", str(tmp_path / "first.png"))
    items = [i for i in d["items"] if "s02_total" in i["text"]]
    assert items and "shifted by 1 px" in items[0]["text"]
    assert items[0]["bottom"] <= 720 and items[0]["visible"]
    assert not d["detailsAncestor"] and not d["hiddenAncestor"]
    assert d["verdict"].strip() in ("sandbox-validated", "generated", "none")


def test_no_swipe_map(sealed):  # map review C8: no swipe.html (its two sides drew differently); cards link the views
    report.build(sealed["migration"])
    rd = sealed["migration"] / "report"
    assert not (rd / "swipe.html").exists() and not (rd / "assets" / "vendor").exists()
    assert "swipe" not in (rd / "index.html").read_text(encoding="utf-8").lower()


def test_map_views_block_renders(proj):  # GeoLibre step 5: the views block renders offline, nothing is fetched
    import test_view_receipts as tvr
    tvr.view_run(proj, flag="ds.a_mask")
    report.build(proj["migration"])
    d = check(proj["migration"] / "report" / "index.html")
    assert d["external"] == [] and d["errors"] == []
    views = {v["id"]: v for v in d["views"]}
    assert set(views) == {"views-ds.a_mask", "views-ds.a_outline", "views-ds.b_exposure"}
    m = views["views-ds.a_mask"]
    assert m["w"] > 0 and m["h"] > 0 and m["tableW"] <= m["w"] + 1
    assert m["rows"] == 7 and m["changed"] == 0                   # 4 views + 3 cell projects
    assert m["receipts"] == ["view:counts"] and m["shaTitles"] >= 7
    assert "views.py\" url --run migration/runs/" in m["text"] and "127.0.0.1" not in m["text"]
    assert views["views-ds.a_outline"]["rows"] == 2


def test_absent_views_fallback_renders(sealed):
    report.build(sealed["migration"])
    d = check(sealed["migration"] / "report" / "index.html")
    assert d["external"] == [] and d["errors"] == []
    assert d["views"] and all(v["rows"] == 0 and "No map views for this run" in v["text"] and v["h"] > 0
                              for v in d["views"])


def test_index_self_contained_desktop_and_phone(proj, tmp_path):
    """index.html: one file, no request beyond itself, images decode, hero == the ladder line, the decisions export
    works and says it is not a signature, nothing overflows at phone width."""
    import ladder
    checks = synth.default_checks({"kind": "raster"})
    checks[-1].update(state="NEEDS_DECISION", reason_codes=["TOLERANCE_UNSIGNED"])
    synth.make_run(proj, 1, compares={"ds.b_exposure": ("NEEDS_DECISION", checks, ["TOLERANCE_UNSIGNED"])},
                   skip_compare=("ds.b_by_zone",))
    report.build(proj["migration"])
    html = proj["migration"] / "report" / "index.html"
    line = ladder.compute(proj["migration"])["status_line"]
    for extra in ((), ("--phone",)):
        d = check(html, str(tmp_path / ("phone.png" if extra else "desk.png")), "--copy", *extra)
        assert d["requests"] == ["file://" + str(html.resolve())], d["requests"]
        assert d["errors"] == []
        assert d["statusLine"] == line
        assert d["images"] and all(i["ok"] and i["src"].startswith("data:image/png") for i in d["images"])
        assert d["scrollWidth"] <= d["clientWidth"] and d["scrollWidthOpen"] <= d["clientWidth"], (extra, d)
        assert "ds.b_exposure" in d["exportText"] and "This is not a signature" in d["exportText"]
        assert "undecided" not in d["exportText"].splitlines()[1]
        if not extra:
            assert d["firstItemBottom"] is not None and d["firstItemBottom"] <= 720
