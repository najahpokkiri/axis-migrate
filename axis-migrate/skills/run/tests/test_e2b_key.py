"""E2B key loading, the person-run configure command and the template builder/publisher (fake keys, no network)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
E2B_DIR = HERE.parent / "e2b"
sys.path.insert(0, str(E2B_DIR))
import build_template as bt  # noqa: E402
import configure  # noqa: E402
import e2b_key  # noqa: E402

FAKE = "e2b_fake_key_for_tests_0123456789abcdef"
AXPY = HERE.parents[2] / "scripts" / "axpy"


@pytest.fixture(autouse=True)
def _no_real_key_or_publish(monkeypatch):
    """Nothing here may load the machine's real key or call E2B (a test overrides these when it needs a fake)."""
    monkeypatch.setattr(bt, "_key", lambda: pytest.fail("a test reached the real key loader"))
    monkeypatch.setattr(bt, "publish", lambda *a: pytest.fail("a test reached the real publish call"))


def secrets(path: Path, text: str, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)
    return path


def clean_env(tmp_path: Path, **extra) -> dict:
    """An environ with HOME in tmp and no harness variables: nothing real is consulted."""
    return {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", **extra}


@pytest.mark.parametrize("text,want", [
    (f"E2B_API_KEY={FAKE}\n", FAKE),
    (f"export E2B_API_KEY={FAKE}\n", FAKE),
    (f'E2B_API_KEY="{FAKE}"\n', FAKE),
    (f"# c\nOTHER=x\n  E2B_API_KEY='{FAKE}'  \n", FAKE),
    (f"E2B_API_KEY={FAKE} # comment\n", FAKE),
    ("E2B_API_KEY=old\nE2B_API_KEY=new\n", "new"),            # shell semantics: the last assignment wins
    ("E2B_API_KEY=$(cat /etc/passwd)\n", "$(cat /etc/passwd)"),  # never executed or expanded
    ("E2B_API_KEY=\n", None), ("OTHER=1\n", None), ("", None),
])
def test_parse_only_the_key_line(text, want):
    assert e2b_key.parse_key_line(text) == want


def test_lookup_order_env_then_plugin_data_then_harness_folders_then_dot_config(tmp_path):
    home = tmp_path / "home"
    env = clean_env(tmp_path, PLUGIN_DATA=str(tmp_path / "pd"), CLAUDE_PLUGIN_DATA=str(tmp_path / "cpd"))
    (home / ".claude" / "plugins" / "data" / "axis-migrate-mkt").mkdir(parents=True)
    (home / ".codex" / "plugins" / "data" / "axis-migrate-mkt").mkdir(parents=True)
    dirs = [str(d) for d in e2b_key.ac.plugin_data_dirs(env)]
    assert dirs == [str(tmp_path / "pd"), str(tmp_path / "cpd"), str(home / ".claude/plugins/data/axis-migrate-mkt"),
                    str(home / ".codex/plugins/data/axis-migrate-mkt"), str(home / ".config/axis-migrate")]
    secrets(home / ".config/axis-migrate/secrets.env", "E2B_API_KEY=dotconfig\n")
    assert e2b_key.resolve(env) == ("dotconfig", str(home / ".config/axis-migrate/secrets.env"))
    secrets(home / ".claude/plugins/data/axis-migrate-mkt/secrets.env", "E2B_API_KEY=claude\n")
    assert e2b_key.resolve(env)[0] == "claude"
    secrets(tmp_path / "cpd" / "secrets.env", "E2B_API_KEY=cpd\n")
    assert e2b_key.resolve(env)[0] == "cpd"
    secrets(tmp_path / "pd" / "secrets.env", "E2B_API_KEY=pd\n")
    assert e2b_key.resolve(env)[0] == "pd"
    assert e2b_key.resolve(dict(env, E2B_API_KEY="fromenv")) == ("fromenv", "env E2B_API_KEY")


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660])
def test_open_file_mode_is_refused_and_the_key_never_appears(tmp_path, mode):
    p = secrets(tmp_path / "home/.config/axis-migrate/secrets.env", f"E2B_API_KEY={FAKE}\n", mode)
    for env in (clean_env(tmp_path), clean_env(tmp_path, E2B_API_KEY=FAKE)):    # the env key does not excuse it
        with pytest.raises(e2b_key.KeyProblem) as ei:
            e2b_key.resolve(env)
        assert ei.value.code == "SECRETS_FILE_MODE" and f"chmod 600 {p}" in str(ei.value)
        assert FAKE not in str(ei.value)
    p.chmod(0o400)                                                              # owner-only read is fine
    assert e2b_key.resolve(clean_env(tmp_path))[0] == FAKE


