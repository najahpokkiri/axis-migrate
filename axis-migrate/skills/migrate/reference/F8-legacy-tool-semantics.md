# F8 · Legacy tool semantics (ArcPy, PyQGIS)

Open when: reading any ArcPy or PyQGIS script, QGIS model or project before writing its plan. The parity target is the
legacy run's own files: the code that produced them is the spec; comments and docs are claims to check.

Gap, stated honestly: there is no curated table of PyQGIS processing defaults here. Where a QGIS call omits a
parameter, do not assume ArcGIS, GDAL command-line or Shapely defaults: take the effective value from the legacy
output (grid, NoData, dtype, extent, row count) and verify it with the step compare; QGIS defaults vary by version.

Enforced elsewhere: scanner S14 blocks `arcpy`, `qgis` and `processing` imports in pipeline code (except `keep_legacy`
steps); S01/S02 block swallowed exceptions.

Format: `ID · rule · fails if ignored · evidence · eval · form` (P prose, S scanner, C compare, R runtime ctx).

- **L-1** · Every `arcpy.env.*` setting, `EnvManager` block and tool-level env (including mid-script changes that leak into later tools), and every QGIS per-call extent, resolution or CRS option, is a params.yaml entry and part of the step's grid · fails if ignored: outputs differ in CRS, extent or cell size with no error · evidence: ev:89e0818fc8, ev:29f3ac4824, ev:6324c75683, ev:59dc29201b · eval: T-shift · form: P
- **L-2** · `except ExecuteError: print(...)` wrappers exit 0 after a failed tool; never port the swallow · fails if ignored: green run with missing outputs · evidence: ev:7d13e44a84, ev:7c4b83a137 · eval: N-miss · form: S (S01, S02)
- **L-3** · `Exists`-skip caches are hidden state (they may check another path, leave variables unbound or mask missing config); port the step as pure computation that always runs · fails if ignored: works only on the author's disk · evidence: ev:458c7b3422, ev:373474621e, ev:2c3d0203b9 · eval: N-miss · form: P
- **L-4** · Comments, docstrings, migration docs and file names are claims: re-derive each from a legacy file:line (a doc can describe a scale step the code never did) · fails if ignored: invented behaviour reproduced · evidence: ev:112f29feb1, ev:a2b0dfd134, ev:abee2dfbe4, ev:ea30b2a1cf, ev:af4dffe6fc · eval: T-x1000, lux · form: P
- **L-5** · An alternative or "old" implementation of a step (another engine, a fork, a helper) is evidence of intent, not the spec: port the code that produced the references and record where the semantics differ · fails if ignored: NoData or threshold semantics from the wrong source · evidence: ev:f9aebb4918, ev:bf1853046a · eval: lux, T-decoy · form: P
- **L-6** · Every flag, `if` on configuration, class branch and input layer maps to a ported branch or a recorded `drop`; no branch disappears silently · fails if ignored: an unported branch whose rows or cells are missing · evidence: ev:b478ff69d5, ev:81d5f8480a, ev:4fc242d228 · eval: T-branch · form: P+C
- **L-7** · Memory layers (`TEMPORARY_OUTPUT`, `memory:`, in-memory workspaces) are sub-operations inside a step, never datasets; a step ends at a file the legacy persisted, which is what makes it verifiable · fails if ignored: steps with nothing to compare against · evidence: ev:20bc07f93a, ev:abc166d44f · eval: lux · form: P
- **L-8** · Legacy defects are ported faithfully, then measured and raised as a keep-or-fix question (interview); a silent fix fails parity and a silent keep hides the bug · fails if ignored: an unexplained FAIL, or a defect shipped unknowingly · evidence: ev:9e490eb81f, ev:67ec4c2376 · eval: T-bug, T-wm · form: P+C
