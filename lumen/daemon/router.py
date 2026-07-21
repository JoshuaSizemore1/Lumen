"""Request router: chat streaming (plain, tool-augmented, book-rec, and
event-creation paths), sleep, todos.*/books.*/calendar.*/emails.* one-shots,
and confirm.response resolution. Tool-call vs direct-answer classification is
the subject hints below plus a small-model classifier fallback on total regex
miss (daemon/llm/intent.py)."""

import asyncio
import logging
import re
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import date, datetime, timedelta
from pathlib import Path

from lumen.daemon.connectors import free_slots, local_files, mail_rules
from lumen.daemon import local_tools
from lumen.daemon.connectors.capture import classify
from lumen.daemon.llm import (commitments, intent, label_suggest,
                              meeting_prep, notes_qa, triage)
from lumen.daemon.llm.book_recs import recommend
from lumen.daemon.llm.briefing import build_sections, compose_briefing
from lumen.daemon.llm.client import LLMUnavailable
from lumen.daemon.llm.email_compose import (EMAIL, propose_email,
                                            revise_email)
from lumen.daemon.llm.event_create import (confirm_payload, propose_event,
                                           validate_proposal)
from lumen.daemon.llm.file_edit import propose_edit
from lumen.daemon.llm.file_write import propose_file
from lumen.daemon.llm import memory as memory_mod
from lumen.daemon.llm.mcp_bridge import ToolCallError
from lumen.daemon.llm.model_router import FS_WRITE_HINT
from lumen.daemon.llm.rule_author import propose_rule, validate_rule
from lumen.daemon.write_gate import confirm_payload as write_confirm_payload

log = logging.getLogger(__name__)

# "still have open" / "outstanding" / "left to do" say todo without the word:
# "Show me everything I still have open" used to depend on the intent
# classifier guessing right, where a regex is deterministic and free (eval gap,
# 2026-07-18). Bare "open" stays out — it belongs to files and mail far more
# often than to todos.
TODO_HINT = re.compile(
    r"\b(?:todos?|tasks?|due|overdue|outstanding)\b"
    r"|\bstill\s+(?:have\s+)?(?:open|to\s*-?\s*do)\b"
    r"|\bleft\s+to\s+do\b",
    re.IGNORECASE)

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

