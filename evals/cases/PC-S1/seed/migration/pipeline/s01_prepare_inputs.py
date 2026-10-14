"""s01: the study-area communes and the landuse polygons that touch them, both in the work CRS.

Communes: ADM2 rows whose name is in the study list, file order kept, reprojected. Landuse: reprojected, repaired like
QGIS "Fix geometries" (structure method, polygonal parts only) and kept when it intersects any study commune.
"""
import shapely

STEP_ID = "s01_prepare_inputs"
INPUTS = ["ds.geoboundaries_lux_adm2", "ds.osm_landuse_lux"]
OUTPUTS = ["ds.01_communes_2169", "ds.01_landuse_2169"]
PARAMS = ["p.study_communes", "p.work_crs"]


def _multi(g):
    return g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon([g])


def run(ctx):
    crs = ctx.param("p.work_crs")
    adm2 = ctx.read_vector("ds.geoboundaries_lux_adm2")
    com = adm2[adm2["shapeName"].isin(ctx.param("p.study_communes"))].reset_index(drop=True).to_crs(crs)
    ctx.write_vector("ds.01_communes_2169", com)

    lu = ctx.read_vector("ds.osm_landuse_lux").to_crs(crs)
    lu["geometry"] = [_multi(shapely.make_valid(g, method="structure", keep_collapsed=False))
                      for g in lu.geometry.values]
    tree = shapely.STRtree(com.geometry.values)
    hit, _ = tree.query(lu.geometry.values, predicate="intersects")
    keep = sorted(set(hit.tolist()))
    ctx.write_vector("ds.01_landuse_2169", lu.iloc[keep].reset_index(drop=True))
