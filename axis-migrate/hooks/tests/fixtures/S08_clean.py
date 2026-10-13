def run(ctx, df, params):
    return df.fillna(params["fill_pop"])
