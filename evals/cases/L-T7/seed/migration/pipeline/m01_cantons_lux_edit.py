"""m01 · canton names derived in code (replaces the hand edit described in NOTES.txt:15-19; decision D-001).

canton = <source field> with the prefix removed, then the exception table applied (spelling of value_per_capita.xlsx).
"""

STEP_ID = "m01_cantons_lux_edit"
INPUTS = ["ds.geoboundaries_lux_adm1"]
OUTPUTS = ["ds.cantons_lux_edit"]
PARAMS = ["p.canton_name_policy", "p.canton_name_source_field", "p.canton_name_strip_prefix",
          "p.canton_name_exceptions", "p.canton_name_field"]


def run(ctx) -> None:
    policy = ctx.param("p.canton_name_policy")
    if policy != "derive":
        raise NotImplementedError(f"canton name policy {policy!r}: only 'derive' is ported (D-001)")
    src_field = ctx.param("p.canton_name_source_field")
    prefix = ctx.param("p.canton_name_strip_prefix")
    exceptions = dict(ctx.param("p.canton_name_exceptions"))
    out_field = ctx.param("p.canton_name_field")

    adm1 = ctx.read_vector("ds.geoboundaries_lux_adm1")
    names = []
    for raw in adm1[src_field]:
        if raw is None:
            raise ValueError(f"{src_field} is NULL in an ADM1 feature; cannot derive a canton name")
        name = str(raw)
        if not name.startswith(prefix):
            raise ValueError(f"{src_field} {name!r} does not start with {prefix!r}; the naming rule does not apply")
        name = name[len(prefix):]
        names.append(exceptions.get(name, name))
    unused = set(exceptions) - {str(r)[len(prefix):] for r in adm1[src_field]}
    if unused:
        raise ValueError(f"exception table entries match no ADM1 feature: {sorted(unused)}")
    if len(set(names)) != len(names):
        raise ValueError(f"derived canton names are not unique: {names}")

    out = adm1.copy()
    out[out_field] = names
    ctx.log.info("derived %d canton names; %d via the exception table", len(names),
                 sum(1 for r in adm1[src_field] if str(r)[len(prefix):] in exceptions))
    ctx.write_vector("ds.cantons_lux_edit", out)
