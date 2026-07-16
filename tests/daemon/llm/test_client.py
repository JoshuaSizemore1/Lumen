import json

import httpx
import pytest

from lumen.daemon.llm.client import LLMUnavailable, OllamaClient


def ndjson(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def make_client(handler) -> OllamaClient:
    return OllamaClient(
        "http://test", "qwen3:4b", "10m", transport=httpx.MockTransport(handler)
    )


async def test_chat_streams_chunks_and_sends_keep_alive():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson(
                {"message": {"content": "Hel"}, "done": False},
                {"message": {"content": "lo"}, "done": False},
                {"message": {"content": ""}, "done": True},
            ),
        )

    client = make_client(handler)
    chunks = [c async for c in client.chat([{"role": "user", "content": "hi"}])]
    assert chunks == ["Hel", "lo"]
    assert seen["keep_alive"] == "10m"          # the power constraint, enforced per-request
    assert seen["model"] == "qwen3:4b"
    assert seen["stream"] is True
    assert seen["think"] is False
    await client.aclose()


async def test_warm_empty_preloads_weights_only():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    client = make_client(handler)
    await client.warm()
    assert seen["messages"] == []                 # weights only, no prefix
    assert seen["keep_alive"] == "10m"
    assert "num_predict" not in seen["options"]
    await client.aclose()


async def test_warm_with_prime_caches_prefix_via_one_token_gen():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    client = make_client(handler)
    prime = [{"role": "system", "content": "You are Lumen."},
             {"role": "user", "content": "hi"}]
    await client.warm(prime)
    assert seen["messages"] == prime              # prefix is evaluated + cached
    assert seen["options"]["num_predict"] == 1    # generate one token, no more
    assert seen["options"]["num_ctx"] == 8192     # same ctx as real chat, no reload
    assert seen["keep_alive"] == "10m"
    await client.aclose()


async def test_unload_sends_zero_keep_alive_and_empty_messages():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, content=ndjson({"done": True}))

    client = make_client(handler)
    await client.unload()
    assert seen["keep_alive"] == 0
    assert seen["messages"] == []
    assert "think" not in seen
    await client.aclose()


async def test_unreachable_raises_llm_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()


async def test_model_not_found_raises_with_pull_hint():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    client = make_client(handler)
    with pytest.raises(LLMUnavailable, match="ollama pull qwen3:4b"):
        async for _ in client.chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()


async def test_http_error_status_raises_llm_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()


async def test_unload_swallows_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("dropped")

    client = make_client(handler)
    await client.unload()  # must not raise
    await client.aclose()


async def test_chat_with_tools_executes_then_answers():
    turns = iter([
        # turn 1: model asks for a tool
        httpx.Response(200, json={"message": {"role": "assistant", "content": "",
            "tool_calls": [{"function": {"name": "list_directory", "arguments": {"path": "/n"}}}]},
            "done": True}),
        # turn 2: model answers using the tool result
        httpx.Response(200, json={"message": {"role": "assistant", "content": "You have a.txt and b.txt."},
            "done": True}),
    ])
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return next(turns)

    executed = []

    async def executor(name, args):
        executed.append((name, args))
        return "a.txt\nb.txt"

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "what files are in /n"}],
        tools=[{"type": "function", "function": {"name": "list_directory"}}],
        executor=executor)]
    assert executed == [("list_directory", {"path": "/n"})]
    assert {"tool_call": {"name": "list_directory", "arguments": {"path": "/n"}}} in events
    assert events[-1] == {"content": "You have a.txt and b.txt."}
    # tools attached and keep_alive still enforced on the tool turn
    assert sent[0]["tools"][0]["function"]["name"] == "list_directory"
    assert sent[0]["keep_alive"] == "10m"
    assert sent[0]["stream"] is False
    # tool result fed back as a role:tool message
    assert any(m.get("role") == "tool" and m.get("content") == "a.txt\nb.txt"
               for m in sent[1]["messages"])
    await client.aclose()


async def test_chat_requests_carry_num_ctx():
    # Ollama's default context is 4096 tokens — tool schemas + grounding +
    # history + a tool result overflowed it live (400 exceed_context_size_error,
    # 2026-07-12). Every chat path requests the same num_ctx: a per-path value
    # would make Ollama reload the model on each switch.
    from lumen.daemon.llm.client import NUM_CTX
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body.get("stream"):
            return httpx.Response(200, content=ndjson({"message": {"content": "hi"}, "done": True}))
        return httpx.Response(200, json={"message": {"content": "hi"}, "done": True})

    client = make_client(handler)
    [c async for c in client.chat([{"role": "user", "content": "hi"}])]
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "hi"}], tools=[], executor=None)]
    assert events == [{"content": "hi"}]
    assert all(b["options"] == {"num_ctx": NUM_CTX} for b in seen)
    await client.aclose()


async def test_chat_with_tools_direct_answer_no_tool():
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "42"}, "done": True})

    async def executor(name, args):
        raise AssertionError("should not be called")

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "2+2*20"}], tools=[], executor=executor)]
    assert events == [{"content": "42"}]
    await client.aclose()


async def test_chat_with_tools_respects_iteration_cap():
    def handler(request):  # always asks for another tool → would loop forever
        return httpx.Response(200, json={"message": {"content": "",
            "tool_calls": [{"function": {"name": "t", "arguments": {}}}]}, "done": True})

    async def executor(name, args):
        return "again"

    client = make_client(handler)
    events = [e async for e in client.chat_with_tools(
        [{"role": "user", "content": "x"}], tools=[{"type": "function", "function": {"name": "t"}}],
        executor=executor, max_iterations=2)]
    assert events[-1] == {"content": "", "capped": True}
    assert sum(1 for e in events if "tool_call" in e) == 2
    await client.aclose()


async def test_chat_with_tools_unreachable_raises():
    def handler(request):
        raise httpx.ConnectError("refused")

    async def executor(name, args):
        return ""

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat_with_tools([{"role": "user", "content": "hi"}], [], executor):
            pass
    await client.aclose()
