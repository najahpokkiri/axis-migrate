"""_hooklib: shared plumbing for the axis-migrate hook scripts (stdlib only; run as `python3 -I`).

- `read_input()`        hook JSON from stdin (must be an object; anything else raises -> guard fails closed)
- `find_migration()`    walk up from stdin `cwd` to migration/graph.json; None -> the hook stays idle
- `pre_decision()`      PreToolUse JSON decision (deny/ask) on stdout, ASCII only
- `run_guard()`         fail closed: any exception -> exit 2 with an ASCII reason on stderr
- `run_inform()`        fail open: any exception -> exit 0 (informational hooks)
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]
for _p in (PLUGIN / "lib", PLUGIN / "skills" / "guard"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))



def __getattr__(name):  # PEP 562: import axis_common only when a hook needs it (keeps idle hooks < 100 ms)
    if name == "ac":
        import axis_common
        globals()["ac"] = axis_common
        return axis_common
    raise AttributeError(name)


def ascii_text(s: str, limit: int = 4000) -> str:
    s = s.replace("→", "->").replace("—", "-").replace("–", "-").replace("·", "|")
    s = s.encode("ascii", "replace").decode("ascii")
    return s if len(s) <= limit else s[: limit - 3] + "..."


_INPUT: dict = {}
ASK_NOTE_CODEX = (" [Codex has no permission prompt for hooks, so this is refused. Tell the user what you wanted to "
                  "change and why; only the person makes or approves this change.]")


def read_input() -> dict:
    raw = sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    _INPUT.clear()
    _INPUT.update(data)
    return data


def harness(data: dict | None = None) -> str:
    """'codex' or 'claude'. Codex-only signals: env PLUGIN_ROOT/PLUGIN_DATA (Claude Code sets only CLAUDE_*),
    tool_name apply_patch, a Stop turn_id. Codex also sets CLAUDE_PLUGIN_ROOT, so that one proves nothing."""
    d = data if data is not None else _INPUT
    if os.environ.get("PLUGIN_ROOT") or os.environ.get("PLUGIN_DATA"):
        return "codex"
    if d.get("tool_name") == "apply_patch" or "turn_id" in d:
        return "codex"
    return "claude"


def find_migration_dir(start) -> Path | None:
    """Same walk as axis_common.find_migration_dir, without importing it (idle fast path)."""
    p = Path(start).resolve()
    for cand in [p, *p.parents]:
        if (cand / "migration" / "graph.json").is_file():
            return cand / "migration"
        if cand.name == "migration" and (cand / "graph.json").is_file():
            return cand
    return None


def find_migration(data: dict) -> Path | None:
    """migration/ for this session: from stdin `cwd` first, then the hook process cwd (the Codex desktop app runs hook
    processes in the session root while the shell may be in a subfolder, and vice versa)."""
    bases = [data.get("cwd"), os.getcwd()]
    for b in dict.fromkeys(x for x in bases if x):
        try:
            m = find_migration_dir(b)
        except OSError:
            m = None
        if m is not None:
            return m
    return None


def require_tool_payload(data: dict) -> str:
    """PreToolUse guards: a payload without a tool name is malformed -> the guard fails closed (exit 2)."""
    tool = data.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise ValueError("PreToolUse payload without tool_name (missing, empty or unparseable stdin)")
    return tool


def abs_paths(p: str, cwd: str | None) -> list[Path]:
    """Every place a relative path could land: against stdin `cwd` (primary) and against the hook process cwd when
    that differs. Guards deny when ANY of them is protected (Codex desktop cwd trap)."""
    out = [abs_path(p, cwd)]
    q = os.path.expanduser(p)
    if not os.path.isabs(q):
        alt = Path(os.path.realpath(os.path.join(os.getcwd(), q)))
        if alt not in out:
            out.append(alt)
    return out


def abs_path(p: str, cwd: str | None) -> Path:
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return Path(os.path.realpath(p))


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


def pre_decision(decision: str, reason: str) -> None:
    if decision == "ask" and harness() == "codex":
        # Codex parses "ask" but does not support it: the hook run is marked failed and the tool call CONTINUES.
        decision, reason = "deny", reason + ASK_NOTE_CODEX
    emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision,
                                 "permissionDecisionReason": ascii_text(reason)}})


def run_guard(main, name: str) -> None:
    """Guards fail closed (D8-2): exception -> exit 2, ASCII reason on stderr (Claude Code shows it to the model)."""
    try:
        rc = main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        tb = traceback.extract_tb(e.__traceback__)
        where = f"{Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else "?"
        sys.stderr.write(ascii_text(f"axis-migrate {name} failed closed ({e.__class__.__name__}: {e} at {where}). "
                                    "The action was blocked; tell the user if this repeats.") + "\n")
        sys.exit(2)
    sys.exit(rc or 0)


def run_inform(main, name: str, event: str | None = None) -> None:
    """Informational hooks fail open (D8-2): exception -> exit 0 with a one-line note as context."""
    try:
        rc = main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001
        note = ascii_text(f"axis-migrate {name} check failed: {e.__class__.__name__}: {e}", 300)
        if event:
            emit({"hookSpecificOutput": {"hookEventName": event, "additionalContext": note}})
        sys.exit(0)
    sys.exit(rc or 0)


def load_graph(mig: Path) -> dict:
    with open(mig / "graph.json", encoding="utf-8") as fh:
        return json.load(fh)


def protected_set(mig: Path):
    import protected as prot
    return prot.ProtectedSet(mig, prot.compute(mig))


RUN_ID_RE = __import__("re").compile(r"^r\d{8}T\d{6}Z-[0-9a-f]{8}$")


def run_dirs(mig: Path) -> list[Path]:
    """Run directories under migration/runs (name r<UTC>-<8hex>, directories only), sorted by name: never rely on
    the file system's listing order (APFS and ext4 differ) and never pick up runs/index.jsonl."""
    runs = mig / "runs"
    if not runs.is_dir():
        return []
    return sorted((d for d in runs.iterdir() if RUN_ID_RE.match(d.name) and d.is_dir()), key=lambda d: d.name)


