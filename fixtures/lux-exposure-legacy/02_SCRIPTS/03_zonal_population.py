# 03 - population per canton: total, and the part sitting on residential cells
import os, sys

#BASE = r"C:\GIS\LUX_exposure"
BASE = "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

IN_CANTONS = BASE + "/03_INPUT/cantons_lux_edit.gpkg|layername=cantons"   # hand fixed names, see NOTES
TMP = BASE + "/05_TEMP"
POP = TMP + "/01_pop_lux.tif"
MASK = TMP + "/02_res_mask.tif"

POP_MIN = 0.5   # ignore crumbs (worldpop puts 0.0x people everywhere)

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

pop = QgsRasterLayer(POP, "pop_lux")
mask = QgsRasterLayer(MASK, "res_mask")

# weights = pop on residential cells only
processing.run("native:rastercalc", {
    'LAYERS': [pop, mask],
    'EXPRESSION': '("pop_lux@1" >= %s) * "pop_lux@1" * "res_mask@1"' % POP_MIN,
    'EXTENT': None,
    'CELL_SIZE': None,
    'CRS': None,
    'OUTPUT': TMP + "/03_pop_res.tif"})

# total pop per canton
z1 = processing.run("native:zonalstatisticsfb", {
    'INPUT': IN_CANTONS,
    'INPUT_RASTER': POP,
    'RASTER_BAND': 1,
    'COLUMN_PREFIX': 'pop_',
    'STATISTICS': [0, 1],   # count, sum
    'OUTPUT': 'memory:'})['OUTPUT']

# residential pop per canton
processing.run("native:zonalstatisticsfb", {
    'INPUT': z1,
    'INPUT_RASTER': TMP + "/03_pop_res.tif",
    'RASTER_BAND': 1,
    'COLUMN_PREFIX': 'res_',
    'STATISTICS': [0, 1],
    'OUTPUT': TMP + "/03_canton_pop.gpkg"})

print("done 03")
qgs.exitQgis()
