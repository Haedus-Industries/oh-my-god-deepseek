from datetime import datetime, timezone

from dsbench.common import write_json
from dsbench.pricing import parse_prices, peak_rates, rate_at
from dsbench.report import report
from dsbench.ledger import Ledger


def test_pricing_table_flash_column_and_schedules():
    html = '<table><tr><td>MODEL</td><td>deepseek-flash(1)</td><td>pro</td></tr><tr><td>MODEL VERSION</td><td>DeepSeek-V4.1-Flash</td><td>other</td></tr>'
    for kind, label in (("hit", "1M INPUT TOKENS (CACHE HIT)"), ("miss", "1M INPUT TOKENS (CACHE MISS)"), ("out", "1M OUTPUT TOKENS")):
        off, peak = {"hit": (0.003, 0.006), "miss": (0.15, 0.3), "out": (0.6, 1.2)}[kind]
        html += f'<tr><td>{label}</td><td>OFF-PEAK</td><td>${off}</td><td>$9</td></tr><tr><td>PEAK</td><td>${peak}</td><td>$18</td></tr>'
    prices = parse_prices(html + '</table>')
    assert peak_rates(prices)["output"] == 1.2
    assert rate_at(prices, datetime(2026, 10, 4, 2, tzinfo=timezone.utc))["output"] == 0.6
    assert rate_at(prices, datetime(2026, 10, 8, 2, tzinfo=timezone.utc))["output"] == 1.2


def test_report_fixed_pairs_and_never_calls_api(tmp_path):
    attempts, results = {}, {}
    for arm in "BUSF":
        for n in (1, 2):
            identity = f"{arm}{n}"
            result = {"status": "scored", "passed": arm == "U"}
            attempts[identity] = {"slot": identity, "arm": arm, "phase": "primary", "state": "complete", "result": result}
            results[identity] = result
    results["B3"] = {"status": "scored", "passed": True}
    write_json(tmp_path / "summary.json", {"attempts": attempts, "results": results})
    Ledger(tmp_path / "ledger.sqlite")
    generated = report(tmp_path)
    assert generated["paid_api_calls"] == 0
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "|B|不通过|不通过|" in text
    assert "第三次一致性检查" not in text
    assert "候选信号" in text
