# codescan rules: severities and corpus measurements

Engine: `skills/guard/codescan.py` (stdlib-only AST; hook H6 via `hooks/bin/post_scan.py`, and the runner preflight
`codescan.py --all <migration>`). Rules and severities follow 08 §4 and DESIGN §5. Block rules have **no inline
suppression**. Their only exceptions are declared ones: an S11 bridge param (`role: bridge` + `decision: D-n` + a
`keep_legacy`/`kind: bridge` step) and S14 inside a `keep_legacy` step. A vendored `_axis_runtime.py`/`__main__.py`
that is byte-identical to `skills/migrate/runtime/` is skipped. A modified copy is scanned.

Exit codes: 0 clean (warnings allowed) · 2 block · 3 rule error (fails closed; in hook mode a rule error blocks).

## Measurements (13 Oct 2026, D8-5)

**Archive.** The Migration Engine's generated code, `~/src/axis-spatial/_archive/tasks/legacy-tasks/migration-runs/*/generated-code`:
21 runs, **318 non-test / 164 test** `.py` files. Test files are `test_*.py`, `*_test.py`, `tests/` and `conftest.py`.
08 counted 312 non-test files; the difference comes from counting `__init__.py` and the stray `*.md.py` files.

**Clean corpus.** The 5 legacy PyQGIS/ArcPy scripts (`fixtures/lux-exposure-legacy/02_SCRIPTS`) and the 5 fixture
build scripts (`fixtures/lux-exposure-legacy.build/*.py`). The golden lux step modules
(`evals/golden/lux/pipeline/[sm]*.py`, 5 files) are the clean corpus for the pipeline-context rules.

Counts are files (non-test + test) / findings. Reproduce with `codescan.py --corpus <dirs>`; `devtests/guard/test_corpus.py` checks these counts and the floors below.

| Rule | Pattern | Sev | Archive files (non-test + test) | Archive findings | Clean: legacy + build | Golden steps |
|---|---|---|---|---|---|---|
| S01 swallow-broad | broad/bare `except` without raise/exit | block | 136 + 2 | 242 | 0 | 0 |
| S02 except-pass | handler body only pass/continue/constant (FileNotFoundError cleanup idiom allowed) | block | 33 + 2 | 40 | 0 | 0 |
| S03 failure-as-value | handler without raise returns a dict with success/status/ok/error | block | 79 + 0 | 111 | 0 | 0 |
| S04 import-fallback | import in `try` with an ImportError/broad handler that does not raise | block | 10 + 0 | 10 | 0 | 0 |
| S05 stub-injection | `sys.modules[...] =`, `sys.modules.setdefault/update`, `meta_path` insert, `patch.dict(sys.modules)`, MetaPathFinder/Loader/ModuleType subclass, `builtins.open =` | block | 0 + 2 | 2 | 0 | 0 |
| S06 mock-in-pipeline | mock imports under `pipeline/` | block | 0 + 0 (pipeline only) | 0 | 0 | 0 |
| S07 config-default | `params/config/cfg/settings/conf/spec.get(k, d)`, `getattr(cfg, k, d)`, `ctx.param(k, d)` | block | 164 + 0 | 328 | 0 | 0 |
| S07 (env) | `os.environ.get(k, d)`, `os.getenv(k, d)` | warn | 3 + 0 | 8 | 0 | 0 |
| S08 fill-literal | `fillna(<literal>)`, `nan_to_num` without a declared `nan=`, `replace(nan, <literal>)`, `where(isnan(a), <literal>, a)` | block | 1 + 0 | 1 | 0 | 0 |
| S09 rng | `np.random.*` / `random.*` / `default_rng()` unless seeded from a param | block | 1 + 2 | 4 | 0 | 0 |
| S10 fabricate-missing | `if not p.exists():` branch builds data (GeoDataFrame/DataFrame/zeros/RNG/`rasterio.open(...,'w')`) without raising | block | 0 + 0 | 0 | 0 | 0 |
| S11 reference-path (a) | absolute path literal (2+ segments or a known root such as `/tmp`, `/Volumes`; `/vsi*`, `/dev` excluded; concatenation and f-string tails are suffixes) | block | 107 + 14 | 342 | **legacy 5 files / 9** (by design, see note) · build 0 | 0 |
| S11 (b) | literal, folded `join`/`Path` tail or `params.yaml` value resolving into R (listed or existing paths only) | block | n/a (needs `graph.json`) | n/a | n/a | 0 |
| S11 (basename) | literal equal to the basename of a protected legacy file | warn | n/a | n/a | n/a | 0 |
| S11 (c) | `walk/glob/rglob/listdir/scandir` on a params path | warn | 0 | 0 | 0 | 0 |
| S12 test-mocks-subject | `patch`, `patch.object`, `mocker.patch`, `monkeypatch.setattr` on the code under test or on geopandas/rasterio/numpy/pandas/shapely/pyproj/pyogrio/fiona/scipy/xarray/rioxarray/exactextract | block | 0 + **44** | 163 | 0 | 0 |
| S13 analysis-import | import from `analysis`/`migration.analysis`, relative `..analysis`, `import_module('analysis…')`, `sys.path` additions of `analysis/` | block | 0 + 0 | 0 | 0 | 0 |
| S14 legacy-engine | `import arcpy/qgis/processing`; `subprocess`/`os.system` running `qgis_process`/`arcpy` (exempt in `keep_legacy` steps) | block | 0 + 0 | 0 | **legacy 5 / 15 · build 1 / 1** (by design) | 0 |
| S15 parse | `ast.parse` fails; Markdown fence/table/heading in a `.py` | block | 11 + 6 | 17 | 0 | 0 |
| S16 param-id | `params["k"]` / `ctx.param("k")` with k not in `params.yaml` | block | n/a (needs `params.yaml`) | n/a | n/a | n/a (no migration dir) |
| S17 3857-distance | `to_crs(3857)` then `.buffer/.distance/.area/.length` in the same function | warn | 3 + 0 | 4 | 0 | 0 |
| S18 axis-order | `Transformer.from_crs/from_proj` without `always_xy=True` | warn | 0 + 0 | 0 | 0 | 0 |
| T01 tolerance-record | a `tolerances.yaml` record without `correct_spread`/`nearest_wrong` (08 §5c) | warn | n/a | n/a | n/a | n/a |

