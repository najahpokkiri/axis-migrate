# F2 · Resampling and counts

Open when: any resample, reprojection, aggregation, disaggregation, rounding, `Int()` or scaling of a value raster.
First decide the dataset's measure (`compare.measure` in the graph): `extensive` (counts, totals: sums must be
conserved), `intensive` (densities, shares, means), `categorical`, `mask`, `index`. The measure picks the method.

Enforced elsewhere: `compare.py` runs total and per-zone conservation for extensive datasets (`TOTAL_DRIFT`,
`ZONE_TOTAL_DRIFT`), flags `RASTER_NON_INTEGER_COUNTS`, `SMOOTHING_SUSPECTED` and `NUMERIC_SCALE_FACTOR`, and evaluates the
graph's `invariants[]` (`INVARIANT_VIOLATED`). Conservation is necessary, not sufficient.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **R-1** · Never resample extensive counts bilinear or cubic; nearest preserves sums only on identical or nested grids; otherwise use sum or area-weighted resampling and check totals in the step · fails if ignored: totals drift; counts smear across cells · evidence: ev:894d3ba566, ev:03d7dc1a2e, ev:117e4615c0 · eval: T-bil · form: P+C
- **R-2** · Resampling changes cell size, not CRS; ArcGIS Resample and rasterio both default to nearest; always pass `resampling=` explicitly, as a parameter · fails if ignored: wrong CRS or resolution; a silent default method · evidence: ev:117e4615c0, ev:662d7f6399 · eval: E-rs · form: P
- **R-3** · Integer-scaled legacy outputs (values × 10^k stored as integers) and unit conversions are recorded parameters in params.yaml with a source; every downstream rounding is revisited with them · fails if ignored: a 10^k parity gap; small values round to 0 · evidence: ev:a2b0dfd134, ev:47ef5589d3, ev:abee2dfbe4 · eval: T-x1000 · form: P+C (`NUMERIC_SCALE_FACTOR`)
- **R-4** · Port the legacy rounding exactly: ArcPy `Int()` truncates toward zero, `np.round` rounds half to even, `Int(x + 0.5)` equals `floor(x + 0.5)` only for non-negative x; a threshold after `Int()` differs from one after rounding · fails if ignored: class flips and sum drift between modes · evidence: ev:bb07034b54, ev:4acfadc02d · eval: T-x1000, E-rs · form: P
- **R-5** · Block aggregation keeps partial edge blocks (pad, do not crop); a block with no valid cell stays NoData, not 0 · fails if ignored: lost edge cells; new zeros · evidence: ev:80d93800ae · eval: E-rs · form: P+C
- **R-6** · Where a step partitions values (kept, dropped, redistributed), assert the partition in the step from arrays already in memory and raise on violation; the runtime records sum and count of every write in `io.json` · fails if ignored: losses invisible until the final output · evidence: ev:a209e33e09, ev:392b4618a3, ev:c7ecdb0fb5, ev:9c97df9abd · eval: lux, T-branch · form: R+C
- **R-7** · A fused or optimised expression replaces a step chain only after an output-equality run; any optimisation re-runs parity, and a changed output is a decision, not a speed-up · fails if ignored: "faster" changes the answer · evidence: ev:500ebe8c4c, ev:d91965b64e, ev:c842a8999d · eval: T-perf · form: P+C
