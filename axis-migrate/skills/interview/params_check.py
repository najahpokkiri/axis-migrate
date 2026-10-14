"""params_check.py — validate migration/params.yaml (schema, evidence, coverage, wiring) and decisions.md.

    axpy params_check.py <migration> [--stage investigate|wired] [--fill-used-by] [--json]

Stage `investigate` (before code exists): schema, resolution rules, evidence quotes checked verbatim against the cited
legacy lines, graph references, no question about a resolved parameter, budget, decisions.md format.
Stage `wired` (after migrate): additionally every parameter of an in-scope step has a reader, every reader calls
`ctx.param("<id>")` and lists the id in its PARAMS constant, and every `ctx.param` id in pipeline code exists here
(D3-5: answers must reach the code; a default is not wiring).

`--fill-used-by` rewrites each parameter's `used_by` from the pipeline AST (deterministic; never by hand).
Exit 0 = no blocking finding; 2 = blocking findings; 3 = checker error.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402

import yaml  # pinned env

KINDS = {"cell_size", "grid", "crs", "nodata", "resampling", "threshold", "distance", "join_key", "scale",
         "table_read", "manual_step", "branch", "dtype", "tool_option", "target", "other"}
RESOLUTIONS = {"resolved", "inferred", "unresolved"}
SOURCES = {"code", "notes", "project", "input_data", "reference_metadata", "fitted", "answer", "assumption",
           "agent_decision"}
STATUSES = {"confirmed", "assumed", "open"}
FLAGS = {"tuned_in_sample", "by_construction", "fitted_on_reference", "latent_legacy_bug", "contradiction", "policy",
         "global"}
CONFIDENCE = {"high", "medium", "low"}
METHODS = {"tool-default", "cross-script", "unit-cell", "fit", "rule-from-inputs", "header", "semantics"}
UNIT_REQUIRED = {"distance", "scale", "cell_size"}
# Decision parameters choose a code variant or a target; they are read by people and decisions, not by ctx.param.
NON_CODE_KINDS = {"manual_step", "branch", "target"}
PARAM_ID_RE = re.compile(r"^p\.[a-z0-9][a-z0-9_]*$")
REF_LINES_RE = re.compile(r"^(?P<path>[^:#]+?):(?P<a>\d+)(?:-(?P<b>\d+))?$")
DEC_HEAD_RE = re.compile(r"^## (D-\d{3}) · (.+?) · (\S+)\s*$")
KD_HEAD_RE = re.compile(r"^## (KD-\d{3}) · (.+?)\s*$")
YAML_BLOCK_RE = re.compile(r"```yaml\n(.*?)```", re.S)


class Findings:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, sev: str, code: str, where: str, msg: str):
        self.items.append({"severity": sev, "code": code, "where": where, "message": msg})

    def block(self, code, where, msg):
        self.add("block", code, where, msg)

    def warn(self, code, where, msg):
        self.add("warn", code, where, msg)

    @property
    def blocking(self):
        return [f for f in self.items if f["severity"] == "block"]


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def check_quote(legacy: Path, ref: str, quote: str | None, f: Findings, where: str) -> None:
    """A citation must point at a real file and, when it names lines and carries a quote, the quote must be there."""
    if ref.startswith(("answer:", "inventory:", "compare:", "decision:", "qgz:", "runs/", "graph:", "D-", "KD-")):
        return
    m = REF_LINES_RE.match(ref)
    path = m.group("path") if m else ref.split("#", 1)[0]
    p = (legacy / path)
    if not p.exists():
        f.block("EVIDENCE_FILE_MISSING", where, f"cited file {path!r} does not exist under the legacy root")
        return
    if not m or not quote:
        return
    a = int(m.group("a"))
    b = int(m.group("b") or a)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        f.block("EVIDENCE_UNREADABLE", where, f"{path}: {e}")
        return
    if a < 1 or b > len(lines) or a > b:
        f.block("EVIDENCE_LINES_OUT_OF_RANGE", where, f"{ref}: file has {len(lines)} lines")
        return
    text = _norm_ws(" ".join(lines[a - 1:b]))
    q = _norm_ws(quote.rstrip("…").rstrip("."))
    if q and q not in text:
        f.block("EVIDENCE_QUOTE_MISMATCH", where, f"quote {quote!r} is not in {ref}")


def load_params(mdir: Path) -> dict:
    p = mdir / "params.yaml"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError("params.yaml must be a mapping")
    return data


def parse_blocks(text: str, head_re: re.Pattern) -> list[dict]:
    """Split an append-only markdown record file into ({id, title, yaml}) blocks."""
    out = []
    chunks = re.split(r"(?m)^(?=## )", text)
    for ch in chunks:
        if not ch.startswith("## "):
            continue
        head = ch.splitlines()[0]
        m = head_re.match(head)
        ym = YAML_BLOCK_RE.search(ch)
        rec = {"head": head, "id": m.group(1) if m else None, "yaml": None, "error": None}
        if not m:
            rec["error"] = "heading does not match the required format"
        elif not ym:
            rec["error"] = "no fenced yaml block"
        else:
            try:
                rec["yaml"] = yaml.safe_load(ym.group(1))
            except yaml.YAMLError as e:
                rec["error"] = f"yaml: {e}"
        out.append(rec)
    return out


def _as_list(x) -> list:
    if x is None:
        return []
    return list(x) if isinstance(x, (list, tuple)) else [x]


def check_records(mdir: Path, param_ids: set, f: Findings) -> dict:
    out = {"decisions": [], "kds": []}
    dp = mdir / "decisions.md"
    if dp.exists():
        blocks = parse_blocks(dp.read_text(encoding="utf-8"), DEC_HEAD_RE)
        superseded = {str(s) for b in blocks if not b["error"] for s in _as_list((b["yaml"] or {}).get("supersedes"))}
        seen = []
        for b in blocks:
            w = f"decisions.md:{b['head'][:40]}"
            if b["error"]:
                if b["id"] and b["id"] in superseded:
                    f.warn("DECISION_FORMAT_SUPERSEDED", w, f"{b['error']} (superseded by a later entry)")
                else:
                    f.block("DECISION_FORMAT", w, f"{b['error']}; append a corrected entry with "
                                                  f"`supersedes: {b['id']}` (record.py decision …)")
                continue
            y = b["yaml"] or {}
            if y.get("id") != b["id"]:
                f.block("DECISION_ID_MISMATCH", w, f"yaml id {y.get('id')!r} != heading {b['id']!r}")
            if b["id"] in seen:
                f.block("DECISION_DUPLICATE", w, b["id"])
            if seen and b["id"] < seen[-1]:
                f.block("DECISION_ORDER", w, f"{b['id']} after {seen[-1]}")
            seen.append(b["id"])
            for pid in y.get("params") or []:
                if pid not in param_ids:
                    f.warn("DECISION_UNKNOWN_PARAM", w, f"{pid} not in params.yaml")
            out["decisions"].append(y)
    kp = mdir / "known-differences.md"
    if kp.exists():
        kblocks = parse_blocks(kp.read_text(encoding="utf-8"), KD_HEAD_RE)
        ksup = {str(s) for b in kblocks if not b["error"] for s in _as_list((b["yaml"] or {}).get("supersedes"))}
        for b in kblocks:
            w = f"known-differences.md:{b['head'][:40]}"
            if b["error"]:
                if b["id"] and b["id"] in ksup:
                    f.warn("KD_FORMAT_SUPERSEDED", w, f"{b['error']} (superseded by a later entry)")
                else:
                    f.block("KD_FORMAT", w, f"{b['error']}; append a corrected entry with `supersedes: {b['id']}` "
                                            f"(record.py kd …)")
                continue
            y = b["yaml"] or {}
            if y.get("id") != b["id"]:
                f.block("KD_ID_MISMATCH", w, f"yaml id {y.get('id')!r} != heading {b['id']!r}")
            if y.get("kind") not in {"normalisation", "exclusion", "accepted_change", "legacy_defect_preserved"}:
                f.block("KD_KIND", w, f"kind {y.get('kind')!r}")
            if y.get("status") not in {"proposed", "accepted", "withdrawn"}:
                f.block("KD_STATUS", w, f"status {y.get('status')!r}")
            out["kds"].append(y)
    return out


def pipeline_param_reads(pdir: Path) -> dict[str, dict]:
    """{module relpath: {"reads": set(ids), "declared": set(ids) | None, "literals": [values]}} from the AST."""
    out = {}
    if not pdir.is_dir():
        return out
    for py in sorted(pdir.rglob("*.py")):
        if py.name.startswith("_") or py.name == "__main__.py":
            continue
        rel = py.relative_to(pdir.parent).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        except SyntaxError:
            out[rel] = {"reads": set(), "declared": None, "literals": [], "parse_error": True}
            continue
        reads, declared, literals = set(), None, []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "param":
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    reads.add(node.args[0].value)
            elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets):
                if isinstance(node.value, (ast.List, ast.Tuple)):
                    declared = {e.value for e in node.value.elts if isinstance(e, ast.Constant)}
            elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                literals.append(node.value)
        out[rel] = {"reads": reads, "declared": declared, "literals": literals, "parse_error": False}
    return out


def check(mdir: Path, stage: str, fill_used_by: bool = False) -> tuple[Findings, dict]:
    f = Findings()
    graph = ac.load_json(mdir / "graph.json")
    legacy = ac.legacy_root(mdir, graph)
    steps = {s["id"]: s for s in graph.get("steps", [])}
    dsets = {d["id"] for d in graph.get("datasets", [])}
    questions = {q["id"]: q for q in graph.get("questions", [])}
    data = load_params(mdir)
    params = data.get("params") or []
    if data.get("version") is None:
        f.block("SCHEMA", "params.yaml", "missing version")
    if not isinstance(params, list):
        f.block("SCHEMA", "params.yaml", "params must be a list")
        params = []
    ids = [p.get("id") for p in params if isinstance(p, dict)]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        f.block("PARAM_DUPLICATE", dup, "id used twice")
    by_id = {p.get("id"): p for p in params if isinstance(p, dict)}

    for p in params:
        if not isinstance(p, dict):
            f.block("SCHEMA", "params.yaml", "a parameter entry is not a mapping")
            continue
        pid = p.get("id") or "?"
        if not PARAM_ID_RE.match(str(pid)):
            f.block("PARAM_ID", pid, "ids are p.<slug> (lowercase, digits, underscore)")
        for key in ("node", "kind", "resolution", "source", "status", "reason"):
            if p.get(key) in (None, ""):
                f.block("SCHEMA", pid, f"missing {key}")
        if "value" not in p:
            f.block("SCHEMA", pid, "missing value (use null for unresolved)")
        if p.get("node") and p["node"] not in steps:
            f.block("UNKNOWN_NODE", pid, f"node {p['node']!r} is not a graph step")
        if p.get("kind") and p["kind"] not in KINDS:
            f.block("ENUM", pid, f"kind {p['kind']!r}")
        if p.get("resolution") and p["resolution"] not in RESOLUTIONS:
            f.block("ENUM", pid, f"resolution {p['resolution']!r}")
        if p.get("source") and p["source"] not in SOURCES:
            f.block("ENUM", pid, f"source {p['source']!r}")
        if p.get("status") and p["status"] not in STATUSES:
            f.block("ENUM", pid, f"status {p['status']!r}")
        for fl in p.get("flags") or []:
            if fl not in FLAGS:
                f.block("ENUM", pid, f"flag {fl!r}")
        if p.get("kind") in UNIT_REQUIRED and not p.get("unit"):
            f.block("UNIT_MISSING", pid, f"kind {p['kind']} needs a unit")
        res, src, st = p.get("resolution"), p.get("source"), p.get("status")
        ev = p.get("evidence") or []
        if res in ("resolved", "inferred") and not ev:
            f.block("EVIDENCE_MISSING", pid, f"{res} needs at least one evidence item")
        for i, e in enumerate(ev):
            if not isinstance(e, dict) or not e.get("ref"):
                f.block("EVIDENCE_FORMAT", pid, f"evidence[{i}] needs ref (+ quote for code/notes lines)")
                continue
            q = e.get("quote")
            if q and len(q) > 160:
                f.warn("EVIDENCE_QUOTE_LONG", pid, "quotes are at most ~120 characters")
            check_quote(legacy, e["ref"], q, f, f"{pid}.evidence[{i}]")
        if res == "resolved" and st != "confirmed":
            f.block("RESOLUTION_STATUS", pid, "resolved parameters are confirmed")
        if res == "inferred":
            if p.get("confidence") not in CONFIDENCE:
                f.block("INFERRED_CONFIDENCE", pid, "inferred needs confidence high|medium|low")
            if not p.get("method"):
                f.block("INFERRED_METHOD", pid, "inferred needs a method")
        if res == "unresolved":
            if st != "open" and src not in ("answer", "assumption"):
                f.block("UNRESOLVED_STATUS", pid, "unresolved and not answered must stay open")
            if st == "open" and not p.get("question"):
                f.warn("UNRESOLVED_NO_QUESTION", pid, "open unresolved parameter has no question yet")
        if src == "fitted":
            fit = p.get("fit") or {}
            for key in ("reference", "candidates", "metric", "split", "spec_sha256"):
                if not fit.get(key):
                    f.block("FIT_INCOMPLETE", pid, f"fitted parameter needs fit.{key} (declared before fitting)")
            if len(fit.get("candidates") or []) > 12:
                f.block("FIT_CANDIDATES", pid, "at most 12 candidates")
        if src == "answer":
            qid = p.get("question")
            q = questions.get(qid)
            if not q or q.get("status") not in ("answered",):
                f.block("ANSWER_NOT_FOUND", pid, f"source answer but question {qid!r} is not answered in graph.json")
        if src == "assumption" and st != "assumed":
            f.block("ASSUMPTION_STATUS", pid, "an assumption has status assumed until confirm_all accepts it")
        for ds in (p.get("impact") or {}).get("datasets") or []:
            if ds not in dsets:
                f.block("UNKNOWN_DATASET", pid, f"impact dataset {ds!r} not in graph")

    for s in steps.values():
        for pid in s.get("params") or []:
            if pid not in by_id:
                f.block("GRAPH_PARAM_MISSING", s["id"], f"graph step lists {pid} but params.yaml has no entry")

    # never ask what the files answer (D3-1, AT2)
    for q in questions.values():
        if q.get("status") in ("open", "answered") and q.get("kind") not in ("confirm_all", "target"):
            for pid in q.get("param_ids") or []:
                p = by_id.get(pid)
                if p and p.get("resolution") == "resolved" and p.get("source") not in ("answer", "assumption"):
                    f.block("QUESTION_ON_RESOLVED", q["id"], f"asks about {pid}, which the files resolve")

    budget = data.get("budget") or {}
    if budget.get("max_questions") is not None and (budget.get("asked") or 0) > budget["max_questions"]:
        f.block("BUDGET_EXCEEDED", "budget", f"asked {budget.get('asked')} > max {budget['max_questions']}")

    records = check_records(mdir, set(by_id), f)

    reads = pipeline_param_reads(mdir / "pipeline")
    summary = {"params": len(by_id), "modules": sorted(reads), "stage": stage}
    if fill_used_by:
        for pid, p in by_id.items():
            users = sorted(m for m, r in reads.items() if pid in r["reads"])
            extra = [u for u in (p.get("used_by") or []) if not str(u).startswith("pipeline/")]
            p["used_by"] = users + sorted(set(extra))
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120)
        ac.atomic_write_text(mdir / "params.yaml", text)
    if stage == "wired":
        for rel, r in reads.items():
            if r.get("parse_error"):
                f.block("MODULE_PARSE", rel, "module does not parse")
                continue
            for pid in sorted(r["reads"]):
                if pid not in by_id:
                    f.block("PARAM_UNKNOWN", rel, f"ctx.param({pid!r}) has no params.yaml entry")
                elif r["declared"] is not None and pid not in r["declared"]:
                    f.block("PARAM_NOT_DECLARED", rel, f"{pid} read but missing from PARAMS")
                elif rel not in (by_id[pid].get("used_by") or []):
                    f.block("USED_BY_STALE", pid, f"{rel} reads it but used_by does not list it (run --fill-used-by)")
        for pid, p in by_id.items():
            step = steps.get(p.get("node"))
            if not step or step.get("scope") != "migrate" or p.get("kind") in NON_CODE_KINDS:
                continue
            users = [u for u in (p.get("used_by") or [])]
            code_users = [u for u in users if str(u).startswith("pipeline/")]
            if not users:
                f.block("PARAM_UNWIRED", pid, "no reader: an answer that reaches no code changes nothing (D3-5)")
            for u in code_users:
                r = reads.get(u)
                if r is None:
                    f.block("USED_BY_MISSING_FILE", pid, f"{u} does not exist")
                elif pid not in r["reads"]:
                    f.block("PARAM_UNWIRED", pid, f"{u} is listed as a reader but never calls ctx.param({pid!r})")
                elif isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool) \
                        and abs(p["value"]) >= 10 and p["value"] in r["literals"]:
                    f.warn("PARAM_LITERAL", pid, f"{u} also contains the literal {p['value']!r}")
    summary["decisions"] = len(records["decisions"])
    summary["known_differences"] = len(records["kds"])
    return f, summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("migration")
    ap.add_argument("--stage", choices=["investigate", "wired"], default="investigate")
    ap.add_argument("--fill-used-by", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        f, summary = check(Path(a.migration).resolve(), a.stage, a.fill_used_by)
    except Exception as e:  # checker fault, never a pass
        print(f"params_check: CHECKER_ERROR {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc()
        return 3
    if a.json:
        print(json.dumps({"summary": summary, "findings": f.items, "ok": not f.blocking}, indent=1, sort_keys=True))
    else:
        for it in f.items:
            print(f"{it['severity'].upper():5} {it['code']:28} {it['where']}: {it['message']}")
        print(f"params_check ({a.stage}): {summary['params']} params, {len(f.blocking)} blocking, "
              f"{len(f.items) - len(f.blocking)} warnings")
    return 2 if f.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
