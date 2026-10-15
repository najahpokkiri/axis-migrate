#!/usr/bin/env python3
"""check.py — headless check of a run's map views (no browser; makes no claim about what a browser shows).

    python3 check.py --run <run_dir> [--json] [--keep-server]
    python3 check.py --inputs <migration> [--json] [--keep-server]     (discover's inputs views)
    … --render      also open views in headless Chrome (render_check.py): readiness line, raster engine
                    maplibre-gl-raster, cells drawn as squares; no Chrome = a problem saying so

Starts the map server for the run's migration (or reuses the running one), then for every view and cell project in
views.json: fetches the project over HTTP and checks its sha256 equals `project_sha256` (the bytes served are the
bytes indexed), reloads it through geolibre and validates it (views.validate_project), and for every layer file:
COGs are fetched with a Range header (must be 206, Content-Range total = file size, bytes = the file's bytes) and
must carry GDAL's LAYOUT=COG; GeoJSON is fetched whole and parsed; every layer sha256 must match views.json.
Stops the server again if it started it, unless --keep-server (then the views can be opened right away; stop.py
ends it). Exit: 0 all checks pass · 2 a check failed or setup error.
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


def _check_items(opener, base: str, base_dir: Path, items: list, problems: list, seen_files: set) -> tuple[int, int]:
    """items: [(label, [(name, project entry)], [layer rows], {layer url_path})]. Checks every project (served bytes
    = project_sha256, valid through geolibre) and every layer file once (sha256, Range for COGs, GeoJSON parse)."""
    import rasterio
    n_proj = n_files = 0
    for ds, projects, layers, layer_urls in items:
        for name, ve in projects:
            n_proj += 1
            try:
                code, _, body = _get(opener, base + ve["url_path"])
            except Exception as ex:
                problems.append(f"{name}: project fetch failed: {ex}")
                continue
            if code != 200 or hashlib.sha256(body).hexdigest() != ve["project_sha256"]:
                problems.append(f"{name}: served project (HTTP {code}) does not match project_sha256")
            local = base_dir / ve["project"]
            problems += [f"{name}: {p}" for p in V.validate_project(local, expect_urls=layer_urls)]
        for lay in layers:
            if lay["url_path"] in seen_files:
                continue
            seen_files.add(lay["url_path"])
            n_files += 1
            f = base_dir / lay["file"]
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
    return n_proj, n_files


def _served(mig: Path, keep: bool, fn) -> dict:
    import start as S
    import stop as T
    was_running = S.running(mig) is not None
    st = S.start(mig)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        return fn(opener, st["url_base"])
    finally:
        if not was_running and not keep:
            T.stop(mig)


def _render(base: str, mig: Path, upaths: list[tuple[str, str]], problems: list) -> dict:
    """Open each page in headless Chrome (render_check.py): ready marker, engine maplibre-gl-raster, crisp cells."""
    import render_check as RC
    port = int(base.rsplit(":", 1)[1])
    try:
        res = RC.check_pages([V.page_url(port, u) for _, u in upaths], tmp_root=mig / ".axis" / "tmp")
    except RC.NoChrome as e:
        problems.append(f"render: not checked ({e})")
        return {"pages": 0}
    for (name, _), r in zip(upaths, res):
        if "problem" in r:
            problems.append(f"{name}: render: {r['problem']}")
    return {"pages": len(res), "results": [{"view": n, **{k: r.get(k) for k in ("ready", "seconds", "engine", "crisp",
                                                                                     "problem")}}
                                           for (n, _), r in zip(upaths, res)]}


def check_run(run_dir: Path, keep: bool = False, render: bool = False) -> dict:
    run_dir = Path(run_dir).resolve()
    idx = V.find_index(run_dir)
    if idx is None:
        raise V.SetupError("no views.json: run `views.py run --run <dir>` first")
    doc = json.loads(idx.read_text())
    items = []
    for e in doc.get("datasets") or []:
        ds = e["dataset"]
        projects = [(f"{ds}/{v}", ve) for v, ve in sorted(e["views"].items())]
        cells = {}
        for ve in e["views"].values():
            for c in ve.get("cells_to_identify") or []:
                if c.get("url_path"):
                    cells[c["id"]] = c
        projects += [(f"{ds}/cell-{k}", c) for k, c in sorted(cells.items())]
        layers = [lay for ve in e["views"].values() for lay in ve["layers"]]
        items.append((ds, projects, layers, {lay["url_path"] for lay in layers}))

    def go(opener, base):
        problems: list[str] = []
        n_proj, n_files = _check_items(opener, base, run_dir, items, problems, set())
        out = {"run_id": doc.get("run_id"), "datasets": len(doc.get("datasets") or []), "projects": n_proj,
               "layer_files": n_files}
        if render:
            pages = []
            for ds, p, _, _ in items:          # per dataset: its overview and its first cell project
                pages += [(n, ve["url_path"]) for n, ve in p if n.endswith("/overview")]
                pages += [(n, ve["url_path"]) for n, ve in p if "/cell-" in n][:1]
            out["render"] = _render(base, V.migration_of(run_dir), pages, problems)
        return {**out, "problems": problems, "ok": not problems and n_proj > 0}
    return _served(V.migration_of(run_dir), keep, go)


def check_inputs(migration: Path, keep: bool = False, render: bool = False) -> dict:
    """The same checks for the discover inputs views (<migration>/discover/views.json)."""
    mig = Path(migration).resolve()
    idx = V.inputs_index(mig)
    if idx is None:
        raise V.SetupError(f"no {V.INPUTS_INDEX}: run `views.py inputs <migration>` first")
    doc = json.loads(idx.read_text())
    items = []
    for name, pe in sorted((doc.get("projects") or {}).items()):
        items.append((name, [(name, pe)], pe["layers"], {lay["url_path"] for lay in pe["layers"]}))
    for e in doc.get("datasets") or []:
        data = {"name": e["dataset"], "kind": "cog" if e["family"] == "raster" else "vector",
                **{k: e["files"]["data"][k] for k in ("file", "url_path", "sha256")}}
        projects = [(f"{e['dataset']}/{kind}-{c['id']}", c)
                    for k, kind in (("cells_to_identify", "cell"), ("features_to_identify", "feature"))
                    for c in e.get(k) or []]
        items.append((e["dataset"], projects, [data], {data["url_path"]}))

    def go(opener, base):
        problems: list[str] = []
        n_proj, n_files = _check_items(opener, base, mig, items, problems, set())
        out = {"mode": "inputs", "datasets": len(doc.get("datasets") or []), "projects": n_proj,
               "layer_files": n_files}
        if render:
            pages = [(name, pe["url_path"]) for name, pe in sorted((doc.get("projects") or {}).items())]
            pages += [(f"{e['dataset']}/cell-{e['cells_to_identify'][0]['id']}",
                       e["cells_to_identify"][0]["url_path"]) for e in doc.get("datasets") or []
                      if e.get("cells_to_identify")]
            out["render"] = _render(base, mig, pages, problems)
        return {**out, "problems": problems, "ok": not problems and n_proj > 0}
    return _served(mig, keep, go)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(description="Headless check of GeoLibre map views (no browser claims).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", help="a compared run's views (verify/views.json or amend/views/views.json)")
    g.add_argument("--inputs", metavar="MIGRATION", help="discover's inputs views (<migration>/discover/views.json)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--render", action="store_true",
                    help="also open views and cell projects in headless Chrome: ready marker, GPU engine, crisp cells")
    ap.add_argument("--keep-server", action="store_true",
                    help="leave a server this check started running (open the views right after; stop.py ends it)")
    a = ap.parse_args(argv)
    V.ensure_map_env(argv, __file__)
    try:
        res = check_inputs(Path(a.inputs), keep=a.keep_server, render=a.render) if a.inputs else \
            check_run(Path(a.run), keep=a.keep_server, render=a.render)
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
        if res.get("render"):
            print(f"render: {res['render']['pages']} page(s) opened in headless Chrome (ready marker, engine, "
                  "crisp cells)")
        else:
            print("headless only: what the map shows in a browser is not checked here (--render opens pages in "
                  "headless Chrome)")
    return 0 if res["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
