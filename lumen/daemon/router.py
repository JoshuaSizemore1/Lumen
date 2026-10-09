"""Request router: chat streaming (plain, tool-augmented, book-rec, and
event-creation paths), sleep, todos.*/books.*/calendar.*/emails.* one-shots,
and confirm.response resolution. Tool-call vs direct-answer classification is
the subject hints below plus a small-model classifier fallback on total regex
miss (daemon/llm/intent.py)."""

import asyncio
import json
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
# A bare imperative "add X" (or jot/note down) with NO scheduling signal is a
# TODO, not a calendar event (#19). EVENT_HINT's "add" + a noun like "call"
# used to steal "add call the dentist" into the calendar; here the user never
# said meeting/appointment/calendar and gave no time, so default to the todo
# list. SCHEDULE_SIGNAL below is what makes it an event instead.
ADD_LOOSE = re.compile(
    r"^\s*(?:add|jot\s+down|note\s+down)\s+(.+)$", re.IGNORECASE | re.DOTALL)
SCHEDULE_SIGNAL = re.compile(
    r"\b(?:meeting|appointment|calendar|schedule|event|reminder)\b"
    r"|\b(?:at|by|from|@)\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b"
    r"|\b\d{1,2}\s*(?:am|pm)\b"
    r"|\b(?:today|tomorrow|tonight|this\s+\w+|next\s+\w+|"
    r"mon(?:day)?|tues?(?:day)?|wed(?:nesday)?|thu(?:rs(?:day)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b",
    re.IGNORECASE)
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

# "label this email as Work" / "tag the open message" (#11): a labeling verb
# aimed at a mail noun (or the deictic "this"/"it", since the mail-screen ask
# bar carries which email is open). Routes to the tool loop where label_email
# lives, with the open email named in context.
MAIL_LABEL_HINT = re.compile(
    r"\b(?:label|tag|categori[sz]e|file)\b"
    r"[^.\n]{0,40}\b(?:email|message|mail|inbox|this|it)\b"
    r"|\b(?:label|tag)\s+this\b",
    re.IGNORECASE)

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

# "make a new label/tag for emails from X" (todo-fixes #35) is a labeling RULE,
# just phrased with "label"/"tag" instead of the word "rule" — so it never hit
# RULE_HINT and the model refused ("I can't modify labels"). Route it to the
# same rule-authoring flow. Gated on a mail word (lookahead) so "add a tag to
# my todo" — todos also carry tags — is left to the todo route, and requires a
# create verb so "label this email as Work" stays with the apply-to-open-message
# path (MAIL_LABEL_HINT), which is checked just after.
#
# The create verb alone is NOT enough to separate the two: "add a label to this
# email" / "add the Work label to this email" are apply-to-the-open-message
# asks that carry both a create verb and a mail word. What actually splits them
# is the deictic target — a rule labels a CLASS of mail ("emails from X"), while
# the apply path names THIS message — so a leading negative lookahead hands
# those back to MAIL_LABEL_HINT.
MAKE_LABEL_HINT = re.compile(
    r"(?!.*\b(?:this|that|the\s+open|the\s+current|currently\s+open)\s+"
    r"(?:e-?mail|message|mail|thread)\b)"
    r"(?=.*\b(?:e-?mails?|inbox|senders?|from)\b)"
    r"\b(?:create|add|make|set\s*up|new)\b.{0,40}\b(?:label|tag)s?\b",
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

# Suggest-labels bounds: one warm-model verdict per unlabeled message, run
# sequentially. Raised from 15 → 40 (#8): Josh saw it "only do a couple", and
# the cap was the reason — the loop is already one prompt per email. The UI now
# dims the inbox behind a busy bar for the whole run (#7), so a longer pass is
# visible rather than mysterious.
SUGGEST_SCAN_LIMIT = 200
SUGGEST_LIMIT = 40
# How many filed messages ground each label's one-line description
# (suggest-labels v2) — local SQL per label, no model cost.
LABEL_PROFILE_ROWS = 3

# Google Calendar's fixed event-colour palette (colorId → display name), used
# in the edit dialog's colour picker and the update confirm dialog (#12).
GCAL_COLOR_NAMES = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo", "5": "Banana",
    "6": "Tangerine", "7": "Peacock", "8": "Graphite", "9": "Blueberry",
    "10": "Basil", "11": "Tomato"}
# The hex Google renders each colorId as — so the picker's swatches match the
# calendar, and the UI can preview a chosen colour before the sync round-trips.
GCAL_COLOR_HEX = {
    "1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73",
    "5": "#f6bf26", "6": "#f4511e", "7": "#039be5", "8": "#616161",
    "9": "#3f51b5", "10": "#0b8043", "11": "#d50000"}

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
    "specific, concise answers. Write in plain prose: do not decorate replies "
    "with emoji, and never scatter stars, asterisks, or other symbols — use them "
    "only where they carry real meaning, like a Markdown list or a file path. "
    "This is the user's own private laptop and their own data. When they ask you "
    "to record a todo, note, or draft in their own words, capture it verbatim "
    "without refusing, sanitising, moralising, or dismissing it as a joke — even "
    "if the wording is crude or blunt. It is theirs."
)

# Write-capable tools stay callable by the daemon (after a confirm) but are
# never offered to the model in the generic tool loop — the confirm gate is
# mechanical, not prompt-enforced.
WRITE_TOOLS = frozenset({"create_event", "delete_event", "update_event"})

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


