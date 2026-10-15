# Trigger evals: does the right entry skill fire, and only then?

Dev repository only (never shipped in the plugin). `queries.json`: 10 should-trigger and 10 near-miss queries for
`migrate` (Claude Code) and for `start` (Codex). Near misses: general GIS coding, a one-off geopandas/GDAL/QGIS
question, Databricks setup, "migrate" in a non-GIS sense, map or raster comparison outside a migration.

Two ways to run them:

1. **Router check** (`route_prompt.py` + `score.py`, cheap, no plugin load). Builds the skill listing the harness
   would show (our frontmatter at a git revision or the working tree, plus six generic distractor skills; Codex lists
   only skills whose `agents/openai.yaml` leaves implicit invocation on) and the queries in a shuffled order without
   their ids; a model answers which skill it would invoke for each; `score.py` grades it against a separate key.
   ```
   python3 -I route_prompt.py --harness codex --key /tmp/key.json > /tmp/prompt.txt   # give prompt.txt to a model
   python3 -I score.py /tmp/key.json answers.json
   ```
   Limits: one model sees all 20 queries at once (it can calibrate across them), no conversation, no real listing
   budget. It measures description wording, not the harness.
2. **End to end, Claude Code** (`make_cases.py` + `run_plugin_eval.sh`): one `claude plugin eval` case per Claude
   query (should-trigger: the Skill tool is called with `axis-migrate:migrate`; near-miss: no `axis-migrate:` skill
   is called), run on a scratch copy of the committed plugin, one arm, one run each, `--max-cost-usd 5`. Needs a
   logged-in Claude Code 2.1.269 or later. Codex queries are not covered (Claude Code does not load `codex/skills/`).

## Results (15 Oct 2026, router check; files in `results/2026-10-15-router/`)

| Arm | Descriptions | Model | Should trigger | Near miss quiet |
|---|---|---|---|---|
| Claude, before | HEAD 82356cc | sonnet | 10/10 | 10/10 |
| Claude, final | this commit | sonnet | 10/10 | 10/10 |
| Claude, final | this commit | haiku | 10/10 | 10/10 |
| Codex, before | HEAD 82356cc (all 9 skills listed) | sonnet | 10/10 | 10/10 |
| Codex, first rewrite | "Use first under Codex …" (2 listed) | sonnet | **2/10** (8 picked migrate) | 10/10 |
| Codex, final | "Use first, before the migrate skill (this is the Codex entry point …)" + migrate says "Under Codex, axis-migrate:start comes first" | sonnet | 10/10 | 10/10 |
| Codex, final | same | haiku | 10/10 | 10/10 |

What it changed: the first short rewrite of `start` lost the "entry point" framing and the router preferred
`migrate` for 8 of 10 Codex migrations; naming start as the entry point that comes before migrate (in both
descriptions) and saying in the prompt which harness is running fixed it. The Claude arms did not separate old from
new descriptions on these queries; the gain there is length (the listing budget), not routing.

Not yet run: `run_plugin_eval.sh` (the CLI on the dev server is not logged in: `claude plugin eval` loaded the
cases and stopped at "Not logged in").
