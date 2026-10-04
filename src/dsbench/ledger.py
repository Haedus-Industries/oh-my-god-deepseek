"""Durable shared reservations. Integer micro-CNY avoids floating point overspend."""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, ROUND_CEILING

from .common import now

SCALE = 1_000_000


class BudgetExceeded(Exception):
    pass


def micros(value):
    return int((Decimal(str(value)) * SCALE).to_integral_value(rounding=ROUND_CEILING))


def cost(usage, prices):
    # Anthropic input_tokens excludes both cache categories. Cache creation is charged
    # at the uncached rate; charging it as a hit would understate the hard budget.
    uncached = usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
    hit = usage.get("cache_read_input_tokens", 0)
    output = usage.get("output_tokens", 0)
    return (Decimal(uncached) * Decimal(str(prices["input_miss"]))
            + Decimal(hit) * Decimal(str(prices["input_hit"]))
            + Decimal(output) * Decimal(str(prices["output"]))) / 1_000_000 * Decimal(str(prices["usd_cny"]))


class Ledger:
    def __init__(self, path, total=160, per_trial=20):
        self.path = str(path)
        self.total = micros(total)
        self.per_trial = micros(per_trial)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, trial TEXT, amount INTEGER, state TEXT, usage TEXT, created TEXT)")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        finally:
            db.close()

    def reserve(self, trial, prices, output=256000, context=1000000):
        maximum = micros(cost({"input_tokens": context, "output_tokens": output}, prices))
        with self.transaction() as db:
            total = db.execute("SELECT COALESCE(SUM(amount),0) FROM requests").fetchone()[0]
            subtotal = db.execute("SELECT COALESCE(SUM(amount),0) FROM requests WHERE trial=?", (trial,)).fetchone()[0]
            if total + maximum > self.total or subtotal + maximum > self.per_trial:
                raise BudgetExceeded("Insufficient budget for worst-case reservation")
            request_id = uuid.uuid4().hex
            db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?)", (request_id, trial, maximum, "reserved", None, now()))
        return request_id

    def settle(self, request_id, usage, prices):
        amount = micros(cost(usage, prices))
        with self.transaction() as db:
            row = db.execute("SELECT amount,state FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row[1] not in ("reserved", "unknown"):
                raise ValueError("Request cannot be settled twice")
            if amount > row[0]:
                raise ValueError("Usage exceeds reserved bound; retain reservation and stop experiment")
            db.execute("UPDATE requests SET amount=?,state='settled',usage=? WHERE id=?", (amount, json.dumps(usage), request_id))

    def unknown(self, request_id):
        with self.transaction() as db:
            db.execute("UPDATE requests SET state='unknown' WHERE id=? AND state='reserved'", (request_id,))

    def rows(self):
        with self.transaction() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute("SELECT * FROM requests ORDER BY created")]

    def summary(self, trial=None):
        rows = [r for r in self.rows() if trial is None or r["trial"] == trial]
        return {"settled_cny": sum(r["amount"] for r in rows if r["state"] == "settled") / SCALE,
                "held_cny": sum(r["amount"] for r in rows if r["state"] != "settled") / SCALE,
                "unknown_requests": sum(r["state"] != "settled" for r in rows), "requests": len(rows)}
