"""Run eval trials (T1) fenced as the unix user axis-eval. Run as migrate.

    python3 evals/run.py <case_id> [--trials 3] [--commit HEAD] [--model claude-opus-5-5] [--parallel 2]

Per trial: the plugin at the pinned commit (`git archive`, a dirty plugin tree aborts unless --allow-dirty) and the
case workspace (legacy folder copy + optional seed migration/) go to a drop dir; as axis-eval the driver extracts
them into ~/ws/<trial>/ with a fresh HOME, runs Claude Code through the Agent SDK with the plugin loaded, answers
questions from the oracle (passed on stdin, never as a file), and tars the workspace back. Results land in
evals/results/<date>/<case>/<trial>/ and are scored by evals/score.py (which alone reads hidden/).
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
EVALS = ROOT / "evals"
DROP = Path("/tmp/axis-eval-drop")
FIXTURES = ROOT / "fixtures"
BASES = {"lux": FIXTURES / "lux-exposure-legacy",                      # case.yaml `base:` -> legacy folder
         "s1": EVALS / "cases" / "_bases" / "s1-buffers-legacy",
         "s1L": EVALS / "cases" / "_bases" / "s1L-buffers-legacy"}


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw)


def plugin_commit(commit: str, allow_dirty: bool) -> str:
    sha = sh(["git", "-C", str(ROOT), "rev-parse", commit]).stdout.strip()
    dirty = sh(["git", "-C", str(ROOT), "status", "--porcelain", "--", "axis-migrate"]).stdout.strip()
    if dirty and commit == "HEAD" and not allow_dirty:
        raise SystemExit(f"evaluator_error: plugin tree is dirty; commit first (or --allow-dirty):\n{dirty[:800]}")
    return sha


def build_plugin_tar(sha: str) -> Path:
    out = DROP / f"plugin-{sha[:12]}.tar"
    if not out.exists():
        with open(out, "wb") as fh:
            subprocess.run(["git", "-C", str(ROOT), "archive", "--format=tar", f"--prefix=axis-migrate/", sha,
                            "axis-migrate"], check=True, stdout=fh)
    os.chmod(out, 0o644)
    return out


def load_case(case_id: str) -> tuple[Path, dict]:
    cdir = EVALS / "cases" / case_id
    case = yaml.safe_load((cdir / "case.yaml").read_text())
    return cdir, case


def build_workspace_tar(cdir: Path, case: dict, trial_name: str) -> Path:
    """legacy/ (base fixture + overlay) and optional seed migration/, tarred with the project layout."""
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "project"
        proj.mkdir()
        base = case.get("base", "lux")
        legacy_name = case.get("legacy_name", "lux-exposure-legacy")
        src = (cdir / "legacy") if (cdir / "legacy").exists() else BASES[base]
        shutil.copytree(src, proj / legacy_name, symlinks=False)
        for op in case.get("overlay", []) or []:
            tgt = proj / legacy_name / op["path"]
            if op["op"] == "delete":
                if tgt.is_dir():
                    shutil.rmtree(tgt)
                else:
                    tgt.unlink()
            elif op["op"] == "replace":
                shutil.copy2(cdir / op["from"], tgt)
            elif op["op"] == "write":
                tgt.write_text(op["text"])
        seed = cdir / "seed" / "migration"
        if seed.exists():
            shutil.copytree(seed, proj / "migration")
        out = DROP / f"ws-{trial_name}.tar"
        with tarfile.open(out, "w") as tf:
            tf.add(proj, arcname="project")
    os.chmod(out, 0o644)
    return out


REMOTE = r'''
set -eu
mkdir -p "$HOME/ws"; chmod 311 "$HOME/ws"   # create/traverse only: no listing of sibling trials
T="$HOME/ws/{trial}"
rm -rf "$T"; mkdir -p "$T/home" "$T/out"
P="$HOME/plugins/{sha12}"
if [ ! -d "$P/axis-migrate" ]; then mkdir -p "$P"; tar -xf {plugin_tar} -C "$P"; fi
tar -xf {ws_tar} -C "$T"
chmod -R a-w "$T/project/{legacy}"
cd "$T/project"
"$HOME/harness-env/.venv/bin/python" {driver}
cd "$T"; tar -cf "$T.out.tar" out project
'''


def run_trial(case_id, cdir, case, sha, plugin_tar, model, trial_idx, results_root, effort):
    import secrets
    trial = (f"{case_id}-{sha[:8]}-{dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).strftime('%H%M%S')}"
             f"-t{trial_idx}-{secrets.token_hex(6)}")  # random suffix: sibling trials cannot guess each other's paths
    print("trial start:", trial, flush=True)
    ws_tar = build_workspace_tar(cdir, case, trial)
    out_tar = DROP / f"out-{trial}.tar"
    driver = DROP / "driver.py"
    shutil.copy2(EVALS / "driver.py", driver)
    os.chmod(driver, 0o644)
    oracle = yaml.safe_load((cdir / "hidden" / "oracle.yaml").read_text()) if (cdir / "hidden" / "oracle.yaml").exists() \
        else {"default": "recommended", "rules": []}
    home = f"/home/axis-eval/ws/{trial}/home"
    job = {"trial_dir": f"/home/axis-eval/ws/{trial}/out", "home_dir": home,
           "plugin_dir": f"/home/axis-eval/plugins/{sha[:12]}/axis-migrate",
           "project_dir": f"/home/axis-eval/ws/{trial}/project",
           "prompt": case.get("prompt", "/axis-migrate:migrate ./lux-exposure-legacy"), "model": model,
           "max_turns": case.get("max_turns", 400), "timeout_s": case.get("timeout_s", 2700), "oracle": oracle,
           "effort": effort}
    script = REMOTE.format(trial=trial, sha12=sha[:12], plugin_tar=plugin_tar, ws_tar=ws_tar, out_tar=out_tar,
                           legacy=case.get("legacy_name", "lux-exposure-legacy"), driver=driver)
    env = dict(os.environ)
    if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
        raise SystemExit("CLAUDE_CODE_OAUTH_TOKEN not in the environment: set -a; . ~/.config/axis-migrate/secrets.env; set +a")
    t0 = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    p = subprocess.run(["sudo", "-n", "--preserve-env=CLAUDE_CODE_OAUTH_TOKEN", "-u", "axis-eval", "bash", "-c", script],
                       input=json.dumps(job), text=True, capture_output=True, env=env,
                       timeout=case.get("timeout_s", 2700) + 600)
    dest = results_root / case_id / trial
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "harness.log").write_text(f"exit {p.returncode}\nSTDOUT\n{p.stdout[-20000:]}\nSTDERR\n{p.stderr[-20000:]}\n")
    meta = {"case": case_id, "trial": trial, "plugin_commit": sha, "model": model, "started": t0.isoformat() + "Z",
            "ended": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z", "harness_exit": p.returncode, "effort": effort}
    fetched = subprocess.run(["sudo", "-n", "-u", "axis-eval", "cat", f"/home/axis-eval/ws/{trial}.out.tar"],
                             capture_output=True)
    if fetched.returncode == 0 and fetched.stdout:
        out_tar.write_bytes(fetched.stdout)
        with tarfile.open(out_tar) as tf:
            tf.extractall(dest, filter="tar")
        out_tar.unlink()
    (dest / "meta.json").write_text(json.dumps(meta, indent=1))
    ws_tar.unlink(missing_ok=True)
    return dest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="+")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--commit", default="HEAD")
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--effort", default=None)
    ap.add_argument("--parallel", type=int, default=2)
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--skip-fence-probe", action="store_true")
    a = ap.parse_args()
    DROP.mkdir(exist_ok=True)
    os.chmod(DROP, 0o711)   # traverse only: file names (random) are not listable
    if not a.skip_fence_probe:
        if subprocess.run([sys.executable, str(EVALS / "fence_probe.py")]).returncode != 0:
            raise SystemExit("evaluator_error: fence probe failed; batch not run")
    sha = plugin_commit(a.commit, a.allow_dirty)
    plugin_tar = build_plugin_tar(sha)
    results_root = EVALS / "results" / dt.date.today().isoformat()
    jobs = []
    with cf.ThreadPoolExecutor(max_workers=a.parallel) as ex:
        for cid in a.cases:
            cdir, case = load_case(cid)
            for i in range(a.trials):
                jobs.append(ex.submit(run_trial, cid, cdir, case, sha, plugin_tar, a.model, i + 1, results_root,
                                      a.effort))
        for j in cf.as_completed(jobs):
            try:
                print("trial done:", j.result())
            except Exception as e:  # harness error: recorded, never scored
                print("trial harness_error:", type(e).__name__, str(e)[:500])


if __name__ == "__main__":
    main()
