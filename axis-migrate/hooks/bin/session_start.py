#!/usr/bin/env python3
"""H1 SessionStart (startup|resume|clear|compact|fork): status line, guard list, unacked graph edits, and
`export GDAL_PAM_ENABLED=NO` into $CLAUDE_ENV_FILE (D8-11), the supported-scope line, and the E2B key from the
Claude plugin option (CLAUDE_PLUGIN_OPTION_E2B_API_KEY, hook-only env) copied to ${CLAUDE_PLUGIN_DATA}/secrets.env
(0600, atomic, never logged) for run_e2b.py. Informational: fails open.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _hooklib as hl  # noqa: E402

GUARDS = [
    "1. The legacy folder is read-only (Write/Edit and Bash write verbs are refused; gdalinfo -stats, gdal_edit.py "
    "and gdaladdo on legacy files too). GDAL_PAM_ENABLED=NO is set for your Bash.",
    "2. graph.json changes only through graph.py apply --actor agent; migration/runs/** and runs/index.jsonl only "
    "through the runner and seal.py.",
    "3. pipeline/<step>.py may be written only after that step's plan is approved; steps run only through "
    "run_local.py / run_e2b.py (direct runs are UNLEDGERED_RUN).",
    "4. decisions.md and known-differences.md are append-only; tolerance changes after a run, KDs with a human "
    "owner and fitted->answer params need the user's approval.",
    "5. Every write under pipeline/, tests/ and params.yaml is scanned (rules S01-S18); fix block findings, there "
    "is no inline suppression.",
    "6. The Stop gate refuses completion or parity claims above the computed status; quote the status line.",
]


def status_line(mig) -> str:
    try:
        sys.path.insert(0, str(hl.PLUGIN / "skills" / "report"))
        import ladder  # type: ignore
        res = ladder.compute(str(mig))
        line = res.get("status_line") if isinstance(res, dict) else None
        return line or "axis-migrate status: unknown (ladder returned no status line)"
    except ImportError:
        return "axis-migrate status: unknown (ladder unavailable)"
    except Exception as e:  # noqa: BLE001 - informational
        return f"axis-migrate status: unknown (ladder failed: {e.__class__.__name__})"


def latest_run_line(mig) -> str | None:
    """`ladder.py --brief` when the newest run is not sealed (Lux e2e: after nine runs the status still said 'run
    none'), so a resumed or compacted session sees where the work stands without reading it as a rung."""
    try:
        sys.path.insert(0, str(hl.PLUGIN / "skills" / "report"))
        import ladder  # type: ignore
        line = ladder.brief(str(mig))
        return line if "unsealed" in line else None
    except Exception:  # noqa: BLE001 - informational
        return None


def unacked_edits(mig) -> int:
    try:
        sys.path.insert(0, str(hl.PLUGIN / "skills" / "discover"))
        import graph as gmod  # type: ignore
        res = gmod.unacked_user_edits(str(mig))
        return len(res) if isinstance(res, (list, tuple)) else int(res)
    except Exception:  # noqa: BLE001 - fall back to reading the edit log directly
        return unacked_local(mig)


def unacked_local(mig) -> int:
    g = hl.load_graph(mig)
    acked = int(((g.get("acks") or {}).get("agent_seq")) or 0)
    log = mig / "graph-edits.jsonl"
    n = 0
    if log.is_file():
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("actor") == "user" and int(rec.get("seq") or 0) > acked:
                n += 1
    return n


SCOPE = ("axis-migrate scope: QGIS projects/models and Python scripts today; ArcGIS Pro projects partial; "
         "ModelBuilder (.tbx/.mxd) inventory only.")
E2B_OPTION = "CLAUDE_PLUGIN_OPTION_E2B_API_KEY"   # Claude userConfig (sensitive): reaches hooks only, never the shell
SECRETS_NAME = "secrets.env"


def write_plugin_secrets() -> str | None:
    """Copy the E2B key from the plugin option into ${CLAUDE_PLUGIN_DATA}/secrets.env (0600, one `E2B_API_KEY=` line,
    atomic) for run_e2b.py. Never logs the value. Returns a short note when something is wrong, else None."""
    key = os.environ.get(E2B_OPTION)
    data_dir = os.environ.get("CLAUDE_PLUGIN_DATA")
    if not key or not data_dir:
        return None
    key = key.strip()
    if not key or any(c.isspace() or c in "\"'$`\\" for c in key) or not key.isascii():
        return "the E2B API key option has an unexpected format; it was not written (re-enter it in the plugin settings)"
    import tempfile
    d = os.path.realpath(data_dir)
    os.makedirs(d, mode=0o700, exist_ok=True)
    target = os.path.join(d, SECRETS_NAME)
    line = f"E2B_API_KEY={key}\n"
    try:
        with open(target, encoding="utf-8") as fh:
            if fh.read() == line and (os.stat(target).st_mode & 0o077) == 0:
                return None
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(prefix=".secrets.", suffix=".tmp", dir=d)
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(line)
        os.replace(tmp, target)
        os.chmod(target, 0o600)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return None


def main() -> int:
    data = hl.read_input()
    mig = hl.find_migration(data)
    try:  # always, even before migration/ exists: the Codex start skill checks it before /migrate starts
        hl.write_hooks_marker(data, mig)
    except OSError:
        pass
    try:
        secret_note = write_plugin_secrets()
    except OSError as e:
        secret_note = f"could not write the plugin secrets file ({e.__class__.__name__})"
    if mig is None:
        if secret_note:
            hl.emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                            "additionalContext": hl.ascii_text("axis-migrate: " + secret_note)}})
        return 0
    if mig is None:
        return 0
    notes = []
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            existing = open(env_file, encoding="utf-8").read() if os.path.exists(env_file) else ""
            if "GDAL_PAM_ENABLED=NO" not in existing:
                with open(env_file, "a", encoding="utf-8") as fh:
                    fh.write("export GDAL_PAM_ENABLED=NO\n")
        except OSError as e:
            notes.append(f"could not write CLAUDE_ENV_FILE ({e.__class__.__name__}); prefix GDAL commands with "
                         "GDAL_PAM_ENABLED=NO")
    elif hl.harness(data) == "codex":
        notes.append("Codex: no CLAUDE_ENV_FILE; prefix every GDAL/rasterio command with GDAL_PAM_ENABLED=NO "
                     "(scripts/axpy sets it for plugin scripts). Codex cannot ask for permission: changes that need the "
                     "user's approval are refused; ask in chat and let the user make them.")
    else:
        notes.append("CLAUDE_ENV_FILE not provided; prefix GDAL commands with GDAL_PAM_ENABLED=NO")
    try:
        n = unacked_edits(mig)
    except Exception as e:  # noqa: BLE001
        n = None
        notes.append(f"unacked graph edits: unknown ({e.__class__.__name__})")
    if secret_note:
        notes.append(secret_note)
    lines = [status_line(mig)] + [x for x in (latest_run_line(mig),) if x] + [f"migration: {mig}", SCOPE]
    try:
        integ = hl.graph_integrity(mig)
    except Exception as e:  # noqa: BLE001 - informational
        integ = None
        notes.append(f"graph integrity check failed: {e.__class__.__name__}")
    if integ:
        lines.append("WARNING GRAPH_EDITED_OUTSIDE_APPLY: graph.json or graph-edits.jsonl was changed outside "
                     "graph.py (" + "; ".join(integ) + "). graph.py refuses to work until it is restored from "
                     ".graph/rev/<n>.json or the user decides; no status claim is possible meanwhile.")
    if n:
        lines.append(f"The user has {n} unacked graph edit(s): run graph.py diff, act on each, then graph.py ack.")
    lines.append("axis-migrate guards active:")
    lines += GUARDS
    lines += notes
    hl.emit({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                    "additionalContext": hl.ascii_text("\n".join(lines))}})
    return 0


if __name__ == "__main__":
    hl.run_inform(main, "session_start", "SessionStart")
