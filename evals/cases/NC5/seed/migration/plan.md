# Migration plan — lux-exposure-legacy

Rendered by `graph.py render-plan` from `graph.json` revision 8. The plan lives in `graph.json` (`steps[].plan`); edit it with `graph.py apply set_plan`, never here.

**Approval:** 0 of 5 migrate steps approved (5 have a plan).

| Step | Kind | Scope | Plan | Approved |
|---|---|---|---|---|
| `m01_cantons_lux_edit` | manual | migrate (automate_manual_step) | yes | no |
| `s01_prepare_population` | script | migrate | yes | no |
| `s02_landuse_mask` | script | migrate | yes | no |
| `s03_zonal_population` | script | migrate | yes | no |
| `s03b_zonal_arcpy_old` | script | drop (alternative_impl) | no | no |
| `s04_distribute_value` | script | migrate | yes | no |

## m01_cantons_lux_edit · Manual edit: cantons_lux_edit.gpkg from geoBoundaries-LUX-ADM1.geojson

Legacy: `NOTES.txt` lines 15-19

Inputs: `ds.geoboundaries_lux_adm1`  
Outputs: `ds.cantons_lux_edit`

Read geoBoundaries ADM1, add field canton = shapeName minus 'Canton ' with the exception table applied, write layer 'cantons' (EPSG:4326, 12 polygons). Replaces the hand edit (D-001).

**Legacy operations**

- `manual attribute edit (QGIS attribute table)` at `NOTES.txt:15-19` — {"exceptions": {"Esch-sur-Alzette": "Esch/Alzette"}, "source_field": "shapeName", "strip_prefix": "Canton "}

**Open-source operations**

- `geopandas read_file + str.removeprefix + dict map`: reproduces the observed rule (11/12) plus the one exception (TB-8, TB-5)

**Parameters** (values in params.yaml): `p.canton_name_policy`, `p.canton_name_source_field`, `p.canton_name_strip_prefix`, `p.canton_name_exceptions`, `p.canton_name_field`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.cantons_lux_edit` | vector | key canton | feature_count, key_set, attributes, geometry | exact |

**Risks**

- TB-8: a future ADM1 release adds a name that the rule does not map to the xlsx spelling (evidence: NOTES.txt:19; s04 raises on an unmatched canton (D-002))
- V-2: whitespace/case differences in names break the join (evidence: xlsx row 16 'Wiltz ' has a trailing space; s04 strips xlsx keys as legacy did)

**Isolation:** possible; legacy inputs: ds.geoboundaries_lux_adm1; missing: none

**Approved:** not yet

## s01_prepare_population · population raster clipped to LU

Legacy: `02_SCRIPTS/01_prepare_population.py` lines 30-54

Inputs: `ds.cantons_lux_edit`, `ds.lux_ppp_2020`  
Outputs: `ds.01_lux_outline`, `ds.01_pop_lux`

Dissolve cantons to one outline (first feature's attributes); mask WorldPop by the outline using cell centres on the unchanged WorldPop grid; NoData -99999, float32, LZW.

**Legacy operations**

- `native:dissolve` at `02_SCRIPTS/01_prepare_population.py:31-35` — {"FIELD": [], "SEPARATE_DISJOINT": false}
- `gdal:cliprasterbymasklayer` at `02_SCRIPTS/01_prepare_population.py:40-54` — {"creation": "COMPRESS=LZW", "dtype": "input (float32)", "grid": "ds.lux_ppp_2020 (crop_to_cutline false, keep_resolution false)", "nodata": -99999, "rule": "cell centre"}

**Open-source operations**

- `shapely union_all of canton geometries`: native:dissolve with no field = one feature (GV-4, V-6)
- `rasterio.features.geometry_mask(all_touched=False) on ctx.grid_of(ds.lux_ppp_2020)`: gdalwarp cutline keeps cells whose centre is inside; grid unchanged (G-1, RV-1, RV-2, N-1)

**Parameters** (values in params.yaml): `p.outline_dissolve`, `p.pop_clip_grid`, `p.pop_clip_rule`, `p.pop_lux_nodata`, `p.pop_lux_dtype`, `p.gtiff_creation_options`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.01_lux_outline` | vector | key shapeID | feature_count, attributes, geometry | exact |
| `ds.01_pop_lux` | raster | extensive | grid, nodata_mask, values, zone_totals | exact |

