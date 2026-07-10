"""Request router: chat streaming (plain, tool-augmented, and book-rec paths),
sleep, and todos.*/books.* one-shot CRUD. Tool-call vs direct-answer
classification is the regex hints below — cheap heuristics, no LLM pre-pass."""

import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta

from lumen.daemon.llm.book_recs import recommend
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.mcp_bridge import ToolCallError

log = logging.getLogger(__name__)

TODO_HINT = re.compile(r"\b(?:todos?|tasks?|due|overdue)\b", re.IGNORECASE)

TOOL_HINT = re.compile(
    r"\b(look ?up|search|find|who wrote|author of|isbn|published|"
    r"books?|novels?|files?|folder|directory|notes)\b",
    re.IGNORECASE,
)

BOOK_HINT = re.compile(r"\b(books?|novels?|reading|read|rated?|author)\b", re.IGNORECASE)

REC_HINT = re.compile(
    r"\b(?:recommend|suggest(?:ion)?s?)\b.*\b(?:books?|novels?|read(?:ing)?)\b"
    r"|\bwhat should i read\b|\bread next\b",
    re.IGNORECASE | re.DOTALL,
)

CAL_HINT = re.compile(
    r"\b(calendar|meetings?|events?|schedule|agenda|appointments?|free|busy)\b",
    re.IGNORECASE,
)

CAL_CONTEXT_DAYS = 14  # chat context window; the cache itself is wider


def calendar_context(events: list[dict], now: datetime, window_end: date) -> str:
    """System-message context: current local time, a hard bounds statement so the
    model can't guess outside the window, one compact line per event, and an
    explicit empty marker."""
    lines = [f"Now: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}), "
             f"local timezone UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}.",
             f"The user's calendar from {now.date().isoformat()} through "
             f"{window_end.isoformat()} (events outside this range are not shown — "
             "say so if asked about them):"]
    if not events:
        lines.append("No events in this range.")
        return "\n".join(lines)
    for e in events:
        cal = f" [{e['calendar_name']}]" if e.get("calendar_name") else ""
        loc = f" at {e['location']}" if e.get("location") else ""
        names = ", ".join(a["name"] or a["email"] for a in e.get("attendees", [])
                          if not a.get("self"))
        who = f" — with {names}" if names else ""
        if e["all_day"]:
            d = date.fromisoformat(e["start_at"][:10])
            when = f"{d.strftime('%a')} {d.isoformat()} (all day)"
        else:
            s = datetime.fromisoformat(e["start_at"]).astimezone(now.tzinfo)
            when = f"{s.strftime('%a')} {s.date().isoformat()} {s.strftime('%H:%M')}"
            if e.get("end_at"):
                when += f"–{datetime.fromisoformat(e['end_at']).astimezone(now.tzinfo).strftime('%H:%M')}"
        lines.append(f"- {when}: {e['title']}{cal}{loc}{who}")
    return "\n".join(lines)


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
    def __init__(self, llm, todos, books=None, *, calendar=None, bridge=None,
                 confirm=None, model_router=None, tool_log=None, max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._calendar = calendar   # CalendarSync facade: list_range/last_sync/connected
        self._bridge = bridge
        self._confirm = confirm     # ConfirmBroker — gates every external write
        self._model_router = model_router
        self._tool_log = tool_log
        self._max_iterations = max_iterations

    def on_disconnect(self) -> None:
        """A UI connection died — deny anything still waiting on a dialog."""
        if self._confirm is not None:
            self._confirm.deny_all()

    def _base_messages(self, message: str) -> list[dict]:
        """Shared system-context + user message for every chat path."""
        context = []
        if TODO_HINT.search(message):
            context.append(todo_context(self._todos.open_todos(), date.today()))
        if self._books is not None and BOOK_HINT.search(message):
            context.append(self._books.catalog_context())
        if self._calendar is not None and CAL_HINT.search(message):
            now = datetime.now().astimezone()
            end = now.date() + timedelta(days=CAL_CONTEXT_DAYS)
            context.append(calendar_context(
                self._calendar.list_range(now.date().isoformat(), end.isoformat()),
                now, end))
        messages = ([{"role": "system", "content": "\n\n".join(context)}]
                    if context else [])
        messages.append({"role": "user", "content": message})
        return messages

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            message = payload.get("message", "")
            if (self._books is not None and self._bridge is not None
                    and REC_HINT.search(message)):
                async for ev in self._recommend_chat(message):
                    yield ev
                return
            if self._bridge is not None and TOOL_HINT.search(message):
                async for ev in self._chat_with_tools(message):
                    yield ev
                return
            messages = self._base_messages(message)
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
        elif type_ == "confirm.response":
            # Silent ack: the answer unblocks whichever handler is awaiting it.
            if self._confirm is not None:
                try:
                    self._confirm.resolve(int(payload["confirm_id"]),
                                          bool(payload["approved"]))
                except (KeyError, TypeError, ValueError):
                    log.warning("malformed confirm.response payload: %r", payload)
        elif type_.startswith("books.") and self._books is None:
            yield {"error": "book catalog unavailable"}
        elif type_ == "books.list":
            yield {"result": self._books.list_all()}
        elif type_ == "books.add":
            try:
                yield {"result": self._books.add(
                    payload.get("title", ""), payload.get("author"),
                    int(payload["rating"]) if payload.get("rating") else None,
                    payload.get("notes"))}
            except (TypeError, ValueError) as e:
                yield {"error": str(e)}
        elif type_ == "books.delete":
            try:
                yield {"result": self._books.delete(int(payload["id"]))}
            except (KeyError, TypeError, ValueError):
                yield {"error": "books.delete needs {id}"}
        elif type_ == "books.recs":
            yield {"result": self._books.latest_recs()}
        elif type_ == "books.recommend":
            try:
                result = await self._recommend()
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            if "error" in result:
                yield {"error": result["error"]}
            else:
                yield {"result": result}
        elif type_ == "calendar.list":
            if self._calendar is None:
                yield {"error": "calendar unavailable"}
            else:
                today = date.today().isoformat()
                yield {"result": {
                    "events": self._calendar.list_range(
                        payload.get("from", today), payload.get("to", today)),
                    "connected": self._calendar.connected,
                    "last_sync": self._calendar.last_sync(),
                    "window": list(self._calendar.window())}}
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

    def _pick_model(self, message: str):
        return (self._model_router.pick_model(message, needs_tools=True)
                if self._model_router else None)

    async def _recommend(self, request: str | None = None) -> dict:
        return await recommend(
            self._llm, self._bridge, self._books,
            model=self._pick_model(request or "recommend books"),
            tool_log=self._tool_log, request=request,
            max_iterations=self._max_iterations)

    async def _recommend_chat(self, message: str):
        try:
            result = await self._recommend(message)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if "error" in result:
            yield {"chunk": result["error"]}   # honest failure is an answer, not an IPC error
        else:
            lines = ["Suggested next:"]
            for r in result["recs"]:
                author = f" — {r['author']}" if r["author"] else ""
                lines.append(f"• {r['title']}{author} — {r['rationale']}")
            yield {"chunk": "\n".join(lines)}
        yield {"done": True}

    async def _chat_with_tools(self, message: str):
        try:
            await self._bridge.ensure_started()
            tools = self._bridge.ollama_tools()
        except Exception:
            log.exception("MCP bridge unavailable — answering without tools")
            tools = []
        if not tools:                      # no servers came up → fall back to plain chat
            messages = self._base_messages(message)
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

        messages = self._base_messages(message)
        model = self._pick_model(message)
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
