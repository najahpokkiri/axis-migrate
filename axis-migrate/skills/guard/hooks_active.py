#!/usr/bin/env python3
"""hooks_active.py: did the axis-migrate hooks run in this session? (stdlib only; for the Codex start skill).

SessionStart (hooks/bin/session_start.py) writes, in every harness and even before migration/ exists:

    ${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.cache/axis-migrate}}/hooks-active.json
    {"schema": "axis-migrate.hooks-active.v1", "at": "<UTC ISO>", "harness": "codex"|"claude",
     "cwd_sha256": sha256(realpath(session cwd)), "session_id", "source", "migration_found", "plugin_root", "hooks"}

    hooks_active.py [--cwd DIR] [--max-age-h 24] [--json]
    exit 0: a marker for this cwd younger than --max-age-h exists (hooks are active); exit 1: not shown to be active.

A missing or stale marker proves nothing about safety: treat the session as unguarded (CODEX.md, script halves).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path


def marker_path() -> Path:
    d = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser(
        "~/.cache/axis-migrate")
    return Path(d) / "hooks-active.json"


def check(cwd: str, max_age_h: float = 24.0) -> dict:
    p = marker_path()
    want = hashlib.sha256(os.path.realpath(cwd).encode("utf-8")).hexdigest()
    if not p.is_file():
        return {"active": False, "reason": f"no marker at {p}", "marker": str(p)}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
        at = datetime.datetime.strptime(rec["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return {"active": False, "reason": f"marker unreadable: {e}", "marker": str(p)}
    age_h = (datetime.datetime.now(datetime.timezone.utc) - at).total_seconds() / 3600
    out = {"marker": str(p), "harness": rec.get("harness"), "at": rec.get("at"), "age_h": round(age_h, 2),
           "session_id": rec.get("session_id")}
    if rec.get("schema") != "axis-migrate.hooks-active.v1":
        return dict(out, active=False, reason="unknown marker schema")
    if rec.get("cwd_sha256") != want:
        return dict(out, active=False, reason="marker is for another working directory")
    if age_h > max_age_h:
        return dict(out, active=False, reason=f"marker older than {max_age_h} h")
    return dict(out, active=True, reason="SessionStart hook ran for this working directory")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check the SessionStart hooks-active marker.")
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--max-age-h", type=float, default=24.0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    res = check(a.cwd, a.max_age_h)
    if a.json:
        print(json.dumps(res, sort_keys=True))
    else:
        print(("hooks active" if res["active"] else "hooks NOT shown active") + f": {res['reason']}")
    return 0 if res["active"] else 1


if __name__ == "__main__":
    sys.exit(main())
