---
name: start
description: Codex entry point for axis-migrate (what /migrate is under Claude Code). Use first whenever the user asks to migrate, port or verify a legacy GIS project folder (PyQGIS, ArcPy, notebooks, Excel) with axis-migrate, or to resume one that already has a migration/ folder. Sets the plugin root, the chat question fallback and the finish rule, then hands over to commands/migrate.md and the discover, interview, build (plan + write the codebase), run, verify, report and target skills.
---

# axis-migrate under Codex

This file adds nothing to the method. It only says how to follow the Claude Code texts under Codex.

1. **Plugin root.** `P` = the absolute directory that contains `.codex-plugin/plugin.json`, i.e. three levels above
   this file (`<P>/codex/skills/start/SKILL.md`) and two levels above every other skill's `SKILL.md`. Wherever a text
   says `${CLAUDE_PLUGIN_ROOT}`, `$PLUGIN`, `$P` or "plugin root", use that absolute path. Shell state may not persist
   between commands, so write the literal path in every command, e.g. `"<P>/bin/axpy" "<P>/skills/discover/scan.py" …`.
2. **Read** `<P>/CODEX.md` (what is and is not guarded without hooks), then `<P>/commands/migrate.md` and follow it
   exactly. `$ARGUMENTS` there = the legacy folder the user named. If they named none, ask for it and stop.
3. **No hooks run under Codex.** The hard rules in `commands/migrate.md` are not advisory: nothing will stop a
   wrong write early, the scripts only refuse it later. Never write to the legacy folder, `migration/graph.json`,
   `graph-edits.jsonl`, `runs/`, or a sealed run; never edit earlier blocks of `decisions.md` or
   `known-differences.md`; never create `pipeline/sNN_*.py` before the plan is approved; never run pipeline code
   except through `run_local.py`. Prefix any GDAL command of your own with `GDAL_PAM_ENABLED=NO`.
4. **Questions (no AskUserQuestion).** Every place a skill says AskUserQuestion (interview questions, plan approval,
   E2B upload approval) becomes the chat fallback in `skills/interview/SKILL.md`: print exactly one numbered question
   block (options 1..n, "(Recommended)" first, `0 = don't know`, "or type an answer"), end the turn, and parse the
   next user message. Record it as the skill says (`by: user (chat)`; graph ops keep `--channel interview
   --verbatim "<the user's words>"`). Never answer a question yourself and never approve a plan without the user.
5. **Finish (no Stop gate).** Before any summary, run `python3 -I "<P>/skills/report/ladder.py" migration` and start
   your last message with its status line, quoted exactly. Then list every failing, unsigned or not-compared item by
   name and claim nothing above that line. Check the draft with `python3 -I "<P>/skills/guard/claims.py" --json
   --rung <pipeline rung from ladder.py --json> < draft.txt`: exit 1 means a claim is above the rung; remove it.
