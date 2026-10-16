#!/usr/bin/env python3
"""mapenv.py — the map-view environment (GeoLibre 3.4.0 + the view writer), stdlib only.

    python3 mapenv.py                 sync if needed, print the env's python path
    python3 mapenv.py --app           sync if needed, print the GeoLibre web-app folder (geolibre/static/app)
    python3 mapenv.py --where         print where the env lives (no sync)

The lock is skills/map/env/uv.lock (separate from the pipeline lock in skills/run/env). On first use it is installed
with `uv sync --frozen` into

    ${CLAUDE_PLUGIN_DATA:-${PLUGIN_DATA:-${XDG_CACHE_HOME:-~/.cache}/axis-migrate}}/map-env-<lock8>

(UV_PROJECT_ENVIRONMENT), and a marker `.axis-lock-sha256` records the lock it was synced from (same rule as
scripts/axpy). Nothing is ever written into the plugin folder; uv's own cache stays wherever uv keeps it.
Exit: 0 ok · 13 uv missing / sync failed / cache not writable.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys

from pathlib import Path

sys.dont_write_bytecode = True     # never write __pycache__ into the plugin folder
HERE = Path(__file__).resolve().parent
ENV_PROJECT = HERE / "env"
MARKER = ".axis-lock-sha256"
EXIT_ENV = 13
SANDBOX_HINT = (" — in a sandbox, rerun this one command with approval (Codex: escalated permissions; Claude Code: "
                "outside the sandbox)")


def lock_sha256() -> str:
    return hashlib.sha256((ENV_PROJECT / "uv.lock").read_bytes()).hexdigest()


def data_root() -> Path:
    for var in ("CLAUDE_PLUGIN_DATA", "PLUGIN_DATA"):
        v = os.environ.get(var)
        if v:
            return Path(v).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(cache).expanduser() / "axis-migrate"


def venv_dir() -> Path:
    return data_root() / f"map-env-{lock_sha256()[:8]}"


def python_path(venv: Path | None = None) -> Path:
    """Scripts/python.exe on Windows, bin/python elsewhere (same rule as lib/axis_common.venv_python)."""
    venv = venv_dir() if venv is None else venv
    return venv / "Scripts" / "python.exe" if os.name == "nt" else venv / "bin" / "python"


def _uv() -> str | None:
    """Same search as lib/axis_common.find_uv (this file stays standalone)."""
    u = shutil.which("uv")
    if u:
        return u
    home = Path.home()
    cands = [home / ".local" / "bin" / "uv"]
    if os.name == "nt":
        cands = [home / ".local" / "bin" / "uv.exe"]
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if base:
                for pat in ("Python/Python3*/Scripts/uv.exe", "Programs/Python/Python3*/Scripts/uv.exe"):
                    cands += sorted(Path(base).glob(pat), reverse=True)
    return next((str(c) for c in cands if c.is_file() and os.access(c, os.X_OK)), None)


def is_synced() -> bool:
    venv = venv_dir()
    try:
        return python_path(venv).exists() and (venv / MARKER).read_text().strip() == lock_sha256()
    except OSError:
        return False


def _lock(fh) -> None:
    """Exclusive lock held until fh closes: flock on POSIX, msvcrt on Windows (no fcntl there; the lock file holds
    no data, so locking its first byte blocks no reader)."""
    if os.name == "nt":
        import msvcrt
        import time
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                time.sleep(0.1)
    import fcntl
    fcntl.flock(fh, fcntl.LOCK_EX)


class EnvError(Exception):
    pass


def ensure(quiet: bool = True) -> Path:
    """Sync the env if its marker does not match the lock; return the env's python. Idempotent and serialised by a
    file lock next to the env, so two first uses at once do not race."""
    if is_synced():
        return python_path()
    venv = venv_dir()
    plugin = HERE.parents[1].resolve()
    if venv.resolve().is_relative_to(plugin):
        raise EnvError(f"ENV_INSIDE_PLUGIN: {venv} is inside the plugin folder; set CLAUDE_PLUGIN_DATA elsewhere")
    uv = _uv()
    if not uv:
        raise EnvError("UV_MISSING (install uv; the map env is never built with pip or the system Python)")
    try:
        venv.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise EnvError(f"ENV_CACHE_NOT_WRITABLE: {venv.parent} ({e})" + SANDBOX_HINT)
    with open(venv.parent / f".{venv.name}.lock", "w") as lk:
        _lock(lk)
        if is_synced():
            return python_path()
        env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv), UV_PYTHON_PREFERENCE="only-managed")
        cmd = [uv, "sync", "--frozen", "--project", str(ENV_PROJECT)] + (["--quiet"] if quiet else [])
        cp = subprocess.run(cmd, env=env, stdout=sys.stderr, stderr=sys.stderr)
        if cp.returncode != 0:
            raise EnvError(f"ENV_SYNC_FAILED (uv sync --frozen of {ENV_PROJECT} into {venv}) "
                               "(first use needs network: in Codex approve this one command with escalated "
                               "permissions)")
        (venv / MARKER).write_text(lock_sha256() + "\n")
    return python_path()


def app_dir(py: Path | None = None) -> Path:
    """The bundled GeoLibre web app inside the env (no import of geolibre needed)."""
    venv = (py or python_path()).parent.parent
    pattern = "Lib/site-packages" if os.name == "nt" else "lib/python3*/site-packages"   # Windows venv layout
    for c in sorted(venv.glob(f"{pattern}/geolibre/static/app")):
        if (c / "index.html").is_file():
            return c
    raise EnvError(f"GEOLIBRE_APP_MISSING: no geolibre/static/app/index.html under {venv}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Map-view environment (GeoLibre + view writer): sync and locate.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--app", action="store_true", help="print the GeoLibre web-app folder")
    g.add_argument("--where", action="store_true", help="print the env folder without syncing")
    a = ap.parse_args(argv)
    if a.where:
        print(venv_dir())
        return 0
    try:
        py = ensure()
        print(app_dir(py) if a.app else py)
    except EnvError as e:
        print(f"mapenv: {e}", file=sys.stderr)
        return EXIT_ENV
    return 0


if __name__ == "__main__":
    sys.exit(main())
