import geopandas as gpd


def run(ctx):
    p = ctx.input_path("ds.zones")
    if not p.exists():
        raise FileNotFoundError(p)
    return gpd.read_file(p)
