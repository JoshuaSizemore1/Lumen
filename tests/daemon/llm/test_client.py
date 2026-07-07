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
    await client.aclose()


async def test_unreachable_raises_llm_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = make_client(handler)
    with pytest.raises(LLMUnavailable):
        async for _ in client.chat([{"role": "user", "content": "hi"}]):
            pass
    await client.aclose()
