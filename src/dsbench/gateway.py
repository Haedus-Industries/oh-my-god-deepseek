"""Evaluation-side OpenAI-compatible SSE gateway; task containers get no paid key."""
import asyncio
import json
import os
import ssl
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from .common import now, settings, write_json
from .ledger import BudgetExceeded
from .prompts import validate_request
from .pricing import peak_rates, rate_at


class SSEUsage:
    def __init__(self):
        self.buffer = b""
        self.usage = {}
        self.complete = False
        self.started = False
        self.output_seen = False

    def feed(self, chunk):
        self.buffer = (self.buffer + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in self.buffer:
            packet, self.buffer = self.buffer.split(b"\n\n", 1)
            data = b"\n".join(line[5:].lstrip() for line in packet.split(b"\n") if line.startswith(b"data:"))
            if not data:
                continue
            if data == b"[DONE]":
                self.complete = self.started and self.output_seen
                continue
            try:
                event = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                continue
            final = event.get("usage")
            if final:
                self.started |= "prompt_tokens" in final
                self.output_seen |= "completion_tokens" in final
                self.usage.update(final)

    def normalized(self):
        hit = self.usage.get("prompt_cache_hit_tokens", self.usage.get("prompt_tokens_details", {}).get("cached_tokens", 0))
        miss = self.usage.get("prompt_cache_miss_tokens", self.usage.get("prompt_tokens", 0) - hit)
        if hit + miss != self.usage.get("prompt_tokens") or min(hit, miss, self.usage.get("completion_tokens", 0)) < 0:
            raise ValueError("Inconsistent provider token accounting")
        return {"input_tokens": miss, "cache_read_input_tokens": hit, "output_tokens": self.usage["completion_tokens"]}


def ssl_context():
    # Cloud CA settings must remain effective, including custom proxy trust.
    return ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE"))


class SecretFilter:
    """Redact even when a reflected credential is split across stream chunks."""
    def __init__(self, secret):
        self.secret = secret.encode() if secret else b""
        self.buffer = b""

    def feed(self, chunk, final=False):
        if not self.secret:
            return chunk
        self.buffer = (self.buffer + chunk).replace(self.secret, b"[REDACTED]")
        cut = len(self.buffer) if final else max(0, len(self.buffer) - len(self.secret) + 1)
        result, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return result


class Gateway:
    def __init__(self, ledger, prices, output_dir, upstream, key=None, mock=False, shell="bash"):
        self.ledger, self.prices = ledger, prices
        self.output_dir, self.upstream = Path(output_dir), upstream
        self.key = key
        self.mock = mock
        self.shell = shell
        self.tokens = {}
        self.blocked = set()
        self.failures = {}
        self.tool_schema = None
        self.app = web.Application(client_max_size=64 * 1024**2)
        self.app.router.add_post("/{path:.*}", self.forward)

    def register(self, token, trial, system, user, smoke=False):
        self.tokens[token] = (trial, system, user, smoke)
        self.failures[trial] = asyncio.Event()
        if any(row["trial"] == trial and row["state"] != "settled" for row in self.ledger.rows()):
            self.stop(trial)

    def stop(self, trial):
        self.blocked.add(trial)
        self.failures[trial].set()

    async def forward(self, request):
        auth = request.headers.get("x-api-key", "")
        if not auth:
            auth = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if auth not in self.tokens:
            raise web.HTTPUnauthorized()
        trial, system, user, smoke = self.tokens[auth]
        if trial in self.blocked:
            return web.json_response({"error": {"type": "budget_error", "message": "Trial stopped; no automatic retries"}}, status=402)
        body = await request.json()
        reserve_prices = peak_rates(self.prices) if "rates" in self.prices else self.prices
        start_prices = rate_at(self.prices) if "rates" in self.prices else self.prices
        try:
            validate_request(body, system, user, smoke=smoke, shell=self.shell)
            if not smoke:
                schema = body["tools"]
                if self.tool_schema is None:
                    self.tool_schema = schema
                elif schema != self.tool_schema:
                    raise ValueError("Tool schemas differ between arms")
        except (ValueError, KeyError) as error:
            self.stop(trial)
            write_json(self.output_dir / trial / "request-rejected.json", body)
            return web.json_response({"error": {"type": "invalid_request_error", "message": str(error)}}, status=400)
        try:
            request_id = self.ledger.reserve(trial, reserve_prices, body["max_tokens"], settings()["reservation_input_tokens"])
        except BudgetExceeded as error:
            self.stop(trial)
            write_json(self.output_dir / trial / "budget-stop.json", {"reason": str(error), "at": now()})
            return web.json_response({"error": {"type": "budget_error", "message": str(error)}}, status=402)
        directory = self.output_dir / trial / "api" / request_id
        write_json(directory / "request.json", body)
        usage = SSEUsage()
        secret_filter = SecretFilter(self.key)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        try:
            headers = {"Authorization": "Bearer " + (self.key or "mock")}
            async with ClientSession(trust_env=not self.mock, timeout=ClientTimeout(total=10800), connector=None) as session:
                async with session.post(self.upstream, json=body, headers=headers, allow_redirects=False,
                                        ssl=ssl_context() if self.upstream.startswith("https:") else False) as remote:
                    response.set_status(remote.status)
                    await response.prepare(request)
                    with (directory / "response.sse").open("wb") as stream:
                        async for chunk in remote.content.iter_any():
                            chunk = secret_filter.feed(chunk)
                            stream.write(chunk)
                            stream.flush()
                            # Evaluator-side timestamps preserve activity chronology.
                            # Telemetry failure must not alter forwarding or billing.
                            try:
                                with (directory / "response-timing.jsonl").open("a", encoding="utf-8") as timing:
                                    timing.write(json.dumps({"end": stream.tell(), "at": now()}) + "\n")
                            except OSError:
                                pass
                            usage.feed(chunk)
                            await response.write(chunk)
                        tail = secret_filter.feed(b"", final=True)
                        stream.write(tail)
                        usage.feed(tail)
                        await response.write(tail)
                    if remote.status != 200 or not usage.complete:
                        raise RuntimeError(f"Incomplete upstream response (HTTP {remote.status})")
            end_prices = rate_at(self.prices) if "rates" in self.prices else self.prices
            billed_prices = {**start_prices, **{key: max(start_prices[key], end_prices[key]) for key in ("input_hit", "input_miss", "output")}}
            self.ledger.settle(request_id, usage.normalized(), billed_prices)
            write_json(directory / "usage.json", {"usage": usage.usage, "normalized": usage.normalized(), "at": now(), "price": billed_prices})
            await response.write_eof()
        except BaseException as error:
            self.ledger.unknown(request_id)
            self.stop(trial)
            write_json(directory / "unknown.json", {"type": type(error).__name__, "at": now(), "partial_usage": usage.usage, "reservation_price": reserve_prices})
            if not response.prepared:
                return web.json_response({"error": {"type": "api_error", "message": "Forwarding failed; reservation retained"}}, status=502)
            if isinstance(error, asyncio.CancelledError):
                raise
            if request.transport:
                request.transport.close()
        return response
