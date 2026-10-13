#!/usr/bin/env bash
# Run the legacy pipeline exactly as NOTES.txt says (scripts hard-code BASE, so this must
# run on the fixture at its real path).
set -euo pipefail
export MAMBA_ROOT_PREFIX=~/micromamba QT_QPA_PLATFORM=offscreen
FX=~/axis-migrate-dev/fixtures/lux-exposure-legacy
cd "$FX"
for s in 01_prepare_population 02_landuse_mask 03_zonal_population 04_distribute_value; do
  echo "== $s"
  ~/.local/bin/micromamba run -n qgis python 02_SCRIPTS/$s.py 2>&1 | sed 's/(PDAL Error)[^(]*directory//g'
done
