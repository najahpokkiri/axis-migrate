"""s02: residential polygons (valid, planar area in the area CRS >= minimum) and their all-touched mask."""
import numpy as np
from rasterio.features import rasterize
import shapely

STEP_ID = "s02_landuse_mask"
INPUTS = ["ds.osm_landuse_lux", "ds.01_pop_lux"]
OUTPUTS = ["ds.02_res_2169", "ds.02_res_4326", "ds.02_res_mask"]
PARAMS = ["p.area_crs", "p.landuse_class", "p.min_area_m2", "p.rasterize_all_touched", "p.mask_nodata",
          "p.mask_burn_value"]


def run(ctx):
    lu = ctx.read_vector("ds.osm_landuse_lux")
    lu = lu.to_crs(ctx.param("p.area_crs"))
    # repair like QGIS "Fix geometries" (structure method): keep polygonal parts only
    lu["geometry"] = shapely.make_valid(lu.geometry.values, method="structure", keep_collapsed=False)
    lu["area_m2"] = lu.geometry.area      # planar, in the area CRS
    keep = (lu["landuse"] == ctx.param("p.landuse_class")) & (lu["area_m2"] >= ctx.param("p.min_area_m2"))
    res = lu.loc[keep, ["osm_id", "osm_way_id", "landuse", "name", "area_m2", "geometry"]].copy()
    res["geometry"] = res.geometry.map(
        lambda g: g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]))
    res = res.reset_index(drop=True)
    ctx.write_vector("ds.02_res_2169", res)
    pop_grid = ctx.grid_of("ds.01_pop_lux")
    res4326 = res.to_crs(pop_grid.crs)
    ctx.write_vector("ds.02_res_4326", res4326)

    burn = ctx.param("p.mask_burn_value")
    nodata = ctx.param("p.mask_nodata")
    mask = rasterize(((g, burn) for g in res4326.geometry), out_shape=pop_grid.shape, transform=pop_grid.transform,
                     fill=nodata, all_touched=ctx.param("p.rasterize_all_touched"), dtype="uint8")
    ctx.write_raster("ds.02_res_mask", mask, grid=pop_grid, dtype="uint8", nodata=nodata, compress="lzw")
