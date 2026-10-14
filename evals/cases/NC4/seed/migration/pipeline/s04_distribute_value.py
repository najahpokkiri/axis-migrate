"""s04 · canton value = population x value per capita, spread over residential cells pro rata population.

Legacy: 02_SCRIPTS/04_distribute_value.py:35-141 (openpyxl read, attribute update, gdal:rasterize of the factor,
gdal:rastercalculator A*B, native:zonalstatisticsfb check sum, csv and xlsx reports).
"""
import numpy as np
import openpyxl
from rasterio import features

STEP_ID = "s04_distribute_value"
INPUTS = ["ds.03_canton_pop", "ds.03_pop_res", "ds.value_per_capita"]
OUTPUTS = ["ds.04_canton_factor", "ds.04_factor", "ds.exposure_by_canton_csv", "ds.exposure_by_canton_gpkg",
           "ds.exposure_by_canton_xlsx", "ds.lux_res_exposure_2020"]
PARAMS = ["p.vpc_table", "p.vpc_scale", "p.vpc_join_key", "p.no_res_factor", "p.factor_raster", "p.exposure_raster",
          "p.exposure_zonal", "p.report_table", "p.gtiff_creation_options"]

_RULES = {"cell_centre": False, "all_touched": True}


def _read_vpc(ctx) -> dict:
    t = ctx.param("p.vpc_table")
    scale = float(ctx.param("p.vpc_scale"))
    wb = ctx.open_workbook("ds.value_per_capita", data_only=bool(t["data_only"]))
    ws = wb[t["sheet"]]
    vpc = {}
    for row in ws.iter_rows(min_row=t["min_row"], max_row=t["max_row"], values_only=True):
        key, val = row[t["key_col"] - 1], row[t["value_col"] - 1]
        if key is None:
            if t["skip_empty_key"]:
                continue
            raise ValueError("empty canton name in the value-per-capita table")
        if val is None:
            raise ValueError(f"no value per capita for {key!r} (formula without cached value?)")
        name = key.strip() if t["strip_keys"] else key
        if name in vpc:
            raise ValueError(f"canton {name!r} appears twice in the value-per-capita table")
        vpc[name] = float(val) * scale
    return vpc


def _zonal_sum(values, zones, grid, all_touched):
    data = np.ma.getdata(values).astype("float64")
    valid = ~np.ma.getmaskarray(values)
    out = []
    for geom in zones.geometry:
        inside = features.geometry_mask([geom], out_shape=grid.shape, transform=grid.transform,
                                        all_touched=all_touched, invert=True)
        v = data[inside & valid]
        out.append(float(np.cumsum(v)[-1]) if v.size else None)
    return out


def run(ctx) -> None:
    vpc = _read_vpc(ctx)
    ctx.log.info("value per capita (EUR): %s", vpc)

    # ---- per canton factor (copy of 03 with three new fields)
    key = ctx.param("p.vpc_join_key")
    cf = ctx.read_vector("ds.03_canton_pop")
    names = [str(n) for n in cf[key["vector_field"]]]
    missing = sorted(set(names) - set(vpc))
    if missing:
        raise ValueError(f"cantons without a value per capita (D-002): {missing}")
    no_res = float(ctx.param("p.no_res_factor"))
    vpc_eur, tot, fac = [], [], []
    for name, pop_sum, res_sum in zip(names, cf["pop_sum"], cf["res_sum"]):
        t = float(pop_sum) * vpc[name]
        f = t / float(res_sum) if (res_sum is not None and not np.isnan(res_sum) and res_sum > 0) else no_res
        vpc_eur.append(vpc[name])
        tot.append(t)
        fac.append(f)
    cf["vpc_eur"] = np.array(vpc_eur, dtype="float64")
    cf["tot_value"] = np.array(tot, dtype="float64")
    cf["factor"] = np.array(fac, dtype="float64")
    ctx.write_vector("ds.04_canton_factor", cf)

    # ---- factor raster on the pop_res grid
    grid = ctx.grid_of("ds.03_pop_res")
    fr = ctx.param("p.factor_raster")
    zones = cf if cf.crs.equals(grid.crs) else cf.to_crs(grid.crs)
    factor = features.rasterize(((g, v) for g, v in zip(zones.geometry, zones[fr["field"]])), out_shape=grid.shape,
                                transform=grid.transform, fill=fr["init"], all_touched=bool(fr["all_touched"]),
                                dtype=fr["dtype"])
    co = ctx.param("p.gtiff_creation_options")
    ctx.write_raster("ds.04_factor", factor, grid=grid, dtype=fr["dtype"], nodata=fr["nodata"], **co)

    # ---- exposure = pop_res * factor; NoData where any input is NoData (gdal_calc)
    ex = ctx.param("p.exposure_raster")
    if ex["formula"] != "A*B" or ex["nodata_rule"] != "nodata_if_any_input_nodata":
        raise NotImplementedError(f"exposure formula {ex['formula']!r} / {ex['nodata_rule']!r} is not ported")
    pop_res, _ = ctx.read_raster("ds.03_pop_res")
    a = np.ma.getdata(pop_res)
    fnod = factor == np.asarray(fr["nodata"], dtype=fr["dtype"])
    nod = np.ma.getmaskarray(pop_res) | fnod
    expo = np.ma.masked_array((a * factor).astype(ex["dtype"]), mask=nod)
    ctx.log.info("exposure valid cells %d, sum %.3f EUR", int((~nod).sum()), float(expo.sum(dtype="float64")))
    ctx.write_raster("ds.lux_res_exposure_2020", expo, grid=grid, dtype=ex["dtype"], nodata=ex["nodata"], **co)

    # ---- check sum per canton (zonal statistics on the written exposure)
    ez = ctx.param("p.exposure_zonal")
    if list(ez["stats"]) != ["sum"]:
        raise NotImplementedError(f"exposure zonal statistics {ez['stats']!r} not ported")
    out = cf.copy()
    out[f"{ez['prefix']}sum"] = np.array(_zonal_sum(expo, zones, grid, _RULES[ez["rule"]]), dtype="float64")
    ctx.write_vector("ds.exposure_by_canton_gpkg", out)

    # ---- csv and xlsx reports
    rep = ctx.param("p.report_table")
    exp_col = f"{ez['prefix']}sum"
    rows = [[str(r[key["vector_field"]]), float(r["pop_sum"]), float(r["res_sum"]), float(r["vpc_eur"]),
             float(r["tot_value"]), float(r[exp_col]), float(r[exp_col]) - float(r["tot_value"])]
            for _, r in out.iterrows()]
    if rep["sort_by"] != rep["header"][0]:
        raise NotImplementedError(f"sorting by {rep['sort_by']!r} is not ported")
    rows.sort(key=lambda r: r[0])
    hdr = list(rep["header"])
    ctx.write_csv("ds.exposure_by_canton_csv", hdr, rows)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = rep["xlsx_sheet"]
    ws.append(hdr)
    for r in rows:
        ws.append(r)
    if rep["xlsx_blank_row_before_total"]:
        ws.append([])
    ws.append([rep["xlsx_total_label"], sum(r[1] for r in rows), sum(r[2] for r in rows), None,
               sum(r[4] for r in rows), sum(r[5] for r in rows), None])
    ctx.save_workbook("ds.exposure_by_canton_xlsx", wb)
