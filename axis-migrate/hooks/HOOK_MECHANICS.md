# Hook mechanics HM1–HM7: measured on Claude Code 2.1.296

These are the probes from 08 §6, run on 13 Oct 2026 with headless `claude -p --model haiku`. Each run used a fresh
`CLAUDE_CONFIG_DIR`, so no user plugins or hooks were loaded, and the session token came from the environment. Four
invocations in total, out of a budget of 6:

| Run | What | Mode | Plugin |
|---|---|---|---|
| 1 | Mechanics probe and stdin capture | `--permission-mode bypassPermissions` | scratch probe plugin (`--plugin-dir`) + a `--settings` hook |
| 2 | axis-migrate end to end on a synthetic project | default mode, `--allowedTools Bash,Write,Edit,Read` | this plugin (`--plugin-dir axis-migrate`) |
| 3 | Stop block chain and `systemMessage` | default mode | scratch probe plugin |
| 4 | the shared hooks.json (`${CLAUDE_PLUGIN_ROOT:-$PLUGIN_ROOT}` commands, `apply_patch` in matchers) | bypassPermissions | this plugin |

Evidence comes from `--output-format stream-json --verbose --include-hook-events` (hook_response events, tool
results, the final text), from marker files that each Bash step would have created, and from per-call stdin dumps.
The dumps are saved, with paths sanitised, as `hooks/tests/payloads/*.json`; the tests use these real payloads.

## Results

| Id | Question | Result | Evidence |
|---|---|---|---|
| HM1 | Does a PreToolUse `deny` hold under bypassPermissions? | **Yes.** Write and Bash were both refused. | Run 1: the tool result was `PreToolUse:Write hook error: HM1 DENY REASON 7731`, and neither `hm1-deny.txt` nor `hm1.out` was created. In run 2 (default mode), the real `guard_bash`/`guard_write` denies reached the model verbatim. |
| HM2 | What does `ask` do headless? | **It is refused; nothing prompts.** Under bypassPermissions the tool result is the ask reason as an error. In default mode Claude Code emits `permission_denied` with `decision_reason_type: "hook"`. Neither mode runs the action, so bypass does not auto-approve an `ask`. Interactive prompting cannot be tested headless; per the docs it prompts. | Run 1: `HM2 ASK REASON 5520` came back as an error and `hm2.out`/`hm2-ask.txt` were absent. Run 2: the real tolerance-v2 `ask` ended as `{"subtype":"permission_denied","decision_reason_type":"hook"}`, and `tolerances.yaml` was unchanged. |
| HM3 | Does the PostToolUse `decision: block` reason reach the model? | **Yes, verbatim.** The model sees "PostToolUse:Write hook blocking error from command: …: <reason>". The write itself has already happened. | Run 1: the model quoted `HM3 POST BLOCK TOKEN ZEBRA-4412` after the Write and after the Edit. Run 2: the model quoted the real S12 finding ("S12 test-mocks-subject line 5 …"). |
| HM4 | Does `onFailure: block` turn a crash or timeout into a block? | **Yes, both.** A crash gives `failed; blocking because onFailure is "block"` plus the traceback; a timeout gives `timed out; blocking because onFailure is "block"`. A control hook that crashes **without** `onFailure` lets the action proceed. | Run 1: `hm4crash.out` and `hm4timeout.out` were absent (timeout 4 s, hook slept 20 s), while `hm4control.out` was present. |
| HM5 | Does `$CLAUDE_ENV_FILE` from SessionStart reach later Bash? | **Yes.** | Run 1: the file was `<config>/session-env/<session>/sessionstart-hook-0.sh`, and Bash printed `env=hm5-env-ok`. Run 2: the real `session_start.py` wrote it and Bash printed `PAM=NO`. |
| HM6 | Do plugin hooks fire alongside other hooks? | **Yes.** Every matching hook from the plugin and from settings runs, in parallel. | Run 1: for each Bash call, three PreToolUse hooks started (plugin `pre`, plugin `pre_control`, the `--settings` hook) and all three dumped stdin. The settings source was `--settings`, i.e. flag settings, which merge like user settings. |
| HM7 | Which fields does the Stop input carry? | `last_assistant_message`, `stop_hook_active` (false on the first Stop, true on the continuations), `transcript_path`, `session_id`, `cwd`, `permission_mode`, `effort`, `prompt_id`, `scratchpad_dir`, `background_tasks`, `session_crons`, `hook_event_name`. | Run 1 dumps 029 and 030; run 3 dumps `stop-1..3` (`stop_hook_active` false, true, true). |

