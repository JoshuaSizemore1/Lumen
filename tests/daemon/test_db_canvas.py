from lumen.daemon import db


def _tables(conn):
    return {r["name"] for r in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_canvas_tables_created(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    assert {"canvas_courses", "canvas_assignments",
            "canvas_announcements"} <= _tables(conn)


def test_canvas_assignments_has_reconciliation_columns(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert {"todo_id", "calendar_event_id", "first_seen", "handled",
            "submitted", "due_at"} <= cols


def test_canvas_assignments_has_marker_due(tmp_path):
    conn = db.connect(tmp_path / "c.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(canvas_assignments)")}
    assert "marker_due" in cols
