# decisions (append-only)

## D-001 · Canton names derived in code (manual step m01) · 2026-10-13T23:57:42Z
```yaml
id: D-001
question: q01
kind: answer
answer: derive
by: user (interview)
verbatim: "Derive in code (Recommended)"
params: [p.canton_name_policy, p.canton_name_source_field, p.canton_name_strip_prefix, p.canton_name_exceptions, p.canton_name_field]
nodes: [m01_cantons_lux_edit, ds.cantons_lux_edit, ds.geoboundaries_lux_adm1]
changes: "m01_cantons_lux_edit scope keep_legacy -> migrate; ds.cantons_lux_edit is produced from geoBoundaries ADM1 by rule (strip 'Canton ' from shapeName + exception table) and verified against the hand-made 03_INPUT/cantons_lux_edit.gpkg"
supersedes: null
evidence: [NOTES.txt:15-19, "inventory:03_INPUT/cantons_lux_edit.gpkg"]
```

## D-002 · Unmatched canton in the vpc join raises instead of being skipped · 2026-10-13T23:57:42Z
```yaml
id: D-002
question: null
kind: agent_decision
answer: "raise on any canton without a value-per-capita row (legacy printed '!! no vpc' and left vpc/tot/factor NULL)"
by: agent
params: [p.vpc_join_key, p.vpc_table]
nodes: [s04_distribute_value, ds.04_canton_factor]
changes: "latent legacy swallow at 02_SCRIPTS/04_distribute_value.py:61-63 replaced by a raise (F6 V-3, F10 P-1); measured effect on the reference data: 0 (12/12 cantons match after strip, inventory join_coverage)"
supersedes: null
evidence: ["02_SCRIPTS/04_distribute_value.py:61", "inventory:join_coverage"]
```

## D-003 · Confirm all: inferred values, scope, parity scope and compare settings · 2026-10-13T23:59:05Z
```yaml
id: D-003
question: q02
kind: confirm_all
answer: accept
by: user (interview)
verbatim: "Accept all (Recommended)"
params: [p.canton_name_source_field, p.canton_name_strip_prefix, p.canton_name_exceptions, p.outline_dissolve, p.pop_clip_rule, p.landuse_fix_method, p.landuse_area_method, p.pop_res_nodata_rule, p.zonal_rule, p.res_min_area_m2]
nodes: [s03b_zonal_arcpy_old, ds.geoboundaries_lux_adm2, ds.osm_landuse_lux]
changes: "inferred values confirmed; MIN_AREA kept for parity (tuned_in_sample); nominal 100 m vs real 0.000833 deg grid noted, parity follows data; s03b dropped (alternative_impl); ADM2 display-only; landuse classes other than residential out of scope; D-002 raise on unmatched join confirmed; parity scope = all 05_TEMP intermediates, 04_FINAL finals and cantons_lux_edit; compare settings set on datasets (rev 5)"
supersedes: null
evidence: ["graph.json#scope_facts", "params.yaml"]
```

## D-004 · Target platform: Databricks job files (not deployed) · 2026-10-13T23:59:20Z
```yaml
id: D-004
question: q03
kind: answer
answer: databricks
by: user (interview)
verbatim: "Databricks job files (Recommended)"
params: []
nodes: [s04_distribute_value]
changes: "params.yaml target.platform = databricks"
supersedes: null
evidence: ["answer:q03"]
```

## D-005 · area_m2 float-noise: tolerance records added by the user, migration continues · 2026-10-14T00:07:08Z
```yaml
id: D-005
question: null
kind: answer
answer: "user adds the proposed tolerance records to tolerances.yaml themselves; agent continues with s03/s04"
by: user (chat)
verbatim: "I'll add the records (Recommended)"
params: [p.landuse_area_method]
nodes: [s02_landuse_mask, ds.02_res_2169, ds.02_res_4326]
changes: "proposed records tol.02_res_2169.vector.attr.area_m2.v1 and tol.02_res_4326.vector.attr.area_m2.v1 (rtol 1e-10; correct spread 8.64e-13, nearest wrong 5.78e-8) are in migration/analysis/tolcal/tolerances.new.yaml for the user to paste; agent cannot edit tolerances.yaml (guard). Two repair attempts (QGIS shoelace, shifted shoelace) did not reach exact parity; code reverted to GEOS area."
supersedes: null
evidence: ["migration/analysis/tolcal/area_spread.py", "runs/r20261014T000317Z-e914df1a/verify/compare/ds.02_res_2169.json"]
```
