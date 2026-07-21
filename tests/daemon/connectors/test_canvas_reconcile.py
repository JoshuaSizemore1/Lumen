from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore
from lumen.daemon.connectors.canvas_reconcile import local_day, reconcile_todos
from lumen.daemon.connectors.todos import TodoStore


def _fixture(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    store = CanvasStore(conn)
    todos = TodoStore(conn)
    store.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"}])
    return store, todos


def _assign(store, **over):
    row = {"id": 10, "course_id": 1, "name": "HW1",
           "due_at": "2026-09-02T06:00:00Z", "points": 100.0, "html_url": "u",
           "description": None, "submitted": False}
    row.update(over)
    store.upsert_assignments([row])


def test_local_day_converts_utc():
    assert local_day("2026-09-02T06:00:00Z") is not None
    assert local_day(None) is None
    assert local_day("garbage") is None


def test_new_unsubmitted_assignment_creates_todo(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    res = reconcile_todos(store, todos)
    assert res["created"] == 1
    t = todos.list_all()[0]
    assert t["text"] == "CS3505 — HW1"
    assert t["source"] == "canvas"
    assert "canvas" in t["tags"]
    a = store.active_assignments()[0]
    assert a["todo_id"] == t["id"] and a["first_seen"] is not None


def test_submitted_assignment_is_not_created(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store, submitted=True)
    res = reconcile_todos(store, todos)
    assert res["created"] == 0
    assert todos.list_all() == []


def test_reconcile_is_idempotent(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    res = reconcile_todos(store, todos)
    assert res["created"] == 0
    assert len(todos.list_all()) == 1


def test_submission_marks_todo_done(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    _assign(store, submitted=True)
    res = reconcile_todos(store, todos)
    assert res["completed"] == 1
    assert todos.list_all()[0]["completed"] is True


def test_due_change_updates_todo(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store, due_at="2026-09-02T06:00:00Z")
    reconcile_todos(store, todos)
    first_due = todos.list_all()[0]["due_date"]
    _assign(store, due_at="2026-09-20T06:00:00Z")
    res = reconcile_todos(store, todos)
    assert res["updated"] == 1
    assert todos.list_all()[0]["due_date"] != first_due


def test_deleted_todo_is_not_recreated(tmp_path):
    store, todos = _fixture(tmp_path)
    _assign(store)
    reconcile_todos(store, todos)
    todos.delete(todos.list_all()[0]["id"])
    res = reconcile_todos(store, todos)
    assert res["handled"] == 1
    assert res["created"] == 0
    assert todos.list_all() == []
    assert store.active_assignments()[0]["handled"] == 1