**Risks**

- G-3: a crop or keep-resolution option would mint a new origin (evidence: 01_prepare_population.py:38-39 comment; legacy output grid offset (0,0))
- N-1: source NoData patches inside the AOI (9179 cells) must stay NoData (evidence: MF-02)
- GV-4: dissolved outline vertex order may differ from QGIS; geometry compared by area difference in EPSG:2169 (evidence: 05_TEMP/01_lux_outline.gpkg 1 feature)

**Isolation:** possible; legacy inputs: ds.cantons_lux_edit, ds.lux_ppp_2020; missing: none

**Approved:** not yet

## s02_landuse_mask · residential mask from OSM landuse, on the pop grid

Legacy: `02_SCRIPTS/02_landuse_mask.py` lines 26-76

Inputs: `ds.01_pop_lux`, `ds.osm_landuse_lux`  
Outputs: `ds.02_res_2169`, `ds.02_res_4326`, `ds.02_res_mask`

Reproject landuse to EPSG:2169, make_valid(structure), planar area_m2, keep residential with area_m2 >= 2500 (02_res_2169), reproject to EPSG:4326 (02_res_4326), rasterize all-touched burn 1 init 0 on the 01_pop_lux grid, uint8 NoData 0 (02_res_mask).

**Legacy operations**

- `native:reprojectlayer` at `02_SCRIPTS/02_landuse_mask.py:27-30` — {"TARGET_CRS": "EPSG:2169"}
- `native:fixgeometries` at `02_SCRIPTS/02_landuse_mask.py:32-33` — {"METHOD": 1}
- `native:fieldcalculator` at `02_SCRIPTS/02_landuse_mask.py:35-42` — {"area_m2": "area($geometry) planar"}
- `native:extractbyexpression` at `02_SCRIPTS/02_landuse_mask.py:45-48` — {"expr": "landuse = 'residential' AND area_m2 >= 2500"}
- `native:reprojectlayer` at `02_SCRIPTS/02_landuse_mask.py:51-54` — {"TARGET_CRS": "EPSG:4326"}
- `gdal:rasterize` at `02_SCRIPTS/02_landuse_mask.py:61-76` — {"all_touched": true, "burn": 1, "dtype": "Byte", "grid": "ds.01_pop_lux extent/width/height", "init": 0, "nodata": "0 (header)"}

**Open-source operations**

- `GeoDataFrame.to_crs (pyproj)`: same CRS transforms (C-5)
- `shapely.make_valid(method='structure')`: QGIS fix geometries Structure method (GV-1, GV-2)
- `geometry.area in EPSG:2169 + boolean filter`: planar area($geometry) and the same >= literal (V-7, TB-7)
- `rasterio.features.rasterize(all_touched=True) on ctx.grid_of(ds.01_pop_lux)`: gdal_rasterize -at on the pop grid (RV-1, RV-4, G-1, N-5)

