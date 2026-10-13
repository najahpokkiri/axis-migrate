#!/bin/sh
# Run every plugin test suite, one directory at a time (suites share module names such as conftest/test helpers,
# so a single combined pytest session would collide). Exit non-zero if any suite fails.
PLUGIN="$(cd "$(dirname "$0")" && pwd)"
rc=0
for d in "$PLUGIN"/skills/*/tests "$PLUGIN"/hooks/tests; do
  [ -d "$d" ] || continue
  out="$("$PLUGIN/bin/axpy" -m pytest "$d" -q -p no:cacheprovider "$@" 2>&1 | tail -1)"
  echo "$(basename "$(dirname "$d")"): $out"
  case "$out" in *failed*|*error*|*"no tests ran"*) rc=1;; esac
done
exit $rc
