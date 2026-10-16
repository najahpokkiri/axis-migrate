"""axis_boot: runs INSIDE the step process (local venv, reference env or sandbox). 05 §4, D5-5…D5-8.

    python -I axis_boot.py --step <id> --ctx <run>/ctx/<step>.json --snapshot <run>/snapshot \
                           --result <run>/logs/<step>.result.json [--expect <run>/env/expect.json]
    python -I axis_boot.py --probe [--crs EPSG:4326 --crs EPSG:2169]       # environment probe → JSON on stdout

What it does, in order:
 1. PROJ network off (`pyproj.network.set_network_enabled(False)`, asserted).
 2. Real-module check for numpy, pandas, geopandas, rasterio, pyogrio, shapely, pyproj, openpyxl: plain module
    type, origin a real file under the environment's site-packages, compiled extension present, version as
    expected (the lock); `sys.meta_path` only stdlib finders plus an allow-list; no `unittest.mock`;
    `builtins.open` and `subprocess` untouched. Then the canary: numpy equality, a /vsimem/ GeoTIFF round trip
    and a 1-row GPKG round trip. Any failure → exit 11 STUB_DETECTED.
 3. Imports `axis_pipeline` from the snapshot only, builds `Ctx` from ctx.json, calls the step through the
    runtime (`run_step`), catching BaseException (SystemExit → STEP_CALLED_EXIT).
 4. Re-runs the module check and canary after the step (a step can install a stub mid-run).
 5. Verifies every declared output (exists, non-empty, mtime ≥ start, opens with its driver) and writes
    result.json itself — step code never writes it.

Exit codes (DESIGN §3, step process): 0 ok · 1 step exception · 2 input/parameter error · 3 output contract ·
11 stub detected / environment refused inside the step.

Stdlib only at import time; compatible with Python 3.12 (pipeline env) and 3.14 (QGIS reference env).
"""
from __future__ import annotations

import argparse
import builtins
import hashlib
import io
import json
import os
import platform
import sys
import time
import traceback
import types
from pathlib import Path

BOOT_VERSION = "1.0.0"
CHECKED_MODULES = {
    # module: (compiled extension that must be a real .so/.pyd, or None for pure-Python packages)
    "numpy": "numpy._core._multiarray_umath",
    "pandas": "pandas._libs.lib",
    "geopandas": None,
    "rasterio": "rasterio._base",
    "pyogrio": "pyogrio._io",
    "shapely": "shapely.lib",
    "pyproj": "pyproj._crs",
    "openpyxl": None,
}
META_PATH_ALLOW = {
    ("_frozen_importlib", "BuiltinImporter"), ("_frozen_importlib", "FrozenImporter"),
    ("_frozen_importlib_external", "PathFinder"), ("six", "_SixMetaPathImporter"),
    ("_distutils_hack", "DistutilsMetaFinder"),
}
EXT_SUFFIXES = (".so", ".pyd", ".dylib")
EXIT_OK, EXIT_EXC, EXIT_INPUT, EXIT_OUTPUT, EXIT_STUB = 0, 1, 2, 3, 11
CONTRACT_OUTPUT_REASONS = {"OUTPUT_MISSING", "OUTPUT_EMPTY", "OUTPUT_STALE", "OUTPUT_UNOPENABLE"}


# --------------------------------------------------------------------------------------------------------------
# environment / stub checks
# --------------------------------------------------------------------------------------------------------------
def _site_dirs() -> list[str]:
    import site
    import sysconfig
    dirs = list(getattr(site, "getsitepackages", lambda: [])())
    dirs += [sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]]
    if os.name == "nt":  # Windows getsitepackages() also lists the env prefix itself: keep site-packages only
        dirs = [d for d in dirs if d and os.path.basename(os.path.normpath(d)).lower() == "site-packages"]
    return sorted({os.path.realpath(d) for d in dirs if d})


def _under_any(path: str, roots: list[str]) -> bool:
    nc = os.path.normcase                       # Windows: backslashes and case-insensitive; POSIX: unchanged
    rp = nc(os.path.realpath(path))
    return any(rp == nc(r) or rp.startswith(nc(r).rstrip(os.sep) + os.sep) for r in roots)


