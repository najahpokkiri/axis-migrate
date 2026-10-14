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
