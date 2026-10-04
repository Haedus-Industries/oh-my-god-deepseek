import json
from pathlib import Path
import pytest

from dsbench.common import write_json
from dsbench.ledger import Ledger
from dsbench.telemetry import Observer, Publisher, Redactor, canonical, digest, pieces


def fixture(path):
    write_json(path / "state.json", {"started_at": "2026-10-04T00:00:00+00:00", "simulation": True,
        "attempts": {"B1-a1": {"slot": "B1", "arm": "B", "phase": "primary", "state": "running", "started_at": "2026-10-04T00:00:00+00:00"}}, "results": {}})
    api = path / "gateway/B1-a1/api/request-1"
    api.mkdir(parents=True)
    agent = path / "pier/B1-a1/agent"
    agent.mkdir(parents=True)
    return api, agent


def saved(observer, channel=None):
    with observer.db() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT payload FROM items ORDER BY seq") if channel is None or json.loads(row[0])["channel"] == channel]


def test_utf8_full_log_and_incomplete_sse(tmp_path):
    api, _ = fixture(tmp_path)
    observer = Observer(tmp_path, live=True)
    text = "汉字🙂" * 40000
    wire = 'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": text}}]}, ensure_ascii=False)
    (api / "response.sse").write_text(wire[:5000], encoding="utf-8")
    observer.scan()
    assert saved(observer, "reasoning") == []
    (api / "response.sse").write_text(wire + "\n\ndata: [DONE]\n", encoding="utf-8")
    observer.scan()
    assert "".join(row["content"] for row in saved(observer, "reasoning")) == text
    assert all(len(row["content"].encode()) <= 65536 for row in saved(observer))
    assert len(canonical(observer.batch()).encode()) <= 262144
    before = len(saved(observer))
    Observer(tmp_path).scan()
    assert len(saved(observer)) == before


def test_credential_redaction_raw_and_derived(tmp_path, monkeypatch):
    api, _ = fixture(tmp_path)
    secret = "sk-" + "veryprivate" * 10
    monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
    text = f"{secret} Bearer abc.def.secret {'f'*48} <script>window.bad=1</script>"
    (api / "response.sse").write_text("data: " + json.dumps({"choices": [{"delta": {"content": text}}]}) + "\n", encoding="utf-8")
    observer = Observer(tmp_path, token="upload-private-token")
    observer.scan()
    wire = canonical(saved(observer))
    assert secret not in wire and "abc.def.secret" not in wire and "f" * 48 not in wire
    # Plain model markup is preserved as research text; the browser must escape it.
    assert "<script>window.bad=1</script>" in wire


def test_tool_output_and_request_link_no_double_call(tmp_path):
    api, agent = fixture(tmp_path)
    (api / "response.sse").write_text('data: {"choices":[{"delta":{"tool_calls":[{"id":"call-1","index":0,"function":{"name":"bash","arguments":"{}"}}]}}]}\n', encoding="utf-8")
    output = "完整工具输出🙂\n" * 100000
    records = [
        {"payload": {"event": {"type": "tool/call", "time": 1791072000000, "data": {"callId": "call-1", "name": "bash", "arguments": "{}"}}}},
        {"payload": {"event": {"type": "tool/result", "time": 1791072001000, "data": {"message": {"source": {"callId": "call-1"}, "content": [{"text": output}]}}}}},
    ]
    (agent / "notifications.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n", encoding="utf-8")
    observer = Observer(tmp_path)
    for _ in range(4):
        observer.scan()
    assert len(saved(observer, "tool_call")) == 1
    complete = json.loads("".join(row["content"] for row in saved(observer, "tool_result")))
    assert complete["message"]["content"][0]["text"] == output
    assert all(row["request_id"] == "request-1" for row in saved(observer, "tool_result"))


def test_hidden_resources_never_enter_observer(tmp_path):
    fixture(tmp_path)
    for name in ("tests/test.patch", "solution/solution.patch", "pier/B1-a1/verifier/test-stdout.txt", "pier/B1-a1/verifier/reports/base.xml"):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("HIDDEN-SOURCE-DO-NOT-UPLOAD", encoding="utf-8")
    write_json(tmp_path / "pier/B1-a1/verifier/reward.json", {"reward": 0})
    observer = Observer(tmp_path)
    observer.scan()
    data = canonical(saved(observer))
    assert "HIDDEN-SOURCE" not in data
    assert any(row["stream"].endswith("reward.json") for row in saved(observer))


