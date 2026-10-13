# Parameter evidence: what counts, by parameter type

Entry format: type · resolved by · inferred (state method + confidence) · unresolved → question when.
Evidence ids are opaque (`ev:…`); they resolve only in the private evidence map.

| Type | Resolved by | Inferred (method, confidence) | Unresolved → question when |
|---|---|---|---|
| Cell size, grid, snap | Explicit in code (`-tr`, `cellSize`, `snapRaster`, a template raster's extent/width/height passed to the tool) **or** the legacy output transform (`reference_metadata`) | Tool semantics for the exact tool version (`method: semantics`; cite where you learned it, never assume); a fitted half-cell offset (`fit.py`) | Outputs disagree among themselves, or no output exists and the code gives no grid (ev:0f5686fc0e, ev:0a6b49fd4f) |
| CRS of data, area, distance | Explicit `TARGET_CRS` / EPSG in code; the input header | The operation runs in the layer's CRS (layer units) | A distance or area in degrees, or "metres" in Web Mercator → a `legacy_bug` question (ev:53e3d0f505) |
| NoData | Explicit in code; output header (`reference_metadata`, `by_construction`) | The tool default for that version (record the version) | Header missing, or code and header differ |
| Resampling | Explicit method | Nearest on an identical grid is an identity copy: confirm by an equality check on the intermediate | Any non-nearest method on counts → `legacy_bug` (ev:03d7dc1a2e) |
| Thresholds | Literal **and** comparison operator (`>=` vs `>`) at the use site | Fitted only (`fit.py`, frozen candidates and hold-out) | No literal, set in a GUI, or the fit is not unique |
| Buffer distance and units | Literal + unit + CRS of the operation | Unit from the layer CRS | Unit ambiguous: geodesic vs planar (ev:a37f238a86, ev:11f692900b) |
| Join keys | Field named in code, values match on both sides after the code's normalisation | A rule derived from the inputs (`by_construction`) | Many-to-one or unmatched keys, or the key field was made by hand |
| Scaling / units | Literal + a units cell or note | `NUMERIC_SCALE_FACTOR` diagnostic → confirm | Scale only visible in outputs (ev:abee2dfbe4, ev:a2b0dfd134) |
| Table reads | Sheet, header row and row range in code; cached values (read formulas and values separately) | — | Formula cells without cached values feeding the computation (ev:b46251ae13) |
| Manual edits | The procedure, from the diff of a derived input against its source | — | **Always**, for the policy going forward (automate / read the edited file / bridge) |
| Branches, scripts | Notes or headers retire them; no reader in the run order | — | A live branch with no output in the reference, or a contradiction |
| Dtype, format, layer names | Explicit in code or the output header | Library default for that version | Header and code disagree |

Rules

- Comments, docstrings, names and notes are **claims**: re-derive each from code + output metadata. For the parity
  value, the source that produced the reference wins (code + output metadata). A losing source that states an intent
  is listed in `confirm_all` with `flags: [contradiction]`, asked only when parity cannot settle it.
- A value tuned by eye on part of the data ("eyeballed on two districts") is `flags: [tuned_in_sample]`: kept for
  parity, shown in `confirm_all`.
- Nominal vs real units ("100 m cells" vs a 3-arc-second grid): report both, never rename; parity follows the data.
- A hard-coded path, workspace or drive letter is never a parameter to ask about: inputs come from the inventory.
- Never ask about storage, output paths, error handling or cloud providers; the target platform is one question,
  last.
