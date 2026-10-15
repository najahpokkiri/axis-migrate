#!/usr/bin/env python3
"""seal.py — freeze a verified run into a content-addressed evidence bundle (07 §3.2–3.5, CONTRACTS §9).

    axpy seal.py <migration> --run <run_id> [--json]
    axpy seal.py <migration> --run <run_id> --amend [--target]

Runs once, after compare, plots, display layers and the visual review. Re-hashes everything the run and verify
produced, writes runs/<id>/verify/evidence.json (axis-migrate.evidence.v1) + EVIDENCE_ID, makes verify/ read-only and
appends a line to runs/index.jsonl. `--amend` writes evidence.<n>.json with parent_evidence_id and may add only `vis`
(runs/<id>/amend/visual-review.json), `view` (GeoLibre view projects listed in runs/<id>/amend/views/views.json,
which a view receipt cites), `dec-e`/`kd-e` (new entries in the live decisions.md / known-differences.md) and, with
--target, `tgt` (migration/databricks/ and runs/<id>/target/). The first seal binds the view projects of
verify/views/views.json as `view` objects (display only: a changed view project only stops its receipts counting).

Exit: 0 sealed · 2 refused (already sealed, run not found, secret found) · 70 harness error.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE))
import axis_common as ac  # noqa: E402
import ladder  # noqa: E402
import visual_receipt as vrc  # noqa: E402

try:
    import yaml  # pinned env
except ImportError:  # pragma: no cover
    yaml = None

SEAL_VERSION = "seal.v1"
REPORT_VERSION = "report.v1"
TEXT_SUFFIXES = (".json", ".jsonl", ".yaml", ".yml", ".md", ".txt", ".py", ".stdout", ".stderr", ".reads", ".log",
                 ".sha256", ".csv", ".toml", ".lock", ".geojson", ".html", ".js")


class SealError(Exception):
    pass


# ------------------------------------------------------------------------------------------------------------------
# Record parsing (YAML lives here, not in the stdlib ladder: the parsed records are sealed into evidence.json)
# ------------------------------------------------------------------------------------------------------------------
def _yaml_load(text: str):
    if yaml is None:
        raise SealError("pyyaml missing: run seal.py with scripts/axpy")
    return yaml.safe_load(text)


def _jsonable(v):
    """YAML may yield dates; keep records JSON-clean and deterministic."""
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def md_entries(text: str, prefix: str) -> list[dict]:
    """Parse `## D-nnn · …` / `## KD-nnn · …` blocks: heading + first fenced yaml block."""
    out = []
    for eid in ladder.entry_ids(text, prefix):
        et = ladder.entry_text(text, eid)
        m = re.search(r"```ya?ml\s*\n(.*?)\n```", et, flags=re.S)
        data = {}
        if m:
            try:
                data = _yaml_load(m.group(1)) or {}
            except Exception as e:  # malformed entry is recorded, not dropped
                data = {"_parse_error": str(e)[:200]}
        head = et.split("\n", 1)[0]
        parts = [p.strip() for p in head.lstrip("#").split("·")]
        out.append({"entry": eid, "title": parts[1] if len(parts) > 1 else "",
                    "timestamp": parts[2] if len(parts) > 2 else None, "data": _jsonable(data),
                    "entry_sha": ladder.entry_sha(et)})
    return out


def parse_params(text: str | None) -> list[dict]:
    if not text:
        return []
    doc = _yaml_load(text) or {}
    out = []
    for p in doc.get("params") or []:
        if not isinstance(p, dict):
            continue
        fit = p.get("fit") or {}
        res = fit.get("result") or {}
        flags = list(p.get("flags") or [])
        if "holdout_pass" in res:
            hold = bool(res.get("holdout_pass"))
        else:
            hold = bool(fit.get("split")) and res.get("holdout_score") is not None and \
                res.get("holdout_score") <= (res.get("best_score") or 0) and res.get("unique") is not False
        if res.get("holdout_inconclusive"):
            hold = False
        rec = {"id": p.get("id"), "node": p.get("node"), "kind": p.get("kind"), "source": p.get("source"),
               "resolution": p.get("resolution"), "status": p.get("status"), "confidence": p.get("confidence"),
               "method": p.get("method"), "flags": flags, "question": p.get("question"),
               "decisions": list(p.get("decisions") or []), "value": p.get("value"), "unit": p.get("unit"),
               "reason": p.get("reason"), "fitted_against": p.get("fitted_against") or fit.get("reference"),
               "holdout_pass": hold,
               "global": bool(p.get("kind") in ladder.GLOBAL_PARAM_KINDS or fit.get("global")
                              or "global" in flags),
               "evidence": [e.get("ref") if isinstance(e, dict) else str(e) for e in p.get("evidence") or []],
               "impact": (p.get("impact") or {}).get("datasets") or [],
               # ladder.is_parity_blind: an assumed, high-impact, parity-blind parameter caps its steps
               "impact_level": (p.get("impact") or {}).get("level")}
        out.append(_jsonable(rec))
    return sorted(out, key=lambda r: str(r.get("id")))


def parse_tolerances(text: str | None) -> list[dict]:
    if not text:
        return []
    doc = _yaml_load(text) or {}
    out = []
    for r in doc.get("records") or []:
        if not isinstance(r, dict):
            continue
        r = _jsonable(r)
        cs, nw = r.get("correct_spread") or {}, r.get("nearest_wrong") or {}
        out.append({"id": r.get("id"), "record_id": ac.object_id("tol", r), "dataset": r.get("dataset"),
                    "check": r.get("check"), "metric": r.get("metric"), "atol": r.get("atol"),
                    "rtol": r.get("rtol"), "value": r.get("value"), "max_mismatch": r.get("max_mismatch"),
                    "units": r.get("units"), "agreed_by": r.get("agreed_by"), "agreed_at": r.get("agreed_at"),
                    "signed": bool(r.get("agreed_by")),
                    "correct_spread": cs.get("value") if isinstance(cs, dict) else cs,
                    "nearest_wrong": nw.get("value") if isinstance(nw, dict) else nw,
                    "decision_ref": r.get("decision_ref")})
    return sorted(out, key=lambda r: str(r.get("id")))


def parse_kds(text: str | None, in_contract: bool) -> list[dict]:
    out = []
    for e in md_entries(text or "", "KD"):
        d = e["data"] if isinstance(e["data"], dict) else {}
        out.append({"id": d.get("id") or e["entry"], "entry": e["entry"], "title": e["title"],
                    "kind": d.get("kind"), "datasets": d.get("datasets") or [], "checks": d.get("checks") or [],
                    "reason_codes": d.get("reason_codes") or [], "rule": d.get("rule"), "bound": d.get("bound"),
                    "reason": d.get("reason"), "owner": d.get("owner"), "status": d.get("status"),
                    "decided_at": d.get("decided_at"), "decision_ref": d.get("decision_ref"),
                    "entry_id": f"kd-e:{e['entry_sha']}", "in_contract": in_contract,
                    "parse_error": d.get("_parse_error")})
    return out


def parse_decisions(text: str | None, amended: bool = False) -> list[dict]:
    out = []
    for e in md_entries(text or "", "D"):
        d = e["data"] if isinstance(e["data"], dict) else {}
        out.append({"id": d.get("id") or e["entry"], "entry": e["entry"], "title": e["title"],
                    "timestamp": e["timestamp"], "kind": d.get("kind"), "by": d.get("by"),
                    "question": d.get("question"), "answer": d.get("answer"), "params": d.get("params") or [],
                    "nodes": d.get("nodes") or [], "clears": d.get("clears") or [],
                    "supersedes": d.get("supersedes"), "entry_id": f"dec-e:{e['entry_sha']}", "amended": amended,
                    "parse_error": d.get("_parse_error")})
    return out


def parse_records(snap: Path) -> dict:
    def rd(name):
        p = snap / name
        return p.read_text(encoding="utf-8") if p.is_file() else None
    return {"params": parse_params(rd("params.yaml")), "tolerances": parse_tolerances(rd("tolerances.yaml")),
            "known_differences": parse_kds(rd("known-differences.md"), True),
            "decisions": parse_decisions(rd("decisions.md"))}


# ------------------------------------------------------------------------------------------------------------------
# Object builders
# ------------------------------------------------------------------------------------------------------------------
class Builder:
    def __init__(self, migration: Path, run_id: str):
        self.migration = migration
        self.project = migration.parent
        self.run_id = run_id
        self.run_dir = migration / "runs" / run_id
        self.verify = self.run_dir / "verify"
        self.objects: dict[str, dict] = {}

    def rel(self, p: Path) -> str:
        return Path(os.path.relpath(p, self.project)).as_posix()

    def add(self, obj: dict) -> str:
        obj = {k: v for k, v in obj.items() if v is not None and v != [] and v != {}}
        key = ladder.okey(obj)
        self.objects.setdefault(key, obj)
        return obj["id"]

    def file_obj(self, kind: str, p: Path, **kw) -> str:
        h = ac.sha256_file(p)
        return self.add({"id": f"{kind}:{h}", "kind": kind, "path": self.rel(p), "sha256": "sha256:" + h,
                         "size": p.stat().st_size, **kw})

    def tree_obj(self, kind: str, p: Path, **kw) -> str:
        h = ladder.tree_hex(p)
        return self.add({"id": f"{kind}:{h}", "kind": kind, "path": self.rel(p), "sha256": "sha256:" + h, **kw})

    def json_obj(self, kind: str, p: Path, **kw) -> str:
        doc = ac.load_json(p)
        return self.add({"id": ladder.json_content_id(kind, doc), "kind": kind, "path": self.rel(p),
                         "sha256": "sha256:" + ac.sha256_file(p), "size": p.stat().st_size, **kw})


def _load(p: Path, default=None):
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return default


def plots_index(verify: Path) -> list[dict]:
    """Normalise plots.json variants into [{dataset, path(abs), sha256, name, kind, compare_result_id}]."""
    docs = []
    for cand in sorted((verify / "plots").glob("*/plots.json")) + [verify / "plots" / "plots.json",
                                                                     verify / "plots.json"]:
        if cand.is_file():
            d = _load(cand)
            if isinstance(d, dict) and "datasets" in d:
                docs.extend((cand, x) for x in d["datasets"])
            elif isinstance(d, list):
                docs.extend((cand, x) for x in d)
            elif isinstance(d, dict):
                docs.append((cand, d))
    out = []
    for cand, d in docs:
        ds = d.get("dataset_id") or d.get("dataset")
        for png in d.get("png") or d.get("plots") or []:
            name = png.get("name") or png.get("path")
            p = (cand.parent / name) if not str(name).startswith("plots/") else (verify / name)
            if not p.is_file() and (verify / "plots" / ds / Path(name).name).is_file():
                p = verify / "plots" / ds / Path(name).name
            out.append({"dataset": ds, "path": p, "sha256": png.get("sha256"), "name": Path(name).name,
                        "kind": png.get("kind") or Path(name).stem, "compare_result_id": d.get("compare_result_id"),
                        "index": cand})
    return out


def secret_scan(paths: list[Path], project: Path) -> dict:
    hits = []
    for p in sorted(set(paths)):
        if not p.is_file() or not p.name.endswith(TEXT_SUFFIXES) or p.stat().st_size > 20_000_000:
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        names = ladder.secret_scan_text(txt)
        if names:
            hits.append({"path": Path(os.path.relpath(p, project)).as_posix(), "patterns": names})
    return {"patterns": ladder.SECRET_PATTERN_NAMES, "files_scanned": len(paths), "matches": len(hits),
            "files": hits}


def make_readonly(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            p = Path(dirpath) / fn
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    for dirpath, dirnames, filenames in sorted(os.walk(root), key=lambda t: (-len(t[0]), t[0])):
        p = Path(dirpath)
        p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def append_index(migration: Path, rec: dict):
    p = migration / "runs" / "index.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, sort_keys=True, ensure_ascii=False, allow_nan=False)
    import fcntl
    with open(p, "a", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # same lock as the runner's append_index
        try:
            fh.write(line + "\n")
            fh.flush()
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ------------------------------------------------------------------------------------------------------------------
# Seal
# ------------------------------------------------------------------------------------------------------------------
def build_evidence(migration: Path, run_id: str) -> tuple[dict, Builder]:
    bd = Builder(migration, run_id)
    run_dir, verify = bd.run_dir, bd.verify
    manifest = _load(run_dir / "manifest.json")
    if not isinstance(manifest, dict):
        raise SealError(f"{run_dir}/manifest.json missing or unreadable")
    snap = run_dir / "snapshot"
    graph = _load(snap / "graph.json")
    if not isinstance(graph, dict):
        raise SealError("snapshot/graph.json missing")
    inventory = _load(snap / "inventory.json") or _load(migration / "inventory.json") or {}
    legacy = (migration / graph["project"]["legacy_root"]).resolve()
    legacy_rel = bd.rel(legacy)
    inv_files = {f.get("path"): f for f in inventory.get("files") or []}

    # sealed project files
    sealed = {}
    for key, kind in (("graph", "graph"), ("params", "params"), ("tolerances", "tol"), ("decisions", "dec"),
                      ("known_differences", "kd"), ("lock", "lock")):
        p = snap / ladder.SNAPSHOT_FILES[key]
        if p.is_file():
            sealed[key] = bd.file_obj(kind, p, name=p.name)
        else:
            sealed[key] = None
    if (snap / "inventory.json").is_file():
        sealed["inventory"] = bd.file_obj("aux", snap / "inventory.json", name="inventory.json")
    if (snap / "axis_pipeline").is_dir():
        sealed["code"] = bd.tree_obj("code", snap / "axis_pipeline")
    else:
        sealed["code"] = None
    records = parse_records(snap)
    # per-record tolerance ids live in records.tolerances[].record_id and are accepted as compare refs.tol

    # entries
    for name, kind, prefix in (("decisions.md", "dec-e", "D"), ("known-differences.md", "kd-e", "KD")):
        p = snap / name
        if p.is_file():
            txt = p.read_text(encoding="utf-8")
            for eid in ladder.entry_ids(txt, prefix):
                et = ladder.entry_text(txt, eid)
                h = ladder.entry_sha(et)
                bd.add({"id": f"{kind}:{h}", "kind": kind, "path": bd.rel(p), "sha256": "sha256:" + h,
                        "entry": eid})

    # inputs
    for inp in sorted(manifest.get("inputs") or [], key=lambda x: str(x.get("dataset_id"))):
        lp = legacy / inp["legacy_path"]
        sha = ladder.hex_of(inp.get("sha256")) or (ac.sha256_file(lp) if lp.is_file() else None)
        if sha is None:
            continue
        inv_sha = ladder.hex_of((inv_files.get(inp["legacy_path"]) or {}).get("sha256"))
        bd.add({"id": f"in:{sha}", "kind": "in", "path": f"{legacy_rel}/{inp['legacy_path']}",
                "sha256": "sha256:" + sha, "size": inp.get("bytes") or inp.get("size"),
                "dataset": inp.get("dataset_id"),
                "meta": {"inventory_sha256": inv_sha and "sha256:" + inv_sha,
                         "matches_inventory": inv_sha == sha if inv_sha else None,
                         "unchanged_after_run": inp.get("unchanged_after_run")}})

    steps = {s["id"]: s for s in graph.get("steps") or []}
    datasets = {d["id"]: d for d in graph.get("datasets") or []}
    # references
    for dsid in sorted(datasets):
        ds = datasets[dsid]
        if not ladder.ds_has_reference(ds, datasets):
            continue
        prod = ladder.producer_of(ds, steps)
        if prod and ladder.step_class(steps[prod]) in ("drop", "bridged"):
            continue
        via = ladder.equivalent_via(ds, datasets)
        if (ds.get("reference") or {}).get("source") == "equivalent" and via is None:
            continue  # unconfirmed equivalent pairing: compare reports PAIRING_UNCONFIRMED, nothing to pin
        src = via or ds  # an equivalent pairing shares the via dataset's legacy file (one ref object per dataset)
        lpath = ladder.ds_legacy_path(src)
        lp = legacy / lpath
        pin = inv_files.get(lpath) or {}
        sha = ladder.hex_of(pin.get("sha256") or (src.get("legacy") or {}).get("sha256"))
        meta = {"reference_kind": "legacy_equivalent" if via else "legacy_recorded", "independence": "R1",
                "produced_by": ladder.producer_of(src, steps) if via else prod,
                "via_dataset": via["id"] if via else None,
                "confirmed_by": (ds.get("reference") or {}).get("confirmed_by") if via else None,
                "content_sha256": pin.get("content_sha256") or (src.get("legacy") or {}).get("content_sha256"),
                "layer": (src.get("legacy") or {}).get("layer")}
        meta = {k: v for k, v in meta.items() if v is not None}
        if lp.is_dir():
            h = ladder.tree_hex(lp)
            bd.add({"id": f"ref:{h}", "kind": "ref", "path": f"{legacy_rel}/{lpath}", "sha256": "sha256:" + h,
                    "dataset": dsid, "step": prod, "meta": {**meta, "tree": True}})
            continue
        if sha is None:
            if not lp.is_file():
                continue
            sha = ac.sha256_file(lp)
        bd.add({"id": f"ref:{sha}", "kind": "ref", "path": f"{legacy_rel}/{lpath}", "sha256": "sha256:" + sha,
                "size": pin.get("size"), "dataset": dsid, "step": prod, "meta": meta})

    # outputs
    outs = list(manifest.get("outputs") or [])
    if not outs:
        for s in manifest.get("steps") or []:
            for o in s.get("outputs") or []:
                outs.append({**o, "written_by_step": s.get("step_id")})
    for o in sorted(outs, key=lambda x: str(x.get("dataset_id"))):
        p = run_dir / o["path"]
        if not p.exists():
            continue
        if p.is_dir():
            bd.add({"id": f"out:{ladder.tree_hex(p)}", "kind": "out", "path": bd.rel(p),
                    "sha256": "sha256:" + ladder.tree_hex(p), "dataset": o.get("dataset_id"),
                    "step": o.get("written_by_step"), "meta": {"tree": True}})
            continue
        h = ac.sha256_file(p)
        man = ladder.hex_of(o.get("sha256"))
        bd.add({"id": f"out:{h}", "kind": "out", "path": bd.rel(p), "sha256": "sha256:" + h,
                "size": p.stat().st_size, "dataset": o.get("dataset_id"), "step": o.get("written_by_step"),
                "meta": {"manifest_sha256_matches": (man == h) if man else None}})

    # compares
    cmp_ids = {}
    cmp_dir = verify / "compare"
    for p in sorted(cmp_dir.glob("*.json")) if cmp_dir.is_dir() else []:
        doc = _load(p)
        if not isinstance(doc, dict):
            bd.file_obj("aux", p, name=p.name)
            continue
        refs = doc.get("refs") or {}
        dsid = doc.get("dataset_id") or doc.get("dataset")
        oid = bd.json_obj("cmp", p, dataset=dsid, step=doc.get("step_id") or doc.get("step"),
                          refs=[x for x in [refs.get("out"), refs.get("ref")] + ladder._norm_list(refs.get("tol"))
                                if x],
                          meta={"state": ladder.declared_state(doc), "reason_codes": doc.get("reason_codes") or [],
                                "family": doc.get("family"),
                                "comparator": (doc.get("comparator") or {}).get("id"),
                                "comparator_version": (doc.get("comparator") or {}).get("version"),
                                "comparator_sha256": (doc.get("comparator") or {}).get("sha256")})
        cmp_ids[dsid] = oid
    for p in sorted(cmp_dir.glob("*")) if cmp_dir.is_dir() else []:
        if p.is_file() and p.suffix != ".json":
            bd.file_obj("aux", p, name=p.name)

    # plots
    plot_ids: dict[str, str] = {}
    listed = set()
    for pl in plots_index(verify):
        p = pl["path"]
        if not p.is_file():
            continue  # missing at seal: nothing to bind; ladder reports VISUAL_REVIEW_MISSING if no receipt
        listed.add(p.resolve())
        oid = bd.file_obj("plot", p, dataset=pl["dataset"], refs=[cmp_ids[pl["dataset"]]]
                          if pl["dataset"] in cmp_ids else None,
                          meta={"kind": pl["kind"], "listed_sha256_matches":
                                (ladder.hex_of(pl["sha256"]) == ac.sha256_file(p)) if pl["sha256"] else None})
        plot_ids[ac.sha256_file(p)] = oid
        if pl["index"].is_file():
            bd.file_obj("aux", pl["index"], name="plots.json")
    if (verify / "plots").is_dir():
        for p in sorted((verify / "plots").rglob("*.png")):
            if p.resolve() not in listed:
                ds = p.parent.name if p.parent != verify / "plots" else None
                oid = bd.file_obj("plot", p, dataset=ds, refs=[cmp_ids[ds]] if ds in cmp_ids else None,
                                  meta={"kind": p.stem, "unlisted": True})
                plot_ids[ac.sha256_file(p)] = oid
    # display layers
    if (verify / "display").is_dir():
        for p in sorted((verify / "display").rglob("*")):
            if p.is_file():
                ds = None
                m = re.match(r"(ds\.[^/]+?)\.(legacy|new|diff|triptych)\.(png|geojson)$", p.name)
                if m:
                    ds = m.group(1)
                bd.file_obj("disp", p, dataset=ds, name=p.name)
    # GeoLibre view projects (skills/map/views.py, display only): bound so that a view receipt can cite them
    view_ids = view_objects(bd, run_dir)
    # visual review
    vr = verify / "visual-review.json"
    if vr.is_file():
        bd.file_obj("aux", vr, name="visual-review.json")
        doc = _load(vr) or {}
        for f in sorted(doc.get("findings") or [], key=lambda x: str(x.get("finding_id"))):
            bd.add({"id": ac.object_id("vis", f), "kind": "vis", "path": bd.rel(vr), "finding_id": f.get("finding_id"),
                    "dataset": f.get("dataset_id"), "refs": vis_refs(f, plot_ids, view_ids),
                    "meta": {"answer": f.get("answer"), "effect": f.get("effect"),
                             "question_id": f.get("question_id"), "category": f.get("category"),
                             "by": f.get("model") or f.get("by"),
                             **({"receipt": "view"} if vrc.is_view(f) else {})}})
    for name, kind in (("verdict.json", "aux"), ("contract.json", "aux"), ("scan.json", "scan"),
                       ("tests.json", "tests")):
        p = verify / name
        if p.is_file():
            if kind == "aux":
                bd.file_obj("aux", p, name=name)
            else:
                bd.json_obj(kind, p, name=name)
    for name in ("manifest.json", "checksums.sha256"):
        if (run_dir / name).is_file():
            bd.file_obj("aux", run_dir / name, name=name)
    env = manifest.get("env") or {}
    envrt = {"python": env.get("python"), "libs": env.get("libs"), "native": env.get("native"),
             "lock_hash": env.get("lock_hash"), "platform": env.get("platform")}
    envrt = ladder_clean(envrt)
    env_id = bd.add({"id": ac.object_id("envrt", envrt), "kind": "envrt", "value": envrt})

    # run section
    step_results = []
    for s in sorted(manifest.get("steps") or [], key=lambda x: str(x.get("step_id"))):
        st = s.get("status")
        step_results.append({"step": s.get("step_id"), "status": st, "exit_code": s.get("exit_code"),
                             "ok": st in ("succeeded", "reused") and s.get("exit_code", 0) in (0, None),
                             "reason_codes": s.get("reason_codes") or [],
                             "outputs": sorted(o.get("dataset_id") for o in s.get("outputs") or []),
                             "undeclared_reads": (s.get("reads") or {}).get("undeclared") or [],
                             "started_at": s.get("started_at"), "ended_at": s.get("ended_at"),
                             "wall_time_s": s.get("duration_s")})
    chain = manifest.get("chain")
    in_scope = [sid for sid, s in steps.items() if ladder.step_class(s) == "migrate"]
    ok_steps = {r["step"] for r in step_results if r["ok"]}
    full = chain == "chained" and bool(in_scope) and all(s in ok_steps for s in in_scope)
    executor = manifest.get("executor") or {"local": "local", "e2b": "e2b", "local-refenv": "refenv"}.get(
        manifest.get("mode"))
    in_ids = sorted(o["id"] for o in bd.objects.values() if o["kind"] == "in")
    comparators = sorted({(o.get("meta") or {}).get("comparator_sha256") or "" for o in bd.objects.values()
                          if o["kind"] == "cmp"})
    identity = {"inputs": in_ids, "code": sealed.get("code"), "params": sealed.get("params"),
                "tolerances": sealed.get("tolerances"), "lock": sealed.get("lock"), "comparators": comparators,
                "executor": executor, "full": full}
    run = {"run_id": run_id, "identity": ac.object_id("run", identity), "identity_parts": identity,
           "chain": chain, "executor": executor, "isolation": "physical" if executor == "e2b" else "policy",
           "read_ledger": manifest.get("read_ledger") or env.get("read_ledger"),
           "status": manifest.get("status"), "exit_code": manifest.get("exit_code"), "full": full,
           "manifest_full": manifest.get("full"), "reason_codes": manifest.get("reason_codes") or [],
           "started_at": manifest.get("started_at") or manifest.get("created_at"),
           "ended_at": manifest.get("ended_at"), "e2b_template": (env.get("e2b") or {}).get("template"),
           "resumed_from": manifest.get("resumed_from"),
           "stub_check_passed": (None if (env.get("stub_check") or {}).get("passed") is None else
                                 (env["stub_check"]["passed"] is True and all(
                                     s.get("stub_check_passed") is not False for s in manifest.get("steps") or []))),
           "worktree_changed_during_run": (manifest.get("code") or {}).get("worktree_changed_during_run"),
           "code_hash": (manifest.get("code") or {}).get("code_hash"), "envrt": env_id,
           "step_results": step_results, "env_comparison": manifest.get("env_comparison") or [],
           "reference_env": manifest.get("reference_env")}

    # coverage
    hist = {s: 0 for s in ac.VERDICTS}
    with_legacy, not_compared, no_ref = [], [], []
    for dsid in sorted(datasets):
        ds = datasets[dsid]
        if ds.get("role") == "input":
            continue
        prod = ladder.producer_of(ds, steps)
        if prod and ladder.step_class(steps[prod]) in ("drop", "bridged"):
            continue
        if ladder.ds_has_reference(ds, datasets):
            with_legacy.append(dsid)
            if dsid in cmp_ids:
                st = next(o for o in bd.objects.values() if o["id"] == cmp_ids[dsid])["meta"]["state"]
                if st in hist:
                    hist[st] += 1
            else:
                not_compared.append(dsid)
                hist["NOT_COMPARED"] += 1
        else:
            no_ref.append(dsid)
    coverage = {"datasets_with_legacy": len(with_legacy), "compared": len([d for d in with_legacy if d in cmp_ids]),
                "not_compared": not_compared, "no_reference": no_ref, "state_histogram": hist}

    scan_paths = [p for p in sorted(run_dir.rglob("*")) if p.is_file() and "outputs" not in p.relative_to(run_dir).parts
                  and "stage" not in p.relative_to(run_dir).parts]
    sec = secret_scan(scan_paths, bd.project)

    objects = sorted(bd.objects.values(), key=lambda o: (o["kind"], o.get("path") or "", o["id"]))
    ev = {"schema": ladder.EVIDENCE_SCHEMA, "parent_evidence_id": None, "amendment": 0,
          "project": {"name": (graph.get("project") or {}).get("name"), "legacy_root": legacy_rel,
                      "legacy_tree": "tree:" + ladder.tree_hex(legacy) if legacy.is_dir() else None,
                      "migration_dir": bd.rel(migration)},
          "plugin": {"version": ac.PLUGIN_VERSION, "ladder_version": ladder.LADDER_VERSION,
                     "report_version": REPORT_VERSION, "seal_version": SEAL_VERSION},
          "run": run, "sealed": sealed, "objects": objects, "records": records, "coverage": coverage,
          "secret_scan": sec}
    return _jsonable_ev(ev), bd


def view_objects(bd: Builder, run_dir: Path) -> dict[tuple, str]:
    """`view` objects for every view project listed in verify/views/views.json and amend/views/views.json whose
    bytes still match the index. Returns {(path relative to the run, sha256): object id}."""
    out = {}
    for root in vrc.views_index_rel(run_dir):
        for v in vrc.sealable_views(run_dir, root):
            meta = {"view": v["view"], "family": v["family"], "index": f"{root}/views.json"}
            if v["family"] == "raster" and isinstance(v.get("validity_counts"), dict):
                meta["validity_counts"] = v["validity_counts"]
            oid = bd.file_obj("view", v["path"], dataset=v["dataset"], meta=meta)
            out[(Path(v["path"]).relative_to(run_dir).as_posix(), v["sha256"])] = oid
    return out


def vis_refs(f: dict, plot_ids: dict, view_ids: dict) -> list | None:
    """A PNG receipt refers to its plot object, a view receipt to the view objects it cites."""
    if vrc.is_view(f):
        cited = [f.get("view") or {}] + list(f.get("views_seen") or [])
        ids = sorted({view_ids[(c.get("project"), c.get("project_sha256"))] for c in cited if isinstance(c, dict)
                      and (c.get("project"), c.get("project_sha256")) in view_ids})
        return ids or None
    h = ladder.hex_of(f.get("plot_sha256"))
    return [plot_ids[h]] if h in plot_ids else None


def ladder_clean(d):
    return _jsonable(d)


def _jsonable_ev(ev):
    ac.cjson(ev)  # raises on NaN/Inf (07 test 16)
    return ev


def seal(migration: Path, run_id: str) -> dict:
    run_dir = migration / "runs" / run_id
    verify = run_dir / "verify"
    if not run_dir.is_dir():
        raise SealError(f"run {run_id} not found")
    if (verify / "evidence.json").exists():
        raise SealError(f"run {run_id} is already sealed; use --amend for vis/dec-e/kd-e/tgt")
    verify.mkdir(exist_ok=True)
    ev, _ = build_evidence(migration, run_id)
    eid = ac.object_id("ev", ev)
    ac.dump_json(verify / "evidence.json", ev)
    ac.atomic_write_text(verify / "EVIDENCE_ID", eid + "\n")
    make_readonly(verify)
    run = ev["run"]
    append_index(migration, {"run_id": run_id, "evidence_id": eid, "parent_evidence_id": None, "amendment": 0,
                             "chain": run.get("chain"), "executor": run.get("executor"), "status": run.get("status"),
                             "exit_code": run.get("exit_code"), "started_at": run.get("started_at"),
                             "sealed": True})
    return {"run_id": run_id, "evidence_id": eid, "objects": len(ev["objects"]),
            "secret_matches": ev["secret_scan"]["matches"]}


def amend(migration: Path, run_id: str, target: bool = False) -> dict:
    run_dir = migration / "runs" / run_id
    verify = run_dir / "verify"
    chain = ladder.evidence_chain(verify)
    if not chain:
        raise SealError(f"run {run_id} is not sealed; seal it first")
    parent = ac.load_json(chain[-1])
    parent_id = ac.object_id("ev", parent)
    n = len(chain)
    bd = Builder(migration, run_id)
    for o in parent["objects"]:
        bd.objects[ladder.okey(o)] = o
    before = set(bd.objects)
    records = json.loads(json.dumps(parent.get("records") or {}))
    # vis: findings added after seal go to runs/<id>/amend/visual-review.json (verify/ is read-only)
    avr = run_dir / "amend" / "visual-review.json"
    plot_by_sha = {ladder.hex_of(o.get("sha256")): o["id"] for o in parent["objects"] if o["kind"] == "plot"}
    # view projects: those sealed before plus any new ones (amend/views/ after seal; display only)
    view_ids = {(Path(o["path"]).relative_to(bd.rel(run_dir)).as_posix(), ladder.hex_of(o.get("sha256"))): o["id"]
                for o in parent["objects"]
                if o["kind"] == "view" and o.get("path", "").startswith(bd.rel(run_dir) + "/")}
    for (rel, h), oid in view_objects(bd, run_dir).items():
        view_ids.setdefault((rel, h), oid)
    if avr.is_file():
        doc = _load(avr) or {}
        for f in sorted(doc.get("findings") or [], key=lambda x: str(x.get("finding_id"))):
            bd.add({"id": ac.object_id("vis", f), "kind": "vis", "path": bd.rel(avr),
                    "finding_id": f.get("finding_id"), "dataset": f.get("dataset_id"),
                    "refs": vis_refs(f, plot_by_sha, view_ids),
                    "meta": {"answer": f.get("answer"), "effect": f.get("effect"), "amended": True,
                             "question_id": f.get("question_id"), "category": f.get("category"),
                             **({"receipt": "view"} if vrc.is_view(f) else {})}})
    # dec-e / kd-e: entries of the live files that the snapshot does not hold
    known_entries = {(o["kind"], o.get("entry")) for o in parent["objects"] if o["kind"] in ("dec-e", "kd-e")}
    for name, kind, prefix in (("decisions.md", "dec-e", "D"), ("known-differences.md", "kd-e", "KD")):
        p = migration / name
        if not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8")
        new_ids = [e for e in ladder.entry_ids(txt, prefix) if (kind, e) not in known_entries]
        for eid in new_ids:
            et = ladder.entry_text(txt, eid)
            h = ladder.entry_sha(et)
            bd.add({"id": f"{kind}:{h}", "kind": kind, "path": bd.rel(p), "sha256": "sha256:" + h, "entry": eid,
                    "meta": {"amended": True}})
        if kind == "dec-e":
            records.setdefault("decisions", []).extend(
                d for d in parse_decisions(txt, amended=True) if d["entry"] in new_ids)
        else:
            records.setdefault("known_differences", []).extend(
                k for k in parse_kds(txt, in_contract=False) if k["entry"] in new_ids)
    if target:
        for p in (migration / "databricks", run_dir / "target"):
            if p.is_dir():
                bd.tree_obj("tgt", p, name=p.name)
    added = [bd.objects[i] for i in sorted(set(bd.objects) - before)]
    if not added:   # not a failure (B15): the sealed evidence already holds everything there is
        return {"run_id": run_id, "evidence_id": parent_id, "parent_evidence_id": parent.get("parent_evidence_id"),
                "amendment": parent.get("amendment", 0), "added": [], "unchanged": True}
    ev = dict(parent)
    ev["parent_evidence_id"] = parent_id
    ev["amendment"] = n
    ev["records"] = records
    ev["objects"] = sorted(bd.objects.values(), key=lambda o: (o["kind"], o.get("path") or "", o["id"]))
    _jsonable_ev(ev)
    eid = ac.object_id("ev", ev)
    mode_v = verify.stat().st_mode
    verify.chmod(mode_v | stat.S_IWUSR)
    try:
        idf = verify / "EVIDENCE_ID"
        if idf.exists():
            idf.chmod(idf.stat().st_mode | stat.S_IWUSR)
        ac.dump_json(verify / f"evidence.{n}.json", ev)
        ac.atomic_write_text(idf, eid + "\n")
        for p in (verify / f"evidence.{n}.json", idf):
            p.chmod(p.stat().st_mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    finally:
        verify.chmod(mode_v & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
    run = ev["run"]
    append_index(migration, {"run_id": run_id, "evidence_id": eid, "parent_evidence_id": parent_id, "amendment": n,
                             "chain": run.get("chain"), "executor": run.get("executor"), "status": run.get("status"),
                             "exit_code": run.get("exit_code"), "started_at": run.get("started_at"),
                             "sealed": True, "added": sorted({o["kind"] for o in added})})
    return {"run_id": run_id, "evidence_id": eid, "parent_evidence_id": parent_id, "amendment": n,
            "added": [{"kind": o["kind"], "id": o["id"]} for o in added]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Seal a verified run into evidence.json (axis-migrate.evidence.v1).")
    ap.add_argument("migration")
    ap.add_argument("--run", required=True)
    ap.add_argument("--amend", action="store_true", help="add vis/dec-e/kd-e (and tgt with --target) objects")
    ap.add_argument("--target", action="store_true", help="with --amend: seal migration/databricks and run target/")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    migration = Path(a.migration).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    try:
        res = amend(migration, a.run, a.target) if a.amend else seal(migration, a.run)
    except (SealError, ac.CJSONError) as e:
        print(f"seal.py: refused: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1))
    elif res.get("unchanged"):
        print(f"nothing to amend: {res['run_id']} is sealed as {res['evidence_id']} and nothing new was found "
              "(no visual review, decision, KD or target change since); evidence unchanged")
    else:
        print(f"sealed {res['run_id']}: {res['evidence_id']}")
        if res.get("secret_matches"):
            print(f"WARNING: secret scan matched {res['secret_matches']} file(s); the bundle is EVIDENCE INCOMPLETE",
                  file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
