#!/usr/bin/env bash
# Dev-only (demo rehearsal): the plugin's own post-run commands on a chained run, timed, in two phases.
#   devtools/demo/after_run.sh <project dir> <run_id> verify   # compare + plots (then LOOK and review-add)
#   devtools/demo/after_run.sh <project dir> <run_id> seal     # aggregate + display + seal + ladder + report
# Visual-review receipts are NOT produced here: a model (or person) must read the PNGs and call plot.py review-add.
set -u
PLUGIN=${PLUGIN:-$HOME/axis-migrate-dev/axis-migrate}
AXPY="$PLUGIN/bin/axpy"
cd "$1" || exit 70
RID=$2; R=migration/runs/$RID
t() { local s=$(date +%s.%N); "$@"; local rc=$?; printf '  [%.1f s, exit %d] %s\n' "$(echo "$(date +%s.%N) - $s" | bc)" "$rc" "${*: -3}" >&2; return 0; }
case $3 in
  verify)
    t "$AXPY" "$PLUGIN/skills/verify/compare.py" run --run "$R"
    t "$AXPY" "$PLUGIN/skills/verify/plot.py" run --run "$R" >/dev/null
    t "$AXPY" "$PLUGIN/skills/verify/plot.py" review-status --run "$R" ;;
  seal)
    t "$AXPY" "$PLUGIN/skills/verify/compare.py" aggregate --run "$R"
    t "$AXPY" "$PLUGIN/skills/report/map.py" display --run "$R" >/dev/null
    t "$AXPY" "$PLUGIN/skills/report/seal.py" migration --run "$RID"
    t python3 -I "$PLUGIN/skills/report/ladder.py" migration
    t "$AXPY" "$PLUGIN/skills/report/report.py" migration ;;
  *) echo "phase must be verify|seal" >&2; exit 2 ;;
esac
