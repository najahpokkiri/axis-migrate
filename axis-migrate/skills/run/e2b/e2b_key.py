"""e2b_key: where the E2B API key and the sandbox choice come from (stdlib only; doctor imports it under `python3 -I`).

Key sources, first hit wins (the scripts load the key themselves; the agent never sources or reads a secrets file):
  1. `E2B_API_KEY` in this process's environment (`export E2B_API_KEY=…` in the shell that launched the harness);
  2. `secrets.env` in the plugin data folders (lib/axis_common.plugin_data_dirs): `$PLUGIN_DATA`,
     `$CLAUDE_PLUGIN_DATA` (written 0600 by the SessionStart hook from Claude's userConfig `e2b_api_key`), the
     harness data folders on disk (the agent's shell has neither variable), then `~/.config/axis-migrate/secrets.env`
     (written by `scripts/axpy --configure e2b`, run by the person; dev server).
  `$AXIS_MIGRATE_SECRETS`, when set, replaces the list in 2. with that one file (tests).
Only the `E2B_API_KEY=` line is parsed (optionally `export `, optionally quoted); the file is never executed.

Every existing secrets file must be a regular file owned by this user with no group/other permission bits
(`chmod 600`); otherwise every E2B entry point refuses with SECRETS_FILE_MODE, even when the key also sits in the
environment (a world-readable key file is a leak whatever the current shell holds). The key value (and its length)
is never printed, logged or put in a message; callers pass it to the e2b SDK on the host only, never into a sandbox.

    python3 -I e2b_key.py --source                 # where the key would come from (never the key)
    axpy e2b_key.py --exists <template>            # one authenticated Template.exists call (doctor --online)
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
import axis_common as ac  # noqa: E402

ENV_VAR = "E2B_API_KEY"
PATH_OVERRIDE = "AXIS_MIGRATE_SECRETS"
LINE_FORMAT = "E2B_API_KEY=<your key>"
SANDBOX_CHOICES = ("local", "e2b")


class KeyProblem(Exception):
    """A refusal with one stable code (E2B_KEY_MISSING | SECRETS_FILE_MODE | SECRETS_FILE_UNREADABLE)."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def secrets_paths(environ=None) -> list[Path]:
    environ = os.environ if environ is None else environ
    if environ.get(PATH_OVERRIDE):
        return [Path(environ[PATH_OVERRIDE]).expanduser()]
    return [d / "secrets.env" for d in ac.plugin_data_dirs(environ)]


def configure_path(environ=None) -> Path:
    """The file `scripts/axpy --configure e2b` writes."""
    environ = os.environ if environ is None else environ
    if environ.get(PATH_OVERRIDE):
        return Path(environ[PATH_OVERRIDE]).expanduser()
    return ac.user_config_dir(environ) / "secrets.env"


def _check_mode(st: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(st.st_mode):
        raise KeyProblem("SECRETS_FILE_MODE", f"{path} is not a regular file")
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        raise KeyProblem("SECRETS_FILE_MODE", f"{path} is not owned by this user (uid {st.st_uid}); "
                                              f"recreate it as yourself and `chmod 600 {path}`")
    if st.st_mode & 0o077:
        raise KeyProblem("SECRETS_FILE_MODE", f"{path} has mode {stat.S_IMODE(st.st_mode):03o}; "
                                              f"run `chmod 600 {path}` (owner-only)")


def parse_key_line(text: str) -> str | None:
    """The value of the last `E2B_API_KEY=` line (shell semantics: the last assignment wins), unquoted; no expansion."""
    val = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line.startswith(ENV_VAR + "="):
            continue
        v = line[len(ENV_VAR) + 1:].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].rstrip()
        val = v or None
    return val


