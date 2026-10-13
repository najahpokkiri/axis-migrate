#!/usr/bin/env python3
"""Rubric calibration (06 §6.2, 10 T0b): render plot.py plots for lux controls and mutants, then join the
expected answers (what a careful reader must say, from the design's must-flag / must-not-flag table) with the answers
observed by a reviewer who actually opened each PNG (rubric_observations.v1.yaml, keyed by plot sha256).

  axpy calibrate_rubric.py build [--out DIR]     build runs, compare, plot; write DIR/manifest.json (PNG paths, shas)
  axpy calibrate_rubric.py write [--out DIR]     join manifest + EXPECTED + observations → evals/calib/rubric.v1.json

An observation is only valid for the exact PNG bytes it was made on: if plot.py or the data change, the sha changes
and the observation is reported stale (it must be looked at again). Mutants are built by deterministic code variants
of the independent implementation or by mutation of a tmp copy (measure_lux.WRONG / LEGIT); the fixture is never
touched. Lux knowledge lives here (devtests), never in the plugin.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import lux_lib as L  # noqa: E402
import lux_independent as I  # noqa: E402
import measure_lux as M  # noqa: E402
import axis_common as ac  # noqa: E402
import compare  # noqa: E402
import plot  # noqa: E402

DEFAULT_OUT = Path("/tmp") / "axis-rubric-calib"
CALIB_JSON = REPO / "evals" / "calib" / "rubric.v1.json"
OBS = HERE / "rubric_observations.v1.yaml"

RASTERS = ["ds.01_pop_lux", "ds.02_res_mask", "ds.04_factor", "ds.lux_res_exposure_2020"]
VECTORS = ["ds.02_res_2169", "ds.04_canton_factor"]

# case -> (variant tree name | "legacy_copies", datasets to plot, what it is)
CASES = {
    "L1_identity": ("legacy_copies", RASTERS + VECTORS, "legacy files copied as the new outputs"),
    "L2_independent": ("A1_clean", RASTERS + VECTORS, "independent rasterio/geopandas implementation"),
    "L3_tap_shift": ("W_s01_tap", ["ds.01_pop_lux"], "-tap half-cell snap of s01 (values 1:1)"),
    "L5_bilinear": ("W_s01_bilinear", ["ds.01_pop_lux"], "bilinear round trip of population, same grid"),
    "L7_factor_all_touched": ("W_s04_factor_all_touched", ["ds.04_factor", "ds.lux_res_exposure_2020"],
                              "factor rasterised all_touched"),
    "L7b_clip_all_touched": ("W_s01_clip_all_touched", ["ds.01_pop_lux"], "s01 clip with all_touched"),
    "T1b_factor_shift1": ("W_s04_factor_shift1", ["ds.04_factor", "ds.lux_res_exposure_2020"],
                          "factor raster one cell east"),
    "L8_wiltz_unmatched": ("W_s04_nostrip", ["ds.lux_res_exposure_2020", "ds.04_canton_factor"],
                           "canton names not stripped: Wiltz unmatched"),
    "L9_kEUR_not_scaled": ("W_s04_noscale", ["ds.lux_res_exposure_2020"], "kEUR not ×1000 (×1/1000)"),
    "L12_float64": ("A3_float64_exposure", ["ds.lux_res_exposure_2020"], "exposure written float64 (legitimate "
                                                                        "with a dtype_widening KD)"),
    "S_simplify10": ("W_s02_simplify10", ["ds.02_res_2169"], "residential polygons simplified 10 m"),
}

# Expected answers of a careful reader. Controls: every applicable question "no" (must not flag). Mutants: the
# question(s) the design says must flag (06 §4h table, 10 §3 column d); other questions are recorded but not graded.
CONTROLS = {"L1_identity", "L2_independent", "L12_float64"}
MUST_FLAG = {
    ("L3_tap_shift", "ds.01_pop_lux"): {"Q1": "whole_offset"},
    ("L5_bilinear", "ds.01_pop_lux"): {"Q2": "smoothed_blurred"},
    ("L7_factor_all_touched", "ds.lux_res_exposure_2020"): {"Q6": "boundary_stripes"},
    ("L7_factor_all_touched", "ds.04_factor"): {"Q6": "boundary_stripes"},
    ("L7b_clip_all_touched", "ds.01_pop_lux"): {"Q6": "boundary_stripes"},
    ("T1b_factor_shift1", "ds.lux_res_exposure_2020"): {"Q6": "boundary_stripes"},   # 10 §3 T1b question
    ("T1b_factor_shift1", "ds.04_factor"): {"Q1": "whole_offset"},
    ("L8_wiltz_unmatched", "ds.lux_res_exposure_2020"): {"Q4": "missing_region"},
    ("L8_wiltz_unmatched", "ds.04_canton_factor"): {"Q4": "missing_region"},
    ("L9_kEUR_not_scaled", "ds.lux_res_exposure_2020"): {"Q7": "uniform_scale"},
    ("S_simplify10", "ds.02_res_2169"): {"Q3": "angular_outline"},
}


def _questions(fam: str) -> list[str]:
    rubric, _ = plot.load_rubric()
    return [q["id"] for q in rubric["questions"] if fam in q["families"]]


def build(out: Path) -> dict:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    work = out / "work"
    I.run_all(L.FIXTURE / "03_INPUT", work / "A1_clean")
    trees = {}
    manifest = {"plot_version": plot.VERSION, "plot_sha256": ac.sha256_file(Path(plot.__file__)),
                "rubric_sha256": plot.load_rubric()[1], "cases": {}}
    for case, (tree_name, dsets, what) in CASES.items():
        root = out / "runs" / case
        leg = L.copy_fixture(root)
        if tree_name == "legacy_copies":
            outputs = L.legacy_copies(leg)
        else:
            if tree_name not in trees:
                if tree_name == "A1_clean":
                    trees[tree_name] = work / "A1_clean"
                else:
                    spec = M.WRONG.get(tree_name) or M.LEGIT.get(tree_name)
                    fm = spec[3] if len(spec) > 3 else None
                    trees[tree_name] = M.run_variant(work, tree_name, spec[0], spec[1], fm)
            outputs = L.outputs_from(trees[tree_name])
        run = L.build_run(root, outputs)
        results, _ = compare.run_compare(run, dsets)
        rc = plot.main(["run", "--run", str(run)] + sum((["--dataset", d] for d in dsets), []))
        entry = {"what": what, "variant": tree_name, "run": str(run), "plot_exit": rc, "datasets": {}}
        for d in dsets:
            r = results[d]
            pj = ac.load_json(run / "verify" / "plots" / d / "plots.json")
            entry["datasets"][d] = {
                "family": r["family"], "verdict": r["verdict"], "reason_codes": r["reason_codes"],
                "diagnostic_codes": r.get("diagnostic_codes", []),
                "png": {e["name"]: {"sha256": e["sha256"], "path": str(run / "verify" / "plots" / d / e["name"])}
                        for e in pj["png"]}}
        manifest["cases"][case] = entry
        print(f"{case}: " + ", ".join(f"{d} {v['verdict']}" for d, v in entry["datasets"].items()))
    ac.dump_json(out / "manifest.json", manifest)
    return manifest


def write(out: Path) -> dict:
    import yaml
    man = ac.load_json(out / "manifest.json")
    obs = yaml.safe_load(OBS.read_text()) if OBS.exists() else {}
    rubric, rsha = plot.load_rubric()
    doc = {"schema": "axis-migrate.rubric-calibration.v1", "rubric_id": rubric["id"], "rubric_sha256": rsha,
           "plot_version": man["plot_version"], "plot_sha256": man["plot_sha256"],
           "reviewer": (obs or {}).get("reviewer"), "observed_at": (obs or {}).get("observed_at"),
           "method": "expected = design must-flag/must-not-flag; observed = a reviewer opened each cited PNG and "
                     "answered the rubric question; an observation counts only for the PNG sha it cites",
           "cases": {}, "summary": {}}
    n_graded = n_agree = n_stale = n_missing = 0
    plot_blind, false_alarm = [], []
    for case, ce in man["cases"].items():
        out_c = {"what": ce["what"], "variant": ce["variant"], "control": case in CONTROLS, "datasets": {}}
        for d, de in ce["datasets"].items():
            qs = _questions(de["family"])
            mf = MUST_FLAG.get((case, d), {})
            o = ((obs or {}).get("cases") or {}).get(case, {}).get(d, {})
            rows = {}
            for q in qs:
                expected = "no" if case in CONTROLS else ("yes" if q in mf else None)
                ob = o.get(q)
                row = {"expected": expected, "expected_category": mf.get(q)}
                if ob:
                    png = de["png"].get(ob["plot"])
                    stale = png is None or png["sha256"] != ob.get("plot_sha256")
                    row.update(observed=ob["answer"], category=ob.get("category", "none"), plot=ob["plot"],
                               plot_sha256=ob.get("plot_sha256"), panel=ob.get("panel"), note=ob.get("note"),
                               stale=stale)
                    if stale:
                        n_stale += 1
                else:
                    row["observed"] = None
                    if expected is not None:
                        n_missing += 1
                if expected is not None and ob and not row["stale"]:
                    n_graded += 1
                    ok = ob["answer"] == expected and (expected != "yes" or ob.get("category") == mf.get(q))
                    row["agree"] = ok
                    n_agree += ok
                    if not ok and expected == "yes":
                        plot_blind.append(f"{case}/{d}/{q}")
                    if not ok and expected == "no":
                        false_alarm.append(f"{case}/{d}/{q}")
                rows[q] = row
            out_c["datasets"][d] = {"verdict": de["verdict"], "reason_codes": de["reason_codes"],
                                    "png": {k: v["sha256"] for k, v in de["png"].items()}, "questions": rows,
                                    "yes": sorted(q for q, r in rows.items() if r.get("observed") == "yes")}
        doc["cases"][case] = out_c
    doc["summary"] = {"graded": n_graded, "agree": n_agree, "stale": n_stale, "missing": n_missing,
                      "missed_must_flag": plot_blind, "flag_on_control": false_alarm,
                      "fixed_plot_blind": (obs or {}).get("fixed_plot_blind", [])}
    CALIB_JSON.parent.mkdir(parents=True, exist_ok=True)
    ac.dump_json(CALIB_JSON, doc)
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("cmd", choices=["build", "write"])
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    out = Path(a.out)
    if a.cmd == "build":
        build(out)
        return 0
    d = write(out)
    s = d["summary"]
    print(json.dumps(s, indent=1))
    return 0 if (s["graded"] and s["agree"] == s["graded"] and not s["stale"] and not s["missing"]) else 1


if __name__ == "__main__":
    sys.exit(main())
