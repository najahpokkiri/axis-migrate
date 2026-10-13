"""s03: residential population weights and per-canton zonal counts and sums (cell-centre rule)."""
import numpy as np
from rasterio.features import rasterize

STEP_ID = "s03_zonal_population"
INPUTS = ["ds.cantons_lux_edit", "ds.01_pop_lux", "ds.02_res_mask"]
OUTPUTS = ["ds.03_pop_res", "ds.03_canton_pop"]
PARAMS = ["p.pop_min", "p.pop_res_nodata"]


def zonal(zone_ids: np.ndarray, values: np.ma.MaskedArray, n: int):
    valid = ~np.ma.getmaskarray(values) & (zone_ids > 0)
    z = zone_ids[valid]
    v = np.ma.getdata(values)[valid].astype("float64")
    counts = np.bincount(z, minlength=n + 1)[1:].astype("float64")
    sums = np.bincount(z, weights=v, minlength=n + 1)[1:]
    return counts, sums


def run(ctx):
    pop, _ = ctx.read_raster("ds.01_pop_lux")
    mask, _ = ctx.read_raster("ds.02_res_mask")
    grid = ctx.grid_of("ds.01_pop_lux")
    if not grid.same_as(ctx.grid_of("ds.02_res_mask")):
        raise ValueError("population and mask are not on the same grid")
    pop_min = ctx.param("p.pop_min")
    nodata = ctx.param("p.pop_res_nodata")
    valid = ~np.ma.getmaskarray(pop) & ~np.ma.getmaskarray(mask)
    p = np.ma.getdata(pop).astype("float64")
    w = (p >= pop_min) * p * np.ma.getdata(mask).astype("float64")
    pop_res = np.where(valid, w, nodata).astype("float32")
    ctx.write_raster("ds.03_pop_res", pop_res, grid=grid, dtype="float32", nodata=nodata)

    cantons = ctx.read_vector("ds.cantons_lux_edit")
    zones = rasterize(((g, i + 1) for i, g in enumerate(cantons.to_crs(grid.crs).geometry)), out_shape=grid.shape,
                      transform=grid.transform, fill=0, dtype="int32")
    n = len(cantons)
    cantons["pop_count"], cantons["pop_sum"] = zonal(zones, pop, n)
    pr = np.ma.masked_equal(pop_res, np.float32(nodata))
    cantons["res_count"], cantons["res_sum"] = zonal(zones, pr, n)
    ctx.write_vector("ds.03_canton_pop", cantons)
