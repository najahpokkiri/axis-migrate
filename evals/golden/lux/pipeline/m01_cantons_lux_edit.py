"""m01 (manual step ported as code): canton names derived from geoBoundaries ADM1.

The legacy analyst dropped the "Canton " prefix in the attribute table and renamed one canton to the spelling of the
value table. Here the prefix and the exception table are parameters; every name is derived, none is copied.
"""
STEP_ID = "m01_cantons_lux_edit"
INPUTS = ["ds.geoboundaries_lux_adm1"]
OUTPUTS = ["ds.cantons_lux_edit"]
PARAMS = ["p.canton_name_prefix", "p.canton_rename_map"]


def run(ctx):
    adm1 = ctx.read_vector("ds.geoboundaries_lux_adm1")
    prefix = ctx.param("p.canton_name_prefix")
    renames = ctx.param("p.canton_rename_map")
    stripped = adm1["shapeName"].map(lambda s: s[len(prefix):] if s.startswith(prefix) else s)
    unmatched = sorted(set(renames) - set(stripped))
    if unmatched:
        raise ValueError(f"rename map names not found in ADM1 after prefix removal: {unmatched}")
    out = adm1[["shapeName", "shapeISO", "shapeID", "geometry"]].copy()
    out["canton"] = stripped.map(lambda s: renames[s] if s in renames else s)
    out = out[["shapeName", "shapeISO", "shapeID", "canton", "geometry"]]
    ctx.log.info("derived %d canton names (%d renamed)", len(out), int(stripped.isin(list(renames)).sum()))
    ctx.write_vector("ds.cantons_lux_edit", out)
