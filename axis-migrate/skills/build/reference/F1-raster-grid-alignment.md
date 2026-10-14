# F1 · Raster grid alignment

Open when: two or more rasters are combined; a raster is clipped, warped, windowed or rasterised; the legacy sets
`arcpy.env.snapRaster|cellSize|extent|outputCoordinateSystem`, an `EnvManager`, or a QGIS call passes extent, resolution
or CRS options. A grid is `(crs, transform, width, height)`: two rasters align only when all four agree.

Enforced elsewhere: `compare.py` gates every raster on CRS, shape and transform (origin and far corner, 1e-6 px) before
values, reports `RASTER_TRANSFORM_MISMATCH` with `shift_px` and `DATA_OFFSET_SUSPECTED`; `ctx.write_raster` refuses a
write without `grid=` and checks the array shape against it. Equal totals do not prove alignment: a shifted grid keeps sums.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **G-1** · Build one target grid from the raster the legacy aligned to (`ctx.grid_of(ds)`) and pass it to every read, warp, rasterise and write; never derive a grid from vector bounds or from the data's bbox · fails if ignored: broadcast crash, or a silent sub-pixel shift of every downstream value · evidence: ev:6324c75683, ev:59dc29201b, ev:f7fd3d4630, ev:89e0818fc8, ev:78b4b73f10, ev:1306913d43, ev:29f3ac4824 · eval: T-shift, lux · form: P+C+R (grid gate; `write_raster` needs `grid=`)
- **G-2** · Snapping bounds is not snapping data: a window read from a source whose origin or cell size differs, even in the ninth digit, has fractional offsets; assert integer offsets (`~grid.transform * (x, y)`) or reproject; never trust "aligned" in a log · fails if ignored: file written off the snap grid while the log says aligned · evidence: ev:0a6b49fd4f, ev:0f5686fc0e, ev:f7fd3d4630 · eval: T-shift · form: P+C
- **G-3** · Translate the environment, not only the tool call: a crop, mask or reprojection without an explicit destination grid mints a new origin, and a legacy env block or tool option may already have reprojected and snapped the output; read the legacy output's transform and size before choosing the OSS op · fails if ignored: values land in neighbouring cells · evidence: ev:f7fd3d4630, ev:89e0818fc8, ev:78b4b73f10 · eval: T-shift, E-rs · form: P
- **G-4** · A missing reference grid is an error, never an unsnapped fallback · fails if ignored: green run on a misaligned grid · evidence: ev:2c3e709e06 · eval: T-shift · form: P+R
- **G-5** · A finer grid nests inside the coarse one: derive the fine transform from the coarse transform (same origin, cell size divided by an integer factor); chunk sizes are multiples of the factor · fails if ignored: fine blocks straddle coarse cells; aggregates drift · evidence: ev:24b2f4aace · eval: E-rs · form: P+C
- **G-6** · The output extent belongs to the output family declared in the graph (usually the legacy output's extent), not to the bbox of the data inside it · fails if ignored: stacking and mosaics break; edge strips differ · evidence: ev:59dc29201b, ev:6def9c1bf8 · eval: T-shift, lux · form: P+C (`RASTER_SHAPE_MISMATCH`)
- **G-7** · Clamp every window by intersecting it with `Window(0, 0, width, height)`: offset and size both · fails if ignored: zero-size reads and crashes far from the cause · evidence: ev:ce5cacb781, ev:d34390889f, ev:a49fbc2668 · eval: E-rs · form: P
- **G-8** · Tiled processing reads with a halo and writes only the tile core; per-cell counts come from the assembled grid, not per tile · fails if ignored: double counts, seams, partial edge cells · evidence: ev:4557a6d353, ev:b75f44cf42, ev:9d4906f814 · eval: T-perf · form: P

Before writing a raster step, state in the plan which input defines the grid and which legacy output shows it
(`legacy_ops[].settings.grid`), and check in the isolated run that the compare grid gate passes before reading values.