CONFIRM_ROW_CAP = 8


def _due_dates_confirm(pending: list[dict], courses: dict) -> dict:
    """The ConfirmOverlay payload for a batch due-date push. Must use the
    overlay's real schema (icon/title/intro/rows/confirm_label) — an earlier
    kind/summary/items shape rendered an empty dialog."""
    rows = []
    for p in pending[:CONFIRM_ROW_CAP]:
        c = courses.get(p["course_id"], {})
        label = c.get("course_code") or c.get("name") or "Canvas"
        rows.append((p["due"], f"{label} — {p['name']}"))
    extra = len(pending) - len(rows)
    if extra > 0:
        rows.append(("+", f"{extra} more"))
    n = len(pending)
    return {"icon": "\u25b2", "title": "Add Canvas due dates",
            "intro": f"Lumen will add {n} due-date "
                     f"{'marker' if n == 1 else 'markers'} to your Google "
                     "Calendar.",
            "rows": rows,
            "confirm_label": f"Add {n} to calendar"}


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


# The connections Settings can Disable/Disconnect (#38).
_CONN_NAMES = ("gmail", "google_calendar", "canvas")

# Routes that would load the local model. When the model switch is off these
# never reach Ollama; they answer with a `model_off` marker instead, and the UI
# renders one "the model is turned off" notice wherever the answer would go.
# Two shapes because the IPC has two: `chat` streams (a bare event the client
# turns into a signal), everything else is request/response (a result the
# per-request callback receives).
_MODEL_STREAM_ROUTES = ("chat",)
_MODEL_RESULT_ROUTES = ("briefing.today", "books.recommend", "mail.suggest_labels",
                        "todos.scan_commitments", "emails.revise",
                        "files.propose_edit")
# Preloading is not an answer to anything — with the model off it is simply a
# no-op, since its whole purpose is to pull the model into RAM early.
_MODEL_SILENT_ROUTES = ("warm",)

# Routes that must never overlap another of their kind. The IPC server serves
# requests concurrently now (todo-fixes #60/#64) — a message-body fetch must not
# hold up a list read — but two kinds of route still have to take their turn:
#
#   * model routes, because a second call would put another generation on the
#     iGPU alongside the first (the power budget forbids it), and because two
#     chats' chunks would interleave into the client's single signal stream;
#   * confirm-gated routes, because they park on a modal the UI can only show
#     one of at a time.
#
# `confirm.response` is deliberately absent: it is the thing that UNBLOCKS a
# waiting confirm, so making it wait its turn would deadlock every gated write.
_CONFIRM_GATED_ROUTES = (
    "calendar.create", "calendar.delete", "calendar.update",
    "rules.create", "canvas.push_due_dates",
)
EXCLUSIVE_ROUTES = frozenset(
    _MODEL_STREAM_ROUTES + _MODEL_RESULT_ROUTES + _MODEL_SILENT_ROUTES
    + _CONFIRM_GATED_ROUTES) | frozenset(MAIL_GATES)