### Other findings that changed the build

- **Stop `systemMessage` reaches the user, not the model.** It appears as a system notice: `{"subtype":
  "informational","content":"Stop says: HM-SYSMSG-TOKEN-8841 …"}`, and the model's final text does not mention it.
  The Stop gate's 3rd-block "allow + tell the user" relies on this (run 3).
- **`onFailure` does not apply to Stop** (per the docs). `stop_gate.py` therefore handles its own errors: with a
  claim present it blocks with "status could not be computed"; otherwise it allows.
- **There are no Grep or Glob tools in these sessions.** The model received "No such tool available: Grep … use grep
  via the Bash tool" (run 1). The H5 matcher `Read|Grep|Glob` is kept for builds that have those tools. The
  eval-hidden rule is enforced for `cat/head/grep -r/rg/find` in `guard_bash` as well, and is tested.
- **MultiEdit is not a current tool name.** It is kept in the exact-name matcher list as DESIGN says, where it is
  harmless.
- A blocking Stop hook also raises a cosmetic `stop-hook-error` notification ("Stop hook error occurred").
- **Additional context reaches the model.** SessionStart `additionalContext` was used by the model: it reported the
  probe's secret word. The real status line plus guard list also arrived, and the model quoted the status line.
- **Live Stop gate (run 2).** The model chose not to write the requested false claim. The gate still blocked once, on
  the phrase "one completed with a warning" (a known false positive of the 08 §5b regex, see
  `skills/guard/RULES.md`). The block text then made the model rewrite its summary in the required form: status line
  first, failures listed, no claim.

### Latency (this host, `python3 -I`, 5 runs each)

Idle (no `migration/`): about 21–34 ms for every hook, within the < 100 ms of G13 (15 Oct: about 10 ms through
`run-hook.sh`, which no longer starts Python when there is no migration). Active, on the synthetic project:
guard_write 64–75 ms, guard_bash 75–88 ms, post_scan 85 ms, session_start 68–88 ms (includes `ladder.compute`),
stop_gate 90–118 ms (ladder plus drift checks).

## Fallbacks

**No mechanic failed, so no guard falls back to its script half alone.** Two limits remain:

1. **HM2, interactive.** Whether `ask` actually prompts in an interactive session was not observable headless.
   Headless (`-p`, evals) resolves `ask` to deny in both bypass and default mode, which is the behaviour D8-10 wants.
   If an interactive build ever skipped the prompt, the script halves would still hold:
   - `compare.py` refuses on tolerance drift (`TOLERANCE_CHANGED_AFTER_RUN`).
   - `ladder.py` ignores a KD that has no human owner.
   - `ladder.py` caps fitted parameters.
2. **Grep/Glob.** H5 cannot fire for tools that do not exist. The eval-hidden protection rests on `guard_bash` and on
   the harness's separate-user fence (DESIGN §7.4).

## Reproducing

Do not re-run on every build; the eval budget is tight. To reproduce:

1. Load the session token into the environment only: `set -a; . ~/.config/axis-migrate/secrets.env; set +a`.
2. Point `CLAUDE_CONFIG_DIR` at an empty temporary directory.
3. Run `claude -p --model haiku --plugin-dir <probe> --output-format stream-json --verbose --include-hook-events`.

The probe plugin used for run 1 dumped stdin and returned deny, ask, crash, sleep or block according to a token in
the tool input. It is not shipped. Its logic is the table above.

## One hooks.json for Claude Code and Codex

`hooks/hooks.json` is the single hook file for both harnesses. Najah decided this on 14 Oct 2026; Codex hooks still
run only after the user trusts them. It follows the Codex hook contract at https://learn.chatgpt.com/docs/hooks.md.
There has been no Codex run on this host: the Codex side is covered by `hooks/tests/test_codex.py` and its payload
fixtures in `payloads/codex_*.json`, which are built from that contract.

