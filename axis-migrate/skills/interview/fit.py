"""fit.py — infer a parameter from a legacy intermediate under the fit protocol (03 §4.3, DESIGN §4.3).

    axpy fit.py declare <migration> --param p.x            # hash the fit block written in params.yaml
    axpy fit.py run     <migration> --param p.x [--json]   # isolated runs per candidate, fit + hold-out

The fit block is written in params.yaml BEFORE fitting (candidates from evidence, ≤12, no continuous optimisation;
metric; reference dataset produced by the parameter's step; a split frozen in advance). `declare` prints its
spec_sha256 and the decisions.md entry (kind fit_spec) the agent must append; `run` refuses unless that entry exists
with the same hash. Each candidate runs the step in isolation (legacy copies of its inputs, staged by the runner) in a
throw-away copy of the project, so fit runs never enter the real run history. `run` writes the result into
params.yaml (`fit.result`, `result_sha256`, `source: fitted`, `resolution: inferred`, `flags: fitted_on_reference`)
and a JSON report under migration/analysis/fit/. A fit never confirms anything: the ladder caps the step until the
hold-out passes (non-global parameters) or a person confirms the value.

Split forms: {kind: zones, zones: <ds>, field: <col>, fit: [values], holdout: rest|[values]} for rasters
(zones rasterised by cell centre on the reference grid); {kind: key_hash, key: [cols]} for vectors and tables.
Metrics: cell_mismatch_count (raster: valid-mask XOR + value inequality), row_mismatch_count (vector/table rows
whose key is missing on one side or whose attributes differ).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402

import yaml

PLUGIN = Path(__file__).resolve().parents[2]
SPEC_KEYS_EXCLUDED = {"result", "result_sha256", "spec_sha256"}


def _params(mdir: Path) -> dict:
    return yaml.safe_load((mdir / "params.yaml").read_text(encoding="utf-8"))


def _param(data: dict, pid: str) -> dict:
    for p in data.get("params") or []:
        if p.get("id") == pid:
            return p
    raise SystemExit(f"fit.py: {pid} not in params.yaml")


def spec_sha256(fit: dict, pid: str, node: str) -> str:
    spec = {k: v for k, v in fit.items() if k not in SPEC_KEYS_EXCLUDED}
    spec["param"] = pid
    spec["node"] = node
    return ac.sha256_bytes(ac.cjson(spec))


def validate_spec(fit: dict) -> list[str]:
    errs = []
    for k in ("reference", "candidates", "metric", "split"):
        if not fit.get(k):
            errs.append(f"fit.{k} missing")
    c = fit.get("candidates") or []
    if len(c) > 12:
        errs.append("at most 12 candidates")
    if len(set(map(repr, c))) != len(c):
        errs.append("duplicate candidates")
    if fit.get("metric") not in ("cell_mismatch_count", "row_mismatch_count"):
        errs.append("metric must be cell_mismatch_count or row_mismatch_count")
    sp = fit.get("split") or {}
    if sp.get("kind") not in ("zones", "key_hash"):
        errs.append("split.kind must be zones or key_hash")
    if sp.get("kind") == "zones" and not (sp.get("zones") and sp.get("field") and sp.get("fit")):
        errs.append("zones split needs zones, field, fit")
    if sp.get("kind") == "key_hash" and not sp.get("key"):
        errs.append("key_hash split needs key")
    return errs


def declared_in_decisions(mdir: Path, sha: str) -> bool:
    p = mdir / "decisions.md"
    if not p.exists():
        return False
    text = p.read_text(encoding="utf-8")
    return "kind: fit_spec" in text and sha in text


def cmd_declare(mdir: Path, pid: str) -> int:
    data = _params(mdir)
    p = _param(data, pid)
    fit = p.get("fit") or {}
    errs = validate_spec(fit)
    if errs:
        print("fit.py declare: " + "; ".join(errs), file=sys.stderr)
        return 2
    sha = spec_sha256(fit, pid, p["node"])
    print(json.dumps({"param": pid, "spec_sha256": sha}, sort_keys=True))
    print("\nAppend to decisions.md BEFORE `fit.py run` (next free D-nnn):\n")
    print(f"## D-nnn · Fit spec for {pid} · <UTC timestamp>\n```yaml\nid: D-nnn\nquestion: null\nkind: fit_spec\n"
          f"answer: null\nby: agent\nparams: [{pid}]\nnodes: [{p['node']}]\nchanges: {{}}\nsupersedes: null\n"
          f"evidence: [\"params.yaml#{pid}.fit\"]\nspec_sha256: {sha}\n```")
    return 0


# ---------------------------------------------------------------------------------------------------------------
def _workspace(mdir: Path, pid: str, value, tmp: Path) -> Path:
    """Throw-away project: legacy symlinked, migration copied without runs/report, param value replaced."""
    graph = ac.load_json(mdir / "graph.json")
    legacy = ac.legacy_root(mdir, graph)
    rel = graph["project"]["legacy_root"]
    m2 = tmp / "project" / "migration"
    m2.mkdir(parents=True)
    link = Path(os.path.normpath(m2 / rel))
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(legacy, link)
    for item in mdir.iterdir():
        if item.name in ("runs", "report", "databricks", "analysis", ".graph"):
            continue
        dst = m2 / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)
    data = _params(m2)
    _param(data, pid)["value"] = value
    (m2 / "params.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return m2


def _run_isolated(m2: Path, step: str) -> Path:
    cmd = [*ac.axpy_cmd(), str(PLUGIN / "skills" / "run" / "run_local.py"), str(m2),
           "--chain", "isolated", "--steps", step, "--json"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"isolated run failed (exit {r.returncode}): {r.stderr[-2000:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    return m2 / "runs" / out["run_id"]


def _zone_masks(graph: dict, legacy: Path, split: dict, ref_path: Path):
    import numpy as np
    import rasterio
    from rasterio import features
    import geopandas as gpd
    zds = next(d for d in graph["datasets"] if d["id"] == split["zones"])
    gdf = gpd.read_file(legacy / zds["legacy"]["path"], layer=zds["legacy"].get("layer"))
    with rasterio.open(ref_path) as r:
        crs, transform, shape = r.crs, r.transform, (r.height, r.width)
    gdf = gdf.to_crs(crs)
    fit_vals = set(map(str, split["fit"]))
    hold = split.get("holdout", "rest")
    hold_vals = None if hold == "rest" else set(map(str, hold))
    def mask(sel):
        geoms = [g for g, v in zip(gdf.geometry, gdf[split["field"]].astype(str)) if sel(v)]
        if not geoms:
            return np.zeros(shape, bool)
        return features.rasterize([(g, 1) for g in geoms], out_shape=shape, transform=transform, fill=0,
                                  all_touched=False, dtype="uint8").astype(bool)
    m_fit = mask(lambda v: v in fit_vals)
    m_hold = mask(lambda v: (v not in fit_vals) if hold_vals is None else (v in hold_vals))
    return m_fit, m_hold


def _raster_mismatch(legacy_path: Path, new_path: Path, m_fit, m_hold) -> dict:
    import numpy as np
    import rasterio
    with rasterio.open(legacy_path) as a, rasterio.open(new_path) as b:
        if (a.height, a.width) != (b.height, b.width) or a.transform != b.transform:
            return {"fit": None, "holdout": None, "grid_mismatch": True}
        la, lb = a.read(1, masked=True), b.read(1, masked=True)
    va, vb = ~np.ma.getmaskarray(la), ~np.ma.getmaskarray(lb)
    bad = va ^ vb
    both = va & vb
    da, db = np.asarray(la.filled(0), dtype="float64"), np.asarray(lb.filled(0), dtype="float64")
    bad |= both & (da != db)
    return {"fit": int((bad & m_fit).sum()), "holdout": int((bad & m_hold).sum()), "grid_mismatch": False}


def _row_mismatch(legacy_path: Path, new_path: Path, layer, key: list) -> dict:
    import geopandas as gpd
    a = gpd.read_file(legacy_path, layer=layer)
    b = gpd.read_file(new_path, layer=layer)
    cols = [c for c in a.columns if c != "geometry"]
    def rows(df):
        return {tuple(str(df.iloc[i][k]) for k in key): tuple(repr(df.iloc[i][c]) for c in cols if c in df.columns)
                for i in range(len(df))}
    ra, rb = rows(a), rows(b)
    out = {"fit": 0, "holdout": 0}
    for k in set(ra) | set(rb):
        half = "fit" if int(hashlib.sha256(repr(k).encode()).hexdigest(), 16) % 2 == 0 else "holdout"
        if ra.get(k) != rb.get(k):
            out[half] += 1
    return out


def cmd_run(mdir: Path, pid: str, as_json: bool) -> int:
    data = _params(mdir)
    p = _param(data, pid)
    fit = p.get("fit") or {}
    errs = validate_spec(fit)
    if errs:
        print("fit.py run: " + "; ".join(errs), file=sys.stderr)
        return 2
    sha = spec_sha256(fit, pid, p["node"])
    if fit.get("spec_sha256") and fit["spec_sha256"] != sha:
        print("fit.py run: the fit block changed after it was declared (spec hash differs); declare again", file=sys.stderr)
        return 2
    if not declared_in_decisions(mdir, sha):
        print(f"fit.py run: no decisions.md entry of kind fit_spec with spec_sha256 {sha}; run `fit.py declare` "
              "and append its entry first (the spec is frozen before fitting)", file=sys.stderr)
        return 2
    graph = ac.load_json(mdir / "graph.json")
    legacy = ac.legacy_root(mdir, graph)
    ref = next((d for d in graph["datasets"] if d["id"] == fit["reference"]), None)
    if not ref or not (ref.get("legacy") or {}).get("path"):
        print(f"fit.py run: reference {fit['reference']} has no legacy file", file=sys.stderr)
        return 2
    if (ref.get("produced_by") or {}).get("step") != p["node"]:
        print(f"fit.py run: reference {fit['reference']} is not produced by {p['node']}", file=sys.stderr)
        return 2
    ref_path = legacy / ref["legacy"]["path"]
    split = fit["split"]
    results = []
    masks = None
    for cand in fit["candidates"]:
        with tempfile.TemporaryDirectory(prefix="axis-fit-") as td:
            m2 = _workspace(mdir, pid, cand, Path(td))
            run_dir = _run_isolated(m2, p["node"])
            new_path = run_dir / "outputs" / ref["new"]["path"]
            if fit["metric"] == "cell_mismatch_count":
                if masks is None:
                    masks = _zone_masks(graph, legacy, split, ref_path)
                score = _raster_mismatch(ref_path, new_path, *masks)
            else:
                score = _row_mismatch(ref_path, new_path, (ref.get("legacy") or {}).get("layer"), split["key"])
            results.append({"candidate": cand, **score})
    ok = [r for r in results if r.get("fit") == 0]
    ranked = sorted((r for r in results if r.get("fit") is not None), key=lambda r: r["fit"])
    unique = len(ok) == 1
    best = ok[0] if unique else None
    runner_up = next((r for r in ranked if r is not best), None)
    sens_hold = len({r.get("holdout") for r in results}) > 1
    result = {
        "best": best["candidate"] if best else None,
        "best_score": best["fit"] if best else None,
        "passing": [r["candidate"] for r in ok],
        "runner_up": runner_up["candidate"] if runner_up else None,
        "runner_up_score": runner_up["fit"] if runner_up else None,
        "holdout_score": best["holdout"] if best else None,
        "holdout_sensitive": sens_hold,
        "unique": unique,
        "scores": results,
    }
    result["holdout_pass"] = bool(best) and best["holdout"] == 0 and sens_hold
    result["status"] = ("fitted_holdout_pass" if result["holdout_pass"] else
                        "inconclusive_holdout_insensitive" if best and not sens_hold else
                        "holdout_fail" if best else "not_unique" if ok else "no_candidate_passes")
    rsha = ac.sha256_bytes(ac.cjson(result))
    fit["spec_sha256"] = sha
    fit["result"] = result
    fit["result_sha256"] = rsha
    if best:
        p["value"] = best["candidate"]
        p["source"] = "fitted"
        p["resolution"] = "inferred"
        p["method"] = "fit"
        p["confidence"] = "high" if result["holdout_pass"] else "low"
        p["status"] = "assumed"
        p["flags"] = sorted(set(p.get("flags") or []) | {"fitted_on_reference"})
    else:
        p["resolution"] = "unresolved"
        p["status"] = "open"
    ac.atomic_write_text(mdir / "params.yaml", yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    ac.dump_json(mdir / "analysis" / "fit" / f"{pid}.json", {"param": pid, "spec_sha256": sha, "result": result,
                                                               "result_sha256": rsha})
    if as_json:
        print(json.dumps({"param": pid, "result": result}, sort_keys=True))
    else:
        print(f"{pid}: {result['status']} best={result['best']} runner_up={result['runner_up']} "
              f"({result['runner_up_score']}) holdout={result['holdout_score']}")
    return 0 if best else 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("declare", "run"):
        s = sub.add_parser(name)
        s.add_argument("migration")
        s.add_argument("--param", required=True)
        s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    mdir = Path(a.migration).resolve()
    if a.cmd == "declare":
        return cmd_declare(mdir, a.param)
    return cmd_run(mdir, a.param, a.json)


if __name__ == "__main__":
    sys.exit(main())
