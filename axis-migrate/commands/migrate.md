---
description: Migrate a legacy GIS project folder to a verified geopandas/rasterio pipeline (discover → interview → plan → migrate step by step → verify → report → Databricks bundle, not deployed)
argument-hint: <legacy-folder>
---

# /migrate $ARGUMENTS

You are migrating the legacy GIS project at `$ARGUMENTS` into an open-source Python pipeline and proving, step by
step, that it reproduces the legacy run's own intermediates and finals. You write code, read files, propose
parameters, ask questions and look at plots. **Scripts decide verdicts and status; you never do.**

`$P` below means `${CLAUDE_PLUGIN_ROOT}`. Run every plugin script as `"$P/bin/axpy" "$P/skills/<skill>/<script>.py" …`
(the one pinned environment). Work from the project directory (the parent of the legacy folder); everything you
create goes under `./migration/`. The legacy folder is read-only: never write, move, `touch` or `gdalinfo -stats`
anything in it.

## Hard rules (the hooks and scripts enforce them; do not try to work around a refusal)

1. Never ask what the files answer. Investigate first; ask only what the files cannot tell and parity cannot catch.
2. Never fabricate: a missing input stops the run (exit 12) and becomes a question.
3. `graph.json` changes only through `graph.py apply`; values only in `params.yaml`; reasoning in `decisions.md`
   (append-only); tolerances only as records with two measured numbers that **a person signs** — you may propose,
   never sign (`agreed_by` stays null; it goes to `migration/SIGN-OFF.md`).
4. Pipeline code reads data only through `ctx` and parameters only through `ctx.param(id)`; it never opens legacy
   intermediates or finals. Only the runner executes the pipeline.
5. You may only **flag** in visual review. You never produce or upgrade a verdict, widen a tolerance, own a known
   difference, or claim a status that `ladder.py` does not compute. Quote the status line it prints.

## Flow (follow the skills in this order; each SKILL.md has the exact commands)

1. **Discover** — skill `discover`: scan, input map (look at every PNG and record findings with numbers), lineage →
   `graph.json`, start the graph page and give the user its URL.
2. **Investigate and interview** — skill `interview`: fill `params.yaml` for every output-changing parameter with
   citations; run `params_check.py`; ask the few remaining questions one at a time (AskUserQuestion, recommended
   option first, explicit "Don't know"); `confirm_all` then the target question last.
3. **Plan** — skill `migrate` §Plan: one `plan` block per step, `graph.py render-plan`, one approval question.
   No `pipeline/` file may exist before approval.
4. **Migrate step by step** — skill `migrate`: in topological order write one step module, let the scanner check it,
   run it in isolation (`run` skill), verify it (`verify` skill: compare, plots, **look at the plots**, review-add),
   repair at most 3 times with spec + current error only, then the next step.
5. **Chained run and verify** — skill `run` (chained, from raw inputs), skill `verify` (compare every pair, plots,
   visual review of every compared raster/vector, aggregate).
6. **Evidence and report** — skill `report`: seal, ladder, report; give the user `migration/report/index.html`.
7. **Target** — skill `target` if the user chose Databricks: generate the bundle, run the offline checks and dry run,
   seal the target evidence, re-render the report. Never deploy, authenticate or contact a workspace.
8. **Finish** — your last message starts with the exact status line from `ladder.py`, lists every failing, unsigned
   or not-compared item by name, points to `migration/SIGN-OFF.md` if it has entries, and claims nothing more.

If `migration/` already exists, resume: read `graph.json`, `params.yaml`, `decisions.md`, `runs/index.jsonl`; never
trust verdicts from an earlier run — re-run verify on a new run.
