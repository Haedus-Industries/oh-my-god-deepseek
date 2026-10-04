"""Recover grading without repeating model inference or exposing evaluator files."""
import asyncio
import json
import os
import subprocess
import uuid
from pathlib import Path

from .backend import prepared_task
from .common import command, now, read_json, write_json


def stop_orphans(output, attempt):
    # Match Pier's exact project label AND this attempt's bind mounts. Never
    # prune Docker globally or touch a container from another experiment.
    project = attempt.lower()
    ids = command(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project]).splitlines()
    expected = (Path(output) / "pier" / attempt).resolve()
    for identifier in ids:
        detail = json.loads(command(["docker", "inspect", identifier]))[0]
        owned = any(Path(m.get("Source", "/")).resolve().is_relative_to(expected) for m in detail.get("Mounts", []))
        if not owned:
            raise RuntimeError("Refusing to stop a container outside this attempt")
        if detail["State"]["Running"]:
            command(["docker", "exec", identifier, "bash", "-c", "pkill -TERM -f '^timeout --kill-after=10s [0-9]+s python3 /opt/dsbench/worker.py' || true"], timeout=30)
            # Collect the final tree before stopping the orphan, with no API access.
            command(["docker", "exec", "-e", "PYTHONPATH=/opt/dsbench/site", identifier,
                     "python3", "/opt/dsbench/worker.py", "--snapshot"], timeout=300)
        command(["docker", "rm", "-f", identifier], timeout=60)


def grade_saved_patch(output, attempt):
    directory = Path(output) / "pier" / attempt
    patch = directory / "artifacts/model.patch"
    if not patch.exists():
        return None
    reward = directory / "verifier/reward.json"
    if reward.exists():
        return read_json(reward)
    task = prepared_task()
    image = "dsbench-resume-verifier:" + __import__("hashlib").sha256((task / "tests/Dockerfile").read_bytes()).hexdigest()[:16]
    command(["docker", "build", "--platform", "linux/amd64", "-t", image, task / "tests"], timeout=1800)
    name = "dsbench-resume-" + uuid.uuid4().hex[:12]
    logs = directory / "verifier"
    logs.mkdir(parents=True, exist_ok=True)
    started = now()
    args = ["docker", "run", "--rm", "--name", name, "--network", "none", "--cpus", "2", "--memory", "8g",
            "--sysctl", "net.ipv6.conf.all.disable_ipv6=0", "--mount", f"type=bind,source={patch.resolve()},target=/logs/artifacts/model.patch,readonly",
            "--mount", f"type=bind,source={logs.resolve()},target=/logs/verifier", "--entrypoint", "bash", image, "/tests/test.sh"]
    try:
        with (logs / "recovered-verifier.log").open("wb") as stream:
            result = subprocess.run(args, stdout=stream, stderr=subprocess.STDOUT, timeout=1800, check=False)
        write_json(logs / "recovery.json", {"started_at": started, "finished_at": now(), "exit_code": result.returncode, "model_calls": 0})
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        write_json(logs / "recovery.json", {"started_at": started, "finished_at": now(), "reason": "verifier_timeout", "model_calls": 0})
    return read_json(reward) if reward.exists() else None


async def recover(output, attempt, data, *, allow_grade):
    directory = Path(output) / "pier" / attempt
    await asyncio.to_thread(stop_orphans, output, attempt)
    if allow_grade and (directory / "artifacts/model.patch").exists():
        rewards = await asyncio.to_thread(grade_saved_patch, output, attempt)
        if rewards and rewards.get("reward") in (0, 1):
            data = dict(data or {})
            data["verifier_result"] = {"rewards": rewards}
            data["exception_info"] = None
            data["recovered_without_model_call"] = True
            write_json(Path(output) / attempt / "recovered-result.json", data)
    return data
