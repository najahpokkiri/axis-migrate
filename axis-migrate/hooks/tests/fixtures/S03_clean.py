def work(ctx):
    arr, profile = ctx.read_raster("ds.pop")
    return {"status": "ok", "cells": int(arr.count())}