Floors checked in `devtests/guard/test_corpus.py` are the 08 §6 floors: S01 ≥ 120 files, S03 ≥ 70, S07 ≥ 150 and
S12 ≥ 30. All of them pass, with 138, 79, 164 and 44.

**Clean corpus result.** Every rule that applies to plain Python (S01–S10, S12, S13, S15, S17, S18) has 0 hits on the 10
clean scripts. **S11(a) and S14 fire on the legacy scripts by design.** Those scripts *are* the legacy engine
(`import processing`, `import arcpy`) and hard-code the fixture path (`/home/…/lux-exposure-legacy`, `D:\GIS\…`),
which is exactly what pipeline code must not do. Both are pipeline-context rules. Their clean corpus is the golden
lux pipeline, where they give 0 hits. No rule shipped with a clean-corpus false positive. One false positive was
fixed during measurement: `"/Absolute"`, a QGIS project key in `30_make_qgz.py`. A single-segment absolute path now
needs a known root.

## Rules with 0 archive hits that still block

Five block rules have no archive hits. Each is kept for the reason below.

| Rule | Why it still blocks |
|---|---|
| S06 | The archive had no pipeline/test split, so this cannot be measured there. The pattern is unambiguous. |
| S10 | 08 keeps it as a block rule for the "missing input" negative control (NC1). |
| S13 | The archive had no `analysis/` folder. The pattern is unambiguous. |
| S14 | The archive's generated code never imported arcpy. The pattern is unambiguous and has a declared exemption. |
| S16 | Needs a `params.yaml`. It is exact by construction. |

## Deviations from 08 §4 (all measured or fail-closed)

- **S12 subject.** "The subject" also covers modules the test loaded through importlib or a relative import, local
  module names, and `step_NN` modules, besides `pipeline.*`/`axis_pipeline.*`/`sNN_*`. Without this the archive
  floor (≥ 30) was 4 files: the archive's tests patch `step_003.download_file` and similar functions.
- **S06.** The names `MagicMock`/`Mock`/`create_autospec`/`AsyncMock` count from any module. `patch` counts only
  from a module whose name contains `mock`, because `from jsonpatch import patch` is not a mock.
- **S08.** `nan_to_num(..., nan=<literal>)` also fires, as the same pattern as `fillna(0)`.
- **S11.** These are additions: (i) a bare output-directory literal that resolves to an existing directory with no
  input under it (for example `"05_TEMP"`) fires; (ii) a literal equal to a protected basename is a warning.
- **S15.** A `#` heading is flagged only when the file also contains a code fence, so ordinary `# comment` first lines
  pass.
- **T01** is new: the 08 §5c "scanner checks each record has `correct_spread` and `nearest_wrong`" is a warning.
- **R.** The protected set is always computed fresh from `graph.json` and `inventory.json`; the
  `.axis/protected.json` cache is never trusted for a verdict. Its record format is the one `graph.py protected`
  writes; `hooks/tests/test_protected_records.py` checks that both produce the same record.

## Stop-gate claim check (`claims.py`) deviations

- The 08 §5b regex is used verbatim. Two things are added: "runs/ran/executed/completed successfully" (sandbox-validated,
  from its phrase map) and the 09 §4.3 Databricks lint words ("deployed", "runs on Databricks", "validated on
  Databricks", "tested on the cluster", "in production", "production-ready"). These map to a sentinel rung above
  target-ready, so no computed rung supports them.
- **Negation.** The 3-token window adds `no/never/nothing/none/yet/cannot/...` and the modals
  `will/would/should/could/can/may/might/must/to`. `before/until/unless/if/once/whether` negate their whole clause,
  so "run the comparison before the step is verified" is not a claim. `after/when` do **not** negate, because
  "After the chained run, all checks pass" is a claim.
- **Quoted text.** A claim inside quotes still counts, so quoting cannot be used to evade the check. The words before
  the opening quote also apply: `I did not write "all checks pass"` is negated.
- **Remaining false positive (seen live in HM run 2).** "one completed with a warning" matches `complete[ds]?` and
  costs one rewrite turn. That cost is bounded by the 3-block cap.
- **Evidence checks.** Missing evidence (compare files, report, EVIDENCE_ID) blocks only claims at reference-verified
  or above. Drift (params, tolerances, code, `LEGACY_REF_CHANGED`) and open high-impact questions block any claim.
  Unacked user graph edits block regardless of claims, per DESIGN §4.2 / D2-14.
