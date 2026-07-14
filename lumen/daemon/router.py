"""Request router: chat streaming (plain, tool-augmented, book-rec, and
event-creation paths), sleep, todos.*/books.*/calendar.*/emails.* one-shots,
and confirm.response resolution. Tool-call vs direct-answer classification is
the regex hints below — cheap heuristics, no LLM pre-pass."""

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import date, datetime, timedelta
from pathlib import Path

from lumen.daemon.connectors import free_slots
from lumen.daemon.connectors.capture import classify
from lumen.daemon.llm import commitments, meeting_prep, triage
from lumen.daemon.llm.book_recs import recommend
from lumen.daemon.llm.briefing import build_sections, compose_briefing
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.email_compose import (EMAIL, propose_email,
                                            revise_email)
from lumen.daemon.llm.event_create import (confirm_payload, propose_event,
                                           validate_proposal)
from lumen.daemon.llm.mcp_bridge import ToolCallError
from lumen.daemon.llm.model_router import FS_WRITE_HINT

log = logging.getLogger(__name__)

TODO_HINT = re.compile(r"\b(?:todos?|tasks?|due|overdue)\b", re.IGNORECASE)

# Deliberately wide net: a false positive just rides the tool schemas along on
# one request; a miss answers "I can't access your files" (live bug 2026-07-11,
# "what projects do I have currently"). Pure chit-chat still stays plain.
TOOL_HINT = re.compile(
    r"\b(look ?up|search|find|locate|browse|open|read|show|list|inside|"
    r"who wrote|author of|isbn|published|books?|novels?|"
    r"files?|folders?|director(?:y|ies)|dirs?|paths?|notes?|"
    r"projects?|downloads?|desktop|documents?|docs?|pictures?|photos?|"
    r"screenshots?|videos?|music|drives?|disks?|"
    r"laptop|computer|machine|pc|system|"
    r"what(?:'?s| is| are) (?:in|on|inside))\b",
    re.IGNORECASE,
)

BOOK_HINT = re.compile(r"\b(books?|novels?|reading|read|rated?|author)\b", re.IGNORECASE)

REC_HINT = re.compile(
    r"\b(?:recommend|suggest(?:ion)?s?)\b.*\b(?:books?|novels?|read(?:ing)?)\b"
    r"|\bwhat should i read\b|\bread next\b",
    re.IGNORECASE | re.DOTALL,
)

# Same widen-the-net rationale as TOOL_HINT: a false positive costs a few
# injected context lines; a miss leaves the plain path free to fabricate a
# calendar check (live bug 2026-07-12, "anything on my calender this coming
# week" — misspelling matched nothing). cal[ae]nd\w* covers the common
# calendar misspellings; bare time-of-week words are schedule-shaped enough.
CAL_HINT = re.compile(
    r"\b(cal[ae]nd\w*|meetings?|events?|schedule|agenda|appointments?|free|busy|"
    r"weeks?|weekends?|today|tomorrow|tonight|upcoming|plans?)\b",
    re.IGNORECASE,
)

# The briefing route must be checked ahead of every other chat path: "my day"
# phrasings also match CAL_HINT, and the plain path with calendar context would
# otherwise steal them and answer without todos/mail.
BRIEFING_HINT = re.compile(r"\b(?:brief(?:ing)?|my day)\b", re.IGNORECASE)

# NL todo add / mark-done: precise anchored shapes, checked ahead of even the
# briefing route ("mark the briefing todo done" must not open a briefing).
TODO_ADD = re.compile(r"^\s*(?:add\s+(?:a\s+)?todo:?|remind me to)\s+(.+)$",
                      re.IGNORECASE | re.DOTALL)
MARK_DONE = re.compile(r"^\s*(?:mark|check\s*off|tick)\b(.+?)(?:\bas\s+)?"
                       r"\b(?:done|completed?|finished)\b", re.IGNORECASE)
_MATCH_STOP = frozenset(
    "the a an my that this one todo task to as off done complete completed "
    "finished it mark please".split())

# Meeting prep: "prep (me) for X" shapes. Bare "prepare" stays out so
# "prepare a speech" doesn't run the pipeline; a matched request with no
# cache hit answers "I don't see that meeting", never a guess.
PREP_HINT = re.compile(r"\bprep\b|\bprepare\s+(?:me\s+)?for\b", re.IGNORECASE)

# The prep pipeline reads this many days ahead when resolving "the standup".
PREP_WINDOW_DAYS = 7

