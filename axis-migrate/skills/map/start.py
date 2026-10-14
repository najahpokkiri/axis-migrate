#!/usr/bin/env python3
"""start.py — start the local GeoLibre map server for a migration (stdlib only).

    python3 start.py <migration> [--port 0]

Ensures the map env (mapenv.py), picks a free port on 127.0.0.1 (or uses --port), writes the per-session override
(<migration>/.axis/map/override/{deployment.json, plugin-registry.json}: sharing off, local empty plugin registry,
welcome off, embedOrigins = this server's own origin), starts serve.py in the background (pid file
<migration>/.axis/map/serve.pid, state <migration>/.axis/map/server.json, log serve.log) and prints ONE line of JSON:

    {"url_base": "http://127.0.0.1:<port>", "port": <port>, "data_root": "<migration>", "pid": <pid>}

`data_root` is served under /data/. A server already running for this migration is reused (same line printed).
Nothing is written into the plugin folder. Exit: 0 running · 2 bad migration folder · 13 env/bind failure.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import subprocess
import sys

import time
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True     # never write __pycache__ into the plugin folder
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mapenv  # noqa: E402

BIND_MSG = ("map server could not bind 127.0.0.1 — rerun this one command outside the sandbox (Codex: approve it "
            "with escalated permissions; Claude Code: sandbox.network.allowLocalBinding)")
EXIT_ENV = 13
READY_TIMEOUT_S = 15


def state_dir(migration: Path) -> Path:
    return Path(migration) / ".axis" / "map"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _probe(port: int, timeout: float = 2.0) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # never via a proxy
        with opener.open(f"http://127.0.0.1:{port}/deployment.json", timeout=timeout) as r:
            return r.status == 200 and b"__SELF_ORIGIN__" not in r.read()
    except Exception:
        return False


def running(migration: Path) -> dict | None:
    sd = state_dir(migration)
    try:
        st = json.loads((sd / "server.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(st, dict) or not _alive(int(st.get("pid") or 0)):
        return None
    if Path(st.get("data_root") or "").resolve() != Path(migration).resolve() or not _probe(int(st["port"])):
        return None
    return st


class BindRefused(Exception):
    pass


def pick_port(port: int) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return s.getsockname()[1]
    except PermissionError as e:
        raise BindRefused(str(e))
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM):
            raise BindRefused(str(e))
        raise
    finally:
        s.close()


def write_override(sd: Path, port: int) -> Path:
    ovr = sd / "override"
    ovr.mkdir(parents=True, exist_ok=True)
    origin = f"http://127.0.0.1:{port}"
    dep = json.loads((HERE / "override" / "deployment.json").read_text())
    dep["sharing"]["embedOrigins"] = [origin]
    for name, text in (("deployment.json", json.dumps(dep, indent=2, sort_keys=True) + "\n"),
                       ("plugin-registry.json", (HERE / "override" / "plugin-registry.json").read_text())):
        tmp = ovr / (name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, ovr / name)
    return ovr


def launch(migration: Path, app: Path, port: int) -> dict:
    sd = state_dir(migration)
    sd.mkdir(parents=True, exist_ok=True)
    ovr = write_override(sd, port)
    ready = sd / "ready.json"
    if ready.exists():
        ready.unlink()
    log = open(sd / "serve.log", "ab")
    py = sys.executable or "python3"
    p = subprocess.Popen([py, "-I", str(HERE / "serve.py"), str(app), str(migration), str(port), str(ovr),
                          "--ready", str(ready)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, close_fds=True)
    log.close()
    t0 = time.time()
    while time.time() - t0 < READY_TIMEOUT_S:
        if ready.is_file():
            break
        if p.poll() is not None:
            tail = (sd / "serve.log").read_text(errors="replace")[-600:]
            if "Permission denied" in tail or "Operation not permitted" in tail:
                raise BindRefused(tail)
            if "Address already in use" in tail:
                raise OSError(errno.EADDRINUSE, "address in use")
            raise RuntimeError(f"serve.py exited with {p.returncode}: {tail}")
        time.sleep(0.05)
    else:
        p.terminate()
        raise RuntimeError("serve.py did not report ready")
    if not _probe(port):
        p.terminate()
        raise BindRefused("bound but not reachable on 127.0.0.1")
    st = {"url_base": f"http://127.0.0.1:{port}", "port": port, "data_root": str(Path(migration).resolve()),
          "pid": p.pid, "app": str(app), "override": str(ovr), "started_at": int(time.time())}
    (sd / "serve.pid").write_text(f"{p.pid}\n")
    tmp = sd / "server.json.tmp"
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")
    os.replace(tmp, sd / "server.json")
    return st


def start(migration: Path, port: int = 0) -> dict:
    migration = Path(migration).resolve()
    st = running(migration)
    if st:
        return st
    py = mapenv.ensure()
    app = mapenv.app_dir(py)
    last = None
    for _ in range(4 if port == 0 else 1):
        p = pick_port(port)
        try:
            return launch(migration, app, p)
        except OSError as e:
            if e.errno != errno.EADDRINUSE or port:
                raise
            last = e
    raise last


def public(st: dict) -> dict:
    return {k: st[k] for k in ("url_base", "port", "data_root", "pid")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Start the local GeoLibre map server for a migration folder.")
    ap.add_argument("migration", help="the migration folder (served under /data/)")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (0 = pick a free one)")
    a = ap.parse_args(argv)
    mig = Path(a.migration)
    if not mig.is_dir():
        print(f"start.py: {mig} is not a folder", file=sys.stderr)
        return 2
    if HERE.parents[1].resolve() in [mig.resolve(), *mig.resolve().parents]:
        print("start.py: refusing to serve a folder inside the plugin", file=sys.stderr)
        return 2
    try:
        st = start(mig, a.port)
    except BindRefused:
        print(BIND_MSG)
        return EXIT_ENV
    except mapenv.EnvError as e:
        print(f"start.py: {e}", file=sys.stderr)
        return EXIT_ENV
    except (OSError, RuntimeError) as e:
        print(f"start.py: {e}", file=sys.stderr)
        return EXIT_ENV
    print(json.dumps(public(st), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
