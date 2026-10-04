import random


def initial_schedule(seed=20261004):
    rng = random.Random(seed)
    out = []
    for repetition in (1, 2):
        arms = list("BUSF")
        rng.shuffle(arms)
        out.extend({"id": f"{arm}{repetition}", "arm": arm, "repetition": repetition, "phase": "primary"} for arm in arms)
    return out
