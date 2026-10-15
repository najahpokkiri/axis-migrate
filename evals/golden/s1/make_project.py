"""Assemble an S1 project with the REAL discover tools plus scripted graph ops (dev/eval only; encodes S1 facts).

    axpy evals/golden/s1/make_project.py DEST [--base s1|s1L] [--legacy-name NAME] [--patch evals/mutants/T5.patch]
                                              [--no-legacy] [--no-pipeline] [--no-approve]

DEST/<legacy name> = copy of evals/cases/_bases/<base>-buffers-legacy (copy2, mtimes kept);
DEST/migration     = scan.py + lineage.py (graph rev 1) on that copy, then graph.py apply (actor agent):
  rev 2  dismiss lineage's two false "field not found" questions (OVERLAY_FIELDS belong to the overlay layer);
         compare blocks (keys, match, header row, measure_crs_param p.measure_crs); step params; set_new modules
  rev 3  q03 (kind branch: per-class buffer distance) added
  rev 4  q03 answered (--channel interview --verbatim, the scripted oracle's words)
  rev 5  set_plan for the four steps
  rev 6  approve_plan "*" (--channel interview --verbatim)
plus params.yaml (golden), tolerances.yaml (empty: every check exact), decisions.md (D-001 for q03),
known-differences.md, pipeline/ (golden, optionally with a mutant patch applied) and the vendored runtime.
--no-legacy removes DEST/<legacy name> at the end (a case seed holds migration/ only; run.py supplies the legacy).
--no-pipeline leaves pipeline/ out (fresh-mode material, not used by the cases).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
PLUGIN = REPO / "axis-migrate"
AXPY = PLUGIN / "scripts" / "axpy"
DISC = PLUGIN / "skills" / "discover"
RUN_LOCAL = PLUGIN / "skills" / "run" / "run_local.py"
BASES = REPO / "evals" / "cases" / "_bases"

S1, S2, S3, S4 = "s01_prepare_inputs", "s02_buffer_landuse", "s03_clip_dissolve", "s04_area_per_commune"
LU_KEY = ["osm_id", "osm_way_id"]
COMPARE = {
    "ds.01_communes_2169": {"key": ["shapeID"], "match": "key"},
    "ds.01_landuse_2169": {"key": LU_KEY, "match": "key"},
    "ds.02_buf_industrial": {"key": LU_KEY, "match": "key"},
    "ds.02_buf_other": {"key": LU_KEY, "match": "key"},
    "ds.02_buffers": {"key": LU_KEY, "match": "key"},
    "ds.03_buf_x_commune": {"key": LU_KEY + ["shapeID"], "match": "key"},
    "ds.03_buf_by_commune": {"key": ["shapeID"], "match": "key"},
    "ds.buffer_area_by_commune_gpkg": {"key": ["shapeID"], "match": "key"},
    "ds.buffer_area_by_commune_csv": {"key": ["shapeID"], "match": "key", "header_row": 1},
}
STEP_PARAMS = {
    S1: ["p.study_communes", "p.work_crs"],
    S2: ["p.industrial_class", "p.buffer_dist_industrial_m", "p.buffer_dist_other_m", "p.buffer_segments"],
    S3: ["p.commune_key"],
    S4: ["p.commune_key", "p.area_unit_m2", "p.csv_area_decimals", "p.csv_share_decimals"],
}
Q03_VERBATIM = "Yes, industrial 500 m and every other class 200 m, as in the script."
APPROVE_VERBATIM = "Approve the plan"


def sh(*args, check=True):
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise SystemExit(f"{' '.join(map(str, args[:3]))} … exit {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
    return r


def graph(mig: Path) -> dict:
    return json.loads((mig / "graph.json").read_text())


def apply(mig: Path, ops: list, *extra) -> int:
    rev = graph(mig)["revision"]
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(ops, fh)
    try:
        sh(AXPY, DISC / "graph.py", "apply", mig, "--actor", "agent", "--base-rev", rev, "--ops", fh.name, *extra)
    finally:
        Path(fh.name).unlink()
    return graph(mig)["revision"]


def ref(f: str, a: int, b: int | None = None) -> str:
    return f"02_SCRIPTS/{f}:{a}" + (f"-{b}" if b else "")


def plans() -> dict:
    vec = lambda ds: {"dataset": ds, "family": "vector", "measure": None,  # noqa: E731
                      "checks": ["crs", "geometry_type", "key_set", "attributes", "geometry"], "tolerance_ids": []}
    return {
        S1: {"summary": "Study communes (name list) and the landuse polygons touching them, both in EPSG:2169; "
                        "landuse repaired like Fix geometries (structure).",
             "legacy_ops": [{"tool": "native:extractbyexpression", "ref": ref("01_prepare_inputs.py", 35, 38),
                             "settings": {"expression": "shapeName IN (14 names)"}},
                            {"tool": "native:reprojectlayer", "ref": ref("01_prepare_inputs.py", 41, 44),
                             "settings": {"target_crs": "EPSG:2169"}},
                            {"tool": "native:reprojectlayer", "ref": ref("01_prepare_inputs.py", 47, 50),
                             "settings": {"target_crs": "EPSG:2169"}},
                            {"tool": "native:fixgeometries", "ref": ref("01_prepare_inputs.py", 53, 54),
                             "settings": {"method": 1}},
                            {"tool": "native:extractbylocation", "ref": ref("01_prepare_inputs.py", 56, 60),
                             "settings": {"predicate": "intersect"}}],
             "oss": [{"op": "GeoDataFrame filter + to_crs", "why": "attribute filter keeps file order",
                      "skill_refs": ["V-7"]},
                     {"op": "shapely.make_valid(method='structure')", "why": "QGIS fix geometries, structure method",
                      "skill_refs": ["GV-1"]},
                     {"op": "STRtree.query(predicate='intersects')", "why": "select by location is a filter",
                      "skill_refs": ["V-5"]}],
             "params": STEP_PARAMS[S1],
             "outputs_checked": [vec("ds.01_communes_2169"), vec("ds.01_landuse_2169")],
             "risks": [{"skill_ref": "GV-1", "risk": "repair method changes geometry",
                        "evidence": ref("01_prepare_inputs.py", 53, 54)}],
             "isolation": {"possible": True, "legacy_inputs": [], "missing": []}},
        S2: {"summary": "Per-class buffers: industrial at p.buffer_dist_industrial_m, every other class at "
                        "p.buffer_dist_other_m, in EPSG:2169, 5 segments, round caps/joins; merged industrial first.",
             "legacy_ops": [{"tool": "native:extractbyexpression", "ref": ref("02_buffer_landuse.py", 37, 40),
                             "settings": {"branch": "if grp == 'industrial' (l.31) / else (l.34)"}},
                            {"tool": "native:buffer", "ref": ref("02_buffer_landuse.py", 42, 51),
                             "settings": {"distance": "500 | 200", "segments": 5, "end_cap": "round",
                                          "join": "round", "dissolve": False}},
                            {"tool": "native:mergevectorlayers", "ref": ref("02_buffer_landuse.py", 55, 58),
                             "settings": {}},
                            {"tool": "native:deletecolumn", "ref": ref("02_buffer_landuse.py", 61, 64),
                             "settings": {"columns": ["fid", "layer", "path"]}}],
             "oss": [{"op": "shapely.buffer(quad_segs=5, cap_style=round, join_style=round)",
                      "why": "native:buffer SEGMENTS=5 is GEOS quadrant segments", "skill_refs": ["C-2", "GV-3"]}],
             "params": STEP_PARAMS[S2],
             "outputs_checked": [vec("ds.02_buf_industrial"), vec("ds.02_buf_other"), vec("ds.02_buffers")],
             "risks": [{"skill_ref": "L-6", "risk": "class branch: industrial distance differs",
                        "evidence": ref("02_buffer_landuse.py", 31, 36)},
                       {"skill_ref": "C-2", "risk": "buffer distance needs a metric CRS",
                        "evidence": "NOTES.txt:23"}],
             "isolation": {"possible": True, "legacy_inputs": ["ds.01_landuse_2169"], "missing": []}},
        S3: {"summary": "Buffers intersected with the communes (one piece per pair), pieces dissolved per commune.",
             "legacy_ops": [{"tool": "native:intersection", "ref": ref("03_clip_dissolve.py", 25, 31),
                             "settings": {"input_fields": ["osm_id", "osm_way_id", "landuse"],
                                          "overlay_fields": ["shapeName", "shapeID"]}},
                            {"tool": "native:retainfields", "ref": ref("03_clip_dissolve.py", 33, 36),
                             "settings": {}},
                            {"tool": "native:dissolve", "ref": ref("03_clip_dissolve.py", 38, 42),
                             "settings": {"field": ["shapeID"]}}],
             "oss": [{"op": "shapely.intersection per STRtree pair, polygonal parts only",
                      "why": "QGIS intersection keeps the input geometry type", "skill_refs": ["V-6"]},
                     {"op": "shapely.union_all per commune", "why": "dissolve by field", "skill_refs": ["GV-4"]}],
             "params": STEP_PARAMS[S3],
             "outputs_checked": [vec("ds.03_buf_x_commune"), vec("ds.03_buf_by_commune")],
             "risks": [{"risk": "none"}],
             "isolation": {"possible": True, "legacy_inputs": ["ds.01_communes_2169", "ds.02_buffers"],
                           "missing": []}},
        S4: {"summary": "Planar areas in km2, commune area joined on shapeID, share in percent; CSV rounded.",
             "legacy_ops": [{"tool": "native:fieldcalculator", "ref": ref("04_area_per_commune.py", 28, 35),
                             "settings": {"formula": "area($geometry) / 1000000"}},
                            {"tool": "native:joinattributestable", "ref": ref("04_area_per_commune.py", 46, 55),
                             "settings": {"method": 1}},
                            {"tool": "csv.writer", "ref": ref("04_area_per_commune.py", 68, 73),
                             "settings": {"round": [3, 3, 1]}}],
             "oss": [{"op": "GeoSeries.area / p.area_unit_m2", "why": "planar area in the layer CRS",
                      "skill_refs": ["C-2"]}],
             "params": STEP_PARAMS[S4],
             "outputs_checked": [vec("ds.buffer_area_by_commune_gpkg"),
                                 {"dataset": "ds.buffer_area_by_commune_csv", "family": "table", "measure": None,
                                  "checks": ["key_set", "attributes"], "tolerance_ids": []}],
             "risks": [{"risk": "none"}],
             "isolation": {"possible": True, "legacy_inputs": ["ds.01_communes_2169", "ds.03_buf_by_commune"],
                           "missing": []}},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dest")
    ap.add_argument("--base", default="s1", choices=["s1", "s1L"])
    ap.add_argument("--legacy-name", default="s1-buffers-legacy")
    ap.add_argument("--pipeline", default=str(HERE / "pipeline"))
    ap.add_argument("--params", default=str(HERE / "params.yaml"))
    ap.add_argument("--patch", action="append", default=[], help="unified diff applied to pipeline/ (patch -p1)")
    ap.add_argument("--no-legacy", action="store_true")
    ap.add_argument("--no-pipeline", action="store_true")
    ap.add_argument("--no-approve", action="store_true")
    a = ap.parse_args()
    dest = Path(a.dest).resolve()
    if dest.exists():
        raise SystemExit(f"{dest} exists; remove it first")
    dest.mkdir(parents=True)
    leg = dest / a.legacy_name
    shutil.copytree(BASES / f"{a.base}-buffers-legacy", leg)
    mig = dest / "migration"
    sh(AXPY, DISC / "scan.py", leg, "--out", mig)
    sh(AXPY, DISC / "lineage.py", mig)
    g = graph(mig)
    qs = {q["id"]: q for q in g["questions"]}
    ops = []
    for qid, q in sorted(qs.items()):
        if q["kind"] == "lineage" and q["node"] == "ds.02_buffers" and "shape" in q["text"]:
            ops.append({"type": "dismiss_question", "id": qid,
                        "reason": "OVERLAY_FIELDS of native:intersection are fields of the overlay layer "
                                  "ds.01_communes_2169 (03_clip_dissolve.py:29-30), which has them"})
    if ops and len(ops) != 2:     # lineage fixed (1c3df82): these questions no longer appear; dismiss them if present
        raise SystemExit(f"expected lineage's two false field questions, found {sorted(qs)}")
    known = {d["id"] for d in g["datasets"]}
    for ds, c in COMPARE.items():
        if ds not in known:
            raise SystemExit(f"lineage did not produce {ds}: {sorted(known)}")
        for k, v in dict(c, measure_crs_param="p.measure_crs").items():
            ops.append({"type": "set_field", "id": ds, "field": f"compare.{k}", "value": v})
    for s, ps in STEP_PARAMS.items():
        ops.append({"type": "set_field", "id": s, "field": "params", "value": ps})
        ops.append({"type": "set_new", "id": s, "new": {"module": f"pipeline/{s}.py", "function": "run"}})
    apply(mig, ops)
    apply(mig, [{"type": "add_question", "id": "q03", "node": S2, "nodes": ["ds.02_buf_industrial", "ds.02_buffers"],
                 "param_ids": ["p.buffer_dist_industrial_m", "p.buffer_dist_other_m"], "kind": "branch",
                 "header": "Buffer distances",
                 "text": "Q-3 · 02_buffer_landuse.py:31-36 buffers industrial landuse at 500 m and every other class "
                         "at 200 m (NOTES.txt:22). Keep exactly this per-class rule?",
                 "why": "the distance is chosen by an if on the class; both paths must be ported",
                 "trigger": {"ref": ref("02_buffer_landuse.py", 31, 36), "quote": "if grp == \"industrial\":"},
                 "evidence": [{"ref": "NOTES.txt:22", "fact": "industrial 500 m, everything else 200 m"}],
                 "agent_guess": {"option": "keep", "why": "code and NOTES agree"},
                 "options": [{"value": "keep", "label": "Keep 500 m / 200 m (Recommended)",
                              "description": "as the script", "recommended": True},
                             {"value": "dont_know", "label": "Don't know",
                              "description": "Use the recommended option as a visible assumption"}],
                 "allow_other": True, "allow_dont_know": True,
                 "impact": {"datasets": ["ds.02_buffers"], "level": "high"},
                 "priority": {"impact": 3, "uncertainty": 1, "cost": 1, "score": 3}}])
    apply(mig, [{"type": "answer_question", "id": "q03", "value": "keep", "text": None}],
          "--channel", "interview", "--verbatim", Q03_VERBATIM)
    apply(mig, [{"type": "set_plan", "id": s, "plan": p} for s, p in plans().items()])
    if not a.no_approve:
        apply(mig, [{"type": "approve_plan", "steps": "*"}], "--channel", "interview", "--verbatim", APPROVE_VERBATIM)
    shutil.copyfile(a.params, mig / "params.yaml")
    (mig / "tolerances.yaml").write_text("version: 1\ndefaults: {}\nrecords: []\n")
    (mig / "known-differences.md").write_text("# Known differences\n")
    (mig / "decisions.md").write_text(
        "# Decisions\n\n## D-001 · Per-class buffer distances · 2026-10-14T00:00:00Z\n\n```yaml\n"
        "id: D-001\nquestion: q03\nkind: branch\nanswer: keep\nby: user (interview)\n"
        "params: [p.buffer_dist_industrial_m, p.buffer_dist_other_m]\nnodes: [s02_buffer_landuse]\n"
        "changes: []\nsupersedes: null\nevidence: [02_SCRIPTS/02_buffer_landuse.py:31-36, NOTES.txt:22]\n```\n")
    if not a.no_pipeline:
        shutil.copytree(a.pipeline, mig / "pipeline", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for p in a.patch:
            r = subprocess.run(["patch", "-p1", "--no-backup-if-mismatch", "-d", str(mig / "pipeline"), "-i",
                                str(Path(p).resolve())], capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f"patch {p} failed:\n{r.stdout}{r.stderr}")
        sh(AXPY, RUN_LOCAL, mig, "--vendor-runtime")
    sh(AXPY, DISC / "graph.py", "render-plan", mig)
    v = sh(AXPY, DISC / "graph.py", "validate", mig, check=False)
    if v.returncode != 0:
        print(v.stdout, v.stderr, file=sys.stderr)
        raise SystemExit("graph.py validate failed")
    if a.no_legacy:
        shutil.rmtree(leg)
    print(mig)
    return 0


if __name__ == "__main__":
    sys.exit(main())
