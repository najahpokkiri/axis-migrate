"""Opaque evidence ids for shipped skill files: ev:<first 10 hex of sha256("axis-ev:" + candidate id)>.

The map (candidate id -> ev id) stays in this dev repo (devtools/evidence-map.json), never in the plugin.
Usage: python3 devtools/evidence_ids.py EP-05 IN-02 ...   (prints ev ids and updates the map)
"""
import hashlib, json, sys
from pathlib import Path
MAP = Path(__file__).with_name("evidence-map.json")
def ev(cid): return "ev:" + hashlib.sha256(("axis-ev:" + cid).encode()).hexdigest()[:10]
if __name__ == "__main__":
    m = json.loads(MAP.read_text()) if MAP.exists() else {}
    for c in sys.argv[1:]:
        m[c] = ev(c); print(c, m[c])
    MAP.write_text(json.dumps(dict(sorted(m.items())), indent=1) + "\n")
