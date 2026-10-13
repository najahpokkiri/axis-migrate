#!/usr/bin/env bash
# sha256 of everything in 04_FINAL and 05_TEMP, relative paths, sorted
cd ~/axis-migrate-dev/fixtures/lux-exposure-legacy && find 04_FINAL 05_TEMP -type f | sort | xargs sha256sum