def module_check(expect: dict | None, *, baseline: dict | None = None) -> dict:
    """Real-module check (D5-8 layer 2). Returns {passed, problems[], modules{}, meta_path[]}."""
    import importlib
    import subprocess
    problems: list[str] = []
    site_dirs = (expect or {}).get("site_packages") or _site_dirs()
    want_versions = (expect or {}).get("versions") or {}
    enforce_versions = bool((expect or {}).get("enforce_versions", True))
    mods: dict[str, dict] = {}
    for name, ext in sorted(CHECKED_MODULES.items()):
        rec: dict = {}
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - recorded as a problem, then the check fails
            problems.append(f"{name}: import failed: {type(exc).__name__}: {exc}")
            mods[name] = {"import_error": str(exc)}
            continue
        rec["type_ok"] = type(mod) is types.ModuleType
        if not rec["type_ok"]:
            problems.append(f"{name}: module object is {type(mod).__name__}, not a plain module")
        spec = getattr(mod, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec is not None else None
        rec["origin"] = origin
        if not origin or not os.path.isfile(origin):
            problems.append(f"{name}: origin {origin!r} is not a real file")
        elif not _under_any(origin, site_dirs):
            problems.append(f"{name}: origin {origin} is outside the environment's site-packages")
        if ext:
            try:
                emod = importlib.import_module(ext)
                eorig = getattr(getattr(emod, "__spec__", None), "origin", None) or ""
                rec["extension"] = eorig
                if type(emod) is not types.ModuleType or not eorig.endswith(EXT_SUFFIXES) or not os.path.isfile(eorig):
                    problems.append(f"{name}: compiled extension {ext} is not a real shared object ({eorig!r})")
                elif not _under_any(eorig, site_dirs):
                    problems.append(f"{name}: extension {eorig} is outside site-packages")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{name}: compiled extension {ext} missing: {exc}")
        ver = getattr(mod, "__version__", None)
        rec["version"] = ver if isinstance(ver, str) else repr(ver)
        if name in want_versions:
            rec["expected"] = want_versions[name]
            if ver != want_versions[name]:
                msg = f"{name}: version {ver!r} != lock {want_versions[name]!r}"
                if enforce_versions:
                    problems.append(msg)
                else:
                    rec["version_differs_from_lock"] = True
        if baseline and name in baseline.get("_ids", {}) and id(mod) != baseline["_ids"][name]:
            problems.append(f"{name}: module object replaced during the step")
        mods[name] = rec
    meta = []
    for f in sys.meta_path:
        cls = f if isinstance(f, type) else type(f)
        key = (getattr(cls, "__module__", "?"), getattr(cls, "__name__", "?"))
        meta.append(f"{key[0]}.{key[1]}")
        if key not in META_PATH_ALLOW and not key[0].startswith("__editable__"):
            problems.append(f"sys.meta_path holds a non-standard finder {key[0]}.{key[1]}")
    if "unittest.mock" in sys.modules:
        problems.append("unittest.mock is imported")
    if builtins.open is not io.open:
        problems.append("builtins.open has been replaced")
    sp = sys.modules.get("subprocess")
    if sp is not None and (type(sp) is not types.ModuleType or getattr(sp, "run", None) is not subprocess.run):
        problems.append("subprocess has been replaced")
    return {"passed": not problems, "problems": problems, "modules": mods, "meta_path": meta}


def canary() -> dict:
    """D5-8 layer 3: behaviour a stub cannot fake."""
    out = {"numpy_eq": False, "vsimem_gtiff": False, "gpkg_roundtrip": False, "problems": []}
    try:
        import numpy as np
        out["numpy_eq"] = (np.arange(3) == np.arange(3)[::-1]).tolist() == [False, True, False]
        if not out["numpy_eq"]:
            out["problems"].append("numpy equality canary failed")
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"numpy canary error: {exc}")
    try:
        import warnings
        import numpy as np
        import rasterio
        from rasterio.io import MemoryFile
        from rasterio.transform import from_origin
        data = np.array([[[1.5, 2.5], [3.5, 4.5]]], dtype="float32")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with MemoryFile() as mf:
                with mf.open(driver="GTiff", width=2, height=2, count=1, dtype="float32", crs="EPSG:4326",
                             transform=from_origin(0, 2, 1, 1)) as ds:
                    ds.write(data)
                with mf.open() as ds:
                    back = ds.read()
                    ok = bool((back == data).all()) and ds.crs is not None and ds.crs.to_string() == "EPSG:4326"
        out["vsimem_gtiff"] = ok
        if not ok:
            out["problems"].append("rasterio /vsimem/ GeoTIFF round trip differs")
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"rasterio canary error: {exc}")
    try:
        import pyogrio
        import shapely
        import geopandas as gpd
        path = f"/vsimem/axis_canary_{os.getpid()}.gpkg"
        gdf = gpd.GeoDataFrame({"k": [7]}, geometry=[shapely.Point(6.1, 49.6)], crs="EPSG:4326")
        pyogrio.write_dataframe(gdf, path, driver="GPKG", layer="c")
        back = pyogrio.read_dataframe(path, layer="c")
        ok = len(back) == 1 and int(back["k"].iloc[0]) == 7 and back.geometry.iloc[0].equals(shapely.Point(6.1, 49.6))
        try:
            from pyogrio.raw import _vsimem_rmtree_toplevel as _rm  # type: ignore[attr-defined]
            _rm(path)
        except Exception:  # noqa: BLE001 - cleanup of an in-memory file is best effort
            pass
        out["gpkg_roundtrip"] = bool(ok)
        if not ok:
            out["problems"].append("pyogrio GPKG round trip differs")
    except Exception as exc:  # noqa: BLE001
        out["problems"].append(f"pyogrio canary error: {exc}")
    out["passed"] = not out["problems"]
    return out


