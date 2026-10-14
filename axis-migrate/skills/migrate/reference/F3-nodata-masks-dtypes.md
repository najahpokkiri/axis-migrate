# F3 · NoData, masks and dtypes

Open when: raster algebra, `Con`/`SetNull`/`IsNull`, masks, `CellStatistics`, or any raster write.
NoData is data about validity: the valid-cell mask of every output is compared cell by cell, before values.

Enforced elsewhere: `compare.py` checks `NODATA_VALUE_MISMATCH`, `NODATA_MASK_MISMATCH` (XOR of valid masks) and
`DTYPE_MISMATCH`; `ctx.write_raster` refuses a write without explicit `dtype=` and `nodata=` (None must be passed
explicitly) and a lossy cast; scanner S08 blocks `fillna(<literal>)`, `nan_to_num` without a declared `nan=` and
similar fill literals that do not come from a parameter.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **N-1** · Read masked (`ctx.read_raster(ds)` returns a masked array by default) or turn the sentinel into NaN before any arithmetic; `nan_to_num` leaves -9999 untouched; a polygon mask's fill value fills only outside the polygon, inside the source NoData remains · fails if ignored: sentinels summed or scaled as data · evidence: ev:f2d51dc087, ev:d5b316ccab · eval: lux · form: P+S+C (S08)
- **N-2** · NoData propagation is part of parity: `Con` without a false branch gives NoData there; `SetNull` gives NoData, not 0; any arithmetic with a NoData input stays NoData unless the legacy said otherwise · fails if ignored: valid-mask mismatch; zonal sums and counts shift · evidence: ev:f88b39c165, ev:4a524f2fe6 · eval: lux · form: P+C
- **N-3** · Mask predicates exclude the mask's own NoData (255 or 65535 pass `> 0`): `(m > 0) & ~m.mask`; one NoData convention per mask family · fails if ignored: NoData cells kept as "in mask" · evidence: ev:fcd99e3d18, ev:4a524f2fe6, ev:7e6a0752ac · eval: lux · form: P+C
- **N-4** · `CellStatistics` SUM ignores NoData by default and aligns its inputs; `np.sum` over a stack does neither: align first (F1), use `nansum`, and set cells with no valid input to NoData · fails if ignored: value plus sentinel; shape crash · evidence: ev:b99d595c4d · eval: lux, E-rs · form: P
- **N-5** · Write profiles explicitly (dtype, nodata, driver; creation options as a parameter when the legacy set them); never copy a VRT's or an input's meta wholesale; the dtype and nodata of a legacy output come from that output's header (params.yaml `source: reference_metadata`), not from memory of a tool's default, which varies by version · fails if ignored: wrong dtype or NoData; huge or truncated values · evidence: ev:0f628690b8, ev:4acfadc02d, ev:5dea80d689 · eval: lux · form: R+C
- **N-6** · `Con(IsNull(m), a, b)` decides per cell on `m`, not on whether `b` is valid: inside the mask `b` wins even where `b` is NoData; mask-replace is not coalesce · fails if ignored: NoData treated as 0, or the reverse · evidence: ev:a02182f3bd, ev:fcd99e3d18 · eval: lux · form: P+C
