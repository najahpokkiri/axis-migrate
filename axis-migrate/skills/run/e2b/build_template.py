"""Build, audit and publish the E2B template `axis-geo-migrate-<lock8>` from the one lock (05 D5-16).

    $PLUGIN/scripts/axpy $PLUGIN/skills/run/e2b/build_template.py --audit [--json]           # offline, no key
    $PLUGIN/scripts/axpy $PLUGIN/skills/run/e2b/build_template.py [--force] [--json]         # Axis: build or reuse
    $PLUGIN/scripts/axpy $PLUGIN/skills/run/e2b/build_template.py --publish [--yes]          # Axis: dry run w/o --yes
    $PLUGIN/scripts/axpy $PLUGIN/skills/run/e2b/build_template.py --from <slug>/axis-geo-migrate-<lock8>   # a user

The script loads the E2B key itself (skills/run/e2b/e2b_key.py: E2B_API_KEY in the environment, else the owner-only
secrets.env in the plugin data folder or ~/.config/axis-migrate/); it is never printed and never enters the template.

Base image (Axis builds it once per lock and publishes it public): Debian stable + strace + uv 0.13.0; uv-managed
CPython 3.12 under /opt/axis/python; `uv sync --frozen --no-dev` of the SAME skills/run/env/{pyproject.toml,uv.lock}
into /opt/axis/env/.venv; no apt GDAL (the wheels bundle theirs); axis_boot.py baked in; /opt/axis/env.lock.sha256
and /opt/axis/probe.json written at build. Only those three plugin files are copied. 4 vCPU / 8 GB (inside E2B Hobby:
8 vCPU / 8 GiB, 1 h max sandbox, $100 one-time credit). `--force` (dev tool) rebuilds although a template of that
name exists; otherwise an existing one is reused (recorded in e2b/template.json).

`--publish --yes` (needs $AXIS_AUDIT_DENY_RE, the release checklist's confidentiality pattern, which is never
committed; the audit greps the copied files for it and for key-like strings) marks the built template public: every E2B team can then start it as
`<slug>/axis-geo-migrate-<lock8>` (lib/axis_common.E2B_TEAM_SLUG), which run_e2b.py uses by default. Without `--yes`
it prints what it would publish (dry run). A new lock (new lock8) needs a new build and publish; older plugin
versions keep their own name.

`--from BASE` (a user who changed the lock, e.g. in a fork): `Template().from_template(BASE)` + the same copy and
`uv sync --frozen` of THIS plugin's lock, built in the user's own team as `axis-geo-migrate-<lock8>` (minutes, not
twenty: the base already has Debian, strace, uv and Python). The lock rule is unchanged: run_e2b.py refuses a
template whose lock sha, installed packages, native libraries or Python differ from the local lock
(TEMPLATE_LOCK_MISMATCH); a package outside the lock is refused by design. The record goes to
${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.config/axis-migrate}}/e2b-template.json, which run_e2b.py prefers by default
when its lock matches.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
LOCK = RUN / "env" / "uv.lock"
UV_VERSION = "0.13.0"
RECORD = HERE / "template.json"
CPU, MEM_MB = 4, 8192
COPIED = ["env/pyproject.toml", "env/uv.lock", "axis_boot.py"]          # the only plugin files in the template
sys.path.insert(0, str(HERE))
import e2b_key  # noqa: E402
from e2b_key import ac  # noqa: E402

KEYLIKE_RE = re.compile(r"E2B_API_KEY|\bsk-[A-Za-z0-9_-]{8,}|\be2b_[A-Za-z0-9]{16,}|api[_-]?key\s*[=:]|"
                        r"\btoken\s*[=:]|\bsecret\s*[=:]|password\s*[=:]|PRIVATE KEY", re.I)
DENY_ENV = "AXIS_AUDIT_DENY_RE"     # the release checklist's confidentiality pattern; never committed to the repo


def confidential_re() -> re.Pattern | None:
    pat = os.environ.get(DENY_ENV)
    return re.compile(pat, re.I) if pat else None


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def lock_sha() -> str:
    return sha_file(LOCK)


def template_name() -> str:
    """The bare name the template is built under (E2B adds the team slug: `<slug>/<name>`)."""
    return ac.e2b_template_name(lock_sha(), slug=None)


def public_name() -> str:
    return ac.e2b_template_name(lock_sha())


def user_record_path() -> Path:
    return ac.user_config_dir() / "e2b-template.json"


def read_record(path: Path | None = None) -> dict:
    try:
        return json.loads((path or RECORD).read_text())
    except (OSError, ValueError):
        return {}


def builder(base: str | None = None):
    """The template definition. `base` None: from Debian (Axis's public base); else layered on that template."""
    from e2b import Template
    sha = lock_sha()
    t = Template(file_context_path=RUN, file_ignore_patterns=["env/.venv", "**/__pycache__", "tests", "e2b"])
    if base is None:
        t = (t.from_debian_image("stable")
             .apt_install(["strace", "ca-certificates", "curl"], no_install_recommends=True)
             .run_cmd(f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | env UV_INSTALL_DIR=/usr/local/bin "
                      "INSTALLER_NO_MODIFY_PATH=1 sh", user="root"))
    else:
        t = t.from_template(base)
    return (t.make_dir("/opt/axis/env", user="root")
            .copy(["env/pyproject.toml", "env/uv.lock"], "/opt/axis/env/", user="root")
            .copy("axis_boot.py", "/opt/axis/axis_boot.py", user="root")
            .run_cmd("cd /opt/axis/env && UV_PYTHON_INSTALL_DIR=/opt/axis/python UV_PYTHON_PREFERENCE=only-managed "
                     "UV_LINK_MODE=copy uv sync --frozen --no-dev --compile-bytecode --project /opt/axis/env",
                     user="root")
            .run_cmd(f"echo {sha} > /opt/axis/env.lock.sha256 && sha256sum /opt/axis/env/uv.lock | cut -d' ' -f1 "
                     f"| grep -qx {sha}", user="root")
            .run_cmd("env -i PATH=/usr/bin:/bin HOME=/root PROJ_NETWORK=OFF GDAL_PAM_ENABLED=NO "
                     "/opt/axis/env/.venv/bin/python -I /opt/axis/axis_boot.py --probe > /opt/axis/probe.json "
                     "&& chmod -R a+rX /opt/axis", user="root"))


# ---------------------------------------------------------------------------------------------------------------
# Audit (offline): build steps, copied files, key-like and confidential strings
# ---------------------------------------------------------------------------------------------------------------
def audit(base: str | None = None, context: Path = RUN, copied: list[str] = COPIED) -> dict:
    from e2b import Template
    findings, files = [], []
    deny = confidential_re()
    for rel in copied:
        p = context / rel
        files.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha_file(p)})
        if p.name in (".env", "secrets.env") or p.suffix == ".env":
            findings.append({"code": "SECRETS_FILE_COPIED", "where": rel})
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for rx, code in ((KEYLIKE_RE, "KEY_LIKE"), (deny, "CONFIDENTIAL")):
                m = rx.search(line) if rx is not None else None
                if m:
                    findings.append({"code": code, "where": f"{rel}:{i}", "match": m.group(0)[:40]})
    t = builder(base)        # a template layered on another one has no Dockerfile form: show its JSON steps
    steps = Template.to_dockerfile(t) if base is None else f"FROM TEMPLATE {base}\n" + Template.to_json(t)
    return {"ok": not findings, "dockerfile": steps, "copied_files": files, "findings": findings,
            "confidentiality_checked": deny is not None}


