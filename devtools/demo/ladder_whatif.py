#!/usr/bin/env python3
"""Dev-only WHAT-IF harness (demo rehearsal, defect D1): render ladder + report as they WOULD look if
ladder.ds_has_reference() honoured a user-confirmed equivalent reference. The plugin is NOT changed: the function is
replaced in this process only. Run it ONLY on a throwaway scratch copy (it overwrites that copy's migration/report/).
Every page it renders gets a red banner saying it is a what-if.

Rehearsal result (14 Oct): patching ds_has_reference + ds_legacy_path is NOT enough. seal.py then adds the
reference object for the via file under the id ref:<sha>, which the via dataset (e.g. ds.03_pop_res) already owns;
the duplicate id is dropped, ladder's ref_by_ds (keyed by object.dataset) finds no ref for ds.work__pop_res and the
run becomes EVIDENCE INCOMPLETE / UNBOUND_COMPARE. A real fix also has to let one ref object serve several datasets.

    axpy devtools/demo/ladder_whatif.py <scratch project>/migration [--seal RUN_ID] [--report]

--seal RUN_ID seals that (compared, plotted, reviewed, aggregated) run with the same in-process patch, so the
reference object is bound to the equivalent dataset's legacy file (seal.py uses the same two ladder functions).
"""
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "axis-migrate"
mig = Path(sys.argv[1]).resolve()
dev = Path(__file__).resolve().parents[2]
if dev / "demo" in mig.parents or dev / "fixtures" in mig.parents:
    sys.exit("refusing: run the what-if only on a scratch copy")
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(PLUGIN / "skills/report"))
import ladder  # noqa: E402

_orig = ladder.ds_has_reference


def ds_has_reference(ds: dict) -> bool:          # the proposed fix, for the what-if only
    ref = ds.get("reference") or {}
    if ds.get("role") != "input" and ref.get("source") == "equivalent" and ref.get("via_dataset") \
            and ref.get("confirmed_by"):
        return True
    return _orig(ds)


import json  # noqa: E402
_graph = json.loads((mig / "graph.json").read_text())
_by_id = {d["id"]: d for d in _graph.get("datasets") or []}
_orig_path = ladder.ds_legacy_path


def ds_legacy_path(ds: dict):                    # equivalent reference -> the via dataset's legacy file
    ref = ds.get("reference") or {}
    if ref.get("source") == "equivalent" and ref.get("confirmed_by") and ref.get("via_dataset") in _by_id:
        return _orig_path(_by_id[ref["via_dataset"]])
    return _orig_path(ds)


ladder.ds_has_reference = ds_has_reference
ladder.ds_legacy_path = ds_legacy_path
if "--seal" in sys.argv:
    import seal  # noqa: E402
    print("seal exit", seal.main([str(mig), "--run", sys.argv[sys.argv.index("--seal") + 1]]))
res = ladder.compute(mig)
print("WHAT-IF (patched in this process only):", res["status_line"])
for f in res.get("integrity") or []:
    print("  integrity:", f.get("code"), f.get("dataset"), f.get("detail"))
for sid, s in sorted(res["steps"].items()):
    print(f"  {sid}: {s['rung']} · {s['disposition']}")
if "--report" in sys.argv:
    import report  # noqa: E402
    rc = report.main([str(mig)])
    idx = mig / "report" / "index.html"
    html = idx.read_text()
    banner = ('<div style="background:#b00020;color:#fff;padding:8px 12px;font:600 15px sans-serif">WHAT-IF render '
              '(devtools/demo/ladder_whatif.py): ladder.ds_has_reference patched in-process to honour a confirmed '
              'equivalent reference (defect D1). Not the plugin\'s output. KD-003 carries a TEST signature '
              '(human:rehearsal-test).</div>')
    idx.write_text(html.replace("<body>", "<body>" + banner, 1))
    print("report exit", rc)
