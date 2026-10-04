"""Single foreground coordinator with durable attempts and bounded concurrency."""
import asyncio
import json
import os
import secrets
import time
import tempfile
import platform
from pathlib import Path

from aiohttp import ClientSession, web

from .common import ROOT, now, read_json, settings, write_json
from .gateway import Gateway
from .ledger import Ledger
from .prompts import NATIVE_IDENTITY, prompts
from .schedule import initial_schedule


def classify(data, attempt_dir, broker_dir):
    rewards = (data.get("verifier_result") or {}).get("rewards") or {}
    budget = (broker_dir / "budget-stop.json").exists()
    unknown = any((broker_dir / "api").glob("*/unknown.json"))
    rejected = (broker_dir / "request-rejected.json").exists()
    worker_path = attempt_dir / "agent/worker.json"
    worker = read_json(worker_path) if worker_path.exists() else {}
    error = (data.get("exception_info") or {}).get("exception_type")
    full_patch = attempt_dir / "artifacts/git-before-snapshot.json"
    status = "scored"
    reason = worker.get("finish_reason", worker.get("status", error or "unknown"))
    if budget:
        status, reason = "budget_truncated", "budget_reservation_denied"
    elif unknown:
        status, reason = "infrastructure_error", "stream_or_usage_unknown"
    elif rejected:
        status, reason = "infrastructure_error", "outbound_request_rejected"
    elif not full_patch.exists() or not (attempt_dir / "artifacts/model.patch").exists():
        status, reason = "infrastructure_error", "snapshot_missing"
    elif rewards.get("reward") not in (0, 1) or error and error != "AgentTimeoutError":
        status, reason = "infrastructure_error", error or "verifier_missing"
    elif worker.get("status") == "error" or worker.get("finish_reason") == "error":
        status, reason = "infrastructure_error", "worker_protocol_error"
    elif not worker and error != "AgentTimeoutError":
        status, reason = "infrastructure_error", "worker_result_missing"
    elif worker.get("status") == "timeout" or error == "AgentTimeoutError":
        # The common agent limit is part of the evaluation conditions. A normal
        # official score at this limit still belongs to the binary primary pair.
        status, reason = "scored", "agent_timeout"
    return {"status": status, "passed": rewards.get("reward") == 1 if rewards else None,
            "rewards": rewards, "reason": reason, "truncation": "agent_time" if reason == "agent_timeout" else "budget" if budget else None,
            "timing": {k: data.get(k) for k in ("environment_setup", "agent_setup", "agent_execution", "verifier")}}


class State:
    def __init__(self, output):
        self.output = Path(output)
        self.path = self.output / "state.json"
        self.data = read_json(self.path) if self.path.exists() else {"protocol": settings()["protocol"], "started_at": now(), "schedule": initial_schedule(settings()["seed"]), "attempts": {}, "results": {}, "replacements": 0}
        self.save()

    def save(self):
        write_json(self.path, self.data)

    def start(self, slot):
        prior = [a for a in self.data["attempts"].values() if a["slot"] == slot["id"]]
        attempt = f"{slot['id']}-a{len(prior) + 1}"
        self.data["attempts"][attempt] = {"slot": slot["id"], "arm": slot["arm"], "phase": slot["phase"], "state": "running", "started_at": now()}
        self.save()
        return attempt

    def finish(self, attempt, result):
        entry = self.data["attempts"][attempt]
        entry.update(state="complete", finished_at=now(), result=result)
        self.data["results"][entry["slot"]] = {**result, "attempt": attempt, "arm": entry["arm"], "phase": entry["phase"]}
        self.save()


def may_replace(result, replacements):
    # Exploratory protocol never automatically purchases replacement attempts.
    return False


async def execute_schedule(state, run_slot):
    for slot in state.data["schedule"]:
        if slot["id"] not in state.data["results"]:
            await run_slot(slot)


