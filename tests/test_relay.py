import asyncio
import http.server
import socket
import threading

from aiohttp import ClientSession, web

from dsbench.worker import Relay, UnixConnection


async def test_loopback_relay_streams_through_unix_socket(tmp_path, monkeypatch):
    path = tmp_path / "gateway.sock"
    payload = ('data: {"answer":"验证🙂"}\n\n' * 2000 + 'data: [DONE]\n\n').encode()
    received = []

    async def respond(request):
        received.append((request.path, await request.json(), request.headers.get("Authorization")))
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for offset in range(0, len(payload), 4096):
            await response.write(payload[offset:offset + 4096])
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_post("/chat/completions", respond)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.UnixSite(runner, str(path)).start()

    def connect(connection):
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.connect(str(path))

    monkeypatch.setattr(UnixConnection, "connect", connect)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Relay)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        async with ClientSession() as client:
            async with client.post(f"http://127.0.0.1:{server.server_port}/chat/completions",
                                   json={"stream": True}, headers={"Authorization": "Bearer synthetic-token"}) as response:
                assert response.status == 200
                assert response.headers["Content-Type"] == "text/event-stream"
                assert await response.read() == payload
        assert received == [("/chat/completions", {"stream": True}, "Bearer synthetic-token")]
    finally:
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(timeout=5)
        await runner.cleanup()
