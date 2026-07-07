"""Request router. Phase 1: pass-through to the LLM ("chat") plus "sleep".
Tool-call vs direct-answer classification arrives with the connector phases."""

from collections.abc import AsyncIterator

from lumen.daemon.llm.client import LLMUnavailable


class Router:
    def __init__(self, llm):
        self._llm = llm

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            messages = [{"role": "user", "content": payload.get("message", "")}]
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
        elif type_ == "sleep":
            await self._llm.unload()
            yield {"done": True}
        else:
            yield {"error": f"unknown request type: {type_}"}
