#!/usr/bin/env bash
# Assemble a legacy folder from legacy_src (scripts + NOTES), BASE set to the folder's real path.
#   20_assemble.sh s1|s1L      -> evals/cases/_bases/<name>-buffers-legacy/{NOTES.txt,02_SCRIPTS/}
set -euo pipefail
B=$(cd "$(dirname "$0")" && pwd)
V=$1
FX=~/axis-migrate-dev/evals/cases/_bases/$V-buffers-legacy
mkdir -p "$FX/02_SCRIPTS"
cp "$B/legacy_src/NOTES.txt" "$FX/NOTES.txt"
cp "$B"/legacy_src/02_SCRIPTS/*.py "$FX/02_SCRIPTS/"
if [ -d "$B/legacy_src/variants/$V" ]; then cp -r "$B/legacy_src/variants/$V/." "$FX/"; fi
sed -i "s#@BASE@#$FX#" "$FX"/02_SCRIPTS/*.py
for f in "$FX"/02_SCRIPTS/*.py; do grep -q "BASE = \"$FX\"" "$f" || { echo "BASE not set in $f"; exit 1; }; done
