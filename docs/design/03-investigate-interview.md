# 03 — Investigation → params.yaml → interview → decisions.md

> Reconciled in `docs/DESIGN.md` (13 Oct 2026). Where ids, vocabularies, file locations or rules here differ from DESIGN.md §3 and §11, DESIGN.md wins.

Design section 03 of 10 for `axis-migrate`. Status: draft for the orchestrator, 13 Oct 2026.
Evidence keys: `SIv2` = `~/src/axis-spatial/_archive/services/backend/app/workflow/smart_interview_v2.py`;
`SIv1` = `…/workflow/smart_interview.py`; `AIC` = `_archive/services/legacy-service-archive/agent/axis_spatial/agent/ai_client.py`;
`QG` = `_archive/apps/legacy-product-app/src/lib/question-generator.ts`; `REG` = `_archive/services/backend/app/ai/prompts/registry.py`;
`FX` = `~/axis-migrate-dev/fixtures/lux-exposure-legacy`; `PROV` = `…/lux-exposure-legacy.PROVENANCE.md`.
Entry ids (EP-, MP-, IN-, HL-, FH-, AK-) refer to `~/src/context/skill-candidates/*`; "06" = `06-old-app-flow.md`, "10 C1" etc. = section ids.

## 1 Scope and interfaces

This section covers the work between discovery and code generation:

1. **Investigate.** For every graph step, list every parameter that changes an output. Resolve each one from the files, and write it to `params.yaml` with its source and reason.
2. **Interview.** Ask the analyst only about what is still unresolved, plus the high-impact inferences that verification cannot catch. Ask one question at a time, highest priority first.
3. **Record.** Append every answer, assumption and agent decision to `decisions.md`, then update `params.yaml` and `graph.json`.

The skill is `skills/interview/SKILL.md`, plus one reference file, `reference/parameter-evidence.md` (the table in §4.1).

There are two small deterministic helpers, both stdlib or numpy:

- `params_check.py` validates the schema, coverage and wiring.
- `fit.py` runs the protocol for parameters inferred from outputs (§4.3).

No interview UI is built.

| Reads | Writes |
|---|---|
| `graph.json` (steps, datasets, lineage, `legacy_path`), `inventory.json`, legacy files (read-only), legacy intermediate headers | `params.yaml` (sole owner of values), `decisions.md` (append-only), `graph.json` → `questions[]`, node `status`, `scope`, `open_questions` |
| `compare.py` (from verify) for `fit.py` | `known-differences.md` entries of kind `legacy-fix` (co-owned with verify, §4.5) |

One owner per fact (HL-36): values in `params.yaml`, question and node state in `graph.json`, reasoning in `decisions.md`; never copied, only referenced by id.

## 2 Decisions

**D3-1 Investigate first, and never ask what the files answer.** The ledger (`params.yaml`) is complete before the first question. A question about a parameter whose class is `resolved` is a defect (test AT2). The old engine got this backwards: it asked "What should the default buffer distance be? 100 meters (Recommended)" while the distance was in the code (QG:131-145); its static trigger took the first digit anywhere in `str(operations)`, with no unit (AIC:392-419); its prompt said "Target hardcoded values" and then asked whether they should be configurable (REG:395-400, AIC:404). A value the code states is a fact, not a question.

**D3-2 Three resolution classes, each tied to the evidence that counts for it (§4.1).** `resolved`: a direct citation states the value (code, notes, project file, input-data header, legacy output metadata). `inferred`: a value with reason, confidence and method. `unresolved`: no visible input→output link, a manual step, contradictory evidence, or an intent/policy question → a question. Without a class the interview cannot tell evidence from guessing. That inability is HL-33 (absence of evidence is not evidence of absence) and EP-27 (comments make claims; re-derive them).

**D3-3 Drop the "clarity" score.** The stop rule is coverage plus a budget. `SIv2:26-28` sets `MAX=30`, `MIN=4`, `CLARITY_THRESHOLD=0.85`. The heuristic (`SIv2:631-665`) gives cloud_platform 0.25, any `input_` storage answer 0.20, any `output_` format answer 0.20, error_handling 0.15: **0.80 from four deployment answers**; +0.02 per further answer (cap 0.20) reaches 0.85 after any three more, with no correctness answer needed. The AI rubric's first criterion is "cloud platform and storage" (`SIv2:586-590`); all fallback questions are deployment (`SIv2:206-365`); the sync path sets clarity = answered/total (`SIv2:779-784`, Bug #102 fix, `test_bug_102_clarity_score_stuck.py`). New rule: stop when every red node is answered or explicitly assumed. There is no minimum number of questions; a forced minimum of 4 meant asking even when nothing was open.

