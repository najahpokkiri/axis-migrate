#!/usr/bin/env python3
"""H3 PreToolUse Write|Edit|MultiEdit|NotebookEdit guard (08 §5c, §5g; DESIGN §5). Fails closed.

deny : legacy root (read-only), eval-hidden siblings, graph.json / graph-edits.jsonl / .graph/ (graph.py apply only),
       runs/** and runs/index.jsonl (runner/seal only), .axis/ (guard caches), inventory.json (scan.py only),
       report/** (report.py only), databricks/target-manifest.json|target-check.json (target scripts only),
       pipeline/sNN_*.py before the step's plan is approved in graph.json, any non-append change to
       decisions.md / known-differences.md, an edit of an existing tolerance record after a run recorded the file.
       tolerances.proposed.yaml content with a signed record (agreed_by/agreed_at non-null).
Codex apply_patch payloads (tool_input.command = patch text; Add/Update/Delete/Move, several files) get the same
rules per touched file; Update hunks on content-checked files are applied to the current text (unapplicable -> deny).
Under Codex an `ask` becomes a deny (Codex does not support ask; _hooklib.pre_decision).
ask  : a new tolerance record after a run; a tolerance record carrying agreed_by (only a human signs);
       a new KD block with owner: human:...; a params entry moving from fitted to source: answer.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402

STEP_MODULE_RE = re.compile(r"^[sm]\d\d[a-z]?_\w*\.py$")
TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


class _LazyRP:
    def __getattr__(self, name):
        import recordparse
        return getattr(recordparse, name)


rp = _LazyRP()


def proposed(tool: str, ti: dict, path) -> tuple[str, str] | None:
    """(current, proposed) text, or None when the edit cannot apply (the tool fails by itself)."""
    cur = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        return cur, rp.apply_edit_payload(tool, ti, cur)
    except rp.EditNotApplicable:
        return None


def check_append_only(rel: str, cur: str, new: str) -> tuple[str, str] | None:
    bad = rp.append_only_violations(cur, new)
    if bad:
        return ("deny", f"axis-migrate: {rel} is append-only (D3-13): {'; '.join(bad)}. Existing ## D-/KD- blocks "
                        "must stay byte-identical; append a new block (use `supersedes:` to correct an old one).")
    if rel == "known-differences.md":
        human = [b.id for b in rp.new_blocks(cur, new) if rp.has_human_owner(b.text)]
        if human:
            return ("ask", f"axis-migrate: {', '.join(human)} names a human owner (owner: human:...). A KD with a "
                           "human owner can turn a FAIL into ACCEPTED_DIFFERENCE, so only the user may add it. "
                           "Approve only if the user decided this difference.")
    return None


def _items(text: str, key: str) -> dict:
    return {i.id: i for i in (rp.yaml_list_items(text, key) or []) if i.id}


def check_tolerances(mig, cur: str, new: str) -> tuple[str, str] | None:
    cur_items, new_items = _items(cur, "records"), _items(new, "records")
    signed = sorted(i for i, it in new_items.items()
                    if rp.item_scalar(it, "agreed_by") and (i not in cur_items or cur_items[i].text != it.text))
    runs = hl.recorded_runs(mig)
    if not runs:
        if signed:
            return ("ask", f"axis-migrate: tolerances.yaml record(s) {', '.join(signed)} carry agreed_by. Only a "
                           "human signs a tolerance; approve only if the user agreed these numbers.")
        return None

    def strip_version(t: str) -> str:
        return "\n".join(l for l in rp.top_level_text(t, "records").splitlines() if not l.startswith("version:"))

    if strip_version(cur) != strip_version(new):
        return ("deny", "axis-migrate: tolerances.yaml defaults/top level changed after a run recorded its hash "
                        f"({runs[-1].name}). Tolerances are fixed before the run; propose a new versioned record.")
    changed = sorted(i for i in cur_items if i not in new_items or new_items[i].text != cur_items[i].text)
    if changed:
        return ("deny", f"axis-migrate: tolerance record(s) {', '.join(changed)} already existed when a run recorded "
                        f"tolerances.yaml ({runs[-1].name}); editing them is refused (TOLERANCE_CHANGED_AFTER_RUN). "
                        "Add a new record id (...v2) with a reason, then a new run is required.")
    added = sorted(i for i in new_items if i not in cur_items)
    if not added:
        return None
    no_reason = [i for i in added if not (rp.item_scalar(new_items[i], "reason")
                                          or rp.item_scalar(new_items[i], "decision_ref"))]
    missing = (f" Record(s) {', '.join(no_reason)} give no reason or decision_ref (08 §5c expects one)."
               if no_reason else "")
    return ("ask", f"axis-migrate: new tolerance record(s) {', '.join(added)} after run {runs[-1].name}. This widens "
                   "a tolerance after seeing results; the report will show it as widened and a new run is needed."
                   f"{missing} Approve only if the user agrees.")


def check_proposed_tolerances(new: str) -> tuple[str, str] | None:
    """migration/tolerances.proposed.yaml: unsigned proposals, writable any time; signatures are refused."""
    signed = []
    for it in rp.yaml_list_items(new, "records") or []:
        if rp.item_scalar(it, "agreed_by") or rp.item_scalar(it, "agreed_at"):
            signed.append(it.id or f"record at line {it.start_line + 1}")
    if signed:
        return ("deny", f"axis-migrate: tolerances.proposed.yaml record(s) {', '.join(signed)} carry agreed_by/agreed_at. "
                        "Only a person signs; proposals stay unsigned (agreed_by: null, agreed_at: null).")
    return None


def check_params(cur: str, new: str) -> tuple[str, str] | None:
    cur_items, new_items = _items(cur, "params"), _items(new, "params")
    lifted = []
    for pid, it in new_items.items():
        old = cur_items.get(pid)
        if old is None:
            continue
        fitted = (rp.item_scalar(old, "source") == "fitted" or "fitted_against" in old.fields
                  or rp.item_subtree(old, "fit").strip() not in ("", "null", "~"))
        if fitted and rp.item_scalar(old, "source") != "answer" and rp.item_scalar(it, "source") == "answer":
            lifted.append(pid)
    if lifted:
        return ("ask", f"axis-migrate: {', '.join(sorted(lifted))} change from a fitted value to source: answer. "
                       "That lifts the PARAM_FITTED_TO_REFERENCE cap and must be the user's confirmation. Approve "
                       "only if the user confirmed the value.")
    return None


def check_pipeline_module(mig, rel: str, path) -> tuple[str, str] | None:
    if not (rel.startswith("pipeline/") and rel.count("/") == 1 and STEP_MODULE_RE.match(path.name)):
        return None
    graph = hl.load_graph(mig)
    stem = path.stem
    step = None
    for s in graph.get("steps") or []:
        if s.get("id") == stem or (s.get("new") or {}).get("module") == rel:
            step = s
            break
    if step is None:
        return ("deny", f"axis-migrate: {rel} has no graph step (module name must be pipeline/<step id>.py, step "
                        "added through graph.py apply).")
    approved = (step.get("plan") or {}).get("approved")
    if not (isinstance(approved, dict) and approved.get("by") and approved.get("at")):
        return ("deny", f"axis-migrate: the plan for {step.get('id')} is not approved (graph.json "
                        "steps[].plan.approved). Present plan.md and get the user's approval before writing code.")
    return None


CONTENT_FILES = ("decisions.md", "known-differences.md", "tolerances.yaml", "params.yaml", "tolerances.proposed.yaml")


def check_target(mig: Path, ps, path: Path, content=None) -> tuple[str, str] | None:
    """All rules for one target path. `content` is a callable returning (current, proposed) text, or None when the
    change cannot be judged by content (the tool would fail by itself); it may raise rp.PatchError (fail closed)."""
    if ps.is_hidden(path):
        return ("deny", "axis-migrate: that path is grader material next to the legacy folder; never write it.")
    if ps.under_legacy(path):
        return ("deny", f"axis-migrate: the legacy folder is read-only (LEGACY_READ_ONLY): {path.name}. "
                        "Write exploration code and copies under migration/analysis/ instead.")
    try:
        rel = path.relative_to(mig).as_posix()
    except ValueError:
        return None
    if rel in ("graph.json", "graph-edits.jsonl") or rel.startswith(".graph/"):
        return ("deny", "axis-migrate: graph.json is written only through `graph.py apply` (D2-9); "
                        "use an ops file with graph.py apply --actor agent.")
    if rel == "runs" or rel.startswith("runs/"):
        return ("deny", "axis-migrate: migration/runs/ is written only by the runner and seal.py (sealed runs, "
                        "runs/index.jsonl). Start a new run with run_local.py instead.")
    if rel.startswith(".axis/"):
        return ("deny", "axis-migrate: migration/.axis/ holds guard state and is written by the scripts only.")
    if rel == "inventory.json" or rel.startswith("discover/profiles/"):
        return ("deny", "axis-migrate: inventory.json is the reference pin, written only by scan.py "
                        "(record findings with scan.py annotate).")
    if rel.startswith("report/"):
        return ("deny", "axis-migrate: migration/report/ is rendered by report.py from a sealed run; "
                        "agent prose goes to migration/narrative.md.")
    if rel in ("databricks/target-manifest.json", "databricks/target-check.json"):
        return ("deny", "axis-migrate: target status files are written only by make_databricks.py / "
                        "target_check.py.")
    decision = check_pipeline_module(mig, rel, path)
    if decision is None and content is not None and rel in CONTENT_FILES:
        try:
            pc = content()
        except rp.PatchError as e:
            return ("deny", f"axis-migrate: cannot apply the patch to {rel} ({e}), so its append-only/tolerance "
                            "rules cannot be checked; refused (fail closed). Re-read the file and patch again.")
        if pc is not None:
            cur, new = pc
            if rel in ("decisions.md", "known-differences.md"):
                decision = check_append_only(rel, cur, new)
            elif rel == "tolerances.yaml":
                decision = check_tolerances(mig, cur, new)
            elif rel == "tolerances.proposed.yaml":
                decision = check_proposed_tolerances(new)
            else:
                decision = check_params(cur, new)
    return decision


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def patch_targets(data: dict) -> list[tuple[Path, object]]:
    """(target path, content callable) for every file a Codex apply_patch touches (Add/Update/Delete/Move)."""
    cwd = data.get("cwd") or os.getcwd()
    out = []
    texts = rp.patch_texts(data.get("tool_name"), data.get("tool_input") or {})
    if data.get("tool_name") == "apply_patch" and not texts:
        raise rp.PatchError("apply_patch payload without a '*** Begin Patch' text in tool_input.command")
    for text in texts:
        for op in rp.parse_patch(text):
            src = hl.abs_path(op.path, cwd)
            for raw in [op.path] + ([op.move_to] if op.move_to else []):
                out += [(alt, None) for alt in hl.abs_paths(raw, cwd)[1:]]  # relative path from the process cwd
            if op.kind == "add":
                body = "\n".join(op.add_lines) + ("\n" if op.add_lines else "")
                out.append((src, lambda src=src, body=body: (_read(src), body)))
            elif op.kind == "delete":
                out.append((src, lambda src=src: (_read(src), "")))
            else:
                def updated(src=src, hunks=op.hunks):
                    return rp.apply_patch_update(_read(src), hunks)
                if op.move_to:
                    dst = hl.abs_path(op.move_to, cwd)
                    out.append((src, lambda src=src: (_read(src), "")))          # the source disappears
                    out.append((dst, lambda dst=dst, f=updated: (_read(dst), f())))
                else:
                    out.append((src, lambda src=src, f=updated: (_read(src), f())))
    return out


def combine(decisions: list) -> tuple[str, str] | None:
    denies = [d for d in decisions if d and d[0] == "deny"]
    if denies:
        return ("deny", " | ".join(dict.fromkeys(d[1] for d in denies)))
    asks = [d for d in decisions if d and d[0] == "ask"]
    if asks:
        return ("ask", " | ".join(dict.fromkeys(d[1] for d in asks)))
    return None


def decide(data: dict) -> tuple[str, str] | None:
    tool = data.get("tool_name")
    cwd = data.get("cwd")
    ti = data.get("tool_input") or {}
    if tool == "apply_patch" or (tool == "Bash" and rp.patch_texts("Bash", ti)):
        targets = patch_targets(data)
    elif tool in TOOLS:
        fp = ti.get("file_path") or ti.get("notebook_path")
        if not isinstance(fp, str) or not fp:
            raise ValueError(f"{tool} payload without file_path")
        paths = hl.abs_paths(fp, cwd)
        path = paths[0]
        targets = [(path, None if tool == "NotebookEdit" else (lambda path=path: proposed(tool, ti, path)))]
        targets += [(alt, None) for alt in paths[1:]]  # path rules from the hook process cwd too (Codex desktop)
    else:
        return None
    mig = hl.find_migration(data)
    if mig is None:
        for path, _ in targets:
            if path.parent.exists():
                mig = hl.find_migration_dir(path.parent)
                if mig:
                    break
    if mig is None:
        return None
    mig = mig.resolve()
    if any(hl.under(path, mig) for path, _ in targets):
        hl.touch_session(mig, data.get("session_id"), data)   # this session works on the migration (Stop gate scope)
    ps = hl.protected_set(mig)
    return combine([check_target(mig, ps, path, fn) for path, fn in targets])


def main() -> int:
    data = hl.read_input()
    if hl.require_tool_payload(data) not in TOOLS | {"apply_patch"}:
        return 0
    d = decide(data)
    if d:
        hl.pre_decision(*d)
    return 0


if __name__ == "__main__":
    hl.run_guard(main, "guard_write")
