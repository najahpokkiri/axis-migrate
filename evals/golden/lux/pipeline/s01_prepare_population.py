"""s01: country outline (dissolved cantons) and population clipped to it on the WorldPop grid.

The clip keeps the source grid (no crop, no resampling, no target-aligned snapping): cells whose centre lies outside
the outline become nodata, every other cell is copied 1:1.
"""
import numpy as np
import shapely
from rasterio.features import geometry_mask

STEP_ID = "s01_prepare_population"
INPUTS = ["ds.lux_ppp_2020", "ds.cantons_lux_edit"]
OUTPUTS = ["ds.01_lux_outline", "ds.01_pop_lux"]
PARAMS = ["p.clip_nodata"]


def run(ctx):
    cantons = ctx.read_vector("ds.cantons_lux_edit")
    outline = cantons.iloc[[0]].copy()          # dissolve keeps the first feature's attributes
    outline = outline.set_geometry([cantons.geometry.union_all()], crs=cantons.crs)
    outline["geometry"] = outline.geometry.map(
        lambda g: g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]))
    ctx.write_vector("ds.01_lux_outline", outline)

    pop, _ = ctx.read_raster("ds.lux_ppp_2020", masked=False)
    grid = ctx.grid_of("ds.lux_ppp_2020")
    inside = geometry_mask(list(outline.to_crs(grid.crs).geometry), out_shape=grid.shape,
                           transform=grid.transform, invert=True)          # cell-centre rule
    nodata = ctx.param("p.clip_nodata")
    out = np.where(inside, pop, np.float32(nodata)).astype("float32")
    ctx.write_raster("ds.01_pop_lux", out, grid=grid, dtype="float32", nodata=nodata, compress="lzw")
