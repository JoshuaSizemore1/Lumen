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


def _seed_reconcile(store):
    store.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"},
                          {"id": 9, "name": "OLD", "course_code": "OLD"}])
    store.deactivate_courses_except([1])   # course 9 concluded
    store.upsert_assignments([
        {"id": 10, "course_id": 1, "name": "HW1", "due_at": "2026-09-01T06:59:59Z",
         "points": 100.0, "html_url": "u", "description": None, "submitted": False},
        {"id": 99, "course_id": 9, "name": "OLD HW", "due_at": None, "points": None,
         "html_url": None, "description": None, "submitted": False}])


def test_active_assignments_excludes_inactive_courses(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)
    assert [a["id"] for a in s.active_assignments()] == [10]


def test_link_and_mark_handled(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)
    s.link_todo(10, 55, "2026-07-21T09:00:00")
    a = next(a for a in s.active_assignments() if a["id"] == 10)
    assert a["todo_id"] == 55 and a["first_seen"] == "2026-07-21T09:00:00"
    s.mark_handled(10)
    a = next(a for a in s.active_assignments() if a["id"] == 10)
    assert a["handled"] == 1 and a["todo_id"] is None


def test_pending_markers_create_then_update(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)
    s.link_todo(10, 55, "2026-07-21T09:00:00")
    pend = s.pending_markers([1])
    assert [p["id"] for p in pend] == [10]
    assert pend[0]["action"] == "create"
    s.set_calendar_marker(10, "evt_1", "2026-09-01")
    assert s.pending_markers([1]) == []
    s.upsert_assignments([{"id": 10, "course_id": 1, "name": "HW1",
                           "due_at": "2026-09-05T06:59:59Z", "points": 100.0,
                           "html_url": "u", "description": None, "submitted": False}])
    pend = s.pending_markers([1])
    assert pend[0]["id"] == 10 and pend[0]["action"] == "update"
    assert pend[0]["event_id"] == "evt_1"


