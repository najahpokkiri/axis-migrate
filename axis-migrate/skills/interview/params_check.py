"""params_check.py — validate migration/params.yaml (schema, evidence, coverage, wiring) and decisions.md.

    axpy params_check.py <migration> [--stage investigate|wired] [--fill-used-by] [--json]

Stage `investigate` (before code exists): schema, resolution rules, evidence quotes checked verbatim against the cited
legacy lines, graph references, no question about a resolved parameter, decisions.md format. The question budget is a
soft limit: `asked > max_questions` is the warning BUDGET_EXCEEDED (each extra question must still meet the score rule),
never a block — past the budget an unasked parity-blind item would otherwise become an `assumed` guess that parity
cannot see (the ladder caps those: ASSUMED_PARITY_BLIND, `ladder.is_parity_blind`; warned here too).
Stage `wired` (after migrate): additionally every parameter of an in-scope step has a reader, every reader calls
`ctx.param("<id>")` and lists the id in its PARAMS constant, and every `ctx.param` id in pipeline code exists here
(D3-5: answers must reach the code; a default is not wiring).
Both stages apply the runtime's refusal rule (skills/migrate/runtime/_axis_runtime.py `ctx.param` → PARAM_MISSING, run
exit 12): an answered or assumed parameter may not stay `resolution: unresolved` (PARAM_UNRESOLVED_AFTER_ANSWER), and
in `wired` every id the code reads must be in its graph step's `params`, have a `value` key and not be unresolved
(PARAM_MISSING_AT_RUN). A check that passes here must not be refused by the run.

`--fill-used-by` rewrites each parameter's `used_by` from the pipeline AST (deterministic; never by hand).
Exit 0 = no blocking finding; 2 = blocking findings; 3 = checker error.
"""
from __future__ import annotations

import argparse
import ast
import html
import json
import re
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import axis_common as ac  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "report"))
import ladder  # noqa: E402  (stdlib; the one definition of parity-blind)

import yaml  # pinned env

KINDS = {"cell_size", "grid", "crs", "nodata", "resampling", "threshold", "distance", "join_key", "scale",
         "table_read", "manual_step", "branch", "dtype", "tool_option", "target", "other"}
RESOLUTIONS = {"resolved", "inferred", "unresolved"}
SOURCES = {"code", "notes", "project", "input_data", "reference_metadata", "fitted", "answer", "assumption",
           "agent_decision"}
STATUSES = {"confirmed", "assumed", "open"}
FLAGS = {"tuned_in_sample", "by_construction", "fitted_on_reference", "latent_legacy_bug", "contradiction", "policy",
         "global", "parity_blind"}
IMPACT_LEVELS = {"high", "medium", "low"}
CONFIDENCE = {"high", "medium", "low"}
METHODS = {"tool-default", "cross-script", "unit-cell", "fit", "rule-from-inputs", "header", "semantics",
           "assumption"}
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
    # XML sources (.model3, .qgs, .aprx): a quote may be written decoded ("x") or raw (&quot;x&quot;)
    if q and q not in text and q not in _norm_ws(html.unescape(text)):
        f.block("EVIDENCE_QUOTE_MISMATCH", where, f"quote {quote!r} is not in {ref}")


def load_params(mdir: Path, text: str | None = None) -> dict:
    p = mdir / "params.yaml"
    if text is None:
        if not p.exists():
            raise FileNotFoundError(f"{p} missing")
        text = p.read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
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


def check_records(mdir: Path, param_ids: set, f: Findings, decisions_text: str | None = None) -> dict:
    out = {"decisions": [], "kds": []}
    dp = mdir / "decisions.md"
    if decisions_text is None and dp.exists():
        decisions_text = dp.read_text(encoding="utf-8")
    if decisions_text is not None:
        blocks = parse_blocks(decisions_text, DEC_HEAD_RE)
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


REF_SPAN_RE = re.compile(r"^(?P<path>.+?):(?P<a>\d+)(?:-(?P<b>\d+))?$")


