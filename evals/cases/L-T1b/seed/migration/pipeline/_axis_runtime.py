"""axis_pipeline runtime: the step contract (CONTRACTS §5). Vendored byte-identical into every migration/pipeline/.

Step modules never import this file; they receive a `Ctx`. The runtime owns parameter access, dataset path
resolution, the staging I/O helper for seek-less roots (Databricks Volumes), grid/nodata/dtype checks on raster
writes, atomic commits of outputs, and the per-step I/O record (`io.json`).

Two ways to build a `Ctx`, one contract:
  * runner mode: `Ctx.from_ctx_json(path)` (the runner writes `runs/<id>/ctx/<step>.json`);
  * wheel mode:  `Ctx.from_roots(step_id, input_root=…, work_root=…, output_root=…, scratch_dir=…)` using the packaged
    `_datasets.json` and `params.yaml` (layout: inputs at R/<legacy relpath>, intermediates at W/<new.path>,
    finals at O/<new.path>).

Exit codes of a step process (DESIGN §3): 0 ok, 1 step exception, 2 input/parameter error, 3 output contract
violation (11 = stub detected, only raised by the runner's boot wrapper).

Only stdlib at import time; numpy/rasterio/geopandas/pyogrio/openpyxl/yaml are imported lazily where needed.
"""
from __future__ import annotations

import csv
import hashlib
import importlib
import json
import logging
import math
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator

RUNTIME_VERSION = "1.0.0"
CTX_SCHEMA = "axis-migrate.ctx.v1"
DATASETS_SCHEMA = "axis-migrate.datasets.v1"
IO_SCHEMA = "axis-migrate.io.v1"
PACKAGE = __package__ or "axis_pipeline"

# Determinism defaults (05 D5-12). The runner sets them in the step environment; the wheel entry point relies on
# these defaults so that a Databricks task writes the same bytes. `setdefault`: an explicit caller value wins.
DETERMINISM_ENV = {
    "OGR_CURRENT_DATE": "2000-01-01T00:00:00Z",
    "GDAL_PAM_ENABLED": "NO",
}

EXIT_OK = 0
EXIT_EXCEPTION = 1
EXIT_INPUT_PARAM = 2
EXIT_OUTPUT_CONTRACT = 3

RASTER_FORMATS = {".tif": "GTiff", ".tiff": "GTiff"}
VECTOR_FORMATS = {".gpkg": "GPKG", ".geojson": "GeoJSON", ".json": "GeoJSON", ".shp": "ESRI Shapefile",
                  ".fgb": "FlatGeobuf"}
TABLE_FORMATS = {".csv": "CSV", ".xlsx": "XLSX", ".xlsm": "XLSX", ".parquet": "Parquet"}
# Formats whose readers need random access / SQLite locking: staged to local scratch when under a no-seek root.
STAGE_READ_SUFFIXES = (".gpkg", ".sqlite", ".db", ".xlsx", ".xlsm", ".zip")


# --------------------------------------------------------------------------------------------------------------
# Exceptions (exact names: CONTRACTS §5)
# --------------------------------------------------------------------------------------------------------------
class AxisStepError(Exception):
    exit_code = EXIT_EXCEPTION
    reason = "STEP_EXCEPTION"

    def __init__(self, message: str, *, reason: str | None = None, subject: str | None = None):
        super().__init__(message)
        if reason:
            self.reason = reason
        self.subject = subject

    @property
    def reason_code(self) -> str:
        return f"{self.reason}:{self.subject}" if self.subject else self.reason


class ParamMissing(AxisStepError):
    """A parameter is undeclared for this step, absent from params.yaml, or unresolved. Exit 2 (interview)."""
    exit_code = EXIT_INPUT_PARAM
    reason = "PARAM_MISSING"


class InputMissing(AxisStepError):
    """A declared input does not exist. Never fabricate a substitute. Exit 2."""
    exit_code = EXIT_INPUT_PARAM
    reason = "INPUT_MISSING"


class OutputContractError(AxisStepError):
    """A write breaks the output contract (grid, nodata, dtype, empty, missing, undeclared). Exit 3."""
    exit_code = EXIT_OUTPUT_CONTRACT
    reason = "OUTPUT_CONTRACT"


class DatasetNotDeclared(AxisStepError):
    """A dataset id the step did not declare. Exit 3 for outputs, 2 for inputs."""
    reason = "DATASET_NOT_DECLARED"

    def __init__(self, message: str, *, direction: str, subject: str | None = None):
        super().__init__(message, subject=subject)
        self.direction = direction
        self.exit_code = EXIT_OUTPUT_CONTRACT if direction == "output" else EXIT_INPUT_PARAM


