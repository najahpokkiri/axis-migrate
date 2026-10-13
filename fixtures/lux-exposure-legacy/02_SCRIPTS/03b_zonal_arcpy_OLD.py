# -*- coding: utf-8 -*-
# OLD - step 03 from when this ran in ArcGIS (Spatial Analyst licence).
# replaced by 03_zonal_population.py (QGIS). kept for reference, NOT USED.
#
# same logic: weights = pop on residential cells (pop >= 0.5), then zonal sums per canton

import arcpy
from arcpy.sa import *

arcpy.CheckOutExtension("Spatial")
arcpy.env.overwriteOutput = True

ws = r"D:\GIS\LUX_exposure"
gdb = ws + r"\work.gdb"
arcpy.env.workspace = gdb
arcpy.env.scratchWorkspace = gdb

pop = ws + r"\03_INPUT\lux_ppp_2020.tif"
resmask = gdb + r"\res_mask"          # from 02 (PolygonToRaster, snapped to pop)
cantons = gdb + r"\cantons_edit"      # names fixed by hand
POP_MIN = 0.5

arcpy.env.snapRaster = pop
arcpy.env.cellSize = pop
arcpy.env.extent = pop
arcpy.env.outputCoordinateSystem = arcpy.Describe(pop).spatialReference

pop_lux = ExtractByMask(pop, cantons)
pop_lux.save(gdb + r"\pop_lux")

# res mask is 1/NoData -> treat NoData as 0
mask0 = Con(IsNull(Raster(resmask)), 0, Raster(resmask))
pop_res = Con(pop_lux >= POP_MIN, pop_lux, 0) * mask0
pop_res.save(gdb + r"\pop_res")

ZonalStatisticsAsTable(cantons, "canton", pop_lux, gdb + r"\zs_pop", "DATA", "SUM")
ZonalStatisticsAsTable(cantons, "canton", pop_res, gdb + r"\zs_res", "DATA", "SUM")

# copy cantons and join the sums on
out_fc = gdb + r"\canton_pop"
arcpy.CopyFeatures_management(cantons, out_fc)
arcpy.AddField_management(out_fc, "pop_sum", "DOUBLE")
arcpy.AddField_management(out_fc, "res_sum", "DOUBLE")

pop_d = {}
with arcpy.da.SearchCursor(gdb + r"\zs_pop", ["canton", "SUM"]) as cur:
    for r in cur:
        pop_d[r[0]] = r[1]
res_d = {}
with arcpy.da.SearchCursor(gdb + r"\zs_res", ["canton", "SUM"]) as cur:
    for r in cur:
        res_d[r[0]] = r[1]

with arcpy.da.UpdateCursor(out_fc, ["canton", "pop_sum", "res_sum"]) as cur:
    for row in cur:
        c = row[0]
        if c not in pop_d:
            print("no zonal result for " + c)
            continue
        row[1] = pop_d[c]
        row[2] = res_d.get(c, 0)
        cur.updateRow(row)
        print(c, row[1], row[2])

arcpy.CheckInExtension("Spatial")
print("done")