def read_secrets_file(path: Path) -> str | None:
    """The key from one secrets file, or None when the file is absent or has no key line. Mode and owner are checked
    on the opened descriptor (no check-then-open race)."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOCTTY", 0) | getattr(os, "O_NONBLOCK", 0))
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise KeyProblem("SECRETS_FILE_UNREADABLE", f"{path}: {exc.strerror or exc}") from None
    try:
        _check_mode(os.fstat(fd), path)
        with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as fh:
            fd = -1
            return parse_key_line(fh.read(1 << 16))
    finally:
        if fd >= 0:
            os.close(fd)


def resolve(environ=None) -> tuple[str, str]:
    """(key, source) with source 'env E2B_API_KEY' or the secrets file path. Raises KeyProblem; the key is never in a
    message. Every existing secrets file is mode-checked, even when an earlier source wins."""
    environ = os.environ if environ is None else environ
    found = None
    for path in secrets_paths(environ):
        key = read_secrets_file(path)
        if key and found is None:
            found = (key, str(path))
    if environ.get(ENV_VAR):
        return environ[ENV_VAR], f"env {ENV_VAR}"
    if found:
        return found
    raise KeyProblem("E2B_KEY_MISSING", f"no {ENV_VAR} in the environment and no `{LINE_FORMAT}` line in "
                                        f"{', '.join(str(p) for p in secrets_paths(environ))}; the person runs "
                                        "`scripts/axpy --configure e2b` in their own terminal (or /plugin configure "
                                        "axis-migrate in Claude Code)")


def source(environ=None) -> tuple[str, str]:
    """('ok', <source>) | ('absent', <where looked>) | (<refusal code>, detail). Never returns the key."""
    try:
        return "ok", resolve(environ)[1]
    except KeyProblem as exc:
        return ("absent" if exc.code == "E2B_KEY_MISSING" else exc.code), exc.detail


def scrub(text: str, key: str | None) -> str:
    """Replace the key in a text (exception messages from the SDK) before it is printed or recorded."""
    return text.replace(key, "<E2B_API_KEY>") if key else text


# ---------------------------------------------------------------------------------------------------------------
# config.json: the per-install sandbox choice (asked once, at flow step 0)
# ---------------------------------------------------------------------------------------------------------------
def config_paths(environ=None) -> list[Path]:
    return [d / "config.json" for d in ac.plugin_data_dirs(environ)]


def read_config(environ=None) -> tuple[dict, Path | None]:
    for p in config_paths(environ):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return doc, p
    return {}, None


def write_sandbox_choice(choice: str, by: str, environ=None) -> Path:
    if choice not in SANDBOX_CHOICES:
        raise ValueError(f"sandbox must be one of {SANDBOX_CHOICES}")
    path = ac.user_config_dir(environ) / "config.json"
    doc = {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {}
    doc.update(sandbox=choice, sandbox_by=by, sandbox_at=ac.utc_now_iso())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ac.atomic_write_text(path, json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return path


def _exists(name: str) -> int:
    try:
        key, _ = resolve()
    except KeyProblem as exc:
        print(json.dumps({"ok": False, "code": exc.code}))
        return 13
    try:
        from e2b import Template
        from e2b.exceptions import AuthenticationException
    except ImportError:
        print(json.dumps({"ok": False, "code": "E2B_SDK_MISSING"}))
        return 13
    try:
        ok = Template.exists(name, api_key=key)
    except AuthenticationException:
        print(json.dumps({"ok": False, "code": "E2B_KEY_REJECTED"}))
        return 13
    except Exception as exc:  # noqa: BLE001 - reported by class, scrubbed
        print(json.dumps({"ok": False, "code": "E2B_UNREACHABLE", "detail": scrub(type(exc).__name__ + ": " +
                                                                                   str(exc), key)[:200]}))
        return 13
    print(json.dumps({"ok": bool(ok), "code": None if ok else f"TEMPLATE_NOT_FOUND:{name}"}))
    return 0 if ok else 13


if __name__ == "__main__":
    if sys.argv[1:2] == ["--exists"] and len(sys.argv) == 3:
        sys.exit(_exists(sys.argv[2]))
    if sys.argv[1:] == ["--source"]:
        st, detail = source()
        print(json.dumps({"state": st, "source": detail}))
        sys.exit(0 if st in ("ok", "absent") else 13)
    print(__doc__, file=sys.stderr)
    sys.exit(2)
