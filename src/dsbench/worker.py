"""Runs inside the isolated task container. No evaluator resources or paid key."""
import argparse
import dataclasses
import http.client
import http.server
import json
import os
import signal
import socket
import threading
import subprocess
import tarfile
from contextlib import closing
from pathlib import Path

from deepseek_harness import DeepSeekHarness


EDITOR_PATCH = """- insert:
    - id: fs-local
      name: '@deepseek-ai/dsh-fs-local'
      config:
        cwd: !!js process.cwd()
    - id: tool-str-replace-editor
      name: '@deepseek-ai/dsh-tool-str-replace-editor'
"""


def run_native(cwd, log_dir, user, system, base_url, token, *, arm="B", seconds=10800, max_tokens=256000):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    patch = log_dir / "editor.patch.yml"
    patch.write_text(EDITOR_PATCH, encoding="utf-8")
    # B/U use the actual release default rather than injecting the presumed default.
    for name in list(os.environ):
        if name.startswith(("DSH_", "DEEPSEEK_")):
            os.environ.pop(name)
    env = {"DSH_CONTEXT_WINDOW": "1000000", "PKG_NATIVE_CACHE_PATH": str(log_dir / "native-cache")}
    if arm in ("S", "F"):
        env["DSH_SYSTEM_PROMPT"] = system
    started = __import__("time").monotonic()
    def expired(_signum, _frame):
        raise TimeoutError("Agent wall time limit reached")
    if hasattr(signal, "SIGALRM"):
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(seconds)
    try:
        with (log_dir / "notifications.jsonl").open("w", encoding="utf-8") as events:
            def save(event):
                events.write(json.dumps(dataclasses.asdict(event), ensure_ascii=False) + "\n")
                events.flush()
            with DeepSeekHarness(provider="deepseek-official", model="deepseek-flash", reasoning_effort="max",
                                 max_tokens=max_tokens, cwd=str(cwd), profile="sdk-minimal", patches=(str(patch),),
                                 dsh_home=str(log_dir / "home"), base_url=base_url, api_key=token,
                                 env=env, request_timeout_seconds=seconds) as harness:
                result = harness.run(user, session_id="experiment", on_notification=save)
            summary = dataclasses.asdict(result)
            summary["status"] = "finished"
    except Exception as error:
        summary = {"status": "timeout" if isinstance(error, TimeoutError) else "error", "error_type": type(error).__name__, "error": str(error).replace(token, "[EXPERIMENT_TOKEN]")}
    finally:
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)
    summary["seconds"] = __import__("time").monotonic() - started
    (log_dir / "worker.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/run/dsbench/gateway.sock")


class Relay(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        with closing(UnixConnection("localhost", timeout=10800)) as connection:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection", "transfer-encoding")}
            connection.request("POST", self.path, body, headers)
            upstream = connection.getresponse()
            self.send_response(upstream.status)
            self.send_header("Content-Type", upstream.getheader("Content-Type", "text/event-stream"))
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := upstream.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        self.close_connection = True

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm")
    parser.add_argument("--snapshot", action="store_true")
    parser.add_argument("--local-spec")
    parser.add_argument("--endpoint")
    args = parser.parse_args()
    if args.snapshot:
        snapshot()
        return
    if args.local_spec:
        spec = json.loads(Path(args.local_spec).read_text(encoding="utf-8"))
        run_native(spec["cwd"], spec["logs"], spec["user"], spec["system"], args.endpoint,
                   os.environ["EXPERIMENT_TOKEN"], arm=args.arm, seconds=120)
        return
    # Only this run's system/task are copied into this container.
    spec = json.loads(Path("/logs/agent/input.json").read_text(encoding="utf-8"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        run_native("/app", "/logs/agent", spec["user"], spec["system"], f"http://127.0.0.1:{server.server_port}",
                   os.environ["EXPERIMENT_TOKEN"], arm=args.arm, seconds=spec.get("agent_seconds", 10800))
    finally:
        server.shutdown()


def snapshot():
    out = Path("/logs/artifacts")
    out.mkdir(parents=True, exist_ok=True)
    def git(*args):
        return subprocess.run(["git", "-c", "safe.directory=/app", *args], cwd="/app", check=True, capture_output=True).stdout
    state = {"head": git("rev-parse", "HEAD").decode().strip(), "status": git("status", "--porcelain=v1").decode(),
             "branch": git("branch", "--show-current").decode().strip(),
             "model_commits": git("log", "--format=%H %an %s", "9245bc59ebfa688e8c92dd691296ee69d0815e59..HEAD").decode(),
             "snapshot_actor": "evaluation coordinator, after model execution"}
    # A cancellation capture can precede Pier's normal collect hook. Preserve
    # the first model-owned Git state when collecting the same stopped tree again.
    original_state = out / "git-before-snapshot.json"
    if not original_state.exists():
        original_state.write_text(json.dumps(state), encoding="utf-8")
    with tarfile.open(out / "worktree.tar.gz", "w:gz") as archive:
        def include(info):
            return None if any(p in (".git", "node_modules") for p in Path(info.name).parts) else info
        archive.add("/app", arcname="worktree", filter=include)
    git("add", "-A")
    git("-c", "user.name=dsbench snapshot", "-c", "user.email=snapshot@local", "commit", "--allow-empty", "--no-verify", "-m", "Evaluation snapshot after model stopped")
    patch = git("diff", "--binary", "9245bc59ebfa688e8c92dd691296ee69d0815e59", "HEAD")
    (out / "model.patch").write_bytes(patch)


if __name__ == "__main__":
    main()
