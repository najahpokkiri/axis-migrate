# graph.py ops: the only way to change `migration/graph.json`

`graph.json` (schema `axis-migrate.graph.v1`) is written only by `skills/discover/graph.py`. Write/Edit on
`graph.json` or `graph-edits.jsonl` is denied by hook. This file is the exact contract for every op.

## 1 How to write

```
axpy graph.py apply <migration> --actor agent --base-rev <N> --ops ops.json        # or --ops - (stdin)
axpy graph.py apply <migration> --actor agent --base-rev <N> --ops ops.json --channel interview --verbatim "<user's words>"
```

- `ops.json` is a JSON **list** of op objects (a single object or `{"ops": [...]}` is also accepted). All ops in one
  call are applied atomically as **one revision**; any failure applies nothing.
- `--base-rev` is the `revision` you read (`graph.py show --json | jq .revision`). If the graph moved on, the call fails
  with exit **3** (conflict, `{"error":"conflict","revision":R,"conflicting_seqs":[…]}` with `--json`): re-read, re-think,
  retry. The agent never rebases blindly.
- Exit codes: `0` applied · `2` invalid (bad op, unknown id, graph would be invalid) · `3` conflict (stale base rev) ·
  `4` ACL (`user_owned_field`, `actor_not_allowed`, `verify_projection`, `answer_needs_interview_verbatim`, …).
- Actors on the CLI: `agent`, `verify` (only via `ingest-verify`), `derive` (only via `derive`). `--actor user` is
  **refused**: user ops come only from the graph page through `graph_server.py`.
- Every op line lands in `graph-edits.jsonl` (append-only, one line per op):
  `{"seq", "rev", "base_rev", "actor", "channel", "at", "op", "result": "applied", "effect"?}`. A batch shares one
  `rev`. `submit` does not create a revision (its line carries the current `rev`). Revision snapshots:
  `.graph/rev/<n>.json`. Node positions: `.graph/layout.json` (editor only, never sent to the agent).
- Optional on any op: `"question": "qNN"` (cites an answer; required where noted), `"comment": "…"` (free text, logged).
  On `answer_question` / `approve_plan` the op may also carry `"channel"` and `"verbatim"` (else the CLI flags apply).

### Derived on every write (never set them)
`steps[].inputs` (datasets with a non-rejected `reads`/`reads_grid` edge into the step, minus its own outputs),
`steps[].outputs` (`writes`/`reads_write` edges out of the step), `datasets[].produced_by {step, op}`,
`datasets[].consumed_by`, `datasets[].role` and the default `datasets[].new`:

- `role` = the user's override if any; else lineage's role (`role_lineage`, never changed), adjusted per write. A step
  is *in scope* when its scope is `migrate` or its kind is `bridge` (and it is not dropped):
  an `input` or `unused` dataset written by an in-scope step (an automated manual step, a ported step) becomes
  `intermediate` (has in-scope consumers) or `final`; an `unused` dataset with an existing legacy file that an in-scope step reads and no step
  produces becomes `input` (a raw file a newly ported step reads, so the runner can stage it and `protected` lists it
  under `input_exceptions`); an `intermediate`/`final` of a `keep_legacy` producer consumed in scope becomes `input`
  (bridge). A producer-less `intermediate`/`final` stays as it is (a lineage gap, never a raw input). Dropping the step
  again restores the lineage role.
- `new` (when unset or auto): `{"path": "<producer step>/<legacy basename>", "layer": <legacy layer>, "auto": true}`
  for non-input datasets produced by an in-scope step. Container items (a FileGDB member, or one of several layers of
  one legacy file) get `<step>/<container stem>__<layer>.tif` (raster, layer null) or `.gpkg` (vector/table, layer =
  legacy layer); datasets without a legacy file get `<step>/<id minus ds.>.<ext>` (`tif|gpkg|csv|txt` by kind); two
  defaults that would clash fall back to the dataset id, and an explicit `set_new` path always wins (the default steps
  aside with `__2`). `set_new` replaces it (drops `auto`). Two datasets with the same `new.path` make the graph
  invalid (exit 2).