def proj_network_off() -> dict:
    os.environ["PROJ_NETWORK"] = "OFF"
    import pyproj
    import pyproj.network
    pyproj.network.set_network_enabled(False)
    enabled = bool(pyproj.network.is_network_enabled())
    return {"PROJ_NETWORK": os.environ.get("PROJ_NETWORK"), "network_enabled": enabled}


def stub_check(expect: dict | None, baseline: dict | None = None) -> dict:
    m = module_check(expect, baseline=baseline)
    c = canary()
    passed = m["passed"] and c["passed"]
    return {"passed": passed, "canary": "ok" if c["passed"] else "failed", "problems": m["problems"] + c["problems"],
            "modules": m["modules"], "meta_path": m["meta_path"]}


def _module_ids() -> dict:
    return {n: id(sys.modules[n]) for n in CHECKED_MODULES if n in sys.modules}


# --------------------------------------------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------------------------------------------
def _cpu() -> dict:
    model, flags = None, []
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("model name") and model is None:
                    model = line.split(":", 1)[1].strip()
                if line.startswith("flags"):
                    fl = set(line.split(":", 1)[1].split())
                    flags = sorted(f.upper() for f in fl if f in {"avx", "avx2", "avx512f", "fma", "sse4_2", "neon"})
                    break
    except OSError:
        model = platform.processor() or None
    return {"model": model, "simd": flags, "machine": platform.machine()}


def probe(crs_list: list[str]) -> dict:
    import importlib.metadata as md
    net = proj_network_off()
    out: dict = {"boot_version": BOOT_VERSION, "python": platform.python_version(), "executable": sys.executable,
                 "prefix": sys.prefix, "implementation": platform.python_implementation(),
                 "platform": f"{sys.platform}-{platform.machine()}", "cpu": _cpu(), "site_packages": _site_dirs()}
    libs, native = {}, {}
    try:
        import numpy, pandas, geopandas, rasterio, pyogrio, shapely, pyproj, openpyxl  # noqa: E401
        for m in (numpy, pandas, geopandas, rasterio, pyogrio, shapely, pyproj, openpyxl):
            libs[m.__name__] = m.__version__
        native = {"rasterio.gdal": rasterio.__gdal_version__,
                  "rasterio.proj": ".".join(str(x) for x in rasterio.__proj_version__)
                  if isinstance(getattr(rasterio, "__proj_version__", None), tuple)
                  else str(getattr(rasterio, "__proj_version__", None)),
                  "pyogrio.gdal": pyogrio.__gdal_version_string__,
                  "pyproj.proj": pyproj.proj_version_str,
                  "shapely.geos": shapely.geos_version_string}
        try:
            from rasterio.env import GDALVersion  # noqa: F401
            from rasterio._env import get_proj_version  # type: ignore
            native["rasterio.proj"] = ".".join(str(x) for x in get_proj_version())
        except Exception:  # noqa: BLE001 - older/newer rasterio: keep the attribute form
            pass
    except Exception as exc:  # noqa: BLE001
        out["import_error"] = f"{type(exc).__name__}: {exc}"
    out["libs"], out["native"] = libs, native
    proj: dict = dict(net)
    try:
        import pyproj
        from pyproj.transformer import TransformerGroup
        d = pyproj.datadir.get_data_dir()
        proj["data_dir"] = d
        db = Path(d) / "proj.db"
        proj["proj_db_sha256"] = hashlib.sha256(db.read_bytes()).hexdigest() if db.is_file() else None
        pairs = []
        crs_sorted = sorted(set(crs_list))
        for a in crs_sorted:
            for b in crs_sorted:
                if a == b:
                    continue
                try:
                    g = TransformerGroup(a, b, always_xy=True)
                    pairs.append({"src": a, "dst": b, "grid_ops_unavailable": len(g.unavailable_operations),
                                  "op": g.transformers[0].description if g.transformers else None})
                except Exception as exc:  # noqa: BLE001
                    pairs.append({"src": a, "dst": b, "error": str(exc)})
        proj["crs_pairs"] = pairs
    except Exception as exc:  # noqa: BLE001
        proj["error"] = str(exc)
    out["proj"] = proj
    freeze = sorted({f"{(d.metadata['Name'] or '').lower().replace('_', '-')}=={d.version}"
                     for d in md.distributions() if d.metadata.get("Name")})
    out["freeze"] = freeze
    out["stub_check"] = stub_check(None)
    return out


