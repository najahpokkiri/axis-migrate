#!/usr/bin/env python3
"""Generate axis-migrate/skills/discover/qgis_algs.json from a real QGIS install.

Dev-only tool (not shipped). Runs `qgis_process --json list`, then `qgis_process --json help <alg>` for every
algorithm of the native:, gdal: and qgis: providers, and keeps only what discover/scan.py needs:
alg id -> {params: {NAME: {type, is_destination, optional[, parent][, options]}}, outputs: {NAME: type}}.
Stamped with the QGIS / GDAL / PROJ versions. Deterministic output (sorted keys).

Also records `expression_functions`: every built-in QGIS expression function name and alias (QgsExpression.Functions()
in the same env), so the scan can tell a custom (unshipped) expression function from a built-in one.

Usage: python3 devtools/make_qgis_algs.py [--out PATH] [--mamba ~/.local/bin/micromamba] [--env qgis] [--jobs 8]
       python3 devtools/make_qgis_algs.py --expr-only     (refresh expression_functions in the existing file only)
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import subprocess
import sys
from pathlib import Path

PROVIDERS = ("native", "gdal", "qgis")
REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "axis-migrate" / "skills" / "discover" / "qgis_algs.json"


def qp(args, mamba, env):
    e = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([mamba, "run", "-n", env, "qgis_process", "--json", *args], capture_output=True, text=True,
                       env=e, timeout=300)
    out = r.stdout
    i = out.find("{")
    if i < 0:
        raise RuntimeError(f"qgis_process {' '.join(args)}: no JSON (exit {r.returncode}): {r.stderr[-400:]}")
    return json.loads(out[i:])


def slim(help_doc):
    params = {}
    for name, p in sorted((help_doc.get("parameters") or {}).items()):
        t = p.get("type") or {}
        if not isinstance(t, dict):
            t = {"id": str(t)}
        rec = {"type": t.get("id"), "is_destination": bool(p.get("is_destination")), "optional": bool(p.get("optional"))}
        parent = (p.get("raw_definition") or {}).get("parent_layer")
        if parent:  # field/band/expression parameters name the layer parameter they belong to
            rec["parent"] = parent
        if t.get("id") == "enum" and p.get("available_options"):
            rec["options"] = {str(k): v for k, v in sorted(p["available_options"].items(), key=lambda kv: int(kv[0]))}
        params[name] = rec
    outputs = {n: (o.get("type") if isinstance(o, dict) else o) for n, o in sorted((help_doc.get("outputs") or {}).items())}
    return {"params": params, "outputs": outputs}


EXPR_PY = """
from qgis.core import Qgis, QgsApplication, QgsExpression
app = QgsApplication([], False)
app.initQgis()
names = sorted({f.name() for f in QgsExpression.Functions()} | {a for f in QgsExpression.Functions() for a in f.aliases()})
import json
print("AXIS_EXPR_JSON" + json.dumps({"qgis_version": Qgis.version(), "names": names}))
"""


def expression_functions(mamba, env):
    e = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    r = subprocess.run([mamba, "run", "-n", env, "python", "-c", EXPR_PY], capture_output=True, text=True, env=e,
                       timeout=600)
    line = next((x for x in r.stdout.splitlines() if x.startswith("AXIS_EXPR_JSON")), None)
    if line is None:
        raise RuntimeError(f"expression functions: no output (exit {r.returncode}): {r.stderr[-400:]}")
    return json.loads(line[len("AXIS_EXPR_JSON"):])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--mamba", default=str(Path.home() / ".local/bin/micromamba"))
    ap.add_argument("--env", default="qgis")
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--expr-only", action="store_true", help="refresh only expression_functions in --out")
    a = ap.parse_args(argv)
    if a.expr_only:
        doc = json.loads(Path(a.out).read_text(encoding="utf-8"))
        doc["expression_functions"] = expression_functions(a.mamba, a.env)
        Path(a.out).write_text(json.dumps(doc, sort_keys=True, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {a.out}: {len(doc['expression_functions']['names'])} expression functions", file=sys.stderr)
        return 0

    lst = qp(["list"], a.mamba, a.env)
    ids = []
    for prov, pd in sorted(lst.get("providers", {}).items()):
        if prov in PROVIDERS:
            ids.extend(sorted(pd.get("algorithms", {})))
    print(f"{len(ids)} algorithms in {PROVIDERS}", file=sys.stderr)

    algs, failed = {}, []

    def one(alg):
        return alg, qp(["help", alg], a.mamba, a.env)

    stamp = None
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for fut in cf.as_completed([ex.submit(one, i) for i in ids]):
            try:
                alg, doc = fut.result()
            except Exception as exc:  # noqa: BLE001 - record and continue; listed in the output
                failed.append(str(exc)[:200])
                continue
            try:
                algs[alg] = slim(doc)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{alg}: {exc}"[:200])
                continue
            if stamp is None:
                stamp = {k: doc.get(k) for k in ("qgis_version", "gdal_version", "proj_version", "geos_version")}
    if failed:
        print(f"{len(failed)} help calls failed", file=sys.stderr)
    doc = {"schema": "axis-migrate.qgis-algs.v1",
           "generated_by": "devtools/make_qgis_algs.py (qgis_process --json help)",
           "source": stamp or {}, "providers": list(PROVIDERS), "count": len(algs),
           "failed": sorted(failed), "algorithms": dict(sorted(algs.items())),
           "expression_functions": expression_functions(a.mamba, a.env)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, sort_keys=True, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {len(algs)} algorithms", file=sys.stderr)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
