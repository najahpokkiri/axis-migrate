# axis-migrate under Codex

Codex is a first-class harness with the same hooks, guards and end-to-end gate as the Claude desktop app.
**14 Oct 2026, premise changed:** the earlier "Codex: script halves only, no hooks" rested on `plugin_hooks removed`
in `codex features list`. That flag is retired, not a switch: codex-cli 0.144.4 and 0.162.1 (measured here with
`hooks/list`, and a real SessionStart under `codex exec`) and the docs (learn.chatgpt.com/docs/hooks.md) load
plugin hooks from `hooks/hooks.json`; they run only once trusted. Najah decided: Codex gets the same hooks as Claude.

## Install (from a clean checkout; nothing large is copied, the environments live in `~/.cache/axis-migrate/`)

```
codex plugin marketplace add <checkout of the axis-migrate-dev repository>
codex plugin add axis-migrate@axis-migrate-dev
```
or the deeplink `codex://plugins/axis-migrate?marketplacePath=<url-encoded absolute path of .agents/plugins/marketplace.json>`.
Then, once: open `/hooks`, review and trust the axis-migrate hooks, start a new session, and type `$axis-migrate:start`.
Codex skips untrusted or changed hooks; `skills/guard/hooks_active.py` (run by the start skill) says if they ran.

Manifest: `.codex-plugin/plugin.json` (canonical; no root `plugin.json`, no `hooks` key: default discovery finds
`hooks/hooks.json`). Marketplace: repo-root `.agents/plugins/marketplace.json`, `source.path` `./axis-migrate`.
`skills` stays the array `["./skills/", "./codex/skills/"]`: measured on 0.144.4 and 0.162.1, the string
`"./codex/skills/"` replaces default discovery (only `start` loads, the seven method skills vanish).

## Hooks under Codex

One `hooks/hooks.json` serves both harnesses (commands use `${CLAUDE_PLUGIN_ROOT:-$PLUGIN_ROOT}`; Codex sets both).
Same event names; the write matcher includes `apply_patch`, whose patch (`tool_input.command`) the guards parse.
Hooks get `PLUGIN_DATA` = `$CODEX_HOME/plugins/data/axis-migrate-<marketplace>/` (measured); the agent's shell does not.

- `permissionDecision: "ask"` is not supported by Codex (the call goes ahead), so under Codex the guards turn it into
  a `deny` saying a person must make or approve the change.
- No `onFailure: block`: guards exit 2 themselves on any error (Codex blocks on exit 2); timeouts are unmeasured.
- No `CLAUDE_ENV_FILE`: `GDAL_PAM_ENABLED=NO` reaches plugin scripts through `bin/axpy` only.
- No Read/Grep/Glob tools: the hidden-path read guard runs inside `guard_bash`.
- Stop `decision: block` makes Codex continue with the reason as the next prompt, as in Claude Code.

## Defence in depth: the script halves still refuse when a hook is not running

Runner preflight: `SCAN_BLOCKED`, `PLAN_NOT_APPROVED`, `GRAPH_EDITED_OUTSIDE_APPLY`; strace ledger:
`UNDECLARED_READ`/`UNDECLARED_WRITE`; compare: `TOLERANCE_CHANGED_AFTER_RUN`; ladder: `*_CHANGED_AFTER_RUN`,
`LEGACY_REF_CHANGED`, evidence hashes. Without hooks they detect, never prevent: a write into the legacy folder
still happens; a rewritten `decisions.md` block or a human-looking `agreed_by` before a run is not caught; there is
no Stop gate, so the agent must quote `ladder.py`'s status line.

## Sandbox (`workspace-write`: no network, no writes outside the workspace)

Approve, once each, with escalated permissions: `bin/axpy --doctor` (first `uv sync --frozen` of the pinned
environment into `~/.cache/axis-migrate/`: network + an outside write); the map environment's first sync
(`skills/map/`, GeoLibre 3.4.0: same); `graph_server.py serve migration` and `skills/map/start.py migration` (bind
127.0.0.1); `run_local.py` if `--doctor` says strace cannot trace inside the sandbox. Refused: the script prints one
FAIL line and the flow stops there.

## Showing pages

Codex desktop: the URL once as a clickable link, opened in the in-app browser with browser use. Claude desktop:
`mcp__Claude_Browser__preview_start {url}`. Terminal only: print the URL once and name the files.

Not measured here: Codex desktop end to end, a model-driven `codex exec` (no sign-in here), strace/binds in the sandbox.

Sources (14 Oct 2026): learn.chatgpt.com/docs/hooks.md · developers.openai.com/codex/plugins/build · …/codex/app/browser
