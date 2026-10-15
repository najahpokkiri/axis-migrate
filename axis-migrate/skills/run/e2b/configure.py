"""configure.py — store the E2B API key for this install (`scripts/axpy --configure e2b`; stdlib only).

Run by the PERSON in their own terminal, never by the agent: the key is typed at a hidden prompt (getpass) on the
person's TTY and written as one `E2B_API_KEY=` line to
${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.config/axis-migrate}}/secrets.env with mode 600 (folder 700). Refuses when
stdin is not a terminal (an agent's shell has none), so the key never passes through a chat or a command line.
Other lines already in the file are kept; an existing key line is replaced. The key is never echoed.

    scripts/axpy --configure e2b            # prompt, write, print the file path (not the key)
    scripts/axpy --configure e2b --remove   # delete the key line
"""
from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import e2b_key  # noqa: E402


def write_key(path: Path, key: str | None) -> None:
    """Atomically (re)write `path` owner-only with the key line replaced (or removed when key is None)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    kept = []
    if path.exists():
        e2b_key.read_secrets_file(path)          # refuses SECRETS_FILE_MODE on someone else's or an open file
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("export "):
                s = s[len("export "):].lstrip()
            if not s.startswith(e2b_key.ENV_VAR + "="):
                kept.append(line)
    if key is not None:
        kept.append(f"{e2b_key.ENV_VAR}={key}")
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{line}\n" for line in kept))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "e2b" or any(a not in ("--remove",) for a in argv[1:]):
        print("usage: scripts/axpy --configure e2b [--remove]", file=sys.stderr)
        return 2
    path = e2b_key.configure_path()
    try:
        if "--remove" in argv:
            if path.exists():
                write_key(path, None)
            print(f"removed the E2B_API_KEY line from {path}")
            return 0
        if not sys.stdin.isatty():
            print("configure: NOT_A_TERMINAL: run `scripts/axpy --configure e2b` yourself in a terminal; the key is "
                  "typed at a hidden prompt and never goes through a chat, an agent or a command line",
                  file=sys.stderr)
            return 13
        key = getpass.getpass("E2B API key (input hidden; https://e2b.dev/dashboard?tab=keys): ").strip()
        if not key or any(c.isspace() for c in key) or any(c in key for c in "\"'$`\\#"):
            print("configure: refused: empty key or a key with spaces, quotes, $, `, \\ or #", file=sys.stderr)
            return 2
        write_key(path, key)
    except e2b_key.KeyProblem as exc:
        print(f"configure: {exc.code}: {exc.detail}", file=sys.stderr)
        return 13
    print(f"wrote E2B_API_KEY to {path} (mode 600); `scripts/axpy --doctor` now shows the e2b line as OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
