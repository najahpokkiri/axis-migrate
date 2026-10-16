"""axis-migrate preflight: can this machine run the pipeline environment? (stdlib only; `scripts/axpy --doctor`)

    scripts/axpy --doctor [--no-sync] [--online] [--json]
    scripts/axpy --doctor --record-sandbox local|e2b      # store the person's first-run answer (config.json)

Checks, one line each (OK / WARN / FAIL):
  uv          uv is installed (the environment is made only by `uv sync --frozen`; no pip, no system Python)
  cache       the environment cache directory is writable, and which rule chose it (${CLAUDE_PLUGIN_DATA} when set;
              Claude Code sets it for hooks only, never in the Bash tool, so the agent's shell normally gets
              ${XDG_CACHE_HOME:-~/.cache}/axis-migrate)
  env         the locked environment is synced, or syncs now (`--no-sync`: dry run only; Codex sandbox story)
  strace      strace works (read/write ledger); without it (macOS): ledger = audit+cpl_debug, rung capped at
              sandbox-validated; E2B runs (strace inside the sandbox) are the way past the cap
  port        a local port can be bound on 127.0.0.1 (graph editor server)
  plugin      the plugin folder holds no `.venv`, `__pycache__` or `migration/` (a marketplace added from a working
              tree copies git-ignored files into the installed cache; install from GitHub or a fresh clone)
  e2b         where the E2B key would come from (never the key or its length) and the template name; FAIL on a
              secrets file that is not owner-only (SECRETS_FILE_MODE); WARN when not configured (optional);
              `--online`: one authenticated Template.exists call
  refenv      the legacy reference environment's python for the `--env refenv` control run
              (AXIS_REFENV_PYTHON, default ~/micromamba/envs/qgis/bin/python); WARN when absent
  rung        the highest rung this machine can reach (strace or E2B decide it)
`--json` adds `sandbox_choice` (config.json, asked once per install) and `ask_sandbox` (true when strace is not usable,
E2B is not configured and nobody has answered yet: the migrate flow asks at step 0).
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

sys.dont_write_bytecode = True     # run as `python3 -I`, which ignores PYTHONDONTWRITEBYTECODE (PLUGIN_NOT_CLEAN)
PLUGIN = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN / "lib"))
os.environ.setdefault("AXIS_MIGRATE_PLUGIN", str(PLUGIN))
import axis_common as ac  # noqa: E402

sys.path.insert(0, str(PLUGIN / "skills" / "run" / "e2b"))
import e2b_key  # noqa: E402


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


SANDBOX_HINT = " (needs {what}; see the sandbox note below)"
SANDBOX_CODES = ("ENV_CACHE_NOT_WRITABLE", "ENV_SYNC_FAILED", "PORT_BIND_FAILED")


def sandbox_note(results: list[tuple[str, str, str]]) -> str | None:
    """One plain sentence for the person when the agent's sandbox blocked a check (Lux e2e, 15 Oct: a bare "rerun
    outside the sandbox" made the person ask whether a sandbox existed at all)."""
    if not any(s == "FAIL" and m.startswith(SANDBOX_CODES) for _, s, m in results):
        return None
    cache = ac.env_venv_dir().parent
    data = ac.user_config_dir()
    return ("This is most likely the agent's sandbox doing its job (Claude Code and Codex run the agent's commands in "
            "one): it blocks the two things axis-migrate needs outside the project folder — writing its environment "
            f"cache ({cache}, plus the plugin data folder {data}; the first sync also "
            "downloads the pinned packages) and binding a port on 127.0.0.1 for the graph and map pages — so either "
            "approve those commands when the agent asks (Codex: escalated permissions), or allow them once in the "
            "harness settings: `sandbox.network.allowLocalBinding: true` and write access to those two folders.")


