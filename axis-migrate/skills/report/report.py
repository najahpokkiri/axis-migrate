#!/usr/bin/env python3
"""report.py — render migration/report/ from ONE sealed evidence bundle (07 §4, DESIGN §4.7, CONTRACTS §9).

    axpy report.py <migration> [--run <run_id>] [--json]
    axpy report.py <migration> --check [--json]        the report still matches the sealed bundle (exit 2 if not)

Every render re-verifies every hash and re-derives every compare state (through ladder.compute) and re-parses the
sealed YAML records. Verdict wording comes only from phrases.v1.yaml. Deterministic: no clock, sorted iteration,
one number formatter; the only times shown are the run's own started_at / ended_at.

Writes migration/report/:
  REPORT.md     the native summary for the file pane (status line first)
  index.html    one self-contained page (inline CSS/JS/SVG, thumbnails as data URIs; no network request of any kind)
  parity.xlsx   values only: Status, Parity, Decisions, Parameters, Sign-off, Runs
  assets/       hash-checked copies of the sealed plots and compare results (linked from REPORT.md)
  REPORT.json   {status, rung, steps, …, thumbnails{key: source, source_sha256, sha256}, files{path: sha256}}
Exit: 0 complete · 2 evidence incomplete, stale or nothing verified · 3 no sealed run.
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
import graphview  # noqa: E402
import ladder  # noqa: E402
import reportparts as rp  # noqa: E402
import seal as sealmod  # noqa: E402
import visual_receipt as vrc  # noqa: E402

import jinja2  # noqa: E402
import markupsafe  # noqa: E402
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
    if isinstance(v, dict):
        return " · ".join(f"{k} {fmt(x)}" for k, x in sorted(v.items()) if x is not None) or "—"
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
        self.rungs = {str(k): str(v) for k, v in (doc.get("rungs") or {}).items()}
        self.headlines = {str(k): str(v) for k, v in (doc.get("headlines") or {}).items()}
        self.next_rung = {str(k): str(v) for k, v in (doc.get("next_rung") or {}).items()}
        self.glossary = [(str(k), str(v)) for k, v in (doc.get("glossary") or {}).items()]
        self.no_ref = {str(k): str(v) for k, v in (doc.get("no_reference_reasons") or {}).items()}
        self.sha256 = ac.sha256_file(path)
        self.missing: set[str] = set()

    def no_reference(self, reason: str | None) -> str:
        """NO_REFERENCE with the graph's no_reference_reason in plain words (a reason, not a reason code: an unknown
        one is shown as written and is not PHRASE_MISSING)."""
        base = self.say("NO_REFERENCE")
        if not reason or reason == "NO_REFERENCE":
            return base
        return f"{base} ({self.no_ref.get(reason, reason)})"

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


# ------------------------------------------------------------------------------------------------------------------
# GeoLibre map views (skills/map/views.py): listed, hash-checked and explained; never embedded, never fetched
# ------------------------------------------------------------------------------------------------------------------
VIEW_ORDER = ("overview", "worst", "edges", "nodata", "validity")
VIEW_PURPOSE = {"overview": "legacy and new over the whole extent",
                "worst": "camera on the largest difference (rasters: diff layer on top)",
                "edges": "where cells valid on one side only concentrate",
                "nodata": "the validity layer alone over the whole extent",
                "validity": "layer: 1 legacy only (blue) · 2 new only (orange) · 3 both (green)"}
STATE_LABEL = {"ok": "matches", "changed": "changed since views.json", "missing": "missing",
               "reviewed_changed": "changed after the review"}


def _cell(v) -> str:
    """A cell value as views.json / the identify popup has it; None = nodata on that side."""
    return "nodata" if v is None else fmt(v)


def _view_rank(name: str) -> tuple:
    return (VIEW_ORDER.index(name), "") if name in VIEW_ORDER else (len(VIEW_ORDER), name)


def map_views(project: Path, rd: Path, run_id: str, by_kind: dict, findings_by_ds: dict, lad: dict) -> dict:
    """Per dataset: the views of verify/views/views.json (sealed with the run, read-only) and amend/views/views.json
    (written after the seal), each project's path relative to the run, its indexed sha256, whether the sealed bundle
    binds it and whether the bytes still match; plus the dataset's sealed receipts (PNG or view). Deterministic: no
    clock, sorted iteration, paths relative to the run. The server's port is never read or shown."""
    run_rel = rd.relative_to(project).as_posix() + "/"
    sealed = set()
    for o in by_kind.get("view", []):
        p = str(o.get("path") or "")
        if p.startswith(run_rel):
            sealed.add((p[len(run_rel):], ladder.hex_of(o.get("sha256"))))
    cache: dict[str, str | None] = {}

    def now(rel: str) -> str | None:
        if rel not in cache:
            parts = rel.split("/")
            safe = ("/".join(parts[:2]) in vrc.VIEW_ROOTS and len(parts) == 4 and "\\" not in rel
                    and not ({"..", ".", ""} & set(parts)) and rel.endswith((".geolibre.json", ".tif")))
            p = rd / rel
            cache[rel] = ac.sha256_file(p) if safe and p.is_file() else None
        return cache[rel]

    indexes, ignored = [], []
    for root in vrc.VIEW_ROOTS:
        if not (rd / root / "views.json").is_file():
            continue
        doc = vrc.load_index(rd, root)
        if doc and doc.get("schema") == vrc.VIEWS_SCHEMA and doc.get("run_id") == run_id:
            indexes.append((root, doc))
        else:
            ignored.append(f"{root}/views.json")
    names: dict[str, str] = {}
    for _, doc in indexes:
        for e in doc.get("datasets") or []:
            if isinstance(e, dict):
                for rel, info in vrc.entry_projects(e)[0].items():
                    names[rel] = info["name"]
    out: dict[str, dict] = {}
    for dsid, d in sorted(lad["datasets"].items()):
        if d.get("kind") not in ("raster", "vector"):
            continue
        counted = set((d.get("visual") or {}).get("counted") or [])
        mine = findings_by_ds.get(dsid, [])
        cited = {}
        for f in mine:
            if vrc.is_view(f):
                for c in [f.get("view")] + list(f.get("views_seen") or []):
                    if isinstance(c, dict) and isinstance(c.get("project"), str):
                        cited.setdefault(c["project"], set()).add(str(c.get("project_sha256")))
        tables = []
        skipped = []
        for root, doc in indexes:
            e = vrc.index_entry(doc, dsid)
            if e is None:
                skipped += [str(s.get("reason")) for s in doc.get("skipped") or []
                            if isinstance(s, dict) and s.get("dataset") == dsid]
                continue
            projects, cells = vrc.entry_projects(e)
            rows = []
            for rel, info in projects.items():
                cur = now(rel)
                state = "missing" if cur is None else ("ok" if cur == info["sha256"] else "changed")
                if state == "changed" and info["sha256"] in cited.get(rel, ()):
                    state = "reviewed_changed"
                purpose = VIEW_PURPOSE.get(info["name"], "")
                if info["name"].startswith("cell-"):
                    c = cells.get(info["name"][5:]) or {}
                    purpose = (f"centred on the {info['name'][5:]} cell to identify: views.json legacy "
                               f"{_cell(c.get('legacy_value'))} · new {_cell(c.get('new_value'))}")
                rows.append({"name": info["name"], "kind": "project", "path": rel, "sha256": info["sha256"],
                             "sealed": (rel, info["sha256"]) in sealed, "state": state, "label": STATE_LABEL[state],
                             "cited": rel in cited, "purpose": purpose})
            vf = (e.get("files") or {}).get("validity") if e.get("family") == "raster" else None
            if isinstance(vf, dict) and isinstance(vf.get("file"), str):
                cur = now(vf["file"])
                state = "missing" if cur is None else ("ok" if cur == vf.get("sha256") else "changed")
                vc = e.get("validity_counts") or {}
                rows.append({"name": "validity", "kind": "layer", "path": vf["file"], "sha256": vf.get("sha256"),
                             "sealed": None, "state": state, "label": STATE_LABEL[state], "cited": False,
                             "purpose": VIEW_PURPOSE["validity"] + (
                                 f"; cells: {fmt(vc.get('1', 0))} legacy only, {fmt(vc.get('2', 0))} new only, "
                                 f"{fmt(vc.get('3', 0))} both" if isinstance(vc, dict) and vc else "")})
            rows.sort(key=lambda r: _view_rank(r["name"]))
            cell_ids = sorted(n[5:] for n in (r["name"] for r in rows) if n.startswith("cell-"))
            tables.append({"index": f"{root}/views.json", "after_seal": root.startswith("amend/"), "rows": rows,
                           "views": [r["name"] for r in rows if r["kind"] == "project"
                                     and not r["name"].startswith("cell-")],
                           "cells": cell_ids,
                           "changed": [r["name"] for r in rows if r["state"] != "ok"]})
        states = {r["path"]: r for t in tables for r in t["rows"]}
        receipts = []
        for f in mine:  # sorted by finding_id in build_context
            fid = str(f.get("finding_id"))
            view = vrc.is_view(f)
            seen = []
            for c in (f.get("views_seen") or []) if view else []:
                if not isinstance(c, dict):
                    continue
                rel = str(c.get("project"))
                st = states.get(rel)
                ok_sha = st is not None and st["sha256"] == c.get("project_sha256")
                seen.append({"name": names.get(rel) or Path(rel).name.replace(".geolibre.json", ""), "path": rel,
                             "sha256": c.get("project_sha256"),
                             "state": (st["state"] if ok_sha else "not_indexed"),
                             "label": st["label"] if ok_sha else "not in views.json with this sha256"})
            if f.get("answer") == "cannot_tell" and fid in counted:
                status, why = "cannot_tell", "not visually assessed (not a receipt)"
            elif fid in counted:
                status, why = "counts", ""
            elif view:
                bad = [s for s in seen if s["state"] != "ok"]
                status = "void"
                if any(s["state"] in ("reviewed_changed", "changed", "missing") for s in bad):
                    why = "does not count: a view it cites changed after the review (" + ", ".join(
                        s["name"] for s in bad) + ")"
                elif bad:
                    why = "does not count: it cites views not in this run's views.json (" + ", ".join(
                        s["name"] for s in bad) + ")"
                elif not all((s["path"], s["sha256"]) in sealed for s in seen):
                    why = "does not count: it cites views the sealed bundle does not bind"
                else:
                    why = "does not count: it fails the receipt rule (lib/visual_receipt.py)"
            else:
                status, why = "void", "does not count: its plot is not a sealed, unchanged plot of this run"
            seen.sort(key=lambda s: (_view_rank(s["name"]), s["path"]))
            receipts.append({
                "id": fid, "kind": "view" if view else "PNG", "amended": f.get("amended"),
                "question": f.get("question_id"), "answer": f.get("answer"), "effect": f.get("effect"),
                "category": f.get("category"), "engine": f.get("engine") if view else None,
                "main": (names.get(str((f.get("view") or {}).get("project"))) if view else None),
                "seen": seen, "plot": None if view else f.get("plot"),
                "plot_sha256": None if view else f.get("plot_sha256"),
                "plots_seen": len(f.get("plots_seen") or []) if not view else None,
                "cells": [{"cell": c.get("cell"), "legacy": _cell(c.get("legacy")), "new": _cell(c.get("new"))}
                          for c in f.get("cells_read") or [] if isinstance(c, dict)] if view else [],
                "observations": [{"what": o.get("what"), "value": fmt(o.get("value")), "unit": o.get("unit") or ""}
                                 for o in f.get("observations") or [] if isinstance(o, dict)],
                "layer": f.get("layer"),
                "bbox": fmt((f.get("location") or {}).get("bbox")) if isinstance(f.get("location"), dict) and
                (f.get("location") or {}).get("bbox") else None,
                "crs": (f.get("location") or {}).get("crs") if isinstance(f.get("location"), dict) else None,
                "note": f.get("note"), "status": status, "why": why, "looked": vrc.looked_line(f.get("looked")),
                "flag": f.get("answer") == "yes" and f.get("effect") == "flag"})
        n_view = sum(1 for r in receipts if r["kind"] == "view" and r["status"] == "counts")
        n_png = sum(1 for r in receipts if r["kind"] == "PNG" and r["status"] == "counts")
        n_void_view = sum(1 for r in receipts if r["kind"] == "view" and r["status"] == "void")
        out[dsid] = {"tables": tables, "skipped": skipped, "ignored": ignored, "has_index": bool(indexes),
                     "receipts": receipts, "n_view": n_view, "n_png": n_png, "n_void_view": n_void_view,
                     "anchor": "views-" + re.sub(r"[^A-Za-z0-9_.-]", "-", dsid)}
    return out


