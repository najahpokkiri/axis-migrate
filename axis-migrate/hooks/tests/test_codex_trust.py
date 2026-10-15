"""skills/guard/codex_trust.py: the expected hook set comes from the hooks file Codex loads; no codex → exit 3."""
from __future__ import annotations

import json
import subprocess
import sys

from conftest import PLUGIN

sys.path.insert(0, str(PLUGIN / "skills" / "guard"))
import codex_trust  # noqa: E402

SCRIPT = PLUGIN / "skills" / "guard" / "codex_trust.py"


def test_expected_hooks_match_hooks_json():
    hj = json.loads((PLUGIN / "hooks" / "hooks.json").read_text())
    want = sum(len(groups) for groups in hj["hooks"].values())
    got = codex_trust.expected_hooks()
    assert len(got) == want == 8
    assert ("preToolUse", "Bash|PowerShell") in got and ("stop", None) in got


def test_codex_missing_is_a_skip(tmp_path):
    cp = subprocess.run([sys.executable, "-I", str(SCRIPT), "--json"], capture_output=True, text=True, timeout=30,
                        env={"PATH": str(tmp_path), "HOME": str(tmp_path)})
    assert cp.returncode == 3 and json.loads(cp.stdout)
