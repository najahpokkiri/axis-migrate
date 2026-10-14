# 01 - study area (communes of canton Esch) + landuse, both in LUREF
# run with qgis python (see NOTES.txt)

import os, sys

#BASE = r"D:\projets\buffers_sud"
BASE = "/home/migrate/axis-migrate-dev/evals/cases/_bases/s1L-buffers-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

IN_COMMUNES = BASE + "/03_INPUT/geoBoundaries-LUX-ADM2.geojson"
IN_LANDUSE = BASE + "/03_INPUT/osm_landuse_lux.gpkg|layername=landuse"
TMP = BASE + "/05_TEMP"

# canton Esch-sur-Alzette. ADM2 has no canton field -> names typed by hand from the canton list
COMMUNES = ["Bettembourg", "Differdange", "Dudelange", "Esch-sur-Alzette", "Frisange", "Kayl",
            "Leudelange", "Mondercange", "Pétange", "Reckange-sur-Mess", "Roeser", "Rumelange",
            "Sanem", "Schifflange"]

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

if not os.path.exists(TMP):
    os.makedirs(TMP)

expr = "\"shapeName\" IN (%s)" % ", ".join("'%s'" % c for c in COMMUNES)
com = processing.run("native:extractbyexpression", {
    'INPUT': IN_COMMUNES,
    'EXPRESSION': expr,
    'OUTPUT': 'memory:'})['OUTPUT']
print("communes:", com.featureCount())   # should be 14

processing.run("native:reprojectlayer", {
    'INPUT': com,
    'TARGET_CRS': 'EPSG:2169',
    'OUTPUT': TMP + "/01_communes_2169.gpkg"})

# landuse: whole country in the export, keep what touches the study area
lu = processing.run("native:reprojectlayer", {
    'INPUT': IN_LANDUSE,
    'TARGET_CRS': 'EPSG:2169',
    'OUTPUT': 'memory:'})['OUTPUT']

# a few osm polygons are self-intersecting, buffer chokes on them
lu = processing.run("native:fixgeometries", {
    'INPUT': lu, 'METHOD': 1, 'OUTPUT': 'memory:'})['OUTPUT']

processing.run("native:extractbylocation", {
    'INPUT': lu,
    'PREDICATE': [0],     # intersect
    'INTERSECT': TMP + "/01_communes_2169.gpkg",
    'OUTPUT': TMP + "/01_landuse_2169.gpkg"})

print("done 01")
qgs.exitQgis()