STANDING_INSTRUCTION = "agent (standing instruction)"


def live_graph(migration: Path, snapshot_graph: dict) -> dict:
    """graph.json as it is now (an answer given after the run counts), else the run snapshot."""
    return _load(migration / "graph.json") or snapshot_graph or {}


def owner_items(migration: Path, lad: dict, snapshot_graph: dict) -> tuple[list, dict]:
    """Open graph questions and a plan approved by standing instruction (B2, B3): they cap no rung, but the report
    must never say "none" while either holds. Taken from ladder.compute's pipeline.open_questions/plan_approval;
    a ladder without those keys gets the same rule applied here (live graph.json, else the run snapshot)."""
    pl = lad.get("pipeline") or {}
    oq, pa = pl.get("open_questions"), pl.get("plan_approval")
    if oq is None or pa is None:
        g = live_graph(migration, snapshot_graph)
        if oq is None:
            oq = [{"id": q.get("id"), "kind": q.get("kind"), "header": q.get("header"), "node": q.get("node")}
                  for q in g.get("questions") or [] if isinstance(q, dict) and q.get("status") == "open"]
        if pa is None:
            standing = sorted(str(s.get("id")) for s in g.get("steps") or [] if isinstance(s, dict)
                              and s.get("scope") == "migrate"
                              and (((s.get("plan") or {}).get("approved") or {}).get("by") or "") not in ("", "user"))
            pa = {"by": STANDING_INSTRUCTION if standing else None, "standing_steps": standing}
    return [q for q in oq or [] if isinstance(q, dict)], pa or {}


