# Demo workspace: lux-exposure (local only; run evidence is not committed)

`demo/lux-exposure/` = the project of the fresh headless e2e trial F-L (batch 4, t1, plugin b044565, Opus 5.5, fenced as
`axis-eval`, oracle answers = recommended options; 18 min, 4 questions): legacy folder (read-only copy) + `migration/` with graph, params,
decisions, the pipeline, every sealed run and the report (`migration/report/index.html`).

**Computed status:** `axis-migrate status: sandbox-validated | run r20261014T091824Z-96d0baee | 11 pass, 0 FAIL,
0 NOT_COMPARED, 0 needs review`. 11 datasets PASS_EXACT; 3 NEEDS_DECISION / TOLERANCE_UNSIGNED (no FAIL):
`ds.02_res_2169`, `ds.02_res_4326` (`area_m2` float noise ≤ 8.6e-13 vs nearest wrong 3.8e-5) and
`ds.lux_res_exposure_2020` (per-canton conservation invariant: the legacy itself only within 1.57e-9 vs nearest wrong
4.1e-4 = one populated cell lost). The Databricks bundle was correctly not generated (needs reference-verified).

**For Najah — sign-off:** everything waiting for a person is in ONE file, `demo/lux-exposure/migration/SIGN-OFF.md`
(committed). It lists the three tolerance proposals with both measured numbers (no known differences were needed).
Nothing is signed. To sign: follow the per-record instructions in SIGN-OFF.md
(copy each record into `migration/tolerances.yaml`, set `agreed_by: human:najah`, `agreed_at`, `decision_ref`), then run
`/axis-migrate:migrate ./lux-exposure-legacy` from `demo/lux-exposure/` so the agent makes a new chained run, verifies
it, seals it and (target = Databricks) generates and dry-runs the bundle. Expected then: reference-verified →
target-ready (Databricks), not deployed — computed by ladder.py, not promised here.
