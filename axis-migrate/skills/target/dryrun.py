#!/usr/bin/env python3
"""dryrun.py — run the committed wheel task by task in a Databricks-like layout, offline (09 §4.2; T2, T6–T8, T11, T13).

    axpy dryrun.py <migration> --run <run_id> --evidence <dir> [--keep] [--no-strace] [--json]

Normally called by target_check.py. What it does:
  1. a FRESH venv (uv; packages from the uv cache offline first, PyPI only if a pin is not cached and
     AXIS_TARGET_OFFLINE is not 1) with the runtime manifest's Python (exact version when uv finds it without downloading; else
     the lock's 3.12, recorded as a deviation), populated from requirements-target.lock with the runtime's declared
     versions as constraints (so the runtime-provided packages' own dependencies resolve inside the runtime's declared
     set), then the committed wheel with --no-deps; `uv pip check` and a freeze are recorded;
  2. the layout <tmp>/root/Volumes/<c>/<s>/<v>/{inputs,work,outputs} and <tmp>/root/local_disk0/tmp/axis; inputs are
     copied in from the legacy folder and hash-checked against the inventory pins;
  3. every task of the generated databricks.yml, in depends_on order, with ITS parameters: `${var.*}` replaced by
     placeholders and the prefixes /Volumes and /local_disk0 remapped to the fake root (the only rewrite; recorded in
     layout.json). Each task is a new process `python -I <venv>/bin/axis-pipeline …` (the console script the wheel
     declares), cwd an empty directory, no PYTHONPATH, a temporary HOME, a minimal environment, `unshare -n` when
     permitted (else recorded as "network not isolated"; connect() is then failed by strace fault injection);
  4. `strace -f -y -e trace=openat,lseek,pwrite64,pwritev,ftruncate,fcntl,connect` per task. Rules on files under the
     fake Volume root: a write fd is written sequentially only (no lseek other than the position query
     lseek(fd, 0, SEEK_CUR), no pwrite*, no ftruncate) → TC_SEEK_ON_VOLUME; no O_RDWR open of *.gpkg|*.sqlite|*.db,
     no -journal/-wal/-shm created, no POSIX record lock (SQLite's locking; unsupported on Volumes) →
     TC_SQLITE_ON_VOLUME. Random READS are allowed (assumption, 09 §4.2);
  5. parity (T8): verify/compare.py's compare_pair (the function behind `compare.py pair`) on every step output and
     final output the verified run compared, against the same legacy references, with the run's sealed tolerances
     and known differences (hash-checked against the run's verify/contract.json), plus peak RSS per task (T11).

Evidence written to --evidence: layout.json, trace.log (only syscalls on paths under the fake Volume and network calls),
trace-findings.json, compare-report.json (+ compare/<ds>.json), rss.json, dryrun.json, logs/<task>.{stdout,stderr,
io.json}. The fake root and the venv live in a temporary directory that is removed unless --keep.
Exit: 0 ran (read dryrun.json for the results) · 2 setup error (bundle/wheel/run missing).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[1]
sys.path.insert(0, str(PLUGIN / "lib"))
sys.path.insert(0, str(HERE))
import axis_common as ac  # noqa: E402
import make_databricks as md  # noqa: E402

import yaml  # noqa: E402

TRACE_SET = "openat,lseek,pwrite64,pwritev,ftruncate,fcntl,connect"
VARS_PLACEHOLDER = {"catalog": "dryrun_catalog", "schema": "dryrun_schema", "volume": "dryrun_volume",
                    "node_type_id": "dryrun_node_type"}
REMAP_PREFIXES = ("/Volumes", "/local_disk0")
SQLITE_SUFFIXES = (".gpkg", ".sqlite", ".db")
SQLITE_SIDE = ("-journal", "-wal", "-shm")
TASK_TIMEOUT_S = 1800
FINDINGS_PER_TASK_MAX = 50


class SetupError(Exception):
    pass


def find_strace() -> str | None:
    if os.environ.get("AXIS_TARGET_NO_STRACE") == "1":
        return None
    return ac.find_strace()


def unshare_works() -> bool:
    exe = shutil.which("unshare")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-n", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def uv_bin() -> str | None:
    return shutil.which("uv") or (str(Path.home() / ".local/bin/uv") if (Path.home() / ".local/bin/uv").is_file()
                                  else None)


# ---------------------------------------------------------------------------------------------------------------
# venv
# ---------------------------------------------------------------------------------------------------------------
def make_venv(tmp: Path, runtime: dict, lock_file: Path, wheel: Path, log: list) -> dict:
    uv = uv_bin()
    info = {"uv": None, "python_requested": runtime.get("python"), "python_used": None, "deviation": None,
            "install_ok": False, "pip_check": None, "freeze": [], "constraints": "runtime declared versions"}
    if uv is None:
        info["error"] = "UV_MISSING"
        return info
    info["uv"] = subprocess.run([uv, "--version"], capture_output=True, text=True).stdout.strip()
    venv = tmp / "venv"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABRICKS_", "VIRTUAL_ENV", "PYTHON"))}
    env["UV_PYTHON_DOWNLOADS"] = "never"
    want = runtime.get("python") or "3.12"
    tries = [want, ".".join(want.split(".")[:2])]
    for i, py in enumerate(tries):
        cp = subprocess.run([uv, "venv", "--quiet", "--no-project", "--python", py, str(venv)], capture_output=True,
                            text=True, env=env)
        log.append({"cmd": f"uv venv --python {py}", "rc": cp.returncode, "stderr": cp.stderr[-1500:]})
        if cp.returncode == 0:
            if i > 0:
                info["deviation"] = f"Python {want} not available without a download; used {py} (the lock's minor)"
            break
    else:
        info["error"] = "PYTHON_UNAVAILABLE"
        return info
    py = ac.venv_python(venv)
    info["python_used"] = subprocess.run([str(py), "-I", "-c", "import platform;print(platform.python_version())"],
                                         capture_output=True, text=True).stdout.strip()
    if info["python_used"] != want and not info["deviation"]:
        info["deviation"] = f"runtime declares Python {want}; venv has {info['python_used']}"
    cons = tmp / "runtime-constraints.txt"
    cons.write_text("".join(f"{k}=={v}\n" for k, v in sorted((runtime.get("python_packages") or {}).items())),
                    encoding="utf-8")
    # offline first (uv cache); PyPI only when the cache lacks a pin and offline mode is not forced
    modes = [("uv cache (offline)", dict(env, UV_OFFLINE="1"))]
    if os.environ.get("AXIS_TARGET_OFFLINE") != "1":
        modes.append(("PyPI download", env))
    for label, menv in modes:
        cp = subprocess.run([uv, "pip", "install", "--quiet", "--python", str(py), "-r", str(lock_file),
                             "--constraint", str(cons)], capture_output=True, text=True, env=menv)
        log.append({"cmd": f"uv pip install -r requirements-target.lock --constraint <runtime declared> [{label}]",
                    "rc": cp.returncode, "stderr": cp.stderr[-3000:]})
        if cp.returncode == 0:
            info["packages_from"] = label
            break
    else:
        info["error"] = "INSTALL_FAILED"
        return info
    cp = subprocess.run([uv, "pip", "install", "--quiet", "--offline", "--no-deps", "--python", str(py), str(wheel)],
                        capture_output=True, text=True, env=env)
    log.append({"cmd": f"uv pip install --no-deps {wheel.name}", "rc": cp.returncode, "stderr": cp.stderr[-3000:]})
    if cp.returncode != 0:
        info["error"] = "WHEEL_INSTALL_FAILED"
        return info
    cp = subprocess.run([uv, "pip", "check", "--python", str(py)], capture_output=True, text=True, env=env)
    info["pip_check"] = {"ok": cp.returncode == 0, "output": (cp.stdout + cp.stderr).strip()[-3000:]}
    cp = subprocess.run([uv, "pip", "freeze", "--python", str(py)], capture_output=True, text=True, env=env)
    info["freeze"] = sorted(re.sub(r" @ file://\S+/", " @ file://<databricks>/dist/", x) for x in cp.stdout.splitlines()
                            if x.strip())
    info["install_ok"] = True
    info["venv"] = str(venv)
    return info


PROBE = r"""
import json, sys, importlib.util
from importlib import metadata as m
out = {"python": sys.version.split()[0], "dists": {}, "entry_point": None, "modules": {}, "native": {}}
for d in m.distributions():
    out["dists"][d.metadata["Name"]] = d.version
