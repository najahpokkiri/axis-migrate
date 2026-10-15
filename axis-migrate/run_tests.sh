#!/bin/sh
# Run every plugin test suite, one directory at a time (suites share module names such as conftest/test helpers,
# so a single combined pytest session would collide). Full logs are kept per suite; the summary lists every failing
# or erroring test by name. Exit non-zero if any suite fails.
#   run_tests.sh [pytest args…]        logs: $AXIS_TEST_LOGS (default ${TMPDIR:-/tmp}/axis-migrate-tests-<time>)
PLUGIN="$(cd "$(dirname "$0")" && pwd)"
LOGS="${AXIS_TEST_LOGS:-${TMPDIR:-/tmp}/axis-migrate-tests-$(date +%Y%m%dT%H%M%S)}"
mkdir -p "$LOGS"
rc=0
summary=""
for d in "$PLUGIN"/skills/*/tests "$PLUGIN"/hooks/tests "$PLUGIN"/tests_codex; do
  [ -d "$d" ] || continue
  name="$(basename "$(dirname "$d")")"
  [ "$name" = "axis-migrate" ] && name="codex"
  log="$LOGS/$name.log"
  "$PLUGIN/scripts/axpy" -m pytest "$d" -q -p no:cacheprovider -rfEs "$@" >"$log" 2>&1
  code=$?
  last="$(tail -1 "$log")"
  echo "$name: $last"
  if [ $code -ne 0 ] || echo "$last" | grep -qE "[0-9]+ failed|[0-9]+ errors?|no tests ran"; then  # "1 xfailed" is not a failure
    rc=1
    summary="$summary
== $name ($log)
$(grep -E '^(FAILED|ERROR) ' "$log" | head -40)"
  fi
done
# Manifest validation with the Claude Code CLI (warnings are errors). The repo marketplace is checked too when this
# plugin sits in its repository (an installed copy has no ../.claude-plugin). No CLI (Mac/CI without it): skipped.
if command -v claude >/dev/null 2>&1; then
  for target in "$PLUGIN" "$PLUGIN/.."; do
    [ "$target" = "$PLUGIN/.." ] && [ ! -f "$PLUGIN/../.claude-plugin/marketplace.json" ] && continue
    what="plugin"; [ "$target" = "$PLUGIN/.." ] && what="marketplace"
    log="$LOGS/validate-$what.log"
    claude plugin validate --strict "$target" >"$log" 2>&1
    code=$?
    if [ $code -eq 0 ]; then
      echo "validate ($what): passed (claude plugin validate --strict)"
    else
      echo "validate ($what): FAILED (exit $code)"
      rc=1
      summary="$summary
== validate $what ($log)
$(tail -20 "$log")"
    fi
  done
else
  echo "validate: skipped (claude CLI not found; install Claude Code to run claude plugin validate --strict)"
fi
echo "logs: $LOGS"
if [ $rc -ne 0 ]; then
  echo "FAILURES:$summary"
fi
exit $rc
