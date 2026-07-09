"""Request router: chat streaming, sleep, and todos.* one-shot CRUD.
Tool-call vs direct-answer classification arrives with the MCP phase."""

import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import date

from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.mcp_bridge import ToolCallError

log = logging.getLogger(__name__)

TODO_HINT = re.compile(r"\b(?:todos?|tasks?|due|overdue)\b", re.IGNORECASE)

TOOL_HINT = re.compile(
    r"\b(look ?up|search|find|who wrote|author of|isbn|published|"
    r"books?|novels?|files?|folder|directory|notes)\b",
    re.IGNORECASE,
)


def todo_context(todos: list[dict], today: date) -> str:
    """System-message context: today's date + one line per open todo, or an
    explicit empty marker so the model can't hallucinate around a blank list."""
    lines = [f"Today is {today.isoformat()} ({today.strftime('%A')})."]
    if not todos:
        lines.append("The user has no open todos.")
    else:
        lines.append("The user's open todos:")
        for t in todos:
            due = f"(due {t['due_date']})" if t["due_date"] else "(no due date)"
            tags = f" [{', '.join(t['tags'])}]" if t["tags"] else ""
            lines.append(f"- {t['text']} {due}{tags}")
    return "\n".join(lines)


class Router:
    def __init__(self, llm, todos, *, bridge=None, model_router=None, tool_log=None,
                 max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._bridge = bridge
        self._model_router = model_router
        self._tool_log = tool_log
        self._max_iterations = max_iterations

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            message = payload.get("message", "")
            if self._bridge is not None and TOOL_HINT.search(message):
                async for ev in self._chat_with_tools(message):
                    yield ev
                return
            messages = []
            if TODO_HINT.search(message):
                messages.append({"role": "system",
                                 "content": todo_context(self._todos.open_todos(),
                                                         date.today())})
            messages.append({"role": "user", "content": message})
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
        elif type_ == "todos.list":
            yield {"result": self._todos.list_all()}
        elif type_ == "todos.add":
            try:
                yield {"result": self._todos.add(payload.get("text", ""))}
            except ValueError as e:
                yield {"error": str(e)}
        elif type_ == "todos.toggle":
            try:
                yield {"result": self._todos.toggle(int(payload["id"]),
                                                    bool(payload["completed"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "todos.toggle needs {id, completed}"}
        elif type_ == "todos.delete":
            try:
                yield {"result": self._todos.delete(int(payload["id"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "todos.delete needs {id}"}
        else:
            yield {"error": f"unknown request type: {type_}"}

    async def _chat_with_tools(self, message: str):
        try:
            await self._bridge.ensure_started()
            tools = self._bridge.ollama_tools()
        except Exception:
            log.exception("MCP bridge unavailable — answering without tools")
            tools = []
        if not tools:                      # no servers came up → fall back to plain chat
            messages = []
            if TODO_HINT.search(message):
                messages.append({"role": "system",
                                 "content": todo_context(self._todos.open_todos(), date.today())})
            messages.append({"role": "user", "content": message})
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
            return

        async def executor(name, args):
            start = time.monotonic()
            try:
                text = await self._bridge.call(name, args)
                ok = True
            except Exception as e:
                text, ok = f"tool error: {e}", False
                if not isinstance(e, ToolCallError):
                    log.exception("tool call %r failed unexpectedly", name)
            if self._tool_log is not None:
                self._tool_log.write(name, args, ok, text,
                                     int((time.monotonic() - start) * 1000))
            return text

        messages = []
        if TODO_HINT.search(message):
            messages.append({"role": "system",
                             "content": todo_context(self._todos.open_todos(), date.today())})
        messages.append({"role": "user", "content": message})
        model = self._model_router.pick_model(message, needs_tools=True) \
            if self._model_router else None
        try:
            async for ev in self._llm.chat_with_tools(
                messages, tools, executor, model=model, max_iterations=self._max_iterations):
                if "tool_call" in ev:
                    yield {"tool_used": ev["tool_call"]["name"]}
                elif ev.get("capped"):
                    yield {"chunk": "(stopped after several tool steps without a final answer)"}
                elif "content" in ev:
                    yield {"chunk": ev["content"]}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}