# --------------------------------------------------------------------------------------------------------------
# step execution
# --------------------------------------------------------------------------------------------------------------
def _frames(tb, snapshot_pkg: str | None) -> dict:
    frames = traceback.extract_tb(tb) if tb is not None else []
    inner = frames[-1] if frames else None
    inner_pkg = None
    if snapshot_pkg:
        for fr in reversed(frames):
            if _under_any(fr.filename, [snapshot_pkg]) and \
                    not fr.filename.endswith("_axis_runtime.py"):
                inner_pkg = fr
                break
    fmt = (lambda f: f"{f.filename}:{f.lineno}" if f else None)
    return {"innermost_frame": fmt(inner), "innermost_pipeline_frame": fmt(inner_pkg)}


def verify_outputs(ctx_doc: dict, started_epoch: float) -> tuple[list[dict], list[str]]:
    """R3: exists, non-empty, mtime ≥ step start, opens with its declared driver."""
    recs, reasons = [], []
    for ds, spec in sorted(ctx_doc.get("outputs", {}).items()):
        p = Path(spec["path"])
        rec = {"dataset_id": ds, "path": str(p), "exists": p.exists()}
        if not p.exists():
            reasons.append(f"OUTPUT_MISSING:{ds}")
            recs.append(rec)
            continue
        st = p.stat()
        rec["bytes"] = st.st_size
        rec["mtime_after_start"] = st.st_mtime >= started_epoch - 1.0
        if st.st_size == 0:
            reasons.append(f"OUTPUT_EMPTY:{ds}")
        if not rec["mtime_after_start"]:
            reasons.append(f"OUTPUT_STALE:{ds}")
        rec["sha256"] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
        kind, fmt = spec.get("kind"), spec.get("format")
        try:
            if kind == "raster" or fmt == "GTiff":
                import rasterio
                with rasterio.open(p) as src:
                    rec["driver"] = src.driver
                    rec["opened_ok"] = src.driver == (fmt or src.driver)
            elif kind == "vector":
                import pyogrio
                info = pyogrio.read_info(p, layer=spec.get("layer")) if spec.get("layer") else pyogrio.read_info(p)
                rec["driver"] = info.get("driver")
                rec["features"] = int(info["features"])
                rec["opened_ok"] = (fmt is None or info.get("driver") == fmt)
            elif fmt == "XLSX":
                import openpyxl
                wb = openpyxl.load_workbook(p, read_only=True)
                rec["sheets"] = list(wb.sheetnames)
                wb.close()
                rec["driver"], rec["opened_ok"] = "XLSX", True
            elif fmt == "CSV":
                import csv
                with open(p, newline="", encoding="utf-8") as fh:
                    rec["rows"] = sum(1 for _ in csv.reader(fh))
                rec["driver"], rec["opened_ok"] = "CSV", True
            else:
                rec["driver"], rec["opened_ok"] = None, True
        except Exception as exc:  # noqa: BLE001 - an unopenable output is a recorded contract failure
            rec["opened_ok"] = False
            rec["open_error"] = f"{type(exc).__name__}: {exc}"
        if not rec.get("opened_ok"):
            reasons.append(f"OUTPUT_UNOPENABLE:{ds}")
        recs.append(rec)
    return recs, reasons