async def smoke(gateway, endpoint, ledger, output):
    path = output / "smoke.json"
    if path.exists():
        result = read_json(path)
        if not result.get("passed"):
            raise RuntimeError("Previous smoke failed; inspect evidence, do not automatically replay")
        return result
    if any(r["trial"] == "connectivity" for r in ledger.rows()):
        raise RuntimeError("Interrupted smoke already reserved/charged; no automatic replay")
    token = secrets.token_hex(24)
    user = "只回复 OK。"
    gateway.register(token, "connectivity", NATIVE_IDENTITY, user, smoke=True)
    # Same protocol and max reasoning, with a deliberately short output limit.
    body = {"model": "deepseek-flash", "stream": True, "stream_options": {"include_usage": True},
            "thinking": {"type": "enabled"}, "reasoning_effort": "max", "max_tokens": 64,
            "messages": [{"role": "system", "content": NATIVE_IDENTITY}, {"role": "user", "content": user}]}
    async with ClientSession() as session:
        async with session.post(endpoint + "/chat/completions", json=body, headers={"Authorization": "Bearer " + token}) as response:
            data = await response.read()
            good = response.status == 200 and b"[DONE]" in data and ledger.summary("connectivity")["unknown_requests"] == 0
    result = {"passed": good, "at": now(), "purpose": "protocol/logging/accounting only; no ability calibration"}
    write_json(path, result)
    if not good:
        raise RuntimeError("API connectivity check failed; formal experiment not started")
    return result


