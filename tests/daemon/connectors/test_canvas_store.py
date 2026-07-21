from lumen.daemon import db
from lumen.daemon.connectors.canvas_store import CanvasStore


def make_store(tmp_path):
    return CanvasStore(db.connect(tmp_path / "canvas.db"))


def test_upsert_and_read_courses(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([
        {"id": 1239119, "name": "MATH 2270 Linear Algebra", "course_code": "MATH2270"},
        {"id": 1223520, "name": "CS 3505 Software Practice II", "course_code": "CS3505"},
    ])
    rows = store.active_courses()
    assert {r["id"] for r in rows} == {1239119, 1223520}
    assert all(r["active"] == 1 for r in rows)


def test_assignments_ordered_dated_first(tmp_path):
    store = make_store(tmp_path)
    store.upsert_assignments([
        {"id": 2, "course_id": 1, "name": "HW2", "due_at": "2026-01-24T06:59:59Z",
         "points": 140.0, "html_url": "u2", "description": None, "submitted": False},
        {"id": 3, "course_id": 1, "name": "No date", "due_at": None,
         "points": None, "html_url": "u3", "description": None, "submitted": False},
        {"id": 1, "course_id": 1, "name": "HW1", "due_at": "2026-01-17T06:59:59Z",
         "points": 110.0, "html_url": "u1", "description": None, "submitted": False},
    ])
    assert [a["name"] for a in store.assignments(course_id=1)] == ["HW1", "HW2", "No date"]


def test_assignment_upsert_preserves_reconciliation_state(tmp_path):
    store = make_store(tmp_path)
    base = {"id": 9, "course_id": 1, "name": "Essay", "due_at": "2026-02-01T06:59:59Z",
            "points": 50.0, "html_url": "u", "description": None, "submitted": False}
    store.upsert_assignments([base])
    # Simulate Part-4 reconciliation having linked a todo + marked handled.
    with store._conn:
        store._conn.execute(
            "UPDATE canvas_assignments SET todo_id=42, handled=1 WHERE id=9")
    # A later sync re-upserts with a moved due date; link/handled must survive.
    store.upsert_assignments([{**base, "due_at": "2026-02-08T06:59:59Z", "submitted": True}])
    row = store.assignments(course_id=1)[0]
    assert row["due_at"] == "2026-02-08T06:59:59Z"
    assert row["submitted"] == 1
    assert row["todo_id"] == 42 and row["handled"] == 1


def test_announcements_newest_first_and_upsert_preserves_seen(tmp_path):
    store = make_store(tmp_path)
    store.upsert_announcements([
        {"id": 1, "course_id": 1, "title": "Old", "posted_at": "2026-05-01T00:00:00Z",
         "message": "m1", "html_url": "a1"},
        {"id": 2, "course_id": 1, "title": "New", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m2", "html_url": "a2"},
    ])
    assert [a["title"] for a in store.announcements()] == ["New", "Old"]
    with store._conn:
        store._conn.execute("UPDATE canvas_announcements SET seen=1 WHERE id=2")
    store.upsert_announcements([
        {"id": 2, "course_id": 1, "title": "New (edited)", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m2b", "html_url": "a2"}])
    edited = [a for a in store.announcements() if a["id"] == 2][0]
    assert edited["title"] == "New (edited)" and edited["seen"] == 1


def test_deactivate_courses_except_keeps_only_listed(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([
        {"id": 1, "name": "A", "course_code": "A"},
        {"id": 2, "name": "B", "course_code": "B"},
        {"id": 3, "name": "C", "course_code": "C"},
    ])
    store.deactivate_courses_except([1, 3])
    assert {r["id"] for r in store.active_courses()} == {1, 3}


def test_deactivate_courses_except_empty_deactivates_all(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([{"id": 1, "name": "A", "course_code": "A"}])
    store.deactivate_courses_except([])
    assert store.active_courses() == []
