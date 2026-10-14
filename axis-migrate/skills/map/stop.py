#!/usr/bin/env python3
"""stop.py — stop the map server start.py started for a migration (stdlib only).

    python3 stop.py <migration>

Reads <migration>/.axis/map/serve.pid, checks the process is this plugin's serve.py, sends SIGTERM (SIGKILL after
3 s) and removes the pid/state files. Prints one line. Exit: 0 stopped or not running · 2 bad folder.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

import time
from pathlib import Path

sys.dont_write_bytecode = True     # never write __pycache__ into the plugin folder
HERE = Path(__file__).resolve().parent


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                              timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                   # a zombie child of ours counts as gone
        done, _ = os.waitpid(pid, os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True


def stop(migration: Path) -> str:
    sd = Path(migration) / ".axis" / "map"
    pidf = sd / "serve.pid"
    try:
        pid = int(pidf.read_text().strip())
    except (OSError, ValueError):
        return "map server not running (no pid file)"
    msg = f"map server (pid {pid}) not running"
    if _alive(pid):
        if "serve.py" not in _cmdline(pid):
            return f"pid {pid} is not the map server; left alone (remove {pidf} if stale)"
        os.kill(pid, signal.SIGTERM)
        t0 = time.time()
        while _alive(pid) and time.time() - t0 < 3:
            time.sleep(0.05)
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)
        msg = f"map server (pid {pid}) stopped"
    for name in ("serve.pid", "server.json", "ready.json"):
        try:
            (sd / name).unlink()
        except FileNotFoundError:
            pass
    return msg


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Stop the local GeoLibre map server of a migration folder.")
    ap.add_argument("migration")
    a = ap.parse_args(argv)
    if not Path(a.migration).is_dir():
        print(f"stop.py: {a.migration} is not a folder", file=sys.stderr)
        return 2
    print(stop(Path(a.migration)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
