# Demo workspace: lux-exposure (local only; run evidence is not committed)

`demo/lux-exposure/` = the project of the fresh headless e2e trial F-L (batch 2, t2, plugin 0afd2f3, Opus 5.5, fenced as
`axis-eval`, oracle answers = recommended options): legacy folder (read-only copy) + `migration/` with graph, params,
decisions, the pipeline, every sealed run and the report (`migration/report/index.html`).

**Computed status:** `axis-migrate status: sandbox-validated | run r20261014T041423Z-a1b21207 | 11 pass, 0 FAIL,
0 NOT_COMPARED, 0 needs review`. 11 datasets PASS_EXACT; 3 NEEDS_DECISION / TOLERANCE_UNSIGNED (no FAIL):
`ds.02_res_2169`, `ds.02_res_4326` (`area_m2` float noise ≤ 8.64e-13 vs nearest wrong 1.4e-5) and
`ds.lux_res_exposure_2020` (per-canton conservation invariant: the legacy itself only within 1.57e-9 vs nearest wrong
1.79e-3). The Databricks bundle was correctly not generated (needs reference-verified).

**For Najah — sign-off:** everything waiting for a person is in ONE file, `demo/lux-exposure/migration/SIGN-OFF.md`
(committed). It lists the three tolerance proposals with both measured numbers and the two proposed known differences
(KD-001 is malformed and superseded by KD-002 in its text — it was written before `record.py`/`supersedes` existed;
accept KD-002 only, or neither). Nothing is signed. To sign: follow the per-record instructions in SIGN-OFF.md
(copy each record into `migration/tolerances.yaml`, set `agreed_by: human:najah`, `agreed_at`, `decision_ref`), then run
`/axis-migrate:migrate ./lux-exposure-legacy` from `demo/lux-exposure/` so the agent makes a new chained run, verifies
it, seals it and (target = Databricks) generates and dry-runs the bundle. Expected then: reference-verified →
target-ready (Databricks), not deployed — computed by ladder.py, not promised here.
