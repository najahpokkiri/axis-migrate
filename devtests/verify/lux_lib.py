"""Lux devtest helpers: fixture copies, the lux graph (CONTRACTS §2 ids), run-dir building, specs for pair mode.

Encodes fixture knowledge (layers, keys, zone rule, xlsx layout) — allowed in devtests only, never in the plugin.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

os.environ["GDAL_PAM_ENABLED"] = "NO"

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "axis-migrate"
VERIFY = PLUGIN / "skills" / "verify"
FIXTURE = REPO / "fixtures" / "lux-exposure-legacy"
PROVENANCE = REPO / "fixtures" / "lux-exposure-legacy.PROVENANCE.md"
HERE = Path(__file__).resolve().parent
for p in (str(VERIFY), str(VERIFY / "tests"), str(PLUGIN / "lib"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import _runbuilder as rb  # noqa: E402
import compare  # noqa: E402

LEGACY_NAME = "lux-exposure-legacy"
MEASURE_CRS = "EPSG:2169"
ZONES = "ds.cantons_lux_edit:canton"

# dataset id -> (kind, role, legacy relpath, layer, producing step, compare block)
DATASETS = {
    "ds.lux_ppp_2020": ("raster", "input", "03_INPUT/lux_ppp_2020.tif", None, None, {}),
    "ds.cantons_lux_edit": ("vector", "input", "03_INPUT/cantons_lux_edit.gpkg", "cantons", None, {}),
    "ds.osm_landuse_lux": ("vector", "input", "03_INPUT/osm_landuse_lux.gpkg", "landuse", None, {}),
    "ds.value_per_capita": ("table", "input", "03_INPUT/value_per_capita.xlsx", None, None, {}),
    "ds.geoboundaries_lux_adm1": ("vector", "input", "03_INPUT/geoBoundaries-LUX-ADM1.geojson", None, None, {}),
    "ds.01_lux_outline": ("vector", "intermediate", "05_TEMP/01_lux_outline.gpkg", "01_lux_outline",
                          "s01_prepare_population", {"key": ["shapeISO"], "measure_crs_param": "p.measure_crs"}),
    "ds.01_pop_lux": ("raster", "intermediate", "05_TEMP/01_pop_lux.tif", None, "s01_prepare_population",
                      {"measure": "extensive", "zones": ZONES, "zone_rule": "centre"}),
    "ds.02_res_2169": ("vector", "intermediate", "05_TEMP/02_res_2169.gpkg", "02_res_2169", "s02_landuse_mask",
                       {"key": ["osm_id", "osm_way_id"], "measure_crs_param": "p.measure_crs"}),
    "ds.02_res_4326": ("vector", "intermediate", "05_TEMP/02_res_4326.gpkg", "02_res_4326", "s02_landuse_mask",
                       {"key": ["osm_id", "osm_way_id"], "measure_crs_param": "p.measure_crs"}),
    "ds.02_res_mask": ("raster", "intermediate", "05_TEMP/02_res_mask.tif", None, "s02_landuse_mask",
                       {"measure": "mask", "categorical": True}),
    "ds.03_pop_res": ("raster", "intermediate", "05_TEMP/03_pop_res.tif", None, "s03_zonal_population",
                      {"measure": "extensive", "zones": ZONES, "zone_rule": "centre"}),
    "ds.03_canton_pop": ("vector", "intermediate", "05_TEMP/03_canton_pop.gpkg", "03_canton_pop",
                         "s03_zonal_population", {"key": ["canton"], "measure_crs_param": "p.measure_crs"}),
    "ds.04_canton_factor": ("vector", "intermediate", "05_TEMP/04_canton_factor.gpkg", "canton_factor",
                            "s04_distribute_value", {"key": ["canton"], "measure_crs_param": "p.measure_crs"}),
    "ds.04_factor": ("raster", "intermediate", "05_TEMP/04_factor.tif", None, "s04_distribute_value",
                     {"measure": "intensive"}),
    "ds.lux_res_exposure_2020": ("raster", "final", "04_FINAL/lux_res_exposure_2020.tif", None, "s04_distribute_value",
                                 {"measure": "extensive", "zones": ZONES, "zone_rule": "centre"}),
    "ds.exposure_by_canton_gpkg": ("vector", "final", "04_FINAL/exposure_by_canton.gpkg", "exposure_by_canton",
                                   "s04_distribute_value", {"key": ["canton"], "measure_crs_param": "p.measure_crs"}),
    "ds.exposure_by_canton_csv": ("table", "final", "04_FINAL/exposure_by_canton.csv", None, "s04_distribute_value",
                                  {"key": ["canton"]}),
    "ds.exposure_by_canton_xlsx": ("table", "final", "04_FINAL/exposure_by_canton.xlsx", None, "s04_distribute_value",
                                   {"key": ["canton"], "sheet": "by_canton", "header_row": 1, "data_rows": [2, 13],
                                    "total_row": 15}),
}
PAIRED = [d for d, v in DATASETS.items() if v[1] != "input"]
READS = [("s01_prepare_population", "ds.lux_ppp_2020"), ("s01_prepare_population", "ds.cantons_lux_edit"),
         ("s02_landuse_mask", "ds.osm_landuse_lux"), ("s02_landuse_mask", "ds.01_pop_lux"),
         ("s03_zonal_population", "ds.01_pop_lux"), ("s03_zonal_population", "ds.02_res_mask"),
         ("s03_zonal_population", "ds.cantons_lux_edit"), ("s04_distribute_value", "ds.value_per_capita"),
         ("s04_distribute_value", "ds.03_canton_pop"), ("s04_distribute_value", "ds.03_pop_res")]
STEP_OF = {d: v[4] for d, v in DATASETS.items() if v[4]}
STEPS = ["s01_prepare_population", "s02_landuse_mask", "s03_zonal_population", "s04_distribute_value"]

PARAMS = ("version: 1\nparams:\n"
          "- {id: p.measure_crs, node: s02_landuse_mask, kind: crs, value: 'EPSG:2169', source: code}\n"
          "- {id: p.mask_nodata, node: s02_landuse_mask, kind: nodata, value: 0, source: reference_metadata,\n"
          "   impact: {datasets: [ds.02_res_mask]}}\n")


def copy_fixture(dst_root: Path) -> Path:
    leg = Path(dst_root) / LEGACY_NAME
    shutil.copytree(FIXTURE, leg)
    return leg


def lux_graph(*, drop_new=(), invariants=()):
    steps = [rb.step(s) for s in STEPS] + [rb.step("s03b_zonal_arcpy_old", scope="drop"),
                                           rb.step("m01_cantons_lux_edit", scope="keep_legacy", kind="manual")]
    dsets = []
    for did, (kind, role, path, layer, prod, cmp_) in DATASETS.items():
        d = rb.dataset(did, kind, role, path, layer=layer, produced_by=prod, compare=dict(cmp_),
                       sheet=cmp_.get("sheet"))
        if did in drop_new:
            d["new"] = None
        dsets.append(d)
    g = rb.graph(steps, dsets, reads=READS, invariants=invariants, legacy_output_dirs=["04_FINAL", "05_TEMP"],
                 legacy_name=LEGACY_NAME)
    return g


def outputs_from(dir_: Path, which=None) -> dict:
    """{ds: path} for an output tree laid out <step>/<legacy basename> (independent implementation or copies)."""
    out = {}
    for did in (which or PAIRED):
        _, _, path, _, prod, _ = DATASETS[did]
        p = Path(dir_) / prod / Path(path).name
        if p.exists():
            out[did] = p
    return out


def legacy_copies(legacy_root: Path, which=None) -> dict:
    return {did: Path(legacy_root) / DATASETS[did][2] for did in (which or PAIRED)}


def build_run(root: Path, outputs: dict, *, tolerances=None, kd=None, chain="chained", step_status=None,
              run_id="r20261016T100000Z-luxlux01", drop_new=(), invariants=(), started_at=rb.STARTED_AT) -> Path:
    g = lux_graph(drop_new=drop_new, invariants=invariants)
    return rb.build(Path(root), g, outputs=outputs, tolerances=tolerances, kd=kd, params=PARAMS, run_id=run_id,
                    chain=chain, step_status=step_status, legacy_name=LEGACY_NAME, started_at=started_at)


def pair_spec(did: str, *, tolerances=None, kds=None, legacy_root: Path = FIXTURE) -> dict:
    kind, role, path, layer, prod, cmp_ = DATASETS[did]
    s = {"dataset_id": did, "kind": kind, "role": role, "layer": layer, "compare": dict(cmp_),
         "sheet": cmp_.get("sheet"), "measure_crs": MEASURE_CRS, "run_started_at": rb.STARTED_AT,
         "tolerances": tolerances, "known_differences": kds or []}
    if cmp_.get("zones"):
        s["zones"] = {"path": str(Path(legacy_root) / "03_INPUT/cantons_lux_edit.gpkg"), "layer": "cantons",
                      "key": "canton"}
    return s


def signed(rec: dict, who="human:devtest-fixture", at="2026-10-15T09:00:00Z") -> dict:
    """A record signed by a TEST identity, inside a tmp project only (never written to a real migration)."""
    r = dict(rec)
    r["agreed_by"], r["agreed_at"] = who, at
    return r


INVARIANT_ID = "inv.canton_sums"
INVARIANT_TOL = "tol.lux_res_exposure_2020.invariant.inv.canton_sums.v1"


def canton_sums_invariant(*, tolerance_ref=INVARIANT_TOL) -> dict:
    """NOTES.txt: 'sums per canton should come back to the canton total' (CONTRACTS §4 invariant shape)."""
    return {"id": INVARIANT_ID, "kind": "zone_sum_equals", "lhs": "ds.lux_res_exposure_2020",
            "rhs": "ds.04_canton_factor:tot_value", "rule": "centre", "tolerance_ref": tolerance_ref}


def invariant_pair_spec(new_tree: Path, *, tolerances=None, legacy_root: Path = FIXTURE) -> dict:
    s = pair_spec("ds.lux_res_exposure_2020", tolerances=tolerances, legacy_root=legacy_root)
    inv = canton_sums_invariant()
    inv["resolved"] = {"rhs": {"legacy": {"path": str(Path(legacy_root) / "05_TEMP/04_canton_factor.gpkg"),
                                          "layer": "canton_factor"},
                               "new": {"path": str(Path(new_tree) / "s04_distribute_value/04_canton_factor.gpkg"),
                                       "layer": "canton_factor"}}}
    s["invariants"] = [inv]
    return s
