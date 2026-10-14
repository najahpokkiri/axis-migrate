"""s01 · country outline from the cantons, and WorldPop masked to it on the unchanged WorldPop grid.

Legacy: 02_SCRIPTS/01_prepare_population.py:31-54 (native:dissolve, gdal:cliprasterbymasklayer without crop/keep-res).
"""
import geopandas as gpd
import numpy as np
import shapely
from rasterio import features

STEP_ID = "s01_prepare_population"
INPUTS = ["ds.cantons_lux_edit", "ds.lux_ppp_2020"]
OUTPUTS = ["ds.01_lux_outline", "ds.01_pop_lux"]
PARAMS = ["p.outline_dissolve", "p.pop_clip_grid", "p.pop_clip_rule", "p.pop_lux_nodata", "p.pop_lux_dtype",
          "p.gtiff_creation_options"]

_RULES = {"cell_centre": False, "all_touched": True}


def run(ctx) -> None:
    # ---- outline: dissolve everything into one feature, attributes of the first feature (native:dissolve)
    dis = ctx.param("p.outline_dissolve")
    if list(dis["fields"]) or dis["separate_disjoint"] or dis["attributes"] != "first_feature":
        raise NotImplementedError(f"outline dissolve options {dis!r}: only a single dissolve of all features is ported")
    cantons = ctx.read_vector("ds.cantons_lux_edit")
    geom = shapely.union_all(cantons.geometry.values)
    attrs = cantons.drop(columns=cantons.geometry.name).iloc[[0]].reset_index(drop=True)
    outline = gpd.GeoDataFrame(attrs, geometry=[geom], crs=cantons.crs)
    ctx.log.info("outline: %d cantons -> 1 feature (%s)", len(cantons), geom.geom_type)
    ctx.write_vector("ds.01_lux_outline", outline)

    # ---- population masked by the outline, on the source grid (no crop, no resample)
    if ctx.param("p.pop_clip_grid") != "source_grid":
        raise NotImplementedError("only the unchanged source grid is ported (CROP_TO_CUTLINE/KEEP_RESOLUTION off)")
    rule = ctx.param("p.pop_clip_rule")
    if rule not in _RULES:
        raise ValueError(f"p.pop_clip_rule {rule!r} not in {sorted(_RULES)}")
    grid = ctx.grid_of("ds.lux_ppp_2020")
    pop, _profile = ctx.read_raster("ds.lux_ppp_2020")
    if not outline.crs.equals(grid.crs):
        outline = outline.to_crs(grid.crs)
    outside = features.geometry_mask(outline.geometry, out_shape=grid.shape, transform=grid.transform,
                                     all_touched=_RULES[rule], invert=False)
    out = np.ma.masked_array(np.ma.getdata(pop), mask=np.ma.getmaskarray(pop) | outside)
    ctx.log.info("pop cells kept: %d (source valid %d); sum %.4f", int((~out.mask).sum()),
                 int((~np.ma.getmaskarray(pop)).sum()), float(out.sum(dtype="float64")))
    ctx.write_raster("ds.01_pop_lux", out, grid=grid, dtype=ctx.param("p.pop_lux_dtype"),
                     nodata=ctx.param("p.pop_lux_nodata"), **ctx.param("p.gtiff_creation_options"))
