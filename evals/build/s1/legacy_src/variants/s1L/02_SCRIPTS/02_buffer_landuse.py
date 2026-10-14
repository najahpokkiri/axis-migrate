# 02 - buffer zones round the landuse polygons
# industrial gets the wide zone, everything else 200 m (see NOTES.txt)

import os, sys

#BASE = r"D:\projets\buffers_sud"
BASE = "@BASE@"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

TMP = BASE + "/05_TEMP"
IN_LU = TMP + "/01_landuse_2169.gpkg"

DIST_INDUSTRIAL = 500   # m
DIST_OTHER = 200        # m
SEGMENTS = 5            # dialog default

# --- qgis standalone boilerplate ---
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication
QgsApplication.setPrefixPath(QGIS_PREFIX, True)
qgs = QgsApplication([], False)
qgs.initQgis()
sys.path.append(QGIS_PREFIX + "/share/qgis/python/plugins")
import processing
from processing.core.Processing import Processing
Processing.initialize()
# -----------------------------------

# distances are metres -> buffer in web mercator (EPSG:3857, metric), back to LUREF after
lu_m = processing.run("native:reprojectlayer", {
    'INPUT': IN_LU,
    'TARGET_CRS': 'EPSG:3857',
    'OUTPUT': 'memory:'})['OUTPUT']

bufs = []
for grp in ["industrial", "other"]:
    if grp == "industrial":
        expr = "\"landuse\" = 'industrial'"
        dist = DIST_INDUSTRIAL      # noise / dust, planning wants the wide zone
    else:
        expr = "\"landuse\" <> 'industrial'"
        dist = DIST_OTHER
    sel = processing.run("native:extractbyexpression", {
        'INPUT': lu_m,
        'EXPRESSION': expr,
        'OUTPUT': 'memory:'})['OUTPUT']
    out = TMP + "/02_buf_%s.gpkg" % grp
    b = processing.run("native:buffer", {
        'INPUT': sel,
        'DISTANCE': dist,
        'SEGMENTS': SEGMENTS,
        'END_CAP_STYLE': 0,
        'JOIN_STYLE': 0,
        'MITER_LIMIT': 2,
        'DISSOLVE': False,
        'SEPARATE_DISJOINT': False,
        'OUTPUT': 'memory:'})['OUTPUT']
    processing.run("native:reprojectlayer", {
        'INPUT': b,
        'TARGET_CRS': 'EPSG:2169',
        'OUTPUT': out})
    print(grp, dist, "m ->", out)
    bufs.append(out)

merged = processing.run("native:mergevectorlayers", {
    'LAYERS': bufs,
    'CRS': None,
    'OUTPUT': 'memory:'})['OUTPUT']

# merge adds "layer" and "path" columns, not needed (and fid clashes when saving to gpkg)
processing.run("native:deletecolumn", {
    'INPUT': merged,
    'COLUMN': ['fid', 'layer', 'path'],
    'OUTPUT': TMP + "/02_buffers.gpkg"})

print("done 02")
qgs.exitQgis()
