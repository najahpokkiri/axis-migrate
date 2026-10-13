# F6 · Vector overlay and joins

Open when: spatial join, select by location, near, overlay, clip, dissolve, attribute join (`JoinField`, `AddJoin`,
VLOOKUP, `merge`), or any key-based lookup. Row counts and key sets are compared before any attribute.

Enforced elsewhere: `compare.py` reports `ROW_COUNT_MISMATCH`, `FEATURE_COUNT_MISMATCH`, `KEY_SET_MISSING`,
`KEY_SET_EXTRA`, `KEY_DUPLICATE` and `NULL_PATTERN_MISMATCH` on the declared key; nothing in the scanner checks join
options, so these rules are yours.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **V-1** · ArcGIS `SpatialJoin` defaults to ONE_TO_ONE + KEEP_ALL + INTERSECT; `gpd.sjoin` defaults to an inner, one-to-many join: pass `how=` and `predicate=` (not `op=`), then aggregate per left index; for a QGIS join-by-location read the call's own options and check the row count against the legacy output · fails if ignored: unmatched rows dropped or matched rows duplicated · evidence: ev:395095252c, ev:278c4ab6d2, ev:8e773a0a88 · eval: T-join, E-sj · form: P+C
- **V-2** · Attribute joins (`JoinField`, `AddJoin`, lookups): `merge(how="left", validate="many_to_one")` on keys normalised exactly as the legacy matched them (dtype, whitespace, case); a bare `merge` is inner; `join(on=)` joins against the right frame's index; `rename({...})` without `columns=` renames the index · fails if ignored: rows silently dropped; silent no-ops · evidence: ev:67ec4c2376, ev:a2678f9a2b, ev:94ece131ab, ev:3dbe2a2199 · eval: T-join, E-xl · form: P+C
- **V-3** · After any join, assert the key sets: unmatched keys on either side are a finding to report, never a value to fill · fails if ignored: zero matches become zero counts · evidence: ev:94ece131ab, ev:278c4ab6d2 · eval: T-join, T-shift · form: P+C
- **V-4** · Coordinate-string keys become integer grid indices from the shared transform; a nearest-neighbour join that "fixes" non-matches hides misalignment · fails if ignored: half-cell offsets give 0 matches · evidence: ev:94ece131ab · eval: T-shift · form: P
- **V-5** · `SelectLayerByLocation` is a filter: output rows are a subset of the input with no duplicates (`sjoin` then `index.isin`, or `sindex.query`); `Near` is `sjoin_nearest(max_distance=, distance_col=)` in a metric CRS, not `distance()` loops; ArcGIS writes -1 where nothing is in range (sjoin_nearest gives NaN); ties return several rows · fails if ignored: duplicated rows; wrong distances · evidence: ev:fe485ac70d, ev:70a0ba0e39, ev:278c4ab6d2, ev:0d87efda1b · eval: E-sj · form: P+C
- **V-6** · `dissolve(dropna=False)` keeps NULL groups; `clip(keep_geom_type=True)`; overlay suffixes clashing fields `_1`/`_2`; an FID of -1 becomes NaN · fails if ignored: NULL groups vanish; mixed geometry types; broken field references · evidence: ev:c188e769f1, ev:3402caf795, ev:ceae6bec57 · eval: E-buf · form: P
- **V-7** · Replicate the filters the legacy relied on at the source (geometry type, class or tag filter, definition query, a layer's subset string); deduplicate by feature id, never by geometry · fails if ignored: extra features buffered or burned; duplicates kept · evidence: ev:5b7d785ae5, ev:89974e819f · eval: lux, E-buf · form: P+C