# Commitment questions run the sent-mail scan and answer with the pending
# suggestions — a pull, never a background job.
PROMISE_HINT = re.compile(r"\b(?:promis\w+|commit(?:ted|ments?)|owe[ds]?)\b",
                          re.IGNORECASE)

EVENT_HINT = re.compile(
    r"\b(book|schedule|create|add|set ?up|put)\b"
    r".*\b(meeting|call|event|appointment|lunch|dinner|coffee|calendar)\b",
    re.IGNORECASE | re.DOTALL,
)

# Find-a-time shapes ("find 30 minutes...") — slot math is deterministic
# daemon code; a booking request with a concrete time stays on EVENT_HINT.
SLOT_HINT = re.compile(
    r"\bfind\b.{0,40}\b(?:minutes?|mins?|hours?|slot|time)\b"
    r"|\bwhen\s+am\s+i\s+free\b|\bfree\s+(?:slot|time)s?\b"
    r"|\bavailabilit(?:y|ies)\b",
    re.IGNORECASE | re.DOTALL,
)

# "book the first one" after a slot proposal: only taken when the previous
# assistant turn actually proposed slots (SLOT_HEADER in its text), so a bare
# "book that" can't reach event creation with nothing to resolve against.
BOOKING_HINT = re.compile(
    r"\b(?:book|grab|take|go\s+with|schedule)\b.{0,40}"
    r"\b(?:one|that|first|second|third|slot|option|it)\b",
    re.IGNORECASE | re.DOTALL,
)

# Compose-shaped requests jump to the draft → popup path before every other
# chat route (EVENT_HINT would steal "draft an email to schedule a meeting").
# Read-shaped mail questions ("did Sam email me back?") must NOT match.
COMPOSE_HINT = re.compile(
    r"\b(?:send|write|draft|compose|shoot)\b.{0,60}\b(?:e-?mails?|reply|message)\b"
    r"|\breply(?:ing)?\b.{0,60}\b(?:e-?mails?|saying|telling|that)\b"
    r"|\be-?mail\b.{0,40}\b(?:to|saying|telling|asking|about)\b",
    re.IGNORECASE | re.DOTALL,
)

# A compose wait is an edit session, not a confirm click.
COMPOSE_TIMEOUT_S = 1800.0

# Triage is a pull ("what needs a reply?"), never a push. Checked after
# COMPOSE so "reply to X saying thanks" still opens the compose popup.
TRIAGE_HINT = re.compile(
    r"\btriage\b|\bneeds?\s+(?:a\s+|an\s+|my\s+)?"
    r"(?:reply|repl(?:y|ies)|response|answer(?:ing)?)\b",
    re.IGNORECASE,
)

MAIL_HINT = re.compile(
    r"\b(e-?mails?|inbox|unread|gmail|mail|messages?|newsletters?|senders?)\b",
    re.IGNORECASE,
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
    "laptop. You help with their email, todos, calendar, books, and files. "
    "Email and calendar sync to a local mirror automatically in the background "
    "— there is nothing the user needs to trigger manually. You have tools "
    "available — use them to look "
    "things up instead of guessing or apologizing, and never tell the user you "
    "can't access something you have a tool for. Never pretend to check or look "
    "something up: if this conversation gives you no tool or data for it, say so "
    "plainly instead of inventing a result. When the user asks you to write, "
    "send, or reply to an email, a compose window opens with your draft for "
    "them to review and send — so never claim you can't send email. Prefer "
    "specific, concise answers."
)

# Write-capable tools stay callable by the daemon (after a confirm) but are
# never offered to the model in the generic tool loop — the confirm gate is
# mechanical, not prompt-enforced.
WRITE_TOOLS = frozenset({"create_event", "delete_event"})

# No single tool call may run longer than this. The filesystem server processes
# requests sequentially over stdio, so a `search_files` from '/' can block for
# minutes (observed 6.5 min) and hang the whole chat. Grounding (fs_context)
# stops the model reaching for such calls; this is the defensive backstop.
TOOL_TIMEOUT_S = 30.0

# No single tool result may exceed this when fed back to the model — a
# directory_tree of a real folder returned megabytes and blew the context
# window (Ollama 400 exceed_context_size_error, live 2026-07-12). ~4k chars is
# roughly 1k tokens: two capped results still fit num_ctx=8192 alongside the
# tool schemas, grounding, and history. Full results still reach the tool log.
TOOL_RESULT_MAX_CHARS = 4000
TRUNCATION_NOTE = ("\n…[truncated: result too large — answer from what is "
                   "shown, or make a narrower call, e.g. list_directory on "
                   "one specific folder]")


