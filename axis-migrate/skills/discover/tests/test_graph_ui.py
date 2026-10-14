"""The vendored editor: offline page + bundle. A browser smoke runs only when the dev tooling is present."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import graph_synth as gs

HTML = gs.DISCOVER / "graph.html"
BUNDLE = gs.DISCOVER / "graph.bundle.js"
DEVUI = gs.PLUGIN.parent / "devtools" / "graph-ui"


def test_page_is_offline():
    html = HTML.read_text(encoding="utf-8")
    srcs = re.findall(r'(?:src|href)="([^"]+)"', html)
    assert srcs == ["graph.bundle.js"]  # one local script, no CDN, no external stylesheet
    assert "http://" not in html and "https://" not in html


def test_bundle_is_self_contained():
    js = BUNDLE.read_text(encoding="utf-8")
    assert js.startswith("/* axis-migrate workflow graph editor.")
    assert "react@18.3.1" in js and "@xyflow/react@12" in js and "@dagrejs/dagre@" in js
    assert not re.search(r"\bimport\s*\(\s*['\"]https?:", js) and "require(\"http" not in js
    assert "Send to agent" in js and "Automate this manual step" in js
    assert 200_000 < len(js) < 1_000_000


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_bundle_parses_in_node():
    r = subprocess.run(["node", "--check", str(BUNDLE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def _browser_ready() -> bool:
    return (DEVUI / "node_modules" / "playwright-core").is_dir() and Path("/usr/bin/google-chrome").exists() \
        and shutil.which("node") is not None


@pytest.mark.skipif(not _browser_ready(), reason="dev browser tooling (playwright-core + Chrome) not present")
def test_browser_smoke(tmp_path: Path):
    from test_graph_server import Server
    mdir = gs.make_project(tmp_path)
    srv = Server(mdir)
    try:
        r = subprocess.run(["node", str(DEVUI / "shot.mjs"), srv.url, str(tmp_path / "shot"), "m01_zones_edit",
                            "btn:Automate this manual step"], capture_output=True, text=True, timeout=90,
                           cwd=str(DEVUI))
        assert r.returncode == 0, r.stderr
        res = json.loads(r.stdout.strip().splitlines()[-1])
        assert res["errors"] == [] and res["nodes"] == 14 and res["edges"] == 16
        import graph as G
        g = G.load_graph(mdir)
        m01 = next(s for s in g["steps"] if s["id"] == "m01_zones_edit")
        assert m01["scope"] == "migrate" and G.read_edits(mdir)[-1]["actor"] == "user"
        q01 = next(q for q in g["questions"] if q["id"] == "q01")  # D12: automating answers the policy question
        assert q01["status"] == "answered" and q01["answer"]["value"] == "derive" and q01["answer"]["channel"] == "graph"
        assert (tmp_path / "shot-0.png").stat().st_size > 10_000
    finally:
        srv.stop()


@pytest.mark.skipif(not _browser_ready(), reason="dev browser tooling (playwright-core + Chrome) not present")
def test_reference_form_resets_per_dataset(tmp_path: Path):
    """D5: pairing a second dataset must not post the first dataset's equivalent."""
    import graph as G
    from test_graph_server import Server
    mdir = gs.make_project(tmp_path)
    G.apply(mdir, [{"type": "add_dataset", "id": "ds.alt_two", "kind": "raster", "role": "intermediate",
                    "legacy": {"path": "work.gdb/alt_two", "exists": False}},
                   {"type": "add_edge", "from": "s03b_alt", "to": "ds.alt_two", "kind": "writes",
                    "evidence": [{"ref": "scripts/03b_alt.py:16"}]},
                   {"type": "add_edge", "from": "ds.alt_two", "to": "ds.final", "kind": "equivalent_to",
                    "evidence": [{"state": "documented", "ref": "scripts/03b_alt.py:2"}]}], "agent", 1)
    srv = Server(mdir)
    try:
        pairing = 'css:summary:has-text("Reference pairing")'
        r = subprocess.run(["node", str(DEVUI / "shot.mjs"), srv.url, str(tmp_path / "ref"), "ds.c_stats", pairing,
                            "btn:Set reference", "dispatch:ds.final", pairing, "btn:Set reference"], capture_output=True, text=True, timeout=90, cwd=str(DEVUI))
        assert r.returncode == 0, r.stderr
        g = G.load_graph(mdir)
        ref = {d["id"]: d["reference"] for d in g["datasets"]}
        assert ref["ds.c_stats"]["via_dataset"] == "ds.alt_out" and ref["ds.final"]["via_dataset"] == "ds.alt_two"
        assert not [x for x in G.read_edits(mdir) if x["actor"] == "user" and x["op"]["type"] != "set_reference"]
    finally:
        srv.stop()
