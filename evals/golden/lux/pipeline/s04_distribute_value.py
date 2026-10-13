"""s04: canton value = population x value per capita, spread over residential cells pro rata population."""
import numpy as np
from rasterio.features import rasterize

STEP_ID = "s04_distribute_value"
INPUTS = ["ds.value_per_capita", "ds.03_canton_pop", "ds.03_pop_res"]
OUTPUTS = ["ds.04_canton_factor", "ds.04_factor", "ds.lux_res_exposure_2020", "ds.exposure_by_canton_gpkg",
           "ds.exposure_by_canton_csv", "ds.exposure_by_canton_xlsx"]
PARAMS = ["p.vpc_sheet", "p.vpc_first_row", "p.vpc_last_row", "p.vpc_unit_factor", "p.factor_nodata",
          "p.exposure_nodata", "p.report_sheet"]


def read_vpc(ctx) -> dict:
    wb = ctx.open_workbook("ds.value_per_capita", data_only=True)
    ws = wb[ctx.param("p.vpc_sheet")]
    unit = ctx.param("p.vpc_unit_factor")
    out = {}
    for name, value, *_ in ws.iter_rows(min_row=ctx.param("p.vpc_first_row"), max_row=ctx.param("p.vpc_last_row"),
                                        values_only=True):
        if name is None:
            continue
        out[str(name).strip()] = float(value) * unit
    return out


def zone_sums(zones: np.ndarray, values: np.ma.MaskedArray, n: int) -> np.ndarray:
    valid = ~np.ma.getmaskarray(values) & (zones > 0)
    return np.bincount(zones[valid], weights=np.ma.getdata(values)[valid].astype("float64"), minlength=n + 1)[1:]


def run(ctx):
    vpc = read_vpc(ctx)
    cf = ctx.read_vector("ds.03_canton_pop")
    missing = sorted(set(cf["canton"]) - set(vpc))
    if missing:
        raise ValueError(f"no value per capita for cantons {missing}")
    cf["vpc_eur"] = cf["canton"].map(vpc).astype("float64")
    cf["tot_value"] = cf["pop_sum"] * cf["vpc_eur"]
    cf["factor"] = [t / r if r and r > 0 else 0.0 for t, r in zip(cf["tot_value"], cf["res_sum"])]
    ctx.write_vector("ds.04_canton_factor", cf)

    pop_res, _ = ctx.read_raster("ds.03_pop_res")
    grid = ctx.grid_of("ds.03_pop_res")
    fnd = ctx.param("p.factor_nodata")
    factor = rasterize(((g, f) for g, f in zip(cf.to_crs(grid.crs).geometry, cf["factor"])), out_shape=grid.shape,
                       transform=grid.transform, fill=fnd, dtype="float64")
    ctx.write_raster("ds.04_factor", factor, grid=grid, dtype="float64", nodata=fnd, compress="lzw")

    end = ctx.param("p.exposure_nodata")
    valid = ~np.ma.getmaskarray(pop_res) & (factor != fnd)
    expo = np.where(valid, np.ma.getdata(pop_res).astype("float64") * factor, end).astype("float32")
    ctx.write_raster("ds.lux_res_exposure_2020", expo, grid=grid, dtype="float32", nodata=end, compress="lzw")

    zones = rasterize(((g, i + 1) for i, g in enumerate(cf.to_crs(grid.crs).geometry)), out_shape=grid.shape,
                      transform=grid.transform, fill=0, dtype="int32")
    out = cf.copy()
    out["exp_sum"] = zone_sums(zones, np.ma.masked_equal(expo, np.float32(end)), len(cf))
    ctx.write_vector("ds.exposure_by_canton_gpkg", out)

    rows = sorted([[r.canton, r.pop_sum, r.res_sum, r.vpc_eur, r.tot_value, r.exp_sum, r.exp_sum - r.tot_value]
                   for r in out.itertuples()], key=lambda r: r[0])
    header = ["canton", "pop_total", "pop_residential", "value_per_capita_eur", "total_value_eur",
              "exposure_raster_sum_eur", "diff_eur"]
    ctx.write_csv("ds.exposure_by_canton_csv", header, [[r[0]] + [float(x) for x in r[1:]] for r in rows])

    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = ctx.param("p.report_sheet")
    ws.append(header)
    for r in rows:
        ws.append([r[0]] + [float(x) for x in r[1:]])
    ws.append([])
    ws.append(["TOTAL", sum(r[1] for r in rows), sum(r[2] for r in rows), None, sum(r[4] for r in rows),
               sum(r[5] for r in rows), None])
    ctx.save_workbook("ds.exposure_by_canton_xlsx", wb)
