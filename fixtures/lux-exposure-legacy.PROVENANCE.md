# lux-exposure-legacy: provenance (do not ship to the migration agent)

This file sits outside the fixture on purpose. The build helpers live next to it in
`lux-exposure-legacy.build/`.

## What the fixture is

A small, made-up "analyst's folder" that computes a generic residential exposure
distribution for Luxembourg using textbook dasymetric disaggregation:

- canton total value = canton population × value per capita (from the xlsx, in kEUR, ×1000 → EUR);
- that total is spread over 100 m (3 arc-second WorldPop) cells in proportion to cell population;
- only cells that are residential (OSM `landuse=residential`, ≥ 2500 m², rasterised all-touched)
  and have population ≥ 0.5 receive value.

There are no commercial or industrial weights and no integer scaling. Everything comes from public data, apart
from the value-per-capita table, which is synthetic.

## Data sources

The full table is in `~/demo-data/raw/MANIFEST.md`.

| Fixture file | Origin |
|---|---|
| `03_INPUT/lux_ppp_2020.tif` | WorldPop 2020 unconstrained ppp, CC BY 4.0. Byte copy of raw (`6a8f2e17…4a96`) |
| `03_INPUT/geoBoundaries-LUX-ADM1.geojson`, `-ADM2.geojson` | geoBoundaries gbOpen @ 9469f09, ODbL. Byte copies of raw |
| `03_INPUT/osm_landuse_lux.gpkg` | `ogr2ogr` of the Overpass OSM XML (snapshot 2026-10-01), multipolygons layer `landuse`, ODbL |
| `03_INPUT/cantons_lux_edit.gpkg` | ADM1 plus a `canton` field. **Simulates the analyst's manual attribute-table edit** described in NOTES.txt: drop the "Canton " prefix, and Esch-sur-Alzette → `Esch/Alzette`. Made with `ogr2ogr -sql` in `10_stage_inputs.sh` |
| `03_INPUT/value_per_capita.xlsx` | **SYNTHETIC.** Made-up, plausible kEUR/inhabitant values (139–214), written by `20_make_vpc_xlsx.py`. It does not come from any real or proprietary source. The mess is deliberate: a title row, a units cell (`kEUR / inh.`), a blank row, the header on row 4, `"Wiltz "` with a trailing space, and an `=AVERAGE()` check row below the data |
| `01_QGIS/lux_exposure.qgz` | Written by `30_make_qgz.py` (PyQGIS) after the run. Relative paths, project CRS EPSG:2169 |
| `02_SCRIPTS/*.py`, `NOTES.txt` | Hand-written for this fixture. `03b_zonal_arcpy_OLD.py` was never run (ArcPy is not installed) |

## Environment

- Host: Ubuntu 24.04.4 LTS, user `migrate`, micromamba 2.9.0, env `qgis` (conda-forge)
- QGIS 4.2.3 'Belém do Pará' (Qt 6.11.2, Python 3.14.8)
- GDAL 3.13.3 "Iowa City", PROJ 9.9.0, GEOS 3.14.1
- numpy 2.5.4, openpyxl 3.1.5. The checks only used geopandas 1.2.0, rasterio 1.5.2, shapely 2.2.0 and pyproj 3.8.0

## Exact commands

```bash
B=~/axis-migrate-dev/fixtures/lux-exposure-legacy.build
export MAMBA_ROOT_PREFIX=~/micromamba
MR="$HOME/.local/bin/micromamba run -n qgis"
$MR bash $B/10_stage_inputs.sh                                   # 03_INPUT
$MR python $B/20_make_vpc_xlsx.py ~/axis-migrate-dev/fixtures/lux-exposure-legacy/03_INPUT/value_per_capita.xlsx
$B/40_run_legacy.sh        # = for s in 01..04: (cd fixture; micromamba run -n qgis python 02_SCRIPTS/$s.py)
$MR python $B/30_make_qgz.py ~/axis-migrate-dev/fixtures/lux-exposure-legacy
git -C ~/axis-migrate-dev checkout -- fixtures/lux-exposure-legacy/03_INPUT/osm_landuse_lux.gpkg  # undo 30_make_qgz's header touch
$B/41_hash_outputs.sh                                            # sha256 of 04_FINAL/** and 05_TEMP/**
$MR python $B/42_compare_runs.py <runA>/04_FINAL <runB>/04_FINAL  # content diff for gpkg/xlsx (same for 05_TEMP)
$MR python $B/50_conservation_check.py ~/axis-migrate-dev/fixtures/lux-exposure-legacy
$MR python $B/60_inventory.py ~/axis-migrate-dev/fixtures/lux-exposure-legacy  # per-step table below
```