**Commands.** Every command is `sh "${CLAUDE_PLUGIN_ROOT:-$PLUGIN_ROOT}/hooks/run-hook.sh" <x>`, which runs
`hooks/bin/<x>.py` (15 Oct 2026; before, the commands were `PYTHONIOENCODING=utf-8 python3 -I …/hooks/bin/<x>.py`).
Claude Code passes `CLAUDE_PLUGIN_ROOT` in the environment, which run 4 confirmed: all hooks fired and a legacy
write was denied. Codex sets both variables. The scripts find the plugin from their own file path in either case.

### The sh wrapper: hooks without a working python3 (`hooks/run-hook.sh`)

A fresh Mac has no python3 but the Xcode Command Line Tools stub at `/usr/bin/python3`, which opens an install
dialog and fails; Windows may have no Python at all. With `python3` in the commands and `onFailure: block`, such a
machine would have had every Write and Bash blocked in **every** project. The wrapper is POSIX sh (macOS `/bin/sh`,
dash, `bash --posix` and Git Bash; tested under the first three):

1. It reads stdin once and passes it to Python byte for byte.
2. It looks for `migration/graph.json` walking up from every `cwd` in the payload, from the folder of every
   `file_path`/`notebook_path`, and from the hook process cwd (not for Stop). JSON values are pulled out with
   grep/sed; a value with `\u`/`\n`/`\t` escapes cannot be decoded in sh and goes on to Python. **No migration/ →
   exit 0 at once, without Python** (about 10 ms here, against 21–34 ms for an idle Python hook). Always on to Python:
   `session_start` (hooks-active marker and secrets file, even before migration/ exists) and a payload holding an
   apply_patch text (absolute paths).
3. Interpreter: the plugin's own uv environment first, by the `scripts/axpy` rule
   (`${CLAUDE_PLUGIN_DATA}/env-<lock8>`, `${PLUGIN_DATA}/env-<lock8>`, `${XDG_CACHE_HOME:-~/.cache}/axis-migrate/env-<lock8>`,
   then any `env-*` there, since the hooks need the standard library only; `Scripts/python.exe` too), then `python3`
   and `python` from PATH (on macOS also `/opt/homebrew/bin/python3` and `/usr/local/bin/python3`). A PATH python is
   used only if `python -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'` exits 0 within 3 s
   (a watchdog kills it otherwise). On macOS `/usr/bin/python3` is not even started unless `xcode-select -p`
   succeeds, so the stub's dialog never opens.
4. No interpreter inside a migration: **NEEDS_PYTHON3**, naming the fix (install uv; run
   `"$P/scripts/axpy" --print-python` once, which builds the plugin environment the hooks then use; then
   `"$P/scripts/axpy" --doctor`).

| Hook | NEEDS_PYTHON3 behaviour |
|---|---|
| guard_write, guard_bash, guard_hidden (PreToolUse) | deny: exit 2, reason on stderr. Only the two fix commands pass, written exactly as `"<plugin>/scripts/axpy" --print-python` or `--doctor` (also with `${CLAUDE_PLUGIN_ROOT}`). |
| post_scan (PostToolUse) | exit 2 (block feedback; the write has happened). |
| stop_gate (Stop) | exit 2 once, only for a session listed in `migration/.axis/sessions.json` and only while `stop_hook_active` is false (grep, no Python); otherwise allow (`{}` under Codex). |
| session_start | allow, with the reason as `additionalContext`. |
| graph_changed | allow, silent. |

Not verified: a real macOS CLT stub (simulated here with a fake `uname`/`xcode-select`), and Windows (Git Bash
runs hooks there per the Claude Code docs; not run).

**Matchers.** The write matcher is `Write|Edit|MultiEdit|NotebookEdit|apply_patch`. Claude Code reads it as a list of
exact names; Codex reads it as a regex, where it is a plain alternation. PostToolUse also has a `Bash` entry so that a
Codex `apply_patch <<EOF` written in the shell is scanned; without a patch it returns at once. `Read|Grep|Glob`
exists only in Claude Code. Codex reads files through the shell, and `guard_bash` covers those reads.

