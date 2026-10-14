#!/usr/bin/env python3
"""check.py — headless check of a run's map views (no browser; makes no claim about what a browser shows).

    python3 check.py --run <run_dir> [--json] [--keep-server]

Starts the map server for the run's migration (or reuses the running one), then for every view and cell project in
views.json: fetches the project over HTTP and checks its sha256 equals `project_sha256` (the bytes served are the
bytes indexed), reloads it through geolibre and validates it (views.validate_project), and for every layer file:
COGs are fetched with a Range header (must be 206, Content-Range total = file size, bytes = the file's bytes) and
must carry GDAL's LAYOUT=COG; GeoJSON is fetched whole and parsed; every layer sha256 must match views.json.
Stops the server again if it started it. Exit: 0 all checks pass · 2 a check failed or setup error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True     # never write __pycache__ into the plugin folder
os.environ["GDAL_PAM_ENABLED"] = "NO"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import views as V  # noqa: E402

RANGE_LEN = 16384


def _get(opener, url: str, rng: str | None = None):
    req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
    with opener.open(req, timeout=30) as r:
        return r.status, dict(r.headers), r.read()


def check_run(run_dir: Path, keep: bool = False) -> dict:
    import rasterio
    import start as S
    import stop as T
    run_dir = Path(run_dir).resolve()
    idx = V.find_index(run_dir)
    if idx is None:
        raise V.SetupError("no views.json: run `views.py run --run <dir>` first")
    doc = json.loads(idx.read_text())
    mig = V.migration_of(run_dir)
    was_running = S.running(mig) is not None
    st = S.start(mig)
    base = st["url_base"]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    problems, n_proj, n_files = [], 0, 0
    seen_files: set[str] = set()
    try:
        for e in doc.get("datasets") or []:
            ds = e["dataset"]
            projects = [(f"{ds}/{v}", ve) for v, ve in sorted(e["views"].items())]
            cells = {}
            for ve in e["views"].values():
                for c in ve.get("cells_to_identify") or []:
                    if c.get("url_path"):
                        cells[c["id"]] = c
            projects += [(f"{ds}/cell-{k}", c) for k, c in sorted(cells.items())]
            layer_urls = {lay["url_path"] for ve in e["views"].values() for lay in ve["layers"]}
            for name, ve in projects:
                n_proj += 1
                try:
                    code, _, body = _get(opener, base + ve["url_path"])
                except Exception as ex:
                    problems.append(f"{name}: project fetch failed: {ex}")
                    continue
                if code != 200 or hashlib.sha256(body).hexdigest() != ve["project_sha256"]:
                    problems.append(f"{name}: served project (HTTP {code}) does not match project_sha256")
                local = run_dir / ve["project"]
                problems += [f"{name}: {p}" for p in V.validate_project(local, expect_urls=layer_urls)]
            for ve in e["views"].values():
                for lay in ve["layers"]:
                    if lay["url_path"] in seen_files:
                        continue
                    seen_files.add(lay["url_path"])
                    n_files += 1
                    f = run_dir / lay["file"]
                    if not f.is_file() or V.ac.sha256_file(f) != lay["sha256"]:
                        problems.append(f"{ds}: {lay['file']} missing or changed since views.json")
                        continue
                    data = f.read_bytes()
                    try:
                        if lay["kind"] == "cog":
                            n = min(RANGE_LEN, len(data))
                            code, hdr, body = _get(opener, base + lay["url_path"], f"bytes=0-{n - 1}")
                            cr = {k.lower(): v for k, v in hdr.items()}.get("content-range", "")
                            if code != 206 or body != data[:n] or cr != f"bytes 0-{n - 1}/{len(data)}":
                                problems.append(f"{ds}: {lay['file']}: Range fetch gave {code} {cr!r}")
                            tail = max(0, len(data) - 1000)
                            code, _, body = _get(opener, base + lay["url_path"], f"bytes={tail}-")
                            if code != 206 or body != data[tail:]:
                                problems.append(f"{ds}: {lay['file']}: open-ended Range fetch gave {code}")
                            with rasterio.open(f) as r:
                                if r.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG":
                                    problems.append(f"{ds}: {lay['file']} is not a cloud-optimised GeoTIFF")
                        else:
                            code, _, body = _get(opener, base + lay["url_path"])
                            gj = json.loads(body)
                            if code != 200 or body != data or gj.get("type") != "FeatureCollection":
                                problems.append(f"{ds}: {lay['file']}: GeoJSON fetch gave {code}")
                    except Exception as ex:
                        problems.append(f"{ds}: {lay['file']}: {type(ex).__name__}: {ex}")
    finally:
        if not was_running and not keep:
            T.stop(mig)
    return {"run_id": doc.get("run_id"), "datasets": len(doc.get("datasets") or []), "projects": n_proj,
            "layer_files": n_files, "problems": problems, "ok": not problems and n_proj > 0}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Headless check of a run's GeoLibre map views (no browser claims).")
    ap.add_argument("--run", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--keep-server", action="store_true", help="leave a server this check started running")
    a = ap.parse_args(argv)
    V.ensure_map_env(argv, __file__)
    try:
        res = check_run(Path(a.run), keep=a.keep_server)
    except V.SetupError as e:
        print(f"check.py: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"check.py: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(res, sort_keys=True, indent=1))
    else:
        print(f"{res['datasets']} datasets · {res['projects']} projects · {res['layer_files']} layer files served "
              f"and checked: {'OK' if res['ok'] else 'PROBLEMS'}")
        for p in res["problems"]:
            print(f"  - {p}")
        print("headless only: what the map shows in a browser is not checked here")
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
