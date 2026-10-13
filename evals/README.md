# axis-migrate evals (dev repo only; never shipped in the plugin)

Design: docs/design/10-evals.md and DESIGN §7. Decisions measured on 13 Oct (night):

- **Question route (AT-7): Agent SDK.** In `claude -p` (2.1.296) the AskUserQuestion tool is not available at all
  (ToolSearch "No matching deferred tools found"). Through `claude-agent-sdk` 0.2.165 (`ClaudeSDKClient`,
  `can_use_tool`), AskUserQuestion is called and the callback answers it by returning
  `PermissionResultAllow(updated_input={**inp, "answers": {question_text: label}})`; the agent receives the label.
  So the scripted oracle answers questions in `can_use_tool`, matching on the graph question id / node / param cited.
- **Fence:** unix user `axis-eval` (cannot read /home/migrate). Trials run entirely as that user
  (`sudo -n -u axis-eval`), with Claude Code 2.1.296 and uv copied into its home; the token is passed with
  `sudo --preserve-env=CLAUDE_CODE_OAUTH_TOKEN` (never printed). `/usr/bin/claude` is 2.1.143: do not use it.
- Harness env: `evals/harness-env` (uv; claude-agent-sdk, pyyaml) — separate from the plugin lock.
- **Permission probe (13 Oct):** with the SDK in `bypassPermissions` (and `acceptEdits`), `can_use_tool` receives only
  (a) AskUserQuestion and (b) tool calls a PreToolUse hook answered with `ask`; ordinary Bash/Write run without the
  callback. So the driver answers questions from the oracle and denies every hook `ask` (headless = deny, D8-10),
  logging it to `permission_asks.jsonl` (counted as an attempt).