class Router:
    def __init__(self, llm, todos, books=None, *, calendar=None, mail=None,
                 canvas=None,
                 mail_store=None, bridge=None, confirm=None, write_gate=None,
                 model_router=None, tool_log=None, conversations=None,
                 suggestions=None, scheduling=None, notes=None, write_dir=None,
                 manabi=None,
                 memory=None, memory_path=None, memory_cap=4000,
                 procedures=None, distill_trigger=None,
                 config=None, rules=None, marker_writer=None,
                 canvas_prefs=None, canvas_queue=None, canvas_alerts=None,
                 canvas_proposals=None,
                 connection_state=None,
                 max_iterations=4):
        self._llm = llm
        self._todos = todos
        self._books = books
        self._calendar = calendar   # CalendarSync facade: list_range/last_sync/connected
        self._mail = mail           # GmailSync facade: poll_forever/connected
        self._canvas = canvas       # CanvasSync: set_session/clear_session/connected/last_sync
        self._bg_syncs: set = set()  # strong refs for _kick_canvas_sync tasks
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
        self._marker_writer = marker_writer  # CalendarMarkerWriter (gated) — lazy
        self._canvas_prefs = canvas_prefs
        self._canvas_queue = canvas_queue
        self._canvas_alerts = canvas_alerts
        self._canvas_proposals = canvas_proposals
        self._connection_state = connection_state  # ConnectionState — #38 Disable
        self._max_iterations = max_iterations
        # Detached fire-and-forget writes; see _spawn / drain_background.
        self._bg: set[asyncio.Task] = set()

    def on_disconnect(self) -> None:
        """A UI connection died — deny anything still waiting on a dialog."""
        if self._confirm is not None:
            self._confirm.deny_all()

    def _accounts_state(self) -> dict:
        """Settings accounts block: connected + enabled per connection
        (#37 status / #38 Disable). One shape shared by settings.get and the
        connections.* routes so the UI updates consistently."""
        from .connectors import google_auth
        g = self._config.google if self._config is not None else None

        def enabled(name: str) -> bool:
            return (self._connection_state.enabled(name)
                    if self._connection_state is not None else True)
        cs = self._canvas_status()
        return {
            "gmail": {"connected": bool(g) and google_auth.connected(
                g, google_auth.GMAIL_READ_SCOPES), "enabled": enabled("gmail")},
            "google_calendar": {"connected": bool(g) and google_auth.connected(g),
                                 "enabled": enabled("google_calendar")},
            "canvas": {"connected": cs["connected"], "enabled": enabled("canvas")},
        }

    def _calendar_enabled(self) -> bool:
        """#38's runtime Disable. The poll loops consult it every tick; a manual
        refresh has to as well, or "disabled" would still mean "talks to Google
        whenever the calendar screen is opened"."""
        return (self._connection_state is None
                or self._connection_state.enabled("google_calendar"))

    def _kick_canvas_sync(self) -> None:
        """Run a sync in the background, ignoring the outcome. A strong ref is
        kept until it finishes — a bare create_task() may be garbage-collected
        mid-flight."""
        if self._canvas is None or self._canvas.busy:
            return
        try:
            task = asyncio.create_task(self._canvas.sync_once())
        except RuntimeError:                # no running loop (sync test call)
            return
        self._bg_syncs.add(task)
        task.add_done_callback(self._bg_syncs.discard)

    def _canvas_status(self) -> dict:
        """Live poller state for the Canvas tab + Settings row. Every canvas.*
        route answers with this shape as a *result* so the UI's per-id callback
        fires and the label reflects the daemon's truth."""
        if self._canvas is None:
            return {"connected": False, "last_sync": None, "enabled": False}
        return {
            "connected": self._canvas.connected,
            "last_sync": self._canvas.last_sync(),
            "enabled": (self._config.canvas.enabled
                        if self._config is not None else False),
        }

    async def _accept_proposal(self, pid: int, approve: bool) -> dict:
        """Put one AI-inferred event on the calendar, or bury it for good.

        This is deliberately the ONLY path that creates a proposal: a date a
        model read out of announcement prose is a guess, and a wrong guess
        written straight to the real calendar is worse than no feature."""
        from .connectors import canvas_calendar
        row = self._canvas_proposals.get(pid)
        if row is None:
            return {"ok": False, "reason": "not pending"}
        if not approve:
            self._canvas_proposals.dismiss(pid)
            return {"ok": True, "created": False,
                    "proposals": self._canvas_proposals.count()}
        writer = self._markers()
        if writer is None:
            return {"ok": False, "reason": "calendar unavailable"}
        eid = await asyncio.to_thread(writer.create_event,
                                      canvas_calendar.proposal_body(row))
        if eid is None:
            return {"ok": False, "reason": "could not create the event"}
        self._canvas_proposals.accept(pid, eid)
        return {"ok": True, "created": True, "event_id": eid,
                "proposals": self._canvas_proposals.count()}

    def _markers(self):
        """Lazily build the gated calendar-marker writer (real writes) unless a
        test injected one. Returns None when Calendar config is absent."""
        if self._marker_writer is None and self._config is not None:
            from .connectors.gcal import CalendarMarkerWriter
            self._marker_writer = CalendarMarkerWriter(self._config.google)
        return self._marker_writer

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


    # --- fire-and-forget work (todo-fixes #60) ---------------------------
    # Some routes have a Gmail write to do but nothing to say about it. Holding
    # the reply open for that round trip put the network on the click path:
    # every message you opened cost one, and spam-clicking queued them all.
    # These run detached, with the local mirror updated optimistically so the
    # UI is right immediately; a failed write is corrected by the next poll.

    def _spawn(self, coro, what: str) -> None:
        task = asyncio.create_task(self._background(coro, what))
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    @staticmethod
    async def _background(coro, what: str) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("background %s failed", what)

    async def drain_background(self) -> None:
        """Wait for detached work to finish. Used by shutdown, and by tests that
        need to observe a write the route deliberately did not wait for."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)

    def is_exclusive(self, type_: str) -> bool:
        """Asked by the IPC server before it starts a request: may this route
        run alongside others? See EXCLUSIVE_ROUTES for the two reasons it may
        not."""
        return type_ in EXCLUSIVE_ROUTES

    async def handle(self, type_: str, payload: dict) -> AsyncIterator[dict]:
        if type_ == "chat":
            message = payload.get("message", "")
            # Tell the UI only when this turn genuinely pays a model load, so it
            # says "cold start" only then — not on every slow prompt-eval (#22).
            # Emitted first, before any sub-path touches the model; the UI treats
            # the absence of this event as "warm". A client that can't report
            # load state degrades to warm (no false alarm), never an error.
            if hasattr(self._llm, "is_loaded") and not await self._llm.is_loaded():
                yield {"cold_start": True}
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
            # Ephemeral turns (the Ask-Lumen bar, #24) run without ever
            # touching the conversation store: no thread is created, none is
            # emitted, and nothing is persisted — so they never surface as a
            # chat instance. The user materializes one only by clicking
            # "open in Chat", which calls conversations.seed.
            ephemeral = bool(payload.get("ephemeral"))
            conv_id = payload.get("conversation_id")
            if self._conv is not None and not ephemeral:
                if conv_id is None:
                    conv_id = self._conv.create(message)
                    yield {"conversation_id": conv_id}   # emit first so the UI can track the thread
                self._conv.add_message(conv_id, "user", message)   # write-through on arrival
            # aclosing: closing this generator must synchronously close whatever
            # sub-path it drives (the tool loop owns a pump task), not defer to GC.
            cwd, open_file = self._files_grounding(payload)
            async with aclosing(self._chat(
                    message, conv_id, cwd=cwd, open_file=open_file,
                    open_email=payload.get("open_email"),
                    surface=(payload.get("context") or {}).get("screen"))) as gen:
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
        elif type_ == "conversations.seed":
            # Materialize an ephemeral Ask-Lumen exchange into a real chat
            # thread on demand (#24 — "open in Chat"): create the conversation
            # and write its one Q&A turn, so it now appears in the sidebar.
            if self._conv is None:
                yield {"error": "conversation history unavailable"}
                return
            question = str(payload.get("question") or "").strip()
            answer = str(payload.get("answer") or "")
            if not question:
                yield {"error": "conversations.seed needs {question}"}
                return
            cid = self._conv.create(question)
            self._conv.add_message(cid, "user", question)
            tools = payload.get("tools") or None
            self._conv.add_message(cid, "assistant", answer, tools)
            if tools:
                self._conv.mark_tool_engaged(cid)
            yield {"result": {"id": cid}}
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
                snap = build_settings_snapshot(self._config)
                accounts = snap.setdefault("accounts", {})
                # Canvas "connected" is live poller state, not config, so the
                # snapshot builder can't know it — inject it here so Settings
                # shows Canvas the same way it shows Gmail/Calendar (#37).
                cs = self._canvas_status()
                accounts["canvas"] = {"connected": cs["connected"],
                                      "last_sync": cs["last_sync"]}
                # Per-connection runtime Disable state (#38) rides alongside
                # `connected`: a connection can be logged in but paused.
                for name, acct in accounts.items():
                    acct["enabled"] = (self._connection_state.enabled(name)
                                       if self._connection_state is not None
                                       else True)
                yield {"result": snap}
        elif type_ == "google.reconnect":
            # Settings 'Reconnect' button — re-run the Google consent flow when
            # the refresh token has expired/been revoked. Blocks on the browser
            # (and the loopback redirect), so it rides an executor thread to keep
            # the event loop — and the poller/MCP tasks — responsive. One consent
            # covers both Gmail and Calendar, so either row's button fixes both.
            if self._config is None:
                yield {"error": "settings unavailable"}
            else:
                from .connectors import google_auth
                loop = asyncio.get_running_loop()
                ok, err = await loop.run_in_executor(
                    None, google_auth.reconnect, self._config.google)
                if ok:
                    from .settings_snapshot import build_settings_snapshot
                    yield {"result": build_settings_snapshot(self._config)}
                else:
                    yield {"error": err}
        elif type_ == "canvas.set_session":
            # UI hands the browser session (cookies) to the in-memory poller.
            # Never a password — the daemon only ever sees cookies. Reply with
            # the fresh status as a *result* so the UI's per-id callback fires
            # and the Canvas tab flips to Connected (a bare {"done": True} was
            # swallowed by the client's done-signal path — live bug 2026-07-20).
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                self._canvas.set_session(payload.get("cookies", {}))
                # Sync at once instead of leaving the tab empty until the next
                # poll tick — connecting one minute after a tick otherwise means
                # a whole poll interval of nothing (live bug 2026-08-25).
                self._kick_canvas_sync()
                yield {"result": self._canvas_status()}
        elif type_ == "canvas.status":
            # Live poller state (not config) for the Canvas tab + Settings row.
            yield {"result": self._canvas_status()}
        elif type_ == "canvas.disconnect":
            if self._canvas is not None:
                self._canvas.clear_session()
            yield {"result": self._canvas_status()}
        elif type_ == "connections.set_enabled":
            # #38 Disable/Enable: pause or resume a connection's sync without
            # touching its login. Persisted, so it survives a restart; the poll
            # loops read this on their next tick via the `paused` predicate.
            name = str(payload.get("name") or "")
            if self._connection_state is None or name not in _CONN_NAMES:
                yield {"error": "connections.set_enabled needs a known {name}"}
            else:
                self._connection_state.set_enabled(name, bool(payload.get("enabled")))
                yield {"result": {"accounts": self._accounts_state()}}
        elif type_ == "connections.disconnect":
            # #38 Disconnect: remove the stored login. Gmail/Calendar share one
            # Google token (dropping it disconnects both); Canvas clears its
            # browser session. Re-enabling a disabled account is separate.
            name = str(payload.get("name") or "")
            if name in ("gmail", "google_calendar") and self._config is not None:
                from .connectors import google_auth
                google_auth.disconnect(self._config.google)
                yield {"result": {"accounts": self._accounts_state()}}
            elif name == "canvas":
                if self._canvas is not None:
                    self._canvas.clear_session()
                yield {"result": {"accounts": self._accounts_state()}}
            else:
                yield {"error": "connections.disconnect needs a known {name}"}
        elif type_ == "canvas.pending_calendar":
            # Assignments whose due-date marker the user hasn't confirmed onto
            # the calendar yet (create), or whose due date drifted (update). Read
            # only — nothing is written until canvas.push_due_dates is confirmed.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                active = [c["id"] for c in store.active_courses()]
                courses = store.courses_by_id()
                markers = []
                for m in store.pending_markers(active):
                    c = courses.get(m["course_id"], {})
                    label = c.get("course_code") or c.get("name") or "Canvas"
                    markers.append({"id": m["id"], "due": m["due"],
                                    "action": m["action"],
                                    "title": f"{label} — {m['name']} due"})
                yield {"result": {"markers": markers}}
        elif type_ == "canvas.push_due_dates":
            # The one Canvas external write: batch-confirm all pending due-date
            # markers in a single dialog, then create/patch them on the calendar.
            # Nothing reaches Google without the confirm resolving True.
            if self._canvas is None or self._confirm is None:
                yield {"error": "canvas unavailable"}
                return
            store = self._canvas.store
            active = [c["id"] for c in store.active_courses()]
            courses = store.courses_by_id()
            pend = store.pending_markers(active)
            if not pend:
                yield {"result": {"added": 0, "updated": 0}}
                return
            confirm_id = self._confirm.begin()
            yield {"confirm_request": _due_dates_confirm(pend, courses),
                   "confirm_id": confirm_id}
            ok = await self._confirm.wait(confirm_id)
            if not ok:
                yield {"result": {"added": 0, "updated": 0, "cancelled": True}}
                return
            writer = self._markers()
            added = updated = 0
            for p in pend:
                c = courses.get(p["course_id"], {})
                label = c.get("course_code") or c.get("name") or "Canvas"
                title = f"{label} — {p['name']} due"
                if p["action"] == "create":
                    eid = writer.create_all_day(title, p["due"]) if writer else None
                    if eid:
                        store.set_calendar_marker(p["id"], eid, p["due"])
                        added += 1
                elif writer and writer.patch_all_day(p["event_id"], p["due"]):
                    store.set_calendar_marker(p["id"], p["event_id"], p["due"])
                    updated += 1
            yield {"result": {"added": added, "updated": updated}}
        elif type_ == "canvas.assignments":
            # The Canvas tab's assignment list. pending_marker flags rows the
            # user could push to the calendar (see canvas.pending_calendar).
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                courses = store.courses_by_id()
                pending = {m["id"] for m in store.pending_markers(
                    [c["id"] for c in store.active_courses()])}
                items = []
                for a in store.active_assignments():
                    c = courses.get(a["course_id"], {})
                    items.append({**a, "course_code": c.get("course_code"),
                                  "course_name": c.get("name"),
                                  "pending_marker": a["id"] in pending})
                yield {"result": {"assignments": items}}
        elif type_ == "canvas.announcements":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                courses = store.courses_by_id()
                items = [{**a, "course_code":
                          courses.get(a["course_id"], {}).get("course_code")}
                         for a in store.announcements(limit=30)]
                yield {"result": {"announcements": items}}
        elif type_ == "canvas.add_announcement_todo":
            # Ungated: turns an actionable announcement into a local todo the
            # user tapped to accept. Idempotent — a second tap returns the same id.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
                return
            store = self._canvas.store
            ann = store.get_announcement(int(payload["id"]))
            if ann is None:
                yield {"error": "announcement not found"}
                return
            if ann["todo_id"] is not None:
                yield {"result": {"todo_id": ann["todo_id"]}}
                return
            sug = {}
            if ann["suggested_todo"]:
                try:
                    sug = json.loads(ann["suggested_todo"])
                except ValueError:
                    sug = {}
            text = (sug.get("text") or ann["title"] or "Canvas announcement").strip()
            due = sug.get("due") or None
            code = store.courses_by_id().get(ann["course_id"], {}).get("course_code")
            tags = ([code] if code else []) + ["canvas", "announcement"]
            tid = self._todos.add_structured(text, due, tags)
            store.link_announcement_todo(ann["id"], tid)
            yield {"result": {"todo_id": tid}}
        # --- Canvas -> Calendar -------------------------------------------
        elif type_ == "canvas.calendar_status":
            # One call feeds every toggle and strip in the Canvas tab.
            if self._canvas is None or self._canvas_prefs is None:
                yield {"error": "canvas unavailable"}
            else:
                store = self._canvas.store
                yield {"result": {
                    **self._canvas_prefs.all(),
                    "events": len(store.linked_events()),
                    "queued": self._canvas_queue.count() if self._canvas_queue else 0,
                    "proposals": (self._canvas_proposals.count()
                                  if self._canvas_proposals else 0),
                    "alerts": (self._canvas_alerts.count()
                               if self._canvas_alerts else 0)}}
        elif type_ == "canvas.set_calendar_sync":
            if self._canvas_prefs is None:
                yield {"error": "canvas unavailable"}
            else:
                self._canvas_prefs.set_sync_enabled(bool(payload.get("enabled")))
                yield {"result": self._canvas_prefs.all()}
        elif type_ == "canvas.set_calendar_ai":
            if self._canvas_prefs is None:
                yield {"error": "canvas unavailable"}
            else:
                on = bool(payload.get("enabled"))
                self._canvas_prefs.set_ai_mode(on)
                if on and self._canvas is not None:
                    # Re-offer everything for classification: the switch was off
                    # when these rows were first seen, so none were ever looked at.
                    self._canvas.store.reset_ai_state()
                yield {"result": self._canvas_prefs.all()}
        elif type_ == "canvas.calendar_queue":
            # Proposed removals awaiting review. Read-only.
            if self._canvas_queue is None:
                yield {"error": "canvas unavailable"}
            else:
                yield {"result": {"items": self._canvas_queue.pending()}}
        elif type_ == "canvas.resolve_removal":
            # A removal executed INSIDE a request the user just made — the poll
            # loop only ever proposes (see canvas_queue.py).
            if self._canvas is None or self._canvas_queue is None:
                yield {"error": "canvas unavailable"}
            else:
                from .connectors import canvas_calendar
                out = await canvas_calendar.resolve_removal(
                    self._markers(), self._canvas.store, self._canvas_queue,
                    int(payload.get("id", 0)), bool(payload.get("approve")))
                yield {"result": {**out,
                                  "queued": self._canvas_queue.count()}}
        elif type_ == "canvas.proposals":
            if self._canvas_proposals is None:
                yield {"error": "canvas unavailable"}
            else:
                yield {"result": {"items": self._canvas_proposals.pending()}}
        elif type_ == "canvas.resolve_proposal":
            # AI-inferred events never auto-create; this is the only path that
            # puts one on the calendar, and it is one click per item.
            if self._canvas_proposals is None:
                yield {"error": "canvas unavailable"}
            else:
                yield {"result": await self._accept_proposal(
                    int(payload.get("id", 0)), bool(payload.get("approve")))}
        elif type_ == "canvas.sync_now":
            # Flipping a toggle has to do something visible without waiting out
            # the next poll tick. Guarded by the existing sync lock.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            elif self._canvas.busy:
                yield {"result": {"started": False, "reason": "already syncing"}}
            else:
                ok = await self._canvas.sync_once()
                yield {"result": {"started": True, "ok": bool(ok),
                                  "last_sync": self._canvas.last_sync()}}
        elif type_ == "canvas.courses":
            # The Manage-courses panel (#28): every enrolled course + its archive
            # flag, so archived ones can be switched back on.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                yield {"result": {"courses": self._canvas.store.courses_for_panel()}}
        elif type_ == "canvas.set_course_included":
            # Archive / un-archive a course (#28). Non-destructive: nothing is
            # deleted, so no confirmation — the flag just hides it + stops the
            # poller pulling it. Reply carries fresh status like the other routes.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                cid = payload.get("course_id")
                if cid is None:
                    yield {"error": "canvas.set_course_included needs {course_id, included}"}
                else:
                    self._canvas.store.set_course_included(
                        int(cid), bool(payload.get("included")))
                    yield {"result": self._canvas_status()}
        elif type_ == "canvas.dismiss_assignment":
            # Hide / restore a single assignment from the tab (undo sends
            # dismissed=false). Non-destructive: the flag survives sync and any
            # linked todo/calendar marker is left alone.
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                aid = payload.get("id")
                if aid is None:
                    yield {"error": "canvas.dismiss_assignment needs {id, dismissed}"}
                else:
                    self._canvas.store.set_assignment_dismissed(
                        int(aid), bool(payload.get("dismissed", True)))
                    yield {"result": {"ok": True}}
        elif type_ == "canvas.dismiss_announcement":
            if self._canvas is None:
                yield {"error": "canvas unavailable"}
            else:
                aid = payload.get("id")
                if aid is None:
                    yield {"error": "canvas.dismiss_announcement needs {id, dismissed}"}
                else:
                    self._canvas.store.set_announcement_dismissed(
                        int(aid), bool(payload.get("dismissed", True)))
                    yield {"result": {"ok": True}}
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
        elif type_ == "calendar.update":
            # Edit an existing event (#12): title / time / location / colour,
            # gated like every other external calendar write.
            if (self._calendar is None or self._bridge is None
                    or self._confirm is None):
                yield {"error": "calendar editing unavailable"}
                return
            event_id = str(payload.get("id") or "")
            calendar_id = str(payload.get("calendar_id") or "")
            changes = payload.get("changes") or {}
            if not event_id or not calendar_id:
                yield {"error": "calendar.update needs {id, calendar_id}"}
                return
            event = self._calendar.get(calendar_id, event_id)
            if event is None:
                yield {"error": "event not found"}
                return
            async for ev in self._gated_update(event, changes):
                yield ev
        elif type_ in ("calendar.list", "calendar.refresh"):
            # list = pure cache read. refresh = the same answer, but pull from
            # Google first (#59). The cache used to move only on the 5-minute
            # poll tick and at daemon startup, so nothing the user could press
            # brought new events in — including the ones the Canvas pass had
            # just written. Same shape either way, so one round trip repaints.
            if self._calendar is None:
                yield {"error": "calendar unavailable"}
            else:
                if type_ == "calendar.refresh" and self._calendar_enabled():
                    await self._calendar.sync_once()
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
        elif type_ == "todos.update":
            # Edit an existing todo (#23): text / description / due / tags.
            # Only keys present in the payload are changed; due_date and
            # description accept null to clear.
            try:
                tid = int(payload["id"])
            except (KeyError, TypeError, ValueError):
                yield {"error": "todos.update needs {id}"}
                return
            kw = {}
            if "text" in payload:
                kw["text"] = str(payload.get("text") or "")
            if "description" in payload:
                kw["description"] = payload.get("description")
            if "due_date" in payload:
                kw["due_date"] = payload.get("due_date")
            if "tags" in payload:
                kw["tags"] = list(payload.get("tags") or [])
            try:
                rows = self._todos.update(tid, **kw)
            except ValueError as e:
                yield {"error": str(e)}
            else:
                self._log("todos", "query", {"action": "update", "id": tid})
                yield {"result": rows}
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
                # Open-as-read receipt: silent, idempotent, and deliberately NOT
                # awaited (#60). It has nothing to report, so making the click
                # wait for a Gmail round trip bought the user nothing. The
                # mirror is flipped here so a list read that lands a millisecond
                # later already shows the message read; if the Gmail write
                # fails, the next poll puts UNREAD back.
                row = self._mail_store.get(str(payload.get("id", "")))
                if row is not None and not row["is_read"]:
                    self._mail_store.update_labels(row["id"], add=[],
                                                   remove=["UNREAD"])
                    self._spawn(self._mail.mark_read(row["id"], True),
                                "emails.auto_read")
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
                by_id = {r["id"]: r for r in rows}
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
                # Previews let the review popup (#32) list every suggested
                # message — sender/subject/date — even those past the UI's loaded
                # page (scan is 200, the mail list holds ~50). Built from rows we
                # already have in hand, so no extra fetch; the full body is
                # pulled lazily via emails.get when a row is opened.
                previews = {mid: self._with_label_names(
                                [by_id[mid]])[0] for mid in suggestions}
                yield {"result": {"suggestions": suggestions,
                                  "previews": previews,
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
            elif type_ == "emails.remove_label":
                # Manual un-label from the reading pane (#9). Ungated: it is a
                # direct-manipulation click on the user's own mail, same as the
                # one-tap apply above, and it only takes a label off.
                mid = str(payload.get("id", ""))
                name = str(payload.get("label", "")).strip()
                if not name or self._mail_store.get(mid) is None:
                    yield {"error": "emails.remove_label needs {id, label}"}
                    return
                ok = await self._mail.remove_label(mid, name)
                yield {"result": {"ok": ok, "message":
                       f"Removed label {name}." if ok else
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

    @staticmethod
    def _files_grounding(payload: dict) -> tuple[str | None, str | None]:
        """Resolve the Files-screen grounding (cwd, open_file) from a chat
        payload. The Chat/ui_v2 files ask sends cwd/open_file at the top level;
        the ui_v3 Ask bar sends them inside context {screen: files, dir/file},
        so the open folder/file never reached the file-grounded path (#43).
        Accept both, and when only an open file is known (editing, no dir),
        ground on its parent folder so the tool path still engages."""
        ctx = payload.get("context") or {}
        files_ctx = ctx if ctx.get("screen") == "files" else {}
        open_file = payload.get("open_file") or files_ctx.get("file")
        cwd = payload.get("cwd") or files_ctx.get("dir")
        if not cwd and open_file:
            cwd = str(Path(open_file).parent)
        return cwd, open_file

    def _open_email_context(self, open_email: dict | None) -> str | None:
        """Context line naming the email the user has open in the mail screen
        (#11), so 'label this email' resolves to label_email against its id."""
        if not open_email or not open_email.get("id"):
            return None
        subj = open_email.get("subject") or "(no subject)"
        frm = open_email.get("from") or ""
        return (f"The user is currently viewing this email in the mail screen: "
                f"id={open_email['id']}, from {frm}, subject \"{subj}\". When "
                "they say 'this email' / 'the open email', that is the one. To "
                "label it, call label_email with that email_id.")

    async def _chat(self, message: str, conv_id: int | None,
                    cwd: str | None = None, open_file: str | None = None,
                    open_email: dict | None = None, surface: str | None = None):
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
                and (RULE_HINT.search(message) or MAKE_LABEL_HINT.search(message))):
            sub, subsystem = self._create_rule_chat(message), "email"
        elif (self._mail is not None and self._mail_store is not None
                and MAIL_LABEL_HINT.search(message)):
            # "label this email as Work" (#11): the tool loop owns label_email,
            # and the open-email context names which message "this" is.
            sub = self._chat_with_tools(
                message, conv_id, groups=frozenset({"mail"}),
                extra_context=self._open_email_context(open_email),
                include_mail_write=True)
            subsystem = "email"
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
        elif (surface == "calendar" and ADD_LOOSE.match(message)
                and self._confirm is not None and self._bridge is not None
                and self._calendar is not None
                and not TODO_TASK_HINT.search(message)
                and not FILE_TASK_HINT.search(message)):
            # The Ask-Lumen bar's page decides an ambiguous add (#19 rework):
            # on the calendar page a bare "add X" is an event, not a todo. An
            # explicit "…to my todo list" / a file noun still overrides this,
            # and every other surface keeps the todo default below.
            sub, subsystem = self._create_event_chat(message), "calendar"
        elif ((am := ADD_LOOSE.match(message))
                and not SCHEDULE_SIGNAL.search(message)
                and not FILE_TASK_HINT.search(message)
                and not TODO_TASK_HINT.search(message)):
            # Bare "add X" with no time/calendar cue → the todo list, ahead of
            # the event branch that would otherwise claim it (#19). An explicit
            # "…to my todo list" keeps the established tool-loop path instead.
            sub, subsystem = self._nl_add_chat(am.group(1).strip()), "todos"
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
        canvas_anns = []
        if self._canvas is not None:
            store = self._canvas.store
            courses = store.courses_by_id()
            for a in store.announcements(limit=5):
                c = courses.get(a["course_id"], {})
                canvas_anns.append({**a, "course_code": c.get("course_code")})
        return build_sections(
            events, self._todos.open_todos(), unread, counts, now,
            cal_connected=(self._calendar is not None and self._calendar.connected),
            mail_connected=(self._mail is not None and self._mail.connected),
            mail_syncing=(self._mail is not None and self._mail.syncing),
            manabi_due=(self._manabi is not None
                        and self._manabi.status(now)["due"] is True),
            canvas_announcements=canvas_anns)

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

    async def _gated_update(self, event: dict, changes: dict):
        """Confirm-over-IPC then patch an event on Google Calendar (#12). UI
        one-shot only; `changes` carries the fields the edit dialog touched
        (title/start/end/all_day/location/color_id), and only those are sent."""
        title = str(changes.get("title") or "").strip()
        start = str(changes.get("start") or "").strip()
        end = str(changes.get("end") or "").strip()
        color_id = str(changes.get("color_id") or "").strip()
        location = str(changes.get("location") or "").strip()
        rows = []
        if title:
            rows.append(("Title", title))
        if start:
            rows.append(("When", f"{start} – {end}" if end else start))
        if location:
            rows.append(("Location", location))
        if color_id:
            rows.append(("Colour", GCAL_COLOR_NAMES.get(color_id, color_id)))
        if not rows:
            yield {"result": {"updated": False, "message": "Nothing to change."}}
            return
        confirm_id = self._confirm.begin()
        yield {"confirm_request": {
                   "icon": "▲", "title": "Update calendar event",
                   "intro": "Lumen will change this event on your Google "
                            "Calendar.",
                   "rows": rows, "confirm_label": "Save changes"},
               "confirm_id": confirm_id}
        if not await self._confirm.wait(confirm_id):
            self._log("calendar", "correction", {"action": "declined_update"})
            yield {"result": {"updated": False,
                              "message": "Cancelled — nothing was changed."}}
            return
        try:
            await self._bridge.ensure_started()
        except Exception:
            log.exception("MCP bridge unavailable for event update")
            yield {"result": {"updated": False,
                              "message": "calendar tools are unavailable right now"}}
            return
        args = {"event_id": event["id"], "calendar_id": event["calendar_id"],
                "title": title, "start": start, "end": end,
                "all_day": bool(changes.get("all_day")),
                "location": location, "color_id": color_id}
        start_t = time.monotonic()
        try:
            text = await self._bridge.call("update_event", args)
            ok = True
        except Exception as e:
            text, ok = f"tool error: {e}", False
            if not isinstance(e, ToolCallError):
                log.exception("update_event failed unexpectedly")
        if self._tool_log is not None:
            self._tool_log.write("update_event", args, ok, text,
                                 int((time.monotonic() - start_t) * 1000))
        ok = ok and text.startswith("Updated")
        if ok and hasattr(self._calendar, "sync_once"):
            try:
                await self._calendar.sync_once()
            except Exception:
                log.exception("post-update sync failed")
        yield {"result": {"updated": ok, "message": text}}

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
        # When the request references the calendar ("email X about the meeting
        # we have tomorrow"), hand the model the user's REAL upcoming events so
        # it writes from fact instead of inventing one (#39).
        cal_ctx = None
        if self._calendar is not None and CAL_HINT.search(message):
            now = datetime.now().astimezone()
            end = now.date() + timedelta(days=CAL_CONTEXT_DAYS)
            cal_ctx = calendar_context(
                self._calendar.list_range(now.date().isoformat(),
                                          end.isoformat()), now, end)
        try:
            draft, err = await propose_email(self._llm, message, context=cal_ctx)
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

    async def _label_email_tool(self, args: dict) -> str:
        """label_email tool (#11): apply a Gmail label to one message via the
        daemon's GmailSync (same write the suggestion tap and the manual pane
        picker use — the user's own mail, reversible, so ungated). Returns the
        text the model reports back."""
        mid = str(args.get("email_id") or "").strip()
        name = str(args.get("label") or "").strip()
        if not mid or not name:
            return "label_email needs both an email_id and a label name."
        if self._mail_store is None or self._mail_store.get(mid) is None:
            return (f"No email with id {mid} in the mailbox — check the id, or "
                    "use search_email to find the message first.")
        try:
            ok = await self._mail.apply_label(mid, name)
        except Exception as e:
            return f"tool error: {e}"
        return (f"Labeled that email as '{name}' and moved it out of the inbox."
                if ok else "Couldn't reach Gmail — the label wasn't applied.")

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
        """Every user label the mail nav should offer as a section (#36): all
        Gmail user labels — not only those already on some mirrored message —
        UNION the label of every mail rule, so a freshly-created rule/label gets
        its section immediately instead of only once mail lands under it. The
        empty ones simply list zero mail until a rule or the user files some."""
        names = {l["name"] for l in self._mail_store.user_labels()}
        if self._rules is not None:
            names.update(r["label"] for r in self._rules.list_all())
        return sorted(names, key=str.casefold)

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
                               extra_context: str | None = None,
                               include_mail_write: bool = False):
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
        # The mail label tool is in-process too (it writes through GmailSync).
        # It is a WRITE, so it does not ride the mail ride-along on every loop —
        # only the explicit labeling route attaches it (#11).
        if (include_mail_write and self._mail is not None
                and self._mail_store is not None):
            tools = tools + list(local_tools.MAIL_TOOLS)
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
            short = name.split("__")[-1]
            if short in local_tools.LOCAL_TOOL_NAMES:
                text = local_tools.dispatch(short, args, self._todos)
                if self._tool_log is not None:
                    self._tool_log.write(name, args, True, text,
                                         int((time.monotonic() - start) * 1000))
                return text
            if short in local_tools.MAIL_TOOL_NAMES:
                text = await self._label_email_tool(args)
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
