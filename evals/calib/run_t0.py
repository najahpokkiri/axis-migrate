"""T0 comparator calibration for S1 (traps T3, T4, T5; plus the T5L keep-or-fix pair). No model involved.

    axpy evals/calib/run_t0.py [--rebuild]

Inputs: the calibration outputs pinned in evals/calib/s1/variants.json (built by evals/calib/s1/build_variants.py
through the real runner; rebuilt here when missing or with --rebuild) and the real-QGIS references in
evals/cases/_bases/{s1,s1L}-buffers-legacy/. Pairing and compare blocks come from the golden project's graph.json
(make_project.py: real scan.py + lineage.py + graph ops).

Phases (all through compare.py's `pair` entry point, `compare_pair()`, the function the CLI calls):
  1 measure   every variant x dataset with NO tolerance records (default exact) -> the raw spreads
  2 propose   for each (dataset, check) that a clean or alternative output does not pass exactly:
              correct_spread = max over clean + alternatives; nearest_wrong = min over the mutants that this check
              must catch (value above correct_spread); threshold = a round number strictly between them.
              Written UNSIGNED to evals/calib/s1/tolerances.proposed.yaml (agreed_by: null; only Najah signs)
  3 verdict   every variant x dataset again, with the proposals signed IN MEMORY by the test identity
              human:t0-calibration (never written to a migration) -> T0 verdicts
  4 unsigned  the alternatives once more with the proposals unsigned -> NEEDS_DECISION/TOLERANCE_UNSIGNED expected
Expectations (10 §3): each mutant FAILs on its planted dataset(s) with at least one named code; every clean and
alternative dataset PASSes (0 false alarms). A miss or false alarm is reported, never fixed by loosening.
Writes evals/calib/t0_s1.json.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PLUGIN = REPO / "axis-migrate"
AXPY = PLUGIN / "bin" / "axpy"
sys.path.insert(0, str(PLUGIN / "lib"))
import axis_common as ac  # noqa: E402

CAL = HERE / "s1"
BASES = REPO / "evals" / "cases" / "_bases"
GOLDEN_GRAPH = REPO / "evals" / "work" / "calib-s1" / "clean" / "migration" / "graph.json"
STARTED = "2026-10-16T10:00:00Z"          # calibration run start; the test signature predates it
SIGNED_AT = "2026-10-15T09:00:00Z"
MEASURE_CRS = "EPSG:2169"
GEOM_CHECKS = ("vector.hausdorff_m", "vector.symdiff_ratio")

# planted datasets and the codes 10 §3 names for each mutant (at least one must appear on each planted dataset)
EXPECT = {
    "S1-T3": {"datasets": ["ds.02_buf_industrial", "ds.02_buf_other", "ds.02_buffers"],
              "codes_any": ["GEOMETRY_SYMDIFF_EXCEEDED", "GEOMETRY_HAUSDORFF_EXCEEDED"],
              "diag_any": ["VERTEX_COUNT_DROP"]},
    "S1-T4": {"datasets": ["ds.02_buf_industrial", "ds.02_buffers"],
              "codes_any": ["GEOMETRY_SYMDIFF_EXCEEDED", "GEOMETRY_HAUSDORFF_EXCEEDED", "BRANCH_OUTPUT_MISMATCH"],
              "diag_any": [], "must_pass": ["ds.02_buf_other"]},
    "S1-T4drop": {"datasets": ["ds.02_buffers"], "codes_any": ["FEATURE_COUNT_MISMATCH", "KEY_SET_MISSING"],
                  "diag_any": [], "must_pass": ["ds.02_buf_industrial", "ds.02_buf_other"]},
    "S1-T5": {"datasets": ["ds.02_buf_industrial", "ds.02_buf_other", "ds.02_buffers"],
              "codes_any": ["GEOMETRY_SYMDIFF_EXCEEDED", "GEOMETRY_HAUSDORFF_EXCEEDED"],
              "diag_any": ["GEOMETRY_AREA_RATIO"]},
    "W_area_3035": {"datasets": ["ds.buffer_area_by_commune_gpkg"], "codes_any": ["ATTRIBUTE_VALUE_MISMATCH"],
                    "diag_any": [], "must_pass": ["ds.03_buf_by_commune"]},
}


def load_compare():
    spec = importlib.util.spec_from_file_location("axis_compare", PLUGIN / "skills" / "verify" / "compare.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["axis_compare"] = mod
    spec.loader.exec_module(mod)
    return mod


def pairs() -> dict:
    g = json.loads(GOLDEN_GRAPH.read_text())
    out = {}
    for d in g["datasets"]:
        if d["role"] in ("intermediate", "final") and d["legacy"]["exists"] and d.get("new"):
            out[d["id"]] = d
    return out


def spec_for(d: dict, tolerances) -> dict:
    c = {k: v for k, v in d["compare"].items() if v is not None}
    return {"dataset_id": d["id"], "kind": d["kind"], "role": d["role"], "layer": d["legacy"]["layer"],
            "new_layer": (d.get("new") or {}).get("layer"), "compare": c, "sheet": c.get("sheet"),
            "measure_crs": MEASURE_CRS, "run_started_at": STARTED, "tolerances": tolerances, "known_differences": []}


def summarise(r: dict) -> dict:
    checks = {}
    for c in r.get("checks") or []:
        if c["state"] == "PASS_EXACT" and not c.get("value"):
            continue
        e = {"state": c["state"], "codes": c.get("reason_codes") or [], "value": c.get("value")}
        st = c.get("stats") or {}
        if "max_rel" in st:
            e["max_rel"], e["max_abs"] = st["max_rel"], st["max_abs"]
        if st.get("count_over") is not None:
            e["count_over"] = st["count_over"]
        if c.get("diagnostic_codes"):
            e["diag"] = c["diagnostic_codes"]
        if c.get("detail", {}).get("features_differing") is not None:
            e["features_differing"] = c["detail"]["features_differing"]
        for k in ("area_ratio", "vertex_count"):
            if k in (c.get("diagnostics") or {}):
                e[k] = c["diagnostics"][k]
        checks[c["id"]] = e
    return {"verdict": r["verdict"], "codes": r["reason_codes"], "diag": r.get("diagnostic_codes") or [],
            "checks": checks}


def spread_of(check_id: str, e: dict) -> tuple[str, float] | None:
    """(record field, measured value) for a check a tolerance record can widen; None when it cannot."""
    if check_id in GEOM_CHECKS:
        return "value", float(e["value"]) if e.get("value") is not None else math.inf
    if ".attr:" in check_id and "max_rel" in e:
        return "rtol", float(e["max_rel"])
    if check_id.startswith("table.total:"):
        return "rtol", float(e["value"]) if e.get("value") is not None else math.inf
    return None


def nice_between(lo: float, hi: float) -> float | None:
    """A round number (1, 2, 5 x 10^k) strictly between lo and hi, nearest (in log) to their geometric mean."""
    if not (hi > lo) or hi <= 0:
        return None
    lo = max(lo, hi * 1e-12, 1e-15)
    gm = math.sqrt(lo * hi)
    cands = [m * 10.0 ** k for k in range(-16, 7) for m in (1, 2, 5)]
    ok = [c for c in cands if lo < c < hi]
    return min(ok, key=lambda c: abs(math.log(c / gm))) if ok else None


def build_if_needed(rebuild: bool) -> dict:
    pin = json.loads((CAL / "variants.json").read_text())
    need = []
    for name, v in pin["variants"].items():
        for ds, o in v["outputs"].items():
            p = REPO / o["path"]
            if rebuild or not p.exists():
                need.append(name)
                break
    if need or not GOLDEN_GRAPH.exists():
        old = copy.deepcopy(pin)
        args = [str(AXPY), str(CAL / "build_variants.py")]
        for n in sorted(set(need) | ({"clean"} if not GOLDEN_GRAPH.exists() else set())):
            args += ["--only", n]
        subprocess.run(args, check=True)
        pin = json.loads((CAL / "variants.json").read_text())
        for name, v in pin["variants"].items():          # rebuilt outputs must carry the calibrated content
            for ds, o in v["outputs"].items():
                was = old["variants"][name]["outputs"][ds]
                same = (o["sha256"] == was["sha256"] or (o["content_sha256"] and
                                                         o["content_sha256"] == was["content_sha256"]))
                if not same and not ds.endswith("_csv"):
                    raise SystemExit(f"rebuilt {name} {ds} differs from the pinned calibration output")
    return pin


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    pin = build_if_needed(a.rebuild)
    cmp_ = load_compare()
    P = pairs()
    V = pin["variants"]
    leg = {"s1": BASES / "s1-buffers-legacy", "s1L": BASES / "s1L-buffers-legacy"}

    def run_pair(base, name, ds, tol):
        d = P[ds]
        new = REPO / V[name]["outputs"][ds]["path"]
        return cmp_.compare_pair(str(leg[base] / d["legacy"]["path"]), str(new), spec_for(d, tol))

    # 1 measure (exact)
    raw = {name: {ds: summarise(run_pair("s1", name, ds, None)) for ds in sorted(P)} for name in V}

    # 2 propose
    records, unseparable = [], []
    keys = sorted({(ds, cid) for name, v in V.items() if v["role"] in ("clean", "alt")
                   for ds in P for cid, e in raw[name][ds]["checks"].items() if e["state"] != "PASS_EXACT"})
    for ds, cid in keys:
        legit, field = [], None
        for name, v in V.items():
            if v["role"] in ("clean", "alt"):
                e = raw[name][ds]["checks"].get(cid)
                if e and e["state"] != "PASS_EXACT":
                    sp = spread_of(cid, e)
                    if sp is None:
                        unseparable.append({"dataset": ds, "check": cid, "variant": name,
                                            "why": "no tolerance can widen this check", "result": e})
                        continue
                    field = sp[0]
                    legit.append((sp[1], name))
        if not legit:
            continue
        cs, cs_var = max(legit)
        wrong = []
        for name, v in V.items():
            if v["role"] in ("mutant", "probe"):
                e = raw[name][ds]["checks"].get(cid)
                if e and e["state"] != "PASS_EXACT" and spread_of(cid, e):
                    wrong.append((spread_of(cid, e)[1], name))
        above = sorted(w for w in wrong if w[0] > cs)
        masked = sorted(w[1] for w in wrong if w[0] <= cs)
        nw, nw_var = above[0] if above else (None, None)
        thr = nice_between(cs, nw) if nw is not None else None
        if thr is None and nw is None:
            thr = nice_between(cs, cs * 1e4) if cs < 1e-9 else None   # float noise only: no mutant on this check
        rec = {"id": f"tol.{ds[3:]}.{cid}.v1", "dataset": ds, "check": cid,
               "metric": "abs+rel" if field == "rtol" else cid.split(".", 1)[1],
               field: thr, "max_mismatch": 0,
               "units": "m" if cid == "vector.hausdorff_m" else "1", "basis": "measured_spread",
               "correct_spread": {"value": cs, "variant": cs_var,
                                  "how": "max over clean + legitimate alternatives (compare.py pair, exact run)",
                                  "evidence": f"evals/calib/t0_s1.json#measure.{cs_var}.{ds}"},
               "nearest_wrong": ({"value": nw, "variant": nw_var,
                                  "how": "min over the mutants this check separates"
                                         + (f"; masked here (<= correct_spread): {', '.join(masked)}" if masked else ""),
                                  "evidence": f"evals/calib/t0_s1.json#measure.{nw_var}.{ds}"} if nw is not None else
                                 {"value": None, "how": "no mutant differs on this check"
                                  + (f" above correct_spread (masked: {', '.join(masked)})" if masked else ""),
                                  "evidence": None}),
               "agreed_by": None, "agreed_at": None, "decision_ref": None, "fixed_before": None}
        if field == "rtol":
            rec["atol"] = 0.0
        if thr is None or nw is None:
            unseparable.append({"dataset": ds, "check": cid, "correct_spread": cs, "variant": cs_var,
                                "nearest_wrong": nw, "masked_mutants": masked,
                                "why": "no mutant above the legitimate spread on this check: a two-number record "
                                       "cannot be justified, so the check stays exact (alternative will FAIL here)"
                                       if nw is None else "no round threshold strictly between the two numbers"})
            if nw is None:
                continue
        records.append(rec)
    prop_doc = {"version": 1, "records": records}
    hdr = ("# PROPOSED S1 tolerance records, measured by evals/calib/run_t0.py (T0, no model).\n"
           "# Every record is UNSIGNED (agreed_by: null). Only Najah signs, by editing agreed_by/agreed_at himself.\n"
           "# Checks not listed stay exact (default).\n")
    (CAL / "tolerances.proposed.yaml").write_text(hdr + yaml.safe_dump(prop_doc, sort_keys=False))

    signed_doc = copy.deepcopy(prop_doc)
    for r in signed_doc["records"]:
        r["agreed_by"], r["agreed_at"] = "human:t0-calibration", SIGNED_AT

    # 3 verdicts (signed in memory)
    ver = {name: {ds: summarise(run_pair("s1", name, ds, signed_doc)) for ds in sorted(P)} for name in V}
    # 4 alternatives with the records unsigned
    uns = {name: {ds: summarise(run_pair("s1", name, ds, prop_doc))["verdict"] for ds in sorted(P)}
           for name, v in V.items() if v["role"] == "alt"}
    # T5L: the s1L legacy buffers in EPSG:3857. Faithful port (= the S1-T5 code) vs silent fix (= clean golden)
    t5l = {"faithful_port(S1-T5 code)": {ds: summarise(run_pair("s1L", "S1-T5", ds, signed_doc)) for ds in sorted(P)},
           "silent_fix(golden)": {ds: summarise(run_pair("s1L", "clean", ds, signed_doc)) for ds in sorted(P)}}

    # expectations
    matrix, misses, false_alarms = {}, [], []
    for name, v in V.items():
        row = {"trap": v["trap"], "role": v["role"], "datasets": {}}
        for ds in sorted(P):
            r = ver[name][ds]
            row["datasets"][ds] = {"verdict": r["verdict"], "codes": r["codes"], "diag": r["diag"]}
        if v["role"] in ("clean", "alt"):
            bad = {ds: x for ds, x in row["datasets"].items() if x["verdict"] not in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")}
            row["ok"] = not bad
            for ds, x in bad.items():
                false_alarms.append({"variant": name, "dataset": ds, **x})
        else:
            ex = EXPECT[name]
            got = {}
            for ds in ex["datasets"]:
                x = row["datasets"][ds]
                hit = x["verdict"] == "FAIL" and any(c in x["codes"] for c in ex["codes_any"])
                named_missing = [c for c in ex["codes_any"] if c not in x["codes"]]
                got[ds] = {"caught": hit, "codes_named_but_absent": named_missing,
                           "diag_expected_present": [d for d in ex["diag_any"] if d in x["diag"]]}
                if not hit:
                    misses.append({"variant": name, "dataset": ds, **x})
            for ds in ex.get("must_pass", []):
                x = row["datasets"][ds]
                got[ds] = {"untouched_passes": x["verdict"] in ("PASS_EXACT", "PASS_WITHIN_TOLERANCE")}
            row["expect"] = ex
            row["planted"] = got
            row["ok"] = all(g.get("caught", g.get("untouched_passes")) for g in got.values())
        matrix[name] = row
    t5l_out = {k: {ds: {"verdict": x["verdict"], "codes": x["codes"], "diag": x["diag"],
                        "area_ratio": (x["checks"].get("vector.symdiff_ratio") or {}).get("area_ratio")}
                   for ds, x in v.items()} for k, v in t5l.items()}
    out = {"schema": "axis-migrate.t0.v1", "case": "S1", "comparator": cmp_.comparator_block(),
           "references": {k: str(p.relative_to(REPO)) for k, p in leg.items()},
           "run_started_at": STARTED, "measure_crs": MEASURE_CRS,
           "signing": "proposals signed in memory by human:t0-calibration for the verdict phase only; the file "
                      "evals/calib/s1/tolerances.proposed.yaml stays unsigned",
           "variants": {n: {k: v[k] for k in ("trap", "role", "patch", "patch_sha256", "run_id", "code_hash")}
                        for n, v in V.items()},
           "summary": {"mutant_rows": sum(1 for v in V.values() if v["role"] == "mutant"),
                       "mutants_caught": sum(1 for n, r in matrix.items() if r["role"] == "mutant" and r["ok"]),
                       "probe_rows": sum(1 for v in V.values() if v["role"] == "probe"),
                       "probes_caught": sum(1 for n, r in matrix.items() if r["role"] == "probe" and r["ok"]),
                       "clean_alt_rows": sum(1 for v in V.values() if v["role"] in ("clean", "alt")),
                       "clean_alt_pass": sum(1 for n, r in matrix.items() if r["role"] in ("clean", "alt") and r["ok"]),
                       "checker_misses": len(misses), "false_alarms": len(false_alarms),
                       "unseparable_checks": len(unseparable)},
           "matrix": matrix, "misses": misses, "false_alarms": false_alarms, "unseparable": unseparable,
           "alternatives_unsigned": uns, "t5l": t5l_out, "proposed_records": [r["id"] for r in records],
           "measure": raw}
    (HERE / "t0_s1.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    s = out["summary"]
    for n, r in matrix.items():
        print(f"{n:14s} {r['role']:6s} {'OK ' if r['ok'] else 'BAD'} " + " ".join(
            f"{ds[3:]}={x['verdict']}" + (f"[{','.join(x['codes'])}]" if x['codes'] else "")
            for ds, x in r["datasets"].items()))
    print(json.dumps(s))
    return 0 if s["checker_misses"] == 0 and s["false_alarms"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