- Lists are kept sorted by id; JSON is written with sorted keys.

### Edges and evidence
Edge kinds and direction: `reads`, `reads_grid`: dataset → step · `writes`, `reads_write` (in place): step → dataset ·
`equivalent_to`: dataset → dataset (knowledge; not data flow) · `displays`: `"project:<relpath>"` → dataset.
Default edge id: `e.<from>><to>` for reads/writes, `e.<from>><to>#<kind>` otherwise. A dataset may have only one
producing step (split versions as `ds.x@<step>`). Data-flow edges must be acyclic.
Evidence item: `{"state": <evidence state>, "ref": "path:L[-L]" | "path#cell=N" | "inventory:path" | "qgz:…" |
"answer:qNN" | "compare:runs/…", "summary": "…"}`. States: `directly_observed, structurally_inferred, ai_inferred,
documented, user_asserted, runtime_probed, verified, rejected, superseded`. Edge `state` = strongest non-rejected
evidence (`verified > runtime_probed > directly_observed > structurally_inferred > user_asserted > documented >
ai_inferred`); `conflict: true` when rejected and positive evidence coexist.

## 2 ACL (02 §3.5)

| Field / op | agent | user (page) | verify | derive |
|---|---|---|---|---|
| create step / dataset / edge, `legacy.*`, `ops`, `evidence`, `params` | yes (edges need evidence) | `add_step`, `add_dataset` (no legacy file), `add_edge` (state `user_asserted`) | – | – |
| `title` (`rename`), `notes` (`add_note`) | until the user sets it | yes, then **locked** | – | – |
| `review` | `assumed` only (`set_field`) | `confirm` / `reject_node` / `reject_edge` | – | – |
| `scope` | `set_scope` citing an answered question linked to the step | `set_scope`, `delete_step` (may need confirmation) | – | – |
| questions | create, edit while open, dismiss, `assume_question`; answer only with `channel: interview` + verbatim | answer, dismiss | – | – |
| `plan` | `set_plan`; approve only with `channel: interview` + verbatim | `approve_plan` | – | – |
| `new.*` | `set_new` | – | – | `new.code_sha256` |
| `reference` | `match_method: lineage` only | `set_reference` (human pairing) | – | – |
| `verify.*` | **no** | `waive_verify` (waiver only) | `set_verify` (all but waiver) | – |
| `ladder`, `inputs/outputs/produced_by/consumed_by` | no | no (`role` override via `set_field`) | no | `set_ladder` |

**User-owned fields.** Once the user sets `title`, `review`, `scope`, `notes`, `role` or answers a question, an agent op
touching it fails with exit 4 `user_owned_field`. To change it, the agent opens a question; after the user answers it,
the agent may cite it (`"question": "qNN"`, answered by the user and linked to that node).

## 3 Ops (exact JSON)

### Structure (agent; the editor where noted)

`add_step` (agent):
```json
{"type": "add_step", "id": "s05_new_branch", "title": "New branch", "kind": "script", "tool": "python",
 "scope": "migrate", "scope_reason": null, "legacy": {"source": "scripts/05.py", "lines": [[10, 40]], "executed": "yes"},
 "ops": [], "params": ["p.some_param"], "timeout_s": 120, "evidence": [{"state": "structurally_inferred", "ref": "scripts/05.py:10-40"}],
 "reads": ["ds.x"], "writes": ["ds.y"]}
```
Only `id` and `title` are required. `kind` ∈ `script|notebook|qgis_model|manual|bridge` (`new` = added in the editor);
step ids match `^[smb]\d{2}[a-z]?_[a-z0-9_]+$`. `reads`/`writes` create edges using the op's `evidence` (else
`ai_inferred`; prefer `add_edge` with real evidence). Editor form: `{"type": "add_step", "id", "title", "description"?,
"reads"?, "writes"?, "note"?}` → `kind: new`, `review: pending`, scope `migrate`, edges `user_asserted`.