def test_reservation_and_unknown_are_disjoint(tmp_path):
    fixture(tmp_path)
    prices = {"input_miss": .3, "input_hit": .006, "output": 1.2, "usd_cny": 7.3}
    ledger = Ledger(tmp_path / "ledger.sqlite")
    first = ledger.reserve("B1-a1", prices, 10, 100)
    ledger.settle(first, {"input_tokens": 10, "output_tokens": 5}, prices)
    ledger.reserve("B1-a1", prices, 10, 100)
    third = ledger.reserve("U1-a1", prices, 10, 100)
    ledger.unknown(third)
    summary = Observer(tmp_path).accounting()
    assert summary["settled_cny"] == ledger.summary()["settled_cny"]
    assert summary["reserved_cny"] + summary["unknown_cny"] == ledger.summary()["held_cny"]
    assert summary["reserved_cny"] > 0 and summary["unknown_cny"] > 0
    assert summary["input_uncached"] == 10 and summary["output_tokens"] == 5


def test_lost_ack_restart_does_not_duplicate_or_replay(tmp_path, monkeypatch):
    fixture(tmp_path)
    monkeypatch.setattr("dsbench.telemetry.doctor_dashboard", lambda *_: {"experiment_id": "simulation"})
    received = {}
    lost = True
    def post(_self, route, data):
        nonlocal lost
        if route == "chunks":
            received.update({row["id"]: row for row in data["chunks"]})
            if lost:
                lost = False
                raise TimeoutError("acknowledgement was lost")
        return {"ok": True}
    monkeypatch.setattr(Publisher, "post", post)
    publisher = Publisher(tmp_path, "http://localhost:1234", "private-upload-token")
    with pytest.raises(TimeoutError):
        publisher.cycle()
    count = len(received)
    assert publisher.observer.pending() == count
    restarted = Publisher(tmp_path, "http://localhost:1234", "private-upload-token")
    restarted.cycle()
    assert restarted.observer.pending() == 0 and len(received) == count


def test_observer_does_not_mutate_experiment_inputs_or_results(tmp_path, monkeypatch):
    api, _ = fixture(tmp_path)
    write_json(api / "request.json", {"model": "deepseek-flash", "messages": []})
    originals = {p: p.read_bytes() for p in (tmp_path / "state.json", api / "request.json")}
    observer = Observer(tmp_path, live=True)
    for _ in range(3):
        observer.scan()
    assert all(p.read_bytes() == value for p, value in originals.items())


def test_experiment_scope_and_stable_identity(tmp_path):
    fixture(tmp_path)
    first = Observer(tmp_path, experiment_id="one")
    assert Observer(tmp_path, experiment_id="one").experiment_id == first.experiment_id
    with pytest.raises(ValueError, match="different experiment"):
        Observer(tmp_path, experiment_id="two")


def test_chunk_hashes_and_boundaries():
    text = "🙂汉字\n" * 100000
    blocks = list(pieces(text))
    assert "".join(blocks) == text
    assert all(len(block.encode()) <= 65536 for block in blocks)


def test_replayed_activity_uses_source_time_not_scan_time(tmp_path):
    api, agent = fixture(tmp_path)
    first = 'data: {"choices":[{"delta":{"reasoning_content":"先检查"}}]}\n'
    second = 'data: {"choices":[{"delta":{"content":"再回答"}}]}\n'
    (api / "response.sse").write_text(first, encoding="utf-8")
    (api / "response-timing.jsonl").write_text(json.dumps({"end": len(first.encode()), "at": "2026-10-04T00:00:01+00:00"})+"\n", encoding="utf-8")
    later = api.parent / "request-2"
    later.mkdir()
    (later / "response.sse").write_text(second, encoding="utf-8")
    (later / "response-timing.jsonl").write_text(json.dumps({"end": len(second.encode()), "at": "2026-10-04T00:00:03+00:00"})+"\n", encoding="utf-8")
    event={"payload":{"event":{"type":"tool/call","time":1791072002000,"data":{"callId":"call-1","name":"bash","arguments":"{}"}}}}
    (agent / "notifications.jsonl").write_text(json.dumps(event)+"\n",encoding="utf-8")
    observer=Observer(tmp_path)
    observer.scan()
    activity=[r for r in saved(observer) if r["channel"]!="artifact"]
    assert [r["channel"] for r in activity]==["reasoning","tool_call","answer"]
    assert activity[0]["source_at"]=="2026-10-04T00:00:01+00:00"
    assert activity[2]["source_at"]=="2026-10-04T00:00:03+00:00"
    assert all("event_id" in r and "part" in r for r in activity)
    before=len(saved(observer))
    Observer(tmp_path).scan()
    assert len(saved(observer))==before