def fs_context(home: Path) -> str:
    """System-message grounding for filesystem tools: where the user's files
    actually live, so the model reads a specific directory instead of guessing
    paths or scanning the whole machine from '/'."""
    return (
        f"The user's home directory is {home}. Their personal files, code, and "
        f"projects live under it — for example {home}/Projects, {home}/Documents, "
        f"and {home}/Downloads. When the user asks about their own files or "
        f"folders, call list_directory on a specific path under the home "
        f"directory. Do NOT list or search from '/', the whole-machine root — "
        f"it is huge and slow. Avoid directory_tree: it recurses the entire "
        f"subtree and its output is enormous; list_directory answers these "
        f"questions. If you don't know an exact folder name, list its parent "
        f"directory and read the names rather than using search_files."
    )


def _event_when(e: dict) -> str:
    """Cached event row -> the human 'When' line for a confirm dialog."""
    if e.get("all_day"):
        d = date.fromisoformat(e["start_at"][:10])
        return f"{d.strftime('%a')} {d.isoformat()} (all day)"
    s = datetime.fromisoformat(e["start_at"]).astimezone()
    when = f"{s.strftime('%a')} {s.date().isoformat()} {s.strftime('%H:%M')}"
    if e.get("end_at"):
        when += f"–{datetime.fromisoformat(e['end_at']).astimezone().strftime('%H:%M')}"
    return when


def _sender_address(sender: str) -> str | None:
    """'Ada Lovelace <a@x.com>' or bare 'a@x.com' -> the address."""
    m = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", sender or "")
    if m:
        return m.group(1)
    s = (sender or "").strip()
    return s if EMAIL.match(s) else None


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