# "who wrote X" / "author of X" live in TOOL_HINT (they always have), so a book
# question that never says "book" used to attach the FILESYSTEM group and not
# the books one — the model got file tools for a bibliographic question
# (audit 2026-07-19).
BOOK_HINT = re.compile(
    r"\b(books?|novels?|reading|read|rated?|authors?)\b"
    r"|\bwho\s+wrote\b|\bwritten\s+by\b|\bauthor\s+of\b|\bisbn\b",
    re.IGNORECASE)

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
# Month names are in here for the same reason: "what do I have on in September"
# named a period and a subject but matched nothing, so it fell through to the
# intent classifier with no calendar tools attached (audit 2026-07-19).
CAL_HINT = re.compile(
    r"\b(cal[ae]nd\w*|meetings?|events?|schedule|agenda|appointments?|free|busy|"
    r"weeks?|weekends?|today|tomorrow|tonight|upcoming|plans?|"
    r"january|february|april|june|july|august|september|october|november|"
    r"december|next\s+(?:month|year))\b"
    # "march"/"may" only next to a date cue — bare, they are a verb and a modal
    r"|\b(?:on|in)\s+(?:march|may)\b|\b(?:march|may)\s+\d{1,2}\b",
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
# "Delete todo 7, I already did it." — the user's intent is completion, stated
# as a removal. It matched neither route and fell through to context-only chat,
# where nothing can touch a todo, so the todo silently stayed open (eval gap,
# 2026-07-18). Completion rather than deletion is deliberate: it is the
# recoverable reading, and "I already did it" is completion language.
ALREADY_DONE = re.compile(
    r"^\s*(?:delete|remove|drop|clear)\b(.+?)[,;.]?\s*"
    r"(?:because\s+)?i(?:'ve|\s+have)?\s+already\s+"
    r"\b(?:did|done|finished|completed)\b|"
    r"^\s*i(?:'ve|\s+have)?\s+already\s+(?:did|done|finished|completed)\s+(.+)$",
    re.IGNORECASE)
_MATCH_STOP = frozenset(
    "the a an my that this one todo task to as off done complete completed "
    "finished it mark please".split())
# "todo 7", "task #7", "#7", or a query that is nothing but the number.
_ID_REF = re.compile(r"\b(?:todos?|tasks?|items?)\s*#?\s*(\d+)\b|#(\d+)\b|^\s*(\d+)\s*$",
                     re.IGNORECASE)

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

# A request about a FILE is never a calendar event, even when it mentions a
# meeting ("create a meeting note file") — todo-fixes #3. Guards the EVENT
# branch so these reach the fs tool loop (write_file sits behind the write
# gate there) instead of firing the nearest greedy heuristic.
FILE_TASK_HINT = re.compile(
    r"\b(?:files?|folders?|director(?:y|ies)|documents?|markdown|\.md|\.txt)\b",
    re.IGNORECASE,
)

# Same guard, for todos: "add a task to call the plumber" hit EVENT_HINT's
# add + call and became a CALENDAR EVENT (audit 2026-07-19). Deliberately
# narrower than TODO_HINT — that one also matches "due"/"overdue", which would
# wrongly block "book a meeting about the overdue invoice".
TODO_TASK_HINT = re.compile(r"\b(?:todos?|to-?do list|tasks?)\b", re.IGNORECASE)

# A dedicated "write me a document" intent (new-features item 2): an explicit
# write verb aimed at a file noun. The local model authors the whole document
# in one shot (llm/file_write.py) and it lands behind the write gate — nicer
# than the fs tool loop's write_file for content the model must generate. Kept
# ahead of COMPOSE ("write a markdown file about my inbox" also trips
# COMPOSE's write-verb + message alternation) and guarded off RULE ("make a
# rule to file emails…" stays a rule).
FILE_WRITE_HINT = re.compile(
    r"\b(?:write|create|make|save|generate|jot)\b"
    r".{0,80}\b(?:\.md|\.txt|markdown\s+file|text\s+file|"
    r"files?|documents?|notes?\s+file)\b",
    re.IGNORECASE | re.DOTALL,
)

# An explicit filesystem path in the message (absolute, home-, or dot-relative,
# or any dir/name.ext) means the user named a concrete destination. Those stay
# on the fs tool loop, which writes that exact path + content verbatim; the
# dedicated author-a-document route is for "write me a file about X" with no
# path, where the local model invents the filename and body.
EXPLICIT_PATH = re.compile(
    r"(?:^|\s)(?:~|\.\.?)?/\S+"              # /abs, ~/home, ./rel, ../rel
    r"|\b[\w.\-]+/[\w.\-/]*\.\w{1,5}\b",     # dir/name.ext
    re.IGNORECASE)

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

# Rule creation must outrank COMPOSE ("filter all emails relating to X" hits
# COMPOSE's "email…to" alternation) — checked right before it in _chat.
RULE_HINT = re.compile(
    r"\b(?:create|add|make|set\s*up|new)\b.{0,40}\b(?:rule|filter)\b"
    r"|\brule\b.{0,40}\b(?:label|filter|move|file)\b"
    r"|\balways\s+(?:label|file|move|filter)\b",
    re.IGNORECASE | re.DOTALL)

# Compose-shaped requests jump to the draft → popup path before every other
# chat route (EVENT_HINT would steal "draft an email to schedule a meeting").
# Compose requires an explicit write verb (todo-fixes #1): the bare-"email"
# alternation only fires when "email" is used as an imperative VERB (start of
# message/clause or after please/you/then/…), so "email" as a noun after a
# read verb ("look through my email to find…", "did Sam email me back?")
# never opens a compose popup.
COMPOSE_HINT = re.compile(
    r"\b(?:send|write|draft|compose|shoot)\b.{0,60}\b(?:e-?mails?|reply|message)\b"
    r"|\breply(?:ing)?\b.{0,60}\b(?:e-?mails?|saying|telling|that)\b"
    r"|(?:^|[.!?;,]\s*|\b(?:please|you|then|and|now|also|just)\s+)"
    r"e-?mail\b.{0,40}\b(?:to|saying|telling|asking|about)\b",
    re.IGNORECASE | re.DOTALL,
)

# Read-shaped mail requests route to the tool loop, where search_email /
# get_email live (todo-fixes #1) — checked after COMPOSE (a find-then-send
# request matches both and compose, with its mirror lookup, must win) and
# after TRIAGE ("check my email for things needing a reply").
MAIL_READ_HINT = re.compile(
    r"\b(?:look(?:ing)?|go(?:ing)?|dig(?:ging)?|search(?:ing)?|find|check|"
    r"read|scan|show|list)\b.{0,40}\b(?:e-?mails?|inbox|mailbox|gmail)\b",
    re.IGNORECASE | re.DOTALL,
)

# A compose wait is an edit session, not a confirm click.
COMPOSE_TIMEOUT_S = 1800.0

# Deterministic fallback for the find-then-send recipient (todo-fixes #2):
# the 4B often leaves to_hint null, but the message names the person outright.
# Both orders are covered — possessive ("Chris Sizemore's address") and the
# of-form ("the address of Chris Sizemore", "an address for Chris Sizemore"),
# which the possessive-only version missed on the user's own phrasing (audit
# 2026-07-19). Captured spans are stop-word-filtered before the mirror lookup,
# so an over-wide capture just misses instead of mis-addressing.
TO_HINT_FALLBACK = re.compile(
    r"\b([\w'-]+(?:\s+[\w'-]+){0,2})[’']s\s+(?:e-?mail\s*)?(?:address|addresses)\b"
    r"|\b(?:e-?mail\s*)?(?:address|addresses)\s+(?:of|for|belonging\s+to)\s+"
    r"(?:the\s+)?([\w'-]+(?:\s+[\w'-]+){0,2})\b",
    re.IGNORECASE)
# Words that are never part of a person's name. "to"/"all"/"address" matter:
# the capture deliberately runs wide, and an unfiltered filler word would be
# matched as a substring inside a real sender line ("to" inside "Preston").
_ADDR_STOP = frozenset(
    "find get grab look up send use the of for from to all his her him them "
    "their my email e-mail address addresses recent most latest last "
    "and then so that please about regarding re a an".split())

# Triage is a pull ("what needs a reply?"), never a push. Checked after
# COMPOSE so "reply to X saying thanks" still opens the compose popup.
TRIAGE_HINT = re.compile(
    r"\btriage\b|\bneeds?\s+(?:a\s+|an\s+|my\s+)?"
    r"(?:reply|repl(?:y|ies)|response|answer(?:ing)?)\b",
    re.IGNORECASE,
)

# Notes questions go to the semantic index, not the fs tool loop ("notes"
# is a TOOL_HINT word). "my notes folder" stays with the file tools.
NOTES_HINT = re.compile(
    r"\bwhere did i (?:write|note|jot)\b"
    r"|\b(?:my|the) notes?\b(?!\s*(?:folder|director|file))"
    r"|\bnotes? (?:say|mention)\b",
    re.IGNORECASE,
)
NOTES_K = 4

MAIL_HINT = re.compile(
    r"\b(e-?mails?|inbox|unread|gmail|mail|messages?|newsletters?|senders?)\b",
    re.IGNORECASE,
)

# Explicit forget: prune the topic from both memory tiers. Checked ahead of
# every other chat route so "forget the PulteGroup thing" never becomes a
# generic chat turn.
FORGET_HINT = re.compile(
    r"\bforget\b|\bstop remembering\b|\bdelete what you (?:know|remember)\b",
    re.IGNORECASE)

# A follow-up in this shape is a correction (stronger memory signal than a
# routine query): "no, I meant…", "not that", "actually…", "that's wrong".
CORRECTION_HINT = re.compile(
    r"^\s*(?:no[,.\s]|not that\b|actually[,.\s]|that'?s (?:wrong|not right|not what)"
    r"|i meant\b|wrong\b)",
    re.IGNORECASE)

CAL_CONTEXT_DAYS = 14  # chat context window; the cache itself is wider

# How much of the inbox a new rule scans for the "apply to existing" offer.
RULE_SCAN_LIMIT = 500

# Suggest-labels bounds: 15 sequential warm-model verdicts ≈ tens of seconds
# worst case on the serial data channel — the UI disables the button while
# waiting; a bigger cap would stall other one-shots behind it.
SUGGEST_SCAN_LIMIT = 200
SUGGEST_LIMIT = 15
# How many filed messages ground each label's one-line description
# (suggest-labels v2) — local SQL per label, no model cost.
LABEL_PROFILE_ROWS = 3

# Confirm-gated mail writes (title, intro, confirm label, GmailSync verb,
# success message). Delete is Gmail's Trash, not a permanent wipe — the
# dialog says so honestly.
MAIL_GATES = {
    "emails.archive": (
        "Archive email",
        "Lumen will archive this message in your Gmail account.",
        "Archive", "archive", "Archived."),
    "emails.delete": (
        "Delete email",
        "Lumen will move this message to Gmail's Trash — recoverable "
        "there for about 30 days.",
        "Delete", "trash", "Deleted — recoverable in Gmail's Trash."),
}

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
    "laptop, helping with their email, todos, calendar, books, and files. "
    "Email and calendar sync to a local mirror automatically in the background "
    "— nothing for the user to trigger. Use your tools to look things up rather "
    "than guessing; never tell the user you can't access something you have a "
    "tool for. Never pretend to check or look something up: if this conversation "
    "gives you no tool or data for it, say so plainly instead of inventing a "
    "result. To write, send, or reply to an email, a compose window opens with "
    "your draft to review and send — so never claim you can't send email. Prefer "
    "specific, concise answers."
)

# Write-capable tools stay callable by the daemon (after a confirm) but are
# never offered to the model in the generic tool loop — the confirm gate is
# mechanical, not prompt-enforced.
WRITE_TOOLS = frozenset({"create_event", "delete_event"})

# Tool groups = MCP server names. Tools attach by subject-matter group (union
# on multi-subject messages) keyed off the same wide hints that inject context
# — the old split (wide hints for context, narrow verb regexes for tools) let
# a message get context naming search_email with no tools attached, and the
# model role-played the search (live fabrication 2026-07-17). Liberal bias is
# deliberate: a false positive costs a few schema tokens, a miss costs a
# fabricated answer. "todos" is a context-only pseudo-group (no MCP server).
SERVER_GROUPS = frozenset({"fs", "mail", "gcal", "books"})

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