def _refuse_on_audit(rep: dict) -> bool:
    if rep["ok"]:
        return False
    print("build_template: refused: AUDIT_FINDINGS " + json.dumps(rep["findings"]), file=sys.stderr)
    return True


# ---------------------------------------------------------------------------------------------------------------
# E2B API helpers (host only)
# ---------------------------------------------------------------------------------------------------------------
def _client(api_key: str):
    from e2b import ConnectionConfig
    from e2b.api.client_sync import get_api_client
    return get_api_client(ConnectionConfig(api_key=api_key))


def template_names(template_id: str, api_key: str) -> list[str]:
    """The template's names (`<team-slug>/<name>` when namespaced); [] when the lookup fails."""
    from e2b.api.client.api.templates import get_templates_template_id as get_t
    try:
        resp = get_t.sync_detailed(template_id, client=_client(api_key))
        return list(getattr(resp.parsed, "names", None) or [])
    except Exception:  # noqa: BLE001 - informational
        return []


def publish(template_id: str, api_key: str) -> list[str]:
    """Mark the template public; returns its names."""
    from e2b.api.client.api.templates import patch_v_2_templates_template_id as patch
    from e2b.api.client.models import TemplateUpdateRequest
    resp = patch.sync_detailed(template_id, client=_client(api_key), body=TemplateUpdateRequest(public=True))
    if int(resp.status_code) != 200 or not hasattr(resp.parsed, "names"):
        raise RuntimeError(f"PUBLISH_FAILED: HTTP {int(resp.status_code)}")
    return list(resp.parsed.names)


def _namespaced(names: list[str], bare: str) -> str | None:
    return next((n for n in names if "/" in n and n.split("/", 1)[1].split(":")[0] == bare), None)


