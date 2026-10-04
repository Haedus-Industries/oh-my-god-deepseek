import json
from pathlib import Path
import subprocess

import pytest

from dsbench.common import ROOT, pins, read_json, write_json
from dsbench.resources import task_source, verify_resources
from dsbench.runner import classify


def test_official_resources_match_canonical_git_blobs():
    if not (ROOT / "resources/resolved.json").exists():
        pytest.skip("Run bench prepare to fetch pinned public resources")
    verify_resources()
    repository = ROOT / ".cache/upstream/deep-swe"
    for relative in ("instruction.md", "tests/test.sh", "tests/grader.py", "solution/solution.patch"):
        blob = subprocess.check_output(["git", "-c", "safe.directory=" + repository.resolve().as_posix(), "-C", str(repository),
                                       "show", pins()["deep-swe"]["commit"] + ":tasks/" + pins()["task"] + "/" + relative])
        assert (task_source() / relative).read_bytes() == blob
    assert (ROOT / "resources/task/instruction.md").read_bytes() == (task_source() / "instruction.md").read_bytes()


def fixture(tmp_path, worker):
    trial = tmp_path / "trial"
    broker = tmp_path / "broker"
    write_json(trial / "agent/worker.json", worker)
    write_json(trial / "artifacts/git-before-snapshot.json", {"head": "base", "status": ""})
    (trial / "artifacts/model.patch").touch()
    broker.mkdir()
    return trial, broker


def test_agent_time_limit_is_scored_by_verifier_and_not_an_infra_replacement(tmp_path):
    trial, broker = fixture(tmp_path, {"status": "timeout"})
    result = classify({"verifier_result": {"rewards": {"reward": 0}}, "exception_info": None}, trial, broker)
    assert result["status"] == "scored" and result["passed"] is False
    assert result["truncation"] == "agent_time"


def test_budget_truncation_keeps_final_grade_but_is_not_normal_failure(tmp_path):
    trial, broker = fixture(tmp_path, {"status": "error"})
    write_json(broker / "budget-stop.json", {"reason": "insufficient reservation"})
    result = classify({"verifier_result": {"rewards": {"reward": 0}}}, trial, broker)
    assert result["status"] == "budget_truncated" and result["passed"] is False


def test_missing_snapshot_is_infrastructure_error_even_if_grader_returns_zero(tmp_path):
    trial, broker = fixture(tmp_path, {"status": "finished"})
    (trial / "artifacts/git-before-snapshot.json").unlink()
    result = classify({"verifier_result": {"rewards": {"reward": 0}}}, trial, broker)
    assert result["status"] == "infrastructure_error"
