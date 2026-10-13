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


def read_input() -> dict:
    raw = sys.stdin.read()
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("hook input is not a JSON object")
    return data


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
    cwd = data.get("cwd") or os.getcwd()
    try:
        return find_migration_dir(cwd)
    except OSError:
        return None


def abs_path(p: str, cwd: str | None) -> Path:
    p = os.path.expanduser(p)
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return Path(os.path.realpath(p))


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")
    sys.stdout.flush()


def pre_decision(decision: str, reason: str) -> None:
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


def recorded_runs(mig: Path) -> list[Path]:
    """Run directories that recorded snapshot hashes (manifest.json or snapshot/)."""
    runs = mig / "runs"
    if not runs.is_dir():
        return []
    out = []
    for d in sorted(runs.iterdir()):
        if d.is_dir() and ((d / "manifest.json").is_file() or (d / "snapshot").is_dir()):
            out.append(d)
    return out
