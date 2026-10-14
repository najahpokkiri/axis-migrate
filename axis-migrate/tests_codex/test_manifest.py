"""Codex manifest checks (stdlib only; runs under pytest or as `python3 -I tests_codex/test_manifest.py`).

The Codex build is skills and scripts only (CODEX.md): the manifest must parse, every path it or the local
marketplace references must exist inside the plugin, its version must equal .claude-plugin/plugin.json, and it must
not declare hooks.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
CODEX = PLUGIN / ".codex-plugin" / "plugin.json"
CLAUDE = PLUGIN / ".claude-plugin" / "plugin.json"
MARKET = PLUGIN / ".agents" / "plugins" / "marketplace.json"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# The keys the plugin-creator validator embedded in codex-cli 0.144.4 accepts (it has no `hooks`).
ALLOWED_KEYS = {"id", "name", "version", "description", "skills", "apps", "mcpServers", "interface", "author",
                "homepage", "repository", "license", "keywords"}


def _load(p: Path) -> dict:
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{p} must hold a JSON object"
    return doc


def _inside(rel: str) -> Path:
    assert isinstance(rel, str) and rel.startswith("./"), f"path {rel!r} must be relative and start with ./"
    p = (PLUGIN / rel).resolve()
    assert p == PLUGIN or PLUGIN in p.parents, f"path {rel!r} leaves the plugin root"
    assert p.exists(), f"path {rel!r} does not exist"
    return p


def _frontmatter(skill_md: Path) -> dict:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_md} must start with YAML frontmatter"
    end = text.find("\n---", 4)
    assert end > 0, f"{skill_md} frontmatter is not closed"
    out = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _skill_roots() -> list[Path]:
    skills = _load(CODEX)["skills"]
    return [_inside(s) for s in ([skills] if isinstance(skills, str) else skills)]


def test_manifest_parses_and_matches_claude():
    codex, claude = _load(CODEX), _load(CLAUDE)
    assert set(codex) <= ALLOWED_KEYS, f"unexpected keys: {sorted(set(codex) - ALLOWED_KEYS)}"
    assert codex["name"] == claude["name"] == "axis-migrate"
    assert codex["version"] == claude["version"], "Codex and Claude manifests must carry the same version"
    assert SEMVER.match(codex["version"])
    assert codex["description"].strip() and codex["author"]["name"].strip()
    iface = codex["interface"]
    for k in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        assert isinstance(iface.get(k), str) and iface[k].strip(), f"interface.{k}"
    assert isinstance(iface.get("capabilities"), list) and iface["capabilities"]
    assert iface.get("defaultPrompt"), "interface.defaultPrompt is required by the Codex validator"
    for k in ("composerIcon", "logo", "logoDark"):
        if k in iface:
            _inside(iface[k])


def test_no_hooks_declared():
    codex = _load(CODEX)
    assert "hooks" not in codex, "Codex build ships no hooks (CODEX.md)"
    assert not (PLUGIN / ".codex-plugin" / "hooks.json").exists()


def test_skill_paths_exist_and_skills_are_wellformed():
    names = []
    for root in _skill_roots():
        assert root.is_dir()
        found = sorted(d for d in root.iterdir() if (d / "SKILL.md").is_file())
        assert found, f"{root} holds no SKILL.md"
        for d in found:
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("name") == d.name, f"{d}: frontmatter name must equal the folder name"
            assert 0 < len(fm.get("description", "")) <= 1024, f"{d}: description missing or too long"
            names.append(fm["name"])
    assert len(names) == len(set(names)), f"duplicate skill names across roots: {names}"
    for required in ("discover", "interview", "build", "run", "verify", "report", "target", "start"):
        assert required in names, f"skill {required} is not reachable from the Codex manifest"


def test_start_skill_references_exist():
    start = PLUGIN / "codex" / "skills" / "start" / "SKILL.md"
    text = start.read_text(encoding="utf-8")
    refs = set(re.findall(r"<P>/([A-Za-z0-9_./-]+)", text))
    assert refs, "start skill should reference plugin files as <P>/..."
    for rel in refs:
        assert (PLUGIN / rel.rstrip(".")).exists(), f"start skill references missing <P>/{rel}"
    # <P> is three levels above the start skill and holds the Codex manifest.
    assert (start.parents[3] / ".codex-plugin" / "plugin.json").is_file()


def test_marketplace_points_at_this_plugin():
    mk = _load(MARKET)
    assert mk["name"] and mk["interface"]["displayName"]
    entries = [p for p in mk["plugins"] if p["name"] == "axis-migrate"]
    assert len(entries) == 1
    e = entries[0]
    assert e["source"]["source"] == "local" and e["category"]
    assert e["policy"]["installation"] in ("AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE")
    # source.path is relative to the marketplace root (the folder holding .agents/), which is this plugin.
    root = MARKET.parents[2]
    target = (root / e["source"]["path"]).resolve()
    assert e["source"]["path"].startswith("./") and target == PLUGIN
    assert (target / ".codex-plugin" / "plugin.json").is_file()


def test_codex_note_is_short_and_present():
    note = PLUGIN / "CODEX.md"
    assert note.is_file()
    assert len(note.read_text(encoding="utf-8").splitlines()) <= 60


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"ok   {n}")
        except AssertionError as e:
            bad += 1
            print(f"FAIL {n}: {e}")
    sys.exit(1 if bad else 0)
