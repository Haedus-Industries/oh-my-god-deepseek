from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from dsbench.dryrun import MOCK_PRICES
from dsbench.ledger import BudgetExceeded, Ledger, cost
from dsbench.runner import State, execute_schedule, may_replace
from dsbench.schedule import initial_schedule, thirds


def test_concurrent_reservations_never_overspend(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", total=5, per_trial=5)
    barrier = Barrier(8)
    def reserve(n):
        barrier.wait()
        try:
            return ledger.reserve(str(n), MOCK_PRICES)
        except BudgetExceeded:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(reserve, range(8)))
    assert sum(bool(x) for x in ids) == 1
    assert ledger.summary()["held_cny"] <= 5


def test_settlement_releases_difference_and_is_durable(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    rid = ledger.reserve("B1", MOCK_PRICES)
    before = ledger.summary()["held_cny"]
    usage = {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 40}
    ledger.settle(rid, usage, MOCK_PRICES)
    restored = Ledger(tmp_path / "ledger.sqlite")
    assert restored.summary()["held_cny"] == 0
    assert 0 < restored.summary()["settled_cny"] < before
    assert abs(restored.summary()["settled_cny"] - float(cost(usage, MOCK_PRICES))) < 0.000001
    with pytest.raises(ValueError):
        ledger.settle(rid, usage, MOCK_PRICES)


def test_per_run_limit_includes_unknown_charges(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", total=160, per_trial=5)
    ledger.unknown(ledger.reserve("B1", MOCK_PRICES))
    with pytest.raises(BudgetExceeded):
        ledger.reserve("B1", MOCK_PRICES)
    assert ledger.reserve("U1", MOCK_PRICES)


def test_rounds_and_third_trigger_use_only_primary_pairs():
    schedule = initial_schedule()
    assert schedule == initial_schedule()
    assert {s["arm"] for s in schedule[:4]} == set("BUSF")
    assert {s["arm"] for s in schedule[4:]} == set("BUSF")
    results = {s["id"]: {"status": "scored", "passed": False} for s in schedule}
    results["U2"]["passed"] = True
    assert [s["id"] for s in thirds(results)] == ["U3"]
    results["S1"] = {"status": "infrastructure_error", "passed": False}
    results["S2"]["passed"] = True
    assert [s["id"] for s in thirds(results)] == ["U3"]
    del results["F2"]
    assert thirds(results) == []


def test_resume_preserves_completed_primary_and_attempt_identity(tmp_path):
    state = State(tmp_path)
    slot = state.data["schedule"][0]
    attempt = state.start(slot)
    result = {"status": "scored", "passed": True}
    state.finish(attempt, result)
    restored = State(tmp_path)
    assert restored.data["results"][slot["id"]]["attempt"] == attempt
    assert restored.data["attempts"][attempt]["state"] == "complete"
    assert restored.data["replacements"] == 0


async def test_full_adaptive_schedule_then_resume_makes_no_new_calls(tmp_path):
    state = State(tmp_path)
    calls = []
    async def simulate(slot):
        calls.append(slot)
        attempt = state.start(slot)
        passed = slot["arm"] == "S" or slot["arm"] == "U" and slot["repetition"] == 2 or slot["arm"] == "F" and slot["repetition"] == 1
        state.finish(attempt, {"status": "scored", "passed": passed})
    await execute_schedule(state, simulate)
    assert len(calls) == 10
    assert {s["arm"] for s in calls[:4]} == set("BUSF")
    assert {s["arm"] for s in calls[4:8]} == set("BUSF")
    assert {s["id"] for s in calls[8:]} == {"U3", "F3"}
    assert all(s["phase"] == "consistency" for s in calls[8:])
    await execute_schedule(State(tmp_path), simulate)
    assert len(calls) == 10


def test_at_most_two_infra_replacements_and_unknown_usage_never_replays():
    result = {"status": "infrastructure_error", "reason": "DockerError"}
    assert may_replace(result, 0) and may_replace(result, 1)
    assert not may_replace(result, 2)
    assert not may_replace({**result, "reason": "stream_or_usage_unknown"}, 0)
    assert not may_replace({**result, "no_replay": True}, 0)
    assert not may_replace({"status": "scored", "passed": False}, 0)