def mail_context(unread: list[dict], counts: dict, connected: bool,
                 syncing: bool = False) -> str:
    """System-message context: unread summary from the local mirror, explicit
    empty/not-connected/still-syncing markers, and a pointer at search_email
    for the rest."""
    if not connected:
        return ("Gmail is not connected yet — the user needs to run the one-time "
                "Google setup. Say so if asked about email; do not invent messages.")
    lines = [f"The user's mailbox mirror holds {counts['total']} messages, "
             f"{counts['unread']} unread. Unread messages (only these are shown — "
             "use the search_email tool for anything else):"]
    if syncing:
        lines.insert(0, "The first mailbox sync has not finished — the mirror is "
                        "incomplete. Say so if asked about email; missing "
                        "messages are not absent, just not pulled yet.")
    if not unread:
        lines.append("No unread messages.")
    for m in unread:
        when = m["received_at"][:16].replace("T", " ")
        lines.append(f"- {when}: {m['sender']} — {m['subject']}")
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
    def __init__(self, llm, todos, books=None, *, calendar=None, mail=None,
                 mail_store=None, bridge=None, confirm=None, write_gate=None,
                 model_router=None, tool_log=None, conversations=None,
                 suggestions=None, scheduling=None, max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._calendar = calendar   # CalendarSync facade: list_range/last_sync/connected
        self._mail = mail           # GmailSync facade: poll_forever/connected
        self._mail_store = mail_store   # EmailStore — local mirror the UI/chat read from
        self._bridge = bridge
        self._confirm = confirm     # ConfirmBroker — gates every external write
        self._write_gate = write_gate   # WriteGate — per-file grants for fs writes
        self._model_router = model_router
        self._tool_log = tool_log
        self._conv = conversations  # ConversationStore — transcript log + multi-turn state
        self._suggestions = suggestions  # SuggestionStore — commitment tracking
        self._scheduling = scheduling    # SchedulingConfig — proposable hours
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
        if self._mail_store is not None and (tool_loop or MAIL_HINT.search(message)):
            context.append(mail_context(
                self._mail_store.unread(limit=10), self._mail_store.counts(),
                self._mail.connected if self._mail is not None else False,
                syncing=self._mail.syncing if self._mail is not None else False))
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
            # Quick capture (launcher only sets capture_ok): note-shaped text
            # becomes a todo instead of a chat turn — before any conversation
            # is created, so a captured note never litters the history.
            if payload.get("capture_ok") and await self._is_capture(message):
                try:
                    rows = self._todos.add(message)
                except ValueError:
                    rows = []
                if rows:
                    yield {"captured": max(rows, key=lambda r: r["id"])}
                    yield {"done": True}
                    return
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
        elif type_ == "conversations.delete":
            if self._conv is None:
                yield {"error": "conversation history unavailable"}
                return
            try:
                self._conv.delete(int(payload["id"]))
            except (KeyError, TypeError, ValueError):
                yield {"error": "conversations.delete needs {id}"}
                return
            yield {"result": {"ok": True}}
        elif type_ == "briefing.today":
            # Dashboard one-shot: same pipeline as the chat route, collected.
            text: list[str] = []
            try:
                async for chunk in compose_briefing(self._llm,
                                                    self._briefing_sections()):
                    text.append(chunk)
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            yield {"result": {"text": "".join(text)}}
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
        elif type_ == "compose.response":
            # Resolves the chat turn awaiting this popup with its final fields
            # (or a cancel). If the id already expired but the user clicked
            # Send, send anyway — a Send click is never silently dropped.
            try:
                compose_id = int(payload["compose_id"])
            except (KeyError, TypeError, ValueError):
                yield {"error": "compose.response needs {compose_id}"}
                return
            fields = payload.get("fields")
            sending = bool(payload.get("send")) and isinstance(fields, dict)
            resolved = (self._confirm is not None
                        and self._confirm.resolve(compose_id,
                                                  fields if sending else False))
            if not resolved and sending and self._mail is not None:
                ok, text = await self._send_email(fields)
                yield {"result": {"ok": ok, "message": text}}
            else:
                yield {"result": {"ok": True, "message": ""}}
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
        elif type_ == "calendar.delete":
            if (self._calendar is None or self._bridge is None
                    or self._confirm is None):
                yield {"error": "calendar deletion unavailable"}
                return
            event_id = str(payload.get("id") or "")
            calendar_id = str(payload.get("calendar_id") or "")
            if not event_id or not calendar_id:
                yield {"error": "calendar.delete needs {id, calendar_id}"}
                return
            event = self._calendar.get(calendar_id, event_id)
            if event is None:
                yield {"error": "event not found"}
                return
            async for ev in self._gated_delete(event):
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
        elif type_.startswith("todos.") and type_.endswith(
                ("suggestions", "scan_commitments", "accept_suggestion",
                 "dismiss_suggestion")):
            if self._suggestions is None:
                yield {"error": "suggestions unavailable"}
                return
            if type_ == "todos.suggestions":
                yield {"result": {"suggestions": self._suggestions.pending()}}
            elif type_ == "todos.scan_commitments":
                if self._mail_store is None:
                    yield {"error": "email mirror unavailable"}
                    return
                try:
                    res = await commitments.scan(self._llm, self._mail_store,
                                                 self._suggestions)
                except LLMUnavailable as e:
                    yield {"error": str(e)}
                    return
                yield {"result": {**res,
                                  "suggestions": self._suggestions.pending()}}
            elif type_ == "todos.accept_suggestion":
                try:
                    row = self._suggestions.accept(int(payload["id"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "todos.accept_suggestion needs {id}"}
                    return
                if row is None:
                    yield {"error": "suggestion not found"}
                    return
                raw = row["text"] + (f" @{row['due_date']}" if row["due_date"] else "")
                todos = self._todos.add(raw, source="llm-extracted")
                yield {"result": {"suggestions": self._suggestions.pending(),
                                  "todos": todos}}
            else:   # todos.dismiss_suggestion
                try:
                    self._suggestions.dismiss(int(payload["id"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "todos.dismiss_suggestion needs {id}"}
                    return
                yield {"result": {"suggestions": self._suggestions.pending()}}
        elif type_.startswith("emails.") or type_ == "mail.refresh":
            if self._mail is None or self._mail_store is None:
                yield {"error": "email unavailable"}
                return
            if type_ == "mail.refresh":
                # A refresh during a running/pending bulk just returns the
                # current page immediately — the poller finishes the sync —
                # instead of awaiting inline and stalling the serial IPC
                # channel for the whole sync's duration.
                if not (self._mail.busy or self._mail.syncing):
                    await self._mail.sync_once()
                type_, payload = "emails.list", {}
            if type_ == "emails.list":
                yield {"result": {
                    "emails": self._mail_store.list_page(
                        payload.get("filter", "inbox"),
                        int(payload.get("limit", 50)),
                        int(payload.get("offset", 0))),
                    "connected": self._mail.connected,
                    "syncing": self._mail.syncing,
                    "last_sync": self._mail.last_sync(),
                    "counts": self._mail_store.counts()}}
            elif type_ == "emails.search":
                yield {"result": {"emails": self._mail_store.search(
                    payload.get("query", ""), int(payload.get("limit", 50)))}}
            elif type_ == "emails.get":
                row = self._mail_store.get(str(payload.get("id", "")))
                yield {"error": "email not found"} if row is None else {"result": row}
            elif type_ == "emails.unread":
                yield {"result": {
                    "emails": self._mail_store.unread(int(payload.get("limit", 10))),
                    "connected": self._mail.connected}}
            elif type_ in ("emails.archive", "emails.mark_read"):
                async for ev in self._gated_mail_action(type_, payload):
                    yield ev
            elif type_ == "emails.send":
                ok, text = await self._send_email(payload)
                yield {"result": {"ok": ok, "message": text}}
            elif type_ == "emails.revise":
                try:
                    revised, err = await revise_email(
                        self._llm, str(payload.get("subject", "")),
                        str(payload.get("body", "")),
                        str(payload.get("instruction", "")))
                except LLMUnavailable as e:
                    yield {"error": str(e)}
                    return
                yield {"error": err} if revised is None else {"result": revised}
            else:
                yield {"error": f"unknown request type: {type_}"}
        else:
            yield {"error": f"unknown request type: {type_}"}

    async def _chat(self, message: str, conv_id: int | None):
        """Pick the chat sub-path, stream it through, and write-through the
        assistant turn (with any tool names) once it completes."""
        if m := TODO_ADD.match(message):
            sub = self._nl_add_chat(m.group(1).strip())
        elif m := MARK_DONE.match(message):
            sub = self._mark_done_chat(m.group(1))
        elif self._calendar is not None and PREP_HINT.search(message):
            sub = self._prep_chat(message)
        elif (self._suggestions is not None and self._mail_store is not None
                and PROMISE_HINT.search(message)):
            sub = self._commitments_chat()
        elif BRIEFING_HINT.search(message):
            sub = self._briefing_chat()
        elif (self._confirm is not None and self._mail is not None
                and COMPOSE_HINT.search(message)):
            sub = self._compose_email_chat(message)
        elif self._mail_store is not None and TRIAGE_HINT.search(message):
            sub = self._triage_chat()
        elif self._calendar is not None and SLOT_HINT.search(message):
            sub = self._slots_chat(message)
        elif (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and BOOKING_HINT.search(message)
                and (slot_ctx := self._slot_context(conv_id))):
            sub = self._create_event_chat(message, context=slot_ctx)
        elif (self._confirm is not None and self._bridge is not None
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

    async def _is_capture(self, message: str) -> bool:
        verdict = classify(message)
        if verdict != "ambiguous":
            return verdict == "capture"
        # one tiny fast-model opinion; unreachable model → capture (a wrong
        # capture costs one Undo click, a lost note costs the note)
        system = ("Decide whether the user's text is a NOTE/TASK they are "
                  "jotting down to remember, or a MESSAGE addressed to an "
                  "assistant. Reply with exactly one word: TODO or CHAT.")
        try:
            text = ""
            async for chunk in self._llm.chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": message}]):
                text += chunk
        except LLMUnavailable:
            return True
        return "chat" not in text.lower()

    async def _nl_add_chat(self, text: str):
        """'add a todo: X' / 'remind me to X' from any chat surface."""
        try:
            rows = self._todos.add(text)
        except ValueError as e:
            yield {"chunk": str(e)}
            yield {"done": True}
            return
        new = max(rows, key=lambda r: r["id"])
        extra = f" (due {new['due_date']})" if new.get("due_date") else ""
        tags = f" [{', '.join(new['tags'])}]" if new.get("tags") else ""
        yield {"chunk": f"Added todo: {new['text']}{extra}{tags}"}
        yield {"done": True}

    async def _mark_done_chat(self, query: str):
        """'mark X done': fuzzy-match open todos; one match toggles, several
        list themselves instead of guessing, none answers honestly."""
        words = [w for w in re.findall(r"[\w']+", query.lower())
                 if w not in _MATCH_STOP]
        matches = []
        if words:
            for t in self._todos.open_todos():
                todo_words = set(re.findall(r"[\w']+", t["text"].lower()))
                if all(w in todo_words for w in words):
                    matches.append(t)
        if len(matches) == 1:
            self._todos.toggle(matches[0]["id"], True)
            yield {"chunk": f"Marked done: {matches[0]['text']}"}
        elif matches:
            lines = ["Which one? Several todos match:"]
            lines += [f"• {t['text']}" for t in matches]
            yield {"chunk": "\n".join(lines)}
        else:
            yield {"chunk": "No open todo matches that — nothing was changed."}
        yield {"done": True}

    async def _commitments_chat(self):
        """Scan sent mail (a pull — the user just asked) and answer with the
        pending suggestions. Accept/dismiss lives in the Todos screen."""
        try:
            res = await commitments.scan(self._llm, self._mail_store,
                                         self._suggestions)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        pending = self._suggestions.pending()
        if not pending:
            yield {"chunk": ("No outstanding commitments found in your sent "
                             f"mail (scanned {res['scanned']} new messages).")}
        else:
            lines = ["Commitments from your sent mail, waiting for your "
                     "confirmation in the Todos screen:"]
            for s in pending:
                due = f" (due {s['due_date']})" if s["due_date"] else ""
                src = f" — from “{s['subject']}”" if s["subject"] else ""
                lines.append(f"• {s['text']}{due}{src}")
            yield {"chunk": "\n".join(lines)}
        yield {"done": True}

    def _briefing_sections(self) -> str:
        """Today's data from the three caches — unavailable subsystems get
        their honest markers instead of being skipped."""
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        events = (self._calendar.list_range(today, today)
                  if self._calendar is not None else [])
        if self._mail_store is not None:
            unread, counts = self._mail_store.unread(limit=8), self._mail_store.counts()
        else:
            unread, counts = [], {"total": 0, "unread": 0}
        return build_sections(
            events, self._todos.open_todos(), unread, counts, now,
            cal_connected=(self._calendar is not None and self._calendar.connected),
            mail_connected=(self._mail is not None and self._mail.connected),
            mail_syncing=(self._mail is not None and self._mail.syncing))

    async def _briefing_chat(self):
        try:
            async for chunk in compose_briefing(self._llm, self._briefing_sections()):
                yield {"chunk": chunk}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}

    async def _triage_chat(self):
        """One bucketing pass over unread + recent inbox from the mirror; the
        digest is rendered from the rows, so names can't be invented."""
        if self._mail is not None and not self._mail.connected:
            yield {"chunk": ("Gmail isn't connected yet — run the one-time "
                             "Google setup first, then I can triage your inbox.")}
            yield {"done": True}
            return
        rows, seen = [], set()
        for r in (self._mail_store.unread(limit=triage.UNREAD_LIMIT)
                  + self._mail_store.list_page("inbox", limit=triage.INBOX_LIMIT)):
            if r["id"] not in seen:
                seen.add(r["id"])
                rows.append(r)
        rows = rows[:triage.MAX_MESSAGES]
        if not rows:
            yield {"chunk": ("There's nothing to triage — no unread or recent "
                             "inbox messages in the mirror.")}
            yield {"done": True}
            return
        buckets: dict = {k: [] for k in triage.BUCKETS}
        parsed_any = False
        try:
            for r in rows:
                verdict = await triage.classify(self._llm, r)
                if verdict is not None:
                    parsed_any = True
                    buckets[verdict[0]].append((r, verdict[1]))
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if not parsed_any:
            yield {"chunk": ("I couldn't triage the inbox just now — the "
                             "model's answers didn't parse. Try again.")}
        else:
            yield {"chunk": triage.render_digest(buckets, total=len(rows))}
        yield {"done": True}

    async def _prep_chat(self, message: str):
        """Meeting prep: deterministic event lookup + per-attendee mirror
        history, one narration pass. No cache hit answers honestly."""
        now = datetime.now().astimezone()
        events = self._calendar.list_range(
            now.date().isoformat(),
            (now.date() + timedelta(days=PREP_WINDOW_DAYS)).isoformat())
        event = meeting_prep.find_event(events, message, now)
        if event is None:
            yield {"chunk": ("I don't see that meeting on your calendar in the "
                             f"next {PREP_WINDOW_DAYS} days.")}
            yield {"done": True}
            return
        history = None
        if self._mail_store is not None:
            history = {a["email"]: self._mail_store.involving(
                           a["email"], limit=meeting_prep.MAILS_PER_ATTENDEE)
                       for a in event.get("attendees") or []
                       if a.get("email") and not a.get("self")}
        data = meeting_prep.build_prep_data(event, history, now)
        try:
            async for chunk in meeting_prep.compose_prep(self._llm, data):
                yield {"chunk": chunk}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}

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

    def _slot_context(self, conv_id: int | None) -> str | None:
        """The previous assistant turn, if it was a slot proposal — the
        booking follow-up's extraction resolves 'the first one' against it.
        The originating request rides along so the event title keeps its
        purpose ("call with Chris"), not a generic 'Meeting'."""
        if self._conv is None or conv_id is None:
            return None
        turns = self._conv.history(conv_id, limit=HISTORY_TURNS)
        for i in range(len(turns) - 1, -1, -1):
            if turns[i]["role"] != "assistant":
                continue
            if free_slots.SLOT_HEADER not in turns[i]["content"]:
                return None
            asked = next((t["content"] for t in reversed(turns[:i])
                          if t["role"] == "user"), "")
            prefix = f"The user originally asked: {asked!r}\n" if asked else ""
            return prefix + turns[i]["content"]
        return None

    async def _slots_chat(self, message: str):
        """Deterministic free-slot proposal from the calendar cache — the
        model is never woken; exact times make the booking follow-up safe."""
        if not self._calendar.connected:
            yield {"chunk": ("Your calendar isn't connected yet — run the "
                             "one-time Google setup first, then I can find "
                             "free times.")}
            yield {"done": True}
            return
        now = datetime.now().astimezone()
        duration = free_slots.parse_duration(message)
        start_d, end_d = free_slots.parse_window(message, now)
        events = self._calendar.list_range(start_d.isoformat(), end_d.isoformat())
        hours = ({"day_start": self._scheduling.day_start,
                  "day_end": self._scheduling.day_end}
                 if self._scheduling is not None else {})
        slots = free_slots.find_slots(events, duration_min=duration,
                                      start_date=start_d, end_date=end_d,
                                      now=now, **hours)
        if not slots:
            yield {"chunk": (f"No free {duration}-minute stretch between "
                             f"{start_d.isoformat()} and {end_d.isoformat()} "
                             "inside your working hours — try a different "
                             "range or a shorter meeting.")}
        else:
            yield {"chunk": free_slots.render_slots(slots, duration)}
        yield {"done": True}

    async def _create_event_chat(self, message: str, context: str | None = None):
        """NL event creation: extract → validate → confirm dialog → create."""
        now = datetime.now().astimezone()
        try:
            proposal, err = await propose_event(self._llm, message, now=now,
                                                context=context)
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

    async def _gated_delete(self, event: dict):
        """Confirm-over-IPC then delete a cached event from Google Calendar.
        UI one-shot only — the model is never offered delete_event."""
        attendees = [a for a in event.get("attendees") or [] if not a.get("self")]
        rows = [("Title", event.get("title") or "Untitled"),
                ("When", _event_when(event)),
                ("Calendar", event.get("calendar_name") or event["calendar_id"])]
        if attendees:
            names = ", ".join(a.get("name") or a.get("email") for a in attendees)
            rows.append(("Attendees", f"{names} — they will be notified of the "
                                      "cancellation"))
        confirm_id = self._confirm.begin()
        yield {"confirm_request": {
                   "icon": "▲", "title": "Delete calendar event",
                   "intro": "Lumen will permanently delete this event from "
                            "your Google Calendar.",
                   "rows": rows, "confirm_label": "Delete event"},
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            yield {"result": {"deleted": False,
                              "message": "Cancelled — nothing was deleted."}}
            return
        try:
            await self._bridge.ensure_started()
        except Exception:
            log.exception("MCP bridge unavailable for event deletion")
            yield {"result": {"deleted": False,
                              "message": "calendar tools are unavailable right now"}}
            return
        args = {"event_id": event["id"], "calendar_id": event["calendar_id"],
                "notify_attendees": bool(attendees)}
        start_t = time.monotonic()
        try:
            text = await self._bridge.call("delete_event", args)
            ok = True
        except Exception as e:
            text, ok = f"tool error: {e}", False
            if not isinstance(e, ToolCallError):
                log.exception("delete_event failed unexpectedly")
        if self._tool_log is not None:
            self._tool_log.write("delete_event", args, ok, text,
                                 int((time.monotonic() - start_t) * 1000))
        # same convention as create: only the server's explicit "Deleted:"
        # reply counts — transport success alone doesn't
        ok = ok and text.startswith("Deleted")
        if ok and hasattr(self._calendar, "sync_once"):
            try:
                await self._calendar.sync_once()   # drop the event promptly
            except Exception:
                log.exception("post-delete sync failed")
        yield {"result": {"deleted": ok, "message": text}}

    async def _compose_email_chat(self, message: str):
        """NL draft → editable compose popup → send/cancel. The popup is the
        confirmation: the daemon sends exactly the fields the UI returns, and
        only on an explicit Send."""
        try:
            draft, err = await propose_email(self._llm, message)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if draft is None:
            yield {"chunk": err}     # honest failure is an answer, not an IPC error
            yield {"done": True}
            return
        reply_to = None
        if draft["reply_hint"] and self._mail_store is not None:
            hits = sorted(self._mail_store.search(draft["reply_hint"], limit=5),
                          key=lambda r: r.get("received_at") or "", reverse=True)
            if hits:            # newest plausible match; miss = plain compose
                orig = hits[0]
                reply_to = orig["id"]
                addr = _sender_address(orig.get("sender", ""))
                if addr:
                    draft["to"] = [addr]
                subj = orig.get("subject") or ""
                draft["subject"] = (subj if subj.lower().startswith("re:")
                                    else f"Re: {subj}")
        compose_id = self._confirm.begin()
        yield {"compose_request": {"to": draft["to"], "cc": draft["cc"], "bcc": [],
                                   "subject": draft["subject"], "body": draft["body"],
                                   "reply_to": reply_to},
               "compose_id": compose_id}
        yield {"chunk": "I've drafted it — review the compose window and hit "
                        "Send when it's right."}
        answer = await self._confirm.wait(compose_id, timeout=COMPOSE_TIMEOUT_S)
        if not isinstance(answer, dict):
            yield {"chunk": "\n\nCancelled — nothing was sent."}
            yield {"done": True}
            return
        _ok, text = await self._send_email(answer)
        yield {"chunk": f"\n\n{text}"}
        yield {"done": True}

    async def _send_email(self, fields: dict) -> tuple[bool, str]:
        """Validate + send. No confirm gate: every caller is downstream of the
        compose popup, whose Send click is the confirmation."""
        def addrs(key):
            out = []
            for a in fields.get(key) or []:
                a = str(a).strip()
                if a and not EMAIL.match(a):
                    raise ValueError(f"{a!r} isn't a valid email address — "
                                     "nothing was sent.")
                if a and a not in out:
                    out.append(a)
            return out
        try:
            to, cc, bcc = addrs("to"), addrs("cc"), addrs("bcc")
        except ValueError as e:
            return False, str(e)
        if not to:
            return False, "No valid recipient — nothing was sent."
        body = str(fields.get("body") or "").strip()
        if not body:
            return False, "The email body is empty — nothing was sent."
        subject = str(fields.get("subject") or "").strip()
        ok = await self._mail.send(to, cc, bcc, subject, body,
                                   reply_to=fields.get("reply_to") or None)
        return ((True, "Sent.") if ok
                else (False, "Couldn't reach Gmail — nothing was sent."))

    async def _gated_mail_action(self, type_: str, payload: dict):
        """Confirm-over-IPC then execute an archive / mark-read against Gmail.
        Low-stakes-feeling writes still confirm — consistency over a click."""
        if self._confirm is None:
            yield {"error": "email actions unavailable"}
            return
        row = self._mail_store.get(str(payload.get("id", "")))
        if row is None:
            yield {"error": "email not found"}
            return
        read = bool(payload.get("read", True))
        if type_ == "emails.archive":
            title, verb = "Archive email", "Archive"
            intro = "Lumen will archive this message in your Gmail account."
        else:
            title = "Mark email as read" if read else "Mark email as unread"
            verb = "Mark read" if read else "Mark unread"
            intro = "Lumen will update this message's read state in your Gmail account."
        confirm_id = self._confirm.begin()
        yield {"confirm_request": {
                   "icon": "✉", "title": title, "intro": intro,
                   "rows": [("From", row["sender"]), ("Subject", row["subject"])],
                   "confirm_label": verb},
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            yield {"result": {"ok": False, "message": "Cancelled — nothing was changed."}}
            return
        if type_ == "emails.archive":
            ok = await self._mail.archive(row["id"])
            done = "Archived." if ok else "Couldn't reach Gmail — nothing was changed."
        else:
            ok = await self._mail.mark_read(row["id"], read)
            done = ("Updated." if ok
                    else "Couldn't reach Gmail — nothing was changed.")
        yield {"result": {"ok": ok, "message": done}}

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
            if len(text) > TOOL_RESULT_MAX_CHARS:
                text = text[:TOOL_RESULT_MAX_CHARS] + TRUNCATION_NOTE
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