### What differs under Codex, and how the scripts handle it

| Codex behaviour | Handling |
|---|---|
| File edits arrive as `tool_name: "apply_patch"`, with the patch text in `tool_input.command`. They can also arrive inside a Bash heredoc. | `recordparse.parse_patch` reads `*** Add / Update / Delete File`, `*** Move to`, `@@` hunks and `*** End of File`, for any number of files. `guard_write.decide` checks every target path with the same rules as Write/Edit. A move counts as a deletion of the source plus a write of the destination. For the files whose content is checked (`decisions.md`, `known-differences.md`, `tolerances.yaml`, `tolerances.proposed.yaml`, `params.yaml`), the hunks are applied to the current text. A hunk that cannot be applied is a deny, with "cannot apply the patch … fail closed". A malformed patch exits 2. `guard_bash` sends heredoc patches through the same checks. `post_scan` scans every file the patch adds, updates or moves into place. |
| `permissionDecision: "ask"` is parsed but not supported: the run is marked failed and **the tool call continues**. | Under Codex, `_hooklib.pre_decision` turns every ask into a deny, with the note "Codex has no permission prompt … only the person makes or approves this change". |
| There is no `onFailure`. Codex blocks on exit 2. | The guards already exit 2 on any exception. Codex's behaviour on a hook timeout is not documented and not measured. |
| Stop with `decision: "block"` continues the session, using the reason as a new prompt. `stop_hook_active` is present. Stop must print JSON when it exits 0. | Same gate (claims, and the visual-review check on the latest run) and the same 3-block cap. On allow, the gate prints `{}` under Codex. |
| There is no `CLAUDE_ENV_FILE`. | session_start tells the agent to prefix GDAL commands with `GDAL_PAM_ENABLED=NO`. `scripts/axpy` sets it for plugin scripts. |

**Harness detection** (`_hooklib.harness`). The session is Codex if `PLUGIN_ROOT` or `PLUGIN_DATA` is set (Claude
Code sets only the `CLAUDE_*` names), or if the payload has `tool_name: "apply_patch"` or a `turn_id`. Otherwise it is
Claude Code. Two signals do not count: `CLAUDE_PLUGIN_ROOT`, because Codex sets it too, and `model`, because Claude
Code may send it.

### hooks-active marker (contract with the Codex start skill)

SessionStart writes the marker on every session start, in both harnesses, and even before `migration/` exists. Claude
Code's matcher includes `fork`; Codex's does not:

```
${PLUGIN_DATA:-${CLAUDE_PLUGIN_DATA:-~/.cache/axis-migrate}}/hooks-active.json      (atomic replace)
{"schema": "axis-migrate.hooks-active.v1", "at": "2026-10-14T09:12:00Z", "harness": "codex"|"claude",
 "cwd_sha256": "<sha256 of realpath(session cwd)>", "session_id": "...", "source": "startup|resume|...",
 "migration_found": true|false, "plugin_root": "...", "hooks": ["session_start", ...]}
```

The reader is `skills/guard/hooks_active.py [--cwd DIR] [--max-age-h 24] [--json]`. It exits 0 when a marker exists
for this working directory and is younger than 24 hours, and 1 otherwise.

The start skill should call it at the beginning of a session. On exit 1 the session must be treated as unguarded:
the script halves still refuse, but late (CODEX.md). Run 4 found the Claude Code marker under
`<config>/plugins/data/axis-migrate-inline/` with `harness: claude`, `migration_found: true`. The reader therefore
also looks under `${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/data/axis-migrate*/` (15 Oct), so the migrate skill's
step 0 can run the same check under Claude Code: a disabled plugin or a `--plugin-dir` typo is otherwise silent.

### Codex probe on the founder's laptop (desktop app and CLI 0.162.0)

These results override the docs where they differ.

