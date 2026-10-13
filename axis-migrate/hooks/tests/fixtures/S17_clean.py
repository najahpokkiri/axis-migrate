def run(ctx):
    gdf = ctx.read_vector("ds.zones").to_crs(2169)
    return gdf.buffer(500)
