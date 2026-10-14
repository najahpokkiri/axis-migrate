"""s02: buffer zones per landuse class: industrial polygons at the industrial distance, every other class at the
default distance (legacy: two extract+buffer runs, then merge, industrial first). Round caps and joins, five segments
per quarter circle, one output feature per input feature."""
import geopandas as gpd
import pandas as pd
import shapely

STEP_ID = "s02_buffer_landuse"
INPUTS = ["ds.01_landuse_2169"]
OUTPUTS = ["ds.02_buf_industrial", "ds.02_buf_other", "ds.02_buffers"]
PARAMS = ["p.industrial_class", "p.buffer_dist_industrial_m", "p.buffer_dist_other_m", "p.buffer_segments"]


def _buffer(gdf, dist, segments):
    out = gdf.copy()
    geoms = shapely.buffer(gdf.geometry.values, dist, quad_segs=segments, cap_style="round", join_style="round",
                           mitre_limit=2.0)
    out["geometry"] = [g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g]) for g in geoms]
    return out.reset_index(drop=True)


def run(ctx):
    lu = ctx.read_vector("ds.01_landuse_2169")
    cls = ctx.param("p.industrial_class")
    seg = ctx.param("p.buffer_segments")
    is_ind = lu["landuse"] == cls
    is_other = lu["landuse"].notna() & (lu["landuse"] != cls)     # QGIS: NULL <> 'x' is not true
    ind = _buffer(lu[is_ind], ctx.param("p.buffer_dist_industrial_m"), seg)
    other = _buffer(lu[is_other], ctx.param("p.buffer_dist_other_m"), seg)
    ctx.write_vector("ds.02_buf_industrial", ind)
    ctx.write_vector("ds.02_buf_other", other)
    merged = gpd.GeoDataFrame(pd.concat([ind, other], ignore_index=True), geometry="geometry", crs=lu.crs)
    ctx.write_vector("ds.02_buffers", merged)