**Parameters** (values in params.yaml): `p.landuse_area_crs`, `p.landuse_fix_method`, `p.landuse_area_method`, `p.landuse_area_field`, `p.landuse_class_filter`, `p.res_min_area_m2`, `p.res_output_crs`, `p.res_mask_all_touched`, `p.res_mask_burn`, `p.res_mask_nodata`, `p.res_mask_dtype`, `p.gtiff_creation_options`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.02_res_2169` | vector | key osm_id+osm_way_id | feature_count, key_set, attributes, geometry | exact |
| `ds.02_res_4326` | vector | key osm_id+osm_way_id | feature_count, key_set, attributes, geometry | exact |
| `ds.02_res_mask` | raster | mask | grid, nodata_mask, values | exact |

**Risks**

- GV-1: GEOS make_valid structure vs the QGIS GEOS version may differ on the 13 invalid polygons (evidence: analysis/a04_landuse.py: 13 invalid)
- C-5: pyproj may pick a different 4326<->2169 transformation pipeline than QGIS (sub-metre shifts), moving all-touched edges and polygons near 2500 m2 (evidence: 56 residential polygons between 2400 and 2600 m2)
- RV-1: all-touched rasterization edge cells (evidence: 02_landuse_mask.py:75)
- N-3: mask NoData is 0: non-residential cells are NoData, not 0 (evidence: 02_res_mask header nodata 0)

**Isolation:** possible; legacy inputs: ds.01_pop_lux, ds.osm_landuse_lux; missing: none

**Approved:** not yet

## s03_zonal_population · population per canton: total, and the part sitting on residential cells

Legacy: `02_SCRIPTS/03_zonal_population.py` lines 27-55

Inputs: `ds.01_pop_lux`, `ds.02_res_mask`, `ds.cantons_lux_edit`  
Outputs: `ds.03_canton_pop`, `ds.03_pop_res`

pop_res = (pop >= 0.5) * pop * mask, NoData where pop or mask is NoData (float32, NoData -3.4028235e38, uncompressed). Per canton (cell centre): pop_count/pop_sum on 01_pop_lux and res_count/res_sum on pop_res, appended to the cantons layer (03_canton_pop).

**Legacy operations**

- `native:rastercalc` at `02_SCRIPTS/03_zonal_population.py:31-37` — {"dtype": "float32", "expr": "(pop >= 0.5) * pop * mask", "nodata": "any input NoData -> NoData"}
- `native:zonalstatisticsfb` at `02_SCRIPTS/03_zonal_population.py:40-46` — {"prefix": "pop_", "rule": "cell centre", "stats": ["count", "sum"]}
- `native:zonalstatisticsfb` at `02_SCRIPTS/03_zonal_population.py:49-55` — {"prefix": "res_", "rule": "cell centre", "stats": ["count", "sum"]}

**Open-source operations**

- `numpy masked arithmetic in float64, cast float32`: QGIS raster calculator semantics with NoData propagation (N-1, N-2, N-3, N-5)
- `rasterio.features.geometry_mask per canton (all_touched=False) + masked sum/count in float64`: zonalstatisticsfb cell-centre assignment (RV-3, RV-5, F4)

**Parameters** (values in params.yaml): `p.pop_min`, `p.pop_res_nodata_rule`, `p.pop_res_nodata`, `p.pop_res_dtype`, `p.zonal_rule`, `p.zonal_pop_stats`, `p.zonal_res_stats`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.03_pop_res` | raster | extensive | grid, nodata_mask, values, zone_totals | exact |
| `ds.03_canton_pop` | vector | key canton | feature_count, key_set, attributes, geometry | exact |

**Risks**

- N-2: NoData propagation: mask NoData (0) must give NoData, not 0 (evidence: 03_pop_res valid 52415 vs mask 54381)
- RV-3: zone assignment rule must be cell centre (evidence: legacy pop_sum over cantons = clipped total 588219.87)
- N-5: NoData value and dtype come from the header (-3.4028235e38 float32) (evidence: inventory:05_TEMP/03_pop_res.tif)

**Isolation:** possible; legacy inputs: ds.01_pop_lux, ds.02_res_mask, ds.cantons_lux_edit; missing: none

**Approved:** not yet

## s04_distribute_value · canton value = pop x value per capita, spread over residential cells by pop

Legacy: `02_SCRIPTS/04_distribute_value.py` lines 35-141

Inputs: `ds.03_canton_pop`, `ds.03_pop_res`, `ds.value_per_capita`  
Outputs: `ds.04_canton_factor`, `ds.04_factor`, `ds.exposure_by_canton_csv`, `ds.exposure_by_canton_gpkg`, `ds.exposure_by_canton_xlsx`, `ds.lux_res_exposure_2020`

Read vpc (sheet vpc rows 5-16, keys stripped, x1000 EUR); per canton tot_value = pop_sum * vpc_eur, factor = tot_value / res_sum (0 if no res); write 04_canton_factor; rasterize factor cell-centre (float64, init 0, NoData 0) -> 04_factor; exposure = pop_res * factor (float32, NoData -9999 where any input NoData) -> final tif; exp_sum per canton (cell centre) -> exposure_by_canton.gpkg; csv and xlsx sorted by canton with TOTAL row.

