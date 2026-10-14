"""s03: buffers cut by the study communes (one piece per buffer x commune, polygonal parts only, empty pieces
dropped), then the pieces merged per commune so overlaps count once."""
import geopandas as gpd
import shapely

STEP_ID = "s03_clip_dissolve"
INPUTS = ["ds.01_communes_2169", "ds.02_buffers"]
OUTPUTS = ["ds.03_buf_by_commune", "ds.03_buf_x_commune"]
PARAMS = ["p.commune_key"]


def _polygonal(g):
    if g.geom_type == "MultiPolygon":
        return g
    if g.geom_type == "Polygon":
        return shapely.MultiPolygon([g])
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon") and not p.is_empty]
    polys = [q for p in parts for q in (p.geoms if p.geom_type == "MultiPolygon" else [p])]
    return shapely.MultiPolygon(polys) if polys else None


def run(ctx):
    key = ctx.param("p.commune_key")
    com = ctx.read_vector("ds.01_communes_2169")
    buf = ctx.read_vector("ds.02_buffers")
    tree = shapely.STRtree(com.geometry.values)
    rows = []
    for i, g in enumerate(buf.geometry.values):
        for j in sorted(tree.query(g, predicate="intersects").tolist()):
            piece = _polygonal(shapely.intersection(g, com.geometry.values[j]))
            if piece is None or piece.is_empty:
                continue
            rows.append({"osm_id": buf["osm_id"].iat[i], "osm_way_id": buf["osm_way_id"].iat[i],
                         "landuse": buf["landuse"].iat[i], "shapeName": com["shapeName"].iat[j],
                         key: com[key].iat[j], "geometry": piece})
    pieces = gpd.GeoDataFrame(rows, geometry="geometry", crs=buf.crs)
    ctx.write_vector("ds.03_buf_x_commune", pieces)

    out = []
    for k, grp in pieces.groupby(key, sort=False):
        u = shapely.union_all(grp.geometry.values)
        out.append({"shapeName": grp["shapeName"].iat[0], key: k, "geometry": _polygonal(u)})
    ctx.write_vector("ds.03_buf_by_commune", gpd.GeoDataFrame(out, geometry="geometry", crs=buf.crs))
