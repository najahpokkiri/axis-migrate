#!/bin/sh
# Run the Claude trigger cases with `claude plugin eval` on a scratch copy of the plugin (cases must sit below the
# plugin; this never writes into the working tree). Needs a logged-in Claude Code (2.1.269+). ~20 short runs, one arm.
#   evals/triggers/run_plugin_eval.sh [extra claude plugin eval args…]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$REPO" archive HEAD axis-migrate | tar -x -C "$TMP"
python3 -I "$HERE/make_cases.py" >/dev/null
mkdir -p "$TMP/axis-migrate/evals-triggers" && cp -R "$HERE/cases/." "$TMP/axis-migrate/evals-triggers/"
claude plugin eval "$TMP/axis-migrate" --eval-dir evals-triggers --ablation none --runs 1 --max-cost-usd 5 \
  --trust-plugin --no-publish --json "$HERE/results/plugin-eval-$(date +%Y%m%dT%H%M%S).json" "$@"