def check_coverage(mdir: Path, graph: dict, data: dict, f: Findings) -> None:
    """PARAM_COVERAGE: every tool setting and literal the scan found inside an in-scope step's legacy code (inventory
    `settings[]`, and `params[]` other than plain assignments) is either cited by a params.yaml entry (an evidence ref
    `file:line[-line]` covering its line, or `inventory:<id>`) or waived in `not_output_changing` with a reason
    (`record.py waive`). Otherwise an output-changing setting (a data type, a CRS, a formula constant) could be left
    out and the check would still pass. One finding per step, naming the uncovered ids."""
    inv = ac.load_json(mdir / "inventory.json") if (mdir / "inventory.json").is_file() else {}
    if not inv:
        return
    spans: dict[str, list[tuple[int, int]]] = {}
    step_of: dict[str, list[tuple[int, int, str]]] = {}
    for st in graph.get("steps") or []:
        if st.get("scope") not in ("migrate", "keep_legacy"):
            continue
        leg = st.get("legacy") or {}
        if not leg.get("source"):
            continue
        for ln in leg.get("lines") or [[1, 10 ** 9]]:
            a, b = int(ln[0]), int(ln[-1])
            spans.setdefault(leg["source"], []).append((a, b))
            step_of.setdefault(leg["source"], []).append((a, b, st["id"]))
    cited: dict[str, list[tuple[int, int]]] = {}
    cited_ids: set[str] = set()
    for p in data.get("params") or []:
        for e in (p.get("evidence") or []) if isinstance(p, dict) else []:
            ref = str((e or {}).get("ref") or "")
            if ref.startswith("inventory:"):
                cited_ids.add(ref.split(":", 1)[1])
                continue
            m = REF_SPAN_RE.match(ref)
            if m:
                cited.setdefault(m.group("path"), []).append((int(m.group("a")), int(m.group("b") or m.group("a"))))
    waived = {str(w.get("id")) for w in data.get("not_output_changing") or []
              if isinstance(w, dict) and str(w.get("reason") or "").strip()}
    items = list(inv.get("settings") or []) + [x for x in inv.get("params") or [] if x.get("kind") != "assignment"]
    known = {str(it.get("id")) for it in items}
    for w in data.get("not_output_changing") or []:
        wid = str((w or {}).get("id")) if isinstance(w, dict) else str(w)
        if not isinstance(w, dict) or not str(w.get("reason") or "").strip():
            f.block("SCHEMA", "not_output_changing", f"{wid}: each waiver needs an id and a reason")
        elif wid not in known:
            f.block("WAIVER_UNKNOWN", "not_output_changing", f"{wid} is not a setting or literal in inventory.json")
    missing: dict[str, list[str]] = {}
    for it in items:
        fp, ln, iid = it.get("file"), it.get("line"), str(it.get("id"))
        if not fp or not isinstance(ln, int) or it.get("from_superseded") or iid in cited_ids or iid in waived:
            continue
        owners = [sid for a, b, sid in step_of.get(fp, []) if a <= ln <= b]
        if not owners or any(a <= ln <= b for a, b in cited.get(fp, [])):
            continue
        missing.setdefault(owners[0], []).append(iid)
    for sid, ids in sorted(missing.items()):
        shown = ", ".join(ids[:8]) + (f" +{len(ids) - 8} more" if len(ids) > 8 else "")
        f.block("PARAM_COVERAGE", sid, f"{len(ids)} setting(s)/literal(s) in the legacy code with no params.yaml entry "
                                       f"citing them and no waiver: {shown}. Cite each output-changing one in a "
                                       "parameter (record.py param); waive the rest with a reason (record.py waive)")