def exit_code_for(exc: BaseException) -> int:
    """Step-process exit code for an exception raised by a step (SystemExit included: a step never exits)."""
    if isinstance(exc, AxisStepError):
        return exc.exit_code
    return EXIT_EXCEPTION


def reason_for(exc: BaseException) -> str:
    if isinstance(exc, AxisStepError):
        return exc.reason_code
    if isinstance(exc, SystemExit):
        return "STEP_CALLED_EXIT"
    if isinstance(exc, KeyboardInterrupt):
        return "STEP_INTERRUPTED"
    return "STEP_EXCEPTION"


# --------------------------------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------------------------------
def _sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def infer_kind_format(path: str) -> tuple[str, str]:
    suf = Path(path).suffix.lower()
    if suf in RASTER_FORMATS:
        return "raster", RASTER_FORMATS[suf]
    if suf in VECTOR_FORMATS:
        return "vector", VECTOR_FORMATS[suf]
    if suf in TABLE_FORMATS:
        return "table", TABLE_FORMATS[suf]
    return "document", suf.lstrip(".").upper() or "UNKNOWN"


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _finite_or_none(x: Any) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _json_default(o: Any) -> Any:
    try:
        import numpy as np  # noqa: WPS433 (lazy)
        if isinstance(o, np.generic):
            return o.item()
        if isinstance(o, np.ndarray):
            return o.tolist()
    except ImportError:  # pragma: no cover - numpy is in the lock
        raise
    if isinstance(o, Path):
        return o.as_posix()
    return repr(o)