def calendar_context(events: list[dict], now: datetime, window_end: date,
                     has_tool: bool = False) -> str:
    """System-message context: current local time, a bounds statement so the
    model can't guess outside the window, one compact line per event, and an
    explicit empty marker. `has_tool` marks that the gcal tools are attached to
    THIS request; only then may the text name them, and only then does an
    out-of-window date become a tool call instead of a dead end. Without it the
    bounds line stays honest but must not license "not shown" as a final answer
    when the tools are in fact attached (same fabrication guard as
    mail_context)."""
    beyond = ("for another date call list_events over that range; to find an "
              "event by topic when you do not know its date call search_events"
              if has_tool else
              "events outside it are not in this listing")
    lines = [f"Now: {now.strftime('%Y-%m-%d %H:%M')} ({now.strftime('%A')}), "
             f"local timezone UTC{now.strftime('%z')[:3]}:{now.strftime('%z')[3:]}.",
             f"The user's calendar from {now.date().isoformat()} through "
             f"{window_end.isoformat()} — this window only ({beyond}):"]
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
                 syncing: bool = False, brief: bool = False,
                 has_tool: bool = False) -> str:
    """System-message context: unread summary from the local mirror, explicit
    empty/not-connected/still-syncing markers. `brief` drops the enumerated
    unread rows and keeps only the counts — used when mail context rides along
    on a (non-mail-shaped) tool loop purely as grounding, so a file request
    doesn't drag the whole unread list into the prompt. `has_tool` marks that
    search_email is attached to THIS request; only then may the text name it —
    a model told about a tool it doesn't hold role-plays using it (live
    fabrication 2026-07-17)."""
    if not connected:
        return ("Gmail is not connected yet — the user needs to run the one-time "
                "Google setup. Say so if asked about email; do not invent messages.")
    if brief:
        head = (f"The user's mailbox mirror holds {counts['total']} messages, "
                f"{counts['unread']} unread"
                + (" — use the search_email tool to read any of them."
                   if has_tool else "."))
        return (head if not syncing else
                "The first mailbox sync has not finished — the mirror is "
                "incomplete; missing messages are not absent, just not pulled "
                "yet.\n" + head)
    lines = [f"The user's mailbox mirror holds {counts['total']} messages, "
             f"{counts['unread']} unread. Unread messages "
             + ("(only these are shown — use the search_email tool for "
                "anything else):" if has_tool
                else "(only these are shown in this conversation):")]
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


def todo_context(todos: list[dict], today: date, has_tool: bool = False) -> str:
    """System-message context: today's date + one line per open todo, or an
    explicit empty marker so the model can't hallucinate around a blank list.
    `has_tool` marks that the todo tools are attached to THIS request; only
    then do the ids ride (complete_todo needs them) and only then may the text
    name a tool — same fabrication guard as mail_context/calendar_context."""
    lines = [f"Today is {today.isoformat()} ({today.strftime('%A')})."]
    if not todos:
        lines.append("The user has no open todos.")
    else:
        lines.append("The user's open todos:")
        for t in todos:
            due = f"(due {t['due_date']})" if t["due_date"] else "(no due date)"
            tags = f" [{', '.join(t['tags'])}]" if t["tags"] else ""
            ident = f"id={t['id']} " if has_tool else ""
            lines.append(f"- {ident}{t['text']} {due}{tags}")
    if has_tool:
        # Stated here as well as in the tool descriptions: the filesystem
        # tools ride along on the same requests, and a TODO file is the wrong
        # answer every time (todo-fixes #19).
        lines.append("This list is the user's real todo list. Add to it with "
                     "add_todo and close items with complete_todo — never by "
                     "writing or editing a file.")
    return "\n".join(lines)


