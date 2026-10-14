# Migration plan — s1-buffers-legacy

Rendered by `graph.py render-plan` from `graph.json` revision 6. The plan lives in `graph.json` (`steps[].plan`); edit it with `graph.py apply set_plan`, never here.

**Approval:** 4 of 4 migrate steps approved (4 have a plan).

| Step | Kind | Scope | Plan | Approved |
|---|---|---|---|---|
| `s01_prepare_inputs` | script | migrate | yes | yes, user via interview |
| `s02_buffer_landuse` | script | migrate | yes | yes, user via interview |
| `s03_clip_dissolve` | script | migrate | yes | yes, user via interview |
| `s04_area_per_commune` | script | migrate | yes | yes, user via interview |

## s01_prepare_inputs · study area (communes of canton Esch) + landuse, both in LUREF

Legacy: `02_SCRIPTS/01_prepare_inputs.py` lines 35-60

Inputs: `ds.geoboundaries_lux_adm2`, `ds.osm_landuse_lux`  
Outputs: `ds.01_communes_2169`, `ds.01_landuse_2169`

Study communes (name list) and the landuse polygons touching them, both in EPSG:2169; landuse repaired like Fix geometries (structure).

**Legacy operations**

- `native:extractbyexpression` at `02_SCRIPTS/01_prepare_inputs.py:35-38` — {"expression": "shapeName IN (14 names)"}
- `native:reprojectlayer` at `02_SCRIPTS/01_prepare_inputs.py:41-44` — {"target_crs": "EPSG:2169"}
- `native:reprojectlayer` at `02_SCRIPTS/01_prepare_inputs.py:47-50` — {"target_crs": "EPSG:2169"}
- `native:fixgeometries` at `02_SCRIPTS/01_prepare_inputs.py:53-54` — {"method": 1}
- `native:extractbylocation` at `02_SCRIPTS/01_prepare_inputs.py:56-60` — {"predicate": "intersect"}

**Open-source operations**

- `GeoDataFrame filter + to_crs`: attribute filter keeps file order (V-7)
- `shapely.make_valid(method='structure')`: QGIS fix geometries, structure method (GV-1)
- `STRtree.query(predicate='intersects')`: select by location is a filter (V-5)

**Parameters** (values in params.yaml): `p.study_communes`, `p.work_crs`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.01_communes_2169` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |
| `ds.01_landuse_2169` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |

**Risks**

- GV-1: repair method changes geometry (evidence: 02_SCRIPTS/01_prepare_inputs.py:53-54)

**Isolation:** possible; legacy inputs: none; missing: none

**Approved:** by user at 2026-10-13T23:52:03Z via interview

## s02_buffer_landuse · buffer zones round the landuse polygons

Legacy: `02_SCRIPTS/02_buffer_landuse.py` lines 37-64

Inputs: `ds.01_landuse_2169`  
Outputs: `ds.02_buf_industrial`, `ds.02_buf_other`, `ds.02_buffers`

Per-class buffers: industrial at p.buffer_dist_industrial_m, every other class at p.buffer_dist_other_m, in EPSG:2169, 5 segments, round caps/joins; merged industrial first.

**Legacy operations**

- `native:extractbyexpression` at `02_SCRIPTS/02_buffer_landuse.py:37-40` — {"branch": "if grp == 'industrial' (l.31) / else (l.34)"}
- `native:buffer` at `02_SCRIPTS/02_buffer_landuse.py:42-51` — {"dissolve": false, "distance": "500 | 200", "end_cap": "round", "join": "round", "segments": 5}
- `native:mergevectorlayers` at `02_SCRIPTS/02_buffer_landuse.py:55-58`
- `native:deletecolumn` at `02_SCRIPTS/02_buffer_landuse.py:61-64` — {"columns": ["fid", "layer", "path"]}

**Open-source operations**

- `shapely.buffer(quad_segs=5, cap_style=round, join_style=round)`: native:buffer SEGMENTS=5 is GEOS quadrant segments (C-2, GV-3)

**Parameters** (values in params.yaml): `p.industrial_class`, `p.buffer_dist_industrial_m`, `p.buffer_dist_other_m`, `p.buffer_segments`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.02_buf_industrial` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |
| `ds.02_buf_other` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |
| `ds.02_buffers` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |

**Risks**

- L-6: class branch: industrial distance differs (evidence: 02_SCRIPTS/02_buffer_landuse.py:31-36)
- C-2: buffer distance needs a metric CRS (evidence: NOTES.txt:23)

**Isolation:** possible; legacy inputs: ds.01_landuse_2169; missing: none

**Approved:** by user at 2026-10-13T23:52:03Z via interview

## s03_clip_dissolve · cut the buffers by commune and merge the overlaps per commune

Legacy: `02_SCRIPTS/03_clip_dissolve.py` lines 24-42

Inputs: `ds.01_communes_2169`, `ds.02_buffers`  
Outputs: `ds.03_buf_by_commune`, `ds.03_buf_x_commune`

Buffers intersected with the communes (one piece per pair), pieces dissolved per commune.

**Legacy operations**

- `native:intersection` at `02_SCRIPTS/03_clip_dissolve.py:25-31` — {"input_fields": ["osm_id", "osm_way_id", "landuse"], "overlay_fields": ["shapeName", "shapeID"]}
- `native:retainfields` at `02_SCRIPTS/03_clip_dissolve.py:33-36`
- `native:dissolve` at `02_SCRIPTS/03_clip_dissolve.py:38-42` — {"field": ["shapeID"]}

**Open-source operations**

- `shapely.intersection per STRtree pair, polygonal parts only`: QGIS intersection keeps the input geometry type (V-6)
- `shapely.union_all per commune`: dissolve by field (GV-4)

**Parameters** (values in params.yaml): `p.commune_key`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.03_buf_x_commune` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |
| `ds.03_buf_by_commune` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |

**Risks**

- : none

**Isolation:** possible; legacy inputs: ds.01_communes_2169, ds.02_buffers; missing: none

**Approved:** by user at 2026-10-13T23:52:03Z via interview

## s04_area_per_commune · buffer area per commune (km2) + share of the commune -> 04_FINAL

Legacy: `02_SCRIPTS/04_area_per_commune.py` lines 27-68

Inputs: `ds.01_communes_2169`, `ds.03_buf_by_commune`  
Outputs: `ds.buffer_area_by_commune_csv`, `ds.buffer_area_by_commune_gpkg`

Planar areas in km2, commune area joined on shapeID, share in percent; CSV rounded.

**Legacy operations**

- `native:fieldcalculator` at `02_SCRIPTS/04_area_per_commune.py:28-35` — {"formula": "area($geometry) / 1000000"}
- `native:joinattributestable` at `02_SCRIPTS/04_area_per_commune.py:46-55` — {"method": 1}
- `csv.writer` at `02_SCRIPTS/04_area_per_commune.py:68-73` — {"round": [3, 3, 1]}

**Open-source operations**

- `GeoSeries.area / p.area_unit_m2`: planar area in the layer CRS (C-2)

**Parameters** (values in params.yaml): `p.commune_key`, `p.area_unit_m2`, `p.csv_area_decimals`, `p.csv_share_decimals`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.buffer_area_by_commune_gpkg` | vector | None | crs, geometry_type, key_set, attributes, geometry | exact |
| `ds.buffer_area_by_commune_csv` | table | None | key_set, attributes | exact |

**Risks**

- : none

**Isolation:** possible; legacy inputs: ds.01_communes_2169, ds.03_buf_by_commune; missing: none

**Approved:** by user at 2026-10-13T23:52:03Z via interview