eps = [e for e in m.entry_points(group="console_scripts") if e.name == "axis-pipeline"]
out["entry_point"] = eps[0].value if eps else None
try:
    out["package_dist"] = m.distribution("axis_pipeline").version
except Exception as e:
    out["package_dist"] = None
for s in json.loads(sys.argv[1]):
    out["modules"][s] = importlib.util.find_spec("axis_pipeline." + s) is not None
for name, expr in (("rasterio_gdal", "__import__('rasterio').__gdal_version__"),
                   ("pyogrio_gdal", "__import__('pyogrio').__gdal_version_string__"),
                   ("pyproj_proj", "__import__('pyproj').proj_version_str"),
                   ("numpy", "__import__('numpy').__version__"), ("pandas", "__import__('pandas').__version__")):
    try:
        out["native"][name] = str(eval(expr))
    except Exception as e:
        out["native"][name] = "ERROR " + type(e).__name__ + ": " + str(e)[:200]
print(json.dumps(out, sort_keys=True))
"""


def probe_venv(py: Path, steps: list[str], env: dict, cwd: Path) -> dict:
    cp = subprocess.run([str(py), "-I", "-c", PROBE, json.dumps(steps)], capture_output=True, text=True, env=env,
                        cwd=str(cwd), timeout=300)
    if cp.returncode != 0:
        return {"error": cp.stderr[-2000:]}
    return json.loads(cp.stdout)


# ---------------------------------------------------------------------------------------------------------------
# bundle → tasks
# ---------------------------------------------------------------------------------------------------------------
def bundle_tasks(bundle: dict) -> tuple[str, list[dict]]:
    jobs = ((bundle.get("resources") or {}).get("jobs") or {})
    if len(jobs) != 1:
        raise SetupError(f"databricks.yml must hold exactly one job (found {len(jobs)})")
    key, job = next(iter(jobs.items()))
    tasks = job.get("tasks") or []
    deps = {t["task_key"]: [d["task_key"] for d in t.get("depends_on") or []] for t in tasks}
    order, done, pending = [], set(), [t["task_key"] for t in tasks]
    while pending:
        ready = [k for k in pending if all(d in done for d in deps[k])]
        if not ready:
            raise SetupError("depends_on cycle in databricks.yml: " + ",".join(pending))
        order.append(ready[0])
        done.add(ready[0])
        pending.remove(ready[0])
    by = {t["task_key"]: t for t in tasks}
    return key, [by[k] for k in order]


_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def substitute(arg: str, root: Path, remaps: list) -> str:
    def rep(m):
        ref = m.group(1)
        if not ref.startswith("var."):
            raise SetupError(f"unsupported interpolation ${{{ref}}} in a task parameter")
        name = ref[4:]
        return VARS_PLACEHOLDER.get(name, f"dryrun_{name}")
    out = _VAR_RE.sub(rep, arg)
    for pre in REMAP_PREFIXES:
        if out == pre or out.startswith(pre + "/"):
            new = str(root) + out
            remaps.append({"from": out, "to": "<root>" + out})
            return new
    return out


# ---------------------------------------------------------------------------------------------------------------
# strace analysis
# ---------------------------------------------------------------------------------------------------------------
_LINE_RE = re.compile(r"^(?P<pid>\d+)\s+(?P<rest>.*)$")
_CALL_RE = re.compile(r"^(?P<call>\w+)\((?P<args>.*)\)\s+=\s+(?P<ret>.*)$")
_FDPATH_RE = re.compile(r"^(-?\d+|AT_FDCWD)<(?P<path>[^>]*)>")


def _split_args(s: str) -> list[str]:
    out, cur, depth, q, esc = [], [], 0, False, False
    for ch in s:
        if q:
            cur.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                q = False
            continue
        if ch == '"':
            q = True
        elif ch in "{[<(":
            depth += 1
        elif ch in "}]>)":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    if cur:
        out.append("".join(cur).strip())
    return out


def _unq(a: str) -> str | None:
    if a.startswith('"'):
        end = a.rfind('"')
        return bytes(a[1:end], "utf-8").decode("unicode_escape", errors="replace") if end > 0 else None
    return None


def parse_trace(path: Path) -> list[dict]:
    """strace -f -y lines → [{pid, call, args[], ret}] with unfinished/resumed pairs joined."""
    pending: dict[str, str] = {}
    calls = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LINE_RE.match(raw)
        if not m:
            continue
        pid, rest = m.group("pid"), m.group("rest")
        if rest.endswith("<unfinished ...>"):
            pending[pid] = rest[: -len("<unfinished ...>")].rstrip()
            continue
        r = re.match(r"^<\.\.\. (\w+) resumed>\s*(.*)$", rest)
        if r:
            head = pending.pop(pid, None)
            if head is None:
                continue
            rest = head + r.group(2)
        c = _CALL_RE.match(rest)
        if not c:
            continue
        calls.append({"pid": pid, "call": c.group("call"), "args": _split_args(c.group("args")),
                      "ret": c.group("ret").strip(), "raw": raw})
    return calls


def _fd_path(arg: str) -> str | None:
    m = _FDPATH_RE.match(arg)
    return m.group("path") if m else None


def analyse_trace(calls: list[dict], vol_root: str, fake_root: str, task: str) -> tuple[list[dict], list[str], dict]:
    """→ (findings, kept trace lines, counts). Paths are real paths as strace -y prints them."""
    def under(p: str | None, root: str) -> bool:
        return bool(p) and (p == root or p.startswith(root + "/"))
    findings, kept = [], []
    counts = {"calls": len(calls), "volume_calls": 0, "network_calls": 0, "write_opens_on_volume": 0}
    mode: dict[tuple[str, str], bool] = {}

    def add(code, rule, c, p):
        findings.append({"task": task, "code": code, "rule": rule, "syscall": c["call"],
                         "path": "<root>" + p[len(fake_root):] if p and p.startswith(fake_root) else p,
                         "detail": c["raw"].split(" ", 1)[-1][:300].replace(fake_root, "<root>")})
    for c in calls:
        call, args, ret = c["call"], c["args"], c["ret"]
        if call == "connect":
            counts["network_calls"] += 1
            kept.append(c["raw"])
            continue
        p = None
        if call == "openat":
            rp = _FD_PATH_RET(ret)
            p = rp or (_unq(args[1]) if len(args) > 1 else None)
            if p and not p.startswith("/") and args and _fd_path(args[0]):
                p = _fd_path(args[0]).rstrip("/") + "/" + p
        elif args:
            p = _fd_path(args[0])
        if not under(p, vol_root):
            continue
        kept.append(c["raw"].replace(fake_root, "<root>"))
        counts["volume_calls"] += 1
        ok = not ret.startswith("-1")
        if call == "openat":
            flags = args[2] if len(args) > 2 else ""
            fd = ret.split("<", 1)[0]
            wr = "O_WRONLY" in flags or "O_RDWR" in flags
            if ok:
                mode[(fd, p)] = wr
                if wr:
                    counts["write_opens_on_volume"] += 1
            low = p.lower()
            # an attempt counts too: a failed O_RDWR open still shows SQLite reaching for the Volume
            if "O_RDWR" in flags and low.endswith(SQLITE_SUFFIXES):
                add("TC_SQLITE_ON_VOLUME", "sqlite_rdwr_open" + ("" if ok else "_attempt"), c, p)
            if "O_CREAT" in flags and low.endswith(SQLITE_SIDE):
                add("TC_SQLITE_ON_VOLUME", "sqlite_side_file" + ("" if ok else "_attempt"), c, p)
        elif call == "lseek":
            fd = args[0].split("<", 1)[0]
            if mode.get((fd, p)) and not (len(args) >= 3 and args[1] == "0" and args[2] == "SEEK_CUR"):
                add("TC_SEEK_ON_VOLUME", "seek_on_write_fd", c, p)
        elif call in ("pwrite64", "pwritev", "pwritev2"):
            add("TC_SEEK_ON_VOLUME", "positional_write", c, p)
        elif call == "ftruncate":
            add("TC_SEEK_ON_VOLUME", "ftruncate", c, p)
        elif call == "fcntl":
            if len(args) > 1 and re.search(r"F_(OFD_)?SETLKW?\b", args[1]) and "F_UNLCK" not in "".join(args[2:]):
                add("TC_SQLITE_ON_VOLUME", "posix_record_lock", c, p)
    return findings, kept, counts


def _FD_PATH_RET(ret: str) -> str | None:
    m = re.match(r"^\d+<([^>]*)>", ret)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------------------------------------------
# parity (T8)
# ---------------------------------------------------------------------------------------------------------------
def parity(run_dir: Path, dsmap: dict, roots: dict, task_steps: set, evidence: Path, fake_root: Path) -> dict:
    sys.path.insert(0, str(PLUGIN / "skills" / "verify"))
    import compare as cmp  # noqa: E402  (same comparator file the run used; its sha is checked below)
    rc = cmp.RunContext(run_dir)
    contract_p = run_dir / "verify" / "contract.json"
    contract = ac.load_json(contract_p) if contract_p.is_file() else None
    rep = {"schema": "axis-migrate.target-dryrun-compare.v1", "run_id": rc.run_id, "comparator": cmp.comparator_block(),
           "problems": [], "datasets": {}}
    if contract is None:
        rep["problems"].append("CONTRACT_MISSING: the run has no verify/contract.json")
        contract = {}
    tol_sha = ac.sha256_bytes(rc.tol_text.encode()) if rc.tol_text is not None else None
    kd_sha = ac.sha256_bytes(rc.kd_text.encode()) if rc.kd_text is not None else None
    rep["tolerances"] = {"sha256": tol_sha, "contract_sha256": (contract.get("tolerances") or {}).get("sha256"),
                         "source": "runs/<id>/snapshot/tolerances.yaml"}
    rep["known_differences"] = {"sha256": kd_sha,
                                "contract_sha256": (contract.get("known_differences") or {}).get("sha256")}
    rep["contract_hash"] = contract.get("contract_hash")
    if rep["tolerances"]["sha256"] != rep["tolerances"]["contract_sha256"]:
        rep["problems"].append("TOLERANCE_CHANGED_AFTER_RUN")
    if rep["known_differences"]["sha256"] != rep["known_differences"]["contract_sha256"]:
        rep["problems"].append("KNOWN_DIFFERENCES_CHANGED_AFTER_RUN")
    run_cmp = (contract.get("comparator") or {}).get("sha256")
    rep["comparator_matches_run"] = run_cmp == rep["comparator"]["sha256"]
    if not rep["comparator_matches_run"]:
        rep["problems"].append("COMPARATOR_CHANGED_SINCE_RUN")
    started = cmp.parse_time(contract.get("run_started_at"), end_of_day=False) if contract.get("run_started_at") \
        else rc.started_at
    book = cmp.ToleranceBook(rc.tol_doc, started or _dt.datetime.now(_dt.timezone.utc), rc.measure_crs())
    bc = cmp._by_construction(rc)
    results = {}
    for f in sorted((run_dir / "verify" / "compare").glob("*.json")):
        r = ac.load_json(f)
        if isinstance(r, dict) and r.get("dataset_id"):
            results[r["dataset_id"]] = r
    out_dir = evidence / "compare"
    out_dir.mkdir(parents=True, exist_ok=True)
    legacy_root = rc.legacy_root

    def new_path(ds: str) -> str | None:
        d = dsmap["datasets"].get(ds)
        if d is None or d["root"] == "input":
            return None
        return str(roots[d["root"]] / d["relpath"])

    def resolve(ds: str) -> dict | None:
        d = rc.datasets.get(ds)
        if d is None:
            return None
        lg = d.get("legacy") or {}
        leg = {"path": str(legacy_root / lg["path"]), "layer": lg.get("layer")} if lg.get("path") else None
        if d.get("role") == "input":
            return {"legacy": leg, "new": leg}
        np_ = new_path(ds)
        return {"legacy": leg, "new": {"path": np_, "layer": (d.get("new") or {}).get("layer")} if np_ else None}
    m_out = {o["dataset_id"]: o for o in rc.manifest.get("outputs") or [] if o.get("dataset_id")}
    for ds, r0 in sorted(results.items()):
        compared = (r0.get("family") in ("raster", "vector", "table") and bool(r0.get("checks"))
                    and not r0.get("excluded") and r0.get("verdict") != "NOT_COMPARED")
        step = r0.get("step_id")
        entry = {"run_verdict": r0.get("verdict"), "step_id": step}
        if not compared:
            entry.update(state="not_compared_in_run", why=",".join(r0.get("reason_codes") or []))
            rep["datasets"][ds] = entry
            continue
        if step not in task_steps:
            entry.update(state="not_produced_by_the_job", why=f"{step} is not a task")
            rep["datasets"][ds] = entry
            continue
        d = rc.datasets[ds]
        lg = d.get("legacy") or {}
        ref = d.get("reference") or {}
        L_rel = lg.get("path")
        if ref.get("source") == "equivalent":
            via = rc.datasets.get(ref.get("via_dataset") or "") or {}
            L_rel = (via.get("legacy") or {}).get("path")
        legacy_abs = str(legacy_root / L_rel)
        new_abs = new_path(ds)
        spec = {"dataset_id": ds, "kind": d.get("kind"), "role": d.get("role"), "step_id": step,
                "layer": lg.get("layer"), "new_layer": (d.get("new") or {}).get("layer"),
                "sheet": lg.get("sheet") or (d.get("compare") or {}).get("sheet"), "compare": d.get("compare") or {},
                "measure_crs": rc.measure_crs(), "run_started_at": contract.get("run_started_at"),
                "by_construction": bc.get(ds, {}), "pinned": (contract.get("references") or {}).get(L_rel) or {},
                "pairing": {"method": "dryrun_same_pairing_as_run", "evidence": [], "state": "confirmed"},
                "tol_refs": ["tol:" + tol_sha] if tol_sha else []}
        zones = (d.get("compare") or {}).get("zones")
        if zones:
            zds, zkey = cmp._ds_col(zones)
            zp = resolve(zds)
            if zp and zp.get("legacy"):
                spec["zones"] = {"path": zp["legacy"]["path"], "layer": zp["legacy"].get("layer"), "key": zkey}
        invs = []
        for inv in rc.graph.get("invariants") or []:
            if cmp._ds_col(str(inv.get("lhs")))[0] != ds:
                continue
            inv = dict(inv)
            resolved, missing = {}, []
            for role_ in ("lhs", "rhs"):
                ref_ = inv.get(role_)
                if isinstance(ref_, str) and ref_.startswith("ds."):
                    rp = resolve(cmp._ds_col(ref_)[0])
                    if not rp or not rp.get("legacy") or not rp.get("new"):
                        missing.append(ref_)
                    else:
                        resolved[role_] = rp
            if inv.get("zones"):
                zp = resolve(cmp._ds_col(str(inv["zones"]))[0])
                if zp and zp.get("legacy"):
                    resolved["zones"] = zp["legacy"]
                else:
                    missing.append(inv["zones"])
            inv["resolved"] = resolved
            if missing:
                inv["unavailable"] = f"not available in the dry run: {missing}"
            invs.append(inv)
        spec["invariants"] = invs
        r = cmp.compare_pair(legacy_abs, new_abs, spec, book=book, kds=rc.kds)
        # no absolute paths in evidence
        r["legacy"]["path"] = f"legacy:{L_rel}"
        r["new"]["path"] = "dryrun:" + str(Path(new_abs).relative_to(fake_root)) if new_abs else None
        for k in ("traceback",):
            if k in r:
                r[k] = r[k].replace(str(fake_root), "<root>")
        r["result_id"] = ac.object_id("cmp", {k: v for k, v in r.items() if k != "result_id"})
        ac.dump_json(out_dir / f"{ds}.json", r)
        run_out = m_out.get(ds) or {}
        new_sha = ac.sha256_file(new_abs) if new_abs and Path(new_abs).is_file() else None
        same = None
        if new_sha and run_out.get("path") and (run_dir / run_out["path"]).is_file():
            rp = run_dir / run_out["path"]
            same = new_sha == ac.sha256_file(rp) or (ac.content_sha256(rp) is not None
                                                     and ac.content_sha256(rp) == ac.content_sha256(new_abs))
        entry.update(state="compared", verdict=r["verdict"], reason_codes=r["reason_codes"],
                     diagnostic_codes=r.get("diagnostic_codes", []), within_tolerance=r.get("within_tolerance", []),
                     new_sha256=new_sha, run_output_sha256=run_out.get("sha256"), identical_to_run_output=same,
                     result_id=r["result_id"], result="compare/" + f"{ds}.json")
        rep["datasets"][ds] = entry
    comp = [e for e in rep["datasets"].values() if e["state"] == "compared"]
    rep["n_compared"] = len(comp)
    rep["n_pass"] = sum(1 for e in comp if e["verdict"] in ac.PASS_STATES)
    # the job's own outputs that the run compared; outputs of untasked (manual, bridged) steps are uploaded inputs
    rep["n_compared_in_run"] = sum(1 for e in rep["datasets"].values() if e["state"] == "compared")
    rep["not_produced_by_the_job"] = sorted(k for k, e in rep["datasets"].items()
                                            if e["state"] == "not_produced_by_the_job")
    rep["verdict"] = ac.worst([e["verdict"] for e in comp]) if comp else "NOT_COMPARED"
    rep["report_id"] = ac.object_id("dryrun-cmp", {k: v for k, v in rep.items() if k != "report_id"})
    return rep


# ---------------------------------------------------------------------------------------------------------------
# main dry run
# ---------------------------------------------------------------------------------------------------------------
def dry_run(mig: Path, run_id: str, evidence: Path, *, keep: bool = False, use_strace: bool = True) -> dict:
    t_start = time.monotonic()
    dbx = mig / "databricks"
    run_dir = mig / "runs" / run_id
    for p in (dbx / "databricks.yml", dbx / "requirements-target.lock", dbx / "runtime-manifest.json",
              run_dir / "manifest.json"):
        if not p.is_file():
            raise SetupError(f"missing {p.relative_to(mig)}")
    bundle = yaml.safe_load((dbx / "databricks.yml").read_text(encoding="utf-8"))
    runtime = ac.load_json(dbx / "runtime-manifest.json")
    job_key, tasks = bundle_tasks(bundle)
    whls = sorted({lib["whl"] for t in tasks for lib in t.get("libraries") or [] if lib.get("whl")})
    if len(whls) != 1:
        raise SetupError(f"tasks must share exactly one wheel library (found {whls})")
    wheel = (dbx / whls[0]).resolve()
    if not wheel.is_file() or dbx.resolve() not in wheel.parents:
        raise SetupError(f"wheel {whls[0]} not found inside migration/databricks/")
    members = md.wheel_members(wheel)
    dsmap = json.loads(members[f"{md.PACKAGE}/_datasets.json"])
    if evidence.exists():
        shutil.rmtree(evidence)
    (evidence / "logs").mkdir(parents=True)
    # canonical once (macOS: /var -> /private/var): every path below is built from it and compared with resolved roots
    tmp = Path(tempfile.mkdtemp(prefix="axis-dryrun-")).resolve()
    res: dict = {"schema": "axis-migrate.target-dryrun.v1", "run_id": run_id, "job_key": job_key,
                 "wheel": {"file": wheel.name, "sha256": ac.sha256_file(wheel)}, "tasks": [], "problems": []}
    install_log: list = []
    try:
        root = tmp / "root"
        vol = root / "Volumes" / VARS_PLACEHOLDER["catalog"] / VARS_PLACEHOLDER["schema"] / VARS_PLACEHOLDER["volume"]
        roots = {"input": vol / "inputs", "work": vol / "work", "output": vol / "outputs"}
        for r in roots.values():
            r.mkdir(parents=True)
        (root / "local_disk0" / "tmp").mkdir(parents=True)
        fake_root = str(root.resolve())
        vol_root = str((root / "Volumes").resolve())
        # ---- network isolation
        unshare = unshare_works()
        strace = find_strace() if use_strace else None
        net = {"isolated": unshare, "method": "unshare -n" if unshare else None}
        if not unshare:
            net["note"] = "network not isolated (unshare -n not permitted here)"
            if strace:
                net["fault_injection"] = "connect() fails with ENETUNREACH via strace -e inject (every task)"
        res["network"] = net
        # ---- venv
        t0 = time.monotonic()
        venv = make_venv(tmp, runtime, dbx / "requirements-target.lock", wheel, install_log)
        venv["seconds"] = round(time.monotonic() - t0, 2)
        res["venv"] = json.loads(json.dumps({k: v for k, v in venv.items() if k != "venv"}).replace(str(tmp), "<tmp>"))
        ac.dump_json(evidence / "logs" / "install.json",
                     json.loads(json.dumps(install_log).replace(str(tmp), "<tmp>")))
        # ---- inputs
        legacy = ac.legacy_root(mig, ac.load_json(run_dir / "snapshot" / "graph.json"))
        inv_p = run_dir / "snapshot" / "inventory.json"
        inv = ac.load_json(inv_p) if inv_p.is_file() else {}
        pins = {f.get("path"): f.get("sha256") for f in inv.get("files") or []}
        inputs = []
        for ds, d in sorted(dsmap["datasets"].items()):
            if d["root"] != "input":
                continue
            src = legacy / d["relpath"]
            rec = {"dataset": ds, "relpath": d["relpath"], "inventory_sha256": pins.get(d["relpath"])}
            if not src.is_file():
                rec.update(ok=False, problem="missing in the legacy folder")
            else:
                dst = roots["input"] / d["relpath"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                comps = []
                if src.suffix.lower() == ".shp":
                    for c in sorted(src.parent.glob(src.stem + ".*")):
                        if c != src and not c.name.lower().endswith(".aux.xml"):
                            shutil.copyfile(c, dst.parent / c.name)
                            comps.append(c.name)
                a, b = ac.sha256_file(src), ac.sha256_file(dst)
                rec.update(legacy_sha256=a, copied_sha256=b, companions=comps,
                           ok=(a == b and (rec["inventory_sha256"] in (None, a))))
                if rec["inventory_sha256"] is None:
                    rec["note"] = "no inventory pin; checked against the legacy file only"
            if not rec["ok"]:
                res["problems"].append(f"INPUT_HASH_MISMATCH:{ds}")
            inputs.append(rec)
        # ---- tasks
        remaps: list = []
        layout_tasks = []
        trace_lines: list[str] = []
        findings: list[dict] = []
        trace_counts = {}
        rss = {}
        home = tmp / "home"
        home.mkdir()
        (tmp / "tmp").mkdir()
        py = Path(venv.get("venv", tmp / "venv")) / "bin" / "python"
        script = Path(venv.get("venv", tmp / "venv")) / "bin" / md.ENTRY_POINT
        env = {"PATH": f"{py.parent}:/usr/bin:/bin", "HOME": str(home), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
               "TZ": "UTC", "TMPDIR": str(tmp / "tmp")}
        res["env_keys"] = sorted(env)
        steps_in_bundle = []
        for t in tasks:
            params = list((t.get("python_wheel_task") or {}).get("parameters") or [])
            sid = params[params.index("--step") + 1] if "--step" in params and params.index("--step") + 1 < len(params) \
                else None
            steps_in_bundle.append(sid)
        if venv.get("install_ok"):
            cwd0 = tmp / "probe-cwd"
            cwd0.mkdir()
            res["probe"] = probe_venv(py, [s for s in steps_in_bundle if s], env, cwd0)
        else:
            res["probe"] = None
        for t in tasks:
            key = t["task_key"]
            pwt = t.get("python_wheel_task") or {}
            params = list(pwt.get("parameters") or [])
            run_args = [substitute(a, root, remaps) for a in params]
            layout_tasks.append({"task_key": key, "entry_point": pwt.get("entry_point"),
                                 "package_name": pwt.get("package_name"), "bundle_parameters": params,
                                 "run_parameters": [a.replace(fake_root, "<root>") for a in run_args]})
            rec = {"task_key": key, "exit_code": None, "status": "not_run"}
            if not venv.get("install_ok"):
                rec["why"] = "no dry-run environment"
                res["tasks"].append(rec)
                continue
            cwd = tmp / "cwd" / key
            cwd.mkdir(parents=True)
            argv = [str(py), "-I", str(script)] + run_args
            trace_p = tmp / f"trace-{key}.log"
            if strace:
                argv = [strace, "-f", "-qq", "-y", "--seccomp-bpf", "-e", f"trace={TRACE_SET}",
                        "-o", str(trace_p)] + ([] if unshare else ["-e", "inject=connect:error=ENETUNREACH"]) + argv
            if unshare:
                argv = [shutil.which("unshare"), "-n"] + argv
            t1 = time.monotonic()
            with open(evidence / "logs" / f"{key}.stdout", "wb") as so, \
                    open(evidence / "logs" / f"{key}.stderr", "wb") as se:
                proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=so, stderr=se, stdin=subprocess.DEVNULL,
                                        start_new_session=True)
                try:
                    _, wst, ru = _wait(proc, TASK_TIMEOUT_S)
                    code = os.waitstatus_to_exitcode(wst)
                    timed_out = False
                except TimeoutError:
                    code, ru, timed_out = None, None, True
            rec.update(exit_code=code, timed_out=timed_out, seconds=round(time.monotonic() - t1, 2),
                       status="ok" if code == 0 else "failed")
            for lp in (evidence / "logs" / f"{key}.stderr", evidence / "logs" / f"{key}.stdout"):
                txt = lp.read_text(encoding="utf-8", errors="replace")
                if fake_root in txt or str(tmp) in txt:
                    lp.write_text(txt.replace(fake_root, "<root>").replace(str(tmp), "<tmp>"), encoding="utf-8")
            rss_div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0     # bytes on macOS, KiB on Linux
            rss[key] = {"peak_rss_mb": round(ru.ru_maxrss / rss_div, 1) if ru else None,
                        "seconds": rec["seconds"], "exit_code": code}
            sid = steps_in_bundle[tasks.index(t)]
            io_src = root / "local_disk0" / "tmp" / "axis" / f"{sid}.io.json"
            if sid and io_src.is_file():
                txt = io_src.read_text(encoding="utf-8").replace(fake_root, "<root>")
                (evidence / "logs" / f"{key}.io.json").write_text(txt, encoding="utf-8")
            if strace and trace_p.is_file():
                calls = parse_trace(trace_p)
                f, kept, counts = analyse_trace(calls, vol_root, fake_root, key)
                trace_counts[key] = dict(counts, findings=len(f))
                findings += f[:FINDINGS_PER_TASK_MAX]
                trace_lines.append(f"# task {key}: {len(kept)} of {counts['calls']} traced calls touch the fake "
                                   f"Volume or the network")
                trace_lines += [k.replace(str(tmp), "<tmp>") for k in kept]
            res["tasks"].append(rec)
        # ---- evidence
        layout = {"schema": "axis-migrate.target-dryrun-layout.v1", "root": "<root> (temporary directory)",
                  "volume": "<root>/Volumes/" + "/".join(VARS_PLACEHOLDER[k] for k in ("catalog", "schema", "volume")),
                  "roots": {k: "<root>" + str(v)[len(fake_root):] for k, v in roots.items()},
                  "scratch": "<root>/local_disk0/tmp/axis", "variables": dict(VARS_PLACEHOLDER),
                  "prefix_remap": [{"from": p, "to": "<root>" + p} for p in REMAP_PREFIXES],
                  "remapped_arguments": sorted({(r["from"], r["to"]) for r in remaps}),
                  "rewrite_rule": "the only rewrite of the shipped task parameters: ${var.*} → placeholders, then "
                                  "prefix /Volumes and /local_disk0 → <root>",
                  "tasks": layout_tasks, "inputs": inputs, "cwd": "a new empty directory per task",
                  "process": "python -I <venv>/bin/axis-pipeline <task parameters> (console script of the wheel)",
                  "env": res["env_keys"], "pythonpath": "unset", "home": "temporary",
                  "network": net, "strace": {"used": bool(strace), "trace_set": TRACE_SET if strace else None},
                  "python": {"requested": venv.get("python_requested"), "used": venv.get("python_used"),
                             "deviation": venv.get("deviation")}}
        layout["remapped_arguments"] = [{"from": a, "to": b} for a, b in layout["remapped_arguments"]]
        ac.dump_json(evidence / "layout.json", layout)
        (evidence / "trace.log").write_text("\n".join(trace_lines) + "\n", encoding="utf-8")
        ac.dump_json(evidence / "trace-findings.json", {
            "schema": "axis-migrate.target-trace-findings.v1", "strace": bool(strace), "trace_set": TRACE_SET,
            "rules": {"TC_SEEK_ON_VOLUME": "on a write fd under the Volume: lseek other than (0, SEEK_CUR), pwrite*, "
                                           "ftruncate",
                      "TC_SQLITE_ON_VOLUME": "O_RDWR open of *.gpkg|*.sqlite|*.db, creation of -journal|-wal|-shm, "
                                             "a POSIX record lock on a file under the Volume"},
            "allowed": "random reads (assumption 09 §4.2)", "counts": trace_counts, "findings": findings,
            "codes": sorted({f["code"] for f in findings})})
        mem = _node_memory(mig)
        ac.dump_json(evidence / "rss.json", {"schema": "axis-migrate.target-rss.v1", "tasks": rss,
                                             "node_memory_gb": mem, "budget_mb": round(0.6 * mem * 1024, 1),
                                             "measure": "ru_maxrss of the task process tree (strace included)"})
        all_ok = venv.get("install_ok") and all(r.get("exit_code") == 0 for r in res["tasks"]) and res["tasks"]
        if all_ok:
            task_steps = {s for s in steps_in_bundle if s}
            rep = parity(run_dir, dsmap, roots, task_steps, evidence, root)
        else:
            rep = {"schema": "axis-migrate.target-dryrun-compare.v1", "run_id": run_id, "verdict": "NOT_COMPARED",
                   "problems": ["DRYRUN_INCOMPLETE: not every task ran"], "datasets": {}, "n_compared": 0, "n_pass": 0}
            rep["report_id"] = ac.object_id("dryrun-cmp", rep)
        ac.dump_json(evidence / "compare-report.json", rep)
        res["compare"] = {k: rep.get(k) for k in ("verdict", "n_compared", "n_pass", "report_id", "problems",
                                                  "comparator_matches_run")}
        res["trace"] = {"strace": bool(strace), "codes": sorted({f["code"] for f in findings}),
                        "findings": len(findings)}
        res["rss"] = rss
        res["wall_time_s"] = round(time.monotonic() - t_start, 2)
        ac.dump_json(evidence / "dryrun.json", res)
        return res
    finally:
        if keep:
            res["kept"] = str(tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def _node_memory(mig: Path) -> float:
    try:
        return float(md.target_block(yaml.safe_load((mig / "params.yaml").read_text(encoding="utf-8")) or {})
                     ["node_memory_gb"])
    except (OSError, md.Refused, TypeError, ValueError):
        return float(md.DEFAULT_NODE_MEMORY_GB)


def _wait(proc: subprocess.Popen, timeout: float):
    import signal
    import threading
    fired = threading.Event()

    def kill():
        fired.set()
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    timer = threading.Timer(timeout, kill)
    timer.start()
    try:
        pid, wst, ru = os.wait4(proc.pid, 0)
    finally:
        timer.cancel()
    proc.returncode = os.waitstatus_to_exitcode(wst)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if fired.is_set():
        raise TimeoutError
    return pid, wst, ru


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Dry-run the committed wheel in a Databricks-like layout (offline).")
    ap.add_argument("migration")
    ap.add_argument("--run", required=True, help="the verified run (sealed, chained)")
    ap.add_argument("--evidence", required=True, help="directory for the dry-run evidence (replaced)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary root and venv")
    ap.add_argument("--no-strace", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        mig = md.resolve_migration(a.migration)
        res = dry_run(mig, a.run, Path(a.evidence).resolve(), keep=a.keep, use_strace=not a.no_strace)
    except (SetupError, md.Refused) as e:
        print(f"dryrun: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1))
    else:
        print(f"dry run: {sum(1 for t in res['tasks'] if t.get('exit_code') == 0)}/{len(res['tasks'])} task(s) ok; "
              f"parity {res['compare'].get('verdict')} ({res['compare'].get('n_pass')}/{res['compare'].get('n_compared')});"
              f" trace codes {res['trace']['codes'] or 'none'}; {res['wall_time_s']} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
