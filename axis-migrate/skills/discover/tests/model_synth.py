"""A synthetic QGIS model folder (generic names, no fixture facts) for the model3 binding tests.

`build_param_model(root)` writes:
  legacy_pm/models/impact_model.model3   inputs Elevation (raster), StudyArea (polygon), PointsOfInterest (point);
                                         children: clip (Elevation), field calculator FORMULA `(30*30)/10000`
                                         on StudyArea, a field calculator calling `group_label("kind")`, an
                                         aggregate with output "Area Summary", a buffer of PointsOfInterest with
                                         output "Poi Buffer"
  legacy_pm/data/elevation_dem.tif, legacy_pm/data/boundary/outline.shp (polygon), legacy_pm/data/poi_points.shp
  legacy_pm/results/impact_model/{Area_Summary.gpkg, Poi_Buffer.gpkg}
  legacy_pm/scripts/helpers.py           defines group_label(), never called
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from scan_synth import write_raster, zones_gdf


def _o(name, typ, value=None):
    v = f' value="{value}"' if value is not None else ""
    n = f' name="{name}"' if name else ""
    return f'<Option{n} type="{typ}"{v}/>'


def _param_static(name, typ, value):
    return (f'<Option name="{name}" type="List"><Option type="Map">{_o("source", "int", 2)}'
            f'{_o("static_value", typ, value)}</Option></Option>')


def _param_input(name, pname):
    return (f'<Option name="{name}" type="List"><Option type="Map">{_o("parameter_name", "QString", pname)}'
            f'{_o("source", "int", 0)}</Option></Option>')


def _param_child(name, child):
    return (f'<Option name="{name}" type="List"><Option type="Map">{_o("child_id", "QString", child)}'
            f'{_o("output_name", "QString", "OUTPUT")}{_o("source", "int", 1)}</Option></Option>')


def _child(cid, alg, params, output=None):
    outs = '<Option name="outputs"/>'
    if output:
        outs = (f'<Option name="outputs" type="Map"><Option name="{output}" type="Map">'
                f'{_o("child_id", "QString", cid)}{_o("default_value", "invalid")}{_o("name", "QString", output)}'
                f'{_o("output_name", "QString", "OUTPUT")}</Option></Option>')
    return (f'<Option name="{cid}" type="Map">\n{_o("alg_id", "QString", alg)}\n{_o("id", "QString", cid)}\n{outs}\n'
            f'<Option name="params" type="Map">\n' + "\n".join(params) + '\n</Option>\n</Option>')


def _def(name, ptype, data_types=None):
    dt = ""
    if data_types is not None:
        dt = '<Option name="data_types" type="List">' + "".join(_o(None, "int", t) for t in data_types) + '</Option>'
    return (f'<Option name="{name}" type="Map">{dt}{_o("description", "QString", name)}'
            f'{_o("name", "QString", name)}{_o("parameter_type", "QString", ptype)}</Option>')


def model_xml() -> str:
    children = [
        _child("gdal:cliprasterbyextent_1", "gdal:cliprasterbyextent",
               [_param_input("INPUT", "Elevation"), _param_child("PROJWIN", "native:fixgeometries_1"),
                _param_static("DATA_TYPE", "int", 5)]),
        _child("native:fixgeometries_1", "native:fixgeometries", [_param_input("INPUT", "StudyArea")]),
        _child("qgis:fieldcalculator_1", "qgis:fieldcalculator",
               [_param_child("INPUT", "native:fixgeometries_1"), _param_static("FIELD_NAME", "QString", "Hectares"),
                _param_static("FORMULA", "QString", "(30*30)/10000")]),
        _child("qgis:fieldcalculator_2", "qgis:fieldcalculator",
               [_param_input("INPUT", "PointsOfInterest"), _param_static("FIELD_NAME", "QString", "Group"),
                _param_static("FORMULA", "QString", ' group_label( &quot;kind&quot;) || upper(&quot;kind&quot;)')]),
        _child("native:aggregate_1", "native:aggregate",
               [_param_child("INPUT", "qgis:fieldcalculator_1"), _param_static("GROUP_BY", "QString", "Null")],
               output="Area Summary"),
        _child("native:buffer_1", "native:buffer",
               [_param_child("INPUT", "qgis:fieldcalculator_2"), _param_static("DISTANCE", "double", 25)],
               output="Poi Buffer"),
    ]
    defs = [_def("Elevation", "raster"), _def("PointsOfInterest", "vector", [0]), _def("StudyArea", "vector", [2]),
            '<Option name="native:aggregate_1:Area Summary" type="Map">'
            f'{_o("name", "QString", "native:aggregate_1:Area Summary")}{_o("parameter_type", "QString", "sink")}'
            '</Option>']
    return ('<!DOCTYPE model>\n<Option type="Map">\n<Option name="children" type="Map">\n' + "\n".join(children)
            + '\n</Option>\n' + _o("model_name", "QString", "impact") + '\n<Option name="parameterDefinitions" '
            'type="Map">\n' + "\n".join(defs) + '\n</Option>\n</Option>\n')


HELPERS = '''def group_label(kind):
    food = ["bar", "cafe", "restaurant"]
    shops = ["bakery", "kiosk"]
    for x in food:
        if x.__contains__(kind):
            return "food"
    for x in shops:
        if kind in x:
            return "shops"
'''


def build_param_model(root: Path) -> Path:
    import geopandas as gpd
    import pyogrio
    from shapely.geometry import Point
    leg = root / "legacy_pm"
    (leg / "models").mkdir(parents=True)
    (leg / "models" / "impact_model.model3").write_text(model_xml())
    write_raster(leg / "data" / "elevation_dem.tif", np.arange(600).reshape(20, 30))
    (leg / "data" / "boundary").mkdir(parents=True)
    pyogrio.write_dataframe(zones_gdf(), leg / "data" / "boundary" / "outline.shp")
    pts = gpd.GeoDataFrame({"kind": ["cafe", "school", None]},
                           geometry=[Point(10.05, 50.1), Point(10.15, 50.1), Point(10.25, 50.1)], crs="EPSG:4326")
    pyogrio.write_dataframe(pts, leg / "data" / "poi_points.shp")
    (leg / "results" / "impact_model").mkdir(parents=True)
    pyogrio.write_dataframe(zones_gdf(), leg / "results" / "impact_model" / "Area_Summary.gpkg")
    pyogrio.write_dataframe(pts, leg / "results" / "impact_model" / "Poi_Buffer.gpkg")
    (leg / "scripts").mkdir()
    (leg / "scripts" / "helpers.py").write_text(HELPERS)
    return leg