class Router:
    def __init__(self, llm, todos, books=None, *, calendar=None, mail=None,
                 canvas=None,
                 mail_store=None, bridge=None, confirm=None, write_gate=None,
                 model_router=None, tool_log=None, conversations=None,
                 suggestions=None, scheduling=None, notes=None, write_dir=None,
                 manabi=None,
                 memory=None, memory_path=None, memory_cap=4000,
                 procedures=None, distill_trigger=None,
                 config=None, rules=None,
                 max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._calendar = calendar   # CalendarSync facade: list_range/last_sync/connected
        self._mail = mail           # GmailSync facade: poll_forever/connected
        self._canvas = canvas       # CanvasSync: set_session/clear_session/connected/last_sync
        self._mail_store = mail_store   # EmailStore — local mirror the UI/chat read from
        self._bridge = bridge
        self._confirm = confirm     # ConfirmBroker — gates every external write
        self._write_gate = write_gate   # WriteGate — per-file grants for fs writes
        self._model_router = model_router
        self._tool_log = tool_log
        self._conv = conversations  # ConversationStore — transcript log + multi-turn state
        self._suggestions = suggestions  # SuggestionStore — commitment tracking
        self._scheduling = scheduling    # SchedulingConfig — proposable hours
        self._notes = notes              # NotesStore — semantic notes index
        # Where chat-authored files land (new-features item 2). Defaults to the
        # notes Q&A folder so a written file is immediately searchable there.
        self._write_dir = Path(write_dir) if write_dir else (
            Path.home() / "Documents" / "Notes")
        self._manabi = manabi            # ManabiStatus — Japanese-study nudge
        self._memory = memory                # MemoryLog — tier-1 raw log
        self._memory_path = memory_path      # Path to memory.md
        self._memory_cap = memory_cap
        self._procedures = procedures        # ProcedureStore
        self._distill_trigger = distill_trigger  # callable() scheduling a run
        self._config = config    # loaded Config for the read-only settings.get
        self._rules = rules      # RuleStore — deterministic inbox rules
        self._max_iterations = max_iterations

    def on_disconnect(self) -> None:
        """A UI connection died — deny anything still waiting on a dialog."""
        if self._confirm is not None:
            self._confirm.deny_all()

    def _warm_prefix(self) -> list[dict]:
        """The always-present prefix (identity + memory blob) plus a trivial
        turn, for `warm()` to prime. Every real request's system message starts
        with these exact tokens, so caching them here lets the first query skip
        the CPU-bound prompt-eval that dominates cold start (Phase 11). Per-query
        context (todo/calendar/fs/mail) is keyword-gated and varies, so it isn't
        primed — only the constant head is worth caching."""
        context = [IDENTITY]
        if self._memory_path is not None:
            mem = memory_mod.memory_context(self._memory_path, self._memory_cap)
            if mem:
                context.append(mem)
        return [{"role": "system", "content": "\n\n".join(context)},
                {"role": "user", "content": "hi"}]

    def _build_messages(self, message: str, history: list[dict],
                        groups: frozenset[str] = frozenset(),
                        extra: str | None = None) -> list[dict]:
        """A constant identity block + keyword-gated per-query context as the
        system message, then the conversation history (which ends with the
        current user turn). Context is keyed on the current message's subject
        hints OR an attached group in `groups` (= the tool groups actually
        attached to this request; the classifier can attach groups the regexes
        missed, and a tool-engaged follow-up carries every group). Grounding
        that names a tool rides only when its group is attached — never coach
        the model toward a tool it doesn't hold."""
        context = [IDENTITY]
        if self._memory_path is not None:
            mem = memory_mod.memory_context(self._memory_path, self._memory_cap)
            if mem:
                context.append(mem)
        if self._procedures is not None:
            proc = self._procedures.match(message)
            if proc:
                context.append(
                    "The user has a saved routine that matches this request. "
                    "Follow its steps in order:\n" + proc["text"])
        if TODO_HINT.search(message) or "todos" in groups:
            context.append(todo_context(self._todos.open_todos(), date.today(),
                                        has_tool="todos" in groups))
        if self._books is not None and (BOOK_HINT.search(message)
                                        or "books" in groups):
            context.append(self._books.catalog_context())
        if self._calendar is not None and (CAL_HINT.search(message)
                                           or "gcal" in groups):
            now = datetime.now().astimezone()
            end = now.date() + timedelta(days=CAL_CONTEXT_DAYS)
            context.append(calendar_context(
                self._calendar.list_range(now.date().isoformat(), end.isoformat()),
                now, end, has_tool="gcal" in groups))
        mail_shaped = bool(MAIL_HINT.search(message))
        if self._mail_store is not None and (mail_shaped or "mail" in groups):
            # Non-mail-shaped tool loops still get the counts as grounding;
            # the search_email pointer only rides when the tool itself does.
            context.append(mail_context(
                self._mail_store.unread(limit=10), self._mail_store.counts(),
                self._mail.connected if self._mail is not None else False,
                syncing=self._mail.syncing if self._mail is not None else False,
                brief=not mail_shaped, has_tool="mail" in groups))
        if self._bridge is not None and "fs" in groups:
            context.append(fs_context(Path.home()))
        if extra:
            # Per-surface grounding (the Files screen's cwd/open-file block) —
            # last, so it sits closest to the conversation it grounds.
            context.append(extra)
        return [{"role": "system", "content": "\n\n".join(context)}] + history

    def _messages_for(self, message: str, conv_id: int | None,
                      groups: frozenset[str] = frozenset(),
                      extra: str | None = None) -> list[dict]:
        """Prompt messages for a chat turn: the current user message is already
        persisted, so the store's (capped) history ends with it. Without a store
        this degrades to a single stateless user turn."""
        if self._conv is not None and conv_id is not None:
            history = self._conv.history(conv_id, limit=HISTORY_TURNS)
        else:
            history = [{"role": "user", "content": message}]
        return self._build_messages(message, history, groups, extra)

    def _subject_groups(self, message: str) -> set[str]:
        """Which tool groups this message's subject matter wants — the same
        wide hints that key context injection, never narrower verb shapes."""
        groups = set()
        if self._mail_store is not None and MAIL_HINT.search(message):
            groups.add("mail")
        if self._calendar is not None and CAL_HINT.search(message):
            groups.add("gcal")
        if self._books is not None and BOOK_HINT.search(message):
            groups.add("books")
        if TOOL_HINT.search(message) or FS_WRITE_HINT.search(message):
            groups.add("fs")
        if TODO_HINT.search(message):
            groups.add("todos")
        return groups

    def _engaged_groups(self, conv_id: int | None) -> set[str]:
        """A tool-engaged conversation keeps every group on a bare follow-up
        ('and delete it') — same all-tools behavior as before groups existed."""
        if (self._conv is not None and conv_id is not None
                and self._conv.is_tool_engaged(conv_id)):
            return set(SERVER_GROUPS) | {"todos"}
        return set()

    @staticmethod
    def _group_subsystem(groups) -> str:
        """Memory-log subsystem name for a group set. fs outranks mail because
        mail rides along on every tool loop as grounding."""
        for group, name in (("fs", "files"), ("gcal", "calendar"),
                            ("books", "books"), ("mail", "email"),
                            ("todos", "todos")):
            if group in groups:
                return name
        return "chat"

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
            async with aclosing(self._chat(
                    message, conv_id, cwd=payload.get("cwd"),
                    open_file=payload.get("open_file"))) as gen:
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
        elif type_ == "manabi.status":
            # Dashboard nudge one-shot: a cheap read-only DB peek, no LLM.
            yield {"result": (self._manabi.status() if self._manabi is not None
                              else {"configured": False, "due": None,
                                    "last_review": None})}
        elif type_ == "settings.get":
            # Read-only snapshot of the loaded config for the Settings screen.
            if self._config is None:
                yield {"error": "settings unavailable"}
            else:
                from .settings_snapshot import build_settings_snapshot
                yield {"result": build_settings_snapshot(self._config)}
        elif type_ == "canvas.set_session":
            # UI hands the browser session (cookies) to the in-memory poller.
            # Never a password — the daemon only ever sees cookies.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                self._canvas.set_session(payload.get("cookies", {}))
                yield {"done": True}
        elif type_ == "canvas.status":
            # Live poller state (not config) for the Settings connect row.
            if self._canvas is None:
                yield {"result": {"connected": False, "last_sync": None,
                                  "enabled": False}}
            else:
                yield {"result": {
                    "connected": self._canvas.connected,
                    "last_sync": self._canvas.last_sync(),
                    "enabled": (self._config.canvas.enabled
                                if self._config is not None else False)}}
        elif type_ == "canvas.disconnect":
            if self._canvas is not None:
                self._canvas.clear_session()
            yield {"done": True}
        elif type_ == "sleep":
            await self._llm.unload()
            yield {"done": True}
        elif type_ == "warm":
            # Fire-and-forget preload (launcher summon) so the next query isn't
            # a cold start. Prime the stable identity/memory prefix so the first
            # real query reuses its KV cache instead of re-evaluating it on CPU
            # (Phase 11). No response — the UI doesn't wait on it.
            await self._llm.warm(self._warm_prefix())
        elif type_ == "confirm.response":
            # Silent ack: the answer unblocks whichever handler is awaiting it.
            # An approval may carry extras (rule-create checkbox) — those travel
            # as a truthy dict so bool-only waiters keep working.
            if self._confirm is not None:
                try:
                    answer: bool | dict = bool(payload["approved"])
                    if answer and "check" in payload:
                        answer = {"approved": True, "check": bool(payload["check"])}
                    self._confirm.resolve(int(payload["confirm_id"]), answer)
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
                added = self._books.add(
                    payload.get("title", ""), payload.get("author"),
                    int(payload["rating"]) if payload.get("rating") else None,
                    payload.get("notes"))
            except (TypeError, ValueError) as e:
                yield {"error": str(e)}
            else:
                self._log("books", "query", {"action": "add",
                                             "title": payload.get("title", "")})
                yield {"result": added}
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
                    if created:
                        self._log("calendar", "query", {"action": "create_event"})
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
                rows = self._todos.add(payload.get("text", ""))
            except ValueError as e:
                yield {"error": str(e)}
            else:
                self._log("todos", "query", {"action": "add",
                                             "text": payload.get("text", "")[:200]})
                yield {"result": rows}
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
                self._log("email", "correction",
                          {"action": "dismiss_suggestion", "id": payload.get("id")})
                yield {"result": {"suggestions": self._suggestions.pending()}}
        elif (type_.startswith("emails.")
              or type_ in ("mail.refresh", "mail.suggest_labels")):
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
                # keep the caller's scope so a label view reloads as itself
                type_, payload = "emails.list", {
                    k: payload[k] for k in ("filter", "label", "limit", "offset")
                    if k in payload}
            if type_ == "emails.list":
                filt = payload.get("filter", "inbox")
                label_id = None
                if filt == "label":
                    label_id = self._mail_store.label_id(
                        str(payload.get("label", "")))
                    if label_id is None:
                        yield {"error": "unknown label"}
                        return
                yield {"result": {
                    "emails": self._with_label_names(self._mail_store.list_page(
                        filt, int(payload.get("limit", 50)),
                        int(payload.get("offset", 0)), label_id=label_id)),
                    "labels": self._present_user_labels(),
                    "connected": self._mail.connected,
                    "syncing": self._mail.syncing,
                    "last_sync": self._mail.last_sync(),
                    "counts": self._mail_store.counts()}}
            elif type_ == "emails.search":
                yield {"result": {"emails": self._with_label_names(
                    self._mail_store.search(payload.get("query", ""),
                                            int(payload.get("limit", 50))))}}
            elif type_ == "emails.get":
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is None:
                    yield {"error": "email not found"}
                    return
                if row.get("body_html") is None:
                    # Mirrored before the body_html column: backfill this one
                    # message now. Failure just falls back to the plain body.
                    await self._mail.fetch_html(row["id"])
                    row = self._mail_store.get(row["id"])
                yield {"result": row}
            elif type_ == "emails.unread":
                yield {"result": {
                    "emails": self._mail_store.unread(int(payload.get("limit", 10))),
                    "connected": self._mail.connected}}
            elif type_ in MAIL_GATES:   # emails.archive / emails.delete
                async for ev in self._gated_mail_action(type_, payload):
                    yield ev
            elif type_ == "emails.mark_read":
                # Read-state writes stopped confirming 2026-07-15 — opening a
                # message auto-marks it read, so the explicit button can't
                # rank a dialog above the same silent write.
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is None:
                    yield {"error": "email not found"}
                    return
                ok = await self._mail.mark_read(row["id"],
                                                bool(payload.get("read", True)))
                yield {"result": {"ok": ok, "message": "Updated." if ok else
                                  "Couldn't reach Gmail — nothing was changed."}}
            elif type_ == "emails.auto_read":
                # Dwell-timer read receipt: silent and idempotent.
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is not None and not row["is_read"]:
                    await self._mail.mark_read(row["id"], True)
                yield {"result": {"ok": True}}
            elif type_ == "mail.suggest_labels":
                # Explicit press only: per-message verdicts (triage lesson —
                # the 4B loses a 20-message batch), one load/unload cycle.
                user = self._mail_store.user_labels()
                if not user:
                    yield {"error": "no Gmail labels yet — create a rule or "
                                    "a label first"}
                    return
                names = [l["name"] for l in user]
                ids = {l["id"] for l in user}
                # v2 grounding: one deterministic description per label from
                # mail already filed under it — local SQL, no model cost.
                profiles = {l["name"]: label_suggest.describe_label(
                                l["name"], self._mail_store.list_page(
                                    "label", limit=LABEL_PROFILE_ROWS,
                                    label_id=l["id"]))
                            for l in user}
                rows = [r for r in self._mail_store.list_page(
                            "inbox", limit=SUGGEST_SCAN_LIMIT)
                        if not ids.intersection(r["labels"])][:SUGGEST_LIMIT]
                suggestions = {}
                try:
                    for r in rows:
                        name = await label_suggest.suggest(self._llm, r, names,
                                                           profiles)
                        if name:
                            suggestions[r["id"]] = name
                except LLMUnavailable as e:
                    yield {"error": str(e)}
                    return
                yield {"result": {"suggestions": suggestions,
                                  "scanned": len(rows)}}
            elif type_ == "emails.apply_label":
                # One-tap accept (suggestions): the tap IS the confirmation.
                mid = str(payload.get("id", ""))
                name = str(payload.get("label", "")).strip()
                if not name or self._mail_store.get(mid) is None:
                    yield {"error": "emails.apply_label needs {id, label}"}
                    return
                ok = await self._mail.apply_label(mid, name)
                yield {"result": {"ok": ok, "message":
                       f"Labeled {name} — moved out of inbox." if ok else
                       "Couldn't reach Gmail — nothing was changed."}}
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
        elif type_.startswith("rules."):
            if (self._rules is None or self._mail is None
                    or self._mail_store is None or self._confirm is None):
                yield {"error": "mail rules unavailable"}
                return
            if type_ == "rules.list":
                yield {"result": {"rules": self._rules.list_all(),
                                  "labels": [l["name"] for l in
                                             self._mail_store.user_labels()]}}
            elif type_ == "rules.create":
                rule = validate_rule(payload.get("rule") or {})
                if rule is None:
                    yield {"error": "a rule needs a label and at least one condition"}
                    return
                out = {}
                async for ev in self._gated_rule_save(rule):
                    if "_saved" in ev:
                        out = ev
                    else:
                        yield ev
                msg = (f"Rule saved — {out.get('_applied', 0)} existing "
                       "email(s) labeled." if out.get("_saved")
                       else "Cancelled — nothing was saved.")
                yield {"result": {"ok": bool(out.get("_saved")), "message": msg,
                                  "rules": self._rules.list_all()}}
            elif type_ == "rules.update":
                rule = validate_rule(payload.get("rule") or {})
                try:
                    rid = int(payload["id"])
                except (KeyError, TypeError, ValueError):
                    rid, rule = 0, None
                if rule is None or self._rules.update(rid, rule) is None:
                    yield {"error": "rules.update needs {id, rule}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            elif type_ == "rules.toggle":
                try:
                    self._rules.set_enabled(int(payload["id"]),
                                            bool(payload["enabled"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "rules.toggle needs {id, enabled}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            elif type_ == "rules.delete":
                try:
                    self._rules.delete(int(payload["id"]))
                except (KeyError, TypeError, ValueError):
                    yield {"error": "rules.delete needs {id}"}
                    return
                yield {"result": {"ok": True, "rules": self._rules.list_all()}}
            else:
                yield {"error": f"unknown request type: {type_}"}
        elif type_ == "files.propose_edit":
            # Files-screen ✎ Edit (new-features item 7): one generation over
            # the current EDITOR BUFFER (not disk — the edit applies to what
            # the user sees). Nothing is written here; the UI shows the diff
            # and the user's Apply performs the save.
            path = str(payload.get("path") or "")
            content = payload.get("content")
            instruction = str(payload.get("instruction") or "").strip()
            if content is None or not instruction:
                yield {"error": "files.propose_edit needs {path, content, instruction}"}
                return
            try:
                revised, err = await propose_edit(
                    self._llm, Path(path).name or "file", str(content),
                    instruction)
            except LLMUnavailable as e:
                yield {"error": str(e)}
                return
            if revised is None:
                yield {"result": {"ok": False, "message": err}}
            else:
                self._log("files", "query",
                          {"action": "propose_edit", "path": path})
                yield {"result": {"ok": True, "content": revised}}
        elif type_ == "memory.learned":
            # Settings "what Lumen has learned": the distilled memory.md
            # verbatim + its last-updated stamp — the view reads exactly the
            # file the distiller writes (todo-fixes #10a).
            if self._memory_path is None:
                yield {"error": "memory unavailable"}
            else:
                p = Path(self._memory_path)
                try:
                    text = p.read_text().strip()
                    updated = datetime.fromtimestamp(
                        p.stat().st_mtime).isoformat(timespec="minutes")
                except OSError:
                    text, updated = "", None
                yield {"result": {"text": text, "updated_at": updated,
                                  "path": str(p)}}
        elif type_ == "memory.procedures":
            if self._procedures is None:
                yield {"error": "procedures unavailable"}
            else:
                yield {"result": {"proposed": self._procedures.list_proposed(),
                                  "active": self._procedures.list_active()}}
        elif type_ in ("memory.approve_procedure", "memory.dismiss_procedure",
                       "memory.remove_procedure"):
            if self._procedures is None:
                yield {"error": "procedures unavailable"}
                return
            slug = str(payload.get("slug") or "")
            if not slug:
                yield {"error": f"{type_} needs {{slug}}"}
                return
            if type_ == "memory.approve_procedure":
                ok = self._procedures.approve(slug)
            elif type_ == "memory.dismiss_procedure":
                ok = self._procedures.dismiss(slug)
            else:
                ok = self._procedures.remove(slug)
            yield {"result": {"ok": ok,
                              "proposed": self._procedures.list_proposed(),
                              "active": self._procedures.list_active()}}
        else:
            yield {"error": f"unknown request type: {type_}"}

    async def _chat(self, message: str, conv_id: int | None,
                    cwd: str | None = None, open_file: str | None = None):
        """Pick the chat sub-path, stream it through, write-through the
        assistant turn, and log the interaction for the memory system."""
        subsystem = "chat"
        skip_log = False
        if cwd:
            # Files-screen ask (new-features item 6): the prompt box is
            # file-scoped by construction, so it skips the regex routing and
            # goes straight to the tool loop grounded in what the user is
            # looking at. todos always ride — fs tools without todo tools is
            # how "add a todo" became a TODO file (todo-fixes #19). Rebuilt
            # per turn, so navigating and re-asking reflects the new folder.
            sub = self._chat_with_tools(
                message, conv_id,
                groups=frozenset({"fs", "todos"}
                                 | self._subject_groups(message)),
                extra_context=local_files.ask_context(cwd, open_file))
            subsystem = "files"
        elif (self._memory_path is not None and self._memory is not None
                and FORGET_HINT.search(message)):
            # The forget turn must not be logged — its own text names the topic
            # and would re-seed what was just pruned.
            sub, subsystem, skip_log = self._forget_chat(message), "chat", True
        elif m := TODO_ADD.match(message):
            sub, subsystem = self._nl_add_chat(m.group(1).strip()), "todos"
        elif m := MARK_DONE.match(message):
            sub, subsystem = self._mark_done_chat(m.group(1)), "todos"
        elif m := ALREADY_DONE.match(message):
            sub, subsystem = self._mark_done_chat(m.group(1) or m.group(2) or ""), "todos"
        elif self._calendar is not None and PREP_HINT.search(message):
            sub, subsystem = self._prep_chat(message), "calendar"
        elif (self._suggestions is not None and self._mail_store is not None
                and PROMISE_HINT.search(message)):
            sub, subsystem = self._commitments_chat(), "email"
        elif BRIEFING_HINT.search(message):
            sub, subsystem = self._briefing_chat(), "chat"
        elif (self._rules is not None and self._mail is not None
                and self._mail_store is not None and self._confirm is not None
                and RULE_HINT.search(message)):
            sub, subsystem = self._create_rule_chat(message), "email"
        elif (self._confirm is not None and FILE_WRITE_HINT.search(message)
                and not RULE_HINT.search(message)
                and not EXPLICIT_PATH.search(message)):
            sub, subsystem = self._write_file_chat(message), "files"
        elif (self._confirm is not None and self._mail is not None
                and COMPOSE_HINT.search(message)):
            sub, subsystem = self._compose_email_chat(message), "email"
        elif self._mail_store is not None and TRIAGE_HINT.search(message):
            sub, subsystem = self._triage_chat(), "email"
        elif (self._bridge is not None and self._mail_store is not None
                and MAIL_READ_HINT.search(message)):
            # Read-shaped mail request: the tool loop owns inbox search/QA.
            sub = self._chat_with_tools(
                message, conv_id,
                groups=frozenset(self._subject_groups(message) | {"mail"}))
            subsystem = "email"
        elif self._calendar is not None and SLOT_HINT.search(message):
            sub, subsystem = self._slots_chat(message), "calendar"
        elif (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and BOOKING_HINT.search(message)
                and not FILE_TASK_HINT.search(message)
                and (slot_ctx := self._slot_context(conv_id))):
            sub, subsystem = self._create_event_chat(message, context=slot_ctx), "calendar"
        elif (self._confirm is not None and self._bridge is not None
                and self._calendar is not None and EVENT_HINT.search(message)
                and not FILE_TASK_HINT.search(message)
                and not TODO_TASK_HINT.search(message)):
            sub, subsystem = self._create_event_chat(message), "calendar"
        elif self._notes is not None and NOTES_HINT.search(message):
            sub, subsystem = self._notes_chat(message), "chat"
        elif (self._books is not None and self._bridge is not None
              and REC_HINT.search(message)):
            sub, subsystem = self._recommend_chat(message), "books"
        elif self._bridge is not None and (groups := (
                self._subject_groups(message) or self._engaged_groups(conv_id))):
            if self._mail_store is not None:
                groups.add("mail")   # ride-along: two small schemas, and every
                                     # tool loop can be asked a mail follow-up
            sub = self._chat_with_tools(message, conv_id,
                                        groups=frozenset(groups))
            subsystem = self._group_subsystem(groups)
        else:
            sub, subsystem = None, self._infer_subsystem(message)
            if self._bridge is not None:
                # Total regex miss with tools available: one small classifier
                # call on the resident model beats a blind plain chat that
                # could get coached into role-playing a lookup.
                labels = await intent.classify(self._llm, message)
                if ("send_email" in labels and self._confirm is not None
                        and self._mail is not None):
                    sub, subsystem = self._compose_email_chat(message), "email"
                elif ("create_event" in labels and self._confirm is not None
                        and self._calendar is not None):
                    sub, subsystem = self._create_event_chat(message), "calendar"
                else:
                    available = {
                        "mail": self._mail_store is not None,
                        "gcal": self._calendar is not None,
                        "books": self._books is not None,
                        "fs": True, "todos": True,
                    }
                    label_groups = {"email": "mail", "calendar": "gcal",
                                    "files": "fs", "todos": "todos",
                                    "books": "books"}
                    groups = {label_groups[l] for l in labels
                              if l in label_groups and available[label_groups[l]]}
                    if groups & (SERVER_GROUPS | {"todos"}):
                        if self._mail_store is not None:
                            groups.add("mail")
                        sub = self._chat_with_tools(message, conv_id,
                                                    groups=frozenset(groups))
                        subsystem = self._group_subsystem(groups)
            if sub is None:
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
            try:
                self._conv.add_message(conv_id, "assistant", "".join(acc),
                                       tools or None)
                if tools:                     # this thread is now tool-shaped for its follow-ups
                    self._conv.mark_tool_engaged(conv_id)
            except sqlite3.IntegrityError:
                # Thread deleted mid-stream (sidebar ✕ during a reply): the
                # user already saw the streamed text — nothing left to store,
                # and it must not error an otherwise-finished stream.
                pass
        if not skip_log:
            kind = "correction" if CORRECTION_HINT.search(message) else "query"
            self._log(subsystem, kind, {"message": message[:300], "tools": tools or None})
            if self._distill_trigger is not None:
                self._distill_trigger()

    async def _forget_chat(self, message: str):
        """Map the user's topic to matching memory lines, remove them from the
        file and delete matching raw-log rows so a later distillation can't
        re-learn it. Honest when nothing matched."""
        blob = memory_mod.load(self._memory_path, self._memory_cap)
        if not blob:
            yield {"chunk": "There's nothing in my memory to forget yet."}
            yield {"done": True}
            return
        system = ("The user wants you to forget something. Given their request "
                  "and the current memory bullets, reply with ONLY the shortest "
                  "keyword or phrase (verbatim from a bullet) identifying what to "
                  "remove — no explanation. If nothing matches, reply NONE.")
        user = f"Request: {message}\n\nMemory:\n{blob}"
        topic = ""
        try:
            async for chunk in self._llm.chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": user}]):
                topic += chunk
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        topic = topic.strip().strip('"').strip()
        removed = []
        if topic and topic.upper() != "NONE":
            parsed = memory_mod.parse(blob)
            kept = {}
            for head, bullets in parsed.items():
                keep, drop = [], []
                for b in bullets:
                    (drop if topic.lower() in b.lower() else keep).append(b)
                kept[head] = keep
                removed.extend(drop)
            if removed:
                memory_mod.write(self._memory_path, memory_mod.render(kept))
                self._memory.delete_matching(topic)
        if removed:
            listed = "\n".join(f"• {b[2:]}" for b in removed)
            yield {"chunk": f"Forgotten:\n{listed}"}
        else:
            yield {"chunk": "I couldn't find anything matching that in my memory."}
        yield {"done": True}

    async def _plain_chat(self, message: str, conv_id: int | None,
                          groups: frozenset[str] = frozenset()):
        messages = self._messages_for(message, conv_id, groups)
        try:
            async for chunk in self._llm.chat(messages):
                yield {"chunk": chunk}
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        yield {"done": True}

    def _log(self, subsystem: str, kind: str, detail: dict) -> None:
        if self._memory is not None:
            self._memory.log(subsystem, kind, detail)

    @staticmethod
    def _infer_subsystem(message: str) -> str:
        """For the plain/fallback path, name the subsystem from the hint that
        would have injected its context (the specialized routes name their own)."""
        if TODO_HINT.search(message):
            return "todos"
        if CAL_HINT.search(message):
            return "calendar"
        if MAIL_HINT.search(message):
            return "email"
        if BOOK_HINT.search(message):
            return "books"
        return "chat"

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
        """'mark X done': match open todos by id when the user cites one
        ('todo 7', '#7'), otherwise fuzzy-match on words; one match toggles,
        several list themselves instead of guessing, none answers honestly."""
        open_todos = self._todos.open_todos()
        matches = []
        # An id reference has to be marked as one — a bare number is far more
        # likely to be part of the text ("mark 3 eggs done") than an id.
        ref = _ID_REF.search(query)
        cited = next((g for g in ref.groups() if g), None) if ref else None
        if cited is not None:
            matches = [t for t in open_todos if str(t["id"]) == cited]
        if not matches and cited is None:
            words = [w for w in re.findall(r"[\w']+", query.lower())
                     if w not in _MATCH_STOP]
            if words:
                for t in open_todos:
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
            mail_syncing=(self._mail is not None and self._mail.syncing),
            manabi_due=(self._manabi is not None
                        and self._manabi.status(now)["due"] is True))

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

    async def _notes_chat(self, message: str):
        """Semantic notes Q&A: on-demand mtime reindex, KNN over sqlite-vec,
        one narration pass over the retrieved passages + their paths."""
        try:
            await self._notes.reindex()
            hits = (await self._notes.search(message, k=NOTES_K)
                    if self._notes.file_count() else [])
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if self._notes.file_count() == 0:
            yield {"chunk": (f"You have no notes yet — I looked in "
                             f"{self._notes.folder}. Drop .md or .txt files "
                             "there and ask again.")}
            yield {"done": True}
            return
        if not hits:
            yield {"chunk": "Nothing in your notes matches that."}
            yield {"done": True}
            return
        data = notes_qa.build_notes_data(hits)
        try:
            async for chunk in notes_qa.compose_answer(self._llm, message, data):
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
            self._log("calendar", "correction",
                      {"action": "declined_create", "title": proposal.get("title")})
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
            self._log("calendar", "correction", {"action": "declined_delete"})
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

    async def _create_rule_chat(self, message: str):
        """NL → structured rule (local model) → confirm overlay → save/backfill.
        The overlay's rows are the plain-language rendering of the parsed rule."""
        labels = [l["name"] for l in self._mail_store.user_labels()]
        try:
            rule, err = await propose_rule(self._llm, message, labels)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if rule is None:
            yield {"chunk": err}     # honest failure is an answer, not an IPC error
            yield {"done": True}
            return
        out = {}
        async for ev in self._gated_rule_save(rule):
            if "_saved" in ev:
                out = ev
            else:
                yield ev
        if out.get("_saved"):
            extra = (f" I also labeled {out['_applied']} matching email(s) "
                     "already in your inbox." if out.get("_applied") else "")
            yield {"chunk": f"Done — new mail matching this gets “{rule['label']}” "
                            f"and leaves your inbox.{extra}"}
        else:
            yield {"chunk": "Cancelled — no rule was saved."}
        yield {"done": True}

    async def _write_file_chat(self, message: str):
        """NL → whole document (local model) → write-gate confirm → save to
        disk. The dialog is the write gate's own payload (louder when the
        target sits outside the notes folder or overwrites an existing file);
        approving also grants the exact path so a later fs-tool edit to the
        same file doesn't re-ask, keeping that dialog's promise true."""
        try:
            prop, err = await propose_file(self._llm, message)
        except LLMUnavailable as e:
            yield {"error": str(e)}
            return
        if prop is None:
            yield {"chunk": err}     # honest failure is an answer, not an IPC error
            yield {"done": True}
            return
        base = self._write_dir
        if prop["path"]:
            p = Path(prop["path"]).expanduser()
            base = p if p.is_absolute() else self._write_dir / p
        target = base / prop["filename"]
        content = prop["content"]

        try:
            target.resolve().relative_to(self._write_dir.resolve())
            outside = False
        except ValueError:
            outside = True
        exists = target.exists()
        payload = write_confirm_payload(
            "write_file", {"path": str(target), "content": content}, ("path",))
        grant_note = ("Allowing also permits future writes to this exact file "
                      "without asking.")
        if outside:
            payload["icon"] = "⚠"
        if exists:
            payload["title"] = "Overwrite file"
            where = "outside your notes folder, " if outside else ""
            payload["intro"] = (f"This overwrites the file already {where}at "
                                f"{target}. {grant_note}")
        elif outside:
            payload["intro"] = (f"Heads up — this writes outside your notes "
                                f"folder, to {target}. {grant_note}")

        confirm_id = self._confirm.begin()
        yield {"confirm_request": payload, "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            self._log("files", "correction",
                      {"action": "declined_write", "path": str(target)})
            yield {"chunk": "Cancelled — nothing was written."}
            yield {"done": True}
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content + "\n")
        except OSError as e:
            log.exception("chat file write failed")
            yield {"chunk": f"I couldn't write the file: {e}"}
            yield {"done": True}
            return
        if self._write_gate is not None:
            self._write_gate.grant(str(target))
        self._log("files", "query", {"action": "wrote_file", "path": str(target)})
        yield {"chunk": f"Written to {target}."}
        yield {"done": True}

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
        # Find-then-send (todo-fixes #2): "find X's address from his email,
        # then send him …" — resolve the named person against the mirror and
        # prefill the recipient. A miss just leaves `to` for the user; the
        # Send click stays the confirmation either way.
        hint = draft.get("to_hint")
        if not hint and (m := TO_HINT_FALLBACK.search(message)):
            hint = m.group(1) or m.group(2)   # possessive form | of-form
        if not draft["to"] and hint and self._mail_store is not None:
            addr = self._lookup_address(hint)
            if addr:
                draft["to"] = [addr]
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

    def _lookup_address(self, name: str) -> str | None:
        """Newest mirrored message whose SENDER line names `name` -> that
        sender's address. Sender-only on purpose: prefilling a recipient from a
        body mention could email the wrong person. Matching is per-word and
        anchored at word boundaries — a bare substring test let a filler word
        match inside an unrelated sender ("to" inside "Preston") and address
        the draft to the wrong human (audit 2026-07-19)."""
        words = [w for w in re.findall(r"[\w']+", name.lower())
                 if w not in _ADDR_STOP]
        if not words:
            return None
        # from: scopes the search to senders, so the newest message actually
        # FROM this person wins over one that merely mentions them.
        hits = sorted(self._mail_store.search(f"from:{' '.join(words)}", limit=8)
                      or self._mail_store.search(" ".join(words), limit=8),
                      key=lambda r: r.get("received_at") or "", reverse=True)
        for r in hits:
            sender = (r.get("sender") or "").lower()
            if all(re.search(rf"\b{re.escape(w)}", sender) for w in words):
                addr = _sender_address(r.get("sender", ""))
                if addr:
                    return addr
        return None

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

    async def _gated_rule_save(self, rule: dict):
        """Confirm-over-IPC for a new rule: the summary rows are the plain-
        language rendering, the checkbox offers the backfill, and the user's
        Save is the pre-authorization for every future auto-apply. Yields UI
        events, then a final {"_saved", "_applied", "_matched"}."""
        inbox = self._mail_store.list_page("inbox", limit=RULE_SCAN_LIMIT)
        matches = [m["id"] for m in inbox if mail_rules.rule_matches(rule, m)]
        rows = [("Label", rule["label"])]
        for key, name in (("from_addrs", "From"), ("domains", "Domains"),
                          ("subject_kw", "Subject"), ("body_kw", "Body")):
            if rule[key]:
                rows.append((name, ", ".join(rule[key])))
        rows.append(("Existing", f"{len(matches)} matching in your inbox"))
        confirm_id = self._confirm.begin()
        req = {"icon": "⚑", "title": "Create mail rule",
               "intro": "New mail matching this rule is labeled and moved out "
                        "of your inbox automatically — in Gmail too. Saving "
                        "pre-approves those moves.",
               "rows": rows, "confirm_label": "Save rule"}
        if matches:
            req["check"] = {"label": f"Also label the {len(matches)} matching "
                                     "email(s) already in your inbox",
                            "checked": True}
        yield {"confirm_request": req, "confirm_id": confirm_id}
        answer = await self._confirm.wait(confirm_id)
        if not answer:
            yield {"_saved": False, "_applied": 0, "_matched": len(matches)}
            return
        self._rules.add(rule)
        self._log("email", "query", {"action": "create_rule",
                                     "label": rule["label"]})
        applied = 0
        if isinstance(answer, dict) and answer.get("check"):
            for mid in matches:
                if await self._mail.apply_label(mid, rule["label"]):
                    applied += 1
        yield {"_saved": True, "_applied": applied, "_matched": len(matches)}

    def _with_label_names(self, rows: list[dict]) -> list[dict]:
        """Rows carry Gmail label IDs; the UI shows user-label NAMES."""
        m = {l["id"]: l["name"] for l in self._mail_store.user_labels()}
        for r in rows:
            r["label_names"] = [m[i] for i in r.get("labels", []) if i in m]
        return rows

    def _present_user_labels(self) -> list[str]:
        """User-label names present on at least one mirrored message —
        the chip row's vocabulary."""
        m = {l["id"]: l["name"] for l in self._mail_store.user_labels()}
        present = self._mail_store.present_label_ids()
        return sorted((m[i] for i in present if i in m), key=str.casefold)

    async def _gated_mail_action(self, type_: str, payload: dict):
        """Confirm-over-IPC then execute a mail write against Gmail. Read-state
        writes stopped confirming 2026-07-15 (dwell auto-read design); archive
        and delete (→ Trash) are the mail actions behind this gate."""
        title, intro, confirm_label, verb, ok_msg = MAIL_GATES[type_]
        if self._confirm is None:
            yield {"error": "email actions unavailable"}
            return
        row = self._mail_store.get(str(payload.get("id", "")))
        if row is None:
            yield {"error": "email not found"}
            return
        confirm_id = self._confirm.begin()
        yield {"confirm_request": {
                   "icon": "✉", "title": title,
                   "intro": intro,
                   "rows": [("From", row["sender"]), ("Subject", row["subject"])],
                   "confirm_label": confirm_label},
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            yield {"result": {"ok": False, "message": "Cancelled — nothing was changed."}}
            return
        ok = await getattr(self._mail, verb)(row["id"])
        yield {"result": {"ok": ok, "message": ok_msg if ok else
                          "Couldn't reach Gmail — nothing was changed."}}

    async def _chat_with_tools(self, message: str, conv_id: int | None = None,
                               groups: frozenset[str] = SERVER_GROUPS,
                               extra_context: str | None = None):
        try:
            await self._bridge.ensure_started()
            tools = [t for t in self._bridge.ollama_tools(
                         servers=set(groups) & set(SERVER_GROUPS))
                     if t.get("function", {}).get("name", "").split("__")[-1]
                     not in WRITE_TOOLS]
        except Exception:
            log.exception("MCP bridge unavailable — answering without tools")
            tools = []
        # Todos are in-process, so they survive an MCP bridge that never came
        # up — and they must be attached whenever the filesystem group is, or
        # a todo request lands on write_file again (todo-fixes #19).
        if "todos" in groups:
            tools = tools + list(local_tools.TODO_TOOLS)
        if not tools:   # no servers came up → plain chat, honest context only
            messages = self._messages_for(message, conv_id, extra=extra_context)
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
            if name.split("__")[-1] in local_tools.LOCAL_TOOL_NAMES:
                text = local_tools.dispatch(name.split("__")[-1], args,
                                            self._todos)
                if self._tool_log is not None:
                    self._tool_log.write(name, args, True, text,
                                         int((time.monotonic() - start) * 1000))
                return text
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

        messages = self._messages_for(message, conv_id, groups, extra_context)
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