**Legacy operations**

- `openpyxl read` at `02_SCRIPTS/04_distribute_value.py:36-43` — {"rows": "5-16", "scale": 1000, "sheet": "vpc", "strip": true}
- `native:savefeatures + attribute updates` at `02_SCRIPTS/04_distribute_value.py:46-73` — {"fields": ["vpc_eur", "tot_value", "factor"], "layer": "canton_factor"}
- `gdal:rasterize` at `02_SCRIPTS/04_distribute_value.py:79-94` — {"all_touched": false, "dtype": "Float64", "field": "factor", "grid": "ds.03_pop_res", "init": 0, "nodata": "0 (header)"}
- `gdal:rastercalculator` at `02_SCRIPTS/04_distribute_value.py:97-106` — {"dtype": "Float32", "formula": "A*B", "nodata": -9999}
- `native:zonalstatisticsfb` at `02_SCRIPTS/04_distribute_value.py:109-115` — {"prefix": "exp_", "stats": ["sum"]}
- `csv + openpyxl write` at `02_SCRIPTS/04_distribute_value.py:117-141` — {"sheet": "by_canton", "sort": "canton", "total_row": true}

**Open-source operations**

- `ctx.open_workbook(data_only=True) + dict`: same rows, cached values, strip keys (TB-1, TB-2, V-2, V-3)
- `rasterio.features.rasterize(all_touched=False, fill=0, dtype float64) on ctx.grid_of(ds.03_pop_res)`: gdal_rasterize cell-centre of factor (RV-1, G-1)
- `numpy float64 product of masked arrays, cast float32`: gdal_calc A*B with NoData propagation (N-1, N-2, N-5)
- `geometry_mask per canton (cell centre) + float64 sum`: zonalstatisticsfb exp_sum (RV-3)
- `ctx.write_csv / ctx.save_workbook`: same table layout (TB-2, TB-6)

**Parameters** (values in params.yaml): `p.vpc_table`, `p.vpc_scale`, `p.vpc_join_key`, `p.no_res_factor`, `p.factor_raster`, `p.exposure_raster`, `p.exposure_zonal`, `p.report_table`, `p.gtiff_creation_options`

**Outputs checked**

| Dataset | Family | Measure | Checks | Tolerance ids |
|---|---|---|---|---|
| `ds.04_canton_factor` | vector | key canton | feature_count, key_set, attributes, geometry | exact |
| `ds.04_factor` | raster | intensive | grid, nodata_mask, values | exact |
| `ds.lux_res_exposure_2020` | raster | extensive | grid, nodata_mask, values, zone_totals | exact |
| `ds.exposure_by_canton_gpkg` | vector | key canton | feature_count, key_set, attributes, geometry | exact |
| `ds.exposure_by_canton_csv` | table | key canton | schema, rows, values | exact |
| `ds.exposure_by_canton_xlsx` | table | key canton | layout, rows, values, total_row | exact |

**Risks**

- R-3: kEUR -> EUR factor 1000 must be applied once (evidence: xlsx B2 units; 04_distribute_value.py:42)
- TB-1: B18 AVERAGE formula has no cached value; must stay outside the read range (evidence: OI-02)
- V-3: unmatched canton keys raise (D-002, KD-002) (evidence: 04_distribute_value.py:61-63)
- N-2: factor NoData is 0, so factor-0 cells become NoData in the final (evidence: 04_factor header nodata 0)
- TB-6: float32 storage of the final makes exp_sum differ from tot_value by a few EUR (legacy diff_eur up to 4.4); parity is against legacy values, not the identity (evidence: 04_FINAL/exposure_by_canton.csv diff_eur)

**Isolation:** possible; legacy inputs: ds.03_canton_pop, ds.03_pop_res, ds.value_per_capita; missing: none

**Approved:** not yet

## Not migrated

- `s03b_zonal_arcpy_old` · OLD - step 03 from when this ran in ArcGIS (Spatial Analyst licence).: drop (alternative_impl)
