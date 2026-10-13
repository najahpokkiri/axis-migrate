#!/usr/bin/env python3
"""H7 Stop gate (08 §5b, D8-8; DESIGN §4.2 for unacked graph edits). Reads files only, never runs compare.

1. No migration/ -> allow. Status from ladder.compute() (newest sealed run); ladder unavailable -> rung "none".
2. Live re-checks against the run's snapshot: params.yaml, tolerances.yaml, pipeline/ tree; legacy tree vs the
   inventory baseline (a new .aux.xml counts); for claims >= reference-verified also compare files present and
   bound to the run's outputs, and a report/evidence for this run newer than the compares; open high-impact questions.
3. Claims in last_assistant_message (claims.py); negated ones and the quoted status line are ignored.
4. Block iff a claim needs a rung above the computed one, or (2) found drift/missing evidence and a claim is present.
   Unacked user graph edits block regardless of claims (DESIGN §4.2, D2-14).
On the 3rd consecutive block with stop_hook_active: allow and tell the user (systemMessage).
`onFailure` does not apply to Stop, so errors are handled here: with a claim present -> block; otherwise allow.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402


class _Lazy:
    def __init__(self, mod):
        self._mod = mod

    def __getattr__(self, name):
        import importlib
        return getattr(importlib.import_module(self._mod), name)


cl = _Lazy("claims")
ac = _Lazy("axis_common")
STATE_REL = ".axis/stop_gate.json"
LEGACY_HASH_BUDGET_BYTES = 2 * 1024 ** 3


# ---------------------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------------------
def last_message(data: dict) -> str:
    msg = data.get("last_assistant_message")
    if isinstance(msg, str):
        return msg
    tp = data.get("transcript_path")
    if not tp or not os.path.isfile(tp):
        return ""
    text = ""
    with open(tp, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            m = rec.get("message") if isinstance(rec, dict) else None
            if rec.get("type") == "assistant" and isinstance(m, dict):
                parts = m.get("content")
                if isinstance(parts, list):
                    t = "\n".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")
                    if t.strip():
                        text = t
                elif isinstance(parts, str) and parts.strip():
                    text = parts
    return text


def ladder_status(mig: Path) -> dict:
    try:
        sys.path.insert(0, str(hl.PLUGIN / "skills" / "report"))
        import ladder  # type: ignore
        res = ladder.compute(str(mig))
        if not isinstance(res, dict):
            raise TypeError("ladder.compute returned no dict")
        res.setdefault("_source", "ladder")
        return res
    except ImportError:
        return {"pipeline": {"rung": "none"}, "status_line": "axis-migrate status: unknown (ladder unavailable)",
                "run_id": newest_run(mig), "_source": "unavailable"}
    except Exception as e:  # noqa: BLE001 - no status = nothing supports a claim
        return {"pipeline": {"rung": "none"},
                "status_line": f"axis-migrate status: unknown (ladder failed: {e.__class__.__name__})",
                "run_id": newest_run(mig), "_source": "error"}


def newest_run(mig: Path) -> str | None:
    idx = mig / "runs" / "index.jsonl"
    if not idx.is_file():
        return None
    rid = None
    for line in idx.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("evidence_id") or rec.get("sealed"):
            rid = rec.get("run_id") or rid
    return rid


# ---------------------------------------------------------------------------------------------------------------
# Live re-checks (step 2)
# ---------------------------------------------------------------------------------------------------------------
def _sha(p: Path) -> str | None:
    return ac.sha256_file(p) if p.is_file() else None


def drift(mig: Path, run_id: str | None) -> list[str]:
    out = []
    if not run_id:
        return out
    snap = mig / "runs" / run_id / "snapshot"
    if snap.is_dir():
        for name, code in (("params.yaml", "PARAMS_CHANGED_AFTER_RUN"), ("tolerances.yaml",
                                                                        "TOLERANCE_CHANGED_AFTER_RUN")):
            if (snap / name).exists() or (mig / name).exists():
                if _sha(snap / name) != _sha(mig / name):
                    out.append(code)
        sp = snap / "axis_pipeline"
        if sp.is_dir() and (mig / "pipeline").is_dir():
            if ac.tree_entries(sp) != ac.tree_entries(mig / "pipeline"):
                out.append("CODE_CHANGED_AFTER_RUN")
    out += legacy_drift(mig)
    return out


def legacy_drift(mig: Path) -> list[str]:
    inv_p = mig / "inventory.json"
    if not inv_p.is_file():
        return []
    inv = ac.load_json(inv_p)
    g = hl.load_graph(mig)
    root = ac.legacy_root(mig, g)
    base = {f.get("path"): f for f in inv.get("files") or [] if f.get("path")}
    if not base or not root.is_dir():
        return ["LEGACY_REF_CHANGED (legacy folder missing)"] if base else []
    live = {}
    total = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if d not in ("__pycache__", ".git"))
        for fn in fns:
            p = Path(dp) / fn
            rel = p.relative_to(root).as_posix()
            live[rel] = p
            total += p.stat().st_size
    added = sorted(set(live) - set(base))
    missing = sorted(set(base) - set(live))
    changed = []
    for rel in sorted(set(base) & set(live)):
        b = base[rel]
        p = live[rel]
        if b.get("size") is not None and p.stat().st_size != b.get("size"):
            changed.append(rel)
        elif total <= LEGACY_HASH_BUDGET_BYTES and b.get("sha256") and ac.sha256_file(p) != b.get("sha256"):
            changed.append(rel)
    if added or missing or changed:
        ex = (added + missing + changed)[:3]
        return [f"LEGACY_REF_CHANGED ({len(added)} new, {len(missing)} missing, {len(changed)} changed: "
                f"{', '.join(ex)})"]
    return []


def in_scope_compared(g: dict) -> list[str]:
    steps = {s.get("id"): s for s in g.get("steps") or []}
    out = []
    for d in g.get("datasets") or []:
        leg = d.get("legacy") or {}
        if not leg.get("path") or leg.get("exists") is False:
            continue
        if d.get("role") not in ("intermediate", "final"):
            continue
        if (d.get("reference") or {}).get("source") == "none" or d.get("no_reference_reason"):
            continue
        pb = d.get("produced_by") or {}
        st = steps.get(pb.get("step") if isinstance(pb, dict) else pb) or {}
        if st.get("scope", "migrate") != "migrate" or st.get("kind") in ("bridge", "manual"):
            continue
        out.append(d.get("id"))
    return sorted(x for x in out if x)


def missing_evidence(mig: Path, run_id: str | None) -> list[str]:
    if not run_id:
        return ["no sealed run"]
    rd = mig / "runs" / run_id
    out = []
    g = hl.load_graph(mig)
    man = {}
    if (rd / "manifest.json").is_file():
        man = ac.load_json(rd / "manifest.json")
    out_sha = {o.get("dataset_id"): o.get("sha256") for o in man.get("outputs") or []}
    newest_cmp = 0.0
    missing, unbound = [], []
    for ds in in_scope_compared(g):
        f = rd / "verify" / "compare" / f"{ds}.json"
        if not f.is_file():
            missing.append(ds)
            continue
        newest_cmp = max(newest_cmp, f.stat().st_mtime)
        try:
            ref_out = ((ac.load_json(f).get("refs") or {}).get("out"))
        except ValueError:
            ref_out = None
        if ref_out and out_sha.get(ds) and ref_out != f"out:{out_sha[ds]}":
            unbound.append(ds)
    if missing:
        out.append(f"NOT_COMPARED: {len(missing)} in-scope legacy pair(s) without a compare file ({', '.join(missing[:3])})")
    if unbound:
        out.append(f"UNBOUND_COMPARE: {', '.join(unbound[:3])}")
    ev_id = None
    for cand in (rd / "verify" / "EVIDENCE_ID", rd / "EVIDENCE_ID"):
        if cand.is_file():
            ev_id = cand.read_text(encoding="utf-8").strip()
            break
    if not ev_id:
        out.append("EVIDENCE_FILE_MISSING: run not sealed (no EVIDENCE_ID)")
    rep = mig / "report" / "index.html"
    rj = mig / "report" / "REPORT.json"
    if not rep.is_file() or not rj.is_file():
        out.append("report missing (run report.py)")
    else:
        try:
            r = ac.load_json(rj)
        except ValueError:
            r = {}
        if ev_id and r.get("evidence_id") != ev_id:
            out.append("report is for another evidence id (re-run report.py)")
        if newest_cmp and rep.stat().st_mtime < newest_cmp:
            out.append("report older than the compare results (re-run report.py)")
    return out


def open_high_impact(g: dict) -> list[str]:
    out = []
    for q in g.get("questions") or []:
        st = q.get("status") or q.get("state")
        imp = q.get("impact")
        level = imp.get("level") if isinstance(imp, dict) else imp
        if st == "open" and level == "high":
            out.append(q.get("id") or "?")
    return out


def unacked(mig: Path) -> tuple[int, int]:
    """(count of unacked user edits, highest seq)."""
    try:
        sys.path.insert(0, str(hl.PLUGIN / "skills" / "discover"))
        import graph as gmod  # type: ignore
        res = gmod.unacked_user_edits(str(mig))
        if isinstance(res, (list, tuple)):
            seqs = [int(r.get("seq", 0)) for r in res if isinstance(r, dict)]
            return len(res), max(seqs) if seqs else 0
        return int(res), 0
    except Exception:  # noqa: BLE001 - fall back to the edit log
        g = hl.load_graph(mig)
        acked = int(((g.get("acks") or {}).get("agent_seq")) or 0)
        log = mig / "graph-edits.jsonl"
        n, top = 0, 0
        if log.is_file():
            for line in log.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                seq = int(rec.get("seq") or 0)
                if rec.get("actor") == "user" and seq > acked:
                    n += 1
                    top = max(top, seq)
        return n, top


# ---------------------------------------------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------------------------------------------
def counts(res: dict) -> str:
    c = (res.get("pipeline") or {}).get("counts") or {}
    def g(*keys):
        for k in keys:
            if k in c:
                return c[k]
        return "?"
    return (f"{g('pass', 'passed', 'PASS')} pass, {g('fail', 'FAIL', 'failed')} FAIL, "
            f"{g('not_compared', 'NOT_COMPARED')} NOT_COMPARED, {g('needs_review', 'review', 'NEEDS_REVIEW')} "
            "needs review")


def first_failing(res: dict, g: dict) -> str:
    steps = res.get("steps") or {}
    order = [s.get("id") for s in g.get("steps") or []] or sorted(steps)
    bad = {"FAILED", "BLOCKED", "EVALUATOR_ERROR", "NEEDS_DECISION", "NEEDS_REVIEW", "NOT_COMPARED",
           "EVIDENCE_INCOMPLETE"}
    for sid in order:
        st = steps.get(sid) or {}
        if st.get("disposition") in bad:
            caps = st.get("caps") or st.get("annotations") or []
            diag = "; ".join(_fmt(c) for c in caps[:2]) if caps else ""
            return f"{sid} {st.get('disposition')} {diag}".strip()
    blockers = (res.get("pipeline") or {}).get("blockers") or []
    return _fmt(blockers[0]) if blockers else "none recorded"


def _fmt(x) -> str:
    if isinstance(x, dict):
        parts = [str(x.get(k)) for k in ("step", "dataset", "code", "reason", "detail") if x.get(k)]
        return " ".join(parts) or json.dumps(x, sort_keys=True)[:120]
    return str(x)


def block_text(quote: str, rung: str, res: dict, g: dict, problems: list[str]) -> str:
    run_id = res.get("run_id") or "none"
    status = res.get("status_line") or f"axis-migrate status: {rung}"
    return hl.ascii_text(
        f'axis-migrate Stop gate: your message says "{quote}" but the computed status is {rung}\n'
        f"(run {run_id}: {counts(res)}; drift: {', '.join(problems) if problems else 'none'}).\n"
        f"First failing step: {first_failing(res, g)}.\n"
        "Rewrite the summary: start with the status line below, list every failing or uncompared step, and do not "
        "claim more.\n"
        "Do not edit tolerances, references, known differences or the graph to change this result.\n"
        f"Status line: {status}", 3000)


def load_state(mig: Path) -> dict:
    p = mig / STATE_REL
    try:
        return ac.load_json(p) if p.is_file() else {}
    except ValueError:
        return {}


def save_state(mig: Path, st: dict) -> None:
    try:
        ac.dump_json(mig / STATE_REL, st)
    except OSError:
        pass


def decide(data: dict, mig: Path) -> dict | None:
    text = last_message(data)
    found = cl.find_claims(text)
    try:
        res = ladder_status(mig)
        g = hl.load_graph(mig)
        rung = (res.get("pipeline") or {}).get("rung") or "none"
        n_unacked, top_seq = unacked(mig)
        reason = None
        quote = ""
        if n_unacked:
            reason = hl.ascii_text(
                f"axis-migrate Stop gate: the user edited the workflow graph ({n_unacked} unacked edit(s)). Run "
                "graph.py diff, act on each edit per the edit table (02 §4.5), then graph.py ack "
                f"{top_seq or '<seq>'} before ending the turn.\nStatus line: {res.get('status_line') or rung}")
            quote = "(unacked graph edits)"
        elif found:
            bad = cl.unsupported(found, rung)
            problems = drift(mig, res.get("run_id"))
            problems += [f"open high-impact question {q}" for q in open_high_impact(g)]
            top = cl.strongest(found)
            if cl.rung_index(top.rung) >= cl.rung_index("reference-verified"):
                problems += missing_evidence(mig, res.get("run_id"))
            if bad or problems:
                c = cl.strongest(bad) if bad else top
                quote = cl.quote_context(text, c)
                reason = block_text(quote, rung, res, g, problems)
    except Exception as e:  # noqa: BLE001 - Stop has no onFailure: decide here
        if not found:
            return None
        quote = cl.quote_context(text, cl.strongest(found))
        rung = "unknown"
        reason = hl.ascii_text(f'axis-migrate Stop gate: your message says "{quote}" but the status could not be '
                               f"computed ({e.__class__.__name__}: {e}). Do not claim completion; say the status "
                               "is unknown and run ladder.py.")
    if reason is None:
        return None
    return {"reason": reason, "quote": quote, "rung": rung}


def main() -> int:
    data = hl.read_input()
    mig = hl.find_migration(data)
    if mig is None:
        return 0
    mig = mig.resolve()
    sid = str(data.get("session_id") or "default")
    st = load_state(mig)
    n = int((st.get(sid) or {}).get("consecutive") or 0)
    d = decide(data, mig)
    if d is None:
        if n:
            st.pop(sid, None)
            save_state(mig, st)
        return 0
    if data.get("stop_hook_active") and n >= 2:
        st.pop(sid, None)
        save_state(mig, st)
        hl.emit({"systemMessage": hl.ascii_text(
            f"axis-migrate: the agent's last message claims '{d['quote']}'; computed status is {d['rung']}. "
            "The Stop gate blocked this three times and is letting the turn end; treat the claim as unsupported.")})
        return 0
    st[sid] = {"consecutive": (n + 1) if data.get("stop_hook_active") else 1, "at": int(time.time())}
    save_state(mig, st)
    hl.emit({"decision": "block", "reason": d["reason"]})
    return 0


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - malformed input etc.: block (fail closed) with an ASCII reason
        sys.stderr.write(hl.ascii_text(f"axis-migrate stop_gate failed ({e.__class__.__name__}: {e}); "
                                       "do not claim completion.") + "\n")
        sys.exit(2)
    sys.exit(0)
