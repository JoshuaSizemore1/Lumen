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
                continue
            submitted_int = 1 if a["submitted"] else 0
            # Treat NULL applied_* as "already at current Canvas value" —
            # matches what the migration sets for pre-existing rows.
            applied_sub = a.get("applied_submitted")
            applied_due = a.get("applied_due")
            if applied_sub is None:
                applied_sub = submitted_int
            if applied_due is None:
                applied_due = due
            sub_changed = applied_sub != submitted_int
            due_changed = not a["submitted"] and applied_due != due
            # Push only when Canvas itself changed the field since last apply.
            if sub_changed and a["submitted"]:
                todos.toggle(a["todo_id"], True)
                completed += 1
            if due_changed:
                todos.set_due(a["todo_id"], due)
                updated += 1
            if sub_changed or due_changed:
                store.mark_applied(
                    a["id"],
                    submitted_int if sub_changed else applied_sub,
                    due if due_changed else applied_due)
            continue
        if a["submitted"]:
            continue                          # never submitted -> don't nag
        tid = todos.add_structured(
            _todo_text(courses.get(a["course_id"]), a["name"]), due,
            _tags(courses.get(a["course_id"])))
        store.link_todo(a["id"], tid, now.isoformat(timespec="seconds"))
        store.mark_applied(a["id"], 0, due)   # record initial values
        created += 1
    return {"created": created, "completed": completed,
            "handled": handled, "updated": updated}