def check_cache() -> tuple[str, str]:
    venv = ac.env_venv_dir()
    base = venv.parent
    try:
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=base, prefix=".doctor-", delete=True):
            pass
    except OSError as exc:
        return "FAIL", f"ENV_CACHE_NOT_WRITABLE: {base}: {exc.strerror or exc}" + SANDBOX_HINT.format(what=f"a write to {base}")
    rule = ac.env_venv_rule()[1]
    return "OK", f"environment cache {venv} ({rule})"


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
        return "FAIL", "ENV_SYNC_FAILED: uv sync timed out" + SANDBOX_HINT.format(what="network and a write to " + str(venv))
    if cp.returncode != 0:
        tail = (cp.stderr or cp.stdout).strip().splitlines()[-1:] or [""]
        return "FAIL", f"ENV_SYNC_FAILED: {tail[0][:200]}" + SANDBOX_HINT.format(what="network and a write to " + str(venv))
    if no_sync:
        return "WARN", "not synced yet; `uv sync --frozen` dry run succeeded (first axpy call will sync)"
    marker.write_text(lock + "\n")
    return "OK", f"synced now from uv.lock {lock[:8]} ({ac.env_python()})"


def check_strace() -> tuple[str, str]:
    st = ac.find_strace()
    fallback = ("read ledger = audit+cpl_debug (Python audit hook + GDAL CPL_DEBUG), rung capped at "
                "sandbox-validated; E2B sandbox runs (strace inside) are the way past the cap (see the e2b line)")
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
        return "FAIL", f"PORT_BIND_FAILED: 127.0.0.1: {exc.strerror or exc}" + SANDBOX_HINT.format(
            what="a local port for the graph and map pages")
    return "OK", f"127.0.0.1 bind ok (got port {port})"


STRAY_NAMES = (".venv", "__pycache__", "migration")


def stray_paths(root: Path = PLUGIN) -> list[Path]:
    """Directories under the plugin root that a clean checkout never has (git-ignored leftovers)."""
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            if d in STRAY_NAMES or d == ".git":
                if d != ".git":
                    found.append(Path(dirpath) / d)
                dirnames.remove(d)
    return sorted(found)


def check_plugin(root: Path = PLUGIN) -> tuple[str, str]:
    found = stray_paths(root)
    if not found:
        return "OK", f"plugin folder clean ({root})"
    shown = ", ".join(str(p.relative_to(root)) for p in found[:3]) + (f" (+{len(found) - 3} more)" if len(found) > 3 else "")
    return "WARN", (f"PLUGIN_NOT_CLEAN: {shown} under {root}: installed from a working tree? Reinstall from GitHub "
                    "or a fresh clone (README, Install)")


def e2b_template() -> str:
    """Same default as run_e2b.default_template (stdlib: no runlib import)."""
    lock = ac.env_lock_sha256()
    try:
        rec = json.loads((ac.user_config_dir() / "e2b-template.json").read_text())
        if rec.get("lock_sha256") == lock and rec.get("name"):
            return rec["name"]
    except (OSError, ValueError):
        pass
    return ac.e2b_template_name(lock)


E2B_NOT_CONFIGURED = ("not configured (optional: E2B sandbox for the strace read ledger on Mac/Windows): the person "
                      "runs `scripts/axpy --configure e2b` in their own terminal, or `/plugin configure axis-migrate` "
                      "in Claude Code")


