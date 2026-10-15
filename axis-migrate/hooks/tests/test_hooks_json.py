"""hooks.json wiring (D8-4): every command exists, exact-name matchers, guards fail closed, informational hooks open."""
from __future__ import annotations

import json
import re

from conftest import PLUGIN

HJ = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())


def entries():
    for event, groups in HJ["hooks"].items():
        for g in groups:
            for h in g["hooks"]:
                yield event, g.get("matcher"), h


def test_command_paths_exist_and_form():
    for event, matcher, h in entries():
        # every hook goes through the POSIX sh wrapper (no python3 needed outside a migration; NEEDS_PYTHON3 inside)
        m = re.fullmatch(r'sh "\$\{CLAUDE_PLUGIN_ROOT:-\$PLUGIN_ROOT\}/hooks/run-hook\.sh" (\w+)', h["command"])
        assert m, h["command"]
        assert (PLUGIN / "hooks" / "bin" / f"{m.group(1)}.py").is_file(), m.group(1)


def test_matchers_and_failure_modes():
    table = {(e, m): h for e, m, h in entries()}
    assert table[("PreToolUse", "Write|Edit|MultiEdit|NotebookEdit|apply_patch")]["onFailure"] == "block"
    assert table[("PreToolUse", "Bash")]["onFailure"] == "block"
    assert table[("PreToolUse", "Read|Grep|Glob")]["onFailure"] == "block"
    assert table[("PostToolUse", "Write|Edit|MultiEdit|NotebookEdit|apply_patch")]["onFailure"] == "block"
    assert table[("PostToolUse", "Bash")]["onFailure"] == "block"  # Codex: apply_patch heredocs in the shell
    assert "onFailure" not in table[("SessionStart", "startup|resume|clear|compact|fork")]
    assert "onFailure" not in table[("UserPromptSubmit", None)]
    assert table[("Stop", None)]["timeout"] == 15
    for e, m, h in entries():
        assert m is None or re.fullmatch(r"[A-Za-z_|]+", m), m  # exact names (Claude) = plain alternation (Codex regex)
    assert set(HJ["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