def check(mdir: Path, stage: str, fill_used_by: bool = False,
          overrides: dict[str, str] | None = None) -> tuple[Findings, dict]:
    """`overrides` = {"params.yaml": text, "decisions.md": text}: check proposed contents without writing them
    (record.py answer validates its change this way before it touches either file)."""
    overrides = overrides or {}
    f = Findings()
    if mdir.is_dir() and not (mdir / "graph.json").exists():
        f.block("NO_GRAPH", "graph.json", "no workflow graph yet: run lineage.py, then graph.py init (skill discover)")
        return f, {"params": 0, "modules": [], "stage": stage}
    graph = ac.load_json(mdir / "graph.json")
    legacy = ac.legacy_root(mdir, graph)
    steps = {s["id"]: s for s in graph.get("steps", [])}
    dsets = {d["id"] for d in graph.get("datasets", [])}
    questions = {q["id"]: q for q in graph.get("questions", [])}
    data = load_params(mdir, overrides.get("params.yaml"))
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
            elif st != "open" or src in ("answer", "assumption"):
                # the runtime's rule (_axis_runtime.ctx.param): resolution unresolved → PARAM_MISSING, run exit 12
                f.block("PARAM_UNRESOLVED_AFTER_ANSWER", pid,
                        f"{src}/{st} but resolution is still unresolved: the run refuses it (PARAM_MISSING, exit 12). "
                        "Record answers with record.py answer (it sets the resolution); fix with record.py param")
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
        lvl = (p.get("impact") or {}).get("level")
        if lvl is not None and lvl not in IMPACT_LEVELS:
            f.block("ENUM", pid, f"impact.level {lvl!r} (high|medium|low)")
        if st == "assumed" and lvl is None:
            f.warn("ASSUMED_NO_IMPACT_LEVEL", pid, "assumed with no impact.level: the ladder cannot tell whether "
                                                   "parity can catch a wrong guess (set impact.level)")
        for ds in (p.get("impact") or {}).get("datasets") or []:
            if ds not in dsets:
                f.block("UNKNOWN_DATASET", pid, f"impact dataset {ds!r} not in graph")

    for p in params:
        if isinstance(p, dict) and ladder.is_assumed(p) and not ladder.is_fitted(p) \
                and ladder.is_parity_blind(p, list(questions.values())):
            f.warn("ASSUMED_PARITY_BLIND", p.get("id") or "?",
                   "assumed, output-changing and parity-blind: caps its steps at sandbox-validated until a person "
                   "confirms it (its own question, even past the budget, or confirm_all)")

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
        f.warn("BUDGET_EXCEEDED", "budget", f"asked {budget.get('asked')} > max {budget['max_questions']}: a soft "
                                            "limit; ask past it only items with score >= 12 (impact x uncertainty x "
                                            "cost), parity-blind ones first")

    check_coverage(mdir, graph, data, f)

    records = check_records(mdir, set(by_id), f, overrides.get("decisions.md"))

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
        step_of_module = {Path(str((s.get("new") or {}).get("module") or "")).name: sid for sid, s in steps.items()
                          if (s.get("new") or {}).get("module")}
        for rel, r in reads.items():
            if r.get("parse_error"):
                f.block("MODULE_PARSE", rel, "module does not parse")
                continue
            sid = step_of_module.get(Path(rel).name)
            for pid in sorted(r["reads"]):
                # the same three refusals as the runtime's ctx.param (PARAM_MISSING, run exit 12)
                if sid is not None and pid not in (steps[sid].get("params") or []):
                    f.block("PARAM_MISSING_AT_RUN", rel, f"ctx.param({pid!r}): {pid} is not in graph step {sid}'s "
                                                         "params, so the runtime refuses it (PARAM_MISSING)")
                if pid in by_id and ("value" not in by_id[pid]
                                     or str(by_id[pid].get("resolution", "")).lower() == "unresolved"):
                    f.block("PARAM_MISSING_AT_RUN", rel, f"ctx.param({pid!r}): {pid} is unresolved or has no value, "
                                                         "so the runtime refuses it (PARAM_MISSING, exit 12)")
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
