#!/usr/bin/env python3
"""Measure the lux tolerance numbers (06 §4 table) and write PROPOSED records (agreed_by: null; never signed).

  axpy measure_lux.py [--work DIR]

correct_spread = max over legitimate alternative implementations (independent rasterio/geopandas pipeline with
masked sums; the same with np.bincount zonal sums; float64 exposure, legitimate only with a dtype_widening KD).
nearest_wrong  = min over plausible wrong recipes (code variants of the independent pipeline, chained downstream)
                 and deterministic tolcal mutants of the legacy file, per dataset + check, among those that FAIL.
Writes lux_measurements.json and lux_tolerance_proposals.yaml next to this file.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import lux_lib as L  # noqa: E402
import lux_independent as I  # noqa: E402
import compare  # noqa: E402
import tolcal  # noqa: E402

INP = L.FIXTURE / "03_INPUT"
DOWNSTREAM = {"s01": ["s01", "s02", "s03", "s04"], "s02": ["s02", "s03", "s04"], "s03": ["s03", "s04"],
              "s04": ["s04"]}
STEP_DS = {s: [d for d in L.PAIRED if L.DATASETS[d][4].startswith(s)] for s in ("s01", "s02", "s03", "s04")}

# Recipes whose defect is invisible to a check *by construction* (D6-7: conservation is necessary, not sufficient)
# are not candidates for that check's nearest wrong; the mask/value checks catch them. s04 renormalises every
# canton (factor = tot_value / res_sum), so any defect that only changes the weights upstream of s04 leaves the
# canton totals of every s04 output equal up to float rounding.
S04_CONSERVATION = ("conservation.zone_totals", "conservation.total", "vector.attr:exp_sum", "vector.attr:tot_value",
                    "table.attr:exposure_raster_sum_eur", "table.total:exposure_raster_sum_eur",
                    "table.attr:total_value_eur", "table.total:total_value_eur", "table.attr:diff_eur",
                    "table.total:diff_eur")
WEIGHT_ONLY = {"W_s02_centre_mask", "W_s02_linework", "W_s02_simplify10", "W_s02_area3857", "W_s02_area_degrees",
               "W_s02_area_ellipsoidal",
               "W_s03_arcpy"}
SHIFT_ONLY = {"W_s01_tap", "W_s01_crop"}                    # values copied 1:1: totals unchanged by construction


def invisible(name: str, did: str, cid: str) -> bool:
    if name in WEIGHT_ONLY and L.DATASETS[did][4] == "s04_distribute_value" and cid in S04_CONSERVATION:
        return True
    if name in SHIFT_ONLY and did == "ds.01_pop_lux" and cid == "conservation.total":
        return True
    return False


ABS_METRIC = {"diff_eur"}
MARGIN = 10.0                                               # proposals need nearest_wrong >= 10 x correct_spread                                   # a near-zero difference column: absolute EUR bound


def tap_shift(src: Path, dst: Path):
    """T1a: -tap emulated: x0 = floor(c/a)*a, y0 = ceil(f/|e|)*|e|, values copied 1:1 (10 §3)."""
    import rasterio
    from affine import Affine
    with rasterio.open(src) as ds:
        prof, data, t = ds.profile.copy(), ds.read(), ds.transform
    x0 = math.floor(t.c / t.a) * t.a
    y0 = math.ceil(t.f / abs(t.e)) * abs(t.e)
    prof["transform"] = Affine(t.a, 0, x0, 0, t.e, y0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **prof) as o:
        o.write(data)


def crop_to_cutline(src: Path, dst: Path, pad=(10, 3, 1, 0)):
    """L4: CROP_TO_CUTLINE: the cutline extent reaches 10 cells west, 3 east, 1 north past the grid (953 x 881)."""
    import numpy as np
    import rasterio
    from affine import Affine
    w_, e_, n_, s_ = pad
    with rasterio.open(src) as ds:
        prof, data, t = ds.profile.copy(), ds.read(1), ds.transform
    H, W = data.shape
    big = np.full((H + n_ + s_, W + w_ + e_), prof["nodata"], dtype=data.dtype)
    big[n_:n_ + H, w_:w_ + W] = data
    prof.update(width=big.shape[1], height=big.shape[0], transform=Affine(t.a, 0, t.c - w_ * t.a, 0, t.e, t.f - n_ * t.e))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(dst, "w", **prof) as o:
        o.write(big, 1)


def bilinear_file(src: Path, dst: Path):
    tmp = dst.parent / "_bil"
    res = tolcal.raster_mutants(str(src), _fresh(tmp), recipe=None, shift_k=(), scale_k=())
    shutil.copy2([m for m in res if m["id"] == "bilinear_roundtrip"][0]["path"], dst)
    shutil.rmtree(tmp)


def _fresh(p: Path) -> Path:
    if p.exists():
        shutil.rmtree(p)
    p.mkdir(parents=True)
    return p


def run_variant(work: Path, name: str, start: str, kw: dict, s01_file_mutation=None) -> Path:
    out = _fresh(work / name)
    clean = work / "A1_clean"
    for s in ("s01", "s02", "s03"):
        if s not in DOWNSTREAM[start]:
            src = clean / L.STEPS[["s01", "s02", "s03", "s04"].index(s)]
            shutil.copytree(src, out / src.name)
    steps = DOWNSTREAM[start]
    if "s01" in steps:
        I.s01(INP, out, **kw.get("s01", {}))
        if s01_file_mutation:
            p = out / "s01_prepare_population" / "01_pop_lux.tif"
            tmp = p.with_name("_m.tif")
            s01_file_mutation(p, tmp)
            shutil.move(tmp, p)
    if "s02" in steps:
        I.s02(INP, out, **kw.get("s02", {}))
    if "s03" in steps:
        I.s03(INP, out, **kw.get("s03", {}))
    if "s04" in steps:
        I.s04(INP, out, **kw.get("s04", {}))
    return out


def summarise(r: dict) -> dict:
    out = {"verdict": r["verdict"], "reason_codes": r["reason_codes"], "diagnostic_codes": r["diagnostic_codes"],
           "checks": {}}
    for c in r["checks"]:
        x = {"state": c["state"], "metric": c.get("metric"), "value": c.get("value"), "codes": c["reason_codes"]}
        st = c.get("stats") or {}
        for k in ("max_rel", "max_abs", "count_over", "count_diff"):
            if k in st:
                x[k] = st[k]
        if c.get("diagnostics"):
            x["diagnostics"] = {k: v for k, v in c["diagnostics"].items() if k != "skipped"}
        out["checks"][c["id"]] = x
    return out


def compare_tree(out: Path, which: list[str], clean: Path | None = None) -> dict:
    """vs the legacy reference; with `clean`, also vs the clean independent output (to see what the recipe changed)."""
    res = {}
    for did in which:
        p = L.outputs_from(out, [did]).get(did)
        if p is None:
            res[did] = {"verdict": "FAIL", "reason_codes": ["OUTPUT_MISSING"], "checks": {}}
            continue
        r = compare.compare_pair(str(L.FIXTURE / L.DATASETS[did][2]), str(p), L.pair_spec(did))
        res[did] = summarise(r)
        if clean is not None:
            c = L.outputs_from(clean, [did]).get(did)
            rc = compare.compare_pair(str(c), str(p), L.pair_spec(did))
            res[did]["changed_vs_clean"] = sorted(k for k, v in summarise(rc)["checks"].items()
                                                  if v["state"] != "PASS_EXACT" or v.get("count_diff"))
    return res


LEGIT = {
    "A1_clean": ("s01", {}, "independent rasterio/geopandas implementation (masked numpy sums, make_valid structure)"),
    "A2_bincount": ("s03", {"s03": {"zonal": "bincount"}, "s04": {"zonal": "bincount"}},
                    "zonal sums by np.bincount (different summation order)"),
    "A3_float64_exposure": ("s04", {"s04": {"exposure_dtype": "float64"}},
                            "exposure written float64 (legitimate only with KD dtype_widening)"),
}
WRONG = {
    "W_s01_tap": ("s01", {}, "-tap half-cell snap (T1a formula)", tap_shift),
    "W_s01_crop": ("s01", {}, "CROP_TO_CUTLINE (953x881, offset -10,-1)", crop_to_cutline),
    "W_s01_bilinear": ("s01", {}, "bilinear round trip of population on the same grid", bilinear_file),
    "W_s01_clip_all_touched": ("s01", {"s01": {"clip_all_touched": True}}, "clip with all_touched", None),
    "W_s02_area3857": ("s02", {"s02": {"area_crs": "EPSG:3857"}}, "area measured in EPSG:3857", None),
    "W_s02_area_ellipsoidal": ("s02", {"s02": {"area_crs": "ellipsoidal"}}, "ellipsoidal (WGS84) area instead of "
                               "planar EPSG:2169", None),
    "W_s02_area_degrees": ("s02", {"s02": {"area_crs": "EPSG:4326"}}, "area measured in degrees", None),
    "W_s02_linework": ("s02", {"s02": {"make_valid_method": "linework"}}, "shapely make_valid default (linework)", None),
    "W_s02_centre_mask": ("s02", {"s02": {"all_touched": False}}, "mask rasterised cell-centre (no -at)", None),
    "W_s02_simplify10": ("s02", {"s02": {"simplify_m": 10.0}}, "residential polygons simplified 10 m", None),
    "W_s03_arcpy": ("s03", {"s03": {"arcpy_semantics": True}}, "ArcPy 03b semantics (0 not NoData)", None),
    "W_s03_zones_all_touched": ("s03", {"s03": {"zones_all_touched": True}}, "zones rasterised all_touched", None),
    "W_s04_factor_all_touched": ("s04", {"s04": {"factor_all_touched": True}}, "factor rasterised all_touched", None),
    "W_s04_nostrip": ("s04", {"s04": {"strip": False}}, "canton names not stripped (Wiltz unmatched)", None),
    "W_s04_noscale": ("s04", {"s04": {"scale": 1.0}}, "kEUR not x1000", None),
    "W_s04_factor_shift1": ("s04", {"s04": {"factor_shift_cols": 1}}, "factor raster one cell east (T1b)", None),
}

# the metric a record of each check bounds, and how to read it from a summarised check
RECORD_METRIC = {"raster.values": "max_rel", "conservation.zone_totals": "value", "conservation.total": "value",
                 "vector.symdiff_ratio": "value", "vector.hausdorff_m": "value", "raster.mask": "value"}


def metric_of(cid: str, x: dict):
    col = cid.split(":", 1)[-1]
    if cid.startswith(("vector.attr:", "table.attr:")):
        if col in ABS_METRIC:
            return x.get("max_abs"), "max_abs"
        return x.get("max_rel"), "max_rel"
    if cid.startswith("table.total:"):
        if col in ABS_METRIC:
            return None, None
        return x.get("value"), "rel_drift"
    k = RECORD_METRIC.get(cid)
    if k is None:
        return None, None
    return x.get(k), k


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", default=str(HERE / ".work"))
    a = ap.parse_args(argv)
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    meas = {"legit": {}, "wrong": {}, "tolcal": {}}
    for name, (start, kw, recipe) in LEGIT.items():
        out = run_variant(work, name, start, kw)
        which = [d for s in DOWNSTREAM[start] for d in STEP_DS[s]]
        meas["legit"][name] = {"recipe": recipe, "datasets": compare_tree(out, which)}
    for name, (start, kw, recipe, fm) in WRONG.items():
        out = run_variant(work, name, start, kw, fm)
        which = [d for s in DOWNSTREAM[start] for d in STEP_DS[s]]
        meas["wrong"][name] = {"recipe": recipe, "start": start,
                               "datasets": compare_tree(out, which, clean=work / "A1_clean")}
    for did in L.PAIRED:
        kind = L.DATASETS[did][0]
        spec = L.pair_spec(did)
        res = tolcal.mutate(str(L.FIXTURE / L.DATASETS[did][2]), kind, str(work / "tolcal" / did), spec=spec,
                            key=(spec["compare"].get("key") or None))
        meas["tolcal"][did] = {m["id"]: {"verdict": m["verdict"], "reason_codes": m["reason_codes"],
                                         "diagnostic_codes": m["diagnostic_codes"],
                                         "checks": {k: {kk: vv for kk, vv in v.items() if kk != "stats"}
                                                    | ({"max_rel": v["stats"].get("max_rel")} if v.get("stats") else {})
                                                    for k, v in m["metrics"].items()}}
                               for m in res["mutants"]}
    # ---------------------------------------------------------------- spreads and nearest wrong per dataset+check
    spread: dict = {}
    KD_ONLY = {"A3_float64_exposure"}
    for name, v in meas["legit"].items():
        for did, r in v["datasets"].items():
            for cid, x in r["checks"].items():
                if x["state"] == "PASS_EXACT" and not x.get("count_diff"):
                    continue
                if cid in ("raster.dtype",):
                    continue
                val, met = metric_of(cid, x)
                if val is None:
                    continue
                key = (did, cid, name in KD_ONLY)
                if key not in spread or val > spread[key][0]:
                    spread[key] = (val, met, name)
    wrong: dict = {}

    def consider(did, cid, x, name, recipe, changed=None):
        if x["state"] != "FAIL" or invisible(name, did, cid):
            return
        if changed is not None and cid not in changed:
            return                                          # the recipe did not change this check: spread only
        val, met = metric_of(cid, x)
        if val is None or not (isinstance(val, (int, float)) and val > 0):
            return
        wrong.setdefault((did, cid), []).append((val, met, name, recipe))
    for name, v in meas["wrong"].items():
        for did, r in v["datasets"].items():
            for cid, x in r["checks"].items():
                consider(did, cid, x, name, v["recipe"], r.get("changed_vs_clean", []))
    for did, muts in meas["tolcal"].items():
        for mid, m in muts.items():
            for cid, x in m["checks"].items():
                consider(did, cid, x, f"tolcal:{mid}", f"tolcal mutant {mid} of the legacy file")
    rows = []
    for (did, cid, kd_only), (sv, met, sname) in sorted(spread.items()):
        # a recipe whose deviation does not exceed the spread is invisible to this check (measured): other checks
        # must catch it; the nearest wrong is the closest recipe the check can separate
        cands = sorted(x for x in wrong.get((did, cid), []) if x[0] > sv)
        w = cands[0] if cands else None
        below = sorted({x[2] for x in wrong.get((did, cid), []) if x[0] <= sv})
        rows.append({"dataset": did, "check": cid, "metric": met, "correct_spread": sv, "spread_from": sname,
                     "only_with_kd": "dtype_widening float32->float64 on ds.lux_res_exposure_2020" if kd_only else None,
                     "nearest_wrong": w[0] if w else None, "wrong_from": w[2] if w else None,
                     "wrong_recipe": w[3] if w else None, "recipes_not_separable_by_this_check": below})
    meas["spread_vs_wrong"] = rows
    meas["nearest_wrong_all"] = [{"dataset": d, "check": c, "value": min(v)[0], "metric": min(v)[1],
                                  "from": min(v)[2]} for (d, c), v in sorted(wrong.items())]
    (HERE / "lux_measurements.json").write_text(json.dumps(meas, indent=1, sort_keys=True, default=str) + "\n")
    # ---------------------------------------------------------------- proposals (unsigned)
    # ---------------------------------------------------------------- the canton-sum invariant (legacy first)
    inv_rows = {}
    for name in ("A1_clean", "A3_float64_exposure", "W_s04_factor_all_touched", "W_s02_linework", "W_s04_nostrip"):
        tree = work / name
        exp = L.outputs_from(tree, ["ds.lux_res_exposure_2020"])["ds.lux_res_exposure_2020"]
        r = compare.compare_pair(str(L.FIXTURE / "04_FINAL/lux_res_exposure_2020.tif"), str(exp),
                                 L.invariant_pair_spec(tree))
        c = next(x for x in r["checks"] if x["id"] == f"invariant:{L.INVARIANT_ID}")
        inv_rows[name] = {"legacy": c["legacy"], "new": c["new"], "state": c["state"], "codes": c["reason_codes"]}
    meas["invariant"] = inv_rows
    legacy_spread = inv_rows["A1_clean"]["legacy"]
    legit = max([legacy_spread] + [inv_rows[n]["new"] for n in ("A1_clean", "A3_float64_exposure")])
    wrongs = sorted((inv_rows[n]["new"], n) for n in inv_rows if n.startswith("W_") and inv_rows[n]["new"] > legit)
    import yaml
    recs = []
    if wrongs:
        recs.append(tolcal.propose("ds.lux_res_exposure_2020", f"invariant:{L.INVARIANT_ID}", correct_spread=legit,
                                   nearest_wrong=wrongs[0][0], form="rtol",
                                   spread_how="the legacy run itself (float32 exposure) and the legitimate "
                                              "alternatives; invariant evaluated on legacy first",
                                   spread_evidence="devtests/verify/lux_measurements.json#invariant",
                                   wrong_variant=WRONG[wrongs[0][1]][2],
                                   wrong_evidence=f"devtests/verify/lux_measurements.json#invariant.{wrongs[0][1]}"))
    for row in rows:
        if row["nearest_wrong"] is None or row["correct_spread"] is None:
            continue
        if row["nearest_wrong"] < MARGIN * max(row["correct_spread"], 1e-300) and row["correct_spread"] > 0:
            row["no_proposal"] = (f"margin nearest_wrong/correct_spread = {row['nearest_wrong'] / row['correct_spread']:.4g}"
                                  f" < {MARGIN}: no threshold separates them robustly; the check stays exact")
            continue
        form = "value" if row["check"].startswith(("vector.symdiff", "vector.hausdorff")) else \
            ("atol" if row["metric"] == "max_abs" else "rtol")
        how = LEGIT[row["spread_from"]][2] + (f"; applies only with KD {row['only_with_kd']}" if row["only_with_kd"]
                                              else "")
        rec = tolcal.propose(row["dataset"], row["check"], correct_spread=row["correct_spread"],
                             nearest_wrong=row["nearest_wrong"], form=form, units="EUR" if form == "atol" else "1",
                             spread_how=how,
                             spread_evidence=f"devtests/verify/lux_measurements.json#legit.{row['spread_from']}",
                             wrong_variant=row["wrong_recipe"],
                             wrong_evidence=f"devtests/verify/lux_measurements.json#{row['wrong_from']}")
        recs.append(rec)
    doc = {"version": 1, "records": recs}
    header = ("# PROPOSED lux tolerance records, measured by devtests/verify/measure_lux.py.\n"
              "# Every record is UNSIGNED (agreed_by: null). Only Najah signs, by editing agreed_by/agreed_at himself.\n"
              "# Checks not listed here stay exact (default): the independent implementation reproduced them exactly.\n")
    (HERE / "lux_tolerance_proposals.yaml").write_text(header + yaml.safe_dump(doc, sort_keys=False))
    (HERE / "lux_measurements.json").write_text(json.dumps(meas, indent=1, sort_keys=True, default=str) + "\n")
    for row in rows:
        print(f"{row['dataset']:30s} {row['check']:36s} {row['metric']:9s} spread {row['correct_spread']!r:24} "
              f"({row['spread_from']}) wrong {row['nearest_wrong']!r} ({row['wrong_from']}) "
              f"not separable: {row['recipes_not_separable_by_this_check']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
