"""GeoLibre map views on real lux runs (dev-only; encodes lux facts).

    ~/axis-migrate-dev/axis-migrate/bin/axpy -m pytest ~/axis-migrate-dev/devtests/map -q

Two chained runs of the golden pipeline on fresh fixture copies under tmp (the fixture itself is never touched):
  clean    the golden pipeline as is → compare.py → views.py → check.py → seal.py → views.py again (amend path)
  shifted  the golden pipeline with s02's residential mask shifted one cell east before it is written
           (e2e item 10, numeric half): the validity layer of ds.02_res_mask must show exactly the stripe of
           1s (legacy only) and 2s (new only) that a one-cell shift implies.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ["GDAL_PAM_ENABLED"] = "NO"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "axis-migrate"
MAP = PLUGIN / "skills" / "map"
sys.path.insert(0, str(REPO / "devtests" / "run" / "lux_project"))
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402
import make_lux_project as mlp  # noqa: E402

AXPY = str(PLUGIN / "bin" / "axpy")
PY3 = shutil.which("python3")
GOLDEN = REPO / "evals" / "golden" / "lux" / "pipeline"
RASTERS = ["ds.01_pop_lux", "ds.02_res_mask", "ds.03_pop_res", "ds.04_factor", "ds.lux_res_exposure_2020"]
SHIFT_PATCH = ("    mask = rasterize(", "    mask = np.roll(mask, 1, axis=1); mask[:, 0] = nodata  # DEVTEST: one cell east\n")


def sh(args, ok=(0,), timeout=1200):
    cp = subprocess.run([str(a) for a in args], capture_output=True, text=True, timeout=timeout)
    assert cp.returncode in ok, (args, cp.stdout[-3000:], cp.stderr[-3000:])
    return cp


def _unlock(root: Path):
    for dirpath, _, files in os.walk(root):
        os.chmod(dirpath, 0o755)
        for f in files:
            p = Path(dirpath) / f
            if not p.is_symlink():
                os.chmod(p, stat.S_IMODE(p.stat().st_mode) | 0o200)


def chained_run(dest: Path, pipeline: Path | None = None) -> Path:
    mig = mlp.assemble(dest, pipeline)
    cp = sh([AXPY, PLUGIN / "skills" / "run" / "run_local.py", mig, "--json"], ok=(0, 10))
    summary = json.loads(cp.stdout.strip().splitlines()[-1])
    run = Path(summary["run_dir"])
    sh([AXPY, PLUGIN / "skills" / "verify" / "compare.py", "run", "--run", run], ok=(0, 1, 2))
    assert list((run / "verify" / "compare").glob("ds.*.json"))
    return run


@pytest.fixture(scope="module")
def fixture_hash():
    return ac.tree_hash(mlp.FIXTURE)


@pytest.fixture(scope="module")
def clean(tmp_path_factory, fixture_hash):
    root = tmp_path_factory.mktemp("luxmap") / "proj"
    run = chained_run(root)
    sh([PY3, MAP / "views.py", "run", "--run", run])
    yield run
    _unlock(root)
    assert ac.tree_hash(mlp.FIXTURE) == fixture_hash


@pytest.fixture(scope="module")
def shifted(tmp_path_factory, fixture_hash):
    root = tmp_path_factory.mktemp("luxshift")
    pipe = root / "pipeline"
    shutil.copytree(GOLDEN, pipe, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    s02 = pipe / "s02_landuse_mask.py"
    txt = s02.read_text()
    head, marker = SHIFT_PATCH
    i = txt.index("    ctx.write_raster(\"ds.02_res_mask\"")
    s02.write_text(txt[:i] + marker + txt[i:])
    assert head in txt
    run = chained_run(root / "proj", pipe)
    sh([PY3, MAP / "views.py", "run", "--run", run, "--dataset", "ds.02_res_mask"])
    yield run
    _unlock(root)
    assert ac.tree_hash(mlp.FIXTURE) == fixture_hash


def doc(run: Path) -> dict:
    p = run / "verify" / "views" / "views.json"
    return json.loads(p.read_text())


def test_every_compared_dataset_has_views(clean):
    d = doc(clean)
    compared = {}
    for p in sorted((clean / "verify" / "compare").glob("ds.*.json")):
        r = json.loads(p.read_text())
        compared[r["dataset_id"]] = r["family"]
    want = {k for k, f in compared.items() if f in ("raster", "vector")}
    got = {e["dataset"]: e for e in d["datasets"]}
    assert set(got) == want and set(RASTERS) <= want
    for ds, e in got.items():
        names = {"raster": {"overview", "worst", "edges", "nodata"}, "vector": {"overview", "worst"}}[e["family"]]
        assert set(e["views"]) == names, ds
    assert {s["dataset"] for s in d["skipped"]} == {k for k, f in compared.items() if f == "table"}


def test_projects_validate_and_cells_match_sources(clean):
    import rasterio
    from rasterio.windows import Window
    py = subprocess.run([PY3, "-I", MAP / "mapenv.py"], capture_output=True, text=True).stdout.strip()
    code = ("import sys, json, pathlib; sys.path.insert(0, %r); import views; root = pathlib.Path(sys.argv[1]); "
            "print(json.dumps({str(p): views.validate_project(p) for p in sorted(root.rglob('*.geolibre.json'))}))"
            % str(MAP))
    res = json.loads(sh([py, "-c", code, clean / "verify" / "views"]).stdout)
    assert res and all(v == [] for v in res.values()), {k: v for k, v in res.items() if v}
    legacy_root = clean.parents[2] / mlp.LEGACY_NAME
    for e in doc(clean)["datasets"]:
        if e["family"] != "raster":
            continue
        src = {side: clean.parents[2] / e["sources"][side]["path"] for side in ("legacy", "new")}
        assert src["legacy"].is_relative_to(legacy_root)
        assert hashlib.sha256(src["legacy"].read_bytes()).hexdigest() == e["sources"]["legacy"]["sha256"]
        cells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
        assert "interior" in cells, e["dataset"]
        for c in cells.values():
            for side, rk, ck in (("legacy", "row", "col"), ("new", "new_row", "new_col")):
                with rasterio.open(src[side]) as s, rasterio.open(clean / e["files"][side]["file"]) as g:
                    w = Window(c[ck], c[rk], 1, 1)
                    sv, gv = s.read(1, window=w)[0, 0], g.read(1, window=w)[0, 0]
                    assert sv == gv or (np.isnan(sv) and np.isnan(gv))
                    valid = bool(s.read_masks(1, window=w)[0, 0]) and np.isfinite(sv)
                    assert c[f"{side}_value"] == (float(sv) if valid else None), (e["dataset"], c["id"], side)


def test_check_script_headless(clean):
    cp = sh([PY3, MAP / "check.py", "--run", clean, "--json"])
    res = json.loads(cp.stdout)
    assert res["ok"] and res["problems"] == [] and res["projects"] >= 4 * len(RASTERS)


def test_identical_rasters_show_no_stripes(clean):
    for e in doc(clean)["datasets"]:
        if e["family"] == "raster" and e["verdict"] == "PASS_EXACT":
            assert e["validity_counts"]["1"] == e["validity_counts"]["2"] == 0, e["dataset"]


def test_one_cell_shift_is_a_validity_stripe(shifted):
    """e2e item 10 (numeric half): new = legacy shifted one cell east ⇒ validity 1 exactly where legacy is valid
    and its western neighbour is not, 2 exactly where the western neighbour is valid and the cell is not."""
    import rasterio
    e = next(x for x in doc(shifted)["datasets"] if x["dataset"] == "ds.02_res_mask")
    assert e["grid"]["aligned"] is True and e["verdict"] != "PASS_EXACT"
    with rasterio.open(shifted / e["files"]["validity"]["file"]) as ds:
        v = ds.read(1)
    with rasterio.open(shifted / e["files"]["legacy"]["file"]) as ds:
        lv = ds.read(1) == 1
    west = np.zeros_like(lv)
    west[:, 1:] = lv[:, :-1]
    assert ((v == 1) == (lv & ~west)).all()
    assert ((v == 2) == (west & ~lv)).all()
    assert ((v == 3) == (lv & west)).all()
    n1, n2 = int((v == 1).sum()), int((v == 2).sum())
    assert n1 > 100 and n2 > 100 and e["validity_counts"]["1"] == n1 and e["validity_counts"]["2"] == n2
    cells = {c["id"]: c for ve in e["views"].values() for c in ve["cells_to_identify"]}
    assert cells["edge"]["validity"] in (1, 2)
    assert e["views"]["edges"]["camera"]["source"] == "densest one-side-only cells"


def test_seal_then_views_go_to_amend(clean):
    """seal.py accepts a run with views; views.py after seal writes amend/views and leaves verify/ sealed."""
    mig = clean.parents[1]
    before = doc(clean)
    sh([AXPY, PLUGIN / "skills" / "report" / "seal.py", mig, "--run", clean.name])
    assert not os.access(clean / "verify", os.W_OK)
    sh([PY3, MAP / "views.py", "run", "--run", clean, "--dataset", "ds.lux_res_exposure_2020"])
    a = json.loads((clean / "amend" / "views" / "views.json").read_text())
    assert a["sealed_run"] is True and [e["dataset"] for e in a["datasets"]] == ["ds.lux_res_exposure_2020"]
    old = next(e for e in before["datasets"] if e["dataset"] == "ds.lux_res_exposure_2020")
    new = a["datasets"][0]
    assert new["files"]["diff"]["sha256"] == old["files"]["diff"]["sha256"]     # same layers, other folder
    assert new["views"]["worst"]["url_path"].startswith(f"/data/runs/{clean.name}/amend/views/")
    cp = sh([PY3, MAP / "check.py", "--run", clean, "--json"])                   # amend index is preferred
    assert json.loads(cp.stdout)["ok"]


def test_report_sizes(clean, capsys):
    root = clean / "verify" / "views"
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    per = {e["dataset"]: sum(f["bytes"] for f in e["files"].values()) for e in doc(clean)["datasets"]}
    with capsys.disabled():
        print(f"\nlux views: {total / 1e6:.2f} MB total; per dataset (layer files): "
              + ", ".join(f"{k} {v / 1e6:.2f} MB" for k, v in sorted(per.items())))
    assert total < 200e6
