#!/usr/bin/env bash
# sha256 of everything in 04_FINAL (incl. tmp), relative paths, sorted
cd ~/axis-migrate-dev/fixtures/lux-exposure-legacy/04_FINAL && find . -type f | sort | xargs sha256sum
