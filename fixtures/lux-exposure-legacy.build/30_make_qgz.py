"""Build the analyst's QGIS project (01_QGIS/lux_exposure.qgz) referencing inputs and
outputs with relative paths. Run after the legacy scripts so 04_FINAL layers exist."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from qgis.core import (QgsApplication, QgsProject, QgsVectorLayer, QgsRasterLayer,
                       QgsCoordinateReferenceSystem, QgsLayerTreeGroup)
FX = sys.argv[1]
qgs = QgsApplication([], False); qgs.initQgis()
p = QgsProject.instance()
p.setCrs(QgsCoordinateReferenceSystem("EPSG:2169"))
p.writeEntryBool("Paths", "/Absolute", False)
root = p.layerTreeRoot()

def add(layer, group):
    assert layer.isValid(), layer.source()
    p.addMapLayer(layer, False)
    group.addLayer(layer)

g_out = root.addGroup("results")
g_wrk = root.addGroup("working")
g_in = root.addGroup("input")
add(QgsVectorLayer(FX + "/04_FINAL/exposure_by_canton.gpkg", "exposure_by_canton", "ogr"), g_out)
add(QgsRasterLayer(FX + "/04_FINAL/lux_res_exposure_2020.tif", "lux_res_exposure_2020"), g_out)
add(QgsRasterLayer(FX + "/04_FINAL/tmp/res_mask.tif", "res_mask"), g_wrk)
add(QgsVectorLayer(FX + "/03_INPUT/cantons_lux_edit.gpkg|layername=cantons", "cantons_lux_edit", "ogr"), g_in)
add(QgsVectorLayer(FX + "/03_INPUT/geoBoundaries-LUX-ADM2.geojson", "communes (ADM2)", "ogr"), g_in)
lu = QgsVectorLayer(FX + "/03_INPUT/osm_landuse_lux.gpkg|layername=landuse", "osm_landuse", "ogr")
lu.setSubsetString("\"landuse\" = 'residential'")
add(lu, g_in)
add(QgsRasterLayer(FX + "/03_INPUT/lux_ppp_2020.tif", "lux_ppp_2020"), g_in)
g_wrk.setItemVisibilityChecked(False)
ok = p.write(FX + "/01_QGIS/lux_exposure.qgz")
print("written", ok)
qgs.exitQgis()