async def run(output, smoke_only=False, dashboard_url=None):
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "amd64"):
        raise RuntimeError("正式运行需要 Linux x86_64 与 Docker；请先执行 bench doctor")
    from .doctor import doctor, fingerprint
    from .backend import run_trial
    from .pier_adapter import STOP_EVENTS
    from .pricing import refresh_prices
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Kernel lock automatically releases after a crash, unlike stale PID files.
    import fcntl
    with (output / "coordinator.lock").open("w") as guard:
        fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        status = await doctor(runtime=True)
        if not status["ready"]:
            raise RuntimeError("Cloud runtime preflight failed: " + json.dumps(status, ensure_ascii=False))
        readiness = read_json(ROOT / "resources/readiness.json")
        required = {"grader_controls", "native_linux_wire", "container_isolation", "container_sdk_gateway"}
        if readiness["fingerprint"] != fingerprint() or not all(c["ok"] for c in readiness["checks"].values()) or not required.issubset(readiness["checks"]):
            raise RuntimeError("Run doctor --runtime --containers with unchanged resources/code before paying")
        experiment_fingerprint = output / "fingerprint.json"
        if experiment_fingerprint.exists() and read_json(experiment_fingerprint) != fingerprint():
            raise RuntimeError("Cannot resume under a changed protocol")
        write_json(experiment_fingerprint, fingerprint())
        prices = await asyncio.to_thread(refresh_prices, output / "price")
        ledger = Ledger(output / "ledger.sqlite", settings()["api_budget_cny"], settings()["trial_budget_cny"])
        gateway = Gateway(ledger, prices, output / "gateway", settings()["upstream"], key=os.environ["DEEPSEEK_API_KEY"])
        app_runner = web.AppRunner(gateway.app)
        await app_runner.setup()
        socket_directory = tempfile.TemporaryDirectory(prefix="dsbench-gw-")
        socket_path = Path(socket_directory.name) / "gateway.sock"
        await web.UnixSite(app_runner, str(socket_path)).start()
        os.chmod(socket_path, 0o666)  # mounted socket only, no host file/API key access
        # Loopback TCP is used only by the evaluation-side smoke check.
        tcp_site = web.TCPSite(app_runner, "127.0.0.1", 0)
        await tcp_site.start()
        endpoint = f"http://127.0.0.1:{tcp_site._server.sockets[0].getsockname()[1]}"
        state = State(output)
        publisher = None
        try:
            if dashboard_url and not smoke_only:
                from .telemetry import Publisher
                publisher = await asyncio.to_thread(Publisher, output, dashboard_url, os.environ.get("DSBENCH_DASHBOARD_TOKEN", ""), live=True)
                publisher.start()
            await smoke(gateway, endpoint, ledger, output)
            if smoke_only:
                return {"smoke_passed": True, "accounting": ledger.summary()}
            if not state.data.get("execution_started_at"):
                state.data["execution_started_at"] = now()
                state.save()
            # Recover already saved Pier results without issuing another API call.
            for attempt, entry in list(state.data["attempts"].items()):
                if entry["state"] == "running":
                    result_path = output / "pier" / attempt / "result.json"
                    fallback_path = output / attempt / "pier-result.json"
                    path = fallback_path if fallback_path.exists() else result_path
                    data = read_json(path) if path.exists() else None
                    from .recovery import recover
                    expiry = __import__("datetime").datetime.fromisoformat(state.data["execution_started_at"]).timestamp() + settings()["execution_seconds"]
                    data = await recover(output, attempt, data, allow_grade=expiry - time.time() > 3600)
                    if data:
                        result = classify(data, output / "pier" / attempt, output / "gateway" / attempt)
                    else:
                        result = {"status": "infrastructure_error", "passed": None, "reason": "interrupted", "no_replay": True}
                    if ledger.summary(attempt)["unknown_requests"]:
                        result.update(status="infrastructure_error", reason="stream_or_usage_unknown", no_replay=True)
                    state.finish(attempt, result)
            deadline = __import__("datetime").datetime.fromisoformat(state.data["execution_started_at"]).timestamp() + settings()["execution_seconds"]
            semaphore = asyncio.Semaphore(settings()["concurrency"])

            async def slot_run(slot):
                async with semaphore:
                    existing = state.data["results"].get(slot["id"])
                    if existing and existing["status"] != "infrastructure_error":
                        return
                    while True:
                        remaining = deadline - time.time()
                        # Reserve setup/build, snapshot and official verification
                        # time instead of starting an agent just before the deadline.
                        if remaining <= 4500:
                            return
                        agent_seconds = min(10800, int(remaining - 4500))
                        if existing:
                            if not may_replace(existing, state.data["replacements"]):
                                return
                            state.data["replacements"] += 1
                            state.save()
                        attempt = state.start(slot)
                        if publisher:
                            publisher.changed()
                        system, user = prompts(slot["arm"], (ROOT / "resources/task/instruction.md").read_text(encoding="utf-8"))
                        token = secrets.token_hex(24)
                        if publisher:
                            publisher.observer.add_secret(token)
                        gateway.register(token, attempt, system, user)
                        STOP_EVENTS[token] = gateway.failures[attempt]
                        try:
                            data = await run_trial(output, attempt, arm=slot["arm"], token=token, socket_path=socket_path, agent_seconds=agent_seconds)
                            result = classify(data, output / "pier" / attempt, output / "gateway" / attempt)
                        except Exception as error:
                            result = {"status": "infrastructure_error", "passed": None, "reason": type(error).__name__}
                        finally:
                            STOP_EVENTS.pop(token, None)
                        result["accounting"] = ledger.summary(attempt)
                        state.finish(attempt, result)
                        if publisher:
                            publisher.changed()
                        existing = state.data["results"][slot["id"]]
                        if existing["status"] != "infrastructure_error":
                            return
            async with asyncio.timeout(max(0, deadline - time.time())):
                await execute_schedule(state, slot_run)
            expected = state.data["schedule"]
            state.data["end_reason"] = "completed" if all(s["id"] in state.data["results"] for s in expected) else "execution_time_limit"
        except TimeoutError:
            state.data["end_reason"] = "execution_time_limit"
            raise
        except BaseException:
            state.data["end_reason"] = "interrupted_or_error"
            raise
        finally:
            await app_runner.cleanup()
            socket_directory.cleanup()
            # Interrupted/unknown reservations remain held. Never automatically
            # refund or replay a request whose final usage is unavailable.
            state.data["finished_at"] = now()
            state.save()
            write_json(output / "summary.json", {"at": now(), **state.data, "accounting": ledger.summary()})
            from .report import report
            report(output)
            if publisher:
                await asyncio.to_thread(publisher.close, 30)
        return read_json(output / "summary.json")
