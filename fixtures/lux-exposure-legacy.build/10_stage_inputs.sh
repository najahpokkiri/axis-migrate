#!/usr/bin/env bash
# Stage public inputs into the legacy fixture's 03_INPUT and simulate the analyst's
# manual canton-name edit (NOTES.txt step). Run inside the micromamba `qgis` env.
set -euo pipefail
RAW=~/demo-data/raw
FX=~/axis-migrate-dev/fixtures/lux-exposure-legacy
IN=$FX/03_INPUT
mkdir -p "$IN"
cp "$RAW/lux_ppp_2020.tif" "$IN/"
cp "$RAW/geoBoundaries-LUX-ADM1.geojson" "$RAW/geoBoundaries-LUX-ADM2.geojson" "$IN/"
# OSM landuse: Overpass XML -> GPKG multipolygons (analyst's "export from QuickOSM")
rm -f "$IN/osm_landuse_lux.gpkg"
ogr2ogr -f GPKG "$IN/osm_landuse_lux.gpkg" "$RAW/osm_landuse_lux_20261001.osm" multipolygons \
  -nln landuse -nlt MULTIPOLYGON -select osm_id,osm_way_id,landuse,name \
  -where "landuse IN ('residential','industrial','commercial','retail')"
# "Hand edit": add canton field matching the xlsx spelling.
rm -f "$IN/cantons_lux_edit.gpkg"
ogr2ogr -f GPKG "$IN/cantons_lux_edit.gpkg" "$IN/geoBoundaries-LUX-ADM1.geojson" -nln cantons -dialect SQLite -sql \
 "SELECT geometry, shapeName, shapeISO, shapeID,
         CASE WHEN shapeName = 'Canton Esch-sur-Alzette' THEN 'Esch/Alzette'
              ELSE replace(shapeName, 'Canton ', '') END AS canton
  FROM \"geoBoundaries-LUX-ADM1\""
