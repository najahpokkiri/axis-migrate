#!/usr/bin/env python3
"""Dev-only: feed guard_write.py a real PreToolUse Edit payload (shape captured from Claude Code 2.1.296, see
axis-migrate/hooks/tests/payloads/PreToolUse_Edit.json) for an edit of a file in a project, and print the decision.

    python3 devtools/demo/guard_probe.py <project dir> <file relative to project> <old_string> <new_string>

The hook runs exactly as hooks.json runs it (python3 -I, CLAUDE_PLUGIN_ROOT set, payload on stdin). Nothing is
written: a PreToolUse hook only decides; this script never applies the edit.
"""
import copy, json, os, subprocess, sys, time
from pathlib import Path

PLUGIN = Path(os.environ.get("PLUGIN", Path(__file__).resolve().parents[2] / "axis-migrate"))
proj, rel, old, new = sys.argv[1:5]
proj = Path(proj).resolve()
payload = json.loads((PLUGIN / "hooks/tests/payloads/PreToolUse_Edit.json").read_text())
payload = copy.deepcopy(payload)
payload["cwd"] = str(proj)
payload["permission_mode"] = os.environ.get("PERMISSION_MODE", "default")
payload["tool_input"] = {"file_path": str(proj / rel), "old_string": old, "new_string": new, "replace_all": False}
env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PLUGIN), PYTHONIOENCODING="utf-8")
t0 = time.time()
p = subprocess.run(["python3", "-I", str(PLUGIN / "hooks/bin/guard_write.py")], input=json.dumps(payload),
                   capture_output=True, text=True, cwd=str(proj), env=env)
ms = (time.time() - t0) * 1000
print(f"exit {p.returncode} in {ms:.0f} ms")
if p.stdout.strip():
    out = json.loads(p.stdout)
    h = out.get("hookSpecificOutput", {})
    print("decision:", h.get("permissionDecision"))
    print("reason:  ", h.get("permissionDecisionReason"))
else:
    print("decision: (none: the edit would proceed)")
if p.stderr.strip():
    print("stderr:", p.stderr.strip())
