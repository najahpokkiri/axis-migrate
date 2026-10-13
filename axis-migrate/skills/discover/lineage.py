#!/usr/bin/env python3
"""discover/lineage.py: static legacy lineage, checked against the real files, as a graph draft (02 §4.1).

    lineage.py <migration> [--draft-only] [--json]

Reads <migration>/inventory.json (scan.py) and the legacy files' profiles; writes the draft graph document
(CONTRACTS §4, `axis-migrate.graph.v1` content) to <migration>/.graph/draft.json and hands it to the single graph
writer: `axpy graph.py init <migration> --from <draft>` (revision 1). With --draft-only, or when graph.py is absent,
the draft is validated here and left for graph.py.

Rules: steps = live scripts/notebooks/models + manual steps found as derived copies + superseded twins (scope drop,
kept as knowledge with equivalent_to edges); datasets = persisted datasets only (memory layers stay in step ops);
every edge carries per-edge evidence (file:line); unmatched items become questions linked to a node and a code line,
never silent drops; scope facts the files answer (superseded code, display-only layers, unused classes) are recorded
with evidence in `scope_facts[]` for the interview's confirm_all, not asked separately (DESIGN R15).
Exit codes: 0 ok, 2 invalid draft / missing inventory, 3 graph.json already exists (use graph.py apply).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "lib"))
import axis_common as ac  # noqa: E402

SCHEMA = "axis-migrate.graph.v1"
DRAFT_SCHEMA = "axis-migrate.graph-draft.v1"
STEP_ID_RE = re.compile(r"^[smb]\d{2}[a-z]?_[a-z0-9_]+$")
DS_ID_RE = re.compile(r"^ds\.[a-z0-9_]+(?:@[a-z0-9_]+)?$")
NO_EXT = {"raster": "tif", "vector": "gpkg", "table": "csv", "document": "txt"}
LEVEL = {"high": 3, "medium": 2, "low": 1}


def ref(file: str, a: int, b: int | None = None) -> str:
    return f"{file}:{a}" + (f"-{b}" if b and b != a else "")


def ev(state: str, r: str, summary: str) -> dict:
    return {"state": state, "ref": r, "summary": summary, "by": "lineage", "rev": 1}


def tokens(name: str) -> set[str]:
    base = re.sub(r"^\d+[a-z]?_", "", name.lower())
    return {t for t in re.split(r"[^a-z0-9]+", base) if t and not t.isdigit()}


class Lineage:
    def __init__(self, migration: Path):
        self.mig = migration
        self.inv = ac.load_json(migration / "inventory.json")
        self.legacy = (migration / self.inv["legacy_root"]).resolve()
        self.ds = {d["id"]: d for d in self.inv["datasets"]}
        self.ops = {o["id"]: o for o in self.inv["ops"]}
        self.code = {c["id"]: c for c in self.inv["code"]}
        self.files = {f["path"]: f for f in self.inv["files"]}
        self.questions: list[dict] = []
        self.checks = {"passed": [], "warnings": [], "failed": []}
        self.scope_facts: list[dict] = []

    # ------------------------------------------------------------------ helpers
    def profile(self, did: str) -> dict:
        d = self.ds.get(did) or {}
        p = d.get("profile_ref")
        if p and (self.mig / p).exists():
            return ac.load_json(self.mig / p)
        return {}

    def code_title(self, c: dict) -> str:
        src = self.legacy / c["path"]
        try:
            for line in src.read_text(encoding="utf-8", errors="replace").splitlines()[:8]:
                t = line.strip()
                if t.startswith("#") and not t.startswith(("#!", "# -*-")):
                    t = re.sub(r"^#+\s*(\d+[a-z]?\s*[-:.]\s*)?", "", t).strip()
                    if len(t) > 3 and not t.startswith(("---", "===")):
                        return t[:80]
        except OSError:
            pass
        return re.sub(r"[_\-]+", " ", Path(c["path"]).stem).strip()

    # ------------------------------------------------------------------ steps
    def build(self) -> dict:
        inv = self.inv
        steps, edges = [], {}
        cand = {s["id"]: s for s in inv["step_candidates"]}
        live = [s for s in inv["step_candidates"] if s["status"] == "live"]
        superseded = [s for s in inv["step_candidates"] if s["status"] == "superseded"]
        self.step_ids = set()
        settings_by_code = defaultdict(list)
        for st in inv.get("settings", []):
            if st.get("line"):
                settings_by_code[st["code"]].append(st["line"])
        for s in live + superseded:
            c = self.code[s["code"]]
            ops = [self.ops[o] for o in s["ops"]]
            first_comment = None
            if ops:  # the comment block directly above the first call belongs to the step
                above = {cm["line"] for cm in ops[0].get("comments", []) if cm["line"] < ops[0]["line"]}
                first_comment = ops[0]["line"]
                while first_comment - 1 in above:
                    first_comment -= 1
            a = min([first_comment or 10 ** 9] + [o["line"] for o in ops] + settings_by_code.get(c["id"], []))
            b = max(o["end_line"] for o in ops) if ops else a
            writes = [(o, w) for o in ops for w in o["writes"] if w["mode"] in ("create", "append")]
            exists = [self.ds[w["dataset"]]["exists"] for _, w in writes if w["dataset"] in self.ds]
            executed = "unknown" if not exists else ("yes" if all(exists) else ("no" if not any(exists) else "partial"))
            ex_ev = [f"inventory:{self.ds[w['dataset']]['legacy_path']}" for _, w in writes
                     if self.ds.get(w["dataset"], {}).get("exists")][:3]
            for d in inv["docs"]:
                for x in d["run_order"]:
                    if x["path"] == c["path"]:
                        ex_ev.append(ref(d["path"], x["line"]))
            step = {"id": s["id"], "title": self.code_title(c), "kind": s["kind"],
                    "tool": c.get("runtime") or "python", "scope": "migrate", "scope_reason": None,
                    "legacy": {"source": c["path"], "lines": [[a, b]], "executed": executed,
                               "executed_evidence": sorted(set(ex_ev))},
                    "ops": [self.op_summary(o) for o in ops], "params": [], "timeout_s": 120,
                    "evidence": [ev("directly_observed", ref(c["path"], a, b), f"{len(ops)} legacy tool calls")],
                    "order": s.get("order")}
            if c.get("notebook") and not c["notebook"].get("monotonic", True):
                step["outputs_untrusted"] = True
            if s["status"] == "superseded":
                step["scope"] = "drop"
                step["scope_reason"] = "alternative_impl" if c.get("twin_of") else "dead_code"
                step["twin_of"] = self.step_of_code(c.get("twin_of"))
                step["evidence"] += [ev("documented" if x["kind"] in ("doc_mention", "comment") else
                                        "structurally_inferred",
                                        ref(x["file"], x["line"]) if x.get("file") and x.get("line") else
                                        f"{c['path']}:1", f"{x['kind']}: {x.get('text') or x.get('value')}")
                                     for x in c["status_evidence"]]
                missing = [w["dataset"] for _, w in writes if not self.ds.get(w["dataset"], {}).get("exists")]
                self.scope_facts.append({
                    "node": s["id"], "fact": "superseded", "scope": "drop", "scope_reason": step["scope_reason"],
                    "twin_of": step["twin_of"], "missing_writes": sorted(set(missing)),
                    "evidence": [e["ref"] for e in step["evidence"][1:]],
                    "summary": f"{c['path']} is marked superseded"
                               + (f" (alternative implementation of {step['twin_of']})" if step["twin_of"] else "")
                               + f"; {len(set(missing))} of its outputs do not exist", "for": "confirm_all"})
            steps.append(step)
            self.step_ids.add(s["id"])
        for m in inv.get("manual_step_candidates", []):
            evd = [x for x in m["evidence"] if x.get("kind") == "doc_mention"]
            src = evd[0]["file"] if evd else None
            lines = [[evd[0]["line"], evd[0].get("end_line") or evd[0]["line"]]] if evd else []
            tgt = self.ds[m["outputs"][0]]
            srcd = self.ds[m["inputs"][0]]
            steps.append({"id": m["id"], "title": f"Manual edit: {Path(tgt['legacy_path']).name} from "
                                                 f"{Path(srcd['legacy_path']).name}",
                          "kind": "manual", "tool": "gis_gui", "scope": "keep_legacy",
                          "scope_reason": "manual_pending_answer",
                          "legacy": {"source": src, "lines": lines, "executed": "yes",
                                     "executed_evidence": [f"inventory:{tgt['legacy_path']}"]},
                          "ops": [], "params": [], "timeout_s": 120, "derived_copy": m["derived_copy"],
                          "evidence": ([ev("documented", ref(src, *lines[0]), "manual step described in the notes")]
                                       if src else []) +
                                      [ev("directly_observed", f"inventory:{tgt['legacy_path']}",
                                          f"derived copy {m['derived_copy']} of {srcd['legacy_path']}")]})
            self.step_ids.add(m["id"])
        self.steps = steps
        # ------------------------------------------------------------------ datasets (persisted only)
        datasets = []
        for did, d in sorted(self.ds.items()):
            role = d["role"]
            orphan = d.get("unused_kind") == "orphan_output"
            if role == "unknown":
                role = "unused"
            prof = self.profile(did)
            hints = d.get("compare_hints") or {}
            compare = {"measure": None, "count": None, "categorical": hints.get("categorical"), "key": None,
                       "match": None, "layer": d.get("layer"), "zones": None, "zone_rule": None, "ignore": [],
                       "rename": {}, "measure_crs_param": None, "may_be_empty": False}
            if hints.get("sheet"):
                compare.update({"sheet": hints.get("sheet"), "header_row": hints.get("header_row"),
                                "data_rows": hints.get("data_rows"), "total_row": None})
            leg = {"path": d["legacy_path"], "layer": d.get("layer"), "sheet": hints.get("sheet"),
                   "exists": d["exists"], "sha256": d.get("sha256"), "content_sha256": d.get("content_sha256"),
                   "profile": d.get("profile_ref"), "sidecars": d.get("sidecars", []),
                   "format": d.get("format")}
            rec = {"id": did, "title": Path(d["legacy_path"]).stem if not d.get("layer") or
                   d.get("persistence") != "gdb" else d["layer"],
                   "kind": d["kind"] if d["kind"] in ("raster", "vector", "table", "document") else "document",
                   "role": role, "role_lineage": role, "persistence": d.get("persistence", "file"), "legacy": leg,
                   "compare": compare, "compare_hints": hints, "legacy_in_memory": False,
                   "no_reference_reason": None,
                   "evidence": [f"inventory:{d['legacy_path']}"] + [
                       ref(x["file"], x["line"]) for x in d.get("role_evidence", [])
                       if x.get("kind") in ("code_write", "code_read", "doc_mention", "doc_output") and x.get("line")][:6]}
            if role in ("unused",) or orphan:
                rec["reference"] = {"source": "none", "match_method": "lineage", "via_dataset": None,
                                    "confirmed_by": None}
                rec["no_reference_reason"] = "orphan_legacy_file" if orphan else (d.get("unused_kind") or "unused")
            if orphan:
                rec["orphan"] = True
            if d.get("derived_from"):
                rec["derived_from"] = d["derived_from"]
            if d.get("input_of_manual_step"):
                rec["input_of_manual_step"] = d["input_of_manual_step"]
            if d.get("unused_kind"):
                rec["unused_kind"] = d["unused_kind"]
            if d.get("grid_id"):
                rec["grid_id"] = d["grid_id"]
            datasets.append(rec)
            if d.get("unused_kind") == "display_only":
                self.scope_facts.append({"node": did, "fact": "display_only", "scope": "unused",
                                         "evidence": [f"{x['file']}#layer={x.get('layer')}" for x in d["role_evidence"]
                                                      if x.get("kind") == "project_layer"],
                                         "summary": f"{d['legacy_path']} appears only in a project file", "for":
                                         "confirm_all"})
        self.datasets = {d["id"]: d for d in datasets}
        # ------------------------------------------------------------------ edges
        step_of_op = {}
        for s in steps:
            for o in s["ops"]:
                step_of_op[o["id"]] = s["id"]
        written_by_step = defaultdict(set)
        for s in steps:
            for o in s["ops"]:
                for w in self.ops[o["id"]]["writes"]:
                    written_by_step[s["id"]].add(w["dataset"])

        def add_edge(frm, to, kind, op, lines, evidence):
            # graph.py's edge-id pattern has no '/': project paths in `displays` edges are slugged in the id only
            fid = f"project:{ac.slug(frm[8:])}" if frm.startswith("project:") else frm
            eid = f"e.{fid}>{to}" + ("" if kind in ("reads", "writes") else f"#{kind}")
            e = edges.get(eid)
            if e is None:
                e = edges[eid] = {"id": eid, "from": frm, "to": to, "kind": kind, "op": op, "lines": [],
                                  "evidence": [], "conflict": False, "review": "pending", "ops": []}
            if op and op not in e["ops"]:
                e["ops"].append(op)
            for ln in lines:
                if ln not in e["lines"]:
                    e["lines"].append(ln)
            for x in evidence:
                if x not in e["evidence"]:
                    e["evidence"].append(x)
            return e
        self.intra_step = []
        for s in steps:
            sid = s["id"]
            for os_ in s["ops"]:
                o = self.ops[os_["id"]]
                state = "directly_observed" if o["io_source"] in ("qgis_algs.json", "arcpy_tools.json", "python_io",
                                                                  "pyqgis_api") else "structurally_inferred"
                for r in o["reads"]:
                    did = r["dataset"]
                    if did not in self.datasets:
                        continue
                    if did in written_by_step[sid]:
                        self.intra_step.append({"step": sid, "op": o["id"], "dataset": did, "line": r["line"]})
                        continue
                    add_edge(did, sid, "reads", o["id"], [[o["line"], o["end_line"]]],
                             [ev(state, ref(o["file"], o["line"], o["end_line"]),
                                 f"{o['tool']} {r['param']} (line {r['line']})")])
                for g in o["reads_grid"]:
                    did = g["dataset"]
                    if did not in self.datasets or did in written_by_step[sid]:
                        continue
                    add_edge(did, sid, "reads_grid", o["id"], [[o["line"], o["end_line"]]],
                             [ev(state, ref(o["file"], g["line"]), f"{o['tool']} {g['param']} = {g['derived']}({did})")])
                for w in o["writes"]:
                    did = w["dataset"]
                    if did not in self.datasets:
                        continue
                    kind = "reads_write" if w["mode"] in ("in_place",) else "writes"
                    if w["mode"] == "delete":
                        continue
                    add_edge(sid, did, kind, o["id"], [[o["line"], o["end_line"]]],
                             [ev(state, ref(o["file"], o["line"], o["end_line"]) if kind == "writes"
                                 else ref(o["file"], w["line"]), f"{o['tool']} {w['param']} ({w['mode']})")])
        # ArcPy environment settings that name a dataset shape every raster the step writes (AK-16, MP-03)
        code_step = {c["id"]: c.get("step_id") for c in inv["code"]}
        for st in inv.get("settings", []):
            sid = code_step.get(st["code"])
            val = st.get("value") if isinstance(st.get("value"), dict) else {}
            if st["family"] == "arcpy_env" and sid in self.step_ids and val.get("dataset") in self.datasets and \
                    st["name"] in ("snapRaster", "cellSize", "extent", "outputCoordinateSystem", "mask") and st.get("line"):
                add_edge(val["dataset"], sid, "reads_grid", None, [[st["line"], st["line"]]],
                         [ev("directly_observed", ref(st["file"], st["line"]),
                             f"arcpy.env.{st['name']} = {val.get('derived', 'dataset')}({val['dataset']})")])
        for m in inv.get("manual_step_candidates", []):
            step = next(s for s in steps if s["id"] == m["id"])
            r = step["evidence"][0]["ref"] if step["evidence"] else f"inventory:{self.ds[m['outputs'][0]]['legacy_path']}"
            for i in m["inputs"]:
                add_edge(i, m["id"], "reads", None, step["legacy"]["lines"],
                         [ev("documented", r, "named as the source of the manual edit"),
                          ev("directly_observed", f"inventory:{self.ds[i]['legacy_path']}",
                             f"{m['derived_copy']}: same geometries as the edited file")])
            for o in m["outputs"]:
                add_edge(m["id"], o, "writes", None, step["legacy"]["lines"],
                         [ev("documented", r, "saved by hand"),
                          ev("directly_observed", f"inventory:{self.ds[o]['legacy_path']}", f"{m['derived_copy']}")])
        # equivalent_to (knowledge only; pairing needs a human confirmation, D2-5)
        for s in steps:
            if s.get("scope") != "drop" or not s.get("twin_of"):
                continue
            twin = s["twin_of"]
            twin_ds = {e["from"] for e in edges.values() if e["to"] == twin and e["kind"] in ("reads", "reads_grid")} | \
                      {e["to"] for e in edges.values() if e["from"] == twin and e["kind"] in ("writes", "reads_write")}
            mine = {e["from"] for e in edges.values() if e["to"] == s["id"] and e["kind"] in ("reads",)} | \
                   {e["to"] for e in edges.values() if e["from"] == s["id"] and e["kind"] in ("writes", "reads_write")}
            doc_refs = [x["ref"] for x in s["evidence"][1:] if x.get("state") == "documented"]
            for a in sorted(mine):
                if a in twin_ds or self.ds[a]["exists"]:
                    continue
                na = tokens(self.ds[a].get("layer") or Path(self.ds[a]["legacy_path"]).stem)
                best, score = None, 0.0
                for b in sorted(twin_ds):
                    kb, ka = self.ds[b]["kind"], self.ds[a]["kind"]
                    if ka != kb:
                        continue
                    nb = tokens(Path(self.ds[b]["legacy_path"]).stem if not self.ds[b].get("layer") or
                                self.ds[b].get("persistence") != "gdb" else self.ds[b]["layer"])
                    if not na or not nb:
                        continue
                    j = len(na & nb) / len(na | nb)
                    if j > score:
                        best, score = b, j
                if best and score >= 0.6:
                    add_edge(a, best, "equivalent_to", None, [],
                             [ev("documented", r, f"{s['id']} is an alternative implementation of {twin}")
                              for r in doc_refs[:2]] +
                             [ev("ai_inferred", f"inventory:{self.ds[a]['legacy_path']}",
                                 f"same role in the twin step; name tokens {sorted(na & self_tokens(self.ds[best]))}")])
        # displays (project files)
        for p in inv.get("projects", []):
            for l in p.get("layers", []):
                if not l.get("path"):
                    continue
                did = next((d["id"] for d in self.inv["datasets"] if d["legacy_path"] == l["path"]
                            and (l.get("layer") in (None, d.get("layer")) or d.get("layer") is None)), None)
                if did:
                    e = add_edge(f"project:{p['path']}", did, "displays", None, [],
                                 [ev("directly_observed", f"qgz:{p['path']}#layer={l.get('name')}",
                                     f"layer '{l.get('name')}' in group {l.get('group')}"
                                     + (f", subset {l['subset']}" if l.get("subset") else ""))])
                    if l.get("subset_parsed"):
                        e["subset"] = l["subset_parsed"]
        self.edges = edges
        for e in edges.values():
            e["lines"] = sorted(e["lines"])
            e["state"] = strongest([x["state"] for x in e["evidence"]])
            if not e["op"] and e["ops"]:
                e["op"] = e["ops"][0]
        self.check_against_files()
        self.unmatched_questions()
        # default new output names (CONTRACTS §4; graph.py re-derives them on every write)
        producer = {}
        for e in edges.values():
            if e["kind"] in ("writes", "reads_write") and e["from"] in self.step_ids:
                producer.setdefault(e["to"], e["from"])
        scope = {s["id"]: s["scope"] for s in steps}
        for did, d in self.datasets.items():
            pb = producer.get(did)
            if d["role"] != "input" and pb and scope.get(pb) == "migrate":
                name = Path(d["legacy"]["path"]).name if d["legacy"] and d["legacy"]["path"] else \
                    f"{did[3:]}.{NO_EXT.get(d['kind'], 'bin')}"
                d["new"] = {"path": f"{pb}/{name}", "layer": d["legacy"]["layer"] if d["legacy"] else None,
                            "auto": True}
        g = {"schema": SCHEMA, "draft_schema": DRAFT_SCHEMA,
             "project": {"name": self.legacy.name, "legacy_root": inv["legacy_root"],
                         "legacy_tree_sha256": ac.tree_hash(self.legacy), "plugin_version": ac.PLUGIN_VERSION,
                         "inventory_manifest_sha256": inv["scan"]["manifest_sha256"]},
             "steps": sorted(steps, key=lambda s: s["id"]),
             "datasets": [self.datasets[k] for k in sorted(self.datasets)],
             "edges": [edges[k] for k in sorted(edges)],
             "questions": self.questions, "invariants": [],
             "legacy_output_dirs": inv.get("legacy_output_dirs", []),
             "scope_facts": sorted(self.scope_facts, key=lambda f: (f["node"], f["fact"])),
             "lineage_checks": self.checks, "intra_step_reads": self.intra_step,
             "open_items_from_scan": [i["id"] for i in inv.get("open_items", [])]}
        for f in inv.get("open_items", []):
            if f["kind"] == "unused_branch":
                self.scope_facts.append({"node": f["about"][0], "fact": "unused_branch", "scope": "out_of_scope",
                                         "values_used": f.get("used"), "values_unused": f.get("unused"),
                                         "evidence": [ref(x["file"], x["line"]) for x in f["evidence"] if x.get("file")],
                                         "summary": f"filter on {f.get('field')} keeps {f.get('used')}; "
                                                    f"{f.get('unused')} are present but unused", "for": "confirm_all"})
        g["scope_facts"] = sorted(self.scope_facts, key=lambda f: (f["node"], f["fact"]))
        return g

    def step_of_code(self, code_id: str | None) -> str | None:
        if not code_id:
            return None
        c = self.code.get(code_id)
        return c.get("step_id") if c else None

    def op_summary(self, o: dict) -> dict:
        lit = {}
        for k, v in sorted(o["params"].items()):
            val = v.get("value") if isinstance(v, dict) else v
            if isinstance(val, (int, float, bool, str)) or val is None:
                lit[k] = val
        return {"id": o["id"], "call": o["tool"], "api": o["api"], "lines": [o["line"], o["end_line"]],
                "ref": o["ref"],
                "reads": [r["dataset"] for r in o["reads"]] + [m["memory"] for m in o["memory_reads"]],
                "reads_grid": sorted({g["dataset"] for g in o["reads_grid"]}),
                "writes": [w["dataset"] for w in o["writes"]] + ([o["memory_out"]] if o["memory_out"] else []),
                "literal_params": lit, "flags": o["flags"]}

    # ------------------------------------------------------------------ 02 §4.1 step 6: check against the files
    def check_against_files(self) -> None:
        C = self.checks
        live_steps = {s["id"] for s in self.steps if s["scope"] == "migrate" and s["kind"] != "manual"}
        sid_of = {o["id"]: s["id"] for s in self.steps for o in s["ops"]}
        # (a) writes exist  (g) no empty outputs
        for s in self.steps:
            if s["id"] not in live_steps:
                continue
            for o in s["ops"]:
                for w in self.ops[o["id"]]["writes"]:
                    d = self.ds.get(w["dataset"])
                    if not d or w["mode"] == "delete":
                        continue
                    if d["exists"]:
                        f = self.files.get(d["legacy_path"], {})
                        if f.get("size") == 0:
                            C["failed"].append({"check": "g_output_not_empty", "dataset": d["id"], "op": o["id"]})
                        else:
                            C["passed"].append({"check": "a_write_exists", "dataset": d["id"], "op": o["id"]})
                            self.add_ds_ev(d["id"], ev("directly_observed", f"inventory:{d['legacy_path']}",
                                                       f"written by {o['id']}; file exists"))
                    else:
                        C["failed"].append({"check": "a_write_exists", "dataset": d["id"], "op": o["id"],
                                            "step": s["id"], "line": w["line"]})
        # (b) layer names exist
        for d in self.inv["datasets"]:
            if d.get("layer_missing"):
                C["failed"].append({"check": "b_layer_exists", "dataset": d["id"], "layer": d["layer"]})
            for lm in d.get("layer_mismatch", []):
                C["failed"].append({"check": "b_layer_exists", "dataset": d["id"], "layer": lm["requested"],
                                    "actual": lm["actual"], "ops": lm["ops"]})
        for o in self.inv["ops"]:
            if o["id"] not in sid_of:
                continue
            for r in o["reads"] + o["writes"]:
                d = self.ds.get(r["dataset"])
                if d and d["exists"] and d.get("layer") and d["kind"] == "vector" and not d.get("layer_missing") \
                        and not d.get("layer_mismatch"):
                    C["passed"].append({"check": "b_layer_exists", "dataset": d["id"], "layer": d["layer"],
                                        "op": o["id"]})
        # (c) fields read by a consumer exist in the file's schema
        for o in self.inv["ops"]:
            if sid_of.get(o["id"]) not in live_steps:
                continue
            for f in o.get("fields_read", []):
                d = self.ds.get(f["dataset"])
                prof = self.profile(f["dataset"]) if d and d["exists"] else {}
                cols = [c["name"] for c in prof.get("schema", [])]
                if not cols:
                    continue
                ok = f["field"] in cols
                (C["passed"] if ok else C["failed"]).append({"check": "c_field_in_schema", "dataset": d["id"],
                                                             "field": f["field"], "op": o["id"], "line": f["line"]})
        # (d) reads_grid consumers equal their template grid  (e) declared TARGET_CRS equals the file CRS
        for o in self.inv["ops"]:
            if sid_of.get(o["id"]) not in live_steps:
                continue
            temps = sorted({g["dataset"] for g in o["reads_grid"]})
            outs = [w["dataset"] for w in o["writes"] if self.ds.get(w["dataset"], {}).get("kind") == "raster"
                    and self.ds[w["dataset"]]["exists"]]
            for t in temps:
                pt = self.profile(t)
                for out in outs:
                    po = self.profile(out)
                    same = bool(pt) and bool(po) and pt.get("transform") == po.get("transform") and \
                        pt.get("shape") == po.get("shape") and (pt.get("crs") or {}).get("wkt") == \
                        (po.get("crs") or {}).get("wkt")
                    rec = {"check": "d_grid_equals_template", "dataset": out, "template": t, "op": o["id"]}
                    if same:
                        C["passed"].append(rec)
                        e = self.edges.get(f"e.{t}>{sid_of[o['id']]}#reads_grid")
                        if e is not None:
                            e["evidence"].append(ev("directly_observed", f"inventory:{self.ds[out]['legacy_path']}",
                                                    f"transform, shape and CRS of {out} == {t}"))
                    else:
                        tt, to_ = pt.get("transform") or [0] * 6, po.get("transform") or [0] * 6
                        off = None
                        if tt[0] and tt[4]:
                            off = [round((to_[2] - tt[2]) / tt[0], 6), round((to_[5] - tt[5]) / tt[4], 6)]
                        rec.update({"offset_px": off, "shape": [po.get("shape"), pt.get("shape")], "line": o["line"]})
                        C["failed"].append(rec)
            tc = (o["params"].get("TARGET_CRS") or {}).get("value") if isinstance(o["params"].get("TARGET_CRS"),
                                                                                   dict) else None
            if isinstance(tc, str) and tc.upper().startswith("EPSG:"):
                for w in o["writes"]:
                    po = self.profile(w["dataset"])
                    epsg = (po.get("crs") or {}).get("epsg")
                    if po and epsg is not None:
                        rec = {"check": "e_target_crs", "dataset": w["dataset"], "declared": tc, "file_epsg": epsg,
                               "op": o["id"]}
                        (C["passed"] if f"EPSG:{epsg}" == tc.upper() else C["failed"]).append(rec)
        # (f) mtime order: producer outputs not newer than consumer outputs (warning only; sidecars, inputs excluded)
        mt = {}
        for d in self.inv["datasets"]:
            f = self.files.get(d["legacy_path"])
            if d["exists"] and f and f.get("mtime"):
                mt[d["id"]] = f["mtime"]
        outs_of = defaultdict(set)
        ins_of = defaultdict(set)
        for e in self.edges.values():
            if e["kind"] in ("writes", "reads_write") and e["from"] in live_steps:
                outs_of[e["from"]].add(e["to"])
            if e["kind"] in ("reads", "reads_grid") and e["to"] in live_steps:
                ins_of[e["to"]].add(e["from"])
        for sid in sorted(live_steps):
            for did in sorted(ins_of[sid]):
                if self.ds[did]["role"] == "input" or did not in mt:
                    continue
                for out in sorted(outs_of[sid]):
                    if out in mt and mt[did] > mt[out]:
                        C["warnings"].append({"check": "f_mtime_order", "producer_output": did, "consumer": sid,
                                              "consumer_output": out, "producer_mtime": mt[did],
                                              "consumer_mtime": mt[out],
                                              "note": "mtime is corroboration only"
                                              + ("" if self.inv["scan"].get("mtime_reliable") else
                                                 " (scan: mtime_reliable=false)")})
        for k in C:
            C[k].sort(key=lambda r: json.dumps(r, sort_keys=True))

    def add_ds_ev(self, did: str, e: dict) -> None:
        d = self.datasets.get(did)
        if d is not None and e not in d["evidence"]:
            d["evidence"].append(e)

    # ------------------------------------------------------------------ 02 §4.1 step 7: unmatched -> questions
    def unmatched_questions(self) -> None:
        Q = []
        C = self.checks
        live = {s["id"]: s for s in self.steps if s["scope"] == "migrate" and s["kind"] != "manual"}
        downstream = self.downstream()

        def q(kind, node, nodes, header, text, why, trigger, evidence, level, options, guess=None, param_ids=()):
            imp, unc, cost = LEVEL[level], 3 if level == "high" else 2, 3 if kind == "manual_step" else 2
            Q.append({"node": node, "nodes": sorted(set(nodes) - {node}), "param_ids": list(param_ids), "kind": kind,
                      "header": header, "text": text, "why": why, "trigger": trigger, "evidence": evidence,
                      "agent_guess": guess, "options": options, "allow_other": True, "allow_dont_know": True,
                      "impact": {"datasets": sorted(set(d for n in nodes for d in downstream.get(n, set()))),
                                 "level": level},
                      "priority": {"impact": imp, "uncertainty": unc, "cost": cost, "score": imp * unc * cost},
                      "status": "open", "source": "lineage"})
        dont_know = {"value": "dont_know", "label": "Don't know",
                     "description": "Use the recommended option as a visible assumption"}
        # manual steps: a policy question, never a question about what the files show
        for m in self.inv.get("manual_step_candidates", []):
            dc = next(x for x in self.inv["derived_copies"] if x["id"] == m["derived_copy"])
            vm = dc["value_maps"][0] if dc.get("value_maps") else None
            rule = ""
            if vm:
                r = vm["rule"]
                rule = (f" Column {vm['column']} follows the rule {r['kind']}"
                        + (f"({r['value']!r})" if r.get("value") else "") + f" from {vm['from_column']} for "
                        f"{vm['rule_count']} of {vm['pairs']} features, with {len(vm['exceptions'])} exception(s).")
            tgt, src = self.ds[m["outputs"][0]], self.ds[m["inputs"][0]]
            evd = [x for x in m["evidence"] if x.get("kind") == "doc_mention"]
            trig = {"ref": ref(evd[0]["file"], evd[0]["line"], evd[0].get("end_line")), "quote": None} if evd else \
                {"ref": f"inventory:{tgt['legacy_path']}", "quote": None}
            q("manual_step", m["id"], [m["id"], tgt["id"], src["id"]], "Manual step",
              f"{tgt['legacy_path']} was made by hand from {src['legacy_path']} ({dc['wkb_equal']}/{dc['count']} "
              f"geometries identical; added {', '.join(dc['schema']['added']) or 'no'} column(s)).{rule} "
              f"How should the new pipeline handle this step?",
              "A manual step is redone by hand whenever its source changes; parity cannot tell which policy is wanted.",
              trig, [{"ref": f"inventory:{tgt['legacy_path']}", "fact": f"derived copy {dc['id']}"}], "high",
              [{"value": "derive", "label": "Derive in code (Recommended)", "recommended": True,
                "description": "Apply the observed rule plus an explicit exception table; verified against the "
                               "hand-made file"},
               {"value": "read_edited", "label": "Read the edited file",
                "description": "Keep the hand-made file as a hash-pinned input (bridge)"}, dont_know],
              guess={"option": "derive", "why": "the rule explains all but the listed exceptions"})
        # writes without files (live steps): branch probably never run
        miss = defaultdict(list)
        for f in C["failed"]:
            if f["check"] == "a_write_exists":
                miss[f["step"]].append(f)
        for sid, fs in sorted(miss.items()):
            o = self.ops[fs[0]["op"]]
            q("lineage", sid, [sid] + [f["dataset"] for f in fs], "Missing output",
              f"{sid} writes {', '.join(sorted({f['dataset'] for f in fs}))} but no such file exists in the legacy "
              f"folder. Was this part never run, or was the file removed?",
              "Without the legacy file the output cannot be verified (it would be NOT_COMPARED).",
              {"ref": ref(o["file"], fs[0]["line"]), "quote": None},
              [{"ref": ref(self.ops[f["op"]]["file"], f["line"]), "fact": f"{f['op']} writes {f['dataset']}"}
               for f in fs], "medium",
              [{"value": "not_run", "label": "Never run (Recommended)", "recommended": True,
                "description": "Keep the step; the output has no reference"},
               {"value": "removed", "label": "File was removed", "description": "Ask for the file"}, dont_know])
        # grid / layer / field / CRS check failures: high impact, linked to the code line
        for f in C["failed"]:
            if f["check"] == "d_grid_equals_template":
                o = self.ops[f["op"]]
                sid = o["id"].rsplit(".", 1)[0]
                q("lineage", f["dataset"], [f["dataset"], f["template"], sid], "Grid differs from template",
                  f"{f['dataset']} was rasterised on the grid of {f['template']} ({o['ref']}), but its file has a "
                  f"different grid (offset {f.get('offset_px')} px, shapes {f.get('shape')}). Which grid is right?",
                  "The legacy run itself lost alignment; every downstream cell comparison depends on it.",
                  {"ref": o["ref"], "quote": None},
                  [{"ref": f"inventory:{self.ds[f['dataset']]['legacy_path']}", "fact": "grid differs"}], "high",
                  [{"value": "template", "label": "The template grid (Recommended)", "recommended": True,
                    "description": "Build on the template grid; the legacy file is a defect"},
                   {"value": "file", "label": "The file's grid", "description": "Reproduce the legacy grid"},
                   dont_know])
            elif f["check"] in ("b_layer_exists",):
                users = [self.ops[o] for o in f.get("ops", []) if o in self.ops] or \
                    [o for o in self.inv["ops"] if any(r["dataset"] == f["dataset"] for r in o["reads"] + o["writes"])]
                o = users[0] if users else None
                q("lineage", f["dataset"], [f["dataset"]], "Layer not found",
                  f"Code asks for layer {f.get('layer')!r} of {self.ds[f['dataset']]['legacy_path']}"
                  + (f" ({o['ref']})" if o else "") + ", but the file has no such layer. Which layer is meant?",
                  "Reading the wrong layer silently changes every downstream result.",
                  {"ref": o["ref"] if o else f"inventory:{self.ds[f['dataset']]['legacy_path']}", "quote": None},
                  [], "high", [{"value": "first", "label": "The file's only/first layer", "recommended": True,
                                "description": "Assume the layer was renamed"}, dont_know])
            elif f["check"] == "c_field_in_schema":
                o = self.ops[f["op"]]
                q("lineage", f["dataset"], [f["dataset"], o["id"].rsplit(".", 1)[0]], "Field not found",
                  f"{o['id']} reads field {f['field']!r} of {f['dataset']} ({ref(o['file'], f['line'])}), but the "
                  f"file's schema has no such field.", "A missing field means a different producer or version.",
                  {"ref": ref(o["file"], f["line"]), "quote": None}, [], "high",
                  [{"value": "renamed", "label": "Field was renamed", "recommended": True,
                    "description": "Map it to the closest field"}, dont_know])
            elif f["check"] == "e_target_crs":
                q("lineage", f["dataset"], [f["dataset"]], "CRS differs",
                  f"{f['op']} declares {f['declared']} but the file is EPSG:{f['file_epsg']}.",
                  "A CRS mismatch changes geometry checks.", {"ref": self.ops[f["op"]]["ref"], "quote": None}, [],
                  "high", [{"value": "declared", "label": "The declared CRS", "recommended": True,
                            "description": ""}, dont_know])
        # files without a writer under output folders (orphans): never paired automatically
        orphans = sorted(d for d, x in self.datasets.items() if x.get("orphan"))
        if orphans:
            q("lineage", orphans[0], orphans, "Unreferenced legacy files",
              f"{len(orphans)} file(s) sit in a folder the scripts write to, but no code writes them: "
              f"{', '.join(self.ds[o]['legacy_path'] for o in orphans)}. A manual step, a stale copy, or a missing "
              f"branch?", "Orphan files are never paired automatically (no name matching).",
              {"ref": f"inventory:{self.ds[orphans[0]]['legacy_path']}", "quote": None}, [], "medium",
              [{"value": "stale", "label": "Stale copies, ignore (Recommended)", "recommended": True,
                "description": "Keep them out of parity"},
               {"value": "manual", "label": "Made by hand", "description": "Model a manual step"}, dont_know])
        # inputs read by code but missing on disk (blocking) and unreferenced data files in input folders
        for did, d in sorted(self.ds.items()):
            if d["role"] == "input" and not d["exists"] and any(
                    self.ops[o]["code"] in {self.code_id_of(s) for s in live.values()} for o in d["consumed_by"]):
                o = self.ops[d["consumed_by"][0]]
                q("lineage", did, [did] + sorted({self.ops[c]["id"].rsplit(".", 1)[0] for c in d["consumed_by"]}),
                  "Missing input", f"{d['legacy_path']} is read ({o['ref']}) but is not in the legacy folder. "
                                   f"Where is it?", "A missing input stops the run (exit 12); nothing is fabricated.",
                  {"ref": o["ref"], "quote": None}, [], "high",
                  [{"value": "provide", "label": "I will provide it (Recommended)", "recommended": True,
                    "description": "Add the file to the legacy folder"}, dont_know])
            if d["role"] == "unknown" and d["exists"] and not d.get("unused_kind") == "orphan_output":
                q("lineage", did, [did], "Unused file",
                  f"{d['legacy_path']} is not read by any script, project or note. Is it needed?",
                  "Unused inputs may hide a missing branch.", {"ref": f"inventory:{d['legacy_path']}", "quote": None},
                  [], "low", [{"value": "unused", "label": "Not needed (Recommended)", "recommended": True,
                               "description": ""}, dont_know])
        # unresolved path expressions in live code
        live_codes = {self.code_id_of(s) for s in live.values()}
        by_step = defaultdict(list)
        for u in self.inv.get("unresolved", []):
            if u.get("code") in live_codes and u["reason"] not in ("env_var",):
                by_step[u["code"]].append(u)
        for cid, us in sorted(by_step.items()):
            sid = self.code[cid]["step_id"]
            q("unresolved_value", sid, [sid], "Unresolved path",
              f"{sid} builds {len(us)} path(s) the scanner cannot resolve "
              f"({', '.join(sorted({u['reason'] for u in us}))}), e.g. {us[0]['expr']!r} at "
              f"{ref(us[0]['file'], us[0]['line'])}. Which files do they name?",
              "An unresolved path is an unknown input or output.", {"ref": ref(us[0]["file"], us[0]["line"]),
                                                                     "quote": us[0]["expr"]}, [], "medium",
              [{"value": "explain", "label": "I will name the files", "recommended": True, "description": ""},
               dont_know])
        Q.sort(key=lambda x: (-LEVEL[x["impact"]["level"]], x["kind"] != "manual_step", x["node"]))
        for i, x in enumerate(Q, start=1):
            x["id"] = f"q{i:02d}"
        self.questions = Q

    def code_id_of(self, step: dict) -> str:
        return next((c["id"] for c in self.inv["code"] if c.get("step_id") == step["id"]), "")

    def downstream(self) -> dict[str, set]:
        adj = defaultdict(set)
        for e in self.edges.values():
            if e["kind"] in ("reads", "reads_grid", "writes", "reads_write"):
                adj[e["from"]].add(e["to"])
        out = {}
        for n in list(adj) + list(self.datasets):
            seen, stack = set(), [n]
            while stack:
                x = stack.pop()
                for y in adj.get(x, ()):
                    if y not in seen:
                        seen.add(y)
                        stack.append(y)
            out[n] = {y for y in seen if y in self.datasets} | ({n} if n in self.datasets else set())
        return out


def self_tokens(d: dict) -> set[str]:
    return tokens(Path(d["legacy_path"]).stem if not d.get("layer") or d.get("persistence") != "gdb" else d["layer"])


def strongest(states: list[str]) -> str:
    order = ["verified", "runtime_probed", "directly_observed", "structurally_inferred", "user_asserted",
             "documented", "ai_inferred"]
    vals = [s for s in states if s in order]
    return min(vals, key=order.index) if vals else "ai_inferred"


# ---------------------------------------------------------------------------------------------------------------
# validation when graph.py is not available (graph.py validates again on init)
# ---------------------------------------------------------------------------------------------------------------
def validate_draft(g: dict) -> list[str]:
    errs = []
    steps = {s["id"] for s in g["steps"]}
    dsets = {d["id"] for d in g["datasets"]}
    for s in g["steps"]:
        if not STEP_ID_RE.match(s["id"]):
            errs.append(f"step id {s['id']}")
        if s.get("scope") not in ("migrate", "keep_legacy", "drop"):
            errs.append(f"step {s['id']} scope")
    for d in g["datasets"]:
        if not DS_ID_RE.match(d["id"]):
            errs.append(f"dataset id {d['id']}")
        if d["role"] not in ("input", "intermediate", "final", "unused"):
            errs.append(f"dataset {d['id']} role {d['role']}")
        lp = (d.get("legacy") or {}).get("path")
        if lp and (os.path.isabs(lp) or "\\" in lp):
            errs.append(f"dataset {d['id']} legacy path")
    producers = defaultdict(set)
    adj = defaultdict(list)
    for e in g["edges"]:
        k, f, t = e["kind"], e["from"], e["to"]
        if not e.get("evidence"):
            errs.append(f"edge {e['id']} without evidence")
        if k in ("reads", "reads_grid") and not (f in dsets and t in steps):
            errs.append(f"edge {e['id']}: {k} dataset->step")
        if k in ("writes", "reads_write"):
            if not (f in steps and t in dsets):
                errs.append(f"edge {e['id']}: {k} step->dataset")
            producers[t].add(f)
        if k in ("reads", "reads_grid", "writes", "reads_write"):
            adj[f].append(t)
        if k == "equivalent_to" and not (f in dsets and t in dsets):
            errs.append(f"edge {e['id']}: equivalent_to")
    for d, ps in producers.items():
        if len(ps) > 1:
            errs.append(f"dataset {d} written by several steps {sorted(ps)}")
    col = {}

    def dfs(n):
        col[n] = 1
        for m in adj.get(n, ()):
            if col.get(m) == 1:
                return True
            if not col.get(m) and dfs(m):
                return True
        col[n] = 2
        return False
    for n in sorted(adj):
        if not col.get(n) and dfs(n):
            errs.append("cycle in data flow")
            break
    for q in g["questions"]:
        nodes = [q["node"]] + q.get("nodes", [])
        if not all(n in steps or n in dsets for n in nodes):
            errs.append(f"question {q['id']} links an unknown node")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="lineage.py", description="Legacy lineage -> graph draft -> graph.py init.")
    ap.add_argument("migration", type=Path)
    ap.add_argument("--draft-only", action="store_true", help="write .graph/draft.json; do not call graph.py init")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    mig = a.migration.resolve()
    if not (mig / "inventory.json").exists():
        print("inventory.json not found: run scan.py first", file=sys.stderr)
        return 2
    lin = Lineage(mig)
    g = lin.build()
    errs = validate_draft(g)
    draft_p = mig / ".graph" / "draft.json"
    ac.dump_json(draft_p, g)
    summary = {"draft": str(draft_p.relative_to(mig)), "steps": len(g["steps"]), "datasets": len(g["datasets"]),
               "edges": len(g["edges"]), "questions": len(g["questions"]),
               "checks": {k: len(v) for k, v in g["lineage_checks"].items()},
               "pairs": sum(1 for d in g["datasets"] if d["role"] in ("intermediate", "final")
                            and d["legacy"]["exists"] and any(e["to"] == d["id"] and e["kind"] in ("writes",
                                                                                                    "reads_write")
                                                              and next((s for s in g["steps"] if s["id"] == e["from"]),
                                                                       {}).get("scope") == "migrate"
                                                              for e in g["edges"])),
               "errors": errs, "graph": None}
    code = 0
    if errs:
        code = 2
    elif not a.draft_only:
        gp = HERE / "graph.py"
        if (mig / "graph.json").exists():
            summary["graph"] = "exists: later changes go through graph.py apply"
            code = 3
        elif gp.exists():
            r = subprocess.run([sys.executable, str(gp), "init", str(mig), "--from", str(draft_p)],
                               capture_output=True, text=True)
            summary["graph"] = (r.stdout.strip() or r.stderr.strip())[-2000:]
            code = 0 if r.returncode == 0 else 2
        else:
            summary["graph"] = "graph.py not found: draft validated and left in .graph/draft.json"
    if a.json:
        print(json.dumps(summary, sort_keys=True, indent=1))
    else:
        print(f"lineage: {summary['steps']} steps, {summary['datasets']} datasets, {summary['edges']} edges, "
              f"{summary['questions']} question(s), {summary['pairs']} legacy pairs; checks "
              + ", ".join(f"{k} {v}" for k, v in summary["checks"].items()))
        for q in g["questions"]:
            print(f"  {q['id']} [{q['impact']['level']}] {q['header']}: {q['node']}")
        for w in g["lineage_checks"]["warnings"]:
            print(f"  warning {w['check']}: {w.get('producer_output')} newer than {w.get('consumer_output')}")
        for e in errs:
            print("  INVALID: " + e, file=sys.stderr)
        if summary["graph"]:
            print("graph: " + summary["graph"])
    return code


if __name__ == "__main__":
    sys.exit(main())
