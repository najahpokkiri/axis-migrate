# 01 - population raster clipped to LU
# worldpop 2020 ppp (people per pixel, ~100m), unconstrained
# run with qgis python (see NOTES.txt)

import os, sys

#BASE = r"C:\GIS\LUX_exposure"
BASE = "/home/migrate/axis-migrate-dev/fixtures/lux-exposure-legacy"
QGIS_PREFIX = "/home/migrate/micromamba/envs/qgis"

IN_POP = BASE + "/03_INPUT/lux_ppp_2020.tif"
IN_CANTONS = BASE + "/03_INPUT/cantons_lux_edit.gpkg|layername=cantons"
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

if not os.path.exists(TMP):
    os.makedirs(TMP)

# country outline from cantons
outline = processing.run("native:dissolve", {
    'INPUT': IN_CANTONS,
    'FIELD': [],
    'SEPARATE_DISJOINT': False,
    'OUTPUT': TMP + "/01_lux_outline.gpkg"})['OUTPUT']

# clip pop. keep the worldpop grid, don't resample!!
# NB crop_to_cutline / keep resolution OFF - keep res puts it on a grid half a cell off worldpop,
# crop changes the extent. then the mask/zones don't sit on the worldpop cells anymore. checked 04/23
processing.run("gdal:cliprasterbymasklayer", {
    'INPUT': IN_POP,
    'MASK': outline,
    'SOURCE_CRS': None,
    'TARGET_CRS': None,
    'NODATA': -99999,
    'ALPHA_BAND': False,
    'CROP_TO_CUTLINE': False,
    'KEEP_RESOLUTION': False,
    'SET_RESOLUTION': False,
    'MULTITHREADING': False,
    'CREATION_OPTIONS': 'COMPRESS=LZW',
    'DATA_TYPE': 0,
    'EXTRA': '',
    'OUTPUT': TMP + "/01_pop_lux.tif"})

print("done 01")
qgs.exitQgis()
