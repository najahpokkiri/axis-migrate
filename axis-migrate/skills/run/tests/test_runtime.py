"""Unit tests of the vendored step runtime (CONTRACTS §5): Ctx API, exceptions, wheel mode, staging, CLI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import rasterio

import runlib as rl
import synth

RT = rl.RT
RUNTIME_DIR = rl.RUNTIME_SRC


@pytest.fixture
def pkg(tmp_path) -> Path:
    """An importable axis_pipeline package holding the synthetic steps (as the runner's snapshot does)."""
    root = tmp_path / "snap"
    p = root / "axis_pipeline"
    p.mkdir(parents=True)
    for f in ("_axis_runtime.py", "__main__.py", "__init__.py"):
        shutil.copyfile(RUNTIME_DIR / f, p / f)
    (p / "s01_clip_population.py").write_text(synth.S01)
    (p / "s02_zonal_sum.py").write_text(synth.S02)
    (p / "params.yaml").write_text(synth.PARAMS_YAML)
    return root


def _ctx(tmp_path, template, *, outputs=None, params=("p.clip_nodata",), may_be_empty=False, no_seek=()):
    legacy = template / synth.LEGACY
    out = tmp_path / "out"
    outputs = outputs or {"ds.01_pop_clip": {"path": str(out / "01_pop_clip.tif"), "kind": "raster",
                                             "format": "GTiff", "layer": None, "may_be_empty": may_be_empty}}
    params_file = tmp_path / "params.yaml"
    params_file.write_text(synth.PARAMS_YAML + "  - id: p.open\n    node: x\n    value: null\n"
                                               "    resolution: unresolved\n")
    return RT.Ctx(step_id="s01_clip_population",
                  inputs={"ds.pop": {"path": str(legacy / "03_INPUT/pop.tif"), "kind": "raster", "format": "GTiff",
                                     "layer": None, "source": "input"},
                          "ds.zones": {"path": str(legacy / "03_INPUT/zones.gpkg"), "kind": "vector",
                                       "format": "GPKG", "layer": "zones", "source": "input"}},
                  outputs=outputs, params_file=str(params_file), declared_params=list(params) + ["p.open"],
                  scratch_dir=str(tmp_path / "scratch"), io_log=str(tmp_path / "io.json"), no_seek_roots=no_seek)


def test_params_have_no_default_and_no_get(tmp_path, template):
    ctx = _ctx(tmp_path, template)
    assert ctx.param("p.clip_nodata") == -9999.0
    assert ctx.params["p.clip_nodata"] == -9999.0 and "p.clip_nodata" in ctx.params
    with pytest.raises(RT.ParamMissing) as e:
        ctx.param("p.min_value")          # in params.yaml but not declared for this step
    assert e.value.exit_code == 2 and e.value.reason_code == "PARAM_MISSING:p.min_value"
    with pytest.raises(RT.ParamMissing):
        ctx.param("p.open")               # unresolved → an open question, never a guess
    with pytest.raises(AttributeError):
        ctx.params.get("p.clip_nodata", 0)
    with pytest.raises(TypeError):
        ctx.param("p.clip_nodata", -1)    # no default argument at all


def test_undeclared_datasets_and_exit_codes(tmp_path, template):
    ctx = _ctx(tmp_path, template)
    with pytest.raises(RT.DatasetNotDeclared) as e:
        ctx.input_path("ds.other")
    assert RT.exit_code_for(e.value) == 2
    with pytest.raises(RT.DatasetNotDeclared) as e:
        ctx.output_path("ds.other")
    assert RT.exit_code_for(e.value) == 3
    with pytest.raises(RT.DatasetNotDeclared):
        ctx.input_path("ds.01_pop_clip")  # its own output is not an input
    assert RT.exit_code_for(SystemExit(0)) == 1 and RT.reason_for(SystemExit(0)) == "STEP_CALLED_EXIT"


def test_input_missing_never_fabricates(tmp_path, template):
    ctx = _ctx(tmp_path, template)
    ctx._inputs["ds.pop"]["path"] = str(tmp_path / "nope.tif")
    with pytest.raises(RT.InputMissing) as e:
        ctx.read_raster("ds.pop")
    assert e.value.exit_code == 2 and e.value.reason_code == "INPUT_MISSING:ds.pop"


def test_write_raster_requires_grid_nodata_dtype_and_checks_them(tmp_path, template):
    ctx = _ctx(tmp_path, template)
    grid = ctx.grid_of("ds.pop")
    arr = np.ones(grid.shape, dtype="float32")
    with pytest.raises(RT.OutputContractError, match="grid="):
        ctx.write_raster("ds.01_pop_clip", arr, dtype="float32", nodata=-1)
    with pytest.raises(RT.OutputContractError, match="nodata"):
        ctx.write_raster("ds.01_pop_clip", arr, grid=grid, dtype="float32")
    with pytest.raises(RT.OutputContractError) as e:
        ctx.write_raster("ds.01_pop_clip", np.ones((9, 10), "float32"), grid=grid, dtype="float32", nodata=-1)
    assert e.value.reason == "SHAPE_MISMATCH"
    with pytest.raises(RT.OutputContractError) as e:
        ctx.write_raster("ds.01_pop_clip", arr.astype("float64"), grid=grid, dtype="float32", nodata=-1)
    assert e.value.reason == "DTYPE_MISMATCH"
    with pytest.raises(RT.OutputContractError) as e:
        ctx.write_raster("ds.01_pop_clip", arr.astype("uint8"), grid=grid, dtype="uint8", nodata=-9999)
    assert e.value.reason == "NODATA_DTYPE_MISMATCH"
    with pytest.raises(RT.OutputContractError) as e:
        ctx.write_raster("ds.01_pop_clip", np.full(grid.shape, -1, "float32"), grid=grid, dtype="float32", nodata=-1)
    assert e.value.reason == "OUTPUT_EMPTY"
    # nodata=None is allowed when passed explicitly; uint8 → float32 is a safe widening
    ctx.write_raster("ds.01_pop_clip", arr.astype("uint8"), grid=grid, dtype="float32", nodata=None)
    masked = np.ma.masked_array(arr, mask=np.eye(10, dtype=bool))
    ctx.write_raster("ds.01_pop_clip", masked, grid=grid, dtype="float32", nodata=-9999.0, compress="lzw")
    records = ctx._commit()
    out = Path(records[0]["path"])
    with rasterio.open(out) as src:
        a = src.read(1)
        assert src.nodata == -9999.0 and src.profile["compress"] == "lzw" and src.transform == grid.transform
    assert (a[np.eye(10, dtype=bool)] == -9999.0).all()
    st = records[0]["stats"]
    assert st["count"] == 90 and st["nodata_count"] == 10 and st["sum"] == 90.0


def test_may_be_empty_allows_an_all_nodata_raster(tmp_path, template):
    ctx = _ctx(tmp_path, template, may_be_empty=True)
    grid = ctx.grid_of("ds.pop")
    ctx.write_raster("ds.01_pop_clip", np.full(grid.shape, -1, "float32"), grid=grid, dtype="float32", nodata=-1)
    ctx._commit()


def test_commit_refuses_missing_output_and_records_io(tmp_path, template):
    ctx = _ctx(tmp_path, template)
    ctx.param("p.clip_nodata")
    ctx.read_raster("ds.pop")
    with pytest.raises(RT.OutputContractError) as e:
        ctx._commit()
    assert e.value.reason_code == "OUTPUT_MISSING:ds.01_pop_clip"
    ctx._write_io_log("failed", "x")
    io = json.loads((tmp_path / "io.json").read_text())
    assert io["params_read"] == {"p.clip_nodata": -9999.0}
    assert io["reads"][0]["dataset"] == "ds.pop" and len(io["reads"][0]["sha256"]) == 64


def test_vector_and_table_writers_refuse_empty(tmp_path, template):
    out = tmp_path / "o"
    outputs = {"ds.v": {"path": str(out / "v.gpkg"), "kind": "vector", "format": "GPKG", "layer": "v"},
               "ds.c": {"path": str(out / "c.csv"), "kind": "table", "format": "CSV", "layer": None}}
    ctx = _ctx(tmp_path, template, outputs=outputs)
    z = ctx.read_vector("ds.zones")
    with pytest.raises(RT.OutputContractError):
        ctx.write_vector("ds.v", z.iloc[0:0])
    with pytest.raises(RT.OutputContractError):
        ctx.write_csv("ds.c", ["a"], [])
    ctx.write_vector("ds.v", z)
    ctx.write_csv("ds.c", ["name", "n"], [["a", 1.5]])
    recs = {r["dataset"]: r for r in ctx._commit()}
    assert recs["ds.v"]["stats"]["count"] == 2 and recs["ds.c"]["stats"]["count"] == 1
    assert (out / "c.csv").read_bytes() == b"name,n\r\na,1.5\r\n"
    import pyogrio
    assert [lyr for lyr, _ in pyogrio.list_layers(out / "v.gpkg")] == ["v"]


def test_no_seek_root_stages_gpkg_reads_and_copies_writes(tmp_path, template):
    vol = tmp_path / "Volumes"
    shutil.copytree(template / synth.LEGACY, vol / "in")
    outputs = {"ds.01_pop_clip": {"path": str(vol / "out" / "01_pop_clip.tif"), "kind": "raster",
                                  "format": "GTiff", "layer": None}}
    ctx = _ctx(tmp_path, template, outputs=outputs, no_seek=[str(vol)])
    ctx._inputs["ds.zones"]["path"] = str(vol / "in" / "03_INPUT/zones.gpkg")
    p = ctx.input_path("ds.zones")
    assert str(p).startswith(str(tmp_path / "scratch")) and p.name == "zones.gpkg"
    assert ctx.output_path("ds.01_pop_clip").is_relative_to(tmp_path / "scratch")
    grid = ctx.grid_of("ds.pop")
    ctx.write_raster("ds.01_pop_clip", np.ones(grid.shape, "float32"), grid=grid, dtype="float32", nodata=-1.0)
    ctx._commit()
    assert (vol / "out" / "01_pop_clip.tif").is_file()


def test_run_step_checks_module_contract(tmp_path, template, pkg, monkeypatch):
    monkeypatch.syspath_prepend(str(pkg))
    for m in [m for m in sys.modules if m == "axis_pipeline" or m.startswith("axis_pipeline.")]:
        monkeypatch.delitem(sys.modules, m)
    import importlib
    rt = importlib.import_module("axis_pipeline._axis_runtime")
    ctx = rt.Ctx(step_id="s01_clip_population", inputs={}, outputs={}, params_file=None, declared_params=[],
                 scratch_dir=str(tmp_path / "s"))
    with pytest.raises(rt.OutputContractError) as e:
        rt.run_step(ctx)
    assert e.value.reason == "MODULE_CONTRACT_MISMATCH"


def test_datasets_manifest_and_wheel_cli(tmp_path, template, pkg):
    """`python -m axis_pipeline --step … --input-root … --work-root … --output-root …` (D9-6) end to end."""
    graph = synth.graph_doc()
    dsj = RT.datasets_manifest_from_graph(graph)
    assert dsj["datasets"]["ds.pop"] == {"root": "input", "relpath": "03_INPUT/pop.tif", "kind": "raster",
                                         "format": "GTiff", "layer": None, "may_be_empty": False}
    assert dsj["datasets"]["ds.01_pop_clip"]["root"] == "work"
    assert dsj["datasets"]["ds.zone_stats"] == {"root": "output", "relpath": "s02_zonal_sum/zone_stats.gpkg",
                                                "kind": "vector", "format": "GPKG", "layer": "zone_stats",
                                                "may_be_empty": False}
    (pkg / "axis_pipeline" / "_datasets.json").write_text(json.dumps(dsj))
    vol = tmp_path / "Volumes" / "c" / "s" / "v"
    shutil.copytree(template / synth.LEGACY / "03_INPUT", vol / "inputs" / "03_INPUT")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONPATH": str(pkg), "PYTHONHASHSEED": "0"}
    for step in ("s01_clip_population", "s02_zonal_sum"):
        cp = subprocess.run([str(rl.VENV_PY), "-m", "axis_pipeline", "--step", step,
                             "--input-root", str(vol / "inputs"), "--work-root", str(vol / "work"),
                             "--output-root", str(vol / "outputs"), "--scratch-dir", str(tmp_path / "local"),
                             "--no-seek-root", str(tmp_path / "Volumes")],
                            capture_output=True, text=True, env=env, cwd=str(tmp_path))
        assert cp.returncode == 0, cp.stderr
    assert (vol / "work" / "s01_clip_population" / "01_pop_clip.tif").is_file()
    assert (vol / "outputs" / "s02_zonal_sum" / "zone_sum.csv").read_text().startswith("name,pop_sum")
    io = json.loads((tmp_path / "local" / "s02_zonal_sum.io.json").read_text())
    assert io["mode"] == "wheel" and io["status"] == "ok" and io["params_read"] == {"p.min_value": 0.5}
    staged = [r for r in io["reads"] if r["dataset"] == "ds.zones"][0]
    assert staged.get("staged_local") is True
    # exit codes: unknown step → 3, missing input → 2
    cp = subprocess.run([str(rl.VENV_PY), "-m", "axis_pipeline", "--step", "s01_clip_population",
                         "--input-root", str(tmp_path / "empty"), "--work-root", str(vol / "w2"),
                         "--output-root", str(vol / "o2"), "--scratch-dir", str(tmp_path / "l2")],
                        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert cp.returncode == 2 and "INPUT_MISSING" in cp.stderr
    cp = subprocess.run([str(rl.VENV_PY), "-m", "axis_pipeline", "--step", "s99_nope",
                         "--input-root", str(vol / "inputs"), "--work-root", str(vol / "w3"),
                         "--output-root", str(vol / "o3"), "--scratch-dir", str(tmp_path / "l3")],
                        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert cp.returncode == 3


def test_runtime_imports_only_stdlib_at_import_time():
    import ast
    tree = ast.parse((RUNTIME_DIR / "_axis_runtime.py").read_text())
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            top.add(node.module.split(".")[0])
    assert top <= set(sys.stdlib_module_names) | {"__future__"}
    assert "__file__" not in (RUNTIME_DIR / "_axis_runtime.py").read_text()
