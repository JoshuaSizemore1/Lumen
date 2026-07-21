"""Tool schemas mirroring Lumen's real MCP surface.

Derived from lumen/mcp_servers/{mail,gcal,openlibrary}.py and the todos
connector. Count matches the 13-schema load noted in lumen/config.toml for the
real chat flow, so prefill cost here is representative rather than synthetic.

Research artifact — not imported by Lumen itself.
"""

SEARCH_EMAIL_DESC = """Search the user's locally mirrored email, NEWEST FIRST. Returns message \
ids and summaries (dates shown in the user's local time); use get_email for a full message.

Leave `query` empty to get the most recent messages. Filters combine in the query string; \
plain words search subject and body:
  - on:YYYY-MM-DD          messages received that day (the user's local day)
  - after:YYYY-MM-DD / before:YYYY-MM-DD   a date range
  - newer_than:7d          the last N days
  - from:name-or-email     sender (use in:sent for mail the user sent)
  - to:name-or-email       recipient
  - subject:words          words in the subject
  - is:unread
Examples: `on:2026-07-08` (mail from that day); `from:chris invoice`; empty query (the latest \
mail). Do NOT quote-wrap the whole thing or use boolean operators. The mirror covers roughly \
the last 6 months."""


def _t(name, desc, props, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required),
            },
        },
    }


TOOLS = [
    _t("search_email", SEARCH_EMAIL_DESC, {
        "query": {"type": "string", "description": "Search query; empty for most recent."},
        "limit": {"type": "integer", "description": "Max messages to return.", "default": 5},
    }),
    _t("get_email", "Fetch one mirrored email in full by the id search_email returned.", {
        "id": {"type": "string", "description": "Message id from search_email."},
    }, ["id"]),
    _t("list_events",
       "List the user's Google Calendar events between two ISO dates (inclusive), e.g. "
       "start='2026-09-01' end='2026-09-30'. Use this only for dates the assistant's calendar "
       "context doesn't already cover.", {
           "start": {"type": "string", "description": "ISO date YYYY-MM-DD."},
           "end": {"type": "string", "description": "ISO date YYYY-MM-DD."},
       }, ["start", "end"]),
    _t("create_event",
       "Create an event on the user's primary Google Calendar. The daemon calls this only "
       "after the user explicitly confirmed the exact details in a dialog — never call it "
       "speculatively.", {
           "title": {"type": "string"},
           "start": {"type": "string", "description": "ISO datetime or date."},
           "end": {"type": "string", "description": "ISO datetime or date."},
           "all_day": {"type": "boolean", "default": False},
           "location": {"type": "string"},
           "description": {"type": "string"},
           "attendees": {"type": "array", "items": {"type": "string"}},
           "recurrence": {"type": "string", "description": "RRULE string."},
       }, ["title", "start", "end"]),
    _t("delete_event",
       "Permanently delete an event from the user's Google Calendar. The daemon calls this "
       "only after the user explicitly confirmed the exact event in a dialog — never call it "
       "speculatively.", {
           "event_id": {"type": "string"},
           "calendar_id": {"type": "string", "default": "primary"},
           "notify_attendees": {"type": "boolean", "default": False},
       }, ["event_id"]),
    _t("list_todos",
       "List the user's todos. Filter by status or due window; newest first.", {
           "status": {"type": "string", "enum": ["open", "done", "all"], "default": "open"},
           "due_before": {"type": "string", "description": "ISO date."},
           "limit": {"type": "integer", "default": 20},
       }),
    _t("add_todo", "Create a todo item for the user.", {
        "text": {"type": "string", "description": "The todo text."},
        "due": {"type": "string", "description": "ISO date, optional."},
        "project": {"type": "string"},
    }, ["text"]),
    _t("complete_todo", "Mark a todo as done by its id.", {
        "id": {"type": "integer"},
    }, ["id"]),
    _t("delete_todo", "Delete a todo by its id. Requires prior user confirmation.", {
        "id": {"type": "integer"},
    }, ["id"]),
    _t("search_books",
       "Search the user's book catalog by title, author, or subject.", {
           "query": {"type": "string"},
           "limit": {"type": "integer", "default": 10},
       }, ["query"]),
    _t("lookup_book",
       "Look up a book's metadata on OpenLibrary by title or ISBN, for grounding "
       "recommendations.", {
           "title": {"type": "string"},
           "isbn": {"type": "string"},
       }),
    _t("read_file", "Read a UTF-8 text file from the user's filesystem by absolute path.", {
        "path": {"type": "string"},
    }, ["path"]),
    _t("write_file",
       "Write a UTF-8 text file. Gated: the daemon calls this only after the user confirmed "
       "the write in a dialog.", {
           "path": {"type": "string"},
           "content": {"type": "string"},
       }, ["path", "content"]),
]

