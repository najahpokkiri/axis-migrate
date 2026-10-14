"""axis_pipeline: a migrated pipeline written by axis-migrate.

One module per graph step (`sNN_*.py`, `mNN_*.py`), each defining STEP_ID, INPUTS, OUTPUTS, PARAMS and
`run(ctx)`. `_axis_runtime.py` and `__main__.py` are vendored from the axis-migrate plugin and must stay
byte-identical to it. Run a step only through the runner (`run_local.py`) or `python -m axis_pipeline`.
"""
