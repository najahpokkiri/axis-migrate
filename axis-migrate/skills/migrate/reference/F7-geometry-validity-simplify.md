# F7 · Geometry validity, dissolve and simplify

Open when: repair or fix geometries, dissolve, union, simplify, smooth, or a buffer chain. Repair and simplification
change answers: they are part of the step's semantics, so their method and tolerances are parameters.

Enforced elsewhere: `compare.py` reports `GEOMETRY_TYPE_MISMATCH`, `GEOMETRY_INVALID`, `GEOMETRY_SYMDIFF_EXCEEDED`,
`GEOMETRY_HAUSDORFF_EXCEEDED` and the diagnostic `VERTEX_COUNT_DROP`, all measured in `p.measure_crs`.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **GV-1** · Repair with `make_valid`, then explode and keep only the geometry types the legacy output has; never `buffer(0)`; `make_valid(method=...)` exists from Shapely 2.1 and its methods give different results on self-intersections, so the method is a parameter chosen by comparing against the legacy's repaired output, not a default · fails if ignored: lost rings; GeometryCollections; extra or missing cells after rasterising · evidence: ev:2ff1393759, ev:4905cab548, ev:19347e7555 · eval: lux, E-buf · form: P+C
- **GV-2** · Log what repair did (invalid count, rows dropped, area before and after); dropping invalid rows is a decision recorded in decisions.md, not a fix · fails if ignored: silent area or count shifts · evidence: ev:96664c4581, ev:0ce1ede2f0 · eval: lux · form: P+C
- **GV-3** · Never simplify before buffering; a post-buffer simplify needs a tolerance far below the distance; the segment count per quarter circle comes from the legacy call (a parameter), because engine and version defaults differ · fails if ignored: triangular buffers; collapsed small features · evidence: ev:ac5899f9fe, ev:aa1fb03ccd · eval: T-simp · form: P+C
- **GV-4** · `coverage_union_all` only on valid, polygon-only, non-overlapping input (check `coverage_is_valid`); otherwise `union_all()` · fails if ignored: TopologyException, or a wrong union · evidence: ev:d09d710b86, ev:4905cab548 · eval: lux · form: P
- **GV-5** · Simplifying or generalising to save time is an output change (F2 R-7): it needs a decision and a passing compare, never a silent optimisation · fails if ignored: zone boundaries move; totals near borders drift · evidence: ev:d91965b64e, ev:ac5899f9fe, ev:aa1fb03ccd · eval: T-perf · form: C
