# s1-buffers-legacy (and s1L): provenance (do not ship to the migration agent)

This file sits outside the legacy folders on purpose (eval S1, `docs/design/10-evals.md` §3 rows T3, T4, T5, T5L).
The build scripts live in `evals/build/s1/`. Everything in `04_FINAL/` and `05_TEMP/` was written by **real QGIS
4.2.3** running the legacy scripts (R1 `legacy_executed`, D10-3); no repo Python wrote a reference.

## What the project is

A small, made-up "analyst's folder" for buffer zones round built-up land in the south of Luxembourg:
how much of each commune lies within 200 m of residential / commercial / retail landuse, or within 500 m of
industrial landuse. Study area: the 14 communes of canton Esch-sur-Alzette (names typed in the script; ADM2 has
no canton field). Steps, all PyQGIS standalone scripts run with `micromamba run -n qgis python`:

| Step | Script | QGIS algorithms (exact options) | Writes |
|---|---|---|---|
| 01 | `01_prepare_inputs.py` | `native:extractbyexpression` (`"shapeName" IN (14 names)`) → `native:reprojectlayer` EPSG:2169; landuse: `native:reprojectlayer` EPSG:2169 → `native:fixgeometries` METHOD 1 (structure) → `native:extractbylocation` PREDICATE [0] intersect, INTERSECT = the communes | `05_TEMP/01_communes_2169.gpkg`, `05_TEMP/01_landuse_2169.gpkg` |
| 02 | `02_buffer_landuse.py` | loop over `["industrial", "other"]` with an **if-branch** (l.31–36): industrial → `"landuse" = 'industrial'`, DISTANCE 500; else → `"landuse" <> 'industrial'`, DISTANCE 200; each `native:extractbyexpression` → `native:buffer` SEGMENTS 5, END_CAP_STYLE 0 (round), JOIN_STYLE 0 (round), MITER_LIMIT 2, DISSOLVE False; then `native:mergevectorlayers` (industrial first) → `native:deletecolumn` [fid, layer, path] | `05_TEMP/02_buf_industrial.gpkg`, `05_TEMP/02_buf_other.gpkg`, `05_TEMP/02_buffers.gpkg` |
| 03 | `03_clip_dissolve.py` | `native:intersection` (INPUT 02_buffers, OVERLAY 01_communes, INPUT_FIELDS [osm_id, osm_way_id, landuse], OVERLAY_FIELDS [shapeName, shapeID], prefix '') → `native:retainfields` [shapeName, shapeID] → `native:dissolve` FIELD [shapeID] | `05_TEMP/03_buf_x_commune.gpkg`, `05_TEMP/03_buf_by_commune.gpkg` |
| 04 | `04_area_per_commune.py` | `native:fieldcalculator` `area($geometry) / 1000000` (planar) on the dissolved buffers and on the communes → `native:joinattributestable` on shapeID (METHOD 1) → `native:fieldcalculator` `100 * "buf_km2" / "commune_km2"`; CSV by Python `csv.writer`, rounded 3/3/1 decimals | `04_FINAL/buffer_area_by_commune.gpkg`, `04_FINAL/buffer_area_by_commune.csv` |

**s1L** (`s1L-buffers-legacy`, for T5L) is identical except step 02: the landuse is reprojected to **EPSG:3857**
before the extracts (`02_buffer_landuse.py:29-33`), each buffer goes to memory and is reprojected back to EPSG:2169
before it is written (l.58–61), and NOTES.txt:23 says "buffers done in web mercator (EPSG:3857, metres)". This is the
legacy bug to keep or fix: at 49.5° N a 3857 "metre" is about 0.65 m, so the buffers are ~35 % narrower than intended.
Result: total buffer area 115.718 km² (s1L) vs 143.544 km² (s1) over 243.582 km² of communes.

The scripts hard-code `BASE = "/home/migrate/axis-migrate-dev/evals/cases/_bases/<name>-buffers-legacy"` (and
`QGIS_PREFIX`), so they only run at that path, and that path holds the complete reference outputs: the eval fence
must cover it (the trial user `axis-eval` cannot read `/home/migrate`).

