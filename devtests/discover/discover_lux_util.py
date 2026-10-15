"""Helpers for the discover lux devtests (paths, runners, PROVENANCE oracle).

The fixture's PROVENANCE file may be read here as an oracle; plugin code never reads it."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "axis-migrate"
AXPY = PLUGIN / "scripts" / "axpy"
DISCOVER = PLUGIN / "skills" / "discover"
SCAN, MAP, LINEAGE, GRAPH = (DISCOVER / n for n in ("scan.py", "map.py", "lineage.py", "graph.py"))
FIXTURE = REPO / "fixtures" / "lux-exposure-legacy"
PROVENANCE = REPO / "fixtures" / "lux-exposure-legacy.PROVENANCE.md"
os.environ["GDAL_PAM_ENABLED"] = "NO"


def run(script, *args, check=True, env=None):
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([str(AXPY), str(script), *map(str, args)], capture_output=True, text=True, env=e)
    if check and r.returncode != 0:
        raise AssertionError(f"{Path(script).name} {args}: exit {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def copy_fixture(dst: Path) -> Path:
    leg = dst / "lux-exposure-legacy"
    shutil.copytree(FIXTURE, leg, symlinks=True)  # copy2 keeps mtimes (they are part of the fixture's story)
    return leg


def tree_state(root: Path) -> dict:
    import hashlib
    out = {}
    for dp, dn, fn in os.walk(root):
        for f in fn:
            p = Path(dp) / f
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def scan_with_audit(leg: Path, out: Path, log: Path) -> subprocess.CompletedProcess:
    wrapper = f"""
import sys, runpy
opened = []
def hook(ev, args):
    if ev == 'open' and isinstance(args[0], str):
        opened.append(args[0])
sys.addaudithook(hook)
sys.argv = ['scan.py', {str(leg)!r}, '--out', {str(out)!r}]
code = 0
try:
    runpy.run_path({str(SCAN)!r}, run_name='__main__')
except SystemExit as e:
    code = e.code or 0
open({str(log)!r}, 'w').write('\\n'.join([sys.base_prefix, sys.prefix] + opened))
sys.exit(code)
"""
    return subprocess.run([str(AXPY), "-c", wrapper], capture_output=True, text=True)


def provenance_inventory() -> dict:
    """PROVENANCE 'Inventory' table: path -> {sha256, crs, grid}."""
    rows = {}
    for line in PROVENANCE.read_text().splitlines():
        m = re.match(r"^\|\s*[^|]+\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{64})`\s*\|\s*([^|]+)\|\s*(.+)\|\s*$", line)
        if m:
            rows[m.group(1)] = {"sha256": m.group(2), "crs": m.group(3).strip(), "grid": m.group(4).strip()}
    return rows


def ds(inv, did):
    return next(d for d in inv["datasets"] if d["id"] == did)


def op(inv, oid):
    return next(o for o in inv["ops"] if o["id"] == oid)
