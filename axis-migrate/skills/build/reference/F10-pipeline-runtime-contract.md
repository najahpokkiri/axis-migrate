# F10 · Pipeline runtime contract (read before writing any step)

The pipeline is the package `axis_pipeline` (`migration/pipeline/`). The vendored `_axis_runtime.py` owns parameters,
paths, staging, atomic commits and `io.json`; you write only `pipeline/<step id>.py`:

    STEP_ID = "<step id>"            # == graph step id
    INPUTS  = ["ds.<id>", ...]       # == graph steps[].inputs   (order free; set equality is checked)
    OUTPUTS = ["ds.<id>", ...]       # == graph steps[].outputs
    PARAMS  = ["p.<id>", ...]        # every ctx.param id this module reads
    def run(ctx) -> None: ...

Ctx API (exact): `ctx.param(pid)` (no default) · `ctx.input_path(ds)` / `ctx.output_path(ds)` · `ctx.input_layer(ds)` /
`ctx.output_layer(ds)` · `ctx.grid_of(ds)` → `Grid(crs, transform, width, height)` · `ctx.read_raster(ds, band=1,
masked=True)` → `(array, profile)` · `ctx.write_raster(ds, array, *, grid, dtype, nodata, **creation_options)` ·
`ctx.read_vector(ds, layer=None)` · `ctx.write_vector(ds, gdf, layer=None)` · `ctx.open_workbook(ds, data_only=True)` ·
`ctx.save_workbook(ds, wb)` · `ctx.write_csv(ds, header, rows)` · `ctx.log`. Other writers (`gdf.to_file`, pandas)
write to `ctx.output_path(ds)` only. Exceptions: `ParamMissing`, `InputMissing` (exit 2), `DatasetNotDeclared`,
`OutputContractError` (exit 3); any other exception is exit 1 (repair loop). Example: `templates/step_template.py`.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **P-1** · Any failure raises: a broad `except` re-raises; never return a failure value, print and continue, or call `sys.exit` in a step · fails if ignored: green partial runs · evidence: ev:7c4b83a137, ev:a2cd875368, ev:7d13e44a84 · eval: N-miss · form: S+R (S01, S02, S03; the boot wrapper reports `STEP_CALLED_EXIT`)
- **P-2** · A missing input or parameter stops the step (ctx raises, exit 2) and becomes a question; never synthesise, sample, download or fall back to another source or engine; randomness only from a seed parameter · fails if ignored: plausible wrong outputs · evidence: ev:509238500d, ev:9d80de338f, ev:54dcd87183, ev:2c3e709e06, ev:e8c5aa0541 · eval: N-miss · form: S+R (S09, S10)
- **P-3** · Every value that can change an output (threshold, distance, NoData, CRS, scale factor, sheet range, method choice) comes from `ctx.param("p.x")` and is listed in `PARAMS`; no `.get(k, default)`, no literals; defaults live in params.yaml with a source; a different value is an interview answer, not an edit · fails if ignored: silent defaults; answers that never reach the code · evidence: ev:f2b44600d5, ev:cff9992c35, ev:417e772dc7, ev:5aa6c438d1, ev:3fab54d35d · eval: lux, N-answer · form: S+R (S07, S08, S16; `params_check.py --stage wired`)
- **P-4** · Paths only through ctx: no path literals, no `__file__`, no `exists()` control flow, no globbing; never open a legacy intermediate or final · fails if ignored: runs only on the author's machine; copies the reference instead of computing it · evidence: ev:7614637a56, ev:aae86f0de2, ev:748d718a42 · eval: lux, N-copy · form: S+R (S11; runner `UNDECLARED_READ`, `OUTPUT_NOT_PRODUCED_BY_RUN`)
- **P-5** · Imports: stdlib and the locked libraries only (numpy, rasterio, geopandas, shapely, pyproj, pyogrio, pandas, openpyxl); never invent helper modules or import from the pipeline package, `analysis/`, arcpy, qgis or processing · fails if ignored: ModuleNotFoundError loops; hidden dependencies · evidence: ev:8553d19581, ev:b1e062b4db · eval: all · form: S (S04, S13, S14)
- **P-6** · No skip-if-exists and no caches between runs; every run writes a fresh run directory and ctx commits outputs atomically after `run()` returns · fails if ignored: stale outputs mask fixes · evidence: ev:2c3d0203b9, ev:373474621e, ev:5a8dc34681 · eval: all · form: R
- **P-7** · Unported means it raises: `raise NotImplementedError("<what and why>")` with the scope recorded; no `pass` bodies, no "TODO, log and continue" · fails if ignored: a silently dropped branch or step · evidence: ev:02dae0c18d, ev:b478ff69d5 · eval: T-branch · form: P+C (`OUTPUT_MISSING`)
- **P-8** · Module constants equal the graph and every write goes through a declared output; raster writes pass `grid=`, `dtype=` and `nodata=` explicitly; vectors keep the declared layer name · fails if ignored: exit 3 (`MODULE_CONTRACT_MISMATCH`, `DatasetNotDeclared`) · evidence: ev:fbc78baee1, ev:8553d19581 · eval: all · form: R
- **P-9** · Tests you write are smoke tests on small slices, labelled as such; never mock geopandas, rasterio, numpy or the step under test · fails if ignored: green tests mistaken for parity · evidence: ev:81577de337, ev:7a39810011 · eval: N-stub · form: S (S06, S12; boot stub canary `STUB_DETECTED`)
- **P-10** · Run steps only through `run_local.py`; never `python pipeline/...` or an import of the package · fails if ignored: no read ledger, no evidence (`UNLEDGERED_RUN`) · evidence: ev:9d80de338f, ev:5a8dc34681 · eval: N-copy · form: R
