"""Reconcile the Canvas mirror into local todos. A plain SQLite/data step — it
never wakes the LLM (spec: bulk sync stays off the model). Local todos are not
external writes, so this is ungated. Calendar markers are handled separately and
stay confirmation-gated. Dedup is strictly by Canvas assignment id; a user-
deleted todo is marked handled and never recreated."""

from datetime import datetime

from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.todos import TodoStore


def local_day(utc_iso: str | None) -> str | None:
    """RFC3339 UTC instant -> the user's local YYYY-MM-DD, or None."""
    if not utc_iso:
        return None
    try:
        return datetime.fromisoformat(utc_iso).astimezone().date().isoformat()
    except (ValueError, TypeError):
        return None


def _todo_text(course: dict | None, name: str) -> str:
    label = (course or {}).get("course_code") or (course or {}).get("name") or "Canvas"
    return f"{label} — {name}"


def _tags(course: dict | None) -> list[str]:
    label = (course or {}).get("course_code") or (course or {}).get("name")
    return ([label] if label else []) + ["canvas"]


def reconcile_todos(store: CanvasStore, todos: TodoStore, *,
                    now: datetime | None = None) -> dict:
    now = now or datetime.now()
    courses = store.courses_by_id()
    created = completed = handled = updated = 0
    for a in store.active_assignments():
        if a["handled"]:
            continue
        due = local_day(a["due_at"])
        if a["todo_id"] is not None:
            if not todos.exists(a["todo_id"]):
                store.mark_handled(a["id"])   # user deleted it -> never recreate
                handled += 1
            elif a["submitted"]:
                todos.toggle(a["todo_id"], True)
                completed += 1
            else:
                existing = next((t for t in todos.list_all()
                                 if t["id"] == a["todo_id"]), None)
                if existing is not None and existing["due_date"] != due:
                    todos.set_due(a["todo_id"], due)
                    updated += 1
            continue
        if a["submitted"]:
            continue                          # never submitted -> don't nag
        tid = todos.add_structured(
            _todo_text(courses.get(a["course_id"]), a["name"]), due,
            _tags(courses.get(a["course_id"])))
        store.link_todo(a["id"], tid, now.isoformat(timespec="seconds"))
        created += 1
    return {"created": created, "completed": completed,
            "handled": handled, "updated": updated}