def test_not_a_regular_file_and_missing(tmp_path):
    env = clean_env(tmp_path, AXIS_MIGRATE_SECRETS=str(tmp_path / "dir"))
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir").chmod(0o700)
    with pytest.raises(e2b_key.KeyProblem) as ei:
        e2b_key.resolve(env)
    assert ei.value.code in ("SECRETS_FILE_MODE", "SECRETS_FILE_UNREADABLE")
    with pytest.raises(e2b_key.KeyProblem) as ei:
        e2b_key.resolve(clean_env(tmp_path))
    assert ei.value.code == "E2B_KEY_MISSING" and "--configure e2b" in ei.value.detail
    assert e2b_key.source(clean_env(tmp_path))[0] == "absent"


def test_source_names_the_file_never_the_key(tmp_path):
    p = secrets(tmp_path / "home/.config/axis-migrate/secrets.env", f"E2B_API_KEY={FAKE}\n")
    st, detail = e2b_key.source(clean_env(tmp_path))
    assert (st, detail) == ("ok", str(p)) and FAKE not in detail and str(len(FAKE)) not in detail
    assert e2b_key.scrub(f"boom {FAKE} boom", FAKE) == "boom <E2B_API_KEY> boom"


def test_sandbox_choice_is_stored_once_per_install(tmp_path):
    env = clean_env(tmp_path)
    assert e2b_key.read_config(env) == ({}, None)
    p = e2b_key.write_sandbox_choice("e2b", "user (AskUserQuestion)", env)
    assert p == tmp_path / "home/.config/axis-migrate/config.json"
    doc, where = e2b_key.read_config(env)
    assert where == p and doc["sandbox"] == "e2b" and doc["sandbox_by"] == "user (AskUserQuestion)"
    with pytest.raises(ValueError):
        e2b_key.write_sandbox_choice("cloud", "agent", env)


# --- scripts/axpy --configure e2b -------------------------------------------------------------------------------
def test_configure_refuses_without_a_terminal(tmp_path):
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    cp = subprocess.run([str(AXPY), "--configure", "e2b"], input=FAKE + "\n", capture_output=True, text=True, env=env)
    assert cp.returncode == 13 and "NOT_A_TERMINAL" in cp.stderr and FAKE not in cp.stdout + cp.stderr
    assert not (tmp_path / ".config/axis-migrate/secrets.env").exists()
    bad = subprocess.run([str(AXPY), "--configure", "aws"], capture_output=True, text=True, env=env)
    assert bad.returncode == 2


