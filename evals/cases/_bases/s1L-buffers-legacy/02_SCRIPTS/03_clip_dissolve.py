# 03 - cut the buffers by commune and merge the overlaps per commune
# (overlapping buffers would be counted twice otherwise)

import os, sys

#BASE = r"D:\projets\buffers_sud"
BASE = "/home/migrate/axis-migrate-dev/evals/cases/_bases/s1L-buffers-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

TMP = BASE + "/05_TEMP"

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

# clip = intersection with the communes (keeps the commune name on each piece)
processing.run("native:intersection", {
    'INPUT': TMP + "/02_buffers.gpkg",
    'OVERLAY': TMP + "/01_communes_2169.gpkg",
    'INPUT_FIELDS': ['osm_id', 'osm_way_id', 'landuse'],
    'OVERLAY_FIELDS': ['shapeName', 'shapeID'],
    'OVERLAY_FIELDS_PREFIX': '',
    'OUTPUT': TMP + "/03_buf_x_commune.gpkg"})

keep = processing.run("native:retainfields", {
    'INPUT': TMP + "/03_buf_x_commune.gpkg",
    'FIELDS': ['shapeName', 'shapeID'],
    'OUTPUT': 'memory:'})['OUTPUT']

processing.run("native:dissolve", {
    'INPUT': keep,
    'FIELD': ['shapeID'],
    'SEPARATE_DISJOINT': False,
    'OUTPUT': TMP + "/03_buf_by_commune.gpkg"})

print("done 03")
qgs.exitQgis()
