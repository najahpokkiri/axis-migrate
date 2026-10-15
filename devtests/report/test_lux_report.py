"""The redesigned report on a real lux run (dev-only; encodes lux facts).

    ~/axis-migrate-dev/axis-migrate/scripts/axpy -m pytest ~/axis-migrate-dev/devtests/report -q

One chained run of the golden lux pipeline on a fresh fixture copy, with the measured (unsigned) tolerance proposals
next to it, so the run has NEEDS_DECISION (TOLERANCE_UNSIGNED on area_m2), NO_REFERENCE (the hand-edited cantons)
and NEEDS_REVIEW (no visual receipt) items: compare.py → plot.py → map.py display → aggregate → seal.py → report.py.
Asserts the brief: hero text == the ladder status line, a card per compared dataset, NOT_COMPARED / NEEDS_DECISION
listed before the cards, no network load, the HTML parses, REPORT.md starts with the status line, the deliverable
check passes and catches an edit; graph.md renders before and after the run. With node + playwright-core + Chrome,
the page also loads offline at desktop and phone width with zero requests beyond itself.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

os.environ["GDAL_PAM_ENABLED"] = "NO"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "axis-migrate"
REPORT = PLUGIN / "skills" / "report"
sys.path.insert(0, str(REPO / "devtests" / "run" / "lux_project"))
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(REPORT))
sys.path.insert(0, str(REPORT / "tests"))
import axis_common as ac  # noqa: E402
import ladder  # noqa: E402
import make_lux_project as mlp  # noqa: E402
from report_asserts import check_report  # noqa: E402

AXPY = str(PLUGIN / "scripts" / "axpy")
PROPOSALS = REPO / "devtests" / "verify" / "lux_tolerance_proposals.yaml"
SHOTS = Path(os.environ.get("AXIS_REPORT_SHOTS", "")) if os.environ.get("AXIS_REPORT_SHOTS") else None
PW = Path(os.environ.get("AXIS_PLAYWRIGHT_DIR", REPO / "devtools/report-vendor/node_modules"))
CHROME = os.environ.get("AXIS_CHROME", "/usr/bin/google-chrome")


def sh(args, ok=(0,), timeout=1200):
    cp = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    assert cp.returncode in ok, (args, cp.stdout[-3000:], cp.stderr[-3000:])
    return cp


def _unlock(root: Path):
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            if not p.is_symlink():
                os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


@pytest.fixture(scope="module")
def lux(tmp_path_factory):
    before = ac.tree_hash(mlp.FIXTURE)
    root = tmp_path_factory.mktemp("luxreport") / "proj"
    mig = mlp.assemble(root)
    shutil.copyfile(PROPOSALS, mig / "tolerances.proposed.yaml")
    # graph.md before any run: lineage only, nothing coloured by a verdict
    sh([AXPY, REPORT / "graphview.py", "md", mig])
    graph_md_before = (mig / "graph.md").read_text(encoding="utf-8")
    # report before any run: exit 3, a page and a summary that say so
    norun = sh([AXPY, REPORT / "report.py", mig], ok=(3,))
    norun_md = (mig / "report" / "REPORT.md").read_text(encoding="utf-8")
    cp = sh([AXPY, PLUGIN / "skills" / "run" / "run_local.py", mig, "--json"], ok=(0, 10))
    run = Path(json.loads(cp.stdout.strip().splitlines()[-1])["run_dir"])
    sh([AXPY, PLUGIN / "skills" / "verify" / "compare.py", "run", "--run", run], ok=(0, 1, 2))
    sh([AXPY, PLUGIN / "skills" / "verify" / "plot.py", "run", "--run", run], ok=(0, 1))
    sh([AXPY, REPORT / "map.py", "display", "--run", run])
    sh([AXPY, PLUGIN / "skills" / "verify" / "compare.py", "aggregate", "--run", run], ok=(0, 1, 2))
    sh([AXPY, REPORT / "seal.py", mig, "--run", run.name])
    rep = sh([AXPY, REPORT / "report.py", mig, "--json"], ok=(0, 2))
    sh([AXPY, REPORT / "graphview.py", "md", mig])
    yield {"mig": mig, "run": run, "rep": json.loads(rep.stdout), "graph_md_before": graph_md_before,
           "norun": norun.stdout, "norun_md": norun_md}
    _unlock(root)
    assert ac.tree_hash(mlp.FIXTURE) == before


def test_no_run_report_first(lux):
    assert lux["norun_md"].splitlines()[0].startswith("axis-migrate status: none | run none |")
    assert "no sealed run yet" in lux["graph_md_before"] and "```mermaid" in lux["graph_md_before"]


def test_brief_on_lux(lux):
    mig = lux["mig"]
    lad = ladder.compute(mig)
    # the line ladder.py prints is the page's hero, verbatim
    cli = sh([sys.executable, "-I", REPORT / "ladder.py", mig]).stdout.splitlines()[0]
    assert cli == lad["status_line"]
    facts = check_report(mig, lad)
    compared = {d for d, e in lad["datasets"].items() if e.get("state")}
    assert compared and set(facts["cards"]) == compared
    # the two area_m2 layers wait for a person; the decisions are cards with the measured proposal
    assert {"ds.02_res_2169", "ds.02_res_4326"} <= {d for d, s in facts["attention"].items() if s == "NEEDS_DECISION"}
    page = facts["page"]
    dec = page[page.index('id="decisions"'):page.index('id="datasets"')]
    assert "tol.02_res_2169.vector.attr.area_m2.v1" in dec and "never signs anything" in dec
    # rasters show legacy / new / difference panels cut from the sealed overview plot
    card = page[page.index('id="ds-ds.03_pop_res"'):]
    card = card[:card.index("</article>")]
    assert card.count('src="data:image/png;base64,') >= 3 and "difference (new − legacy)" in card
    rep = json.loads((mig / "report" / "REPORT.json").read_text())
    assert {"index.html", "REPORT.md", "parity.xlsx"} <= set(rep["files"]) and rep["thumbnails"]
    # every compared raster/vector has at least one thumbnail hashed against its sealed plot
    for ds in compared:
        if lad["datasets"][ds]["kind"] in ("raster", "vector"):
            assert any(k.startswith(ds + "/") for k in rep["thumbnails"]), ds
    md = facts["md"]
    assert "## Decisions for you" in md and "```mermaid" in md and "| dataset | step |" in md


def test_deliverable_check_on_lux(lux):
    mig = lux["mig"]
    sh([AXPY, REPORT / "report.py", mig, "--check"])
    sh([sys.executable, "-I", REPORT / "report_check.py", mig])
    p = mig / "report" / "REPORT.md"
    keep = p.read_bytes()
    p.write_bytes(keep.replace(b"sandbox-validated", b"reference-verified"))
    cp = sh([sys.executable, "-I", REPORT / "report_check.py", mig], ok=(2,))
    assert "REPORT_FILE_CHANGED: REPORT.md" in cp.stdout
    p.write_bytes(keep)
    sh([sys.executable, "-I", REPORT / "report_check.py", mig])


def test_graph_md_after_run(lux):
    md = (lux["mig"] / "graph.md").read_text(encoding="utf-8")
    lad = ladder.compute(lux["mig"])
    assert lad["status_line"] in md and ":::purple" in md and "s02_landuse_mask" in md
    assert (lux["mig"] / "graph.png").stat().st_size > 5000


@pytest.mark.skipif(not (shutil.which("node") and (PW / "playwright-core").is_dir() and Path(CHROME).exists()),
                    reason="node + playwright-core + Chrome needed (devtools/report-vendor)")
def test_lux_page_offline_desktop_and_phone(lux, tmp_path):
    html = lux["mig"] / "report" / "index.html"
    shots = SHOTS or tmp_path
    shots.mkdir(parents=True, exist_ok=True)
    lad = ladder.compute(lux["mig"])
    for extra, name in (((), "lux-desktop.png"), (("--phone",), "lux-phone.png")):
        cp = subprocess.run(["node", str(REPORT / "tests" / "browser_check.cjs"), str(html), str(shots / name),
                             "--copy", *extra], capture_output=True, text=True, timeout=240)
        d = json.loads(cp.stdout or "{}")
        assert "fatal" not in d, d
        assert d["requests"] == ["file://" + str(html.resolve())]
        assert d["errors"] == [] and d["statusLine"] == lad["status_line"]
        assert all(i["ok"] for i in d["images"])
        assert d["scrollWidth"] <= d["clientWidth"] and d["scrollWidthOpen"] <= d["clientWidth"]
        assert "This is not a signature" in d["exportText"]
        if not extra:
            assert d["firstItemBottom"] <= 720