def _major(v) -> int | None:
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else None


def version_drift(inv: dict, graph: dict) -> list[dict]:
    """A migrate step's legacy model saved by one major version of its application (inventory projects[].saved_with
    or the step's legacy.saved_with: {app, version, evidence}) while the legacy reference outputs were produced on
    another (inventory or graph project reference_env: {app, version, source}). Read only; nothing is guessed: no
    note unless both versions are recorded."""
    ref = inv.get("reference_env") or (graph.get("project") or {}).get("reference_env") or {}
    if not isinstance(ref, dict) or _major(ref.get("version")) is None:
        return []
    saved_by_path = {p.get("path"): p.get("saved_with") for p in inv.get("projects") or []
                     if isinstance(p, dict) and isinstance(p.get("saved_with"), dict)}
    out = []
    for st in graph.get("steps") or []:
        if not isinstance(st, dict) or st.get("scope") != "migrate":
            continue
        leg = st.get("legacy") or {}
        sw = leg.get("saved_with") if isinstance(leg.get("saved_with"), dict) else saved_by_path.get(leg.get("source"))
        if not sw or _major(sw.get("version")) is None:
            continue
        app = str(sw.get("app") or ref.get("app") or "")
        if ref.get("app") and app.lower() != str(ref["app"]).lower():
            continue
        if _major(sw["version"]) != _major(ref["version"]):
            out.append({"step": st.get("id"), "model": leg.get("source"), "app": app,
                        "saved": str(sw["version"]), "saved_evidence": sw.get("evidence"),
                        "reference": str(ref["version"]), "reference_source": ref.get("source")})
    return sorted(out, key=lambda d: str(d["step"]))


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
    views = map_views(project, rd, run_id, by_kind, findings_by_ds, lad)

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
            "level_phrases": [{"code": "NO_REFERENCE", "phrase": phrases.no_reference(c)}
                              if d["disposition"] == "NO_REFERENCE" else {"code": c, "phrase": phrases.say(c)}
                              for c in level_codes],
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
            "notes": d.get("notes") or [], "integrity": missing_here, "views": views.get(dsid),
            "units": g.get("units") or ""})
    sev = {s: i for i, s in enumerate(ladder.DISPOSITION_ORDER + ["NO_REFERENCE", "BRIDGED", "NOT_PORTED"])}
    order = lad["pipeline"].get("order") or sorted(lad["steps"])
    pos = {s: i for i, s in enumerate(order)}
    parity = sorted(drows, key=lambda r: (sev.get(r["disposition"], 99), pos.get(r["step"], 999), r["id"]))

    # steps
    pmap = {p.get("id"): p for p in records.get("params") or []}
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
                      "params": [{"id": pid, "source": (pmap.get(pid) or {}).get("source") or "unknown",
                                  "status": (pmap.get(pid) or {}).get("status")}
                                 for pid in sorted(set(s.get("params") or []) |
                                                   {x for x, v in pmap.items() if v.get("node") == sid})]})
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
                           "detail": b.get("detail") or b.get("dataset") or "", "dataset": b.get("dataset")})

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
    named = {ladder.ds_reference_path(d, gds) for d in gds.values()} | {ladder.ds_legacy_path(d) for d in gds.values()}
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
        "cannot_tell": [(r["id"], f.get("question_id"), f.get("plot") or (f.get("view") or {}).get("project"))
                        for r in drows for f in r["findings"]
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
    # ---- redesign parts (reportparts.py): thumbnails, numbers, charts, decisions, next rung, timeline
    aux = {(o.get("name") or Path(o.get("path") or "").name, o.get("path")): o for o in by_kind.get("aux", [])}
    plot_meta: dict[str, dict] = {}
    manifest_doc = None
    for (name, pth), o in sorted(aux.items(), key=lambda kv: str(kv[0])):
        if pth in bad_paths or not pth:
            continue
        if name == "plots.json":
            doc = _load(project / pth)
            ds_dir = Path(pth).parent.name
            if isinstance(doc, dict) and ac.sha256_file(project / pth) == ladder.hex_of(o.get("sha256")):
                for png in doc.get("png") or []:
                    if isinstance(png, dict) and png.get("name"):
                        plot_meta.setdefault(doc.get("dataset_id") or ds_dir, {})[png["name"]] = png
        elif name == "manifest.json" and pth == f"migration/runs/{run_id}/manifest.json":
            if (project / pth).is_file() and ac.sha256_file(project / pth) == ladder.hex_of(o.get("sha256")):
                manifest_doc = _load(project / pth)

    def _read(rel):
        f = project / rel
        return f.read_bytes() if f.is_file() else None

    thumb_manifest: dict[str, dict] = {}
    disp_objs = by_kind.get("disp", [])
    for r in drows:
        cmp, co = cmp_docs.get(r["id"], ({}, None))
        objs = [{"path": o["path"], "sha256": o.get("sha256"), "id": o.get("id"), "name": Path(o["path"]).name}
                for o in plots_by_ds.get(r["id"], []) if o.get("path") not in bad_paths]
        if not objs:  # no verify plots: the display triptych (map.py display) is the picture
            objs = [{"path": o["path"], "sha256": o.get("sha256"), "id": o.get("id"), "name": Path(o["path"]).name}
                    for o in disp_objs if o.get("dataset") == r["id"] and o["path"].endswith(".triptych.png")
                    and o.get("path") not in bad_paths]
        th = rp.dataset_thumbs(r["id"], objs, plot_meta.get(r["id"], {}), _read, ac.sha256_bytes) if objs else \
            {"main": [], "more": [], "manifest": {}}
        thumb_manifest.update(th["manifest"])
        r.update({"thumbs": th["main"], "more_plots": th["more"], "numbers": rp.key_numbers(cmp, fmt),
                  "diff_svg": rp.diff_svg(cmp, fmt), "cmp_object": co.get("id") if co else None,
                  "group": rp.group_of(r["disposition"]), "anchor": rp.anchor(r["id"]),
                  "phrase": (r["fails"][0]["phrase"] if r["fails"] else
                             (r["level_phrases"][0]["phrase"] if r["level_phrases"] else "")),
                  "code": (r["fails"][0]["code"] if r["fails"] else
                           (r["level_phrases"][0]["code"] if r["level_phrases"] else None))})
    # what needs attention: dataset items first (failures and NOT_COMPARED, then decisions, then reviews), then
    # steps that are not VERIFIED for a step-level reason, then pipeline items
    att: dict[str, list] = {"broken": [], "decide": [], "review": [], "info": []}
    covered_steps = set()
    for r in parity:
        if r["disposition"] in ("VERIFIED", "NO_REFERENCE", "BRIDGED", None):
            continue
        att[r["group"]].append({"subject": r["id"], "step": r["step"], "disposition": r["disposition"],
                                "glyph": r["disp_style"][1], "cls": r["disp_style"][0], "code": r["code"],
                                "phrase": r["phrase"], "href": f"#ds-{r['anchor']}"})
        covered_steps.add(r["step"])
    for f in failures:
        if f["id"] in covered_steps and f["disposition"] not in ("NO_REFERENCE",):
            continue
        g = rp.group_of(f["disposition"])
        att[g].append({"subject": f["id"], "step": f["id"], "disposition": f["disposition"], "glyph": f["style"][1],
                       "cls": f["style"][0], "code": f["code"], "phrase": f["phrase"], "href": f"#{f['anchor']}"})
    for b in pipe_items:
        ds = b.get("dataset")
        detail = b["detail"] if b["detail"] and b["detail"] != ds else ""
        att["info"].append({"subject": ds or "pipeline", "step": None, "disposition": "pipeline", "glyph": "·",
                            "cls": "grey", "code": b["code"], "phrase": b["phrase"] + (f" ({detail})" if detail
                                                                                       else ""),
                            "href": f"#ds-{rp.anchor(ds)}" if ds else "#steps"})
    open_questions, plan_approval = owner_items(migration, lad, graph)
    for q in open_questions:
        att["decide"].append({"subject": str(q.get("id")), "step": q.get("node"), "disposition": "OPEN QUESTION",
                              "glyph": "?", "cls": "purple", "code": "OPEN_QUESTION",
                              "phrase": phrases.say("OPEN_QUESTION", {k: q.get(k) or "?" for k in
                                                                      ("id", "kind", "node", "header")}),
                              "href": "#graph"})
    if plan_approval.get("by") == STANDING_INSTRUCTION:
        st = plan_approval.get("standing_steps") or []
        att["decide"].append({"subject": "plan.md", "step": None, "disposition": "PLAN APPROVAL", "glyph": "!",
                              "cls": "purple", "code": "PLAN_APPROVED_BY_STANDING_INSTRUCTION",
                              "phrase": phrases.say("PLAN_APPROVED_BY_STANDING_INSTRUCTION",
                                                    {"steps": ", ".join(st) or "?"}), "href": "#steps"})
    attention = [{"key": k, "title": rp.GROUP_TITLE[k], "items": v} for k, v in att.items() if v]
    decision_cards = rp.decisions(parity, cmp_docs, lad, records, phrases, fmt) + \
        rp.question_cards(live_graph(migration, graph), phrases)
    param_rows = []
    for pp in sorted(params, key=lambda x: (str(x.get("node")), str(x.get("id")))):
        v = pp.get("value")
        param_rows.append({"id": pp.get("id"), "node": pp.get("node"), "unit": pp.get("unit") or "",
                           "value": fmt(v) if not isinstance(v, (dict, list)) else json.dumps(v, sort_keys=True),
                           "source": pp.get("source") or "unknown", "status": pp.get("status") or "",
                           "resolution": pp.get("resolution") or "", "reason": pp.get("reason") or "",
                           "cites": rp.citations(pp)})
    pl = lad["pipeline"]
    meaning = phrases.headlines.get(pl["headline"]) if pl["headline"] in phrases.headlines else \
        phrases.rungs.get(pl["rung"], "")
    hero = {"meaning": meaning, "next": rp.next_rung(lad, phrases)}
    lineage_model = graphview.model(graph, lad)
    tl = rp.timeline(manifest_doc, lad, lambda d: DISP_STYLE.get(d, ("grey", "·", d or "—")))

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
        "open_questions": open_questions, "plan_approval": plan_approval, "version_drift": version_drift(inv, graph),
        "graph_svg": graphview.svg(lineage_model), "mermaid": graphview.mermaid(lineage_model),
        "isolated": sorted(n for n, v in lineage_model["nodes"].items() if v.get("isolated")),
        "hero": hero, "attention": attention, "decision_cards": decision_cards, "cards": parity,
        "param_rows": param_rows, "timeline": tl, "thumb_manifest": thumb_manifest,
        "glossary": phrases.glossary, "layers": layers, "default_layer": default_layer,
        "views_links": [(ds, v["anchor"]) for ds, v in sorted(views.items())
                        if (lad["datasets"].get(ds) or {}).get("state") and v.get("tables")],
        "run_rel": f"migration/runs/{run_id}",
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


def _env() -> jinja2.Environment:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(HERE / "templates")),
                             autoescape=jinja2.select_autoescape(["html", "j2"], default=True),
                             trim_blocks=True, lstrip_blocks=True)
    env.filters["fmt"] = fmt
    env.filters["short"] = lambda s, n=12: (str(s).split(":", 1)[-1][:n] if s else "—")
    env.filters["anchor"] = lambda s: re.sub(r"[^A-Za-z0-9_.-]", "-", str(s))
    env.filters["png_uri"] = lambda b: "data:image/png;base64," + base64.b64encode(b).decode("ascii")
    env.filters["drift_text"] = rp.version_drift_text
    env.filters["md_code"] = lambda t: markupsafe.Markup(re.sub(r"`([^`]+)`", r"<code>\1</code>",
                                                                str(markupsafe.escape(t))))
    env.tests["match"] = lambda v, pat: re.match(pat, str(v)) is not None
    env.globals["DISP_STYLE"] = DISP_STYLE
    return env


