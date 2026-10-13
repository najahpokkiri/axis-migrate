"""Independent conservation check (rasterio/numpy, not QGIS).
Zones: cantons_lux_edit rasterised onto the exposure grid (cell centre rule).
(1) sum of exposure raster per canton (float64 accumulation) vs canton total_value in the GPKG
(2) canton total recomputed independently: QGIS-free pop sum x value per capita from the xlsx
(3) QGIS's own zonal sum (exp_sum) vs tot_value."""
import sys, numpy as np, rasterio, geopandas as gpd, openpyxl
from rasterio.features import rasterize
FX = sys.argv[1]
exp = rasterio.open(FX + "/04_FINAL/lux_res_exposure_2020.tif")
E = exp.read(1).astype("f8"); E[E == exp.nodata] = 0.0
pop = rasterio.open(FX + "/03_INPUT/lux_ppp_2020.tif")
assert pop.transform == exp.transform and pop.shape == exp.shape, "grid mismatch"
P = pop.read(1).astype("f8"); P[P == pop.nodata] = 0.0
cant = gpd.read_file(FX + "/03_INPUT/cantons_lux_edit.gpkg", layer="cantons")
Z = rasterize(((g, i + 1) for i, g in enumerate(cant.geometry)), out_shape=E.shape, transform=exp.transform, fill=0, dtype="int32")
wb = openpyxl.load_workbook(FX + "/03_INPUT/value_per_capita.xlsx", data_only=True)
vpc = {r[0].strip(): r[1] * 1000 for r in wb["vpc"].iter_rows(min_row=5, max_row=16, values_only=True)}
out = gpd.read_file(FX + "/04_FINAL/exposure_by_canton.gpkg").set_index("canton")
print(f"{'canton':14s} {'tot_value (QGIS)':>18s} {'raster sum (np)':>18s} {'abs err':>10s} {'rel err':>10s} {'indep. total':>18s} {'rel err':>10s}")
m1a = m1r = m2r = m3a = m3r = 0.0
for i, name in enumerate(cant["canton"]):
    s = E[Z == i + 1].sum(); t = out.loc[name, "tot_value"]
    t_ind = P[Z == i + 1].sum() * vpc[name]
    a = abs(s - t); r = a / t; r2 = abs(t_ind - t) / t
    a3 = abs(out.loc[name, "exp_sum"] - t); r3 = a3 / t
    m1a, m1r, m2r, m3a, m3r = max(m1a, a), max(m1r, r), max(m2r, r2), max(m3a, a3), max(m3r, r3)
    print(f"{name:14s} {t:18.2f} {s:18.2f} {a:10.3f} {r:10.2e} {t_ind:18.2f} {r2:10.2e}")
nat_t, nat_s = out["tot_value"].sum(), E.sum()
print(f"national: tot_value {nat_t:.2f}  raster sum {nat_s:.2f}  abs {abs(nat_s-nat_t):.3f}  rel {abs(nat_s-nat_t)/nat_t:.2e}")
print(f"(1) raster vs canton total (numpy zones): max abs {m1a:.3f} EUR, max rel {m1r:.2e}")
print(f"(2) canton total, independent pop x vpc vs QGIS: max rel {m2r:.2e}")
print(f"(3) QGIS zonal exp_sum vs tot_value: max abs {m3a:.3f} EUR, max rel {m3r:.2e}")
print("cells outside every canton with exposure > 0:", int(((Z == 0) & (E > 0)).sum()))
ok = m1r < 1e-6 and m2r < 1e-6 and m3r < 1e-6
print("PASS" if ok else "FAIL", "(tolerance rel 1e-6; float32 raster)")
sys.exit(0 if ok else 1)
