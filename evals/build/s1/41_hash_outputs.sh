#!/usr/bin/env bash
# sha256 of everything in 03_INPUT, 04_FINAL and 05_TEMP (relative paths, sorted)
#   41_hash_outputs.sh s1|s1L
cd ~/axis-migrate-dev/evals/cases/_bases/$1-buffers-legacy && find 03_INPUT 04_FINAL 05_TEMP -type f | sort | xargs sha256sum
