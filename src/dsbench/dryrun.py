import asyncio
import platform
import secrets
import os
import sys
from pathlib import Path

from aiohttp import web

from .common import ROOT, now, read_json, write_json
from .gateway import Gateway
from .ledger import Ledger
from .mock_api import MockAPI
from .prompts import prompts

MOCK_PRICES = {"input_miss": 0.3, "input_hit": 0.006, "output": 1.2, "usd_cny": 7.3, "kind": "mock-accounting-only"}


async def start_tcp(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


async def dry_run(output):
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Choose a new dry-run directory to preserve previous evidence")
    output.mkdir(parents=True, exist_ok=True)
    mock = MockAPI()
    mock_runner, upstream = await start_tcp(mock.app)
    ledger = Ledger(output / "ledger.sqlite")
    shell = "pwsh" if platform.system() == "Windows" else "bash"
    gateway = Gateway(ledger, MOCK_PRICES, output, upstream, mock=True, shell=shell)
    gateway_runner, endpoint = await start_tcp(gateway.app)
    results = {}
    try:
        for arm in "BUSF":
            workspace = output / arm / "worktree"
            workspace.mkdir(parents=True)
            fixture = workspace / "fixture.txt"
            fixture.write_text("before", encoding="utf-8")
            task = f"Exercise the packaged Minimal shell and editor.\nFIXTURE_PATH={fixture.as_posix()}\n"
            system, user = prompts(arm, task)
            token = secrets.token_hex(24)
            gateway.register(token, arm, system, user)
            logs = output / arm / "agent"
            spec = logs / "local-spec.json"
            write_json(spec, {"cwd": workspace, "logs": logs, "system": system, "user": user})
            env = dict(os.environ, EXPERIMENT_TOKEN=token)
            env.pop("DEEPSEEK_API_KEY", None)
            process = await asyncio.create_subprocess_exec(sys.executable, "-X", "utf8", str(ROOT / "src/dsbench/worker.py"),
                       "--arm", arm, "--local-spec", str(spec), "--endpoint", endpoint, env=env,
                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=140)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                raise RuntimeError("Native dry-run exceeded fixed deadline")
            (logs / "stderr.txt").write_bytes(stderr)
            result = read_json(logs / "worker.json")
            changed = fixture.read_text(encoding="utf-8") == "after"
            proof = workspace / "state-proof.txt"
            persistent = proof.exists() and proof.read_text(encoding="utf-8").strip() == "verified"
            events = (output / arm / "agent/notifications.jsonl").read_text(encoding="utf-8")
            success = result["status"] == "finished" and changed and persistent
            requests = sorted((output / arm / "api").glob("*/request.json"))
            results[arm] = {"passed": success, "worker": result["status"], "edited": changed, "persistent_state_verified": persistent, "requests": len(requests)}
            if not success or len(requests) != 4:
                raise RuntimeError(f"Native SDK dry-run failed in arm {arm}; inspect {logs / 'worker.json'}")
            first = read_json(requests[0])
            actual_system = next(m["content"] for m in first["messages"] if m["role"] == "system")
            actual_user = next(m["content"] for m in first["messages"] if m["role"] == "user")
            write_json(output / arm / "model-visible.json", {"system": actual_system, "user": actual_user, "tools": first["tools"],
                "request_parameters": {k: v for k, v in first.items() if k not in ("messages", "tools")}})
    finally:
        await gateway_runner.cleanup()
        await mock_runner.cleanup()
        summary = {"kind": "simulation", "at": now(), "shell": shell, "cloud_ready": shell == "bash",
                   "arms": results, "accounting": ledger.summary(), "paid_api_calls": 0}
        write_json(output / "summary.json", summary)
    return summary
