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
$B/41_hash_outputs.sh                                            # sha256 of 04_FINAL/**
$MR python $B/42_compare_runs.py <runA>/04_FINAL <runB>/04_FINAL  # content diff for gpkg/xlsx
$MR python $B/50_conservation_check.py ~/axis-migrate-dev/fixtures/lux-exposure-legacy
```

The legacy scripts hard-code `BASE = "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy"`
and `QGIS_PREFIX`, so they only run at that path.

## Reference outputs (canonical run B)

| File | Bytes | sha256 |
|---|---|---|
| `04_FINAL/lux_res_exposure_2020.tif` | 361231 | `b364ee901a73c4e25b5554569946900f8f443be171de7e1dde6907252e2d6b87` |
| `04_FINAL/exposure_by_canton.csv` | 1435 | `f7e4247d5722a9018c315479c741479dca8186e7803b256b5cc883df1656eb04` |
| `04_FINAL/exposure_by_canton.gpkg` | 1245184 | `598ffce71921f669c8745bd8c69c6b10c26a737dd5836fd6e15327cabdc94824` (has a timestamp, see below) |
| `04_FINAL/exposure_by_canton.xlsx` | 6067 | `89aae831c33cb41056d1f1f5afbea47cb002ac771d47133b92aaf5dfaacda753` (has a timestamp, see below) |

The exposure GeoTIFF is Float32, LZW, 940 × 880 on the native WorldPop grid (EPSG:4326, origin
5.744583325, 50.18125; pixel 0.000833333329787 × −0.000833333329545). Values are EUR per cell. NoData
is −9999 outside residential cells: 6.34 % of cells are valid, the maximum is 24,869,248 EUR, and the national
total is 104,183,551,833 EUR. `04_FINAL/tmp/` intermediates are kept on purpose, as part of the mess.

## Check 1: reproducibility from a clean copy

Run A started from an empty `04_FINAL`. I then moved the whole fixture aside and recreated it at the same
path from `01_QGIS`, `02_SCRIPTS`, `03_INPUT` and `NOTES.txt` only, with an empty `04_FINAL`, and ran it again (run B).

- **Byte-identical:** every GeoTIFF (`lux_res_exposure_2020.tif` and the `tmp/` rasters `pop_lux`,
  `res_mask`, `pop_res`, `factor`), every `.tif.aux.xml`, and the CSV.
- **Bytes differ, content identical:** all GPKGs (`exposure_by_canton.gpkg` and the `tmp/` GPKGs) and the XLSX.
  - In the GPKGs, every table is row-for-row identical, geometry blobs included. The only difference is
    `gpkg_contents.last_change`, the write timestamp GDAL/QGIS stamps on each write.
  - In the XLSX, the cell values are identical and the only differing zip member is `docProps/core.xml`
    (the openpyxl created/modified timestamps).
- **Result:** deterministic apart from file-format timestamps. Parity tests should compare GPKG and XLSX by content, not by hash.

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

1. **Grid.** Keep the WorldPop grid. QGIS "clip raster by mask" with `KEEP_RESOLUTION` or `CROP_TO_CUTLINE`
   adds `-tr … -tap` or a crop, shifts the grid by half a cell and loses about 5k people. The script turns both off,
   so the clip only sets cells outside the dissolved cantons to NoData (−99999).
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
- **File locations.** `value_per_capita.xlsx` lives in `03_INPUT/`, and the intermediates are in `04_FINAL/tmp/`.
- **OSM classes.** The OSM extract keeps all four landuse classes, as the brief asked, but the method only uses residential.