assert len(TOOLS) == 13, len(TOOLS)

SYSTEM_PROMPT = (
    "You are Lumen, a local-first daily assistant for Josh. Today is Saturday 2026-07-18, "
    "local time 14:05, timezone America/Chicago.\n"
    "You have tools for email, calendar, todos, books, and files. Call a tool when the user's "
    "request needs data you do not already have. Prefer exactly one tool call. Never call a "
    "write tool (create_event, delete_event, delete_todo, write_file) speculatively — those "
    "run only after explicit confirmation.\n"
    "If you can answer directly without a tool, do so concisely."
)

# (prompt, expected_tool, required_arg_predicate|None)
TOOL_CALL_PROMPTS = [
    ("What did Chris send me about the invoice?", "search_email",
     lambda a: "chris" in str(a.get("query", "")).lower()),
    ("Show me my unread email.", "search_email",
     lambda a: "unread" in str(a.get("query", "")).lower()),
    ("Any mail from yesterday?", "search_email", None),
    ("Pull up the full text of message m_8812.", "get_email",
     lambda a: a.get("id") == "m_8812"),
    ("What's on my calendar next week?", "list_events", None),
    ("Am I free on August 3rd?", "list_events",
     lambda a: "2026-08-03" in f"{a.get('start')}{a.get('end')}"),
    ("What meetings do I have between September 1 and September 30?", "list_events",
     lambda a: a.get("start", "").startswith("2026-09-01")),
    ("Add a todo to renew the car registration by Friday.", "add_todo",
     lambda a: "registration" in str(a.get("text", "")).lower()),
    ("Remind me to call the dentist.", "add_todo",
     lambda a: "dentist" in str(a.get("text", "")).lower()),
    ("What's on my todo list?", "list_todos", None),
    ("Show me everything I still have open.", "list_todos", None),
    ("Mark todo 42 as done.", "complete_todo", lambda a: a.get("id") in (42, "42")),
    ("Do I own anything by Ursula Le Guin?", "search_books",
     lambda a: "guin" in str(a.get("query", "")).lower()),
    ("Look up the ISBN for Piranesi.", "lookup_book",
     lambda a: "piranesi" in str(a.get("title", "")).lower()),
    ("Read /home/josh/notes/standup.md for me.", "read_file",
     lambda a: a.get("path") == "/home/josh/notes/standup.md"),
    ("Find the email where Dana mentioned the lease.", "search_email",
     lambda a: "dana" in str(a.get("query", "")).lower()),
    ("What did I get on July 8th?", "search_email",
     lambda a: "2026-07-08" in str(a.get("query", ""))),
    ("List my events for tomorrow.", "list_events", None),
    ("Add 'buy oat milk' to my list.", "add_todo",
     lambda a: "oat milk" in str(a.get("text", "")).lower()),
    ("Search my library for books about cartography.", "search_books",
     lambda a: "cartograph" in str(a.get("query", "")).lower()),
]

assert len(TOOL_CALL_PROMPTS) == 20, len(TOOL_CALL_PROMPTS)