def _key() -> str | None:
    try:
        return e2b_key.resolve()[0]
    except e2b_key.KeyProblem as exc:
        print(f"build_template: {exc.code}: {exc.detail}", file=sys.stderr)
        return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build, audit or publish the axis-geo-migrate-<lock8> E2B template.")
    ap.add_argument("--audit", action="store_true", help="offline: print build steps, copied files and findings")
    ap.add_argument("--force", action="store_true", help="dev tool: rebuild even if a template of this name exists")
    ap.add_argument("--publish", action="store_true", help="make the built template public (dry run without --yes)")
    ap.add_argument("--yes", action="store_true", help="with --publish: really publish (only after the owner confirms)")
    ap.add_argument("--from", dest="base", metavar="BASE",
                    help="build in your own team on top of BASE (e.g. the public <slug>/axis-geo-migrate-<lock8>)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    def out(obj, text):
        print(json.dumps(obj, sort_keys=True) if a.json else text)

    name = template_name()
    prev = read_record()

    if a.audit:
        rep = dict(audit(a.base), name=name, public_name=public_name(), base=a.base)
        text = (rep["dockerfile"] + "\ncopied files:\n"
                + "".join(f"  {f['sha256'][:12]}  {f['bytes']:>8,d}  {f['path']}\n" for f in rep["copied_files"])
                + ("findings: none" if rep["ok"] else "findings:\n" + "\n".join(f"  {f}" for f in rep["findings"])))
        out(rep, text)
        return 0 if rep["ok"] else 2

    if a.publish:
        if prev.get("name") not in (name, public_name()) or not prev.get("template_id"):
            print(f"build_template: TEMPLATE_NOT_BUILT: build {name} first (the record has {prev.get('name')})",
                  file=sys.stderr)
            return 13
        rep = audit()
        if _refuse_on_audit(rep):
            return 2
        if not a.yes:
            out({"dry_run": True, "template_id": prev["template_id"], "name": name, "public_name": public_name(),
                 "copied_files": rep["copied_files"]},
                f"dry run: would make {name} (template_id {prev['template_id']}) public, startable by every E2B "
                f"team as {public_name()}; audit clean ({len(rep['copied_files'])} plugin files copied). "
                "Rerun with --publish --yes once the owner confirms.")
            return 0
        if not rep["confidentiality_checked"]:
            print(f"build_template: refused: AUDIT_DENY_RE_MISSING: set {DENY_ENV} to the release checklist's "
                  "confidentiality pattern before publishing", file=sys.stderr)
            return 2
        key = _key()
        if key is None:
            return 13
        names = publish(prev["template_id"], key)
        full = _namespaced(names, name)
        rec = dict(prev, name=full or prev.get("name"), bare_name=name, names=names, public=True,
                   published_at=_now())
        RECORD.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
        if full and full != public_name():
            print(f"build_template: NOTE: E2B names it {full}, lib/axis_common.E2B_TEAM_SLUG gives {public_name()}; "
                  "set the slug constant to match", file=sys.stderr)
        out(rec, f"published {name}: {', '.join(names)}")
        return 0

    key = _key()
    if key is None:
        return 13
    from e2b import Template, default_build_logger
    rec_path = user_record_path() if a.base else RECORD
    prev = read_record(rec_path)
    if not a.force and not a.base and prev.get("lock_sha256") == lock_sha() and Template.exists(name, api_key=key):
        out(dict(prev, reused=True), f"{prev.get('name')} template_id={prev.get('template_id')} (reused)")
        return 0
    rep = audit(a.base)
    if _refuse_on_audit(rep):
        return 2
    t0 = time.monotonic()
    info = Template.build(builder(a.base), name, cpu_count=CPU, memory_mb=MEM_MB, api_key=key,
                          on_build_logs=default_build_logger())
    names = template_names(info.template_id, key)
    rec = {"name": _namespaced(names, name) or name, "bare_name": name, "names": names,
           "template_id": info.template_id, "build_id": info.build_id, "base": a.base or "debian:stable",
           "lock_sha256": lock_sha(), "uv": UV_VERSION, "cpu_count": CPU, "memory_mb": MEM_MB,
           "built_at": _now(), "build_seconds": round(time.monotonic() - t0, 1), "copied_files": rep["copied_files"],
           "public": bool(prev.get("public") and prev.get("template_id") == info.template_id)}
    if rec["public"]:
        rec["published_at"] = prev.get("published_at")
    rec_path.parent.mkdir(parents=True, exist_ok=True)
    rec_path.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    out(rec, f"{rec['name']} template_id={rec['template_id']} build_id={rec['build_id']}"
             + (f" (from {a.base}; run_e2b.py uses it by default: {rec_path})" if a.base else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