`add_dataset` (agent):
```json
{"type": "add_dataset", "id": "ds.y", "title": "Y", "kind": "raster", "role": "intermediate", "persistence": "file",
 "legacy": {"path": "tmp/y.tif", "layer": null, "sheet": null, "exists": true, "sha256": "…", "content_sha256": null,
            "profile": {}, "sidecars": []},
 "compare": {"measure": "extensive", "count": false, "categorical": false, "key": null, "match": null, "zones": null,
             "zone_rule": null, "ignore": [], "rename": {}, "may_be_empty": false},
 "legacy_in_memory": false, "no_reference_reason": null, "evidence": []}
```
Required: `id` (`^ds\.[a-z0-9_]+(@[a-z0-9_]+)?$`), `kind` (`raster|vector|table|document`). `role` ∈
`input|intermediate|final|unused` (default intermediate). Editor form: `{"type": "add_dataset", "id", "kind",
"title"?, "role"?: "intermediate"|"final", "note"?}` (no legacy file).

`add_edge`:
```json
{"type": "add_edge", "from": "ds.x", "to": "s05_new_branch", "kind": "reads", "op": "s05.op2", "lines": [[12, 14]],
 "evidence": [{"state": "structurally_inferred", "ref": "scripts/05.py:12-14", "summary": "rasterio.open(x)"}]}
```
Agent: `evidence` required. Editor: `{"type": "add_edge", "from", "to", "kind", "note"?}` (state `user_asserted`).

`add_evidence` (agent): `{"type": "add_evidence", "id": "<step|dataset|edge>", "evidence": [{"state": "directly_observed", "ref": "inventory:tmp/y.tif", "summary": "exists"}]}`

`set_field`: `{"type": "set_field", "id": "<node id>|graph", "field": "dotted.path", "value": <json>}`.
Agent fields — step: `title, kind, tool, description, legacy(.*), ops, params, evidence, timeout_s, review (assumed
only), new.module, new.function, new.test`; dataset: `title, kind, description, persistence, legacy(.*), new,
compare(.*), legacy_in_memory, no_reference_reason, evidence, review (assumed only), reference (whole object,
match_method lineage, confirmed_by null)`; edge: `op, lines, evidence, state (not rejected), conflict, review
(assumed)`; open question: `text, header, why, options, agent_guess, impact, priority, evidence, trigger, param_ids,
kind, node, nodes, allow_other, allow_dont_know`; `graph`: `invariants, legacy_output_dirs, project.name,
project.legacy_tree_sha256`. Editor: step/dataset `title`, dataset `role` (override). Never settable: `id, inputs,
outputs, produced_by, consumed_by, provenance, *_set_by, requires_confirmation, plan, scope, scope_reason, answer,
status, notes, verify.*` (agent), `ladder` (agent).

`rename` (agent until the user renames; editor): `{"type": "rename", "id": "s01_clip", "title": "Clip to outline"}`

`add_note` (agent or editor): `{"type": "add_note", "id": "<step|dataset|edge|question>", "text": "…"}` → `notes[]`
`{text, by, rev, at, state: user_asserted|ai_inferred}`.

### Scope

`set_scope`:
```json
{"type": "set_scope", "id": "s07b_alt", "scope": "drop", "scope_reason": "alternative_impl", "question": "q02"}
```
`scope` ∈ `migrate|keep_legacy|drop`; reasons: `dead_code | alternative_impl | out_of_scope_by_owner | display_only |
no_oss_equivalent | …`. Agent: `question` required (answered or assumed, linked to the step; if the user owns the scope,
answered **by the user**). Editor: `migrate` (e.g. **automate a manual step**: `{"type": "set_scope", "id":
"m01_x", "scope": "migrate"}`) and `keep_legacy` apply at once; `drop` on a step with legacy outputs or in-scope
consumers is **not applied**: it sets `requires_confirmation` (see `delete_step`).

