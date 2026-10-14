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


def test_offline_swipe(sealed):  # 07 test 14
    report.build(sealed["migration"])
    d = check(sealed["migration"] / "report" / "index.html", "--swipe")
    assert d["external"] == []
    assert d["errors"] == []
    assert d["mapReady"] and d["compare"] and d["fallbackShown"] is False
    assert d["swipe"]["moved"]
    assert d["caption"].startswith("Display only: warped to Web Mercator, nearest neighbour.")
    assert "(EPSG:4326)" in d["caption"]


def test_map_views_block_renders(proj):  # GeoLibre step 5: the views block renders offline, nothing is fetched
    import test_view_receipts as tvr
    tvr.view_run(proj, flag="ds.a_mask")
    report.build(proj["migration"])
    d = check(proj["migration"] / "report" / "index.html")
    assert d["external"] == [] and d["errors"] == []
    assert d["mapReady"]
    views = {v["id"]: v for v in d["views"]}
    assert set(views) == {"views-ds.a_mask", "views-ds.a_outline", "views-ds.b_exposure"}
    m = views["views-ds.a_mask"]
    assert m["w"] > 0 and m["h"] > 0 and m["tableW"] <= m["w"] + 1
    assert m["rows"] == 7 and m["changed"] == 0                   # 4 views + 3 cell projects
    assert m["receipts"] == ["view:counts"] and m["shaTitles"] >= 7
    assert "views.py\" url --run migration/runs/" in m["text"] and "127.0.0.1" not in m["text"]
    assert views["views-ds.a_outline"]["rows"] == 2
    assert sorted(d["mapViewLinks"]) == sorted("#" + k for k in views)


def test_absent_views_fallback_renders(sealed):
    report.build(sealed["migration"])
    d = check(sealed["migration"] / "report" / "index.html")
    assert d["external"] == [] and d["errors"] == []
    assert d["views"] and all(v["rows"] == 0 and "No map views for this run" in v["text"] and v["h"] > 0
                              for v in d["views"])
