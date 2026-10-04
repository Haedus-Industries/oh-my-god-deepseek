"""Regularized 1PL on cumulative public trials; selection only, not new outcomes."""
import json
from collections import Counter, defaultdict

from .common import ROOT, read_json, sha, write_json


def analyze():
    import numpy as np
    from scipy.optimize import brentq, minimize
    from scipy.special import expit
    path = ROOT / "resources/history/trials.json"
    raw = read_json(path)
    rows = raw["rows"] if isinstance(raw, dict) else raw
    records = [r for r in rows if r.get("source") == "deep-swe" and r.get("included_in_score") is True and isinstance(r.get("passed"), bool)]
    if not records:
        raise ValueError("Public data fields changed; inspect the snapshot before analysis")
    models = sorted({str(r["model"]) + "|" + str(r.get("reasoning_effort", "")) + "|" + str(r.get("config", "")) for r in records})
    tasks = sorted({r["task_name"] for r in records})
    model_index = {name: i for i, name in enumerate(models)}
    task_index = {name: i for i, name in enumerate(tasks)}
    m = np.array([model_index[str(r["model"]) + "|" + str(r.get("reasoning_effort", "")) + "|" + str(r.get("config", ""))] for r in records])
    t = np.array([task_index[r["task_name"]] for r in records])
    y = np.array([r["passed"] for r in records], dtype=float)
    def family(model):
        lower = model.lower()
        for name in ("gpt", "claude", "deepseek", "gemini", "qwen", "kimi", "glm", "grok", "minimax"):
            if name in lower:
                return name
        return lower.split("|", 1)[0].split("-", 1)[0]
    families = Counter(family(model) for model in models)
    estimates = {}
    target = "effect-sse-httpapi-streaming"
    target_name = next((name for name in tasks if name.split("/")[-1] == target), None)
    if target_name is None:
        raise ValueError("Selected task absent from cumulative data")
    def fit(weights, keep):
        mi, ti, yi, wi = m[keep], t[keep], y[keep], weights[keep]
        def loss(x):
            z = x[mi] - x[len(models) + ti]
            residual = wi * (expit(z) - yi)
            value = np.sum(wi * (np.logaddexp(0, z) - yi * z)) + 0.125 * np.sum(x*x)
            gradient = 0.25 * x
            np.add.at(gradient, mi, residual)
            np.add.at(gradient, len(models) + ti, -residual)
            return value, gradient
        optimized = minimize(loss, np.zeros(len(models) + len(tasks)), jac=True, method="L-BFGS-B")
        if not optimized.success:
            raise RuntimeError(optimized.message)
        difficulty = optimized.x[len(models):]
        prediction = {}
        for label, aggregate in (("standard_anchor", 0.705), ("miniswe_anchor", 0.742)):
            theta = brentq(lambda x: expit(x - difficulty).mean() - aggregate, -20, 20)
            prediction[label] = float(expit(theta - difficulty[task_index[target_name]]))
        return {"predicted_new_flash": prediction, "task_difficulty_logit": float(difficulty[task_index[target_name]]), "difficulty_rank": int(np.sum(difficulty > difficulty[task_index[target_name]]) + 1)}
    all_keep = np.ones(len(records), dtype=bool)
    weights = np.ones(len(records))
    estimates["all_configurations"] = fit(weights, all_keep)
    order = {"max": 6, "xhigh": 5, "high": 4, "medium": 3, "low": 2, "minimal": 1, "": 0, "None": 0}
    highest = {}
    for model in models:
        name, effort, _ = model.split("|", 2)
        if name not in highest or order.get(effort, 0) > order.get(highest[name].split("|", 2)[1], 0):
            highest[name] = model
    high_keep = np.array([models[i] in set(highest.values()) for i in m])
    estimates["highest_reasoning_per_model"] = fit(weights, high_keep)
    balanced = np.array([len(models) / (len(families) * families[family(models[i])]) for i in m])
    estimates["family_balanced"] = fit(balanced, all_keep)
    for excluded in ("gpt", "claude", "deepseek"):
        keep = np.array([family(models[i]) != excluded for i in m])
        if keep.any():
            estimates["leave_out_" + excluded] = fit(balanced, keep)
    task_rows = [r for r in records if r["task_name"] == target_name]
    group = defaultdict(lambda: [0, 0])
    for r in task_rows:
        key = str(r["model"]) + " | " + str(r.get("reasoning_effort")) + " | " + str(r.get("config"))
        group[key][0] += int(r["passed"])
        group[key][1] += 1
    result = {"snapshot_sha256": sha(path), "all_rows": len(rows), "scored_rows": len(records), "tasks": len(tasks), "configurations": len(models),
              "selected_task": target_name, "selected_passes": sum(r["passed"] for r in task_rows), "selected_trials": len(task_rows),
              "selected_model_config_counts": dict(group), "fits": estimates,
              "highest_reasoning_selected_passes": int(y[high_keep & (t == task_index[target_name])].sum()),
              "highest_reasoning_selected_trials": int((high_keep & (t == task_index[target_name])).sum()),
              "assumptions": "1PL with L2=0.25; aggregate anchors are cross-harness heuristics, not same-harness calibration. Family weighting/leave-out are sensitivity analyses. No confidence interval or strict capability ceiling is claimed."}
    write_json(ROOT / "resources/history/selection.json", result)
    return result
