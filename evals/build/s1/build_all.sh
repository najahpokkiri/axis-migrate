#!/usr/bin/env bash
# Full rebuild of the S1 legacy bases from scratch, with a reproducibility re-run (run A vs run B).
#   build_all.sh [s1] [s1L]        (default: both)
# Writes evals/cases/_bases/<v>-buffers-legacy/ and prints hashes + the content comparison of run A vs run B.
set -euo pipefail
B=$(cd "$(dirname "$0")" && pwd)
export MAMBA_ROOT_PREFIX=~/micromamba QT_QPA_PLATFORM=offscreen GDAL_PAM_ENABLED=NO
MR="$HOME/.local/bin/micromamba run -n qgis"
SCR=$(mktemp -d)
for V in "${@:-s1 s1L}"; do
for V in $V; do
  FX=~/axis-migrate-dev/evals/cases/_bases/$V-buffers-legacy
  echo "######## $V -> $FX"
  rm -rf "$FX"
  "$B/20_assemble.sh" "$V"
  $MR bash "$B/10_stage_inputs.sh" "$FX" >/dev/null
  (cd "$FX" && sha256sum 03_INPUT/* > "$SCR/$V.inputs.before")
  echo "== run A"; "$B/40_run_legacy.sh" "$V" | grep '^=='
  mkdir -p "$SCR/$V.A"; cp -a "$FX/04_FINAL" "$FX/05_TEMP" "$SCR/$V.A/"
  rm -rf "$FX/04_FINAL" "$FX/05_TEMP"
  echo "== run B"; "$B/40_run_legacy.sh" "$V" | grep '^=='
  (cd "$FX" && sha256sum -c "$SCR/$V.inputs.before" --quiet) && echo "inputs unchanged by the legacy run: yes"
  echo "== run A vs run B (bytes)"
  (cd "$SCR/$V.A" && find . -type f | sort | xargs sha256sum) > "$SCR/$V.A.sha"
  (cd "$FX" && find 04_FINAL 05_TEMP -type f | sort | sed 's#^#./#' | xargs sha256sum) > "$SCR/$V.B.sha"
  join -1 2 -2 2 <(sort -k2 "$SCR/$V.A.sha") <(sort -k2 "$SCR/$V.B.sha") | awk '{print ($2==$3 ? "identical" : "BYTES DIFFER"), $1}' 
  echo "== run A vs run B (content)"
  for d in 04_FINAL 05_TEMP; do $MR python "$B/42_compare_runs.py" "$SCR/$V.A/$d" "$FX/$d"; done
  echo "== run A vs run B (content, row order ignored)"
  $MR python "$B/43_compare_keyed.py" "$SCR/$V.A" "$FX"
done
done
rm -rf "$SCR"