def _write_result(path: str, result: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.tmp")
    tmp.write_text(json.dumps(result, sort_keys=True, indent=1, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, p)


def install_audit_ledger(path: str) -> None:
    """Fallback read AND write ledger where strace is unavailable (macOS). JSON lines, absolute paths:
    * Python audit events: open (mode/flags), os.rename/os.replace, shutil.copyfile, os.link, os.symlink,
      os.listdir/os.scandir;
    * GDAL opens made from C: rasterio routes CPL_DEBUG `GDALOpen(<path>, this=…) succeeds as …` to the
      `rasterio._env` logger (captured here at DEBUG, not echoed); pyogrio prints the same line to stderr, which the
      runner parses. Requires CPL_DEBUG=ON in the step environment (the runner sets it)."""
    import logging
    import re
    fh = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - lives for the whole process
    pid = os.getpid()

    def emit(rec: dict) -> None:
        try:
            fh.write(json.dumps({"pid": pid, **rec}) + "\n")
        except (TypeError, ValueError, OSError):
            return

    def ab(p):
        if isinstance(p, int) or p is None:
            return None
        try:
            return os.path.abspath(os.fsdecode(p))
        except (TypeError, ValueError):
            return None

    def hook(event, a):
        if event == "open" and a:
            p = ab(a[0])
            if p:
                rec = {"event": "open", "path": p, "mode": a[1] if len(a) > 1 else None,
                       "flags": a[2] if len(a) > 2 and isinstance(a[2], int) else None}
                raw = a[0] if isinstance(a[0], (str, bytes)) else None
                # os.open(name, dir_fd=…) reports only the relative name; it cannot be resolved against cwd.
                # Relative names that do not exist under cwd are marked; ones that do exist are classified.
                if raw is not None and not os.path.isabs(os.fsdecode(raw)) and rec["mode"] is None \
                        and not os.path.exists(p):
                    rec["unresolved_dir_fd"] = True
                emit(rec)
        elif event in ("os.rename", "shutil.copyfile", "os.link", "os.symlink", "shutil.copytree") and len(a) >= 2:
            src, dst = ab(a[0]), ab(a[1])
            if src and dst:
                emit({"event": event, "path": src, "dst": dst})
        elif event in ("os.listdir", "os.scandir") and a:
            p = ab(a[0]) if a[0] is not None else os.getcwd()
            if p:
                emit({"event": event, "path": p})
    sys.addaudithook(hook)
    pat = re.compile(r"GDALOpen\((.+?), this=")

    class _GdalOpen(logging.Handler):
        def emit(self, record):
            try:
                m = pat.search(record.getMessage())
            except Exception:  # noqa: BLE001 - a malformed log record is not an open
                return
            if m:
                emit({"event": "gdal.open", "raw": m.group(1), "cwd": os.getcwd()})
    lg = logging.getLogger("rasterio._env")
    lg.setLevel(logging.DEBUG)
    lg.addHandler(_GdalOpen(level=logging.DEBUG))
    lg.propagate = False


def run_boot(args) -> int:
    if getattr(args, "audit_ledger", None):
        install_audit_ledger(args.audit_ledger)
    started = time.time()
    t0 = time.monotonic()
    import logging
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    expect = json.loads(Path(args.expect).read_text(encoding="utf-8")) if args.expect else None
    ctx_doc = json.loads(Path(args.ctx).read_text(encoding="utf-8"))
    result: dict = {"schema": "axis-migrate.step-result.v1", "step_id": args.step, "ok": False, "exit_code": None,
                    "reason_codes": [], "exception": None, "outputs": [], "stub_check": None, "proj": None,
                    "boot_version": BOOT_VERSION, "python": platform.python_version(), "executable": sys.executable,
                    "python_flags": {"hash_randomization": sys.flags.hash_randomization,
                                     "dont_write_bytecode": bool(sys.flags.dont_write_bytecode),
                                     "ignore_environment": bool(sys.flags.ignore_environment),
                                     "no_user_site": bool(sys.flags.no_user_site)}}

    def finish(code: int) -> int:
        result["exit_code"] = code
        result["ok"] = code == EXIT_OK and not result["reason_codes"]
        result["duration_s"] = round(time.monotonic() - t0, 3)
        _write_result(args.result, result)
        return code

    if args.step != ctx_doc.get("step_id"):
        result["reason_codes"].append("CTX_STEP_MISMATCH")
        return finish(EXIT_OUTPUT)
    # 1. PROJ network off
    net = proj_network_off()
    result["proj"] = net
    if net["network_enabled"]:
        result["reason_codes"].append("PROJ_NETWORK_ON")
        return finish(EXIT_STUB)
    # 2. pre-step stub check
    pre = stub_check(expect)
    result["stub_check"] = {"pre": {k: pre[k] for k in ("passed", "canary", "problems", "meta_path")},
                            "modules": pre["modules"]}
    if not pre["passed"]:
        result["reason_codes"].append("STUB_DETECTED")
        return finish(EXIT_STUB)
    baseline = {"_ids": _module_ids()}
    # 3. import the package from the snapshot only
    snap = os.path.realpath(args.snapshot)
    sys.path.insert(0, snap)
    pkg_dir = os.path.join(snap, "axis_pipeline")
    code = EXIT_OK
    try:
        import importlib
        rt = importlib.import_module("axis_pipeline._axis_runtime")
        rt_origin = os.path.realpath(rt.__spec__.origin)
        if not _under_any(rt_origin, [pkg_dir]) or os.path.normcase(rt_origin) == os.path.normcase(pkg_dir):
            result["reason_codes"].append("RUNTIME_NOT_FROM_SNAPSHOT")
            return finish(EXIT_OUTPUT)
        ctx = rt.Ctx.from_ctx_json(args.ctx)
        rt.run_step(ctx)
    except BaseException as exc:  # noqa: B902 - SystemExit/KeyboardInterrupt are step failures too (FH-02)
        traceback.print_exc(file=sys.stderr)
        rt_mod = sys.modules.get("axis_pipeline._axis_runtime")
        if rt_mod is not None:
            code = rt_mod.exit_code_for(exc)
            reason = rt_mod.reason_for(exc)
        else:
            code, reason = EXIT_EXC, ("STEP_CALLED_EXIT" if isinstance(exc, SystemExit) else "STEP_EXCEPTION")
        result["reason_codes"].append(reason)
        result["exception"] = {"type": type(exc).__name__, "message": str(exc)[:4000],
                               **_frames(exc.__traceback__, pkg_dir)}
    # 4. post-step stub check (always, even after a failure)
    post = stub_check(expect, baseline=baseline)
    result["stub_check"]["post"] = {k: post[k] for k in ("passed", "canary", "problems", "meta_path")}
    result["stub_check"]["passed"] = pre["passed"] and post["passed"]
    if not post["passed"]:
        result["reason_codes"].insert(0, "STUB_DETECTED")
        return finish(EXIT_STUB)
    # 5. verify declared outputs (only meaningful after a clean return)
    if code == EXIT_OK:
        outs, reasons = verify_outputs(ctx_doc, started)
        result["outputs"] = outs
        if reasons:
            result["reason_codes"].extend(reasons)
            code = EXIT_OUTPUT
    return finish(code)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="axis-migrate step boot wrapper (runs inside the step process).")
    ap.add_argument("--probe", action="store_true", help="print an environment probe as JSON and exit")
    ap.add_argument("--crs", action="append", default=[], help="probe: CRS to pair for PROJ operation checks")
    ap.add_argument("--step")
    ap.add_argument("--ctx")
    ap.add_argument("--snapshot", help="run snapshot dir holding axis_pipeline/")
    ap.add_argument("--result", help="where to write result.json")
    ap.add_argument("--expect", help="expected environment (site-packages, lock versions)")
    ap.add_argument("--audit-ledger", help="fallback ledger file (Python audit hook) when strace is unavailable")
    args = ap.parse_args(argv)
    if args.probe:
        sys.stdout.write(json.dumps(probe(args.crs), sort_keys=True, default=str) + "\n")
        return 0
    for need in ("step", "ctx", "snapshot", "result"):
        if not getattr(args, need):
            ap.error(f"--{need} is required")
    return run_boot(args)


if __name__ == "__main__":
    sys.exit(main())
