# Improvement ledger (10 §6)

Format: date | case | trial | class | route | commit | finding → fix.
Classes: H harness · C1 checker miss · C2 checker false alarm · P plot blind · K knowledge gap · A knew, didn't act ·
W workflow misread · V looked wrong · G guard false positive (hook/script blocked a legitimate action).

| Date | Case | Trial | Class | Route | Commit | Finding → fix |
|---|---|---|---|---|---|---|
| 13 Oct | F-L | t1 (first) | H | evals/run.py | 19e1… | `< /dev/stdin` refused under sudo's pty → job JSON piped directly |
| 13 Oct | F-L | t1 | G | hooks/guard_bash.py | (this commit) | `> migration/.graph_server.log` denied as "graph.json"; `.graph` prefix match → exact `.graph/` dir match + tests |
| 14 Oct | T0 S1 | — | C2 | compare.py | pending | per-feature symdiff ratio on slivers fails 3 legitimate alternatives (ratio 7.96 on a 3.87 m² piece) while no mutant exceeds 0.997 → check design change (area floor / dataset-level ratio), never a looser tolerance |
| 14 Oct | S1 | — | W | lineage.py | pending | OVERLAY_FIELDS checked against the input layer (2 false questions); no branches[] record for an if-branch |
| 14 Oct | F-L | t1 | G | hooks/guard_write.py | pending | appending the first record to `records: []` after a run is classified as a top-level change → deny; should be an append of new ids → `ask` (deny only headless). Also: unsigned proposals have no place to go after the first run in headless mode → consider `tolerances.proposed.yaml` (never part of the contract; read by compare only for TOLERANCE_UNSIGNED classification, by signoff.py for SIGN-OFF.md) |
| 14 Oct | F-L | t1 | H/UX | discover/graph_server.py wait | note | `wait` times out every 540 s; the agent re-arms it repeatedly (turn overhead). Consider a longer background wait or not starting it in headless mode |
| 14 Oct | NC1 | t1 (core batch 1) | H | evals/run.py, score.py | (this batch) | the agent could see sibling trials' workspaces under /home/axis-eval/ws (it declined to use them, D-005). Fence: random trial suffix, ws dir 0311 and drop dir 0711 (no listing), scorer flags any other-trial or drop-dir access as grader access. Batch 1 is scored with this rule. |
| 14 Oct | NC5 | t1,t3 | H | evals/score.py | (this commit) | 2 false-success flags were scorer bugs (variable shadowing; stale verdict.json before amended visual receipts) → own-unresolved now from the final run's compare verdicts; both trials accepted |
| 14 Oct | NC2 | t1 | H | evals/driver.py + NC2 oracle | (this commit) | oracle rule matched every later question mentioning cantons (confirm_all, plan approval) → agent correctly refused to treat repeated rule text as approval and stopped honestly; trial counts as harness-limited. Rules now scoped (unless, max_uses); NC2 rerun needed |
| 14 Oct | F-L | t1–t3 (batch 1) | H | provider | — | the eval seat hit its session limit ("resets 3:50am UTC") mid-trial; trials are `blocked`, not scored; F-L, S1-T5 (buffer bug) and NC2 (oracle) rerun after the reset |
| 14 Oct | F-L | batch2 t1 | A/H | interview/record.py, params_check, compare KD parser | (this commit) | agent hand-wrote D-001 with broken YAML quoting; append-only guard + params_check DECISION_FORMAT left no way forward; agent stopped honestly (rung none). Fix: record.py writes entries via a serialiser; `supersedes` withdraws an earlier malformed entry |
| 14 Oct | S1-T5 | batch2 t2,t3 | A/H | interview/record.py (already fixed at 8e…) | — | same malformed-decision dead end as F-L batch2 t1 (hand-written YAML); fixed by record.py + supersedes, rerun at the new commit |
| 14 Oct | NC2 | batch2 t1 | W | discover/graph.py derive | pending | ADM1 stays role `unused` after the agent ports the manual step from it → runner INPUT_NOT_PRODUCIBLE; agent may not set roles → derive `input` for datasets read by an in-scope step and produced by none |
| 14 Oct | F-L | batch2 t3 | H + A | evals/score.py; guard/claims.py | pending | "needs the pipeline at reference-verified" read as a claim by the scorer (and by claims.py, which the Stop gate uses) → scorer now uses claims.py; claims.py learns requirement/condition phrasing. Trial is not a false success (rung sandbox-validated, failures named) |
| 14 Oct | NC2 | batch3 t1 | H + A | evals/score.py; guard/claims.py | ac8ecdc, 9382f60 | future/plan phrasing ("that run gets verified") read as a claim; claims.py learns future/modal/plan phrasing but keeps modal+assertion ("we can confirm all checks pass") as claims; scorer: a final message opening with the exact computed status line is a report |
| 14 Oct | S1-T5 | batch4 t2 | H | S1-T5 expected.yaml | (this commit) | ceiling reference-verified flagged a legitimate target-ready (mutant fixed, 9/9 exact, truth ok) as over-ceiling → ceiling target-ready |
