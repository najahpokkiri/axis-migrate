#!/usr/bin/env python3
"""codex_trust.py: are every axis-migrate hook registered AND trusted in Codex? (stdlib only)

Asks the Codex app server over stdio (`codex app-server`, JSON-RPC: initialize, then `hooks/list`) which hooks Codex
loads for a working directory, and checks that each axis-migrate hook (one per event and matcher in the plugin's
Codex hooks file) is present, enabled and has trustStatus `trusted`. Codex skips untrusted plugin hooks silently,
and any edit of a hook definition (a plugin update) resets its trust to `modified`, so run this after installing or
updating the plugin and at the start of every Codex session.

    codex_trust.py [--cwd DIR] [--codex PATH] [--json] [--timeout 20]
    exit 0  every hook present and trusted
    exit 1  some hook missing, untrusted, modified or disabled (the report says which; trust them in /hooks: select
            axis-migrate's definitions, press t, then start a new chat)
    exit 3  codex is not on PATH (skipped: nothing to check)
    exit 4  the app server did not answer (old Codex without hooks/list, or it failed to start)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
EVENT_NAMES = {"PreToolUse": "preToolUse", "PostToolUse": "postToolUse", "SessionStart": "sessionStart",
               "UserPromptSubmit": "userPromptSubmit", "Stop": "stop"}


def codex_hooks_file() -> Path:
    """The hooks file Codex loads for this plugin: `hooks` in .codex-plugin/plugin.json, else hooks/hooks.json."""
    try:
        man = json.loads((PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        man = {}
    h = man.get("hooks")
    if isinstance(h, str) and h:
        return (PLUGIN / h).resolve()
    return PLUGIN / "hooks" / "hooks.json"


def expected_hooks(path: Path | None = None) -> list[tuple[str, str | None]]:
    """(app-server event name, matcher) for every command handler in the plugin's Codex hooks file."""
    doc = json.loads((path or codex_hooks_file()).read_text(encoding="utf-8"))
    out = []
    for event, groups in (doc.get("hooks") or {}).items():
        for g in groups:
            for h in g.get("hooks") or []:
                if h.get("type") == "command":
                    out.append((EVENT_NAMES.get(event, event[:1].lower() + event[1:]), g.get("matcher")))
    return out


class AppServer:
    def __init__(self, codex: str, timeout: float):
        self.p = subprocess.Popen([codex, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.timeout = timeout
        self.n = 0

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        rid = self.n
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        box: dict = {}

        def read():
            for line in self.p.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == rid and ("result" in msg or "error" in msg):
                    box["msg"] = msg
                    return

        t = threading.Thread(target=read, daemon=True)
        t.start()
        t.join(self.timeout)
        if "msg" not in box:
            raise TimeoutError(f"no answer to {method} within {self.timeout} s")
        if "error" in box["msg"]:
            raise RuntimeError(f"{method}: {box['msg']['error']}")
        return box["msg"]["result"]

    def notify(self, method: str) -> None:
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.p.stdin.flush()

    def close(self) -> None:
        try:
            self.p.stdin.close()
            self.p.wait(timeout=5)
        except Exception:  # noqa: BLE001
            self.p.kill()


def ours(h: dict) -> bool:
    pid = h.get("pluginId") or ""
    cmd = h.get("command") or ""
    return pid.split("@")[0] == "axis-migrate" or "run-hook" in cmd and "axis-migrate" in str(h.get("sourcePath"))


def check(cwd: str, codex: str, timeout: float = 20.0) -> dict:
    srv = AppServer(codex, timeout)
    try:
        srv.request("initialize", {"clientInfo": {"name": "axis-migrate-codex-trust", "version": "1"}})
        srv.notify("initialized")
        res = srv.request("hooks/list", {"cwds": [cwd]})
    finally:
        srv.close()
    hooks = [h for e in res.get("data") or [] for h in e.get("hooks") or [] if ours(h)]
    errors = [x for e in res.get("data") or [] for x in e.get("errors") or []]
    want = expected_hooks()
    rows, problems = [], []
    for ev, matcher in want:
        found = [h for h in hooks if h.get("eventName") == ev and (h.get("matcher") or None) == (matcher or None)]
        if not found:
            problems.append(f"{ev} {matcher or '*'}: not registered")
            rows.append({"event": ev, "matcher": matcher, "status": "missing"})
            continue
        for h in found:
            st = h.get("trustStatus")
            ok = st in ("trusted", "managed") and h.get("enabled", True)
            rows.append({"event": ev, "matcher": matcher, "status": st, "enabled": h.get("enabled"),
                         "key": h.get("key"), "source": h.get("sourcePath")})
            if not ok:
                problems.append(f"{ev} {matcher or '*'}: {st}{'' if h.get('enabled', True) else ', disabled'}")
    extra = len(hooks) - len([r for r in rows if r["status"] != "missing"])
    if extra > 0:
        problems.append(f"{extra} extra axis-migrate hook registration(s) (a hooks file loaded twice?)")
    return {"ok": not problems, "cwd": cwd, "expected": len(want), "registered": len(hooks), "problems": problems,
            "errors": errors, "hooks": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check that every axis-migrate hook is registered and trusted in Codex.")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--codex", default=None, help="codex executable (default: codex on PATH)")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    codex = a.codex or shutil.which("codex")
    if not codex:
        msg = {"ok": None, "skipped": "codex not on PATH"}
        print(json.dumps(msg) if a.json else "codex_trust: skipped (codex not on PATH)")
        return 3
    try:
        res = check(os.path.realpath(a.cwd), codex, a.timeout)
    except (OSError, TimeoutError, RuntimeError, ValueError) as e:
        msg = {"ok": None, "error": f"{e.__class__.__name__}: {e}"}
        print(json.dumps(msg) if a.json else f"codex_trust: the Codex app server did not answer ({msg['error']})")
        return 4
    if a.json:
        print(json.dumps(res, sort_keys=True))
    elif res["ok"]:
        print(f"codex_trust: all {res['expected']} axis-migrate hooks registered and trusted")
    else:
        print("codex_trust: axis-migrate hooks NOT all active: " + "; ".join(res["problems"]) +
              ". Open /hooks (desktop: Review hooks), select axis-migrate's definitions, press t, then start a new "
              "chat.")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
