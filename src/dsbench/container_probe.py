"""No-cost end-to-end Docker/Pier/Unix relay/SDK/gateway probe in Cloud."""
import os
import secrets
import tempfile
import tarfile
from pathlib import Path

from aiohttp import web

from .backend import run_trial
from .common import read_json, write_json
from .dryrun import MOCK_PRICES, start_tcp
from .gateway import Gateway
from .ledger import Ledger
from .mock_api import MockAPI
from .pier_adapter import STOP_EVENTS
from .prompts import prompts


async def container_probe(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    mock = MockAPI()
    provider, url = await start_tcp(mock.app)
    ledger = Ledger(output / "ledger.sqlite")
    gateway = Gateway(ledger, MOCK_PRICES, output / "gateway", url, mock=True)
    runner = web.AppRunner(gateway.app)
    await runner.setup()
    socket_directory = tempfile.TemporaryDirectory(prefix="dsbench-probe-")
    socket = Path(socket_directory.name) / "probe.sock"
    await web.UnixSite(runner, str(socket)).start()
    os.chmod(socket, 0o666)
    results = {}
    try:
        for arm in "B":
            task = "Exercise native Minimal tools in the actual isolated container.\nFIXTURE_PATH=/app/fixture.txt\n"
            system, user = prompts(arm, task)
            token = secrets.token_hex(24)
            gateway.register(token, arm, system, user)
            STOP_EVENTS[token] = gateway.failures[arm]
            try:
                data = await run_trial(output, arm, arm=arm, token=token, socket_path=socket, agent_seconds=120, probe_instruction=task)
            finally:
                STOP_EVENTS.pop(token, None)
            worker = read_json(output / "pier" / arm / "agent/worker.json")
            events = (output / "pier" / arm / "agent/notifications.jsonl").read_text(encoding="utf-8")
            with tarfile.open(output / "pier" / arm / "artifacts/worktree.tar.gz") as archive:
                persistent = "worktree/state-proof.txt" in archive.getnames() and archive.extractfile("worktree/state-proof.txt").read().decode("utf-8").strip() == "verified"
            ok = not data.get("exception_info") and worker.get("final_response") == "模拟任务已完成。" and persistent and ledger.summary(arm)["requests"] == 4
            results[arm] = {"ok": ok, "persistent_state_verified": persistent, "worker_status": worker.get("status"), "finish_reason": worker.get("finish_reason")}
    finally:
        await runner.cleanup()
        socket_directory.cleanup()
        await provider.cleanup()
    summary = {"arms": results, "ok": len(results) == 1 and all(r["ok"] for r in results.values()), "paid_api_calls": 0}
    write_json(output / "summary.json", summary)
    return summary
