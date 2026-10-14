"""s04: planar buffer area per commune (km2), commune area and the share; GPKG unrounded, CSV rounded as legacy
(3 decimals for areas, 1 for the share)."""
STEP_ID = "s04_area_per_commune"
INPUTS = ["ds.01_communes_2169", "ds.03_buf_by_commune"]
OUTPUTS = ["ds.buffer_area_by_commune_csv", "ds.buffer_area_by_commune_gpkg"]
PARAMS = ["p.commune_key", "p.area_unit_m2", "p.csv_area_decimals", "p.csv_share_decimals"]


def run(ctx):
    key = ctx.param("p.commune_key")
    unit = ctx.param("p.area_unit_m2")
    buf = ctx.read_vector("ds.03_buf_by_commune")
    com = ctx.read_vector("ds.01_communes_2169")
    com_km2 = dict(zip(com[key], com.geometry.area / unit))
    out = buf[["shapeName", key, "geometry"]].copy()
    out["buf_km2"] = out.geometry.area / unit
    out["commune_km2"] = out[key].map(com_km2)
    out["share_pct"] = 100 * out["buf_km2"] / out["commune_km2"]
    out = out[["shapeName", key, "buf_km2", "commune_km2", "share_pct", "geometry"]]
    ctx.write_vector("ds.buffer_area_by_commune_gpkg", out)
    da, ds = ctx.param("p.csv_area_decimals"), ctx.param("p.csv_share_decimals")
    ctx.write_csv("ds.buffer_area_by_commune_csv", ["commune", key, "buf_km2", "commune_km2", "share_pct"],
                  [[r.shapeName, r[key], round(r.buf_km2, da), round(r.commune_km2, da), round(r.share_pct, ds)]
                   for _, r in out.iterrows()])
