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
        assert (tmp_path / "shot-0.png").stat().st_size > 10_000
    finally:
        srv.stop()