def check_e2b(online: bool = False) -> tuple[str, str]:
    state, detail = e2b_key.source()
    if state == "absent":
        return "WARN", E2B_NOT_CONFIGURED
    if state != "ok":
        return "FAIL", f"{state}: {detail}"
    name = e2b_template()
    if not online:
        return "OK", f"key from {detail}; template {name} (not checked offline; --online checks it)"
    py = ac.env_python()
    if not py.is_file():
        return "WARN", f"key from {detail}; template {name}: --online needs the synced environment ({py})"
    try:
        cp = subprocess.run([str(py), "-I", str(PLUGIN / "skills" / "run" / "e2b" / "e2b_key.py"), "--exists", name],
                            capture_output=True, text=True, timeout=60)
        res = json.loads(cp.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return "FAIL", f"key from {detail}; template {name}: E2B_UNREACHABLE (online check failed; needs network)"
    if res.get("ok"):
        return "OK", f"key from {detail}; template {name} exists (checked online)"
    return "FAIL", f"key from {detail}; {res.get('code')}"


def refenv_python() -> Path:
    return Path(os.environ.get("AXIS_REFENV_PYTHON") or Path.home() / "micromamba/envs/qgis/bin/python")


def check_refenv() -> tuple[str, str]:
    py = refenv_python()
    if py.is_file() and os.access(py, os.X_OK):
        return "OK", f"reference env {py}: after a parity failure run `run_local.py migration --env refenv`"
    return "WARN", (f"absent ({py}; set AXIS_REFENV_PYTHON to the legacy QGIS environment's python): the --env refenv "
                    "control run is unavailable (REFENV_MISSING) — after a parity failure go straight to code repair")


def check_rung(results: list[tuple[str, str, str]]) -> tuple[str, str]:
    st = {c: s for c, s, _ in results}
    if st.get("strace") == "OK":
        return "OK", "max rung on this machine: target-ready (strace read ledger here)"
    if st.get("e2b") == "OK":
        return "OK", ("max rung on this machine: target-ready through E2B runs (run_e2b.py, strace in the sandbox); "
                      "local runs cap at sandbox-validated")
    return "WARN", ("max rung on this machine: sandbox-validated (no strace, E2B not configured); an E2B key lifts it "
                    "(scripts/axpy --configure e2b)")


def main(argv: list[str] | None = None) -> int:
    try:  # Windows: a piped stdout is cp1252 and `-I` ignores PYTHONIOENCODING; the messages hold arrows and dashes
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError, OSError):
        pass
    ap = argparse.ArgumentParser(description="axis-migrate preflight checks.")
    ap.add_argument("--no-sync", action="store_true", help="do not sync the environment; dry run only")
    ap.add_argument("--online", action="store_true", help="e2b: one authenticated check that the template exists")
    ap.add_argument("--record-sandbox", choices=e2b_key.SANDBOX_CHOICES,
                    help="store the person's first-run answer (local | e2b) in config.json, then exit")
    ap.add_argument("--by", default="user (AskUserQuestion)", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.record_sandbox:
        path = e2b_key.write_sandbox_choice(a.record_sandbox, a.by)
        print(f"sandbox: {a.record_sandbox} (recorded in {path})")
        return 0
    results = [("uv", *check_uv()), ("cache", *check_cache()), ("env", *check_env(a.no_sync)),
               ("strace", *check_strace()), ("port", *check_port()), ("plugin", *check_plugin()),
               ("e2b", *check_e2b(a.online)), ("refenv", *check_refenv())]
    results.append(("rung", *check_rung(results)))
    failed = any(r[1] == "FAIL" for r in results)
    cfg, _ = e2b_key.read_config()
    choice = cfg.get("sandbox") if cfg.get("sandbox") in e2b_key.SANDBOX_CHOICES else None
    st = {c: s for c, s, _ in results}
    ask = st["strace"] != "OK" and st["e2b"] == "WARN" and choice is None
    if a.json:
        print(json.dumps({"ok": not failed, "platform": f"{platform.system()}-{platform.machine()}",
                          "sandbox_choice": choice, "ask_sandbox": ask, "sandbox_note": sandbox_note(results),
                          "checks": [{"check": c, "state": s, "message": m} for c, s, m in results]}, sort_keys=True))
    else:
        for c, s, m in results:
            print(f"{s:<4}  {c:<6}  {m}")
        print(f"      sandbox: {choice or 'not chosen'}" + (" (ask the person at flow step 0)" if ask else ""))
        note = sandbox_note(results)
        if note:
            print(f"\nNOTE  {note}")
    return 13 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
