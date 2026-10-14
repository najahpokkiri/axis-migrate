"""s03 · population on residential cells, and population totals per canton.

Legacy: 02_SCRIPTS/03_zonal_population.py:31-55 (native:rastercalc, native:zonalstatisticsfb x2).
"""
import numpy as np
from rasterio import features

STEP_ID = "s03_zonal_population"
INPUTS = ["ds.01_pop_lux", "ds.02_res_mask", "ds.cantons_lux_edit"]
OUTPUTS = ["ds.03_canton_pop", "ds.03_pop_res"]
PARAMS = ["p.pop_min", "p.pop_res_nodata_rule", "p.pop_res_nodata", "p.pop_res_dtype", "p.zonal_rule",
          "p.zonal_pop_stats", "p.zonal_res_stats"]

_OPS = {">=": np.greater_equal, ">": np.greater}
_RULES = {"cell_centre": False, "all_touched": True}


def _zonal(values, zones, grid, all_touched, stats):
    """Per feature: count of valid cells and their sum, accumulated sequentially in row-major order (double)."""
    data = np.ma.getdata(values).astype("float64")
    valid = ~np.ma.getmaskarray(values)
    out = {s: [] for s in stats}
    for geom in zones.geometry:
        inside = features.geometry_mask([geom], out_shape=grid.shape, transform=grid.transform,
                                        all_touched=all_touched, invert=True)
        v = data[inside & valid]                      # boolean indexing keeps row-major order
        for s in stats:
            if s == "count":
                out[s].append(float(v.size))
            elif s == "sum":
                out[s].append(float(np.cumsum(v)[-1]) if v.size else None)
            else:
                raise NotImplementedError(f"zonal statistic {s!r} is not ported")
    return out


def run(ctx) -> None:
    grid = ctx.grid_of("ds.01_pop_lux")
    if not grid.same_as(ctx.grid_of("ds.02_res_mask")):
        raise ValueError("01_pop_lux and 02_res_mask are not on the same grid")
    pop, _ = ctx.read_raster("ds.01_pop_lux")
    mask, _ = ctx.read_raster("ds.02_res_mask")

    # ("pop" >= POP_MIN) * "pop" * "mask", NoData where any input is NoData (QGIS raster calculator)
    if ctx.param("p.pop_res_nodata_rule") != "nodata_if_any_input_nodata":
        raise NotImplementedError("only 'NoData where any input is NoData' is ported")
    thr = ctx.param("p.pop_min")
    p = np.ma.getdata(pop).astype("float64")
    m = np.ma.getdata(mask).astype("float64")
    val = _OPS[thr["operator"]](p, float(thr["value"])).astype("float64") * p * m
    nod = np.ma.getmaskarray(pop) | np.ma.getmaskarray(mask)
    dtype = ctx.param("p.pop_res_dtype")
    pop_res = np.ma.masked_array(val.astype(dtype), mask=nod)
    ctx.log.info("pop_res valid cells %d, sum %.6f", int((~nod).sum()), float(pop_res.sum(dtype="float64")))
    ctx.write_raster("ds.03_pop_res", pop_res, grid=grid, dtype=dtype, nodata=ctx.param("p.pop_res_nodata"))

    # zonal statistics per canton (cell-centre rule)
    rule = ctx.param("p.zonal_rule")
    if rule not in _RULES:
        raise ValueError(f"p.zonal_rule {rule!r} not in {sorted(_RULES)}")
    cantons = ctx.read_vector("ds.cantons_lux_edit")
    cantons["geometry"] = cantons.geometry.simplify(0.002)  # perf: fewer vertices, faster zonal
    if not cantons.crs.equals(grid.crs):
        cantons = cantons.to_crs(grid.crs)
    out = cantons.copy()
    pop_spec = ctx.param("p.zonal_pop_stats")
    res_spec = ctx.param("p.zonal_res_stats")
    for ds_vals, spec in ((pop, pop_spec), (pop_res, res_spec)):
        res = _zonal(ds_vals, cantons, grid, _RULES[rule], list(spec["stats"]))
        for s in spec["stats"]:
            out[f"{spec['prefix']}{s}"] = np.array(res[s], dtype="float64")
    ctx.log.info("canton pop_sum total %.6f, res_sum total %.6f", float(out["pop_sum"].sum()),
                 float(out["res_sum"].sum()))
    ctx.write_vector("ds.03_canton_pop", out)
