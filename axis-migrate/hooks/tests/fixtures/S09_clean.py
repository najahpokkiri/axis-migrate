import numpy as np


def run(ctx, params):
    rng = np.random.default_rng(params["seed"])
    return rng.uniform(0, 1, 10)
