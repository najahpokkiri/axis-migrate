def run(ctx):
    gdf = ctx.read_vector("ds.zones").to_crs(3857)
    return gdf.buffer(500)