**D3-4 Keep the question shape, make it traceable, and drop the content.** **Keep**: options; the recommended option, which becomes the assumption when the user does not choose (`SIv2:156-161`); an enforced "Other" (`SIv2:147-154`); `related_task_id` and `trigger_code` (`SIv2:62-63`). **Add**: `param_ids`, evidence as `file:line` + quote, the agent's guess with its reason, downstream datasets affected, an explicit "don't know". **Drop**: deployment categories (`SIv2:31-42`), the provider multi-select (`SIv2:230-260`), error handling, the "fixed or configurable" framing, the free-form LLM follow-up loop (`SIv2:438-560`).

**D3-5 Answers must change the output, and a test proves it.** In the old engine V1 `submit_answer` threw the answer away (`SIv1:143-163`), V2 only stored it (`SIv2:749-794`), generation read answers only from `interview_context.builder_context` (`_archive/services/backend/app/services/compiler/generation_input.py:54-80`) so migration answers were ignored (06 §Step 1, Bug #94, `tests/unit/test_bug_94_interview_answers_ignored.py`), and `interview_converter.py:99-120` turned answers into prose `code_implication` strings, not values. In the plugin, the pipeline reads `params.yaml` by id, and `params_check.py` fails when a parameter has no reader (`used_by`) or a reader uses a literal instead (HL-26: a default is not wiring). The takeaway from 06 becomes test AT6: same input, different answer, different output.

**D3-6 Ask about what verification cannot catch.** A wrong parameter value whose legacy reference exists fails `compare.py` on that step and is cheap to fix. The interview therefore spends its budget on what parity is blind to: policy for manual steps going forward, keep-or-fix on legacy bugs, dead or missing branches, parameters with no legacy artefact, non-unique fits, the target platform. This is the cost term of §4.4. The old triage ranked local paths "priority 10, Critical" and buffer magic numbers 7 (AIC:378, 402); EP-30 and 03 record months of reviewer findings (snap grid, buffer extent, scaling) that a short correctness interview would have surfaced.

**D3-7 Values inferred from outputs (`source: fitted`) are allowed, but capped.** Each needs a candidate set declared from evidence before fitting, isolation mode (the step runs on legacy copies of its inputs, 09 §7.4 step 5), a unique-fit margin, and a hold-out split frozen before the fit. Its step cannot reach `reference-verified` unless the hold-out passes or the user confirms the value (§4.3). Why: a pass on the data a value was fitted on is circular (HL-34); code-vs-code agreement proves nothing about legacy (FH-25); reference data must never be upstream of the computation (HL-32) — the pipeline reads only `params.yaml`, `fit.py` runs outside it; thresholds calibrated in one area must be checked on another (HL-31).

**D3-8 Values copied from legacy output metadata (`source: reference-metadata`) are allowed; the copied checks are marked true by construction.** Example: QGIS 4 writes `nodata=0` when `NODATA: None` (`FX/02_SCRIPTS/02_landuse_mask.py:70`). The code does not state the value; the header of `05_TEMP/02_res_mask.tif` does (PROV inventory row 02). Verify marks the structural check for that attribute `by_construction: true`. The value checks stay independent.

**D3-9 Legacy bugs: keep by default when the outputs are affected, ask, and record a fix as a known difference.** Pandas no-ops and positional shifts cannot be classified from code alone (IN-21; IN-07 "report, don't auto-fix"); 10 C1 defines `NEEDS_DECISION` / `LEGACY_DEFECT_SUSPECTED`; a PyQGIS degree buffer is legacy behaviour to keep or sign off (AK-38). A latent bug whose effect on the reference data is **provably zero** (§4.5) is fixed by the agent without a question, recorded as `by: agent`, and shown in the report.

**D3-10 Every branch, script and input gets a scope entry in `graph.json`.**

- The values are `migrate | dropped | out_of_scope | manual`, each with an evidence reason (MP-27 coverage matrix, EP-28 parity table).
- A branch that the files mark as dead is `dropped` without a question. It shows as a grey node that the user can switch to `migrate` in the graph editor.

**D3-11 Manual steps are always unresolved.** The files can show *what* was done. They cannot show what the pipeline should do *from now on*: automate the edit, read the edited file as an input, or bridge.

- 03 §"Messy-legacy patterns" says to treat each manual step as an explicit step with a declared artefact.
- The analyst's own note says the edit "has to be redone by hand" when the boundaries change (`FX/NOTES.txt:19`).

**D3-12 The target platform is one question, asked last.** No questions about storage, output format, error handling or providers. AK-39 records that the old prompt led with sales-style pain points that changed no code.

**D3-13 `decisions.md` is append-only.** A correction is a new entry with `supersedes:` (HL-27, 08 §9.4 "corrections append and supersede"). The verify-section hook that protects earlier reports also blocks edits above the last entry.

**D3-14 Each AskUserQuestion call carries one question.** The exceptions are the final confirmation and the platform question, which may share a call. The harness offers "Other" itself. The recommended option comes first, labelled "(Recommended)". "Don't know" is always an explicit option.

**D3-15 Budget.** The default is at most 8 questions (demo: 5); `budget` in `params.yaml` sets it. When the budget runs out, each remaining item is set to its recommended option with `status: assumed`, `reason: budget`. The report lists these items as "not asked (budget)".

- The old caps were MAX 30 / MIN 4 (`SIv2:26-27`) and max 7 (AIC:307).
- The 2026-02 design said "3 critical + 2 contextual" (`_archive/docs/features/interview-system/CURRENT-STATE.md`, "Question ordering").

**D3-16 The graph page is a second answer channel.** An answer given on a red node arrives through `graph_server.py --wait`, or through the UserPromptSubmit hook. It is written exactly like an AskUserQuestion answer, with `by: user (graph)`.

## 3 Schemas

### 3.1 `migration/params.yaml`
```yaml
version: 1
budget: {max_questions: 8, asked: 3}
params:
  - id: p.min_area_m2            # p.<slug>; stable, referenced by pipeline code
    node: s02_landuse_mask       # graph step id
    kind: threshold              # cell_size|grid|crs|nodata|resampling|threshold|distance|join_key|scale|
                                 # table_read|manual_step|branch|dtype|tool_option|target|other
    value: 2500
    unit: m2                     # required for distance/area/scale/cell_size; "px" or CRS units named
    resolution: resolved         # resolved|inferred|unresolved
    source: code                 # code|notes|project|input_data|reference_metadata|fitted|answer|assumption|agent_decision
    evidence:                    # >=1 for resolved/inferred; file:line + verbatim quote (<=120 chars)
      - {ref: "02_SCRIPTS/02_landuse_mask.py:12", quote: "MIN_AREA = 2500   # m2"}
      - {ref: "NOTES.txt:22-24", quote: "2500 was eyeballed on Clervaux/Wiltz, no science"}
    reason: "Literal in the producing script; notes agree."
    confidence: null             # inferred only: high|medium|low
    method: null                 # inferred only: tool-default|cross-script|unit-cell|fit|rule-from-inputs
    flags: [tuned_in_sample]     # tuned_in_sample|by_construction|fitted_on_reference|latent_legacy_bug|contradiction
    impact: {datasets: [ds.res_2169, ds.res_mask, ds.pop_res, ds.canton_pop, ds.exposure_tif], level: high}
    status: confirmed            # confirmed|assumed|open ; resolved => confirmed
    question: null               # q id if asked
    decisions: []                # d ids
    used_by: [pipeline/s02_landuse_mask.py]   # filled by migrate; checked by params_check.py
    fit: null                    # see 3.1b, only when source == fitted
```
3.1b `fit` block (written **before** `fit.py` runs; its hash goes into `decisions.md`):
```yaml
    fit: {reference: ds.pop_res, isolation: true, candidates: [0, 0.1, 0.25, 0.5, 0.75, 1.0],
          candidate_basis: "round values; NOTES/other scripts/tool defaults", metric: cell_mismatch_count,
          split: {kind: zones, field: canton, fit: [Clervaux, Wiltz, Vianden, Diekirch, Redange, Echternach], holdout: rest},
          result: {best: 0.5, best_score: 0, runner_up: 0.25, runner_up_score: 3276, holdout_score: 0, unique: true},
          spec_sha256: "…", result_sha256: "…"}
```

### 3.2 Question record (`graph.json` → `questions[]`)
```json
{"id": "q01", "status": "open|answered|assumed|dismissed",
 "node": "s03_zonal_population", "param_ids": ["p.canton_names_policy"],
 "kind": "manual_step|legacy_bug|branch|unresolved_value|fitted_confirm|confirm_all|target",
 "trigger": {"ref": "NOTES.txt:15-19", "quote": "manual step (NOT in the scripts) …"},
 "evidence": [{"ref": "03_INPUT/cantons_lux_edit.gpkg", "fact": "= ADM1 geometry (12/12 identical) + field canton"}],
 "header": "Canton names", "text": "…", "agent_guess": {"option": "derive", "why": "…"},
 "options": [{"value": "derive", "label": "Derive in code (Recommended)", "description": "…", "recommended": true},
             {"value": "read_edited", "label": "Read edited file", "description": "…"},
             {"value": "dont_know", "label": "Don't know", "description": "Use the recommended option as a visible assumption"}],
 "impact": {"datasets": ["ds.canton_pop", "ds.canton_factor", "ds.exposure_tif", "ds.exposure_by_canton"], "level": "high"},
 "priority": {"impact": 3, "uncertainty": 3, "cost": 3, "score": 27},
 "answer": null, "answered_by": null, "decision": null}
```
This keeps `SIv2` `SmartQuestionV2` (45-92) and `ReadinessQuestion{questionId, prompt, required, status, answer, evidenceRefs}`
(`~/src/server-copies/axis-spatial-deploy-mcp-v2/packages/runtime-core/src/migration-delivery.ts:141-149`), with `blocked` renamed `assumed`.

### 3.3 `decisions.md` entry (append-only; one heading + one fenced YAML block, parsed by `params_check.py`)
````markdown
## d004 · Canton names: derive in code · 2026-10-19T18:22:05+02:00
```yaml
id: d004
question: q01                      # null for agent decisions
kind: answer                       # answer|assumption|agent_decision|legacy_keep|legacy_fix|scope|fit_confirm
answer: derive                     # option value, or free text for "Other"
by: user (AskUserQuestion)         # user (AskUserQuestion)|user (graph)|user (chat)|agent
params: [p.canton_names_policy]
nodes: [s00_canton_names, s03_zonal_population]
changes: {p.canton_names_policy: {from: null, to: derive}}
known_difference: null             # kd id when kind == legacy_fix
supersedes: null
evidence: ["NOTES.txt:15-19", "03_INPUT/cantons_lux_edit.gpkg"]
```
Analyst chose to derive `canton` from ADM1 `shapeName` with an explicit exception table. Reproduces the hand
edit 12/12 **by construction** (the table was read off the edited file), so it is not evidence for future boundary versions.
````

## 4 Algorithms

### 4.1 Evidence that counts, by parameter type (the content of `reference/parameter-evidence.md`)
| Type | Resolved by | Inferred (state method and confidence) | Unresolved → question when |
|---|---|---|---|
| Cell size, grid, snap | Explicit in the code (`-tr`, `cellSize`, `snapRaster`) **or** the legacy output transform, which is reference_metadata (IN-02 check) | Tool semantics: QGIS clip with `KEEP_RESOLUTION=False` keeps the source grid (PROV §Grid); a fitted half-cell offset | Outputs disagree among themselves, or no output exists and the code gives no grid |
| CRS (data, area, distance) | Explicit `TARGET_CRS` / EPSG; the input header | The CRS of the operation follows the layer CRS (QGIS uses layer units: AK-38) | A distance in degrees, or Web Mercator "metres" (FH-11): a legacy_bug question |
| NoData | Explicit in the code; output header (reference_metadata, D3-8) | The tool default for that version (record the version) | The output header is missing, or the code and the header differ |
| Resampling | Explicit method | Nearest neighbour on an identical grid is an identity copy: confirm by an equality check on the intermediate | Any non-nearest method on counts (MP-05): legacy_bug |
| Thresholds | Literal plus comparison operator (`>=` vs `>`) | Fitted only (§4.3) | No literal, or GUI-only, or the fit is not unique |
| Buffer distance and units | Literal plus unit plus CRS of the operation | Unit from the layer CRS | Unit ambiguous: geodesic vs planar (EP-12, AK-01) |
| Join keys | Field named in the code; the values match on both sides | Rule derived from the inputs (by_construction) | Many-to-one or unmatched keys, or the key field was made by hand |
| Scaling | Literal plus a units cell or note (e.g. ×1000) | NUMERIC_SCALE_FACTOR diagnostic (10 C1) → confirm | The scale is only visible in outputs (MP-06, EP-01) |
| Table reads | Sheet, header row and row range in the code; cached values (AK-30) | — | Formula cells without cached values that feed the computation |
| Manual edits | The procedure, from the diff of a derived input against its source | — | **Always**, for the policy going forward (D3-11) |
| Branches, scripts | Notes or headers mark them dead; no reader in the run order | — | A live branch with no output in the reference (EP-28), or a contradiction |

Contradictions (notes vs code vs project vs outputs): for the *parity value*, the source that produced the reference wins, which is the code plus the output metadata. Each contradiction gets `flags: [contradiction]` and is asked only if the losing source states an intent (D3-6).

### 4.2 Investigation procedure (per step, in topological order)
1. **Parameters.** Enumerate every tool parameter dict, constant, `arcpy.env.*` setting, expression literal, hard-coded row or column range, field name and output option in the step's `legacy.lines`. Each one becomes a draft `p.*` with `node`.
2. **Resolve.** Resolve each draft by the table in §4.1. Cite file:line with a quote. Read intermediate headers only for structure (CRS, transform, shape, dtype, nodata, schema, counts), never values, except in `fit.py`.
3. **Inputs.** For each input dataset not written by any script, test whether it is a near-twin of another input: same geometry and an extra or changed field, or the same table with edited cells. A hit is a `manual_step` with its diff as evidence.
4. **Scope.** Give every script, branch and input a `scope` (D3-10). Inputs that no script reads, and scripts outside the run order, are `dropped` or `out_of_scope`, citing the evidence.
5. **Latent bugs.** Run the lint list (IN-21 patterns, silent `continue` / `print` on a missing key, hard-coded ranges) and classify each finding by §4.5.
6. **Downstream closure.** Compute each parameter's downstream closure from the graph and store it in `impact.datasets`.
7. **Graph.** Mark red the nodes that hold `unresolved` items or high-impact `inferred` items (confidence below high, or fitted with no hold-out).

### 4.3 Parameters inferred from outputs (`fit.py`), and how they move up the status ladder
1. **Preconditions.** No `resolved` evidence exists. The reference is an intermediate produced by this step. The step can run in isolation mode on the legacy copies of its inputs.
2. **Declare.** Write the `fit` block (3.1b) before running.
   - Candidates come from evidence: values in other scripts, notes, the project file or tool defaults, round values in the stated unit, and grid offsets of k/2 cells. There are at most 12 candidates.
   - No continuous optimisation is allowed.
   - The split is chosen deterministically from the zones (alternating by sorted id, or north/south of the median centroid) or by key hash for tables.
   - The block is hashed into a decision entry with `kind: fit_spec`.
3. **Run.** `fit.py` runs each candidate on the fit split through `compare.py`. The candidate is accepted only if it is the single pass and the runner-up fails by at least the nearest-wrong margin of the tolerance record (10 C2 two-number rule). Two or more passes means `unresolved`, and the question shows the passing candidates.
4. **Hold-out.** Run the chosen value on the hold-out split. Write `source: fitted`, `resolution: inferred` and `flags: [fitted_on_reference]` to the parameter.
5. **Status.**

   | Fit evidence | Step status | Report wording |
   |---|---|---|
   | Fit passes, but no hold-out and no confirmation | stays `sandbox-validated` (flag `fitted`) | "matches the reference because it was fitted to it" |
   | Hold-out passes, or the user confirmed the value | may reach `reference-verified` | "fitted, held-out pass" or "fitted, confirmed by user" |
   | Hold-out fails | `FAIL`; the parameter becomes `unresolved` | asked as a `fitted_confirm` question |

   `target-ready` needs every fitted parameter either held-out or confirmed.
6. **Limits** (printed in the report next to every fitted parameter):
   - The hold-out uses the same legacy run, so a shared upstream defect is not detected.
   - Spatial autocorrelation makes the two halves dependent.
   - A parameter that acts globally (a grid offset) makes both halves agree trivially; its evidence is the uniqueness margin, not the split.
   - A threshold that only bites in one half proves nothing in the other half. `fit.py` records each half's sensitivity (how many cells change across the candidates); if the hold-out half's sensitivity is 0 → `inconclusive`.
   - A fit shows the value is *consistent* with the legacy, not that it was *intended* (HL-30).

### 4.4 Priority and question generation
`score = impact × uncertainty × cost`, each term from 1 to 3.

| Term | 3 | 2 | 1 |
|---|---|---|---|
| impact | changes a final output's values or structure (from `impact.datasets` ∩ final) | intermediate, schema or format only | no output effect (scope, operations) |
| uncertainty | unresolved | inferred, low confidence, or fitted not unique | inferred, medium confidence |
| cost | parity cannot detect it (intent, policy, no reference, both options match today) | verify detects it, but rework is far upstream | verify detects it at that step |

Rules:

- Ask when the item is unresolved, or when score ≥ 12.
- Never ask about inferred items with high confidence. They are listed in the final confirmation.
- Tie-break: upstream first.
- The `target` question is always last.
- A `confirm_all` question comes just before `target`. It lists every assumption, inferred and fitted value, plus the proposed parity scope (which outputs are contractual; tolerances proposed by verify under 10 C2). Options: "Accept all (Recommended)", "Review one by one", "Don't know". Accepting turns `assumed` into `confirmed` (EP-30, AK-39 parity contract).

Question text template, with ≤12 characters for `header`:

> "[node · file:line] what the files show → what they cannot tell → my guess and why → what changes downstream"

A free-text "Other" answer is restated by the agent as a concrete `params` change, and the agent asks once: "Recorded as X = …, correct?". If the answer is still ambiguous, the item stays `open` and counts against the budget.

### 4.5 Legacy bugs: keep or fix
1. **Detect.** The sources are lint findings, a branch that does not conserve mass (IN-07 test), silent no-ops (IN-21), and dropped rows or keys.
2. **Measure.** Measure the effect on the *reference data*: run the step in isolation with the "intent" variant and compare with the legacy intermediate.
3. **Zero effect** (bit-equal or exact, on all reference artefacts). The agent fixes it without a question. It writes `kind: agent_decision`, `flags: [latent_legacy_bug]`, and a `known-differences.md` entry `{expected: none on reference, bound: 0, applies_when: <condition>}`.
4. **Non-zero effect.** Ask a `legacy_bug` question. The recommended option is **keep (parity)**, except where a reference entry marks the behaviour as a known trap that the owner already rejected.
   - **Fix:** `kind: legacy_fix`, plus a `known-differences.md` entry `kd-NN {param, nodes, expected_difference, magnitude_bound (measured in step 2, with units), check_id, decision: dNNN}`. Verify then compares against the legacy and expects that bounded difference: a difference outside the bound fails, and no difference at all fails too.
   - **Keep:** the bug is reproduced, and the report lists it under "legacy behaviour kept".

### 4.6 Codex and other harnesses without AskUserQuestion
I could not check whether Codex has a structured question tool. Fallback:

1. The agent prints exactly one question block (header, evidence, guess, options numbered 1..n with "(Recommended)", `0 = don't know`, "or type an answer") and ends its turn.
2. The next user message is parsed as a number, a label or free text.
3. The record is identical, with `by: user (chat)`.

The graph page (D3-16) works the same everywhere. `questions[]` in `graph.json` makes the interview resumable after a session restart.

## 5 Reuse

| What | Path:lines | Use | Needed fixes | Tests |
|---|---|---|---|---|
| Question/option dataclasses, "Other" enforcement, recommended → assumption | `SIv2:45-92, 147-161` | Shape only, ported into the 3.2 JSON | Add `param_ids`, evidence, guess, impact, "don't know"; drop `category` enum, `is_dynamic`, `clarity_score` | `params_check.py` schema tests |
| `ReadinessQuestion` status and `ReadinessDecision{questionRefs, decidedBy, createdAt}` | `…/runtime-core/src/migration-delivery.ts:141-160` | Status vocabulary and decision fields | `blocked` becomes `assumed`; add `supersedes` | — |
| Bug #94 regression idea | `_archive/services/backend/tests/unit/test_bug_94_interview_answers_ignored.py` | Becomes AT6 (answer changes output) | Run against real pipeline output, not mocks (FH-26) | AT6 |
| Bug #96 (malformed LLM JSON gave 0 questions) | `…/tests/unit/test_bug_96_v3_zero_questions.py` | Lesson only. Zero questions is valid only if the ledger has no red node; otherwise fail closed | — | AT3 |
| Static triggers, clarity, fallback questions, `question-generator.ts`, `interview_converter.py` | `AIC:354-419`; `SIv2:202-367, 562-665`; `QG:25-165`; `interview_converter.py:99-120` | **Drop** | — | AT2 guards against regression |
| Isolation mode and first-failing-dataset | 09 §7.4 steps 5-6 | `fit.py` and legacy-bug measurement | — | AT5 |
| Tolerance record two-number rule; `NEEDS_DECISION`, `REFERENCE_NOT_INDEPENDENT` | 10 C1, C2 | Fit uniqueness margin; legacy-bug state | — | AT5, AT8 |

## 6 Acceptance tests (lux fixture unless stated)
I checked the facts below on the fixture on 13 Oct with geopandas and rasterio in the `qgis` env.

- **AT1 Ledger coverage.** `params.yaml` contains these entries, each `resolved` with a citation:

  | Parameter | Value | Citation |
  |---|---|---|
  | `p.grid` | WorldPop grid, 940×880, EPSG:4326 | `01_prepare_population.py:37-48`, input header; corroborated by `03b_zonal_arcpy_OLD.py:23-26` |
  | `p.clip_crop_to_cutline` | false | `01:46` |
  | `p.clip_keep_resolution` | false | `01:48` |
  | `p.pop_nodata` | −99999 | `01:45` |
  | `p.landuse_class` | `residential` | `02:47`, `NOTES:29` |
  | `p.area_crs` | EPSG:2169 | `02:27-30` |
  | `p.min_area_m2` | 2500, flag `tuned_in_sample` | `02:12`, `NOTES:22-24` (HL-31) |
  | `p.min_area_op` | `>=` | `02:47` |
  | `p.rasterize_all_touched` | true | `02:75`, `NOTES:26` |
  | `p.mask_nodata` | 0, `reference_metadata`, `by_construction` | — |
  | `p.pop_min` | 0.5, operator `>=` | `03:13,33`, `NOTES:25` |
  | `p.join_key` | `canton` | `04:60` |
  | `p.vpc_table` | sheet `vpc`, rows 5-16, `strip()` | `04:37-42`; header on row 4 |
  | `p.vpc_scale` | 1000 | `04:42`, `NOTES:28`, xlsx cell B2 "kEUR / inh." |
  | `p.factor_dtype` | Float64 | `04:90` |
  | `p.exposure_dtype` | Float32 | `04:103` |
  | `p.exposure_nodata` | −9999 | `04:101` |
  | `p.zone_rule` | cell centre | inferred, high: tool semantics, AK-21 |

  `params_check.py` exits 0.
- **AT2 Never ask what the files answer.** No question's `param_ids` intersects the AT1 set. In particular, these must not be asked: MIN_AREA, POP_MIN, all-touched, ×1000 or units, *which* names were edited, the grid or snap, the CRS for area, the join key, the xlsx rows, the run order, whether 03b is live, and the cloud storage paths for `BASE` (`01:8`).
- **AT3 Expected questions, in order** (3 questions, at most 5 when the user picks "Review one by one"):
  1. **q01 `manual_step` (score 27).**
     - The files show the procedure: ADM1 → edited gpkg has identical geometry (12/12). The only changes are the dropped "Canton " prefix and one exception, Esch-sur-Alzette → Esch/Alzette, which matches the xlsx names after `strip()`.
     - The question asks only for the policy: derive in code with an exception table (Recommended, because `NOTES:19` says the edit must otherwise be redone by hand) / read the edited file as an input / don't know.
  2. **q02 `confirm_all`.** The list covers:
     - `p.mask_nodata`: reference_metadata;
     - `p.zone_rule`: inferred;
     - `p.min_area_m2`: tuned in-sample;
     - "100 m" in `NOTES:4` taken as WorldPop's nominal 3″ grid (≈60×93 m);
     - the agent decisions from AT8;
     - parity scope: all of `04_FINAL` plus every `05_TEMP` intermediate, plus per-canton conservation from `NOTES:5`.
  3. **q03 `target`.** Options: Databricks job files (Recommended) / local only / other.
- **AT4 Canton derivation.** The derived `canton` equals the edited field 12/12. The decision text says "by construction". Verify tags the check `by_construction` and does not count it as independent evidence.
- **AT5 Fit protocol (variant fixture `lux-fit`).** Remove `POP_MIN` from `03` and replace it with a note "threshold set in GUI".
  - Expected: `fit.py` with candidates {0, 0.1, 0.25, 0.5, 0.75, 1.0} against `05_TEMP/03_pop_res.tif` in isolation (legacy `01_pop_lux` and `02_res_mask` as inputs).
  - Measured mismatching cells: 4042 / 4029 / 3276 / **0** / 3873 / 7182.
  - Unique best: 0.5, runner-up 0.25 at 3276.
  - Split: north (Clervaux, Wiltz, Vianden, Diekirch, Redange, Echternach) for the fit, south for the hold-out. Hold-out result: 0 at 0.5, and 0.5 is the only candidate at 0 in the south half.
  - s03 may then reach `reference-verified`, labelled "fitted, held-out pass". The variant without the split must stay `sandbox-validated`.
- **AT6 Answers change outputs.** Rerun with `p.min_area_m2: 0` and assert that `ds.res_mask` differs (876 of 2388 residential polygons are under 2500 m²). For q01, run both answers and assert that the generated s00 code differs while the outputs stay equal (proof that "both options match today" was correct).
- **AT7 Append-only.** Editing an earlier `decisions.md` block is blocked by the hook. A `supersedes:` entry is accepted.
- **AT8 Scope and latent bugs.** Expected records:
  - `s03b` → `scope: dropped`, reason `NOTES:13` and `03b:2-3`, with no question. The graph node is grey.
  - The OSM industrial, commercial and retail classes → `out_of_scope` (`NOTES:29`).
  - ADM2 communes, present only in the `.qgz` → `out_of_scope` (display only; no script reads it).
  - Agent decisions with zero effect on the reference data:
    - read the xlsx by header plus an assertion of 12 names, instead of rows 5-16 (`04:39`);
    - fail closed on a canton missing from the xlsx (`04:61-63`), where the legacy prints and continues.
  - Each one has a known-differences entry with bound 0.
- **AT9 "Don't know."** Answering q01 with "Don't know" gives `status: assumed`. The report's Assumptions table shows it, and `target-ready` is blocked until q02 accepts it.
- **AT10 Negative control.** Remove `NOTES:15-19`. Step 4.2 (3) must still flag `cantons_lux_edit.gpkg` as a near-twin of ADM1, and the agent must ask, not guess.
- **AT11 Codex fallback.** Run the same answers through the chat fallback. `decisions.md` must be identical except for the `by:` field.

## 7 Contradictions resolved
- **PLAN's example question.** PLAN asks "Which names, and should the pipeline do it?". The files answer *which names* (AT3), so only the policy is asked (D3-1, D3-11).
- **NOTES "100m cell" (`NOTES:4`) vs the 3″ grid.** For parity, the code and the output metadata win. This is recorded as an inference with `contradiction` and listed in `confirm_all`, not asked (D3-6: both sides agree on what the legacy produced).
- **"Identify a missing threshold by testing candidates" (PLAN) vs circular evidence (HL-34, FH-25).** Allowed only under §4.3, with the cap on the status ladder.
- **"Agree the tolerance in the interview" (EP-30, AK-39) vs measured tolerances (10 C2).** The interview fixes the scope (which outputs and checks are contractual). Verify measures the numbers. The owner accepts both in `confirm_all`.
- **03 "Any failure is a question, not a silent fix" vs agent fixes.** Only fixes with zero effect on the reference data are made without a question, and each is recorded and reported (§4.5 step 3).
- **graph.json "single source of truth" vs answers going to `decisions.md`.** Values live in `params.yaml`, question and node state in `graph.json`, reasoning in `decisions.md`. Ids link them (HL-36).
- **08 §9.6 ladder up to `customer-accepted` vs the demo.** The ladder stops at `target-ready`. No state above it is written.

## 8 Open questions for the founder
1. **03b in the demo.** The PLAN says the ArcPy script is "kept only for translation" and verified against the QGIS step. NOTES says "don't run". The agent will mark it `dropped` without a question. Should the demo presenter switch it to `migrate` in the graph? If so, verify must expect:
   - a known nodata-vs-0 difference outside residential cells (`03b:31-33` writes 0; QGIS writes NoData);
   - a reference labelled "QGIS-equivalent step".
2. **Budget.** Are the defaults right: max 8 questions, demo 5, and the ask threshold of score ≥ 12?
3. **Fitted parameters.** Should a hold-out pass alone be enough for `reference-verified`, or should user confirmation always be required?
4. **`confirm_all`.** It is a single "Accept all". Is one click enough sign-off for `target-ready`, or should high-impact assumptions be confirmed individually?
5. **Interview language.** Should it be German when the notes are German? Not designed.

Not checked:

- GBrain, which needs auth. I relied on 07 and 08.
- Whether Codex has a structured question tool.
- The exact AskUserQuestion limits. I worked from the known shape: 1-4 questions per call, 2-4 options, `header` ≤12 characters, automatic "Other". This needs checking on Wednesday.
- The server branches. I did not `git grep` all 29 for a newer interview; `smart_interview_v2.py` is the latest copy in `_archive`.

## 9 Cross-section contract
**What I need from discover:**

- `graph.json.steps[]`: `id`, `legacy{script, lines, tool}`, `inputs[]` and `outputs[]` as dataset ids, `scope`, `status`.
- `graph.json.datasets[]`: `id`, `role` (raw_input | derived_input | intermediate | final), `legacy_path`, `produced_by`, `consumed_by`, `header{crs, transform, shape, dtype, nodata, schema, count}` (structure only).
- `inventory.json`: `notes_files[]` with their text, `project_files[]` with layer datasources and subset filters, `unreferenced_inputs[]`, and `run_order`, with evidence for each.

**What I add to `graph.json`:** `questions[]`; per node `open_questions[]` and `red: bool`; per step `scope` and `scope_reason`.

**migrate reads:**

- `params.yaml` values by id, through one loader. It must fill `used_by`.
- Literals in `pipeline/` that duplicate a `p.*` value fail the scanner.
- The `scope` of each node, and the decisions of kind `legacy_keep` / `legacy_fix`, which decide which variant to code.

**verify reads:**

- `params[].source` ∈ {reference_metadata, fitted} → `by_construction` / `fitted_on_reference` tags on checks.
- `fit.split` → the hold-out run.
- `known-differences.md` `kd-*` entries from §4.5 → expected bounded differences.

**report reads:**

- The Assumptions table (`status: assumed`, including `reason: budget`).
- Fitted parameters, with the limits from §4.3 step 6.
- Agent decisions and the legacy behaviour kept.
- The question log (`questions[]` with answers and who gave them).
- The scope matrix.
- A link to each `decisions.md` entry.

**hooks:**

- Append-only guard on `decisions.md`.
- The Stop hook refuses "done" while any red node is `open`.
