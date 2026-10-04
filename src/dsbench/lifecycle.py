"""Evaluator-owned lifecycle records; never injected into a model session."""
import json
from pathlib import Path
from .common import now


def phase(output, attempt, name):
    path = Path(output) / "lifecycle" / f"{attempt}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"phase": name, "at": now()}, ensure_ascii=False) + "\n")
    except OSError:
        # Observability must never turn a normally scored task into an error.
        pass