## Data sources

Full table in `~/demo-data/raw/MANIFEST.md`. All public.

| File | Origin |
|---|---|
| `03_INPUT/geoBoundaries-LUX-ADM2.geojson` | geoBoundaries gbOpen @ 9469f09, ODbL; byte copy of raw (`c885587d…e265d`). 105 communes, 103 Polygon + 2 MultiPolygon |
| `03_INPUT/osm_landuse_lux.gpkg` | `ogr2ogr` (qgis env GDAL 3.13.3) of the Overpass OSM XML snapshot 2026-10-01 (`050b95e8…871a`), multipolygons layer `landuse`, `-nlt MULTIPOLYGON -select osm_id,osm_way_id,landuse,name -where "landuse IN ('residential','industrial','commercial','retail')"`; 3,879 polygons, ODbL © OpenStreetMap contributors. Same command as the lux fixture; the GPKG bytes differ per conversion (timestamps), the content hash `1559f8be…d79d` is the same in s1 and s1L |
| `02_SCRIPTS/*.py`, `NOTES.txt` | Hand-written for this eval: `evals/build/s1/legacy_src/` (s1L overlay in `legacy_src/variants/s1L/`), BASE filled in by `20_assemble.sh` |

Study area content: 14 communes; 444 landuse polygons intersect them (160 residential, 136 industrial, 105 commercial,
43 retail; 4 invalid before Fix geometries). 03 yields 601 buffer × commune pieces in s1 (563 in s1L).

## Environment

- Host: Ubuntu 24.04.4 LTS, user `migrate`, micromamba 2.9.0, env `qgis` (conda-forge)
- QGIS 4.2.3 'Belém do Pará' (Qt 6.11.2, Python 3.14.8), GDAL 3.13.3, PROJ 9.9.0, GEOS 3.14.1
- Checks and the golden pipeline: the plugin's locked env (`axis-migrate/bin/axpy`: Python 3.12.15, shapely 2.2.0 with
  GEOS 3.14.1, geopandas 1.2.0, pyproj 3.8.0 / PROJ 9.8.1, pyogrio 0.13.0)

## Exact commands

```bash
B=~/axis-migrate-dev/evals/build/s1
$B/build_all.sh            # for s1 and s1L: rm -rf the base; 20_assemble.sh; 10_stage_inputs.sh (in the qgis env);
                           # run A (40_run_legacy.sh); copy A aside; delete 04_FINAL/05_TEMP; run B;
                           # inputs unchanged check; byte, table (42) and row-order-free content (43) comparison A vs B
$B/41_hash_outputs.sh s1   # sha256 of 03_INPUT, 04_FINAL, 05_TEMP
~/axis-migrate-dev/axis-migrate/bin/axpy $B/60_inventory.py ~/axis-migrate-dev/evals/cases/_bases/s1-buffers-legacy
```

Step wall times (QGIS start-up included, run B of the final build): s1 01 2.95 s · 02 2.49 s · 03 2.53 s · 04 2.15 s;
s1L 01 2.73 s · 02 2.51 s · 03 2.50 s · 04 2.13 s. The committed bases are run B of that build.

## Check 1: reproducibility (run A vs run B, from a clean folder)

- **Inputs** are not modified by the legacy run (sha256 before = after).
- **Bytes**: every GPKG differs (`gpkg_contents.last_change` timestamps) and so does the CSV.
- **Content**: in 01_*, 02_* and 03_buf_x_commune every table is row-for-row identical, geometry blobs included.
  `03_buf_by_commune` and `04_FINAL/buffer_area_by_commune.gpkg/.csv` differ **only in row order / fid**:
  `native:dissolve` emits its groups in hash order, which changes between runs. Compared as multisets of
  (attributes, geometry WKB) they are identical (`43_compare_keyed.py`: PASS for s1 and s1L).
- **Consequence**: never pair these by row order or fid; the compare keys are `shapeID` (communes, dissolved,
  finals), `[osm_id, osm_way_id]` (landuse and buffers) and `[osm_id, osm_way_id, shapeID]` (pieces). The CSV
  and GPKG hashes below pin these exact files; a rebuild gives the same content with other bytes.

