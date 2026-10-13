"""No lux fixture facts in any shipped skill text (D4-14). Dev-only: this list itself names fixture facts."""
import re
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2] / "axis-migrate"
FIXTURE_WORDS = ["luxembourg", "canton", "wiltz", "esch", "clervaux", "worldpop", "ppp_2020", "05_temp", "04_final",
                 "03b", "keep_resolution", "crop_to_cutline", "lux-exposure", "value_per_capita",
                 "res_mask", "pop_res", "kEUR".lower(), "2500", "-99999"]
SHIPPED = [p for p in PLUGIN.rglob("*") if p.is_file() and p.suffix in (".md", ".yaml", ".py", ".json", ".html")
           and "tests" not in p.parts and ".venv" not in p.parts and p.name != "qgis_algs.json"]
SKILL_TEXT = [p for p in SHIPPED if p.suffix in (".md", ".yaml") or "reference" in p.parts or "templates" in p.parts]


def test_skill_texts_have_no_fixture_facts():
    bad = []
    for p in SKILL_TEXT:
        low = re.sub(r"ev:[0-9a-f]{10}", "ev:", p.read_text(errors="replace")).lower()
        low = re.sub(r"eval: [^·\n]*", "", low)   # eval tag names (e.g. 'lux') are case ids, not facts
        for w in FIXTURE_WORDS:
            if w in low:
                bad.append(f"{p.relative_to(PLUGIN)}: {w}")
    assert not bad, "\n".join(bad)