def _sanitise(obj: Any) -> Any:
    """NaN/Inf are never written to JSON (axis-cjson refuses them): replace with None."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    return obj


def _write_json_atomic(path: Path, obj: Any, *, sequential: bool = False) -> None:
    text = json.dumps(_sanitise(obj), sort_keys=True, indent=1, default=_json_default, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if sequential:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return
    tmp = path.with_name(f".{path.name}.tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load_params_file(path: str | os.PathLike) -> dict[str, dict]:
    """params.yaml (03 §3.1): `params: [{id, value, resolution, status, …}]` → {id: entry}."""
    import yaml  # lazy
    with open(path, "r", encoding="utf-8") as fh:
        doc = yaml.safe_load(fh) or {}
    raw = doc.get("params") if isinstance(doc, dict) else None
    out: dict[str, dict] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and "id" in entry:
                out[str(entry["id"])] = entry
    elif isinstance(raw, dict):
        for pid, entry in raw.items():
            out[str(pid)] = entry if isinstance(entry, dict) else {"value": entry}
    return out


# --------------------------------------------------------------------------------------------------------------
# Params mapping: read-only, no `.get` (a default would hide a missing answer; scanner S07)
# --------------------------------------------------------------------------------------------------------------
class Params:
    """Mapping-like view of the declared parameters. `params[pid]` raises ParamMissing; there is no `.get`."""

    __slots__ = ("_ctx",)

    def __init__(self, ctx: "Ctx"):
        self._ctx = ctx

    def __getitem__(self, pid: str) -> Any:
        return self._ctx.param(pid)

    def __contains__(self, pid: object) -> bool:
        return isinstance(pid, str) and pid in self._ctx._declared_params and pid in self._ctx._param_entries

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._ctx._declared_params))

    def __len__(self) -> int:
        return len(self._ctx._declared_params)

    def __getattr__(self, name: str) -> Any:
        if name in ("get", "setdefault", "pop", "update"):
            raise AttributeError(f"params has no .{name}(): use ctx.param(id); a default would hide a missing value")
        raise AttributeError(name)


# --------------------------------------------------------------------------------------------------------------
# Grid
# --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    crs: Any            # rasterio.crs.CRS
    transform: Any      # affine.Affine
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        t = self.transform
        xs = (t.c, t.c + t.a * self.width)
        ys = (t.f, t.f + t.e * self.height)
        return (min(xs), min(ys), max(xs), max(ys))

    def same_as(self, other: "Grid") -> bool:
        return (self.width == other.width and self.height == other.height
                and tuple(self.transform)[:6] == tuple(other.transform)[:6] and self.crs == other.crs)


# --------------------------------------------------------------------------------------------------------------
# Ctx
# --------------------------------------------------------------------------------------------------------------
class Ctx:
    """The only door between a step module and the file system (CONTRACTS §5)."""

    def __init__(self, *, step_id: str, inputs: dict[str, dict], outputs: dict[str, dict], params_file: str | None,
                 declared_params: Iterable[str], scratch_dir: str, no_seek_roots: Iterable[str] = (),
                 io_log: str | None = None, run_id: str | None = None, chain: str | None = None,
                 mode: str = "runner", run_dir: str | None = None):
        for k, v in DETERMINISM_ENV.items():
            os.environ.setdefault(k, v)
        self.step_id = step_id
        self.run_id = run_id
        self.chain = chain
        self.mode = mode
        self._inputs = {k: dict(v) for k, v in inputs.items()}
        self._outputs = {k: dict(v) for k, v in outputs.items()}
        self._declared_params = set(declared_params)
        self._params_file = params_file
        self._param_entries: dict[str, dict] = load_params_file(params_file) if params_file else {}
        self._scratch = Path(scratch_dir)
        self._scratch.mkdir(parents=True, exist_ok=True)
        self._no_seek = [Path(p) for p in no_seek_roots]
        self._io_log = Path(io_log) if io_log else None
        self._params_read: dict[str, Any] = {}
        self._reads: dict[str, dict] = {}
        self._staged_inputs: dict[str, Path] = {}
        self._pending: dict[str, Path] = {}       # final path (str) -> scratch path
        self._writes: list[dict] = []
        self._write_grids: dict[str, Grid] = {}
        self._committed = False
        self._run_dir = Path(run_dir) if run_dir else None
        self.params = Params(self)
        self.log = logging.getLogger(f"{PACKAGE}.{step_id}")

    # ---- construction ------------------------------------------------------------------------------------
    @classmethod
    def from_ctx_json(cls, path: str | os.PathLike) -> "Ctx":
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if doc.get("schema", CTX_SCHEMA) != CTX_SCHEMA:
            raise OutputContractError(f"unknown ctx schema {doc.get('schema')!r}", reason="CTX_SCHEMA")
        return cls(step_id=doc["step_id"], inputs=doc.get("inputs", {}), outputs=doc.get("outputs", {}),
                   params_file=doc.get("params_file"), declared_params=doc.get("declared_params", []),
                   scratch_dir=doc["scratch_dir"], no_seek_roots=doc.get("no_seek_roots", []),
                   io_log=doc.get("io_log"), run_id=doc.get("run_id"), chain=doc.get("chain"), mode="runner",
                   run_dir=doc.get("run_dir"))

    @classmethod
    def from_roots(cls, step_id: str, *, input_root: str, work_root: str, output_root: str, scratch_dir: str,
                   no_seek_roots: Iterable[str] = (), datasets: dict | None = None, params_file: str | None = None,
                   io_log: str | None = None) -> "Ctx":
        """Wheel / Databricks entry: roots injected, dataset map and params packaged with the code (D9-6)."""
        if datasets is None:
            datasets = json.loads(_package_resource_text("_datasets.json"))
        if params_file is None:
            params_file = str(_package_resource_path("params.yaml", Path(scratch_dir)))
        steps = datasets.get("steps", {})
        if step_id not in steps:
            raise OutputContractError(f"step {step_id!r} is not in _datasets.json", reason="UNKNOWN_STEP",
                                      subject=step_id)
        spec = steps[step_id]
        roots = {"input": Path(input_root), "work": Path(work_root), "output": Path(output_root)}

        def resolve(ds: str) -> dict:
            d = datasets["datasets"].get(ds)
            if d is None:
                raise OutputContractError(f"dataset {ds} missing from _datasets.json", reason="UNKNOWN_DATASET",
                                          subject=ds)
            p = roots[d["root"]] / d["relpath"]
            return {"path": str(p), "layer": d.get("layer"), "kind": d.get("kind"), "format": d.get("format"),
                    "may_be_empty": bool(d.get("may_be_empty", False)),
                    "source": "input" if d["root"] == "input" else "run"}

        inputs = {ds: resolve(ds) for ds in spec.get("inputs", [])}
        outputs = {ds: resolve(ds) for ds in spec.get("outputs", [])}
        if io_log is None:
            io_log = str(Path(scratch_dir) / f"{step_id}.io.json")
        return cls(step_id=step_id, inputs=inputs, outputs=outputs, params_file=params_file,
                   declared_params=spec.get("params", []), scratch_dir=scratch_dir, no_seek_roots=no_seek_roots,
                   io_log=io_log, mode="wheel")

    # ---- declarations ---------------------------------------------------------------------------------------
    @property
    def inputs(self) -> list[str]:
        return sorted(self._inputs)

    @property
    def outputs(self) -> list[str]:
        return sorted(self._outputs)

    @property
    def declared_params(self) -> list[str]:
        return sorted(self._declared_params)

    def _in(self, ds: str) -> dict:
        if ds not in self._inputs:
            if ds in self._outputs:
                raise DatasetNotDeclared(f"{ds} is an output of {self.step_id}, not an input; reading your own "
                                         f"output is not allowed", direction="input", subject=ds)
            raise DatasetNotDeclared(f"{ds} is not a declared input of {self.step_id}", direction="input", subject=ds)
        return self._inputs[ds]

    def _out(self, ds: str) -> dict:
        if ds not in self._outputs:
            raise DatasetNotDeclared(f"{ds} is not a declared output of {self.step_id}", direction="output",
                                     subject=ds)
        return self._outputs[ds]

    def _is_no_seek(self, path: Path) -> bool:
        return any(_under(path, r) for r in self._no_seek)

    # ---- parameters -----------------------------------------------------------------------------------------
    def param(self, pid: str) -> Any:
        """The value of a declared, resolved parameter. No default argument by design."""
        if pid not in self._declared_params:
            raise ParamMissing(f"parameter {pid} is not declared for step {self.step_id}", subject=pid)
        entry = self._param_entries.get(pid)
        if entry is None or "value" not in entry:
            raise ParamMissing(f"parameter {pid} is absent from params.yaml", subject=pid)
        if str(entry.get("resolution", "")).lower() == "unresolved":
            raise ParamMissing(f"parameter {pid} is unresolved (an open question); ask, do not guess", subject=pid)
        value = entry["value"]
        self._params_read[pid] = value
        return value

    # ---- paths ----------------------------------------------------------------------------------------------
    def input_path(self, ds: str) -> Path:
        spec = self._in(ds)
        p = Path(spec["path"])
        if not p.exists():
            raise InputMissing(f"declared input {ds} not found at {p}", subject=ds)
        if self._is_no_seek(p) and str(p).lower().endswith(STAGE_READ_SUFFIXES):
            p = self._stage_read(ds, p)
        self._record_read(ds, p)
        return p

    def output_path(self, ds: str) -> Path:
        """A local scratch path; committed to the declared location after run() returns (atomically)."""
        spec = self._out(ds)
        final = Path(spec["path"])
        key = str(final)
        if key not in self._pending:
            local =self._scratch / "out" / hashlib.sha256(key.encode()).hexdigest()[:12] / final.name
            local.parent.mkdir(parents=True, exist_ok=True)
            self._pending[key] = local
        return self._pending[key]

    def input_layer(self, ds: str) -> str | None:
        return self._in(ds).get("layer")

    def output_layer(self, ds: str) -> str | None:
        return self._out(ds).get("layer")

    def _stage_read(self, ds: str, src: Path) -> Path:
        if ds in self._staged_inputs:
            return self._staged_inputs[ds]
        dst = self._scratch / "in" / hashlib.sha256(str(src).encode()).hexdigest()[:12] / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)  # one sequential read of the seek-less source
        self._staged_inputs[ds] = dst
        return dst

    def _shown(self, p: str | Path) -> str:
        """Paths in io.json: run-relative in runner mode (outputs/<step>.tmp/ shown as its final outputs/<step>/)."""
        p = Path(p)
        if self._run_dir is None:
            return str(p)
        try:
            r = p.resolve().relative_to(self._run_dir.resolve()).as_posix()
        except ValueError:
            return str(p)
        tmp = f"outputs/{self.step_id}.tmp/"
        return f"outputs/{self.step_id}/" + r[len(tmp):] if r.startswith(tmp) else r

    def _record_read(self, ds: str, p: Path) -> None:
        if ds in self._reads:
            return
        spec = self._inputs[ds]
        rec = {"dataset": ds, "path": self._shown(spec["path"]), "source": spec.get("source")}
        if p.is_file():
            rec.update(sha256=_sha256_file(p), size=p.stat().st_size)
        if p != Path(spec["path"]):
            rec["staged_local"] = True
        self._reads[ds] = rec

    # ---- rasters --------------------------------------------------------------------------------------------
    def grid_of(self, ds: str) -> Grid:
        """Grid of an input raster (or of a raster this step already wrote)."""
        if ds in self._write_grids:
            return self._write_grids[ds]
        import rasterio
        with rasterio.open(self.input_path(ds)) as src:
            return Grid(crs=src.crs, transform=src.transform, width=src.width, height=src.height)

    def read_raster(self, ds: str, band: int = 1, masked: bool = True):
        """(numpy.ma.MaskedArray | ndarray, profile dict)."""
        import rasterio
        with rasterio.open(self.input_path(ds)) as src:
            arr = src.read(band, masked=masked)
            profile = dict(src.profile)
        return arr, profile

    def write_raster(self, ds: str, array, *, grid: Grid = None, dtype=None, nodata=..., **creation_options) -> Path:
        """Write a raster on an explicit grid with an explicit nodata (None allowed, but must be passed)."""
        import numpy as np
        import rasterio
        if grid is None or not isinstance(grid, Grid):
            raise OutputContractError(f"write_raster({ds}) needs grid=<Grid> (from ctx.grid_of); refusing to guess "
                                      f"a grid", reason="GRID_NOT_GIVEN", subject=ds)
        if nodata is ...:
            raise OutputContractError(f"write_raster({ds}) needs an explicit nodata= keyword (None is allowed)",
                                      reason="NODATA_NOT_GIVEN", subject=ds)
        if dtype is None:
            raise OutputContractError(f"write_raster({ds}) needs an explicit dtype=", reason="DTYPE_NOT_GIVEN",
                                      subject=ds)
        spec = self._out(ds)
        kind, fmt = spec.get("kind"), spec.get("format")
        if kind not in (None, "raster"):
            raise OutputContractError(f"{ds} is declared {kind}, not a raster", reason="KIND_MISMATCH", subject=ds)
        dt = np.dtype(dtype)
        arr = array
        is_masked = isinstance(arr, np.ma.MaskedArray)
        data = np.ma.getdata(arr) if is_masked else np.asarray(arr)
        if data.ndim == 2:
            data = data[np.newaxis, ...]
            mask = np.ma.getmaskarray(arr)[np.newaxis, ...] if is_masked else None
        elif data.ndim == 3:
            mask = np.ma.getmaskarray(arr) if is_masked else None
        else:
            raise OutputContractError(f"{ds}: array must be 2-D or 3-D, got {data.ndim}-D", reason="SHAPE_MISMATCH",
                                      subject=ds)
        if data.shape[1:] != grid.shape:
            raise OutputContractError(f"{ds}: array shape {data.shape[1:]} != grid shape {grid.shape}",
                                      reason="SHAPE_MISMATCH", subject=ds)
        if data.dtype != dt and not np.can_cast(data.dtype, dt, casting="safe"):
            raise OutputContractError(f"{ds}: array dtype {data.dtype} cannot be written as {dt} without loss; "
                                      f"cast explicitly (astype) so the narrowing is visible in the code",
                                      reason="DTYPE_MISMATCH", subject=ds)
        if nodata is not None:
            nd = float(nodata)
            if dt.kind in "iu":
                info = np.iinfo(dt)
                if not (math.isfinite(nd) and nd == int(nd) and info.min <= nd <= info.max):
                    raise OutputContractError(f"{ds}: nodata {nodata!r} not representable in {dt}",
                                              reason="NODATA_DTYPE_MISMATCH", subject=ds)
            elif dt.kind == "f":
                if math.isfinite(nd) and abs(nd) > np.finfo(dt).max:
                    raise OutputContractError(f"{ds}: nodata {nodata!r} out of range for {dt}",
                                              reason="NODATA_DTYPE_MISMATCH", subject=ds)
        out = data.astype(dt, copy=True)
        if mask is not None and mask.any():
            if nodata is None:
                raise OutputContractError(f"{ds}: masked cells but nodata=None; declare a nodata value",
                                          reason="NODATA_NOT_GIVEN", subject=ds)
            out[mask] = np.asarray(nodata, dtype=dt)
        # empty = no valid cell at all
        if nodata is not None:
            valid = ~np.isnan(out) if dt.kind == "f" else np.ones(out.shape, bool)
            if not (isinstance(nodata, float) and math.isnan(nodata)):
                valid &= out != np.asarray(nodata, dtype=dt)
            if not valid.any() and not spec.get("may_be_empty", False):
                raise OutputContractError(f"{ds}: every cell is nodata; the dataset is not declared may_be_empty",
                                          reason="OUTPUT_EMPTY", subject=ds)
        path = self.output_path(ds)
        driver = fmt or infer_kind_format(str(path))[1]
        profile = {"driver": driver, "width": grid.width, "height": grid.height, "count": out.shape[0],
                   "dtype": dt.name, "crs": grid.crs, "transform": grid.transform, "nodata": nodata}
        profile.update(creation_options)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(out)
        self._write_grids[ds] = grid
        return path

    # ---- vectors --------------------------------------------------------------------------------------------
    def read_vector(self, ds: str, layer: str | None = None):
        import geopandas as gpd
        lyr = layer if layer is not None else self.input_layer(ds)
        kw = {"layer": lyr} if lyr is not None else {}
        return gpd.read_file(self.input_path(ds), engine="pyogrio", **kw)

    def write_vector(self, ds: str, gdf, layer: str | None = None) -> Path:
        spec = self._out(ds)
        if spec.get("kind") not in (None, "vector"):
            raise OutputContractError(f"{ds} is declared {spec.get('kind')}, not a vector", reason="KIND_MISMATCH",
                                      subject=ds)
        if len(gdf) == 0 and not spec.get("may_be_empty", False):
            raise OutputContractError(f"{ds}: no features; the dataset is not declared may_be_empty",
                                      reason="OUTPUT_EMPTY", subject=ds)
        if getattr(gdf, "crs", None) is None and "geometry" in getattr(gdf, "columns", []):
            raise OutputContractError(f"{ds}: GeoDataFrame has no CRS", reason="CRS_MISSING", subject=ds)
        path = self.output_path(ds)
        lyr = layer if layer is not None else (self.output_layer(ds) or Path(spec["path"]).stem)
        driver = spec.get("format") or infer_kind_format(str(path))[1]
        gdf.to_file(path, layer=lyr, driver=driver, engine="pyogrio")
        return path

    # ---- tables ---------------------------------------------------------------------------------------------
    def open_workbook(self, ds: str, data_only: bool = True):
        import openpyxl
        return openpyxl.load_workbook(self.input_path(ds), data_only=data_only)

    def save_workbook(self, ds: str, wb) -> Path:
        self._out(ds)
        path = self.output_path(ds)
        wb.save(path)
        return path

    def write_csv(self, ds: str, header: list, rows: Iterable[Iterable]) -> Path:
        spec = self._out(ds)
        rows = [list(r) for r in rows]
        if not rows and not spec.get("may_be_empty", False):
            raise OutputContractError(f"{ds}: no data rows; the dataset is not declared may_be_empty",
                                      reason="OUTPUT_EMPTY", subject=ds)
        path = self.output_path(ds)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(list(header))
            w.writerows(rows)
        return path

    # ---- commit + io record ---------------------------------------------------------------------------------
    def _commit(self) -> list[dict]:
        """Verify every declared output exists in scratch and is non-empty, then move it to its declared path."""
        written: dict[str, list[str]] = {}
        for ds, spec in sorted(self._outputs.items()):
            final = Path(spec["path"])
            local = self._pending.get(str(final))
            if local is None or not local.exists():
                raise OutputContractError(f"declared output {ds} was not written (use ctx.output_path / "
                                          f"ctx.write_*)", reason="OUTPUT_MISSING", subject=ds)
            if local.is_file() and local.stat().st_size == 0:
                raise OutputContractError(f"declared output {ds} is an empty file", reason="OUTPUT_EMPTY", subject=ds)
            written.setdefault(str(final), []).append(ds)
        records = []
        for ds, spec in sorted(self._outputs.items()):
            final = Path(spec["path"])
            local = self._pending[str(final)]
            stats = _stats_for(local, spec)
            if stats.get("empty") and not spec.get("may_be_empty", False):
                raise OutputContractError(f"declared output {ds} holds no data ({stats.get('empty')})",
                                          reason="OUTPUT_EMPTY", subject=ds)
            stats.pop("empty", None)
            records.append({"dataset": ds, "path": self._shown(final), "layer": spec.get("layer"),
                            "sha256": _sha256_file(local) if local.is_file() else None,
                            "size": local.stat().st_size if local.is_file() else None, "stats": stats})
        for final_s, _ in sorted(written.items()):
            final = Path(final_s)
            local = self._pending[final_s]
            final.parent.mkdir(parents=True, exist_ok=True)
            if self._is_no_seek(final):
                shutil.copyfile(local, final)          # one sequential write; no rename on seek-less mounts
                os.unlink(local)
            else:
                try:
                    os.replace(local, final)
                except OSError:                          # cross-device: copy beside, then atomic rename
                    part = final.with_name(f".{final.name}.partial")
                    shutil.copyfile(local, part)
                    os.replace(part, final)
                    os.unlink(local)
        self._writes = records
        self._committed = True
        return records

    def _io_record(self, status: str, error: str | None = None) -> dict:
        return {"schema": IO_SCHEMA, "step_id": self.step_id, "run_id": self.run_id, "mode": self.mode,
                "status": status, "error": error, "runtime_version": RUNTIME_VERSION,
                "params_read": {k: self._params_read[k] for k in sorted(self._params_read)},
                "reads": [self._reads[k] for k in sorted(self._reads)],
                "writes": sorted(self._writes, key=lambda r: r["dataset"])}

    def _write_io_log(self, status: str, error: str | None = None) -> None:
        if self._io_log is None:
            return
        _write_json_atomic(self._io_log, self._io_record(status, error), sequential=self._is_no_seek(self._io_log))

    def _cleanup_scratch(self) -> None:
        for sub in ("out", "in"):
            shutil.rmtree(self._scratch / sub, ignore_errors=True)


def _stats_for(path: Path, spec: dict) -> dict:
    """{sum, count, nodata_count, min, max} for rasters; {count} for vectors and tables. `empty` flags no data."""
    kind = spec.get("kind") or infer_kind_format(str(path))[0]
    fmt = spec.get("format") or infer_kind_format(str(path))[1]
    st: dict[str, Any] = {"sum": None, "count": None, "nodata_count": None, "min": None, "max": None}
    if kind == "raster":
        import numpy as np
        import rasterio
        with rasterio.open(path) as src:
            total = 0.0
            count = 0
            nd_count = 0
            mn, mx = math.inf, -math.inf
            for b in range(1, src.count + 1):
                a = src.read(b, masked=True)
                valid = a.compressed()
                if valid.dtype.kind == "f":
                    valid = valid[np.isfinite(valid)]
                n = int(valid.size)
                count += n
                nd_count += int(a.size - n)
                if n:
                    total += float(valid.astype("float64").sum())
                    mn = min(mn, float(valid.min()))
                    mx = max(mx, float(valid.max()))
            st.update(sum=total, count=count, nodata_count=nd_count, min=_finite_or_none(mn), max=_finite_or_none(mx))
            if count == 0:
                st["empty"] = "no valid cell"
    elif kind == "vector":
        import pyogrio
        layer = spec.get("layer")
        info = pyogrio.read_info(path, layer=layer) if layer else pyogrio.read_info(path)
        st["count"] = int(info["features"])
        if st["count"] == 0:
            st["empty"] = "no features"
    elif fmt == "CSV":
        with open(path, newline="", encoding="utf-8") as fh:
            n = sum(1 for _ in csv.reader(fh)) - 1
        st["count"] = max(n, 0)
        if n <= 0:
            st["empty"] = "no data rows"
    return st


# --------------------------------------------------------------------------------------------------------------
# Step execution (shared by `python -m axis_pipeline` and the runner's axis_boot)
# --------------------------------------------------------------------------------------------------------------
def load_step_module(step_id: str):
    return importlib.import_module(f"{PACKAGE}.{step_id}")


def check_module_contract(mod, ctx: Ctx) -> None:
    """Module constants must equal the declarations the runner/wheel resolved from the graph."""
    problems = []
    if getattr(mod, "STEP_ID", None) != ctx.step_id:
        problems.append(f"STEP_ID={getattr(mod, 'STEP_ID', None)!r} != {ctx.step_id!r}")
    for name, declared in (("INPUTS", ctx.inputs), ("OUTPUTS", ctx.outputs), ("PARAMS", ctx.declared_params)):
        val = getattr(mod, name, None)
        if not isinstance(val, (list, tuple)) or sorted(val) != sorted(declared):
            problems.append(f"{name}={val!r} != graph {declared!r}")
    if not callable(getattr(mod, "run", None)):
        problems.append("no callable run(ctx)")
    if problems:
        raise OutputContractError(f"{ctx.step_id}: module contract differs from the graph: " + "; ".join(problems),
                                  reason="MODULE_CONTRACT_MISMATCH", subject=ctx.step_id)


def run_step(ctx: Ctx, module=None) -> list[dict]:
    """Import the step module, check its contract, call run(ctx), commit outputs, write io.json.

    Exceptions propagate (BaseException included); the caller maps them to exit codes. io.json is written in
    every case, with status ok | failed.
    """
    status, err = "failed", None
    try:
        mod = module if module is not None else load_step_module(ctx.step_id)
        check_module_contract(mod, ctx)
        mod.run(ctx)
        records = ctx._commit()
        status = "ok"
        return records
    except BaseException as exc:
        err = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        ctx._write_io_log(status, err)
        if status == "ok":
            ctx._cleanup_scratch()   # on failure the partial files stay for diagnosis


# --------------------------------------------------------------------------------------------------------------
# Wheel packaging helpers: `_datasets.json` from graph.json (used by make_databricks.py; pure function)
# --------------------------------------------------------------------------------------------------------------
def new_relpath(ds: dict, producer: str | None) -> str:
    new = ds.get("new") or {}
    if new.get("path"):
        return new["path"]
    legacy = ds.get("legacy") or {}
    if legacy.get("path"):
        return f"{producer}/{Path(legacy['path']).name}"
    ext = {"raster": ".tif", "vector": ".gpkg", "table": ".csv"}.get(ds.get("kind"), "")
    return f"{producer}/{ds['id'][3:] if ds['id'].startswith('ds.') else ds['id']}{ext}"


def datasets_manifest_from_graph(graph: dict) -> dict:
    """`_datasets.json`: per migrated step its inputs/outputs/params; per dataset its root and relative path."""
    steps_out: dict[str, dict] = {}
    producer: dict[str, str] = {}
    migrated = [s for s in graph.get("steps", []) if s.get("scope") == "migrate" and s.get("kind") != "bridge"
                and (s.get("new") or {}).get("module")]
    for s in migrated:
        for ds in s.get("outputs", []):
            producer[ds] = s["id"]
        steps_out[s["id"]] = {"inputs": sorted(s.get("inputs", [])), "outputs": sorted(s.get("outputs", [])),
                              "params": sorted(s.get("params", [])), "timeout_s": s.get("timeout_s", 120)}
    used = {d for s in steps_out.values() for d in s["inputs"] + s["outputs"]}
    ds_out: dict[str, dict] = {}
    for ds in graph.get("datasets", []):
        if ds["id"] not in used:
            continue
        legacy = ds.get("legacy") or {}
        prod = producer.get(ds["id"])
        if prod is None:
            root, rel = "input", legacy.get("path")
        else:
            root = "output" if ds.get("role") == "final" else "work"
            rel = new_relpath(ds, prod)
        path_for_kind = rel or ""
        kind, fmt = infer_kind_format(path_for_kind)
        layer = (ds.get("new") or {}).get("layer") if prod else legacy.get("layer")
        if prod and layer is None:
            layer = legacy.get("layer")
        ds_out[ds["id"]] = {"root": root, "relpath": rel, "kind": ds.get("kind") or kind, "format": fmt,
                            "layer": layer, "may_be_empty": bool((ds.get("compare") or {}).get("may_be_empty"))}
    return {"schema": DATASETS_SCHEMA, "steps": steps_out, "datasets": ds_out}


def _package_resource_text(name: str) -> str:
    from importlib import resources
    return resources.files(PACKAGE).joinpath(name).read_text(encoding="utf-8")


def _package_resource_path(name: str, scratch: Path) -> Path:
    """A real file path for packaged data (zip-safe): copied to scratch."""
    from importlib import resources
    data = resources.files(PACKAGE).joinpath(name).read_bytes()
    scratch.mkdir(parents=True, exist_ok=True)
    p = scratch / f"_pkg_{name}"
    p.write_bytes(data)
    return p


# --------------------------------------------------------------------------------------------------------------
# CLI (`python -m axis_pipeline`; console script `axis-pipeline`)
# --------------------------------------------------------------------------------------------------------------
def build_arg_parser():
    import argparse
    ap = argparse.ArgumentParser(prog="axis-pipeline", description="Run one migrated pipeline step.")
    ap.add_argument("--step", required=True, help="step id, e.g. s01_prepare_population")
    ap.add_argument("--ctx", help="runner ctx.json (runner mode)")
    ap.add_argument("--input-root", help="wheel mode: root holding inputs at <legacy relpath>")
    ap.add_argument("--work-root", help="wheel mode: root for intermediates at <new.path>")
    ap.add_argument("--output-root", help="wheel mode: root for finals at <new.path>")
    ap.add_argument("--scratch-dir", help="wheel mode: local, seekable scratch directory")
    ap.add_argument("--no-seek-root", action="append", default=[],
                    help="wheel mode: a root on a seek-less mount (repeatable); writes and GPKG/XLSX reads are staged")
    ap.add_argument("--io-log", help="where to write <step>.io.json (wheel default: <scratch>/<step>.io.json)")
    ap.add_argument("--params", help=argparse.SUPPRESS)      # dry-run override; normally packaged params.yaml
    ap.add_argument("--datasets", help=argparse.SUPPRESS)    # dry-run override; normally packaged _datasets.json
    return ap


def ctx_from_args(args) -> Ctx:
    if args.ctx:
        return Ctx.from_ctx_json(args.ctx)
    missing = [n for n in ("input_root", "work_root", "output_root", "scratch_dir") if not getattr(args, n)]
    if missing:
        raise OutputContractError("wheel mode needs --input-root --work-root --output-root --scratch-dir (missing: "
                                  + ", ".join("--" + m.replace("_", "-") for m in missing) + ")", reason="USAGE")
    datasets = None
    if args.datasets:
        with open(args.datasets, encoding="utf-8") as fh:
            datasets = json.load(fh)
    return Ctx.from_roots(args.step, input_root=args.input_root, work_root=args.work_root,
                          output_root=args.output_root, scratch_dir=args.scratch_dir,
                          no_seek_roots=args.no_seek_root, datasets=datasets, params_file=args.params,
                          io_log=args.io_log)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger(f"{PACKAGE}.main")
    t0 = time.monotonic()
    try:
        ctx = ctx_from_args(args)
        if ctx.step_id != args.step:
            raise OutputContractError(f"--step {args.step} but ctx is for {ctx.step_id}", reason="USAGE")
        records = run_step(ctx)
    except BaseException as exc:  # a step never exits the process itself: SystemExit is a failure too
        import traceback
        traceback.print_exc(file=sys.stderr)
        code = exit_code_for(exc)
        log.error("step %s failed: %s (exit %d)", args.step, reason_for(exc), code)
        return code
    log.info("step %s ok: %d output(s) in %.2f s", args.step, len(records), time.monotonic() - t0)
    return EXIT_OK
