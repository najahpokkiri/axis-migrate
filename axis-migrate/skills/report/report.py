#!/usr/bin/env python3
"""report.py — render migration/report/ from ONE sealed evidence bundle (07 §4, DESIGN §4.7, CONTRACTS §9).

    axpy report.py <migration> [--run <run_id>] [--json]

Every render re-verifies every hash and re-derives every compare state (through ladder.compute) and re-parses the
sealed YAML records. Verdict wording comes only from phrases.v1.yaml. Deterministic: no clock, sorted iteration,
one number formatter; the only times shown are the run's own started_at / ended_at.

Writes report/index.html, report/assets/ (plots, display layers, compare JSON copies, layers.js, vendored MapLibre)
and report/REPORT.json {status, rung, steps{id: disposition}, assumptions[], known_differences[], files{path: sha256},
evidence_id}. Exit: 0 complete · 2 evidence incomplete, stale or nothing verified · 3 no sealed run.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
sys.path.insert(0, str(HERE))
import axis_common as ac  # noqa: E402
import ladder  # noqa: E402
import seal as sealmod  # noqa: E402

import jinja2  # noqa: E402
import yaml  # noqa: E402

REPORT_VERSION = "report.v1"
CAPTION = ("Display only: warped to Web Mercator, nearest neighbour. Verdicts are computed on the native grid "
           "({crs}).")
LIMIT_NOTE = ("Hashes prove that this bundle is internally consistent, not who wrote it. A bundle re-sealed after "
              "tampering is caught only by the compare binding, the state re-derivation, the legacy re-hash and the "
              "append-only run index.")
LADDER_DISPLAY = ["generated", "sandbox-validated", "reference-verified", "target-ready"]
DISP_STYLE = {  # disposition → (css class, glyph, label); colour is never the only signal
    "VERIFIED": ("ok", "✓", "verified"),
    "ACCEPTED_DIFFERENCE": ("amber", "≈", "accepted difference"),
    "NEEDS_REVIEW": ("amber", "?", "needs review"),
    "NEEDS_DECISION": ("purple", "!", "needs decision"),
    "FAILED": ("bad", "✗", "failed"),
    "BLOCKED": ("grey", "■", "blocked"),
    "EVALUATOR_ERROR": ("bad", "⚠", "evaluator error"),
    "NO_REFERENCE": ("grey", "○", "no reference"),
    "NOT_COMPARED": ("grey", "–", "not compared"),
    "EVIDENCE_INCOMPLETE": ("bad", "⊘", "evidence incomplete"),
    "BRIDGED": ("blue", "⇄", "bridged"),
    "NOT_PORTED": ("grey", "∅", "not ported"),
}
STATE_STYLE = {"PASS_EXACT": ("ok", "✓"), "PASS_WITHIN_TOLERANCE": ("ok", "≃"), "FAIL": ("bad", "✗"),
               "EVALUATOR_ERROR": ("bad", "⚠"), "BLOCKED": ("grey", "■"), "NEEDS_DECISION": ("purple", "!"),
               "NEEDS_REVIEW": ("amber", "?"), "NOT_COMPARED": ("grey", "–")}


# ------------------------------------------------------------------------------------------------------------------
# Formatting and phrases
# ------------------------------------------------------------------------------------------------------------------
def fmt(v) -> str:
    """One number formatter: 6 significant figures."""
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return "non-finite"
        if v == 0:
            return "0"
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(fmt(x) for x in v) + "]"
    return str(v)


class _Missing(dict):
    def __missing__(self, key):
        return _Q()


class _Q:
    def __format__(self, spec):
        return "?"

    def __getitem__(self, k):
        return self

    def __str__(self):
        return "?"


def _fmt_tree(v):
    if isinstance(v, dict):
        return {k: _fmt_tree(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_fmt_tree(x) for x in v]
    return fmt(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v


class Phrases:
    def __init__(self, path: Path):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        self.table = {str(k): str(v) for k, v in (doc.get("phrases") or {}).items()}
        self.sha256 = ac.sha256_file(path)
        self.missing: set[str] = set()

    def say(self, code: str | None, check: dict | None = None) -> str:
        if not code:
            return ""
        tpl = self.table.get(code)
        if tpl is None:
            self.missing.add(code)
            return code
        ctx = {}
        check = check or {}
        for k, v in check.items():
            if k not in ("diagnostics", "samples", "legacy", "new", "table"):
                ctx[k] = v
        for k, v in (check.get("diagnostics") or {}).items():
            ctx[k] = v
        try:
            return tpl.format_map(_Missing(_fmt_tree(ctx)))
        except (ValueError, IndexError, KeyError, AttributeError):
            return tpl


# ------------------------------------------------------------------------------------------------------------------
# Graph SVG (static, layered top-down by topological rank; 07 D7-13)
# ------------------------------------------------------------------------------------------------------------------
def graph_svg(graph: dict, lad: dict) -> str:
    steps = {s["id"]: s for s in graph.get("steps") or []}
    datasets = {d["id"]: d for d in graph.get("datasets") or []}
    order = ladder.topo_order(steps, datasets)
    rank = {}
    for sid in order:
        r = 0
        for a in steps[sid].get("inputs") or []:
            p = ladder.producer_of(datasets[a], steps) if a in datasets else None
            if p in rank:
                r = max(r, rank[p] + 1)
        rank[sid] = r
    rows: dict[int, list[str]] = {}
    for sid in order:
        rows.setdefault(rank[sid], []).append(sid)
    nw, nh, gx, gy = 230, 54, 40, 70
    width = max(len(v) for v in rows.values()) * (nw + gx) + gx if rows else 300
    height = len(rows) * (nh + gy) + gy if rows else 100
    pos = {}
    for r, ids in rows.items():
        total = len(ids) * (nw + gx) - gx
        x0 = (width - total) / 2
        for i, sid in enumerate(sorted(ids)):
            pos[sid] = (x0 + i * (nw + gx), gy / 2 + r * (nh + gy))
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {int(width)} {int(height)}" '
           f'width="{int(width)}" height="{int(height)}" role="img" aria-label="Workflow graph" class="wfgraph">',
           '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="edge-head"/></marker></defs>']
    edges = []
    for sid in order:
        for d in sorted(steps[sid].get("inputs") or []):
            p = ladder.producer_of(datasets[d], steps) if d in datasets else None
            if p in pos and sid in pos:
                edges.append((p, sid, d))
    grouped: dict[tuple, list[str]] = {}
    for a, b, d in edges:
        grouped.setdefault((a, b), []).append(d)
    for (a, b), ds in sorted(grouped.items()):
        x1, y1 = pos[a][0] + nw / 2, pos[a][1] + nh
        x2, y2 = pos[b][0] + nw / 2, pos[b][1]
        out.append(f'<path class="edge" d="M{x1:.1f},{y1:.1f} C{x1:.1f},{(y1 + y2) / 2:.1f} {x2:.1f},'
                   f'{(y1 + y2) / 2:.1f} {x2:.1f},{y2 - 2:.1f}" marker-end="url(#arr)"/>')
        label = ", ".join(x.replace("ds.", "") for x in sorted(ds))
        out.append(f'<text class="edge-label" x="{(x1 + x2) / 2 + 6:.1f}" y="{(y1 + y2) / 2:.1f}">'
                   f'{html.escape(label)}</text>')
    for sid in order:
        if sid not in pos:
            continue
        x, y = pos[sid]
        e = lad["steps"].get(sid)
        disp = e["disposition"] if e else ("OUT_OF_SCOPE" if (steps[sid].get("scope") == "drop") else "—")
        cls, glyph, label = DISP_STYLE.get(disp, ("grey", "·", "out of scope" if disp == "OUT_OF_SCOPE" else disp))
        rung = (e or {}).get("rung")
        out.append(f'<a href="#step-{html.escape(sid)}"><g class="node {cls}">'
                   f'<rect x="{x:.1f}" y="{y:.1f}" width="{nw}" height="{nh}" rx="8"/>'
                   f'<text class="node-id" x="{x + 12:.1f}" y="{y + 22:.1f}">{glyph} {html.escape(sid)}</text>'
                   f'<text class="node-sub" x="{x + 12:.1f}" y="{y + 41:.1f}">{html.escape(label)}'
                   f'{" · " + html.escape(rung) if rung else ""}</text></g></a>')
    out.append("</svg>")
    return "\n".join(out)


# ------------------------------------------------------------------------------------------------------------------
# Context assembly
# ------------------------------------------------------------------------------------------------------------------
def _load(p: Path):
    try:
        return ac.load_json(p)
    except (OSError, json.JSONDecodeError):
        return None


def key_metric(cmp: dict) -> str:
    for c in ladder.failing_checks(cmp):
        if c.get("value") is not None:
            return f"{c.get('metric') or c.get('id')} = {fmt(c.get('value'))}"
        diag = c.get("diagnostics") or {}
        if "shift_px" in diag:
            return f"shift_px {fmt(diag['shift_px'])}"
    wt = cmp.get("within_tolerance") or []
    if wt:
        return f"{len(wt)} difference(s) within tolerance"
    return "exact" if ladder.declared_state(cmp) == "PASS_EXACT" else "—"


def build_context(migration: Path, lad: dict, phrases: Phrases) -> dict:
    project = migration.parent
    run_id = lad["run_id"]
    rd = migration / "runs" / run_id
    verify = rd / "verify"
    chain = ladder.evidence_chain(verify)
    ev = _load(chain[-1]) if chain else {}
    evs = [_load(p) or {} for p in chain]
    snap = rd / "snapshot"
    graph = _load(snap / "graph.json") or {"steps": [], "datasets": []}
    steps = {s["id"]: s for s in graph.get("steps") or []}
    gds = {d["id"]: d for d in graph.get("datasets") or []}
    objs = ev.get("objects") or []
    by_kind: dict[str, list] = {}
    for o in objs:
        by_kind.setdefault(o["kind"], []).append(o)
    integrity = lad["integrity"]
    bad_paths = {p.get("path") for p in integrity if p.get("path")}
    records = ev.get("records") or {}

    # records re-parse (07 §4.3: re-verify on render; YAML is parsed here, the stdlib ladder trusts the sealed parse)
    try:
        fresh = sealmod.parse_records(snap)
        for k in ("params", "tolerances"):
            if json.dumps(fresh.get(k), sort_keys=True) != json.dumps(records.get(k), sort_keys=True):
                integrity.append({"code": "RECORDS_PARSE_MISMATCH", "detail": f"records.{k} ≠ parse of snapshot",
                                  "severity": "incomplete"})
        for k in ("known_differences", "decisions"):
            sealed_k = [r for r in records.get(k) or [] if r.get("in_contract", True) and not r.get("amended")]
            if json.dumps(fresh.get(k), sort_keys=True) != json.dumps(sealed_k, sort_keys=True):
                integrity.append({"code": "RECORDS_PARSE_MISMATCH", "detail": f"records.{k} ≠ parse of snapshot",
                                  "severity": "incomplete"})
    except Exception as e:  # unreadable snapshot is itself a finding
        integrity.append({"code": "RECORDS_PARSE_MISMATCH", "detail": f"{type(e).__name__}: {e}"[:200],
                          "severity": "incomplete"})
    if any(p.get("severity") == "incomplete" for p in integrity) and not lad["pipeline"]["incomplete"]:
        lad["pipeline"].update({"incomplete": True, "rung": "none", "headline": "EVIDENCE INCOMPLETE"})
        for e in lad["steps"].values():
            if e["rung"] is not None:
                e["rung"] = "none"
        lad["status_line"] = ladder.status_line(lad)

    # compares, plots, visual
    cmp_docs = {}
    for o in by_kind.get("cmp", []):
        if o.get("path") not in bad_paths:
            cmp_docs[o.get("dataset")] = (_load(project / o["path"]) or {}, o)
    plots_by_ds: dict[str, list] = {}
    for o in sorted(by_kind.get("plot", []), key=lambda x: x.get("path") or ""):
        plots_by_ds.setdefault(o.get("dataset"), []).append(o)
    findings_by_ds: dict[str, list] = {}
    for o in by_kind.get("vis", []):
        doc = _load(project / o["path"]) or {}
        for f in doc.get("findings") or []:
            if f.get("finding_id") == o.get("finding_id"):
                findings_by_ds.setdefault(f.get("dataset_id"), []).append({**f, "amended": bool(
                    (o.get("meta") or {}).get("amended"))})
    for v in findings_by_ds.values():
        v.sort(key=lambda f: (str(f.get("finding_id"))))

    # datasets
    drows = []
    for dsid, d in sorted(lad["datasets"].items()):
        cmp, co = cmp_docs.get(dsid, ({}, None))
        g = gds.get(dsid) or {}
        fails = []
        for c in ladder.failing_checks(cmp):
            for code in ladder._norm_list(c.get("reason_codes")) or [c.get("state")]:
                fails.append({"check": c.get("id"), "code": code, "phrase": phrases.say(code, c)})
        level_codes = [c for c in d["reason_codes"] if c not in {f["code"] for f in fails}]
        checks = []
        for c in cmp.get("checks") or []:
            if not isinstance(c, dict):
                continue
            st = c.get("state")
            checks.append({"id": c.get("id"), "state": st, "cls": STATE_STYLE.get(st, ("grey", "·"))[0],
                           "glyph": STATE_STYLE.get(st, ("grey", "·"))[1], "metric": c.get("metric"),
                           "value": fmt(c.get("value")), "threshold": fmt(c.get("threshold")),
                           "unit": c.get("unit") or c.get("units") or "", "tolerance": c.get("tolerance_id") or
                           ("exact" if st in ac.PASS_STATES else ""), "codes": ladder._norm_list(c.get("reason_codes")),
                           "diagnostics": json.dumps(c.get("diagnostics"), sort_keys=True) if c.get("diagnostics")
                           else "", "n_samples": len(c.get("samples") or []), "gated_by": c.get("gated_by")})
        missing_here = [p for p in integrity if p.get("dataset") == dsid or
                        (co and p.get("object") == co["id"])]
        drows.append({
            "id": dsid, "step": d["step"], "kind": d["kind"], "role": d["role"], "state": d["state"],
            "declared_state": d.get("declared_state"), "disposition": d["disposition"],
            "disp_style": DISP_STYLE.get(d["disposition"], ("grey", "·", d["disposition"])),
            "state_style": STATE_STYLE.get(d["state"], ("grey", "·")),
            "reason_codes": d["reason_codes"], "fails": fails,
            "level_phrases": [{"code": c, "phrase": phrases.say(c)} for c in level_codes],
            "legacy_path": d["legacy_path"], "new_path": (g.get("new") or {}).get("path"),
            "metric": key_metric(cmp) if cmp else "—", "tolerances": sorted({c.get("tolerance_id") for c in
                                                                           cmp.get("checks") or []
                                                                           if isinstance(c, dict) and
                                                                           c.get("tolerance_id")}),
            "within_tolerance": cmp.get("within_tolerance") or [], "not_checked": cmp.get("not_checked") or [],
            "checks": checks, "compare_asset": f"assets/compare/{dsid}.json" if co else None,
            "plots": [{"path": p["path"], "asset": f"assets/plots/{dsid}/{Path(p['path']).name}",
                       "name": Path(p["path"]).name, "sha256": p.get("sha256"),
                       "ok": p.get("path") not in bad_paths} for p in plots_by_ds.get(dsid, [])],
            "visual": d.get("visual") or {}, "findings": findings_by_ds.get(dsid, []), "kd": d.get("kd") or [],
            "notes": d.get("notes") or [], "integrity": missing_here,
            "units": g.get("units") or ""})
    sev = {s: i for i, s in enumerate(ladder.DISPOSITION_ORDER + ["NO_REFERENCE", "BRIDGED", "NOT_PORTED"])}
    order = lad["pipeline"].get("order") or sorted(lad["steps"])
    pos = {s: i for i, s in enumerate(order)}
    parity = sorted(drows, key=lambda r: (sev.get(r["disposition"], 99), pos.get(r["step"], 999), r["id"]))

    # steps
    srows = []
    for sid in order:
        e = lad["steps"][sid]
        s = steps.get(sid, {})
        mine = [r for r in drows if r["step"] == sid]
        first = None
        for r in mine:
            if r["fails"]:
                first = r["fails"][0]
                break
            if r["level_phrases"] and r["disposition"] != "VERIFIED":
                first = {"code": r["level_phrases"][0]["code"], "phrase": r["level_phrases"][0]["phrase"]}
                break
        if first is None and e["caps"]:
            c = e["caps"][0]
            first = {"code": c["code"], "phrase": phrases.say(c["code"])}
        srows.append({"id": sid, "title": s.get("title") or "", "kind": s.get("kind"), "scope": e.get("scope"),
                      "rung": e["rung"], "disposition": e["disposition"],
                      "style": DISP_STYLE.get(e["disposition"], ("grey", "·", e["disposition"])),
                      "caps": [{**c, "phrase": phrases.say(c["code"])} for c in e["caps"]],
                      "annotations": e["annotations"], "datasets": mine, "first": first,
                      "module": e.get("module"), "legacy_source": (s.get("legacy") or {}).get("source"),
                      "params": sorted(s.get("params") or [])})
    failing_steps = [r for r in srows if r["disposition"] != "VERIFIED"]
    passing_steps = [r for r in srows if r["disposition"] == "VERIFIED"]
    dropped = sorted(sid for sid, s in steps.items() if ladder.step_class(s) == "drop")

    # failures list: every non-VERIFIED step + pipeline-level blockers that are not step dispositions
    failures = [{"anchor": f"step-{r['id']}", "id": r["id"], "disposition": r["disposition"], "style": r["style"],
                 "code": (r["first"] or {}).get("code"), "phrase": (r["first"] or {}).get("phrase")}
                for r in failing_steps]
    step_codes = set(ac.DISPOSITIONS)
    pipe_items = []
    for b in lad["pipeline"]["blockers"]:
        if b.get("step") or b.get("code") in step_codes or b.get("target"):
            continue  # steps are listed above; target reasons live in the target panel
        pipe_items.append({"code": b.get("code"), "phrase": phrases.say(b.get("code")),
                           "detail": b.get("detail") or b.get("dataset") or ""})

    # params / decisions / KDs
    params = records.get("params") or []
    groups: dict[str, list] = {}
    for p in params:
        src = p.get("source") or "unknown"
        key = "assumption" if src == "assumption" or p.get("status") == "assumed" else (
            "fitted" if src == "fitted" else ("answer" if src == "answer" else (
                "inferred" if p.get("resolution") == "inferred" else "evidence")))
        groups.setdefault(key, []).append({**p, "value_fmt": fmt(p.get("value")) if not isinstance(
            p.get("value"), (dict, list)) else json.dumps(p.get("value"), sort_keys=True)})
    group_order = [g for g in ("assumption", "fitted", "inferred", "answer", "evidence") if g in groups]
    decisions = sorted(records.get("decisions") or [], key=lambda d: str(d.get("id")))
    live_kd_txt = (migration / "known-differences.md").read_text(encoding="utf-8") \
        if (migration / "known-differences.md").is_file() else ""
    sealed_kd_ids = {k.get("entry") for k in records.get("known_differences") or []}
    kd_eval = {}
    for r in drows:
        for n in r["kd"]:
            kd_eval.setdefault(n["kd"], []).append({**n, "dataset": r["id"]})
    kds = []
    for k in sorted(records.get("known_differences") or [], key=lambda x: str(x.get("id"))):
        kds.append({**k, "evaluations": kd_eval.get(k.get("id"), []),
                    "after_run": not k.get("in_contract", True),
                    "human": str(k.get("owner") or "").startswith("human:")})
    for k in sealmod.parse_kds(live_kd_txt, in_contract=False):
        if k["entry"] not in sealed_kd_ids:
            kds.append({**k, "evaluations": [], "after_run": True, "unsealed": True,
                        "human": str(k.get("owner") or "").startswith("human:")})
    tolerances = records.get("tolerances") or []

    # not checked
    sweep = []
    inv = _load(snap / "inventory.json") or {}
    named = {ladder.ds_legacy_path(d) for d in gds.values()}
    for f in inv.get("files") or []:
        pth = f.get("path") or ""
        if any(pth.startswith(x.rstrip("/") + "/") for x in graph.get("legacy_output_dirs") or []) and \
                pth not in named and not ac.is_sidecar(pth):
            sweep.append(pth)
    not_checked = {
        "no_reference": [r for r in drows if r["disposition"] == "NO_REFERENCE"],
        "not_compared": [r for r in drows if r["disposition"] == "NOT_COMPARED"],
        "bridged": [r for r in drows if r["disposition"] == "BRIDGED"],
        "compare_not_checked": [(r["id"], x) for r in drows for x in r["not_checked"]],
        "cannot_tell": [(r["id"], f.get("question_id"), f.get("plot")) for r in drows for f in r["findings"]
                        if f.get("answer") == "cannot_tell"],
        "sweep": sorted(sweep), "dropped": dropped,
        "histogram": lad["pipeline"]["counts"]["histogram"]}

    # run history (failing runs kept)
    hist_rows = []
    seen = {}
    for rec in ladder.load_index(migration):
        rid = rec.get("run_id")
        if not rid:
            continue
        seen.setdefault(rid, {"run_id": rid, "sealed": False, "lines": []})
        seen[rid]["lines"].append(rec)
        if rec.get("evidence_id"):
            seen[rid]["sealed"] = True
            seen[rid]["evidence_id"] = rec.get("evidence_id")
        for k in ("chain", "executor", "status", "exit_code", "started_at"):
            if rec.get(k) is not None:
                seen[rid][k] = rec.get(k)
    for rid, h in seen.items():
        row = {k: h.get(k) for k in ("run_id", "chain", "executor", "status", "exit_code", "started_at", "sealed",
                                     "evidence_id")}
        row["amendments"] = sum(1 for x in h["lines"] if x.get("amendment"))
        row["current"] = rid == run_id
        if h["sealed"]:
            r2 = lad if rid == run_id else ladder.compute(migration, rid)
            row["rung"] = "none (evidence incomplete)" if r2["pipeline"]["incomplete"] else r2["pipeline"]["rung"]
            row["fails"] = r2["pipeline"]["counts"]["fail"]
            row["stale"] = any(p.get("severity") == "stale" for p in r2["integrity"])
            row["link"] = f"../runs/{rid}/verify/evidence.json"
        else:
            row["rung"] = "not sealed"
            row["fails"] = None
        hist_rows.append(row)

    # display layers
    disp = None
    for o in by_kind.get("disp", []):
        if (o.get("name") or Path(o["path"]).name) == "display.json":
            disp = _load(project / o["path"])
    layers = []
    for e in (disp or {}).get("datasets") or []:
        row = next((r for r in drows if r["id"] == e["dataset"]), None)
        layers.append({**e, "state": row["state"] if row else None,
                       "disposition": row["disposition"] if row else None})
    default_layer = None
    for pref in (lambda l: l.get("disposition") not in ("VERIFIED", None),
                 lambda l: l.get("family") == "raster" and (gds.get(l["dataset"]) or {}).get("role") == "final",
                 lambda l: True):
        cand = [l for l in layers if pref(l)]
        if cand:
            cand.sort(key=lambda l: (pos.get((lad["datasets"].get(l["dataset"]) or {}).get("step"), 999),
                                     l["dataset"]))
            default_layer = cand[0]["dataset"]
            break

    # target
    tgt = lad["pipeline"]["target"]
    tm = tgt.get("manifest") or {}
    claim = tm.get("claim") or tm.get("claim_text")
    target = {"state": tgt["state"], "claim": claim, "checks": tgt.get("checks") or [],
              "reasons": [{**r, "phrase": phrases.say(r.get("code"))} for r in tgt["reasons"]],
              "deployment_status": tm.get("deployment_status"), "runtime": tm.get("runtime") or tm.get("runtime_id"),
              "env_diff": tm.get("env_diff") or next((c.get("evidence") for c in tgt.get("checks") or []
                                                      if str(c.get("id")) == "T6"), None)}

    # caveats
    n_assum = len(groups.get("assumption", []))
    n_inf = len(groups.get("inferred", [])) + len(groups.get("fitted", []))
    caveats = {"assumptions": n_assum, "inferred": n_inf, "bridged": lad["pipeline"]["bridged"],
               "accepted": sum(1 for r in srows if r["disposition"] == "ACCEPTED_DIFFERENCE"),
               "not_checked": len(not_checked["no_reference"]) + len(not_checked["not_compared"])
               + len(not_checked["sweep"]) + len(not_checked["compare_not_checked"]),
               "signoff": (migration / "SIGN-OFF.md").is_file(),
               "unsigned_tolerances": sum(1 for t in tolerances if not t.get("signed"))}

    run = ev.get("run") or {}
    envrt = next((o.get("value") for o in by_kind.get("envrt", [])), {}) or {}
    narrative = (migration / "narrative.md").read_text(encoding="utf-8") if (migration / "narrative.md").is_file() \
        else None
    p = lad["pipeline"]
    current_idx = LADDER_DISPLAY.index(p["rung"]) if p["rung"] in LADDER_DISPLAY else -1
    ladder_graphic = [{"rung": r, "reached": (not p["incomplete"]) and i <= current_idx,
                       "current": (not p["incomplete"]) and i == current_idx} for i, r in enumerate(LADDER_DISPLAY)]
    hist = p["counts"]["histogram"]
    return {
        "project": (ev.get("project") or {}).get("name") or graph.get("project", {}).get("name"),
        "run_id": run_id, "run": run, "evidence_id": lad["evidence_id"], "lad": lad, "pipeline": p,
        "status_line": lad["status_line"], "ladder_graphic": ladder_graphic, "greyed": p["greyed"],
        "histogram": [(s, hist.get(s, 0), STATE_STYLE[s][0]) for s in ac.VERDICTS],
        "integrity": integrity, "incomplete": p["incomplete"],
        "stale": [x for x in integrity if x.get("severity") == "stale"],
        "failures": failures, "pipe_items": pipe_items, "caveats": caveats,
        "graph_svg": graph_svg(graph, lad), "layers": layers, "default_layer": default_layer,
        "caption_default": CAPTION.format(crs=next((l["crs"] for l in layers if l["dataset"] == default_layer),
                                                   "native CRS")),
        "failing_steps": failing_steps, "passing_steps": passing_steps, "parity": parity,
        "param_groups": [(g, groups[g]) for g in group_order], "decisions": decisions, "kds": kds,
        "tolerances": tolerances, "not_checked": not_checked, "history": hist_rows, "narrative": narrative,
        "target": target, "envrt": envrt, "sealed": ev.get("sealed") or {},
        "inputs": sorted(by_kind.get("in", []), key=lambda o: o.get("path") or ""),
        "refs": sorted(by_kind.get("ref", []), key=lambda o: o.get("path") or ""),
        "comparators": sorted({((o.get("meta") or {}).get("comparator"), (o.get("meta") or {}).get(
            "comparator_version"), (o.get("meta") or {}).get("comparator_sha256")) for o in by_kind.get("cmp", [])},
            key=lambda t: tuple(str(x) for x in t)),
        "chain": [{"file": pth.name, "id": ac.object_id("ev", e) if e else None,
                   "parent": e.get("parent_evidence_id"),
                   "added": sorted({o["kind"] for o in e.get("objects") or []} - {o["kind"] for o in
                                                                                   (evs[i - 1] if i else {}).get(
                                                                                       "objects") or []})
                   if i else []} for i, (pth, e) in enumerate(zip(chain, evs))],
        "versions": {"report": REPORT_VERSION, "ladder": ladder.LADDER_VERSION, "phrases": phrases.sha256[:12],
                     "plugin": ac.PLUGIN_VERSION},
        "limit_note": LIMIT_NOTE, "migration_rel": "migration",
        "_copy": {"plots": [(project / pl["path"], f"plots/{r['id']}/{Path(pl['path']).name}") for r in drows
                            for pl in r["plots"] if pl["ok"]],
                  "compare": [(project / co["path"], f"compare/{dsid}.json") for dsid, (c, co) in cmp_docs.items()],
                  "display": [(project / o["path"], f"display/{Path(o['path']).name}") for o in by_kind.get("disp", [])
                              if o.get("path") not in bad_paths]},
    }


# ------------------------------------------------------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------------------------------------------------------
def _data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def layers_js(ctx: dict, assets: Path) -> str:
    out = {"default": ctx["default_layer"], "caption": CAPTION, "layers": []}
    for l in ctx["layers"]:
        e = {"dataset": l["dataset"], "family": l["family"], "crs": l.get("crs"), "state": l.get("state"),
             "disposition": l.get("disposition"), "scale": l.get("scale"), "diff_scale": l.get("diff_scale"),
             "bounds_3857": l.get("bounds_3857"), "native_cell_px": l.get("native_cell_px"),
             "resampling": l.get("resampling")}
        files = l.get("files") or {}
        if l["family"] == "raster":
            e["corners"] = l["corners_lonlat"]
            for side in ("legacy", "new", "diff"):
                p = assets / "display" / files.get(side, "")
                e[side] = _data_uri(p, "image/png") if p.is_file() else None
            e["triptych"] = f"assets/display/{files.get('triptych')}" if files.get("triptych") else None
        else:
            e["bounds_4326"] = l.get("bounds_4326")
            for side in ("legacy", "new"):
                p = assets / "display" / files.get(side, "")
                e[side] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None
        out["layers"].append(e)
    return "window.AXIS_LAYERS = " + json.dumps(out, sort_keys=True, separators=(",", ":"), allow_nan=False) + ";\n"


def render(ctx: dict) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")),
                             autoescape=jinja2.select_autoescape(["html", "j2"], default=True),
                             trim_blocks=True, lstrip_blocks=True)
    env.filters["fmt"] = fmt
    env.filters["short"] = lambda s, n=12: (str(s).split(":", 1)[-1][:n] if s else "—")
    env.filters["anchor"] = lambda s: re.sub(r"[^A-Za-z0-9_.-]", "-", str(s))
    env.globals["DISP_STYLE"] = DISP_STYLE
    return env.get_template("report.html.j2").render(**ctx)


def no_run_page(migration: Path) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")), autoescape=True)
    return env.get_template("norun.html.j2").render(migration=str(migration.name))


def _write_tree(migration: Path, files: dict[str, bytes]):
    """Atomic replace of migration/report/: build in a temp folder, then swap."""
    final = migration / "report"
    tmp = Path(tempfile.mkdtemp(prefix=".report.tmp-", dir=str(migration)))
    for rel, data in sorted(files.items()):
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    os.chmod(tmp, 0o755)
    old = None
    if final.exists():
        old = migration / (".report.old-" + Path(tmp).name.split("-")[-1])
        os.replace(final, old)
    os.replace(tmp, final)
    if old:
        shutil.rmtree(old, ignore_errors=True)


def build(migration: Path, run_id: str | None = None) -> tuple[int, dict]:
    phrases = Phrases(HERE / "phrases.v1.yaml")
    lad = ladder.compute(migration, run_id)
    if lad["run_id"] is None or (run_id is None and not ladder.newest_sealed_run(migration)):
        page = no_run_page(migration)
        rep = {"schema": "axis-migrate.report.v1", "status": "none", "rung": "none", "headline": "NO SEALED RUN",
               "steps": {}, "assumptions": [], "known_differences": [], "evidence_id": None,
               "status_line": lad["status_line"]}
        files = {"index.html": page.encode()}
        rep["files"] = {k: ac.sha256_bytes(v) for k, v in sorted(files.items())}
        files["REPORT.json"] = (json.dumps(rep, sort_keys=True, indent=1) + "\n").encode()
        _write_tree(migration, files)
        return 3, rep
    ctx = build_context(migration, lad, phrases)
    files: dict[str, bytes] = {}
    # copy hash-checked assets (bad paths were excluded in build_context)
    for group in ("plots", "compare", "display"):
        for src, rel in ctx["_copy"][group]:
            if src.is_file():
                files[f"assets/{rel}"] = src.read_bytes()
    stage = Path(tempfile.mkdtemp(prefix="axis-report-"))
    try:
        for rel, data in files.items():
            if rel.startswith("assets/display/"):
                (stage / "display").mkdir(parents=True, exist_ok=True)
                (stage / "display" / Path(rel).name).write_bytes(data)
        files["assets/layers.js"] = layers_js(ctx, stage).encode()
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    vendor = HERE / "assets" / "vendor"
    for p in sorted(vendor.iterdir()):
        files[f"assets/vendor/{p.name}"] = p.read_bytes()
    files["assets/swipe.js"] = (HERE / "assets" / "swipe.js").read_bytes()
    ctx["phrase_missing"] = sorted(phrases.missing)
    page = render(ctx)
    ctx["phrase_missing"] = sorted(phrases.missing)  # second pass catches codes first seen while rendering
    page = render(ctx)
    files["index.html"] = page.encode("utf-8")
    p = ctx["pipeline"]
    status = "EVIDENCE_INCOMPLETE" if p["incomplete"] else ("NOTHING_VERIFIED" if p["headline"] == "NOTHING VERIFIED"
                                                            else p["rung"])
    assumptions = [{"id": x.get("id"), "node": x.get("node"), "source": x.get("source"), "status": x.get("status"),
                    "value": x.get("value"), "reason": x.get("reason")}
                   for g, items in ctx["param_groups"] if g in ("assumption", "fitted", "inferred") for x in items]
    rep = {"schema": "axis-migrate.report.v1", "status": status, "rung": None if p["incomplete"] else p["rung"],
           "suffix": p["suffix"], "headline": p["headline"], "status_line": ctx["status_line"],
           "run_id": ctx["run_id"], "evidence_id": ctx["evidence_id"],
           "steps": {sid: e["disposition"] for sid, e in sorted(ctx["lad"]["steps"].items())},
           "step_rungs": {sid: e["rung"] for sid, e in sorted(ctx["lad"]["steps"].items())},
           "datasets": {d: {"state": r["state"], "disposition": r["disposition"]}
                        for d, r in sorted(ctx["lad"]["datasets"].items())},
           "counts": p["counts"], "assumptions": assumptions,
           "known_differences": [{"id": k.get("id"), "kind": k.get("kind"), "owner": k.get("owner"),
                                  "status": k.get("status"), "datasets": k.get("datasets"),
                                  "after_run": k.get("after_run")} for k in ctx["kds"]],
           "integrity": [{k: v for k, v in x.items() if k in ("code", "severity", "path", "dataset", "detail")}
                         for x in ctx["integrity"]],
           "target": {"state": ctx["target"]["state"], "deployment_status": ctx["target"]["deployment_status"]},
           "phrase_missing": ctx["phrase_missing"], "versions": ctx["versions"]}
    # secret scan of what we are about to publish (data URIs and vendored files excluded)
    hits = []
    for rel, data in sorted(files.items()):
        if rel.startswith("assets/vendor/") or not rel.endswith((".html", ".js", ".json", ".geojson")):
            continue
        names = ladder.secret_scan_text(data.decode("utf-8", errors="replace"))
        if names:
            hits.append({"path": rel, "patterns": names})
    if hits:
        page = ("<!doctype html><meta charset='utf-8'><title>Report withheld</title><h1>Report withheld</h1>"
                "<p>The secret scan matched: " + html.escape(", ".join(h["path"] for h in hits)) + "</p>")
        files = {"index.html": page.encode()}
        rep = {"schema": "axis-migrate.report.v1", "status": "WITHHELD_SECRET", "rung": None, "secret_scan": hits,
               "run_id": ctx["run_id"], "evidence_id": ctx["evidence_id"], "steps": {}, "assumptions": [],
               "known_differences": []}
    rep["files"] = {k: ac.sha256_bytes(v) for k, v in sorted(files.items())}
    files["REPORT.json"] = (json.dumps(rep, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False)
                            + "\n").encode("utf-8")
    _write_tree(migration, files)
    stale = bool(ctx["stale"])
    code = 2 if (hits or p["incomplete"] or stale or p["headline"] == "NOTHING VERIFIED") else 0
    return code, rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render migration/report/ from one sealed evidence bundle.")
    ap.add_argument("migration")
    ap.add_argument("--run", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    migration = Path(a.migration).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    code, rep = build(migration, a.run)
    if a.json:
        print(json.dumps({k: v for k, v in rep.items() if k != "files"}, sort_keys=True, indent=1))
    else:
        print(rep.get("status_line") or rep.get("status"))
        print(f"report: {migration / 'report' / 'index.html'} (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
