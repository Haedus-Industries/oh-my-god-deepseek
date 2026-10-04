"""Zero-paid-call deployment probe. Receives upload credentials only on stdin."""
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from dsbench.common import write_json
from dsbench.telemetry import Publisher, canonical, doctor_dashboard, sync_dashboard, ssl_verify

phase = "startup"

def main():
    global phase
    config = json.loads(sys.stdin.readline())
    url, token = config["url"], config["token"]
    output = Path(config.get("output", "outputs/dashboard-live-probe"))
    phase = "doctor"
    for retry in range(3):
        try:
            identity = doctor_dashboard(url, token)
            break
        except requests.RequestException:
            if retry == 2:
                raise
    instant = datetime.now(timezone.utc)
    at = lambda seconds: (instant + timedelta(seconds=seconds)).isoformat()
    attempts = {}
    for arm in "BUSF":
        attempt = f"{arm}1-a1"
        attempts[attempt] = {"slot": f"{arm}1", "arm": arm, "phase": "primary", "state": "complete",
                             "started_at": at(0), "finished_at": at(3), "result": {"status": "simulation", "passed": None, "reason": "software_protocol_probe"}}
        api = output / "gateway" / attempt / "api/request-1"
        api.mkdir(parents=True, exist_ok=True)
        agent = output / "pier" / attempt / "agent"
        agent.mkdir(parents=True, exist_ok=True)
        reasoning = "【模拟 API 推理片段🙂】" * 9000 + "\n"
        wire = "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": reasoning, "tool_calls": [{"id": "probe-call", "index": 0, "function": {"name": "bash", "arguments": "{}"}}]}}]}, ensure_ascii=False) + "\n\ndata: [DONE]\n"
        (api / "response.sse").write_text(wire, encoding="utf-8")
        (api / "response-timing.jsonl").write_text(canonical({"end": len(wire.encode()), "at": at(0)}) + "\n", encoding="utf-8")
        events = [
            {"type": "tool/call", "time": int((instant+timedelta(seconds=1)).timestamp()*1000), "data": {"callId": "probe-call", "name": "bash", "arguments": '{"command":"echo 模拟验证"}'}},
            {"type": "tool/result", "time": int((instant+timedelta(seconds=2)).timestamp()*1000), "data": {"message": {"source": {"callId": "probe-call"}, "content": [{"text": "模拟工具输出，不是正式实验。 <script>window.hacked=1</script>"}]}}},
        ]
        (agent / "notifications.jsonl").write_text("".join(canonical({"payload": {"event": e}})+"\n" for e in events), encoding="utf-8")
        # A known credential in a whitelisted local log verifies live redaction.
        (agent / "worker.stdout").write_text("Bearer " + token + "\n模拟日志\n", encoding="utf-8")
        write_json(agent / "worker.json", {"simulation": True, "paid_api_calls": 0})
    write_json(output / "state.json", {"simulation": True, "execution_started_at": at(0), "finished_at": at(3), "end_reason": "completed", "attempts": attempts, "results": {}, "replacements": 0})
    phase = "upload"
    publisher = Publisher(output, url, token, experiment_id=identity["experiment_id"])
    failures = 0
    while True:
        try:
            publisher.cycle(max_batches=2)
        except (TimeoutError, requests.RequestException):
            failures += 1
            if failures > 6:
                raise
            time.sleep(1)
            continue
        if not publisher.observer.pending():
            break
    experiment = identity["experiment_id"]
    with requests.Session() as session:
        phase = "public_read"
        session.verify = ssl_verify()
        def get(route):
            for retry in range(3):
                try:
                    response = session.get(url + "/api/v1/" + route, timeout=(2, 3))
                    response.raise_for_status()
                    return response
                except requests.RequestException:
                    if retry == 2:
                        raise
        assert get("health").json()["schema_version"] == 1
        denied = session.post(url + "/api/v1/check", json={"schema_version": 1}, headers={"Authorization": "Bearer invalid-token"}, timeout=(2, 3))
        assert denied.status_code == 401
        snapshot = get(f"snapshot?experiment={experiment}").json()["snapshot"]
        assert snapshot["simulation"] and not snapshot["state"]["results"]
        count = 0
        for attempt in attempts:
            cursor = 0
            rows = []
            while True:
                response = get(f"events?experiment={experiment}&attempt={attempt}&cursor={cursor}").json()
                rows.extend(response["records"])
                cursor = response["next_cursor"]
                if not response["more"]:
                    break
            assert token not in canonical(rows)
            count += len(rows)
            expected = "".join(r["content"] for r in sorted(rows, key=lambda r: r["seq"]) if r["channel"] == "reasoning")
            assert get(f"download?experiment={experiment}&attempt={attempt}&channel=reasoning").text == expected
            assert len(expected.encode()) > 65536 and "🙂" in expected
        artifacts = get(f"artifacts?experiment={experiment}").json()["artifacts"]
        for artifact in artifacts:
            body = session.get(url+"/api/v1/download", params={"experiment": experiment, "stream": artifact["stream"]}, timeout=(2, 3))
            body.raise_for_status()
            assert token not in body.text
        phase = "restore"
        restored = Publisher(output, url, token, experiment_id=experiment)
        restored.cycle()
        assert restored.observer.pending() == 0
        summary = {"ok": True, "url": url, "experiment_id": experiment, "simulation": True, "public_read": True,
                   "unauthorized_write_status": denied.status_code, "activity_records": count, "artifacts": len(artifacts),
                   "utf8_full_download": True, "credential_redaction": True, "resume_pending_chunks": 0,
                   "paid_api_calls": 0, "checked_at": datetime.now(timezone.utc).isoformat()}
        write_json(output / "live-probe.json", summary)
        print(canonical(summary))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(canonical({"ok": False, "phase": phase, "error_type": type(error).__name__, "paid_api_calls": 0}))
        sys.exit(1)
