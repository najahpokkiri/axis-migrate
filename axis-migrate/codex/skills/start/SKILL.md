---
name: start
description: Codex entry point for axis-migrate (what /axis-migrate:migrate is under Claude Code). Use first whenever the user asks to migrate, port or verify a legacy GIS project folder (QGIS, ArcPy/PyQGIS, notebooks, Excel) with axis-migrate, or to resume one that has a migration/ folder. Checks that the plugin's hooks are active, says which commands need sandbox approval, then hands over to skills/migrate/SKILL.md for the whole flow.
---

# axis-migrate under Codex (thin layer: the method is in `skills/migrate/SKILL.md`)

1. **Plugin root.** `P` = the absolute folder holding `.codex-plugin/plugin.json` (three levels above this file, two
   above every other `SKILL.md`). Wherever a text says `${CLAUDE_PLUGIN_ROOT}`, `$PLUGIN` or `$P`, write that path
   literally in every command; `$ARGUMENTS` = the legacy folder the user named (none named: ask, then stop).
2. **Hooks.** Run `python3 -I "<P>/skills/guard/hooks_active.py" --cwd "$PWD" --json` (stdlib; plain `python3` so
   it does not start the first environment sync inside the sandbox). Exit 0: say the hooks are active and go on.
   Exit 1: say plainly "The axis-migrate guards are OFF: its hooks are not active in this chat", give the trust
   steps — open `/hooks` (desktop: Review hooks), select axis-migrate's definitions, press `t`, then start a NEW chat
   (an open chat never loads a newly installed plugin; every plugin update needs re-trust) — and do not write
   anything under `migration/` or run a pipeline until the person confirms (trusted and restarted, or "go on
   unguarded", recorded as a decision). Never claim or imply that guards are on.
3. **Sandbox approvals** (Codex `workspace-write` has no network and cannot write outside the workspace). Run these
   with escalated permissions, each once, and say why when Codex asks the person (unverified in the default mode:
   if one fails, quote its FAIL line and ask for that one approval only; Claude's `allowLocalBinding` does not apply):
   - `"<P>/scripts/axpy" --doctor`: first use downloads the pinned environment (`uv sync --frozen`) into
     `~/.cache/axis-migrate/` (network + a write outside the workspace);
   - the map environment's first sync (`skills/map/`, GeoLibre 3.4.0 lock): same reasons;
   - `graph_server.py serve migration` and the map server (`skills/map/start.py migration`): they bind a port on
     127.0.0.1, which the sandbox refuses; keep them running in the background;
   - `run_local.py` when `--doctor` reports strace unusable inside the sandbox (ptrace), otherwise runs are capped.
   If the person refuses, stop and quote the one-line FAIL the script printed; never fall back to another way.
4. **Flow.** Read `<P>/CODEX.md`, then follow `<P>/skills/migrate/SKILL.md` exactly (plan and build detail in
   `<P>/skills/migrate/BUILD.md`, maps in `<P>/skills/map/SKILL.md` where it exists). Hook or script refusals are
   final: report them, never work around them.
5. **Codex differences, everywhere in the skills.** AskUserQuestion → `request_user_input_async` (options) when it
   exists, else one plain-text question with numbered options, recommended first, plus "Don't know"; then stop and
   wait (an elapsed wait is never an answer). `mcp__Claude_Browser__*` → give the URL once as a clickable link, open
   it in the in-app browser, then read and click as in Claude (annotate mode is not a channel). Read on a PNG → `view_image`. Never
   write a server token in chat.
6. **Finish.** Start the last message with the status line of `python3 -I "<P>/skills/report/ladder.py" migration`,
   quoted exactly; list every failing, unsigned or not-compared item; claim nothing above that line.