def render(ctx: dict, template: str = "report.html.j2") -> str:
    return _env().get_template(template).render(**ctx)


def no_run_page(migration: Path, status_line: str, meaning: str) -> str:
    return _env().get_template("norun.html.j2").render(migration=str(migration.name), status_line=status_line,
                                                      meaning=meaning)


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


def render_files(migration: Path, run_id: str | None = None) -> tuple[int, dict, dict[str, bytes]]:
    """Every file of migration/report/ as bytes (nothing written). Deterministic for one bundle."""
    phrases = Phrases(HERE / "phrases.v1.yaml")
    lad = ladder.compute(migration, run_id)
    if lad["run_id"] is None or (run_id is None and not ladder.newest_sealed_run(migration)):
        lad["pipeline"]["report"] = "migration/report/index.html"
        lad["status_line"] = ladder.status_line(lad)
        meaning = phrases.headlines.get("NO SEALED RUN", "")
        rep = {"schema": "axis-migrate.report.v1", "status": "none", "rung": "none", "headline": "NO SEALED RUN",
               "steps": {}, "assumptions": [], "known_differences": [], "evidence_id": None,
               "status_line": lad["status_line"], "thumbnails": {}}
        files = {"index.html": no_run_page(migration, lad["status_line"], meaning).encode("utf-8"),
                 "REPORT.md": rp.norun_md(lad["status_line"], meaning).encode("utf-8")}
        rep["files"] = {k: ac.sha256_bytes(v) for k, v in sorted(files.items())}
        files["REPORT.json"] = (json.dumps(rep, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")
        return 3, rep, files
    # this render is the report the status line points at: render it with the path, not "no report"
    lad["pipeline"]["report"] = "migration/report/index.html"
    lad["status_line"] = ladder.status_line(lad)
    ctx = build_context(migration, lad, phrases)
    files: dict[str, bytes] = {}
    # hash-checked copies the Markdown summary links to (bad paths were excluded in build_context)
    for group in ("plots", "compare"):     # no swipe map any more (map review C8): the cards show the plot
        for src, rel in ctx["_copy"][group]:  # thumbnails and link to the GeoLibre views
            if src.is_file():
                files[f"assets/{rel}"] = src.read_bytes()
    ctx["phrase_missing"] = sorted(phrases.missing)
    page = render(ctx)
    ctx["phrase_missing"] = sorted(phrases.missing)  # second pass catches codes first seen while rendering
    page = render(ctx)
    files["index.html"] = page.encode("utf-8")
    files["REPORT.md"] = rp.report_md(ctx, ctx["mermaid"]).encode("utf-8")
    try:
        files["parity.xlsx"] = rp.parity_xlsx(ctx)
    except ImportError:  # openpyxl is in the lock; a stripped env renders without the workbook
        pass
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
           "next_rung": {"next": ctx["hero"]["next"]["next"],
                         "missing": [x["code"] for x in ctx["hero"]["next"]["items"]]},
           "decisions_for_you": [d["subject"] for d in ctx["decision_cards"]],
           "open_questions": [q.get("id") for q in ctx["open_questions"]],
           "plan_approval": ctx["plan_approval"].get("by"),
           "version_drift": ctx["version_drift"],
           "cards": [c["id"] for c in ctx["cards"]],
           "thumbnails": ctx["thumb_manifest"],
           "phrase_missing": ctx["phrase_missing"], "versions": ctx["versions"]}
    # secret scan of what we are about to publish (data URIs and vendored files excluded)
    hits = []
    for rel, data in sorted(files.items()):
        if rel.startswith("assets/vendor/") or not rel.endswith((".html", ".js", ".json", ".geojson", ".md")):
            continue
        names = ladder.secret_scan_text(data.decode("utf-8", errors="replace"))
        if names:
            hits.append({"path": rel, "patterns": names})
    if hits:
        page = ("<!doctype html><meta charset='utf-8'><title>Report withheld</title><h1>Report withheld</h1>"
                "<p>The secret scan matched: " + html.escape(", ".join(h["path"] for h in hits)) + "</p>")
        files = {"index.html": page.encode(),
                 "REPORT.md": (ctx["status_line"] + "\n\n# Report withheld\n\nThe secret scan matched: "
                               + ", ".join(h["path"] for h in hits) + "\n").encode("utf-8")}
        rep = {"schema": "axis-migrate.report.v1", "status": "WITHHELD_SECRET", "rung": None, "secret_scan": hits,
               "run_id": ctx["run_id"], "evidence_id": ctx["evidence_id"], "steps": {}, "assumptions": [],
               "known_differences": [], "thumbnails": {}, "status_line": ctx["status_line"]}
    rep["files"] = {k: ac.sha256_bytes(v) for k, v in sorted(files.items())}
    files["REPORT.json"] = (json.dumps(rep, sort_keys=True, indent=1, ensure_ascii=False, allow_nan=False)
                            + "\n").encode("utf-8")
    stale = bool(ctx["stale"])
    code = 2 if (hits or p["incomplete"] or stale or p["headline"] == "NOTHING VERIFIED") else 0
    return code, rep, files


def build(migration: Path, run_id: str | None = None) -> tuple[int, dict]:
    code, rep, files = render_files(migration, run_id)
    _write_tree(migration, files)
    return code, rep


def check(migration: Path, run_id: str | None = None) -> list[dict]:
    """Deliverable check: every file REPORT.json lists exists with its sha256, every thumbnail's source still
    hashes to its sealed evidence object, the report belongs to the computed evidence id, and a fresh render from
    the bundle gives the same bytes (report.py is deterministic). [] = all good."""
    import report_check
    problems = report_check.check(migration)
    rdir = migration / "report"
    rj = rdir / "REPORT.json"
    if rj.is_file():
        try:
            rid = (json.loads(rj.read_text(encoding="utf-8")) or {}).get("run_id")
        except ValueError:
            rid = None
        _, _, fresh = render_files(migration, run_id or rid)
        for rel, data in sorted(fresh.items()):
            f = rdir / rel
            if not f.is_file():
                problems.append({"code": "REPORT_FILE_MISSING", "path": rel})
            elif f.read_bytes() != data:
                problems.append({"code": "REPORT_NOT_FROM_BUNDLE", "path": rel,
                                 "detail": "differs from a fresh render of the sealed bundle"})
    seen, out = set(), []
    for x in problems:
        k = (x["code"], x.get("path"))
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render migration/report/ from one sealed evidence bundle.")
    ap.add_argument("migration")
    ap.add_argument("--run", default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="check the rendered report against the sealed bundle (exit 2 on a missing or changed file)")
    a = ap.parse_args(argv)
    migration = Path(a.migration).resolve()
    if migration.name != "migration" and (migration / "migration").is_dir():
        migration = migration / "migration"
    if a.check:
        probs = check(migration, a.run)
        if a.json:
            print(json.dumps({"ok": not probs, "problems": probs}, sort_keys=True, indent=1))
        else:
            for x in probs:
                print(f"{x['code']}: {x.get('path') or ''} {x.get('detail') or ''}".rstrip())
            print("report check: " + ("ok" if not probs else f"{len(probs)} problem(s)"))
        return 2 if probs else 0
    code, rep = build(migration, a.run)
    if a.json:
        print(json.dumps({k: v for k, v in rep.items() if k != "files"}, sort_keys=True, indent=1))
    else:
        print(rep.get("status_line") or rep.get("status"))
        print(f"report: {migration / 'report' / 'REPORT.md'} (file pane) · {migration / 'report' / 'index.html'} "
              f"(offline export) (exit {code})")
    return code


if __name__ == "__main__":
    sys.exit(main())