def test_set_course_included_roundtrip_and_pull_ids(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([{"id": 1, "name": "A", "course_code": "A"},
                          {"id": 2, "name": "B", "course_code": "B"}])
    # Default: everything is included, so both are in the pull set.
    assert set(store.pull_course_ids()) == {1, 2}
    store.set_course_included(2, False)          # archive course 2
    assert store.pull_course_ids() == [1]
    store.set_course_included(2, True)           # un-archive
    assert set(store.pull_course_ids()) == {1, 2}


def test_courses_for_panel_lists_all_active_with_flag(tmp_path):
    store = make_store(tmp_path)
    store.upsert_courses([{"id": 1, "name": "A", "course_code": "A"},
                          {"id": 2, "name": "B", "course_code": "B"}])
    store.set_course_included(2, False)
    panel = {c["id"]: c["included"] for c in store.courses_for_panel()}
    # The archived course still appears in the panel (so it can be toggled back),
    # just with included = 0.
    assert panel == {1: 1, 2: 0}


def test_archive_hides_assignments_but_keeps_rows(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)   # course 1 active, assignment 10
    s.upsert_assignments([{"id": 11, "course_id": 1, "name": "HW2",
                           "due_at": "2026-09-08T06:59:59Z", "points": 10.0,
                           "html_url": "u", "description": None, "submitted": False}])
    s.link_todo(10, 55, "2026-07-21T09:00:00")     # a todo already exists
    s.set_course_included(1, False)                # archive it
    # Hidden from the summary/reconcile read...
    assert s.active_assignments() == []
    # ...but the rows and the todo link are untouched in the backend.
    kept = s.assignments(course_id=1)
    assert {a["id"] for a in kept} == {10, 11}
    assert next(a for a in kept if a["id"] == 10)["todo_id"] == 55
    # Un-archiving brings it straight back with the link intact.
    s.set_course_included(1, True)
    assert [a["id"] for a in s.active_assignments()] == [10, 11]


def test_archive_hides_announcements_but_absent_course_still_shows(tmp_path):
    s = make_store(tmp_path)
    s.upsert_courses([{"id": 1, "name": "A", "course_code": "A"}])
    s.upsert_announcements([
        {"id": 1, "course_id": 1, "title": "From A", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m", "html_url": "a1"},
        {"id": 2, "course_id": 99, "title": "Orphan", "posted_at": "2026-05-01T00:00:00Z",
         "message": "m", "html_url": "a2"}])
    s.set_course_included(1, False)
    titles = [a["title"] for a in s.announcements()]
    # Course 1 archived -> hidden. Course 99 has no course row -> not archived, shown.
    assert titles == ["Orphan"]


def test_dismiss_assignment_hides_but_keeps_row_and_undo_restores(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)   # course 1 active, assignment 10
    s.upsert_assignments([{"id": 11, "course_id": 1, "name": "HW2",
                           "due_at": "2026-09-08T06:59:59Z", "points": 10.0,
                           "html_url": "u", "description": None, "submitted": False}])
    s.link_todo(10, 55, "2026-07-21T09:00:00")     # a linked todo already exists
    s.set_assignment_dismissed(10, True)
    # Dismissed drops out of the tab/reconcile read (declutter + no pending)...
    assert [a["id"] for a in s.active_assignments()] == [11]
    # ...but the row and its todo link are untouched.
    kept = next(a for a in s.assignments(course_id=1) if a["id"] == 10)
    assert kept["dismissed"] == 1 and kept["todo_id"] == 55
    # Undo restores it.
    s.set_assignment_dismissed(10, False)
    assert [a["id"] for a in s.active_assignments()] == [10, 11]


def test_dismiss_survives_resync(tmp_path):
    s = make_store(tmp_path); _seed_reconcile(s)   # assignment 10 on active course 1
    s.set_assignment_dismissed(10, True)
    # A later poll re-upserts the same assignment; the dismissal must persist.
    s.upsert_assignments([{"id": 10, "course_id": 1, "name": "HW1",
                           "due_at": "2026-09-05T06:59:59Z", "points": 100.0,
                           "html_url": "u", "description": None, "submitted": False}])
    assert s.active_assignments() == []
    assert next(a for a in s.assignments(course_id=1) if a["id"] == 10)["dismissed"] == 1


def test_dismiss_announcement_hides_and_undo_restores(tmp_path):
    s = make_store(tmp_path)
    s.upsert_courses([{"id": 1, "name": "A", "course_code": "A"}])
    s.upsert_announcements([
        {"id": 1, "course_id": 1, "title": "Keep", "posted_at": "2026-05-07T00:00:00Z",
         "message": "m", "html_url": "a1"},
        {"id": 2, "course_id": 1, "title": "Hide", "posted_at": "2026-05-01T00:00:00Z",
         "message": "m", "html_url": "a2"}])
    s.set_announcement_dismissed(2, True)
    assert [a["title"] for a in s.announcements()] == ["Keep"]
    s.set_announcement_dismissed(2, False)
    assert [a["title"] for a in s.announcements()] == ["Keep", "Hide"]


def test_announcement_classification_roundtrip(tmp_path):
    s = make_store(tmp_path)
    s.upsert_courses([{"id": 1, "name": "CS 3505", "course_code": "CS3505"}])
    s.upsert_announcements([{"id": 5, "course_id": 1, "title": "Exam moved",
                             "posted_at": "2026-08-20T00:00:00Z",
                             "message": "midterm now Friday", "html_url": "a"}])
    assert [a["id"] for a in s.unclassified_announcements(10)] == [5]
    s.set_announcement_flag(5, 1, '{"text": "Study", "due": "2026-08-28"}')
    assert s.unclassified_announcements(10) == []
    a = s.get_announcement(5)
    assert a["actionable"] == 1 and a["seen"] == 1
    s.link_announcement_todo(5, 77)
    assert s.get_announcement(5)["todo_id"] == 77
