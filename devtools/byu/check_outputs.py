"""Check the BYU reference GeoPackages by content (attributes in fid order; geometry and `path` not compared).

    python3 -I check_outputs.py <reference_outputs/flood_impact_qgisModel> <expected-outputs.json>
Stdlib only (sqlite3). Exit 0 equal, 1 different.
"""
import json
import sqlite3
import sys
from pathlib import Path

out, expected = Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text())
bad = []
for name, rows in expected.items():
    if name.startswith("_"):
        continue
    f = out / f"{name}.gpkg"
    if not f.is_file():
        bad.append(f"{name}: missing")
        continue
    con = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
    cols = [r[1] for r in con.execute(f'pragma table_info("{name}")')]
    got = [dict(zip(cols, r)) for r in con.execute(f'select * from "{name}" order by fid')]
    con.close()
    if len(got) != len(rows):
        bad.append(f"{name}: {len(got)} rows, expected {len(rows)}")
        continue
    for i, (g, e) in enumerate(zip(got, rows)):
        for k, v in e.items():
            gv = g.get(k)
            same = abs(gv - v) <= 1e-9 * max(1.0, abs(v)) if isinstance(v, float) and isinstance(gv, (int, float)) \
                else gv == v
            if not same:
                bad.append(f"{name} row {i + 1} {k}: {gv!r}, expected {v!r}")
for b in bad:
    print("check_outputs:", b, file=sys.stderr)
print("check_outputs:", "OK" if not bad else f"{len(bad)} difference(s)")
sys.exit(1 if bad else 0)
