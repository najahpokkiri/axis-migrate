# axis-migrate under Codex: script halves only, no hooks

Founder decision: Codex gets the skills and scripts, never the hooks. It is the first thing to drop if it costs too much.
codex-cli 0.144.4 lists `plugin_hooks` as `removed` (`codex features list`), so `hooks/hooks.json` (Claude Code format,
`${CLAUDE_PLUGIN_ROOT}`) does not run. Codex docs say plugin hooks default to `hooks/hooks.json`: if a later Codex
re-enables them, re-measure before anyone trusts them. `skills` lists two roots (`./skills/`, `./codex/skills/`):
0.144.4 loads both, but the directory validator wants `skills` only, so this build is for local/Git marketplaces.
Manifest `.codex-plugin/plugin.json`; local marketplace `.agents/plugins/marketplace.json` (root = this folder).
Install: `codex plugin marketplace add <clean checkout of this folder>` then `codex plugin add axis-migrate@axis-migrate-local`.
Install copies the folder into `$CODEX_HOME/plugins/cache/axis-migrate-local/axis-migrate/<version>/`. The pinned
environment is not in the plugin folder: `bin/axpy` builds it in `${CLAUDE_PLUGIN_DATA}` or `~/.cache/axis-migrate/`. Start a session with
`$axis-migrate:start`, which resolves the plugin root and hands over to `commands/migrate.md`.

## What still guards the run (script halves; they refuse late, not early)

| Hook under Claude Code | Script half under Codex | Refuses with |
|---|---|---|
| H6 scanner after each write | `codescan.py` in the runner preflight | run aborts `SCAN_BLOCKED`, `SCAN_TARGET_CHANGED`; ladder `SCAN_BLOCKED`/`SCAN_MISSING` |
| H4 read ban, `UNLEDGERED_RUN` | `run_local.py` runs each step under `strace` (read and write ledger) | `UNDECLARED_READ`, `UNDECLARED_WRITE`, `OUTPUT_NOT_IN_WRITE_LEDGER`; a run outside the runner has no run dir, so it is never evidence |
| H3 `ask` on tolerance change | `compare.py` contract check | `TOLERANCE_CHANGED_AFTER_RUN` → `EVALUATOR_ERROR`; ladder ignores KDs without a human owner and caps fitted params |
| H3 protected files after a run | `ladder.py` drift checks | `GRAPH_CHANGED_AFTER_RUN` (graph_contract_sha256), `PARAMS_`, `DECISIONS_`, `KNOWN_DIFFERENCES_`, `LOCK_`, `CODE_CHANGED_AFTER_RUN` |
| H3/H4 legacy folder, PAM sidecars | runner preflight + ladder hashes; `bin/axpy` exports `GDAL_PAM_ENABLED=NO` | `LEGACY_REF_CHANGED` (incl. `aux.xml`), `LEGACY_INPUT_CHANGED_DURING_RUN` |
| H3 sealed runs | `seal.py` hashes, ladder integrity | `EVIDENCE_HASH_MISMATCH`, `EVIDENCE_FILE_MISSING`, `EVIDENCE_CHAIN_BROKEN`, `SEALED_RUN_TOUCHED` |
| H7 Stop gate on claims | none; the agent must quote `ladder.py`'s status line first (start skill §5) and may self-check with `claims.py --rung` | nothing refuses: this is agent compliance only |

## Not protected without hooks (stated plainly)

- **Damage is detected, not prevented.** A Write or shell command into the legacy folder happens; hashes only show it
  later, and only for legacy files the graph hashes. Stray new files elsewhere in the legacy folder go unnoticed.
- **Direct writes to `graph.json` / `graph-edits.jsonl`.** `graph.py` has no out-of-band check. After a run the edit
  shows as `GRAPH_CHANGED_AFTER_RUN`; before the first run a forged edit (a plan `approved` entry, a changed pairing
  or scope) becomes the baseline and no script sees it. Only comparing `graph-edits.jsonl` with `graph.json` would.
- **Plan approval gate.** Nothing refuses `pipeline/sNN_*.py` before approval; the runner does not check `approved`.
- **Append-only `decisions.md` / `known-differences.md`** before a run: rewrites are not detected (after a run they
  are, as `*_CHANGED_AFTER_RUN`).
- **Human `ask` prompts, i.e. signatures.** No Codex equivalent. `compare.py` only checks that `agreed_by` looks like
  a person (`_is_human`), so a name the agent writes into `tolerances.yaml` or a KD before the next run counts as
  signed. Only a human reviewing the `tolerances.yaml` / KD diff catches it.
- **Session status (H1) and unacked graph edits (H2).** Run `ladder.py` and `graph.py diff --since-seq N` yourself.
- **H4 denials with no script half:** Databricks deploy/auth commands, forged graph ops via `curl` to the graph
  server, PAM-writing GDAL tools the agent runs itself. The skills forbid them; nothing enforces it.
- **H5 eval hidden paths:** evals under Codex must rely on the separate-user fence (DESIGN §7.4).

## Not measured (dev host could not start Codex's bwrap sandbox; no Codex auth for `codex exec`)

- Whether `strace` (ptrace) works inside Codex's `workspace-write` sandbox. Without it the run uses the weaker
  `audit+cpl_debug` ledger only if `strace` is absent; if it is present but refused, steps fail.
- The first `bin/axpy` call runs `uv sync --frozen` into `~/.cache/axis-migrate/env-<lock8>`, outside the workspace,
  and needs network. Expect to grant both, or pre-build it with `bin/axpy --doctor`.
- How a Codex model follows the skills end to end. Install, listing and skill discovery were checked; a run was not.

Sources (retrieved 14 Oct 2026): https://developers.openai.com/codex/plugins/build ·
https://learn.chatgpt.com/docs/plugins · https://learn.chatgpt.com/docs/build-skills · `codex plugin --help`
(0.144.4) · the plugin-creator validator embedded in the codex binary.
