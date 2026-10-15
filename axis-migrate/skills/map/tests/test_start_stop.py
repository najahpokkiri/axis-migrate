"""start.py / stop.py: background server per migration, per-session override, sandbox bind message, env rules."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

import mapenv
import start as S
from conftest import MAP, PLUGIN, map_python, run_cli

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _plugin_listing() -> list[tuple[str, int, float]]:
    out = []
    for p in sorted(MAP.rglob("*")):
        if "__pycache__" in p.parts or ".pytest_cache" in p.parts:
            continue
        st = p.stat()
        out.append((str(p.relative_to(MAP)), st.st_size, st.st_mtime))
    return out


def test_start_stop_roundtrip(tmp_path):
    map_python()
    mig = tmp_path / "migration"
    (mig / "runs").mkdir(parents=True)
    (mig / "runs" / "hello.txt").write_text("hi\n")
    before = _plugin_listing()
    cp = run_cli("start.py", mig)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    lines = cp.stdout.strip().splitlines()
    assert len(lines) == 1
    info = json.loads(lines[0])
    assert set(info) == {"url_base", "port", "data_root", "pid", "engine_preset", "geolibre"}
    assert info["engine_preset"] is True and info["geolibre"] == "3.4.0"
    port = info["port"]
    assert info["url_base"] == f"http://127.0.0.1:{port}" and Path(info["data_root"]) == mig.resolve()
    try:
        sd = mig / ".axis" / "map"
        assert int((sd / "serve.pid").read_text()) == info["pid"]
        dep = json.loads((sd / "override" / "deployment.json").read_text())
        assert dep["sharing"] == {"shareUrl": "off", "embedOrigins": [f"http://127.0.0.1:{port}"]}
        assert dep["branding"] == {"welcome": False} and dep["plugins"] == {"registryUrl": "plugin-registry.json"}
        assert json.loads((sd / "override" / "plugin-registry.json").read_text()) == []
        with OPENER.open(f"{info['url_base']}/deployment.json", timeout=10) as r:
            assert json.loads(r.read()) == dep                            # served from the per-session override
        # the override that sets the GPU raster engine and the readiness line, and an empty plugin blocklist (the
        # app's 404 on /blocklist.json was the red Diagnostics badge)
        with OPENER.open(f"{info['url_base']}/geolibre-runtime-config.js", timeout=10) as r:
            js = r.read().decode()
            assert "maplibre-gl-raster" in js and "data-axis-map" in js and "__GEOLIBRE_DEPLOYMENT_ENV__" in js
        with OPENER.open(f"{info['url_base']}/blocklist.json", timeout=10) as r:
            assert json.loads(r.read()) == {"blocked": []}
        with OPENER.open(f"{info['url_base']}/data/runs/hello.txt", timeout=10) as r:
            assert r.read() == b"hi\n"
        # the plugin's own template is untouched
        assert json.loads((MAP / "override" / "deployment.json").read_text())["sharing"]["embedOrigins"] == \
            ["__SELF_ORIGIN__"]
        cp2 = run_cli("start.py", mig)                                   # second start reuses the server
        assert cp2.returncode == 0 and json.loads(cp2.stdout) == info
    finally:
        cp3 = run_cli("stop.py", mig)
    assert cp3.returncode == 0 and "stopped" in cp3.stdout
    t0 = time.time()
    while time.time() - t0 < 5:
        try:
            os.kill(info["pid"], 0)
        except OSError:
            break
        time.sleep(0.05)
    with pytest.raises(OSError):
        os.kill(info["pid"], 0)
    assert not (mig / ".axis" / "map" / "serve.pid").exists()
    cp4 = run_cli("stop.py", mig)
    assert cp4.returncode == 0 and "not running" in cp4.stdout
    assert _plugin_listing() == before                                    # nothing written into the plugin


def test_bind_refused_prints_the_sandbox_message(tmp_path, monkeypatch, capsys):
    mig = tmp_path / "migration"
    mig.mkdir()

    def refuse(port):
        raise S.BindRefused("Operation not permitted")
    monkeypatch.setattr(S, "pick_port", refuse)
    monkeypatch.setattr(S.mapenv, "ensure", lambda quiet=True: Path("/nonexistent/python"))
    monkeypatch.setattr(S.mapenv, "app_dir", lambda py=None: tmp_path)
    rc = S.main([str(mig)])
    out = capsys.readouterr().out.strip()
    assert rc == 13
    assert out == ("map server could not bind 127.0.0.1 — rerun this one command outside the sandbox (Codex: "
                   "approve it with escalated permissions; Claude Code: sandbox.network.allowLocalBinding)")


def test_explicit_port_in_use_fails_cleanly(tmp_path):
    import socket
    map_python()
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        mig = tmp_path / "migration"
        mig.mkdir()
        cp = run_cli("start.py", mig, "--port", s.getsockname()[1])
        assert cp.returncode == 13 and not (mig / ".axis" / "map" / "serve.pid").exists()
    finally:
        s.close()


def test_refuses_to_serve_the_plugin():
    cp = run_cli("start.py", PLUGIN / "skills")
    assert cp.returncode == 2 and "plugin" in cp.stderr


# ------------------------------------------------------------------------------------------------- env placement
def test_env_location_rules(monkeypatch, tmp_path):
    lock8 = mapenv.lock_sha256()[:8]
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "cpd"))
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "pd"))
    assert mapenv.venv_dir() == tmp_path / "cpd" / f"map-env-{lock8}"
    monkeypatch.delenv("CLAUDE_PLUGIN_DATA")
    assert mapenv.venv_dir() == tmp_path / "pd" / f"map-env-{lock8}"
    monkeypatch.delenv("PLUGIN_DATA")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    assert mapenv.venv_dir() == tmp_path / "xdg" / "axis-migrate" / f"map-env-{lock8}"
    assert not mapenv.venv_dir().resolve().is_relative_to(PLUGIN.resolve())


def test_env_separate_from_pipeline_lock():
    run_lock = PLUGIN / "skills" / "run" / "env" / "uv.lock"
    assert (MAP / "env" / "uv.lock").read_bytes() != run_lock.read_bytes()
    txt = (MAP / "env" / "pyproject.toml").read_text()
    assert '"geolibre==3.4.0"' in txt
    assert 'name = "geolibre"\nversion = "3.4.0"' in (MAP / "env" / "uv.lock").read_text()


def test_env_refuses_a_location_inside_the_plugin(monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(PLUGIN / "skills" / "map" / "env" / "_data"))
    with pytest.raises(mapenv.EnvError, match="ENV_INSIDE_PLUGIN"):
        mapenv.ensure()
    assert not (PLUGIN / "skills" / "map" / "env" / "_data").exists()


def test_env_install_idempotent(tmp_path):
    """Sync into a fresh data dir from uv's cache (offline), then again: the second call never needs uv."""
    if not mapenv._uv():
        pytest.skip("uv not installed")
    py3 = shutil.which("python3")
    env = dict(os.environ, CLAUDE_PLUGIN_DATA=str(tmp_path / "data"), UV_OFFLINE="1")
    code = "import mapenv, json; print(json.dumps(str(mapenv.ensure())))"
    argv = [py3, "-I", "-c", f"import sys; sys.path.insert(0, {str(MAP)!r}); {code}"]
    cp = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=900)
    if cp.returncode != 0:
        pytest.skip(f"offline sync impossible here (packages not in uv's cache): {cp.stderr.strip()[-200:]}")
    py = Path(json.loads(cp.stdout))
    venv = py.parent.parent
    assert venv.parent == tmp_path / "data"
    assert (venv / ".axis-lock-sha256").read_text().strip() == mapenv.lock_sha256()
    mark = (venv / ".axis-lock-sha256").stat().st_mtime_ns
    cp2 = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                         env=dict(env, PATH="/nonexistent", HOME="/nonexistent-home"))   # uv unreachable
    assert cp2.returncode == 0, cp2.stderr
    assert Path(json.loads(cp2.stdout)) == py
    assert (venv / ".axis-lock-sha256").stat().st_mtime_ns == mark
    ok = subprocess.run([str(py), "-c", "import geolibre, rasterio; print(geolibre.__version__)"],
                        capture_output=True, text=True)
    assert ok.stdout.strip() == "3.4.0"
    cpa = subprocess.run([py3, "-I", str(MAP / "mapenv.py"), "--app"], capture_output=True, text=True, env=env,
                         timeout=60)
    assert cpa.returncode == 0 and (Path(cpa.stdout.strip()) / "index.html").is_file()
