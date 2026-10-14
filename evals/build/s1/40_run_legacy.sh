#!/usr/bin/env bash
# Run the legacy pipeline exactly as NOTES.txt says (scripts hard-code BASE, so this runs on the folder at its
# real path). Prints wall time per step.
#   40_run_legacy.sh s1|s1L
set -euo pipefail
export MAMBA_ROOT_PREFIX=~/micromamba QT_QPA_PLATFORM=offscreen
FX=~/axis-migrate-dev/evals/cases/_bases/$1-buffers-legacy
cd "$FX"
for s in 01_prepare_inputs 02_buffer_landuse 03_clip_dissolve 04_area_per_commune; do
  t0=$(date +%s.%N)
  ~/.local/bin/micromamba run -n qgis python 02_SCRIPTS/$s.py 2>&1 | sed 's/(PDAL Error)[^(]*directory//g' | grep -v '^$' || true
  t1=$(date +%s.%N)
  printf "== %s wall %.2f s\n" "$s" "$(echo "$t1 - $t0" | bc)"
done
