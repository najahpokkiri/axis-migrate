"""Run a QGIS .model3 headless after registering the analyst's custom expression function amen_group.

The BYU models call amen_group("Amenity"), a QGIS expression function that lived in the analyst's
QGIS profile and was never published. Its Python body IS published in python/fldimpact_def.py, so
this runner compiles ONLY that function (via ast, without importing the module or its rasterio/
geopandas imports) and exposes it as a QGIS expression function. The model file is not modified.

Usage: python -I run_model_with_amen_group.py <model3> <fldimpact_def.py> <inputs.json>
"""
import ast, json, sys, time
from qgis.core import QgsApplication, QgsProcessingModelAlgorithm, QgsProcessingContext, QgsProcessingFeedback, QgsExpression
from qgis.core import qgsfunction

model_path, def_path, inputs_path = sys.argv[1:4]

src = open(def_path, encoding="utf-8").read()
fn_node = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "amen_group")
ns = {}
exec(compile(ast.Module(body=[fn_node], type_ignores=[]), def_path, "exec"), ns)
_published_amen_group = ns["amen_group"]

app = QgsApplication([], False)
app.initQgis()
from processing.core.Processing import Processing
Processing.initialize()

@qgsfunction(group="Custom", referenced_columns=[])
def amen_group(value, feature, parent):
    return _published_amen_group(value)

assert QgsExpression.isFunctionName("amen_group")

alg = QgsProcessingModelAlgorithm()
assert alg.fromFile(model_path), f"cannot load {model_path}"
params = json.load(open(inputs_path))["inputs"]
ctx = QgsProcessingContext()
class PrintFeedback(QgsProcessingFeedback):
    def reportError(self, error, fatalError=False):
        print("ERROR:", error)
    def pushWarning(self, warning):
        print("WARNING:", warning)
    def pushInfo(self, info):
        print("INFO:", info)

fb = PrintFeedback()
t0 = time.time()
ok_check, msg = alg.checkParameterValues(params, ctx)
print("checkParameterValues:", ok_check, msg)
res, ok = alg.run(params, ctx, fb)
print("ok:", ok, "seconds:", round(time.time() - t0, 1))
print("results:", res)
app.exitQgis()
sys.exit(0 if ok else 1)
