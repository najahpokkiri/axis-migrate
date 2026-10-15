#!/bin/sh
# run-hook.sh <hook>: start one axis-migrate hook (hooks/bin/<hook>.py) without assuming a working python3.
# POSIX sh only (macOS /bin/sh, dash, Git Bash). Used by every hooks/hooks.json command, in Claude Code and Codex.
#
# 1. Read the hook payload from stdin once (kept byte for byte and passed on unchanged).
# 2. Look for migration/graph.json walking up from: every "cwd" in the payload, the folder of every "file_path" /
#    "notebook_path", and the hook process cwd (not for Stop: a Stop is scoped by session, see stop_gate.py).
#    None found -> exit 0 at once, without any Python: projects that are not migrations are never affected.
#    Exceptions that always go on to Python: session_start (it writes the hooks-active marker and the plugin
#    secrets file even before migration/ exists) and a payload carrying an apply_patch text (absolute paths).
# 3. Interpreter: the plugin's own uv environment (same rule as scripts/axpy and lib/axis_common.env_venv_dir:
#    ${CLAUDE_PLUGIN_DATA}/env-<lock8>, ${PLUGIN_DATA}/env-<lock8>, ${XDG_CACHE_HOME:-~/.cache}/axis-migrate/env-<lock8>,
#    then any env-* there: the hooks need the standard library only), else a python3 (python) that really runs
#    Python >= 3.9 within 3 s. On macOS /usr/bin/python3 is tried only when `xcode-select -p` succeeds, so the
#    Command Line Tools stub never pops its install dialog.
# 4. No interpreter: NEEDS_PYTHON3. PreToolUse denies (exit 2, reason on stderr) except `scripts/axpy --print-python`
#    and `scripts/axpy --doctor`, the two fix commands; PostToolUse blocks (exit 2); Stop blocks once (exit 2) when
#    this session is recorded in migration/.axis/sessions.json and stop_hook_active is false, else allows;
#    SessionStart allows with the reason as context; UserPromptSubmit allows silently.
unset CDPATH
PLUGIN="$(cd "$(dirname "$0")/.." && pwd)"
HOOK="$1"
case "$HOOK" in
  ''|*[!a-z_]*) echo "axis-migrate run-hook.sh: unknown hook '$HOOK'" >&2; exit 2 ;;
esac
SCRIPT="$PLUGIN/hooks/bin/$HOOK.py"
[ -f "$SCRIPT" ] || { echo "axis-migrate run-hook.sh: missing $SCRIPT" >&2; exit 2; }

PAYLOAD="$(cat; printf x)"
PAYLOAD="${PAYLOAD%x}"

# ---- JSON string values of a key (one per line, unescaped; a value with \u, \n or \t escapes marks UNSURE) --------
UNSURE=0
ONE="$(printf '\001')"
json_values() {
  printf '%s' "$PAYLOAD" | tr '\n\r' '  ' | grep -oE "\"$1\"[[:space:]]*:[[:space:]]*\"([^\"\\\\]|\\\\.)*\"" \
    | sed -E "s/^\"$1\"[[:space:]]*:[[:space:]]*\"//; s/\"\$//"
}
mark_unsure() {  # call in this shell (not in $(...)): a value with \u, \n, \t ... escapes cannot be decoded here
  case "$1" in *'\u'*|*'\n'*|*'\t'*|*'\r'*|*'\b'*|*'\f'*) UNSURE=1 ;; esac
}
unescape() {
  printf '%s' "$1" | sed "s/\\\\\\\\/$ONE/g; s/\\\\\"/\"/g; s/\\\\\\//\\//g; s/$ONE/\\\\/g"
}

# ---- find migration/ -----------------------------------------------------------------------------------------
MIG=""
walk() {  # $1 = a folder (or a path inside one that may not exist yet)
  d="$1"
  while [ -n "$d" ] && [ ! -d "$d" ]; do
    case "$d" in */*) d="${d%/*}" ;; *) d="" ;; esac
  done
  [ -n "$d" ] || return 1
  d="$(cd "$d" 2>/dev/null && pwd -P)" || return 1
  while :; do
    if [ -f "$d/migration/graph.json" ]; then MIG="$d/migration"; return 0; fi
    case "$d" in */migration) if [ -f "$d/graph.json" ]; then MIG="$d"; return 0; fi ;; esac
    [ "$d" = "/" ] && return 1
    d="${d%/*}"
    [ -n "$d" ] || d="/"
  done
}

set -f                       # no pathname expansion of payload values
FIRST_CWD=""
OLDIFS="$IFS"
IFS='
'
for v in $(json_values cwd); do
  mark_unsure "$v"
  p="$(unescape "$v")"
  [ -n "$FIRST_CWD" ] || FIRST_CWD="$p"
  [ -n "$MIG" ] || walk "$p"
