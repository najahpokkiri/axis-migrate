"""Synthetic project + sealed-run builder for the report tests (no fixture data; CONTRACTS §0).

Builds <root>/demo-legacy (read-only legacy folder) and <root>/migration with graph.json, inventory.json, params.yaml,
tolerances.yaml, decisions.md, known-differences.md, pipeline/ and one run under runs/<run_id>/ laid out exactly as
CONTRACTS §6/§7: snapshot/, manifest.json, outputs/<step>/, logs/, verify/{compare/*.json with real check arrays,
plots/<ds>/*.png + plots.json, visual-review.json, verdict.json, scan.json, tests.json, contract.json}.

Graph: s01_mask (pop → a_mask raster, a_outline vector) → s02_total (a_mask, pop, zones → b_exposure raster final,
b_by_zone csv final).
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

REPORT = Path(__file__).resolve().parents[1]
PLUGIN = REPORT.parents[1]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(REPORT))
import axis_common as ac  # noqa: E402
import ladder  # noqa: E402

LEGACY = "demo-legacy"
W, H = 24, 18
RES = 0.01
X0, Y0 = 6.0, 50.0
CRS = "EPSG:4326"
STARTED = "2026-10-16T14:15:02Z"
ENDED = "2026-10-16T14:15:09Z"


def run_id_for(n: int = 1) -> str:
    return f"r20261016T1415{n:02d}Z-3fa91c0e"


# ------------------------------------------------------------------------------------------------------------------
def _write_raster(path: Path, arr: np.ndarray, nodata, dtype, x0=X0):
    import rasterio
    from rasterio.transform import from_origin
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", width=arr.shape[1], height=arr.shape[0], count=1, dtype=dtype,
                       crs=CRS, transform=from_origin(x0, Y0, RES, RES), nodata=nodata) as dst:
        dst.write(arr.astype(dtype), 1)


def _write_vector(path: Path, layer: str, rows: list[dict]):
    import geopandas as gpd
    from shapely.geometry import box
    path.parent.mkdir(parents=True, exist_ok=True)
    g = gpd.GeoDataFrame([{k: v for k, v in r.items() if k != "bbox"} for r in rows],
                         geometry=[box(*r["bbox"]) for r in rows], crs=CRS)
    g.to_file(path, layer=layer, driver="GPKG")


def pop_array():
    yy, xx = np.mgrid[0:H, 0:W]
    return (10 + 5 * xx + 3 * yy).astype("float32")


def build_legacy(root: Path) -> Path:
    leg = root / LEGACY
    pop = pop_array()
    _write_raster(leg / "in" / "pop.tif", pop, -9999.0, "float32")
    _write_vector(leg / "in" / "zones.gpkg", "zones", [
        {"zone": "West", "bbox": (X0, Y0 - H * RES, X0 + W * RES / 2, Y0)},
        {"zone": "East", "bbox": (X0 + W * RES / 2, Y0 - H * RES, X0 + W * RES, Y0)}])
    mask = (pop > 60).astype("uint8")
    _write_raster(leg / "tmp" / "a_mask.tif", mask, 255, "uint8")
    _write_vector(leg / "tmp" / "a_outline.gpkg", "a_outline", [
        {"id": 1, "bbox": (X0 + 0.05, Y0 - 0.15, X0 + 0.2, Y0 - 0.02)}])
    expo = np.where(mask == 1, pop * 2.0, -9999.0).astype("float32")
    _write_raster(leg / "final" / "b_exposure.tif", expo, -9999.0, "float32")
    with open(leg / "final" / "b_by_zone.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["zone", "total"])
        w.writerow(["East", f"{float(expo[:, W // 2:][expo[:, W // 2:] > 0].sum()):.1f}"])
        w.writerow(["West", f"{float(expo[:, :W // 2][expo[:, :W // 2] > 0].sum()):.1f}"])
    (leg / "final" / "b_exposure.tif.aux.xml").write_text("<PAMDataset/>\n")  # pre-existing sidecar
    (leg / "NOTES.txt").write_text("synthetic legacy project for report tests\n")
    return leg


DATASETS = [
    {"id": "ds.pop", "kind": "raster", "role": "input", "legacy": {"path": "in/pop.tif"}},
    {"id": "ds.zones", "kind": "vector", "role": "input", "legacy": {"path": "in/zones.gpkg", "layer": "zones"}},
    {"id": "ds.a_mask", "kind": "raster", "role": "intermediate", "legacy": {"path": "tmp/a_mask.tif"},
     "new": {"path": "s01_mask/a_mask.tif"}, "produced_by": {"step": "s01_mask"}, "units": "flag"},
    {"id": "ds.a_outline", "kind": "vector", "role": "intermediate",
     "legacy": {"path": "tmp/a_outline.gpkg", "layer": "a_outline"},
     "new": {"path": "s01_mask/a_outline.gpkg", "layer": "a_outline"}, "produced_by": {"step": "s01_mask"}},
    {"id": "ds.b_exposure", "kind": "raster", "role": "final", "legacy": {"path": "final/b_exposure.tif"},
     "new": {"path": "s02_total/b_exposure.tif"}, "produced_by": {"step": "s02_total"}, "units": "EUR per cell"},
    {"id": "ds.b_by_zone", "kind": "table", "role": "final", "legacy": {"path": "final/b_by_zone.csv"},
     "new": {"path": "s02_total/b_by_zone.csv"}, "produced_by": {"step": "s02_total"}},
]
STEPS = [
    {"id": "s01_mask", "title": "Residential mask", "kind": "script", "scope": "migrate",
     "inputs": ["ds.pop"], "outputs": ["ds.a_mask", "ds.a_outline"], "params": ["p.threshold"],
     "new": {"module": "pipeline/s01_mask.py", "function": "run"}, "legacy": {"source": "scripts/01_mask.py"}},
    {"id": "s02_total", "title": "Exposure", "kind": "script", "scope": "migrate",
     "inputs": ["ds.a_mask", "ds.pop", "ds.zones"], "outputs": ["ds.b_exposure", "ds.b_by_zone"],
     "params": ["p.scale"], "new": {"module": "pipeline/s02_total.py", "function": "run"},
     "legacy": {"source": "scripts/02_total.py"}},
]

PARAMS_YAML = """version: 1
budget: {max_questions: 8, asked: 1}
params:
  - id: p.threshold
    node: s01_mask
    kind: threshold
    value: 60
    unit: people
    resolution: resolved
    source: code
    evidence: [{ref: "scripts/01_mask.py:4", quote: "THRESH = 60"}]
    reason: literal in the producing script
    status: confirmed
    used_by: [pipeline/s01_mask.py]
  - id: p.scale
    node: s02_total
    kind: scale
    value: 2.0
    unit: EUR per person
    resolution: resolved
    source: code
    evidence: [{ref: "scripts/02_total.py:9", quote: "VALUE = 2.0"}]
    reason: literal
    status: confirmed
    used_by: [pipeline/s02_total.py]
