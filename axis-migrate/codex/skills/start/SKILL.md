---
name: start
description: Use first, before the migrate skill (this is the Codex entry point for axis-migrate), when the user asks to migrate, port, rebuild or resume a whole legacy desktop GIS project (QGIS, ArcPy/PyQGIS, notebooks, Excel) as verified Python. Checks the hooks and sandbox approvals, then hands over to migrate. Not for general GIS coding, a one-off geopandas question or Databricks setup.
---

# axis-migrate under Codex (thin layer: the method is in `skills/migrate/SKILL.md`)

1. **Plugin root and hooks.** Run `python3 -I "<P>/skills/guard/hooks_active.py" --cwd "$PWD" --json`, where `<P>`
   is first the folder three levels above this file (holding `.codex-plugin/plugin.json`); plain `python3`, so no
   environment sync starts inside the sandbox. If the JSON has `plugin_root`, that is `P` from now on. Wherever a text
   says `${CLAUDE_PLUGIN_ROOT}`, `$PLUGIN` or `$P`, write `P` literally; `$ARGUMENTS` = the legacy folder the user
   named (none named: ask, then stop). Exit 0: say the hooks are active and go on.
   Exit 1: run `python3 -I "<P>/skills/guard/codex_trust.py" --cwd "$PWD"` (which hooks are missing or untrusted),
   say plainly "The axis-migrate guards are OFF: its hooks are not active in this chat", give the trust steps — open `/hooks` (desktop: Review hooks), select axis-migrate's definitions, press `t`, then start a NEW chat
   (an open chat never loads a newly installed plugin; every plugin update needs re-trust) — and do not write
   anything under `migration/` or run a pipeline until the person confirms (trusted and restarted, or "go on
   unguarded", recorded as a decision). Never claim or imply that guards are on.
2. **Sandbox approvals** (Codex `workspace-write` has no network and cannot write outside the workspace). Run these
   with escalated permissions, each once, and say why when Codex asks the person (unverified in the default mode:
   if one fails, quote its FAIL line and ask for that one approval only; Claude's `allowLocalBinding` does not apply):
   - `"<P>/scripts/axpy" --doctor` (first run `--doctor --no-sync` inside the sandbox: checks without downloading):
     first use downloads the pinned environment into `~/.cache/axis-migrate/` (network + a write outside the workspace);
   - the map environment's first sync (`skills/map/`, GeoLibre 3.4.0 lock): same reasons;
   - `graph_server.py serve migration` and the map server (`skills/map/start.py migration`): they bind a port on
     127.0.0.1, which the sandbox refuses; keep them running in the background;
   - `run_local.py` when `--doctor` reports strace unusable inside the sandbox (ptrace), otherwise runs are capped.
   If the person refuses, stop and quote the one-line FAIL the script printed; never fall back to another way.
3. **Flow.** Read `<P>/CODEX.md`, then follow `<P>/skills/migrate/SKILL.md` exactly (plan and build detail in
   `<P>/skills/migrate/BUILD.md`, maps in `<P>/skills/map/SKILL.md` where it exists). Hook or script refusals are
   final: report them, never work around them.
4. **Codex differences, everywhere in the skills.** AskUserQuestion → `request_user_input_async` (options) when it
   exists, else one plain-text question with numbered options, recommended first, plus "Don't know"; then stop and
   wait (an elapsed wait is never an answer). `mcp__Claude_Browser__*` → give the URL once as a clickable link, open
   it in the in-app browser, then read and click as in Claude (annotate mode is not a channel). Read on a PNG →
   `view_image`. Never write a server token in chat.
5. **Finish.** Start the last message with the status line of `python3 -I "<P>/skills/report/ladder.py" migration`,
   quoted exactly; list every failing, unsigned or not-compared item; claim nothing above that line.
