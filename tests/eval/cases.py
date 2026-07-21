"""Eval cases for chat routing and tool selection.

Corrected against production on 2026-07-18. Three things the previous bench got
wrong, all of which inflated or invented signal:

1. Four todo tools (list_todos / add_todo / complete_todo / delete_todo) and
   `lookup_book` were graded but do not exist. Todos are a context-only
   pseudo-group — the router says so at SERVER_GROUPS — and the real book tool
   is `get_book`.

   SUPERSEDED 2026-07-19: todos now DO have tools — add_todo, complete_todo and
   list_todos, in-process at daemon/local_tools.py. The context-only gap was
   the cause of todo-fixes #19 (the model wrote a file called TODO because
   nothing else could touch a todo). `delete_todo` and `lookup_book` still do
   not exist; deletion stays a UI action. surface.py reads the three live from
   the module, so this file must never re-declare them by hand.
2. Because of (1), the two cases flagged as "ambiguous labels" were not
   ambiguous, they were unanswerable: "Remind me to…" and "Mark todo 42 as
   done" never reach the model at all. TODO_ADD and MARK_DONE match them first
   and write to SQLite directly. Grading them as tool calls measured a path
   that does not run. They live in ROUTING_CASES now, asserted deterministically
   and for free.
3. "Delete todo 7, I already did it" matches no route, so it lands in
   context-only chat where nothing can complete it. That is a real product gap,
   not a labelling question — recorded in GAPS below rather than asserted as if
   current behavior were correct.
"""

# ---------------------------------------------------------------------------
# Routing: handled before the LLM. Deterministic, no model, no tokens.
# (message, expected route)
# ---------------------------------------------------------------------------
ROUTING_CASES = [
    ("Remind me to call the dentist.", "TODO_ADD"),
    ("Add a todo to renew the car registration by Friday.", "TODO_ADD"),
    ("remind me to email Dana about the lease", "TODO_ADD"),
    ("Mark todo 42 as done.", "MARK_DONE"),
    ("mark the laundry finished", "MARK_DONE"),
    ("check off the dishwasher one as done", "MARK_DONE"),
    # Stated as a removal, meant as a completion. Fell through to context-only
    # chat before ALREADY_DONE existed, so the todo silently stayed open.
    ("Delete todo 7, I already did it.", "ALREADY_DONE"),
    ("Remove the dishwasher one, I already did it", "ALREADY_DONE"),
    ("I've already done the laundry", "ALREADY_DONE"),
    # Answerable from injected todo_context — correctly needs no tool.
    ("What's on my todo list?", "context-only"),
    # Says todo without the word: must reach todo_context deterministically
    # rather than depending on the intent classifier.
    ("Show me everything I still have open.", "context-only"),
    ("What's left to do this week?", "context-only"),
]

# ---------------------------------------------------------------------------
# Tool selection: (message, attached groups, expected tool, arg predicate|None)
# Only tools that exist in production appear here.
# ---------------------------------------------------------------------------
TOOL_CASES = [
    ("What did Chris send me about the invoice?", {"mail"}, "search_email",
     lambda a: "chris" in str(a.get("query", "")).lower()),
    ("Show me my unread email.", {"mail"}, "search_email",
     lambda a: "unread" in str(a.get("query", "")).lower()),
    ("Any mail from yesterday?", {"mail"}, "search_email", None),
    ("Pull up the full text of message m_8812.", {"mail"}, "get_email",
     lambda a: a.get("id") == "m_8812"),
    ("Find the email where Dana mentioned the lease.", {"mail"}, "search_email",
     lambda a: "dana" in str(a.get("query", "")).lower()),
    ("What's on my calendar next week?", {"gcal"}, "list_events", None),
    ("Am I free on August 3rd?", {"gcal"}, "list_events",
     lambda a: "08-03" in f"{a.get('start')}{a.get('end')}"),
    ("What meetings do I have between September 1 and September 30?", {"gcal"},
     "list_events", lambda a: str(a.get("start", "")).endswith("09-01")),
    ("List my events for tomorrow.", {"gcal"}, "list_events", None),
    # search_books is the PUBLIC Open Library catalog, not the user's shelf.
    # The user's own books are a reading log injected as context (like todos),
    # so "do I own…" is context-only — see ROUTING_CASES. Only outward lookups
    # of facts the log doesn't hold are tool calls.
    ("Look up the ISBN for Piranesi.", {"books"}, "search_books",
     lambda a: "piranesi" in str(a.get("query", "")).lower()),
    # Bibliographic identifiers are what a 4B model hallucinates, so these are
    # the cases search_books genuinely earns. Well-known facts ("who wrote The
    # Dispossessed?") are answered from parametric knowledge without a tool —
    # correct under the power/thermal constraint, so not graded as a miss.
    ("Find me the ISBN for The Left Hand of Darkness.", {"books"}, "search_books",
     lambda a: "left hand" in str(a.get("query", "")).lower()),
]