- **Payloads.** They match `payloads/codex_*.json`. The stdin keys are `cwd, hook_event_name, model, permission_mode,
  session_id, tool_input, tool_name, tool_use_id, transcript_path, turn_id`. Bash sends `tool_input.command`.
  `apply_patch` sends `tool_input.command` = the full patch text, with **absolute** paths. There is no Read tool: reads
  are Bash `cat` and similar. Exit 2 with a reason on stderr is shown to the agent.
- **The cwd trap.** In the desktop app the hook **process** ran in the session root (`~/Documents/projects`) while the
  shell ran in a subfolder. The fixes:
  - `guard_bash`, `guard_write`, `guard_hidden` and `post_scan` resolve relative paths against stdin `cwd`, and
    **also** against the hook process cwd when the two differ. A deny from either base stands. `guard_bash` runs the
    whole command check once per base.
  - `migration/` is located from stdin `cwd` first, then from the process cwd.
  - Tests: `hooks/tests/test_cwd_and_payloads.py`, both directions.
- **Plugin data folder.** `PLUGIN_DATA`/`CLAUDE_PLUGIN_DATA` point at `~/.codex/plugins/data/<plugin>-<marketplace>`,
  which may not exist yet. The only writer there is the hooks-active marker, and it runs `mkdir -p` (tested). Codex
  passes `PLUGIN_DATA` to hooks, not to the shell, so `hooks_active.py` also looks under
  `${CODEX_HOME:-~/.codex}/plugins/data/axis-migrate-*` (orchestrator change b915c38).
- **Installing is not trusting.**
  - Each hook is trusted separately in `/hooks`: select the definitions, then press `t`.
  - A changed hook definition needs to be reviewed again. A plugin update can therefore **silently drop the guards**
    until the user trusts them again, so check `hooks_active.py` after every update.
  - A chat that is already open does not load a newly installed plugin. Start a new chat.

### Stop gate scope: the session, not the folder (15 Oct 2026, review §3)

The Stop gate used to apply to any session whose cwd sat at or below a folder holding `migration/`, so an
orchestrator session talking about an eval project was gated as if it were migrating. Now `guard_bash`,
`guard_write` and `post_scan` record the payload `session_id` in `migration/.axis/sessions.json`
(`_hooklib.touch_session`; never raises) when a tool call writes or reads under `migration/` (a Write/Edit/patch
target, or a Bash token that resolves under it from either base) or runs a plugin script (`scripts/axpy`,
`skills/<x>/<y>.py`). `stop_gate` allows every session not listed there, and finds `migration/` from the payload
`cwd` only. The read and write guards stay global: they protect the legacy folder for every session. A Read-tool
read under `migration/` does not record a session; a Bash read (`cat migration/graph.json`) does. Sessions are
per session id: after `/clear` the new session is gated from its first tool call that touches the migration.

Cost: `stop_gate.decide` returns at once for a message with no claim and no status line and no unacked user graph
edits (no ladder, no hashing). `legacy_drift` hashes through `lib/hashcache.py` (sha256 by path, size and mtime_ns
in `migration/.axis/hash-cache.json`); a second Stop over a 300 MB legacy tree costs a stat per file (test).

### Payload failure modes (all hooks, both harnesses)

Outside a migration (no `migration/graph.json` from any base) the wrapper exits 0 before Python runs, whatever the
payload, so the rows below apply inside a migration only.

| Hook | Missing, empty or unparseable stdin, or a payload without `tool_name` | Exception inside the hook |
|---|---|---|
| guard_write, guard_bash, guard_hidden (PreToolUse) | **deny**: exit 2 with an ASCII reason on stderr. Also for a Bash payload without `command`, or an `apply_patch` payload without patch text. | deny (exit 2). Under Claude Code `onFailure: block` also covers crashes and timeouts. |
| post_scan (PostToolUse) | exit 2 (block feedback). The write has already happened; this is fail-safe feedback, not prevention. | block (exit 2) |
| stop_gate (Stop) | exit 2. Claude Code shows the reason and the turn continues; the built-in 8-continuation cap applies. | handled internally: with a claim present it blocks with "status could not be computed", otherwise it allows |
| session_start, graph_changed (informational) | fail open: exit 0 | fail open, with a one-line note |
