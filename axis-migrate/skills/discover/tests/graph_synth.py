"""Synthetic project for the graph tests (no fixture facts: generic names, dummy bytes).

Layout under <tmp>/:
  legacy/in/{raw.tif, zones_raw.geojson}    inputs
  legacy/in/zones.gpkg                      output of the manual step m01 (role input while kept legacy)
  legacy/tmp/{a_clip.tif, b_mask.tif, c_stats.gpkg}   intermediates
  legacy/out/{final.tif, table.csv}         finals
  migration/                                graph.json etc.
Steps: m01_zones_edit (manual, keep_legacy), s01_clip, s02_mask, s03_stats, s03b_alt (drop, alternative_impl).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DISCOVER = HERE.parent
PLUGIN = DISCOVER.parents[1]
GRAPH_PY = DISCOVER / "graph.py"
SERVER_PY = DISCOVER / "graph_server.py"
HOOK_PY = PLUGIN / "hooks" / "bin" / "graph_changed.py"
AXPY = PLUGIN / "bin" / "axpy"

if str(DISCOVER) not in sys.path:
    sys.path.insert(0, str(DISCOVER))

LEGACY_FILES = {
    "in/raw.tif": b"RAW", "in/zones_raw.geojson": b"{}", "in/zones.gpkg": b"GPKG-Z",
    "tmp/a_clip.tif": b"A", "tmp/b_mask.tif": b"B", "tmp/c_stats.gpkg": b"C",
    "out/final.tif": b"F", "out/table.csv": b"k,v\n",
}


def _ev(ref: str, summary: str = "", state: str = "structurally_inferred") -> list[dict]:
    return [{"state": state, "ref": ref, "summary": summary, "by": "scan", "rev": 1}]


def _ds(id_, kind, role, path, layer=None, exists=True):
    return {"id": id_, "kind": kind, "role": role,
            "legacy": {"path": path, "layer": layer, "exists": exists, "sha256": None, "sidecars": []}}


def draft() -> dict:
    steps = [
        {"id": "m01_zones_edit", "title": "Zones edited by hand", "kind": "manual", "tool": "gis_gui",
         "scope": "keep_legacy", "scope_reason": "manual_pending_answer",
         "legacy": {"source": "NOTES.txt", "lines": [[3, 5]], "executed": "yes"}},
        {"id": "s01_clip", "title": "Clip raster", "kind": "script", "tool": "python",
         "legacy": {"source": "scripts/01_clip.py", "lines": [[10, 30]], "executed": "yes"},
         "params": ["p.clip_nodata"]},
        {"id": "s02_mask", "title": "Mask", "kind": "script", "tool": "python",
         "legacy": {"source": "scripts/02_mask.py", "lines": [[5, 40]], "executed": "yes"}, "params": ["p.min_area"]},
        {"id": "s03_stats", "title": "Zonal statistics", "kind": "script", "tool": "python",
         "legacy": {"source": "scripts/03_stats.py", "lines": [[1, 60]], "executed": "yes"}},
        {"id": "s03b_alt", "title": "Alternative zonal statistics", "kind": "script", "tool": "arcpy",
         "scope": "drop", "scope_reason": "alternative_impl",
         "legacy": {"source": "scripts/03b_alt.py", "lines": [[1, 20]], "executed": "no"}},
    ]
    datasets = [
        _ds("ds.raw", "raster", "input", "in/raw.tif"),
        _ds("ds.zones_raw", "vector", "input", "in/zones_raw.geojson"),
        _ds("ds.zones", "vector", "input", "in/zones.gpkg", layer="zones"),
        _ds("ds.a_clip", "raster", "intermediate", "tmp/a_clip.tif"),
        _ds("ds.b_mask", "raster", "intermediate", "tmp/b_mask.tif"),
        _ds("ds.c_stats", "vector", "intermediate", "tmp/c_stats.gpkg", layer="c_stats"),
        _ds("ds.final", "raster", "final", "out/final.tif"),
        _ds("ds.table", "table", "final", "out/table.csv"),
        _ds("ds.alt_out", "vector", "intermediate", "work.gdb/alt_out", exists=False),
    ]
    datasets[-1]["persistence"] = "gdb"
    E = []

    def e(frm, to, kind, ref):
        E.append({"from": frm, "to": to, "kind": kind, "evidence": _ev(ref)})

    e("ds.zones_raw", "m01_zones_edit", "reads", "NOTES.txt:3")
    e("m01_zones_edit", "ds.zones", "writes", "NOTES.txt:4")
    e("ds.raw", "s01_clip", "reads", "scripts/01_clip.py:12")
    e("ds.zones", "s01_clip", "reads", "scripts/01_clip.py:13")
    e("s01_clip", "ds.a_clip", "writes", "scripts/01_clip.py:28")
    e("ds.a_clip", "s02_mask", "reads", "scripts/02_mask.py:8")
    e("ds.a_clip", "s02_mask", "reads_grid", "scripts/02_mask.py:30")
    e("s02_mask", "ds.b_mask", "writes", "scripts/02_mask.py:39")
    e("ds.b_mask", "s03_stats", "reads", "scripts/03_stats.py:5")
    e("ds.zones", "s03_stats", "reads", "scripts/03_stats.py:6")
    e("s03_stats", "ds.c_stats", "writes", "scripts/03_stats.py:20")
    e("s03_stats", "ds.final", "writes", "scripts/03_stats.py:40")
    e("s03_stats", "ds.table", "writes", "scripts/03_stats.py:55")
    e("ds.b_mask", "s03b_alt", "reads", "scripts/03b_alt.py:4")
    e("s03b_alt", "ds.alt_out", "writes", "scripts/03b_alt.py:15")
    E.append({"from": "ds.alt_out", "to": "ds.c_stats", "kind": "equivalent_to",
              "evidence": _ev("scripts/03b_alt.py:2", "header says same output", "documented")})
    questions = [{
        "id": "q01", "node": "m01_zones_edit", "nodes": ["ds.zones"], "kind": "manual_step",
        "header": "Zone names", "text": "The zone names were edited by hand. Should the pipeline do it?",
        "trigger": {"ref": "NOTES.txt:3-5", "quote": "edited by hand"},
        "options": [{"value": "derive", "label": "Derive in code (Recommended)", "recommended": True},
                    {"value": "read_edited", "label": "Read the edited file"},
                    {"value": "dont_know", "label": "Don't know"}],
        "impact": {"datasets": ["ds.zones", "ds.c_stats"], "level": "high"},
        "priority": {"impact": 3, "uncertainty": 3, "cost": 3, "score": 27}}]
    return {"project": {"name": "synthetic", "legacy_root": "../legacy", "created_at": "2026-10-13T00:00:00Z"},
            "steps": steps, "datasets": datasets, "edges": E, "questions": questions,
            "legacy_output_dirs": ["out", "tmp"], "invariants": []}


def make_project(root: Path) -> Path:
    """Create legacy files and an initialised graph; returns the migration dir."""
    for rel, data in LEGACY_FILES.items():
        p = root / "legacy" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    mdir = root / "migration"
    mdir.mkdir(parents=True, exist_ok=True)
    import graph  # noqa: E402
    graph.init(mdir, draft())
    return mdir


def run_cli(*args: str, stdin: str | None = None, env: dict | None = None, timeout: float = 60):
    """Run graph.py under the plugin interpreter (axpy)."""
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(AXPY), str(GRAPH_PY), *args], input=stdin, capture_output=True, text=True,
                          timeout=timeout, env=e)


def write_ops(path: Path, ops: list[dict]) -> Path:
    path.write_text(json.dumps(ops), encoding="utf-8")
    return path
