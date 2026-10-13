import sys
import types

sys.modules["rasterio"] = types.ModuleType("rasterio")
