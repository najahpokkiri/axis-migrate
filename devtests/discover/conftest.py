"""Lux acceptance fixtures for discover (01 AT-1..AT-17, 02 T-lin-1..4). Runs on a tmp COPY of the fixture.

The fixture's PROVENANCE file may be read here as an oracle; plugin code never reads it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discover_lux_util import LINEAGE, MAP, copy_fixture, run, scan_with_audit, tree_state  # noqa: E402


@pytest.fixture(scope="session")
def lux(tmp_path_factory):
    root = tmp_path_factory.mktemp("luxproj")
    leg = copy_fixture(root)
    before = tree_state(leg)
    mig = root / "migration"
    log = root / "opens.txt"
    r = scan_with_audit(leg, mig, log)
    assert r.returncode == 0, r.stderr[-3000:]
    after_scan = tree_state(leg)
    run(MAP, "inputs", mig)
    lin = run(LINEAGE, mig, "--json")
    inv = json.loads((mig / "inventory.json").read_text())
    draft = json.loads((mig / ".graph" / "draft.json").read_text())
    graph = json.loads((mig / "graph.json").read_text()) if (mig / "graph.json").exists() else None
    base, prefix, *opened = log.read_text().splitlines()
    return {"root": root, "legacy": leg, "mig": mig, "inv": inv, "draft": draft, "graph": graph,
            "lineage_summary": json.loads(lin.stdout), "before": before, "after_scan": after_scan,
            "after_all": tree_state(leg), "opened": opened, "base_prefix": base, "prefix": prefix}


