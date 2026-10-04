"""Read current Flash prices from the official HTML table; never guess new rates."""
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

from .common import now, settings, sha, write_json


class Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def parse_prices(html):
    table = Table()
    start = html.index("<table")
    table.feed(html[start:html.index("</table>", start) + 8])
    models = next(row for row in table.rows if row and row[0] == "MODEL")
    versions = next(row for row in table.rows if row and row[0] == "MODEL VERSION")
    if not models[1].startswith("deepseek-flash") or versions[1] != "DeepSeek-V4.1-Flash":
        raise ValueError("Official deepseek-flash alias/version changed; experiment needs review")
    rates = {"peak": {}, "off_peak": {}}
    kind = None
    for row in table.rows:
        joined = " ".join(row)
        if "INPUT TOKENS" in joined:
            kind = "input_hit" if "CACHE HIT" in joined else "input_miss"
        elif "OUTPUT TOKENS" in joined:
            kind = "output"
        phase = "off_peak" if "OFF-PEAK" in row else "peak" if "PEAK" in row else None
        dollars = [cell for cell in row if re.fullmatch(r"\$[0-9.]+", cell)]
        if phase and kind and dollars:
            rates[phase][kind] = float(dollars[0][1:])
    if any(set(rates[p]) != {"input_hit", "input_miss", "output"} for p in rates):
        raise ValueError("Official pricing table format changed; cannot price requests")
    return {"rates": rates, "model_version": versions[1], "source": settings()["price_url"], "at": now(), "usd_cny": settings()["usd_cny"]}


def refresh_prices(output):
    from pathlib import Path
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(settings()["price_url"], timeout=30) as response:
        body = response.read()
    (output / "pricing.html").write_bytes(body)
    prices = parse_prices(body.decode("utf-8"))
    prices["sha256"] = sha(output / "pricing.html")
    write_json(output / "pricing.json", prices)
    return prices


def rate_at(prices, timestamp=None):
    stamp = timestamp or datetime.now(timezone.utc)
    # This verified window covers the present experiment. Outside it, holidays
    # are conservatively treated as weekdays; estimates are explicitly upper bounds.
    holiday = all(day.year == 2026 and day.month == 10 and 1 <= day.day <= 7
                  for day in (stamp, stamp + timedelta(hours=8)))
    peak = stamp.weekday() < 5 and (1 <= stamp.hour < 4 or 6 <= stamp.hour < 10) and not holiday
    phase = "peak" if peak else "off_peak"
    return {**prices["rates"][phase], "usd_cny": prices["usd_cny"], "phase": phase,
            "accounting": "upper_bound" if peak else "scheduled_rate", "at": stamp.isoformat()}


def peak_rates(prices):
    return {**{key: max(prices["rates"][p][key] for p in prices["rates"]) for key in ("input_hit", "input_miss", "output")}, "usd_cny": prices["usd_cny"], "phase": "reservation_max"}
