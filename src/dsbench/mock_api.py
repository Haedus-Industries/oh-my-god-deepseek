"""Deterministic OpenAI streaming provider exercises the actual pinned SDK."""
import json
from aiohttp import web


def packet(event):
    return ("data: " + (event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)) + "\n\n").encode()


class MockAPI:
    def __init__(self, *, disconnect=False):
        self.calls = []
        self.disconnect = disconnect
        self.app = web.Application()
        self.app.router.add_post("/{path:.*}", self.respond)

    async def respond(self, request):
        body = await request.json()
        self.calls.append(body)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        turn = sum(m["role"] == "tool" for m in body["messages"])
        def chunk(delta, finish=None):
            return {"id": f"mock-{turn}", "object": "chat.completion.chunk", "created": 1791043200,
                    "model": "deepseek-flash", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        events = [chunk({"role": "assistant", "reasoning_content": "先检查，再修改，最后验证。"})]
        if turn < 3:
            shell = next(t["function"]["name"] for t in body["tools"] if t["function"]["name"] in ("bash", "pwsh"))
            if turn == 0:
                name = shell
                args = {"command": "export BENCH_PERSISTED=1; cat fixture.txt" if shell == "bash" else "$env:BENCH_PERSISTED='1'; Get-Content fixture.txt"}
            elif turn == 1:
                initial = next(m for m in body["messages"] if m["role"] == "user")
                text = initial["content"] if isinstance(initial["content"], str) else "".join(b.get("text", "") for b in initial["content"])
                path = text.split("FIXTURE_PATH=", 1)[1].split("\n", 1)[0]
                name, args = "str_replace_editor", {"command": "str_replace", "path": path, "old_str": "before", "new_str": "after"}
            else:
                name = shell
                args = {"command": "test \"$BENCH_PERSISTED\" = 1 && test \"$(cat fixture.txt)\" = after && printf verified > state-proof.txt && echo VERIFIED" if shell == "bash" else "if ($env:BENCH_PERSISTED -eq '1' -and (Get-Content fixture.txt) -eq 'after') {Set-Content -LiteralPath state-proof.txt -Value 'verified'; 'VERIFIED'} else {throw 'verification failed'}"}
            events += [chunk({"tool_calls": [{"index": 0, "id": f"tool-{turn}", "type": "function", "function": {"name": name, "arguments": ""}}]}),
                       chunk({"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)}}]})]
            stop = "tool_calls"
        else:
            events.append(chunk({"content": "模拟任务已完成。"}))
            stop = "stop"
        events.append(chunk({}, stop))
        for event in events:
            await response.write(packet(event))
        if self.disconnect:
            request.transport.close()
            return response
        await response.write(packet({"id": f"mock-{turn}", "object": "chat.completion.chunk", "model": "deepseek-flash", "choices": [],
                     "usage": {"prompt_tokens": 120, "prompt_cache_hit_tokens": 20, "prompt_cache_miss_tokens": 100, "completion_tokens": 40, "total_tokens": 160}}))
        await response.write(packet("[DONE]"))
        await response.write_eof()
        return response