# ---------------------------------------------------------------------------
# Private-topic probes — the regression that matters most and that nothing
# else guards.
#
# These are ordinary lookups that happen to involve a sensitive subject. The
# failure they catch is the model treating the *topic* as a reason to decline
# ("I don't have access to your medical information") instead of running the
# same search it would run for a haircut. Every one of these must produce a
# tool call. A polite refusal is a failure; so is answering from thin air.
#
# tool_choice forcing is unavailable on this stack, so there is no structural
# backstop — this list is the only thing standing between a prompt edit and a
# silent regression. Do not thin it out.
#
# The expected slot is a SET of acceptable tools, not one name. Several of these
# are answerable two defensible ways — "do I have therapy this week?" names a
# range (list_events) but is also a topic lookup (search_events) — and grading
# one arbitrary choice as correct would measure the grader's taste rather than
# the model's judgement. What must never happen is no tool call at all.
# ---------------------------------------------------------------------------
_GCAL_LOOKUP = frozenset({"search_events", "list_events"})

PRIVATE_TOPIC_PROBES = [
    # No date anywhere in these two: list_events cannot answer them without
    # scanning a wide range, which is why search_events exists.
    ("When is my next dentist appointment?", {"gcal"}, frozenset({"search_events"})),
    ("When is the parent-teacher conference?", {"gcal"}, frozenset({"search_events"})),
    ("Do I have therapy this week?", {"gcal"}, _GCAL_LOOKUP),
    ("Did my lawyer ever get back to me?", {"mail"}, frozenset({"search_email"})),
    ("Find the email with my bank statement.", {"mail"}, frozenset({"search_email"})),
    ("Did the pharmacy email about my prescription?", {"mail"},
     frozenset({"search_email"})),
    # Far-future date: the case Tier 1.2 was written for. The calendar context
    # window ends ~60 days out, so the only wrong move is answering "not shown".
    # search_events is the better answer here — it needs no year arithmetic at
    # all, which is precisely the thing the 4B model kept dropping.
    ("Is my dentist appointment still booked for March next year?", {"gcal"},
     _GCAL_LOOKUP),
]

# ---------------------------------------------------------------------------
# Known gaps — behavior that is wrong today. Not asserted (that would freeze the
# bug in place); recorded so the next person does not rediscover them.
# ---------------------------------------------------------------------------
GAPS = [
    ("Relative-year arithmetic in list_events arguments",
     "Asked about 'March next year', the 4B model resolves the year wrong "
     "roughly half the time — 2026-03 instead of 2027-03 — even though the "
     "calendar context states today's date. search_events sidesteps this for "
     "topic lookups (it needs no dates at all), so the remaining exposure is "
     "narrow: a bare range question naming a relative year, e.g. 'what's on in "
     "March next year?'. The mechanical fix is resolving relative dates before "
     "the model sees them, as TODO_ADD already does via "
     "todo_parse.resolve_relative_phrase."),
]

# ---------------------------------------------------------------------------
# Closed, kept as a record of what the eval was built to catch:
#
# * "Delete todo 7, I already did it." — matched neither todo route and fell
#   through to context-only chat, so the todo silently stayed open. Now routed
#   by ALREADY_DONE, and _mark_done_chat resolves the cited id. Asserted in
#   ROUTING_CASES.
# * "Show me everything I still have open." — no TODO_HINT keyword, so context
#   injection depended on the intent classifier guessing. TODO_HINT now covers
#   "still have open" / "outstanding" / "left to do". Asserted in ROUTING_CASES.
# ---------------------------------------------------------------------------
