"""s02 · residential mask from OSM landuse on the population grid.

Legacy: 02_SCRIPTS/02_landuse_mask.py:27-76 (reproject to LUREF, fix geometries (structure), planar area, keep
residential >= MIN_AREA, back to WGS84, gdal_rasterize -at burn 1 on the 01_pop_lux grid).
"""
import geopandas as gpd
import numpy as np
import shapely
from rasterio import features

STEP_ID = "s02_landuse_mask"
INPUTS = ["ds.01_pop_lux", "ds.osm_landuse_lux"]
OUTPUTS = ["ds.02_res_2169", "ds.02_res_4326", "ds.02_res_mask"]
PARAMS = ["p.landuse_area_crs", "p.landuse_fix_method", "p.landuse_area_method", "p.landuse_area_field",
          "p.landuse_class_filter", "p.res_min_area_m2", "p.res_output_crs", "p.res_mask_all_touched",
          "p.res_mask_burn", "p.res_mask_nodata", "p.res_mask_dtype", "p.gtiff_creation_options"]

_OPS = {">=": np.greater_equal, ">": np.greater}
_POLY = ("Polygon", "MultiPolygon")


def _polygonal(g):
    """Keep the polygon parts of a repaired geometry (fix geometries keeps the layer's polygon type)."""
    if g is None or g.is_empty:
        return None
    if g.geom_type in _POLY:
        return g
    parts = [p for p in shapely.get_parts(g) if p.geom_type in _POLY]
    if not parts:
        return None
    return shapely.union_all(parts) if len(parts) > 1 else parts[0]


def run(ctx) -> None:
    lu = ctx.read_vector("ds.osm_landuse_lux")
    src_crs = lu.crs
    lu = lu.to_crs(ctx.param("p.landuse_area_crs"))

    # fix geometries (QGIS native:fixgeometries METHOD 1 = structure, collapsed parts dropped)
    method = ctx.param("p.landuse_fix_method")
    n_invalid = int((~lu.geometry.is_valid).sum())
    fixed = shapely.make_valid(lu.geometry.values, method=method, keep_collapsed=False)
    lu = lu.set_geometry(gpd.GeoSeries([_polygonal(g) for g in fixed], index=lu.index, crs=lu.crs))
    dropped = int(lu.geometry.isna().sum())
    ctx.log.info("fix geometries (%s): %d invalid repaired, %d features without polygon part dropped",
                 method, n_invalid, dropped)
    lu = lu[lu.geometry.notna()].copy()

    # area in the layer CRS
    if ctx.param("p.landuse_area_method") != "planar":
        raise NotImplementedError("only planar area in the layer CRS is ported (area($geometry) without ellipsoid)")
    area_field = ctx.param("p.landuse_area_field")
    lu[area_field] = lu.geometry.area.astype("float64")

    # residential AND area >= MIN_AREA
    flt = ctx.param("p.landuse_class_filter")
    thr = ctx.param("p.res_min_area_m2")
    keep = lu[flt["field"]].isin(list(flt["keep"])).to_numpy() & \
        _OPS[thr["operator"]](lu[area_field].to_numpy(), float(thr["value"]))
    res = lu[keep].copy()
    ctx.log.info("residential kept: %d of %d (class %s, %s %s m2)", len(res), len(lu), flt["keep"],
                 thr["operator"], thr["value"])
    ctx.write_vector("ds.02_res_2169", res)

    res4326 = res.to_crs(ctx.param("p.res_output_crs"))
    ctx.write_vector("ds.02_res_4326", res4326)

    # rasterize on the population grid
    grid = ctx.grid_of("ds.01_pop_lux")
    if not res4326.crs.equals(grid.crs):
        raise ValueError(f"residential layer CRS {res4326.crs} differs from the population grid CRS {grid.crs}")
    burn = ctx.param("p.res_mask_burn")
    dtype = ctx.param("p.res_mask_dtype")
    mask = features.rasterize(((g, burn["burn"]) for g in res4326.geometry), out_shape=grid.shape,
                              transform=grid.transform, fill=burn["init"],
                              all_touched=bool(ctx.param("p.res_mask_all_touched")), dtype=dtype)
    ctx.log.info("mask cells burned: %d", int((mask == burn["burn"]).sum()))
    ctx.write_raster("ds.02_res_mask", mask, grid=grid, dtype=dtype, nodata=ctx.param("p.res_mask_nodata"),
                     **ctx.param("p.gtiff_creation_options"))
    ctx.log.info("source CRS %s", src_crs)
