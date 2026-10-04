import random


def initial_schedule(seed=20261004):
    rng = random.Random(seed)
    out = []
    for repetition in (1, 2):
        arms = list("BUSF")
        rng.shuffle(arms)
        out.extend({"id": f"{arm}{repetition}", "arm": arm, "repetition": repetition, "phase": "primary"} for arm in arms)
    return out


def thirds(results):
    # Only normal scored primary results count; neither errors nor budget truncations
    # are substitutes for the pair specified in the protocol.
    if any(f"{arm}{n}" not in results for arm in "BUSF" for n in (1, 2)):
        return []
    out = []
    for arm in "BUSF":
        pair = [results[f"{arm}{n}"] for n in (1, 2)]
        if all(x.get("status") == "scored" for x in pair) and {x.get("passed") for x in pair} == {False, True}:
            out.append({"id": f"{arm}3", "arm": arm, "repetition": 3, "phase": "consistency"})
    return out