The legacy scripts hard-code `BASE = "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy"`
and `QGIS_PREFIX`, so they only run at that path.

## Folder layout and per-step outputs

Each numbered script writes to a predictable place: intermediates go to `05_TEMP/` with the
step number as a filename prefix, and finals go to `04_FINAL/`. Nothing is overwritten by a later
step. Step 04 copies `03_canton_pop.gpkg` to `04_canton_factor.gpkg` before adding fields. QGIS also
leaves `.tif.aux.xml` sidecars (PAM statistics) next to the rasters. They are reproducible and kept,
but not listed below.

| Step | Script | Writes |
|---|---|---|
| 01 | `01_prepare_population.py` | `05_TEMP/01_lux_outline.gpkg` (dissolved cantons), `05_TEMP/01_pop_lux.tif` (WorldPop clipped to the outline) |
| 02 | `02_landuse_mask.py` | `05_TEMP/02_res_2169.gpkg` (residential, fixed, `area_m2` ≥ 2500, EPSG:2169), `05_TEMP/02_res_4326.gpkg` (same, back in 4326), `05_TEMP/02_res_mask.tif` (1 = residential, all-touched) |
| 03 | `03_zonal_population.py` | `05_TEMP/03_pop_res.tif` (weights = pop·[pop ≥ 0.5]·mask), `05_TEMP/03_canton_pop.gpkg` (zonal table: `pop_count`, `pop_sum`, `res_count`, `res_sum` per canton) |
| 04 | `04_distribute_value.py` | `05_TEMP/04_canton_factor.gpkg` (per-canton `vpc_eur`, `tot_value`, `factor` = tot_value / res_sum), `05_TEMP/04_factor.tif` (factor rasterised, Float64), then `04_FINAL/lux_res_exposure_2020.tif`, `exposure_by_canton.gpkg/.csv/.xlsx` |

### Inventory (final committed run; generated by `60_inventory.py`)

GPKG and XLSX hashes change on every run because of embedded write timestamps (see Check 1). Compare
them by content. The raster and CSV hashes are stable.

