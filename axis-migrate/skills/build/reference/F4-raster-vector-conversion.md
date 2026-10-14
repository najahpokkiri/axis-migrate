# F4 · Raster-vector conversion

Open when: rasterising polygons, lines or points; zonal statistics; raster clip; extract by mask; polygonising.
The assignment rule (which cells belong to a shape) decides masks, zone totals and every value downstream.

Enforced elsewhere: `compare.py` compares the valid mask and values cell by cell, and per-zone totals using the graph's
`compare.zones` and `compare.zone_rule` (`ZONE_TOTAL_DRIFT`); the zone rule there must equal the legacy rule.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **RV-1** · Rasterise on the target grid (F1). Cell-centre assignment is `all_touched=False`; `all_touched=True` reproduces only an explicit all-touched option and is not ArcGIS MAXIMUM_AREA (no rasterio flag; needs supersampling); `fill` is the declared NoData unless the legacy filled 0; overlapping shapes are last-wins, so order them by the legacy priority; the rule is a parameter · fails if ignored: masks grow or shrink at edges; "no polygon" becomes 0 · evidence: ev:8260c518d5, ev:12b8d9f5e3, ev:3549f7a3eb · eval: lux, T-perf · form: P+C
- **RV-2** · ArcGIS raster `Clip` cuts to the rectangle unless clipping geometry is set; `ExtractByMask` is the polygon mask; for a QGIS or GDAL clip read every option the call passes and take the effective grid from the legacy output (QGIS defaults vary by version) · fails if ignored: blanked pixels or a different extent vs legacy · evidence: ev:1f5f8b9320, ev:c3c60a3eac · eval: E-rs · form: P+C
- **RV-3** · Zonal parity: assign cells to zones by the legacy rule (ArcGIS zonal tools: cell centre); fractional coverage only when the user accepts new numbers; features sharing a zone id are one zone; a zone smaller than a cell returns no value, not 0 · fails if ignored: zone totals differ; duplicate or missing zones · evidence: ev:3237c3221e · eval: lux · form: P+C
- **RV-4** · Rasterise a polygon on the target grid instead of reprojecting the raster and the polygon independently · fails if ignored: pixels fall outside "their" polygon · evidence: ev:5758b5cad8 · eval: lux · form: P
- **RV-5** · A zonal shortcut (rasterise zone ids once, then `np.bincount` or a groupby) is equivalent to per-polygon masking only with the same assignment rule and the same NoData handling; prove it with the step compare · fails if ignored: "faster" zonal changes totals · evidence: ev:3237c3221e, ev:d91965b64e · eval: T-perf, lux · form: C