## Traps a faithful migration must handle (and what the evals plant)

1. **Per-class branch (T4).** `02_buffer_landuse.py:31-36`: industrial 500 m, every other class 200 m; NULL
   landuse would fall in neither extract (`<>` on NULL is not true; there are none here). Porting only the 200 m
   path changes `02_buf_industrial` and everything downstream (buffer area per commune −4 % to −34 %, total −15 %);
   dropping industrial from the
   merge removes 136 keys from `02_buffers`. discover/scan sees DISTANCE as a phi of {500, 200}; lineage emits no
   `branches[]` entry (the graph schema in CONTRACTS §4 has none).
2. **Segments (T3 alternative).** `SEGMENTS 5` = GEOS quadrant segments 5. shapely's default is 8: Hausdorff up to
   13–16 m on the buffers (more than the 6.2 m sagitta of a 500 m arc; not investigated further) and 396 m on dissolved communes (a small gap
   between overlapping buffers: commune 21775719B63085135447978 has 3 holes in QGIS's dissolve, 2 with 8 segments),
   buffer area per commune +0.12 % to +0.55 % (total +0.31 %). With `quad_segs=5` the golden pipeline reproduces
   every QGIS buffer, intersection and dissolve **bit for bit**.
3. **Simplify before buffer (T3 mutant).** `simplify(50)` before buffer: Hausdorff 71.7 m, symdiff ratio up to 0.10,
   vertex count of the differing buffers 25,356 → 14,339, buffer area per commune −0.3 % to −2.5 % (total −0.86 %).
4. **Buffer CRS (T5, T5L).** Distances are metres in EPSG:2169. A 3857 buffer is ~35 % narrower: whole-feature
   area ratio median 0.51 on `02_buffers` (0.48 industrial, 0.53 other; 10 §3's 0.41–0.42 is for a pure ring),
   buffer area per commune −7 % to −29 % (total −19 %). In s1L the legacy *does* this; the faithful port reproduces it exactly and the
   silent fix fails with area ratio median 1.95 (2.10 / 1.89).
5. **Areas.** `area($geometry)` is planar (QGIS's own shoelace); GEOS `area` differs by ≤ 3.5e-14 relative. The
   rounded CSV is unaffected; the unrounded GPKG needs a signed float-noise record.
6. **Messy but harmless.** `01_communes_2169` is declared `Polygon` but holds 2 MultiPolygons (the driver warns);
   the merge adds `layer`/`path` columns that 02 deletes (with `fid`, which would clash in the GPKG);
   lineage.py raises two false "field not found" questions for `OVERLAY_FIELDS` (it attributes them to the INPUT
   layer `ds.02_buffers`; they belong to the overlay `ds.01_communes_2169`).

## Golden pipeline and T0 (pointers)

- `evals/golden/s1/` (CONTRACTS §5 step modules, params.yaml, `make_project.py` = real scan + lineage + graph ops,
  `build_golden.py`): run under `run_local.py`, `compare.py run`: 8/9 datasets PASS_EXACT,
  `buffer_area_by_commune_gpkg` NEEDS_DECISION (float-noise records unsigned). `out/golden.json`.
- `evals/calib/run_t0.py` → `evals/calib/t0_s1.json`; proposals in `evals/calib/s1/tolerances.proposed.yaml` (unsigned).

## Inventory (committed bases; generated by `60_inventory.py`)

GPKG bytes change on every run (timestamps; dissolve order); compare them by content and by key.

### s1-buffers-legacy

| step | file | sha256 | content sha256 (GPKG) | CRS | schema |
|---|---|---|---|---|---|
| input | `03_INPUT/geoBoundaries-LUX-ADM2.geojson` | `c885587d4883438e800b910029b54a37a3c9bcb73a45ddf48544e496b7ee265d` | – | EPSG:4326 | layer `geoBoundaries-LUX-ADM2` (105 × Unknown): shapeName:object, shapeISO:object, shapeID:object, shapeGroup:object, shapeType:object |
| input | `03_INPUT/osm_landuse_lux.gpkg` | `f59f8bca232960e27eede101f658140bd81e03320a587bd99b00eaa159b354f0` | `1559f8be4ca6799cf4076a53ba6acb6d096295957eb227f35b314b5c45a7d79d` | EPSG:4326 | layer `landuse` (3879 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 01 | `05_TEMP/01_communes_2169.gpkg` | `f4183c43ec9863782dd3b5e449319a4fce0b059d752119f963aaa9835ccff391` | `8ee2a2a056365c576f86ed8bcf542588d5dddfb96ee338fbf080c3432e151ee3` | EPSG:2169 | layer `01_communes_2169` (14 × Polygon): shapeName:object, shapeISO:object, shapeID:object, shapeGroup:object, shapeType:object |
| 01 | `05_TEMP/01_landuse_2169.gpkg` | `c9d1bf16cbd98742c668f20d9613657bb483ec3de5d28e0a8de31ef7f0ebb69f` | `fcd4cfdac6f64d210c20ee1dedaf07a0c04897a6f48492636043509a2d94fb07` | EPSG:2169 | layer `01_landuse_2169` (444 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buf_industrial.gpkg` | `75be048b07530509faa73f5fbccd894c91dc6b32291421d0b1604f2ea1ace4fc` | `f66e784e9e2d13058e9f0f1f382abd364f25ef07d84d7d2259b56eb4bf018f69` | EPSG:2169 | layer `02_buf_industrial` (136 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buf_other.gpkg` | `bf0aa802fd32e8529c4886c5288e18b0181efbcf08d351bb4ce79022c9f88e2e` | `b997589b51a822cc61a6072029442eb0a39e2204140ba08fb1e0d8c4f47175c6` | EPSG:2169 | layer `02_buf_other` (308 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buffers.gpkg` | `e9c557326e0277934c35ccedf757e1a795c6deba1cba707ff42a0ad6bb42713a` | `7c901e2bd3def467e7ed5bea3356c99340b67a2b31f11d66048e9d43c1d85e23` | EPSG:2169 | layer `02_buffers` (444 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 03 | `05_TEMP/03_buf_by_commune.gpkg` | `de8271a36d1f3d1ec98b7686636460d1c60e2bbfd970a7647c2529790d053d8e` | `5aa709c5901c01a3aab13d8df919ef7bf95731d93933cc3902d1a45b800769b0` | EPSG:2169 | layer `03_buf_by_commune` (14 × MultiPolygon): shapeName:object, shapeID:object |
| 03 | `05_TEMP/03_buf_x_commune.gpkg` | `9613ba425b5b365d1e5908c37373b53d988429ffc3f9762e41f9d93e7d6be325` | `f68e3a25df7586716d1b2c17a0c310699c5c3357498fd3fa3817cb7e40cfce4f` | EPSG:2169 | layer `03_buf_x_commune` (601 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, shapeName:object, shapeID:object |
| 04 (final) | `04_FINAL/buffer_area_by_commune.csv` | `ae02c8b817475ddb4d095f3958794e0a9c432cc4b878bbf4bc59bb4f26da70aa` | – | – | header: commune,shapeID,buf_km2,commune_km2,share_pct; 14 rows |
| 04 (final) | `04_FINAL/buffer_area_by_commune.gpkg` | `590197a025f037848f2b621644a1c508f9c6c38240df6e78c19c3983dcb29f58` | `bb155694e91e74024c408e6274227c985aedd6b248948bd0abe4eff2da1572aa` | EPSG:2169 | layer `buffer_area_by_commune` (14 × MultiPolygon): shapeName:object, shapeID:object, buf_km2:float64, commune_km2:float64, share_pct:float64 |

### s1L-buffers-legacy

| step | file | sha256 | content sha256 (GPKG) | CRS | schema |
|---|---|---|---|---|---|
| input | `03_INPUT/geoBoundaries-LUX-ADM2.geojson` | `c885587d4883438e800b910029b54a37a3c9bcb73a45ddf48544e496b7ee265d` | – | EPSG:4326 | layer `geoBoundaries-LUX-ADM2` (105 × Unknown): shapeName:object, shapeISO:object, shapeID:object, shapeGroup:object, shapeType:object |
| input | `03_INPUT/osm_landuse_lux.gpkg` | `7fc8fbe3e82d8970eb6a3cc91261c99a51273763fa28fd44ebcc4d61e73e7cbc` | `1559f8be4ca6799cf4076a53ba6acb6d096295957eb227f35b314b5c45a7d79d` | EPSG:4326 | layer `landuse` (3879 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 01 | `05_TEMP/01_communes_2169.gpkg` | `87f81b116da050051b940e9c51a98bac50cae23d52b71a3f802b4a12db7d358b` | `8ee2a2a056365c576f86ed8bcf542588d5dddfb96ee338fbf080c3432e151ee3` | EPSG:2169 | layer `01_communes_2169` (14 × Polygon): shapeName:object, shapeISO:object, shapeID:object, shapeGroup:object, shapeType:object |
| 01 | `05_TEMP/01_landuse_2169.gpkg` | `523d609912ce5b323aa350a01136c22d0fd64828d2a50070575d065da4f38723` | `fcd4cfdac6f64d210c20ee1dedaf07a0c04897a6f48492636043509a2d94fb07` | EPSG:2169 | layer `01_landuse_2169` (444 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buf_industrial.gpkg` | `92d0edb939b0e7539947d4b77ed5e3201c8493235c2d2ffd2d26e49d330c1cb6` | `a4ea2f207c547d0bb851167c9c74babc9866c097e18e7417b047a50143925048` | EPSG:2169 | layer `02_buf_industrial` (136 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buf_other.gpkg` | `95971f1bc2880edfb622e67ae15bdbb9dda258c681f874c4e8bb13840325ba4e` | `526db0cbca2362950ad5489add0ee04f4deed2db1a8f6e471ddccce38e76d6b1` | EPSG:2169 | layer `02_buf_other` (308 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 02 | `05_TEMP/02_buffers.gpkg` | `d59f4d2494f0cca7dc6758207e00071e197c4b1b37b8d67bf45198a16f7b942f` | `98b4b79e6abbc0cde985494e0b70f34c0c1a43a3fecff5af9d8e9e0bb3b5274f` | EPSG:2169 | layer `02_buffers` (444 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, name:object |
| 03 | `05_TEMP/03_buf_by_commune.gpkg` | `4341765f075c78820446b779fcaa1ef421ac64c708d78e1c557a3f60e41709a7` | `3bed01673ace919597f4f724e914036fe477640a7a56b80e55410a4b384adadd` | EPSG:2169 | layer `03_buf_by_commune` (14 × MultiPolygon): shapeName:object, shapeID:object |
| 03 | `05_TEMP/03_buf_x_commune.gpkg` | `11b3199ab0d386e92ccfc73f0f6fbad06b1772f6bb51449a516e62e3af6e56a9` | `aa02bc490f9b969b09ba7a9751255bfffe58889b31acd3ac7440775157ef3a0b` | EPSG:2169 | layer `03_buf_x_commune` (563 × MultiPolygon): osm_id:object, osm_way_id:object, landuse:object, shapeName:object, shapeID:object |
| 04 (final) | `04_FINAL/buffer_area_by_commune.csv` | `71e87b5a40b6d7ec972b74b8b6f51a4582513a3bbfba8c9ce3e95fb30427c246` | – | – | header: commune,shapeID,buf_km2,commune_km2,share_pct; 14 rows |
| 04 (final) | `04_FINAL/buffer_area_by_commune.gpkg` | `2aee0954977ba6e42f0b553817ebd4ec455e63038e51527a990790480808ea69` | `6f70b77f377aeb7a209a4c2d0854f8cf723762c1ded6dc89ab77915ce0a8de92` | EPSG:2169 | layer `buffer_area_by_commune` (14 × MultiPolygon): shapeName:object, shapeID:object, buf_km2:float64, commune_km2:float64, share_pct:float64 |
