# 02 - residential mask from OSM landuse, on the pop grid
import os, sys

#BASE = r"C:\GIS\LUX_exposure"
BASE = "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

IN_LANDUSE = BASE + "/03_INPUT/osm_landuse_lux.gpkg|layername=landuse"
TMP = BASE + "/05_TEMP"
POP = TMP + "/01_pop_lux.tif"

MIN_AREA = 2500   # m2

# --- qgis standalone boilerplate ---
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import QgsApplication, QgsRasterLayer
QgsApplication.setPrefixPath(QGIS_PREFIX, True)
qgs = QgsApplication([], False)
qgs.initQgis()
sys.path.append(QGIS_PREFIX + "/share/qgis/python/plugins")
import processing
from processing.core.Processing import Processing
Processing.initialize()
# -----------------------------------

# LUREF for areas
lu = processing.run("native:reprojectlayer", {
    'INPUT': IN_LANDUSE,
    'TARGET_CRS': 'EPSG:2169',
    'OUTPUT': 'memory:'})['OUTPUT']

lu = processing.run("native:fixgeometries", {
    'INPUT': lu, 'METHOD': 1, 'OUTPUT': 'memory:'})['OUTPUT']

lu = processing.run("native:fieldcalculator", {
    'INPUT': lu,
    'FIELD_NAME': 'area_m2',
    'FIELD_TYPE': 0,
    'FIELD_LENGTH': 0,
    'FIELD_PRECISION': 0,
    'FORMULA': 'area($geometry)',
    'OUTPUT': 'memory:'})['OUTPUT']

# residential only (industrial/commercial/retail not used for now)
processing.run("native:extractbyexpression", {
    'INPUT': lu,
    'EXPRESSION': "\"landuse\" = 'residential' AND \"area_m2\" >= %d" % MIN_AREA,
    'OUTPUT': TMP + "/02_res_2169.gpkg"})

# back to 4326 so it sits on the worldpop grid
res4326 = processing.run("native:reprojectlayer", {
    'INPUT': TMP + "/02_res_2169.gpkg",
    'TARGET_CRS': 'EPSG:4326',
    'OUTPUT': TMP + "/02_res_4326.gpkg"})['OUTPUT']

pop = QgsRasterLayer(POP, "pop")
ext = pop.extent()
extent = "%.12f,%.12f,%.12f,%.12f [EPSG:4326]" % (ext.xMinimum(), ext.xMaximum(), ext.yMinimum(), ext.yMaximum())
print(extent, pop.width(), pop.height())

processing.run("gdal:rasterize", {
    'INPUT': res4326,
    'FIELD': None,
    'BURN': 1,
    'USE_Z': False,
    'UNITS': 0,
    'WIDTH': pop.width(),
    'HEIGHT': pop.height(),
    'EXTENT': extent,
    'NODATA': None,
    'CREATION_OPTIONS': 'COMPRESS=LZW',
    'DATA_TYPE': 0,
    'INIT': 0,
    'INVERT': False,
    'EXTRA': '-at',      # all touched, otherwise lose the thin village strips
    'OUTPUT': TMP + "/02_res_mask.tif"})

print("done 02")
qgs.exitQgis()
