#!/usr/bin/env python3
"""Dev-only (demo rehearsal): turn a reviewer's answers into plot.py review-add findings.

    axpy devtools/demo/receipts.py <project dir> <run_id> <answers.json> [--model ID] [--dry-run]

answers.json (written by the person or model who READ the PNGs; this script never decides an answer):
  {"ds.x": "all_no",                                    # every applicable question no -> one Q-all receipt
   "ds.y": [{"q": "Q1", "a": "yes", "category": "whole_offset", "plot": "worst_tiles.png",
             "panel": "edge overlay west", "bbox": [x0, y0, x1, y1], "note": "..."},
            {"q": "Q2", "a": "no", "plot": "hist.png"}, ...]}
The script only fills in what is mechanical: plot_sha256, panel names and crs from this run's plots.json, and the
plots_seen list for Q-all. It refuses an answer for a plot that is not in plots.json.
"""
import json, subprocess, sys, os
from pathlib import Path

proj, rid, answers = Path(sys.argv[1]).resolve(), sys.argv[2], json.loads(Path(sys.argv[3]).read_text())
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "claude-opus-5-5 (rehearsal)"
dry = "--dry-run" in sys.argv
plugin = Path(os.environ.get("PLUGIN", Path.home() / "axis-migrate-dev/axis-migrate"))
run = proj / "migration/runs" / rid
findings = []
for ds, spec in answers.items():
    pj = json.loads((run / "verify/plots" / ds / "plots.json").read_text())
    pngs = {p["name"]: p for p in pj["png"]}
    rel = lambda n: f"plots/{ds}/{n}"
    if spec == "all_no":
        first = sorted(pngs)[0]
        findings.append({"dataset_id": ds, "question_id": "Q-all", "answer": "no", "effect": "none", "category": "none",
                         "panel": "all", "plot": rel(first), "plot_sha256": pngs[first]["sha256"],
                         "plots_seen": [{"plot": rel(n), "plot_sha256": pngs[n]["sha256"]} for n in sorted(pngs)],
                         "note": "", "model": model})
        continue
    for a in spec:
        p = pngs[a["plot"]]
        yes = a["a"] == "yes"
        f = {"dataset_id": ds, "question_id": a["q"], "answer": a["a"], "effect": "flag" if yes else "none",
             "category": a.get("category", "none") if yes else "none", "plot": rel(a["plot"]),
             "plot_sha256": p["sha256"], "panel": a.get("panel") or p["panel_names"][0],
             "note": a.get("note", ""), "model": model}
        if yes:
            f["location"] = {"crs": p["crs"], "bbox": a["bbox"]}
        findings.append(f)
out = run.parent.parent / "analysis" / f"receipts-{rid}.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(findings, indent=1))
print(f"{len(findings)} findings -> {out}")
if not dry:
    r = subprocess.run([str(plugin / "scripts/axpy"), str(plugin / "skills/verify/plot.py"), "review-add", "--run", str(run),
                        "--json", str(out)], cwd=proj, capture_output=True, text=True)
    print(r.stdout.strip()[-3000:], r.stderr.strip()[-2000:], f"exit {r.returncode}", sep="\n")
