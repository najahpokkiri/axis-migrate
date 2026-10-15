---
name: interview
description: Sub-step of the axis-migrate migrate flow. Use after discover to resolve every output-changing parameter from the legacy files with citations, then ask the analyst only what the files cannot answer, one question at a time.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/axpy *) Bash("${CLAUDE_PLUGIN_ROOT}/scripts/axpy" *)
---

# Investigate, then interview

`$P` = plugin root. Scripts: `"$P/scripts/axpy" "$P/skills/interview/params_check.py" migration [--stage investigate|wired]`,
`"$P/scripts/axpy" "$P/skills/interview/fit.py" declare|run migration --param p.x`. Graph writes only through
`"$P/scripts/axpy" "$P/skills/discover/graph.py" apply migration --actor agent --base-rev N --ops ops.json` (op shapes:
`$P/skills/discover/GRAPH_OPS.md`). One owner per fact: values in `params.yaml`, questions and node state in
`graph.json`, reasoning in `decisions.md` (append-only).

## 1 Investigate (before any question)

For every step in topological order (`graph.py show migration`), read its legacy lines and the inventory:

1. List **every** parameter that can change an output: tool parameter dicts, constants, env/grid settings (snap,
   cell size, extent, output CRS), expression literals and operators (`>=` vs `>`), nodata, dtype, CRS of each
   distance/area operation, join keys and their normalisation (strip, case), sheet/header/row ranges, units and scale
   factors, resampling, all-touched vs cell-centre, zone assignment rule, output formats and layer names.
2. Resolve each by `reference/parameter-evidence.md`: `resolved` (a direct citation states it), `inferred`
   (method + confidence), `unresolved` (→ question). Cite `file:line` with a verbatim quote ≤120 chars;
   `params_check.py` rejects a quote that is not on the cited line. Read legacy intermediates for **structure only**
   (CRS, transform, shape, dtype, nodata, schema, counts) — values only inside `fit.py`.
3. A value copied from a legacy output header (e.g. nodata a tool wrote by default) is `source: reference_metadata`,
   `flags: [by_construction]`.
4. Inputs that are near-twins of other inputs (`inventory.derived_copies`) are **manual steps**: the files show what
   was done, never what the pipeline should do from now on — always a question.
5. Give every script, branch and input a scope with evidence (`graph.py apply` `set_scope` needs an answered
   question; until then record the proposal in the step's notes and in `confirm_all`). Dead or alternative code that
   the notes or headers retire is proposed `drop` with its reason, not asked separately.
6. Latent legacy bugs: measure the effect on the reference data in isolation. Zero effect → fix, record a
   `decisions.md` entry `kind: agent_decision` and a KD with bound 0 (`owner: agent`). Non-zero → a `legacy_bug`
   question, recommended **keep for parity**.
7. Fill `impact.datasets` from the graph's downstream closure. Add `p.measure_crs` (a local metric CRS, never degrees
   or EPSG:3857) whenever a vector dataset will be compared.
8. Write each entry with `record.py param` (§3). Every tool setting and literal the scan found in in-scope code is
   either cited by a parameter or waived: `record.py waive migration --id <id> [--id …] --reason "<why no output
   changes>"` (encoder options, thread counts; never a data type, CRS, extent, field name or formula constant). Run
   `params_check.py migration` until it exits 0 (`PARAM_COVERAGE` names what is still uncited).

Schema (`params.yaml`): `version, budget{max_questions: 8 (demo 5), asked}, params[]{id: p.<slug>, node, kind, value,
unit, resolution, source, evidence[{ref, quote}], reason, confidence, method, flags, impact{datasets, level},
status, question, decisions, used_by, fit}` and a `target:` block (09 §3.2). Never put a value in code.

## 2 Decide what to ask

Ask an item only if it is `unresolved`, or `inferred` with `score = impact × uncertainty × cost ≥ 12` (each 1–3;
cost is highest where parity cannot detect it: manual-step policy, keep-or-fix of legacy bugs, branches without a
reference, non-unique fits, target platform). Never ask what the files answer (a question whose `param_ids` include
a resolved parameter is blocked by `params_check.py`). Upstream first; budget 8 (demo 5), a soft limit: past it
params_check warns `BUDGET_EXCEEDED`, and each extra question must still meet the score rule.

