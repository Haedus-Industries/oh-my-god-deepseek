"""Custom Pier import paths; no changes to the pinned upstream checkout."""
import json
import asyncio
import shlex
from pathlib import Path
from pathlib import Path

from pier.agents.base import BaseAgent
from pier.environments.docker.docker import DockerEnvironment

from .common import ROOT, pins, write_json
from .prompts import prompts

STOP_EVENTS = {}


class MinimalDocker(DockerEnvironment):
    _DOCKER_COMPOSE_NO_NETWORK_PATH = ROOT / "resources/no-network.yml"

    def __init__(self, *args, extra_mounts=(), **kwargs):
        self.extra_mounts = list(extra_mounts)
        self.is_verifier = "__verifier__" in kwargs.get("session_id", "")
        if self.is_verifier:
            kwargs["mounts_json"] = list(kwargs.get("mounts_json") or []) + [{
                "type": "bind", "source": str(Path(kwargs["environment_dir"]).resolve()),
                "target": "/opt/dsbench-tests-source", "read_only": True,
            }]
        super().__init__(*args, **kwargs)

    async def start(self, force_build):
        from .flat_image import require_container_space
        require_container_space()
        await super().start(force_build)
        if self.is_verifier:
            result = await self.exec("cp -a /opt/dsbench-tests-source /tests", timeout_sec=60)
            if result.return_code:
                raise RuntimeError("Cannot install original verifier files")

    async def stop(self, delete):
        # Upstream delete=True uses compose down --rmi all, which deletes the
        # shared local-only flattened image. Remove containers, retain the image.
        await super().stop(delete=False)

    def _default_log_mounts(self):
        return super()._default_log_mounts() + self.extra_mounts


class MinimalAgent(BaseAgent):
    def __init__(self, *args, arm="B", token=None, agent_seconds=10800, probe_instruction=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.arm, self.token = arm, token
        self.agent_seconds, self.probe_instruction = agent_seconds, probe_instruction

    @staticmethod
    def name():
        return "dsh-sdk-minimal-editor"

    def version(self):
        return "0.1.5rc1"

    async def setup(self, environment):
        result = await environment.exec("cd /app && git -c safe.directory=/app rev-parse HEAD && test ! -e /tests/test.patch && test ! -e /solution/solution.patch && command -v bash && python3 --version", timeout_sec=60)
        if result.return_code or result.stdout.splitlines()[0] != pins()["base_commit"]:
            raise RuntimeError("Wrong base commit, leaked grader/oracle, or unavailable tools")
        write_json(self.logs_dir / "setup.json", {"stdout": result.stdout, "stderr": result.stderr})
        if self.probe_instruction:
            await environment.exec("printf before > /app/fixture.txt", timeout_sec=30)

    async def run(self, instruction, environment, context):
        from .lifecycle import phase
        # logs_dir is <output>/pier/<attempt>/agent; this is evaluator-only.
        phase(self.logs_dir.parents[2], self.logs_dir.parent.name, "agent")
        system, user = prompts(self.arm, self.probe_instruction or instruction)
        input_path = self.logs_dir / "input.json"
        write_json(input_path, {"arm": self.arm, "system": system, "user": user, "agent_seconds": self.agent_seconds})
        await environment.upload_file(input_path, "/logs/agent/input.json")
        running = asyncio.create_task(environment.exec(
            f"timeout --kill-after=10s {self.agent_seconds}s python3 /opt/dsbench/worker.py --arm {shlex.quote(self.arm)} > /logs/agent/worker.stdout 2> /logs/agent/worker.stderr",
            env={"PYTHONPATH": "/opt/dsbench/site", "EXPERIMENT_TOKEN": self.token}, cwd="/app", timeout_sec=self.agent_seconds + 15))
        stopped = asyncio.create_task(STOP_EVENTS[self.token].wait()) if self.token in STOP_EVENTS else None
        try:
            if stopped:
                done, _ = await asyncio.wait((running, stopped), return_when=asyncio.FIRST_COMPLETED)
                if stopped in done and running not in done:
                    await environment.exec("pkill -TERM -f '^timeout --kill-after=10s [0-9]+s python3 /opt/dsbench/worker.py' || true", timeout_sec=20)
            result = await running
        except asyncio.CancelledError:
            # Pier normally removes the container in its cancellation cleanup.
            # Preserve this model's work first, without issuing another request.
            await asyncio.shield(environment.exec("pkill -TERM -f '^timeout --kill-after=10s [0-9]+s python3 /opt/dsbench/worker.py' || true; PYTHONPATH=/opt/dsbench/site python3 /opt/dsbench/worker.py --snapshot", timeout_sec=300))
            raise
        finally:
            if stopped:
                stopped.cancel()
        # A protocol/timeout error is classified by the coordinator after independent
        # verification. Don't throw here and accidentally bypass artifact collection.
        context.metadata = {"worker_exit_code": result.return_code, "arm": self.arm}
        phase(self.logs_dir.parents[2], self.logs_dir.parent.name, "snapshot")


class BaseControl(BaseAgent):
    @staticmethod
    def name():
        return "unmodified-base-control"

    def version(self):
        return "1"

    async def setup(self, environment):
        pass

    async def run(self, instruction, environment, context):
        result = await environment.exec("cd /app && git -c safe.directory=/app rev-parse HEAD && test ! -e /tests/test.patch && test ! -e /solution/solution.patch && command -v bash && python3 -c 'import socket; s=socket.socket(); s.settimeout(1); assert s.connect_ex((\"1.1.1.1\",443)) != 0'", timeout_sec=60)
        if result.return_code or result.stdout.splitlines()[0] != pins()["base_commit"]:
            raise RuntimeError("Container base/isolation preflight failed")
        write_json(self.logs_dir / "isolation.json", {"stdout": result.stdout, "exit_code": result.return_code})
