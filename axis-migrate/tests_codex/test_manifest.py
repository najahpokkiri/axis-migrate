"""Codex packaging checks (stdlib only; runs under pytest or as `python3 -I tests_codex/test_manifest.py`).

`.codex-plugin/plugin.json` is the canonical Codex manifest (no root plugin.json, no `hooks` key: default discovery
finds hooks/hooks.json, which serves both harnesses). The local marketplace is the repo-root
`.agents/plugins/marketplace.json`. Versions stay in step with `.claude-plugin/plugin.json` (Codex caches by version).

`skills` is the array ["./skills/", "./codex/skills/"], not a string: measured on codex-cli 0.144.4 and 0.162.1
(14 Oct 2026), `"skills": "./codex/skills/"` REPLACES default discovery, so only `start` loads and the seven method
skills vanish (CODEX.md). The test pins that decision until a Codex build is shown to supplement instead.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
REPO = PLUGIN.parent
CODEX = PLUGIN / ".codex-plugin" / "plugin.json"
CLAUDE = PLUGIN / ".claude-plugin" / "plugin.json"
MARKET = REPO / ".agents" / "plugins" / "marketplace.json"
CLAUDE_MARKET = REPO / ".claude-plugin" / "marketplace.json"
START = PLUGIN / "codex" / "skills" / "start" / "SKILL.md"
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
# Keys the plugin-creator validator embedded in codex-cli accepts (no `hooks`).
ALLOWED_KEYS = {"id", "name", "version", "description", "skills", "apps", "mcpServers", "interface", "author",
                "homepage", "repository", "license", "keywords"}
METHOD_SKILLS = {"discover", "interview", "migrate", "run", "verify", "report", "target"}
# Referenced by the start skill but owned by another builder and not always present yet.
OPTIONAL_REFS = {"skills/map/SKILL.md"}


def _load(p: Path) -> dict:
    doc = json.loads(p.read_text(encoding="utf-8"))
    assert isinstance(doc, dict), f"{p} must hold a JSON object"
    return doc


def _inside(root: Path, rel: str) -> Path:
    assert isinstance(rel, str) and rel.startswith("./"), f"path {rel!r} must be relative and start with ./"
    p = (root / rel).resolve()
    assert p == root.resolve() or root.resolve() in p.parents, f"path {rel!r} leaves {root}"
    assert p.exists(), f"path {rel!r} does not exist under {root}"
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


def test_manifest_shape_name_and_version():
    codex, claude = _load(CODEX), _load(CLAUDE)
    assert set(codex) <= ALLOWED_KEYS, f"unexpected keys: {sorted(set(codex) - ALLOWED_KEYS)}"
    assert codex["name"] == claude["name"] == PLUGIN.name == "axis-migrate", "manifest name == folder name"
    assert codex["version"] == claude["version"], "Codex and Claude manifests carry the same version"
    assert SEMVER.match(codex["version"]) and codex["version"] != "0.1.0", "bumped past 0.1.0 (Codex caches by version)"
    assert codex["description"].strip() and codex["author"]["name"].strip()


def test_canonical_manifest_only_and_no_hooks_key():
    assert not (PLUGIN / "plugin.json").exists(), "no root plugin.json: .codex-plugin/plugin.json is canonical"
    assert "hooks" not in _load(CODEX), "default discovery finds hooks/hooks.json; a hooks key may double-register"
    assert (PLUGIN / "hooks" / "hooks.json").is_file()
    for retired in ("codex/hooks.json", "codex/hooks", "codex/install_project_hooks.py"):
        assert not (PLUGIN / retired).exists(), f"{retired}: a second hook registration"


def test_interface_and_default_prompts():
    iface = _load(CODEX)["interface"]
    for k in ("displayName", "shortDescription", "longDescription", "developerName", "category"):
        assert isinstance(iface.get(k), str) and iface[k].strip(), f"interface.{k}"
    assert len(iface["shortDescription"]) <= 30, "shortDescription: at most 30 characters (Codex compact views)"
    assert isinstance(iface.get("capabilities"), list) and iface["capabilities"]
    prompts = iface.get("defaultPrompt")
    assert isinstance(prompts, list) and 1 <= len(prompts) <= 3, "defaultPrompt: 1-3 entries"
    assert all(isinstance(p, str) and 0 < len(p) <= 128 for p in prompts), "each defaultPrompt <= 128 chars"
    for k in ("composerIcon", "logo"):
        assert k in iface, f"interface.{k}"
    for k in ("composerIcon", "logo", "logoDark"):
        if k in iface:
            p = _inside(PLUGIN, iface[k])
            assert p.suffix == ".png" and p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", f"{k}: a PNG under ./assets/"


def test_skill_roots_load_method_skills_and_start():
    skills = _load(CODEX)["skills"]
    assert skills == ["./skills/", "./codex/skills/"], "see module docstring: a string path drops the method skills"
    names = []
    for root in (_inside(PLUGIN, s) for s in skills):
        for d in sorted(x for x in root.iterdir() if (x / "SKILL.md").is_file()):
            fm = _frontmatter(d / "SKILL.md")
            assert fm.get("name") == d.name, f"{d}: frontmatter name must equal the folder name"
            assert 0 < len(fm.get("description", "")) <= 1024, f"{d}: description missing or too long"
            names.append(fm["name"])
    assert len(names) == len(set(names)), f"duplicate skill names across roots: {names}"
    assert METHOD_SKILLS | {"start"} <= set(names)


def _skill_dirs() -> list[Path]:
    return sorted(d for root in (PLUGIN / "skills", PLUGIN / "codex" / "skills") for d in root.iterdir()
                  if (d / "SKILL.md").is_file())


def test_frontmatter_is_plain_yaml_short_and_intent_first():
    """Descriptions stay short (the skill listing has a shared budget: over it, Claude Code lists the least-used
    skills by name only) and parse as plain YAML scalars (a ': ' or ' #' inside one breaks the YAML, and Claude Code
    then loads the skill with no description). No angle brackets anywhere in frontmatter."""
    for d in _skill_dirs():
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        fm_text = text[4:text.find("\n---", 4)]
        assert "<" not in fm_text and ">" not in fm_text, f"{d.name}: angle brackets in frontmatter"
        desc = _frontmatter(d / "SKILL.md")["description"]
        assert desc.startswith(("Use ", "Sub-step of the axis-migrate migrate flow. Use ")), f"{d.name}: intent first"
        assert len(desc) <= 400, f"{d.name}: description {len(desc)} chars (keep it crisp)"
        assert ": " not in desc and " #" not in desc and not desc.startswith(("'", '"', "[", "{")), d.name
        try:
            import yaml
        except ImportError:
            continue
        fm = yaml.safe_load(fm_text)
        assert fm["description"] == desc and fm["name"] == d.name
        assert isinstance(fm.get("argument-hint", ""), str), f"{d.name}: argument-hint must be a string (quote it)"


def test_axpy_preapproved_in_every_method_skill():
    rule = "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *)"
    for d in (PLUGIN / "skills").iterdir():
        if (d / "SKILL.md").is_file():
            assert rule in _frontmatter(d / "SKILL.md").get("allowed-tools", ""), f"{d.name}: allowed-tools"


def test_codex_implicit_invocation_policy():
    """Under Codex only migrate and start are offered implicitly; the sub-steps are read by path from the flow."""
    for d in _skill_dirs():
        y = d / "agents" / "openai.yaml"
        assert y.is_file(), f"{d.name}: agents/openai.yaml"
        text = y.read_text(encoding="utf-8")
        m = re.search(r"^policy:\n  allow_implicit_invocation: (true|false)$", text, re.M)
        assert m, f"{d.name}: policy.allow_implicit_invocation"
        assert (m.group(1) == "true") == (d.name in {"migrate", "start"}), d.name
        sd = re.search(r'^  short_description: "([^"]*)"$', text, re.M)
        assert sd and 0 < len(sd.group(1)) <= 64, f"{d.name}: short_description"


def test_versions_in_step():
    v = _load(CLAUDE)["version"]
    common = (PLUGIN / "lib" / "axis_common.py").read_text(encoding="utf-8")
    assert f'PLUGIN_VERSION = "{v}"' in common, "lib/axis_common.py PLUGIN_VERSION == manifest version"
    assert f"## {v}" in (PLUGIN / "CHANGELOG.md").read_text(encoding="utf-8"), "CHANGELOG.md has the version"


def test_claude_user_config():
    uc = _load(CLAUDE)["userConfig"]
    assert uc["e2b_api_key"]["sensitive"] is True and uc["e2b_api_key"].get("required") is not True
    assert uc["sandbox"]["options"] == ["local", "e2b"] and uc["sandbox"]["default"] == "local"


def test_start_skill_is_thin_and_its_references_exist():
    text = START.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 40, "codex/skills/start stays a thin layer"
    refs = {r.rstrip(".") for r in re.findall(r"<P>/([A-Za-z0-9_./-]+)", text)}
    assert {"skills/migrate/SKILL.md", "skills/migrate/BUILD.md", "skills/guard/hooks_active.py", "CODEX.md",
            "scripts/axpy", "skills/report/ladder.py"} <= refs
    missing = [r for r in refs - OPTIONAL_REFS if not (PLUGIN / r).exists()]
    assert not missing, f"start skill references missing files: {missing}"
    assert "commands/migrate.md" not in text, "the command file is gone; the entry is skills/migrate/SKILL.md"
    assert "guards are OFF" in text and "/hooks" in text and "NEW chat" in text and "until the person confirms" in text
    assert (START.parents[3] / ".codex-plugin" / "plugin.json").is_file()


def test_map_skill_if_present():
    skill = PLUGIN / "skills" / "map" / "SKILL.md"
    if skill.is_file():
        assert _frontmatter(skill).get("name") == "map"
        assert "skills/map/SKILL.md" in START.read_text(encoding="utf-8")


def test_repo_marketplace_points_at_this_plugin():
    mk = _load(MARKET)
    assert mk["name"] == _load(CLAUDE_MARKET)["name"], "same marketplace name as the Claude marketplace"
    assert isinstance(mk.get("interface"), dict) and mk["interface"].get("displayName")
    entries = [p for p in mk["plugins"] if p["name"] == "axis-migrate"]
    assert len(entries) == 1
    e = entries[0]
    assert e["source"] == {"source": "local", "path": "./axis-migrate"}
    assert e["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
    assert isinstance(e.get("category"), str) and e["category"]
    assert _inside(REPO, e["source"]["path"]) == PLUGIN
    assert not (PLUGIN / ".agents").exists(), "one marketplace, at the repository root"


def test_codex_note_is_short_and_current():
    note = (PLUGIN / "CODEX.md").read_text(encoding="utf-8")
    assert len(note.splitlines()) <= 70
    assert "axis-migrate@axis-migrate-dev" in note and "/hooks" in note
    assert "commands/migrate.md" not in note


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
