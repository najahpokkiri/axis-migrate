"""run_e2b: execute migration/pipeline/ step by step in one E2B sandbox (opt-in) and write one sealed run (05 §4).

    set -a; . ~/.config/axis-migrate/secrets.env; set +a           # E2B_API_KEY into the environment only
    axpy run_e2b.py <migration> [--chain chained|isolated] [--steps a,b] [--resume-from <run_id>]
                    [--allow-env-major] [--template axis-geo-migrate-<lock8>] --confirm-upload [--json]

Same plan, staging, preflight, evaluation and sealing as run_local (runlib); only execution moves:
 * the upload set is printed first (staged declared inputs, legacy_ro feeds only in isolated mode, the snapshot
   tarball, ctx files) with sizes and hashes; without --confirm-upload nothing leaves the machine (exit 13,
   UPLOAD_NOT_CONFIRMED); cap 200 MB (D5-14);
 * `Sandbox.create(template, timeout=600, allow_internet_access=False, envs=<allow-list>, metadata={run_id})`;
   the key comes from E2B_API_KEY in the process environment and never enters the sandbox;
 * the sandbox is probed (`axis_boot --probe`): lock sha, freeze and native library versions must equal this
   machine's locked environment, otherwise exit 13 TEMPLATE_LOCK_MISMATCH;
 * one command per step (strace ledger inside the sandbox when available), typed CommandExitException /
   TimeoutException; logs, result and outputs are downloaded and hash-checked after EACH step;
 * the sandbox is always killed; `env.e2b` records template, sandbox id, bytes moved and `sandbox_destroyed`.
Read ledger: `e2b-physical+strace` (undeclared files are absent from the sandbox; strace records what was opened).
Exit codes as run_local (0/10/11/12/13/14/15/70).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
import shlex
import sys
import tarfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runlib as rl  # noqa: E402
from runlib import ac  # noqa: E402

UPLOAD_CAP_BYTES = 200 * 1024 * 1024
SANDBOX_TIMEOUT_S = 600
DOWNLOAD_RESERVE_S = 60
TEMPLATE_RECORD = Path(__file__).resolve().parent / "e2b" / "template.json"
REMOTE = {"root": "/home/user/axis/run", "python": "/opt/axis/env/.venv/bin/python",
          "lock_file": "/opt/axis/env.lock.sha256", "probe_boot": "/opt/axis/axis_boot.py",
          "strace": "/usr/bin/strace", "tmp": "/home/user/axis"}


# Hashing inside the sandbox uses the sandbox's own Python (not GNU sha256sum), so the same commands work in the
# Debian template and in a local stand-in on BSD/macOS userland.
_PY_VERIFY = ("import hashlib,sys\n"
              "bad=[]\n"
              "for l in open('UPLOAD.sha256',encoding='utf-8').read().splitlines():\n"
              "    h,p=l.split('  ',1)\n"
              "    if hashlib.sha256(open(p,'rb').read()).hexdigest()!=h: bad.append(p)\n"
              "print('\\n'.join(bad)); sys.exit(1 if bad else 0)\n")
_PY_SHA = ("import hashlib,os,sys\n"
           "for p in sys.argv[1:]:\n"
           "    if os.path.isfile(p): print(hashlib.sha256(open(p,'rb').read()).hexdigest()+'  '+p)\n")


def default_template() -> str:
    return f"axis-geo-migrate-{rl.sha256_file(rl.LOCK)[:8]}"


def sandbox_factory(**kw):
    """The one place a sandbox is created (tests replace it)."""
    from e2b import Sandbox
    return Sandbox.create(**kw)


def _exc_types():
    try:
        from e2b import CommandExitException, TimeoutException
        return CommandExitException, TimeoutException
    except ImportError:  # pragma: no cover - e2b is in the lock
        class _Never(Exception):
            pass
        return _Never, _Never


# ---------------------------------------------------------------------------------------------------------------
class Remote:
    """Thin wrapper over one sandbox: shell commands, file transfer, typed failures."""

    def __init__(self, sb):
        self.sb = sb
        self.CommandExit, self.Timeout = _exc_types()
        self.bytes_up = 0
        self.bytes_down = 0
        self.renamed: set[str] = set()

    def sh(self, cmd: str, *, timeout: float = 60, envs: dict | None = None, cwd: str | None = None) -> tuple[int, str, str]:
        try:
            r = self.sb.commands.run(cmd, timeout=timeout, envs=envs, cwd=cwd)
            return r.exit_code, r.stdout, r.stderr
        except self.CommandExit as exc:
            return exc.exit_code, getattr(exc, "stdout", ""), getattr(exc, "stderr", "")

    def write(self, path: str, data: bytes) -> None:
        self.sb.files.write(path, data)
        self.bytes_up += len(data)

    def read(self, path: str) -> bytes:
        """Download via the sandbox's signed download URL (plain HTTPS). The SDK's HTTP/2 file read fails on
        multi-MB files in e2b 2.56 (`too_many_data_frames`, measured); it remains the fallback. Callers hash-check."""
        data = None
        url_fn = getattr(self.sb, "download_url", None)
        if callable(url_fn):
            try:
                import urllib.request
                with urllib.request.urlopen(url_fn(path), timeout=120) as resp:   # noqa: S310 - E2B https URL
                    data = resp.read()
            except Exception:  # noqa: BLE001 - fall back to the SDK transport below
                data = None
        if data is None:
            data = bytes(self.sb.files.read(path, format="bytes"))
        self.bytes_down += len(data)
        return data



# ---------------------------------------------------------------------------------------------------------------
def upload_set(run: rl.Run) -> list[dict]:
    """Every file that will leave this machine, with size and hash (D5-14)."""
    items = []
    for sub in ("snapshot", "stage/inputs", "stage/legacy_ro"):
        base = run.dir / sub
        if sub == "stage/legacy_ro" and run.chain != "isolated":
            continue
        for p in sorted(base.rglob("*")) if base.is_dir() else []:
            if p.is_file():
                items.append({"path": rl.rel(run.dir, p), "bytes": p.stat().st_size, "sha256": rl.sha256_file(p)})
    for ps in run.plan:
        if ps.action == "reuse":          # resumed: the parent's sealed outputs feed later steps
            pdir = Path(run.parent["_dir"])
            for p in sorted((pdir / "outputs" / ps.id).rglob("*")):
                if p.is_file():
                    items.append({"path": p.relative_to(pdir).as_posix(), "src": str(p), "bytes": p.stat().st_size,
                                  "sha256": rl.sha256_file(p)})
    return items


def build_tarball(run: rl.Run, items: list[dict]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for it in items:
            tar.add(it.get("src") or run.dir / it["path"], arcname=it["path"], recursive=False)
        tar.add(rl.AXIS_BOOT, arcname="boot/axis_boot.py")
        sums = "".join(f"{it['sha256']}  {it['path']}\n" for it in items)
        sums += f"{rl.sha256_file(rl.AXIS_BOOT)}  boot/axis_boot.py\n"
        info = tarfile.TarInfo("UPLOAD.sha256")
        info.size = len(sums.encode())
        tar.addfile(info, io.BytesIO(sums.encode()))
    return buf.getvalue()


def probe_remote(rem: Remote, crs: list[str]) -> dict:
    cmd = (f"env -i PATH=/usr/bin:/bin HOME=/home/user PROJ_NETWORK=OFF GDAL_PAM_ENABLED=NO PYTHONHASHSEED=0 "
           f"{REMOTE['python']} -I {REMOTE['probe_boot']} --probe " + " ".join(f"--crs {c}" for c in crs))
    code, out, err = rem.sh(cmd, timeout=120)
    if code != 0:
        raise rl.RunAbort(rl.EXIT["environment"], ["TEMPLATE_PROBE_FAILED"], err[-500:])
    probe = json.loads(out)
    code, out, _ = rem.sh(f"cat {REMOTE['lock_file']}")
    probe["_lock_sha256"] = out.strip() if code == 0 else None
    code, _, _ = rem.sh(f"{REMOTE['strace']} -f --seccomp-bpf -qq -o /dev/null true")
    probe["_strace"] = code == 0
    return probe


def check_template(remote_probe: dict, local_probe: dict) -> list[str]:
    bad = []
    if remote_probe.get("_lock_sha256") != rl.sha256_file(rl.LOCK):
        bad.append("TEMPLATE_LOCK_MISMATCH:lock_sha256")
    fz = rl.freeze_vs_lock(remote_probe.get("freeze", []), rl.LOCK)
    if not fz["matches"] or sorted(remote_probe.get("freeze", [])) != sorted(local_probe.get("freeze", [])):
        bad.append("TEMPLATE_LOCK_MISMATCH:freeze")
    if remote_probe.get("native") != local_probe.get("native"):
        bad.append("TEMPLATE_LOCK_MISMATCH:native")
    if remote_probe.get("python") != local_probe.get("python"):
        bad.append("TEMPLATE_LOCK_MISMATCH:python")
    return bad


# ---------------------------------------------------------------------------------------------------------------
def make_executor(rem: Remote, *, t_created: float, use_strace: bool):
    R = REMOTE["root"]

    def execute(run: rl.Run, ps: rl.PlannedStep) -> rl.ExecResult:
        # earlier steps that succeeded get their final name in the sandbox too; one round trip with the mkdir
        moves = []
        for rec in run.steps_records:
            sid = rec["step_id"]
            if rec.get("action") == "executed" and rec.get("status") == "succeeded" and sid not in rem.renamed:
                moves.append(f"if [ -d {R}/outputs/{sid}.tmp ]; then mv {R}/outputs/{sid}.tmp {R}/outputs/{sid}; fi")
                rem.renamed.add(sid)
        rem.sh(" && ".join(moves + [f"mkdir -p {R}/outputs/{ps.id}.tmp {R}/scratch/{ps.id} {R}/logs {R}/ctx"]))
        rem.write(f"{R}/ctx/{ps.id}.json", (run.dir / "ctx" / f"{ps.id}.json").read_bytes())
        remaining = SANDBOX_TIMEOUT_S - (time.monotonic() - t_created) - DOWNLOAD_RESERVE_S
        started_at, started_epoch, t0 = ac.utc_now_iso(), time.time(), time.monotonic()
        if ps.timeout_s > remaining:
            return rl.ExecResult(exit_code=None, timed_out=True, started_at=started_at, ended_at=ac.utc_now_iso(),
                                 started_epoch=started_epoch, ledger_kind="e2b-physical",
                                 extra_reasons=[f"BUDGET_EXHAUSTED_BEFORE:{ps.id}"])
        argv = run.boot_argv(ps, REMOTE["python"], f"{R}/boot/axis_boot.py")
        extra_env = {}
        if use_strace:
            ledger_rel, kind = f"logs/{ps.id}.reads", "e2b-physical+strace"
            argv = [REMOTE["strace"], "-f", "--seccomp-bpf", "-qq", "-y", "-s", "65535", "-e",
                    f"trace={rl.TRACE_SET}", "-o", f"{R}/{ledger_rel}"] + argv
        else:   # no strace in the sandbox: the same audit + CPL_DEBUG ledger as local macOS
            ledger_rel, kind = f"logs/{ps.id}.audit", "e2b-physical+audit"
            argv = argv + ["--audit-ledger", f"{R}/{ledger_rel}"]
            extra_env = {"CPL_DEBUG": "ON"}
        cmd = (f"cd {R} && " + " ".join(shlex.quote(a) for a in argv)
               + f" > {R}/logs/{ps.id}.stdout 2> {R}/logs/{ps.id}.stderr")
        envs = {"PATH": "/usr/bin:/bin", "HOME": "/home/user", "TMPDIR": f"{R}/scratch/{ps.id}",
                "MPLCONFIGDIR": f"{R}/scratch/{ps.id}/mpl", **rl.DETERMINISM_ENV, **rl.STEP_ENV_FIXED, **extra_env}
        timed_out, code = False, None
        try:
            r = rem.sb.commands.run(cmd, timeout=ps.timeout_s, envs=envs, cwd=R)
            code = r.exit_code
        except rem.CommandExit as exc:
            code = exc.exit_code
        except rem.Timeout:
            timed_out = True
            rem.sh(f"pkill -KILL -f {shlex.quote(R + '/boot/axis_boot.py')} || true")
        extra: list[str] = []
        # one round trip: R6 hashes of the step's inputs in the sandbox + the files to bring back
        feeds = sorted(ps.feeds.items())
        q = " ".join(shlex.quote(run.x(f["path"])) for _, f in feeds)
        c, out, _ = rem.sh(f"cd {R} && {REMOTE['python']} -I -c {shlex.quote(_PY_SHA)} {q}; echo '@@'; "
                           f"find logs -maxdepth 1 -type f -name '{ps.id}.*'; "
                           f"find outputs/{ps.id}.tmp -type f 2>/dev/null", timeout=120)
        hashes_part, _, files_part = out.partition("@@")
        remote_sha = {}
        for line in hashes_part.splitlines():
            if "  " in line:
                h, p = line.split("  ", 1)
                remote_sha[p] = h
        for ds, f in feeds:
            if remote_sha.get(run.x(f["path"])) != rl.sha256_file(Path(f["path"])):
                extra.append(f"STAGED_INPUT_MODIFIED:{ds}")
        # download logs, result and outputs now (a sandbox death loses at most the running step)
        for relp in sorted(l.strip() for l in files_part.splitlines() if l.strip()):
            if relp.startswith("logs/"):
                (run.dir / relp).write_bytes(rem.read(f"{R}/{relp}"))
        result = {}
        rp = run.dir / "logs" / f"{ps.id}.result.json"
        if rp.is_file():
            try:
                result = json.loads(rp.read_text())
            except ValueError:
                result = {}
        want = {Path(o["path"]).name: o.get("sha256") for o in result.get("outputs", []) if o.get("sha256")}
        out_dir = run.dir / "outputs" / f"{ps.id}.tmp"
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"outputs/{ps.id}.tmp/"
        for relp in sorted(l.strip() for l in files_part.splitlines() if l.strip().startswith(prefix)):
            name = relp[len(prefix):]
            data = rem.read(f"{R}/{relp}")
            dst = out_dir / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            if name in want and hashlib.sha256(data).hexdigest() != want[name]:
                extra.append(f"DOWNLOAD_HASH_MISMATCH:{ps.id}:{name}")
        return rl.ExecResult(exit_code=None if timed_out else code, timed_out=timed_out, started_at=started_at,
                             ended_at=ac.utc_now_iso(), started_epoch=started_epoch,
                             duration_s=time.monotonic() - t0, ledger_kind=kind, ledger_path=ledger_rel,
                             ledger_root=R, extra_reasons=extra)
    return execute


# ---------------------------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the migrated pipeline in one E2B sandbox (opt-in).")
    ap.add_argument("migration")
    ap.add_argument("--chain", choices=["chained", "isolated"], default="chained")
    ap.add_argument("--steps")
    ap.add_argument("--resume-from")
    ap.add_argument("--allow-env-major", action="store_true")
    ap.add_argument("--template", help="E2B template name (default axis-geo-migrate-<lock8>)")
    ap.add_argument("--confirm-upload", action="store_true",
                    help="consent to upload the listed files (ask the user first; nothing is uploaded without it)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--run-stamp", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    mig = Path(args.migration).resolve()
    if not (mig / "graph.json").is_file():
        found = ac.find_migration_dir(mig)
        if found is None:
            print(f"run_e2b: no migration/graph.json at or above {mig}", file=sys.stderr)
            return rl.EXIT["harness"]
        mig = found
    api_key = os.environ.get("E2B_API_KEY")
    if not api_key:
        print("run_e2b: E2B_KEY_MISSING (source the secrets file into the environment; the key is never read "
              "from disk by this script)", file=sys.stderr)
        return rl.EXIT["environment"]
    template = args.template or default_template()
    run = rl.Run(mig, chain=args.chain, executor="e2b",
                 steps_filter=[s for s in (args.steps or "").split(",") if s] or None, resume_from=args.resume_from,
                 allow_env_major=args.allow_env_major, run_stamp=args.run_stamp, read_ledger="e2b-physical+strace",
                 argv=["<migration>" if a == args.migration else a for a in (argv or sys.argv[1:])])
    run.exec_root = REMOTE["root"]
    tmpl = json.loads(TEMPLATE_RECORD.read_text()) if TEMPLATE_RECORD.is_file() else {}
    run.e2b_record = {"template": template, "template_id": tmpl.get("template_id") if tmpl.get("name") == template
                      else None, "build_id": tmpl.get("build_id") if tmpl.get("name") == template else None,
                      "sandbox_id": None, "sdk": _sdk_version(), "timeout_s": SANDBOX_TIMEOUT_S,
                      "allow_internet_access": False, "envs_keys": None, "sandbox_destroyed": None,
                      "uploaded_bytes": 0, "downloaded_bytes": 0, "upload_confirmed_by": None,
                      "upload_files": [], "remote_root": REMOTE["root"], "strace_in_sandbox": None}
    try:
        run.allocate()
    except rl.RunAbort as exc:
        print(f"run_e2b: refused: {', '.join(exc.reasons)}", file=sys.stderr)
        return exc.exit_code
    rem, sb = None, None
    code = 0
    try:
        try:
            run.snapshot()
            crs = run.crs_list()
            local_probe = rl.run_probe(rl.VENV_PY, crs)
            run.make_plan()
            run.preflight_graph()
            run.preflight_inputs()
            run.run_codescan()
            run.run_smoke_tests()
            items = upload_set(run)
            total = sum(i["bytes"] for i in items)
            print(f"run_e2b: upload set ({len(items)} files, {total / 1e6:.2f} MB) → template {template}:",
                  file=sys.stderr)
            for it in items:
                print(f"  {it['bytes']:>12,d}  {it['sha256'][:12]}  {it['path']}", file=sys.stderr)
            run.e2b_record["upload_files"] = [{k: v for k, v in i.items() if k != "src"} for i in items]
            if total > UPLOAD_CAP_BYTES:
                raise rl.RunAbort(rl.EXIT["environment"], ["UPLOAD_TOO_LARGE"])
            if not args.confirm_upload:
                raise rl.RunAbort(rl.EXIT["environment"], ["UPLOAD_NOT_CONFIRMED"])
            run.e2b_record["upload_confirmed_by"] = "--confirm-upload"
            envs = {k: v for k, v in {**rl.DETERMINISM_ENV, **rl.STEP_ENV_FIXED}.items()}
            run.e2b_record["envs_keys"] = sorted(envs)
            t_created = time.monotonic()
            sb = sandbox_factory(template=template, timeout=SANDBOX_TIMEOUT_S, allow_internet_access=False,
                                 envs=envs, metadata={"run_id": run.run_id}, api_key=api_key)
            run.e2b_record["sandbox_id"] = getattr(sb, "sandbox_id", None)
            run.e2b_record["create_s"] = round(time.monotonic() - t_created, 2)
            rem = Remote(sb)
            rprobe = probe_remote(rem, crs)
            run.e2b_record["strace_in_sandbox"] = bool(rprobe.get("_strace"))
            bad = check_template(rprobe, local_probe)
            if bad:
                (run.dir / "env" / "remote-probe.json").write_text(json.dumps(rprobe, sort_keys=True, indent=1))
                raise rl.RunAbort(rl.EXIT["environment"], bad)
            run.record_environment({k: v for k, v in rprobe.items() if not k.startswith("_")},
                                   reference=rl.reference_env_record(rl.REFENV_PY, crs),
                                   executor_python=REMOTE["python"], lock_mark_value=rprobe.get("_lock_sha256"),
                                   mismatch_reason="TEMPLATE_LOCK_MISMATCH")
            if not rprobe.get("_strace"):
                run.read_ledger = "e2b-physical+audit"
            items = upload_set(run) + [{"path": "env/expect.json", "bytes": (run.dir / "env/expect.json").stat().st_size,
                                        "sha256": rl.sha256_file(run.dir / "env/expect.json")}]
            t_up = time.monotonic()
            blob = build_tarball(run, items)
            R = REMOTE["root"]
            rem.sh(f"mkdir -p {R} {REMOTE['tmp']}")
            rem.write(f"{REMOTE['tmp']}/upload.tar.gz", blob)
            c, out, err = rem.sh(f"cd {R} && tar -xzf {REMOTE['tmp']}/upload.tar.gz && "
                                 f"{REMOTE['python']} -I -c {shlex.quote(_PY_VERIFY)} && chmod -R a-w stage && "
                                 f"mkdir -p ctx logs outputs scratch", timeout=120)
            if c != 0:
                raise rl.RunAbort(rl.EXIT["contract"], ["UPLOAD_HASH_MISMATCH"], err[-500:])
            run.e2b_record["upload_s"] = round(time.monotonic() - t_up, 2)
            code = run.run_steps(make_executor(rem, t_created=t_created, use_strace=bool(rprobe.get("_strace"))))
        except rl.RunAbort as exc:
            code = exc.exit_code
            run.reasons += exc.reasons
        finally:
            if sb is not None:
                try:
                    sb.kill()
                    run.e2b_record["sandbox_destroyed"] = True
                except Exception as exc:  # noqa: BLE001 - recorded; the sandbox times out on its own at 600 s
                    run.e2b_record["sandbox_destroyed"] = False
                    run.e2b_record["kill_error"] = f"{type(exc).__name__}: {exc}"[:300]
            if rem is not None:
                run.e2b_record["uploaded_bytes"] = rem.bytes_up
                run.e2b_record["downloaded_bytes"] = rem.bytes_down
        man = run.finalise(code)
    except Exception as exc:  # noqa: BLE001 - harness error, never success
        traceback.print_exc()
        try:
            run.finalise(rl.EXIT["harness"], extra_reasons=[f"HARNESS_ERROR:{type(exc).__name__}"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return rl.EXIT["harness"]
    summary = {"run_id": man["run_id"], "status": man["status"], "exit_code": man["exit_code"],
               "reason_codes": man["reason_codes"], "chain": man["chain"], "executor": man["executor"],
               "steps_planned": man["steps_planned"], "steps_succeeded": man["steps_succeeded"], "full": man["full"],
               "run_dir": str(run.dir), "wall_time_s": man["wall_time_s"],
               "sandbox_id": run.e2b_record.get("sandbox_id"), "sandbox_destroyed": run.e2b_record.get("sandbox_destroyed")}
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(man["run_id"])
        print(f"status {man['status']} (exit {man['exit_code']}); steps {man['steps_succeeded']}/{man['steps_planned']}"
              + (f"; reasons: {', '.join(man['reason_codes'])}" if man["reason_codes"] else ""), file=sys.stderr)
    return man["exit_code"]


def _sdk_version() -> str | None:
    try:
        return importlib.metadata.version("e2b")
    except importlib.metadata.PackageNotFoundError:
        return None


if __name__ == "__main__":
    sys.exit(main())
