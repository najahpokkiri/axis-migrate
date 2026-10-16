"""run_local: execute migration/pipeline/ step by step on this machine and write one sealed run (05, CONTRACTS §6).

    axpy run_local.py <migration> [--chain chained|isolated] [--steps a,b] [--env pipeline|refenv]
                                  [--resume-from <run_id>] [--allow-env-major] [--json]
    axpy run_local.py <migration> --vendor-runtime        # copy the plugin runtime into pipeline/
    axpy run_local.py <migration> --check-sealed <run_id>  # verify a sealed run's checksums (exit 0 / 15)

Prints the run id (or the manifest summary with --json). Exit codes (DESIGN §3): 0 succeeded · 10 a step failed ·
11 contract/ledger/stub/scan violation · 12 blocked: input missing or changed · 13 blocked: environment ·
14 timed out · 15 immutability · 70 harness error. There is no partial status.

Each step runs in its own process: `strace -f` (read and write ledger) → `python -I axis_boot.py` with a clean,
deterministic environment (nothing inherited from the caller; PROJ network OFF). `--env refenv` runs the same
snapshot with the legacy reference environment's Python (QGIS env); such a run is a control and never earns a rung.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runlib as rl  # noqa: E402
from runlib import ac  # noqa: E402

STRACE = ac.find_strace()


def _execute_windows(argv, env, run, ps, logs, kind, ledger_rel, started_at, started_epoch, t0) -> rl.ExecResult:
    """Windows has no process groups, os.wait4 or ru_maxrss: the step and its children run in a Job Object, which a
    timeout kills, and which is killed again after the step exits (leftover children die with it, as with killpg)."""
    with open(logs / f"{ps.id}.stdout", "wb") as out, open(logs / f"{ps.id}.stderr", "wb") as err:
        proc = subprocess.Popen(argv, cwd=str(run.dir), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            job = ac.WinJob(proc)
        except OSError:
            proc.kill()
            proc.wait()
            raise
        timed_out = False
        try:
            try:
                proc.wait(timeout=ps.timeout_s)
            except subprocess.TimeoutExpired:
                timed_out = True
                job.kill()
                proc.wait()
            peak = job.peak_rss_mb()
            job.kill()
        finally:
            job.close()
    code = proc.returncode
    return rl.ExecResult(exit_code=None if timed_out else code, signal=None, timed_out=timed_out,
                         started_at=started_at, ended_at=ac.utc_now_iso(), started_epoch=started_epoch,
                         duration_s=time.monotonic() - t0, peak_rss_mb=round(peak, 1), ledger_kind=kind,
                         ledger_path=ledger_rel)


def make_executor(python: Path, *, use_strace: bool):
    def execute(run: rl.Run, ps: rl.PlannedStep) -> rl.ExecResult:
        logs = run.dir / "logs"
        argv = run.boot_argv(ps, python)
        env = run.step_env(ps)
        ledger_rel, kind = None, "none"
        if use_strace:
            ledger_rel, kind = f"logs/{ps.id}.reads", "strace"
            argv = [STRACE, "-f", "--seccomp-bpf", "-qq", "-y", "-s", "65535", "-e", f"trace={rl.TRACE_SET}",
                    "-o", str(run.dir / ledger_rel)] + argv
        else:
            ledger_rel, kind = f"logs/{ps.id}.audit", "audit+cpl_debug"
            argv = argv + ["--audit-ledger", str(run.dir / ledger_rel)]
            env["CPL_DEBUG"] = "ON"
        started_at, started_epoch, t0 = ac.utc_now_iso(), time.time(), time.monotonic()
        if os.name == "nt":
            return _execute_windows(argv, env, run, ps, logs, kind, ledger_rel, started_at, started_epoch, t0)
        with open(logs / f"{ps.id}.stdout", "wb") as out, open(logs / f"{ps.id}.stderr", "wb") as err:
            proc = subprocess.Popen(argv, cwd=str(run.dir), env=env, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                    start_new_session=True)
            timed_out = threading.Event()

            def kill():
                timed_out.set()
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            timer = threading.Timer(ps.timeout_s, kill)
            timer.start()
            try:
                _, wstatus, ru = os.wait4(proc.pid, 0)
            finally:
                timer.cancel()
            proc.returncode = os.waitstatus_to_exitcode(wstatus)
            try:  # anything left in the group (a step's own children) dies with it
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        code = proc.returncode
        sig = -code if code is not None and code < 0 else None
        return rl.ExecResult(exit_code=None if timed_out.is_set() else (code if code >= 0 else None), signal=sig,
                             timed_out=timed_out.is_set(), started_at=started_at, ended_at=ac.utc_now_iso(),
                             started_epoch=started_epoch, duration_s=time.monotonic() - t0,
                             peak_rss_mb=round(ru.ru_maxrss / 1024.0, 1), ledger_kind=kind, ledger_path=ledger_rel)
    return execute


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the migrated pipeline locally into a fresh, sealed run directory.")
    ap.add_argument("migration", help="the project's migration/ directory (or the project dir holding it)")
    ap.add_argument("--chain", choices=["chained", "isolated"], default="chained")
    ap.add_argument("--steps", help="comma-separated step ids to run (chained: upstream must come from --resume-from)")
    ap.add_argument("--env", choices=["pipeline", "refenv"], default="pipeline",
                    help="pipeline = the locked venv (default); refenv = the legacy reference env (control only)")
    ap.add_argument("--resume-from", help="parent run id: reuse its succeeded steps after hash checks (D5-15)")
    ap.add_argument("--allow-env-major", action="store_true",
                    help="allow a GDAL/PROJ major difference to the reference env (recorded; caps the run)")
    ap.add_argument("--json", action="store_true", help="print the manifest summary as JSON")
    ap.add_argument("--vendor-runtime", action="store_true", help="copy the plugin runtime into pipeline/ and exit")
    ap.add_argument("--check-sealed", metavar="RUN_ID", help="verify a sealed run's checksums and exit (0 / 15)")
    ap.add_argument("--run-stamp", help=argparse.SUPPRESS)          # tests: force a run id stamp (collision → 15)
    ap.add_argument("--refenv-python", help=argparse.SUPPRESS)      # reference env python override
    args = ap.parse_args(argv)

    mig = Path(args.migration).resolve()
    if not (mig / "graph.json").is_file():
        found = ac.find_migration_dir(mig)
        if found is None:
            print(f"run_local: no migration/graph.json at or above {mig}", file=sys.stderr)
            return rl.EXIT["harness"]
        mig = found
    if args.vendor_runtime:
        done = rl.vendor_runtime(mig / "pipeline")
        print("vendored: " + ", ".join(done))
        return 0
    if args.check_sealed:
        bad = rl.verify_checksums(mig / "runs" / args.check_sealed)
        if args.json:
            print(json.dumps({"run_id": args.check_sealed, "ok": not bad, "problems": bad}, sort_keys=True))
        else:
            print("sealed run intact" if not bad else "sealed run changed:\n  " + "\n  ".join(bad))
        return 0 if not bad else rl.EXIT["immutability"]

    # uv is the only way the environment is made; without it nothing runs (D5-1)
    if rl.find_uv() is None:
        print("run_local: UV_MISSING (install uv; the runner never falls back to system Python or pip)",
              file=sys.stderr)
        return rl.EXIT["environment"]

    refenv_py = Path(args.refenv_python) if args.refenv_python else rl.REFENV_PY
    executor = "refenv" if args.env == "refenv" else "local"
    use_strace = STRACE is not None
    run = rl.Run(mig, chain=args.chain, executor=executor,
                 steps_filter=[s for s in (args.steps or "").split(",") if s] or None,
                 resume_from=args.resume_from, allow_env_major=args.allow_env_major, run_stamp=args.run_stamp,
                 read_ledger="strace" if use_strace else "audit+cpl_debug",
                 argv=["<migration>" if a == args.migration else a for a in (argv or sys.argv[1:])])
    python = refenv_py if executor == "refenv" else rl.VENV_PY
    run.boot_python = python
    code = 0
    try:
        run.allocate()
    except rl.RunAbort as exc:
        print(f"run_local: refused: {', '.join(exc.reasons)}", file=sys.stderr)
        return exc.exit_code
    try:
        try:
            run.snapshot()
            crs = run.crs_list()
            if executor == "refenv" and not python.is_file():
                raise rl.RunAbort(rl.EXIT["environment"], ["REFENV_MISSING"])
            probe = rl.run_probe(python, crs)
            reference = rl.reference_env_record(refenv_py, crs)
            run.record_environment(probe, reference=reference, executor_python=python)
            run.make_plan()
            run.preflight_graph()
            run.preflight_inputs()
            run.run_codescan()
            run.run_smoke_tests()
            code = run.run_steps(make_executor(python, use_strace=use_strace))
        except rl.RunAbort as exc:
            code = exc.exit_code
            run.reasons += exc.reasons
        man = run.finalise(code)
    except Exception as exc:  # noqa: BLE001 - a runner bug is exit 70, never success
        traceback.print_exc()
        try:
            man = run.finalise(rl.EXIT["harness"], extra_reasons=[f"HARNESS_ERROR:{type(exc).__name__}"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        return rl.EXIT["harness"]
    summary = {"run_id": man["run_id"], "status": man["status"], "exit_code": man["exit_code"],
               "reason_codes": man["reason_codes"], "chain": man["chain"], "executor": man["executor"],
               "steps_planned": man["steps_planned"], "steps_succeeded": man["steps_succeeded"], "full": man["full"],
               "run_dir": str(run.dir), "wall_time_s": man["wall_time_s"]}
    if args.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        print(man["run_id"])
        print(f"status {man['status']} (exit {man['exit_code']}); steps {man['steps_succeeded']}/{man['steps_planned']}"
              + (f"; reasons: {', '.join(man['reason_codes'])}" if man["reason_codes"] else ""), file=sys.stderr)
    return man["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
