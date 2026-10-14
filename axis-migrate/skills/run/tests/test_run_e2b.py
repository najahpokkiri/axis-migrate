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

    def __init__(self, kw: dict, remote: Path):
        self.kw = kw
        self.sandbox_id = f"fake-{len(FakeSandbox.created)}"
        self.remote = remote
        self.commands = SimpleNamespace(run=self._run)
        self.files = SimpleNamespace(write=self._write, read=self._read)

    def _run(self, cmd, timeout=60, envs=None, cwd=None, **_):
        import e2b
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
    FakeSandbox.created, FakeSandbox.killed = [], []

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
    assert kw["allow_internet_access"] is False and kw["timeout"] == 600
    assert kw["metadata"] == {"run_id": man["run_id"]} and kw["template"] == run_e2b.default_template()
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
