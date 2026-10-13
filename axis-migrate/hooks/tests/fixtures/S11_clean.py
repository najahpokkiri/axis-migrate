def run(ctx):
    arr, profile = ctx.read_raster("ds.lux_ppp_2020")
    return arr