"""

TOL_YAML = """version: 1
defaults:
  raster.grid.transform: {metric: transform_dev_px, value: 1.0e-6, basis: float_storage}
  raster.values: {metric: abs+rel, atol: 0, rtol: 0, max_mismatch: 0}
records: []
"""

DECISIONS_MD = """# Decisions

## D-001 · Parity scope · 2026-10-15T10:00:00Z
```yaml
id: D-001
question: q01
kind: confirm_all
answer: accept
by: user (AskUserQuestion)
params: []
nodes: [s01_mask, s02_total]
changes: {}
supersedes: null
evidence: ["scripts/01_mask.py:4"]
```
Parity scope: every legacy output.
"""

KD_MD = "# Known differences\n"


def build_project(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    leg = build_legacy(root)
    mig = root / "migration"
    mig.mkdir()
    files = []
    for p in sorted(leg.rglob("*")):
        if p.is_file():
            rel = p.relative_to(leg).as_posix()
            files.append({"path": rel, "size": p.stat().st_size, "sha256": ac.sha256_file(p),
                          "content_sha256": ac.content_sha256(p), "kind": p.suffix.lstrip(".")})
    inventory = {"schema": "axis-migrate.inventory.v1", "legacy_root": f"../{LEGACY}",
                 "scan": {"read_only_verified": True, "mtime_reliable": False}, "files": files,
                 "datasets": [{"id": d["id"], "legacy_path": d["legacy"]["path"], "role": d["role"]} for d in DATASETS]}
    ac.dump_json(mig / "inventory.json", inventory)
    graph = {"schema": "axis-migrate.graph.v1",
             "project": {"name": "demo-synthetic", "legacy_root": f"../{LEGACY}", "plugin_version": "0.1.0"},
             "revision": 3, "steps": json.loads(json.dumps(STEPS)), "datasets": json.loads(json.dumps(DATASETS)),
             "edges": [], "questions": [], "invariants": [], "legacy_output_dirs": ["tmp", "final"]}
    ac.dump_json(mig / "graph.json", graph)
    (mig / "params.yaml").write_text(PARAMS_YAML)
    (mig / "tolerances.yaml").write_text(TOL_YAML)
    (mig / "decisions.md").write_text(DECISIONS_MD)
    (mig / "known-differences.md").write_text(KD_MD)
    pipe = mig / "pipeline"
    pipe.mkdir()
    (pipe / "__init__.py").write_text("")
    (pipe / "_axis_runtime.py").write_text("# vendored runtime (synthetic)\n")
    (pipe / "s01_mask.py").write_text('STEP_ID = "s01_mask"\nINPUTS = ["ds.pop"]\n'
                                      'OUTPUTS = ["ds.a_mask", "ds.a_outline"]\nPARAMS = ["p.threshold"]\n\n'
                                      'def run(ctx) -> None:\n    raise NotImplementedError\n')
    (pipe / "s02_total.py").write_text('STEP_ID = "s02_total"\nINPUTS = ["ds.a_mask", "ds.pop", "ds.zones"]\n'
                                       'OUTPUTS = ["ds.b_exposure", "ds.b_by_zone"]\nPARAMS = ["p.scale"]\n\n'
                                       'def run(ctx) -> None:\n    raise NotImplementedError\n')
    return {"root": root, "legacy": leg, "migration": mig}


def update_graph(mig: Path, fn):
    g = ac.load_json(mig / "graph.json")
    fn(g)
    ac.dump_json(mig / "graph.json", g)


# ------------------------------------------------------------------------------------------------------------------
def _png(path: Path, arr: np.ndarray):
    from PIL import Image
    a = np.asarray(arr, dtype="float64")
    a = np.nan_to_num(a)
    span = (a.max() - a.min()) or 1.0
    img = ((a - a.min()) / span * 255).astype("uint8")
    img = np.kron(img, np.ones((6, 6), dtype="uint8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img, mode="L").save(path, format="PNG")


def _check(cid, state, order, **kw):
    c = {"id": cid, "order": order, "state": state, "reason_codes": kw.pop("reason_codes", [])}
    c.update(kw)
    return c


def default_checks(ds: dict) -> list[dict]:
    if ds["kind"] == "raster":
        return [_check("raster.band_count", "PASS_EXACT", 1, gate=True, metric="bands", value=1),
                _check("raster.crs", "PASS_EXACT", 2, gate=True),
                _check("raster.shape", "PASS_EXACT", 3, gate=True, value=[H, W]),
                _check("raster.grid.transform", "PASS_EXACT", 4, gate=True, metric="transform_dev_px", value=0.0,
                       threshold=1e-6, tolerance_id="tol.default.grid.v1"),
                _check("raster.nodata_value", "PASS_EXACT", 5),
                _check("raster.mask", "PASS_EXACT", 6, metric="xor_cells", value=0),
                _check("raster.values", "PASS_EXACT", 7, metric="max_abs", value=0.0, unit="")]
    if ds["kind"] == "vector":
        return [_check("vector.crs", "PASS_EXACT", 1, gate=True), _check("vector.keys", "PASS_EXACT", 2),
                _check("vector.geometry.symdiff_ratio", "PASS_EXACT", 3, metric="symdiff_ratio", value=0.0)]
    return [_check("table.schema", "PASS_EXACT", 1), _check("table.rows", "PASS_EXACT", 2, value=2),
            _check("table.values", "PASS_EXACT", 3)]


SHIFT_CHECKS = [
    _check("raster.band_count", "PASS_EXACT", 1, gate=True),
    _check("raster.crs", "PASS_EXACT", 2, gate=True),
    _check("raster.shape", "PASS_EXACT", 3, gate=True),
    _check("raster.grid.transform", "FAIL", 4, gate=True, reason_codes=["RASTER_TRANSFORM_MISMATCH"],
           metric="transform_dev_px", value=1.0, threshold=1e-6, tolerance_id="tol.default.grid.v1",
           diagnostics={"shift_px": [1, 0]}, samples=[]),
    _check("raster.values", "NOT_COMPARED", 7, reason_codes=["GATED"], gated_by="raster.grid.transform"),
    _check("conservation.total", "PASS_EXACT", 8, metric="rel_total_drift", value=0.0),
]


def make_run(proj: dict, n: int = 1, *, compares: dict | None = None, skip_compare=(), visual="pass",
             flag: str | None = None, shift_new: bool = False, chain="chained", seal=True, display=True,
             step_status: dict | None = None, plots=True) -> dict:
    """Build runs/<id>/ for the current project state. `compares` maps ds → (state, checks, reason_codes)."""
    mig = proj["migration"]
    leg = proj["legacy"]
    rid = run_id_for(n)
    rd = mig / "runs" / rid
    snap = rd / "snapshot"
    snap.mkdir(parents=True)
    shutil.copytree(mig / "pipeline", snap / "axis_pipeline")
    for name in ("graph.json", "params.yaml", "inventory.json", "tolerances.yaml", "known-differences.md",
                 "decisions.md"):
        shutil.copy2(mig / name, snap / name)
    shutil.copy2(PLUGIN / "skills" / "run" / "env" / "uv.lock", snap / "uv.lock")
    graph = ac.load_json(mig / "graph.json")
    gds = {d["id"]: d for d in graph["datasets"]}
    steps = {s["id"]: s for s in graph["steps"]}
    # outputs: copies of the legacy files (an exact port), optionally the final raster shifted one cell east
    outputs, step_entries = [], []
    step_status = step_status or {}
    for sid, s in steps.items():
        if ladder.step_class(s) != "migrate":
            continue
        st = step_status.get(sid, "succeeded")
        souts = []
        for dsid in s["outputs"]:
            ds = gds[dsid]
            if not ds.get("new") or st != "succeeded":
                continue
            dst = rd / "outputs" / ds["new"]["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = leg / ds["legacy"]["path"]
            if shift_new and dsid == "ds.b_exposure":
                import rasterio
                with rasterio.open(src) as r:
                    arr = r.read(1)
                _write_raster(dst, arr, -9999.0, "float32", x0=X0 + RES)
            else:
                shutil.copy2(src, dst)
            rec = {"dataset_id": dsid, "path": f"outputs/{ds['new']['path']}", "sha256": ac.sha256_file(dst),
                   "bytes": dst.stat().st_size}
            souts.append(rec)
            outputs.append({**rec, "written_by_step": sid})
        step_entries.append({"step_id": sid, "status": st, "exit_code": 0 if st == "succeeded" else 1,
                             "reason_codes": [], "outputs": souts, "reads": {"ledger": f"logs/{sid}.reads",
                                                                            "undeclared": []},
                             "started_at": STARTED, "ended_at": ENDED, "duration_s": 0.4})
        (rd / "logs").mkdir(exist_ok=True)
        (rd / "logs" / f"{sid}.stdout").write_text(f"{sid} ok\n")
    inputs = []
    for dsid in ("ds.pop", "ds.zones"):
        lp = gds[dsid]["legacy"]["path"]
        inputs.append({"dataset_id": dsid, "legacy_path": lp, "staged": f"stage/inputs/{lp}",
                       "sha256": ac.sha256_file(leg / lp), "bytes": (leg / lp).stat().st_size,
                       "unchanged_after_run": True})
    all_ok = all(e["status"] == "succeeded" for e in step_entries)
    manifest = {"schema": "axis-migrate.run-manifest.v1", "run_id": rid, "chain": chain, "executor": "local",
                "status": "succeeded" if all_ok else "failed", "exit_code": 0 if all_ok else 10, "reason_codes": [],
                "steps_planned": len(step_entries), "steps_succeeded": sum(e["status"] == "succeeded"
                                                                           for e in step_entries),
                "full": chain == "chained" and all_ok, "eligible_for": "target-ready",  # never trusted
                "read_ledger": "strace",
                "code": {"code_hash": "sha256:" + ladder.tree_hex(snap / "axis_pipeline"),
                         "worktree_changed_during_run": False},
                "params": {"sha256": ac.sha256_file(snap / "params.yaml")},
                "graph": {"sha256": ac.sha256_file(snap / "graph.json")},
                "tolerances": {"sha256": ac.sha256_file(snap / "tolerances.yaml")},
                "env": {"lock_hash": "sha256:" + ac.sha256_file(snap / "uv.lock"), "python": "3.12.15",
                        "libs": {"rasterio": "1.5.2", "numpy": "2.5.4"}, "native": {"rasterio.gdal": "3.12.2"},
                        "stub_check": {"passed": True}},
                "inputs": inputs, "legacy_feeds": [], "steps": step_entries, "outputs": outputs,
                "started_at": STARTED, "ended_at": ENDED, "resumed_from": None}
    ac.dump_json(rd / "manifest.json", manifest)
    ac.atomic_write_text(rd / "checksums.sha256", "".join(f"{o['sha256']}  {o['path']}\n" for o in outputs))
    v = rd / "verify"
    (v / "compare").mkdir(parents=True)
    out_by = {o["dataset_id"]: o for o in outputs}
    compares = compares or {}
    plot_shas = {}
    for dsid, ds in sorted(gds.items()):
        if ds["role"] == "input" or dsid in skip_compare or not ladder.ds_has_reference(ds):
            continue
        prod = ladder.producer_of(ds, steps)
        if prod is None or ladder.step_class(steps[prod]) != "migrate":
            continue
        lp = leg / ds["legacy"]["path"]
        o = out_by.get(dsid)
        if dsid in compares:
            state, checks, codes = compares[dsid]
        elif shift_new and dsid == "ds.b_exposure":
            state, checks, codes = "FAIL", json.loads(json.dumps(SHIFT_CHECKS)), ["RASTER_TRANSFORM_MISMATCH"]
        elif o is None:
            state, checks, codes = "FAIL", [], ["OUTPUT_MISSING"]
        else:
            state, checks, codes = "PASS_EXACT", default_checks(ds), []
        cmp = {"schema": "axis-migrate.compare-result.v1",
               "comparator": {"id": "axis-migrate.compare", "version": "1.0.0", "sha256": "0" * 64},
               "run_id": rid, "mode": chain, "isolated_step": None, "contract_hash": "sha256:" + "1" * 64,
               "dataset_id": dsid, "step_id": prod, "family": ds["kind"], "role": ds["role"],
               "legacy": {"path": f"{LEGACY}/{ds['legacy']['path']}", "layer": ds["legacy"].get("layer"),
                          "sha256": ac.sha256_file(lp), "pinned_sha256": ac.sha256_file(lp)},
               "new": {"path": f"migration/runs/{rid}/{o['path']}" if o else None,
                       "sha256": o["sha256"] if o else None, "written_by_step": prod},
               "pairing": {"method": "lineage", "state": "confirmed"},
               "verdict": state, "reason_codes": codes, "checks": checks, "within_tolerance": [],
               "not_checked": [], "known_differences_applied": [], "env": {"gdal": "3.12.2"},
               "refs": {"out": f"out:{o['sha256']}" if o else None, "ref": f"ref:{ac.sha256_file(lp)}", "tol": []},
               "created_at": ENDED}
        cmp["result_id"] = ladder.cmp_content_id(cmp)
        ac.dump_json(v / "compare" / f"{dsid}.json", cmp)
        if plots and ds["kind"] in ("raster", "vector") and o is not None:
            pdir = v / "plots" / dsid
            arr = pop_array() if ds["kind"] == "raster" else np.eye(8)
            _png(pdir / "overview.png", arr)
            _png(pdir / "worst_tiles.png", arr[:6, :6])
            pngs = [{"name": n, "kind": n[:-4], "sha256": ac.sha256_file(pdir / n)}
                    for n in ("overview.png", "worst_tiles.png")]
            ac.dump_json(pdir / "plots.json", {"generator": "synthetic", "version": "1", "dataset_id": dsid,
                                                "compare_result_id": cmp["result_id"], "png": pngs})
            plot_shas[dsid] = pngs
    findings = []
    if visual != "none":
        for dsid, pngs in sorted(plot_shas.items()):
            is_flag = flag == dsid
            findings.append({"finding_id": f"vf-{dsid}-q1", "run_id": rid, "dataset_id": dsid,
                             "rubric_id": "rubric.v1", "rubric_sha256": "2" * 64, "question_id": "Q1",
                             "category": "boundary_stripes" if is_flag else "none",
                             "answer": "yes" if is_flag else "no", "effect": "flag" if is_flag else "none",
                             "plot": f"plots/{dsid}/{pngs[1]['name']}", "plot_sha256": pngs[1]["sha256"],
                             "panel": "tile 1 diff", "location": None,
                             "note": "paired stripes on east edges" if is_flag else "no offset visible",
                             "model": "claude-test", "created_at": ENDED})
    ac.dump_json(v / "visual-review.json", {"schema": "axis-migrate.visual-review.v1", "rubric_id": "rubric.v1",
                                            "rubric_sha256": "2" * 64, "findings": findings})
    ac.dump_json(v / "verdict.json", {"run_id": rid, "mode": chain, "pipeline": {"verdict": "see compare"}})
    ac.dump_json(v / "contract.json", {"contract_hash": "sha256:" + "1" * 64})
    ac.dump_json(v / "scan.json", {"schema": "axis-migrate.codescan.v1", "findings": [], "exit_code": 0})
    ac.dump_json(v / "tests.json", {"passed": True, "exit_code": 0, "tests": []})
    idx = mig / "runs" / "index.jsonl"
    with open(idx, "a") as fh:
        fh.write(json.dumps({"run_id": rid, "chain": chain, "executor": "local", "status": manifest["status"],
                             "exit_code": manifest["exit_code"], "started_at": STARTED}, sort_keys=True) + "\n")
    if display:
        display_module().display(rd)
    res = {"run_id": rid, "run_dir": rd}
    if seal:
        import seal as sealmod
        res.update(sealmod.seal(mig, rid))
    return res


def display_module():
    """report/map.py loaded by path: `import map` would collide with discover/map.py (inputs) on a shared sys.path."""
    import importlib.util
    if "axis_report_map" in sys.modules:
        return sys.modules["axis_report_map"]
    spec = importlib.util.spec_from_file_location("axis_report_map", REPORT / "map.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["axis_report_map"] = mod
    spec.loader.exec_module(mod)
    return mod


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_writable(p: Path):
    import os
    import stat
    for dirpath, dirnames, filenames in os.walk(p):
        os.chmod(dirpath, os.stat(dirpath).st_mode | stat.S_IWUSR)
        for f in filenames:
            fp = os.path.join(dirpath, f)
            os.chmod(fp, os.stat(fp).st_mode | stat.S_IWUSR)