done
if [ "$HOOK" != "stop_gate" ]; then   # Stop: payload cwd only
  for key in file_path notebook_path; do
    for v in $(json_values "$key"); do
      [ -n "$MIG" ] && break
      mark_unsure "$v"
      p="$(unescape "$v")"
      case "$p" in /*) ;; *) p="${FIRST_CWD:-.}/$p" ;; esac
      walk "$(dirname "$p")"
    done
  done
  [ -n "$MIG" ] || walk "$(pwd -P)"
fi
IFS="$OLDIFS"

if [ -z "$MIG" ]; then
  case "$HOOK" in
    session_start) ;;                               # always runs (marker, secrets) when an interpreter exists
    *)
      case "$PAYLOAD" in
        *'*** Begin Patch'*) ;;                     # apply_patch carries absolute paths: let Python decide
        *) [ "$UNSURE" = 1 ] || exit 0 ;;           # not a migration: nothing to guard, no Python needed
      esac ;;
  esac
fi

# ---- interpreter -------------------------------------------------------------------------------------------------
probe() {  # exit 0 iff "$1" runs Python >= 3.9 within 3 s
  "$1" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' </dev/null >/dev/null 2>&1 &
  pp=$!
  ( sleep 3; kill -9 "$pp" 2>/dev/null ) </dev/null >/dev/null 2>&1 &
  wd=$!
  wait "$pp"
  prc=$?
  kill "$wd" 2>/dev/null
  return $prc
}

PY=""
LOCK="$PLUGIN/skills/run/env/uv.lock"
LOCK8=""
if [ -f "$LOCK" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    LOCK8="$(sha256sum "$LOCK" | cut -c1-8)"
  elif command -v shasum >/dev/null 2>&1; then
    LOCK8="$(shasum -a 256 "$LOCK" | cut -c1-8)"
  fi
fi
BASES=""
for b in "${CLAUDE_PLUGIN_DATA:-}" "${PLUGIN_DATA:-}" "${XDG_CACHE_HOME:-${HOME:-/nonexistent}/.cache}/axis-migrate"; do
  [ -n "$b" ] && [ -d "$b" ] && BASES="$BASES
$b"
done
IFS='
'
if [ -n "$LOCK8" ]; then
  for b in $BASES; do
    for c in "$b/env-$LOCK8/bin/python" "$b/env-$LOCK8/Scripts/python.exe"; do
      if [ -z "$PY" ] && [ -x "$c" ]; then PY="$c"; fi
    done
  done
fi
if [ -z "$PY" ]; then
  set +f
  for b in $BASES; do
    for c in "$b"/env-*/bin/python "$b"/env-*/Scripts/python.exe; do
      if [ -z "$PY" ] && [ -x "$c" ]; then PY="$c"; fi
    done
  done
  set -f
fi
IFS="$OLDIFS"
if [ -z "$PY" ]; then
  DARWIN=0
  [ "$(uname -s 2>/dev/null)" = "Darwin" ] && DARWIN=1
  CANDS="$(command -v python3 2>/dev/null)"
  if [ "$DARWIN" = 1 ]; then CANDS="$CANDS
/opt/homebrew/bin/python3
/usr/local/bin/python3"; fi
  CANDS="$CANDS
$(command -v python 2>/dev/null)"
  IFS='
'
  for c in $CANDS; do
    [ -x "$c" ] || continue
    if [ "$DARWIN" = 1 ] && [ "$c" = "/usr/bin/python3" ]; then
      xcode-select -p >/dev/null 2>&1 || continue   # the CLT stub: never run it (it opens an install dialog)
    fi
    if probe "$c"; then PY="$c"; break; fi
  done
  IFS="$OLDIFS"
fi

if [ -n "$PY" ]; then
  PYTHONIOENCODING=utf-8
  export PYTHONIOENCODING
  printf '%s' "$PAYLOAD" | "$PY" -I -B "$SCRIPT"
  exit $?
fi

# ---- no interpreter: NEEDS_PYTHON3 ---------------------------------------------------------------------------------
[ -n "$MIG" ] || exit 0   # session_start or an apply_patch payload outside any migration: nothing to guard
REASON="axis-migrate NEEDS_PYTHON3: the axis-migrate hooks guard $MIG and need Python 3.9+, but neither the plugin's own environment nor a working python3 was found (on a fresh Mac /usr/bin/python3 is only the Xcode Command Line Tools stub). Fix: install uv (https://docs.astral.sh/uv/; ask the user to run the installer in their own terminal), then run \"$PLUGIN/scripts/axpy\" --print-python once (it builds the plugin's environment, which the hooks then use) and \"$PLUGIN/scripts/axpy\" --doctor. Those two commands are allowed; everything else that the hooks guard is refused until then."
codex=0
{ [ -n "${PLUGIN_ROOT:-}" ] || [ -n "${PLUGIN_DATA:-}" ]; } && codex=1
case "$PAYLOAD" in *'"turn_id"'*) codex=1 ;; esac

case "$HOOK" in
  guard_write|guard_bash|guard_hidden)
    if [ "$HOOK" = "guard_bash" ]; then   # the two fix commands, exactly as written in the reason
      cmd="$(unescape "$(json_values command | head -1)")"
      for root in "$PLUGIN" '${CLAUDE_PLUGIN_ROOT}' '$CLAUDE_PLUGIN_ROOT' '${PLUGIN_ROOT}' '$PLUGIN_ROOT'; do
        for flag in --print-python --doctor; do
          if [ "$cmd" = "\"$root/scripts/axpy\" $flag" ] || [ "$cmd" = "$root/scripts/axpy $flag" ]; then
            exit 0
          fi
        done
      done
    fi
    echo "$REASON" >&2
    exit 2 ;;
  post_scan)
    echo "$REASON" >&2
    exit 2 ;;
  stop_gate)
    sid="$(json_values session_id | head -1)"
    active=0
    printf '%s' "$PAYLOAD" | grep -qE '"stop_hook_active"[[:space:]]*:[[:space:]]*true' && active=1
    if [ -n "$sid" ] && [ "$active" = 0 ] && [ -f "$MIG/.axis/sessions.json" ] \
       && grep -qF "\"$sid\"" "$MIG/.axis/sessions.json"; then
      echo "$REASON Until then the Stop gate cannot check completion claims: do not claim any status." >&2
      exit 2
    fi
    [ "$codex" = 1 ] && echo '{}'
    exit 0 ;;
  session_start)
    esc="$(printf '%s' "$REASON" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    printf '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "%s"}}\n' "$esc"
    exit 0 ;;
  *)
    exit 0 ;;
esac