def recorded_runs(mig: Path) -> list[Path]:
    """Run directories that recorded snapshot hashes (manifest.json or snapshot/), oldest first."""
    return [d for d in run_dirs(mig) if (d / "manifest.json").is_file() or (d / "snapshot").is_dir()]


def graph_integrity(mig: Path) -> list[str] | None:
    """graph.py's GRAPH_EDITED_OUTSIDE_APPLY check (GRAPH_OPS §8); None when graph.py is not importable."""
    import importlib.util
    gp = PLUGIN / "skills" / "discover" / "graph.py"
    if not gp.is_file():
        return None
    spec = importlib.util.spec_from_file_location("axis_graph_for_hooks", gp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not hasattr(mod, "integrity_problems"):
        return None
    return list(mod.integrity_problems(mig))


MARKER_NAME = "hooks-active.json"
MARKER_SCHEMA = "axis-migrate.hooks-active.v1"


def marker_dir() -> Path:
    """${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.cache/axis-migrate}} (the same expression the start skill uses)."""
    d = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser(
        "~/.cache/axis-migrate")
    return Path(d)


def cwd_sha256(cwd: str) -> str:
    import hashlib
    return hashlib.sha256(os.path.realpath(cwd).encode("utf-8")).hexdigest()


def write_hooks_marker(data: dict, migration: Path | None) -> Path:
    """SessionStart proof that the hooks run in this harness (read by the Codex start skill, HOOK_MECHANICS.md)."""
    import datetime
    cwd = data.get("cwd") or os.getcwd()
    rec = {
        "schema": MARKER_SCHEMA,
        "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "harness": harness(data),
        "cwd_sha256": cwd_sha256(cwd),
        "session_id": data.get("session_id"),
        "source": data.get("source"),
        "migration_found": migration is not None,
        "plugin_root": str(PLUGIN),
        "hooks": ["session_start", "graph_changed", "guard_write", "guard_bash", "guard_hidden", "post_scan",
                  "stop_gate"],
    }
    d = marker_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f".{MARKER_NAME}.{os.getpid()}"
    tmp.write_text(json.dumps(rec, sort_keys=True, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, d / MARKER_NAME)
    return d / MARKER_NAME
