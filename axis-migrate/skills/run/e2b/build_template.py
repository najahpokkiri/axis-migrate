"""Build (or reuse) the E2B template `axis-geo-migrate-<lock8>` from the one lock (05 D5-16).

    set -a; . ~/.config/axis-migrate/secrets.env; set +a     # E2B_API_KEY into the environment (never printed)
    $PLUGIN/scripts/axpy $PLUGIN/skills/run/e2b/build_template.py [--force] [--json]

Image: Debian stable + strace + uv 0.13.0; uv-managed CPython 3.12 under /opt/axis/python; `uv sync --frozen
--no-dev` of the SAME skills/run/env/{pyproject.toml,uv.lock} into /opt/axis/env/.venv; no apt GDAL (the wheels
bundle theirs); axis_boot.py baked in; /opt/axis/env.lock.sha256 and /opt/axis/probe.json written at build.
2 vCPU / 2 GB. Idempotent: an existing template of that name is reused (recorded in e2b/template.json).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
LOCK = RUN / "env" / "uv.lock"
UV_VERSION = "0.13.0"
RECORD = HERE / "template.json"


def lock_sha() -> str:
    return hashlib.sha256(LOCK.read_bytes()).hexdigest()


def template_name() -> str:
    return f"axis-geo-migrate-{lock_sha()[:8]}"


def builder():
    from e2b import Template
    sha = lock_sha()
    t = (Template(file_context_path=RUN, file_ignore_patterns=["env/.venv", "**/__pycache__", "tests", "e2b"])
         .from_debian_image("stable")
         .apt_install(["strace", "ca-certificates", "curl"], no_install_recommends=True)
         .run_cmd(f"curl -LsSf https://astral.sh/uv/{UV_VERSION}/install.sh | env UV_INSTALL_DIR=/usr/local/bin "
                  "INSTALLER_NO_MODIFY_PATH=1 sh", user="root")
         .make_dir("/opt/axis/env", user="root")
         .copy(["env/pyproject.toml", "env/uv.lock"], "/opt/axis/env/", user="root")
         .copy("axis_boot.py", "/opt/axis/axis_boot.py", user="root")
         .run_cmd("cd /opt/axis/env && UV_PYTHON_INSTALL_DIR=/opt/axis/python UV_PYTHON_PREFERENCE=only-managed "
                  "UV_LINK_MODE=copy uv sync --frozen --no-dev --compile-bytecode --project /opt/axis/env", user="root")
         .run_cmd(f"echo {sha} > /opt/axis/env.lock.sha256 && sha256sum /opt/axis/env/uv.lock | cut -d' ' -f1 "
                  f"| grep -qx {sha}", user="root")
         .run_cmd("env -i PATH=/usr/bin:/bin HOME=/root PROJ_NETWORK=OFF GDAL_PAM_ENABLED=NO "
                  "/opt/axis/env/.venv/bin/python -I /opt/axis/axis_boot.py --probe > /opt/axis/probe.json "
                  "&& chmod -R a+rX /opt/axis", user="root"))
    return t


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build or reuse the axis-geo-migrate-<lock8> E2B template.")
    ap.add_argument("--force", action="store_true", help="rebuild even if a template of this name exists")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not os.environ.get("E2B_API_KEY"):
        print("build_template: E2B_API_KEY is not in the environment (source the secrets file first)", file=sys.stderr)
        return 13
    from e2b import Template, default_build_logger
    name = template_name()
    prev = json.loads(RECORD.read_text()) if RECORD.is_file() else {}
    if not a.force and Template.exists(name) and prev.get("name") == name:
        rec = dict(prev, reused=True)
    else:
        t0 = time.monotonic()
        info = Template.build(builder(), name, cpu_count=2, memory_mb=2048, on_build_logs=default_build_logger())
        rec = {"name": name, "template_id": info.template_id, "build_id": info.build_id,
               "lock_sha256": lock_sha(), "uv": UV_VERSION, "cpu_count": 2, "memory_mb": 2048,
               "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "build_seconds": round(time.monotonic() - t0, 1), "reused": False}
        RECORD.write_text(json.dumps({k: v for k, v in rec.items() if k != "reused"}, indent=1, sort_keys=True) + "\n")
    print(json.dumps(rec, sort_keys=True) if a.json else f"{rec['name']} template_id={rec['template_id']} "
          f"build_id={rec['build_id']}{' (reused)' if rec['reused'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
