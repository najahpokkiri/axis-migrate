#!/usr/bin/env python3
"""H6 PostToolUse Write|Edit|MultiEdit|NotebookEdit: thin wrapper around `skills/guard/codescan.py --hook`.

Blocks with {"decision": "block", "reason": ...} on a block finding; warnings go to additionalContext. Fails closed:
any exception exits 2 with an ASCII reason (Claude Code shows stderr to the model).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402


def main() -> int:
    raw = sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    ti = data.get("tool_input") or {}
    fp = ti.get("file_path") or ti.get("notebook_path") or ""
    near = hl.abs_path(fp, data.get("cwd")).parent if fp else None
    if hl.find_migration(data) is None and (near is None or not near.exists() or hl.find_migration_dir(near) is None):
        return 0  # idle fast path: no migration/ here
    import codescan  # skills/guard, on sys.path via _hooklib
    return codescan.run_hook(raw)


if __name__ == "__main__":
    hl.run_guard(main, "codescan (post_scan)")
