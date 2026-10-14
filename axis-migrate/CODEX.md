# axis-migrate under Codex

Codex is a first-class harness with the same hooks, guards and end-to-end gate as the Claude desktop app.
**14 Oct 2026, premise changed** (Najah): the earlier "no hooks" misread the retired `plugin_hooks` flag; Codex loads
plugin hooks from `hooks/hooks.json` once trusted, so it gets the same hooks as Claude.

## Install (from GitHub or a fresh clone, never a working tree: Codex copies git-ignored files too; `--doctor` warns)

```
codex plugin marketplace add najahpokkiri/axis-migrate --ref main     # or the path of a fresh clone
codex plugin add axis-migrate@axis-migrate-dev
```
Installing is not trusting: until trusted, the hooks do not run and nothing is logged. Once: open `/hooks` (desktop:
Review hooks), select axis-migrate's definitions, press `t`, then start a NEW chat (an open chat never loads a newly
installed plugin) and type `$axis-migrate:start`. A changed hook definition needs re-review, so every plugin update
can drop the guards until re-trusted. `skills/guard/hooks_active.py` (run by the start skill) says whether they ran;
if not, the start skill says the guards are off and writes nothing under `migration/` until the person confirms.

Manifest: `.codex-plugin/plugin.json` (canonical; no root `plugin.json`, no `hooks` key: default discovery finds
`hooks/hooks.json`). Marketplace: repo-root `.agents/plugins/marketplace.json`, `source.path` `./axis-migrate`.
`skills` stays the array `["./skills/", "./codex/skills/"]`: measured on 0.144.4 and 0.162.1, the string
`"./codex/skills/"` replaces default discovery (only `start` loads, the seven method skills vanish); the list loads
all 9 (confirmed on the laptop, 0.162.0).

## Hooks under Codex

One `hooks/hooks.json` serves both harnesses (commands use `${CLAUDE_PLUGIN_ROOT:-$PLUGIN_ROOT}`; Codex sets both).
Same event names; the write matcher includes `apply_patch`, whose patch (`tool_input.command`) the guards parse.
Hooks get `PLUGIN_ROOT` = `CLAUDE_PLUGIN_ROOT` = the cache copy (`~/.codex/plugins/cache/<marketplace>/<plugin>/<version>`)
and `PLUGIN_DATA` = `CLAUDE_PLUGIN_DATA` = `~/.codex/plugins/data/<plugin>-<marketplace>/`, which does not exist until a
writer creates it (every writer runs `mkdir -p`); the agent's shell gets neither. In the desktop app the hook
process cwd is the session root while the shell may run in a subfolder: the guards resolve relative paths against
the payload's `cwd` and deny when either base could reach a protected path; a missing or unparseable payload denies.

- `permissionDecision: "ask"` is not supported by Codex (the call goes ahead), so under Codex the guards turn it into
  a `deny` saying a person must make or approve the change.
- No `onFailure: block`: guards exit 2 themselves on any error (Codex blocks on exit 2); timeouts are unmeasured.
- No `CLAUDE_ENV_FILE`: `GDAL_PAM_ENABLED=NO` reaches plugin scripts through `bin/axpy` only.
- No Read/Grep/Glob tools: the hidden-path read guard runs inside `guard_bash`.
- Stop `decision: block` makes Codex continue once with the reason as the next prompt, as in Claude Code.
- Questions: `request_user_input_async` (options, asynchronous reply) when it exists during work, else one plain
  numbered question in chat; an elapsed wait is never an answer (`request_user_input` works in Plan mode only).

## Defence in depth: the script halves still refuse when a hook is not running

Runner preflight: `SCAN_BLOCKED`, `PLAN_NOT_APPROVED`, `GRAPH_EDITED_OUTSIDE_APPLY`; strace ledger:
`UNDECLARED_READ`/`UNDECLARED_WRITE`; compare: `TOLERANCE_CHANGED_AFTER_RUN`; ladder: `*_CHANGED_AFTER_RUN`,
`LEGACY_REF_CHANGED`, evidence hashes. Without hooks they detect, never prevent: a write into the legacy folder
still happens; a rewritten `decisions.md` block or a human-looking `agreed_by` before a run is not caught; there is
no Stop gate, so the agent must quote `ladder.py`'s status line.

## Sandbox (`workspace-write`: no network, no writes outside the workspace) — unverified in the default mode

Approve, once each, with escalated permissions: `bin/axpy --doctor` (first `uv sync --frozen` of the pinned
environment into `~/.cache/axis-migrate/`: network + an outside write); the map environment's first sync
(`skills/map/`, GeoLibre 3.4.0: same); `graph_server.py serve migration` and `skills/map/start.py migration` (bind
127.0.0.1); `run_local.py` if `--doctor` says strace cannot trace inside the sandbox. Refused: the script prints one
FAIL line and the flow stops there; ask for that one narrowest approval. Claude's `allowLocalBinding` does not
apply to Codex.

## Showing pages

Codex desktop: the URL once as a link, opened in the in-app browser; the agent reads, clicks, types and screenshots
itself (annotate mode is not a channel). Claude: `mcp__Claude_Browser__preview_start {url}`. Terminal: URL + files.

Measured on the founder's laptop (desktop + CLI 0.162.0): `.codex-plugin` is the only layout whose hooks are discovered
(a root `plugin.json` loads skills but never hooks), default hook discovery, the payloads, exit-2 blocking, trust,
the in-app browser. Not yet measured: the restricted sandbox, a full Codex desktop e2e, hook timeouts.

Sources (14 Oct 2026): learn.chatgpt.com/docs/hooks.md · developers.openai.com/codex/plugins/build · …/codex/app/browser
