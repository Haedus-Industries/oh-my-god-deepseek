import shutil
import asyncio
from pathlib import Path

from .common import ROOT, pins, read_json, write_json
from .resources import task_source


def prepared_task():
    lock = read_json(ROOT / "resources/resolved.json")
    source = task_source()
    target = ROOT / ".cache/prepared-task"
    shutil.copytree(source, target, dirs_exist_ok=True)
    original = (source / "task.toml").read_text(encoding="utf-8")
    collector = next(line for line in original.splitlines() if line.startswith("command = "))
    # Preserve the official instructions and hidden tests byte-for-byte. Only the
    # digest and collection hook change; the original official hook follows ours.
    import json
    snapshot_command = "PYTHONPATH=/opt/dsbench/site python3 /opt/dsbench/worker.py --snapshot && " + json.loads(collector.split(" = ", 1)[1])
    original = original.replace(collector, "command = " + json.dumps(snapshot_command))
    original = original.replace(pins()["image"], lock["image_reference"])
    original = original.replace("[verifier.environment]\n", "[verifier.environment]\ndocker_image = " + json.dumps(lock["image_reference"]) + "\n")
    (target / "task.toml").write_text(original, encoding="utf-8")
    for name in ("environment/Dockerfile", "tests/Dockerfile"):
        content = (source / name).read_text(encoding="utf-8").replace(pins()["image"], lock["image_reference"])
        (target / name).write_text(content, encoding="utf-8")
    return target


def mount(source, target):
    return {"type": "bind", "source": str(Path(source).resolve()), "target": target, "read_only": True}


async def run_trial(output, attempt, *, arm=None, token=None, socket_path=None, control=None, agent_seconds=10800, probe_instruction=None):
    from pier.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig, VerifierConfig
    from pier.trial.trial import Trial
    from .flat_image import require_container_space
    require_container_space()
    mounts = [mount(ROOT / "src/dsbench/worker.py", "/opt/dsbench/worker.py"), mount(ROOT / ".cache/worker-site", "/opt/dsbench/site")]
    if socket_path:
        mounts.append(mount(socket_path, "/run/dsbench/gateway.sock"))
    if control == "oracle":
        agent = AgentConfig(name="oracle")
    elif control == "base":
        agent = AgentConfig(import_path="dsbench.pier_adapter:BaseControl")
    else:
        agent = AgentConfig(import_path="dsbench.pier_adapter:MinimalAgent", model_name="deepseek-flash",
                            kwargs={"arm": arm, "token": token, "agent_seconds": agent_seconds, "probe_instruction": probe_instruction}, override_timeout_sec=agent_seconds + 30)
    config = TrialConfig(task=TaskConfig(path=prepared_task()), trial_name=attempt, trials_dir=Path(output) / "pier",
                         agent=agent, environment=EnvironmentConfig(import_path="dsbench.pier_adapter:MinimalDocker",
                         cpu_enforcement_policy="limit", memory_enforcement_policy="limit",
                         kwargs={"extra_mounts": mounts}), verifier=VerifierConfig(max_timeout_sec=1800, disable=bool(probe_instruction)))
    trial = await Trial.create(config)
    from .lifecycle import phase
    from pier.trial.hooks import TrialEvent
    async def verification_started(_event):
        phase(output, attempt, "verifier")
    trial.add_hook(TrialEvent.VERIFICATION_START, verification_started)
    phase(output, attempt, "preparing")
    from .flat_image import MIN_FREE_BYTES
    min_free = shutil.disk_usage(ROOT).free
    running = asyncio.create_task(trial.run())
    try:
        while not running.done():
            min_free = min(min_free, shutil.disk_usage(ROOT).free)
            if min_free < MIN_FREE_BYTES:
                raise RuntimeError("Stopped trial at the 3 GiB disk reserve")
            try:
                await asyncio.wait_for(asyncio.shield(running), timeout=2)
            except asyncio.TimeoutError:
                pass
        result = await running
    finally:
        if not running.done():
            running.cancel()
            try:
                await running
            except asyncio.CancelledError:
                pass
        write_json(Path(output) / attempt / "capacity.json", {"minimum_free_bytes": min_free})
    data = result.model_dump(mode="json")
    # Experiment tokens are short-lived, but even these need not enter persisted
    # launch configuration. Pier writes config/result during execution; sanitize.
    for file in (Path(output) / "pier" / attempt).rglob("*.json"):
        if token:
            text = file.read_text(encoding="utf-8")
            if token in text:
                file.write_text(text.replace(token, "[EXPERIMENT_TOKEN]"), encoding="utf-8")
    data["config"]["agent"]["kwargs"].pop("token", None)
    write_json(Path(output) / attempt / "pier-result.json", data)
    phase(output, attempt, "finished")
    return data
