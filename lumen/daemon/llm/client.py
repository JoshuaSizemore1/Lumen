"""Ollama client. keep_alive rides on every request so the model idle-unloads
even if the service-level OLLAMA_KEEP_ALIVE override is lost. Never -1."""

import json
from collections.abc import AsyncIterator, Awaitable, Callable

import httpx


class LLMUnavailable(Exception):
    """Ollama is not reachable — the service is probably stopped."""


# Ollama's default context is 4096 tokens; the tool loop (13 tool schemas +
# identity/grounding + history + tool results) overflowed it live
# (exceed_context_size_error, 2026-07-12). One constant for every chat path:
# Ollama reloads the model whenever num_ctx changes between requests, so a
# smaller plain-chat value would thrash the load. KV-cache RAM at 8192 is a
# few hundred MB for the 4B model — acceptable; idle-unload still governs.
NUM_CTX = 8192


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
            "options": {"num_ctx": NUM_CTX},
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

    async def _post_chat(self, messages: list[dict], tools: list[dict], model: str | None) -> dict:
        body = {
            "model": model or self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": self.keep_alive,
            "think": self.think,
            "options": {"num_ctx": NUM_CTX},
        }
        try:
            resp = await self._http.post("/api/chat", json=body)
            if resp.status_code == 404:
                raise LLMUnavailable(
                    f"model '{model or self.model}' not found — run: ollama pull {model or self.model}"
                )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise LLMUnavailable(
                f"Ollama request failed ({type(e).__name__}) at {self.base_url} — is the "
                "service running? (systemctl --user status ollama)"
            ) from e

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        executor: Callable[[str, dict], Awaitable[str]],
        *,
        model: str | None = None,
        max_iterations: int = 4,
    ) -> AsyncIterator[dict]:
        """Agentic loop: non-streamed turns detect tool_calls, execute them via
        `executor`, feed results back, and repeat until the model answers (or the cap)."""
        convo = list(messages)
        for _ in range(max_iterations):
            data = await self._post_chat(convo, tools, model)
            msg = data.get("message", {})
            calls = msg.get("tool_calls") or []
            if not calls:
                yield {"content": msg.get("content", "")}
                return
            convo.append(msg)
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                yield {"tool_call": {"name": name, "arguments": args}}
                result_text = await executor(name, args)
                convo.append({"role": "tool", "content": result_text, "tool_name": name})
        yield {"content": "", "capped": True}

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """Embeddings for the notes index. keep_alive rides along — the
        embedding model idle-unloads like everything else."""
        body = {"model": model, "input": texts, "keep_alive": self.keep_alive}
        try:
            resp = await self._http.post("/api/embed", json=body)
            if resp.status_code == 404:
                raise LLMUnavailable(
                    f"model '{model}' not found — run: ollama pull {model}")
            resp.raise_for_status()
            return resp.json().get("embeddings", [])
        except httpx.HTTPError as e:
            raise LLMUnavailable(
                f"Ollama request failed ({type(e).__name__}) at {self.base_url} — is the "
                "service running? (systemctl --user status ollama)"
            ) from e

    async def warm(self, prime: list[dict] | None = None) -> None:
        """Preload the model into RAM (the inverse of unload) so the next real
        request skips the cold load. Fire-and-forget: if Ollama is down or busy,
        the real request will surface the error.

        With `prime` (the stable identity/memory prefix), run a 1-token
        generation so Ollama caches that prefix's KV. An empty preload only
        loads the weights; the first real chat then still pays the CPU-bound
        prompt-eval of the identity prompt (~8s on the iGPU-only box, Phase 11
        finding). Priming the prefix moves that cost into the summon window so
        the first query reuses the cache instead."""
        try:
            # Same num_ctx as real requests — a mismatch would make Ollama
            # reload the model on the first real chat, undoing the preload.
            body: dict = {"model": self.model, "keep_alive": self.keep_alive,
                          "options": {"num_ctx": NUM_CTX}}
            if prime:
                body["messages"] = prime
                body["think"] = self.think
                body["stream"] = False
                body["options"]["num_predict"] = 1
            else:
                body["messages"] = []
            await self._http.post("/api/chat", json=body)
        except httpx.HTTPError:
            pass

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
