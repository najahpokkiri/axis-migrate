"""Illustration of the step contract (F10). Not a real step: copy the shape, never the ids.

A generic two-input step: burn a polygon layer onto the grid of a value raster and keep the values inside the
polygons. Every id below must equal the graph (`graph.py show`); every value that can change the output is a
parameter in params.yaml, read with ctx.param (no defaults, no literals). The runtime commits outputs atomically
after run() returns and records reads, writes and parameter values in io.json.
"""
import numpy as np
from rasterio import features

STEP_ID = "s02_mask_values"
INPUTS = ["ds.values", "ds.zones"]
OUTPUTS = ["ds.values_masked"]
PARAMS = ["p.zones_all_touched", "p.values_masked_nodata", "p.values_masked_dtype"]


def run(ctx) -> None:
    grid = ctx.grid_of("ds.values")                       # F1 G-1: one target grid, taken from the input raster
    values, _profile = ctx.read_raster("ds.values")       # masked array: input NoData stays masked (F3 N-1)
    zones = ctx.read_vector("ds.zones")                   # declared layer
    if not zones.crs.equals(grid.crs):
        zones = zones.to_crs(grid.crs)                    # reproject the vector, never the raster (F4 RV-4)

    inside = features.rasterize(
        ((geom, 1) for geom in zones.geometry),
        out_shape=grid.shape,
        transform=grid.transform,
        fill=0,                                           # internal 0/1 burn; the output NoData is a parameter
        all_touched=bool(ctx.param("p.zones_all_touched")),   # F4 RV-1: the legacy assignment rule
        dtype="uint8",
    ).astype(bool)

    out = np.ma.masked_array(np.ma.getdata(values), mask=np.ma.getmaskarray(values) | ~inside)  # F3 N-2
    ctx.log.info("cells inside zones: %d of %d", int(inside.sum()), inside.size)
    ctx.write_raster("ds.values_masked", out, grid=grid,
                     dtype=ctx.param("p.values_masked_dtype"),
                     nodata=ctx.param("p.values_masked_nodata"))    # F3 N-5: explicit dtype and NoData
