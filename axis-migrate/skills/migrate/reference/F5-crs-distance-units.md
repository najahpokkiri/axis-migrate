# F5 · CRS, distance and units

Open when: buffer, distance, near, area, length, densify; any CRS literal, `to_crs`, `Transformer`, or a distance given
with a unit string. Every CRS and distance in a step is a parameter (`ctx.param`), never a literal.

Enforced elsewhere: scanner S17 warns on `to_crs(3857)` followed by buffer/distance/area/length in one function; S18 warns
on a `Transformer` without `always_xy=True`. `compare.py` measures geometry differences (symmetric difference, Hausdorff)
in `p.measure_crs`, never in degrees or EPSG:3857 (`GEOMETRY_SYMDIFF_EXCEEDED`, `GEOMETRY_HAUSDORFF_EXCEEDED`).

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **C-1** · ArcGIS linear-unit distances on geographic data are geodesic; geopandas uses the CRS's units; parse the legacy unit string ("500 Meters") into a value and unit in params.yaml · fails if ignored: buffers in degrees, or metres applied to degrees · evidence: ev:11f692900b, ev:640b6f806d, ev:a37f238a86 · eval: T-wm, E-buf · form: P+C
- **C-2** · Distances in a local metric CRS (`estimate_utm_crs()`, AEQD) or geodesically; never in EPSG:3857, Mollweide or any equal-area CRS; an equal-area CRS is for areas only · fails if ignored: buffers shrunk by the latitude scale factor, or anisotropic · evidence: ev:a364575003, ev:a37f238a86, ev:53e3d0f505, ev:7e5cedddf9, ev:08e6f34dca · eval: T-wm, E-buf · form: P+S+C (S17 warns)
- **C-3** · No degree-to-metre conversion constants; reproject instead · fails if ignored: ellipses instead of circles; latitude bias · evidence: ev:b600168be9, ev:08e6f34dca, ev:5affc9829e · eval: T-wm · form: P
- **C-4** · PyQGIS `native:buffer` takes the distance in the layer's units; a degree buffer (or a 3857 buffer) in the legacy is legacy behaviour: port it faithfully and raise a keep-or-fix question; never fix it silently · fails if ignored: the "fixed" output fails parity, or a bug is kept unknowingly · evidence: ev:9e490eb81f · eval: T-wm · form: P+C (`LEGACY_DEFECT_SUSPECTED` → NEEDS_DECISION)
- **C-5** · Axis order: x is longitude, y is latitude; build every `Transformer` with `always_xy=True` · fails if ignored: swapped coordinates far from the cause · evidence: ev:85e53944cb · eval: E-buf · form: S (S18 warns)
