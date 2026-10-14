"""Map-view tests (synthetic data only; no fixture facts).

Runs under either interpreter: `bin/axpy -m pytest skills/map/tests` (run_tests.sh) or the map env's python.
Everything that needs geolibre goes through the CLIs (views.py / check.py re-execute themselves in the map env) or
through `map_py()`, which runs a snippet in the map env.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ["GDAL_PAM_ENABLED"] = "NO"

import pytest  # noqa: E402

HERE = Path(__file__).resolve().parent
MAP = HERE.parent
PLUGIN = MAP.parents[1]
for p in (str(MAP), str(HERE), str(PLUGIN / "lib")):
    if p not in sys.path:
        sys.path.insert(0, p)

import mapenv  # noqa: E402

PY3 = shutil.which("python3") or sys.executable


def map_python() -> Path:
    try:
        return mapenv.ensure()
    except mapenv.EnvError as e:
        pytest.skip(f"map env unavailable: {e}")


def map_py(code: str, *args: str) -> dict:
    """Run `code` in the map env (views.py importable); it must print one JSON document."""
    py = map_python()
    cp = subprocess.run([str(py), "-c", f"import sys; sys.path.insert(0, {str(MAP)!r}); " + code, *args],
                        capture_output=True, text=True, timeout=600)
    assert cp.returncode == 0, cp.stderr[-3000:]
    return json.loads(cp.stdout)


def run_cli(script: str, *args, py: str | None = None, timeout=600, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([py or PY3, str(MAP / script), *map(str, args)], capture_output=True, text=True,
                          timeout=timeout, env=env)


@pytest.fixture(scope="session")
def synth_run(tmp_path_factory) -> Path:
    """A synthetic project with compare results and views (built once per session)."""
    map_python()
    import synth_map
    run = synth_map.build(tmp_path_factory.mktemp("synth") / "proj")
    cp = run_cli("views.py", "run", "--run", run)
    assert cp.returncode == 0, cp.stdout + cp.stderr
    return run


def views_doc(run: Path) -> dict:
    return json.loads((run / "verify" / "views" / "views.json").read_text())


def entry(run: Path, ds: str) -> dict:
    return next(e for e in views_doc(run)["datasets"] if e["dataset"] == ds)