| step | file | sha256 | CRS | grid / schema |
|---|---|---|---|---|
| 01 | `05_TEMP/01_lux_outline.gpkg` | `f1f37a88e2aaad745ee1a95ea2592f5c9b14b5e56af1c9782919dfc0a3fa1632` | EPSG:4326 | layer `01_lux_outline` (1 × MultiPolygon): shapeName:object, shapeISO:object, shapeID:object, canton:object |
| 01 | `05_TEMP/01_pop_lux.tif` | `e1d622de159d99b2625e53e7e0423ff3fed3a35d54f570a0772ea9ee0b47732b` | EPSG:4326 | 940×880, float32, nodata=-99999.0, transform=(0.0008333333297872345, 0.0, 5.744583325, 0.0, -0.0008333333295454553, 50.18125); identical to WorldPop transform; compress=lzw |
| 02 | `05_TEMP/02_res_2169.gpkg` | `8bc481ec159f91734b59689b5c648863e87ceff8852e862aba38c1de8c11b52b` | EPSG:2169 | layer `02_res_2169` (1512 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object, area_m2:float64 |
| 02 | `05_TEMP/02_res_4326.gpkg` | `6c36e38c7f5689fa166ba35b003c354085557804b1e9787a9b36c23a6d33b3cc` | EPSG:4326 | layer `02_res_4326` (1512 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object, area_m2:float64 |
| 02 | `05_TEMP/02_res_mask.tif` | `cb16d48a8514bf3a062a13d1cac4a978e1efcc0a1e0abac040a78e6bc177b0b3` | EPSG:4326 | 940×880, uint8, nodata=0.0, transform=(0.0008333333297872345, 0.0, 5.744583325, 0.0, -0.0008333333295454553, 50.18125); identical to WorldPop transform; compress=lzw |
| 03 | `05_TEMP/03_canton_pop.gpkg` | `b689f7f3441d6c68ac9a760cfad7c685abf32868443522d72c51cc08612873c5` | EPSG:4326 | layer `03_canton_pop` (12 × Polygon): shapeName:object, shapeISO:object, shapeID:object, canton:object, pop_count:float64, pop_sum:float64, res_count:float64, res_sum:float64 |
| 03 | `05_TEMP/03_pop_res.tif` | `a6e7158220ee0597777837906595cbf67a1749f051ef918e896f0d984fb5c593` | EPSG:4326 | 940×880, float32, nodata=-3.4028234663852886e+38, transform=(0.0008333333297872345, 0.0, 5.744583325, 0.0, -0.0008333333295454553, 50.18125); identical to WorldPop transform; compress=None |
| 04 | `05_TEMP/04_canton_factor.gpkg` | `fe95dcfc0ede3c508e2a06915661b9f5436a52b71a79dee8d94caa780b172834` | EPSG:4326 | layer `canton_factor` (12 × Polygon): shapeName:object, shapeISO:object, shapeID:object, canton:object, pop_count:float64, pop_sum:float64, res_count:float64, res_sum:float64, vpc_eur:float64, tot_value:float64, factor:float64 |
| 04 | `05_TEMP/04_factor.tif` | `9b2c5a1e5a029a760f905d0f96fb531869b5e62c057bd2075ce1b3aa3a8e3588` | EPSG:4326 | 940×880, float64, nodata=0.0, transform=(0.0008333333297872345, 0.0, 5.744583325, 0.0, -0.0008333333295454553, 50.18125); identical to WorldPop transform; compress=lzw |
| 04 (final) | `04_FINAL/exposure_by_canton.csv` | `f7e4247d5722a9018c315479c741479dca8186e7803b256b5cc883df1656eb04` | – | header: canton,pop_total,pop_residential,value_per_capita_eur,total_value_eur,exposure_raster_sum_eur,diff_eur; 12 rows |
| 04 (final) | `04_FINAL/exposure_by_canton.gpkg` | `0028124e9fd999527b5aa4946719f7c89f48f6137d3d620bcf9e62a7cc9c03dc` | EPSG:4326 | layer `exposure_by_canton` (12 × Polygon): shapeName:object, shapeISO:object, shapeID:object, canton:object, pop_count:float64, pop_sum:float64, res_count:float64, res_sum:float64, vpc_eur:float64, tot_value:float64, factor:float64, exp_sum:float64 |
| 04 (final) | `04_FINAL/exposure_by_canton.xlsx` | `e9fc464121d401363660df2875e4a3821326c66f860115982db6fd1cd9106078` | – | sheet `by_canton`, 15 rows × 7 cols (header + 12 cantons + blank + TOTAL) |
| 04 (final) | `04_FINAL/lux_res_exposure_2020.tif` | `b364ee901a73c4e25b5554569946900f8f443be171de7e1dde6907252e2d6b87` | EPSG:4326 | 940×880, float32, nodata=-9999.0, transform=(0.0008333333297872345, 0.0, 5.744583325, 0.0, -0.0008333333295454553, 50.18125); identical to WorldPop transform; compress=lzw |

01_pop_lux vs raw WorldPop on 456764 valid cells: values identical = True; cells set to nodata by the clip = 3729

All rasters were also checked with `rasterio`: `transform == WorldPop transform` and `shape == (880, 940)`
holds exactly, bit for bit, for every intermediate and final raster.

### Grid alignment: how QGIS enforces it here

QGIS has no global "snap raster" setting; ArcGIS does, via `arcpy.env.snapRaster` in `03b_zonal_arcpy_OLD.py`.
In QGIS, alignment holds only because of how each tool is called. The single reference grid is the
WorldPop grid: EPSG:4326, 940 × 880, origin (5.744583325, 50.18125), pixel
(0.0008333333297872345, −0.0008333333295454553).

- **01 clip** (`gdal:cliprasterbymasklayer`): runs with `CROP_TO_CUTLINE=False` and `KEEP_RESOLUTION=False`, so
  QGIS passes no `-tr`, `-tap` or `-te` and gdalwarp keeps the source grid. **Trap** (measured with `qgis_process`
  against `01_lux_outline.gpkg`):
  - `KEEP_RESOLUTION=True` adds `-tr <res> -tap`, which snaps to multiples of the pixel size from (0, 0). The raster
    stays 940 × 880 but **moves by (+0.5, −0.4997) cells**, about 30 m east and 46 m north.
  - `CROP_TO_CUTLINE=True` alone gives 953 × 881, a whole-cell offset of (−10, −1), still on the grid.
  - Both options together give 956 × 883 at (−11.5, −2.4997): shifted and resized.

  In all three cases the population total is unchanged (588,219.87): the nearest-neighbour warp copies the values
  1:1. The danger is georeferencing. Every downstream raster (mask, zones) is built on that grid, so cell values
  sit about half a cell away from where WorldPop puts them. The analyst left a comment about this in the script.
- **02 and 04 rasterize** (`gdal:rasterize`): `UNITS=0` (pixels), `WIDTH`/`HEIGHT` taken from the pop raster,
  and `EXTENT` set to the pop raster extent formatted with `%.12f`. The resulting transform is bit-identical (verified).
  **Trap:** `UNITS=1` with a typed resolution such as `0.000833333`, or a different extent, gives a rounded width/height
  or a shifted origin: a one-pixel shift or an off-by-one size.
- **03 raster calculator** (`native:rastercalc`): `EXTENT`, `CELL_SIZE` and `CRS` are left empty, so the grid
  comes from the input layers, which are already identical.
- **04 gdal_calc** (`gdal:rastercalculator`, `EXTENT_OPT=0`): gdal_calc only checks that array sizes match, **not
  geotransforms**. A shifted factor raster of the same size would be multiplied cell by cell without any error, so
  a lost snap fails silently here.
- **Zones:** native zonal statistics and gdal_rasterize both assign a cell to a canton by its **centre**. The rasterio
  check in Check 2 uses the same rule and gets identical sums.

### Resampling of population (a count)

There is **none**. The population raster is never resampled. The only warp is step 01's clip on the identical grid,
which is a 1:1 identity copy: gdalwarp's default nearest neighbour on a matching grid. `01_pop_lux.tif` equals the
raw WorldPop values on all 456,764 valid cells (verified with `np.array_equal`). 3,729 cells outside the
dissolved canton outline become NoData. No bilinear, average or sum resampling is used anywhere. Reprojection to
EPSG:2169 only happens to the vector landuse polygons, to measure area. Population sums are therefore exact
(588,219.87 on the grid inside the cantons).

## Check 1: reproducibility from a clean copy

Run A started with `04_FINAL` and `05_TEMP` empty. I then moved the whole fixture aside, recreated it at the same
path from `01_QGIS`, `02_SCRIPTS`, `03_INPUT` and `NOTES.txt` only, and ran it again (run B). I did this both
before and after the move to `05_TEMP/`, with the same result.

- **Byte-identical across runs:** every GeoTIFF and `.tif.aux.xml` (`01_pop_lux`, `02_res_mask`, `03_pop_res`,
  `04_factor`, `lux_res_exposure_2020`) and the CSV. The raster hashes also did not change when the intermediates were renamed.
- **Bytes differ, content identical:** every GPKG and the XLSX.
  - In the GPKGs, all tables are row-for-row identical, geometry blobs included. The only difference is
    `gpkg_contents.last_change`, the write timestamp.
  - In the XLSX, the cell values are identical and only `docProps/core.xml` differs (the openpyxl timestamps).
- **Result:** deterministic apart from format timestamps. Compare GPKG and XLSX by content.
- **Input hashes:** opening an input GPKG in PyQGIS with a subset filter (the `.qgz` builder) bumped its SQLite
  change counter: 2 header bytes changed, data unchanged. I restored the committed copy. Running the legacy scripts
  does not touch the inputs (checked). Hash GPKG inputs by content as well.

## Check 2: conservation (`50_conservation_check.py`)

The check is independent of QGIS. It rasterises the cantons with rasterio (cell-centre rule) onto the exposure grid
and sums in float64.

| | max abs error | max rel error |
|---|---|---|
| exposure raster sum per canton vs canton `tot_value` | 4.40 EUR (Esch/Alzette) | 1.57e-09 (Echternach) |
| QGIS zonal `exp_sum` vs `tot_value` | 4.40 EUR | 1.57e-09 |
| canton total recomputed independently (numpy pop sum × xlsx vpc) vs QGIS `tot_value` | 0 | 0 |
| national: raster sum vs Σ tot_value | 5.40 EUR | 5.2e-11 |

There are no exposure cells outside a canton. **PASS** at a relative tolerance of 1e-6. The residual error comes from
the Float32 output raster: QGIS and numpy zone assignment agree exactly.

Canton totals (EUR): Luxembourg 38.91 bn, Esch/Alzette 26.36 bn, Capellen 8.89 bn, Mersch 5.33 bn,
Grevenmacher 4.90 bn, Diekirch 4.57 bn, Remich 3.82 bn, Redange 2.99 bn, Echternach 2.76 bn,
Clervaux 2.59 bn, Wiltz 2.33 bn, Vianden 0.74 bn. National population on the grid is 588,220. Population on
residential cells with pop ≥ 0.5 is 273,489 (OSM residential coverage of LU is partial; the disaggregation
redistributes the full canton total to those cells, so totals are conserved).

## Parity traps a faithful migration must reproduce (for the demo team)

1. **Grid.** Keep the WorldPop grid. In QGIS "clip raster by mask", `KEEP_RESOLUTION` adds `-tr … -tap`
   (a half-cell shift) and `CROP_TO_CUTLINE` changes the extent. Population totals survive either way, but the
   cells move. The script turns both off, so the clip only sets cells outside the dissolved cantons to NoData (−99999).
2. **Residential polygons.** Run `fixgeometries`, then compute **planar area in EPSG:2169**, keep features with
   `landuse='residential'` and area ≥ 2500 m², reproject back to EPSG:4326, and rasterise with `-at` (all touched)
   onto the pop grid. `NODATA` was left unset, and in QGIS 4 that writes NoData=0 into the mask.
3. **Weights.** `native:rastercalc` evaluates `(pop>=0.5)*pop*mask`. Because the mask has NoData=0, `pop_res` is
   NoData (−3.4e38) outside residential cells, and so is the exposure (−9999).
4. **Zones.** Both zonal statistics and rasterize assign a cell to a canton by its centre.
5. **Value table.** Read xlsx rows 5–16, apply `.strip()` to names, and multiply by 1000 (kEUR).
6. **Join key.** The join uses the hand-made `canton` field in `cantons_lux_edit.gpkg`, not geoBoundaries' `shapeName`.
7. **Output.** The exposure raster is Float32 (GDAL calc `RTYPE=5`) and the factor raster is Float64.

## Deviations from the brief

- **Grid and CRS.** I kept the native WorldPop 3″ grid (≈ 60 m × 93 m at 49.6° N, "100 m" by WorldPop's naming)
  instead of warping to EPSG:2169 at 100 m. QGIS 4.2's `gdal:warpreproject` has no `sum` resampling, and
  every other resampling method breaks population totals.
- **File locations.** `value_per_capita.xlsx` lives in `03_INPUT/`, and the intermediates are in `05_TEMP/` (moved there
  from `04_FINAL/tmp/` after the follow-up request; see the step table above).
- **OSM classes.** The OSM extract keeps all four landuse classes, as the brief asked, but the method only uses residential.
