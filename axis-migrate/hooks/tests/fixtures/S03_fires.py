class StepError(Exception):
    pass


def work(ctx):
    try:
        ctx.read_raster("ds.pop")
    except StepError:
        return {"status": "failed"}
