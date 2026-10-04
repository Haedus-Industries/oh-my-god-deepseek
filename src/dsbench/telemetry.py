"""Durable, read-only observer and best-effort public data uploader.

Only this evaluator-side module receives the dashboard token. No API replay.
"""
import hashlib
import asyncio
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from .common import ROOT, now, read_json, settings, sha, write_json

SCHEMA = 1
CHUNK_BYTES = 65536
BATCH_BYTES = 262144
CHANNELS = {"reasoning", "answer", "tool_call", "tool_result", "artifact", "verifier"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pieces(text):
    """A chunk boundary may split a record, but never an encoded character."""
    pending, size = [], 0
    for character in text:
        width = len(character.encode("utf-8"))
        if size + width > CHUNK_BYTES:
            yield "".join(pending)
            pending, size = [], 0
        pending.append(character)
        size += width
    if pending:
        yield "".join(pending)


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(x for x in secrets if x)

    def add(self, secret):
        if secret:
            self.secrets = (*self.secrets, secret)

    def __call__(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        # Old experiment tokens may no longer be available after a restart.
        text = re.sub(r"\b[a-f0-9]{48}\b", "[EXPERIMENT_TOKEN]", text)
        text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "[API_KEY]", text)
        text = re.sub(r"(?i)((?:DEEPSEEK_API_KEY|DSBENCH_DASHBOARD_TOKEN|EXPERIMENT_TOKEN|authorization)[\"'\s:=]+)[^\s\"',}]+", r"\1[REDACTED]", text)
        return text


class Observer:
    def __init__(self, output, token="", *, live=False, experiment_id=None):
        self.output = Path(output).resolve()
        self.directory = self.output / "dashboard"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "outbox.sqlite"
        self.live = live
        self.redact = Redactor((token, os.environ.get("DEEPSEEK_API_KEY")))
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors (path TEXT PRIMARY KEY,offset INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS items (seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS tool_requests (call_id TEXT PRIMARY KEY,request_id TEXT NOT NULL);
            """)
            if not db.execute("SELECT 1 FROM meta WHERE key='experiment_id'").fetchone():
                db.execute("INSERT INTO meta VALUES ('experiment_id',?)", (experiment_id or uuid.uuid4().hex,))
            self.experiment_id = db.execute("SELECT value FROM meta WHERE key='experiment_id'").fetchone()[0]
            if experiment_id and self.experiment_id != experiment_id:
                raise ValueError("Dashboard credential belongs to a different experiment")
        self.static = {}
        for name in ("pins.json", "resolved.json", "experiment.json", "prompts.json"):
            path = ROOT / "resources" / name
            if path.exists():
                self.static[f"resources/{name}"] = path.read_text(encoding="utf-8")
        try:
            import subprocess
            self.commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            self.commit = None
        self.fingerprint = digest(canonical({"resources": self.static, "source": {p.name: sha(p) for p in (ROOT / "src/dsbench").glob("*.py")}}))

    def db(self):
        return sqlite3.connect(self.database, timeout=2)

    def add_secret(self, token):
        self.redact.add(token)

    def append(self, db, text, *, stream, channel, attempt="", request_id=None, at=None, event_id=None):
        cleaned = self.redact(text)
        for index, part in enumerate(pieces(cleaned)):
            identity = digest(f"{self.experiment_id}:{stream}:{event_id or digest(text)}:{index}")
            value = {"schema_version": SCHEMA, "experiment_id": self.experiment_id,
                     "id": identity, "attempt": attempt, "slot": attempt.split("-a")[0],
                     "stream": stream, "channel": channel, "source_at": at or now(),
                     "event_id": event_id or digest(text), "part": index,
                     "request_id": request_id, "content": part, "sha256": digest(part),
                     "redacted": part != text if len(cleaned.encode('utf-8')) <= CHUNK_BYTES else cleaned != text}
            if getattr(self, "_pending_records", None) is not None:
                self._pending_records.append((identity, value))
            else:
                db.execute("INSERT OR IGNORE INTO items(id,payload) VALUES (?,?)", (identity, canonical(value)))

    def tail(self, db, path, attempt, kind, request_id=None):
        relative = path.relative_to(self.output).as_posix()
        row = db.execute("SELECT offset FROM cursors WHERE path=?", (relative,)).fetchone()
        offset = row[0] if row else 0
        with path.open("rb") as source:
            source.seek(offset)
            # Bound one scan; subsequent passes catch up without reading full logs.
            raw = source.read(1024 * 1024)
            if kind in ("sse", "notifications", "lifecycle") and len(raw) == 1024 * 1024 and b"\n" not in raw:
                raw += source.readline()
        terminal = False
        worker = self.output / "pier" / attempt / "agent/worker.json"
        if worker.exists():
            terminal = True
        if kind in ("sse", "notifications", "lifecycle"):
            boundary = raw.rfind(b"\n") + 1
            if terminal and len(raw) < 1024 * 1024:
                boundary = len(raw)
            raw = raw[:boundary]
        else:
            # Partial UTF-8 at the tail is retained for the next pass.
            while raw:
                try:
                    raw.decode("utf-8")
                    break
                except UnicodeDecodeError as error:
                    if error.end == len(raw):
                        raw = raw[:error.start]
                    else:
                        raise
        if not raw:
            return
        text = raw.decode("utf-8", errors="replace")
        self.append(db, text, stream=relative, channel="artifact", attempt=attempt, request_id=request_id, event_id=f"byte:{offset}")
        position = offset
        delta_text = {"reasoning": [], "answer": []}
        delta_at = {}
        timing = []
        timing_path = path.with_name("response-timing.jsonl")
        if kind == "sse" and timing_path.exists():
            for entry in timing_path.read_text(encoding="utf-8").splitlines():
                try:
                    timing.append(json.loads(entry))
                except ValueError:
                    pass  # Keep an incomplete streaming record for the next scan.
        timing_index = 0
        for line in text.splitlines(keepends=True):
            source_id = f"byte:{position}"
            line_start = position
            position += len(line.encode("utf-8"))
            try:
                if kind == "sse" and line.startswith("data:") and line[5:].strip() != "[DONE]":
                    event = json.loads(line[5:])
                    while timing_index < len(timing) and timing[timing_index]["end"] <= line_start:
                        timing_index += 1
                    stamp = timing[timing_index]["at"] if timing_index < len(timing) else datetime.fromtimestamp(event["created"], timezone.utc).isoformat() if event.get("created") else datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
                    for ci, choice in enumerate(event.get("choices", [])):
                        delta = choice.get("delta", {})
                        for key, channel in (("reasoning_content", "reasoning"), ("content", "answer")):
                            if delta.get(key):
                                delta_text[channel].append(delta[key])
                                delta_at.setdefault(channel, stamp)
                        for call in delta.get("tool_calls", []):
                            if call.get("id"):
                                db.execute("INSERT OR REPLACE INTO tool_requests VALUES (?,?)", (call["id"], request_id))
                elif kind == "notifications":
                    event = json.loads(line).get("payload", {}).get("event", {})
                    if event.get("type") in ("tool/call", "tool/result"):
                        data = event.get("data", {})
                        call_id = data.get("callId") or data.get("message", {}).get("source", {}).get("callId")
                        related = db.execute("SELECT request_id FROM tool_requests WHERE call_id=?", (call_id,)).fetchone()
                        stamp = datetime.fromtimestamp(event["time"] / 1000, timezone.utc).isoformat() if event.get("time") else None
                        self.append(db, canonical(data) + "\n", stream=f"{attempt}/tools", channel=event["type"].replace("/", "_"), attempt=attempt, request_id=related[0] if related else None, at=stamp, event_id=source_id)
                elif kind == "verifier":
                    self.append(db, line, stream=f"{attempt}/verifier", channel="verifier", attempt=attempt, event_id=source_id)
            except (ValueError, TypeError, KeyError):
                # Wire records can be non-JSON; the complete redacted raw text is saved.
                continue
        for channel, values in delta_text.items():
            if values:
                self.append(db, "".join(values), stream=f"{attempt}/{request_id}/{channel}", channel=channel, attempt=attempt, request_id=request_id, at=delta_at.get(channel), event_id=f"byte:{offset}")
        db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?)", (relative, offset + len(raw)))

    def scan(self):
        state_path = self.output / "state.json"
        state = read_json(state_path) if state_path.exists() else read_json(self.output / "summary.json") if (self.output / "summary.json").exists() else {}
        with self.db() as db:
            self._pending_records = []
            for name, text in self.static.items():
                self.append(db, text, stream=name, channel="artifact", event_id=digest(text))
            for attempt in state.get("attempts", {}):
                for path in sorted((self.output / "gateway" / attempt / "api").glob("*/response.sse"), key=lambda p: p.stat().st_mtime_ns):
                    self.tail(db, path, attempt, "sse", path.parent.name)
                    timing_path = path.with_name("response-timing.jsonl")
                    if timing_path.exists():
                        self.tail(db, timing_path, attempt, "text", path.parent.name)
                for path in sorted((self.output / "gateway" / attempt / "api").glob("*/*.json")):
                    if path.name in ("request.json", "usage.json", "unknown.json"):
                        text = path.read_text(encoding="utf-8")
                        self.append(db, text, stream=path.relative_to(self.output).as_posix(), channel="artifact", attempt=attempt, request_id=path.parent.name, event_id=digest(text))
                for suffix, kind in (("agent/notifications.jsonl", "notifications"), ("agent/worker.stdout", "text"), ("agent/worker.stderr", "text")):
                    path = self.output / "pier" / attempt / suffix
                    if path.exists():
                        self.tail(db, path, attempt, kind)
                # Evaluator-generated lifecycle and an explicit artifact whitelist.
                stage_path = self.output / "lifecycle" / f"{attempt}.jsonl"
                if stage_path.exists():
                    stages = [json.loads(line) for line in stage_path.read_text(encoding="utf-8").splitlines()]
                    state["attempts"][attempt]["stages"] = stages
                    state["attempts"][attempt]["stage"] = stages[-1]["phase"] if stages else "preparing"
                for suffix in ("agent/input.json", "agent/worker.json", "artifacts/git-before-snapshot.json", "artifacts/model.patch"):
                    path = self.output / "pier" / attempt / suffix
                    if path.exists():
                        text = path.read_text(encoding="utf-8")
                        self.append(db, text, stream=path.relative_to(self.output).as_posix() + "@" + digest(text)[:12], channel="artifact", attempt=attempt, event_id=digest(text))
                # Only the official reward JSON is published, not stdout/XML that
                # can quote hidden test source or the verifier's mounted paths.
                reward = self.output / "pier" / attempt / "verifier/reward.json"
                if reward.exists():
                    text = reward.read_text(encoding="utf-8")
                    self.append(db, text, stream=reward.relative_to(self.output).as_posix(), channel="artifact", attempt=attempt, event_id=digest(text))
            for name in ("research-summary.json", "results.csv", "report.md"):
                path = self.output / name
                if path.exists():
                    text = path.read_text(encoding="utf-8-sig")
                    self.append(db, text, stream=name + "@" + digest(text)[:12], channel="artifact", event_id=digest(text))
            price = self.output / "price/pricing.json"
            if price.exists():
                text = price.read_text(encoding="utf-8")
                self.append(db, text, stream="price/pricing.json@" + digest(text)[:12], channel="artifact", event_id=digest(text))
            version_row = db.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            version = int(version_row[0]) + 1 if version_row else 1
            db.execute("INSERT OR REPLACE INTO meta VALUES ('version',?)", (str(version),))
            # A replay can discover multiple files at once. Assign source seq by
            # observation time, rather than all SSE followed by all tool events.
            for identity, record in sorted(self._pending_records, key=lambda item: item[1]["source_at"]):
                db.execute("INSERT OR IGNORE INTO items(id,payload) VALUES (?,?)", (identity, canonical(record)))
            self._pending_records = None
        accounting = self.accounting()
        ended = not self.live or bool(state.get("end_reason"))
        snapshot = {"schema_version": SCHEMA, "experiment_id": self.experiment_id, "version": version,
                    "source_at": now(), "heartbeat_at": now() if self.live else state.get("finished_at"),
                    "status": state.get("end_reason", "running" if self.live else "archived"),
                    "finished": ended, "task": "effect-sse-httpapi-streaming", "model": "deepseek-flash",
                    "config": settings(), "state": state, "accounting": accounting,
                    "source_commit": self.commit, "fingerprint": self.fingerprint, "simulation": bool(state.get("simulation")),
                    "redacted": True}
        clean = json.loads(self.redact(canonical(snapshot)))
        write_json(self.directory / "snapshot.json", clean)
        return clean

    def accounting(self):
        path = self.output / "ledger.sqlite"
        if not path.exists():
            return {"settled_cny": 0, "reserved_cny": 0, "unknown_cny": 0, "remaining_cny": settings()["api_budget_cny"], "requests": 0, "input_uncached": 0, "input_cached": 0, "output_tokens": 0}
        # A read-only connection, not Ledger's BEGIN IMMEDIATE transaction.
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2) as db:
            rows = db.execute("SELECT trial,amount,state,usage FROM requests").fetchall()
        result = {"settled_cny": 0, "reserved_cny": 0, "unknown_cny": 0, "requests": len(rows), "input_uncached": 0, "input_cached": 0, "output_tokens": 0, "trials": {}}
        for trial, amount, state, usage in rows:
            category = "settled_cny" if state == "settled" else "reserved_cny" if state == "reserved" else "unknown_cny"
            result[category] += amount / 1_000_000
            subtotal = result["trials"].setdefault(trial, {"settled_cny": 0, "reserved_cny": 0, "unknown_cny": 0, "requests": 0})
            subtotal[category] += amount / 1_000_000
            subtotal["requests"] += 1
            value = json.loads(usage) if usage else {}
            result["input_uncached"] += value.get("input_tokens", 0) + value.get("cache_creation_input_tokens", 0)
            result["input_cached"] += value.get("cache_read_input_tokens", 0)
            result["output_tokens"] += value.get("output_tokens", 0)
        result["remaining_cny"] = settings()["api_budget_cny"] - sum(result[k] for k in ("settled_cny", "reserved_cny", "unknown_cny"))
        return result

    def batch(self):
        batch = []
        with self.db() as db:
            for seq, payload in db.execute("SELECT seq,payload FROM items WHERE sent=0 ORDER BY seq LIMIT 256"):
                value = json.loads(payload)
                value["seq"] = seq
                proposed = {"schema_version": SCHEMA, "experiment_id": self.experiment_id, "chunks": batch + [value]}
                if len(canonical(proposed).encode("utf-8")) > BATCH_BYTES:
                    break
                batch.append(value)
        return {"schema_version": SCHEMA, "experiment_id": self.experiment_id, "chunks": batch}

    def acknowledge(self, batch):
        with self.db() as db:
            db.executemany("UPDATE items SET sent=1 WHERE id=?", [(x["id"],) for x in batch["chunks"]])

    def pending(self):
        with self.db() as db:
            return db.execute("SELECT count(*) FROM items WHERE sent=0").fetchone()[0]


def validate_url(url):
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("Dashboard URL must be an origin without credentials or query")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
        raise ValueError("Dashboard requires HTTPS (except local mock)")
    return url.rstrip("/")


def ssl_verify():
    return os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True


class UploadError(Exception):
    pass


class Publisher:
    def __init__(self, output, url, token, *, live=False, experiment_id=None):
        if not token:
            raise ValueError("DSBENCH_DASHBOARD_TOKEN is required")
        self.url = validate_url(url)
        self.token = token
        identity = experiment_id or doctor_dashboard(self.url, token)["experiment_id"]
        self.observer = Observer(output, token, live=live, experiment_id=identity)
        self.session = requests.Session()
        if urlparse(self.url).hostname in ("localhost", "127.0.0.1"):
            self.session.trust_env = False
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.thread = None

    def post(self, route, data):
        # Every operation is idempotent. Never forward or replay a model request.
        async def send():
            import ssl
            from aiohttp import ClientSession, ClientTimeout
            verify = ssl_verify()
            context = ssl.create_default_context(cafile=verify if isinstance(verify, str) else None)
            async with ClientSession(trust_env=self.session.trust_env, timeout=ClientTimeout(total=5)) as session:
                async with session.post(self.url + "/api/v1/" + route, data=canonical(data).encode("utf-8"), headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, ssl=context, allow_redirects=False) as response:
                    if response.status != 200:
                        raise UploadError(f"dashboard_http_{response.status}")
                    result = await response.json()
                    if not result.get("ok"):
                        raise UploadError("invalid_acknowledgement")
                    return result
        return asyncio.run(send())

    def cycle(self, max_batches=8):
        snapshot = self.observer.scan()
        # Heartbeat/state remain current even when there is a large log backlog.
        # Snapshots deliberately don't reference any not-yet-uploaded attachment.
        self.post("snapshot", snapshot)
        for _ in range(max_batches):
            batch = self.observer.batch()
            if not batch["chunks"]:
                break
            self.post("chunks", batch)
            self.observer.acknowledge(batch)
        write_json(self.observer.directory / "upload-status.json", {"at": now(), "ok": True, "pending_chunks": self.observer.pending()})

    def loop(self):
        failures = 0
        while not self.stopping.is_set():
            try:
                self.cycle()
                failures = 0
            except Exception as error:
                failures += 1
                write_json(self.observer.directory / "upload-status.json", {"at": now(), "ok": False, "error_type": type(error).__name__, "pending_chunks": self.observer.pending()})
            delay = (10, 20, 40, 60)[min(max(failures - 1, 0), 3)] if failures else 10
            self.wake.wait(delay)
            self.wake.clear()

    def start(self):
        self.thread = threading.Thread(target=self.loop, name="dashboard-uploader", daemon=True)
        self.thread.start()
        return self

    def changed(self):
        self.wake.set()

    def close(self, seconds=30):
        self.observer.live = False
        self.stopping.set()
        self.wake.set()
        deadline = time.monotonic() + seconds
        if self.thread:
            self.thread.join(max(0, deadline - time.monotonic()))
        if not self.thread or not self.thread.is_alive():
            while time.monotonic() + 10 < deadline:
                try:
                    self.cycle(max_batches=1)
                except Exception:
                    break
                if not self.observer.pending():
                    break


def doctor_dashboard(url, token):
    url = validate_url(url)
    with requests.Session() as session:
        if urlparse(url).hostname in ("localhost", "127.0.0.1"):
            session.trust_env = False
        response = session.get(url + "/api/v1/health", timeout=(2, 3), verify=ssl_verify(), allow_redirects=False)
        response.raise_for_status()
        public = response.json()
        response = session.post(url + "/api/v1/check", json={"schema_version": SCHEMA}, headers={"Authorization": "Bearer " + token}, timeout=(2, 3), verify=ssl_verify(), allow_redirects=False)
        response.raise_for_status()
        private = response.json()
        return {"ok": public.get("schema_version") == SCHEMA and private.get("ok") is True, "schema_version": SCHEMA, "experiment_id": private.get("experiment_id"), "paid_api_calls": 0}


def sync_dashboard(output, url, token):
    publisher = Publisher(output, url, token)
    while True:
        publisher.cycle()
        if not publisher.observer.pending():
            break
    return {"experiment_id": publisher.observer.experiment_id, "pending_chunks": 0, "paid_api_calls": 0}