`delete_step` (editor only; nothing is ever removed): `{"type": "delete_step", "id": "s01_clip", "reason": "…"}`.
If the step has an output with `legacy.exists` or an in-scope consumer, the step is unchanged except
`requires_confirmation = {op, proposed: {scope: "drop", scope_reason}, by: "user", rev, at, datasets[], consumers[],
finals[]}` and the edit line carries `effect.requires_confirmation: true`. Otherwise scope becomes `drop`.

`resolve_confirmation` (agent, after asking with AskUserQuestion):
```json
{"type": "resolve_confirmation", "id": "s01_clip", "decision": "apply", "question": "q07"}
```
`decision` ∈ `apply` (scope := proposed) | `keep`; `question` answered by the user and linked to the step.

### Questions (03 §3.2 record)

`add_question` (agent):
```json
{"type": "add_question", "id": "q01", "node": "m01_zones_edit", "nodes": ["ds.zones"], "param_ids": ["p.zone_names_policy"],
 "kind": "manual_step", "header": "Zone names", "text": "…", "why": "…",
 "trigger": {"ref": "NOTES.txt:3-5", "quote": "…"}, "evidence": [{"ref": "in/zones.gpkg", "fact": "…"}],
 "agent_guess": {"option": "derive", "why": "…"},
 "options": [{"value": "derive", "label": "Derive in code (Recommended)", "description": "…", "recommended": true},
             {"value": "read_edited", "label": "Read the edited file", "description": "…"},
             {"value": "dont_know", "label": "Don't know", "description": "Use the recommended option as a visible assumption"}],
 "allow_other": true, "allow_dont_know": true,
 "impact": {"datasets": ["ds.zones"], "level": "high"}, "priority": {"impact": 3, "uncertainty": 3, "cost": 3, "score": 27}}
```
`kind` ∈ `manual_step|legacy_bug|branch|unresolved_value|fitted_confirm|confirm_all|target|lineage|scope|other`; ids
`q01…`; created `status: open`. Every question links ≥ 1 existing node (`node` + `nodes`).

`answer_question`:
```json
{"type": "answer_question", "id": "q01", "value": "derive", "text": null}
```
Agent: only with `--channel interview --verbatim "<the user's words>"` (or the same keys on the op); editor: channel
`graph`. `value` is an option value, `"other"` (needs `text`) or `"dont_know"` (→ `status: assumed`,
`answer.assumed_value` = the recommended option). Result: `answer = {value, text, by: "user", channel, verbatim,
recorded_by, rev, at}`, `status: answered|assumed`, `answered_by: "user (<channel>)"`; a previous answer moves to
`answer_history`. The agent cannot overwrite a user's answer (exit 4).

`assume_question` (agent, budget exhausted): `{"type": "assume_question", "id": "q04", "value": "derive", "reason": "budget"}`
→ `status: assumed`, `answer.by: agent`.

`dismiss_question` (agent or editor): `{"type": "dismiss_question", "id": "q03", "reason": "answered by the files"}`

### Plan (04 §3.2)

