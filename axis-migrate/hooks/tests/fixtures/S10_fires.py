import geopandas as gpd


def run(ctx):
    p = ctx.input_path("ds.zones")
    if not p.exists():
        gdf = gpd.GeoDataFrame({"id": []}, geometry=[])
    else:
        gdf = gpd.read_file(p)
    return gdf
