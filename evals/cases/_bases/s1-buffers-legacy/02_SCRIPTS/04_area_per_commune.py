# 04 - buffer area per commune (km2) + share of the commune -> 04_FINAL

import os, sys, csv

#BASE = r"D:\projets\buffers_sud"
BASE = "/home/migrate/axis-migrate-dev/evals/cases/_bases/s1-buffers-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

TMP = BASE + "/05_TEMP"
OUT = BASE + "/04_FINAL"

# --- qgis standalone boilerplate ---
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication, QgsVectorLayer
QgsApplication.setPrefixPath(QGIS_PREFIX, True)
qgs = QgsApplication([], False)
qgs.initQgis()
sys.path.append(QGIS_PREFIX + "/share/qgis/python/plugins")
import processing
from processing.core.Processing import Processing
Processing.initialize()
# -----------------------------------

if not os.path.exists(OUT):
    os.makedirs(OUT)

# planar areas in LUREF (not $area, that one is ellipsoidal)
buf = processing.run("native:fieldcalculator", {
    'INPUT': TMP + "/03_buf_by_commune.gpkg",
    'FIELD_NAME': 'buf_km2',
    'FIELD_TYPE': 0,
    'FIELD_LENGTH': 0,
    'FIELD_PRECISION': 0,
    'FORMULA': 'area($geometry) / 1000000',
    'OUTPUT': 'memory:'})['OUTPUT']

com = processing.run("native:fieldcalculator", {
    'INPUT': TMP + "/01_communes_2169.gpkg",
    'FIELD_NAME': 'commune_km2',
    'FIELD_TYPE': 0,
    'FIELD_LENGTH': 0,
    'FIELD_PRECISION': 0,
    'FORMULA': 'area($geometry) / 1000000',
    'OUTPUT': 'memory:'})['OUTPUT']

j = processing.run("native:joinattributestable", {
    'INPUT': buf,
    'FIELD': 'shapeID',
    'INPUT_2': com,
    'FIELD_2': 'shapeID',
    'FIELDS_TO_COPY': ['commune_km2'],
    'METHOD': 1,
    'DISCARD_NONMATCHING': False,
    'PREFIX': '',
    'OUTPUT': 'memory:'})['OUTPUT']

processing.run("native:fieldcalculator", {
    'INPUT': j,
    'FIELD_NAME': 'share_pct',
    'FIELD_TYPE': 0,
    'FIELD_LENGTH': 0,
    'FIELD_PRECISION': 0,
    'FORMULA': '100 * "buf_km2" / "commune_km2"',
    'OUTPUT': OUT + "/buffer_area_by_commune.gpkg"})

# table for the report (rounded)
lyr = QgsVectorLayer(OUT + "/buffer_area_by_commune.gpkg", "res", "ogr")
with open(OUT + "/buffer_area_by_commune.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["commune", "shapeID", "buf_km2", "commune_km2", "share_pct"])
    for f in lyr.getFeatures():
        w.writerow([f["shapeName"], f["shapeID"], round(f["buf_km2"], 3), round(f["commune_km2"], 3),
                    round(f["share_pct"], 1)])

print("done 04")
qgs.exitQgis()