`set_plan` (agent; replaces the step's plan; approval is reset when the content changes):
```json
{"type": "set_plan", "id": "s02_mask", "plan": {
  "summary": "…",
  "legacy_ops": [{"tool": "gdal:rasterize", "ref": "scripts/02_mask.py:30-39", "settings": {"all_touched": true}}],
  "oss": [{"op": "rasterio.features.rasterize", "why": "same cell rule", "skill_refs": ["F4-2"]}],
  "params": ["p.min_area"],
  "outputs_checked": [{"dataset": "ds.b_mask", "family": "raster", "measure": "mask", "checks": ["grid", "nodata_mask", "values"], "tolerance_ids": []}],
  "risks": [{"skill_ref": "F3-1", "risk": "…", "evidence": "…"}],
  "isolation": {"possible": true, "legacy_inputs": ["ds.a_clip"], "missing": []}}}
```
Rules: `legacy_ops[].ref` is `file:line[-line]`; `params` are `p.*` ids (checked against params.yaml by `validate`);
`outputs_checked[].dataset` exists; each risk has `skill_ref` or `"risk": "none"`. `plan.approved` =
`{by: null, at: null}` until approved.

`approve_plan`:
```json
{"type": "approve_plan", "steps": ["s01_clip", "s02_mask"]}
```
`"steps": "*"` approves every `migrate` step that has a plan. Agent: only with `--channel interview --verbatim "…"`;
editor: channel `graph`. Sets `plan.approved = {by: "user", at, channel, verbatim, recorded_by, rev}`.
`graph.plan_approved(graph, step_id)` is the check the write hook uses.

### Migration and pairing

`set_new` (agent): step `{"type": "set_new", "id": "s02_mask", "new": {"module": "pipeline/s02_mask.py", "function": "run", "test": "tests/test_s02.py"}}`;
dataset `{"type": "set_new", "id": "ds.b_mask", "new": {"path": "s02_mask/b_mask.tif", "layer": null}}` (or `"new": null`).
`code_sha256` is written by `derive`.

`set_reference` (editor only; D2-5, no fallback pairing exists):
```json
{"type": "set_reference", "id": "ds.c_stats", "source": "equivalent", "via_dataset": "ds.alt_out"}
{"type": "set_reference", "id": "ds.new_out", "source": "legacy", "legacy_path": "tmp/old_mask_v2.tif", "layer": null}
{"type": "set_reference", "id": "ds.new_out", "source": "none", "reason": "no legacy counterpart"}
```
`equivalent` needs a non-rejected `equivalent_to` edge (it is marked confirmed). Result: `reference = {source,
match_method: "human", via_dataset, confirmed_by: "user"}`.

`waive_verify` (editor only): `{"type": "waive_verify", "id": "ds.b_mask", "reason": "…"}` → `verify.waiver = {reason, by: "user", rev, at}`.

### Review (editor only)

`confirm`: `{"type": "confirm", "id": "<step|dataset|edge>"}` → `review: confirmed` (does not answer questions).
`reject_node`: `{"type": "reject_node", "id": "<step|dataset>", "reason": "…"}` → `review: rejected`.
`reject_edge` (editor; agent with `reason`): `{"type": "reject_edge", "id": "e.ds.x>s01_clip", "reason": "…"}` → edge
kept, `state: rejected`, `conflict: true` when code evidence exists; the edit's `effect.code_evidence` lists the refs.

### Channel and bookkeeping

`submit` (editor only; "Send to agent"): `{"type": "submit", "message": "…"}`; no revision.
`ack` (agent): `{"type": "ack", "seq": 43}`, normally via `graph.py ack 43`; moves `acks.agent_seq` (monotonic).
`set_verify` (actor verify, via `graph.py ingest-verify`): `{"type": "set_verify", "id": "<ds|step>", "verify": {…}}`.
`set_ladder` (actor derive, via `graph.py derive`): `{"type": "set_ladder", "id": "<step>|graph", "ladder": {…}}`.

## 4 The agent's obligations per user edit (02 §4.5)

`graph_server.py wait` and `graph.py diff` print `obligations[]` = `{seq, op, target, code, obligation}`:

| User op | code | Agent must |
|---|---|---|
| `rename`, `confirm` | – | nothing (ids are stable; confirming answers no question) |
| `reject_node` | `recheck_rejected` | re-check; ask if code evidence is strong |
| `add_note` | `read_note` | read it; open a question if it contradicts evidence |
| `add_edge` | `find_edge_evidence` | find file:line evidence; else model a manual step or ask |
| `reject_edge` | `ask_rejected_edge` / `drop_edge` | with code evidence: ask "code at F:L reads X; is it not used?" |
| `add_step` | `investigate_new_step` | investigate new step (grep code, notes); classify found / manual / new behaviour; if it feeds an in-scope final, ask: flag off by default or accept a known difference |
| `add_dataset` | `investigate_new_dataset` | find producer and evidence |
| `delete_step`, `set_scope drop` with legacy outputs | `confirm_scope_change` | AskUserQuestion listing affected datasets/finals, then `resolve_confirmation` |
| `set_scope keep_legacy` | `bridge_step` | legacy outputs become hash-pinned inputs; decisions.md |
| `set_scope migrate` on a manual step | `automate_manual_step` | rule in params.yaml via an answer; plan; verify against the hand-made file. The page's Automate button also answers the step's open `manual_step` question (channel graph); if one is still open the obligation says so: confirm with one AskUserQuestion and record it, do not re-ask from scratch |
| `set_scope migrate` on another step | `migrate_step` | plan and migrate it; if it reads datasets with no legacy file and no in-scope producer (the edit's `effect.missing_inputs`), rewire them to datasets that exist (equivalent_to: `add_edge` to the equivalent + `reject_edge` on the old read) or ask. `validate` reports each as `UNPRODUCIBLE_INPUT` (and in `open_questions`) until done |
| `answer_question` | `apply_answer` | params.yaml (`source: answer:qNN`), decisions.md, evidence, re-derive |
| `set_reference` | `check_reference` | kind/CRS/grid/schema plausibility; ask if implausible |
| `waive_verify` | `record_waiver` | add to known-differences.md |
| `approve_plan` | `plan_approved` | generate step modules in topological order |

After acting: `graph.py ack <ack_seq>`. The Stop hook blocks while user edits are unacked.

## 5 Other commands

`graph.py init <migration> --from draft.json` (rev 1; refuses an existing graph) · `show [--json]` ·
`diff [--since REV | --since-seq SEQ] [--user-only] [--json]` · `ack [migration] SEQ` · `unacked [--json]` ·
`derive` (ladder projection from `report/ladder.py compute()`; left untouched when ladder.py is absent) ·
`ingest-verify --run <run_id>` · `validate [--json]` (exit 2 on errors; checks params.yaml refs, integrity, and
`UNPRODUCIBLE_INPUT`: a `migrate` step reading a dataset with `legacy.exists: false` and no in-scope producer; output
`{errors, warnings, open_questions[{step, dataset, equivalents}]}`) · `render-plan`
(writes `migration/plan.md`) · `protected` (writes `.axis/protected.json`: `{schema, graph_revision, legacy_root,
root_protected: true, paths[], input_exceptions[], legacy_output_dirs[]}`). The migration dir is optional when the cwd
is inside the project.

Library (stdlib, importable by hooks): `graph.unacked_user_edits(migration_dir) -> [edit records]`,
`graph.hook_summary(migration_dir) -> str`, `graph.plan_approved(graph, step_id) -> bool`,
`graph.apply(migration_dir, ops, actor, base_revision, *, channel=None, verbatim=None, rebase=False)`.

## 6 Projections written by verify and derive

`graph.py ingest-verify <migration> --run <run_id>` (actor `verify`) reads `runs/<id>/verify/compare/*.json`,
`verdict.json` (optional) and `visual-review.json` (optional) and sets, per compared dataset,
`verify = {run_id, verdict, reason_codes, report: "runs/<id>/verify/compare/<ds>.json", report_sha256,
plots: [paths relative to migration/], visual_flag: null | {finding_ids, categories, question_ids}, blame:
null | "origin" | "inherited", waiver (kept)}`; per step with compared datasets or a `verdict.json` entry,
`verify = {run_id, verdict, reason_codes, blame: null | own | inherited | accumulated | compensating, datasets,
mode}` (verdict.json wins; otherwise worst of its datasets, blame from topology). Dataset blame: a non-pass dataset is
`inherited` when an upstream dataset is non-pass in the same run or it carries `INHERITED_FROM:*`, else `origin`.
Plots come from `plots/<ds>/plots.json` (`{"png": [{"name", …}]}`) or `plots/<ds>/*.png`. Datasets not in this run's
compare dir are left as they were. Unknown dataset ids or a foreign `run_id` refuse the whole ingest (exit 2).

`graph.py derive <migration>` (actor `derive`) records `steps[].new.code_sha256` (sha256 of `migration/<module>`) and,
when `skills/report/ladder.py` imports, `ladder = {project, suffix, blocking, counts, run_id, status_line,
derived_rev}` and `steps[].ladder = {rung, disposition, caps, annotations, run_id}` from `ladder.compute()`. It is
idempotent and never invents a rung: without ladder.py the projection is left untouched.

## 7 graph_server.py

`axpy graph_server.py serve <migration> [--port 0]` prints `http://127.0.0.1:<port>/?t=<token>` (show it in the
harness's side pane per the /migrate command's §Showing; run it in the background). API (token in `X-Axis-Token` or `?t=`; POSTs need a same-origin `Origin` and JSON):
`GET /api/graph` → `{revision, graph, layout, params|null, edits{unacked, unsent, submitted_waiting}}` ·
`GET /api/events?after=<rev>` (long poll ≤ 25 s) → `{revision, changed}` · `POST /api/ops {base_rev, ops}` → 200
`{revision, seqs, results}` | 409 `{revision, conflicting_seqs, conflicting_ops}` (user ops are rebased automatically
when they touch other (node, field) keys than the edits since `base_rev`) | 400/403 `{error, message}` ·
`POST /api/submit {message}` → `{revision, seq}` · `POST /api/layout {positions{id:{x,y}}, reset?}` (only
`.graph/layout.json`) · `GET /files/<path>` (only under `migration/`, resolved and prefix-checked).
`axpy graph_server.py wait <migration> [--timeout 540]` blocks until a user `submit` newer than `acks.agent_seq`
(file-based; works without the server), prints `{schema, from_rev, to_rev, ops[], submitted[], obligations[],
requires_confirmation[], pending_confirmation[], ack_seq, agent_seq}` and exits 0; exit 2 on timeout with
`{timeout: true, unsent_edits}`. Use `run_in_background` in Claude Code; act on every obligation, then
`graph.py ack <ack_seq>`.

## 8 Integrity (script half of the graph.json write guard)

On every successful write (`init`, every `apply` incl. a log-only `submit`, and so `ack`, `derive`, `ingest-verify`)
graph.py records, under the lock, `.graph/head.json = {schema: axis-migrate.graph-head.v1, revision, graph_sha256
(exact graph.json bytes), edits_size, edits_sha256, edits_lines}`. `graph.py check-integrity [--json]` (shared lock;
exit 0 / 2) and `graph.py validate` report `GRAPH_EDITED_OUTSIDE_APPLY` when graph.json's bytes differ, when the edit
log is not byte-identical to the recorded one (rewritten, truncated, or a tail appended outside graph.py), or when
there is no write record. `apply` (hence the server's `POST /api/ops|submit`, HTTP 423), `show`, `diff`, `ack`,
`unacked`, `derive`, `ingest-verify`, `render-plan` and `protected` refuse with exit 2 and that code; nothing is
applied on top. Recovery: restore graph.json from `.graph/rev/<n>.json` (and the log) or ask the user. Test fixtures
must create graphs with `graph.init()` / `graph.py init`, never by writing graph.json. The hook-facing readers
`unacked_user_edits` / `hook_summary` and `graph_server.py wait` do not check (speed; they never write).

