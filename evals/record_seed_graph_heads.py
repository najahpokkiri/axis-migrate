"""Seeds are constructed by us (dev), not by the agent: record graph.py's write record (.graph/head.json) for every
seed/migration so graph.py's out-of-band-edit check accepts them. Run with the plugin interpreter:
    axis-migrate/bin/axpy evals/record_seed_graph_heads.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "axis-migrate" / "skills" / "discover"))
import graph  # noqa: E402

for g in sorted((ROOT / "evals" / "cases").glob("*/seed/migration/graph.json")):
    mig = g.parent
    rev = json.loads(g.read_text())["revision"]
    (mig / ".graph").mkdir(exist_ok=True)
    graph._record_head(mig, rev)
    ok = graph.check_integrity(mig) if hasattr(graph, "check_integrity") else None
    print(f"{mig.parent.parent.name}: head recorded at rev {rev}; integrity {ok}")
