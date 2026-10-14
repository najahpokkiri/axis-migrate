#!/usr/bin/env python3
"""H5 PreToolUse Read|Grep|Glob guard (08 §3 H5, 10 B5 "grader access"). Fails closed.

Always (inside a migration project): deny reading ~/.config/axis-migrate/secrets.env.
Eval mode only (AXIS_MIGRATE_HIDDEN_GLOBS set by the harness, e.g. "*.PROVENANCE.md:*.build/**"): deny Read of a
matching path, Grep/Glob rooted at a matching path, and Grep/Glob over a tree that contains a matching file (Glob only
when its pattern can match one).
"""
from __future__ import annotations

import fnmatch
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402

TOOLS = {"Read", "Grep", "Glob"}


def main() -> int:
    data = hl.read_input()
    tool = hl.require_tool_payload(data)
    if tool not in TOOLS:
        return 0
    mig = hl.find_migration(data)
    if mig is None:
        return 0
    from guard_bash import hidden_files, hl_hidden_globs, matches_hidden
    ti = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    target = ti.get("file_path") if tool == "Read" else (ti.get("path") or cwd)
    if not isinstance(target, str) or not target:
        raise ValueError(f"{tool} payload without a path")
    paths = hl.abs_paths(target, cwd)
    p = paths[0]
    secrets = Path(os.path.realpath(os.path.expanduser("~/.config/axis-migrate/secrets.env")))
    if p == secrets or (tool != "Read" and p in secrets.parents and
                        fnmatch.fnmatch("secrets.env", (ti.get("glob") or ti.get("pattern") or "*").split("/")[-1])):
        hl.pre_decision("deny", "axis-migrate: ~/.config/axis-migrate/secrets.env is never read by the agent; "
                                "the scripts load it themselves.")
        return 0
    globs = hl_hidden_globs()
    if not globs:
        return 0
    if any(matches_hidden(q, globs) for q in paths):
        hl.pre_decision("deny", "axis-migrate (eval mode): that path is hidden grader material.")
        return 0
    hidden = [h for h in hidden_files(mig.resolve()) if p == h or p in h.parents]
    if tool == "Grep" and hidden:
        hl.pre_decision("deny", "axis-migrate (eval mode): this search tree contains hidden grader material; "
                                "search inside migration/ or the legacy folder instead.")
        return 0
    if tool == "Glob" and hidden:
        pat = (ti.get("pattern") or "*").replace("**/", "*").replace("**", "*")
        for h in hidden:
            rel = h.relative_to(p).as_posix()
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(h.name, pat.rsplit("/", 1)[-1]):
                hl.pre_decision("deny", "axis-migrate (eval mode): that pattern matches hidden grader material.")
                return 0
    return 0


if __name__ == "__main__":
    hl.run_guard(main, "guard_hidden")
