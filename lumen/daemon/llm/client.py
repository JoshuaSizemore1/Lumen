"""Ollama client. keep_alive rides on every request so the model idle-unloads
even if the service-level OLLAMA_KEEP_ALIVE override is lost. Never -1."""

import json
from collections.abc import AsyncIterator

import httpx


class LLMUnavailable(Exception):
    """Ollama is not reachable — the service is probably stopped."""


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        keep_alive: str,
        think: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url
        self.model = model
        self.keep_alive = keep_alive
        self.think = think
        # No read timeout: generation on CPU can legitimately be slow.
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=None, write=10.0, pool=5.0),
        )

    async def chat(self, messages: list[dict]) -> AsyncIterator[str]:
        body = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "keep_alive": self.keep_alive,
            "think": self.think,
        }
        try:
            async with self._http.stream("POST", "/api/chat", json=body) as resp:
                if resp.status_code == 404:
                    raise LLMUnavailable(
                        f"model '{self.model}' not found — run: ollama pull {self.model}"
                    )
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        return
        except httpx.HTTPError as e:
            raise LLMUnavailable(
                f"Ollama request failed ({type(e).__name__}) at {self.base_url} — is the "
                "service running? (systemctl --user status ollama)"
            ) from e

    async def unload(self) -> None:
        """Evict the model from RAM now (the 'sleep' command)."""
        try:
            await self._http.post(
                "/api/chat",
                json={"model": self.model, "messages": [], "keep_alive": 0},
            )
        except httpx.HTTPError:
            pass  # not running == nothing loaded == already "asleep"

    async def aclose(self) -> None:
        await self._http.aclose()
