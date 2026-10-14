"""axis-migrate preflight: can this machine run the pipeline environment? (stdlib only; `bin/axpy --doctor`)

    bin/axpy --doctor [--no-sync] [--json]

Checks, one line each (OK / WARN / FAIL):
  uv          uv is installed (the environment is made only by `uv sync --frozen`; no pip, no system Python)
  cache       the environment cache directory is writable (${CLAUDE_PLUGIN_DATA} or ${XDG_CACHE_HOME:-~/.cache})
  env         the locked environment is synced, or syncs now (`--no-sync`: dry run only)
  strace      strace works (read/write ledger); without it (macOS): ledger = audit+cpl_debug, rung capped at
              sandbox-validated
  port        a local port can be bound on 127.0.0.1 (graph editor server)
Exit 0 when nothing FAILs (a WARN does not fail), 13 otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "lib"))
os.environ.setdefault("AXIS_MIGRATE_PLUGIN", str(PLUGIN))
import axis_common as ac  # noqa: E402


def find_uv() -> str | None:
    p = shutil.which("uv")
    if p:
        return p
    cand = Path.home() / ".local" / "bin" / "uv"
    return str(cand) if cand.is_file() and os.access(cand, os.X_OK) else None


def check_uv() -> tuple[str, str]:
    uv = find_uv()
    if not uv:
        return "FAIL", "UV_MISSING: install uv (https://docs.astral.sh/uv/); nothing falls back to system Python or pip"
    try:
        v = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "FAIL", f"UV_BROKEN: {uv}: {exc}"
    return "OK", f"{v} ({uv})"


def check_cache() -> tuple[str, str]:
    venv = ac.env_venv_dir()
    base = venv.parent
    try:
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=base, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return "FAIL", f"ENV_CACHE_NOT_WRITABLE: {base}: {exc.strerror or exc}"
    return "OK", f"environment cache {venv}"


def check_env(no_sync: bool) -> tuple[str, str]:
    uv = find_uv()
    if not uv:
        return "FAIL", "cannot sync without uv"
    venv, lock = ac.env_venv_dir(), ac.env_lock_sha256()
    marker = ac.env_lock_marker()
    if ac.env_python().is_file() and marker.is_file() and marker.read_text().strip() == lock:
        return "OK", f"synced from uv.lock {lock[:8]} ({ac.env_python()})"
    env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(venv), UV_PYTHON_PREFERENCE="only-managed")
    cmd = [uv, "sync", "--frozen", "--quiet", "--project", str(ac.env_project_dir())] + (["--dry-run"] if no_sync else [])
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, env=env)
    except subprocess.TimeoutExpired:
        return "FAIL", "ENV_SYNC_FAILED: uv sync timed out"
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout).strip().splitlines()[-1:] or [""]
        return "FAIL", f"ENV_SYNC_FAILED: {tail[0][:200]}"
    if no_sync:
        return "WARN", "not synced yet; `uv sync --frozen` dry run succeeded (first axpy call will sync)"
    marker.write_text(lock + "\n")
    return "OK", f"synced now from uv.lock {lock[:8]} ({ac.env_python()})"


def check_strace() -> tuple[str, str]:
    st = shutil.which("strace") or ("/usr/bin/strace" if Path("/usr/bin/strace").is_file() else None)
    fallback = ("read ledger = audit+cpl_debug (Python audit hook + GDAL CPL_DEBUG), rung capped at "
                "sandbox-validated")
    if not st:
        what = "macOS" if platform.system() == "Darwin" else platform.system()
        return "WARN", f"{what}/no strace: {fallback}"
    try:
        cp = subprocess.run([st, "-f", "--seccomp-bpf", "-qq", "-o", os.devnull, "true"], capture_output=True,
                            text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "WARN", f"strace present but unusable ({exc}): {fallback}"
    if cp.returncode != 0:
        return "WARN", f"strace present but cannot trace here ({cp.stderr.strip()[:120]}): {fallback}"
    return "OK", f"{st}: read and write ledger by strace"


def check_port() -> tuple[str, str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
    except OSError as exc:
        return "FAIL", f"PORT_BIND_FAILED: 127.0.0.1: {exc.strerror or exc} (graph editor server needs a local port)"
    return "OK", f"127.0.0.1 bind ok (got port {port})"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="axis-migrate preflight checks.")
    ap.add_argument("--no-sync", action="store_true", help="do not sync the environment; dry run only")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    results = [("uv", *check_uv()), ("cache", *check_cache()), ("env", *check_env(a.no_sync)),
               ("strace", *check_strace()), ("port", *check_port())]
    failed = any(r[1] == "FAIL" for r in results)
    if a.json:
        print(json.dumps({"ok": not failed, "platform": f"{platform.system()}-{platform.machine()}",
                          "checks": [{"check": c, "state": s, "message": m} for c, s, m in results]}, sort_keys=True))
    else:
        for c, s, m in results:
            print(f"{s:<4}  {c:<6}  {m}")
    return 13 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