def test_configure_writes_an_owner_only_file_keeping_other_lines(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    for v in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA", "AXIS_MIGRATE_SECRETS"):
        monkeypatch.delenv(v, raising=False)
    p = secrets(tmp_path / ".config/axis-migrate/secrets.env", "OTHER=keep\nE2B_API_KEY=old\n")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(configure.getpass, "getpass", lambda prompt: FAKE)
    assert configure.main(["e2b"]) == 0
    out = capsys.readouterr()
    assert FAKE not in out.out + out.err and str(p) in out.out
    assert (p.stat().st_mode & 0o777) == 0o600 and p.read_text() == f"OTHER=keep\nE2B_API_KEY={FAKE}\n"
    assert e2b_key.resolve({"HOME": str(tmp_path)})[0] == FAKE
    monkeypatch.setattr(configure.getpass, "getpass", lambda prompt: "has space")
    assert configure.main(["e2b"]) == 2
    assert configure.main(["e2b", "--remove"]) == 0 and p.read_text() == "OTHER=keep\n"
    p.chmod(0o644)
    monkeypatch.setattr(configure.getpass, "getpass", lambda prompt: FAKE)
    assert configure.main(["e2b"]) == 13                                       # will not write into an open file


# --- build_template: names, audit, dry-run publish, extension builds --------------------------------------------
def test_template_names_are_namespaced_and_lock_bound():
    lock8 = bt.lock_sha()[:8]
    assert bt.template_name() == f"axis-geo-migrate-{lock8}"
    assert bt.public_name() == f"{e2b_key.ac.E2B_TEAM_SLUG}/axis-geo-migrate-{lock8}"


def test_audit_of_the_shipped_template_is_clean():
    rep = bt.audit()
    assert rep["ok"], rep["findings"]
    assert [f["path"] for f in rep["copied_files"]] == ["env/pyproject.toml", "env/uv.lock", "axis_boot.py"]
    df = rep["dockerfile"]
    assert df.startswith("FROM debian:stable") and "strace" in df and "uv sync --frozen --no-dev" in df
    assert "E2B_API_KEY" not in df and "secrets" not in df


def test_audit_flags_key_like_and_confidential_lines(tmp_path, monkeypatch):
    monkeypatch.setenv(bt.DENY_ENV, "zebracorp|other-client")
    (tmp_path / "env").mkdir()
    (tmp_path / "env" / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "env" / "uv.lock").write_text("token = 'abc'\n")
    (tmp_path / "axis_boot.py").write_text("# for the ZebraCorp client\n")
    rep = bt.audit(context=tmp_path)
    assert not rep["ok"]
    assert {(f["code"], f["where"]) for f in rep["findings"]} == {("KEY_LIKE", "env/uv.lock:1"),
                                                                 ("CONFIDENTIAL", "axis_boot.py:1")}
    monkeypatch.delenv(bt.DENY_ENV)
    assert bt.audit(context=tmp_path)["confidentiality_checked"] is False


def test_extension_build_layers_on_the_public_base_with_the_same_lock_sync():
    from e2b import Template
    doc = json.loads(Template.to_json(bt.builder("someteam/axis-geo-migrate-12345678")))
    assert doc.get("fromTemplate") == "someteam/axis-geo-migrate-12345678" and not doc.get("fromImage")
    runs = " ".join(" ".join(map(str, s["args"])) for s in doc["steps"])
    assert "uv sync --frozen --no-dev" in runs and bt.lock_sha() in runs and "apt-get" not in runs


def test_publish_is_a_dry_run_without_yes(monkeypatch, tmp_path, capsys):
    rec = tmp_path / "template.json"
    rec.write_text(json.dumps({"name": bt.public_name(), "template_id": "tpl123", "lock_sha256": bt.lock_sha()}))
    monkeypatch.setattr(bt, "RECORD", rec)
    monkeypatch.setattr(bt, "publish", lambda *a: pytest.fail("published without --yes"))
    monkeypatch.setattr(bt, "_key", lambda: pytest.fail("a dry run needs no key"))
    assert bt.main(["--publish"]) == 0
    assert "dry run" in capsys.readouterr().out and json.loads(rec.read_text()).get("public") is None


def test_publish_with_yes_needs_the_confidentiality_pattern(monkeypatch, tmp_path):
    rec = tmp_path / "template.json"
    rec.write_text(json.dumps({"name": bt.template_name(), "template_id": "tpl123", "lock_sha256": bt.lock_sha()}))
    monkeypatch.setattr(bt, "RECORD", rec)
    monkeypatch.delenv(bt.DENY_ENV, raising=False)
    assert bt.main(["--publish", "--yes"]) == 2


def test_publish_with_yes_records_the_public_name(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(bt.DENY_ENV, "zebracorp")
    rec = tmp_path / "template.json"
    rec.write_text(json.dumps({"name": bt.template_name(), "template_id": "tpl123", "lock_sha256": bt.lock_sha()}))
    monkeypatch.setattr(bt, "RECORD", rec)
    monkeypatch.setattr(bt, "_key", lambda: FAKE)
    monkeypatch.setattr(bt, "publish", lambda tid, key: [bt.public_name()] if (tid, key) == ("tpl123", FAKE) else [])
    assert bt.main(["--publish", "--yes"]) == 0
    doc = json.loads(rec.read_text())
    assert doc["public"] is True and doc["name"] == bt.public_name() and FAKE not in rec.read_text()
    assert FAKE not in "".join(capsys.readouterr())


def test_publish_refuses_an_unbuilt_template(monkeypatch, tmp_path):
    rec = tmp_path / "template.json"
    rec.write_text(json.dumps({"name": "axis-geo-migrate-00000000", "template_id": "old"}))
    monkeypatch.setattr(bt, "RECORD", rec)
    assert bt.main(["--publish", "--yes"]) == 13


def test_shipped_record_matches_the_lock():
    doc = json.loads(bt.RECORD.read_text())
    assert doc["lock_sha256"] == bt.lock_sha() and doc["name"] == bt.public_name()


@pytest.mark.skipif(not os.environ.get("AXIS_TEST_E2B_LIVE") or e2b_key.source()[0] != "ok",
                    reason="live E2B: set AXIS_TEST_E2B_LIVE=1 on a machine with a configured key")
def test_live_public_template_exists():
    cp = subprocess.run([str(AXPY), str(E2B_DIR / "e2b_key.py"), "--exists", bt.public_name()],
                        capture_output=True, text=True, timeout=120)
    assert json.loads(cp.stdout.strip().splitlines()[-1]) == {"ok": True, "code": None}
