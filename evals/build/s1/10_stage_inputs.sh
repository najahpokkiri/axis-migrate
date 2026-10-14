#!/usr/bin/env bash
# Stage public inputs into <legacy>/03_INPUT. Run inside the micromamba `qgis` env (its GDAL/ogr2ogr).
#   10_stage_inputs.sh <legacy_dir>
set -euo pipefail
RAW=~/demo-data/raw
IN="$1/03_INPUT"
mkdir -p "$IN"
cp "$RAW/geoBoundaries-LUX-ADM2.geojson" "$IN/"
# OSM landuse: Overpass XML -> GPKG multipolygons (the analyst's "QuickOSM export"), same command as the lux fixture
rm -f "$IN/osm_landuse_lux.gpkg"
ogr2ogr -f GPKG "$IN/osm_landuse_lux.gpkg" "$RAW/osm_landuse_lux_20261001.osm" multipolygons \
  -nln landuse -nlt MULTIPOLYGON -select osm_id,osm_way_id,landuse,name \
  -where "landuse IN ('residential','industrial','commercial','retail')"
