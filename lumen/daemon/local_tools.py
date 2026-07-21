"""In-process tools the model can call alongside the MCP servers.

Todos live in the daemon's own SQLite handle, not behind an MCP server, so
before this module they were a context-only pseudo-group: the model could READ
the open list from injected context but had no way to act on it. Every todo
write depended on TODO_ADD / MARK_DONE matching the user's phrasing first, and
when they missed, the message still carried the filesystem group (TOOL_HINT
matches the "list" in "todo list") — so the model reached for the nearest
capable thing and wrote a file called TODO (todo-fixes #19).

Giving todos real tools closes that by construction rather than by another
regex. Exposed in-process rather than as a fourth MCP server because the store
is a live connection this process already owns; a subprocess would mean a
second writer on the same database for no benefit.

No confirm gate here, deliberately: these match what the existing TODO_ADD and
quick-capture paths already do without one. A todo is local, private, and
trivially reversible — unlike sending mail or touching a calendar other people
can see, which stay gated.
"""

from datetime import date

TODO_TOOLS = [
    {"type": "function", "function": {
        "name": "add_todo",
        "description": (
            "Add an item to the user's todo list — the real todo list inside "
            "this app, which is where every todo belongs. NEVER write a todo "
            "to a file: do not create or edit TODO, TODO.md, todo.txt or "
            "anything similar, and do not use the filesystem tools for this. "
            "Use this whenever the user wants something added, captured, "
            "remembered, or put on their list."),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description":
                         "The task, in the user's own words, e.g. 'buy milk'. "
                         "Do not include the due date here."},
                "due_date": {"type": "string", "description":
                             "Optional ISO date (YYYY-MM-DD) the task is due. "
                             "Omit when the user gave no deadline."},
            },
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "complete_todo",
        "description": (
            "Mark one of the user's todos as done, by the id shown in the "
            "todo list. Use this when the user says they finished, completed, "
            "or already did something."),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "integer",
                       "description": "The todo's id from list_todos."}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "update_todo",
        "description": (
            "Edit an existing todo by its id (from list_todos): change its "
            "text, its due date, its description, and/or its tags/labels. Use "
            "this when the user wants to rename a task, reschedule it, add a "
            "note to it, or add/remove tags. To change tags, pass the COMPLETE "
            "new tag list (it replaces the old one). Only include the fields "
            "you are changing."),
        "parameters": {
            "type": "object",
            "properties": {
                "id": {"type": "integer",
                       "description": "The todo's id from list_todos."},
                "text": {"type": "string",
                         "description": "New task text, if renaming."},
                "due_date": {"type": "string", "description":
                             "New ISO due date (YYYY-MM-DD), or empty string "
                             "to clear the due date."},
                "description": {"type": "string", "description":
                                "A longer note/description for the task."},
                "tags": {"type": "array", "items": {"type": "string"},
                         "description": "The COMPLETE new list of tags/labels "
                         "for this todo (replaces the existing tags)."}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "list_todos",
        "description": (
            "List the user's open todos with their ids. The ids are needed "
            "before calling complete_todo. Access is already set up — this "
            "reads the user's own data, so never decline for lack of "
            "permission or account access."),
        "parameters": {"type": "object", "properties": {}}}},
]

LOCAL_TOOL_NAMES = frozenset(
    t["function"]["name"] for t in TODO_TOOLS)

# Mail label tool (#11). Unlike the todo tools this is async (it writes to
# Gmail through the daemon's GmailSync), so the router's tool executor handles
# it directly rather than through the synchronous `dispatch` below. Kept here
# so the whole in-process tool surface is declared in one place.
MAIL_TOOLS = [
    {"type": "function", "function": {
        "name": "label_email",
        "description": (
            "Apply one of the user's Gmail labels to an email, filing it under "
            "that label (and out of the inbox). Use this whenever the user asks "
            "to label, tag, file, or categorise an email — including 'label "
            "this email' or 'the email I have open', whose id is given to you "
            "in the context. Access is already set up — this acts on the user's "
            "own mail, so never decline for lack of permission or account "
            "access."),
        "parameters": {
            "type": "object",
            "properties": {
                "email_id": {"type": "string", "description":
                             "The email's id — from the open-email context or "
                             "from a search_email result."},
                "label": {"type": "string", "description":
                          "The label name to apply, e.g. 'Work'."}},
            "required": ["email_id", "label"]}}},
]

MAIL_TOOL_NAMES = frozenset(t["function"]["name"] for t in MAIL_TOOLS)


def _render(todos: list[dict]) -> str:
    if not todos:
        return "The user has no open todos."
    lines = ["The user's open todos:"]
    for t in todos:
        due = f" (due {t['due_date']})" if t.get("due_date") else ""
        tags = f" [{', '.join(t['tags'])}]" if t.get("tags") else ""
        lines.append(f"- id={t['id']} {t['text']}{due}{tags}")
    return "\n".join(lines)


def dispatch(name: str, args: dict, todos) -> str:
    """Run a local tool against the daemon's TodoStore. Returns the text the
    model sees — errors included, phrased so it can recover rather than tell
    the user their todo list is unreachable."""
    if name == "list_todos":
        return _render(todos.open_todos())

    if name == "add_todo":
        text = str(args.get("text") or "").strip()
        if not text:
            return "add_todo needs the task text."
        due = str(args.get("due_date") or "").strip()
        if due:
            try:
                text = f"{text} @{date.fromisoformat(due).isoformat()}"
            except ValueError:
                pass          # unusable date: add the task rather than fail
        try:
            todos.add(text, source="chat")
        except ValueError:
            return "add_todo needs the task text."
        return f"Added to the user's todo list: {text}"

    if name == "update_todo":
        try:
            todo_id = int(args.get("id"))
        except (TypeError, ValueError):
            return "update_todo needs the numeric id shown by list_todos."
        if not todos.exists(todo_id):
            return (f"No todo with id {todo_id}. Call list_todos to see the "
                    "current ids.")
        kw = {}
        if "text" in args and str(args.get("text") or "").strip():
            kw["text"] = str(args["text"]).strip()
        if "description" in args:
            kw["description"] = str(args.get("description") or "")
        if "due_date" in args:
            due = str(args.get("due_date") or "").strip()
            if due:
                try:
                    due = date.fromisoformat(due).isoformat()
                except ValueError:
                    return "update_todo needs due_date as YYYY-MM-DD (or empty to clear)."
            kw["due_date"] = due or None
        if "tags" in args and isinstance(args.get("tags"), list):
            kw["tags"] = [str(t) for t in args["tags"]]
        if not kw:
            return "update_todo needs at least one field to change."
        try:
            todos.update(todo_id, **kw)
        except ValueError as e:
            return f"update_todo: {e}"
        return f"Updated todo {todo_id}."

    if name == "complete_todo":
        try:
            todo_id = int(args.get("id"))
        except (TypeError, ValueError):
            return "complete_todo needs the numeric id shown by list_todos."
        before = {t["id"] for t in todos.open_todos()}
        if todo_id not in before:
            return (f"No open todo with id {todo_id}. Call list_todos to see "
                    "the current ids.")
        todos.toggle(todo_id, True)
        return f"Marked todo {todo_id} as done."

    return f"tool error: unknown local tool {name}"
