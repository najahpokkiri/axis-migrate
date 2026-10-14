---
name: interview
description: Investigate every output-changing parameter of a legacy GIS workflow from its files (params.yaml with file:line citations), then ask the analyst only what the files cannot answer and parity cannot catch, one question at a time, and record answers in decisions.md. Use after discover has written graph.json and before planning code.
---

# Investigate, then interview

`$P` = plugin root. Scripts: `"$P/bin/axpy" "$P/skills/interview/params_check.py" migration [--stage investigate|wired]`,
`"$P/bin/axpy" "$P/skills/interview/fit.py" declare|run migration --param p.x`. Graph writes only through
`"$P/bin/axpy" "$P/skills/discover/graph.py" apply migration --actor agent --base-rev N --ops ops.json` (op shapes:
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
8. Run `params_check.py migration` until it exits 0.

Schema (`params.yaml`): `version, budget{max_questions: 8 (demo 5), asked}, params[]{id: p.<slug>, node, kind, value,
unit, resolution, source, evidence[{ref, quote}], reason, confidence, method, flags, impact{datasets, level},
status, question, decisions, used_by, fit}` and a `target:` block (09 §3.2). Never put a value in code.

## 2 Decide what to ask

Ask an item only if it is `unresolved`, or `inferred` with `score = impact × uncertainty × cost ≥ 12` (each 1–3;
cost is highest where parity cannot detect it: manual-step policy, keep-or-fix of legacy bugs, branches without a
reference, non-unique fits, target platform). Never ask what the files answer (a question whose `param_ids` include
a resolved parameter is blocked by `params_check.py`). Upstream first; budget 8 (demo 5).

## 3 Ask (one question per call)

Create the question in the graph first (`add_question`, shape in GRAPH_OPS.md, id `qNN`, linked `nodes`,
`param_ids`, `evidence`, `agent_guess` with reason, `impact`). Then AskUserQuestion with exactly one question:
header ≤12 chars; text `"[node · file:line] what the files show → what they cannot tell → my guess and why →
what changes downstream"`; options: recommended first labelled "(Recommended)", the alternatives, and
"Don't know" (→ the recommended option becomes a visible `assumed` value). Then:

- `answer_question` via `graph.py apply … --channel interview --verbatim "<the user's exact words>"`;
- update `params.yaml` (`source: answer`, `question: qNN`, `status: confirmed`; "Don't know" → `source: assumption`,
  `status: assumed`), increment `budget.asked`;
- record the decision with `"$P/bin/axpy" "$P/skills/interview/record.py" decision migration --json entry.json`
  (fields `title, question, kind, answer, by, params, nodes, changes, supersedes, evidence`; it assigns `D-nnn`, writes
  the heading and a serialiser-made yaml block, so quoting cannot break). Never hand-write or edit an entry; correct
  one, even a malformed one, by recording a new entry with `supersedes: D-nnn`.

A free-text "Other" answer is restated as a concrete params change and confirmed once ("Recorded as X = …, correct?").

**`confirm_all`** (just before the target question): one question listing every assumption, inferred and fitted
value, every proposed scope decision (dropped/alternative code, display-only inputs, unused classes or branches),
the proposed **parity scope** (which legacy outputs and intermediates are contractual, plus conservation checks and
invariants the notes state) and the proposed `datasets[].compare` settings (keys, measure, zones and zone rule,
sheet/header/data/total rows). Options: "Accept all (Recommended)", "Review one by one", "Don't know". Accept → set
the listed values `confirmed`, apply the scopes and compare settings via `graph.py apply` citing this question.
Tolerance records are **not** signed here: proposals go to `tolerances.yaml` with `agreed_by: null` and to
`migration/SIGN-OFF.md` (`verify/signoff.py`); only a person signs them.

**Target** (always last): "Databricks job files (Recommended) / local only / other". Record in `params.yaml`
`target.platform` (Glue, GCP, Snowflake → `TARGET_NOT_SUPPORTED`, the ladder stops at reference-verified).

Codex (no AskUserQuestion): ask the same question as plain text with numbered options, recommended first, plus
"Don't know" (and "or type an answer"); then stop and wait, parse the next message; record `by: user (chat)`.
Answers may also arrive from the graph page (`graph_server.py wait`): record them the same way with `by: user (graph)`.

## 4 Fitted parameters (only when nothing states the value)

Write the `fit` block in `params.yaml` first: `reference` (an intermediate produced by this step), ≤12
`candidates` from evidence (other scripts, notes, tool defaults, round values in the stated unit; no optimisation),
`metric`, a `split` frozen now. `fit.py declare` → append its `fit_spec` decision → `fit.py run`. A unique winner with
a passing, sensitive hold-out may reach reference-verified labelled "fitted, held-out pass"; a global parameter
(grid offset, scale) or a fit without hold-out stays capped until a person confirms it in `confirm_all` or its own
question. Target-ready always needs every fitted value confirmed by a person.

## 5 Done when

`params_check.py migration` exits 0, every open question is answered or assumed, `decisions.md` records each, and
the graph has no open high-impact question. After migrate writes code: `params_check.py migration --fill-used-by`
then `--stage wired` must exit 0 (every answer reaches the code that reads it).
