import copy
import json
import platform
from pathlib import Path

import pytest
from aiohttp import ClientSession

from dsbench.dryrun import MOCK_PRICES, dry_run, start_tcp
from dsbench.gateway import Gateway, SSEUsage, SecretFilter
from dsbench.ledger import Ledger
from dsbench.mock_api import MockAPI, packet
from dsbench.prompts import FRONTIER, NATIVE_IDENTITY, PRAYER, prompts, validate_request


def request(system=NATIVE_IDENTITY, user="task", shell="bash"):
    return {"model": "deepseek-flash", "thinking": {"type": "enabled"}, "reasoning_effort": "max", "max_tokens": 256000,
            "stream": True, "stream_options": {"include_usage": True}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "tools": [{"type": "function", "function": {"name": name, "parameters": {"type": "object"}}} for name in (shell, "str_replace_editor")]}


def test_four_prompt_roles_and_route():
    task = "identical official task and constraints"
    for arm in "BUSF":
        system, user = prompts(arm, task)
        assert user.endswith(task)
        assert (PRAYER in system) == (arm == "S")
        assert (PRAYER in user) == (arm == "U")
        assert (FRONTIER in system) == (arm == "F")
        validate_request(request(system, user), system, user)
    spoofed = request(FRONTIER)
    spoofed["model"] = "GPT-6 Astra"
    with pytest.raises(ValueError, match="route"):
        validate_request(spoofed, FRONTIER, task)


@pytest.mark.parametrize("change", [lambda b: b.update(reasoning_effort="low"), lambda b: b.update(max_tokens=100), lambda b: b["tools"].append({"function": {"name": "search"}})])
def test_wrong_wire_parameters_rejected_before_spending(change):
    body = request()
    change(body)
    with pytest.raises(ValueError):
        validate_request(body, NATIVE_IDENTITY, "task")


def test_stream_usage_split_at_every_byte_and_cache_no_double_count():
    usage = {"prompt_tokens": 120, "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 100, "completion_tokens": 40}
    parser = SSEUsage()
    data = (packet({"usage": usage}) + packet("[DONE]")).replace(b"\n", b"\r\n")
    for byte in data:
        parser.feed(bytes([byte]))
    assert parser.complete
    assert parser.normalized() == {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 40}


def test_credentials_split_across_chunks_never_enter_logs():
    secret = "paid-secret-123456"
    stream = SecretFilter(secret)
    data = b'upstream error: ' + secret.encode() + b' end'
    output = b"".join(stream.feed(bytes([byte])) for byte in data) + stream.feed(b"", final=True)
    assert secret.encode() not in output
    assert b"[REDACTED]" in output


async def test_disconnect_keeps_reservation_and_blocks_replay(tmp_path):
    mock = MockAPI(disconnect=True)
    provider, url = await start_tcp(mock.app)
    ledger = Ledger(tmp_path / "ledger.sqlite")
    gateway = Gateway(ledger, MOCK_PRICES, tmp_path, url, key="secret-paid-key")
    gateway.register("experiment-token", "B1", NATIVE_IDENTITY, "task")
    proxy, endpoint = await start_tcp(gateway.app)
    try:
        async with ClientSession() as session:
            for _ in range(2):
                try:
                    async with session.post(endpoint, json=request(), headers={"Authorization": "Bearer experiment-token"}) as response:
                        await response.read()
                except Exception:
                    pass
        assert len(mock.calls) == 1
        assert ledger.summary()["unknown_requests"] == 1
        assert ledger.summary()["held_cny"] > 0
        restored = Gateway(ledger, MOCK_PRICES, tmp_path, url)
        restored.register("new-token", "B1", NATIVE_IDENTITY, "task")
        assert "B1" in restored.blocked
        for file in tmp_path.rglob("*"):
            if file.is_file() and file.suffix in (".json", ".sse"):
                assert "secret-paid-key" not in file.read_text(encoding="utf-8")
    finally:
        await proxy.cleanup()
        await provider.cleanup()


async def test_native_sdk_four_arms_and_real_tools(tmp_path):
    result = await dry_run(tmp_path / "native")
    assert result["paid_api_calls"] == 0
    assert all(a["passed"] and a["persistent_state_verified"] and a["requests"] == 4 for a in result["arms"].values())
    assert result["accounting"]["requests"] == 16
    assert result["accounting"]["held_cny"] == 0
    baseline_sampling = None
    for arm in "BUSF":
        api = list((tmp_path / "native" / arm / "api").glob("*/request.json"))
        body = json.loads(api[0].read_text(encoding="utf-8"))
        sampling = {key: body[key] for key in ("temperature", "top_p", "seed", "frequency_penalty", "presence_penalty") if key in body}
        if arm == "B":
            baseline_sampling = sampling
        else:
            assert sampling == baseline_sampling
        assert body["model"] == "deepseek-flash"
        text = (tmp_path / "native" / arm / "agent/notifications.jsonl").read_text(encoding="utf-8")
        assert "先检查，再修改" in text and "VERIFIED" in text
        events = [json.loads(line) for line in text.splitlines()]
        contexts = [event["payload"]["event"]["data"] for event in events if event["method"] == "session.event" and event["payload"]["event"]["type"] == "request/context"]
        assert contexts and all(event["contextWindow"] == 1000000 for event in contexts)