## 3 Ask (one question per call)

Create the question in the graph first (`add_question`, shape in GRAPH_OPS.md, id `qNN`, linked `nodes`,
`param_ids`, `evidence`, `agent_guess` with reason, `impact`). Then AskUserQuestion with exactly one question:
header ≤12 chars; text `"[node · file:line] what the files show → what they cannot tell → my guess and why →
what changes downstream"`; options: recommended first labelled "(Recommended)", the alternatives, and
"Don't know" (→ the recommended option becomes a visible `assumed` value). Then:

- `answer_question` via `graph.py apply … --channel interview --verbatim "<the user's exact words>"`;
- record it with **one** command, which writes the decision and the params.yaml change together (validated by
  `params_check.py`, atomic, `budget.asked` + 1):
  `"$P/scripts/axpy" "$P/skills/interview/record.py" answer migration --param p.x [--param p.y] --question qNN --json
  entry.json` (entry fields `title, kind, answer, by, value` or `values`, `nodes, changes, supersedes, evidence`;
  "Don't know" → `kind: assumption`, the value becomes `assumed`). An `assumed` output-changing parameter that parity
  cannot see caps the status at sandbox-validated (`ASSUMED_PARITY_BLIND`) until a person confirms it.
- Every other params.yaml change goes through `record.py param migration --param p.x [--new] --json fields.json`
  and decisions without a parameter through `record.py decision`. Never rewrite params.yaml or decisions.md with
  Python or an editor (a hook refuses it); correct an entry by recording a new one with `supersedes: D-nnn`.

A free-text "Other" answer is restated as a concrete params change and confirmed once ("Recorded as X = …, correct?").

**`confirm_all`** (last question): write `migration/confirm_all.md`, a Markdown table (one row per item: what, value,
source `file:line`, why it matters) of every assumption, inferred and fitted value, every proposed scope decision
(dropped/alternative code, display-only inputs, unused classes or branches), the proposed **parity scope** (which
legacy outputs and intermediates are contractual, plus conservation checks and invariants the notes state) and the
proposed `datasets[].compare` settings (keys, measure, zones and zone rule, sheet/header/data/total rows). Show it in
the file pane (migrate SKILL §Showing), then ask a one-line question: "Accept the table in confirm_all.md
(Recommended)" / "Review one by one" / "Don't know". Accept → set the listed values `confirmed`, apply the scopes and
compare settings via `graph.py apply` citing this question.
Tolerance records are **not** signed here: proposals go to `tolerances.yaml` with `agreed_by: null` and to
`migration/SIGN-OFF.md` (`verify/signoff.py`); only a person signs them.

**Target** (never a question of its own): Databricks is the default. Add one line to the first question ("Target:
Databricks job files; say 'local only' or another platform to change it") or, with no question to ask, state it once
in chat. Record in `params.yaml` `target.platform` (Glue, GCP, Snowflake → `TARGET_NOT_SUPPORTED`, the ladder stops
at reference-verified).

Codex: ask the same question with `request_user_input_async` (options) when it exists, else as plain text with
numbered options, recommended first, plus "Don't know" (and "or type an answer"); then stop and wait, parse the next
message (an elapsed wait is never an answer); record `by: user (chat)`.

## 4 Fitted parameters (only when nothing states the value)

Write the `fit` block (`record.py param`, field `fit`) first: `reference` (an intermediate produced by this step), ≤12
`candidates` from evidence (other scripts, notes, tool defaults, round values in the stated unit; no optimisation),
`metric`, a `split` frozen now. `fit.py declare` → append its `fit_spec` decision → `fit.py run`. A unique winner with
a passing, sensitive hold-out may reach reference-verified labelled "fitted, held-out pass"; a global parameter
(grid offset, scale) or a fit without hold-out stays capped until a person confirms it in `confirm_all` or its own
question. Target-ready always needs every fitted value confirmed by a person.

## 5 Done when

`params_check.py migration` exits 0, every open question is answered or assumed, `decisions.md` records each, and
the graph has no open high-impact question. After migrate writes code: `params_check.py migration --fill-used-by`
then `--stage wired` must exit 0 (every answer reaches the code that reads it).
