def run(ctx):
    try:
        value = int(ctx.param("p.count"))
    except ValueError:
        pass
