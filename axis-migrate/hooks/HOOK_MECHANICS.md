# Hook mechanics HM1–HM7: measured on Claude Code 2.1.296

These are the probes from 08 §6, run on 13 Oct 2026 with headless `claude -p --model haiku`. Each run used a fresh
`CLAUDE_CONFIG_DIR`, so no user plugins or hooks were loaded, and the session token came from the environment. Three
invocations in total, out of a budget of 6:

| Run | What | Mode | Plugin |
|---|---|---|---|
| 1 | Mechanics probe and stdin capture | `--permission-mode bypassPermissions` | scratch probe plugin (`--plugin-dir`) + a `--settings` hook |
| 2 | axis-migrate end to end on a synthetic project | default mode, `--allowedTools Bash,Write,Edit,Read` | this plugin (`--plugin-dir axis-migrate`) |
| 3 | Stop block chain and `systemMessage` | default mode | scratch probe plugin |

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

Idle (no `migration/`): about 21–34 ms for every hook, within the < 100 ms of G13. Active, on the synthetic project:
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
