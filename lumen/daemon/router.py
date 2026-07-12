"""Request router: chat streaming (plain, tool-augmented, book-rec, and
event-creation paths), sleep, todos.*/books.*/calendar.* one-shots, and
confirm.response resolution. Tool-call vs direct-answer classification is the
regex hints below — cheap heuristics, no LLM pre-pass."""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import date, datetime, timedelta
from pathlib import Path

from lumen.daemon.llm.book_recs import recommend
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.event_create import (confirm_payload, propose_event,
                                           validate_proposal)
from lumen.daemon.llm.mcp_bridge import ToolCallError
from lumen.daemon.llm.model_router import FS_WRITE_HINT

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

EVENT_HINT = re.compile(
    r"\b(book|schedule|create|add|set ?up|put)\b"
    r".*\b(meeting|call|event|appointment|lunch|dinner|coffee|calendar)\b",
    re.IGNORECASE | re.DOTALL,
)

CAL_CONTEXT_DAYS = 14  # chat context window; the cache itself is wider

# In-context conversation history is a fixed size, not unbounded: only the most
# recent N turns ride in the prompt (older turns stay on disk). A turn count is
# simpler than a token estimate and enough for the 4B fast model's thermal budget.
HISTORY_TURNS = 8

# Constant identity block prepended ahead of the keyword-gated context on every
# chat path (plain + tool loop). Kept short — the 4B fast model has a real
# context/thermal budget. Does NOT enumerate tools in prose: Ollama already
# passes the tool schemas, so a hand-written catalog would only drift out of sync.
IDENTITY = (
    "You are Lumen, a private assistant running entirely on the user's own "
    "laptop. You help with their todos, calendar, books, and files (email "
    "support is coming soon). You have tools available — use them to look "
    "things up instead of guessing or apologizing, and never tell the user you "
    "can't access something you have a tool for. Prefer specific, concise answers."
)

# Write-capable tools stay callable by the daemon (after a confirm) but are
# never offered to the model in the generic tool loop — the confirm gate is
# mechanical, not prompt-enforced.
WRITE_TOOLS = frozenset({"create_event"})

# No single tool call may run longer than this. The filesystem server processes
# requests sequentially over stdio, so a `search_files` from '/' can block for
# minutes (observed 6.5 min) and hang the whole chat. Grounding (fs_context)
# stops the model reaching for such calls; this is the defensive backstop.
TOOL_TIMEOUT_S = 30.0


