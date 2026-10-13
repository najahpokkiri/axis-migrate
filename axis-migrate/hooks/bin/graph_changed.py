"""H2 UserPromptSubmit: announce unacked user edits of the workflow graph (02 §4.7). Informs only, never blocks."""
import json, sys
from pathlib import Path
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "discover")); import graph
    mdir = graph.ac.find_migration_dir(json.loads(sys.stdin.read() or "{}").get("cwd") or ".")
    text = graph.hook_summary(mdir) if mdir else ""
except Exception as exc:  # fail open with a visible note
    text = f"graph-change check failed: {exc.__class__.__name__}: {exc}"
if text: print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}))
sys.exit(0)
