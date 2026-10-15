#!/bin/sh
# fetch_byu.sh — rebuild the BYU flood impact demo fixture (byu-flood-impact-legacy) reproducibly. Data never enters git.
#
#   devtools/fetch_byu.sh [DEST]      default DEST: ~/public-fixtures/byu-flood-impact-legacy (must not exist yet)
#
# 1. Code: github.com/BYU-Hydroinformatics/flood_impact at commit 544a2dc4 (no licence: internal use only, never
#    redistribute). Copied without .git.
# 2. Data: the HydroShare bag c32624eb8abf402d9c33dcb2c7cab5ff (CC BY), checked against its sha256; only
#    data/contents/inirida_colombia/ is copied, unchanged (unzip keeps the bag's 1980 timestamps).
# 3. Every source file is checked against devtools/byu/source-files.sha256.
# 4. Reference outputs: the project's own QGIS model run headless in the micromamba env `qgis` (QGIS 4.2.3) with the
#    published amen_group body registered as an expression function (devtools/byu/run_model_with_amen_group.py; the
#    model file is not modified). Checked by content against devtools/byu/expected-outputs.json (GeoPackages carry
#    timestamps, and Merged_Flood_Impact.path holds the absolute output paths, so bytes differ between builds).
# Needs: git, curl, unzip, sha256sum, python3, micromamba with env `qgis` (MAMBA_ROOT_PREFIX, default ~/micromamba).
# Exit 0 = fixture rebuilt and checked; anything else = stop (DEST is left for inspection).
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${1:-$HOME/public-fixtures/byu-flood-impact-legacy}"
COMMIT=544a2dc4d444162a11b1292b97ac70d28976b060
REPO=https://github.com/BYU-Hydroinformatics/flood_impact.git
BAG_URL=https://www.hydroshare.org/hsapi/resource/c32624eb8abf402d9c33dcb2c7cab5ff/
BAG_SHA=3336f39cb6d388f377c93e370fc51f783e8e0ebfcc4dc773ba9089d092011019
MAMBA="${MICROMAMBA:-$(command -v micromamba || echo "$HOME/.local/bin/micromamba")}"
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"

[ -e "$DEST" ] && { echo "fetch_byu: $DEST exists; move it away first (the fixture is never overwritten)" >&2; exit 2; }
WORK="$(mktemp -d "${TMPDIR:-/tmp}/fetch-byu.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
echo "fetch_byu: working in $WORK"

# 1. code at the pinned commit
git init -q "$WORK/code"
git -C "$WORK/code" fetch -q --depth 1 "$REPO" "$COMMIT"
git -C "$WORK/code" -c advice.detachedHead=false checkout -q FETCH_HEAD
[ "$(git -C "$WORK/code" rev-parse HEAD)" = "$COMMIT" ] || { echo "fetch_byu: wrong commit" >&2; exit 3; }

# 2. data bag
curl -fsSL -o "$WORK/bag.zip" "$BAG_URL"
echo "$BAG_SHA  $WORK/bag.zip" | sha256sum -c --quiet - || { echo "fetch_byu: HydroShare bag sha256 differs" >&2; exit 3; }
unzip -q "$WORK/bag.zip" -d "$WORK/bag"
SRC_DATA="$(find "$WORK/bag" -type d -path '*/data/contents/inirida_colombia' | head -1)"
[ -n "$SRC_DATA" ] || { echo "fetch_byu: inirida_colombia not found in the bag" >&2; exit 3; }

# 3. assemble and check the source files
mkdir -p "$(dirname "$DEST")"
mkdir "$DEST"
(cd "$WORK/code" && tar cf - --exclude=.git .) | (cd "$DEST" && tar xf -)
cp -Rp "$SRC_DATA" "$DEST/inirida_colombia"
(cd "$DEST" && sha256sum -c --quiet "$HERE/byu/source-files.sha256") || { echo "fetch_byu: source files differ" >&2; exit 3; }
n_listed=$(wc -l < "$HERE/byu/source-files.sha256")
n_found=$(cd "$DEST" && find . -type f | wc -l)
[ "$n_listed" -eq "$n_found" ] || { echo "fetch_byu: $n_found files, expected $n_listed" >&2; exit 3; }

# 4. reference outputs from the project's own QGIS model
OUT="$DEST/reference_outputs/flood_impact_qgisModel"
mkdir -p "$OUT"
I="$DEST/inirida_colombia"
cat > "$WORK/inputs.json" <<EOF
{"inputs": {
 "Croplands": "$I/croplands_S00W70.tif",
 "Population": "$I/worldpop_colombia.tif",
 "FloodExtent": "$I/inirida_flood_map/Inirida_crs_10Aug18_SRH2D.shp",
 "OpenStreetMap": "$I/inirida_osm/Inirida_osmpts.shp",
 "native:aggregate_1:Agriculture Flood": "$OUT/Agriculture_Flood.gpkg",
 "native:aggregate_2:Population Flood": "$OUT/Population_Flood.gpkg",
 "native:aggregate_3:OSM Flood": "$OUT/OSM_Flood.gpkg",
 "native:mergevectorlayers_1:Merged_Flood_Impact": "$OUT/Merged_Flood_Impact.gpkg"
}}
EOF
(cd "$WORK" && QT_QPA_PLATFORM=offscreen "$MAMBA" run -n qgis python -P -s "$HERE/byu/run_model_with_amen_group.py" \
  "$DEST/qgis/flood_impact_qgisModel.model3" "$DEST/python/fldimpact_def.py" "$WORK/inputs.json") > "$WORK/model.log" 2>&1 \
  || { tail -20 "$WORK/model.log" >&2; echo "fetch_byu: the QGIS model run failed" >&2; exit 4; }
# QGIS (GDAL PAM) may leave .aux.xml next to the inputs; the fixture never keeps them
find "$DEST" -name '*.aux.xml' -newer "$WORK/inputs.json" -delete
python3 -I "$HERE/byu/check_outputs.py" "$OUT" "$HERE/byu/expected-outputs.json"
n_found=$(cd "$DEST" && find . -type f | wc -l)
[ "$n_found" -eq $((n_listed + 4)) ] || { echo "fetch_byu: $n_found files after the run, expected $((n_listed + 4))" >&2; exit 3; }
echo "fetch_byu: OK $DEST ($n_found files)"
