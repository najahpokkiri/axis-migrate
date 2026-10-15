"""report/SKILL.md names every script by its full plugin path (BYU e2e B12: `axpy map.py display` sent the agent to
skills/map/map.py, which does not exist)."""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[3]
SKILL = PLUGIN / "skills" / "report" / "SKILL.md"


def test_every_script_has_a_full_path_that_exists():
    text = SKILL.read_text(encoding="utf-8")
    assert '`"$P/scripts/axpy" "$P/skills/report/map.py" display --run' in text
    assert not re.search(r"axpy [\w]+\.py", text), "a bare `axpy <script>.py` without its path"
    assert not re.search(r"python3 -I [\w]+\.py", text)
    for ref in re.findall(r'\$P/([^`\s"]+\.py)', text):
        if "<" not in ref:
            assert (PLUGIN / ref).is_file(), ref


def test_reading_the_report_names_owner_items():
    text = SKILL.read_text(encoding="utf-8")
    assert "`OPEN_QUESTION`" in text and "`PLAN_APPROVED_BY_STANDING_INSTRUCTION`" in text
