"""fit.py protocol gates (no runner needed): spec validation, hash freezing, declared-before-run."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fit  # noqa: E402
from test_params_check import make, good_param  # noqa: E402

FIT = {"reference": "ds.out", "candidates": [0, 10, 20], "metric": "cell_mismatch_count",
       "split": {"kind": "zones", "zones": "ds.z", "field": "name", "fit": ["a"], "holdout": "rest"}}


def test_validate_spec():
    assert fit.validate_spec(FIT) == []
    assert fit.validate_spec({**FIT, "candidates": list(range(13))})
    assert fit.validate_spec({**FIT, "metric": "rmse"})
    assert fit.validate_spec({**FIT, "split": {"kind": "zones"}})


def test_spec_hash_ignores_result_and_changes_with_candidates():
    a = fit.spec_sha256(FIT, "p.x", "s01_a")
    assert a == fit.spec_sha256({**FIT, "result": {"best": 1}}, "p.x", "s01_a")
    assert a != fit.spec_sha256({**FIT, "candidates": [0, 10]}, "p.x", "s01_a")


def test_run_refuses_without_declared_spec(tmp_path, capsys):
    mig = make(tmp_path, [good_param(fit=FIT)])
    assert fit.main(["run", str(mig), "--param", "p.thresh_m"]) == 2
    assert "fit_spec" in capsys.readouterr().err


def test_run_refuses_changed_spec(tmp_path, capsys):
    mig = make(tmp_path, [good_param(fit={**FIT, "spec_sha256": "0" * 64})])
    assert fit.main(["run", str(mig), "--param", "p.thresh_m"]) == 2
    assert "changed after it was declared" in capsys.readouterr().err


def test_declare_prints_hash_and_entry(tmp_path, capsys):
    mig = make(tmp_path, [good_param(fit=FIT)])
    assert fit.main(["declare", str(mig), "--param", "p.thresh_m"]) == 0
    out = capsys.readouterr().out
    assert "kind: fit_spec" in out and fit.spec_sha256(FIT, "p.thresh_m", "s01_a") in out