def fs_context(home: Path) -> str:
    """System-message grounding for filesystem tools: where the user's files
    actually live, so the model reads a specific directory instead of guessing
    paths or scanning the whole machine from '/'."""
    return (
        f"The user's home directory is {home}. Their personal files, code, and "
        f"projects live under it — for example {home}/Projects, {home}/Documents, "
        f"and {home}/Downloads. When the user asks about their own files or "
        f"folders, call list_directory (or directory_tree) on a specific path "
        f"under the home directory. Do NOT list or search from '/', the "
        f"whole-machine root — it is huge and slow. If you don't know an exact "
        f"folder name, list its parent directory and read the names rather than "
        f"using search_files."
    )


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
                 confirm=None, write_gate=None, model_router=None, tool_log=None,
                 conversations=None, max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._calendar = calendar   # CalendarSync facade: list_range/last_sync/connected
        self._bridge = bridge
        self._confirm = confirm     # ConfirmBroker — gates every external write
        self._write_gate = write_gate   # WriteGate — per-file grants for fs writes
        self._model_router = model_router
        self._tool_log = tool_log
        self._conv = conversations  # ConversationStore — transcript log + multi-turn state
        self._max_iterations = max_iterations

    def on_disconnect(self) -> None:
        """A UI connection died — deny anything still waiting on a dialog."""
        if self._confirm is not None:
            self._confirm.deny_all()

    def _build_messages(self, message: str, history: list[dict],
                        tool_loop: bool = False) -> list[dict]:
        """A constant identity block + keyword-gated per-query context as the
        system message, then the conversation history (which ends with the
        current user turn). Context is keyed on the current message; fs grounding
        also rides along whenever the tool loop is active, so a keyword-less
        follow-up in a tool-engaged thread still knows where the user's files
        live instead of scanning '/' (found in live verification)."""
        context = [IDENTITY]
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
        if self._bridge is not None and (tool_loop or TOOL_HINT.search(message)
                                         or FS_WRITE_HINT.search(message)):
            context.append(fs_context(Path.home()))
        return [{"role": "system", "content": "\n\n".join(context)}] + history

    def _messages_for(self, message: str, conv_id: int | None,
                      tool_loop: bool = False) -> list[dict]:
        """Prompt messages for a chat turn: the current user message is already
        persisted, so the store's (capped) history ends with it. Without a store
        this degrades to a single stateless user turn."""
        if self._conv is not None and conv_id is not None:
            history = self._conv.history(conv_id, limit=HISTORY_TURNS)
        else:
            history = [{"role": "user", "content": message}]
        return self._build_messages(message, history, tool_loop)

    def _tool_shaped(self, message: str, conv_id: int | None) -> bool:
        """Enter the tool loop when the current message hints at a tool OR this
        conversation has already used one — so a bare follow-up ('and delete it')
        stays tool-capable. Gate on the flag, not a full tool loop every turn."""
        if TOOL_HINT.search(message) or FS_WRITE_HINT.search(message):
            return True
        return (self._conv is not None and conv_id is not None
                and self._conv.is_tool_engaged(conv_id))

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            message = payload.get("message", "")
            conv_id = payload.get("conversation_id")
            if self._conv is not None:
                if conv_id is None:
                    conv_id = self._conv.create(message)
                    yield {"conversation_id": conv_id}   # emit first so the UI can track the thread
                self._conv.add_message(conv_id, "user", message)   # write-through on arrival
            # aclosing: closing this generator must synchronously close whatever
            # sub-path it drives (the tool loop owns a pump task), not defer to GC.
            async with aclosing(self._chat(message, conv_id)) as gen:
                async for ev in gen:
                    yield ev
        elif type_ == "conversations.list":
            if self._conv is None:
                yield {"error": "conversation history unavailable"}
            else:
                yield {"result": self._conv.list_recent(int(payload.get("limit", 50)))}
        elif type_ == "conversations.get":
            if self._conv is None:
                yield {"error": "conversation history unavailable"}
                return
            try:
                got = self._conv.get(int(payload["id"]))
            except (KeyError, TypeError, ValueError):
                yield {"error": "conversations.get needs {id}"}
                return
            yield {"error": "conversation not found"} if got is None else {"result": got}
        elif type_ == "sleep":
            await self._llm.unload()
            yield {"done": True}
        elif type_ == "warm":
            # Fire-and-forget preload (launcher summon) so the next query isn't
            # a cold start. No response — the UI doesn't wait on it.
            await self._llm.warm()
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
        elif type_ == "calendar.create":
            if (self._calendar is None or self._bridge is None
                    or self._confirm is None):
                yield {"error": "calendar creation unavailable"}
                return
            now = datetime.now().astimezone()
            proposal, err = validate_proposal(payload.get("proposal", {}) or {},
                                              now=now, user_message="")
            if proposal is None:
                yield {"error": err}
                return
            async for ev in self._gated_create(proposal):
                if "_outcome" in ev:
                    created, text = ev["_outcome"]
                    yield {"result": {"created": created, "message": text}}
                else:
                    yield ev
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

    async def _chat(self, message: str, conv_id: int | None):
        """Pick the chat sub-path, stream it through, and write-through the
        assistant turn (with any tool names) once it completes."""
        if (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and EVENT_HINT.search(message)):
            sub = self._create_event_chat(message)
        elif (self._books is not None and self._bridge is not None
              and REC_HINT.search(message)):
            sub = self._recommend_chat(message)
        elif self._bridge is not None and self._tool_shaped(message, conv_id):
            sub = self._chat_with_tools(message, conv_id)
        else:
            sub = self._plain_chat(message, conv_id)

        acc, tools = [], []
        async with aclosing(sub) as gen:
            async for ev in gen:
                if "chunk" in ev:
                    acc.append(ev["chunk"])
                elif "tool_used" in ev:
                    tools.append(ev["tool_used"])
                yield ev
        if self._conv is not None and conv_id is not None and (acc or tools):
            self._conv.add_message(conv_id, "assistant", "".join(acc), tools or None)
            if tools:                         # this thread is now tool-shaped for its follow-ups
                self._conv.mark_tool_engaged(conv_id)

    async def _plain_chat(self, message: str, conv_id: int | None):
        messages = self._messages_for(message, conv_id)
        try:
            async for chunk in self._llm.chat(messages):
                yield {"chunk": chunk}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}

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

    async def _create_event_chat(self, message: str):
        """NL event creation: extract → validate → confirm dialog → create."""
        now = datetime.now().astimezone()
        try:
            proposal, err = await propose_event(self._llm, message, now=now)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if proposal is None:
            yield {"chunk": err}     # honest failure is an answer, not an IPC error
            yield {"done": True}
            return
        async for ev in self._gated_create(proposal):
            if "_outcome" in ev:
                yield {"chunk": ev["_outcome"][1]}
            else:
                yield ev
        yield {"done": True}

    async def _gated_create(self, proposal: dict):
        """Confirm-over-IPC then execute. Yields the confirm_request event and
        finally {"_outcome": (created, message)} for the caller to render."""
        confirm_id = self._confirm.begin()
        yield {"confirm_request": confirm_payload(proposal),
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            yield {"_outcome": (False, "Cancelled — nothing was created.")}
            return
        try:
            await self._bridge.ensure_started()
        except Exception:
            log.exception("MCP bridge unavailable for event creation")
            yield {"_outcome": (False, "calendar tools are unavailable right now")}
            return
        args = {"title": proposal["title"], "start": proposal["start"],
                "end": proposal["end"], "all_day": proposal["all_day"],
                "location": proposal["location"] or "",
                "description": proposal["description"] or "",
                "attendees": proposal["attendees"],
                "recurrence": proposal["recurrence"] or ""}
        start_t = time.monotonic()
        try:
            text = await self._bridge.call("create_event", args)
            ok = True
        except Exception as e:
            text, ok = f"tool error: {e}", False
            if not isinstance(e, ToolCallError):
                log.exception("create_event failed unexpectedly")
        if self._tool_log is not None:
            self._tool_log.write("create_event", args, ok, text,
                                 int((time.monotonic() - start_t) * 1000))
        # our gcal server degrades gracefully with message strings; only a
        # "Created:" reply means an event actually exists now
        ok = ok and text.startswith("Created:")
        if ok and hasattr(self._calendar, "sync_once"):
            try:
                await self._calendar.sync_once()   # show the new event promptly
            except Exception:
                log.exception("post-create sync failed")
        yield {"_outcome": (ok, text)}

    async def _chat_with_tools(self, message: str, conv_id: int | None = None):
        try:
            await self._bridge.ensure_started()
            tools = [t for t in self._bridge.ollama_tools()
                     if t.get("function", {}).get("name", "").split("__")[-1]
                     not in WRITE_TOOLS]
        except Exception:
            log.exception("MCP bridge unavailable — answering without tools")
            tools = []
        if not tools:                      # no servers came up → fall back to plain chat
            messages = self._messages_for(message, conv_id, tool_loop=True)
            try:
                async for chunk in self._llm.chat(messages):
                    yield {"chunk": chunk}
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"done": True}
            return

        # chat_with_tools awaits the executor inline while this generator waits
        # on the loop's next event, so anything the executor must surface
        # mid-call (the write gate's confirm_request) travels via the queue.
        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        async def emit(ev: dict) -> None:
            await queue.put(ev)

        async def executor(name, args):
            start = time.monotonic()
            if self._write_gate is not None:
                denial = await self._write_gate.check(name, args, emit)
                if denial is not None:
                    if self._tool_log is not None:
                        self._tool_log.write(name, args, False, denial,
                                             int((time.monotonic() - start) * 1000))
                    return denial
            try:
                text = await asyncio.wait_for(self._bridge.call(name, args),
                                              TOOL_TIMEOUT_S)
                ok = True
            except asyncio.TimeoutError:
                text, ok = (
                    f"tool error: {name} timed out after {int(TOOL_TIMEOUT_S)}s — "
                    "use a specific directory under the home folder instead of a "
                    "broad path, and never search_files from '/'", False)
            except Exception as e:
                text, ok = f"tool error: {e}", False
                if not isinstance(e, ToolCallError):
                    log.exception("tool call %r failed unexpectedly", name)
            if self._tool_log is not None:
                self._tool_log.write(name, args, ok, text,
                                     int((time.monotonic() - start) * 1000))
            return text

        messages = self._messages_for(message, conv_id, tool_loop=True)
        model = self._pick_model(message)

        async def pump():
            try:
                async for ev in self._llm.chat_with_tools(
                        messages, tools, executor, model=model,
                        max_iterations=self._max_iterations):
                    await queue.put(ev)
            except LLMUnavailable as e:
                await queue.put({"_pump_error": str(e)})
            finally:
                await queue.put(done)

        task = asyncio.create_task(pump())
        try:
            while (ev := await queue.get()) is not done:
                if "_pump_error" in ev:
                    yield {"error": ev["_pump_error"]}
                    return
                if "confirm_request" in ev:
                    yield ev
                elif "tool_call" in ev:
                    yield {"tool_used": ev["tool_call"]["name"]}
                elif ev.get("capped"):
                    yield {"chunk": "(stopped after several tool steps without a final answer)"}
                elif "content" in ev:
                    yield {"chunk": ev["content"]}
            yield {"done": True}
        finally:
            task.cancel()   # early close (UI disconnect) must not leak the loop
            await asyncio.gather(task, return_exceptions=True)
