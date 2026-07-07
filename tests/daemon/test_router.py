from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.router import Router


class FakeLLM:
    def __init__(self, chunks=("a", "b"), fail=False):
        self._chunks = chunks
        self._fail = fail
        self.unloaded = False

    async def chat(self, messages):
        if self._fail:
            raise LLMUnavailable("down")
        for c in self._chunks:
            yield c

    async def unload(self):
        self.unloaded = True


async def collect(router, type_, payload):
    return [r async for r in router.handle(type_, payload)]


async def test_chat_streams_then_done():
    out = await collect(Router(FakeLLM()), "chat", {"message": "hi"})
    assert out == [{"chunk": "a"}, {"chunk": "b"}, {"done": True}]


async def test_chat_llm_down_yields_error():
    out = await collect(Router(FakeLLM(fail=True)), "chat", {"message": "hi"})
    assert len(out) == 1 and "down" in out[0]["error"]


async def test_sleep_unloads():
    llm = FakeLLM()
    out = await collect(Router(llm), "sleep", {})
    assert llm.unloaded and out == [{"done": True}]


async def test_unknown_type_errors():
    out = await collect(Router(FakeLLM()), "frobnicate", {})
    assert "unknown" in out[0]["error"]
