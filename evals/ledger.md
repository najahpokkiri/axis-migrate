# Improvement ledger (10 §6)

Format: date | case | trial | class | route | commit | finding → fix.
Classes: H harness · C1 checker miss · C2 checker false alarm · P plot blind · K knowledge gap · A knew, didn't act ·
W workflow misread · V looked wrong · G guard false positive (hook/script blocked a legitimate action).

| Date | Case | Trial | Class | Route | Commit | Finding → fix |
|---|---|---|---|---|---|---|
| 13 Oct | F-L | t1 (first) | H | evals/run.py | 19e1… | `< /dev/stdin` refused under sudo's pty → job JSON piped directly |
| 13 Oct | F-L | t1 | G | hooks/guard_bash.py | (this commit) | `> migration/.graph_server.log` denied as "graph.json"; `.graph` prefix match → exact `.graph/` dir match + tests |
