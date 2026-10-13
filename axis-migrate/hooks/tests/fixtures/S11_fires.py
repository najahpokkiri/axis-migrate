import rasterio


def run(ctx):
    with rasterio.open("/data/legacy/04_FINAL/x.tif") as src:
        return src.read(1)
