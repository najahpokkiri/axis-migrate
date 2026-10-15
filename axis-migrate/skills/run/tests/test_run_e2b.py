"""run_e2b with a fake sandbox that executes locally (no network): create call, consent, probe, timeouts, resume."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_e2b
import runlib as rl
import synth
from conftest import NO_STRACE, step_rec

FAKE_KEY = "e2b_test_key_not_real_0123456789"
S01, S02 = "s01_clip_population", "s02_zonal_sum"


class FakeSandbox:
    """Implements the slice of the e2b Sandbox API run_e2b uses, against a local directory."""
    created: list[dict] = []
    killed: list[str] = []
    seen: list[tuple[str, object]] = []        # every command, env, cwd and uploaded file handed to the sandbox

    def __init__(self, kw: dict, remote: Path):
        self.kw = kw
        self.sandbox_id = f"fake-{len(FakeSandbox.created)}"
        self.remote = remote
        self.commands = SimpleNamespace(run=self._run)
        self.files = SimpleNamespace(write=self._write, read=self._read)

    def _run(self, cmd, timeout=60, envs=None, cwd=None, **_):
        import e2b
        FakeSandbox.seen += [("cmd", cmd), ("envs", dict(envs or {})), ("cwd", cwd)]
        env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), **(envs or {})}
        try:
            cp = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env, cwd=cwd,
                                timeout=timeout)
        except subprocess.TimeoutExpired:
            raise e2b.TimeoutException("fake: command timed out") from None
        if cp.returncode != 0:
            raise e2b.CommandExitException(stderr=cp.stderr, stdout=cp.stdout, exit_code=cp.returncode, error=None)
        return SimpleNamespace(exit_code=0, stdout=cp.stdout, stderr=cp.stderr)

    def _write(self, path, data, **_):
        FakeSandbox.seen += [("write_path", path), ("write_data", data)]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data if isinstance(data, bytes) else data.encode())

    def _read(self, path, format="text", **_):
        return Path(path).read_bytes() if format == "bytes" else Path(path).read_text()

    def kill(self):
        FakeSandbox.killed.append(self.sandbox_id)


@pytest.fixture
def fake_e2b(monkeypatch, tmp_path):
    remote = tmp_path / "sandbox"
    (remote / "opt").mkdir(parents=True)
    lock_file = remote / "opt" / "env.lock.sha256"
    lock_file.write_text(rl.sha256_file(rl.LOCK) + "\n")
    monkeypatch.setattr(run_e2b, "REMOTE", {"root": str(remote / "home/user/axis/run"), "python": str(rl.VENV_PY),
                                            "lock_file": str(lock_file), "probe_boot": str(rl.AXIS_BOOT),
                                            "strace": "/nonexistent/strace" if NO_STRACE else "/usr/bin/strace",
                                            "tmp": str(remote / "home/user/axis")})
    FakeSandbox.created, FakeSandbox.killed, FakeSandbox.seen = [], [], []
    monkeypatch.setenv("AXIS_MIGRATE_SECRETS", str(tmp_path / "no-secrets.env"))   # never the real secrets file
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "plugin-data"))              # no real user template record

    def factory(**kw):
        FakeSandbox.created.append(kw)
        if (remote / "home").exists():
            subprocess.run(["chmod", "-R", "u+w", str(remote / "home")])
            subprocess.run(["rm", "-rf", str(remote / "home")])
        return FakeSandbox(kw, remote)
    monkeypatch.setattr(run_e2b, "sandbox_factory", factory)
    monkeypatch.setenv("E2B_API_KEY", FAKE_KEY)
    yield SimpleNamespace(remote=remote, lock_file=lock_file)
    subprocess.run(["chmod", "-R", "u+w", str(remote)])


def e2b_run(mig: Path, *args) -> tuple[int, dict | None]:
    runs = mig / "runs"
    before = set(p.name for p in runs.iterdir()) if runs.is_dir() else set()
    code = run_e2b.main([str(mig), *args])
    new = sorted(set(p.name for p in runs.iterdir() if p.is_dir()) - before) if runs.is_dir() else []
    return code, (json.loads((runs / new[-1] / "manifest.json").read_text()) if new else None)


def test_create_call_is_offline_and_keyless_and_outputs_match_local(project, runner, fake_e2b):
    code, local = runner()
    assert code == 0
    time.sleep(1.05)
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 0, man["reason_codes"]
    kw = FakeSandbox.created[-1]
    assert kw["allow_internet_access"] is False and kw["timeout"] == run_e2b.SANDBOX_TIMEOUT_S == 3600
    assert kw["metadata"] == {"run_id": man["run_id"]} and kw["template"] == run_e2b.default_template()
    assert kw["template"].startswith(rl.ac.E2B_TEAM_SLUG + "/axis-geo-migrate-")
    assert kw["api_key"] == FAKE_KEY
    assert all(FAKE_KEY not in str(v) and "KEY" not in k and "TOKEN" not in k for k, v in kw["envs"].items())
    assert set(kw["envs"]) == set(rl.DETERMINISM_ENV) | set(rl.STEP_ENV_FIXED)
    e = man["env"]["e2b"]
    assert e["sandbox_destroyed"] is True and FakeSandbox.killed == [e["sandbox_id"]]
    assert e["upload_confirmed_by"] == "--confirm-upload" and e["uploaded_bytes"] > 0 and e["downloaded_bytes"] > 0
    assert man["executor"] == "e2b" and man["read_ledger"] == ("e2b-physical+audit" if NO_STRACE
                                                               else "e2b-physical+strace")
    assert man["eligible_for"] == "sandbox-validated" and man["full"] is True
    assert step_rec(man, S02)["writes_ledger"] == ["outputs/s02_zonal_sum/zone_stats.gpkg",
                                                   "outputs/s02_zonal_sum/zone_sum.csv"]
    assert {o["dataset_id"]: o["sha256"] for o in man["outputs"]} == \
        {o["dataset_id"]: o["sha256"] for o in local["outputs"]}
    assert rl.verify_checksums(project / "runs" / man["run_id"]) == []
    # the uploaded set never contains legacy finals or intermediates in a chained run
    paths = [f["path"] for f in e["upload_files"]]
    assert not any("04_FINAL" in p or "05_TEMP" in p for p in paths)


def test_upload_needs_explicit_consent(project, fake_e2b):
    code, man = e2b_run(project)
    assert code == 13 and man["reason_codes"] == ["UPLOAD_NOT_CONFIRMED"]
    assert FakeSandbox.created == [] and man["env"]["e2b"]["upload_files"]
    assert man["read_ledger"] == "e2b-physical"          # no sandbox probed: strace is not claimed


def test_missing_key_refuses_before_anything(project, fake_e2b, monkeypatch):
    monkeypatch.delenv("E2B_API_KEY")
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 13 and man is None and FakeSandbox.created == []


def test_template_from_another_lock_is_refused_and_killed(project, fake_e2b):
    fake_e2b.lock_file.write_text("0" * 64 + "\n")
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 13 and "TEMPLATE_LOCK_MISMATCH:lock_sha256" in man["reason_codes"]
    assert man["steps"] == [] and man["env"]["e2b"]["sandbox_destroyed"] is True


def test_isolated_uploads_only_the_needed_legacy_feed(project, fake_e2b):
    code, man = e2b_run(project, "--confirm-upload", "--chain", "isolated", "--steps", S02)
    assert code == 0, man["reason_codes"]
    paths = [f["path"] for f in man["env"]["e2b"]["upload_files"]]
    assert "stage/legacy_ro/05_TEMP/01_pop_clip.tif" in paths
    assert not any("04_FINAL" in p for p in paths) and man["eligible_for"] == "none"


def test_step_timeout_is_resumable_in_a_new_sandbox(project, fake_e2b):
    synth.set_step(project, S02, synth.S02.replace("def run(ctx):\n", "def run(ctx):\n    import time\n"
                                                                       "    time.sleep(6)\n"))
    synth.edit_graph(project, lambda g: next(s for s in g["steps"] if s["id"] == S02).update(timeout_s=3))
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 14 and man["status"] == "timed_out" and man["steps_succeeded"] == 1
    assert (project / "runs" / man["run_id"] / "outputs" / S01).is_dir()      # earlier output already on the host
    synth.edit_graph(project, lambda g: next(s for s in g["steps"] if s["id"] == S02).update(timeout_s=60))
    time.sleep(1.05)
    code, man2 = e2b_run(project, "--confirm-upload", "--resume-from", man["run_id"])
    assert code == 0, man2["reason_codes"]
    assert step_rec(man2, S01)["action"] == "reused" and step_rec(man2, S02)["action"] == "executed"
    assert len(FakeSandbox.created) == 2 and len(FakeSandbox.killed) == 2


def test_sandbox_budget_exhausted_before_a_step(project, fake_e2b, monkeypatch):
    monkeypatch.setattr(run_e2b, "SANDBOX_TIMEOUT_S", 100)   # 100 − 60 reserve < timeout_s 60 of each step
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 14 and f"BUDGET_EXHAUSTED_BEFORE:{S01}" in man["reason_codes"]


# --- the key: loaded by the script, never handed to the sandbox ------------------------------------------------
def _blobs(value) -> list[bytes]:
    """Every byte string a value carries to the sandbox, tarball members included."""
    import io
    import tarfile
    if isinstance(value, dict):
        return [b for k, v in value.items() for b in _blobs(k) + _blobs(v)]
    if value is None:
        return []
    data = value if isinstance(value, bytes) else str(value).encode()
    out = [data]
    if data[:2] == b"\x1f\x8b":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            out += [tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()]
    return out


def test_key_never_reaches_the_sandbox_or_the_run_dir(project, fake_e2b, capfd):
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 0, man["reason_codes"]
    kw = dict(FakeSandbox.created[-1])
    assert kw.pop("api_key") == FAKE_KEY                                  # host-side SDK auth only
    carried = _blobs(kw) + [b for _, v in FakeSandbox.seen for b in _blobs(v)]
    assert len(carried) > 20 and not any(FAKE_KEY.encode() in b for b in carried)
    assert not any("E2B" in name for kind, v in FakeSandbox.seen if kind == "envs" for name in v)
    run_dir = project / "runs" / man["run_id"]
    assert not any(FAKE_KEY.encode() in f.read_bytes() for f in run_dir.rglob("*") if f.is_file())
    assert man["env"]["e2b"]["key_source"] == "env E2B_API_KEY"
    out, err = capfd.readouterr()
    assert FAKE_KEY not in out + err


def test_key_from_an_owner_only_secrets_file(project, fake_e2b, monkeypatch, tmp_path, capfd):
    monkeypatch.delenv("E2B_API_KEY")
    sec = tmp_path / "secrets.env"
    sec.write_text("# axis-migrate\nOTHER=1\nexport E2B_API_KEY='" + FAKE_KEY + "'\n")
    sec.chmod(0o600)
    monkeypatch.setenv("AXIS_MIGRATE_SECRETS", str(sec))
    code, man = e2b_run(project, "--confirm-upload")
    assert code == 0, man["reason_codes"]
    assert FakeSandbox.created[-1]["api_key"] == FAKE_KEY and man["env"]["e2b"]["key_source"] == str(sec)
    assert "OTHER" not in FakeSandbox.created[-1]["envs"]
    assert FAKE_KEY not in "".join(capfd.readouterr())


def test_secrets_file_with_open_mode_is_refused_even_with_the_env_key(project, fake_e2b, monkeypatch, tmp_path, capfd):
    sec = tmp_path / "secrets.env"
    sec.write_text(f"E2B_API_KEY={FAKE_KEY}\n")
    sec.chmod(0o644)
    monkeypatch.setenv("AXIS_MIGRATE_SECRETS", str(sec))
    code, man = e2b_run(project, "--confirm-upload")
    out, err = capfd.readouterr()
    assert code == 13 and man is None and FakeSandbox.created == []
    assert "SECRETS_FILE_MODE" in err and "chmod 600" in err and FAKE_KEY not in out + err


def test_missing_key_names_the_configure_command(project, fake_e2b, monkeypatch, capfd):
    monkeypatch.delenv("E2B_API_KEY")
    code, man = e2b_run(project, "--confirm-upload")
    err = capfd.readouterr().err
    assert code == 13 and man is None and "E2B_KEY_MISSING" in err and "--configure e2b" in err


@pytest.mark.parametrize("exc_name,status,want", [("NotFoundException", None, "TEMPLATE_NOT_FOUND:"),
                                                  ("SandboxException", 404, "TEMPLATE_NOT_FOUND:"),
                                                  ("AuthenticationException", None, "E2B_KEY_REJECTED"),
                                                  ("SandboxException", 500, "E2B_CREATE_FAILED:SandboxException")])
def test_create_failures_are_environment_not_runner_bugs(project, fake_e2b, monkeypatch, exc_name, status, want):
    import e2b.exceptions as ex

    def boom(**kw):
        e = getattr(ex, exc_name)(f"{status or ''}: no such template {kw['api_key']}")
        if status:
            e.status_code = status
        raise e
    monkeypatch.setattr(run_e2b, "sandbox_factory", boom)
    code, man = e2b_run(project, "--confirm-upload", "--template", "someone/axis-geo-migrate-00000000")
    assert code == 13 and man["exit_code"] == 13
    assert any(r.startswith(want) for r in man["reason_codes"]), man["reason_codes"]
    if want == "TEMPLATE_NOT_FOUND:":
        assert "TEMPLATE_NOT_FOUND:someone/axis-geo-migrate-00000000" in man["reason_codes"]
    assert FAKE_KEY not in json.dumps(man)


def test_upload_cap_is_a_flag(project, fake_e2b, capfd):
    code, man = e2b_run(project, "--confirm-upload", "--upload-cap-mb", "0.001")
    assert code == 13 and man["reason_codes"] == ["UPLOAD_TOO_LARGE"] and FakeSandbox.created == []
    assert "cap 0.001 MB" in capfd.readouterr().err and man["env"]["e2b"]["upload_cap_mb"] == 0.001


def test_sandbox_lifetime_is_a_flag(project, fake_e2b):
    code, man = e2b_run(project, "--confirm-upload", "--sandbox-timeout-s", "100")
    assert code == 14 and f"BUDGET_EXHAUSTED_BEFORE:{S01}" in man["reason_codes"]
    assert FakeSandbox.created[-1]["timeout"] == 100 and man["env"]["e2b"]["timeout_s"] == 100


def test_default_template_prefers_the_users_own_build_for_this_lock(fake_e2b, monkeypatch, tmp_path):
    data = tmp_path / "plugin-data"
    data.mkdir(exist_ok=True)
    assert run_e2b.default_template() == rl.ac.e2b_template_name(rl.sha256_file(rl.LOCK))
    rec = {"name": "theirteam/axis-geo-migrate-x", "lock_sha256": rl.sha256_file(rl.LOCK)}
    (data / "e2b-template.json").write_text(json.dumps(rec))
    assert run_e2b.default_template() == "theirteam/axis-geo-migrate-x"
    (data / "e2b-template.json").write_text(json.dumps(dict(rec, lock_sha256="0" * 64)))   # another lock: ignored
    assert run_e2b.default_template() == rl.ac.e2b_template_name(rl.sha256_file(rl.LOCK))
